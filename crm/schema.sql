-- =============================================================================
-- Operational CRM & Intelligence Platform — MVP Core Schema Migration
-- Business: Managed Development Partner
-- Phase: 2.1 — Final Security & Data Integrity Hardening (v3.0.0)
-- Scope: MVP Core (Discovery, Research, Qualification, Outreach, Conversations, Activities, Audit)
-- Deferred: Delivery & Finance tables separated to crm/phase2_delivery_expansion.sql
-- Status: DESIGN ONLY (DO NOT EXECUTE AGAINST REMOTE DATABASE)
-- Engine: PostgreSQL 15+ (Supabase Native)
-- =============================================================================

-- =============================================================================
-- 1. EXTENSIONS & BASE CONFIGURATION
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Function to automatically maintain updated_at timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- Function: add_business_days
-- Calculates deadline by adding standard working days (Mon-Fri), explicitly
-- skipping Saturday (DOW 6) and Sunday (DOW 0) in the target timezone.
-- Volatility: STABLE (Correct for timezone-dependent calculations).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION add_business_days(
    start_ts TIMESTAMPTZ, 
    num_days INT, 
    tz TEXT DEFAULT 'UTC'
)
RETURNS TIMESTAMPTZ AS $$
DECLARE
    curr_ts TIMESTAMPTZ := start_ts;
    added INT := 0;
    dow INT;
BEGIN
    IF num_days <= 0 THEN
        RETURN start_ts;
    END IF;

    WHILE added < num_days LOOP
        curr_ts := curr_ts + INTERVAL '1 day';
        -- Evaluate day of week in the specified timezone
        dow := EXTRACT(DOW FROM curr_ts AT TIME ZONE tz);
        IF dow NOT IN (0, 6) THEN
            added := added + 1;
        END IF;
    END LOOP;
    RETURN curr_ts;
END;
$$ LANGUAGE plpgsql STABLE;

-- -----------------------------------------------------------------------------
-- Security Helper Functions for Supabase JWT Role Identification
-- Enforces strict least privilege: unknown roles receive ZERO operational access.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION is_human_operator()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'user_role') IN ('operator', 'admin', 'founder'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION is_admin_operator()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'user_role') IN ('admin', 'founder'),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION is_agent()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'user_role') = 'agent',
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION is_agent_type(p_type TEXT)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'user_role') = 'agent' AND
        (auth.jwt() -> 'app_metadata' ->> 'subagent_type') = p_type,
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION current_agent_identity()
RETURNS TEXT AS $$
BEGIN
    RETURN COALESCE(
        auth.jwt() -> 'app_metadata' ->> 'agent_id',
        auth.jwt() ->> 'sub'
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION current_actor_role()
RETURNS TEXT AS $$
BEGIN
    RETURN COALESCE(
        auth.jwt() -> 'app_metadata' ->> 'user_role',
        auth.jwt() ->> 'role',
        'anon'
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- =============================================================================
-- 2. CUSTOM ENUM TYPES (MVP CORE)
-- =============================================================================

CREATE TYPE market_segment_type AS ENUM (
    'digital_agency',           -- Creative, branding, and full-service marketing agencies
    'webflow_studio',          -- Specialized Webflow design & visual development studios
    'framer_studio',           -- Modern Framer visual design and product studios
    'ecommerce_agency',        -- (Future expansion) Shopify, WooCommerce, headless commerce
    'independent_consultant',   -- (Future expansion) Product & strategy consultants
    'small_saas',              -- (Future expansion) Early-stage software product teams
    'other_recurring'          -- Other entities with predictable, recurring software demand
);

CREATE TYPE company_research_status_type AS ENUM (
    'not_started',
    'in_progress',
    'completed',
    'refresh_needed'
);

CREATE TYPE qualification_tier_type AS ENUM (
    'tier_1_priority',         -- High recurring client demand, strong scoping & technical literacy (80-100)
    'tier_2_prospective',      -- Moderate demand, good potential wholesale fit, needs review (60-79)
    'tier_3_deferred',         -- Niche or future expansion segment (40-59)
    'disqualified'             -- Violates IPP criteria or scored < 40
);

CREATE TYPE company_lifecycle_stage_type AS ENUM (
    'identified',              -- Discovered via directory/portfolio/referral, unverified
    'researching',             -- Deep research underway by Hermes agent or analyst
    'qualified',               -- Verified fit meeting Ideal Partner Profile
    'disqualified',            -- Evaluated and rejected based on hard disqualifiers
    'outreach_queued',         -- Personalized draft prepared, awaiting human approval
    'in_outreach',             -- Human-approved outreach sequence actively dispatching
    'active_conversation',     -- Direct response received, dialogue in progress
    'negotiating',             -- Commercial terms and initial scope being defined
    'partner_active',          -- Agreement executed; actively collaborating
    'partner_dormant',         -- Established partner with no current active project
    'churned'                  -- Relationship formally ended
);

CREATE TYPE contact_role_type AS ENUM (
    'founder_executive',       -- Founder, CEO, Managing Director, Agency Partner
    'technical_lead',          -- CTO, Head of Engineering, Technical Director
    'operations_lead',         -- COO, Operations Director, Delivery Head
    'account_manager',         -- Client Services Director, Account Executive
    'designer_developer',      -- Senior Designer, Visual Developer, Webflow/Framer Specialist
    'other'
);

CREATE TYPE contact_verification_status_type AS ENUM (
    'unverified',              -- Scraped or found, identity/inbox unconfirmed
    'verified',                -- Manually confirmed or verified via MX/deliverability check
    'bounced',                 -- Email bounced or channel unreachable
    'invalid'                  -- Role mismatch or departed organization
);

CREATE TYPE research_finding_type AS ENUM (
    'verified_fact',           -- Backed by direct public evidence (URL, portfolio item, client quote)
    'hypothesis',              -- Inference requiring further qualification or conversational discovery
    'information_gap_tbd'      -- Unknown parameter explicitly flagged for discovery (never assumed)
);

CREATE TYPE finding_category_type AS ENUM (
    'business_model',          -- How the partner monetizes (retainers, fixed projects, subscriptions)
    'client_roster',           -- Verifiable brands and ongoing client engagements
    'technology_stack',        -- Confirmed CMS platforms, frontend frameworks, backend tools
    'scoping_maturity',        -- Evident design-to-dev workflow, Figma usage, structured handoffs
    'growth_signals',          -- Hiring activity, client case study frequency, market expansion
    'pain_points',             -- Indicators of technical bottlenecks, delayed client deliverables
    'pricing_rates',           -- Publicly observed client rate ranges or project minimums
    'team_structure',          -- In-house design vs engineering headcount distribution
    'other'
);

CREATE TYPE confidence_level_type AS ENUM (
    'high',                    -- Direct first-party statement (agency website, founder post, case study)
    'medium',                  -- Third-party directory profile (Clutch, Dribbble, LinkedIn listing)
    'low',                     -- Inferred or circumstantial indicator
    'unverified'               -- Unsubstantiated hypothesis or question
);

CREATE TYPE qualification_review_status_type AS ENUM (
    'draft',                   -- Agent-generated score awaiting human review
    'pending_human_review',    -- Submitted to human operator queue
    'approved',                -- Validated and endorsed by human operator
    'rejected'                 -- Overruled or adjusted by human operator
);

CREATE TYPE commercial_model_type AS ENUM (
    'project_based_wholesale',  -- Entry Model: Fixed-scope or milestone engagements to prove execution
    'monthly_reserved_capacity',-- Strategic Recurring Model: Dedicated capacity (deferred execution)
    'strategic_revenue_sharing' -- Selective Model: Vetted partners only; strictly prohibited in cold outreach
);

CREATE TYPE outreach_channel_type AS ENUM (
    'email',                   -- Direct personalized professional email
    'linkedin_inmail',         -- Direct professional social outreach
    'contact_form',            -- Agency official inquiry form (used conservatively)
    'other'
);

CREATE TYPE outreach_approval_status_type AS ENUM (
    'draft',                   -- Initial draft created by agent or operator
    'pending_human_approval',   -- Placed in mandatory human review queue
    'approved',                -- Explicitly authorized by human operator for dispatch
    'rejected',                -- Rejected by human operator (will not be sent)
    'revision_requested'       -- Returned to drafting agent for adjustments
);

CREATE TYPE outreach_sending_status_type AS ENUM (
    'draft',
    'queued',                  -- Scheduled for transmission after human sign-off
    'sending',
    'sent',                    -- Successfully transmitted
    'delivered',
    'opened',
    'replied',
    'bounced',
    'failed',
    'cancelled'
);

CREATE TYPE conversation_channel_type AS ENUM (
    'email',
    'linkedin',
    'video_call',
    'phone',
    'slack',
    'in_person',
    'other'
);

CREATE TYPE conversation_status_type AS ENUM (
    'active',
    'waiting_on_partner',
    'waiting_on_us',
    'paused',
    'closed_lost',
    'closed_won'
);

CREATE TYPE activity_type_type AS ENUM (
    'call',
    'meeting',
    'note',
    'email_sent',
    'email_received',
    'qualification_completed',
    'proposal_presented',
    'status_change'
);

CREATE TYPE task_priority_type AS ENUM (
    'low',
    'medium',
    'high',
    'urgent'
);

CREATE TYPE task_status_type AS ENUM (
    'pending',
    'in_progress',
    'completed',
    'cancelled'
);

CREATE TYPE actor_type AS ENUM (
    'human',                   -- Human founder, operator, or engineer
    'agent',                   -- Hermes orchestration agent or specialized subagent
    'system'                   -- Automated cron, trigger, or webhook event
);

CREATE TYPE suppression_target_type AS ENUM (
    'email',
    'domain',
    'linkedin',
    'phone',
    'other'
);

-- =============================================================================
-- 3. CORE SCHEMA TABLES (14 MVP TABLES)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 3.1. Suppression Registry (Independent Permanent Suppression Ledger)
-- Survives any ordinary deletion or cleanup of companies or contacts.
-- Records cannot be deleted through standard operations.
-- -----------------------------------------------------------------------------
CREATE TABLE suppression_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type suppression_target_type NOT NULL,
    identifier TEXT NOT NULL UNIQUE, -- Normalized lowercased email, domain, or handle
    reason TEXT NOT NULL,
    source TEXT NOT NULL, -- 'opt_out_request', 'gdpr_removal', 'bounced_permanent', 'manual_blacklist'
    created_by_actor_type actor_type NOT NULL,
    created_by_actor_identity TEXT NOT NULL,
    suppressed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT chk_suppression_identifier_clean CHECK (identifier = LOWER(TRIM(identifier)))
);

COMMENT ON TABLE suppression_registry IS 'Permanent, immutable suppression ledger. Survives company/contact deletions.';

-- -----------------------------------------------------------------------------
-- 3.2. Companies
-- Central entity representing prospective and existing wholesale partner agencies/studios.
-- -----------------------------------------------------------------------------
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    primary_domain TEXT NOT NULL UNIQUE,
    website_url TEXT NOT NULL,
    primary_segment market_segment_type NOT NULL,
    secondary_segments market_segment_type[] DEFAULT '{}',
    country TEXT,
    city TEXT,
    timezone TEXT DEFAULT 'UTC',
    headcount_min INT,
    headcount_max INT,
    headcount_verifiable BOOLEAN NOT NULL DEFAULT FALSE,
    services_offered TEXT[] NOT NULL DEFAULT '{}',
    tech_stack TEXT[] NOT NULL DEFAULT '{}',
    tech_stack_verifiable BOOLEAN NOT NULL DEFAULT FALSE,
    discovery_source TEXT NOT NULL,
    discovery_details JSONB NOT NULL DEFAULT '{}',
    research_status company_research_status_type NOT NULL DEFAULT 'not_started',
    qualification_tier qualification_tier_type,
    lifecycle_stage company_lifecycle_stage_type NOT NULL DEFAULT 'identified',
    is_suppressed BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_reason TEXT,
    suppression_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT chk_companies_domain_format CHECK (primary_domain ~* '^[a-z0-9.-]+\.[a-z]{2,}$'),
    CONSTRAINT chk_companies_headcount_bounds CHECK (
        (headcount_min IS NULL AND headcount_max IS NULL) OR
        (headcount_min >= 0 AND (headcount_max IS NULL OR headcount_max >= headcount_min))
    ),
    CONSTRAINT chk_companies_suppression_logic CHECK (
        (is_suppressed = FALSE AND suppression_reason IS NULL AND suppression_date IS NULL) OR
        (is_suppressed = TRUE AND suppression_reason IS NOT NULL AND suppression_date IS NOT NULL)
    ),
    CONSTRAINT chk_companies_disqualified_stage CHECK (
        qualification_tier IS DISTINCT FROM 'disqualified' OR 
        lifecycle_stage IN ('identified', 'researching', 'disqualified', 'churned')
    )
);

