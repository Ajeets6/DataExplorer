import asyncio
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from dataexplorer.models import AccessContext
from redis.asyncio import Redis


class UnsafeQueryError(ValueError):
    """Raised when a deterministic input policy blocks a query."""


class UnsafeDocumentError(ValueError):
    """Raised when active instructions are detected in an ingested document."""


class RateLimitError(ValueError):
    pass


class TokenBudgetError(ValueError):
    pass


_HIGH_CONFIDENCE_INJECTION_PATTERNS = (
    re.compile(r"\bignore (all|any|the|your) (previous|prior|system) instructions\b", re.I),
    re.compile(r"\breveal (the )?(system|developer) prompt\b", re.I),
    re.compile(r"\b(system|developer) message\s*:\s*", re.I),
)
_EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"
)


def validate_query(question: str, *, maximum_characters: int) -> str:
    normalized = " ".join(question.split())
    if not normalized:
        raise UnsafeQueryError("query cannot be empty")
    if len(normalized) > maximum_characters:
        raise UnsafeQueryError("query exceeds the configured size limit")
    if _contains_injection(normalized):
        raise UnsafeQueryError("query was blocked by the input safety policy")
    return normalized


def validate_document_content(text: str) -> None:
    if _contains_injection(text):
        raise UnsafeDocumentError(
            "document contains active instruction patterns and requires quarantine review"
        )


def redact_sensitive_text(text: str) -> str:
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _CARD.sub("[REDACTED_PAYMENT_CARD]", text)
    return _SECRET.sub("[REDACTED_SECRET]", text)


def _contains_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _HIGH_CONFIDENCE_INJECTION_PATTERNS)


@dataclass(slots=True)
class InMemoryPolicyEnforcer:
    """Process-local limiter; production swaps this contract for atomic Redis scripts."""

    requests_per_minute: int = 20
    daily_token_budget: int = 100_000
    _requests: dict[tuple[str, str], deque[float]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    _tokens: dict[tuple[date, str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enforce(self, access: AccessContext, text: str) -> None:
        now = time.monotonic()
        request_key = (access.tenant_id, access.user_id)
        estimated_tokens = max(1, len(text) // 4)
        token_key = (datetime.now(UTC).date(), *request_key)
        async with self._lock:
            timestamps = self._requests[request_key]
            while timestamps and timestamps[0] <= now - 60:
                timestamps.popleft()
            if len(timestamps) >= self.requests_per_minute:
                raise RateLimitError("request rate limit exceeded")
            if self._tokens[token_key] + estimated_tokens > self.daily_token_budget:
                raise TokenBudgetError("daily token budget exceeded")
            timestamps.append(now)
            self._tokens[token_key] += estimated_tokens


@dataclass(slots=True)
class RedisPolicyEnforcer:
    redis: Redis
    requests_per_minute: int = 20
    daily_token_budget: int = 100_000
    key_prefix: str = "dataexplorer"

    _SCRIPT = """
local requests = redis.call('INCR', KEYS[1])
if requests == 1 then redis.call('EXPIRE', KEYS[1], 60) end
if requests > tonumber(ARGV[1]) then return 1 end
local tokens = redis.call('INCRBY', KEYS[2], ARGV[3])
if tokens == tonumber(ARGV[3]) then redis.call('EXPIRE', KEYS[2], ARGV[4]) end
if tokens > tonumber(ARGV[2]) then return 2 end
return 0
"""

    async def enforce(self, access: AccessContext, text: str) -> None:
        estimated_tokens = max(1, len(text) // 4)
        now = datetime.now(UTC)
        identity = f"{access.tenant_id}:{access.user_id}"
        request_key = f"{self.key_prefix}:rate:{identity}:{int(now.timestamp()) // 60}"
        budget_key = f"{self.key_prefix}:tokens:{identity}:{now.date().isoformat()}"
        seconds_to_tomorrow = int(
            (datetime.combine(now.date(), datetime.min.time(), UTC).timestamp() + 86400)
            - now.timestamp()
        )
        result = await self.redis.eval(
            self._SCRIPT,
            2,
            request_key,
            budget_key,
            self.requests_per_minute,
            self.daily_token_budget,
            estimated_tokens,
            max(seconds_to_tomorrow, 1),
        )
        if result == 1:
            raise RateLimitError("request rate limit exceeded")
        if result == 2:
            raise TokenBudgetError("daily token budget exceeded")
