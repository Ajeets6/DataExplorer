from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from dataexplorer.audit import AuditSink, InMemoryAuditSink, make_audit_event
from dataexplorer.artifacts import (
    ArtifactPolicyError,
    ArtifactRenderError,
    ArtifactService,
    ArtifactToolPptxRenderer,
    DocxRenderer,
    GcsArtifactPublisher,
)
from dataexplorer.auth import (
    AuthenticationError,
    Authenticator,
    DevelopmentAuthenticator,
    JwtAuthenticator,
)
from dataexplorer.config import Settings, get_settings
from dataexplorer.models import (
    AccessContext,
    ArtifactApprovalIn,
    ArtifactDraft,
    ArtifactSpec,
    DocumentIn,
    IngestResponse,
    QueryIn,
    QueryResponse,
    SqlApprovalIn,
    SqlProposal,
    SqlProposalIn,
    SqlQueryResult,
    WorkspaceContext,
)
from dataexplorer.observability import (
    InMemoryLlmTraceStore,
    LlmTraceEvent,
    LlmTraceStore,
    MetricsRegistry,
    PricingCatalog,
    summarize_traces,
    observe_request,
)
from dataexplorer.persistence import (
    PostgresArtifactRepository,
    PostgresAuditSink,
    PostgresLlmTraceStore,
    PostgresSqlProposalRepository,
)
from dataexplorer.cloud_providers import (
    AnthropicProvider,
    OpenAIProvider,
    PolicyRouter,
    ProviderPolicyError,
    ProviderRoute,
)
from dataexplorer.providers import ModelProvider, OllamaProvider
from dataexplorer.qdrant_retrieval import QdrantHybridRetriever
from dataexplorer.retrieval import FlashRankReranker, InMemoryRetriever, LexicalReranker
from dataexplorer.security import (
    InMemoryPolicyEnforcer,
    RateLimitError,
    TokenBudgetError,
    UnsafeDocumentError,
    UnsafeQueryError,
    RedisPolicyEnforcer,
)
from dataexplorer.service import RagService
from dataexplorer.text2sql import (
    DisabledSqlExecutor,
    InMemorySqlProposalRepository,
    PostgresReadOnlyExecutor,
    SqlApprovalError,
    SqlExecutionError,
    SqlPolicyError,
    Text2SqlService,
)


def build_service(settings: Settings) -> RagService:
    ollama = OllamaProvider(
        base_url=str(settings.ollama_base_url).rstrip("/"),
        chat_model=settings.ollama_chat_model,
        embedding_model=settings.ollama_embedding_model,
        timeout_seconds=settings.request_timeout_seconds,
    )
    routes = [
        ProviderRoute(
            name="ollama-local",
            provider=ollama,
            external=False,
            allowed_classifications=frozenset(
                {"public", "internal", "confidential", "restricted"}
            ),
        )
    ]
    embedding_provider: ModelProvider = ollama
    if settings.openai_api_key:
        openai = OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            chat_model=settings.openai_chat_model,
            embedding_model=settings.openai_embedding_model,
            base_url=str(settings.openai_base_url).rstrip("/"),
            timeout_seconds=settings.request_timeout_seconds,
        )
        routes.append(ProviderRoute(
            name="openai-managed",
            provider=openai,
            external=True,
            allowed_classifications=frozenset({"public", "internal"}),
        ))
        if settings.model_provider == "openai":
            routes = [routes[-1]]
            embedding_provider = openai
    if settings.anthropic_api_key:
        anthropic = AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            chat_model=settings.anthropic_chat_model,
            embedding_provider=embedding_provider,
            base_url=str(settings.anthropic_base_url).rstrip("/"),
            timeout_seconds=settings.request_timeout_seconds,
        )
        routes.append(ProviderRoute(
            name="anthropic-managed",
            provider=anthropic,
            external=True,
            allowed_classifications=frozenset({"public", "internal"}),
        ))
        if settings.model_provider == "anthropic":
            routes = [routes[-1]]
    if settings.model_provider == "ollama":
        routes = routes[:1]
    provider = PolicyRouter(routes=routes, embedding_provider=embedding_provider)
    retriever = InMemoryRetriever(
        reranker=(
            FlashRankReranker()
            if settings.reranker == "flashrank"
            else LexicalReranker()
        )
    )
    if settings.vector_backend == "qdrant" and settings.qdrant_url:
        retriever = QdrantHybridRetriever(
            client=AsyncQdrantClient(
                url=str(settings.qdrant_url).rstrip("/"),
                api_key=(
                    settings.qdrant_api_key.get_secret_value()
                    if settings.qdrant_api_key else None
                ),
                timeout=settings.request_timeout_seconds,
            ),
            collection_name=settings.qdrant_collection,
            reranker=(
                FlashRankReranker()
                if settings.reranker == "flashrank"
                else LexicalReranker()
            ),
        )
    return RagService(
        provider=provider,
        retriever=retriever,
        retrieval_limit=settings.retrieval_limit,
        minimum_relevance=settings.minimum_relevance,
        max_query_characters=settings.max_query_characters,
        deployment_region=settings.deployment_region,
    )


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "development":
        return DevelopmentAuthenticator()
    return JwtAuthenticator(
        jwks_url=str(settings.oidc_jwks_url),
        issuer=settings.oidc_issuer or "",
        audience=settings.oidc_audience or "",
    )


