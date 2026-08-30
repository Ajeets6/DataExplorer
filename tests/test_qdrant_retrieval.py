from qdrant_client import AsyncQdrantClient

from dataexplorer.models import AccessContext, StoredChunk
from dataexplorer.qdrant_retrieval import QdrantHybridRetriever


def chunk(chunk_id: str, groups: frozenset[str]) -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        tenant_id="acme",
        title="Operating policy",
        text="Approved rail travel reimbursement policy",
        position=0,
        allowed_groups=groups,
        classification="internal",
        trust_tier="authoritative",
        version="1",
        content_sha256="a" * 64,
    )


async def test_qdrant_hybrid_retrieval_applies_tenant_and_group_filters() -> None:
    client = AsyncQdrantClient(location=":memory:")
    retriever = QdrantHybridRetriever(client=client, collection_name="chunks")
    await retriever.index(chunk("public-to-tenant", frozenset()), [1.0, 0.0])
    await retriever.index(chunk("finance-only", frozenset({"finance"})), [1.0, 0.0])

    general = await retriever.search(
        "rail reimbursement",
        [1.0, 0.0],
        AccessContext(user_id="u1", tenant_id="acme"),
        limit=5,
        minimum_relevance=0,
    )
    finance = await retriever.search(
        "rail reimbursement",
        [1.0, 0.0],
        AccessContext(user_id="u2", tenant_id="acme", groups={"finance"}),
        limit=5,
        minimum_relevance=0,
    )
    assert {item.source_id for item in general} == {"public-to-tenant"}
    assert {item.source_id for item in finance} == {"public-to-tenant", "finance-only"}
    await client.close()
