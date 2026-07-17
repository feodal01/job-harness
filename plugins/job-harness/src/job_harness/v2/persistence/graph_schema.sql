CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS search_intents (
    intent_id TEXT PRIMARY KEY,
    schema_id TEXT NOT NULL,
    intent_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS search_executions (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL REFERENCES search_intents(intent_id),
    append_sequence INTEGER NOT NULL CHECK (append_sequence >= 0),
    execution_kind TEXT NOT NULL DEFAULT 'search' CHECK (
        execution_kind IN ('search', 'enrichment', 'discovered_search')
    ),
    parent_execution_id TEXT REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'stopping', 'assembling', 'artifacts_pending', 'completed', 'failed')
    ),
    policy_version TEXT NOT NULL,
    runtime_config_version TEXT NOT NULL,
    active_runtime_budget_ms INTEGER NOT NULL CHECK (active_runtime_budget_ms > 0),
    active_runtime_ms INTEGER NOT NULL DEFAULT 0 CHECK (active_runtime_ms >= 0),
    active_session_started_at REAL,
    active_heartbeat_at REAL,
    discovery_plan_budget INTEGER NOT NULL CHECK (discovery_plan_budget >= 0),
    discovery_plans_created INTEGER NOT NULL DEFAULT 0 CHECK (discovery_plans_created >= 0),
    speculative_admission_budget INTEGER NOT NULL DEFAULT 0 CHECK (speculative_admission_budget >= 0),
    speculative_admissions_created INTEGER NOT NULL DEFAULT 0 CHECK (speculative_admissions_created >= 0),
    scheduler_cursor INTEGER NOT NULL DEFAULT 0 CHECK (scheduler_cursor BETWEEN 0 AND 2),
    coordinator_owner TEXT,
    coordinator_token TEXT,
    coordinator_lease_until REAL,
    completion_reason TEXT,
    created_at REAL NOT NULL,
    UNIQUE (run_id, append_sequence, execution_kind)
);

CREATE TABLE IF NOT EXISTS execution_artifacts (
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    artifact_name TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('expected', 'verified')),
    prepared_at REAL NOT NULL,
    verified_at REAL,
    PRIMARY KEY (execution_id, artifact_name),
    UNIQUE (execution_id, artifact_path)
);

CREATE TABLE IF NOT EXISTS source_plans (
    source_plan_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    origin_event_id TEXT REFERENCES domain_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    origin_company_id TEXT REFERENCES companies(company_id) DEFERRABLE INITIALLY DEFERRED,
    origin_endpoint_id TEXT REFERENCES discovered_endpoints(endpoint_id) DEFERRABLE INITIALLY DEFERRED,
    source_id TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    query_mode TEXT NOT NULL CHECK (query_mode IN ('per_query', 'query_group', 'downstream_only')),
    queries_json TEXT NOT NULL,
    unit_budget INTEGER NOT NULL CHECK (unit_budget >= 1),
    item_budget INTEGER NOT NULL CHECK (item_budget >= 1),
    invocation_budget INTEGER NOT NULL CHECK (invocation_budget >= 1),
    units_used INTEGER NOT NULL DEFAULT 0 CHECK (units_used >= 0),
    items_used INTEGER NOT NULL DEFAULT 0 CHECK (items_used >= 0),
    invocations_used INTEGER NOT NULL DEFAULT 0 CHECK (invocations_used >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('planned', 'running', 'succeeded', 'no_results', 'partial', 'limit_reached', 'failed', 'cancelled')
    ),
    terminal_reason TEXT,
    UNIQUE (execution_id, source_id, parser_id, queries_json)
);

CREATE TABLE IF NOT EXISTS criterion_requirements (
    requirement_id TEXT PRIMARY KEY,
    source_plan_id TEXT NOT NULL REFERENCES source_plans(source_plan_id) ON DELETE CASCADE,
    criterion TEXT NOT NULL,
    required_fact_path TEXT NOT NULL,
    comparison_json TEXT NOT NULL,
    skip_when_final_keep INTEGER NOT NULL CHECK (skip_when_final_keep IN (0, 1)),
    unsupported_reason TEXT
);

