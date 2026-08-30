from fastapi.testclient import TestClient

from dataexplorer.api import create_app
from dataexplorer.models import AccessContext, DocumentIn
from dataexplorer.observability import LlmTraceEvent, PricingCatalog, summarize_traces
from dataexplorer.providers import GeneratedText
from dataexplorer.retrieval import InMemoryRetriever
from dataexplorer.service import RagService


class HealthyProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    async def generate(self, *, system: str, prompt: str, policy=None) -> GeneratedText:
        return GeneratedText(
            text="ok [S1]", provider="test", model="test",
            input_tokens=100, output_tokens=25,
        )

    async def healthcheck(self) -> bool:
        return True


def test_metrics_use_route_templates_and_correlation_ids() -> None:
    service = RagService(provider=HealthyProvider(), retriever=InMemoryRetriever())
    with TestClient(create_app(service)) as client:
        response = client.get("/health/live", headers={"X-Correlation-ID": "trace-123"})
        assert response.headers["X-Correlation-ID"] == "trace-123"
        metrics = client.get("/metrics").text
    assert 'route="/health/live"' in metrics
    assert 'status="200"' in metrics


def test_pricing_and_trace_summary() -> None:
    catalog = PricingCatalog(
        input_per_million_usd={"test:model": 2.0},
        output_per_million_usd={"test:model": 8.0},
    )
    cost = catalog.estimate("test", "model", 1000, 250)
    assert cost == 0.004
    summary = summarize_traces([LlmTraceEvent(
        correlation_id="c", tenant_id="acme", user_id="u", operation="rag.query",
        provider="test", model="model", input_tokens=1000, output_tokens=250,
        total_tokens=1250, estimated_cost_usd=cost, latency_ms=20, grounded=True,
        citation_count=1,
    )])
    assert summary.total_tokens == 1250
    assert summary.estimated_cost_usd == 0.004
    assert summary.grounded_rate == 1


def test_server_owned_context_and_admin_observability() -> None:
    service = RagService(provider=HealthyProvider(), retriever=InMemoryRetriever())
    with TestClient(create_app(service)) as client:
        analyst = {"X-User-ID": "analyst", "X-Tenant-ID": "acme", "X-Groups": "finance"}
        admin = {
            "X-User-ID": "admin", "X-Tenant-ID": "acme",
            "X-Groups": "observability-admins",
        }
        context = client.get("/v1/workspace/me", headers=analyst)
        assert context.json()["user_id"] == "analyst"
        assert context.json()["can_observe"] is False
        assert client.get("/v1/observability/traces", headers=analyst).status_code == 403

        import asyncio
        asyncio.run(service.ingest(DocumentIn(
            document_id="d", title="Policy", text="Approved travel is reimbursable.",
            allowed_groups=frozenset({"finance"}),
        ), AccessContext(user_id="analyst", tenant_id="acme", groups=frozenset({"finance"}))))
        assert client.post("/v1/query", headers=analyst, json={"question": "What travel?"}).status_code == 200
        traces = client.get("/v1/observability/traces", headers=admin).json()
        assert traces[0]["user_id"] == "analyst"
        assert traces[0]["total_tokens"] == 125
        assert "prompt" not in traces[0]


def test_retrieval_refusal_does_not_create_a_false_llm_trace() -> None:
    service = RagService(provider=HealthyProvider(), retriever=InMemoryRetriever())
    with TestClient(create_app(service)) as client:
        analyst = {"X-User-ID": "analyst", "X-Tenant-ID": "acme", "X-Groups": "finance"}
        admin = {
            "X-User-ID": "admin", "X-Tenant-ID": "acme",
            "X-Groups": "observability-admins",
        }
        response = client.post("/v1/query", headers=analyst, json={"question": "Missing evidence"})
        assert response.status_code == 200
        assert response.json()["model_provider"] == "none"
        assert client.get("/v1/observability/traces", headers=admin).json() == []
