"""Ingest a generated SEC enterprise corpus through the governed API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path(".artifacts/datasets/sec-enterprise"))
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    indexed = deduplicated = failed = 0
    with httpx.Client(base_url=args.api_url, timeout=180) as client:
        try:
            readiness = client.get("/health/live")
        except httpx.HTTPError as error:
            print(f"Dataset ingestion stopped: API connection failed: {error}")
            raise SystemExit(2) from error
        if not readiness.is_success:
            print(
                "Dataset ingestion stopped: API is not live.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        for document in manifest["documents"]:
            headers = {"Authorization": f"Bearer {args.token}"} if args.token else {
                "X-User-ID": "dataset-loader",
                "X-Tenant-ID": document["tenant_id"],
                "X-Groups": ",".join(sorted(set(document["allowed_groups"]) | {"data-approvers"})),
            }
            payload = {
                "document_id": document["document_id"],
                "title": document["title"],
                "text": (args.dataset / document["path"]).read_text(encoding="utf-8"),
                "allowed_groups": document["allowed_groups"],
                "classification": document["classification"],
                "trust_tier": document["trust_tier"],
                "version": document["version"],
                "metadata": {
                    "dataset": manifest["dataset"],
                    "source_type": document["source_type"],
                    "source_url": document.get("source_url"),
                    "expected_behavior": document["expected_behavior"],
                    "sha256": document["sha256"],
                },
            }
            response = client.post("/v1/documents", headers=headers, json=payload)
            if response.is_success:
                if response.json().get("deduplicated"):
                    deduplicated += 1
                else:
                    indexed += 1
            else:
                failed += 1
                print(f"FAILED {document['document_id']}: {response.status_code} {response.text}")
    print(json.dumps({"indexed": indexed, "deduplicated": deduplicated, "failed": failed}, indent=2))


if __name__ == "__main__":
    main()
