"""Normalize explicit source values without deriving facts from an employer or free-form prose."""

"""
Review:
Этот файл не отражает как должна работать нормализация на самом деле, 
хардкод маппинговнормализаций вне зависимости от источника - это супер плохая практика и должно быть исправлено
Этот файл подлежит удалению и вынесению всех перекладок из запроса пользователя в параметры источника
в соответствующие папки самих парсеров источников
"""
from typing import cast

from src.geography.catalog import default_catalog
from src.models.filters import Employment, WorkFormat, WorkLocation
from src.parsers.ats.common import text

_FORMATS: dict[str, WorkFormat] = {
    "remote": "remote_country",
    "telecommute": "remote_country",
    "hybrid": "hybrid",
    "onsite": "office",
    "on-site": "office",
    "on_site": "office",
    "office": "office",
    "on site": "office",
    "remote_country": "remote_country",
    "remote_worldwide": "remote_worldwide",
}
_EMPLOYMENT: dict[str, Employment] = {
    "fulltime": "full_time",
    "full time": "full_time",
    "full-time": "full_time",
    "full_time": "full_time",
    "parttime": "part_time",
    "part time": "part_time",
    "part-time": "part_time",
    "part_time": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "intern": "internship",
    "internship": "internship",
    "volunteer": "volunteer",
}
def country_code(value: object) -> str | None:
    """ISO code from an ISO code or a country name in any CLDR language (single geography catalog)."""
    name = text(value)
    return default_catalog().country(name).value if name else None


def work_formats(value: object) -> list[str]:
    name = text(value)
    normalized = _FORMATS.get(name.casefold()) if name else None
    return [normalized] if normalized else []


def employment(value: object) -> list[Employment]:
    values = value if isinstance(value, list) else [value]
    result: list[Employment] = []
    for item in values:
        name = text(item)
        normalized = _EMPLOYMENT.get(name.casefold()) if name else None
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def locations(
    *, country: object = None, city: object = None, format: str | None = None, worldwide: bool = False
) -> list[WorkLocation]:
    formats = work_formats(format)
    normalized_format = cast(WorkFormat, formats[0]) if formats else None
    if worldwide:
        normalized_format = "remote_worldwide"
    code, city_name = country_code(country), text(city)
    if city_name and city_name.casefold() in {"remote", "worldwide", "anywhere"}:
        city_name = None
    if not any((code, city_name, normalized_format)):
        return []
    return [WorkLocation(country=code, city=city_name, format=normalized_format, worldwide=worldwide)]
