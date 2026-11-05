"""HH work format codes → the brief's work format (HH dictionary `work_format`, api.hh.ru/dictionaries).

    ON_SITE «На месте работодателя», HYBRID «Гибрид», REMOTE «Удалённо», FIELD_WORK «Разъездной».

REMOTE has no geography of its own: remote work in the country of publication. FIELD_WORK has no
counterpart in the brief and stays unknown. Stored vacancies parsed before stage 5 keep formats already
written in the brief's vocabulary.
"""

from src.models.filters import WORK_FORMATS, WorkFormat

CODES: dict[str, WorkFormat | None] = {
    "ON_SITE": "office",
    "HYBRID": "hybrid",
    "REMOTE": "remote_country",
    "FIELD_WORK": None,
}


def place_format(code: object) -> WorkFormat | None:
    if not isinstance(code, str):
        return None
    if code in CODES:
        return CODES[code]
    return next((f for f in WORK_FORMATS if f == code), None)
