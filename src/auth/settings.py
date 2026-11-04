from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = "https://tcsqtzjfrtsuapnqiyke.supabase.co"
    publishable_key: str = "sb_publishable_Ga1Pu6SUeDV3_zOWH72nqQ_x1aRDYTw"
    timeout_seconds: float = Field(default=20, gt=0)

    @field_validator("url")
    @classmethod
    def project_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Supabase URL must use HTTPS without credentials")
        if url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("Supabase URL must be a project origin")
        return value.rstrip("/")

    @field_validator("publishable_key")
    @classmethod
    def public_key_only(cls, value: str) -> str:
        if not value.startswith("sb_publishable_"):
            raise ValueError("Only a Supabase publishable key is allowed in the plugin")
        return value
