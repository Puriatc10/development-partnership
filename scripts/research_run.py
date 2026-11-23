"""Run one research execution for a registered company and print the result as JSON.

Two ways in, matching the two ways the platform is operated:

* **Operator session (default).** Reads ERYNDEX_DB_URL / ERYNDEX_JWT_SECRET /
  OPERATOR_EMAIL / OPERATOR_PASSWORD, signs in, and refuses any identity that is not a human
  operator — the executor must never be the thing that decides a human is a human.
* **Supplied DSN (`--dsn`).** Builds an RLS-bound client directly, with claims from the flags.
  This is the subagent path: ``--user-role agent --subagent-type researcher --identity
  hermes-researcher-v1 --task-id ...``. The DSN must be an ordinary ``authenticated`` role
  connection: nothing here escalates it, and the policies still decide every row. Use it only
  from a trusted local process, because the claims are asserted locally rather than signed.

The output is one JSON object on stdout, so a caller never has to parse prose. A 42501 refusal
carries ``missing_claim`` naming the exact claim or task scope the policy needed.

The object now also carries ``run``: the run summary (run id, trace id, event tally, summed
counts, retries, failures, durations) of what this invocation actually emitted. Step events
go to stderr as one JSON line each, so stdout stays a single parseable object::

    {"counts":{},"duration_ms":3.1,"event":"research.started","outcome":"ok",
     "run_id":"...","stage":"research","trace_id":"..."}

``--run-id`` is how a retry is followed. Pass the ``run.run_id`` printed by an earlier attempt
and this attempt joins that trace: it gets its own ``run_id`` (so "which run" stays
unambiguous) and shares the ``trace_id`` (so "which work" does not).

    python scripts/research_run.py --company-id <uuid> --provider fixture --task-id <uuid>
    python scripts/research_run.py --company-id <uuid> --provider firstparty --task-id <uuid>
    python scripts/research_run.py --company-id <uuid> --dsn "$ERYNDEX_DB_URL" \\
        --identity hermes-researcher-v1 --task-id <uuid>
    python scripts/research_run.py --company-id <uuid> --task-id <uuid> --run-id <first run_id>

``--provider firstparty`` reads the company's own site. Its page budget, user agent and
timeouts come from ``RESEARCH_MAX_PAGES`` / ``RESEARCH_USER_AGENT`` / ``RESEARCH_TIMEOUT_S`` /
``RESEARCH_DELAY_S`` (see ``research/firstparty.py``), and the output carries
``research_source``: how many pages were read, which failed and why. A partial crawl is reported
as partial — this script never prints a green result for a run that read nothing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.environment import DatabaseConfig, assert_database_environment  # noqa: E402
from contracts.errors import EnvironmentGuardError  # noqa: E402
from integrations.postgres.client import PostgresClient  # noqa: E402
from observability.run import LOGGER_NAME, RunContext  # noqa: E402
from observability.redaction import redact  # noqa: E402
from research.executor import (  # noqa: E402
    HUMAN_ROLES,
    FixtureResearchProvider,
    ResearchError,
    ResearchWriteDenied,
    execute_research,
    provider_from_integration,
)
from research.firstparty import FirstPartyResearchProvider  # noqa: E402

DEFAULT_AGENT_IDENTITY = "hermes-researcher-v1"
DEFAULT_RESEARCHER_URL = "http://127.0.0.1:8000"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute research for one registered company.")
    parser.add_argument("--company-id", required=True, help="companies.id to research")
    parser.add_argument(
        "--provider",
        choices=("fixture", "firstparty", "gpt_researcher"),
        default="fixture",
        help="fixture writes a clearly-labelled synthetic payload; firstparty reads the "
        "company's own site (no vendor, no key, env RESEARCH_MAX_PAGES etc.); gpt_researcher "
        "calls the self-hosted adapter (needs --base-url)",
    )
    parser.add_argument("--task-id", default=None, help="tasks.id to move pending -> in_progress -> completed")
    parser.add_argument("--report-version", type=int, default=None, help="pin the report version")
    parser.add_argument(
        "--dsn",
        default=None,
        help="use this connection string instead of signing in an operator (the subagent path)",
    )
    parser.add_argument("--identity", default=None, help="agent_id (with --dsn) or operator override")
    parser.add_argument(
        "--user-role",
        default=None,
        choices=sorted(HUMAN_ROLES | {"agent"}),
        help="with --dsn: 'agent' (default) or a human role",
    )
    parser.add_argument("--subagent-type", default="researcher", help="with --dsn and --user-role agent")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ERYNDEX_RESEARCHER_URL", DEFAULT_RESEARCHER_URL),
        help="gpt_researcher base URL (env ERYNDEX_RESEARCHER_URL)",
    )
    parser.add_argument(
        "--api-token",
        default=os.environ.get("ERYNDEX_RESEARCHER_TOKEN", ""),
        help="optional bearer token for the researcher service (env ERYNDEX_RESEARCHER_TOKEN)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="join an earlier attempt's trace: pass the run.run_id a previous run printed. This "
        "run still gets its own run_id, so the two attempts stay distinguishable",
    )
    return parser


def configure_logging() -> None:
    """Send run events to stderr as bare JSON lines; stdout stays one JSON object.

    Configured here rather than at import so importing this module (a test, a
    REPL) does not install a global handler. ``propagate = False`` keeps a host
    application's root handler from prefixing our JSON with a second format.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if any(getattr(handler, "_eryndex_run_handler", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._eryndex_run_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def build_provider(args: argparse.Namespace):
    if args.provider == "fixture":
        return FixtureResearchProvider()
    if args.provider == "firstparty":
        # Construction only reads RESEARCH_* variables; the network is touched inside produce().
        return FirstPartyResearchProvider.from_env()
    return provider_from_integration("gpt_researcher", base_url=args.base_url, api_token=args.api_token)


