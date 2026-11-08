# Ideal Partner Profile (IPP) Scoring Rubric v2 — Evidence-Based Qualification Specification

**Document Version:** 2.0.1
**Phase:** 2.2 — Qualification & Evaluation Architecture (Evidence Consistency Patch)
**Target Platform:** Runtime-Independent (Interactive Antigravity Supervised Runtime [Current] / Hermes Autonomous Runtime [Future]) & PostgreSQL CRM (`crm/schema.sql`)
**Status:** Canonical Design Specification & Human-Reviewable Scoring Contract (Documentation Only)
**Date:** 2026-11-08

---

> [!IMPORTANT]
> **Pre-Operational Stage Disclosure:** The business is currently in a **pre-operational foundation-building stage**. The planned technical delivery team is **not yet onboarded** and is **not currently available for client delivery**.
> All evaluations, scorecards, and outreach preparations conducted under this rubric operate strictly in a research, qualification, and relationship-building capacity. Evaluators and agents must **never** claim, imply, or represent that technical capacity is immediately available or that delivery projects are actively executing.

> [!NOTE]
> **Human-Reviewable Scoring Contract:** This document establishes an objective, evidence-based scoring specification and standard operating procedure (SOP) for evaluating prospective Managed Development Partners. It functions as a governance contract and review guide; it is **not** an autonomous agent implementation and does not directly mutate CRM or database records without explicit Human Operator authorization.

---

## 1. Purpose and Scope

### 1.1. Why This Rubric Exists
Evaluating prospective business partners cannot rely on subjective impressions, superficial aesthetic appraisal, or optimistic assumptions. In wholesale software development, misqualifying an agency leads to wasted outreach, misaligned commercial expectations, scope friction, or project failure.

The **IPP Scoring Rubric v2** establishes an objective, mathematical, and evidence-grounded framework for assessing whether a design studio or agency possesses the operational maturity, client volume, technical demand, and commercial posture required for a successful, high-leverage development partnership.

### 1.2. Partner Evaluation vs. End-Client Evaluation
A critical foundational principle governs this entire rubric:
- **We evaluate prospective wholesale partners (agencies and studios), NEVER end clients.**
- The prospective partner is an established agency, Webflow studio, or Framer studio that already owns relationships with corporate or B2B brand clients.
- We do **not** seek direct end clients, consumer accounts, or unvetted startups seeking bespoke application development.
- The partner acts as the commercial account owner, client manager, and design lead; we act as their behind-the-scenes, white-label software engineering partner.

### 1.3. Business Mission & Wholesale Partnership Objective
The primary objective of this business is to serve as a high-reliability **Managed Development Partner**. We provide dedicated, dependable, managed software development capacity to digital agencies and visual development studios that face technical bottlenecks, recurring technical overflow, or lack in-house full-stack software engineers.

The partnership structure follows a strict commercial hierarchy:
1. **Project-Based Wholesale Development (Entry Model):** Fixed-scope or milestone-based deliverables to prove reliability, establish communication rhythm, and build trust without long-term commitment.
2. **Monthly Reserved Development Capacity (Core Recurring Model):** Dedicated developer capacity units reserved on a monthly recurring basis once trust and predictable project volume are proven.
3. **Strategic Revenue-Sharing Partnerships (Selective Model):** Reserved strictly for vetted, long-term partners with proven volume; strictly prohibited during cold outreach or standard prospecting.

### 1.4. Scoring Contract vs. Autonomous Implementation
This specification defines the **scoring contract**—the rules of evidence, point allocations, scoring anchors, and decision boundaries.
- In the current **supervised interactive runtime (Antigravity)**, human operators and supervised subagents apply this rubric to manually or semi-automatically evaluate researched prospects.
- In the future **autonomous runtime (Hermes)**, the Qualification Agent (`qualifier`) will consume this specification as its deterministic logic contract.
- In all phases, **every scorecard submitted by an agent remains a `draft`** and strictly requires review, verification, and endorsement by an authenticated **Human Operator** before lifecycle transitions or outreach workflows can be unlocked.