COMMENT ON TABLE companies IS 'Central registry of prospective and active wholesale partner organizations.';

CREATE TRIGGER trg_companies_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.3. Company Domains
-- Multi-domain mapping to prevent duplicate prospect records when agencies rebrand or use multiple domains.
-- -----------------------------------------------------------------------------
CREATE TABLE company_domains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    domain TEXT NOT NULL UNIQUE,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT chk_company_domains_format CHECK (domain ~* '^[a-z0-9.-]+\.[a-z]{2,}$')
);

COMMENT ON TABLE company_domains IS 'Alternate and historical domain names for duplicate detection.';

-- -----------------------------------------------------------------------------
-- 3.4. Contacts
-- Individual professional contacts associated with a company.
-- Composite UNIQUE (id, company_id) enforces cross-entity referential integrity.
-- -----------------------------------------------------------------------------
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    first_name TEXT NOT NULL,
    last_name TEXT,
    full_name TEXT GENERATED ALWAYS AS (TRIM(first_name || ' ' || COALESCE(last_name, ''))) STORED,
    job_title TEXT,
    role_type contact_role_type NOT NULL DEFAULT 'other',
    is_decision_maker BOOLEAN NOT NULL DEFAULT FALSE,
    email TEXT,
    linkedin_url TEXT,
    twitter_url TEXT,
    phone TEXT,
    verification_status contact_verification_status_type NOT NULL DEFAULT 'unverified',
    verification_source_url TEXT,
    verification_notes TEXT,
    is_opted_out BOOLEAN NOT NULL DEFAULT FALSE,
    opt_out_at TIMESTAMPTZ,
    opt_out_reason TEXT,
    last_contacted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_contacts_id_company UNIQUE (id, company_id),
    CONSTRAINT chk_contacts_email_format CHECK (email IS NULL OR email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'),
    CONSTRAINT chk_contacts_opt_out_logic CHECK (
        (is_opted_out = FALSE AND opt_out_at IS NULL) OR
        (is_opted_out = TRUE AND opt_out_at IS NOT NULL)
    )
);

COMMENT ON TABLE contacts IS 'Professional decision-makers and key personnel within partner organizations.';

CREATE UNIQUE INDEX idx_contacts_unique_company_email 
    ON contacts(company_id, LOWER(email)) 
    WHERE email IS NOT NULL;

CREATE TRIGGER trg_contacts_updated_at
    BEFORE UPDATE ON contacts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.5. Research Reports
-- Container for deep-dive partner research conducted by Hermes research agents or human analysts.
-- Composite UNIQUE (id, company_id) enforces cross-entity referential integrity.
-- -----------------------------------------------------------------------------
CREATE TABLE research_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    research_agent_identity TEXT NOT NULL,
    report_version INT NOT NULL DEFAULT 1,
    summary TEXT NOT NULL,
    recommended_angle TEXT,
    overall_confidence confidence_level_type NOT NULL DEFAULT 'medium',
    gaps_and_unknowns TEXT, -- Explicitly cataloged TBD parameters
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_research_reports_id_company UNIQUE (id, company_id)
);

COMMENT ON TABLE research_reports IS 'Audit trail of comprehensive research sessions performed on partner prospects.';

CREATE TRIGGER trg_research_reports_updated_at
    BEFORE UPDATE ON research_reports
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.6. Research Findings
-- Atomic evidence statements backing research reports.
-- Composite FK guarantees report_id and company_id belong to the same parent entity.
-- -----------------------------------------------------------------------------
CREATE TABLE research_findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL,
    company_id UUID NOT NULL,
    finding_type research_finding_type NOT NULL,
    category finding_category_type NOT NULL,
    statement TEXT NOT NULL,
    evidence_url TEXT,
    evidence_excerpt TEXT,
    confidence confidence_level_type NOT NULL DEFAULT 'unverified',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_findings_report_company FOREIGN KEY (report_id, company_id) 
        REFERENCES research_reports(id, company_id) ON DELETE CASCADE,
    CONSTRAINT chk_verified_fact_requires_evidence CHECK (
        finding_type != 'verified_fact' OR 
        (evidence_url IS NOT NULL AND confidence IN ('high', 'medium'))
    )
);

COMMENT ON TABLE research_findings IS 'Granular evidence statements backing research. Distinguishes facts from hypotheses.';