def provider_run_summary(provider: object) -> dict | None:
    """How the last provider run went: pages read, pages failed, why — or ``None``.

    The payload says what was concluded; this says how much evidence it rests on. It is read
    from the provider after the fact rather than threaded through ``execute_research``, because
    the executor's contract (payload in, persisted research out) must not grow a statistics
    channel for one provider's benefit. Duck-typed on ``last_run`` so every other provider —
    fixture, integration — simply reports nothing.
    """
    stats = getattr(provider, "last_run", None)
    as_dict = getattr(stats, "as_dict", None)
    if not callable(as_dict):
        return None
    try:
        summary = as_dict()
    except (TypeError, ValueError):  # a provider's private shape is not our contract
        return None
    return dict(summary) if isinstance(summary, Mapping) else None


def record_source_stats(payload: dict, run: RunContext, provider: object) -> dict:
    """Put the page count and failures into both the payload and the run summary.

    Counted as an event as well as attached to the JSON because the summary's ``counts`` are
    summed from events: a caller that only kept the log would otherwise not know a run read
    three pages and failed five.
    """
    stats = provider_run_summary(provider)
    if stats is None:
        return payload
    payload["research_source"] = stats
    run.structured_event(
        "research.pages.read",
        stage="research",
        counts={
            "pages_read": _as_int(stats.get("pages_read")),
            "pages_failed": _as_int(stats.get("pages_failed")),
        },
        start_url=stats.get("start_url"),
        partial=bool(stats.get("partial")),
        failure_reasons=list(stats.get("failures") or ()),
    )
    return payload


