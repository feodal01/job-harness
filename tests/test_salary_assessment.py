"""The salary check of the brief's minimum, case by case from the spec.

Spec: docs/brief-parameters/salary-minimum-check.md (05.11.2026), test cases 1–74 and properties.
Coefficients in tests: k₁ = 1.25, k₂ = 1.75. Numbers are thousands, the brief is RUB per month
unless a case says otherwise.
"""

import random
import re
from fractions import Fraction

import pytest

from src.models.salary import SalaryRecord, salary_record
from src.selection.salary import SalaryRange, assess

K = SalaryRange(exact=1.25, possible=1.75)
UNITS = {"в год": "year", "в час": "hour", "в день": "day", "в неделю": "week", "за смену": "shift"}


def parse(text: str) -> SalaryRecord:
    """A record written as in the spec: «от 200», «до 300», «200–300», «250», «от 2400 в год», «3000 USD»…"""
    bonus = "+премия" in text
    text = text.replace("+премия", "").strip()
    if text in {"не указана", "пусто"}:
        return SalaryRecord(state="not_specified", bonus=bonus)
    if text == "скрыта":
        return SalaryRecord(state="hidden")
    total = "совокупно" in text
    currency = "USD" if "USD" in text else "RUB"
    unit: str | None = "month"
    for words, code in UNITS.items():
        if words in text:
            unit = code
    if "без периода" in text:
        unit = None
    numbers = [float(n.replace(",", ".").replace(" ", "")) for n in re.findall(r"\d[\d ,]*", text)]
    if text.startswith("от"):
        low, high = numbers[0], None
    elif text.startswith("до"):
        low, high = None, numbers[0]
    elif len(numbers) == 2:
        low, high = numbers
    else:
        low = high = numbers[0]
    return salary_record(low, high, currency=currency, unit=unit, total=total, bonus=bonus)


def records(text: str) -> list[SalaryRecord]:
    return [parse(part) for part in text.split(" + ")]


def check(text, minimum, period="month"):
    return assess(records(text), Fraction(str(minimum)), "RUB", period, K)


# (records, P, E) — P and E as in the spec table.
PE = [
    (1, "от 200", 250, 350),
    (2, "до 300", 300, 300),
    (3, "200–300", 300, 300),
    (4, "250", 250, 250),
    (5, "от 200 + до 100", 100, 350),
    (6, "от 200 + до 200", 200, 200),
    (7, "от 200 + до 250", 250, 250),
    (8, "от 200 + до 300", 300, 300),
    (9, "от 200 + до 500", 500, 500),
    (10, "от 200 + до 1000", 250, 1000),
    (11, "от 200 + 100–150", 150, 350),
    (12, "от 200 + 100–200", 200, 200),
    (13, "от 200 + 100–300", 300, 300),
    (14, "от 200 + 200–300", 300, 300),
    (15, "от 200 + 300–400", 400, 400),
    (16, "от 200 + 400–500", 250, 500),
    (17, "от 200 + 250", 250, 250),
    (18, "от 200 + 150", 150, 350),
    (19, "от 200 + от 200", 250, 350),
    (20, "от 200 + от 300", 375, 525),
    (21, "от 200 + от 400", 250, 700),
    (22, "до 200 + до 300", 300, 300),
    (23, "до 100 + до 300", 100, 300),
    (24, "до 300 + 100–200", 300, 300),
    (25, "до 300 + 200–300", 300, 300),
    (26, "до 200 + 250–300", 200, 300),
    (27, "до 100 + 30–40", 40, 100),
    (28, "100–200 + 100–200", 200, 200),
    (29, "100–200 + 150–300", 300, 300),
    (30, "100–200 + 200–300", 300, 300),
    (31, "100–200 + 300–400", 200, 400),
    (32, "100–300 + 150–200", 300, 300),
    (33, "250 + 300", 250, 300),
    (34, "от 200 + 250–280 + 250–280", 280, 280),
    (35, "от 200 + до 250 + 400–500", 250, 500),
    (36, "250–280 + 250–280 + 2 500 000", 280, 2_500_000),
    (37, "от 200 + от 220 + от 240", 300, 420),
    (38, "от 200 + до 250 + от 300", 250, 525),
    (39, "от 200 + до 250 + 260–270", 250, 270),
    (40, "100–200 + 150–300 + 500", 300, 500),
    (42, "от 200 + не указана", 250, 350),
    (44, "от 200 + 3000 USD", 250, 350),
    (46, "250 + 250", 250, 250),  # «на руки» and «до вычета»: taxes are not compared.
    (47, "250 + 300", 250, 300),
    (48, "от 2400 в год", 250, 350),
    (49, "3000–3600 в год", 300, 300),
    (50, "от 200 + от 2400 в год", 250, 350),
    (51, "от 200 + 2400–3600 в год", 300, 300),
    (52, "100–200 + 3600–4800 в год", 200, 400),
    (53, "1,5 в час", 260, 260),
    (54, "12 в день", 260, 260),
    (55, "60 в неделю", 260, 260),
    (56, "1,5 в час + 12 в день + 60 в неделю", 260, 260),
    (57, "от 1,2 в час", 260, 364),
    (58, "200–300 + 2 в час", 300, Fraction(1040, 3)),
    (59, "200–300 +премия", 300, 300),
    (60, "от 200 +премия", 250, 350),
    (63, "от 200 + скрыта", 250, 350),
    (66, "от 200 + 250 без периода", 250, 350),
    (67, "500 совокупно", 500, 500),
    (68, "300–500 совокупно", 500, 500),
    (69, "от 500 совокупно", 500, 500),
    (70, "от 200 + 500 совокупно", 500, 500),
    (72, "от 200 + 5 за смену", 250, 350),
]


