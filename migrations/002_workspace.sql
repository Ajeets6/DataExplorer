CREATE TABLE IF NOT EXISTS workspace_records (
    tenant_id text NOT NULL,
    kind text NOT NULL,
    record_id text NOT NULL,
    owner_id text NOT NULL,
    allowed_groups text[] NOT NULL DEFAULT '{}',
    private boolean NOT NULL DEFAULT false,
    title text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, kind, record_id)
);
CREATE INDEX IF NOT EXISTS workspace_records_library_idx
    ON workspace_records (tenant_id, kind, created_at DESC, record_id DESC);
