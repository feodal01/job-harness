"""Work format of one option against the brief place's minimum ("this or more flexible")."""

from src.models.filters import WORK_FORMATS, WorkFormat

# Keyed by str: vacancy data may carry formats outside the model, which have no rank.
FORMAT_RANK: dict[str, int] = {fmt: rank for rank, fmt in enumerate(WORK_FORMATS)}


def format_ok(mode: str | None, minimum: WorkFormat | None) -> bool | None:
    if minimum is None:
        return True
    return None if mode is None else FORMAT_RANK[mode] >= FORMAT_RANK[minimum]