-- -----------------------------------------------------------------------------
-- 3.7. Lead Qualifications (Approved 7-Dimension Model with Evidence Validation)
-- Evaluates prospects against the 7 IPP criteria (100 pts max):
-- 1. Customer Flow (20)
-- 2. Development Demand (20)
-- 3. Repeatability of Orders (15)
-- 4. Technical Fit (15)
-- 5. Business Size (10)
-- 6. Decision-Maker Accessibility (10)
-- 7. Commercial Fit (10)
-- -----------------------------------------------------------------------------
CREATE TABLE lead_qualifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    evaluator_type actor_type NOT NULL,
    evaluator_identity TEXT NOT NULL,
    overall_score NUMERIC(5,2) NOT NULL,
    customer_flow_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    development_demand_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    repeatability_orders_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    technical_fit_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    business_size_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    decision_maker_accessibility_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    commercial_fit_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    dimension_evidence JSONB NOT NULL DEFAULT '{}',
    qualification_tier qualification_tier_type NOT NULL,
    disqualification_reasons TEXT[] NOT NULL DEFAULT '{}',
    disqualification_notes TEXT,
    recommended_next_action TEXT NOT NULL,
    review_status qualification_review_status_type NOT NULL DEFAULT 'draft',
    reviewed_by_human_id UUID,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT chk_lead_qual_customer_flow CHECK (customer_flow_score >= 0 AND customer_flow_score <= 20),
    CONSTRAINT chk_lead_qual_development_demand CHECK (development_demand_score >= 0 AND development_demand_score <= 20),
    CONSTRAINT chk_lead_qual_repeatability_orders CHECK (repeatability_orders_score >= 0 AND repeatability_orders_score <= 15),
    CONSTRAINT chk_lead_qual_technical_fit CHECK (technical_fit_score >= 0 AND technical_fit_score <= 15),
    CONSTRAINT chk_lead_qual_business_size CHECK (business_size_score >= 0 AND business_size_score <= 10),
    CONSTRAINT chk_lead_qual_decision_maker_accessibility CHECK (decision_maker_accessibility_score >= 0 AND decision_maker_accessibility_score <= 10),
    CONSTRAINT chk_lead_qual_commercial_fit CHECK (commercial_fit_score >= 0 AND commercial_fit_score <= 10),
    CONSTRAINT chk_lead_qual_score_sum CHECK (
        overall_score = (
            customer_flow_score + 
            development_demand_score + 
            repeatability_orders_score + 
            technical_fit_score + 
            business_size_score + 
            decision_maker_accessibility_score + 
            commercial_fit_score
        )
    ),
    CONSTRAINT chk_lead_qual_score_range CHECK (overall_score >= 0 AND overall_score <= 100),
    CONSTRAINT chk_lead_qual_review_human_gate CHECK (
        (review_status != 'approved' AND reviewed_by_human_id IS NULL AND reviewed_at IS NULL) OR 
        (review_status = 'approved' AND reviewed_by_human_id IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

COMMENT ON TABLE lead_qualifications IS 'Objective qualification scorecards based on 7 approved dimensions. Unknowns score 0.';

CREATE TRIGGER trg_lead_qualifications_updated_at
    BEFORE UPDATE ON lead_qualifications
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.8. Outreach Campaigns
-- Logical groupings for segmented partnership outreach initiatives.
-- -----------------------------------------------------------------------------
CREATE TABLE outreach_campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    target_segment market_segment_type NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

COMMENT ON TABLE outreach_campaigns IS 'Campaign structures for targeted wholesale partner prospecting.';

CREATE TRIGGER trg_outreach_campaigns_updated_at
    BEFORE UPDATE ON outreach_campaigns
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.9. Outreach Messages
-- Personalized draft correspondence with mandatory human approval gates before dispatch.
-- Composite FK guarantees contact_id belongs to the designated company_id.
-- -----------------------------------------------------------------------------
CREATE TABLE outreach_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID REFERENCES outreach_campaigns(id) ON DELETE SET NULL,
    company_id UUID NOT NULL,
    contact_id UUID NOT NULL,
    drafted_by_agent_identity TEXT NOT NULL,
    channel outreach_channel_type NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    personalization_evidence_ids UUID[] NOT NULL DEFAULT '{}',
    personalization_rationale TEXT NOT NULL,
    proposed_commercial_model commercial_model_type NOT NULL DEFAULT 'project_based_wholesale',
    approval_status outreach_approval_status_type NOT NULL DEFAULT 'draft',
    approved_by_human_id UUID,
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,
    is_admin_override BOOLEAN NOT NULL DEFAULT FALSE,
    override_reason TEXT,
    override_by_human_id UUID,
    sending_status outreach_sending_status_type NOT NULL DEFAULT 'draft',
    scheduled_for TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    external_message_id TEXT,
    error_message TEXT,
    sequence_step INT NOT NULL DEFAULT 1,
    parent_message_id UUID REFERENCES outreach_messages(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    -- Composite FK: Guarantees recipient contact belongs to the target company!
    CONSTRAINT fk_outreach_contact_company FOREIGN KEY (contact_id, company_id) 
        REFERENCES contacts(id, company_id) ON DELETE RESTRICT,

    -- Business Rule: Revenue sharing is strictly prohibited during cold outreach!
    CONSTRAINT chk_outreach_no_revenue_sharing CHECK (
        proposed_commercial_model != 'strategic_revenue_sharing'
    ),

    -- Strict Human Gate: Messages can NEVER enter dispatch or downstream delivery status without human sign-off!
    CONSTRAINT chk_outreach_mandatory_human_gate CHECK (
        sending_status NOT IN ('queued', 'sending', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed') OR 
        (approval_status = 'approved' AND approved_by_human_id IS NOT NULL AND approved_at IS NOT NULL)
    ),

    CONSTRAINT chk_outreach_override_audit CHECK (
        (is_admin_override = FALSE AND override_reason IS NULL AND override_by_human_id IS NULL) OR 
        (is_admin_override = TRUE AND override_reason IS NOT NULL AND TRIM(override_reason) != '' AND override_by_human_id IS NOT NULL)
    ),

    CONSTRAINT chk_outreach_sequence_step CHECK (sequence_step >= 1)
);

COMMENT ON TABLE outreach_messages IS 'Draft messages with composite FK integrity, human approval gates, and rev-share prohibition.';

CREATE TRIGGER trg_outreach_messages_updated_at
    BEFORE UPDATE ON outreach_messages
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.10. Conversations
-- Active partner communication threads, negotiation notes, and requirement captures.
-- Composite UNIQUE (id, company_id) enforces cross-entity referential integrity.
-- -----------------------------------------------------------------------------
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    primary_contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    status conversation_status_type NOT NULL DEFAULT 'active',
    current_commercial_model_discussed commercial_model_type NOT NULL DEFAULT 'project_based_wholesale',
    partner_requirements TEXT,
    partner_objections TEXT[] NOT NULL DEFAULT '{}',
    objection_handling_notes TEXT,
    next_action TEXT,
    next_action_due_date DATE,
    owner_human_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_conversations_id_company UNIQUE (id, company_id)
);

COMMENT ON TABLE conversations IS 'Tracks high-level dialogues, objections, and requirement alignment during deal shaping.';

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.11. Conversation Messages
-- Inbound and outbound message log across communication channels.
-- -----------------------------------------------------------------------------
CREATE TABLE conversation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    channel conversation_channel_type NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    author_type actor_type NOT NULL,
    author_identity TEXT NOT NULL,
    message_timestamp TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

COMMENT ON TABLE conversation_messages IS 'Detailed chronological message history for active partner conversations.';

-- -----------------------------------------------------------------------------
-- 3.12. Activities (Unified Partner Relationship Timeline)
-- Composite FKs guarantee contact_id and conversation_id belong to the same company_id.
-- -----------------------------------------------------------------------------
CREATE TABLE activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    contact_id UUID,
    conversation_id UUID,
    activity_type activity_type_type NOT NULL,
    subject TEXT NOT NULL,
    description TEXT,
    performed_by_actor_type actor_type NOT NULL,
    performed_by_actor_identity TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    -- Composite FK: If contact_id is specified, must belong to company_id
    CONSTRAINT fk_activities_contact_company FOREIGN KEY (contact_id, company_id) 
        REFERENCES contacts(id, company_id) ON DELETE SET NULL,

    -- Composite FK: If conversation_id is specified, must belong to company_id
    CONSTRAINT fk_activities_conversation_company FOREIGN KEY (conversation_id, company_id) 
        REFERENCES conversations(id, company_id) ON DELETE SET NULL
);

COMMENT ON TABLE activities IS 'Unified commercial engagement timeline (calls, meetings, notes, milestones).';

-- -----------------------------------------------------------------------------
-- 3.13. Tasks
-- Operational tasks assigned to human operators, Hermes orchestrator, or specialized subagents.
-- -----------------------------------------------------------------------------
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL,
    priority task_priority_type NOT NULL DEFAULT 'medium',
    status task_status_type NOT NULL DEFAULT 'pending',
    assigned_actor_type actor_type NOT NULL,
    assigned_actor_identity TEXT NOT NULL,
    campaign_id UUID REFERENCES outreach_campaigns(id) ON DELETE SET NULL,
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    contact_id UUID,
    due_date TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    -- Composite FK: If contact_id is specified, must belong to designated company_id!
    CONSTRAINT fk_tasks_contact_company FOREIGN KEY (contact_id, company_id) 
        REFERENCES contacts(id, company_id) ON DELETE CASCADE,

    -- Integrity Rule: A task cannot reference a contact without referencing its company
    CONSTRAINT chk_tasks_contact_requires_company CHECK (
        contact_id IS NULL OR company_id IS NOT NULL
    ),

    -- State Machine Rule: completed_at must be populated if and only if status is completed
    CONSTRAINT chk_task_completed_at_sync CHECK (
        (status = 'completed' AND completed_at IS NOT NULL) OR 
        (status != 'completed' AND completed_at IS NULL)
    )
);

COMMENT ON TABLE tasks IS 'Actionable tasks coordinating human operators and Hermes subagents.';

CREATE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.14. Audit Logs (Immutable Technical Security Audit Ledger)
-- Append-only system-wide audit trail recording critical business and security events.
-- Direct UPDATE and DELETE are prohibited by database triggers.
-- -----------------------------------------------------------------------------
CREATE TABLE audit_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name TEXT NOT NULL,
    record_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE', 'APPROVAL_GRANTED', 'APPROVAL_REVOKED', 'OVERRIDE_EXECUTED')),
    actor_type actor_type NOT NULL,
    actor_identity TEXT NOT NULL,
    old_values JSONB,
    new_values JSONB,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

COMMENT ON TABLE audit_logs IS 'Append-only audit ledger recording sensitive state transitions. Immutable.';

-- =============================================================================
-- 4. BUSINESS LOGIC TRIGGERS & AUTOMATION FUNCTIONS
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Audit Log Immutability
-- Blocks any manual UPDATE or DELETE on the audit_logs table.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_audit_log_immutability()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit Integrity Violation: audit_logs records are permanent and immutable. UPDATE and DELETE are strictly prohibited.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_audit_log_immutability
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_audit_log_immutability();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Suppression Registry Immutability
-- Prevents ordinary deletion of historical suppression records.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_suppression_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Compliance Violation: suppression_registry records cannot be deleted. Suppression is permanent.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_suppression_immutability
    BEFORE DELETE ON suppression_registry
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_suppression_immutability();

