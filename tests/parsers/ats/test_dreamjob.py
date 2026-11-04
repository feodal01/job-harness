import json
from pathlib import Path

import pytest

from src.parsers.ats.dreamjob.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/dreamjob"


@pytest.mark.parametrize(
    ("company", "count", "title", "city", "minimum", "maximum", "next_count"),
    [
        ("sbk-parus", 3, "BIM-инженер / BIM-моделлер (Инженерные сети / MEP)", "Москва", None, None, 0),
        ("softmall", 8, "Менеджер по работе с ключевыми клиентами (Уфа)", "Уфа", 140000, None, 0),
        ("retnnet", 1, "Младший инженер техподдержки (2 линия)", "Санкт-Петербург", 79000, None, 0),
        ("znanie", 12, "Старший менеджер межведомственных проектов", "Москва", None, None, 0),
        ("nii-spetsvuzavtomatika", 12, "Специалист по кадрам", "Ростов-на-Дону", 50000, 55000, 1),
    ],
)
def test_captured_dreamjob_boards(company, count, title, city, minimum, maximum, next_count) -> None:
    fixture = FIXTURES / company / "success"
    meta = json.loads((fixture / "meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    page = adapter.search.parse((fixture / "response.html").read_text(), adapter.search.build(""))
    assert len(page.vacancies) == count
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == title and first.location == city
    assert first.locations[0].city == city and first.locations[0].country is None
    assert first.salary.get("from") == minimum and first.salary.get("to") == maximum
    if minimum is not None:
        assert first.salary["currencyCode"] == "RUB"
        assert first.salary["period"] is None
    assert first.published_at is None  # Captured relative date is not today's date.
    assert first.source_data["published_text"] in {"сегодня", "вчера"}
    assert first.summary and first.summary_html
    assert len(page.next_requests) == next_count
    if company == "znanie":
        assert first.work_formats == ["remote_country"]
    if company == "retnnet":
        assert first.min_experience == 0


@pytest.mark.parametrize("company", ["sbk-parus", "softmall", "retnnet", "znanie", "nii-spetsvuzavtomatika"])
def test_captured_dreamjob_details(company: str) -> None:
    fixture = FIXTURES / company
    meta = json.loads((fixture / "success/meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    page = adapter.search.parse((fixture / "success/response.html").read_text(), adapter.search.build(""))
    meta = json.loads((fixture / "detail/meta.json").read_text())
    vacancy = next(v for v in page.vacancies if v.url == meta["captured_url"])
    assert adapter.vacancy_details is not None
    result = adapter.vacancy_details.parse((fixture / "detail/response.html").read_text(), vacancy)
    assert result.id == vacancy.id and result.url == vacancy.url
    assert result.published_at and result.published_at.startswith("2026-")
    assert result.description and len(result.description) > 2000
    assert result.description_html and "<" in result.description_html
    assert "<script" not in result.description_html
    assert result.locations[0].city == vacancy.location
    assert result.locations[0].country is None


def test_captured_dreamjob_pagination() -> None:
    fixture = FIXTURES / "nii-spetsvuzavtomatika"
    meta = json.loads((fixture / "success/meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], "Example"))
    meta = json.loads((fixture / "pagination/meta.json").read_text())
    page = adapter.search.parse(
        (fixture / "pagination/response.html").read_text(), SearchRequest(meta["captured_url"], "qa")
    )
    assert len(page.vacancies) == 10
    assert not page.next_requests
    with pytest.raises(ParseError):
        adapter.search.parse("<html>Unavailable</html>", adapter.search.build(""))
    with pytest.raises(ValueError):
        create_adapter(Board("https://dreamjob.ru/employers/1/vakansii/2", "Example"))
