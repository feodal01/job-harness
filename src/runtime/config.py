import os
import sys
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Job Search"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "job-search"


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JOB_SEARCH_", extra="ignore")
    data_dir: Path = Field(default_factory=default_data_dir)
    port: int = Field(default=8765, ge=1, le=65535)
    startup_seconds: float = Field(default=45, gt=0)

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def state_path(self) -> Path:
        return self.data_dir / "service.json"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
