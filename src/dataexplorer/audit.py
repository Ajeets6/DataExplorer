from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from dataexplorer.models import AccessContext


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str
    action: str
    decision: str
    user_id: str
    tenant_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...

    async def list_events(self, tenant_id: str, limit: int = 100) -> list[AuditEvent]: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def list_events(self, tenant_id: str, limit: int = 100) -> list[AuditEvent]:
        return [
            event for event in reversed(self.events) if event.tenant_id == tenant_id
        ][:limit]

    async def query_events(self, tenant_id, start, end, search="", offset=0, limit=26):
        rows = [e for e in self.events if e.tenant_id == tenant_id and start <= e.occurred_at < end
                and search.casefold() in f"{e.user_id} {e.action} {e.decision} {e.correlation_id}".casefold()]
        return sorted(rows, key=lambda e: (e.occurred_at, e.event_id), reverse=True)[offset:offset+limit]


def make_audit_event(
    *,
    correlation_id: str,
    action: str,
    decision: str,
    access: AccessContext,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        correlation_id=correlation_id,
        action=action,
        decision=decision,
        user_id=access.user_id,
        tenant_id=access.tenant_id,
        metadata=metadata or {},
    )
