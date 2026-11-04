from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from src.auth.settings import AuthSettings
from src.parsers.aggregators.getmatch.settings import GetmatchSettings
from src.parsers.aggregators.hh.settings import HHSettings
from src.runtime.config import RuntimeSettings

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = Path(__file__).resolve().parents[1] / "settings.toml"
if not DEFAULT_SETTINGS.exists():
    DEFAULT_SETTINGS = ROOT / "settings.toml"


class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hh_ru: HHSettings = Field(default_factory=HHSettings)
    getmatch: GetmatchSettings = Field(default_factory=GetmatchSettings)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_SEARCH_", env_nested_delimiter="__", toml_file=DEFAULT_SETTINGS, extra="forbid"
    )
    database_path: Path = Field(default_factory=lambda: RuntimeSettings().data_dir / "search.sqlite")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_backup_count: int = Field(default=5, ge=1)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    second_chance: bool = False
    run_timeout_seconds: float = Field(default=900, gt=0)
    max_active_runs: int = Field(default=3, ge=1, le=10)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, TomlConfigSettingsSource(settings_cls)

    @field_validator("database_path")
    @classmethod
    def absolute_database_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value
