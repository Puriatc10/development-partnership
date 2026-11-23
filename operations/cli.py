"""Operator CLI — the human surface for the platform's gates.

One command group per gate, so an operator never has to know which module implements what:

    python -m operations.cli whoami
    python -m operations.cli candidates list
    python -m operations.cli candidates show <candidate-id>
    python -m operations.cli candidates approve <candidate-id> --note "..."
    python -m operations.cli candidates reject <candidate-id> --reason "..."
    python -m operations.cli candidates register <candidate-id> --segment digital_agency

Every refusal is printed as a reason plus the next action, never as a traceback: the
operator has to be able to tell a legitimate policy denial from a bug.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Mapping, Sequence

import requests

from contracts.errors import EnvironmentGuardError
from integrations.postgres.client import PostgrestError
from qualification.loader import QualificationInputError

from . import candidates as gate1
from .registration import register_candidate
from .session import OperatorSessionError, open_session


def _json_default(value: Any) -> Any:
    return str(value)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=_json_default))


def _want_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


_CONTROL = {code: None for code in list(range(0x00, 0x20)) + [0x7F]}


def _safe(value: Any) -> str:
    """Strip control characters from any value an operator will see on a terminal.

    Agent-authored text (candidate names, evidence excerpts) is untrusted input; an ESC
    sequence could otherwise redraw the lines that report status or reviewer.
    """
    return str(value if value is not None else "").translate(_CONTROL)


def _table(rows: Sequence[Mapping[str, Any]], columns: Sequence[tuple[str, str]]) -> str:
    if not rows:
        return "  (none)"
    widths = {key: len(title) for key, title in columns}
    for row in rows:
        for key, _ in columns:
            widths[key] = max(widths[key], len(str(row.get(key) or "")))
    header = "  ".join(title.ljust(widths[key]) for key, title in columns)
    lines = [header, "  ".join("-" * widths[key] for key, _ in columns)]
    for row in rows:
        lines.append("  ".join(str(row.get(key) or "").ljust(widths[key]) for key, _ in columns))
    return "\n".join(lines)


def _cmd_whoami(session: Any, args: argparse.Namespace) -> int:
    payload = {
        "user_id": session.user_id,
        "email": session.email,
        "role": session.role,
        "project": "development",
    }
    if _want_json(args):
        _print_json(payload)
    else:
        print(f"operator : {session.email} ({session.role})")
        print(f"user id  : {session.user_id}")
    return 0


def _cmd_candidates_list(session: Any, args: argparse.Namespace) -> int:
    rows = gate1.list_candidates(session.client, status=args.status, limit=args.limit)
    if _want_json(args):
        _print_json(rows)
        return 0
    print(f"{len(rows)} candidate(s) with status {args.status}")
    print(
        _table(
            rows,
            [
                ("id", "ID"),
                ("domain", "DOMAIN"),
                ("name", "NAME"),
                ("status", "STATUS"),
                ("discovered_at", "DISCOVERED"),
            ],
        )
    )
    return 0


def _cmd_candidates_show(session: Any, args: argparse.Namespace) -> int:
    candidate = gate1.get_candidate(session.client, args.candidate_id)
    evidence = gate1.list_evidence(session.client, args.candidate_id)
    if _want_json(args):
        _print_json({"candidate": candidate, "evidence": evidence})
        return 0
    print(f"candidate {candidate.get('id')}")
    for key in (
        "name",
        "domain",
        "status",
        "source_name",
        "source_url",
        "batch_key",
        "discovered_by_agent_identity",
        "discovered_at",
        "reviewed_by_human_id",
        "reviewed_at",
        "review_notes",
        "converted_company_id",
    ):
        print(f"  {key:32}: {candidate.get(key)}")
    print(f"  evidence ({len(evidence)}):")
    for row in evidence:
        excerpt = _safe(row.get("excerpt")).strip()
        print(f"    - {_safe(row.get('url'))} [{_safe(row.get('provider'))}] {excerpt[:140]}")
    if str(candidate.get("status")) == gate1.PENDING:
        print("\nnext: `candidates approve <id>` or `candidates reject <id> --reason ...`")
    elif str(candidate.get("status")) == gate1.APPROVED:
        print("\nnext: `candidates register <id> --segment <segment>`")
    return 0


def _cmd_candidates_approve(session: Any, args: argparse.Namespace) -> int:
    row = gate1.approve_candidate(
        session.client, args.candidate_id, operator_id=session.user_id, review_notes=args.note
    )
    if _want_json(args):
        _print_json(row)
    else:
        print(f"approved {row.get('domain')} ({row.get('id')}) by {row.get('reviewed_by_human_id')}")
        print(f"next: `candidates register {row.get('id')} --segment <segment>`")
    return 0


def _cmd_candidates_reject(session: Any, args: argparse.Namespace) -> int:
    row = gate1.reject_candidate(
        session.client, args.candidate_id, operator_id=session.user_id, reason=args.reason
    )
    if _want_json(args):
        _print_json(row)
    else:
        print(f"rejected {row.get('domain')} ({row.get('id')}): {row.get('review_notes')}")
    return 0


def _cmd_candidates_register(session: Any, args: argparse.Namespace) -> int:
    result = register_candidate(
        session.client,
        args.candidate_id,
        operator_id=session.user_id,
        segment=args.segment,
        website_url=args.website,
    )
    if _want_json(args):
        _print_json(
            {
                "company": result.company,
                "candidate": result.candidate,
                "created": result.created,
                "duplicate": result.duplicate,
            }
        )
        return 0
    if result.created:
        print(f"created company {result.company_id} ({result.company.get('primary_domain')})")
    elif result.duplicate:
        print(
            f"already in the CRM: domain belongs to company {result.company_id} "
            f"({result.company.get('name')}); the candidate is recorded as duplicate"
        )
    else:
        print(f"candidate was already registered as company {result.company_id}; nothing created")
    return 0


def _cmd_qualify(session: Any, args: argparse.Namespace) -> int:
    """Score a company from its own persisted research, as a preview.

    The draft is not persisted from an operator session: a scorecard's ``evaluator_type``
    is ``agent`` and must name the agent that produced it, so writing one here would
    attribute the engine's output to the wrong identity. Persistence needs a qualifier
    agent identity (roadmap OW-1).
    """
    from qualification.service import qualify_company

    result = qualify_company(session.client, args.company_id, agent_identity="preview", persist=False)
    card = result.scorecard
    if _want_json(args):
        _print_json(
            {
                "company": result.evidence.company.get("name"),
                "total_score": card.total_score,
                "tier": card.tier,
                "tier_label": card.tier_label,
                "evidence_coverage": card.evidence_coverage,
                "insufficient_evidence": card.insufficient_evidence,
                "findings": list(result.evidence.finding_ids),
                "dimensions": [
                    {
                        "key": d.key,
                        "points": d.points_awarded,
                        "max_points": d.max_points,
                        "confidence": d.confidence,
                    }
                    for d in card.dimensions
                ],
            }
        )
        return 0
    print(f"{result.evidence.company.get('name')} — {card.total_score}/100 ({card.tier}) {card.tier_label}")
    print(f"  coverage {card.evidence_coverage:.3f} · {len(result.evidence.findings)} findings · "
          f"insufficient_evidence={card.insufficient_evidence}")
    for dimension in card.dimensions:
        print(f"  {dimension.key:26} {dimension.points_awarded:>5}/{dimension.max_points:<3} {dimension.confidence}")
    print("\nnot persisted: a draft must name the agent that produced it, and this session is a "
          "human operator. Persistence requires a qualifier agent identity (OW-1).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m operations.cli",
        description="Eryndex operator surface — human gates only, no agent actions.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="show the signed-in operator and its role")

    cand = sub.add_parser("candidates", help="Human Gate 1 — review and register candidates")
    cand_sub = cand.add_subparsers(dest="action", required=True)

    listing = cand_sub.add_parser("list", help="list candidates (pending by default)")
    listing.add_argument("--status", default=gate1.PENDING)
    listing.add_argument("--limit", type=int, default=50)
    listing.set_defaults(func=_cmd_candidates_list)

    show = cand_sub.add_parser("show", help="show one candidate and its evidence")
    show.add_argument("candidate_id")
    show.set_defaults(func=_cmd_candidates_show)

    approve = cand_sub.add_parser("approve", help="record Gate 1 approval")
    approve.add_argument("candidate_id")
    approve.add_argument("--note", default=None)
    approve.set_defaults(func=_cmd_candidates_approve)

    reject = cand_sub.add_parser("reject", help="reject a candidate with a reason")
    reject.add_argument("candidate_id")
    reject.add_argument("--reason", required=True)
    reject.set_defaults(func=_cmd_candidates_reject)

    register = cand_sub.add_parser("register", help="create the CRM company for an approved candidate")
    register.add_argument("candidate_id")
    register.add_argument("--segment", default=None, help="market_segment_type value")
    register.add_argument("--website", default=None)
    register.set_defaults(func=_cmd_candidates_register)

    qualify = sub.add_parser("qualify", help="score a company from its persisted research (preview)")
    qualify.add_argument("company_id")
    qualify.set_defaults(func=_cmd_qualify)

    parser.set_defaults(func=_cmd_whoami)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        session = open_session()
    except OperatorSessionError as exc:
        print(f"cannot open an operator session: {exc}", file=sys.stderr)
        return 2

    try:
        return int(args.func(session, args))
    except gate1.Gate1Error as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except QualificationInputError as exc:
        print(f"cannot qualify: {exc}", file=sys.stderr)
        return 2
    except EnvironmentGuardError as exc:
        print(f"environment refused: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        print(
            f"network failure talking to Supabase ({exc.__class__.__name__}); the action may or may "
            "not have been applied — re-run the same command, it is idempotent.",
            file=sys.stderr,
        )
        return 2
    except PostgrestError as exc:
        detail = f"{exc.code}: {exc.message}" if exc.code else str(exc)
        print(
            f"database refused the action ({detail}). If this is a policy denial, the identity "
            "signed in is not allowed to perform it; if it is a constraint, the message above "
            "names the rule that stopped it.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