-- -----------------------------------------------------------------------------
-- Trigger Function: Auto-Sync Company Primary Domain to company_domains
-- Guarantees primary domain is always recorded in company_domains for seamless deduplication.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sync_company_primary_domain()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO company_domains (company_id, domain, is_primary)
    VALUES (NEW.id, LOWER(TRIM(NEW.primary_domain)), TRUE)
    ON CONFLICT (domain) DO UPDATE 
        SET company_id = EXCLUDED.company_id, is_primary = TRUE;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_company_primary_domain
    AFTER INSERT OR UPDATE OF primary_domain ON companies
    FOR EACH ROW
    EXECUTE FUNCTION fn_sync_company_primary_domain();

-- -----------------------------------------------------------------------------
-- Trigger Function: Robust Multi-Channel Suppression Synchronization
-- Syncs email, LinkedIn, and primary domain on opt-out or changes.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sync_contact_suppression()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_opted_out = TRUE THEN
        -- Sync Email if changed or newly opted out
        IF (OLD IS NULL OR OLD.is_opted_out = FALSE OR NEW.email IS DISTINCT FROM OLD.email) AND NEW.email IS NOT NULL THEN
            INSERT INTO suppression_registry (
                target_type, identifier, reason, source, created_by_actor_type, created_by_actor_identity
            ) VALUES (
                'email', LOWER(TRIM(NEW.email)), COALESCE(NEW.opt_out_reason, 'Contact opt-out request'),
                'opt_out_request', 'system', 'trg_contacts_suppression_sync'
            ) ON CONFLICT (identifier) DO NOTHING;
        END IF;

        -- Sync LinkedIn profile handle/URL if present
        IF (OLD IS NULL OR OLD.is_opted_out = FALSE OR NEW.linkedin_url IS DISTINCT FROM OLD.linkedin_url) AND NEW.linkedin_url IS NOT NULL THEN
            INSERT INTO suppression_registry (
                target_type, identifier, reason, source, created_by_actor_type, created_by_actor_identity
            ) VALUES (
                'linkedin', LOWER(TRIM(NEW.linkedin_url)), COALESCE(NEW.opt_out_reason, 'Contact opt-out request'),
                'opt_out_request', 'system', 'trg_contacts_suppression_sync'
            ) ON CONFLICT (identifier) DO NOTHING;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_contact_suppression
    AFTER INSERT OR UPDATE OF is_opted_out, email, linkedin_url ON contacts
    FOR EACH ROW
    EXECUTE FUNCTION fn_sync_contact_suppression();

CREATE OR REPLACE FUNCTION fn_sync_company_suppression()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_suppressed = TRUE THEN
        IF (OLD IS NULL OR OLD.is_suppressed = FALSE OR NEW.primary_domain IS DISTINCT FROM OLD.primary_domain) AND NEW.primary_domain IS NOT NULL THEN
            INSERT INTO suppression_registry (
                target_type, identifier, reason, source, created_by_actor_type, created_by_actor_identity
            ) VALUES (
                'domain', LOWER(TRIM(NEW.primary_domain)), COALESCE(NEW.suppression_reason, 'Company suppression/blacklist'),
                'manual_blacklist', 'system', 'trg_companies_suppression_sync'
            ) ON CONFLICT (identifier) DO NOTHING;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_company_suppression
    AFTER INSERT OR UPDATE OF is_suppressed, primary_domain ON companies
    FOR EACH ROW
    EXECUTE FUNCTION fn_sync_company_suppression();

-- -----------------------------------------------------------------------------
-- Function: is_identifier_suppressed
-- Hardened boolean lookup verifying if a specific identifier is suppressed.
-- Does NOT expose underlying suppression records, reasons, or sources to callers.
-- Returns TRUE if suppressed, FALSE otherwise.
-- Security Guarantees:
-- 1. Authentication required (unauthenticated calls raise exception).
-- 2. Human operators have operational access for legitimate verification.
-- 3. Agents must have an active task and queried identifier must be demonstrably
--    within that task's authorized company/contact scope; out-of-scope queries
--    are rejected with exceptions to prevent identifier enumeration.
-- 4. Identifier normalization matches suppression_registry (lower/trim).
-- 5. Safe search_path (pg_catalog, public) and schema-qualified relations.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION is_identifier_suppressed(
    p_target_type suppression_target_type,
    p_identifier TEXT
)
RETURNS BOOLEAN
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_clean_id TEXT;
    v_agent_id TEXT;
    v_in_scope BOOLEAN := FALSE;
BEGIN
    -- 1. Unauthenticated or anonymous callers are strictly blocked
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'Security Policy Violation: Authentication required for suppression lookup.';
    END IF;

    -- Normalize identifier consistently with suppression registry
    v_clean_id := LOWER(TRIM(COALESCE(p_identifier, '')));

    -- 2. Human operators: preserve legitimate operational verification workflow
    IF is_human_operator() THEN
        IF v_clean_id = '' THEN
            RETURN FALSE;
        END IF;

        RETURN EXISTS (
            SELECT 1 FROM public.suppression_registry
            WHERE target_type = p_target_type
              AND identifier = v_clean_id
        );
    END IF;

    -- 3. Agent callers: strict task-scoped authorization to prevent registry enumeration
    IF is_agent() THEN
        v_agent_id := current_agent_identity();
        IF v_agent_id IS NULL OR TRIM(v_agent_id) = '' THEN
            RAISE EXCEPTION 'Security Policy Violation: Valid authenticated agent identity required for suppression lookup.';
        END IF;

        IF v_clean_id = '' THEN
            RETURN FALSE;
        END IF;

        -- Verify that the queried identifier is demonstrably within an active task assigned to this agent
        IF p_target_type = 'email' THEN
            SELECT EXISTS (
                SELECT 1 
                FROM public.tasks t
                JOIN public.contacts c ON c.company_id = t.company_id 
                                      AND (t.contact_id IS NULL OR t.contact_id = c.id)
                WHERE t.assigned_actor_type = 'agent'
                  AND t.assigned_actor_identity = v_agent_id
                  AND t.status IN ('pending', 'in_progress')
                  AND LOWER(TRIM(c.email)) = v_clean_id
            ) INTO v_in_scope;

        ELSIF p_target_type = 'domain' THEN
            SELECT EXISTS (
                SELECT 1 
                FROM public.tasks t
                WHERE t.assigned_actor_type = 'agent'
                  AND t.assigned_actor_identity = v_agent_id
                  AND t.status IN ('pending', 'in_progress')
                  AND (
                      EXISTS (
                          SELECT 1 FROM public.companies comp 
                          WHERE comp.id = t.company_id 
                            AND LOWER(TRIM(comp.primary_domain)) = v_clean_id
                      )
                      OR EXISTS (
                          SELECT 1 FROM public.company_domains cd 
                          WHERE cd.company_id = t.company_id 
                            AND LOWER(TRIM(cd.domain)) = v_clean_id
                      )
                  )
            ) INTO v_in_scope;

        ELSIF p_target_type = 'linkedin' THEN
            SELECT EXISTS (
                SELECT 1 
                FROM public.tasks t
                JOIN public.contacts c ON c.company_id = t.company_id 
                                      AND (t.contact_id IS NULL OR t.contact_id = c.id)
                WHERE t.assigned_actor_type = 'agent'
                  AND t.assigned_actor_identity = v_agent_id
                  AND t.status IN ('pending', 'in_progress')
                  AND LOWER(TRIM(c.linkedin_url)) = v_clean_id
            ) INTO v_in_scope;

        ELSIF p_target_type = 'phone' THEN
            SELECT EXISTS (
                SELECT 1 
                FROM public.tasks t
                JOIN public.contacts c ON c.company_id = t.company_id 
                                      AND (t.contact_id IS NULL OR t.contact_id = c.id)
                WHERE t.assigned_actor_type = 'agent'
                  AND t.assigned_actor_identity = v_agent_id
                  AND t.status IN ('pending', 'in_progress')
                  AND LOWER(TRIM(c.phone)) = v_clean_id
            ) INTO v_in_scope;
        ELSE
            v_in_scope := FALSE;
        END IF;

        IF NOT v_in_scope THEN
            RAISE EXCEPTION 'Security Policy Violation: Queried % identifier "%" is outside the authorized scope of active tasks for agent %.', p_target_type, p_identifier, v_agent_id;
        END IF;

        RETURN EXISTS (
            SELECT 1 FROM public.suppression_registry
            WHERE target_type = p_target_type
              AND identifier = v_clean_id
        );
    END IF;

    -- 4. Reject any caller that is neither an authenticated human operator nor an agent
    RAISE EXCEPTION 'Security Policy Violation: Caller lacks authorization for suppression lookup.';
END;
$$ LANGUAGE plpgsql STABLE;

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Human Approval Gate & Invalidation on Outreach
-- Enforces:
-- 1. No message can be queued or sent without human operator sign-off.
-- 2. Non-null authenticated auth.uid() required.
-- 3. Approver ID must match auth.uid() unless explicit auditable override.
-- 4. Approver timestamp is generated by database (clock_timestamp()).
-- 5. Material changes to approved message content automatically invalidate approval.
-- 6. Pre-dispatch suppression check across email, domain, and LinkedIn.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_outreach_human_approval()
RETURNS TRIGGER AS $$
DECLARE
    contact_rec RECORD;
