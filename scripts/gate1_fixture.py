"""Create and remove a clearly-marked synthetic candidate for Gate 1 verification.

A Gate 1 path cannot be verified without a candidate, and only a discovery agent may stage
one. When no discovery identity exists (see the roadmap's OW-1), an operator may insert a
row directly — the operator policy allows it — provided the row is unmistakably synthetic:

* ``domain`` ends in ``.example.com`` (reserved by RFC 2606, so it can never be a real partner)
* ``source_name`` is ``synthetic_fixture``
* the name is prefixed ``[SYNTHETIC]``

``--remove`` deletes only rows that satisfy all three, and only the companies that were
created from them, so it cannot touch real CRM data.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from discovery.identity import normalize_name  # noqa: E402
from integrations.postgres.client import PostgrestError  # noqa: E402
from operations.session import OperatorSessionError, open_session  # noqa: E402

SOURCE_NAME = "synthetic_fixture"
SYNTHETIC_IDENTITY = "synthetic-fixture"
NAME_PREFIX = "[SYNTHETIC]"
RESERVED_SUFFIX = ".example.com"
CANDIDATES = "discovery_candidates"
EVIDENCE = "discovery_candidate_evidence"
COMPANIES = "companies"


def create(session, name: str, domain: str, segment: str, excerpt: str | None) -> dict:
    if not domain.endswith(RESERVED_SUFFIX):
        raise SystemExit(f"refused: fixtures must use a reserved {RESERVED_SUFFIX} domain")
    full_name = name if name.startswith(NAME_PREFIX) else f"{NAME_PREFIX} {name}"
    payload = {
        "name": full_name,
        "normalized_name": normalize_name(full_name),
        "domain": domain,
        "source_name": SOURCE_NAME,
        "source_url": f"https://{domain}",
        "signals": {"segment": segment, "fixture": True},
        "status": "pending_review",
        "batch_key": f"synthetic-fixture-{datetime.now(timezone.utc):%Y%m%d%H%M%S}",
        # Not the discovery agent's identity: this row was created by a human to exercise
        # Gate 1, and the registration payload copies this value into the company record.
        "discovered_by_agent_identity": SYNTHETIC_IDENTITY,
    }
    rows = session.client.post(CANDIDATES, payload)
    if not rows:
        raise SystemExit("the candidate was not created and no row was returned")
    candidate = rows[0]
    if excerpt:
        session.client.post(
            EVIDENCE,
            [
                {
                    "candidate_id": candidate["id"],
                    "url": f"https://{domain}/about",
                    "excerpt": excerpt,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "provider": "synthetic_fixture",
                }
            ],
        )
    return candidate


def remove(session, candidate_id: str) -> dict:
    rows = session.client.get(
        CANDIDATES,
        {
            "select": "id,domain,source_name,converted_company_id",
            "id": f"eq.{candidate_id}",
        },
    )
    if not rows:
        raise SystemExit(f"candidate {candidate_id} is not visible")
    row = rows[0]
    if not str(row["domain"]).endswith(RESERVED_SUFFIX) or row["source_name"] != SOURCE_NAME:
        raise SystemExit(
            "refused: this row is not a synthetic fixture (domain must end .example.com and "
            f"source_name must be {SOURCE_NAME!r}); nothing was deleted"
        )
    company_id = row.get("converted_company_id")
    if company_id:
        companies = session.client.get(
            COMPANIES,
            {"select": "id,name,discovery_source,discovery_details", "id": f"eq.{company_id}"},
        )
        if not companies:
            raise SystemExit(f"company {company_id} is not visible; nothing was deleted")
        company = companies[0]
        details = company.get("discovery_details") or {}
        linked = str(details.get("candidate_id") or "")
        if company.get("discovery_source") != "discovery_candidate" or linked != candidate_id:
            raise SystemExit(
                f"refused: company {company_id} was not created from this candidate "
                f"(source={company.get('discovery_source')!r}, candidate_id={linked!r}); "
                "nothing was deleted"
            )
        # Company first: the candidate references it ON DELETE SET NULL, so deleting the
        # candidate first would leave an orphaned company with no record of why it exists.
        session.client.delete(COMPANIES, {"id": f"eq.{company_id}"})

    session.client.delete(CANDIDATES, {"id": f"eq.{candidate_id}"})

    still_candidates = session.client.get(CANDIDATES, {"select": "id", "id": f"eq.{candidate_id}"})
    still_companies = (
        session.client.get(COMPANIES, {"select": "id", "id": f"eq.{company_id}"}) if company_id else []
    )
    if still_candidates or still_companies:
        raise SystemExit(
            "deletion did not complete: candidate visible after delete="
            f"{bool(still_candidates)}, company visible after delete={bool(still_companies)}"
        )
    return {"candidate": candidate_id, "company": company_id, "verified_absent": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or remove a synthetic Gate 1 candidate")
    parser.add_argument("--name", default="Gate 1 Fixture Studio")
    parser.add_argument("--domain", default=None, help=f"must end {RESERVED_SUFFIX}")
    parser.add_argument("--segment", default="digital_agency")
    parser.add_argument("--excerpt", default="Synthetic evidence created for Gate 1 verification.")
    parser.add_argument("--remove", metavar="CANDIDATE_ID", default=None)
    args = parser.parse_args(argv)

    try:
        session = open_session()
    except OperatorSessionError as exc:
        print(f"cannot open an operator session: {exc}", file=sys.stderr)
        return 2

    try:
        if args.remove:
            print(remove(session, args.remove))
            return 0
        domain = args.domain or f"gate1-fixture-{datetime.now(timezone.utc):%Y%m%d%H%M%S}{RESERVED_SUFFIX}"
        candidate = create(session, args.name, domain, args.segment, args.excerpt)
        print(f"candidate_id={candidate['id']}")
        print(f"domain={candidate['domain']}")
        return 0
    except PostgrestError as exc:
        print(f"database refused the action: {exc.code} {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
