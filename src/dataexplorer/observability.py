import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from fastapi import Request, Response
from pydantic import BaseModel, Field


logger = logging.getLogger("dataexplorer.http")


class LlmTraceEvent(BaseModel):
    """Sanitized generation metadata. Prompt and response bodies are never stored."""

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str
    tenant_id: str
    user_id: str
    operation: str
    provider: str
    model: str
    status: str = "succeeded"
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    grounded: bool | None = None
    citation_count: int = Field(default=0, ge=0)
    reflection_attempts: int = Field(default=0, ge=0)


class LlmTraceStore(Protocol):
    async def record(self, event: LlmTraceEvent) -> None: ...

    async def list_traces(self, tenant_id: str, limit: int = 100) -> list[LlmTraceEvent]: ...


class InMemoryLlmTraceStore:
    def __init__(self) -> None:
        self.events: list[LlmTraceEvent] = []

    async def record(self, event: LlmTraceEvent) -> None:
        self.events.append(event)

    async def list_traces(self, tenant_id: str, limit: int = 100) -> list[LlmTraceEvent]:
        return [
            event for event in reversed(self.events) if event.tenant_id == tenant_id
        ][:limit]


@dataclass(frozen=True, slots=True)
class PricingCatalog:
    input_per_million_usd: dict[str, float]
    output_per_million_usd: dict[str, float]

    def estimate(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> float | None:
        input_rate = self._rate(self.input_per_million_usd, provider, model)
        output_rate = self._rate(self.output_per_million_usd, provider, model)
        if input_rate is None or output_rate is None:
            return None
        return round(
            (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000,
            8,
        )

    @staticmethod
    def _rate(rates: dict[str, float], provider: str, model: str) -> float | None:
        return rates.get(f"{provider}:{model}", rates.get(provider))


class ObservabilitySummary(BaseModel):
    requests: int
    users: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    unpriced_requests: int
    grounded_rate: float
    average_latency_ms: float


def summarize_traces(events: list[LlmTraceEvent]) -> ObservabilitySummary:
    count = len(events)
    grounded = [event for event in events if event.grounded is not None]
    priced = [event.estimated_cost_usd for event in events if event.estimated_cost_usd is not None]
    return ObservabilitySummary(
        requests=count,
        users=len({event.user_id for event in events}),
        input_tokens=sum(event.input_tokens for event in events),
        output_tokens=sum(event.output_tokens for event in events),
        total_tokens=sum(event.total_tokens for event in events),
        estimated_cost_usd=round(sum(priced), 8),
        unpriced_requests=count - len(priced),
        grounded_rate=(
            round(sum(bool(event.grounded) for event in grounded) / len(grounded), 4)
            if grounded else 0
        ),
        average_latency_ms=(
            round(sum(event.latency_ms for event in events) / count, 2) if count else 0
        ),
    )


@dataclass(slots=True)
class MetricsRegistry:
    requests: dict[tuple[str, str, int], int] = field(default_factory=lambda: defaultdict(int))
    duration_seconds: dict[tuple[str, str], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, method: str, route: str, status_code: int, duration: float) -> None:
        with self._lock:
            self.requests[(method, route, status_code)] += 1
            self.duration_seconds[(method, route)] += duration

    def prometheus(self) -> str:
        lines = [
            "# HELP dataexplorer_http_requests_total HTTP requests handled.",
            "# TYPE dataexplorer_http_requests_total counter",
        ]
        with self._lock:
            for (method, route, status_code), value in sorted(self.requests.items()):
                lines.append(
                    f'dataexplorer_http_requests_total{{method="{method}",route="{route}",'
                    f'status="{status_code}"}} {value}'
                )
            lines.extend([
                "# HELP dataexplorer_http_request_duration_seconds_sum Total request time.",
                "# TYPE dataexplorer_http_request_duration_seconds_sum counter",
            ])
            for (method, route), value in sorted(self.duration_seconds.items()):
                lines.append(
                    f'dataexplorer_http_request_duration_seconds_sum{{method="{method}",'
                    f'route="{route}"}} {value:.9f}'
                )
        return "\n".join(lines) + "\n"


async def observe_request(
    request: Request,
    call_next,
    registry: MetricsRegistry,
) -> Response:
    started = time.perf_counter()
    status_code = 500
    try:
        response: Response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - started
        route_object = request.scope.get("route")
        route = getattr(route_object, "path", request.url.path)
        registry.observe(request.method, route, status_code, duration)
        logger.info(json.dumps({
            "event": "http_request",
            "method": request.method,
            "route": route,
            "status_code": status_code,
            "duration_ms": round(duration * 1000, 3),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }, separators=(",", ":")))
