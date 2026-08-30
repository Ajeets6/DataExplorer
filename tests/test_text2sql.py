from typing import Any

import pytest

from dataexplorer.models import AccessContext, SqlProposalIn
from dataexplorer.providers import GeneratedText
from dataexplorer.text2sql import (
    SchemaPolicy,
    SqlApprovalError,
    SqlPolicyError,
    Text2SqlService,
    validate_and_bound_sql,
)


class SqlProvider:
    def __init__(self, sql: str = "SELECT region, SUM(revenue) AS total FROM sales GROUP BY region") -> None:
        self.sql = sql

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    async def generate(self, *, system: str, prompt: str, policy=None) -> GeneratedText:
        assert "exactly one PostgreSQL SELECT" in system
        assert "sales" in prompt
        return GeneratedText(text=self.sql, provider="fake", model="sql")

    async def healthcheck(self) -> bool:
        return True


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_readonly(
        self,
        *,
        sql: str,
        access: AccessContext,
        policy: SchemaPolicy,
    ) -> tuple[list[str], list[dict[str, Any]], bool, dict[str, Any]]:
        self.calls += 1
        assert sql.endswith(f"LIMIT {policy.maximum_rows}")
        assert access.tenant_id == "acme"
        return ["region", "total"], [{"region": "APAC", "total": 42}], False, {"total_cost": 10}


def policy() -> SchemaPolicy:
    return SchemaPolicy(
        schema_name="finance",
        tables={"sales": {"region", "revenue", "tenant_id"}},
        allowed_groups={"finance"},
        approver_group="data-approvers",
        maximum_rows=100,
    )


def test_sql_validator_rejects_writes_wildcards_and_unknown_columns() -> None:
    rules = policy()
    with pytest.raises(SqlPolicyError):
        validate_and_bound_sql("DELETE FROM sales", rules)
    with pytest.raises(SqlPolicyError):
        validate_and_bound_sql("SELECT * FROM sales", rules)
    with pytest.raises(SqlPolicyError):
        validate_and_bound_sql("SELECT salary FROM sales", rules)
    with pytest.raises(SqlPolicyError):
        validate_and_bound_sql("SELECT region FROM other.sales", rules)
    with pytest.raises(SqlPolicyError):
        validate_and_bound_sql("SELECT pg_sleep(10), region FROM sales", rules)
    with pytest.raises(SqlPolicyError):
        validate_and_bound_sql("SELECT region INTO copied FROM sales", rules)


def test_sql_validator_applies_hard_row_limit() -> None:
    bounded = validate_and_bound_sql("SELECT region FROM sales", policy())
    assert bounded.endswith("LIMIT 100")
    bounded_existing = validate_and_bound_sql("SELECT region FROM sales LIMIT 1000", policy())
    assert bounded_existing.endswith("LIMIT 100")


async def test_sql_requires_independent_approval_before_execution() -> None:
    executor = FakeExecutor()
    service = Text2SqlService(
        provider=SqlProvider(),
        executor=executor,
        schemas={"finance": policy()},
    )
    requester = AccessContext(user_id="analyst", tenant_id="acme", groups={"finance"})
    approver = AccessContext(
        user_id="approver",
        tenant_id="acme",
        groups={"finance", "data-approvers"},
    )
    proposal = await service.propose(
        SqlProposalIn(question="Revenue by region", schema_name="finance"), requester
    )
    assert proposal.status == "pending"
    with pytest.raises(SqlApprovalError):
        await service.execute(proposal.proposal_id, requester)
    with pytest.raises(SqlApprovalError):
        await service.decide(
            proposal.proposal_id,
            approved=True,
            reason="self approval",
            access=requester.model_copy(update={"groups": {"finance", "data-approvers"}}),
        )
    approved = await service.decide(
        proposal.proposal_id,
        approved=True,
        reason="Validated metric and scope",
        access=approver,
    )
    assert approved.status == "approved"
    result = await service.execute(proposal.proposal_id, requester)
    assert result.rows == [{"region": "APAC", "total": 42}]
    assert result.lineage["approved_by"] == "approver"
    assert executor.calls == 1


async def test_cross_tenant_approver_cannot_access_proposal() -> None:
    service = Text2SqlService(
        provider=SqlProvider(),
        executor=FakeExecutor(),
        schemas={"finance": policy()},
    )
    requester = AccessContext(user_id="analyst", tenant_id="acme", groups={"finance"})
    proposal = await service.propose(
        SqlProposalIn(question="Revenue by region", schema_name="finance"), requester
    )
    outsider = AccessContext(
        user_id="approver",
        tenant_id="other",
        groups={"finance", "data-approvers"},
    )
    with pytest.raises(SqlApprovalError):
        await service.decide(
            proposal.proposal_id,
            approved=True,
            reason="Looks good",
            access=outsider,
        )
