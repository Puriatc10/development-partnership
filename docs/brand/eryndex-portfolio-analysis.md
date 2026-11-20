# Eryndex — Portfolio Reference Site: Design System, Content Inventory, Claims Audit, and IA Proposal

**Subject:** `https://arshiataheri851-ops.github.io/portfolio/eryndex/`
**Source:** `github.com/arshiataheri851-ops/portfolio` → `eryndex/` (`index.html`, `assets/style.css`, `assets/script.js`)
**Status of analysed build:** pre-operational marketing page, brand wordmark **ERYNDEXCO**, contact `eryndexco@gmail.com`, `eryndex.net` not resolving.
**Method (all values in this document are read from source or measured, not estimated):**

| Evidence step | What was done | Result |
|---|---|---|
| Source read | `eryndex/index.html` (101 lines, 17,741 B), `assets/style.css` (13,318 B, 6 physical lines — minified), `assets/script.js` (673 B) | Complete; every token below traced to a declaration |
| Live vs. repo | `curl` of live page, `diff` against repo file (CRLF-normalised) | **Byte-identical** — repo `main` is what visitors see |
| Computed styles | Chrome via CDP, viewport 1440×900 | Verified cascade results, including three places where the CSS author's intent is overridden (see §1.7) |
| Mobile | Viewports 390×844 and 320×720 | No horizontal overflow at 390; **h1 clipping at 320** (see §1.7) |
| Font load | `document.fonts.check()` per family/weight | DM Sans 400/700 and Space Grotesk 500/600/700 load; DM Sans 500 and Space Grotesk 400 are requested but never used |
| Link audit | HTTP status for all 26 anchors' outbound targets | **1 broken source link (404)**; see §3.2 |
| Contrast | WCAG 2.1 relative-luminance computation on 15 text/background pairs | All pass AA (4.83–17.26); failures are size-based, not contrast-based |

---

## 1. Extracted design system

### 1.1 Colour — primitives (verbatim from `:root`)

| Token | Value | RGB | Role as actually used |
|---|---|---|---|
| `--bg` | `#090d18` | 9, 13, 24 | Page base. Near-black navy, not pure black |
| `--panel` | `#101727` | 16, 23, 39 | Card fills (`.work-card`, `.person-card`, `.resume-stats div`) |
| `--panel2` | `#121d30` | 18, 29, 48 | **Declared, never referenced** (dead token) |
| `--text` | `#edf2fa` | 237, 242, 250 | Primary text — slightly cool off-white, never `#fff` |
| `--muted` | `#9ba9bd` | 155, 169, 189 | Body/secondary copy |
| `--line` | `rgba(180,200,230,.14)` | — | Every hairline: borders, rules, card strokes, tag outlines |
| `--mint` | `#8df0d0` | 141, 240, 208 | The single accent: labels, numbers, links, CTA fill, logo mark |
| `--blue` | `#8baeff` | 139, 174, 255 | **Declared, never referenced** (dead token) |
| `--font` | `'DM Sans',sans-serif` | — | Body |
| `--display` | `'Space Grotesk',sans-serif` | — | All headings, numbers, logo, marks |

**Hard-coded surfaces (not tokenised — this is the main system debt):**

| Value | Where |
|---|---|
| `#101b2b` | `.work-art` base plate (all six project artworks) |
| `#101b28` | `.art-route` radial centre-adjacent stop |
| `#111827` | `.art-signal` radial stop |
| `#24415a` → `#101b2b` | `.art-rag` radial gradient |
| `#26384c` → `#101b2b` | `.art-relay` radial gradient |
| `#19283a` + border `#5b7d9b55` | `.rag-window` mock chrome inside the AtlasRAG art |
| `#0d1725` | `.contact` section background (one step lighter than `--bg`) |
| `#0b1220` | Mobile nav dropdown panel |
| `rgba(9,13,24,.86)` + `blur(18px)` | Sticky header glass |
| `rgba(142,170,210,.045)` @ `70px 70px` | Hero grid lines (two stacked `linear-gradient`s) |
| `radial-gradient(circle, rgba(73,132,180,.2), transparent 68%)` | `.hero-glow`, 600×600, right 2% / top 4% |
| `radial-gradient(ellipse at 70% 50%, #4c8e8930, transparent 65%)` | `.contact-glow`, 60% width |

**Text shades outside the token set (11 distinct greys — worth collapsing):**

`#aab8ca` (hero lead, tags) · `#b8c9d9` (eyebrow) · `#bac5d5` (nav) · `#d0d9e6` (text-link, superseded) · `#718198` (h2 span) · `#718096` (footer) · `#748399` (fine print) · `#72839a` (team note, person-links) · `#7d91a8` (art caption) · `#8191a7` (resume dates) · `#8493a8` (resume disclaimer) · `#d5deea` (resume-mini value).

**Accent drift (a real defect, not a design choice):** `--mint` is `#8df0d0`, but a later CSS block re-declares `.text-link` and `.art-route .pulse-core` with `#83e0c2`, and `.art-signal .pulse-core` with `#b9a1ff` (lavender) plus `#8df0d0` appears in 9 places. Visually near-identical, technically three accents.

**Contrast audit (WCAG 2.1, computed):**

| Pair | Ratio | Verdict |
|---|---|---|
| `--text` on `--bg` | 17.26 | AAA |
| `--text` on `--panel` | 15.91 | AAA |
| `--mint` on `--bg` | 14.33 | AAA |
| `--mint` on `--panel` | 13.21 | AAA |
| `#06120f` on `--mint` (CTA label) | 14.08 | AAA |
| `#aab8ca` on `--bg` (hero lead) | 9.63 | AAA |
| `--muted` on `--bg` | 8.13 | AAA |
| `--muted` on `--panel` | 7.50 | AAA |
| `#7d91a8` on `#101b2b` (art caption) | 5.35 | AA |
| `#748399` on `--bg` (fine print) | 5.03 | AA |
| `#72839a` on `--bg` (team note) | 5.02 | AA |
| `#718198` on `--bg` (h2 span) | 4.89 | AA |
| `#718096` on `--bg` (footer) | 4.83 | AA — lowest in system |

Palette is disciplined: one accent, two neutrals, one hairline. Contrast is uniformly compliant. The accessibility weakness is **type size**, not colour (§1.7).

### 1.2 Typography

**Loaded from Google Fonts** (`css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700`), `display=swap`, with `preconnect` to both hosts.

| Family | Weights requested | Weights actually rendered | Notes |
|---|---|---|---|
| DM Sans | 400, 500, 600, 700 | **400, 700** | 500/600 unused → wasted request |
| Space Grotesk | 400, 500, 600, 700 | **500, 600, 700** | 400 unused |

**Scale (measured, desktop 1440 wide):**

| Role | Selector | Size | Weight | Line-height | Letter-spacing | Family |
|---|---|---|---|---|---|---|
| Hero H1 | `h1` | `clamp(66px, 9vw, 124px)` → **124px** measured | 500 | **0.99** (122.76px) | **-.055em** (-6.82px) | Space Grotesk |
| Section H2 | `.intro h2, .section-head h2, .approach h2` | `clamp(36px, 5vw, 62px)` → **62px** | 500 | 1.1 | -.055em (-3.41px) | Space Grotesk |
| Contact H2 | `.contact h2` | `clamp(55px, 8vw, 95px)` → **95px** | 500 | 1.0 | -.055em (-5.23px) | Space Grotesk |
| Card title | `.work-card h3` | 27px | 500 | inherit (1.6) | -.03em | Space Grotesk |
| Person name | `.person-card h3` | 28px | 500 | inherit | -.055em | Space Grotesk |
| Service row title | `.service h3` | 23px | 500 | inherit | -.03em | Space Grotesk |
| Step title | `.step h3` | 18px | 500 | inherit | -.055em | Space Grotesk |
| Resume heading | `.resume-heading` | 21px | 500 | inherit | -.03em | Space Grotesk |
| Resume employer | `.resume-role h4` | 17px | 500 | inherit | normal | Space Grotesk |
| Stat figure | `.resume-stats b` | 23px | 500 | inherit | normal | Space Grotesk |
| Logo | `.logo` | 17px | **700** | inherit | **+.13em** | Space Grotesk |
| Hero lead | `.hero-lead` | 17px | 400 | 1.8 | normal | DM Sans |
| Body copy | `.body-copy` | 15px | 400 | 1.9 | normal | DM Sans |
| Section intro / card body | `.section-head>p`, `.service p`, `.work-card>p` | 13px | 400 | 1.8 | normal | DM Sans |
| Mid copy | `.resume-summary`, `.contact-content>p` | 14px | 400 | 1.9 | normal | DM Sans |
| Nav | `nav` | 12px | 400 | 1.6 | normal | DM Sans |
| Step body | `.step p` | 12px | 400 | 1.6 | normal | DM Sans |
| Micro-label | `.eyebrow, .section-label, .overline, .hero-bottom, .hero-aside, .work-meta, .person-top` | 10px | **700** | 1.6 | **+.18em** | DM Sans, uppercase |
| Fine print | `.fine-print` | 10px | 400 | 1.7 | normal | DM Sans |
| Skill list | `.skill-group p` | 10px | 400 | 1.8 | normal | DM Sans |
| Tags | `.tags span` | **9px** | 400 | 1.6 | +.04em | DM Sans |
| Caption | `.art-caption` | **8px** | 400 | — | **+.17em** | DM Sans |
| Card meta | `.work-meta` | **8px** | 700 | — | +.18em | DM Sans |

