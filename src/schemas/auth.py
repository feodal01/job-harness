from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Provider = Literal["github", "google"]


class AuthUser(BaseModel):
    id: UUID
    name: str
    email: str | None = None


class AuthStatus(BaseModel):
    authenticated: bool
    user: AuthUser | None = None
    login_url: str | None = None


class AuthProvider(BaseModel):
    id: Provider
    name: str


class AuthProviders(BaseModel):
    providers: list[AuthProvider]


class StartLoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider


class LoginRedirect(BaseModel):
    url: str
