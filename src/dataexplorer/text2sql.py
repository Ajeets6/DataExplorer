import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from dataexplorer.models import (
    AccessContext,
    SqlProposal,
    SqlProposalIn,
    SqlQueryResult,
)
from dataexplorer.cloud_providers import ProviderContext
from dataexplorer.providers import ModelProvider
from dataexplorer.security import redact_sensitive_text, validate_query


class SqlPolicyError(ValueError):
    pass


class SqlApprovalError(ValueError):
    pass


class SqlExecutionError(RuntimeError):
    pass


class SchemaPolicy(BaseModel):
    schema_name: str
    tables: dict[str, frozenset[str]]
    allowed_groups: frozenset[str] = Field(default_factory=frozenset)
    approver_group: str = "data-approvers"
    maximum_rows: int = Field(default=500, ge=1, le=10_000)
    maximum_plan_cost: float = Field(default=100_000, gt=0)
    statement_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)
    allowed_functions: frozenset[str] = Field(
        default_factory=lambda: frozenset(
            {"avg", "cast", "coalesce", "count", "date_trunc", "max", "min", "round", "sum"}
        )
    )


class SqlExecutor(Protocol):
    async def execute_readonly(
        self,
        *,
        sql: str,
        access: AccessContext,
        policy: SchemaPolicy,
    ) -> tuple[list[str], list[dict[str, Any]], bool, dict[str, Any]]: ...


class DisabledSqlExecutor:
    async def execute_readonly(
        self,
        *,
        sql: str,
        access: AccessContext,
        policy: SchemaPolicy,
    ) -> tuple[list[str], list[dict[str, Any]], bool, dict[str, Any]]:
        del sql, access, policy
        raise SqlExecutionError("the SQL executor is not configured")


@dataclass(slots=True)
class PostgresReadOnlyExecutor:
    dsn: str

    async def execute_readonly(
        self,
        *,
        sql: str,
        access: AccessContext,
        policy: SchemaPolicy,
    ) -> tuple[list[str], list[dict[str, Any]], bool, dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        async with await psycopg.AsyncConnection.connect(
            self.dsn,
            autocommit=False,
            row_factory=dict_row,
        ) as connection:
            async with connection.transaction():
                await connection.execute("SET TRANSACTION READ ONLY")
                await connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(policy.statement_timeout_ms),),
                )
                await connection.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (access.tenant_id,),
                )
                explain_cursor = await connection.execute(
                    f"EXPLAIN (FORMAT JSON) {sql}"
                )
                explain_row = await explain_cursor.fetchone()
                plan = explain_row["QUERY PLAN"][0]["Plan"]
                total_cost = float(plan["Total Cost"])
                if total_cost > policy.maximum_plan_cost:
                    raise SqlExecutionError("query plan exceeds the approved cost limit")
                cursor = await connection.execute(sql)
                raw_rows = await cursor.fetchmany(policy.maximum_rows + 1)
                truncated = len(raw_rows) > policy.maximum_rows
                raw_rows = raw_rows[: policy.maximum_rows]
                columns = [column.name for column in cursor.description or []]
                rows = [
                    {
                        key: redact_sensitive_text(value) if isinstance(value, str) else value
                        for key, value in row.items()
                    }
                    for row in raw_rows
                ]
                return columns, rows, truncated, {"total_cost": total_cost}


@dataclass(slots=True)
class InMemorySqlProposalRepository:
    proposals: dict[str, SqlProposal] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def save(self, proposal: SqlProposal) -> None:
        async with self._lock:
            self.proposals[proposal.proposal_id] = proposal

    async def get(self, proposal_id: str) -> SqlProposal:
        async with self._lock:
            proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise SqlApprovalError("SQL proposal was not found")
        return proposal


