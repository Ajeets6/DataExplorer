import hashlib
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import BaseModel

from dataexplorer.providers import GeneratedText, ModelProvider


class ProviderPolicyError(RuntimeError):
    pass


class ProviderContext(BaseModel):
    tenant_id: str = "system"
    user_id: str = "system"
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    region: str = "global"


@dataclass(slots=True)
class OpenAIProvider:
    api_key: str
    chat_model: str = "gpt-5.4"
    embedding_model: str = "text-embedding-3-large"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60.0
    transport: httpx.AsyncBaseTransport | None = None

    async def generate(self, *, system: str, prompt: str, policy: object | None = None) -> GeneratedText:
        context = policy if isinstance(policy, ProviderContext) else ProviderContext()
        response = await self._post("/responses", {
            "model": self.chat_model,
            "instructions": system,
            "input": prompt,
            "store": False,
            "max_output_tokens": 2_000,
            "safety_identifier": hashlib.sha256(
                f"{context.tenant_id}:{context.user_id}".encode()
            ).hexdigest(),
        })
        payload = response.json()
        usage = payload.get("usage", {})
        return GeneratedText(
            text=_openai_output_text(payload),
            provider="openai",
            model=payload.get("model", self.chat_model),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            metadata={"response_id": payload.get("id")},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._post("/embeddings", {
            "model": self.embedding_model,
            "input": texts,
        })
        data = sorted(response.json().get("data", []), key=lambda item: item["index"])
        if len(data) != len(texts):
            raise RuntimeError("OpenAI returned an unexpected embedding count")
        return [item["embedding"] for item in data]

    async def healthcheck(self) -> bool:
        try:
            async with self._client(timeout=min(self.timeout_seconds, 3.0)) as client:
                response = await client.get("/models")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def _post(self, path: str, body: dict[str, object]) -> httpx.Response:
        async with self._client() as client:
            response = await client.post(path, json=body)
        response.raise_for_status()
        return response

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout or self.timeout_seconds,
            headers={"Authorization": f"Bearer {self.api_key}"},
            transport=self.transport,
        )


@dataclass(slots=True)
class AnthropicProvider:
    api_key: str
    chat_model: str = "claude-sonnet-4-5"
    embedding_provider: ModelProvider | None = None
    base_url: str = "https://api.anthropic.com/v1"
    timeout_seconds: float = 60.0
    transport: httpx.AsyncBaseTransport | None = None

    async def generate(self, *, system: str, prompt: str, policy: object | None = None) -> GeneratedText:
        async with self._client() as client:
            response = await client.post("/messages", json={
                "model": self.chat_model,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2_000,
                "temperature": 0,
            })
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage", {})
        text = "".join(
            block.get("text", "") for block in payload.get("content", [])
            if block.get("type") == "text"
        ).strip()
        return GeneratedText(
            text=text,
            provider="anthropic",
            model=payload.get("model", self.chat_model),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            metadata={"response_id": payload.get("id")},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.embedding_provider is None:
            raise ProviderPolicyError("Anthropic requires a configured embedding provider")
        return await self.embedding_provider.embed(texts)

    async def healthcheck(self) -> bool:
        try:
            async with self._client(timeout=min(self.timeout_seconds, 3.0)) as client:
                response = await client.get("/models")
            return response.is_success
        except httpx.HTTPError:
            return False

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout or self.timeout_seconds,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            transport=self.transport,
        )


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    name: str
    provider: ModelProvider
    external: bool
    allowed_classifications: frozenset[str]
    allowed_regions: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))


@dataclass(slots=True)
class PolicyRouter:
    routes: list[ProviderRoute]
    embedding_provider: ModelProvider

    async def generate(self, *, system: str, prompt: str, policy: object | None = None) -> GeneratedText:
        context = policy if isinstance(policy, ProviderContext) else ProviderContext()
        eligible = [route for route in self.routes if self._eligible(route, context)]
        if not eligible:
            raise ProviderPolicyError(
                f"no model route permits {context.classification} data in {context.region}"
            )
        failures: list[str] = []
        for route in eligible:
            try:
                result = await route.provider.generate(system=system, prompt=prompt, policy=context)
                result.metadata["route"] = route.name
                return result
            except (httpx.HTTPError, RuntimeError) as error:
                failures.append(f"{route.name}: {type(error).__name__}")
        raise RuntimeError(f"all eligible providers failed ({'; '.join(failures)})")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self.embedding_provider.embed(texts)

    async def healthcheck(self) -> bool:
        checks = [await route.provider.healthcheck() for route in self.routes]
        return await self.embedding_provider.healthcheck() and any(checks)

    @staticmethod
    def _eligible(route: ProviderRoute, context: ProviderContext) -> bool:
        return (
            context.classification in route.allowed_classifications
            and ("*" in route.allowed_regions or context.region in route.allowed_regions)
            and not (
                route.external and context.classification in {"confidential", "restricted"}
            )
        )


def _openai_output_text(payload: dict[str, object]) -> str:
    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"]).strip()
    parts: list[str] = []
    output = payload.get("output", [])
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text", "")))
    return "".join(parts).strip()
