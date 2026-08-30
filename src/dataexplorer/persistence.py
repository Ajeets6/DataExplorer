import json
from dataclasses import dataclass

import psycopg

from dataexplorer.artifacts import ArtifactPolicyError
from dataexplorer.audit import AuditEvent
from dataexplorer.models import ArtifactDraft, SqlProposal
from dataexplorer.observability import LlmTraceEvent
from dataexplorer.text2sql import SqlApprovalError


@dataclass(slots=True)
class PostgresAuditSink:
    dsn: str

    async def record(self, event: AuditEvent) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            await connection.execute(
                """
                INSERT INTO audit_events
                    (event_id, occurred_at, correlation_id, action, decision,
                     user_id, tenant_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    event.event_id,
                    event.occurred_at,
                    event.correlation_id,
                    event.action,
                    event.decision,
                    event.user_id,
                    event.tenant_id,
                    json.dumps(event.metadata),
                ),
            )

    async def list_events(self, tenant_id: str, limit: int = 100) -> list[AuditEvent]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """
                SELECT event_id, occurred_at, correlation_id, action, decision,
                       user_id, tenant_id, metadata
                FROM audit_events WHERE tenant_id = %s
                ORDER BY occurred_at DESC LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            AuditEvent(
                event_id=str(row[0]), occurred_at=row[1], correlation_id=row[2],
                action=row[3], decision=row[4], user_id=row[5], tenant_id=row[6],
                metadata=row[7],
            )
            for row in rows
        ]


@dataclass(slots=True)
class PostgresLlmTraceStore:
    dsn: str

    async def record(self, event: LlmTraceEvent) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            await connection.execute(
                """
                INSERT INTO llm_traces
                    (trace_id, occurred_at, correlation_id, tenant_id, user_id,
                     operation, provider, model, status, input_tokens,
                     output_tokens, total_tokens, estimated_cost_usd, latency_ms,
                     grounded, citation_count, reflection_attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s)
                """,
                (
                    event.trace_id, event.occurred_at, event.correlation_id,
                    event.tenant_id, event.user_id, event.operation, event.provider,
                    event.model, event.status, event.input_tokens, event.output_tokens,
                    event.total_tokens, event.estimated_cost_usd, event.latency_ms,
                    event.grounded, event.citation_count, event.reflection_attempts,
                ),
            )

    async def list_traces(self, tenant_id: str, limit: int = 100) -> list[LlmTraceEvent]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """
                SELECT trace_id, occurred_at, correlation_id, tenant_id, user_id,
                       operation, provider, model, status, input_tokens,
                       output_tokens, total_tokens, estimated_cost_usd, latency_ms,
                       grounded, citation_count, reflection_attempts
                FROM llm_traces WHERE tenant_id = %s
                ORDER BY occurred_at DESC LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            LlmTraceEvent(
                trace_id=str(row[0]), occurred_at=row[1], correlation_id=row[2],
                tenant_id=row[3], user_id=row[4], operation=row[5], provider=row[6],
                model=row[7], status=row[8], input_tokens=row[9], output_tokens=row[10],
                total_tokens=row[11], estimated_cost_usd=row[12], latency_ms=row[13],
                grounded=row[14], citation_count=row[15], reflection_attempts=row[16],
            )
            for row in rows
        ]


@dataclass(slots=True)
class PostgresArtifactRepository:
    dsn: str

    async def save(self, draft: ArtifactDraft) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            await connection.execute(
                """
                INSERT INTO artifact_drafts (artifact_id, tenant_id, status, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                WHERE artifact_drafts.tenant_id = EXCLUDED.tenant_id
                """,
                (draft.artifact_id, draft.tenant_id, draft.status, draft.model_dump_json()),
            )

    async def get(self, artifact_id: str) -> ArtifactDraft:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                "SELECT payload FROM artifact_drafts WHERE artifact_id = %s",
                (artifact_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ArtifactPolicyError("artifact draft was not found")
        return ArtifactDraft.model_validate(row[0])


@dataclass(slots=True)
class PostgresSqlProposalRepository:
    dsn: str

    async def save(self, proposal: SqlProposal) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            await connection.execute(
                """
                INSERT INTO sql_proposals (proposal_id, tenant_id, status, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (proposal_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                WHERE sql_proposals.tenant_id = EXCLUDED.tenant_id
                """,
                (
                    proposal.proposal_id,
                    proposal.tenant_id,
                    proposal.status,
                    proposal.model_dump_json(),
                ),
            )

    async def get(self, proposal_id: str) -> SqlProposal:
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                "SELECT payload FROM sql_proposals WHERE proposal_id = %s",
                (proposal_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise SqlApprovalError("SQL proposal was not found")
        return SqlProposal.model_validate(row[0])
