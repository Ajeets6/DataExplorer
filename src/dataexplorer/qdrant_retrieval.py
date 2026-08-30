import asyncio
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from dataexplorer.models import AccessContext, Evidence, StoredChunk
from dataexplorer.retrieval import LexicalReranker, Reranker


@dataclass(slots=True)
class QdrantHybridRetriever:
    client: AsyncQdrantClient
    collection_name: str
    reranker: Reranker = field(default_factory=LexicalReranker)
    _ready: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def index(self, chunk: StoredChunk, vector: list[float]) -> None:
        if not vector:
            raise ValueError("document embedding cannot be empty")
        await self._ensure_collection(len(vector))
        sparse = _sparse_vector(chunk.text)
        payload = chunk.model_dump(mode="json")
        payload["acl_public"] = not bool(chunk.allowed_groups)
        await self.client.upsert(
            collection_name=self.collection_name,
            wait=True,
            points=[models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, f"{chunk.tenant_id}:{chunk.chunk_id}")),
                vector={"dense": vector, "sparse": sparse},
                payload=payload,
            )],
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
        if not self._ready and not await self.client.collection_exists(self.collection_name):
            return []
        self._ready = True
        group_conditions: list[models.Condition] = [models.FieldCondition(
            key="acl_public", match=models.MatchValue(value=True)
        )]
        if access.groups:
            group_conditions.append(models.FieldCondition(
                key="allowed_groups",
                match=models.MatchAny(any=sorted(access.groups)),
            ))
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="tenant_id", match=models.MatchValue(value=access.tenant_id)
                ),
                models.Filter(should=group_conditions),
            ],
        )
        candidate_limit = max(limit * 4, 20)
        response = await self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                models.Prefetch(
                    query=vector,
                    using="dense",
                    filter=query_filter,
                    limit=candidate_limit,
                ),
                models.Prefetch(
                    query=_sparse_vector(query),
                    using="sparse",
                    filter=query_filter,
                    limit=candidate_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=candidate_limit,
            with_payload=True,
        )
        evidence: list[Evidence] = []
        for point in response.points:
            payload = point.payload or {}
            if not _authorized_payload(payload, access) or _expired(payload):
                continue
            score = max(0.0, min(1.0, float(point.score or 0)))
            if score < minimum_relevance:
                continue
            evidence.append(Evidence(
                source_id=str(payload["chunk_id"]),
                document_id=str(payload["document_id"]),
                title=str(payload["title"]),
                text=str(payload["text"]),
                score=score,
                metadata={
                    "classification": payload.get("classification", "internal"),
                    "trust_tier": payload.get("trust_tier", "approved-reference"),
                    "version": payload.get("version", "1"),
                    "content_sha256": payload.get("content_sha256", ""),
                    "retrieval": "qdrant-dense-sparse-rrf",
                },
            ))
        return await self.reranker.rerank(query, evidence, limit=limit)

    async def _ensure_collection(self, dimensions: int) -> None:
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            if not await self.client.collection_exists(self.collection_name):
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={
                        "dense": models.VectorParams(
                            size=dimensions, distance=models.Distance.COSINE
                        )
                    },
                    sparse_vectors_config={
                        "sparse": models.SparseVectorParams(
                            index=models.SparseIndexParams(on_disk=True)
                        )
                    },
                )
                for field_name in ("tenant_id", "allowed_groups", "classification", "acl_public"):
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field_name,
                        field_schema=(
                            models.PayloadSchemaType.BOOL
                            if field_name == "acl_public"
                            else models.PayloadSchemaType.KEYWORD
                        ),
                    )
            self._ready = True


def _sparse_vector(text: str) -> models.SparseVector:
    counts = Counter(re.findall(r"[a-z0-9]+", text.lower()))
    pairs = sorted(
        (
            int.from_bytes(hashlib.blake2b(term.encode(), digest_size=4).digest(), "big"),
            1.0 + math.log(count),
        )
        for term, count in counts.items()
    )
    return models.SparseVector(
        indices=[index for index, _ in pairs],
        values=[value for _, value in pairs],
    )


def _expired(payload: dict[str, object]) -> bool:
    if payload.get("trust_tier") == "expired":
        return True
    raw = payload.get("valid_until")
    if not isinstance(raw, str) or not raw:
        return False
    expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires < datetime.now(UTC)


def _authorized_payload(payload: dict[str, object], access: AccessContext) -> bool:
    if payload.get("tenant_id") != access.tenant_id:
        return False
    allowed = payload.get("allowed_groups")
    if not isinstance(allowed, list) or not allowed:
        return True
    return bool(set(map(str, allowed)) & set(access.groups))