BEGIN
    -- Block revenue sharing in cold outreach drafts
    IF NEW.proposed_commercial_model = 'strategic_revenue_sharing' THEN
        RAISE EXCEPTION 'Business Rule Violation: Strategic Revenue Sharing is strictly prohibited in outreach correspondence.';
    END IF;

    -- Block outreach creation or queueing for disqualified companies
    IF EXISTS (
        SELECT 1 FROM companies comp 
        WHERE comp.id = NEW.company_id 
          AND (comp.qualification_tier = 'disqualified' OR comp.lifecycle_stage = 'disqualified')
    ) THEN
        RAISE EXCEPTION 'Compliance Violation: Outreach messages cannot be created, queued, or dispatched for a disqualified company (%).', NEW.company_id;
    END IF;

    -- Approval status state machine rules
    IF OLD IS NOT NULL AND NEW.approval_status != OLD.approval_status THEN
        -- Cannot jump directly from rejected to approved or pending_human_approval
        IF OLD.approval_status = 'rejected' AND NEW.approval_status != 'draft' THEN
            RAISE EXCEPTION 'Lifecycle Violation: Rejected outreach messages must return to draft before resubmission or approval.';
        END IF;

        -- Rejection requires non-empty reason and locks sending status
        IF NEW.approval_status = 'rejected' THEN
            IF NEW.rejection_reason IS NULL OR TRIM(NEW.rejection_reason) = '' THEN
                RAISE EXCEPTION 'Integrity Violation: Rejection of outreach message requires a non-empty rejection_reason.';
            END IF;
            IF NEW.sending_status NOT IN ('draft', 'cancelled') THEN
                RAISE EXCEPTION 'Integrity Violation: Rejected outreach message cannot enter dispatch states.';
            END IF;
        END IF;
    END IF;

    -- Sending status state machine progression rules
    IF OLD IS NOT NULL AND NEW.sending_status != OLD.sending_status THEN
        IF OLD.sending_status IN ('replied', 'bounced') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Outreach message in terminal sending status (%) cannot be transitioned.', OLD.sending_status;
        END IF;

        IF OLD.sending_status = 'draft' AND NEW.sending_status NOT IN ('queued', 'cancelled') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Draft outreach message must transition to queued before sending.';
        END IF;

        IF OLD.sending_status = 'queued' AND NEW.sending_status NOT IN ('sending', 'cancelled', 'draft', 'failed') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Queued outreach message can only transition to sending, cancelled, draft, or failed.';
        END IF;

        IF OLD.sending_status = 'sending' AND NEW.sending_status NOT IN ('sent', 'failed') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Sending outreach message can only transition to sent or failed.';
        END IF;

        IF OLD.sending_status = 'sent' AND NEW.sending_status NOT IN ('delivered', 'bounced', 'opened', 'replied') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Sent outreach message cannot transition backward to %.', NEW.sending_status;
        END IF;

        IF OLD.sending_status = 'delivered' AND NEW.sending_status NOT IN ('opened', 'replied') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Delivered outreach message cannot transition backward to %.', NEW.sending_status;
        END IF;

        IF OLD.sending_status = 'opened' AND NEW.sending_status NOT IN ('replied') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Opened outreach message cannot transition backward to %.', NEW.sending_status;
        END IF;

        -- Delivery events must not alter approval status or approval metadata
        IF NEW.sending_status IN ('delivered', 'opened', 'replied', 'bounced') THEN
            IF NEW.approval_status != OLD.approval_status OR
               NEW.approved_by_human_id IS DISTINCT FROM OLD.approved_by_human_id OR
               NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                RAISE EXCEPTION 'Integrity Violation: Delivery event status updates cannot modify message approval status or approval metadata.';
            END IF;
        END IF;
    END IF;

    -- Invalidation on material edits: If an approved message has its content, recipient, targeting, commercial meaning, or personalization altered, reset to draft!
    IF OLD IS NOT NULL AND OLD.approval_status = 'approved' AND NEW.approval_status = 'approved' THEN
        IF (
            NEW.body IS DISTINCT FROM OLD.body OR
            NEW.subject IS DISTINCT FROM OLD.subject OR
            NEW.channel IS DISTINCT FROM OLD.channel OR
            NEW.company_id IS DISTINCT FROM OLD.company_id OR
            NEW.contact_id IS DISTINCT FROM OLD.contact_id OR
            NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR
            NEW.sequence_step IS DISTINCT FROM OLD.sequence_step OR
            NEW.parent_message_id IS DISTINCT FROM OLD.parent_message_id OR
            NEW.personalization_evidence_ids IS DISTINCT FROM OLD.personalization_evidence_ids OR
            NEW.personalization_rationale IS DISTINCT FROM OLD.personalization_rationale OR
            NEW.proposed_commercial_model IS DISTINCT FROM OLD.proposed_commercial_model
        ) THEN
            -- Invalidate approval due to material modification
            NEW.approval_status = 'draft';
            NEW.approved_by_human_id = NULL;
            NEW.approved_at = NULL;
            NEW.is_admin_override = FALSE;
            NEW.override_reason = NULL;
            NEW.override_by_human_id = NULL;
            NEW.sending_status = 'draft';
        END IF;
    END IF;

    -- Protect approval metadata against arbitrary modification after approval
    IF OLD IS NOT NULL AND OLD.approval_status = 'approved' AND NEW.approval_status = 'approved' THEN
        IF NEW.approved_by_human_id IS DISTINCT FROM OLD.approved_by_human_id OR
           NEW.approved_at IS DISTINCT FROM OLD.approved_at OR
           NEW.is_admin_override IS DISTINCT FROM OLD.is_admin_override OR
           NEW.override_reason IS DISTINCT FROM OLD.override_reason OR
           NEW.override_by_human_id IS DISTINCT FROM OLD.override_by_human_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter approval identity, timestamps, or override metadata on an already approved message.';
        END IF;
    END IF;

    -- Handle transition to approved
    IF NEW.approval_status = 'approved' AND (OLD IS NULL OR OLD.approval_status != 'approved') THEN
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from approving outreach messages.';
        END IF;

        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only authenticated human operators can approve outreach messages.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Approval requires an authenticated session with non-null auth.uid().';
        END IF;

        -- Admin override check
        IF NEW.is_admin_override = TRUE THEN
            IF NOT is_admin_operator() THEN
                RAISE EXCEPTION 'Security Policy Violation: Only authorized admin or founder roles can perform an administrative override.';
            END IF;

            IF NEW.override_reason IS NULL OR TRIM(NEW.override_reason) = '' THEN
                RAISE EXCEPTION 'Audit Violation: Admin override requires a non-empty override_reason.';
            END IF;

            IF NEW.override_by_human_id IS NULL OR NEW.override_by_human_id != auth.uid() THEN
                RAISE EXCEPTION 'Audit Violation: override_by_human_id must match authenticated admin user ID (%).', auth.uid();
            END IF;

            IF NEW.approved_by_human_id IS NULL THEN
                RAISE EXCEPTION 'Audit Violation: approved_by_human_id must be specified when performing admin override.';
            END IF;
        ELSE
            -- Standard Approval: approved_by_human_id must match auth.uid()
            IF NEW.approved_by_human_id IS DISTINCT FROM auth.uid() THEN
                RAISE EXCEPTION 'Security Policy Violation: approved_by_human_id (%) must match authenticated user (%).', NEW.approved_by_human_id, auth.uid();
            END IF;

            -- Non-override approvals must not contain override metadata
            NEW.override_reason = NULL;
            NEW.override_by_human_id = NULL;
        END IF;

        -- Overwrite with trusted database timestamp
        NEW.approved_at = clock_timestamp();
    END IF;

    -- Reset approval fields if approval_status is not approved
    IF NEW.approval_status != 'approved' THEN
        NEW.approved_by_human_id = NULL;
        NEW.approved_at = NULL;
        NEW.is_admin_override = FALSE;
        NEW.override_reason = NULL;
        NEW.override_by_human_id = NULL;
    END IF;

    -- Verify dispatch authorization & suppression check:
    -- Any status other than 'draft' or 'cancelled' implies dispatch or downstream event
    IF NEW.sending_status IN ('queued', 'sending', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed') THEN
        IF NEW.approval_status != 'approved' OR NEW.approved_by_human_id IS NULL OR NEW.approved_at IS NULL THEN
            RAISE EXCEPTION 'Integrity Violation: Outreach message % cannot enter dispatch or downstream status (%) without valid human approval.', NEW.id, NEW.sending_status;
        END IF;

        -- Fetch contact details and company domain
        SELECT LOWER(c.email) AS email, LOWER(c.linkedin_url) AS linkedin_url, LOWER(comp.primary_domain) AS domain
        INTO contact_rec
        FROM contacts c
        JOIN companies comp ON comp.id = c.company_id
        WHERE c.id = NEW.contact_id;

        -- Check permanent multi-channel suppression directly against registry (trusted database gate)
        IF EXISTS (
            SELECT 1 FROM suppression_registry sr
            WHERE (sr.target_type = 'email' AND sr.identifier = contact_rec.email)
               OR (sr.target_type = 'domain' AND sr.identifier = contact_rec.domain)
               OR (sr.target_type = 'linkedin' AND sr.identifier = contact_rec.linkedin_url)
        ) THEN
            RAISE EXCEPTION 'Suppression Violation: Recipient or domain is on the permanent suppression list.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_outreach_approval
    BEFORE INSERT OR UPDATE ON outreach_messages
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_outreach_human_approval();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Evidence-Based Lead Qualification & Human Gate
-- Enforces:
-- 1. Dimensions > 0 must have corresponding evidence in dimension_evidence JSONB.
-- 2. Tier assignment must agree with approved thresholds.
-- 3. Approvals require human operator with auth.uid() matching reviewed_by_human_id.
-- 4. Material edits to approved scorecards automatically reset review_status to draft.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_lead_qualification_rules()
RETURNS TRIGGER AS $$
DECLARE
    has_dq BOOLEAN;
BEGIN
    -- 1. Validate that positive scores possess structured supporting evidence
    IF NEW.customer_flow_score > 0 AND (NEW.dimension_evidence -> 'customer_flow' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: customer_flow_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.development_demand_score > 0 AND (NEW.dimension_evidence -> 'development_demand' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: development_demand_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.repeatability_orders_score > 0 AND (NEW.dimension_evidence -> 'repeatability_orders' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: repeatability_orders_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.technical_fit_score > 0 AND (NEW.dimension_evidence -> 'technical_fit' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: technical_fit_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.business_size_score > 0 AND (NEW.dimension_evidence -> 'business_size' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: business_size_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.decision_maker_accessibility_score > 0 AND (NEW.dimension_evidence -> 'decision_maker_accessibility' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: decision_maker_accessibility_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.commercial_fit_score > 0 AND (NEW.dimension_evidence -> 'commercial_fit' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: commercial_fit_score > 0 requires documented evidence in dimension_evidence.';
    END IF;

    -- 2. Validate Tier assignment strictly against approved thresholds
    has_dq := (cardinality(NEW.disqualification_reasons) > 0);
    IF has_dq OR NEW.overall_score < 40 THEN
        IF NEW.qualification_tier != 'disqualified' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Leads with disqualification reasons or score < 40 must be disqualified.';
        END IF;
    ELSIF NEW.overall_score >= 80 THEN
        IF NEW.qualification_tier != 'tier_1_priority' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Score >= 80 must be assigned tier_1_priority.';
        END IF;
    ELSIF NEW.overall_score >= 60 THEN
        IF NEW.qualification_tier != 'tier_2_prospective' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Score between 60-79 must be assigned tier_2_prospective.';
        END IF;
    ELSE
        IF NEW.qualification_tier != 'tier_3_deferred' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Score between 40-59 must be assigned tier_3_deferred.';
        END IF;
    END IF;

    -- 3. Invalidate approval on material score, evidence, tier, or recommendation modification
    IF OLD IS NOT NULL AND OLD.review_status = 'approved' AND NEW.review_status = 'approved' THEN
        IF (
            NEW.company_id IS DISTINCT FROM OLD.company_id OR
            NEW.overall_score IS DISTINCT FROM OLD.overall_score OR
            NEW.customer_flow_score IS DISTINCT FROM OLD.customer_flow_score OR
            NEW.development_demand_score IS DISTINCT FROM OLD.development_demand_score OR
            NEW.repeatability_orders_score IS DISTINCT FROM OLD.repeatability_orders_score OR
            NEW.technical_fit_score IS DISTINCT FROM OLD.technical_fit_score OR
            NEW.business_size_score IS DISTINCT FROM OLD.business_size_score OR
            NEW.decision_maker_accessibility_score IS DISTINCT FROM OLD.decision_maker_accessibility_score OR
            NEW.commercial_fit_score IS DISTINCT FROM OLD.commercial_fit_score OR
            NEW.dimension_evidence IS DISTINCT FROM OLD.dimension_evidence OR
            NEW.qualification_tier IS DISTINCT FROM OLD.qualification_tier OR
            NEW.disqualification_reasons IS DISTINCT FROM OLD.disqualification_reasons OR
            NEW.disqualification_notes IS DISTINCT FROM OLD.disqualification_notes OR
            NEW.recommended_next_action IS DISTINCT FROM OLD.recommended_next_action
        ) THEN
            NEW.review_status = 'draft';
            NEW.reviewed_by_human_id = NULL;
            NEW.reviewed_at = NULL;
        END IF;
    END IF;

    -- 4. Protect approval metadata against arbitrary modification after approval
    IF OLD IS NOT NULL AND OLD.review_status = 'approved' AND NEW.review_status = 'approved' THEN
        IF NEW.reviewed_by_human_id IS DISTINCT FROM OLD.reviewed_by_human_id OR
           NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter reviewer identity or timestamp of an approved qualification.';
        END IF;
    END IF;

    -- 5. Enforce Human Gate on Approval
    IF NEW.review_status = 'approved' AND (OLD IS NULL OR OLD.review_status != 'approved') THEN
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from approving lead qualifications.';
        END IF;

        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only authenticated human operators can approve lead qualifications.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Qualification approval requires an authenticated session with non-null auth.uid().';
        END IF;

        IF NEW.reviewed_by_human_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Security Policy Violation: reviewed_by_human_id must match authenticated operator ID (%).', auth.uid();
        END IF;

        NEW.reviewed_at = clock_timestamp();
    END IF;

    -- Review status transition rules
    IF OLD IS NOT NULL AND NEW.review_status != OLD.review_status THEN
        -- Rejected qualifications must return to draft before resubmission
        IF OLD.review_status = 'rejected' AND NEW.review_status NOT IN ('draft') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Rejected qualifications must return to draft before resubmission or approval.';
        END IF;

        -- Rejection requires disqualification reasons or notes
        IF NEW.review_status = 'rejected' THEN
            IF NEW.disqualification_notes IS NULL AND cardinality(NEW.disqualification_reasons) = 0 THEN
                RAISE EXCEPTION 'Integrity Violation: Rejection of lead qualification requires documented disqualification reasons or notes.';
            END IF;
        END IF;
    END IF;

    -- Reset reviewer fields if review_status is not approved
    IF NEW.review_status != 'approved' THEN
        NEW.reviewed_by_human_id = NULL;
        NEW.reviewed_at = NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_lead_qual_rules
    BEFORE INSERT OR UPDATE ON lead_qualifications
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_lead_qualification_rules();

-- -----------------------------------------------------------------------------
-- Trigger Function: Sync Company Lifecycle and Tier on Lead Qualification Approval
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sync_company_qualification()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.review_status = 'approved' AND (OLD IS NULL OR OLD.review_status != 'approved') THEN
        UPDATE companies
        SET 
            qualification_tier = NEW.qualification_tier,
            lifecycle_stage = CASE 
                WHEN NEW.qualification_tier = 'disqualified' THEN 'disqualified'::company_lifecycle_stage_type
                WHEN lifecycle_stage IN ('identified', 'researching') THEN 'qualified'::company_lifecycle_stage_type
                ELSE lifecycle_stage -- Preserve later legitimate lifecycle stages!
            END,
            updated_at = clock_timestamp()
        WHERE id = NEW.company_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_lead_qualification_to_company
    AFTER INSERT OR UPDATE ON lead_qualifications
    FOR EACH ROW
    EXECUTE FUNCTION fn_sync_company_qualification();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Company Lifecycle Stage Transitions & Disqualification Guards
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_company_lifecycle_transitions()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD IS NOT NULL AND NEW.lifecycle_stage != OLD.lifecycle_stage THEN
        -- Agents are strictly prohibited from changing company lifecycle stage
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from modifying company lifecycle stages.';
        END IF;

        -- Disqualified company cannot enter active outreach, negotiation, or partnership
        IF OLD.lifecycle_stage = 'disqualified' AND NEW.lifecycle_stage NOT IN ('identified', 'researching', 'churned') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Disqualified company cannot transition directly to %. Re-evaluation via researching or identified required.', NEW.lifecycle_stage;
        END IF;

        -- Outreach stages require qualification tier to be qualified (not null and not disqualified)
        IF NEW.lifecycle_stage IN ('outreach_queued', 'in_outreach') THEN
            IF NEW.qualification_tier IS NULL OR NEW.qualification_tier = 'disqualified' THEN
                RAISE EXCEPTION 'Lifecycle Violation: Company cannot enter outreach stages while unqualified or disqualified.';
            END IF;
        END IF;

        -- Identified cannot jump directly to active execution or partnership
        IF OLD.lifecycle_stage = 'identified' AND NEW.lifecycle_stage IN ('in_outreach', 'active_conversation', 'negotiating', 'partner_active', 'partner_dormant') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Identified companies must be researched and qualified before entering %.', NEW.lifecycle_stage;
        END IF;

        -- Churned partner re-activation rules
        IF OLD.lifecycle_stage = 'churned' AND NEW.lifecycle_stage NOT IN ('identified', 'researching', 'partner_dormant') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Churned partners can only re-enter lifecycle via identified, researching, or partner_dormant.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_company_lifecycle
    BEFORE UPDATE OF lifecycle_stage ON companies
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_company_lifecycle_transitions();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Task Governance & Immutability for Agents
-- Enforces:
-- 1. Agents can only update their own assigned tasks (further gated by RLS).
-- 2. Agents can NEVER modify:
--    - assigned_actor_type
--    - assigned_actor_identity
--    - company_id
--    - contact_id
--    - task_type
--    - title
--    - priority
--    - due_date
--    - created_at
-- 3. Agents can only update operational fields: status, description, completed_at.
-- 4. When status is transitioned to 'completed', completed_at is set by database (clock_timestamp()).
-- 5. If status is transitioned away from 'completed', completed_at is reset to NULL.
-- 6. Human operators retain full management authority.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_task_governance()
RETURNS TRIGGER AS $$
BEGIN
    -- Operational lifecycle rules for all callers
    IF NEW.status = 'completed' AND (OLD IS NULL OR OLD.status != 'completed') THEN
        IF NEW.completed_at IS NULL THEN
            NEW.completed_at = clock_timestamp();
        END IF;
    ELSIF NEW.status != 'completed' THEN
        NEW.completed_at = NULL;
    END IF;

    -- Only restrict agents; human operators retain full operational management
    IF is_agent() THEN
        -- Prevent agents from modifying or reopening completed or cancelled tasks
        IF OLD IS NOT NULL AND OLD.status IN ('completed', 'cancelled') THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot reopen, modify, or transition completed or cancelled tasks.';
        END IF;

        -- Agents cannot jump pending directly to completed without in_progress
        IF OLD IS NOT NULL AND OLD.status = 'pending' AND NEW.status = 'completed' THEN
            RAISE EXCEPTION 'Lifecycle Violation: Tasks must be transitioned to in_progress before completion.';
        END IF;

        -- Prevent modification of assignment and scope fields
        IF NEW.assigned_actor_type IS DISTINCT FROM OLD.assigned_actor_type OR
           NEW.assigned_actor_identity IS DISTINCT FROM OLD.assigned_actor_identity THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot reassign tasks or alter task actor assignment.';
        END IF;

        IF NEW.campaign_id IS DISTINCT FROM OLD.campaign_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task campaign_id scope.';
        END IF;

        IF NEW.company_id IS DISTINCT FROM OLD.company_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task company_id scope.';
        END IF;

        IF NEW.contact_id IS DISTINCT FROM OLD.contact_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task contact_id scope.';
        END IF;

        IF NEW.task_type IS DISTINCT FROM OLD.task_type THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task_type.';
        END IF;

        IF NEW.title IS DISTINCT FROM OLD.title THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task title.';
        END IF;

        IF NEW.priority IS DISTINCT FROM OLD.priority THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task priority.';
        END IF;

        IF NEW.due_date IS DISTINCT FROM OLD.due_date THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task due_date.';
        END IF;

        IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task created_at.';
        END IF;

        -- Prevent tampering with task completed_at timestamp
        IF OLD.status = 'completed' AND NEW.status = 'completed' THEN
            IF NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                RAISE EXCEPTION 'Security Policy Violation: Agents cannot tamper with task completed_at timestamp.';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_task_governance
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_task_governance();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Conversation Status Transitions
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_conversation_status_transitions()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD IS NOT NULL AND NEW.status != OLD.status THEN
        -- Only human operators can change conversation status
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are not permitted to modify conversation status.';
        END IF;

        -- Reopening terminal conversations requires human operator authority
        IF OLD.status IN ('closed_lost', 'closed_won') AND NEW.status NOT IN ('closed_lost', 'closed_won') THEN
            IF NOT is_human_operator() THEN
                RAISE EXCEPTION 'Security Policy Violation: Only authenticated human operators can reopen closed conversations.';
            END IF;
            IF NEW.status NOT IN ('active', 'paused') THEN
                RAISE EXCEPTION 'Lifecycle Violation: Closed conversations can only be reopened to active or paused stage.';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_conversation_status
    BEFORE UPDATE OF status ON conversations
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_conversation_status_transitions();

-- -----------------------------------------------------------------------------
-- Trigger Function: Capture Technical Audit Trail
-- Records immutable technical audit logs across sensitive state mutations.
-- Security Mode: SECURITY DEFINER with fixed safe search_path = pg_catalog, public.
-- Explicitly schema-qualifies relation references to prevent search-path hijack.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_audit_log_capture()
RETURNS TRIGGER 
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_action TEXT;
    v_actor_type actor_type;
    v_actor_id TEXT;
BEGIN
    v_action := TG_OP;

    -- Refine specific business action tags
    IF TG_TABLE_NAME = 'outreach_messages' THEN
        IF TG_OP = 'UPDATE' AND OLD.approval_status != 'approved' AND NEW.approval_status = 'approved' THEN
            v_action := 'APPROVAL_GRANTED';
        ELSIF TG_OP = 'UPDATE' AND OLD.approval_status = 'approved' AND NEW.approval_status = 'draft' THEN
            v_action := 'APPROVAL_REVOKED';
        END IF;
    ELSIF TG_TABLE_NAME = 'lead_qualifications' THEN
        IF TG_OP = 'UPDATE' AND OLD.review_status != 'approved' AND NEW.review_status = 'approved' THEN
            v_action := 'APPROVAL_GRANTED';
        END IF;
    END IF;

    -- Determine actor attribution
    IF public.is_human_operator() THEN
        v_actor_type := 'human';
        v_actor_id := COALESCE(auth.uid()::TEXT, 'unknown_human');
    ELSIF public.is_agent() THEN
        v_actor_type := 'agent';
        v_actor_id := COALESCE(public.current_agent_identity(), 'unknown_agent');
    ELSE
        v_actor_type := 'system';
        v_actor_id := 'system_trigger';
    END IF;

    INSERT INTO public.audit_logs (
        table_name, record_id, action, actor_type, actor_identity, old_values, new_values, metadata
    ) VALUES (
        TG_TABLE_NAME,
        COALESCE(NEW.id, OLD.id),
        v_action,
        v_actor_type,
        v_actor_id,
        CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) ELSE NULL END,
        CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) ELSE NULL END,
        jsonb_build_object('client_role', public.current_actor_role(), 'occurred_at', clock_timestamp())
    );

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_outreach_messages
    AFTER INSERT OR UPDATE OR DELETE ON outreach_messages
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

CREATE TRIGGER trg_audit_lead_qualifications
    AFTER INSERT OR UPDATE OR DELETE ON lead_qualifications
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

CREATE TRIGGER trg_audit_suppression
    AFTER INSERT OR UPDATE ON suppression_registry
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

-- =============================================================================
-- 5. PERFORMANCE & SEARCH INDEXES (MVP CORE)
-- =============================================================================

CREATE INDEX idx_suppression_target_identifier ON suppression_registry(target_type, identifier);
CREATE INDEX idx_companies_lifecycle_segment ON companies(lifecycle_stage, primary_segment);
CREATE INDEX idx_companies_qualification_tier ON companies(qualification_tier) WHERE qualification_tier IS NOT NULL;
CREATE INDEX idx_companies_tech_stack_gin ON companies USING GIN (tech_stack);
CREATE INDEX idx_companies_services_gin ON companies USING GIN (services_offered);
CREATE INDEX idx_companies_normalized_name ON companies(normalized_name);

CREATE INDEX idx_contacts_company_id ON contacts(company_id);
CREATE INDEX idx_contacts_role_decision ON contacts(company_id, role_type, is_decision_maker);
CREATE INDEX idx_contacts_verification ON contacts(verification_status) WHERE verification_status = 'unverified';

CREATE INDEX idx_research_findings_report ON research_findings(report_id, company_id);
CREATE INDEX idx_research_findings_company_type ON research_findings(company_id, finding_type);

CREATE INDEX idx_lead_qualifications_company ON lead_qualifications(company_id);
CREATE INDEX idx_lead_qualifications_pending_review ON lead_qualifications(review_status) WHERE review_status = 'pending_human_review';

CREATE INDEX idx_outreach_messages_company ON outreach_messages(company_id);
CREATE INDEX idx_outreach_messages_contact ON outreach_messages(contact_id);
CREATE INDEX idx_outreach_messages_approval_queue ON outreach_messages(approval_status) WHERE approval_status = 'pending_human_approval';
CREATE INDEX idx_outreach_messages_send_queue ON outreach_messages(sending_status, scheduled_for) WHERE sending_status = 'queued';

CREATE INDEX idx_conversations_company ON conversations(company_id);
CREATE INDEX idx_conversations_status ON conversations(status);
CREATE INDEX idx_conversation_messages_conversation ON conversation_messages(conversation_id, message_timestamp);
CREATE INDEX idx_activities_company ON activities(company_id, occurred_at);
CREATE INDEX idx_activities_type ON activities(activity_type);

CREATE INDEX idx_tasks_status_assigned ON tasks(status, assigned_actor_type, assigned_actor_identity);
CREATE INDEX idx_tasks_company ON tasks(company_id) WHERE company_id IS NOT NULL;
CREATE INDEX idx_tasks_contact ON tasks(contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX idx_tasks_campaign ON tasks(campaign_id) WHERE campaign_id IS NOT NULL;

CREATE INDEX idx_audit_logs_record ON audit_logs(table_name, record_id);
CREATE INDEX idx_audit_logs_actor ON audit_logs(actor_type, actor_identity);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);

-- =============================================================================
-- 6. ROW LEVEL SECURITY (RLS) & PRIVILEGE MATRIX
-- =============================================================================

-- Enable RLS on all 14 MVP tables
ALTER TABLE suppression_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE lead_qualifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 6.1. Public Anonymous Role Policies (Strict Zero Public Access)
-- -----------------------------------------------------------------------------
CREATE POLICY anon_deny_suppression ON suppression_registry FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_companies ON companies FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_company_domains ON company_domains FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_contacts ON contacts FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_research_reports ON research_reports FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_research_findings ON research_findings FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_lead_qualifications ON lead_qualifications FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_outreach_campaigns ON outreach_campaigns FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_outreach_messages ON outreach_messages FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_conversations ON conversations FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_conversation_messages ON conversation_messages FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_activities ON activities FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_tasks ON tasks FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_audit_logs ON audit_logs FOR ALL TO anon USING (false);

-- -----------------------------------------------------------------------------
-- 6.2. Human Operator Policies (Full Operational Access)
-- Authenticated users carrying verified human operator claim.
-- -----------------------------------------------------------------------------
CREATE POLICY human_operator_all_suppression ON suppression_registry FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_companies ON companies FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_company_domains ON company_domains FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_contacts ON contacts FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_research_reports ON research_reports FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_research_findings ON research_findings FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_lead_qualifications ON lead_qualifications FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_outreach_campaigns ON outreach_campaigns FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_outreach_messages ON outreach_messages FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_conversations ON conversations FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_conversation_messages ON conversation_messages FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_activities ON activities FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_tasks ON tasks FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_select_audit_logs ON audit_logs FOR SELECT TO authenticated 
    USING (is_human_operator());

-- -----------------------------------------------------------------------------
-- 6.3. Scoped Automated Agent Policies (Least Privilege Separation)
-- Verified via is_agent(), is_agent_type(), and task/author scoping.
-- Broad USING (is_agent()) policies removed to prevent unrestricted CRM dumping.
-- Unknown roles receive ZERO access.
-- -----------------------------------------------------------------------------

-- Directory Read Access for Agents (Task-scoped, active non-suppressed companies & domains only)
CREATE POLICY agent_select_companies ON companies FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        is_suppressed = FALSE AND 
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = companies.id
              AND t.status IN ('pending', 'in_progress')
        )
    );

CREATE POLICY agent_select_company_domains ON company_domains FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = company_domains.company_id
              AND t.status IN ('pending', 'in_progress')
        )
    );

-- Note on suppression_registry: Broad agent SELECT is removed to prevent enumeration.
-- Subagents must use trusted helper function is_identifier_suppressed().

-- Scoped Contact Access: Only non-opted-out contacts within active assigned task scope
CREATE POLICY agent_select_contacts ON contacts FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        is_opted_out = FALSE AND 
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.status IN ('pending', 'in_progress')
              AND t.company_id = contacts.company_id
              AND (t.contact_id IS NULL OR t.contact_id = contacts.id)
        )
    );

