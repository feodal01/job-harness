from pydantic import BaseModel


class CountryResponse(BaseModel):
    code: str
    name: str


class CityResponse(BaseModel):
    geoname_id: int
    country: str
    country_name_en: str
    name: str
    ascii_name: str
    region: str | None
    path: list[str]
