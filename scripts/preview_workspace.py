"""Isolated visual-QA server with synthetic records; never used by normal startup.

Run with DATAEXPLORER_ENVIRONMENT=test and point a separate UI at port 8010.
No external model calls, production credentials, or production records are used.
"""
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dataexplorer.api import create_app
from dataexplorer.artifacts import ArtifactService, DocxRenderer
from dataexplorer.models import AccessContext, ArtifactSpec, DocumentIn
from dataexplorer.observability import LlmTraceEvent
from dataexplorer.providers import GeneratedText
from dataexplorer.retrieval import InMemoryRetriever
from dataexplorer.service import RagService
from dataexplorer.workspace import WorkspaceRecord


class FixtureProvider:
    async def embed(self, texts):
        return [[1.0] for _ in texts]

    async def generate(self, **kwargs):
        return GeneratedText(text="Approved rail travel is reimbursable with a receipt [S1].",
                             provider="qa-fixture", model="synthetic", input_tokens=120, output_tokens=35)

    async def healthcheck(self):
        return True


def create_preview():
    if os.getenv("DATAEXPLORER_ENVIRONMENT") != "test":
        raise RuntimeError("Visual QA requires DATAEXPLORER_ENVIRONMENT=test")
    service = RagService(provider=FixtureProvider(), retriever=InMemoryRetriever())
    artifacts = ArtifactService(output_root=Path(".artifacts/workspace-preview"), renderers={"docx": DocxRenderer()})
    app = create_app(service, artifact_service=artifacts)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            author = AccessContext(user_id="analyst-1", tenant_id="preview-acme", groups=frozenset({"finance"}))
            reviewer = AccessContext(user_id="reviewer", tenant_id="preview-acme", groups=frozenset({"finance", "content-approvers"}))
            document = DocumentIn(document_id="travel-policy", title="Travel & expense policy",
                                  text="Approved rail travel is reimbursable with a receipt. This is synthetic QA content.",
                                  allowed_groups=frozenset({"finance"}))
            await service.ingest(document, author)
            await application.state.workspace_store.save(WorkspaceRecord(
                record_id=document.document_id, tenant_id=author.tenant_id, owner=author.user_id,
                kind="document", title=document.title, groups=["finance"],
                payload={**document.model_dump(mode="json"), "status": "indexed"}))
            titles = ["September operating review", "Travel spend · regional overview", "Supplier onboarding brief", "Quarterly leadership update"]
            for index, title in enumerate(titles):
                owner = reviewer if index in {1, 2} else author
                draft = await artifacts.create(ArtifactSpec.model_validate({
                    "kind": "docx", "filename": f"sample-{index}.docx", "title": title,
                    "audience": "Finance and operations leadership", "purpose": "Review company travel guidance",
                    "sections": [{"title": "Executive summary", "summary": "Approved rail travel is reimbursable with a receipt. This report contains synthetic QA content.", "source_ids": ["travel-policy"]}],
                    "sources": [{"source_id": "travel-policy", "label": document.title, "locator": "document://travel-policy"}],
                }), owner)
                if index == 3:
                    await artifacts.decide(draft.artifact_id, approved=True, reason="Synthetic evidence reviewed for QA", access=reviewer)
                    await artifacts.render(draft.artifact_id, author)
            answer = await service.answer("What travel costs can I claim?", author)
            await application.state.workspace_store.save(WorkspaceRecord(
                tenant_id=author.tenant_id, owner=author.user_id, kind="answer", private=True,
                title="What travel costs can I claim?",
                payload={"question": "What travel costs can I claim?", "response": answer.model_dump(mode="json")}))
            now = datetime.now(UTC)
            for day in range(7):
                for index in range(5 + day):
                    failed = index == 2 and day in {1, 3}
                    base = dict(occurred_at=now-timedelta(days=day, minutes=index), correlation_id=f"qa-{day}-{index}",
                                tenant_id=author.tenant_id, user_id="analyst-1" if index % 2 else "reviewer",
                                provider="qa-fixture", model="synthetic", input_tokens=120, output_tokens=35,
                                total_tokens=155, estimated_cost_usd=.00155)
                    await application.state.trace_store.record(LlmTraceEvent(**base, operation="rag.query.attempt",
                        status="failed" if failed else "succeeded", latency_ms=700+day*120))
                    if not failed:
                        await application.state.trace_store.record(LlmTraceEvent(**base, operation="rag.query",
                            grounded=True, citation_count=1, latency_ms=900+day*150))
            yield
    app.router.lifespan_context = lifespan
    return app
