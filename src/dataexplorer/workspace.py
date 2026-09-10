"""Authorized business records and read APIs for the employee workspace."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from dataexplorer.models import AccessContext


class WorkspaceRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    owner: str
    kind: str
    title: str
    groups: list[str] = Field(default_factory=list)
    private: bool = False
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn
        self.records: dict[tuple[str, str, str], WorkspaceRecord] = {}

    async def save(self, record: WorkspaceRecord) -> None:
        if not self.dsn:
            self.records[(record.tenant_id, record.kind, record.record_id)] = record
            return
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            await connection.execute(
                """INSERT INTO workspace_records
                   (tenant_id, kind, record_id, owner_id, allowed_groups, private, title, payload, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (tenant_id,kind,record_id) DO UPDATE SET
                     owner_id=EXCLUDED.owner_id, allowed_groups=EXCLUDED.allowed_groups,
                     private=EXCLUDED.private, title=EXCLUDED.title, payload=EXCLUDED.payload""",
                (record.tenant_id, record.kind, record.record_id, record.owner,
                 record.groups, record.private, record.title, record.model_dump_json(), record.created_at),
            )

    async def list(self, kind: str, access: AccessContext, *, search: str = "",
                   offset: int = 0, limit: int = 50, record_id: str | None = None) -> list[WorkspaceRecord]:
        if not self.dsn:
            rows = [r for r in self.records.values()
                    if r.tenant_id == access.tenant_id and r.kind == kind
                    and (not r.private or r.owner == access.user_id)
                    and (not r.groups or bool(set(r.groups) & access.groups))
                    and search.casefold() in r.title.casefold()
                    and (record_id is None or r.record_id == record_id)]
            return sorted(rows, key=lambda r: (r.created_at, r.record_id), reverse=True)[offset:offset + limit]
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT payload FROM workspace_records
                   WHERE tenant_id=%s AND kind=%s AND (NOT private OR owner_id=%s)
                   AND (cardinality(allowed_groups)=0 OR allowed_groups && %s::text[])
                   AND position(lower(%s) in lower(title)) > 0
                   AND (%s::text IS NULL OR record_id=%s)
                   ORDER BY created_at DESC, record_id DESC LIMIT %s OFFSET %s""",
                (access.tenant_id, kind, access.user_id, list(access.groups), search,
                 record_id, record_id, limit, offset),
            )
            return [WorkspaceRecord.model_validate(row[0]) for row in await cursor.fetchall()]

    async def get(self, kind: str, record_id: str, access: AccessContext) -> WorkspaceRecord:
        rows = await self.list(kind, access, record_id=record_id, limit=1)
        if not rows:
            raise HTTPException(404, "Record not found or no longer accessible")
        return rows[0]

    async def count(self, kind: str, access: AccessContext) -> int:
        if not self.dsn:
            return len(await self.list(kind, access, limit=len(self.records)))
        async with await psycopg.AsyncConnection.connect(self.dsn) as connection:
            cursor = await connection.execute(
                """SELECT count(*) FROM workspace_records WHERE tenant_id=%s AND kind=%s
                   AND (NOT private OR owner_id=%s)
                   AND (cardinality(allowed_groups)=0 OR allowed_groups && %s::text[])""",
                (access.tenant_id, kind, access.user_id, list(access.groups)),
            )
            return (await cursor.fetchone())[0]


