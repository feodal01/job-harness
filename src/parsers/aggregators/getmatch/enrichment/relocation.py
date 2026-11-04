"""Getmatch `format` code → relocation support of the place.

`relocation_company` is shown on the vacancy page as «Помощь с переездом» (checked on vacancy
32642, 2026-11-03); `relocation_candidate` is «релокация за счет кандидата» in the site dictionary.
"""

from typing import Any

RELOCATION = {"relocation_company": True, "relocation_candidate": False}


def place_relocation(entry: dict[str, Any]) -> bool | None:
    return RELOCATION.get(entry.get("format") or "")
