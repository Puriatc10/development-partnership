"""Live quality check of the first-party research engine, on public sites.

Reads only public pages, obeys robots.txt, never contacts anyone. Writes a JSON evidence file so
the numbers in the report come from a run, not from a claim.

Run: python scripts/research_quality.py [--limit 3] [--out reports/eryndex/evidence/research-live.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.firstparty import FirstPartyResearchProvider  # noqa: E402
from research.research_types import excerpt_is_grounded  # noqa: E402

#: Public studio/agency sites that publish their services. Nothing here is a prospect or a
#: customer; the run answers "does the engine read a site correctly", not "who should we call".
CANDIDATES = [
    "https://www.ramotion.com",
    "https://www.hellomonday.com",
    "https://www.bluegg.co.uk",
    "https://www.uncommon.studio",
    "https://www.webfactoryltd.com",
    "https://www.thedesigncouncil.co.uk",
]


def measure(site: str, provider: FirstPartyResearchProvider) -> dict:
    started = time.time()
    company = {"name": site.split("//", 1)[-1], "website_url": site, "primary_domain": site.split("//", 1)[-1]}
    try:
        payload = provider.produce(company)
    except Exception as exc:  # noqa: BLE001 - the point is to record why a site failed
        return {"site": site, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    categories = Counter(f.category for f in payload.findings)
    types = Counter(f.finding_type for f in payload.findings)
    grounded = sum(
        1
        for f in payload.findings
        if f.evidence_url and f.evidence_excerpt and excerpt_is_grounded(f.evidence_excerpt, f.evidence_excerpt)
    )
    return {
        "site": site,
        "ok": True,
        "seconds": round(time.time() - started, 1),
        "confidence": payload.overall_confidence,
        "pages": len(payload.sources),
        "page_urls": [s.url for s in payload.sources],
        "findings": len(payload.findings),
        "verified_facts": types.get("verified_fact", 0),
        "with_citation": sum(1 for f in payload.findings if f.evidence_url),
        "categories": dict(categories),
        "summary": payload.summary[:400],
        "gaps": (payload.gaps_and_unknowns or "")[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--out", default="reports/eryndex/evidence/research-live.json")
    args = parser.parse_args()

    provider = FirstPartyResearchProvider.from_env()
    results = []
    for site in CANDIDATES:
        if sum(1 for r in results if r["ok"]) >= args.limit:
            break
        print(f"… {site}", flush=True)
        results.append(measure(site, provider))

    payload = {"provider": provider.name, "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    for row in results:
        if row["ok"]:
            print(
                f"{row['site']}: {row['pages']} pages, {row['findings']} findings "
                f"({row['verified_facts']} verified), {row['with_citation']} cited, "
                f"confidence={row['confidence']}, {row['seconds']}s"
            )
        else:
            print(f"{row['site']}: FAILED — {row['error']}")
    print(f"evidence -> {out}")
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
