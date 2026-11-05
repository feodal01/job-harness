"""HH-only text heuristics for Russian/English wording: worldwide remote work, excluded countries, relocation.

HH has no structured value for any of them (checked 04.11.2026: the work format dictionary has no
"worldwide", relocation dictionaries describe the candidate). Kept by decision of 04.11.2026 until a
text enricher replaces them; facts they set are marked `method=text` in the place's evidence (§12).
Refined on 260 live HH cards (docs/experiments/hh-enrichment-2026-11-04); every case is a test with
the real sentence (tests/test_hh_enrichment.py).
"""

import re

from src.geography.catalog import GeographyCatalog

SENTENCES = re.compile(r"[.!?;\n]")
REMOTE_WORDING = re.compile(r"удал[её]н|remote|work from anywhere", re.I)
NEGATION_BEFORE = re.compile(r"\b(?:не|нет|нельзя|not|no)\b.{0,35}$", re.I)

# --- Worldwide remote work -------------------------------------------------------------------

WORLDWIDE = re.compile(
    r"(?:из|или)\s+любой\s+точки\s+(?:мира|земного\s+шара)|из\s+любой\s+страны|"
    r"(?:remote|work(?:ing)?)(?:\s+\w+){0,3}\s+(?:worldwide|anywhere)|"
    r"worldwide\s+(?:remote|work)|удал[её]н\w*[^.!?;\n]{0,25}по\s+всему\s+миру",
    re.I,
)


def worldwide(text: str) -> bool:
    """Work from anywhere in the world is stated (not merely «работай из любой точки»)."""
    for sentence in SENTENCES.split(text):
        match = WORLDWIDE.search(sentence)
        if match and not NEGATION_BEFORE.search(sentence[: match.start()]):
            return True
    return False


# --- Countries excluded from remote work ------------------------------------------------------

EXCEPT = re.compile(r"(?:кроме|за исключением|except|excluding)\s+", re.I)
# The exception must be about where one works: «Кроме того…», «кроме выходных» are not.
GEOGRAPHIC = re.compile(r"удал[её]н|remote|любой точки|любой стран|worldwide|anywhere|countr|стран", re.I)
LIST_ITEMS = re.compile(r"\s*(?:,|\bи\b|\bили\b|\band\b|\bor\b)\s*", re.I)


def _country_forms(catalog: GeographyCatalog) -> dict[str, str]:
    """Capitalized country names in Russian (with the usual genitive after «кроме») and English → ISO."""
    forms: dict[str, str] = {}
    english = catalog.country_names("en")
    for code, name in catalog.country_names("ru").items():
        variants = {name, english[code]}
        if name.endswith("ия"):
            variants.add(name[:-1] + "и")
        elif name.endswith("а"):
            variants.update({name[:-1] + "ы", name[:-1] + "и"})
        elif name.endswith("ь"):
            variants.add(name[:-1] + "и")
        else:
            variants.add(name + "а")
        forms.update(dict.fromkeys(variants, code))
    forms["РФ"] = "RU"
    return forms


def excluded_countries(text: str, catalog: GeographyCatalog) -> set[str]:
    """Countries listed right after «кроме / except» in a sentence about where one may work."""
    forms = _country_forms(catalog)
    excluded: set[str] = set()
    for sentence in SENTENCES.split(text):
        if not GEOGRAPHIC.search(sentence):
            continue
        for match in EXCEPT.finditer(sentence):
            tail = re.split(r"[:()]", sentence[match.end() :])[0]
            # Only the list that starts the exception, item by exact (capitalized) country name.
            for item in LIST_ITEMS.split(tail.strip()):
                code = forms.get(item.strip())
                if code is None:
                    break
                excluded.add(code)
    return excluded


# --- Relocation assistance by the employer ----------------------------------------------------

MOVE = r"(?:переезд|релокац)"
ASSISTANCE = re.compile(
    rf"помо(?:щ[ьи]|га\w*)\s+(?:\w+\s+)?(?:с|в|при)\s+{MOVE}|"
    # The help names the move itself, at most two words apart: «компенсация аренды жилья при релокации».
    rf"(?:программ\w*|пакет\w*|оплат\w*|компенсац\w*|поддержк\w*|служебн\w*\s+жиль\w*)\s+(?:\S+\s+){{0,2}}?(?:(?:при|на|в|с|для)\s+)?{MOVE}|"
    rf"релокационн\w*\s+пакет|(?:оплачива\w*|компенсиру\w*|покрыва\w*)[^.!?]{{0,30}}{MOVE}|"
    r"relocation\s+(?:assistance|support|package|bonus|allowance)|support\s+(?:in|with)\s+relocat",
    re.I,
)
# A duty or requirement of the job, not an offer.
DUTY = re.compile(r"\b(?:experience|required|administer\w*|coordinat\w*)\b|\bопыт\b|обязател\w*", re.I)
CONDITIONAL = re.compile(
    r"\b(?:may|might|if|only|depending|applicable)\b|возможн|при условии|в отдельных|только для", re.I
)
REFUSAL = re.compile(
    r"\bno relocation (?:support|assistance|package)\b|"
    r"\bnot (?:provide|offer|cover|support)\w*[^.!?]{0,30}relocation|"
    r"relocation (?:support|assistance|package)[^.!?]{0,15}not (?:provided|available|offered)|"
    r"не (?:предоставля\w*|оплачива\w*|компенсиру\w*|помога\w*)[^.!?]{0,45}(?:релокац|переезд)|"
    r"(?:релокац\w*|переезд\w*|пакет)\s+не (?:предоставля\w*|оплачива\w*|компенсиру\w*)|"
    r"без помощи[^.!?]{0,20}(?:переезд|релокац)",
    re.I,
)
ANY_NEGATION = re.compile(r"\b(?:not|no)\b|\bне\b|\bбез\b", re.I)


def relocation(text: str) -> bool | None:
    """True: the employer helps to relocate; False: it says it does not; None: unknown or contradictory."""
    offers, refusals = [], []
    for raw in re.split(r"[.!?\n]", text):
        sentence = raw.strip()
        if not ASSISTANCE.search(sentence) or DUTY.search(sentence) or CONDITIONAL.search(sentence):
            continue
        if REFUSAL.search(sentence):
            refusals.append(sentence)
        elif not ANY_NEGATION.search(sentence):
            offers.append(sentence)
    if offers and refusals:
        return None
    return True if offers else False if refusals else None
