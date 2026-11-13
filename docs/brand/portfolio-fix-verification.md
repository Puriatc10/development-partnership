# Portfolio Fix Verification — Phase F

**Date:** 2026-11-13
**Source inspected:** `arshiataheri851-ops/portfolio` → `eryndex/index.html` (local clone at `%TEMP%\portfolio-clone`)
**Deliverable:** `docs/brand/portfolio-fixes.patch` — a unified diff, **not applied, not published**. Applying it requires owner review (Directive 02 §17: public deployment needs approval).

---

## 1. Fixes included (4 hunks, 7 changed lines)

| # | Defect | Verified before | Fix | How it was verified |
|---|---|---|---|---|
| F-1 | FarsiPulse source link points to a path that does not exist | `.../tree/main/projects/persian-sentiment-api` — repo root contains `persian-sentiment-api/`, no `projects/` directory | link → `.../tree/main/persian-sentiment-api` | directory listing of the clone; the old path is not in `ls` |
| F-2 | Internal editorial note rendered in public HTML | `<p class="resume-disclaimer">… should be checked and approved before external publication …</p>` at line 79 | element removed | `'resume-disclaimer' not in fixed` |
| F-3 | Founder presented a third time under a placeholder name, with a public "pending approval" marker | `<h3>Engineer Taher</h3>` + `<span>Profile details pending approval</span>` at line 86 | `<h3>Technical Co-founder</h3>` + `<span>Technical leadership profile</span>` | both old strings absent from the fixed file |
| F-4 | Literal `\n\n` renders as visible text between the resume and team sections | the source contains a literal `\\n` between `</section>` and `<section class="team"` | replaced with real newlines | string search before/after |

The patch is a pure text edit: no CSS, no design tokens, no layout changed. Brand identity is untouched.

## 2. What I deliberately did **not** change

- **The real name of the technical co-founder.** "Technical Co-founder" is a truthful role label, not a name. Naming them is an owner decision (BLK-05) — inventing a name would be worse than the placeholder.
- **The professional history block** (Nobitex/Digikala/Snapp, "6 years", "10–15 team size led", F1 ≈ 88%). These are supplied-CV claims. The page already discloses their provenance *in the removed disclaimer*; removing that note without replacing it with a verified-claims review would make the page **less** honest, not more. Recommended replacement (owner decision): publish only the claims the owner can evidence on request, and drop the rest.
- **Service and capability claims** ("engineering what's next", delivery approach). These describe intent, not delivered work, and are consistent with a pre-operational partnership business — no change needed.
- **The six "work" cards.** They are explicitly labelled demonstrators, not client work, and the page now says so on one line. Keep.

## 3. How to apply (owner)

```bash
cd <portfolio repo>
git checkout -b fix/eryndex-claims-and-links
git apply /path/to/docs/brand/portfolio-fixes.patch
# preview locally, then open a PR — do not push to main (Pages would redeploy)
```

Verify: the FarsiPulse SOURCE link resolves to a real directory; no "pending approval" or "checked and approved" text is visible; the team section renders as a new block rather than inline `\n\n`.

## 4. Remaining claim work (not in this patch)

`docs/brand/eryndex-portfolio-analysis.md` lists **33** unsafe claims with safe rewrites. This patch fixes the four that are objectively broken (dead link, leaked editorial note, contradictory identity, rendering defect). The remaining 29 are judgement calls about biography and performance figures and need the owner's evidence, not an engineer's rewrite.

## 5. Publish status

**Not published.** No push, no Pages rebuild, no DNS change. The patch exists in the Eryndex repo so the fix is reviewed like code rather than typed into a live page.