def _as_int(value: object) -> int:
    """``counts`` must stay integers; anything else is reported as zero rather than coerced."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def build_claims(identity: str, role: str, subagent_type: str) -> dict:
    """Claims the database reads: ``app_metadata.user_role`` etc. (never a password)."""
    if role in HUMAN_ROLES:
        return {
            "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator:{identity}")),
            "role": "authenticated",
            "app_metadata": {"user_role": role},
        }
    return {
        "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent:{identity}")),
        "role": "authenticated",
        "app_metadata": {
            "user_role": "agent",
            "subagent_type": subagent_type,
            "agent_id": identity,
        },
    }


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _error_payload(exc: Exception) -> dict:
    payload: dict = {
        "status": "error",
        "error": type(exc).__name__,
        # The message is the only diagnosis available and it can carry a DSN or a
        # provider URL with a key in it, so it is scrubbed before it is printed.
        # The event's copy of the error is capped shorter (see the emitter); this
        # one keeps the whole explanation, because it is the operator's answer.
        "message": redact(str(exc), limit=2000),
    }
    if isinstance(exc, ResearchWriteDenied):
        payload["missing_claim"] = exc.missing_claim
        payload["table"] = exc.table
        payload["sqlstate"] = exc.code
    elif isinstance(exc, ResearchError) and exc.code:
        payload["sqlstate"] = exc.code
    return payload


def _with_run(payload: dict, run: RunContext) -> dict:
    """Attach the run summary to an output object (success or failure alike)."""
    payload["run"] = run.run_summary()
    return payload


def _fail(run: RunContext, exc: Exception, *, stage: str) -> dict:
    """Record the failure as an event, then return the redacted error payload.

    Event first: the summary embedded in the payload has to include it, otherwise
    a failed run reports ``failures: 0``.
    """
    run.structured_event(
        "research.failed",
        stage=stage,
        outcome="failed",
        counts={},
        error=redact(f"{type(exc).__name__}: {exc}"),
    )
    return _with_run(_error_payload(exc), run)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    run = RunContext.begin(stage="research", trace_id=args.run_id)

    try:
        config = DatabaseConfig.load()
        if args.dsn:
            config = replace(config, dsn=args.dsn)
        assert_database_environment(config)
    except (EnvironmentGuardError, ValueError) as exc:
        _emit(_fail(run, exc, stage="config"))
        return 2

    try:
        provider = build_provider(args)
    except ImportError as exc:
        # A provider whose sibling module is missing is a packaging problem, and it must read
        # like one: a JSON error, not a traceback from inside a research run.
        _emit(_fail(run, exc, stage="provider"))
        return 2
    except (ResearchError, ValueError) as exc:
        _emit(_fail(run, exc, stage="provider"))
        return 2

    session_client = None
    client = None
    try:
        if args.dsn:
            role = args.user_role or "agent"
            identity = (
                args.identity
                or os.environ.get("ERYNDEX_RESEARCH_AGENT_ID")
                or os.environ.get("RESEARCHER_AGENT_ID")
                or DEFAULT_AGENT_IDENTITY
            )
            if role in HUMAN_ROLES and args.identity is None:
                identity = os.environ.get("OPERATOR_EMAIL") or "operator"
            claims = build_claims(identity, role, args.subagent_type)
            client = PostgresClient(config.dsn, role="authenticated", claims=claims)
            identity = str(claims["app_metadata"].get("agent_id") or claims["sub"])
        else:
            from operations.session import OperatorSessionError, open_session

            try:
                session = open_session()
            except OperatorSessionError as exc:
                _emit(_fail(run, exc, stage="session"))
                return 2
            session_client = client = session.client
            identity = args.identity or session.user_id

        result = execute_research(
            client,
            args.company_id,
            provider,
            task_id=args.task_id,
            report_version=args.report_version,
            identity=identity,
            run=run,
        )
        payload = result.to_dict()
        payload["database"] = config.database
        payload["environment"] = config.env
        record_source_stats(payload, run, provider)
        _emit(_with_run(payload, run))
        return 0
    except ResearchError as exc:
        _emit(_fail(run, exc, stage="research"))
        return 1
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not crash
        _emit(_fail(run, exc, stage="research"))
        return 1
    finally:
        for open_client in (client, session_client):
            if open_client is not None:
                try:
                    open_client.close()
                except Exception:  # noqa: BLE001 - closing must never mask the result
                    pass


if __name__ == "__main__":
    sys.exit(main())
