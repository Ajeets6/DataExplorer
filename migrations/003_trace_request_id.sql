ALTER TABLE llm_traces ADD COLUMN IF NOT EXISTS request_id text;
CREATE INDEX IF NOT EXISTS llm_traces_request_idx ON llm_traces (tenant_id, request_id);