def build_artifact_service(settings: Settings) -> ArtifactService:
    renderers = {"docx": DocxRenderer()}
    if settings.artifact_tool_node and settings.artifact_tool_node_modules:
        renderers["pptx"] = ArtifactToolPptxRenderer(
            node_executable=settings.artifact_tool_node,
            node_modules=settings.artifact_tool_node_modules,
            script_path=Path(__file__).with_name("pptx_renderer.mjs"),
        )
    service = ArtifactService(
        output_root=settings.artifact_output_root,
        renderers=renderers,
        approver_group=settings.artifact_approver_group,
        publisher=(
            GcsArtifactPublisher(
                bucket_name=settings.artifact_gcs_bucket,
                kms_key_name=settings.artifact_gcs_kms_key,
            )
            if settings.artifact_gcs_bucket
            else None
        ),
    )
    if settings.persistence_mode == "postgres" and settings.database_dsn:
        service.repository = PostgresArtifactRepository(
            settings.database_dsn.get_secret_value()
        )
    return service


def create_app(
    service: RagService | None = None,
    *,
    authenticator: Authenticator | None = None,
    policy_enforcer: InMemoryPolicyEnforcer | None = None,
    audit_sink: AuditSink | None = None,
    text2sql_service: Text2SqlService | None = None,
    artifact_service: ArtifactService | None = None,
    trace_store: LlmTraceStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        application.state.rag_service = service or build_service(settings)
        application.state.authenticator = authenticator or build_authenticator(settings)
        application.state.policy_enforcer = policy_enforcer or (
            RedisPolicyEnforcer(
                redis=Redis.from_url(settings.redis_url.get_secret_value()),
                requests_per_minute=settings.requests_per_minute,
                daily_token_budget=settings.daily_token_budget,
            )
            if settings.policy_backend == "redis" and settings.redis_url
            else InMemoryPolicyEnforcer(
                requests_per_minute=settings.requests_per_minute,
                daily_token_budget=settings.daily_token_budget,
            )
        )
        database_dsn = (
            settings.database_dsn.get_secret_value() if settings.database_dsn else None
        )
        application.state.audit_sink = audit_sink or (
            PostgresAuditSink(database_dsn)
            if settings.persistence_mode == "postgres" and database_dsn
            else InMemoryAuditSink()
        )
        application.state.trace_store = trace_store or (
            PostgresLlmTraceStore(database_dsn)
            if settings.persistence_mode == "postgres" and database_dsn
            else InMemoryLlmTraceStore()
        )
        application.state.pricing = PricingCatalog(
            input_per_million_usd=settings.llm_input_cost_per_million_usd,
            output_per_million_usd=settings.llm_output_cost_per_million_usd,
        )
        application.state.auth_mode = settings.auth_mode
        application.state.observability_admin_groups = settings.observability_admin_groups
        application.state.text2sql_service = text2sql_service or Text2SqlService(
            provider=application.state.rag_service.provider,
            executor=(
                PostgresReadOnlyExecutor(database_dsn)
                if settings.persistence_mode == "postgres" and database_dsn
                else DisabledSqlExecutor()
            ),
            repository=(
                PostgresSqlProposalRepository(database_dsn)
                if settings.persistence_mode == "postgres" and database_dsn
                else InMemorySqlProposalRepository()
            ),
        )
        application.state.artifact_service = artifact_service or build_artifact_service(
            settings
        )
        application.state.metrics = MetricsRegistry()
        try:
            yield
        finally:
            if isinstance(application.state.policy_enforcer, RedisPolicyEnforcer):
                await application.state.policy_enforcer.redis.aclose()
            retriever = application.state.rag_service.retriever
            if isinstance(retriever, QdrantHybridRetriever):
                await retriever.client.close()

    application = FastAPI(
        title="Data Explorer",
        version="0.3.0",
        description="Governed enterprise retrieval-augmented generation API",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def observability_middleware(request: Request, call_next):
        registry = getattr(request.app.state, "metrics", None)
        if registry is None:
            return await call_next(request)
        return await observe_request(request, call_next, registry)

    @application.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        request.state.correlation_id = correlation_id[:200]
        response: Response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @application.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"])
    async def ready(request: Request) -> dict[str, str]:
        if not await _service(request).ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="model provider is unavailable",
            )
        return {"status": "ready"}

    @application.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        return Response(
            request.app.state.metrics.prometheus(),
            media_type="text/plain; version=0.0.4",
        )

    @application.get(
        "/v1/workspace/me", response_model=WorkspaceContext, tags=["workspace"]
    )
    async def workspace_context(
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> WorkspaceContext:
        return WorkspaceContext(
            user_id=access.user_id,
            tenant_id=access.tenant_id,
            groups=sorted(access.groups),
            auth_mode=request.app.state.auth_mode,
            can_observe=_is_observability_admin(request, access),
        )

    @application.post(
        "/v1/documents",
        response_model=IngestResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def ingest_document(
        document: DocumentIn,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> IngestResponse:
        await _enforce(request, access, document.text)
        try:
            result = await _service(request).ingest(document, access)
        except UnsafeDocumentError as error:
            await _audit(request, access, "document.ingest", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
        await _audit(
            request,
            access,
            "document.ingest",
            "allowed",
            {"document_id": document.document_id, "chunk_count": result.chunk_count},
        )
        return result

    @application.post("/v1/query", response_model=QueryResponse, tags=["query"])
    async def query(
        payload: QueryIn,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> QueryResponse:
        await _enforce(request, access, payload.question)
        started = time.perf_counter()
        try:
            result = await _service(request).answer(payload.question, access)
        except UnsafeQueryError as error:
            await _audit(request, access, "rag.query", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
        except ProviderPolicyError as error:
            await _audit(request, access, "rag.query", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        await _audit(
            request,
            access,
            "rag.query",
            "allowed",
            {
                "grounded": result.grounded,
                "citation_count": len(result.citations),
                "model_provider": result.model_provider,
                "model_name": result.model_name,
            },
        )
        if result.model_provider != "none":
            cost = request.app.state.pricing.estimate(
                result.model_provider,
                result.model_name,
                result.input_tokens,
                result.output_tokens,
            )
            await request.app.state.trace_store.record(
                LlmTraceEvent(
                    correlation_id=request.state.correlation_id,
                    tenant_id=access.tenant_id,
                    user_id=access.user_id,
                    operation="rag.query",
                    provider=result.model_provider,
                    model=result.model_name,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    total_tokens=result.input_tokens + result.output_tokens,
                    estimated_cost_usd=cost,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    grounded=result.grounded,
                    citation_count=len(result.citations),
                    reflection_attempts=result.reflection_attempts,
                )
            )
        return result

    @application.get("/v1/observability/summary", tags=["observability"])
    async def observability_summary(
        request: Request,
        access: AccessContext = Depends(_access_context),
    ):
        _require_observability_admin(request, access)
        traces = await request.app.state.trace_store.list_traces(access.tenant_id, 10_000)
        return summarize_traces(traces)

    @application.get("/v1/observability/traces", tags=["observability"])
    async def observability_traces(
        request: Request,
        limit: int = 100,
        access: AccessContext = Depends(_access_context),
    ):
        _require_observability_admin(request, access)
        bounded_limit = max(1, min(limit, 500))
        return await request.app.state.trace_store.list_traces(
            access.tenant_id, bounded_limit
        )

    @application.get("/v1/observability/audit", tags=["observability"])
    async def observability_audit(
        request: Request,
        limit: int = 100,
        access: AccessContext = Depends(_access_context),
    ):
        _require_observability_admin(request, access)
        bounded_limit = max(1, min(limit, 500))
        return await request.app.state.audit_sink.list_events(
            access.tenant_id, bounded_limit
        )

    @application.get("/v1/observability/users", tags=["observability"])
    async def observability_users(
        request: Request,
        access: AccessContext = Depends(_access_context),
    ):
        _require_observability_admin(request, access)
        traces = await request.app.state.trace_store.list_traces(access.tenant_id, 10_000)
        users: dict[str, dict[str, object]] = {}
        for trace in traces:
            item = users.setdefault(trace.user_id, {
                "user_id": trace.user_id,
                "requests": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "last_seen": trace.occurred_at,
            })
            item["requests"] = int(item["requests"]) + 1
            item["total_tokens"] = int(item["total_tokens"]) + trace.total_tokens
            item["estimated_cost_usd"] = round(
                float(item["estimated_cost_usd"]) + (trace.estimated_cost_usd or 0), 8
            )
            if trace.occurred_at > item["last_seen"]:
                item["last_seen"] = trace.occurred_at
        return list(users.values())

    @application.post(
        "/v1/sql/proposals",
        response_model=SqlProposal,
        status_code=status.HTTP_201_CREATED,
        tags=["structured-data"],
    )
    async def propose_sql(
        payload: SqlProposalIn,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> SqlProposal:
        await _enforce(request, access, payload.question)
        try:
            proposal = await _text2sql(request).propose(payload, access)
        except (SqlPolicyError, UnsafeQueryError, ProviderPolicyError) as error:
            await _audit(request, access, "sql.propose", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        await _audit(
            request,
            access,
            "sql.propose",
            "pending_approval",
            {"proposal_id": proposal.proposal_id, "schema_name": proposal.schema_name},
        )
        return proposal

    @application.post(
        "/v1/sql/proposals/{proposal_id}/decision",
        response_model=SqlProposal,
        tags=["structured-data"],
    )
    async def decide_sql(
        proposal_id: str,
        payload: SqlApprovalIn,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> SqlProposal:
        try:
            proposal = await _text2sql(request).decide(
                proposal_id,
                approved=payload.approved,
                reason=payload.reason,
                access=access,
            )
        except (SqlPolicyError, SqlApprovalError) as error:
            await _audit(request, access, "sql.decide", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        await _audit(
            request,
            access,
            "sql.decide",
            proposal.status,
            {"proposal_id": proposal.proposal_id},
        )
        return proposal

    @application.post(
        "/v1/sql/proposals/{proposal_id}/execute",
        response_model=SqlQueryResult,
        tags=["structured-data"],
    )
    async def execute_sql(
        proposal_id: str,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> SqlQueryResult:
        try:
            result = await _text2sql(request).execute(proposal_id, access)
        except (SqlPolicyError, SqlApprovalError) as error:
            await _audit(request, access, "sql.execute", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except SqlExecutionError as error:
            await _audit(request, access, "sql.execute", "failed", {"reason": str(error)})
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        await _audit(
            request,
            access,
            "sql.execute",
            "allowed",
            {
                "proposal_id": proposal_id,
                "row_count": result.row_count,
                "truncated": result.truncated,
            },
        )
        return result

    @application.post(
        "/v1/artifacts",
        response_model=ArtifactDraft,
        status_code=status.HTTP_201_CREATED,
        tags=["publishing"],
    )
    async def create_artifact(
        payload: ArtifactSpec,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> ArtifactDraft:
        await _enforce(request, access, payload.model_dump_json())
        draft = await _artifacts(request).create(payload, access)
        await _audit(
            request,
            access,
            "artifact.create",
            "pending_approval",
            {"artifact_id": draft.artifact_id, "kind": payload.kind},
        )
        return draft

    @application.post(
        "/v1/artifacts/{artifact_id}/decision",
        response_model=ArtifactDraft,
        tags=["publishing"],
    )
    async def decide_artifact(
        artifact_id: str,
        payload: ArtifactApprovalIn,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> ArtifactDraft:
        try:
            draft = await _artifacts(request).decide(
                artifact_id,
                approved=payload.approved,
                reason=payload.reason,
                access=access,
            )
        except ArtifactPolicyError as error:
            await _audit(request, access, "artifact.decide", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        await _audit(
            request,
            access,
            "artifact.decide",
            draft.status,
            {"artifact_id": artifact_id},
        )
        return draft

    @application.post(
        "/v1/artifacts/{artifact_id}/render",
        response_model=ArtifactDraft,
        tags=["publishing"],
    )
    async def render_artifact(
        artifact_id: str,
        request: Request,
        access: AccessContext = Depends(_access_context),
    ) -> ArtifactDraft:
        try:
            draft = await _artifacts(request).render(artifact_id, access)
        except ArtifactPolicyError as error:
            await _audit(request, access, "artifact.render", "blocked", {"reason": str(error)})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except ArtifactRenderError as error:
            await _audit(request, access, "artifact.render", "failed", {"reason": str(error)})
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        await _audit(
            request,
            access,
            "artifact.render",
            "allowed",
            {"artifact_id": artifact_id, "sha256": draft.output_sha256},
        )
        return draft

    return application


def _service(request: Request) -> RagService:
    return request.app.state.rag_service


def _text2sql(request: Request) -> Text2SqlService:
    return request.app.state.text2sql_service


def _artifacts(request: Request) -> ArtifactService:
    return request.app.state.artifact_service


def _is_observability_admin(request: Request, access: AccessContext) -> bool:
    return bool(access.groups & request.app.state.observability_admin_groups)


def _require_observability_admin(request: Request, access: AccessContext) -> None:
    if not _is_observability_admin(request, access):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="observability administrator access is required",
        )


async def _access_context(
    request: Request,
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_groups: str = Header(default=""),
) -> AccessContext:
    try:
        return await request.app.state.authenticator.authenticate(
            authorization=authorization,
            development_user_id=x_user_id,
            development_tenant_id=x_tenant_id,
            development_groups=x_groups,
        )
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


async def _enforce(request: Request, access: AccessContext, text: str) -> None:
    try:
        await request.app.state.policy_enforcer.enforce(access, text)
    except RateLimitError as error:
        await _audit(request, access, "request.limit", "blocked", {"reason": str(error)})
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    except TokenBudgetError as error:
        await _audit(request, access, "request.budget", "blocked", {"reason": str(error)})
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error


async def _audit(
    request: Request,
    access: AccessContext,
    action: str,
    decision: str,
    metadata: dict[str, object],
) -> None:
    await request.app.state.audit_sink.record(
        make_audit_event(
            correlation_id=request.state.correlation_id,
            action=action,
            decision=decision,
            access=access,
            metadata=metadata,
        )
    )


app = create_app()
