-- HT Lead Radar canonical PostgreSQL schema
-- Version 0.1, 2026-07-23
-- Raw evidence, parsed signals, model decisions, human review, and sales outcomes
-- are deliberately separated so every conclusion can be reproduced and audited.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE companies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name text NOT NULL,
    legal_name_zh text,
    legal_name_en text,
    primary_domain text,
    unified_social_credit_code text,
    sector text,
    sub_sector text,
    company_stage text,
    headquarters_city text,
    parent_company_id uuid REFERENCES companies(id),
    seed_priority smallint CHECK (seed_priority BETWEEN 0 AND 5),
    resolution_status text NOT NULL DEFAULT 'unreviewed'
        CHECK (resolution_status IN ('unreviewed', 'auto_matched', 'human_confirmed', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX companies_primary_domain_uq
    ON companies (lower(primary_domain))
    WHERE primary_domain IS NOT NULL;

CREATE UNIQUE INDEX companies_uscc_uq
    ON companies (unified_social_credit_code)
    WHERE unified_social_credit_code IS NOT NULL;

CREATE TABLE company_aliases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    alias text NOT NULL,
    normalized_alias text NOT NULL,
    alias_type text NOT NULL
        CHECK (alias_type IN ('legal', 'brand', 'english', 'abbreviation', 'former', 'subsidiary_hint')),
    source_url text,
    valid_from date,
    valid_to date,
    confirmed_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, normalized_alias, alias_type)
);

CREATE INDEX company_aliases_normalized_idx ON company_aliases (normalized_alias);

CREATE TABLE source_registry (
    slug text PRIMARY KEY,
    display_name text NOT NULL,
    source_type text NOT NULL,
    access_method text NOT NULL,
    base_url text,
    terms_reviewed_at date,
    robots_reviewed_at date,
    allowed_purpose text,
    rate_limit_per_hour integer,
    retention_days integer,
    owner text,
    enabled boolean NOT NULL DEFAULT true,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE crawl_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_slug text NOT NULL REFERENCES source_registry(slug),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    fetched_count integer NOT NULL DEFAULT 0,
    new_count integer NOT NULL DEFAULT 0,
    updated_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    error_summary text,
    parser_version text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE raw_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_slug text NOT NULL REFERENCES source_registry(slug),
    crawl_run_id uuid REFERENCES crawl_runs(id),
    source_url text NOT NULL,
    canonical_url text,
    source_record_id text,
    occurred_at timestamptz,
    observed_at timestamptz NOT NULL DEFAULT now(),
    content_hash text NOT NULL,
    title text,
    raw_text text,
    raw_json jsonb,
    http_status integer,
    language text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_slug, content_hash)
);

CREATE INDEX raw_documents_source_record_idx
    ON raw_documents (source_slug, source_record_id);
CREATE INDEX raw_documents_observed_idx ON raw_documents (observed_at DESC);

CREATE TABLE company_resolution_candidates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_document_id uuid NOT NULL REFERENCES raw_documents(id) ON DELETE CASCADE,
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    method text NOT NULL,
    probability numeric(5,4) CHECK (probability BETWEEN 0 AND 1),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    decision text NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending', 'accepted', 'rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (raw_document_id, company_id, method)
);

CREATE TABLE signals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_document_id uuid NOT NULL REFERENCES raw_documents(id),
    company_id uuid REFERENCES companies(id),
    signal_type text NOT NULL,
    signal_family text NOT NULL,
    funnel_phase text NOT NULL
        CHECK (funnel_phase IN ('strategy_capital', 'build_organize', 'recruit', 'marketed_competitive', 'negative')),
    is_upstream boolean NOT NULL DEFAULT false,
    estimated_lead_time_days integer,
    direction text NOT NULL DEFAULT 'positive'
        CHECK (direction IN ('positive', 'negative', 'neutral')),
    occurred_at timestamptz,
    observed_at timestamptz NOT NULL,
    base_weight numeric(6,2) NOT NULL,
    half_life_days numeric(8,2) NOT NULL CHECK (half_life_days > 0),
    confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confidence_grade text NOT NULL CHECK (confidence_grade IN ('A', 'B', 'C', 'D')),
    evidence_snippet text NOT NULL,
    independent_source_group text NOT NULL,
    parser_version text,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'rejected', 'superseded')),
    reviewed_by text,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX signals_company_time_idx
    ON signals (company_id, occurred_at DESC)
    WHERE status = 'active';
CREATE INDEX signals_type_time_idx ON signals (signal_type, occurred_at DESC);

CREATE TABLE job_posts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id uuid NOT NULL UNIQUE REFERENCES signals(id) ON DELETE CASCADE,
    source_slug text NOT NULL REFERENCES source_registry(slug),
    source_job_id text,
    title text NOT NULL,
    normalized_title text,
    location text,
    function_label text,
    sector_label text,
    seniority_level text NOT NULL DEFAULT 'unknown'
        CHECK (seniority_level IN ('cxo', 'vp', 'gm', 'director_head', 'manager', 'expert_ic', 'other', 'unknown')),
    seniority_confidence numeric(5,4)
        CHECK (seniority_confidence BETWEEN 0 AND 1),
    is_target_seniority boolean NOT NULL DEFAULT false,
    organization_scope_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    jd_fingerprint text,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    closed_at timestamptz,
    is_evergreen boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'unknown')),
    UNIQUE (source_slug, source_job_id)
);

