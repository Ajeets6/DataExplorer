import pytest

from dataexplorer.models import AccessContext
from dataexplorer.security import RateLimitError, RedisPolicyEnforcer, TokenBudgetError


class FakeRedis:
    def __init__(self, result: int) -> None:
        self.result = result
        self.keys: tuple[str, str] | None = None

    async def eval(self, script: str, key_count: int, *args):
        assert "INCRBY" in script
        assert key_count == 2
        self.keys = (args[0], args[1])
        return self.result


async def test_redis_policy_uses_tenant_scoped_keys() -> None:
    redis = FakeRedis(0)
    enforcer = RedisPolicyEnforcer(redis=redis)  # type: ignore[arg-type]
    await enforcer.enforce(AccessContext(user_id="u1", tenant_id="acme"), "hello")
    assert redis.keys is not None
    assert all("acme:u1" in key for key in redis.keys)


@pytest.mark.parametrize(
    ("result", "error"),
    [(1, RateLimitError), (2, TokenBudgetError)],
)
async def test_redis_policy_maps_atomic_script_decisions(result: int, error: type[Exception]) -> None:
    enforcer = RedisPolicyEnforcer(redis=FakeRedis(result))  # type: ignore[arg-type]
    with pytest.raises(error):
        await enforcer.enforce(AccessContext(user_id="u1", tenant_id="acme"), "hello")