**Typographic rules that define the look:**

1. Display weight is **500, never 700** — the brand reads as engineered, not shouted.
2. Negative tracking scales with size: `-.055em` on display, `-.03em` on card titles, `-.08em` on the big person-letter.
3. Positive tracking only on uppercase micro-labels: `+.18em` (labels), `+.17em` (captions), `+.14em` (mock UI), `+.13em` (logo).
4. Two-tone headlines: first clause in `--text`, second clause in `#718198` (H2) or `--mint` (H1 `<em>`, contact `<em>`). The `<em>` is reset to `font-style:normal` — emphasis is colour, not italics.
5. Line-height is inverse to size: 0.99 at 124px, 1.1 at 62px, 1.6 at 16px body, 1.9 at long-form 15px.
6. `h1, h2, h3 { margin-top: 0 }` — vertical rhythm is carried entirely by section padding and explicit margins.

**Where the scale breaks:** 8px and 9px labels (`.art-caption`, `.work-meta`, `.tags span`) are below any comfortable reading size and shrink real content (category labels, technology tags) into texture. This is the system's one systemic a11y compromise. Recommended floor: 11px.

### 1.3 Spacing

There is **no spacing scale token set** — every value is literal. Full inventory of declared values (px), sorted:

`2, 3, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22, 23, 25, 26, 28, 30, 31, 32, 34, 35, 38, 45, 48, 50, 52, 55, 60, 68, 70, 76, 82, 90, 95, 110, 125, 130`

That is 42 distinct values with no ratio — a genuine debt. The *rhythm* is nevertheless consistent because it is driven by four rules:

| Rhythm rule | Desktop | ≤800px | ≤600px |
|---|---|---|---|
| Section vertical padding | **125px** | 90px | 76px |
| Section horizontal gutter | **12vw** (=172.8px @1440) | 8vw | 7vw |
| Header/footer gutter | **7vw** (=100.8px) | 6vw header / 7vw footer | 7vw |
| Header height | **82px** (nav dropdown offset uses `top:70px`) | 70px | 70px |

**Working scale recovered from the source** (values that repeat ≥2× and behave as a scale): `8, 12, 16, 20/22, 26/28, 32, 48/50, 60, 70, 125`. A 4px-based replacement would be `4, 8, 12, 16, 24, 32, 48, 64, 96, 128` — see appendix, §A.4.

### 1.4 Layout and grid

| Property | Value | Measured consequence |
|---|---|---|
| Content width | `100vw − 24vw` = **76vw**, no `max-width` | 1079px at 1440 — grows unbounded on 2560px displays |
| Section container | `.section-wrap { padding:125px 12vw; border-top:1px solid var(--line) }` | Hairline rule between every section; no section has a background except contact |
| Hero | `min-height:680px; height:min(790px, 100vh − 82px); padding:130px 12vw 70px` | 790px measured at 900px viewport |
| Intro grid | `grid-template-columns: 1fr 2.2fr; gap: 50px` | Label column 322px / copy column 708px |
| Approach grid | `1fr 2fr`, gap 50px | Label column 351px / steps 686px |
| Work grid | `repeat(2, 1fr)`, gap **22px**; `.work-feature` spans 2 columns and becomes a 2-column inner grid with `column-gap:26px` | 6 cards = feature + 1 + feature + 1 + 1 + 1; grid height 2179px |
| Team grid | `repeat(2, 1fr)`, gap 18px | Card 531×514 |
| Service row | `70px 1fr 30px`, gap 20px, `31px` vertical padding, bottom hairline | Row 172px tall |
| Step row | `60px 1fr`, gap 20px, `23px` vertical padding | Row 100px |
| Resume columns | `1.25fr .85fr`, gap 48px | — |
| Stat row | `repeat(3, 1fr)`, gap 12px | — |
| Section head | flex, `justify-content:space-between`, `align-items:end`, gap 50px, `margin-bottom:55px` | H2 left, 330px standfirst right, baseline-aligned to the H2's last line |
| Contact block | `max-width:650px; margin:35px 0 0 20%` | Inset to 20% so the glow owns the right 40% |

**Breakpoints: only three.** `800px` (stack grids, hide hero-aside, single-column feature cards), `700px` (resume columns only), `600px` (mobile nav, h1 → 68px, single-column work/team grids). **There is no tablet tier** — at 1024px the 12vw gutters plus a 2-column work grid leave cards at ~380px, which is where the 8px meta labels and 27px titles start colliding. A 1024–1100px breakpoint that reduces gutters to 8vw and drops the feature card to one column is the cheapest layout fix.

**Vertical rhythm of a section (measured sequence):** 125px top padding → section label (10px, 16px line box) → 22px gap → H2 → 24px → standfirst/body → 55px → content list → 25px → fine print → 125px bottom padding. Two deliberate pauses only (22px inside a head, 55px between head and content); everything else is 1px rules.

### 1.5 Component patterns

**A. Section label (`0N / TITLE`)** — the site's strongest signature.
`10px / 700 / +.18em / uppercase / color: var(--mint)`. Format is literally `NN / UPPERCASE TITLE`, space-slash-space: `01 / WHO WE ARE`, `02 / WHAT WE DO`, `03 / SELECTED WORK`, `04 / TECHNICAL LEADERSHIP`, `05 / THE PEOPLE`, `06 / HOW WE WORK`, `07 / START A CONVERSATION`. Seven instances, one per section, always the first child of the section. In `.intro` and `.approach` it occupies a full grid column (measured box 322×431 — it stretches to row height, which is invisible today but will bite if the label ever becomes a link).

**B. Hero** — 4 stacked layers: grid plate (70px lattice masked left→right), 600px radial glow, content column (`max-width:800px`), and three absolute ornaments: vertical `writing-mode:vertical-rl` aside at `right:7vw; top:38%` with a 60px mint hairline; a bottom index strip `01 — ENGINEERING | 02 — INTELLIGENCE | 03 — PARTNERSHIPS` at `left/right:12vw; bottom:35px`; and the 7px mint pulse dot with `box-shadow:0 0 18px var(--mint)` inside the eyebrow.

**C. Logo lockup** — 24×24 three-bar glyph (bars 5px wide, `border-radius:4px`, `skew(-25deg)`, heights 12/20/16, mint) + wordmark at `700 17px Space Grotesk, +.13em`, with the terminal period in mint. Pure CSS, no asset — reusable at any size, but currently duplicated inline in header and footer.

**D. Numbered content row** (`.service`, used 4×) — 3-column grid: mint number (`500 13px Space Grotesk`), title 23px + 13px description + tag row, mint `↗` glyph. Rows separated only by `1px` hairlines, `border-top` on the list container. No hover state at all — the rows read as a document, not a menu. This is the pattern that should be extended to engagements and standards.

**E. Project card** (`.work-card`) — `background:var(--panel)`, `1px var(--line)`, `padding:14px` (deliberately tight so the art plate looks inset). Hover: `border-color:#8df0d05c; transform:translateY(-4px)`, `transition:.25s`. Anatomy top→bottom: art plate (`height:230px`, feature variant 290px with `grid-row:span 5`) → `.work-meta` (8px, left uppercase category, right mint `SOURCE ↗`, `margin:18px 0 12px`) → title 27px → description (`min-height:68px` to align cards in a row) → tags → mint text-link.

**F. Art plate system** — six distinct CSS-only artworks, all on `#101b2b`, each with an 8px caption bottom-left:
- `.art-route` / `.art-signal`: radial gradient + 90px outlined circle (`#83e0c2` / `#b9a1ff`) holding a 2-letter monogram, `margin:52px auto 0`.
- `.art-pulse`: concentric rings via stacked `box-shadow` (`0 0 0 28px #8df0d00b, 0 0 0 58px #8df0d008`) around a 94px mint gradient core.
- `.art-rag`: 60%-wide `#19283a` mock window with three grey bars at 90/70/82% width — a miniature document UI.
- `.art-relay`: four 27px diamonds (`rotate(45deg)`, 1px mint, `box-shadow` glow) plus a mint `→`.
No images ship at all (`document.images.length === 0`) — total page weight is HTML + CSS + 673B JS.

