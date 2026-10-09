from pydantic import BaseModel, ConfigDict, Field, model_validator


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    path: list[str] = Field(default_factory=list)


class Location(BaseModel):
    """Native source coordinates prepared internally; public briefs use SearchFilters."""

    model_config = ConfigDict(extra="forbid")
    source: str
    country: Place | None = None
    city: Place | None = None
    options: list[Place] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_selection(self) -> "Location":
        if bool(self.country) == bool(self.options) or (self.city and not self.country):
            raise ValueError("Choose either country/city or source region options")
        ids = [p.id for p in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate region options")
        return self