-- Campaign Access: Only active campaigns explicitly associated with an active task assigned to the Drafter
CREATE POLICY agent_select_campaigns ON outreach_campaigns FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        is_agent_type('drafter') AND 
        is_active = TRUE AND
        EXISTS (
            SELECT 1 FROM tasks t
            WHERE t.assigned_actor_type = 'agent'
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.campaign_id = outreach_campaigns.id
              AND t.status IN ('pending', 'in_progress')
        )
    );

-- Research Subagent Scoped Permissions (Task-scoped to assigned company)
CREATE POLICY agent_insert_research_reports ON research_reports FOR INSERT TO authenticated 
    WITH CHECK (
        is_agent() AND 
        is_agent_type('researcher') AND 
        research_agent_identity = current_agent_identity() AND
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = research_reports.company_id
              AND t.status IN ('pending', 'in_progress')
        )
    );

CREATE POLICY agent_select_research_reports ON research_reports FOR SELECT TO authenticated 
    USING (
        is_agent() AND (
            (is_agent_type('researcher') AND research_agent_identity = current_agent_identity()) OR
            EXISTS (
                SELECT 1 FROM tasks t 
                WHERE t.assigned_actor_type = 'agent' 
                  AND t.assigned_actor_identity = current_agent_identity()
                  AND t.company_id = research_reports.company_id
                  AND t.status IN ('pending', 'in_progress')
            )
        )
    );

