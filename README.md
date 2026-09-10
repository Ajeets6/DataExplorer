# Data Explorer

An enterprise RAG platform for cited answers, document and presentation
publishing, and approved Text2SQL. Supports Ollama, OpenAI, and Anthropic.

## Quick start

Prerequisites: Python 3.11+ and a running Ollama instance.

```powershell
uv sync
Copy-Item .env.example .env
ollama pull llama3.1:8b
ollama pull embeddinggemma
uv run dataexplorer
```

The API is available at `http://127.0.0.1:8000`; OpenAPI is at `/docs`.

Start the enterprise workspace in another terminal:

```powershell
uv run dataexplorer-ui
```

The workspace is at `http://127.0.0.1:8501`. Run `uv run dataexplorer-admin`
for the observability console at `http://127.0.0.1:8502` (requires
`observability-admins` or `platform-admins`). Use `docker compose up` to start
both UIs, the API, and supporting services.

The employee workspace opens on Overview with live counts, recent work, and
review actions. Knowledge, saved answers, analyses, reports, and approvals have
dedicated pages. Reports can be inspected, independently approved or rejected,
rendered, and downloaded without copying identifiers. Text/Markdown uploads are
limited to 1 MB; unsupported binary formats are not advertised as available.

The admin console provides UTC date filters, period comparisons, generation
trends, request details, sanitized CSV exports, cost coverage, audit search, and
quality checks. Missing metrics display as unavailable or not applicable rather
than a measured zero. Token/cost totals separate attempts from request summaries.

Production must apply both `migrations/001_governance.sql` and
`migrations/002_workspace.sql`. The included migration worker applies all SQL
files in order. With PostgreSQL enabled, workspace records persist across API
restarts; development memory mode persists only for the running API process.

For enterprise UI sign-in, configure an OIDC identity gateway to forward a signed
`Authorization: Bearer ...` token over the Streamlit connection. The FastAPI
server independently validates that token. Set `DATAEXPLORER_AUTH_MODE=jwt` on
both UI services and configure `DATAEXPLORER_LOGIN_URL` to the gateway's HTTPS
sign-in URL. Direct ingress and token issuer/audience alignment must be validated
in the deployment; the app does not invent identity from unverified proxy claims.

Approved analysis schemas can be supplied as `DATAEXPLORER_SQL_SCHEMAS`, a JSON
object keyed by schema name with `tables`, `allowed_groups`, and `approver_group`.
Only configured sources allowed by the caller's groups appear in Analyses.
Do not point the analysis executor at ungoverned production tables.

Header-based identity is development-only; staging and production require
validated JWT claims.

Ingest a development document:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/documents `
  -Headers @{"X-User-ID"="analyst-1"; "X-Tenant-ID"="acme"; "X-Groups"="finance"} `
  -ContentType application/json `
  -Body '{"document_id":"policy-1","title":"Travel policy","text":"Employees may claim approved rail travel.","allowed_groups":["finance"]}'
```

Query it:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/query `
  -Headers @{"X-User-ID"="analyst-1"; "X-Tenant-ID"="acme"; "X-Groups"="finance"} `
  -ContentType application/json `
  -Body '{"question":"What travel can employees claim?"}'
```

Run tests with `uv run pytest`.

## Evaluation dataset

The [SEC Enterprise RAG Test Pack](datasets/sec_enterprise/README.md) combines
company filings with synthetic policy and security fixtures. With Ollama running:

```powershell
uv run python scripts/build_sec_enterprise_dataset.py --offline
uv run python scripts/ingest_sec_enterprise_dataset.py
```

Live SEC downloads require `DATAEXPLORER_SEC_USER_AGENT` with your organization
and operational contact.

## Models and publishing

Select a model route with `DATAEXPLORER_MODEL_PROVIDER=ollama|openai|anthropic|router`.
Managed providers are limited to public/internal content; confidential and
restricted content fails closed unless an eligible private/local route exists.

Publishing requires creating `/v1/artifacts`, obtaining independent approval,
then rendering. Factual sections require source IDs. DOCX is built in; PPTX
requires configured Node and artifact-tool runtime paths.

## Security

- Tenant and group authorization is applied before evidence reaches a model.
- Retrieval checks relevance, trust, and expiry.
- Guardrails block high-confidence injection patterns, check citations, and
  redact common PII and secrets.
- JWT authentication, request limits, and token budgets protect API access.
- SQL requires independent approval and an allowlisted, bounded `SELECT`.
  Execution uses read-only transactions, tenant context, and resource limits.

## Production deployment

Development uses in-memory defaults. Production requires PostgreSQL, Redis,
Qdrant, and JWT identity with issuer, audience, and JWKS settings. Apply
`migrations/001_governance.sql` through the included worker job.

`infra/terraform` provisions Cloud Run and supporting Google Cloud services.
Before deploying, create the remote-state bucket, copy `terraform.tfvars.example`,
and add versions for the database, OpenAI, and Qdrant secrets. Cloud Build deploys
commit-tagged images; GitHub CI validates tests, containers, and Terraform.

Logs and `/metrics` track requests and LLM usage without storing prompt,
response, evidence, or document bodies. Configure cost estimates with
`DATAEXPLORER_LLM_*_COST_PER_MILLION_USD` JSON maps; missing rates remain unpriced.