@dataclass(slots=True)
class Text2SqlService:
    provider: ModelProvider
    executor: SqlExecutor = field(default_factory=DisabledSqlExecutor)
    schemas: dict[str, SchemaPolicy] = field(default_factory=dict)
    repository: InMemorySqlProposalRepository = field(
        default_factory=InMemorySqlProposalRepository
    )

    async def propose(
        self,
        request: SqlProposalIn,
        access: AccessContext,
    ) -> SqlProposal:
        question = validate_query(request.question, maximum_characters=8_000)
        policy = self._policy(request.schema_name, access)
        schema = "\n".join(
            f"{table}({', '.join(sorted(columns))})"
            for table, columns in sorted(policy.tables.items())
        )
        generated = await self.provider.generate(
            system=(
                "Generate exactly one PostgreSQL SELECT statement. Do not use markdown, "
                "comments, wildcard columns, DDL, DML, or system catalogs. Use only the "
                "provided schema. The query will be independently validated and reviewed."
            ),
            prompt=f"Authorized schema:\n{schema}\n\nBusiness question: {question}",
            policy=ProviderContext(
                tenant_id=access.tenant_id,
                user_id=access.user_id,
                classification="confidential",
            ),
        )
        sql = validate_and_bound_sql(
            _extract_sql(generated.text),
            policy,
        )
        proposal = SqlProposal(
            question=question,
            schema_name=request.schema_name,
            sql=sql,
            requested_by=access.user_id,
            tenant_id=access.tenant_id,
        )
        await self.repository.save(proposal)
        return proposal

    async def decide(
        self,
        proposal_id: str,
        *,
        approved: bool,
        reason: str,
        access: AccessContext,
    ) -> SqlProposal:
        proposal = await self.repository.get(proposal_id)
        policy = self._policy(proposal.schema_name, access)
        self._same_tenant(proposal, access)
        if policy.approver_group not in access.groups:
            raise SqlApprovalError("the caller is not an authorized SQL approver")
        if proposal.requested_by == access.user_id:
            raise SqlApprovalError("requesters cannot approve their own SQL")
        if proposal.status != "pending":
            raise SqlApprovalError("only pending SQL can be reviewed")
        updated = proposal.model_copy(
            update={
                "status": "approved" if approved else "rejected",
                "approved_by": access.user_id,
                "approval_reason": reason,
            }
        )
        await self.repository.save(updated)
        return updated

    async def execute(
        self,
        proposal_id: str,
        access: AccessContext,
    ) -> SqlQueryResult:
        proposal = await self.repository.get(proposal_id)
        self._same_tenant(proposal, access)
        if proposal.status != "approved":
            raise SqlApprovalError("SQL must be approved before execution")
        policy = self._policy(proposal.schema_name, access)
        sql = validate_and_bound_sql(proposal.sql, policy)
        columns, rows, truncated, execution_lineage = await self.executor.execute_readonly(
            sql=sql,
            access=access,
            policy=policy,
        )
        updated = proposal.model_copy(update={"status": "executed"})
        await self.repository.save(updated)
        return SqlQueryResult(
            proposal_id=proposal.proposal_id,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            lineage={
                "schema_name": proposal.schema_name,
                "requested_by": proposal.requested_by,
                "approved_by": proposal.approved_by,
                "tenant_id": proposal.tenant_id,
                **execution_lineage,
            },
        )

    def _policy(self, schema_name: str, access: AccessContext) -> SchemaPolicy:
        policy = self.schemas.get(schema_name)
        if policy is None:
            raise SqlPolicyError("the requested schema is not configured")
        if policy.allowed_groups and policy.allowed_groups.isdisjoint(access.groups):
            raise SqlPolicyError("the caller is not authorized for this schema")
        return policy

    @staticmethod
    def _same_tenant(proposal: SqlProposal, access: AccessContext) -> None:
        if proposal.tenant_id != access.tenant_id:
            raise SqlApprovalError("SQL proposal belongs to another tenant")


def validate_and_bound_sql(sql: str, policy: SchemaPolicy) -> str:
    if ";" in sql.rstrip().rstrip(";") or "--" in sql or "/*" in sql:
        raise SqlPolicyError("multiple statements and comments are forbidden")
    try:
        statements = parse(sql, read="postgres")
    except ParseError as error:
        raise SqlPolicyError("SQL could not be parsed") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SqlPolicyError("only one SELECT statement is allowed")
    expression = statements[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command)
    if any(expression.find(kind) is not None for kind in forbidden):
        raise SqlPolicyError("SQL contains a forbidden operation")
    if expression.find(exp.Star) is not None:
        raise SqlPolicyError("wildcard columns are forbidden")
    if expression.args.get("into") is not None:
        raise SqlPolicyError("SELECT INTO is forbidden")
    if expression.find(exp.Lock) is not None:
        raise SqlPolicyError("locking SELECT statements are forbidden")
    table_expressions = list(expression.find_all(exp.Table))
    if any(table.db or table.catalog for table in table_expressions):
        raise SqlPolicyError("schema-qualified and catalog-qualified tables are forbidden")
    tables = {table.name for table in table_expressions}
    if not tables or not tables.issubset(policy.tables):
        raise SqlPolicyError("SQL references a table outside the allowlist")
    allowed_columns = set().union(*(policy.tables[table] for table in tables))
    aliases = {table.alias_or_name: table.name for table in table_expressions}
    referenced_columns = set()
    for column in expression.find_all(exp.Column):
        referenced_columns.add(column.name)
        if column.table:
            actual_table = aliases.get(column.table)
            if actual_table is None or column.name not in policy.tables[actual_table]:
                raise SqlPolicyError("SQL references a qualified column outside the allowlist")
    if not referenced_columns.issubset(allowed_columns):
        raise SqlPolicyError("SQL references a column outside the allowlist")
    functions = {
        function.sql_name().lower()
        for function in expression.find_all(exp.Func)
    }
    if not functions.issubset(policy.allowed_functions):
        raise SqlPolicyError("SQL references a function outside the allowlist")
    current_limit = expression.args.get("limit")
    if current_limit is not None:
        limit_expression = current_limit.expression
        if not isinstance(limit_expression, exp.Literal) or not limit_expression.is_int:
            raise SqlPolicyError("SQL LIMIT must be a fixed integer")
        if int(limit_expression.this) > policy.maximum_rows:
            expression.set("limit", exp.Limit(expression=exp.Literal.number(policy.maximum_rows)))
    else:
        expression = expression.limit(policy.maximum_rows)
    return expression.sql(dialect="postgres")


def _extract_sql(text: str) -> str:
    fenced = re.fullmatch(r"\s*```(?:sql)?\s*(.*?)\s*```\s*", text, re.I | re.S)
    return (fenced.group(1) if fenced else text).strip()
