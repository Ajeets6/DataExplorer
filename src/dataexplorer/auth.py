import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import jwt

from dataexplorer.models import AccessContext


class AuthenticationError(ValueError):
    pass


class Authenticator(Protocol):
    async def authenticate(
        self,
        *,
        authorization: str | None,
        development_user_id: str | None,
        development_tenant_id: str | None,
        development_groups: str,
    ) -> AccessContext: ...


@dataclass(frozen=True, slots=True)
class DevelopmentAuthenticator:
    async def authenticate(
        self,
        *,
        authorization: str | None,
        development_user_id: str | None,
        development_tenant_id: str | None,
        development_groups: str,
    ) -> AccessContext:
        del authorization
        if not development_user_id or not development_tenant_id:
            raise AuthenticationError("development identity headers are required")
        groups = frozenset(
            item.strip() for item in development_groups.split(",") if item.strip()
        )
        return AccessContext(
            user_id=development_user_id,
            tenant_id=development_tenant_id,
            groups=groups,
        )


@dataclass(slots=True)
class JwtAuthenticator:
    issuer: str
    audience: str
    jwks_url: str | None = None
    signing_key: str | bytes | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    _jwks_client: jwt.PyJWKClient | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.signing_key is None and self.jwks_url is None:
            raise ValueError("a signing key or JWKS URL is required")
        if self.jwks_url:
            self._jwks_client = jwt.PyJWKClient(self.jwks_url, cache_keys=True)

    async def authenticate(
        self,
        *,
        authorization: str | None,
        development_user_id: str | None,
        development_tenant_id: str | None,
        development_groups: str,
    ) -> AccessContext:
        del development_user_id, development_tenant_id, development_groups
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("a bearer token is required")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise AuthenticationError("a bearer token is required")
        try:
            key = self.signing_key
            if key is None and self._jwks_client is not None:
                signing_key = await asyncio.to_thread(
                    self._jwks_client.get_signing_key_from_jwt,
                    token,
                )
                key = signing_key.key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "tenant_id"]},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("the bearer token is invalid") from error
        groups_claim = claims.get("groups", [])
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise AuthenticationError("the token subject claim is invalid")
        if not isinstance(claims.get("tenant_id"), str) or not claims["tenant_id"]:
            raise AuthenticationError("the token tenant claim is invalid")
        if not isinstance(groups_claim, list) or not all(
            isinstance(group, str) for group in groups_claim
        ):
            raise AuthenticationError("the token groups claim is invalid")
        return AccessContext(
            user_id=claims["sub"],
            tenant_id=claims["tenant_id"],
            groups=frozenset(groups_claim),
        )