def register_workspace_routes(app, access_dependency) -> None:
    from fastapi import Query
    from dataexplorer.artifacts import ArtifactPolicyError

    @app.get("/v1/workspace/overview", tags=["workspace"])
    async def overview(request: Request, access: AccessContext = Depends(access_dependency)):
        store = request.app.state.workspace_store
        service = request.app.state.artifact_service
        totals = await service.repository.summary(access, service.approver_group in access.groups)
        return {**totals, "documents": await store.count("document", access),
                "saved_answers": await store.count("answer", access),
                "updated_at": datetime.now(UTC).isoformat()}

    @app.get("/v1/library/{kind}", tags=["workspace"])
    async def library(kind: str, request: Request, search: str = Query("", max_length=200),
                      offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                      access: AccessContext = Depends(access_dependency)):
        if kind not in {"document", "answer", "result"}:
            raise HTTPException(404, "Library not found")
        rows = await request.app.state.workspace_store.list(kind, access, search=search,
                                                             offset=offset, limit=limit + 1)
        return {"items": [r.model_dump(exclude={"payload", "groups", "tenant_id"}) for r in rows[:limit]],
                "has_more": len(rows) > limit, "offset": offset}

    @app.get("/v1/library/{kind}/{record_id}", tags=["workspace"])
    async def library_detail(kind: str, record_id: str, request: Request,
                             access: AccessContext = Depends(access_dependency)):
        if kind not in {"document", "answer", "result"}:
            raise HTTPException(404, "Library not found")
        record = await request.app.state.workspace_store.get(kind, record_id, access)
        if kind == "answer":
            for citation in record.payload.get("response", {}).get("citations", []):
                await request.app.state.workspace_store.get("document", citation["document_id"], access)
        return record

    async def reports(request, access, search="", offset=0, limit=25, status="", mine=False, review_queue=False):
        return await request.app.state.artifact_service.repository.list_for(
            access, approver=request.app.state.artifact_service.approver_group in access.groups,
            search=search, offset=offset, limit=limit, status=status, mine=mine, review_queue=review_queue)

    @app.get("/v1/artifacts", tags=["publishing"])
    async def report_library(request: Request, search: str = Query("", max_length=200),
                             offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
                             status: str = "", mine: bool = False, review_queue: bool = False,
                             access: AccessContext = Depends(access_dependency)):
        rows = await reports(request, access, search, offset, limit + 1, status, mine, review_queue)
        return {"items": [r.model_dump(exclude={"output_path", "qa_manifest_path"}) for r in rows[:limit]],
                "has_more": len(rows) > limit}

    async def get_report(request, artifact_id, access):
        try:
            draft = await request.app.state.artifact_service.repository.get(artifact_id)
        except ArtifactPolicyError:
            raise HTTPException(404, "Report not found") from None
        service = request.app.state.artifact_service
        if draft.tenant_id != access.tenant_id or (
            draft.requested_by != access.user_id and service.approver_group not in access.groups
        ):
            raise HTTPException(404, "Report not found")
        return draft

    @app.get("/v1/artifacts/{artifact_id}", tags=["publishing"])
    async def report_detail(artifact_id: str, request: Request,
                            access: AccessContext = Depends(access_dependency)):
        draft = await get_report(request, artifact_id, access)
        return draft.model_dump(exclude={"output_path", "qa_manifest_path"})

    @app.get("/v1/artifacts/{artifact_id}/download", tags=["publishing"])
    async def download_report(artifact_id: str, request: Request,
                              access: AccessContext = Depends(access_dependency)):
        draft = await get_report(request, artifact_id, access)
        if draft.status != "rendered" or not draft.output_path:
            raise HTTPException(409, "This report is not ready to download")
        service = request.app.state.artifact_service
        try:
            if draft.output_path.startswith("gs://"):
                if not service.publisher or not hasattr(service.publisher, "download"):
                    raise HTTPException(503, "Report storage is unavailable")
                data = await service.publisher.download(draft.output_path)
            else:
                path = Path(draft.output_path).resolve()
                expected = (service.output_root / draft.tenant_id / draft.artifact_id).resolve()
                if expected not in path.parents or service.output_root.resolve() not in expected.parents:
                    raise HTTPException(404, "Report file not found")
                data = path.read_bytes()
        except OSError:
            raise HTTPException(503, "Report file is temporarily unavailable") from None
        import hashlib
        if hashlib.sha256(data).hexdigest() != draft.output_sha256:
            raise HTTPException(409, "Report integrity check failed; contact the report owner")
        from dataexplorer.audit import make_audit_event
        await request.app.state.audit_sink.record(make_audit_event(
            access=access, correlation_id=request.state.correlation_id,
            action="artifact.download", decision="allowed", metadata={"artifact_id": artifact_id}))
        mime = "application/vnd.openxmlformats-officedocument."
        mime += "wordprocessingml.document" if draft.spec.kind == "docx" else "presentationml.presentation"
        return Response(data, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{draft.spec.filename}"'})

    @app.get("/v1/sql/proposals", tags=["structured-data"])
    async def analysis_library(request: Request, access: AccessContext = Depends(access_dependency)):
        service = request.app.state.text2sql_service
        schemas = [name for name, policy in service.schemas.items()
                   if not policy.allowed_groups or policy.allowed_groups & access.groups]
        return await service.repository.list_for(access, schemas)

    @app.get("/v1/sql/schemas", tags=["structured-data"])
    async def schemas(request: Request, access: AccessContext = Depends(access_dependency)):
        return [{"name": name, "maximum_rows": policy.maximum_rows,
                 "can_approve": policy.approver_group in access.groups}
                for name, policy in request.app.state.text2sql_service.schemas.items()
                if not policy.allowed_groups or policy.allowed_groups & access.groups]
