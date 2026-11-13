"""One controlled Discovery -> brief -> IPP scoring cycle, offline.

Development utility, not a runtime path. It exercises the whole chain without a
database, a provider, or a network call:

    fixture candidates -> discovery (normalize/dedupe/stage to a JSONL outbox)
        -> research brief aimed at IPP anchors
        -> evidence contract validation
        -> deterministic IPP scorecard (draft, human review required)

Output is written to reports/eryndex/evidence/discovery-cycle-sample.json so the
run is auditable. Nothing is persisted to Supabase; nothing is researched.

    python scripts/discovery_cycle.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from discovery.ports import JsonFileSource, JsonlOutboxSink  # noqa: E402
from discovery.service import DiscoveryRequest, run_discovery  # noqa: E402
from intelligence.bridge import score_candidate  # noqa: E402
from intelligence.briefs import brief_questions, build_research_brief  # noqa: E402

# Read-only reference to the one company that exists in Development. Used only to
# demonstrate duplicate detection; Refokus itself is never researched or modified.
EXISTING_COMPANIES = [
    {
        "id": "1afd5547-ce67-45d3-8aaa-35619514f835",
        "name": "Refokus",
        "normalized_name": "refokus",
        "primary_domain": "refokus.com",
    }
]

# Evidence for the staged candidate, as a researcher would return it against the
# brief. Each row maps to a schema finding_category_type value.
SAMPLE_EVIDENCE = [
    {
        "finding_id": "ev-work",
        "statement": "Northwind Studio publishes four commercial client case studies for named brands.",
        "category": "client_roster",
        "finding_type": "verified_fact",
        "confidence": "high",
        "evidence_url": "https://northwind.studio/work",
        "evidence_excerpt": "Selected work for Meridian, Arqitel, BCG and Yahoo!.",
    },
    {
        "finding_id": "ev-stack",
        "statement": "The studio builds Webflow sites with custom JavaScript and external API integrations.",
        "category": "technology_stack",
        "finding_type": "verified_fact",
        "confidence": "high",
        "evidence_url": "https://northwind.studio/about",
    },
    {
        "finding_id": "ev-team",
        "statement": "The team page lists 18 designers and engineers led by the founder.",
        "category": "team_structure",
        "finding_type": "verified_fact",
        "confidence": "medium",
        "evidence_url": "https://northwind.studio/team",
    },
    {
        "finding_id": "ev-gap",
        "statement": "No public evidence of post-launch support retainers or subcontracting policy.",
        "category": "business_model",
        "finding_type": "information_gap_tbd",
        "confidence": "unverified",
        "evidence_url": "",
    },
]


def main() -> int:
    request = DiscoveryRequest(
        segments=["webflow_studio"],
        sources=["webflow_experts", "clutch", "dribbble"],
        limit=10,
        campaign_id="sample-campaign-001",
    )
    source = JsonFileSource(REPO / "discovery" / "fixtures" / "sample_candidates.json", name="fixture")
    outbox = REPO / "backups" / "discovery-outbox.jsonl"
    outcome = run_discovery(request, source, JsonlOutboxSink(outbox), existing_companies=EXISTING_COMPANIES)

    print("== discovery ==")
    for key, value in outcome.stats.items():
        print(f"  {key}: {value}")
    print(f"  idempotency_key: {outcome.idempotency_key}")
    for conflict in outcome.conflicts:
        print(f"  conflict: {conflict['name']} ({conflict['domain']})")
    for match in outcome.existing_matches:
        print(f"  existing: {match['name']} -> {match['matched_company_id']}")

    if not outcome.accepted:
        print("no candidate accepted; nothing to score")
        return 1

    candidate = dict(outcome.accepted[0])
    brief = build_research_brief(candidate)
    questions = brief_questions(brief)
    card = score_candidate(candidate, SAMPLE_EVIDENCE)

    print("\n== brief ==")
    print(f"  dimensions: {len(brief['targets'])}  questions: {len(questions)}")
    print(f"  priority: {', '.join(brief['priority_dimensions'])}")

    print("\n== scorecard ==")
    print(f"  total: {card.total_score}/100  tier: {card.tier}  confidence: {card.overall_confidence}")
    print(f"  coverage: {card.evidence_coverage}  insufficient: {card.insufficient_evidence}")
    print(f"  needs_human_review: {card.needs_human_review}  review_status: {card.review_status}")

    artifact = REPO / "reports" / "eryndex" / "evidence" / "discovery-cycle-sample.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "discovery": outcome.to_dict(),
                "brief": brief,
                "brief_questions": questions,
                "evidence": SAMPLE_EVIDENCE,
                "scorecard": card.as_dict(),
                "persistence": "none — staging sink was a local JSONL outbox; nothing written to Supabase",
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {artifact.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
