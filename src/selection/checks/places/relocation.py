"""Relocation support of one option: the fact of the same vacancy place, set by its source normalizer."""

from src.models.filters import VacancyPlace


def relocation_ok(place: VacancyPlace, *, required: bool) -> bool | None:
    return place.relocation_support if required else True


def relocation_reason(value: bool | None, label: str) -> str:
    """Explains the decided value of the option (unknown when its geography is not proven)."""
    if value is None:
        return f"Нет подтверждённых сведений о помощи с переездом для подходящего места: {label}"
    return f"Помощь с переездом {'есть' if value else 'не предоставляется'}: {label}"
