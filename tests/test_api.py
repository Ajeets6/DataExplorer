from httpx import ASGITransport, AsyncClient

from dataexplorer.api import create_app
from dataexplorer.audit import InMemoryAuditSink
from dataexplorer.providers import GeneratedText
from dataexplorer.retrieval import InMemoryRetriever
from dataexplorer.service import RagService


class FakeProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def generate(self, *, system: str, prompt: str, policy=None) -> GeneratedText:
        return GeneratedText(text="Grounded answer [S1]", provider="fake", model="fake")

    async def healthcheck(self) -> bool:
        return True


async def test_health_and_end_to_end_query() -> None:
    service = RagService(
        provider=FakeProvider(),
        retriever=InMemoryRetriever(),
        minimum_relevance=0.1,
    )
    app = create_app(service)
    transport = ASGITransport(app=app)
    headers = {
        "X-User-ID": "analyst-1",
        "X-Tenant-ID": "acme",
        "X-Groups": "finance",
    }

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == 200
            ingested = await client.post(
                "/v1/documents",
                headers=headers,
                json={
                    "document_id": "doc-1",
                    "title": "Travel policy",
                    "text": "Approved rail travel is reimbursable.",
                    "allowed_groups": ["finance"],
                },
            )
            queried = await client.post(
                "/v1/query",
                headers=headers,
                json={"question": "What travel is reimbursable?"},
            )

    assert ingested.status_code == 201
    assert queried.status_code == 200
    assert queried.json()["grounded"] is True
    assert queried.json()["citations"][0]["document_id"] == "doc-1"


async def test_identity_headers_are_required() -> None:
    app = create_app(
        RagService(provider=FakeProvider(), retriever=InMemoryRetriever())
    )
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/query", json={"question": "hello"})

    assert response.status_code == 401


async def test_query_emits_a_correlated_audit_event() -> None:
    audit = InMemoryAuditSink()
    app = create_app(
        RagService(provider=FakeProvider(), retriever=InMemoryRetriever()),
        audit_sink=audit,
    )
    transport = ASGITransport(app=app)
    headers = {
        "X-User-ID": "analyst-1",
        "X-Tenant-ID": "acme",
        "X-Correlation-ID": "corr-123",
    }
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/query",
                headers=headers,
                json={"question": "What is the policy?"},
            )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr-123"
    assert audit.events[-1].action == "rag.query"
    assert audit.events[-1].correlation_id == "corr-123"
    assert "question" not in audit.events[-1].metadata