CREATE POLICY agent_insert_research_findings ON research_findings FOR INSERT TO authenticated 
    WITH CHECK (
        is_agent() AND 
        is_agent_type('researcher') AND 
        EXISTS (
            SELECT 1 FROM research_reports r 
            WHERE r.id = research_findings.report_id 
              AND r.company_id = research_findings.company_id
              AND r.research_agent_identity = current_agent_identity()
        ) AND
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = research_findings.company_id
              AND t.status IN ('pending', 'in_progress')
        )
    );

CREATE POLICY agent_select_research_findings ON research_findings FOR SELECT TO authenticated 
    USING (
        is_agent() AND EXISTS (
            SELECT 1 FROM research_reports r 
            WHERE r.id = research_findings.report_id 
              AND (
                  (is_agent_type('researcher') AND r.research_agent_identity = current_agent_identity()) OR
                  EXISTS (
                      SELECT 1 FROM tasks t 
                      WHERE t.assigned_actor_type = 'agent' 
                        AND t.assigned_actor_identity = current_agent_identity()
                        AND t.company_id = r.company_id
                        AND t.status IN ('pending', 'in_progress')
                  )
              )
        )
    );

-- Qualification Subagent Scoped Permissions (Task-scoped to assigned company)
CREATE POLICY agent_insert_lead_qualifications ON lead_qualifications FOR INSERT TO authenticated 
    WITH CHECK (
        is_agent() AND 
        is_agent_type('qualifier') AND 
        evaluator_type = 'agent' AND 
        evaluator_identity = current_agent_identity() AND 
        review_status = 'draft' AND 
        reviewed_by_human_id IS NULL AND 
        reviewed_at IS NULL AND
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = lead_qualifications.company_id
              AND t.status IN ('pending', 'in_progress')
        )
    );

