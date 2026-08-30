CREATE TABLE IF NOT EXISTS audit_events (
    event_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    correlation_id text NOT NULL,
    action text NOT NULL,
    decision text NOT NULL,
    user_id text NOT NULL,
    tenant_id text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_events_tenant_time_idx
    ON audit_events (tenant_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS llm_traces (
    trace_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    correlation_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    operation text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    status text NOT NULL,
    input_tokens integer NOT NULL CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL CHECK (output_tokens >= 0),
    total_tokens integer NOT NULL CHECK (total_tokens >= 0),
    estimated_cost_usd numeric(20, 8),
    latency_ms double precision NOT NULL CHECK (latency_ms >= 0),
    grounded boolean,
    citation_count integer NOT NULL CHECK (citation_count >= 0),
    reflection_attempts integer NOT NULL CHECK (reflection_attempts >= 0)
);
CREATE INDEX IF NOT EXISTS llm_traces_tenant_time_idx
    ON llm_traces (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS llm_traces_correlation_idx
    ON llm_traces (correlation_id);

CREATE TABLE IF NOT EXISTS artifact_drafts (
    artifact_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artifact_drafts_tenant_status_idx
    ON artifact_drafts (tenant_id, status);

CREATE TABLE IF NOT EXISTS sql_proposals (
    proposal_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sql_proposals_tenant_status_idx
    ON sql_proposals (tenant_id, status);
