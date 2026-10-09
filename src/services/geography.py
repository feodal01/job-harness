from src.geography.countries import COUNTRIES
from src.geography.directory import CityDirectory


class GeographyService:
    def __init__(self, directory: CityDirectory) -> None:
        self.directory = directory

    def countries(self) -> list[dict[str, str]]:
        return [{"code": code, "name": name} for code, name in COUNTRIES.items()]

    def cities(self, country: str, text: str) -> list[dict]:
        return self.directory.search(country, text)
