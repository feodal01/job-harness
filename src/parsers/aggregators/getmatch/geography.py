"""Native Getmatch region/work-format categories, verified against its public UI."""

from typing import Literal

from src.parsers.interfaces import RegionGeography
from src.parsers.models import Place


class GetmatchGeography(RegionGeography):
    mode: Literal["regions"] = "regions"
    options = (
        Place(id="moscow", name="Офис или гибрид в Москве"),
        Place(id="saints_p", name="Офис или гибрид в Санкт-Петербурге"),
        Place(id="regions_ru", name="Офис или гибрид в других городах России"),
        Place(id="relocate", name="Офис или гибрид в других странах (релокация)"),
        Place(id="remote", name="Удалённо"),
    )
