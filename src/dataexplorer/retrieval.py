import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from dataexplorer.models import AccessContext, Evidence, StoredChunk


class Reranker(Protocol):
    async def rerank(
        self, query: str, evidence: list[Evidence], *, limit: int
    ) -> list[Evidence]: ...


class Retriever(Protocol):
    async def index(self, chunk: StoredChunk, vector: list[float]) -> None: ...

    async def search(
        self,
        query: str,
        vector: list[float],
        access: AccessContext,
        *,
        limit: int,
        minimum_relevance: float,
    ) -> list[Evidence]: ...


@dataclass(slots=True)
class _Entry:
    chunk: StoredChunk
    vector: list[float]
    terms: Counter[str]


@dataclass(slots=True)
class LexicalReranker:
    """Deterministic local reranker used in tests and constrained environments."""

    async def rerank(
        self, query: str, evidence: list[Evidence], *, limit: int
    ) -> list[Evidence]:
        query_terms = set(_tokenize(query))
        for item in evidence:
            document_terms = set(_tokenize(f"{item.title} {item.text}"))
            overlap = len(query_terms & document_terms) / max(len(query_terms), 1)
            item.score = min(1.0, 0.8 * item.score + 0.2 * overlap)
        return sorted(evidence, key=lambda item: item.score, reverse=True)[:limit]


@dataclass(slots=True)
class FlashRankReranker:
    """Lazy FlashRank adapter so importing the API never downloads a model."""

    model_name: str = "ms-marco-TinyBERT-L-2-v2"
    _ranker: object | None = field(default=None, init=False, repr=False)

    async def rerank(
        self, query: str, evidence: list[Evidence], *, limit: int
    ) -> list[Evidence]:
        from flashrank import Ranker, RerankRequest

        if self._ranker is None:
            self._ranker = Ranker(model_name=self.model_name)
        passages = [
            {"id": index, "text": item.text, "meta": {}}
            for index, item in enumerate(evidence)
        ]
        ranked = self._ranker.rerank(RerankRequest(query=query, passages=passages))
        output: list[Evidence] = []
        for result in ranked[:limit]:
            item = evidence[int(result["id"])]
            item.score = max(0.0, min(1.0, float(result["score"])))
            output.append(item)
        return output


@dataclass(slots=True)
class InMemoryRetriever:
    """Hybrid development retriever with authorization pre-filter semantics."""

    reranker: Reranker = field(default_factory=LexicalReranker)
    rrf_k: int = 60
    entries: dict[tuple[str, str], _Entry] = field(default_factory=dict)

    async def index(self, chunk: StoredChunk, vector: list[float]) -> None:
        if not vector:
            raise ValueError("document embedding cannot be empty")
        self.entries[(chunk.tenant_id, chunk.chunk_id)] = _Entry(
            chunk=chunk,
            vector=vector,
            terms=Counter(_tokenize(f"{chunk.title} {chunk.text}")),
        )

    async def search(
        self,
        query: str,
        vector: list[float],
        access: AccessContext,
        *,
        limit: int,
        minimum_relevance: float,
    ) -> list[Evidence]:
        eligible = [
            entry
            for entry in self.entries.values()
            if _authorized(entry.chunk, access) and not entry.chunk.expired
        ]
        if not eligible:
            return []

        dense_scores = {
            item.chunk.chunk_id: max(0.0, _cosine_similarity(vector, item.vector))
            for item in eligible
        }
        dense = sorted(
            eligible,
            key=lambda item: dense_scores[item.chunk.chunk_id],
            reverse=True,
        )
        query_terms = Counter(_tokenize(query))
        sparse_scores = _bm25_scores(query_terms, eligible)
        sparse = sorted(
            eligible,
            key=lambda item: sparse_scores[item.chunk.chunk_id],
            reverse=True,
        )
        dense_rank = {item.chunk.chunk_id: rank for rank, item in enumerate(dense, 1)}
        sparse_rank = {item.chunk.chunk_id: rank for rank, item in enumerate(sparse, 1)}
        raw_rrf = {
            item.chunk.chunk_id: (
                1 / (self.rrf_k + dense_rank[item.chunk.chunk_id])
                + 1 / (self.rrf_k + sparse_rank[item.chunk.chunk_id])
            )
            for item in eligible
        }
        maximum_rrf = max(raw_rrf.values())
        maximum_sparse = max(sparse_scores.values())
        candidates: list[Evidence] = []
        for entry in eligible:
            rrf_score = raw_rrf[entry.chunk.chunk_id] / maximum_rrf
            dense_score = dense_scores[entry.chunk.chunk_id]
            sparse_score = (
                sparse_scores[entry.chunk.chunk_id] / maximum_sparse
                if maximum_sparse > 0
                else 0.0
            )
            trust_score = _TRUST_SCORES.get(entry.chunk.trust_tier, 0.0)
            score = (
                0.45 * dense_score
                + 0.35 * sparse_score
                + 0.1 * rrf_score
                + 0.1 * trust_score
            )
            if score < minimum_relevance:
                continue
            chunk = entry.chunk
            candidates.append(
                Evidence(
                    source_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    title=chunk.title,
                    text=chunk.text,
                    score=score,
                    metadata={
                        **chunk.metadata,
                        "classification": chunk.classification,
                        "trust_tier": chunk.trust_tier,
                        "version": chunk.version,
                        "content_sha256": chunk.content_sha256,
                        "dense_rank": dense_rank[chunk.chunk_id],
                        "sparse_rank": sparse_rank[chunk.chunk_id],
                    },
                )
            )
        return await self.reranker.rerank(query, candidates, limit=limit)


_TRUST_SCORES = {
    "authoritative": 1.0,
    "approved-reference": 0.8,
    "working-draft": 0.45,
    "external": 0.3,
    "untrusted": 0.0,
}


def _authorized(chunk: StoredChunk, access: AccessContext) -> bool:
    if chunk.tenant_id != access.tenant_id:
        return False
    return not chunk.allowed_groups or not chunk.allowed_groups.isdisjoint(access.groups)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bm25_scores(query: Counter[str], entries: list[_Entry]) -> dict[str, float]:
    document_count = len(entries)
    average_length = sum(sum(item.terms.values()) for item in entries) / max(document_count, 1)
    document_frequency = Counter(term for item in entries for term in item.terms)
    scores: dict[str, float] = {}
    k1, b = 1.5, 0.75
    for item in entries:
        length = sum(item.terms.values())
        score = 0.0
        for term in query:
            frequency = item.terms[term]
            if frequency == 0:
                continue
            frequency_in_docs = document_frequency[term]
            inverse_frequency = math.log(
                1 + (document_count - frequency_in_docs + 0.5) / (frequency_in_docs + 0.5)
            )
            denominator = frequency + k1 * (1 - b + b * length / max(average_length, 1))
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        scores[item.chunk.chunk_id] = score
    return scores


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("vectors must have the same non-zero dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