CREATE INDEX job_posts_fingerprint_idx ON job_posts (jd_fingerprint);

CREATE TABLE people (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid REFERENCES companies(id),
    full_name text NOT NULL,
    normalized_name text,
    current_title text,
    role_started_at date,
    role_last_verified_at timestamptz,
    public_profile_url text,
    source_url text NOT NULL,
    resolution_status text NOT NULL DEFAULT 'unreviewed'
        CHECK (resolution_status IN ('unreviewed', 'human_confirmed', 'stale', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX people_company_idx ON people (company_id);

CREATE TABLE buying_center_roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    person_id uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    persona text NOT NULL
        CHECK (persona IN ('budget_sponsor', 'need_owner', 'execution_owner', 'warm_connector')),
    role_relevance_score numeric(5,2) CHECK (role_relevance_score BETWEEN 0 AND 100),
    evidence text NOT NULL,
    source_url text NOT NULL,
    valid_from date,
    valid_to date,
    reviewed_by text,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, person_id, persona)
);

CREATE TABLE contact_channels (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    channel_type text NOT NULL
        CHECK (channel_type IN ('official_profile', 'company_switchboard', 'work_email', 'professional_social', 'other')),
    channel_value_encrypted bytea,
    channel_value_hash text,
    evidence_grade text NOT NULL CHECK (evidence_grade IN ('A', 'B', 'D', 'X')),
    is_public_professional boolean NOT NULL DEFAULT false,
    source_url text NOT NULL,
    last_verified_at timestamptz,
    approved_for_use boolean NOT NULL DEFAULT false,
    approved_by text,
    approved_at timestamptz,
    opted_out_at timestamptz,
    retention_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX contact_channels_hash_idx ON contact_channels (channel_value_hash);

CREATE TABLE lead_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    generated_at timestamptz NOT NULL DEFAULT now(),
    target_role_title text NOT NULL,
    target_seniority text NOT NULL DEFAULT 'director_plus'
        CHECK (target_seniority = 'director_plus'),
    seniority_evidence jsonb NOT NULL,
    has_upstream_signal boolean NOT NULL,
    earliest_upstream_signal_at timestamptz,
    first_relevant_public_job_at timestamptz,
    lead_time_days integer,
    timing_stage text NOT NULL
        CHECK (timing_stage IN ('pre_ad', 'ad_live', 'marketed_competitive', 'unknown')),
    opportunity_score numeric(5,2) NOT NULL CHECK (opportunity_score BETWEEN 0 AND 100),
    confidence_grade text NOT NULL CHECK (confidence_grade IN ('A', 'B', 'C', 'D')),
    tier text NOT NULL CHECK (tier IN ('bd_now', 'research_watch', 'archive_monitor', 'blocked')),
    score_components jsonb NOT NULL,
    hiring_hypothesis text NOT NULL,
    evidence_signal_ids uuid[] NOT NULL,
    risk_summary text,
    recommended_action text,
    scoring_version text NOT NULL,
    human_status text NOT NULL DEFAULT 'pending'
        CHECK (human_status IN ('pending', 'accepted', 'deferred', 'rejected', 'blocked')),
    human_reason text,
    reviewed_by text,
    reviewed_at timestamptz,
    CHECK (tier <> 'bd_now' OR (has_upstream_signal AND target_seniority = 'director_plus'))
);

CREATE INDEX lead_snapshots_company_time_idx
    ON lead_snapshots (company_id, generated_at DESC);
CREATE INDEX lead_snapshots_tier_time_idx
    ON lead_snapshots (tier, generated_at DESC);

CREATE TABLE bd_outcomes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES companies(id),
    lead_snapshot_id uuid REFERENCES lead_snapshots(id),
    person_id uuid REFERENCES people(id),
    consultant text NOT NULL,
    stage text NOT NULL
        CHECK (stage IN (
            'reviewed',
            'contacted',
            'positive_reply',
            'meeting',
            'qualified_opportunity',
            'mandate',
            'won',
            'lost',
            'no_response'
        )),
    happened_at timestamptz NOT NULL,
    channel_type text,
    reason_code text,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX bd_outcomes_company_time_idx
    ON bd_outcomes (company_id, happened_at DESC);
CREATE INDEX bd_outcomes_stage_time_idx ON bd_outcomes (stage, happened_at DESC);

CREATE TABLE suppressions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid REFERENCES companies(id),
    person_id uuid REFERENCES people(id),
    contact_value_hash text,
    scope text NOT NULL CHECK (scope IN ('company', 'person', 'channel')),
    reason text NOT NULL,
    starts_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (scope = 'company' AND company_id IS NOT NULL)
        OR (scope = 'person' AND person_id IS NOT NULL)
        OR (scope = 'channel' AND contact_value_hash IS NOT NULL)
    )
);

CREATE TABLE model_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_type text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    model_or_rule_version text NOT NULL,
    input_references jsonb NOT NULL,
    output jsonb NOT NULL,
    prompt_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    review_status text NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN ('unreviewed', 'accepted', 'corrected', 'rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    correction jsonb
);

-- Recommended operational rule:
-- A lead is blocked if an active company/person/channel suppression exists.
-- Enforce this in the service layer and test it before any contact is approved.
