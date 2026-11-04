"""Legacy shared work-format vocabulary (HH codes, stored values); removed with the HH stage.

Per-source normalization lives in each source's enrichment.py and src/enrichment
(docs/vacancy-facts-plan.md).
"""

from src.models.filters import WorkFormat

FORMATS: dict[str, WorkFormat] = {
    "ON_SITE": "office",
    "HYBRID": "hybrid",
    "REMOTE": "remote_country",
    "remote": "remote_country",
    "office": "office",
    "hybrid": "hybrid",
    "remote_country": "remote_country",
    "remote_worldwide": "remote_worldwide",
}
