from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ServiceStatus(BaseModel):
    application: Literal["job-search"] = "job-search"
    protocol_version: int = 1
    version: str
    status: Literal["running", "stopping", "stopped"]
    managed: bool = False
    instance_id: str | None = None
    url: str | None = None
    active_runs: list[str] = Field(default_factory=list)


class StopServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel_active: bool = False
