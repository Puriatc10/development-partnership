# Stage 4 — the platform does its own research

**Question this stage answers:** can Eryndex research a company completely, from public evidence,
with no vendor, no API key and no invented claims?

**Answer:** yes for everything a studio publishes in HTML. The engine now reads a company's own
site, extracts quotable evidence, and refuses to emit a claim whose excerpt is not verbatim on the
page it cites. On three real agency sites it produced 25, 26 and 11 findings — every one cited.

## What was built

| Module | Responsibility |
|---|---|
| `research/research_types.py` | shared types + the grounding rule (`excerpt_is_grounded`, `grounded`) |
| `research/http.py` | `Fetcher`: robots.txt (longest-match Allow/Disallow, Crawl-delay), per-host delay, byte cap, redirects, typed failure reasons, optional on-disk cache |
| `research/extract.py` | stdlib HTML → `PageContent`: title, description, chrome-stripped text, headings, absolute links, JSON-LD, emails (incl. `mailto:`) and phones (from `tel:`) |
| `research/crawl.py` | page plan by business question (about → services → work → contact → team → pricing → blog), sitemap + home-link discovery, host boundary, failure tolerance, portfolio-index expansion into case studies |
| `research/findings.py` | evidence → findings across the deployed IPP categories, with per-kind caps and information gaps for what the site does not say |
| `research/firstparty.py` | the provider the executor already knows: `produce(company) -> ResearchPayload` |
| `scripts/research_quality.py` | measures the engine against public sites and writes JSON evidence |

The rule everything hangs on: **a finding exists only if its excerpt appears verbatim in the page it
cites**. `make_finding` is the single door, and the provider re-checks every draft against the pages
it actually fetched before the payload is built. A missing excerpt is a dropped finding, not a
shorter one.

## Live results

| Site | Pages | Findings | Verified | Cited | Confidence |
|---|---|---|---|---|---|
| ramotion.com | 9 | 25 | 24 | 25 | medium |
| hellomonday.com | 8 | 26 | 22 | 26 | medium |
| bluegg.co.uk | 5 | 11 | 8 | 11 | medium |

Evidence: `reports/eryndex/evidence/research-live.json`.

Db round trip, live: company `b96df034…` (ramotion.com) → 25 findings persisted with `evidence_url`
and `evidence_excerpt` on all 25 → qualifier persisted scorecard `385a6fa0…` → **12.00, disqualified**,
with `dimension_evidence` naming the findings behind each partial score
(`customer_flow 5/20` from the two case-study clients, `technical_fit 4/15` from a Webflow mention).

## Reading that score honestly

12/100 is not a failure of the engine. Ramotion publishes no backend work, no retainer or recurring
model, and no headcount — the three dimensions a development-partnership rubric is built on. The
engine collected what exists and the rubric scored what it collected. Manufacturing the missing
points would require inventing evidence, which is exactly what the grounding rule forbids.

The useful signal is `dimension_evidence`: a human can now see *why* a score is low, per dimension,
with the quoted sentence that justifies it. That is what makes a "disqualified" verdict reviewable
rather than mysterious.

## Precision work this stage required

Real output exposed four classes of shaped-like-evidence that were not evidence. Each was fixed and
pinned with a test:

| Symptom | Cause | Fix |
|---|---|---|
| "Names Design Assets Brand Templates as a client" | hero copy read through the `trusted by` trigger | generic-marketing blocklist in `_looks_like_name` |
| Testimonials and case studies recorded as "service offerings" | one cue matched past-tense delivery sentences | `_PORTFOLIO_CUE`; services skip them, portfolio names the client instead |
| Phone numbers glued to postal codes | one greedy regex over prose | phones come from `tel:` links and must be printed on the page to be quotable |
| "Names the role Ceo" | `str.title()` on acronyms | `_display_role` keeps CEO/CTO/CFO… upper-case |

Also added: portfolio-index expansion (a `/work` page now leads to its case studies within the same
fetch budget), which is where clients actually appear.

## Limits, stated plainly

- **No JavaScript.** Pages rendered client-side yield empty text and are skipped, not faked.
- **Rule-based extraction, not a model.** It reads what is written; it will not infer that a studio
  needs backend help from a screenshot.
- **Case studies are followed one level.** A portfolio index leads to its category pages; individual
  project pages below those are not yet crawled.
- **English-centric cues.** Non-English sites will yield thin findings.
- **Caps are deliberate:** 40 findings total, per-kind caps, 8–10 pages by default.

## What "perfect" would still require

1. Render JS (Playwright) for the growing share of studios whose copy is client-rendered.
2. Follow each case study to its own page and extract the client, the outcome and the stack per project.
3. Read logo alt text and JSON-LD `Organization`/`Service`/`Employee` as first-class evidence.
4. Multi-language cue sets (at least German and French, given the target market).

None of these is blocked on you; each is a bounded piece of work on top of the current interfaces.