CREATE POLICY agent_select_lead_qualifications ON lead_qualifications FOR SELECT TO authenticated 
    USING (
        is_agent() AND (
            (is_agent_type('qualifier') AND (
                evaluator_identity = current_agent_identity() OR 
                EXISTS (
                    SELECT 1 FROM tasks t 
                    WHERE t.assigned_actor_type = 'agent' 
                      AND t.assigned_actor_identity = current_agent_identity()
                      AND t.company_id = lead_qualifications.company_id
                      AND t.status IN ('pending', 'in_progress')
                )
            )) OR
            (is_agent_type('drafter') AND review_status = 'approved' AND EXISTS (
                SELECT 1 FROM tasks t 
                WHERE t.assigned_actor_type = 'agent' 
                  AND t.assigned_actor_identity = current_agent_identity()
                  AND t.company_id = lead_qualifications.company_id
                  AND t.status IN ('pending', 'in_progress')
            ))
        )
    );

-- Outreach Drafting Subagent Scoped Permissions (Task-scoped to assigned company & contact)
CREATE POLICY agent_insert_outreach_messages ON outreach_messages FOR INSERT TO authenticated 
    WITH CHECK (
        is_agent() AND 
        is_agent_type('drafter') AND 
        drafted_by_agent_identity = current_agent_identity() AND 
        approval_status = 'draft' AND 
        approved_by_human_id IS NULL AND 
        approved_at IS NULL AND 
        is_admin_override = FALSE AND 
        override_reason IS NULL AND 
        override_by_human_id IS NULL AND 
        sending_status = 'draft' AND
        proposed_commercial_model != 'strategic_revenue_sharing' AND
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = outreach_messages.company_id
              AND (t.contact_id IS NULL OR t.contact_id = outreach_messages.contact_id)
              AND (t.campaign_id IS NULL OR t.campaign_id = outreach_messages.campaign_id)
              AND t.status IN ('pending', 'in_progress')
        )
    );

CREATE POLICY agent_select_outreach_messages ON outreach_messages FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        is_agent_type('drafter') AND 
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.status IN ('pending', 'in_progress')
              AND t.company_id = outreach_messages.company_id
              AND (t.contact_id IS NULL OR t.contact_id = outreach_messages.contact_id)
              AND (t.campaign_id IS NULL OR t.campaign_id = outreach_messages.campaign_id)
        )
    );

-- Activity Logging for Agents (Strictly task-scoped to assigned company & contact)
CREATE POLICY agent_insert_activities ON activities FOR INSERT TO authenticated 
    WITH CHECK (
        is_agent() AND 
        performed_by_actor_type = 'agent' AND 
        performed_by_actor_identity = current_agent_identity() AND
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = activities.company_id
              AND (t.contact_id IS NULL OR t.contact_id = activities.contact_id)
              AND t.status IN ('pending', 'in_progress')
        )
    );

CREATE POLICY agent_select_activities ON activities FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.status IN ('pending', 'in_progress')
              AND t.company_id = activities.company_id
              AND (t.contact_id IS NULL OR t.contact_id = activities.contact_id)
        )
    );

-- Task Access Restricted by Agent Identity
CREATE POLICY agent_select_assigned_tasks ON tasks FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        assigned_actor_type = 'agent' AND 
        assigned_actor_identity = current_agent_identity()
    );

CREATE POLICY agent_update_assigned_tasks ON tasks FOR UPDATE TO authenticated 
    USING (
        is_agent() AND 
        assigned_actor_type = 'agent' AND 
        assigned_actor_identity = current_agent_identity()
    )
    WITH CHECK (
        is_agent() AND 
        assigned_actor_type = 'agent' AND 
        assigned_actor_identity = current_agent_identity()
    );

-- =============================================================================
-- 7. POSTGRESQL TABLE PRIVILEGES (GRANT / REVOKE MATRIX)
-- =============================================================================

-- Revoke default wide privileges
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, public;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated, public;

-- Explicitly revoke execution on internal trigger functions and private helpers
REVOKE ALL ON FUNCTION fn_audit_log_capture() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION fn_enforce_company_lifecycle_transitions() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION fn_enforce_conversation_status_transitions() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION is_identifier_suppressed(suppression_target_type, TEXT) FROM PUBLIC, anon;

-- Grant EXECUTE on trusted security and calculation helpers
GRANT EXECUTE ON FUNCTION is_human_operator, is_admin_operator, is_agent, is_agent_type, current_agent_identity, current_actor_role, add_business_days, is_identifier_suppressed TO authenticated;

-- Grant standard DML to authenticated role (Row Level Security narrows row access)
GRANT SELECT, INSERT, UPDATE, DELETE ON companies, company_domains, contacts, research_reports, research_findings, lead_qualifications, outreach_campaigns, outreach_messages, conversations, conversation_messages, activities, tasks TO authenticated;

-- Grant read-only to suppression_registry and audit_logs (Mutations managed via triggers)
GRANT SELECT ON suppression_registry, audit_logs TO authenticated;

-- =============================================================================
-- END OF MVP CORE SCHEMA MIGRATION
-- =============================================================================
