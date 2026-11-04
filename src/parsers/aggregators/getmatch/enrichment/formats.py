"""Getmatch `format` code → work format in the brief's vocabulary.

Getmatch's own vocabulary (vacancy page dictionary, 2026-11-03): «Формат работы» is one of
офис/гибрид, удаленка (в пределах РФ / по всему миру) or релокация (за счет компании / кандидата).
Relocation is an alternative to a format, not a format: its work format is unknown.
"""

from typing import Any

from src.models.filters import WorkFormat

# Getmatch API codes; remote is narrowed to worldwide by the place geography in _combine.
FORMATS: dict[str, WorkFormat] = {"office": "office", "hybrid": "hybrid", "remote": "remote_country"}


def place_format(entry: dict[str, Any]) -> WorkFormat | None:
    return FORMATS.get(entry.get("format") or "")
