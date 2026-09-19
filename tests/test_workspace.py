from datetime import UTC, datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from dataexplorer.api import create_app
from dataexplorer.artifacts import ArtifactService, DocxRenderer
from dataexplorer.models import AccessContext, ArtifactSpec, DocumentIn
from dataexplorer.observability import LlmTraceEvent, summarize_traces
from dataexplorer.providers import GeneratedText
from dataexplorer.retrieval import InMemoryRetriever
from dataexplorer.service import RagService
from dataexplorer.telemetry import dashboard
from dataexplorer.workspace import WorkspaceRecord, WorkspaceStore


class Provider:
    async def embed(self, texts):
        return [[1.0] for _ in texts]

    async def generate(self, *, system, prompt, policy=None):
        return GeneratedText(text="Rail travel is approved [S1]", provider="test", model="test", input_tokens=20, output_tokens=5)

    async def healthcheck(self):
        return True


def headers(user="analyst", tenant="acme", groups="finance"):
    return {"X-User-ID": user, "X-Tenant-ID": tenant, "X-Groups": groups}


def spec():
    return {"kind": "docx", "filename": "travel-report.docx", "title": "Travel report",
            "audience": "Finance", "purpose": "Policy review", "classification": "internal",
            "sections": [{"title": "Summary", "summary": "Rail travel is approved", "source_ids": ["policy"]}],
            "sources": [{"source_id": "policy", "label": "Travel policy", "locator": "warehouse://policy"}]}


def app_for(tmp_path=None, provider=None):
    return create_app(RagService(provider=provider or Provider(), retriever=InMemoryRetriever()),
                      artifact_service=ArtifactService(output_root=tmp_path, renderers={"docx": DocxRenderer()}) if tmp_path else None)


