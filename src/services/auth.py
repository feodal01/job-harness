"""One user session shared by browser and MCP, with serialized token rotation."""

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from src.auth.settings import AuthSettings
from src.auth.storage import Session, SessionStore
from src.core.errors import ApplicationError
from src.schemas.auth import AuthProvider, AuthProviders, AuthStatus, AuthUser, Provider

PROVIDERS: dict[Provider, str] = {"github": "GitHub", "google": "Google"}
FLOW_SECONDS = 600


class AuthError(ApplicationError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class LoginRequired(AuthError):
    def __init__(self, login_url: str) -> None:
        super().__init__(f"Для доступа к каталогу войдите: {login_url}", 401)
        self.login_url = login_url


class RemoteUser(BaseModel):
    id: UUID
    email: str | None = None
    role: str
    is_anonymous: bool
    user_metadata: dict[str, Any] = Field(default_factory=dict)


class RemoteSession(BaseModel):
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    expires_in: int = Field(gt=0)
    user: RemoteUser


@dataclass
class LoginFlow:
    verifier: str
    expires_at: float
    browser_id: str


class AuthService:
    def __init__(
        self, settings: AuthSettings, store: SessionStore, client: httpx.AsyncClient, base_url: str
    ) -> None:
        self.settings = settings
        self.store = store
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.login_url = self.base_url + "/auth"
        self.callback_url = self.base_url + "/auth/callback"
        self.cookie_name = f"job_search_login_{urlsplit(self.base_url).port or 80}"
        self._session: Session | None = None
        self._loaded = False
        self._lock = asyncio.Lock()
        self._flows: dict[str, LoginFlow] = {}

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"apikey": self.settings.publishable_key, **kwargs.pop("headers", {})}
        try:
            return await self.client.request(
                method,
                self.settings.url + path,
                headers=headers,
                timeout=self.settings.timeout_seconds,
                **kwargs,
            )
        except httpx.RequestError:
            raise AuthError("Не удалось связаться с Supabase. Проверьте сеть и повторите запрос.") from None

    async def providers(self) -> AuthProviders:
        response = await self.request("GET", "/auth/v1/settings")
        if response.status_code != 200:
            raise AuthError("Не удалось загрузить способы входа. Повторите запрос позже.")
        try:
            external = response.json()["external"]
            if not isinstance(external, dict):
                raise ValueError
        except (ValueError, KeyError):
            raise AuthError("Supabase вернул некорректные настройки входа.") from None
        return AuthProviders(
            providers=[
                AuthProvider(id=provider, name=name)
                for provider, name in PROVIDERS.items()
                if external.get(provider) is True
            ]
        )

    async def start_login(self, provider: Provider, browser_id: str) -> str:
        available = await self.providers()
        if provider not in {item.id for item in available.providers}:
            raise AuthError("Этот способ входа сейчас недоступен.", 422)
        async with self._lock:
            now = time.time()
            self._flows = {
                key: flow
                for key, flow in self._flows.items()
                if flow.expires_at > now and flow.browser_id != browser_id
            }
            if len(self._flows) >= 32:
                raise AuthError("Слишком много незавершённых входов. Повторите попытку позже.", 429)
            verifier = secrets.token_urlsafe(48)
            state = secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            self._flows[state] = LoginFlow(verifier, now + FLOW_SECONDS, browser_id)
        return (
            self.settings.url
            + "/auth/v1/authorize?"
            + urlencode(
                {
                    "provider": provider,
                    "redirect_to": self.callback_url + "?state=" + state,
                    "code_challenge": challenge.rstrip(b"=").decode(),
                    "code_challenge_method": "s256",
                }
            )
        )

    def _parse_session(self, response: httpx.Response) -> Session:
        try:
            remote = RemoteSession.model_validate(response.json())
        except (ValueError, ValidationError):
            raise AuthError("Supabase вернул некорректную сессию.") from None
        if remote.user.is_anonymous or remote.user.role != "authenticated":
            raise AuthError("Для каталога нужен обычный аккаунт пользователя.", 403)
        metadata = remote.user.user_metadata
        name = metadata.get("full_name") or metadata.get("user_name") or remote.user.email
        return Session(
            project_url=self.settings.url,
            access_token=remote.access_token,
            refresh_token=remote.refresh_token,
            expires_at=time.time() + remote.expires_in,
            user=AuthUser(id=remote.user.id, name=str(name or "Пользователь"), email=remote.user.email),
        )

    def _save(self, session: Session) -> None:
        try:
            self.store.save(session)
        except OSError:
            raise AuthError("Не удалось сохранить сессию. Проверьте доступ к каталогу данных.", 503) from None
        self._session, self._loaded = session, True

    async def finish_login(self, browser_id: str, state: str, code: str) -> None:
        async with self._lock:
            flow = self._flows.get(state)
            if flow is None or not secrets.compare_digest(flow.browser_id, browser_id):
                raise AuthError("Попытка входа не найдена. Начните вход ещё раз.", 400)
            del self._flows[state]
            if flow.expires_at <= time.time():
                raise AuthError("Время входа истекло. Начните вход ещё раз.", 400)
            response = await self.request(
                "POST",
                "/auth/v1/token?grant_type=pkce",
                json={
                    "auth_code": code,
                    "code_verifier": flow.verifier,
                },
            )
            if response.status_code != 200:
                raise AuthError("Не удалось завершить вход. Начните вход ещё раз.", 400)
            self._save(self._parse_session(response))

    async def _active_session(self) -> Session | None:
        # The caller holds _lock for loading, refresh, persistence and invalidation.
        if not self._loaded:
            try:
                self._session = self.store.load()
            except (ValueError, OSError):
                raise AuthError(
                    "Не удалось прочитать сессию. Выйдите из аккаунта и войдите снова.", 503
                ) from None
            if self._session and self._session.project_url != self.settings.url:
                self._session = None
                raise AuthError("Сессия относится к другому проекту. Выйдите и войдите снова.", 409)
            self._loaded = True
        if self._session is None or self._session.expires_at > time.time() + 60:
            return self._session
        response = await self.request(
            "POST",
            "/auth/v1/token?grant_type=refresh_token",
            json={
                "refresh_token": self._session.refresh_token.get_secret_value(),
            },
        )
        if response.status_code in {400, 401, 403}:
            self.store.clear()
            self._session = None
            return None
        if response.status_code != 200:
            raise AuthError("Не удалось обновить сессию. Повторите запрос позже.")
        self._save(self._parse_session(response))
        return self._session

    async def status(self) -> AuthStatus:
        async with self._lock:
            session = await self._active_session()
            return AuthStatus(
                authenticated=session is not None,
                user=session.user if session else None,
                login_url=None if session else self.login_url,
            )

    async def access_token(self) -> str:
        async with self._lock:
            session = await self._active_session()
            if session is None:
                raise LoginRequired(self.login_url)
            return session.access_token.get_secret_value()

    async def invalidate(self, token: str) -> None:
        async with self._lock:
            if self._session and secrets.compare_digest(self._session.access_token.get_secret_value(), token):
                self.store.clear()
                self._session = None

    async def logout(self) -> AuthStatus:
        async with self._lock:
            # Forget local credentials even when the persisted file was damaged or the network is unavailable.
            session = self._session
            if not self._loaded:
                try:
                    session = self.store.load()
                except (ValueError, OSError):
                    session = None
            self.store.clear()
            self._session, self._loaded = None, True
            self._flows.clear()
            if session and session.project_url == self.settings.url:
                response = await self.request(
                    "POST",
                    "/auth/v1/logout?scope=local",
                    headers={
                        "Authorization": "Bearer " + session.access_token.get_secret_value(),
                    },
                )
                if response.status_code not in {204, 401, 403}:
                    raise AuthError("Локальная сессия удалена, но Supabase не подтвердил выход.")
        return AuthStatus(authenticated=False, login_url=self.login_url)