**G. Tag chip** — `1px var(--line)`, `border-radius:2px` (near-square; the whole system's radii are 2/3/4/7px + 50%), `padding:5px 9px`, `9px`, `+.04em`, `#aab8ca`. Non-interactive.

**H. Primary button** — mint fill, ink `#06120f`, `border-radius:3px`, `padding:15px 19px`, `12px/700`, and a **35px gap** between label and `↗` (the arrow is pushed right, making the button feel like a key). Hover: `translateY(-3px)` + `box-shadow:0 12px 34px #8df0d02b`.

**I. Ghost nav CTA** — 1px `--line` border, 3px radius, `10px 16px`, arrow `<b>` in mint with 12px offset. The only bordered control in the header.

**J. Honesty device** (`.fine-print`, `.resume-disclaimer`, `.team-note`) — the page already has a *typographic pattern for caveats*: 10px `#748399`, `max-width:760px`, `margin-top:25px`; the resume variant gets a 2px mint left border and 13px padding. Semantically this is the most valuable component on the site and it is currently **misused**: it sits at the *bottom* of sections, after the claim has landed.

**K. Bordered strip** (`.repo-link`) — `space-between`, 20px padding, 12px text, one link. Good pattern for "download capability statement" / "read the standards" in the proposed IA.

**L. Stat cell** (`.resume-stats div`) — `background:var(--panel)`, 1px border, 17px padding, `23px Space Grotesk` mint figure over a 10px muted caption. Excellent for *company* facts (engagement models, response window) once the personal metrics come out.

**M. Person card** — 28px padding, `person-top` with `01 / BUSINESS` + a 34px Space Grotesk initial (`-.08em`), name 28px, mint role line 11px, 75px-min description, then a `.resume-mini` key/value grid (8px mint labels / 11px values / 9px grey notes) and a `.person-links` footer separated by a hairline.

**N. Step row** — `60px 1fr` grid, mint 12px numeral, 18px title, 12px body, hairline between. The clearest "process" expression and the right skeleton for the engagements page.

### 1.6 Motion and effects

Deliberately near-zero. Full inventory:

| Mechanism | Detail |
|---|---|
| Transitions | Exactly **two**: `.btn { transition:.2s }`, `.work-card { transition:.25s }` |
| Hover transforms | `translateY(-3px)` (button), `translateY(-4px)` (card) |
| Hover colour | `nav a`, `.text-link` → `--mint`; card border → `rgba(141,240,208,.36)` |
| Glow shadows | `0 12px 34px #8df0d02b` (button), `0 0 60px #8df0d044` (pulse core), `0 0 45px #83e0c233`, `0 18px 60px #0005` (mock window) |
| Scroll progress | `.progress` — 2px fixed mint bar, width = `scrollY / (scrollHeight − innerHeight)`, updated on `scroll` (passive). Measured page height 9,722px at 1440 |
| Smooth scroll | `html{scroll-behavior:smooth}`, `scroll-padding-top:90px` (clears the 82px sticky header) |
| Menu | `#menuToggle` toggles `.open` on `<nav>`, sets `aria-expanded`; all nav links close it on click |
| Glass | `backdrop-filter:blur(18px)` on the header only |
| Keyframes | **None.** No entrance animations, no scroll reveals, no marquee, no cursor effects |
| Reduced motion | `@media (prefers-reduced-motion:reduce)` sets `scroll-behavior:auto` and kills all transitions/animations |
| JS total | 3 responsibilities: mobile nav, progress bar, `#year` → current year. 673 bytes, no dependencies |

The "premium" impression comes from typography, hairlines and glow gradients — not animation. Any redesign should preserve that: the site's restraint is its most credible attribute.

### 1.7 Defects found (fix before reuse)

| # | Defect | Evidence | Impact |
|---|---|---|---|
| D1 | **Literal `\n\n` renders as visible text** between the resume and team sections | Text node in `<main>` containing the two-character sequence `\n`; `document.body.innerText.includes("\\n") === true` on the live page; CSS also has 5 lines ending in literal `\r\n` | Visitors see stray `\n` on the page — an obvious "unfinished" tell |
| D2 | **Broken source link** on FarsiPulse | `.../portfolio/tree/main/projects/persian-sentiment-api` → **404**; correct path is `.../tree/main/persian-sentiment-api` | Directly contradicts "Each project links to its source so the implementation can be reviewed" |
| D3 | **Accent drift** | `#8df0d0` (9 uses) vs `#83e0c2` (4 uses) vs `#b9a1ff` (1 use) | Inconsistent mint across link/card/accent states |
| D4 | **Dead tokens** | `--panel2` and `--blue` declared once, referenced zero times | Misleads future edits |
| D5 | **Duplicate `.text-link` rule** | Declared twice; the later 10px/mint version wins over the intended 12px/`#d0d9e6` | Silent cascade collision; text links are 2px smaller than the design intended |
| D6 | **h1 clips at 320px** | At 320×720 viewport the `<em>` right edge measures **324px** inside a `overflow:hidden` hero; h1 is fixed at 68px under 600px | "the expected." loses its final glyph(s) on iPhone SE-class screens |
| D7 | **Internal QA copy is public** | `Profile details pending approval`; `…should be checked and approved before external publication…` | Strongest credibility liability on the page |
| D8 | **Same founder profiled twice, differently** | `Arshia Taheri — Co-founder · Business Development` and `Engineer Taher — Co-founder · Technical Leadership` | Reads as two people or as an unresolved draft; also `Arshia Taheri` is the H2 of the resume section as "Senior AI Engineer" |
| D9 | **Sub-12px content type** | 8px meta/captions, 9px tags, 10px labels | Category and tech labels are effectively decoration |
| D10 | **No head metadata for a company** | No favicon, no `link[rel=canonical]`, no `og:image`, no JSON-LD | Shared links render as a bare URL; no machine-readable Organization |
| D11 | **No tablet breakpoint** | Only 800/700/600 | 1024-class laptops get the tightest combination of gutters and 2-col grid |
| D12 | **Contact is a bare mailto to a Gmail address** | `mailto:eryndexco@gmail.com` on a site presenting as a company | Procurement-safe path (form, NDA, SLA) absent; brand mismatch |

---

## 2. Section-by-section content inventory

Order is exactly the DOM order. Copy is verbatim (typographic apostrophes preserved). Headline copy is what a visitor actually reads; `<br>` positions are shown with `⏎`.

### 2.0 Header (sticky, 82px, glass)
- Logo: `ERYNDEXCO` + mint period, three-bar CSS glyph.
- Nav (6 items): **Services** → `#services` · **Work** → `#work` · **Leadership** → `#resume` · **Team** → `#team` · **Approach** → `#approach` · ghost CTA **Let's talk ↗** → `#contact`.
- Mobile: two-line hamburger, dropdown panel, no lockup change.

### 2.1 Hero — no section label (the only section without one)
- Eyebrow with pulse dot: `SOFTWARE ENGINEERING · APPLIED AI`
- **H1:** `Build beyond⏎the expected.` — `the expected.` in mint (`<em>`)
- Lead: *"We engineer dependable software, thoughtful backend systems and practical AI integrations that help ambitious teams move forward."*
- Actions: `Start a conversation ↗` (mint button) · `Explore selected work ↓` (text link)
- Vertical aside (right edge): `INDEPENDENT ENGINEERING PARTNER` / `BUILT FOR TEAMS THAT BUILD` with a 60px mint rule.
- Bottom index strip: `01 — ENGINEERING` `02 — INTELLIGENCE` `03 — PARTNERSHIPS`

### 2.2 `01 / WHO WE ARE`
- Overline: `A focused technology partner`
- **H2:** `Good software is more than code.⏎It's clarity, craft and follow-through.` (second line `#718198`)
- Body: *"ERYNDEXCO works with businesses, product teams and software agencies to turn defined technical needs into maintainable software. We value clear scope, honest engineering and delivery you can understand."*
- Layout: label in a 1fr column, copy in 2.2fr. No visual, no CTA.

### 2.3 `02 / WHAT WE DO` (`#services`)
- **H2:** `Capabilities with⏎purpose.` · Standfirst: *"Practical engineering services, shaped around your product, your team and the problem at hand."*
- Four numbered rows:
 1. **Backend & API Engineering** — *"Structured backend services and clear API foundations for products that need to work reliably."* Tags: `Python` `FastAPI` `REST APIs` `Integrations`
 2. **Applied AI Integration** — *"Bring useful AI capabilities into existing workflows through model integration, text processing and evaluation."* Tags: `LLM integration` `NLP` `Hugging Face` `Evaluation`
 3. **Knowledge Retrieval & RAG** — *"Build retrieval foundations that help teams find relevant information and inspect the evidence behind results."* Tags: `Document processing` `BM25` `RAG foundations` `Citations`
 4. **Integration & Delivery Systems** — *"Connect services with deliberate handling for retries, idempotency, request validation and delivery lifecycle."* Tags: `Webhooks` `HMAC` `Retries` `Testing`
- Fine print: *"Every engagement begins with a technical review and a written scope. Final deliverables, timelines and service levels are agreed per project."*

### 2.4 `03 / SELECTED WORK` (`#work`)
- **H2:** `Proof through⏎building.` · Standfirst: *"A selection of engineering demonstrators. Each project links to its source so the implementation can be reviewed directly."*
- Six cards, in order (feature / standard alternating):
 1. **RoutePilot** — feature card. Meta `LOGISTICS · OPERATIONS` · art caption `FLEET · DELIVERY · DOCUMENTS / 04` · *"Fleet operations concept combining simulated GPS route visibility, delivery status tracking, proof-of-delivery workflow and a delivery-note preview."* Tags `GPS workflow` `Dispatch` `Delivery proof` `Documents` · links: SOURCE, OPEN INTERACTIVE DEMO (`../routepilot/`).
 2. **SignalDesk** — `WORKFLOW · INTELLIGENCE` / `SERVICE OPERATIONS / 05` · *"Service desk concept with searchable request queues, SLA-risk filters and transparent rule-based prioritization."* Tags `Operations` `SLA` `Queue triage` `Dashboard` · SOURCE + demo (`../signaldesk/`).
 3. **FarsiPulse** — feature card. `APPLIED AI · NLP` / `TEXT INTELLIGENCE / 01` · *"Persian sentiment intelligence API featuring text normalization, transformer-based inference and batch processing."* Tags `FastAPI` `Transformers` `Docker` · SOURCE (**404**), no demo link.
 4. **AtlasRAG** — `SEARCH · RETRIEVAL` / `KNOWLEDGE SYSTEMS / 02` · *"Evidence-focused retrieval service with BM25 ranking, document chunking and evaluation metrics."* Tags `BM25` `SQLite` `FastAPI` · SOURCE.
 5. **ERYNDEXCO Digital Presence** — `COMPANY WEBSITE` / `WEB ENGINEERING / 06` · *"Responsive company website presenting services, team responsibilities, engagement approach and selected engineering work."* Tags `HTML` `CSS` `JavaScript` `Responsive` · SOURCE + `VIEW COMPANY WEBSITE ↗` (links to itself).
 6. **RelayForge** — `BACKEND · RELIABILITY` / `EVENT DELIVERY / 03` · *"Webhook delivery demonstrator with idempotency, HMAC signing, bounded retries and dead-letter handling."* Tags `Python` `HMAC` `Testing` · SOURCE.
- Bordered strip: `Want to inspect the implementation?` → `Explore the engineering repository ↗`
- Fine print: *"These are portfolio demonstrators, not claims of independently verified enterprise production deployments. Implementation details and current limitations are documented in the repositories."*

**Observation:** the art captions carry a second, unrelated numbering (`/ 01`–`/ 06`) that does not match card order (04, 05, 01, 02, 06, 03). It reads as either a build order or a leftover; nothing on the page explains it.

### 2.5 `04 / TECHNICAL LEADERSHIP` (`#resume`)
- **H2:** `Arshia Taheri⏎Senior AI Engineer` · Standfirst: *"AI and software engineering leadership, with a focus on production-minded machine learning, applied NLP and reliable backend systems."*
- Summary: *"Co-founder of ERYNDEXCO, combining technical direction with business development and partner relationships. Experienced across machine learning delivery, AI-enabled products, backend services and cross-functional engineering collaboration."*
- Stats: `6 years — Professional experience claimed` · `2 years — Team leadership experience` · `10–15 — Team size led / coordinated`
- Experience:
 - **Nobitex** — `Aban 1399 – Present` — `Team Lead, Machine Learning · Senior ML Expert` — four bullets including *"Led a machine-learning squad and mentored engineers and data scientists."*, *"Reported outcomes include Persian text-classification F1 around 88%, reduced ASR word error rate, lower fraud false positives and improved churn prediction."*, *"Contributed to faster deployment cycles, lower inference costs and more efficient ETL pipelines."*
 - **Digikala** — `Project-based · Remote` — `Team Lead, ML — Marketing & Growth` — segmentation, purchase prediction, campaign targeting.
 - **Snapp** — `Project-based · Remote` — `Team Lead, Machine Learning` — demand/user-behaviour prediction.
- Education & recognition: **Sharif University of Technology** `B.Sc. · Computer Engineering · 1399–1402`; **Computer Olympiad** `National Silver Medal · 1398`; **Graduate studies** `M.Sc. studies planned / commenced 1405`.
- Technical skills (4 groups, ~30 keywords): AI & ML / Languages & Data / Frameworks & Platforms / MLOps & Cloud.
- Disclaimer: *"Professional history, titles, dates and performance figures on this page are based on information supplied for this profile. They should be checked and approved before external publication or use in formal procurement and hiring processes."*
- Links: `GitHub ↗` · `LinkedIn ↗` · `Contact ERYNDEXCO ↗`

### 2.6 `05 / THE PEOPLE` (`#team`)
- **H2:** `Clear ownership.⏎Shared standards.` · Standfirst: *"A partnership built around complementary responsibilities: client relationships and commercial direction alongside technical leadership and delivery."*
- Card 1 — `01 / BUSINESS`, mark `A.`, **Arshia Taheri**, `Co-founder · Business Development`, *"Co-leads business development, partnerships and international client relationships. Focus areas include opportunity research, commercial discussions, client-to-engineering coordination and white-label partnerships."* Mini-CV: `BSc, Computer Engineering · Sharif University of Technology` / `Graduated 2023` / `Business development · Software partnerships · Client relations` / `ERYNDEXCO co-founder`. Links: GitHub, LinkedIn.
- Card 2 — `02 / TECHNOLOGY`, mark `T.`, **Engineer Taher**, `Co-founder · Technical Leadership`, *"Leads technical assessment, solution architecture, engineering execution, delivery coordination and technical quality."* Footer: `Profile details pending approval`.
- Note: *"Team biographies and credentials are published only after each person has reviewed and approved their details."*

### 2.7 `06 / HOW WE WORK` (`#approach`)
- **H2:** `Less ambiguity.⏎Better delivery.`
- Four steps: `01 Understand` *"We clarify the business need, users, constraints and desired outcome."* · `02 Scope` *"We agree deliverables, assumptions, milestones, responsibilities and acceptance criteria."* · `03 Build` *"We work in reviewable increments and keep communication practical and transparent."* · `04 Handover` *"We provide the agreed code, documentation and knowledge transfer, with follow-on support scoped separately."*

### 2.8 `07 / START A CONVERSATION` (`#contact`)
- Overline: `Have a project in mind?`
- **H2:** `Let's make the⏎next thing real.` (`next thing real.` in mint)
- Body: *"Tell us what you're building, where you're stuck or what kind of delivery partnership you need."*
- Button: `Email ERYNDEXCO ↗` → `mailto:eryndexco@gmail.com?subject=Project%20inquiry%20—%20ERYNDEXCO`
- Small: *"For project inquiries, contact the ERYNDEXCO team by email."*

### 2.9 Footer
Logo lockup · `Software engineering · Applied AI` · `© 2026 ERYNDEXCO. All rights reserved.` (year JS-driven). No legal links, no address, no company registration.

**Structural read:** the page tells a coherent story — *positioning → belief → capability → evidence → pedigree → people → process → ask* — but the middle third (resume + unverified people) is where the "company" narrative collapses into "one person's CV", and both honesty caveats are placed after the claims they qualify.

---

## 3. Claims audit — unsafe or unverifiable for a pre-operational company

Rule applied: the delivery team is not onboarded, no clients exist, no delivery history exists, nothing is legally registered. A claim is unsafe if it (a) asserts history, (b) implies staff/capacity that doesn't exist, (c) asserts third-party-verifiable facts nobody has verified, or (d) tells the visitor the page isn't approved.

### 3.1 Claim-by-claim

| # | Verbatim claim | Location | Why it fails | Safe rewrite |
|---|---|---|---|---|
| C1 | "We engineer dependable software, thoughtful backend systems and practical AI integrations that help ambitious teams move forward." | Hero lead | Present-simple "We engineer… that help teams" asserts an operating practice and client outcomes. "Dependable" is an unearned quality claim. | "We take on defined engineering work — backend services, integrations, applied AI — and deliver it against a written scope." |
| C2 | "ERYNDEXCO works with businesses, product teams and software agencies to turn defined technical needs into maintainable software." | `01 / WHO WE ARE` body | "works with" is a history claim. | "ERYNDEXCO is built to work with agencies, studios and product teams that need defined technical work delivered to a written scope." |
| C3 | "We value clear scope, honest engineering and delivery you can understand." | same | Values are fine; "delivery you can understand" borrows credibility from deliveries that don't exist yet. | "What we commit to: a written scope, code you can read, and a handover you can run without us." |
| C4 | "Practical engineering services, shaped around your product, your team and the problem at hand." | Services standfirst | "services" + "your team" implies current service delivery. | "Four kinds of work we can scope today. Everything else starts with a conversation about fit." |
| C5 | "Proof through building." | Work H2 | "Proof" is an evidentiary claim; six self-built demonstrators are evidence, not proof. | "Evidence you can read." (or "Built to be inspected.") |
| C6 | "Each project links to its source so the implementation can be reviewed directly." | Work standfirst | False as shipped — the FarsiPulse link 404s. | Fix the link to `.../tree/main/persian-sentiment-api`, then keep the sentence. |
| C7 | "A selection of engineering demonstrators." | Work standfirst | Accurate and well-chosen — but the qualifier is 300 words away from the six cards that look like client case studies. | Move the qualifier into each card's `.work-meta`: a `DEMONSTRATOR` chip beside the category, plus the existing fine print. |
| C8 | Card description "Fleet operations **concept** …" / "Service desk **concept** …" | Work cards 1–2 | Correctly worded as concepts; cards 3–6 are not ("API featuring…", "service with…", "demonstrator with…"). Inconsistent hedge depth. | Standardise one clause at the start of every card: "Self-built demonstrator — [what it does]." |
| C9 | "ERYNDEXCO Digital Presence — Responsive company website presenting services, team responsibilities…" | Work card 5, inside *Selected Work* | Presenting your own site as portfolio work implies a client roster; also self-links. | Demote out of the work grid, or label `INTERNAL · THIS SITE`. |
| C10 | "04 / TECHNICAL LEADERSHIP" + "Arshia Taheri / Senior AI Engineer" | Resume H2 | Company page section titled as a leadership bench; "Senior" is an unverified seniority adjective (and the same person appears as "Business Development" in §05). | "FOUNDER" / "Arshia Taheri — Technical direction". Save titles for a CV, not the company site. |
| C11 | "Experienced across machine learning delivery, AI-enabled products, backend services and cross-functional engineering collaboration." | Resume summary | "Experienced" = history claim; unquantified and unverified. | "Works on machine-learning delivery, applied NLP and backend services. Current focus: scoping and delivering client work." |
| C12 | "6 years — **Professional experience claimed**" | Resume stats | The word "claimed" publishes the doubt. A stat that hedges itself is worse than no stat. | Delete the stats block, or replace with company facts: "2 founders · Project-based engagements · Written scope before work". |
| C13 | "2 years — Team leadership experience" / "10–15 — Team size led / coordinated" | Resume stats | Unverifiable, and directly contradicts the constraint that no team is onboarded — a reader will infer a staffed delivery org exists. | Remove both. If team-lead experience is relevant, state it once in prose inside a founder bio that has been verified. |
| C14 | "Reported outcomes include Persian text-classification F1 around 88%, reduced ASR word error rate, lower fraud false positives and improved churn prediction." | Nobitex bullets | Third-party confidential metrics at named employers. Even hedged with "reported", this is an attribution claim you cannot substantiate, and publishing employer internals is a legal/NDA risk. | Remove all metrics. Replace with scope language: "Production ML systems: model monitoring, versioning and retraining workflows." |
| C15 | "Contributed to faster deployment cycles, lower inference costs and more efficient ETL pipelines." | Nobitex bullets | Unquantified outcome claim tied to a named employer. | Remove. Keep only the technical scope (systems built, not results achieved). |
| C16 | "Nobitex — Aban 1399 – Present" alongside "Sharif University — B.Sc. · 1399–1402" | Resume + education | Not false, but a diligence reader will ask how a full-time team-lead role overlaps a B.Sc. window with no explanation, and "Present" asserts current employment at another company. | State the role as `1399–1405` (or the true end date) once confirmed; add one line "Concurrent with undergraduate study" if accurate. Never publish "Present" for a former employer. |
| C17 | "Digikala — Team Lead, ML — Marketing & Growth" (no dates) / "Snapp — Team Lead, Machine Learning" (no dates) | Resume | Employment claims at two highly recognisable companies with no dates or verification status — the highest-risk items on the page if either title is approximate. | Publish only after written confirmation of title and dates; otherwise omit. Recognisable logos/names are precisely what a procurement reviewer checks first. |
| C18 | "Computer Olympiad — National Silver Medal · 1398" | Education | Verifiable only by certificate; "National" and the medal level are specific. | Keep if the certificate exists and can be produced on request; add "available on request". |
| C19 | "Graduate studies — M.Sc. studies **planned / commenced** 1405" | Education | "planned / commenced" is not a fact; it's two mutually exclusive statuses. | Delete until there is a single true status. |
| C20 | "Professional history, titles, dates and performance figures on this page are based on information supplied for this profile. They should be checked and approved before external publication…" | Resume disclaimer, public | Publishes the fact that the page's own facts are unapproved, and instructs the reader to do QA. Most damaging sentence on the site. | Remove from the public build. Move to an internal checklist (this file, or an onboarding doc). Replace publicly with nothing — an unverified section should be unpublished, not annotated. |
| C21 | "Profile details pending approval" | Team card 2 | Visible placeholder; also implies the other card *is* approved when both are the same person. | Unpublish the card until details are confirmed, or publish a name-only card: "Co-founder · Technical direction. Full profile available on request." |
| C22 | Team standfirst: "A partnership built around complementary responsibilities: client relationships and commercial direction **alongside technical leadership and delivery**." | `05 / THE PEOPLE` | Implies a delivery function exists. | "Two founders, two responsibilities, one accountable owner per engagement." |
| C23 | Card 1: "Co-leads business development, partnerships and **international client relationships**." | Team card 1 | "client relationships" implies existing clients. | "Responsible for business development, partnerships and client communication." |
| C24 | Card 2: "Leads technical assessment, solution architecture, engineering **execution, delivery coordination** and technical quality." | Team card 2 | Asserts delivery capability and coordination of people who aren't onboarded. | "Owns technical assessment, solution architecture and technical quality on engagements." |
| C25 | "Team biographies and credentials are published only after each person has reviewed and approved their details." | `.team-note` | Exposes internal governance; a visitor reads "these aren't approved". | Delete. Approval is a process, not a selling point. |
| C26 | "For project inquiries, contact **the ERYNDEXCO team** by email." | Contact small print | Implies staff. | "Email us — one of the founders replies." |
| C27 | "Every engagement begins with a technical review and a written scope. Final deliverables, timelines and service levels are agreed per project." | Services fine print | **This is the model to copy** — process commitment, no history claim, no capacity claim. | Promote it out of fine print into a visible strip under the service list. |
| C28 | "We provide the agreed code, documentation and knowledge transfer, with follow-on support scoped separately." | Step 04 | Sound, but "follow-on support" hints at a support organization. | "Handover includes the agreed code, documentation and a walkthrough. Ongoing support is a separate agreement." |
| C29 | "© 2026 ERYNDEXCO. All rights reserved." | Footer | Implies a legal entity and registered name. The repo README itself says the legal name and registered status are unconfirmed. | Use `© 2026 Eryndex` only once the entity/name is confirmed; until then `Eryndex — project site`. Add imprint when registered. |
| C30 | Nav item **"Leadership"** pointing at a personal CV | Header | Frames a two-person pre-launch partnership as a leadership bench. | Rename to **Company** (or **Founders**) and give it a page, not a CV. |
| C31 | "BUILT FOR TEAMS THAT BUILD" / "INDEPENDENT ENGINEERING PARTNER" | Hero aside | Safe — structural, not historical. Keep. | — |
| C32 | `mailto:eryndexco@gmail.com` on a site presenting as a company with a domain | Contact | Not a claim, but the strongest credibility leak: Gmail + unregistered name + "all rights reserved" reads as unlaunched. | `hello@eryndex.net` (and an alias on the same domain) before any outbound campaign; keep a Gmail fallback only in a hidden field. |
| C33 | Meta description "…for ambitious teams." + `og:title "ERYNDEXCO — Engineering What's Next"` | `<head>` | "What's Next" is generic; no `og:image`, no canonical, no Organization JSON-LD. | Reposition the description around the actual transaction (§4), add OG image + canonical + Organization schema with *verified* fields only. |

### 3.2 Verification failures (not copy, but they falsify copy)

- **FarsiPulse source link → 404.** Live check: `portfolio/tree/main/projects/persian-sentiment-api` returns **404**; `portfolio/tree/main/persian-sentiment-api` returns **200**. This breaks C6 and the "reviewable implementation" promise.
- **AtlasRAG / RelayForge** point into a *different* repository (`arshiataheri851-ops/arshiataheri851-ops`) than RoutePilot/SignalDesk (`portfolio`). Both resolve, but the split is undocumented and reads as inconsistent provenance.
- **Self-link:** "ERYNDEXCO Digital Presence → VIEW COMPANY WEBSITE" links to the page you are on.
- **`eryndex.net` does not resolve** (no A record — `curl` exited `000`). Nothing on the page should reference the domain until it serves the site.
- **LinkedIn** returns HTTP 999 to bots (normal for LinkedIn) — cannot be verified automatically; must be checked manually in a logged-in browser before publication.

### 3.3 Copy rules to enforce going forward

1. **Tense:** capability and commitment in present ("we scope", "we hand over"), history only in past tense and only when verified. No present-simple claim of ongoing client work until a client exists.
2. **"We" must be literally true.** Today "we" = two founders plus subcontracted capacity that is not yet onboarded. Every sentence containing "we deliver/we work with/our team" needs a check against that.
3. **Numbers require a source.** Any figure (years, team size, F1, %, counts) needs an owner, an artefact and a date, or it comes out.
4. **Qualifiers go next to the thing they qualify** — in the card meta or the row, not in a fine-print block 4 sections later.
5. **Never publish build notes.** "Pending approval", "should be checked", "TODO", "draft" — these live in the repo, not in the render.
6. **Named third parties** (Nobitex, Digikala, Snapp, Sharif) need explicit, written permission for the *specific* framing used, and never carry their metrics.
7. **New-company honesty is a differentiator, not a liability** — one visible line does more for credibility than any hedge: *"Eryndex is newly formed. We take a small number of scoped engagements while our delivery bench is being onboarded."*

---

## 4. Proposed information architecture for `eryndex.net`

### 4.1 Positioning the IA has to encode

Not "AI automation agency" (competing on novelty against hundreds of lookalikes, and inviting AI-hype scepticism from the exact buyer: an agency principal who has been pitched by 40 of them). Instead:

> **A wholesale engineering partner for agencies and studios.** You have the client and the relationship; we take a defined slice of the build — backend, integrations, retrieval, applied AI — to a written scope, and hand it back reviewable and documented.

IA consequences:
- **The buyer is named on the first screen** (agencies/studios), because the current hero ("ambitious teams") addresses nobody.
- **The transaction is on the homepage** (project-based scoping; reserved capacity as a later, honest "not open yet").
- **Proof is replaced by inspectable evidence** — source-linked demonstrators plus published engineering standards. For a company with no clients, *process transparency is the only credible proof*, and it is stronger than a stock-photo testimonial.
- **The founders' background is the company's only real asset** — so it must be framed as *founder credibility* (verifiable, scoped) rather than as company history or a staffed team.
- **AI is a capability line, not the brand.** "Applied AI" moves from the hero to one of four capability pages.

### 4.2 Sitemap

```
/                                  Home (one-screen positioning → engagement models → evidence)
/engagements/                      How to buy: the two models, side by side
/engagements/project-based/        Scope, milestones, what's included, what isn't, how pricing works
/engagements/reserved-capacity/    Monthly reserved capacity — status: onboarding later; waitlist
/capabilities/                     Index of the four work types (reuses the numbered-row pattern)
/capabilities/backend-api/
/capabilities/applied-ai/
/capabilities/retrieval-rag/
/capabilities/integrations-delivery/
/evidence/                         Demonstrator index — every card badged DEMONSTRATOR
/evidence/routepilot/              Per-project page: problem, what's real vs simulated, source, limits
/evidence/signaldesk/
/evidence/farsipulse/
/evidence/atlasrag/
/evidence/relayforge/
/standards/                        Engineering standards: definition of done, review gates, security, NDA
/standards/handover/               The handover checklist (the artefact that closes agency deals)
/company/                          Who we are: two founders, mandate, what we don't do
/company/founders/                 Verified founder profiles (one canonical profile per person)
/contact/                          Qualifying brief form + email + response window
/notes/                            (optional, post-launch) 3–5 engineering notes; parked until written
/legal/imprint/  /legal/privacy/  /legal/terms/    Required for EU/UK agency buyers
/404
```

**Nav (5 + CTA):** `Engagements` · `Capabilities` · `Evidence` · `Standards` · `Company` — CTA `Send a brief`. Rationale: the current nav mixes buyer intent (`Services`, `Work`) with self-description (`Leadership`, `Team`, `Approach`). The proposed set is ordered by the buyer's own questions: *how do I buy → what can you take → can I inspect it → will it survive review → who am I dealing with.*

### 4.3 Homepage section order (with the reason each exists)

| # | Section | Label (keeps the `0N / TITLE` language) | Content | Why here |
|---|---|---|---|---|
| 1 | Hero | *(no label, as today)* | Eyebrow `WHOLESALE ENGINEERING · FOR AGENCIES & STUDIOS`; H1 `Engineering capacity⏎for agencies that ship.`; lead naming the transaction (defined slice, written scope, reviewable handover); primary CTA `Send a brief`, secondary `See the evidence`; keep the vertical aside, pulse dot, bottom index strip (retitle to `SCOPE — BUILD — HANDOVER`) | Names buyer and transaction in one screen. "Capacity" is the word agency principals buy; it also honestly implies ramping |
| 2 | Who this is for | `01 / FIT` | Three hairline rows: agencies at capacity mid-project; studios without backend depth; product teams needing a second pair of hands. Plus one honest **"probably not a fit"** row (needs a 10-person team, needs on-site, needs 24/7 support) | Self-qualification is the cheapest credibility tool available; refusing work is evidence of standards |
| 3 | Engagement models | `02 / HOW TO ENGAGE` | Two `.work-card`-style panels or a 2-col table: **Project-based** (available now) and **Reserved capacity** (badged `OPENING LATER`) — scope, minimum, lead time, what's included. No prices required; `from` bands optional | The single biggest gap in the current site: nothing tells an agency how to actually buy. The honest "later" badge turns a missing product into a signal of sequencing discipline |
| 4 | What we take on | `03 / CAPABILITIES` | The existing 4 numbered rows, rewritten per row as **in scope / out of scope / typical slice size**; each row links to its capability page. Keep the `01`–`04` numerals, mint numbers, tag chips | Reuses the strongest existing component; "out of scope" lines are what separates an engineering partner from an agency |
| 5 | How an engagement runs | `04 / PROCESS` | Existing 4 steps (Understand / Scope / Build / Handover), plus per-step output artefacts (`scope.md`, milestone plan, review link, handover checklist) and a "what you get every week" line | Process is the product pre-launch. Artefacts make the process auditable |
| 6 | Evidence | `05 / EVIDENCE` | 5 demonstrator cards (drop the self-referential website card), each with a `DEMONSTRATOR` chip *in the meta row*, one line of problem, one line of "what's simulated", `SOURCE ↗` and `LIMITATIONS →` | Moves the honesty qualifier from fine print into the card, which is the only place a scanning reader will see it |
| 7 | Standards | `06 / STANDARDS` | 4–5 numbered commitments: every PR reviewed before merge; tests for the integration path; documented runbook at handover; no silent scope growth; IP assigned to the client on payment. Link: `/standards/` | This is the substitute for logos. It is checkable, and it is exactly what an agency's tech lead wants to read |
| 8 | Founders | `07 / WHO YOU WORK WITH` | Two cards, **verified details only**: name, role, one scoped paragraph, links. No years/team-size stats. One line: "The person who scopes your work is the person accountable for it" | Two named people with fixed responsibilities outrank an anonymous "team" for a pre-operational company |
| 9 | FAQ | `08 / QUESTIONS` | 5–6: NDA before scoping? who owns the IP? timezone/overlap? what if the scope changes? can you work white-label? what happens when capacity is full? | Every unanswered procurement question becomes a lost reply |
| 10 | Contact | `09 / START` | Short qualifying form (agency name, what's needed, timeline, NDA required y/n, budget band) + `hello@eryndex.net` + "one of the founders replies within two working days" + what happens next | Removes the mailto-to-Gmail dead end and pre-qualifies, which a two-person team needs |
| 11 | Footer | — | Logo, one-line positioning, `© Eryndex` (post-registration), imprint/privacy links, honest status line | Legal + trust surface |

**Deliberate omissions:** no client logos, no testimonials, no "trusted by", no stats band, no availability calendar, no team headcount, no revenue-share mention (per the cold-outreach strategy — it's a negotiation instrument, not a homepage item).

### 4.4 Reusing the existing visual language

**Keep (it's the brand):** `#090d18` base / `#101727` panels / single mint `#8df0d0` accent / `rgba(180,200,230,.14)` hairlines · Space Grotesk 500 display with `-.055em` tracking and two-tone headline clauses · 10px `+.18em` mint `0N / TITLE` section labels · numbered hairline rows (`70px 1fr 30px`) · CSS-art plates with 8px captions · 3px-radius mint buttons with the 35px arrow gap · 2px scroll progress bar · 12vw gutters / 125px section rhythm · `.fine-print` as the typographic voice of caveats (relocated, not deleted).

**Change:** h1 copy and eyebrow (positioning, not styling) · `.resume-stats` repurposed from personal metrics to **company facts** (`2 founders`, `Project-based`, `Written scope before work`) · resume columns become an **engagement spec table** (included / excluded / client provides / we provide) · `.person-card` mini-CV block drops education and keeps "responsibility + what they own" · `PROOF` becomes `EVIDENCE` in the label and the H2.

**Add (missing primitives, same vocabulary):**
1. **Status badge** — 1px border, 2px radius, 9px `+.14em`, variants: `DEMONSTRATOR`, `AVAILABLE NOW` (mint outline), `OPENING LATER` (grey), `INTERNAL`. Sits in `.work-meta`, not in fine print.
2. **Scope table** — two columns `INCLUDED` / `NOT INCLUDED` on hairlines, 13px; the single highest-trust component you can ship.
3. **Artefact chip row** — tiny bordered chips naming deliverables (`scope.md`, `API spec`, `runbook`, `test suite`) inside `.step`.
4. **Founder verification line** — 10px `#748399` under a bio: `Reviewed and confirmed · 2026-11` (only publish when true).
5. **MDX-friendly `evidence` template** — problem / constraints / what's real / what's simulated / how to inspect / known limits. Makes the honesty discipline structural instead of editorial.

**Fix while reusing:** the `\n` artifact (D1), the 404 link (D2), accent unification to `#8df0d0` (D3), `.text-link` duplicate (D5), 320px h1 clipping (D6), and a 1024px breakpoint (D11).

---

## Appendix A — Extracted tokens (JSON)

Values are read from `eryndex/assets/style.css` and confirmed against computed styles at 1440×900. Anything marked `"unused": true` is declared but never referenced. `"observed"` lists document measurement, not design intent.

```json
{
  "$meta": {
    "source": "https://arshiataheri851-ops.github.io/portfolio/eryndex/",
    "source_files": ["eryndex/index.html", "eryndex/assets/style.css", "eryndex/assets/script.js"],
    "extracted": "2026-11-13",
    "method": "static read of CSS + Chrome computed styles at 1440x900, 390x844, 320x720",
    "live_equals_repo": true,
    "page_height_desktop_px": 9722
  },

  "color": {
    "primitive": {
      "bg":        { "value": "#090d18", "rgb": [9, 13, 24] },
      "panel":     { "value": "#101727", "rgb": [16, 23, 39] },
      "panel-2":   { "value": "#121d30", "rgb": [18, 29, 48], "unused": true },
      "text":      { "value": "#edf2fa", "rgb": [237, 242, 250] },
      "muted":     { "value": "#9ba9bd", "rgb": [155, 169, 189] },
      "line":      { "value": "rgba(180,200,230,.14)" },
      "mint":      { "value": "#8df0d0", "rgb": [141, 240, 208] },
      "blue":      { "value": "#8baeff", "rgb": [139, 174, 255], "unused": true }
    },
    "accent-drift": {
      "note": "three values in circulation for the same role",
      "mint-canonical": { "value": "#8df0d0", "uses": 9 },
      "mint-variant":   { "value": "#83e0c2", "uses": 4, "where": [".text-link (override)", ".art-route .pulse-core", ".art-route .pulse-core border"] },
      "lavender":       { "value": "#b9a1ff", "uses": 1, "where": ".art-signal .pulse-core" }
    },
    "surface": {
      "work-art":     { "value": "#101b2b" },
      "art-route-bg": { "value": "#101b28" },
      "art-signal-bg":{ "value": "#111827" },
      "rag-window":   { "value": "#19283a", "border": "rgba(91,125,155,.33)" },
      "contact":      { "value": "#0d1725" },
      "nav-mobile":   { "value": "#0b1220" },
      "header-glass": { "value": "rgba(9,13,24,.86)", "blur": "18px" }
    },
    "text-shades": {
      "hero-lead-tags": "#aab8ca",
      "eyebrow": "#b8c9d9",
      "nav": "#bac5d5",
      "heading-clause-2": "#718198",
      "footer": "#718096",
      "fine-print": "#748399",
      "note": "#72839a",
      "art-caption": "#7d91a8",
      "resume-date": "#8191a7",
      "disclaimer": "#8493a8",
      "ink-on-mint": "#06120f",
      "ink-on-mint-alt": "#071612",
      "mint-gradient-end": "#4a9d9a"
    },
    "effect": {
      "hero-grid-line": "rgba(142,170,210,.045)",
      "hero-grid-size": "70px 70px",
      "hero-glow": "radial-gradient(circle, rgba(73,132,180,.2), rgba(73,132,180,0) 68%)",
      "hero-glow-size": "600px",
      "contact-glow": "radial-gradient(ellipse at 70% 50%, rgba(76,142,137,.19), transparent 65%)",
      "card-hover-border": "rgba(141,240,208,.36)",
      "button-hover-shadow": "0 12px 34px rgba(141,240,208,.17)",
      "pulse-shadow": "0 0 18px #8df0d0",
      "core-shadow": "0 0 60px rgba(141,240,208,.27)"
    },
    "contrast": {
      "text-on-bg": 17.26, "text-on-panel": 15.91, "mint-on-bg": 14.33,
      "mint-on-panel": 13.21, "ink-on-mint": 14.08, "hero-lead-on-bg": 9.63,
      "muted-on-bg": 8.13, "muted-on-panel": 7.5, "art-caption": 5.35,
      "fine-print": 5.03, "team-note": 5.02, "heading-clause-2": 4.89, "footer": 4.83
    }
  },

  "typography": {
    "families": {
      "display": { "stack": "'Space Grotesk', sans-serif", "role": "headings, numerals, logo, marks" },
      "body":    { "stack": "'DM Sans', sans-serif", "role": "all running text" }
    },
    "loaded_from": "https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap",
    "weights_rendered": { "DM Sans": [400, 700], "Space Grotesk": [500, 600, 700] },
    "weights_requested_unused": { "DM Sans": [500, 600], "Space Grotesk": [400] },
    "scale": {
      "h1":     { "size": "clamp(66px, 9vw, 124px)", "measured": "124px", "weight": 500, "line-height": 0.99, "tracking": "-0.055em", "family": "display" },
      "h2":     { "size": "clamp(36px, 5vw, 62px)", "measured": "62px", "weight": 500, "line-height": 1.1, "tracking": "-0.055em", "family": "display" },
      "h2-contact": { "size": "clamp(55px, 8vw, 95px)", "measured": "95px", "weight": 500, "line-height": 1.0, "tracking": "-0.055em", "family": "display" },
      "h3-work":    { "size": "27px", "weight": 500, "tracking": "-0.03em", "family": "display" },
      "h3-person":  { "size": "28px", "weight": 500, "tracking": "-0.055em", "family": "display" },
      "h3-service": { "size": "23px", "weight": 500, "tracking": "-0.03em", "family": "display" },
      "h3-step":    { "size": "18px", "weight": 500, "family": "display" },
      "h4-role":    { "size": "17px", "weight": 500, "family": "display" },
      "h3-resume":  { "size": "21px", "weight": 500, "tracking": "-0.03em", "family": "display" },
      "stat-figure":{ "size": "23px", "weight": 500, "family": "display" },
      "logo":       { "size": "17px", "weight": 700, "tracking": "0.13em", "family": "display" },
      "lead":       { "size": "17px", "weight": 400, "line-height": 1.8, "family": "body" },
      "body":       { "size": "15px", "weight": 400, "line-height": 1.9, "family": "body" },
      "body-sm":    { "size": "13px", "weight": 400, "line-height": 1.8, "family": "body" },
      "nav":        { "size": "12px", "weight": 400, "line-height": 1.6, "family": "body" },
      "label":      { "size": "10px", "weight": 700, "tracking": "0.18em", "transform": "uppercase", "family": "body" },
      "fine-print": { "size": "10px", "weight": 400, "line-height": 1.7, "family": "body" },
      "tag":        { "size": "9px", "weight": 400, "tracking": "0.04em", "family": "body" },
      "caption":    { "size": "8px", "weight": 400, "tracking": "0.17em", "family": "body" },
      "meta":       { "size": "8px", "weight": 700, "tracking": "0.18em", "family": "body" }
    },
    "base": { "font-size": "16px", "line-height": 1.6, "body-font": "body" }
  },

  "space": {
    "base_unit_px": null,
    "note": "no scale token exists; 42 literal values in use",
    "all_declared_values_px": [2,3,5,7,8,9,10,12,13,14,15,16,17,18,19,20,22,23,25,26,28,30,31,32,34,35,38,45,48,50,52,55,60,68,70,76,82,90,95,110,125,130],
    "recovered_scale_px": [8,12,16,20,22,26,28,32,48,50,60,70,125],
    "measured": {
      "section_padding_block": "125px",
      "section_padding_inline": "12vw",
      "section_padding_block_tablet": "90px",
      "section_padding_block_mobile": "76px",
      "section_padding_inline_tablet": "8vw",
      "section_padding_inline_mobile": "7vw",
      "header_height": "82px",
      "header_height_mobile": "70px",
      "header_inline_padding": "7vw",
      "hero_padding": "130px 12vw 70px",
      "hero_min_height": "680px",
      "hero_height_desktop": "790px",
      "section_head_gap": "50px",
      "section_head_margin_bottom": "55px",
      "label_to_h2": "22px",
      "h2_to_body": "24px",
      "content_to_fine_print": "25px",
      "service_row_padding": "31px 0",
      "step_row_padding": "23px 0",
      "work_grid_gap": "22px",
      "work_feature_column_gap": "26px",
      "team_grid_gap": "18px",
      "resume_columns_gap": "48px",
      "stat_grid_gap": "12px"
    },
    "recommended_scale_px": [4,8,12,16,24,32,48,64,96,128]
  },

  "layout": {
    "content_width": "100vw - 24vw (no max-width)",
    "content_width_desktop_px": 1079,
    "grid": {
      "intro": "1fr 2.2fr / gap 50px",
      "approach": "1fr 2fr / gap 50px",
      "work": "repeat(2, 1fr) / gap 22px; .work-feature spans 2 cols, inner 1fr 1fr / 26px",
      "team": "repeat(2, 1fr) / gap 18px",
      "service-row": "70px 1fr 30px / gap 20px",
      "step-row": "60px 1fr / gap 20px",
      "resume": "1.25fr .85fr / gap 48px",
      "stats": "repeat(3, 1fr) / gap 12px"
    },
    "max_widths": { "hero-content": "800px", "hero-lead": "540px", "body-copy": "660px", "card-body": "680px", "section-standfirst": "330px", "fine-print": "760px", "contact-content": "650px + margin-left 20%" },
    "breakpoints": { "lg": "801px+ (desktop rules)", "md": "max-width: 800px", "resume": "max-width: 700px", "sm": "max-width: 600px", "missing": "no 1024-1100px tier" },
    "radii": { "tag": "2px", "button": "3px", "nav-cta": "3px", "logo-bar": "4px", "relay-node": "7px", "circle": "50%" },
    "borders": { "hairline": "1px solid rgba(180,200,230,.14)", "accent-2px": "2px solid #8df0d0 (resume disclaimer left edge)", "card": "1px solid rgba(180,200,230,.14)" }
  },

  "components": {
    "section-label": { "content-pattern": "NN / UPPERCASE TITLE", "examples": ["01 / WHO WE ARE","02 / WHAT WE DO","03 / SELECTED WORK","04 / TECHNICAL LEADERSHIP","05 / THE PEOPLE","06 / HOW WE WORK","07 / START A CONVERSATION"], "color": "mint", "size": "10px", "weight": 700, "tracking": "0.18em" },
    "hero": { "layers": ["70px grid plate, mask linear-gradient(90deg, black, transparent 90%)", "600px radial glow right 2% top 4%", "content column max 800px", "vertical aside right 7vw top 38%", "bottom index strip left/right 12vw bottom 35px"], "eyebrow-dot": "7px mint + 0 0 18px mint glow" },
    "logo": { "mark": "3 bars, 5px wide, radius 4px, skew(-25deg), heights 12/20/16", "wordmark": "Space Grotesk 700 / 17px / +0.13em", "period": "mint" },
    "service-row": { "columns": "70px 1fr 30px", "padding": "31px 0", "number": "mint 13px Space Grotesk 500", "title": "23px / 500", "hover": "none" },
    "work-card": { "bg": "panel", "border": "1px hairline", "padding": "14px", "hover": ["border-color rgba(141,240,208,.36)", "translateY(-4px)"], "transition": "0.25s", "art-height": "230px (feature 290px)", "body-min-height": "68px" },
    "work-meta": { "size": "8px", "weight": 700, "tracking": "0.18em", "layout": "space-between: category | mint SOURCE ↗", "margins": "18px 0 12px" },
    "art-caption": { "size": "8px", "tracking": "0.17em", "color": "#7d91a8", "position": "absolute bottom 14px left 16px" },
    "tag": { "border": "1px hairline", "radius": "2px", "padding": "5px 9px", "size": "9px", "tracking": "0.04em", "color": "#aab8ca" },
    "button-primary": { "bg": "mint", "color": "#06120f", "radius": "3px", "padding": "15px 19px", "size": "12px", "weight": 700, "gap": "35px", "hover": ["translateY(-3px)", "0 12px 34px rgba(141,240,208,.17)"], "transition": "0.2s" },
    "nav-cta": { "border": "1px hairline", "radius": "3px", "padding": "10px 16px", "arrow-color": "mint", "arrow-offset": "12px" },
    "fine-print": { "size": "10px", "line-height": 1.7, "color": "#748399", "max-width": "760px", "margin-top": "25px" },
    "disclaimer": { "size": "9px", "color": "#8493a8", "border-left": "2px mint", "padding-left": "13px" },
    "bordered-strip": { "layout": "space-between", "padding": "20px", "border": "1px hairline", "size": "12px" },
    "stat-cell": { "bg": "panel", "border": "1px hairline", "padding": "17px", "figure": "23px Space Grotesk mint", "caption": "10px muted" },
    "person-card": { "padding": "28px", "bg": "panel", "mark": "34px Space Grotesk 500 / -0.08em / mint", "name": "28px", "role": "11px mint", "body-min-height": "75px" },
    "step-row": { "columns": "60px 1fr", "padding": "23px 0", "number": "12px Space Grotesk 500 mint", "title": "18px", "body": "12px muted" },
    "progress-bar": { "height": "2px", "position": "fixed top 0", "color": "mint", "driver": "scroll position, passive listener" },
    "skill-group": { "padding": "13px 0", "border-top": "1px hairline", "label": "10px #dce5f0", "body": "10px muted" }
  },

  "motion": {
    "keyframes": [],
    "transitions": { "button": "0.2s", "card": "0.25s" },
    "transforms": { "button-hover": "translateY(-3px)", "card-hover": "translateY(-4px)" },
    "scroll": { "behavior": "smooth", "padding-top": "90px" },
    "reduced_motion": "disables smooth scroll and all transitions/animations",
    "js_bytes": 673,
    "js_responsibilities": ["mobile nav toggle", "scroll progress bar", "footer year"]
  },

  "content_model": {
    "sections_in_order": [
      { "n": null, "label": null, "id": null, "headline": "Build beyond / the expected." },
      { "n": 1, "label": "01 / WHO WE ARE", "id": null, "headline": "Good software is more than code. / It's clarity, craft and follow-through." },
      { "n": 2, "label": "02 / WHAT WE DO", "id": "services", "headline": "Capabilities with / purpose." },
      { "n": 3, "label": "03 / SELECTED WORK", "id": "work", "headline": "Proof through / building." },
      { "n": 4, "label": "04 / TECHNICAL LEADERSHIP", "id": "resume", "headline": "Arshia Taheri / Senior AI Engineer" },
      { "n": 5, "label": "05 / THE PEOPLE", "id": "team", "headline": "Clear ownership. / Shared standards." },
      { "n": 6, "label": "06 / HOW WE WORK", "id": "approach", "headline": "Less ambiguity. / Better delivery." },
      { "n": 7, "label": "07 / START A CONVERSATION", "id": "contact", "headline": "Let's make the / next thing real." }
    ],
    "counts": { "sections": 8, "service_rows": 4, "work_cards": 6, "project_demonstrators": 5, "team_cards": 2, "process_steps": 4, "nav_items": 6, "anchors": 26, "images": 0 }
  },

  "known_defects": [
    { "id": "D1", "severity": "high", "issue": "literal backslash-n text node renders between resume and team sections" },
    { "id": "D2", "severity": "high", "issue": "FarsiPulse source link 404 (projects/persian-sentiment-api)", "fix": "portfolio/tree/main/persian-sentiment-api" },
    { "id": "D3", "severity": "medium", "issue": "accent drift #8df0d0 / #83e0c2 / #b9a1ff" },
    { "id": "D4", "severity": "low", "issue": "--panel2 and --blue declared but unused" },
    { "id": "D5", "severity": "medium", "issue": ".text-link declared twice; later 10px override wins" },
    { "id": "D6", "severity": "medium", "issue": "h1 clips at 320px viewport (em right edge 324px, hero overflow hidden)" },
    { "id": "D7", "severity": "high", "issue": "internal QA copy public: 'Profile details pending approval', 'should be checked and approved'" },
    { "id": "D8", "severity": "high", "issue": "same founder appears as three identities (Senior AI Engineer, Co-founder Business Development, Engineer Taher)" },
    { "id": "D9", "severity": "medium", "issue": "8-9px content type for meta, tags, captions" },
    { "id": "D10", "severity": "medium", "issue": "no favicon, canonical, og:image or JSON-LD" },
    { "id": "D11", "severity": "medium", "issue": "no 1024-1100px breakpoint" },
    { "id": "D12", "severity": "high", "issue": "contact is bare mailto to eryndexco@gmail.com" }
  ]
}
```

---

## Appendix B — Measured reference table (1440×900)

| Element | Box (px) | Type |
|---|---|---|
| `.header` | 1425×82, pad `0 100.8` | DM Sans 16/400 |
| `.hero` | 1425×790, pad `130 172.8 70` | — |
| `h1` | 800×246 | Space Grotesk **124/500**, lh 122.76, ls **-6.82px** |
| `.hero-lead` | 540×61 | DM Sans 17/400, lh 30.6 |
| `.eyebrow` | 800×16 | DM Sans 10/700, ls 1.8 |
| `.section-wrap` | 1425×682, pad `125 172.8` | — |
| `.section-head` | 1079×174 | — |
| `.intro h2` | 708×273 | Space Grotesk **62/500**, ls -3.41 |
| `.body-copy` | 660×86 | DM Sans 15/400, lh 28.5 |
| `.service` row | 1079×172, pad `31 0` | grid `70/1fr/30` |
| `.work-grid` | 1079×2179 | 2 cols, gap 22 |
| `.work-card` | 1079×320 (feature), pad 14 | bg `#101727` |
| `.person-card` | 531×514, pad 28 | bg `#101727` |
| `.step` | 686×100, pad `23 0` | grid `60/1fr` |
| `.contact` | 1425×686, pad `125 172.8` | bg `#0d1725` |
| `.contact h2` | 650×190 | Space Grotesk **95/500**, ls -5.225 |
| `.btn-primary` | 201×49, pad `15 19` | DM Sans 12/700, ink `#06120f` |
| `.tags span` | 52×26, pad `5 9` | DM Sans 9/400 |
| `footer` | 1425×81, pad `28 100.8` | DM Sans 10/400 |

Mobile (390×844): h1 68px, hero 762px, nav hidden (`display:none`), no horizontal overflow.
Mobile (320×720): h1 `<em>` overflows to x=324 inside `overflow:hidden` → clipped final glyph.
