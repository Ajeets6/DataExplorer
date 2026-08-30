# SEC Enterprise RAG Test Pack

This reproducible corpus combines public SEC filings and Company Facts JSON with
synthetic enterprise policies, access-control boundaries, superseded content,
duplicates, restricted test data, and prompt-injection fixtures.

Generate the safe offline portion:

```powershell
uv run python scripts/build_sec_enterprise_dataset.py --offline
```

To add two 10-K and four 10-Q filings for Microsoft, Walmart, and Delta, declare
a compliant SEC User-Agent containing your organization and operational contact:

```powershell
$env:DATAEXPLORER_SEC_USER_AGENT="Example Organisation admin@example.com"
uv run python scripts/build_sec_enterprise_dataset.py
```

The builder stays below the SEC's published 10 requests/second fair-access
limit. Output is written under `.artifacts/datasets/sec-enterprise` and is not
committed. It contains a `manifest.json`, normalized filing text, raw filing
HTML, structured Company Facts JSON, synthetic text fixtures, checksums, and
golden questions.

With the API running in development mode, ingest it using:

```powershell
uv run python scripts/ingest_sec_enterprise_dataset.py
```

The loader uses each document's tenant, ACL, classification, trust tier, version,
source type, and checksum. Production ingestion requires a real JWT supplied
with `--token`; development headers are never used when a token is present.
