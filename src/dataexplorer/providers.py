from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, Field


class GeneratedText(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ModelProvider(Protocol):
    async def generate(
        self, *, system: str, prompt: str, policy: object | None = None
    ) -> GeneratedText: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def healthcheck(self) -> bool: ...


@dataclass(slots=True)
class OllamaProvider:
    base_url: str
    chat_model: str
    embedding_model: str
    timeout_seconds: float = 60.0

    async def generate(
        self, *, system: str, prompt: str, policy: object | None = None
    ) -> GeneratedText:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.post(
                "/api/chat",
                json={
                    "model": self.chat_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            payload = response.json()
        return GeneratedText(
            text=payload["message"]["content"].strip(),
            provider="ollama",
            model=payload.get("model", self.chat_model),
            input_tokens=payload.get("prompt_eval_count"),
            output_tokens=payload.get("eval_count"),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        ) as client:
            # Large enterprise documents can produce hundreds of chunks. Keep
            # each Ollama request bounded so the embedding runner does not reject
            # an otherwise valid document because the aggregate batch is too big.
            for start in range(0, len(texts), 32):
                batch = texts[start : start + 32]
                response = await client.post(
                    "/api/embed",
                    json={"model": self.embedding_model, "input": batch},
                )
                response.raise_for_status()
                batch_embeddings = response.json().get("embeddings", [])
                if len(batch_embeddings) != len(batch):
                    raise RuntimeError("Ollama returned an unexpected embedding count")
                embeddings.extend(batch_embeddings)
        if len(embeddings) != len(texts):
            raise RuntimeError("Ollama returned an unexpected embedding count")
        return embeddings

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=min(self.timeout_seconds, 3.0),
            ) as client:
                response = await client.get("/api/tags")
            response.raise_for_status()
            installed = {
                item.get("name", "") for item in response.json().get("models", [])
            }
            return _ollama_model_is_installed(
                self.chat_model, installed
            ) and _ollama_model_is_installed(self.embedding_model, installed)
        except (httpx.HTTPError, ValueError, TypeError):
            return False


def _ollama_model_is_installed(model: str, installed: set[str]) -> bool:
    """Treat Ollama's implicit and explicit `latest` tags as equivalent."""

    candidates = {model}
    if ":" not in model:
        candidates.add(f"{model}:latest")
    return not candidates.isdisjoint(installed)
