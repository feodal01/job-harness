"""Private session storage, independent of the installed plugin directory."""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from src.schemas.auth import AuthUser


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_url: str
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    expires_at: float = Field(gt=0)
    user: AuthUser


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Session | None:
        try:
            content = self.path.read_text()
        except FileNotFoundError:
            return None
        if self.path.stat().st_mode & 0o077:
            raise ValueError("Session file must only be readable by its owner")
        return Session.model_validate_json(content)

    def save(self, session: Session) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        data = session.model_dump(mode="json")
        data["access_token"] = session.access_token.get_secret_value()
        data["refresh_token"] = session.refresh_token.get_secret_value()
        descriptor, temporary = tempfile.mkstemp(prefix="session-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