CREATE TABLE IF NOT EXISTS fact_providers (
    provider_id TEXT PRIMARY KEY,
    requirement_id TEXT NOT NULL REFERENCES criterion_requirements(requirement_id) ON DELETE CASCADE,
    provider_stage TEXT NOT NULL CHECK (
        provider_stage IN ('native_request', 'listing_output', 'detail_output', 'profile_output', 'site_output', 'derived_fact', 'unavailable')
    ),
    parser_id TEXT,
    parser_version TEXT,
    deriver_id TEXT,
    deriver_version TEXT,
    fact_path TEXT NOT NULL,
    depends_on_fact_paths_json TEXT NOT NULL,
    required_for_final INTEGER NOT NULL CHECK (required_for_final IN (0, 1)),
    cost_class TEXT NOT NULL,
    ordering INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS parser_invocations (
    invocation_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    source_plan_id TEXT REFERENCES source_plans(source_plan_id) ON DELETE CASCADE,
    parent_invocation_id TEXT REFERENCES parser_invocations(invocation_id),
    cause_event_id TEXT REFERENCES domain_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    task_key TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    parser_type TEXT NOT NULL CHECK (parser_type IN ('search_listing', 'vacancy_detail', 'company_profile', 'company_site')),
    input_schema_id TEXT NOT NULL,
    input_json TEXT NOT NULL,
    task_class TEXT NOT NULL CHECK (task_class IN ('listing', 'detail', 'profile', 'site')),
    resource_key TEXT,
    resource_key_resolved INTEGER NOT NULL DEFAULT 0 CHECK (resource_key_resolved IN (0, 1)),
    reserved_collection_units INTEGER CHECK (reserved_collection_units >= 1),
    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'waiting', 'succeeded', 'failed', 'cancelled')),
    available_at REAL NOT NULL,
    waiting_reason TEXT CHECK (waiting_reason IN ('retry_backoff', 'resource_pacing')),
    lease_owner TEXT,
    lease_token TEXT,
    lease_until REAL,
    result_kind TEXT,
    outcome TEXT,
    created_at REAL NOT NULL,
    finished_at REAL,
    UNIQUE (execution_id, task_key)
);

CREATE INDEX IF NOT EXISTS parser_invocations_ready_idx
    ON parser_invocations (execution_id, status, available_at, task_class, created_at);

CREATE INDEX IF NOT EXISTS parser_invocations_plan_lease_idx
    ON parser_invocations (source_plan_id, status, lease_until);

