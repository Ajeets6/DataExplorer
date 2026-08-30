from dataexplorer.providers import OllamaProvider, _ollama_model_is_installed


class _EmbeddingResponse:
    def __init__(self, count: int) -> None:
        self.count = count

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, list[list[float]]]:
        return {"embeddings": [[1.0, 0.0] for _ in range(self.count)]}


class _EmbeddingClient:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, _path: str, *, json: dict[str, object]):
        inputs = json["input"]
        assert isinstance(inputs, list)
        self.batch_sizes.append(len(inputs))
        return _EmbeddingResponse(len(inputs))


def test_ollama_implicit_latest_tag_matches_installed_model() -> None:
    assert _ollama_model_is_installed(
        "embeddinggemma",
        {"embeddinggemma:latest"},
    )


def test_ollama_missing_model_is_not_ready() -> None:
    assert not _ollama_model_is_installed(
        "llama3.1:8b",
        {"qwen2.5-coder:7b"},
    )


async def test_ollama_embeddings_are_sent_in_bounded_batches(monkeypatch) -> None:
    client = _EmbeddingClient()
    monkeypatch.setattr(
        "dataexplorer.providers.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        chat_model="chat",
        embedding_model="embed",
    )

    embeddings = await provider.embed([f"chunk-{index}" for index in range(65)])

    assert len(embeddings) == 65
    assert client.batch_sizes == [32, 32, 1]