def test_complete_report_review_and_authorized_download(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        author = headers()
        reviewer = headers("reviewer", groups="finance,content-approvers")
        other_tenant = headers("reviewer", tenant="other", groups="content-approvers")
        created = client.post("/v1/artifacts", headers=author, json=spec())
        assert created.status_code == 201
        report_id = created.json()["artifact_id"]
        path = f"/v1/artifacts/{report_id}"
        assert client.get("/v1/artifacts", headers=author).json()["items"][0]["artifact_id"] == report_id
        assert client.get("/v1/artifacts", headers=other_tenant).json()["items"] == []
        assert client.get(path, headers=other_tenant).status_code == 404
        assert client.get(path, headers=headers("unrelated")).status_code == 404
        assert client.get(path + "/download", headers=author).status_code == 409
        own_approver = headers(groups="content-approvers")
        assert client.post(path + "/decision", headers=own_approver,
                           json={"approved": True, "reason": "Reviewed evidence"}).status_code == 403
        queue = client.get("/v1/artifacts?review_queue=true", headers=reviewer).json()["items"]
        assert [r["artifact_id"] for r in queue] == [report_id]
        assert client.get("/v1/artifacts?review_queue=true", headers=own_approver).json()["items"] == []
        assert client.post(path + "/decision", headers=reviewer,
                           json={"approved": True, "reason": "Reviewed evidence"}).status_code == 200
        assert client.get(path, headers=author).json()["approved_by"] == "reviewer"
        assert client.post(path + "/render", headers=author).status_code == 200
        downloaded = client.get(path + "/download", headers=author)
        assert downloaded.status_code == 200
        assert "wordprocessingml.document" in downloaded.headers["content-type"]
        with ZipFile(BytesIO(downloaded.content)) as docx:
            assert b"Rail travel is approved" in docx.read("word/document.xml")
        assert client.get(path + "/download", headers=other_tenant).status_code == 404
        totals = client.get("/v1/workspace/overview", headers=author).json()
        assert totals["my_reports"] == 1
        assert totals["awaiting_review"] == 0


def test_report_filters_are_applied_before_pagination(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        reviewer = headers("reviewer", groups="content-approvers")
        for i in range(3):
            client.post("/v1/artifacts", headers=headers(), json={**spec(), "title": f"Team report {i}"})
        client.post("/v1/artifacts", headers=reviewer, json={**spec(), "title": "My report"})
        response = client.get("/v1/artifacts?mine=true&limit=1", headers=reviewer).json()
        assert response["items"][0]["spec"]["title"] == "My report"
        assert response["has_more"] is False
        queue = client.get("/v1/artifacts?review_queue=true&limit=2", headers=reviewer).json()
        assert len(queue["items"]) == 2
        assert queue["has_more"] is True


def test_saved_answers_and_documents_are_authorized_and_reloadable():
    with TestClient(app_for()) as client:
        response = client.post("/v1/documents", headers=headers(), json={
            "document_id": "policy", "title": "Travel policy", "text": "Rail travel is approved.", "allowed_groups": ["finance"]})
        assert response.status_code == 201
        assert client.get("/v1/library/document", headers=headers(groups="hr")).json()["items"] == []
        assert client.get("/v1/library/document/policy", headers=headers(groups="hr")).status_code == 404
        assert client.post("/v1/query", headers=headers(), json={"question": "What rail travel is approved?"}).status_code == 200
        saved = client.get("/v1/library/answer", headers=headers()).json()["items"]
        assert len(saved) == 1
        path = "/v1/library/answer/" + saved[0]["record_id"]
        assert client.get(path, headers=headers()).json()["payload"]["response"]["citations"][0]["document_id"] == "policy"
        assert client.get(path, headers=headers("another")).status_code == 404
        assert client.get(path, headers=headers(groups="hr")).status_code == 404
        assert client.get("/v1/library/answer", headers=headers(tenant="other")).json()["items"] == []


def test_dashboard_counts_attempts_without_double_counting_tokens():
    now = datetime.now(UTC)
    base = dict(correlation_id="request", tenant_id="acme", user_id="analyst", provider="test", model="test", occurred_at=now)
    events = [LlmTraceEvent(**base, operation="rag.query.attempt", total_tokens=10, estimated_cost_usd=.01),
              LlmTraceEvent(**base, operation="rag.query.attempt", total_tokens=20, estimated_cost_usd=.02),
              LlmTraceEvent(**base, operation="rag.query", total_tokens=30, estimated_cost_usd=.03,
                            grounded=True, latency_ms=900)]
    summary = dashboard(events)
    assert summary["requests"] == 1
    assert summary["attempts"] == 2
    assert summary["tokens"] == 30
    assert summary["estimated_cost_usd"] == pytest.approx(.03)
    assert summary["grounded_samples"] == 1
    assert summary["p95_latency_ms"] == 900
    assert dashboard([])["grounded_rate"] is None
    assert dashboard([])["estimated_cost_usd"] is None
    assert summarize_traces([]).grounded_rate is None


def test_date_scoped_dashboard_and_audit_access():
    with TestClient(app_for()) as client:
        admin = headers("admin", groups="observability-admins")
        now = datetime.now(UTC)
        params = {"start": (now-timedelta(days=1)).isoformat(), "end": (now+timedelta(days=1)).isoformat()}
        assert client.get("/v1/observability/dashboard", headers=headers(), params=params).status_code == 403
        response = client.get("/v1/observability/dashboard", headers=admin, params=params)
        assert response.status_code == 200
        assert response.json()["grounded_rate"] is None
        assert response.json()["requests"] == 0
        assert client.get("/v1/observability/events", headers=admin, params=params).json()["items"] == []
        assert client.get("/v1/observability/dashboard", headers=admin,
                          params={"start": now.isoformat(), "end": (now-timedelta(days=1)).isoformat()}).status_code == 422


def test_failed_generation_is_visible_and_sanitized():
    class BrokenProvider(Provider):
        async def generate(self, **kwargs):
            raise RuntimeError("sensitive provider response")

    with TestClient(app_for(provider=BrokenProvider())) as client:
        client.post("/v1/documents", headers=headers(), json={
            "document_id": "policy", "title": "Travel policy", "text": "Rail travel is approved.", "allowed_groups": ["finance"]})
        response = client.post("/v1/query", headers=headers(), json={"question": "What rail travel is approved?"})
        assert response.status_code == 503
        assert "sensitive" not in response.text
        admin = headers("admin", groups="observability-admins")
        summary = client.get("/v1/observability/dashboard", headers=admin).json()
        assert summary["failed_requests"] == 1
        assert summary["unpriced_attempts"] == 1
        assert "sensitive" not in str(summary)


def test_report_revision_preserves_previous_decision(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        author = headers()
        reviewer = headers("reviewer", groups="content-approvers")
        first = client.post("/v1/artifacts", headers=author, json=spec()).json()
        report_id = first["artifact_id"]
        assert client.post("/v1/artifacts", headers=author,
                           json={**spec(), "revision_of": report_id}).status_code == 403
        assert client.post(f"/v1/artifacts/{report_id}/decision", headers=reviewer,
                           json={"approved": False, "reason": "Clarify the policy scope"}).status_code == 200
        second = client.post("/v1/artifacts", headers=author,
                             json={**spec(), "revision_of": report_id, "report_version": 999}).json()
        assert second["spec"]["report_version"] == 2
        assert second["status"] == "pending"
        assert second["approved_by"] is None
        assert second["artifact_id"] != report_id
        assert client.get(f"/v1/artifacts/{report_id}", headers=author).json()["status"] == "rejected"
        assert client.post("/v1/artifacts", headers=reviewer,
                           json={**spec(), "revision_of": report_id}).status_code == 403


def test_unpriced_chart_values_remain_unknown():
    event = LlmTraceEvent(correlation_id="unpriced", tenant_id="acme", user_id="u", provider="test",
                         model="test", operation="rag.query.attempt", estimated_cost_usd=None)
    summary = dashboard([event])
    assert summary["estimated_cost_usd"] is None
    assert summary["series"][0]["estimated_cost_usd"] is None


def test_unrelated_user_cannot_render_an_approved_report(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        draft = client.post("/v1/artifacts", headers=headers(), json=spec()).json()
        path = "/v1/artifacts/" + draft["artifact_id"]
        client.post(path + "/decision", headers=headers("reviewer", groups="content-approvers"),
                    json={"approved": True, "reason": "Reviewed evidence"})
        assert client.post(path + "/render", headers=headers("unrelated")).status_code == 403


def test_report_evidence_access_is_required_throughout_workflow(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        author = headers()
        reviewer = headers("reviewer", groups="content-approvers")
        allowed_reviewer = headers("reviewer", groups="finance,content-approvers")
        client.post("/v1/documents", headers=author, json={
            "document_id": "policy", "title": "Finance policy", "text": "Rail travel is approved.",
            "allowed_groups": ["finance"]})
        payload = spec()
        payload["sources"][0]["locator"] = "document://policy"
        assert client.post("/v1/artifacts", headers=reviewer, json=payload).status_code == 403
        draft = client.post("/v1/artifacts", headers=author, json=payload).json()
        path = "/v1/artifacts/" + draft["artifact_id"]
        assert client.get(path, headers=reviewer).status_code == 404
        assert client.get("/v1/artifacts", headers=reviewer).json()["items"] == []
        assert client.get("/v1/workspace/overview", headers=reviewer).json()["awaiting_review"] == 0
        decision = {"approved": True, "reason": "Reviewed evidence"}
        assert client.post(path + "/decision", headers=reviewer, json=decision).status_code == 403
        assert client.post(path + "/decision", headers=allowed_reviewer, json=decision).status_code == 200
        assert client.post(path + "/render", headers=reviewer).status_code == 403
        assert client.post(path + "/render", headers=author).status_code == 200
        assert client.get(path + "/download", headers=reviewer).status_code == 404
        assert client.get(path + "/download", headers=allowed_reviewer).status_code == 200
        # Revoking the author's source group also revokes the saved report.
        assert client.get(path + "/download", headers=headers(groups="hr")).status_code == 404


def test_reused_correlation_header_does_not_merge_requests():
    app = app_for()
    with TestClient(app) as client:
        identity = {**headers(), "X-Correlation-ID": "shared-client-reference"}
        client.post("/v1/documents", headers=identity, json={
            "document_id": "policy", "title": "Travel policy", "text": "Rail travel is approved.",
            "allowed_groups": ["finance"]})
        for _ in range(2):
            assert client.post("/v1/query", headers=identity, json={"question": "What rail travel is approved?"}).status_code == 200
        events = app.state.trace_store.events
        assert len({event.request_id for event in events}) == 2
        assert all(event.request_id for event in events)
        assert {event.correlation_id for event in events} == {"shared-client-reference"}
        result = dashboard(events)
        assert result["requests"] == 2
        assert result["tokens"] == 50