### 1.5. Three-Part Runtime Decoupling
In alignment with [`agents/agent-operating-model.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/agent-operating-model.md), this rubric is runtime-agnostic:
1. **Logical Scoring Specification (This Document):** Defines mathematical weights, scoring anchors, evidence thresholds, and disqualification triggers.
2. **Runtime Execution Adapter:** Operates interactively within Antigravity tool sessions today, transitioning to Hermes background orchestration when deployed.
3. **Database Persistence & Security Tier (`crm/schema.sql`):** Persists scorecards in `lead_qualifications`, enforces evidence constraints via database triggers, and restricts write approvals to human operators via Row Level Security (RLS).

---

## 2. Seven Scoring Dimensions (100-Point Model)

The IPP qualification scorecard evaluates prospective partners across seven distinct dimensions, totaling **100 maximum points**:

| Dimension | Max Points | Weight | Core Focus | Database Column Name |
| :--- | :---: | :---: | :--- | :--- |
| **1. Customer Flow** | **20** | 20% | Commercial client activity, project velocity, brand quality | `customer_flow_score` |
| **2. Development Demand** | **20** | 20% | Demonstrated technical need beyond standard visual builder limits | `development_demand_score` |
| **3. Repeatability of Orders** | **15** | 15% | Client retainers, recurring rollouts, ongoing platform maintenance | `repeatability_orders_score` |
| **4. Technical Fit** | **15** | 15% | Stack alignment with our 3 core service priorities | `technical_fit_score` |
| **5. Business Size** | **10** | 10% | Target studio team scale (5–50 employees) | `business_size_score` |
| **6. Decision-Maker Accessibility** | **10** | 10% | Direct, identifiable, and reachable route to key leadership | `decision_maker_accessibility_score` |
| **7. Commercial Fit** | **10** | 10% | Premium positioning, wholesale/white-label compatibility | `commercial_fit_score` |
| **Total** | **100** | **100%** | **Comprehensive Partner Viability Index** | `overall_score` |

```
                              100-POINT SCORING ALLOCATION
   ┌──────────────────────┬──────────────────────┬───────────────┬───────────────┬──────────┬──────────┬──────────┐
   │ Customer Flow        │ Development Demand   │ Repeatability │ Technical Fit │ Size     │ Access   │ Comm Fit │
   │ (20 pts)             │ (20 pts)             │ (15 pts)      │ (15 pts)      │ (10 pts) │ (10 pts) │ (10 pts) │
   └──────────────────────┴──────────────────────┴───────────────┴───────────────┴──────────┴──────────┴──────────┘
   ◄──────────────────── 40% ────────────────────►◄──────────── 30% ────────────►◄──────────── 30% ────────────►
                  Volume & Demand                           Fit & Architecture                  Maturity & Access
```

---

### 2.1. Dimension 1: Customer Flow (Max 20 Points)

#### What It Measures
The volume, cadence, quality, and commercial reality of the prospect's active client engagements. It assesses whether the agency has paying external clients who commission digital builds on an ongoing basis.

#### What It Does Not Measure
- It does not measure the prospect's visual aesthetics, design awards, or social media follower count.
- It does not measure speculative concepts, unsolicited redesigns, or self-initiated internal projects.

#### Explicit Scoring Anchors
- **0 Points:** Completely unknown or unverified customer flow; OR no credible evidence of commercial clients; personal student portfolio, speculative redesigns only, or defunct/inactive agency.
- **5 Points:** A few limited, dated, or weakly attributable client examples (e.g., 1–2 projects older than 18 months, or unverifiable client identities).
- **10 Points:** Multiple identifiable real clients and projects (e.g., 3–5 verifiable commercial client case studies published within the last 12–18 months).
- **15 Points:** Strong evidence of ongoing corporate/B2B customers and multiple projects (e.g., continuous stream of verified case studies, recognized brands, active client testimonials).
- **20 Points:** Verified active and continuing commercial pipeline (e.g., high delivery volume, enterprise client roster, published quarterly case studies, clear public hiring or client intake signals).

#### Acceptable Evidence Examples
- First-party case studies on official agency website naming corporate clients with live URL links.
- Third-party verified client reviews on Clutch, Google Business, or Webflow Experts Directory with confirmed dates.
- Video testimonials or detailed case retrospectives featuring corporate client executives.

#### Insufficient Evidence Examples
- Unattributed screenshots labeled "Confidential Client" with no verifiable business identity.
- Dribbble or Behance UI concept shots without a confirmed client brief or live deployment.
- High Twitter/LinkedIn follower counts without documented client deliverables.

#### Common Scoring Mistakes
- **Aesthetic Halo Effect:** Awarding 20 points because the agency's portfolio has beautiful WebGL motion, even though they only have two live client projects.
- **Historic Bias:** Awarding 20 points to an agency that won major awards in 2021 but has published zero client work in the past two years.

---

### 2.2. Dimension 2: Development Demand (Max 20 Points)

#### What It Measures
The technical complexity and frequency of software engineering requirements in the prospect's client work. It assesses whether their projects require custom code, backend architecture, database synchronization, external authentication, or API integrations that exceed the limits of visual website builders.

#### What It Does Not Measure
- It does not measure pure graphic design, branding typography, 3D art rendering, or static visual layout work.
- It does not measure whether the agency *wants* to do software development in theory; it measures empirical evidence of software engineering in their actual deliverables.

#### Explicit Scoring Anchors
- **0 Points:** Completely unknown or unverified development demand; OR pure branding, graphic design, marketing copywriting, or static-content brochure websites with zero custom code.
- **5 Points:** Occasional simple custom code snippets, embed widgets, basic contact form webhooks, or minor CSS styling overrides.
- **10 Points:** Actual projects involving third-party APIs, external databases, member authentication systems, or custom JavaScript/React application logic.
- **15 Points:** Complex or repeated technical work across multiple projects (e.g., multi-source data sync, headless CMS setups, bespoke calculators, gated customer portals).
- **20 Points:** Documented continuous demand for custom backend architectures, complex logic, and external systems, coupled with verifiable indicators of technical delivery bottlenecks or capacity constraints.

#### Acceptable Evidence Examples
- Case studies detailing customer portals, dynamic dashboards, external database integrations (PostgreSQL, Supabase, Airtable, Xano), or bespoke user login flows.
- Public agency GitHub repositories or proprietary developer tool releases (e.g., component booster libraries, API wrappers).
- Client reviews specifically commending functional web application delivery or complex integration work.

#### Insufficient Evidence Examples
- Complex visual animations (GSAP, Three.js) that are purely client-side and require zero backend data persistence.
- Generic marketing claims such as "We build full-stack digital solutions" unsupported by any documented project architecture.
- Use of off-the-shelf no-code visual plugins with zero bespoke engineering.

#### Common Scoring Mistakes
- **Confusing Visual Complexity with Engineering Complexity:** Assuming a site with continuous 3D scrolling has high backend development demand when it is actually a static WebGL canvas.
- **Assuming Internal Bottlenecks:** Scoring 20 without evidence that their engineering demand exceeds their current delivery capacity.

---

### 2.3. Dimension 3: Repeatability of Orders (Max 15 Points)

#### What It Measures
The commercial continuity and lifecycle duration of the agency's client relationships. It evaluates whether the agency operates on ongoing retainers, multi-phase platform roadmaps, and continuous technical support, or strictly delivers one-off transactional projects.

#### What It Does Not Measure
- It does not measure the total number of past clients (volume is measured in Customer Flow).
- It does not measure the agency's internal employee retention rate.

#### Explicit Scoring Anchors
- **0 Points:** Completely unknown client retention or contract history; OR strictly one-off transactional projects; "build and hand off" model with zero ongoing relationship.
- **4 Points:** Evidence of occasional post-delivery support, minor warranty periods, or sporadic client maintenance requests.
- **8 Points:** Ongoing maintenance, feature iteration, or continuous development contracts documented for a subset of clients.
- **12 Points:** Verified long-term commercial contracts, recurring monthly retainers, or multi-year continuous rollout agreements with corporate clients.
- **15 Points:** Stable, recurring commercial model built around dedicated retainers, continuous platform evolution, and documented recurring technical work across their primary client base.

#### Acceptable Evidence Examples
- Client case studies explicitly detailing multi-year, multi-phase roadmap deliveries (e.g., "Phase 1 launch in 2024, followed by continuous quarterly portal expansions throughout 2025–2026").
- Clutch or directory reviews mentioning "we have retained them for 2+ years for continuous platform management."
- Agency service offerings explicitly featuring "Dedicated Growth Retainers," "Ongoing Engineering Support," or "Continuous Optimization Partnerships."

#### Insufficient Evidence Examples
- A large portfolio containing 60 distinct logos with no evidence of ongoing work with any of them.
- Inferred repeat business based solely on client brand prestige.
- Agency marketing copy stating "we build lasting relationships" without operational proof of retainers.

#### Common Scoring Mistakes
- **Conflating Project Volume with Continuity:** Assuming an agency with 50 projects must have recurring retainers, when all 50 were one-and-done promotional landing pages.
- **Ignoring Project Types:** Overlooking that brand launch sites rarely carry ongoing backend retainers unless external application infrastructure is involved.

---

### 2.4. Dimension 4: Technical Fit (Max 15 Points)

#### What It Measures
The architectural compatibility between the prospect's technology ecosystem and our **three strategic service priorities**:
1. **Backend and API Development:** Relational databases (PostgreSQL/Supabase), custom API endpoints, authentication middleware, serverless functions, dynamic data pipelines.
2. **Webflow Extensions and Integrations:** Connecting Webflow sites to external databases, custom membership portals, webhook handling, and API synchronization.
3. **Framer Custom Development:** Authoring custom React components, client-side state handling, Framer-to-backend API connectivity, and advanced interactive logic.

#### What It Does Not Measure
- It does not measure compatibility with deferred or out-of-scope services (e.g., native iOS/Android mobile apps, legacy PHP/WordPress monoliths, enterprise SAP integrations).
- It does not measure the aesthetic quality of their Figma designs.

#### Explicit Scoring Anchors
- **0 Points:** Completely unknown technology stack; OR serious architectural incompatibility (e.g., strictly WordPress/PHP themes, legacy Drupal, Shopify Liquid-only storefronts, proprietary closed enterprise CMS) or reliance on technologies outside our strategic scope.
- **4 Points:** Limited compatibility requiring substantial workflow adaptation (e.g., visual builders with closed ecosystems, rare custom code needs).
- **8 Points:** Some technical needs overlap with our core services (e.g., standard Webflow or Framer builds with occasional external API requests).
- **12 Points:** Strong overlap with our core offerings: verified Webflow or Framer development combined with documented needs for backend architecture, custom APIs, external databases, or bespoke React components.
- **15 Points:** Strong technical overlap PLUS documented, standardized development workflows (e.g., strict adherence to Webflow Client-First styling, componentized React design systems in Framer, structured Git/GitHub code management, clean Figma developer handoffs).

#### Acceptable Evidence Examples
- Confirmed use of Webflow or Framer as the primary frontend delivery platform.
- Public documentation or job postings citing React, TypeScript, Supabase, PostgreSQL, or REST/GraphQL APIs.
- Case studies detailing web applications combining visual builders with external services (Stripe, Memberstack, custom auth).
- Adherence to recognized structural standards (e.g., Relume, Client-First style guide, component-driven Framer architecture).

#### Insufficient Evidence Examples
- Mentioning "HTML, CSS, and JS" on a website without context on modern frameworks or visual builder ecosystems.
- High technical sophistication in technologies outside our core priorities (e.g., Magento e-commerce, Unity game engines).

#### Common Scoring Mistakes
- **Rewarding Irrelevant Technical Prowess:** Scoring an agency 15 points because they have brilliant custom WebGL shaders, even though they build zero database-backed applications.
- **Overlooking Workflow Standards:** Giving full points to an agency using Webflow without checking whether their builds are structured and maintainable.

---

### 2.5. Dimension 5: Business Size (Max 10 Points)

#### What It Measures
The operational scale, organizational maturity, and leverage potential of the prospect studio. The ideal partner operates within a **sweet spot of 5 to 50 employees**: large enough to maintain steady commercial flow and structured scoping, but nimble enough to benefit from wholesale engineering without bureaucratic vendor procurement layers.

#### What It Does Not Measure
- It does not measure the individual talent or brilliance of a solo practitioner.
- It does not measure total agency capitalization or venture funding.

#### Explicit Scoring Anchors
- **0 Points:** Completely unknown or unverified team size or operational status; OR extreme mismatch: solo practitioner (1 person) with zero commercial leverage / hobbyist, OR enterprise holding company / large agency (>100 employees) with extensive in-house engineering departments.
- **3 Points:** Verifiable positive evidence of a very small micro-team (2–4 core people) with limited commercial bandwidth or early-stage operational structure.
- **6 Points:** Verifiable small boutique studio (5–9 people) with demonstrable commercial delivery cadence and active project volume.
- **8 Points:** Verifiable mid-sized studio within the core target sweet spot (10–24 people) with clear functional role separation across design, client account management, and visual development.
- **10 Points:** Verifiable established studio optimally scaled across the upper target sweet spot (25–50 people) with mature operational infrastructure, dedicated producers/delivery leads, and multi-year organizational stability.

#### Acceptable Evidence Examples
- Verified company LinkedIn page showing active employees filtered by relevant roles.
- Public team/about page listing core team members with identifiable roles (e.g., Founders, Art Directors, Visual Developers, Account Leads).
- Third-party registry or directory listings (Clutch, official Webflow/Framer partner profiles) confirming verified headcount range.

#### Insufficient Evidence Examples
- Inferred headcount based solely on office photos or marketing rhetoric ("We are a global collective").
- Unverified directory claims that contradict direct company statements.
- Including unpaid interns, advisory boards, or temporary contractors as core operational staff.
- Awarding 3 points merely because operations are "uncertain" (unknowns score 0).

#### Common Scoring Mistakes
- **Treating Large Enterprise as Ideal:** Scoring a 200-person agency 10 points, ignoring that they have in-house software teams and bureaucratic vendor approval processes that block wholesale partnerships.
- **Accepting Solo Freelancers:** Giving high size scores to solo freelancers because they have impressive portfolio work, ignoring that they lack project pipeline consistency.
- **Overlapping Ranges:** Conflating micro-teams (2–4) with established studios (10–24). The revised scale is strictly non-overlapping.

---

### 2.6. Dimension 6: Decision-Maker Accessibility (Max 10 Points)

#### What It Measures
The feasibility of identifying, reaching, and engaging the key executive or technical decision-maker responsible for engineering partnerships, delivery capacity, and vendor relationships.

#### What It Does Not Measure
- It does not measure general company responsiveness to customer support inquiries.
- It does not measure whether the decision-maker will accept a deal; it measures direct route accessibility.

#### Explicit Scoring Anchors
- **0 Points:** Decision-makers completely unknown; anonymous website with no identifiable leadership, no LinkedIn footprint, and no contact path beyond a generic web form.
- **3 Points:** Relevant founder, executive, or technical director identified by name and role, but with no direct, personal contact channel (e.g., no active LinkedIn, personal email unknown).
- **6 Points:** Valid, responsive company-level contact channel (e.g., official partnership/inquiry email managed by leadership, active founder social channel).
- **8 Points:** Direct, personal route identified to the relevant decision-maker (e.g., verified personal professional email, active LinkedIn InMail route, direct founder inbox).
- **10 Points:** Warm introduction path, prior direct relationship, verified mutual contact, or confirmed prior positive interaction. *(Strict Evidence Rule: Anchor 10 requires a verifiable prior relationship, warm intro, or confirmed direct interaction; direct cold outreach routes cap at Anchor 8).*

#### Acceptable Evidence Examples
- Verified direct professional email address confirmed through company domain patterns and deliverability checks.
- Active personal LinkedIn profile for the Founder, CEO, CTO, or Head of Delivery with demonstrable platform activity within the last 30 days.
- Public podcast interviews or articles where the founder explicitly publishes their preferred direct contact channel for business inquiries.

#### Insufficient Evidence Examples
- Generic `info@`, `contact@`, or `hello@` email addresses with no confirmation of who monitors the inbox.
- A public website contact form with zero indication of recipient routing.
- A founder's personal Twitter/X profile that has been dormant for two years.

#### Common Scoring Mistakes
- **Confusing Name Identification with Accessibility:** Awarding 8 points simply because the website lists the CEO's name, without having a viable, direct communication path.
- **Awarding Anchor 10 to Cold Leads:** Assigning 10 points simply because a founder's profile is public, even though no prior relationship or warm introduction exists.
- **Overvaluing Contact Forms:** Scoring a web form as a direct route to executive leadership.

---

### 2.7. Dimension 7: Commercial Fit (Max 10 Points)

#### What It Measures
The commercial alignment between the prospect's pricing positioning, customer base, and our wholesale partnership model. It evaluates whether the partner commands sufficient client project fees to sustain wholesale development margins and is culturally receptive to a **behind-the-scenes, white-label delivery relationship**.

#### What It Does Not Measure
- It does not measure our own internal profitability or pricing targets.
- It does not measure whether the partner will sign a contract today.

#### Explicit Scoring Anchors
- **0 Points:** Completely unknown or unverified commercial model, pricing tiers, or outsourcing posture; OR clear commercial mismatch (commodity price shopper, demands unpaid speculative work, offshore rate expectations, or refuses non-circumvention/wholesale terms).
- **3 Points:** Limited verifiable positive commercial signals: evidence of standard commercial B2B billing (e.g., modest verified project minimums around $5k–$10k or standard commercial rates), but without premium margin cushion or any verified outsourcing history.
- **5 Points:** Premium market positioning: verifiable corporate/B2B customer base and documented premium budget indicators (e.g., verified project minimums of $20k–$25k+ or $100–$150+/hr rates) providing economic margin for wholesale subcontracting, but outsourcing posture and wholesale willingness remain unverified (`information_gap_tbd`). *(Strict Rule: Premium positioning alone does NOT imply outsourcing acceptance).*
- **7 Points:** Documented collaboration with external technical partners or contractors: premium positioning PLUS explicit evidence that the agency actively engages external software developers, engineering contractors, or technical vendor partners for client delivery. *(Strict Rule: A general creative/freelancer network or design collective does NOT qualify for 7 points unless there is verifiable evidence of external software engineering engagement).*
- **10 Points:** Explicitly verified acceptance of a White-Label / Wholesale partnership model: verified public policy, direct executive statement, or confirmed commercial history of deploying behind-the-scenes white-label development partners, or an active public search for a dedicated managed development partner.

#### Acceptable Evidence Examples
- Published project minimums or hourly rates on directories (e.g., Clutch showing $25k+ project minimums or $100–$150+/hr rates) providing economic room for wholesale margins.
- Case studies or agency blog posts explicitly discussing their hybrid delivery model using specialized external software engineering partners.
- Public statements by agency leadership emphasizing their focus on core visual design while delegating complex backend engineering to technical partners.

#### Insufficient Evidence Examples
- Premium visual aesthetic alone, which does not guarantee high project pricing or healthy commercial margins.
- Third-party directory pricing aggregations that are outdated, contradictory, or unverified.
- Treating a general creative specialist network (copywriters, illustrators, 3D artists) as proof of external software development outsourcing.
- Assuming an agency will outsource simply because they charge high rates.

#### Common Scoring Mistakes
- **Assuming High Aesthetic Means High Rates:** Believing that an award-winning site design implies a $50k+ client budget, when the studio may have built it for a friend at cost.
- **Treating Premium Pricing as Proof of Outsourcing:** Awarding 7 or 8 points to an agency that charges high rates but has an unverified outsourcing posture (must be capped at 5).
- **Conflating General Freelancer Networks with Development Partners:** Assuming an agency that hires freelance animators also outsources software development.

---

### 2.8. Intermediate Scoring Governance
While the scoring anchors define standard benchmarks (0, 5, 10, 15, 20 or 0, 4, 8, 12, 15), intermediate integer scores (e.g., 6, 7, 11, 13, 17) are permitted **only under strict governance**:
- The evaluator must provide an explicit, documented explanation in `dimension_evidence` justifying why the prospect exceeds one anchor but fails to satisfy the next.
- Arbitrary fractional or ungrounded scores (e.g., awarding 13.5 without precise justification) are prohibited.

---

## 3. Evidence Classification & Epistemic Standards

### 3.1. Canonical Evidence Types (`research_finding_type`)
In strict alignment with `crm/schema.sql`, all research statements and scorecard inputs must be classified under one of three canonical PostgreSQL enum values:

```sql
CREATE TYPE research_finding_type AS ENUM (
    'verified_fact',           -- Backed by direct public evidence (URL, portfolio item, client quote)
    'hypothesis',              -- Inference requiring further qualification or conversational discovery
    'information_gap_tbd'      -- Unknown parameter explicitly flagged for discovery (never assumed)
);
```

1. **`verified_fact`:**
   - Empirical, publicly accessible, first-party data directly documented by the agency or confirmed by reputable client sources.
   - **Mandatory Requirements:** Must include a valid `evidence_url`, verbatim or concise `evidence_excerpt`, and a confidence rating of `high` or `medium` (enforced by SQL check constraint `chk_verified_fact_requires_evidence`).
2. **`hypothesis`:**
   - A logical deduction, strategic inference, or operational interpretation synthesized from verified facts.
   - Example: *"Because Refokus focuses on branding and Webflow frontend motion, partnering with a backend wholesale developer would allow them to capture full-stack contracts without backend hires."*
   - Hypotheses guide outreach angles and discovery questions; they **cannot substitute for empirical facts**.
3. **`information_gap_tbd`:**
   - An essential operational, commercial, or technical parameter that could not be determined from public evidence.
   - Must be explicitly labeled as **`TBD`** (To Be Determined).
   - Flagging an information gap preserves epistemic honesty and prevents premature assumptions.

### 3.2. Evidence Classification vs. Confidence Level
Evaluators must never confuse an **evidence classification** with a **confidence level**:

| Dimension | Evidence Classification (`research_finding_type`) | Source Confidence (`confidence_level_type`) |
| :--- | :--- | :--- |
| **What It Governs** | The epistemic status of the statement (Fact vs. Inference vs. Unknown). | The reliability and directness of the information source. |
| **Enum Values** | `'verified_fact'`, `'hypothesis'`, `'information_gap_tbd'` | `'high'`, `'medium'`, `'low'`, `'unverified'` |
| **Key Rule** | **High confidence does NOT turn a hypothesis into a fact.** | High confidence means the source is authoritative, not that an inference is empirically proven. |

#### Critical Epistemic Distinctions:
- **An Unknown is NOT a Negative Fact:** The absence of public information regarding an agency's backend stack does not prove they lack backend needs; it simply means it is an `information_gap_tbd`.
- **Directory Estimates are NOT Confirmed Direct Facts:** Third-party directory listings (e.g., Clutch hourly rates, employee counts, minimum project sizes) are aggregations or self-reported ranges. They must be cataloged as `medium` confidence third-party indicators and never presented as verified contractual facts.
- **Traceability Requirement:** Every positive dimension score (`> 0`) must link directly to structured evidence records in `dimension_evidence`.

### 3.3. Required Schema for `dimension_evidence` JSONB
Under database trigger `fn_enforce_lead_qualification_rules`, any positive dimension score must have structured supporting evidence persisted in the `dimension_evidence` JSONB column.

Each dimension key (`customer_flow`, `development_demand`, `repeatability_orders`, `technical_fit`, `business_size`, `decision_maker_accessibility`, `commercial_fit`) must adhere to this structured format:

```json
{
  "customer_flow": {
    "score": 15.00,
    "max": 20,
    "source_url": "https://agency.com/work",
    "source_type": "first_party_case_study",
    "observed_date": "2026-11-08",
    "excerpt": "Over 25 corporate B2B platforms launched in 2025-2026, including Meridian and Analog Way.",
    "finding_type": "verified_fact",
    "confidence": "high",
    "score_justification": "Verified continuous roster of enterprise B2B accounts with active 2026 project launches satisfies the 15-point anchor."
  },
  "development_demand": {
    "score": 10.00,
    "max": 20,
    "source_url": "https://tools.agency.com",
    "source_type": "first_party_developer_portal",
    "observed_date": "2026-11-08",
    "excerpt": "Publishes proprietary JavaScript extensions and CMS filtering scripts for Webflow.",
    "finding_type": "verified_fact",
    "confidence": "high",
    "score_justification": "Demonstrates actual code development and API integration in Webflow, satisfying 10-point anchor; backend architecture remains unverified."
  }
}
```

---

## 4. The Zero-Evidence Rule

### 4.1. Foundational Policy: Unknown Parameters Receive Zero Positive Points
The foundational integrity rule of IPP Scoring Rubric v2 is the **Zero-Evidence Rule**:

$$\text{If an operational parameter is unverified, unknown, or speculative} \implies \text{Score} = 0 \text{ positive points}.$$

No evaluator—whether human or agent—is permitted to award points based on:
- "Plausible industry assumptions."
- "The agency looks highly professional, so they probably have this."
- "They have high rates, so they surely have retainers."
- "Their founder is well-known, so they must be easy to reach."
- "They have an extended network, so they surely outsource development."

Database trigger `fn_enforce_lead_qualification_rules` enforces this at the engine level: any score greater than zero requires explicit, documented evidence in `dimension_evidence`.

### 4.2. Explicit Practical Scenarios
To ensure absolute clarity, the following specific evaluation scenarios must strictly adhere to the Zero-Evidence Rule:

1. **Named Founder Without Contact Route:**
   - *Scenario:* The agency website features a photo and bio of the Founder & CEO, but provides no direct email, no personal LinkedIn link, and only a generic `info@agency.com` address.
   - *Scoring Reality:* Awarding points for a direct executive route (8 or 10) is strictly forbidden. The score must be capped at 3 (founder identified, no route) or 6 (valid public company channel).
2. **Large Portfolio Without Retainer Evidence:**
   - *Scenario:* An agency showcases 40 delivered projects, but every case study describes a discrete launch with no mention of continuous maintenance, post-launch phases, or retainers.
   - *Scoring Reality:* Repeatability of Orders cannot score 8, 12, or 15 based on volume. It must receive 0 (purely transactional) or 4 (inferred warranty support), with retainer status flagged as `information_gap_tbd`.
3. **Complex Projects Without Unmet Demand Proof:**
   - *Scenario:* An agency delivers sophisticated Webflow sites with custom React embeds.
   - *Scoring Reality:* This proves *actual technical work* (Development Demand = 10–15), but it does **not** prove they need an external development partner. Unmet engineering demand cannot be awarded points unless there is evidence of delivery delays, engineering hiring struggles, or stated subcontracting needs.
4. **Premium Positioning Without Outsourcing Willingness:**
   - *Scenario:* Clutch lists an agency's average project size as $50k+ and hourly rates at $150/hr.
   - *Scoring Reality:* This justifies Commercial Fit anchor 5 (premium positioning). It does **not** justify anchor 7 (evidence of outsourcing) or 10 (white-label acceptance). Willingness to use an external partner remains an information gap.
5. **Public Contact Form vs. Decision-Maker Accessibility:**
   - *Scenario:* The agency has an intake form on their `/contact` page.
   - *Scoring Reality:* A public inquiry form is an intake channel for sales prospects, not a direct route to executive leadership. It can score at most 6 (valid company channel), never 8 or 10.
6. **Unknown Team Size or Operational Status:**
   - *Scenario:* The agency website lacks an about/team page, LinkedIn reports 0–1 employees, and third-party directory listings are missing or contradictory.
   - *Scoring Reality:* Business Size must receive 0 positive points. Under no circumstances can points (e.g., 3) be awarded for "uncertain operations." Positive scores strictly require positive proof of team scale.
7. **General Specialist Network vs. External Software Development Contracting:**
   - *Scenario:* An agency's marketing states they are "supported by an extended network of digital specialists" or "collaborate with freelance talents."
   - *Scoring Reality:* A general creative talent network (copywriters, illustrators, motion designers) does NOT prove the agency engages external software developers. Commercial Fit cannot score 7 or 8 based on a general network statement; unless explicit evidence documents external software engineering contracting, Commercial Fit caps at 5 (if premium rates are verified) or 0 (if pricing is unverified).

### 4.3. Unknowns Are Information Gaps, Not Automatic Disqualifiers
The Zero-Evidence Rule prevents score inflation; it does **not** mandate instant disqualification:
- An unknown retainer model simply scores 0 (or lower anchor) for Repeatability; it does not disqualify the lead.
- An unverified backend stack simply scores lower in Technical Fit; it does not brand the prospect illegitimate.
- Unknown parameters are cataloged in `gaps_and_unknowns` as **`TBD`** to be investigated during human review or addressed in exploratory discovery dialogue.

---

## 5. Seven Critical Analytical Distinctions

Evaluators must rigorously maintain seven analytical distinctions to avoid common qualification traps:

```
┌───────────────────────────────────────┬───────────────────────────────────────┐
│           SURFACE ASSUMPTION          │           OBJECTIVE REALITY           │
├───────────────────────────────────────┼───────────────────────────────────────┤
│ 1. Market demand is high              │ Prospect's actual demand may be zero  │
│ 2. Prospect has technical demand      │ Demand may be 100% met in-house       │
│ 3. Agency has dozens of past projects │ Projects may be 100% one-off launches │
│ 4. Agency has deep technical skill    │ Agency may strictly refuse outsourcing│
│ 5. Decision-maker is publicly known   │ Decision-maker may be unreachable     │
│ 6. Agency charges premium rates       │ Agency may reject white-label models  │
│ 7. Agency has major industry awards   │ Agency may be operationally chaotic   │
└───────────────────────────────────────┴───────────────────────────────────────┘
```

### 5.1. Market Demand vs. Prospect's Actual Development Demand
- **Distinction:** High macro demand for web applications in the broader economy does not mean a specific creative agency receives software development inquiries.
- **Rule:** The prospect must show direct evidence of technical inquiries in their own portfolio or client testimonials.

### 5.2. Actual Development Demand vs. Unmet Demand Fulfillable by Us
- **Distinction:** An agency may deliver complex full-stack web applications, but possess a full-time, 10-person in-house engineering team that operates smoothly with zero backlog.
- **Rule:** High development demand only creates partnership opportunity if there is an operational mismatch—such as a design-heavy team lacking dedicated backend engineering, or documented delivery overflow.

### 5.3. One-Off Projects vs. Recurring Commercial Relationships
- **Distinction:** A high volume of distinct project logos indicates customer acquisition ability, but does not indicate recurring commercial retainers.
- **Rule:** Continuous revenue potential requires evidence of multi-phase engagements, long-term maintenance contracts, or ongoing platform evolution.

### 5.4. Technical Capability vs. Willingness to Use an External Partner
- **Distinction:** An agency may possess elite technical literacy and build custom software, yet adhere to an ideological policy of "100% in-house engineering, zero outsourcing."
- **Rule:** High technical fit must not be conflated with commercial receptivity to wholesale white-label subcontracting.

### 5.5. Publicly Identifiable Decision-Makers vs. Reachable Decision-Makers
- **Distinction:** Discovering the name and biography of an agency founder on their `/about` page does not establish a viable, deliverable communication channel.
- **Rule:** Accessibility points require a verified, active route (direct professional email, active LinkedIn InMail, or warm referral path).

### 5.6. Premium Positioning vs. Verified Wholesale/White-Label Acceptance
- **Distinction:** High client rates and prestigious corporate clients prove financial viability, but do not prove willingness to share margins with a wholesale development partner.
- **Rule:** Commercial fit scoring must separate rate health (anchor 5) from verified acceptance of white-label collaboration (anchors 7 and 10).

### 5.7. Company Reputation vs. Actual Commercial Compatibility
- **Distinction:** Prestigious design awards (Awwwards, CSSDA, FWA) indicate world-class visual craft, but often correlate with hyper-bespoke, scope-fluid, non-standardized workflows that create friction in structured wholesale development.
- **Rule:** High prestige must be balanced against operational scoping maturity and willingness to work under structured Scopes of Work (SOWs) and milestone acceptance procedures.

---

## 6. Disqualification Framework & Database Constraints

### 6.1. Hard Disqualifiers (Evidence-Backed Rejection)
A prospect must be immediately disqualified if verifiable evidence demonstrates any of the following five conditions:

1. **Clearly Outside Approved Target Segments:**
   - Solitary freelancers without commercial leverage or subcontractors.
   - Consumer hardware, pure print/branding agencies, or marketing agencies with zero web presence.
   - Massive enterprise holding companies (>100+ staff) with entrenched, bureaucratic vendor approval mazes.
2. **Confirmed Refusal to Use External Suppliers:**
   - Explicit public statements or direct communications confirming a strict, unbending policy: *"We build 100% in-house and never work with external contractors or engineering vendors."*
3. **Confirmed Commercial Model Incompatibility:**
   - Demand for speculative, unpaid development builds or contingent equity-only compensation.
   - Commodity offshore price shopping (demanding lowest hourly rates below viable professional thresholds).
   - Unwillingness or refusal to operate under written Scopes of Work (SOWs), upfront deposits, or formal change order procedures.
4. **Consistent Requests Outside Service Scope:**
   - Prospect requirements exclusively demand out-of-scope services: native mobile applications (iOS/Android Swift/Kotlin), legacy monolith migrations (PHP/Drupal), enterprise ERP implementations, or blockchain smart contracts.
5. **Verified Commercial Integrity or Ethical Concerns:**
   - Documented history of unpaid vendor claims, public litigation with suppliers, refusal to sign mutual non-circumvention agreements, or fraudulent business practices.

### 6.2. Information Gaps vs. Hard Disqualifiers
Evaluators must maintain a strict boundary between verified disqualifiers and unresolved unknowns:
- **Hard Disqualifier:** Supported by verifiable, documented evidence. Causes immediate assignment of `qualification_tier = 'disqualified'` and freezes downstream outreach.
- **Information Gap:** Unknown parameters (e.g., unknown outsourcing policy, unknown backend preference, unconfirmed project minimums). Scored as **zero positive points**, cataloged as `information_gap_tbd`, but **never** used as grounds for automatic disqualification.

### 6.3. Database Constraint Alignment (`crm/schema.sql`)
The disqualification logic documented here strictly aligns with the database schema and triggers:
- **Database Column:** `lead_qualifications.disqualification_reasons TEXT[]` stores specific disqualification codes.
- **Database Constraint & Trigger (`fn_enforce_lead_qualification_rules`):**
  ```sql
  has_dq := (cardinality(NEW.disqualification_reasons) > 0);
  IF has_dq OR NEW.overall_score < 40 THEN
      IF NEW.qualification_tier != 'disqualified' THEN
          RAISE EXCEPTION 'Tier Integrity Violation: Leads with disqualification reasons or score < 40 must be disqualified.';
      END IF;
  ```
- **Lifecycle Synchronization (`fn_sync_company_qualification`):** When the Human Operator approves a scorecard with `qualification_tier = 'disqualified'`, the company's lifecycle stage is automatically updated to `'disqualified'`, preventing the creation or dispatch of outreach messages.

---

## 7. Qualification Tier Interpretation & Progression Rules

### 7.1. Score Bands and PostgreSQL Enums
Scorecards evaluate prospects into four distinct tiers based on the 100-point total:

| Score Band | PostgreSQL Enum Value (`qualification_tier_type`) | Human-Readable Tier Label | Operational Meaning & Routing Action |
| :---: | :--- | :--- | :--- |
| **80 – 100** | `'tier_1_priority'` | **Tier 1 (Priority)** | Premier wholesale partner candidate. Exceptional client flow, high recurring demand, verified technical fit. Immediate priority for Human Operator review and personalized outreach drafting. |
| **60 – 79** | `'tier_2_prospective'` | **Tier 2 (Prospective)** | Viable wholesale candidate. Solid commercial foundation with minor information gaps or moderate technical complexity. Routed to Human Operator review; viable for exploratory outreach. |
| **40 – 59** | `'tier_3_deferred'` | **Tier 3 (Deferred)** | Niche or future expansion prospect. Significant information gaps or limited current development demand. Retained in CRM; outreach deferred until primary segments are exhausted. |
| **< 40 (or DQ)** | `'disqualified'` | **Disqualified** | Fails minimum viability threshold (<40 pts) or triggers an explicit hard disqualifier. Company stage set to `disqualified`; permanently blocked from outreach. |

### 7.2. Strict Distinction: Human-Readable Labels vs. PostgreSQL Enums
To prevent database syntax errors and maintain data integrity, all documentation, agent contracts, and database queries must distinguish human-readable display labels from underlying PostgreSQL enum values:

- Display label *"Tier 1 Priority"* $\iff$ PostgreSQL enum `'tier_1_priority'`.
- Display label *"Tier 2 Prospective"* $\iff$ PostgreSQL enum `'tier_2_prospective'`.
- Display label *"Tier 3 Deferred"* $\iff$ PostgreSQL enum `'tier_3_deferred'`.
- Display label *"Disqualified"* $\iff$ PostgreSQL enum `'disqualified'`.

### 7.3. A High Numerical Tier Does NOT Automatically Authorize Outreach
Achieving a score of 80+ (`tier_1_priority`) or 60+ (`tier_2_prospective`) does **NOT** grant authorization to draft or send outreach:
1. **Mandatory Human Review Gate:** The scorecard is initially persisted with `review_status = 'draft'`. Only an authenticated Human Operator (`is_human_operator() = TRUE`) can endorse the evaluation and transition it to `'approved'`.
2. **Outreach Suitability Check:** A prospect may achieve Tier 1 on customer flow and technical craft, but still be unsuitable for immediate outreach (e.g., pending company rebrand, missing verified direct contact email, or sensitive competitive timing).
3. **Outreach Drafting Gate:** Outreach preparation is an independent workflow (Workflow 4) requiring explicit task assignment and separate human approval before any message can enter dispatch queues.

### 7.4. Company Lifecycle State Transitions
When a qualification scorecard is approved by the Human Operator, trigger `fn_sync_company_qualification()` updates the parent company record:
- If `qualification_tier = 'disqualified'` $\implies$ `companies.lifecycle_stage` transitions to `'disqualified'`.
- If `overall_score >= 60` and no disqualifiers $\implies$ `companies.lifecycle_stage` transitions from `'identified'` or `'researching'` to `'qualified'`.
- Advanced lifecycle stages (`active_conversation`, `negotiating`, `partner_active`) are preserved and protected against regression.

---

## 8. Human Reviewer Qualification Readiness Checklist

Before an authenticated Human Operator signs off on an agent-generated or manually compiled scorecard (`review_status = 'approved'`), they must systematically verify all eight readiness checks:

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                    HUMAN REVIEWER QUALIFICATION READINESS CHECKLIST               │
├───────────────────────────────────────────────────────────────────────────────────┤
│ [ ] 1. EVIDENCE QUALITY: All positive scores (>0) backed by verifiable URLs/quotes│
│ [ ] 2. SCORE JUSTIFICATION: Explicit rationale documented for all 7 dimensions    │
│ [ ] 3. UNRESOLVED GAPS: Key TBDs identified; no speculative assumptions filled   │
│ [ ] 4. DISQUALIFICATION AUDIT: Confirmed zero hard disqualifiers present          │
│ [ ] 5. COMMERCIAL COMPATIBILITY: Budget/rates align with wholesale project model  │
│ [ ] 6. TECHNICAL COMPATIBILITY: Overlaps with Backend, Webflow, or Framer priorities│
│ [ ] 7. DECISION-MAKER ACCESSIBILITY: Reachable professional contact confirmed     │
│ [ ] 8. OUTREACH READINESS: Prospect is contextually appropriate for active contact│
└───────────────────────────────────────────────────────────────────────────────────┘
```

1. **Evidence Quality & Freshness:**
   - Are all positive scores supported by direct evidence links and excerpts?
   - Is the evidence recent (ideally within the last 12–18 months)?
   - Are third-party directory estimates clearly designated as estimates rather than confirmed facts?
2. **Score Justification Audit:**
   - Does the written rationale in `dimension_evidence` clearly substantiate the assigned point value against the explicit scoring anchors?
   - If intermediate scores were awarded, is the specific rationale documented?
3. **Unresolved Critical Information Gaps (`TBD`):**
   - Are unknown parameters explicitly cataloged as `information_gap_tbd`?
   - Did the evaluator resist the temptation to fill unknowns with generous assumptions?
4. **Disqualification Checks:**
   - Has the evaluator verified that the prospect does not violate any of the 5 hard disqualification rules?
   - If disqualification is asserted, is it backed by concrete evidence rather than guesswork?
5. **Commercial Compatibility:**
   - Does the agency command sufficient project fees to support wholesale margin?
   - Is the prospect suitable for our initial **Project-Based Wholesale Development** model?
6. **Technical Compatibility:**
   - Does the prospect's primary technical need map to our three core strategic priorities (Backend/API, Webflow extensions, Framer React)?
7. **Decision-Maker Accessibility:**
   - Is there a verified, reachable route to a founder, CEO, or technical director?
   - Has the contact been reviewed for future human-controlled ingestion into `contacts`?
8. **Outreach Readiness Assessment:**
   - Even if the score qualifies for Tier 1 or Tier 2, is the prospect *actually ready* for outreach?
   - Has an authentic, non-generic personalization angle been identified from their verified portfolio work?

---

## 9. Manual Calibration Case Studies (Provisional Reference)

### 9.1. Purpose of Calibration Cases
To stress-test and calibrate the scoring anchors of Rubric v2, three candidate prospects were manually reviewed: **Refokus**, **Kvalifik**, and **Trueform**.

> [!WARNING]
> **Provisional Calibration Data Only:** The scores below represent **provisional manual calibration exercises** conducted during rubric development. They are **not** persisted, authoritative CRM database records. They serve exclusively to expose rubric edge cases, illustrate scoring variance, and establish evaluation discipline.

---

### 9.2. Refokus (Webflow Studio) — Provisional Score: 66.00 / 100 (`tier_2_prospective`)

- **Target Profile:** Refokus (`refokus.com`) — Elite Webflow Enterprise Partner specializing in brand strategy, visual identity, motion graphics, and advanced Webflow engineering for high-growth B2B tech startups and global brands.
- **Primary Domain:** `refokus.com`
- **Market Segment:** `webflow_studio`
- **Recalculated Calibration Total:** **66.00 / 100**
- **Assigned Qualification Tier:** **`tier_2_prospective`** (Tier 2 Prospective)

#### Granular 7-Dimension Traceable Evidence Records:

1. **Customer Flow: 15.00 / 20**
   - **Source URL:** `https://refokus.com/work` (Corroborated by `https://webflow.com/partners/refokus`)
   - **Source Type:** First-party portfolio & official platform partner directory
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Delivered brand and viral Webflow websites for Meridian (AI search engine), Arqitel (Three.js/3D data scrollytelling), Analog Way, and Exebenus. Past collaborations include BCG, Yahoo!, Spotify. Over 200 projects delivered."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Satisfies Anchor 15 (strong evidence of ongoing corporate/B2B customers and multiple projects across high-profile brands). Anchor 20 withheld because real-time pipeline volume and current intake capacity remain unverified direct facts (`information_gap_tbd`).

2. **Development Demand: 10.00 / 20**
   - **Source URL:** `https://tools.refokus.com` (Corroborated by `https://github.com/refokus-agency`)
   - **Source Type:** First-party developer portal & public open-source repository
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Refokus Tools (tools.refokus.com) is a popular collection of free, custom-built utilities, boosters, and advanced features designed specifically to extend the capabilities of Webflow (CMS filters, pagination, form validation)."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Demonstrates actual programming logic, JavaScript extensions, and DOM manipulation in Webflow, satisfying Anchor 10. Anchor 15/20 withheld because actual external relational databases (PostgreSQL/Supabase) and custom server-side APIs are unverified from public sources (`information_gap_tbd`).

3. **Repeatability of Orders: 8.00 / 15**
   - **Source URL:** `https://refokus.com/work` (Cited in `reports/research/refokus-company-research.md`)
   - **Source Type:** First-party client case studies
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Continuous platform iterations and multi-phase brand evolution documented for scaling tech clients (Meridian, Arqitel); selective ongoing retainers noted in commercial model."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Ongoing maintenance and continuous platform development for select clients satisfy Anchor 8. Anchor 12/15 withheld because formal retainer contract terms, multi-year rollout commitments, and billing proportions remain undisclosed (`information_gap_tbd`).

4. **Technical Fit: 12.00 / 15**
   - **Source URL:** `https://webflow.com/partners/refokus` & `https://refokus.com`
   - **Source Type:** Official platform partner listing & agency website
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Certified Webflow Enterprise Partner specializing in advanced custom development... Client-First styling, modular component boilerplates, and custom React/JavaScript integrations."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Deep technical alignment with our Webflow extension priority and modern component styling standards satisfies Anchor 12. Anchor 15 withheld because standardized backend collaboration protocols and preferred database runtimes remain unverified (`information_gap_tbd`).

5. **Business Size: 8.00 / 10**
   - **Source URL:** `https://clutch.co/profile/refokus` (Observed 2026-11-08) & `https://refokus.com`
   - **Source Type:** Third-party directory profile (corroborated by first-party about page)
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Boutique agency averaging between 10 to 49 employees... remote-first distributed team with footprints in New York City, USA and Hamburg, Germany (~20-25 core team members across design, motion, and visual development)."
   - **Evidence Classification & Confidence:** `verified_fact` | `medium` (directory estimate corroborated by primary website)
   - **Anchor Justification:** Established team of 10–24 core professionals with clear role specialization satisfies Anchor 8 (mid-sized studio, 10–24 people, with clear functional separation).

6. **Decision-Maker Accessibility: 8.00 / 10**
   - **Source URL:** `https://refokus.com` & executive LinkedIn profiles
   - **Source Type:** First-party website & public professional network
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Leonardo (Leo) Zakour – Co-Founder & CEO; Stefanie (Steffi) Zakour – Co-Founder & COO overseeing operations and delivery scaling."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Key co-founders identified with direct professional profiles and identifiable email routes satisfy Anchor 8 (direct route to relevant decision-makers). Anchor 10 withheld because no warm introduction or prior direct interaction exists.

7. **Commercial Fit: 5.00 / 10**
   - **Source URL:** `https://clutch.co/profile/refokus` (Observed 2026-11-08)
   - **Source Type:** Third-party directory profile
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Min. project size: $25,000+; Avg. hourly rate: $100 - $149 / hr (current observed 2026-11-08 listing; historical date-unverified listing cited $10,000+ / $150-$199/hr)."
   - **Evidence Classification & Confidence:** `verified_fact` | `medium` (third-party directory estimate)
   - **Anchor Justification:** Premium B2B market positioning comfortably supporting wholesale margin satisfies Anchor 5. Anchor 7/10 strictly withheld because willingness to subcontract or collaborate with external white-label development partners is completely unverified (`information_gap_tbd`).

#### Refokus Score Summary:
$$\text{Total Score} = 15.00 + 10.00 + 8.00 + 12.00 + 8.00 + 8.00 + 5.00 = \mathbf{66.00 / 100} \implies \text{'tier\_2\_prospective'}$$

#### Analysis of the Historical 60 vs. 71 Discrepancy & Recalibration to 66:
In earlier exploratory discussions, Refokus was cited with an informal score of **60** in some notes and **71** in others. When evaluating Refokus strictly against Rubric v2's evidence anchors, the mathematically consistent provisional total is **66.00**:
- **Why 71 was an over-score:** The 71 score resulted from generous assumptions that violated the Zero-Evidence Rule: evaluators awarded 18/20 in Development Demand (assuming backend engineering based on visual motion), 7/10 in Commercial Fit (assuming outsourcing willingness without proof), and 9/10 in Business Size.
- **Why 60 was an under-score:** The earlier informal 60 arose when evaluators assigned lower conservative anchors (Repeatability at 4 for warranty-only, Tech Fit at 8 for basic builder overlap, while keeping Commercial Fit at 5), totaling $15 + 10 + 4 + 8 + 8 + 8 + 5 = 58 \approx 60$.
- **Why 66.00 is the calibrated reality:** Grounding each dimension in verifiable facts yields exactly 66.00. Refokus qualifies comfortably as Tier 2 (`tier_2_prospective`), with backend demand and white-label receptivity remaining explicit information gaps (`information_gap_tbd`).

---

### 9.3. Kvalifik (Digital Agency / Visual Studio) — Provisional Score: 71.00 / 100 (`tier_2_prospective`)

- **Target Profile:** Kvalifik (`kvalifik.dk`) — Prominent Copenhagen-based digital product and innovation agency and certified Webflow Professional Partner specializing in branding, web applications, and digital platforms.
- **Primary Domain:** `kvalifik.dk`
- **Market Segment:** `digital_agency`
- **Recalculated Calibration Total:** **71.00 / 100** (Lowered from 73.00)
- **Assigned Qualification Tier:** **`tier_2_prospective`** (Tier 2 Prospective)

#### Granular 7-Dimension Traceable Evidence Records:

1. **Customer Flow: 15.00 / 20**
   - **Source URL:** `https://kvalifik.dk/en` & `https://webflow.com/partners/kvalifik`
   - **Source Type:** First-party agency portfolio & platform directory
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Portfolio of live web platforms for Danish and European corporate accounts, scale-ups, and NGOs (Mærsk Mc-Kinney Møller Center, Dixa, Grandhood); awarded Webflow Professional Partner of the Year."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Verified roster of recognized European corporate, institutional, and scale-up accounts with continuous deliverables satisfies Anchor 15. Anchor 20 withheld because real-time pipeline volume is unverified.

2. **Development Demand: 15.00 / 20**
   - **Source URL:** `https://kvalifik.dk/en/services` & `https://clutch.co/profile/kvalifik`
   - **Source Type:** First-party services page & third-party directory profile
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Custom software development, web applications, API integrations, and complex data migration from legacy platforms (WordPress/Umbraco) to modern stacks."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Documented delivery of bespoke software applications, API integrations, and database migrations repeated across projects satisfies Anchor 15. Anchor 20 withheld because internal capacity bottlenecks are not confirmed.

3. **Repeatability of Orders: 8.00 / 15**
   - **Source URL:** `https://clutch.co/profile/kvalifik` & `https://kvalifik.dk/en`
   - **Source Type:** Third-party verified client reviews & case studies
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Verified Clutch reviews document multi-year engagements for continuous platform evolution, ongoing feature development, and marketing/product support."
   - **Evidence Classification & Confidence:** `verified_fact` | `medium` (verified directory reviews)
   - **Anchor Justification:** Continuous platform development and multi-year relationships for select clients satisfy Anchor 8. Anchor 12/15 withheld because formal retainer contract proportions are unverified (`information_gap_tbd`).

4. **Technical Fit: 12.00 / 15**
   - **Source URL:** `https://kvalifik.dk/en` & `https://webflow.com/partners/kvalifik`
   - **Source Type:** First-party website & platform directory
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Certified Webflow Partner delivering custom code embeds, API middleware, React integrations, and relational database connections."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Strong technical overlap with Webflow extension and backend API priorities satisfies Anchor 12. Anchor 15 withheld because standardized backend collaboration workflows are unverified.

5. **Business Size: 8.00 / 10**
   - **Source URL:** `https://clutch.co/profile/kvalifik` & `https://4dayweek.io/company/kvalifik`
   - **Source Type:** Directory profile & verified workplace profile
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Core team of approximately 12–18 digital professionals in Copenhagen across design, creative technology, engineering, and client growth (Clutch lists 10–49 range)."
   - **Evidence Classification & Confidence:** `verified_fact` | `medium`
   - **Anchor Justification:** Core team of 10–24 specialists with clear role differentiation satisfies Anchor 8 (mid-sized studio in core target sweet spot).

6. **Decision-Maker Accessibility: 8.00 / 10**
   - **Source URL:** `https://kvalifik.dk/en/about` & executive LinkedIn profiles
   - **Source Type:** First-party team page & professional network
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Key leadership identified with public profiles: Rebecca Busk (CEO & Partner), August Kristian Gjede (Co-Founder), and technical architecture leads."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Direct identifiable routes to executive leadership satisfy Anchor 8. Anchor 10 withheld because no warm intro or prior relationship exists.

7. **Commercial Fit: 5.00 / 10 (Re-evaluated & Lowered from 7.00)**
   - **Source URL:** `https://clutch.co/profile/kvalifik` (Observed 2026-11-08)
   - **Source Type:** Third-party directory profile
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Min. project size: $10,000+ (with service tiers up to $25,000+); Avg. hourly rate: $150 - $199 / hr. Verified corporate/B2B client base."
   - **Evidence Classification & Confidence:** `verified_fact` | `medium` (directory estimate)
   - **Anchor Justification & Re-Evaluation:** Previous calibration awarded 7.00 based on assumed external contractor collaboration. However, rigorous evidence inspection reveals that public sources do NOT demonstrate actual engagement of external software-development contractors or acceptance of a white-label wholesale model. Outsourcing posture remains an unverified information gap (`information_gap_tbd`). Verified premium rates ($150–$199/hr) satisfy Anchor 5 (premium market positioning); without explicit evidence of software development subcontracting, the score is strictly lowered to **5.00 / 10**.

#### Kvalifik Score Summary:
$$\text{Total Score} = 15.00 + 15.00 + 8.00 + 12.00 + 8.00 + 8.00 + 5.00 = \mathbf{71.00 / 100} \implies \text{'tier\_2\_prospective'}$$

- **Rubric Calibration Finding:** Lowering Commercial Fit from 7.00 to 5.00 adjusts Kvalifik from 73.00 to **71.00**. Kvalifik remains a strong Tier 2 candidate due to verified full-stack development demand and an established Copenhagen team, but cannot advance to Anchor 7 in Commercial Fit without empirical proof of external software engineering contracting.

---

### 9.4. Trueform (Framer Studio / Digital Design) — Provisional Score: 61.00 / 100 (`tier_2_prospective`)

- **Target Profile:** Trueform (`trueform.agency`) — Premier Swiss Framer studio (based in Wil & Zürich) specializing in high-performance marketing websites, brand systems, and large CMS-driven web platforms for SaaS and hardware scale-ups.
- **Primary Domain:** `trueform.agency`
- **Market Segment:** `framer_studio`
- **Recalculated Calibration Total:** **61.00 / 100** (Lowered from 66.00)
- **Assigned Qualification Tier:** **`tier_2_prospective`** (Tier 2 Prospective)

#### Granular 7-Dimension Traceable Evidence Records:

1. **Customer Flow: 12.00 / 20**
   - **Source URL:** `https://trueform.agency` & `https://framer.com/experts/trueform`
   - **Source Type:** First-party website & platform directory
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Published Framer marketing platforms for high-growth tech and B2B SaaS companies, including Miro (winner of Framer's Best Big Site Award), Flexion, Gather, and OhMD."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Multiple verified B2B tech client platforms published within the last 12 months satisfy Anchor 12. Anchor 15 withheld because volume is selective rather than a continuous high-volume agency flow.

2. **Development Demand: 12.00 / 20**
   - **Source URL:** `https://trueform.agency` (Observed 2026-11-08)
   - **Source Type:** First-party services and solutions page
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Publishes service tiers for 'Marketing Platforms (From $49K / CHF 40'000)' requiring 'complex localization, advanced CMS governance, dynamic layouts, and custom code integrations.'"
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Complex React component embeds, custom CMS architecture, and external API connectivity in Framer satisfy Anchor 12. Anchor 15/20 withheld because deep backend database architectures remain unverified.

3. **Repeatability of Orders: 6.00 / 15**
   - **Source URL:** `https://trueform.agency` (Observed 2026-11-08)
   - **Source Type:** First-party solutions and pricing page
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Explicitly offers published 'Post-Launch Support: From $7K / month retainer covering ongoing content operations, CMS updates, SEO upkeep, and performance scaling.'"
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Intermediate score of 6.00: verified offering of a $7k/month post-launch retainer satisfies more than basic warranty (Anchor 4), but because portfolio remains project-launch dominated and actual client retainer adoption proportion is unverified (`information_gap_tbd`), Anchor 8 is capped conservatively at 6.00.

4. **Technical Fit: 12.00 / 15**
   - **Source URL:** `https://trueform.agency` & `https://framer.com/experts/trueform`
   - **Source Type:** First-party agency website & platform showcase
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Elite Framer expert studio specializing in bespoke React components, modular design systems, and Framer code overrides for tech scaleups."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Direct match with our Framer Custom Development service priority (React components, API integrations) satisfies Anchor 12. Anchor 15 withheld because standardized backend collaboration workflows are unverified.

5. **Business Size: 6.00 / 10**
   - **Source URL:** `https://trueform.agency` (Observed 2026-11-08)
   - **Source Type:** First-party team page
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "'Trueform operates as a lean, senior-led studio where clients work directly with the core makers from kickoff to handoff: Cédric, Sam, Dominik.' Core team of 3–5 makers, supported by an extended network of digital specialists."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification:** Verified boutique studio of 4–9 people (core makers plus regular extended collaborators) with active commercial delivery satisfies Anchor 6 under the revised, non-overlapping Business Size scale.

6. **Decision-Maker Accessibility: 8.00 / 10 (Corrected from 10.00)**
   - **Source URL:** `https://trueform.agency` & public LinkedIn profiles
   - **Source Type:** First-party team page & professional network
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Co-founders Cédric and Sam, and Engineering Lead Dominik, are directly identified with active public visibility and direct inquiry routes."
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification & Re-Evaluation:** Direct route to key founders satisfies Anchor 8. Anchor 10 was previously assigned erroneously; under strict rubric rules, Anchor 10 requires a warm introduction, direct prior relationship, or confirmed prior interaction, none of which exist. Corrected to **8.00 / 10**.

7. **Commercial Fit: 5.00 / 10 (Re-evaluated & Lowered from 8.00)**
   - **Source URL:** `https://trueform.agency` (Observed 2026-11-08)
   - **Source Type:** First-party pricing and solutions page
   - **Observed Date:** 2026-11-08
   - **Relevant Excerpt:** "Published project pricing tiers: Landing Page Sprint ($15K / CHF 12'000), Marketing Website (From $25K / CHF 20'000), Marketing Platforms (From $49K / CHF 40'000), Post-Launch Support (From $7K / month). States studio is 'supported by a flexible extended network of specialized digital talents.'"
   - **Evidence Classification & Confidence:** `verified_fact` | `high`
   - **Anchor Justification & Re-Evaluation:** Verified premium pricing ($25k–$49k+ projects, $7k/mo retainers) satisfies Anchor 5. Anchors 7 and 8 are NOT supported: Trueform's "flexible extended network of specialized digital talents" describes a general creative/talent network, not verified use of external software development contractors. Furthermore, Dominik is their internal Engineering Lead, and willingness to accept behind-the-scenes white-label wholesale development is an unverified information gap (`information_gap_tbd`). Lowered to **5.00 / 10**.

#### Trueform Score Summary:
$$\text{Total Score} = 12.00 + 12.00 + 6.00 + 12.00 + 6.00 + 8.00 + 5.00 = \mathbf{61.00 / 100} \implies \text{'tier\_2\_prospective'}$$

- **Rubric Calibration Finding:** Correcting Accessibility from 10.00 to 8.00 and Commercial Fit from 8.00 to 5.00 adjusts Trueform from 66.00 to **61.00**. Trueform remains in Tier 2 (`tier_2_prospective`) with exceptional design craft and verified premium pricing tiers ($25k–$49k+), but strict evidence discipline prevents awarding outsourcing points based merely on a general specialist network.

---

### 9.5. Comparative Calibration Summary Matrix

| Evaluation Dimension | Weight | Refokus (Provisional) | Kvalifik (Provisional) | Trueform (Provisional) | Primary Rubric Calibration Insight |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Customer Flow** | 20 | 15.00 | 15.00 | 12.00 | High portfolio volume must be checked against live active pipeline. |
| **Development Demand** | 20 | 10.00 | 15.00 | 12.00 | Visual animation complexity $\ne$ backend engineering demand. |
| **Repeatability of Orders** | 15 | 8.00 | 8.00 | 6.00 | Retainers cannot be assumed without explicit contract proof; published retainer tiers require adoption verification. |
| **Technical Fit** | 15 | 12.00 | 12.00 | 12.00 | All three match our core stack; standards determine higher tiers. |
| **Business Size** | 10 | 8.00 | 8.00 | 6.00 | Non-overlapping ranges (2–4, 5–9, 10–24, 25–50) provide clear evaluation without ambiguity. |
| **Decision-Maker Accessibility** | 10 | 8.00 | 8.00 | 8.00 | Direct routes without warm introductions cap at 8.00; 10.00 strictly reserved for confirmed warm paths. |
| **Commercial Fit** | 10 | 5.00 | 5.00 | 5.00 | Premium rates confirm Anchor 5, but do NOT prove white-label acceptance; general specialist networks do not qualify for Anchor 7 without explicit development contracting evidence. |
| **Overall Score** | **100** | **66.00** | **71.00** | **61.00** | **Strict evidence enforcement eliminates score inflation.** |
| **Assigned Tier** | — | **Tier 2** | **Tier 2** | **Tier 2** | All three fall into Tier 2 (`tier_2_prospective`); none achieve Tier 1 without verified retainers & outsourcing data. |

---

## 10. Repository Consistency & Schema Integrity

### 10.1. Alignment with Database Schema (`crm/schema.sql`)
This specification is designed to maintain 100% relational, constraint, and trigger integrity with `crm/schema.sql`:

1. **Table Names:** Directly maps to `lead_qualifications`, `companies`, `research_reports`, `research_findings`, and `tasks`.
2. **Column Names & Math:** All seven dimension columns (`customer_flow_score`, `development_demand_score`, `repeatability_orders_score`, `technical_fit_score`, `business_size_score`, `decision_maker_accessibility_score`, `commercial_fit_score`) match the database schema. Constraint `chk_lead_qual_score_sum` mathematically enforces:
   $$\text{overall\_score} = \sum_{i=1}^7 \text{dimension\_score}_i$$
3. **Database Enums:**
   - `qualification_tier_type`: `'tier_1_priority'`, `'tier_2_prospective'`, `'tier_3_deferred'`, `'disqualified'`.
   - `qualification_review_status_type`: `'draft'`, `'pending_human_review'`, `'approved'`, `'rejected'`.
   - `research_finding_type`: `'verified_fact'`, `'hypothesis'`, `'information_gap_tbd'`.
   - `confidence_level_type`: `'high'`, `'medium'`, `'low'`, `'unverified'`.
   - `company_lifecycle_stage_type`: `'identified'`, `'researching'`, `'qualified'`, `'disqualified'`, `'outreach_queued'`, `'in_outreach'`, `'active_conversation'`, etc.
4. **Triggers Enforced:**
   - `fn_enforce_lead_qualification_rules`: Requires supporting evidence for all positive scores, validates tier thresholds, restricts approvals to authenticated human operators, and resets approvals on material edits.
   - `fn_sync_company_qualification`: Synchronizes approved qualification tiers and updates company lifecycle stage to `'qualified'` or `'disqualified'`.

### 10.2. Strict Human-in-the-Loop Governance Gate
- **Zero Autonomous Approval:** Neither Antigravity, Hermes Agent, nor any specialized subagent can approve a qualification scorecard.
- **Engine-Level Block:** Row Level Security (RLS) and database trigger `fn_enforce_lead_qualification_rules` strictly reject any update setting `review_status = 'approved'` unless `is_human_operator() = TRUE` and `reviewed_by_human_id = auth.uid()`.
- **Review Queue Routing:** All agent outputs are submitted as `review_status = 'draft'` for explicit human operator review, modification, or endorsement.

### 10.3. Pre-Operational Reality Safeguards
- **No Delivery Commitments:** This rubric is an analytical evaluation tool. It does not initiate client communications, quote pricing, promise developer sprint availability, or imply that our technical team is currently operational.
- **Commercial Boundary:** Standard cold outreach is restricted to **Project-Based Wholesale Development**. Strategic Revenue Sharing is strictly prohibited during initial prospecting.
