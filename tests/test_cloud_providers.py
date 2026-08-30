import json

import httpx
import pytest

from dataexplorer.cloud_providers import (
    AnthropicProvider,
    OpenAIProvider,
    PolicyRouter,
    ProviderContext,
    ProviderPolicyError,
    ProviderRoute,
)
from dataexplorer.providers import GeneratedText


async def test_openai_responses_disables_storage_and_sets_safety_identifier() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={
            "id": "resp_1",
            "model": "gpt-test",
            "output_text": "Grounded [S1]",
            "usage": {"input_tokens": 10, "output_tokens": 3},
        })

    provider = OpenAIProvider(
        api_key="secret",
        chat_model="gpt-test",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.generate(
        system="ground answers",
        prompt="question",
        policy=ProviderContext(tenant_id="acme", user_id="user-1"),
    )
    assert result.text == "Grounded [S1]"
    assert captured["store"] is False
    assert len(str(captured["safety_identifier"])) == 64


async def test_anthropic_messages_adapter_extracts_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={
            "id": "msg_1",
            "model": "claude-test",
            "content": [{"type": "text", "text": "Grounded [S1]"}],
            "usage": {"input_tokens": 8, "output_tokens": 3},
        })

    provider = AnthropicProvider(
        api_key="secret",
        chat_model="claude-test",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.generate(system="ground answers", prompt="question")
    assert result.provider == "anthropic"
    assert result.text == "Grounded [S1]"


class StubProvider:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    async def generate(self, *, system: str, prompt: str, policy=None) -> GeneratedText:
        if self.fail:
            raise RuntimeError("unavailable")
        return GeneratedText(text="ok", provider=self.name, model="test")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    async def healthcheck(self) -> bool:
        return not self.fail


async def test_router_blocks_external_confidential_data_and_uses_local_route() -> None:
    external = StubProvider("external")
    local = StubProvider("local")
    router = PolicyRouter(
        routes=[
            ProviderRoute("external", external, True, frozenset({"internal", "confidential"})),
            ProviderRoute("local", local, False, frozenset({"confidential"})),
        ],
        embedding_provider=local,
    )
    result = await router.generate(
        system="safe",
        prompt="sensitive",
        policy=ProviderContext(classification="confidential"),
    )
    assert result.provider == "local"
    assert result.metadata["route"] == "local"


async def test_router_fails_closed_without_an_eligible_route() -> None:
    external = StubProvider("external")
    router = PolicyRouter(
        routes=[ProviderRoute("external", external, True, frozenset({"public"}))],
        embedding_provider=external,
    )
    with pytest.raises(ProviderPolicyError):
        await router.generate(
            system="safe",
            prompt="sensitive",
            policy=ProviderContext(classification="restricted"),
        )
