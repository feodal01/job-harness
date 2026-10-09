from pydantic import BaseModel


class CountryResponse(BaseModel):
    code: str
    name: str


class CityResponse(BaseModel):
    name: str
    region: str | None
    path: list[str]
