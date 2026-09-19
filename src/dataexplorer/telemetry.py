"""Sanitized per-attempt telemetry and period-scoped dashboard contracts."""
import math
from dataclasses import replace
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Query, Request

from dataexplorer.models import AccessContext
from dataexplorer.observability import LlmTraceEvent


attempt_context = ContextVar("model_attempt_context", default=None)


def instrument_provider(provider, store, pricing):
    from dataexplorer.cloud_providers import PolicyRouter
    if isinstance(provider, PolicyRouter):
        return replace(provider, routes=[replace(route, provider=ObservedProvider(route.provider, store, pricing))
                                         for route in provider.routes])
    return ObservedProvider(provider, store, pricing)


class ObservedProvider:
    def __init__(self, provider, store, pricing):
        self.provider, self.store, self.pricing = provider, store, pricing

    async def embed(self, texts):
        return await self.provider.embed(texts)

    async def healthcheck(self):
        return await self.provider.healthcheck()

    async def generate(self, *, system, prompt, policy=None):
        context = attempt_context.get()
        if context is None:
            return await self.provider.generate(system=system, prompt=prompt, policy=policy)
        started = time.perf_counter()
        result = None
        outcome = "failed"
        try:
            result = await self.provider.generate(system=system, prompt=prompt, policy=policy)
            outcome = "succeeded"
            return result
        except Exception as error:
            from dataexplorer.cloud_providers import ProviderPolicyError
            if isinstance(error, ProviderPolicyError):
                outcome = "blocked"
            raise
        finally:
            provider = result.provider if result else type(self.provider).__name__.removesuffix("Provider").lower()
            model = result.model if result else getattr(self.provider, "chat_model", "unresolved")
            input_tokens = result.input_tokens or 0 if result else 0
            output_tokens = result.output_tokens or 0 if result else 0
            known = result is not None and result.input_tokens is not None and result.output_tokens is not None
            await self.store.record(LlmTraceEvent(
                request_id=context["request_id"],
                correlation_id=context["correlation_id"], tenant_id=context["access"].tenant_id,
                user_id=context["access"].user_id, operation=context["operation"] + ".attempt",
                provider=provider, model=model, status=outcome,
                input_tokens=input_tokens, output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                estimated_cost_usd=self.pricing.estimate(provider, model, input_tokens, output_tokens) if known else None,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            ))


def dashboard(events):
    """Use attempts for usage, completed request events for end-to-end latency/grounding."""
    attempts = [e for e in events if e.operation.endswith(".attempt")]
    # Legacy traces have no request ID; preserve their historical grouping.
    def key(event):
        return (event.tenant_id, event.user_id, event.request_id or event.correlation_id)
    attempted_ids = {key(e) for e in attempts}
    completed = [e for e in events if not e.operation.endswith(".attempt")]
    usage = attempts + [e for e in completed if key(e) not in attempted_ids]
    request_ids = {key(e) for e in events}
    failures = {key(e) for e in usage if e.status == "failed"}
    successes = {key(e) for e in usage if e.status == "succeeded"}
    failures -= successes
    grounded = [e for e in completed if e.grounded is not None]
    latencies = sorted(e.latency_ms for e in completed if e.operation == "rag.query")
    series, users = {}, {}
    for event in usage:
        day = event.occurred_at.strftime("%Y-%m-%d")
        row = series.setdefault(day, {"date": day, "attempts": 0, "failures": 0, "estimated_cost_usd": None})
        row["attempts"] += 1
        row["failures"] += event.status == "failed"
        if event.estimated_cost_usd is not None:
            row["estimated_cost_usd"] = (row["estimated_cost_usd"] or 0) + event.estimated_cost_usd
        user = users.setdefault(event.user_id, {"user": event.user_id, "attempts": 0, "tokens": 0, "estimated_cost_usd": 0.0, "unpriced_attempts": 0})
        user["attempts"] += 1
        user["tokens"] += event.total_tokens
        user["estimated_cost_usd"] += event.estimated_cost_usd or 0
        user["unpriced_attempts"] += event.estimated_cost_usd is None
    priced = [e.estimated_cost_usd for e in usage if e.estimated_cost_usd is not None]
    return {
        "requests": len(request_ids), "attempts": len(usage), "active_users": len(users),
        "failed_requests": len(failures), "failure_rate": len(failures) / len(request_ids) if request_ids else None,
        "tokens": sum(e.total_tokens for e in usage), "estimated_cost_usd": sum(priced) if priced else None,
        "unpriced_attempts": len(usage) - len(priced), "priced_attempts": len(priced),
        "grounded_rate": sum(bool(e.grounded) for e in grounded) / len(grounded) if grounded else None,
        "grounded_samples": len(grounded),
        "p95_latency_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None,
        "latency_samples": len(latencies), "series": sorted(series.values(), key=lambda r: r["date"]),
        "users": sorted(users.values(), key=lambda r: r["attempts"], reverse=True),
    }


def register_telemetry_routes(app, access_dependency, require_admin):
    @app.get("/v1/observability/events", tags=["observability"])
    async def audit_events(request: Request, start: datetime, end: datetime,
                           search: str = Query("", max_length=200), offset: int = Query(0, ge=0),
                           access: AccessContext = Depends(access_dependency)):
        require_admin(request, access)
        if start.tzinfo is None or end.tzinfo is None or not start < end or end-start > timedelta(days=93):
            raise HTTPException(422, "Choose a timezone-aware range of up to 93 days")
        rows = await request.app.state.audit_sink.query_events(access.tenant_id, start, end, search, offset, 26)
        return {"items": rows[:25], "has_more": len(rows) > 25}

    @app.get("/v1/observability/dashboard", tags=["observability"])
    async def overview(request: Request, start: datetime | None = None, end: datetime | None = None,
                       provider: str = Query("", max_length=100), model: str = Query("", max_length=200),
                       search: str = Query("", max_length=200), offset: int = Query(0, ge=0),
                       limit: int = Query(25, ge=1, le=100),
                       access: AccessContext = Depends(access_dependency)):
        require_admin(request, access)
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=7)
        if start.tzinfo is None or end.tzinfo is None or not start < end or end-start > timedelta(days=93):
            raise HTTPException(422, "Choose a timezone-aware range of up to 93 days")
        events = await request.app.state.trace_store.query_traces(access.tenant_id, start, end, provider, model)
        result = dashboard(events)
        previous = await request.app.state.trace_store.query_traces(access.tenant_id, start-(end-start), start, provider, model)
        result["previous"] = dashboard(previous)
        traces = [e for e in events if search.casefold() in
                  f"{e.correlation_id} {e.user_id} {e.provider} {e.model} {e.status}".casefold()]
        result.update({"start": start, "end": end, "updated_at": datetime.now(UTC),
                       "traces": traces[offset:offset+limit], "has_more": len(traces) > offset + limit,
                       "trace_count": len(traces), "environment": request.app.state.environment})
        return result