CREATE TABLE IF NOT EXISTS parser_attempts (
    parser_attempt_id TEXT PRIMARY KEY,
    invocation_id TEXT NOT NULL REFERENCES parser_invocations(invocation_id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    started_at REAL NOT NULL,
    finished_at REAL,
    outcome TEXT,
    failure_kind TEXT,
    network_action_count INTEGER NOT NULL DEFAULT 0,
    network_elapsed_ms INTEGER NOT NULL DEFAULT 0,
    last_status_code INTEGER,
    last_error_class TEXT,
    retry_decision TEXT CHECK (retry_decision IN ('scheduled', 'exhausted', 'terminal')),
    retry_delay_ms INTEGER NOT NULL DEFAULT 0 CHECK (retry_delay_ms >= 0),
    UNIQUE (invocation_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    producer_invocation_id TEXT REFERENCES parser_invocations(invocation_id),
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    payload_json TEXT NOT NULL,
    occurred_at REAL NOT NULL,
    processing_offset INTEGER NOT NULL DEFAULT 0 CHECK (processing_offset >= 0),
    processed_at REAL,
    UNIQUE (execution_id, event_key)
);

CREATE INDEX IF NOT EXISTS domain_events_unprocessed_idx
    ON domain_events (execution_id, processed_at, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS vacancy_resources (
    vacancy_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    target_provider_id TEXT NOT NULL,
    source_listing_id TEXT,
    canonical_url TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    identity_schema_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (run_id, identity_key),
    UNIQUE (run_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS vacancy_url_aliases (
    vacancy_url_alias_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    vacancy_id TEXT NOT NULL REFERENCES vacancy_resources(vacancy_id) ON DELETE CASCADE,
    normalized_url TEXT NOT NULL,
    normalizer_version INTEGER NOT NULL,
    UNIQUE (run_id, normalized_url)
);

CREATE TABLE IF NOT EXISTS vacancy_provider_aliases (
    vacancy_provider_alias_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    vacancy_id TEXT NOT NULL REFERENCES vacancy_resources(vacancy_id) ON DELETE CASCADE,
    target_provider_id TEXT NOT NULL,
    source_listing_id TEXT NOT NULL,
    claim_value TEXT NOT NULL,
    UNIQUE (run_id, claim_value)
);

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    display_name TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancy_listings (
    listing_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    vacancy_id TEXT NOT NULL REFERENCES vacancy_resources(vacancy_id),
    company_id TEXT REFERENCES companies(company_id),
    source_id TEXT NOT NULL,
    source_listing_id TEXT,
    identity_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (run_id, source_id, identity_key)
);

CREATE TABLE IF NOT EXISTS listing_observations (
    listing_observation_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id),
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    source_plan_id TEXT NOT NULL REFERENCES source_plans(source_plan_id),
    invocation_id TEXT NOT NULL REFERENCES parser_invocations(invocation_id),
    output_schema_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at REAL NOT NULL,
    UNIQUE (invocation_id, item_key)
);

CREATE INDEX IF NOT EXISTS listing_observations_listing_idx
    ON listing_observations (execution_id, listing_id, observed_at);

CREATE TABLE IF NOT EXISTS vacancy_detail_observations (
    detail_observation_id TEXT PRIMARY KEY,
    vacancy_id TEXT NOT NULL REFERENCES vacancy_resources(vacancy_id),
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    invocation_id TEXT NOT NULL UNIQUE REFERENCES parser_invocations(invocation_id),
    output_schema_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS company_profile_observations (
    profile_observation_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    invocation_id TEXT NOT NULL UNIQUE REFERENCES parser_invocations(invocation_id),
    output_schema_id TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS company_site_observations (
    site_observation_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    invocation_id TEXT NOT NULL UNIQUE REFERENCES parser_invocations(invocation_id),
    output_schema_id TEXT NOT NULL,
    canonical_site_url TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS company_identity_claims (
    company_claim_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    claim_type TEXT NOT NULL CHECK (claim_type IN ('provider_id', 'profile_url', 'verified_domain')),
    claim_value TEXT NOT NULL,
    listing_observation_id TEXT REFERENCES listing_observations(listing_observation_id),
    detail_observation_id TEXT REFERENCES vacancy_detail_observations(detail_observation_id),
    profile_observation_id TEXT REFERENCES company_profile_observations(profile_observation_id),
    site_observation_id TEXT REFERENCES company_site_observations(site_observation_id),
    status TEXT NOT NULL CHECK (status IN ('active', 'disputed', 'superseded')),
    UNIQUE (run_id, claim_type, claim_value),
    CHECK (
        (listing_observation_id IS NOT NULL) +
        (detail_observation_id IS NOT NULL) +
        (profile_observation_id IS NOT NULL) +
        (site_observation_id IS NOT NULL) = 1
    )
);

CREATE TABLE IF NOT EXISTS company_merges (
    company_merge_id TEXT PRIMARY KEY,
    from_company_id TEXT NOT NULL REFERENCES companies(company_id),
    into_company_id TEXT NOT NULL REFERENCES companies(company_id),
    reason TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    profile_observation_id TEXT REFERENCES company_profile_observations(profile_observation_id),
    site_observation_id TEXT REFERENCES company_site_observations(site_observation_id),
    origin_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    provider_hint TEXT,
    confidence TEXT NOT NULL,
    discovery_method TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    resolved_parser_id TEXT,
    resolved_parser_version TEXT,
    CHECK ((profile_observation_id IS NOT NULL) + (site_observation_id IS NOT NULL) = 1)
);

CREATE TABLE IF NOT EXISTS listing_enrichment_requests (
    enrichment_request_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    source_execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id) ON DELETE CASCADE,
    invocation_id TEXT REFERENCES parser_invocations(invocation_id),
    provider_id TEXT NOT NULL REFERENCES fact_providers(provider_id),
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('waiting', 'satisfied', 'terminal')),
    resolution_outcome TEXT,
    terminal_reason TEXT,
    UNIQUE (execution_id, listing_id, provider_id)
);

CREATE INDEX IF NOT EXISTS listing_enrichment_invocation_idx
    ON listing_enrichment_requests (execution_id, invocation_id, status, listing_id);

CREATE TABLE IF NOT EXISTS enrichment_admissions (
    child_execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    parent_execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id) ON DELETE CASCADE,
    admission_kind TEXT NOT NULL CHECK (admission_kind IN ('speculative', 'final')),
    created_at REAL NOT NULL,
    PRIMARY KEY (child_execution_id, listing_id)
);

CREATE TABLE IF NOT EXISTS fact_derivations (
    fact_derivation_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id),
    deriver_id TEXT NOT NULL,
    deriver_version TEXT NOT NULL,
    input_evidence_refs_json TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    output_schema_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (execution_id, listing_id, deriver_id, deriver_version, input_fingerprint)
);

CREATE TABLE IF NOT EXISTS fact_sets (
    fact_set_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id),
    evidence_refs_json TEXT NOT NULL,
    materialized_facts_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (execution_id, listing_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS fact_sets_latest_idx
    ON fact_sets (execution_id, listing_id, created_at DESC);

CREATE TABLE IF NOT EXISTS selection_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id),
    fact_set_id TEXT NOT NULL REFERENCES fact_sets(fact_set_id),
    stage TEXT NOT NULL CHECK (stage IN ('preliminary', 'final')),
    outcome TEXT NOT NULL CHECK (outcome IN ('reject', 'enrich', 'keep')),
    reason_codes_json TEXT NOT NULL,
    UNIQUE (execution_id, listing_id, stage, fact_set_id)
);

CREATE TABLE IF NOT EXISTS vacancy_duplicate_groups (
    duplicate_group_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    confidence TEXT NOT NULL CHECK (confidence IN ('exact', 'probable')),
    evidence_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancy_duplicate_members (
    duplicate_group_id TEXT NOT NULL REFERENCES vacancy_duplicate_groups(duplicate_group_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id),
    member_role TEXT NOT NULL CHECK (member_role IN ('representative', 'variant')),
    PRIMARY KEY (duplicate_group_id, listing_id)
);

CREATE TABLE IF NOT EXISTS final_vacancies (
    final_vacancy_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES search_executions(execution_id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL REFERENCES vacancy_listings(listing_id),
    evaluation_id TEXT NOT NULL REFERENCES selection_evaluations(evaluation_id),
    duplicate_group_id TEXT REFERENCES vacancy_duplicate_groups(duplicate_group_id),
    snapshot_version INTEGER NOT NULL,
    score REAL NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (execution_id, listing_id, snapshot_version)
);

CREATE TABLE IF NOT EXISTS artifact_index (
    artifact_id TEXT PRIMARY KEY,
    parser_attempt_id TEXT NOT NULL REFERENCES parser_attempts(parser_attempt_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    checksum TEXT NOT NULL,
    capture_reason TEXT NOT NULL CHECK (capture_reason IN ('failure', 'debug', 'fixture')),
    expires_at REAL
);
