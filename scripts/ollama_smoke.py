import asyncio
import os

from dataexplorer.api import build_service
from dataexplorer.config import Settings
from dataexplorer.models import AccessContext, DocumentIn


async def main() -> None:
    settings = Settings(
        ollama_chat_model=os.getenv("OLLAMA_SMOKE_CHAT_MODEL", "llama3.1:8b"),
        ollama_embedding_model=os.getenv(
            "OLLAMA_SMOKE_EMBEDDING_MODEL", "embeddinggemma"
        ),
    )
    service = build_service(settings)
    if not await service.ready():
        raise RuntimeError("configured Ollama smoke-test models are not ready")
    access = AccessContext(user_id="smoke", tenant_id="qa")
    await service.ingest(
        DocumentIn(
            document_id="smoke-policy",
            title="Travel policy",
            text="Approved rail travel is reimbursable.",
        ),
        access,
    )
    result = await service.answer("What travel is reimbursable?", access)
    if not result.grounded or not result.citations:
        raise RuntimeError("Ollama smoke answer was not grounded")
    print(
        f"ready=true grounded={result.grounded} provider={result.model_provider} "
        f"citations={len(result.citations)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
