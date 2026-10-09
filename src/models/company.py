from pydantic import BaseModel


class Company(BaseModel):
    id: str | None = None
    name: str | None = None
    profile_url: str | None = None
    site_url: str | None = None
    location: str | None = None