@pytest.mark.parametrize(("case", "text", "p", "e"), PE, ids=[f"case-{c[0]}" for c in PE])
def test_p_and_e_on_four_minimums(case, text, p, e):
    p, e = Fraction(p), Fraction(e)
    result = check(text, 0)
    assert (result.p, result.e) == (p, e), case
    assert check(text, p).verdict is True
    assert check(text, p + 1).verdict is (None if e > p else False)
    assert check(text, e).verdict is (True if e == p else None)
    assert check(text, e + 1).verdict is False


@pytest.mark.parametrize(
    ("case", "text", "verdict", "reason"),
    [
        (41, "не указана", True, "не указана"),
        (41, "пусто", True, "не указана"),
        (43, "3000 USD", None, "USD"),
        (45, "300–200", None, "некорректн"),
        (61, "не указана +премия", True, "не указана"),
        (62, "скрыта", True, "скрыта"),
        (64, "скрыта + не указана", True, "скрыта"),
        (65, "200–300 без периода", None, "период"),
        (71, "5 за смену", None, "смену"),
    ],
)
def test_without_comparable_records(case, text, verdict, reason):
    for minimum in (1, 250, 10**9):
        result = check(text, minimum)
        assert result.verdict is verdict, case
        assert reason in result.reason, (case, result.reason)


def test_missing_records_pass_as_before():
    assert assess([], Fraction(250), "RUB", "month", K).verdict is True


@pytest.mark.parametrize(
    ("text", "period", "p", "e"),
    [
        ("от 2400 в год", "year", 3000, 4200),  # Case 73: the brief per year.
        ("1,5 в час", "hour", Fraction(3, 2), Fraction(3, 2)),  # Case 74: the brief per hour.
    ],
)
def test_the_brief_period_is_the_basis(text, period, p, e):
    result = check(text, 0, period)
    assert (result.p, result.e) == (Fraction(p), Fraction(e))


def test_reason_names_incomparable_records_and_bonus_label():
    result = check("от 200 + 3000 USD + 5 за смену +премия", 250)
    assert "USD" in result.reason and "смену" in result.reason
    assert result.bonus is True
    assert check("от 200", 250).bonus is False
    assert check("500 совокупно", 250).bonus is True  # «Совокупно» includes bonuses.


# --- Properties on random records ----------------------------------------------------------


def random_record(rng: random.Random) -> SalaryRecord:
    kind = rng.choice(["from", "to", "range", "sum", "none", "hidden"])
    a, b = sorted(rng.choice(range(50, 600, 10)) for _ in range(2))
    return {
        "from": salary_record(a, None),
        "to": salary_record(None, b),
        "range": salary_record(a, b),
        "sum": salary_record(a, a),
        "none": SalaryRecord(state="not_specified"),
        "hidden": SalaryRecord(state="hidden"),
    }[kind]


@pytest.mark.parametrize("seed", range(200))
def test_properties(seed):
    rng = random.Random(seed)
    items = [random_record(rng) for _ in range(rng.randint(1, 5))]
    result = assess(items, Fraction(0), "RUB", "month", K)
    shuffled = items[:]
    rng.shuffle(shuffled)
    same = assess(shuffled, Fraction(0), "RUB", "month", K)
    assert (same.p, same.e) == (result.p, result.e)  # Order does not matter.
    doubled = assess(items + [items[0]], Fraction(0), "RUB", "month", K)
    assert (doubled.p, doubled.e) == (result.p, result.e)  # A duplicate changes nothing.
    extra = assess(
        items + [SalaryRecord(state="not_specified"), SalaryRecord(state="hidden")], 0, "RUB", "month", K
    )
    assert (extra.p, extra.e) == (result.p, result.e)
    if result.p is not None:
        assert result.e is not None and result.p <= result.e
        scaled = [
            salary_record(*(None if v is None else v * 3 for v in (r.from_, r.to)))
            for r in items
            if r.state == "specified"
        ]
        tripled = assess(scaled, Fraction(0), "RUB", "month", K)
        assert (tripled.p, tripled.e) == (result.p * 3, result.e * 3)  # All rules are relative.


def test_record_keeps_the_original_and_converts_to_every_standard_period():
    record = salary_record(1.5, 1.5, unit="hour")
    assert record.unit == "hour"
    assert record.converted["month"] == {"from": 260.0, "to": 260.0}
    assert record.converted["year"] == {"from": 3120.0, "to": 3120.0}
    assert set(record.converted) == {"hour", "day", "week", "month", "year"}
    shift = salary_record(5, 5, unit="shift")
    assert shift.converted == {}  # A source's own unit is kept as is.
    dumped = record.model_dump(mode="json")
    assert dumped["from"] == 1.5 and "from_" not in dumped
    assert SalaryRecord.model_validate(dumped) == record


def test_an_unknown_unit_is_incomparable_not_a_crash():
    # Review 05.11.2026: a unit outside the standard periods and the known source units.
    for total in (False, True):
        record = salary_record(100, 200, unit="per_task", total=total)
        result = assess([record], Fraction(150), "RUB", "month", K)
        assert result.verdict is None and "per_task" in result.reason
