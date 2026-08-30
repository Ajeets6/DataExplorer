from datetime import UTC, datetime, timedelta

from dataexplorer.chunking import chunk_document
from dataexplorer.models import AccessContext, DocumentIn, StoredDocument
from dataexplorer.providers import GeneratedText
from dataexplorer.retrieval import InMemoryRetriever
from dataexplorer.service import RagService


class QualityProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def generate(self, *, system: str, prompt: str, policy=None) -> GeneratedText:
        self.calls += 1
        text = "Uncited answer" if self.calls == 1 else "Grounded answer [S1]"
        return GeneratedText(text=text, provider="fake", model="quality")

    async def healthcheck(self) -> bool:
        return True


def test_semantic_chunking_preserves_lineage() -> None:
    document = StoredDocument(
        document_id="doc",
        tenant_id="acme",
        title="Policy",
        text="First paragraph.\n\nSecond paragraph with more detail.",
    )
    chunks = chunk_document(document, maximum_characters=25, overlap_characters=5)
    assert len(chunks) >= 2
    assert chunks[0].chunk_id == "doc:0"
    assert all(chunk.content_sha256 == chunks[0].content_sha256 for chunk in chunks)


async def test_duplicate_content_is_not_indexed_twice() -> None:
    provider = QualityProvider()
    service = RagService(provider=provider, retriever=InMemoryRetriever())
    access = AccessContext(user_id="u", tenant_id="acme")
    document = DocumentIn(document_id="one", title="Policy", text="Same content")
    first = await service.ingest(document, access)
    second = await service.ingest(document.model_copy(update={"document_id": "two"}), access)
    assert first.indexed is True
    assert first.chunk_count == 1
    assert second.deduplicated is True


async def test_expired_document_is_not_retrieved() -> None:
    provider = QualityProvider()
    service = RagService(provider=provider, retriever=InMemoryRetriever())
    access = AccessContext(user_id="u", tenant_id="acme")
    await service.ingest(
        DocumentIn(
            document_id="old",
            title="Old policy",
            text="An obsolete policy",
            valid_until=datetime.now(UTC) - timedelta(days=1),
        ),
        access,
    )
    answer = await service.answer("What is the policy?", access)
    assert answer.grounded is False
    assert provider.calls == 0


async def test_bounded_reflection_repairs_missing_citation() -> None:
    provider = QualityProvider()
    service = RagService(provider=provider, retriever=InMemoryRetriever())
    access = AccessContext(user_id="u", tenant_id="acme")
    await service.ingest(
        DocumentIn(document_id="policy", title="Policy", text="Policy evidence"),
        access,
    )
    answer = await service.answer("What is the policy?", access)
    assert answer.grounded is True
    assert answer.reflection_attempts == 1
    assert provider.calls == 2
