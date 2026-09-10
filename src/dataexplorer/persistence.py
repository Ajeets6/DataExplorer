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

    async def query_events(self, tenant_id, start, end, search="", offset=0, limit=26):
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT event_id, occurred_at, correlation_id, action, decision, user_id, tenant_id, metadata
                   FROM audit_events WHERE tenant_id=%s AND occurred_at >= %s AND occurred_at < %s
                   AND position(lower(%s) in lower(user_id || ' ' || action || ' ' || decision || ' ' || correlation_id)) > 0
                   ORDER BY occurred_at DESC, event_id DESC LIMIT %s OFFSET %s""",
                (tenant_id, start, end, search, limit, offset),
            )
            columns = [c.name for c in cursor.description]
            return [AuditEvent.model_validate(dict(zip(columns, row))) for row in await cursor.fetchall()]

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

    async def query_traces(self, tenant_id, start, end, provider="", model=""):
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT trace_id, occurred_at, correlation_id, tenant_id, user_id,
                          operation, provider, model, status, input_tokens, output_tokens,
                          total_tokens, estimated_cost_usd, latency_ms, grounded, citation_count, reflection_attempts
                   FROM llm_traces WHERE tenant_id=%s AND occurred_at >= %s AND occurred_at < %s
                   AND (%s='' OR provider=%s) AND (%s='' OR model=%s)
                   ORDER BY occurred_at DESC, trace_id DESC""",
                (tenant_id, start, end, provider, provider, model, model),
            )
            columns = [c.name for c in cursor.description]
            return [LlmTraceEvent.model_validate(dict(zip(columns, row))) for row in await cursor.fetchall()]

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

    async def summary(self, access, approver):
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT count(*) FILTER (WHERE payload->>'requested_by'=%s),
                   count(*) FILTER (WHERE %s AND payload->>'requested_by'<>%s AND status='pending')
                   FROM artifact_drafts WHERE tenant_id=%s""",
                (access.user_id, approver, access.user_id, access.tenant_id),
            )
            row = await cursor.fetchone()
            return {"my_reports": row[0], "awaiting_review": row[1]}

    async def list_for(self, access, *, approver=False, search="", offset=0, limit=25, status="", mine=False, review_queue=False):
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT payload FROM artifact_drafts WHERE tenant_id=%s
                   AND (%s OR payload->>'requested_by'=%s)
                   AND position(lower(%s) in lower(payload->'spec'->>'title')) > 0
                   AND (%s='' OR status=%s)
                   AND (NOT %s OR payload->>'requested_by'=%s)
                   AND (NOT %s OR (%s AND payload->>'requested_by'<>%s AND status='pending'))
                   ORDER BY payload->>'created_at' DESC, artifact_id DESC LIMIT %s OFFSET %s""",
                (access.tenant_id, approver, access.user_id, search, status, status,
                 mine, access.user_id, review_queue, approver, access.user_id, limit, offset),
            )
            return [ArtifactDraft.model_validate(row[0]) for row in await cursor.fetchall()]

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

    async def list_for(self, access, schemas):
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT payload FROM sql_proposals WHERE tenant_id=%s
                   AND payload->>'schema_name'=ANY(%s)
                   ORDER BY payload->>'created_at' DESC LIMIT 100""",
                (access.tenant_id, schemas),
            )
            return [SqlProposal.model_validate(row[0]) for row in await cursor.fetchall()]

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
