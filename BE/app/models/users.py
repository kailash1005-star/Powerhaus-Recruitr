"""
User Document Model

Auth0 owns identity (credentials, MFA, SSO). This collection is the LOCAL
projection of it — the record other collections can point at, and the place to
hang app-specific state (tenant, roles, last seen) that Auth0 shouldn't carry.

Deliberately NOT stored here: passwords, MFA secrets, tokens. Auth0 holds those.
Anything we don't store is something we can't leak.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserModel(BaseModel):
    """A user, keyed on the Auth0 subject.

    `auth0Sub` (e.g. "auth0|65f3c2...", "google-oauth2|1179...") is the join key,
    not email. Email is mutable — people change them, and the same human can
    arrive via both a password login and Google SSO. `sub` is stable and unique
    per identity, which is what a foreign key needs to be.
    """

    auth0Sub: str = Field(description="Auth0 `sub` claim — the stable identity key")
    email: Optional[str] = Field(default=None, description="From the token; may be absent for some connections")
    emailVerified: bool = Field(default=False)
    name: Optional[str] = None
    picture: Optional[str] = None

    # Stamped by the post-login Action; "default" until Organizations are on.
    tenantId: str = Field(default="default")
    roles: list[str] = Field(default_factory=list)

    createdAt: datetime
    lastLoginAt: datetime
    loginCount: int = Field(default=0)

    # Soft-disable without deleting: lets a client cut access while keeping the
    # audit trail intact, which is the GDPR-friendlier answer for a hiring tool.
    isActive: bool = Field(default=True)
