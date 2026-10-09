"""Структура описаний сохраняется, активное содержимое удаляется до показа в браузере."""

import json

import pytest
from bs4 import BeautifulSoup

from src.db import Database
from src.models.brief import BriefInput
from src.models.vacancy import Vacancy
from src.parsers.aggregators.getmatch.parser import GetmatchSearch, GetmatchVacancyDetails
from src.parsers.aggregators.hh.parser import HHVacancyDetails
from src.parsers.rich_text import parse_rich_text

BASE = "https://getmatch.ru/vacancies/42-example"
MARKUP = (
    "<h2>Требования</h2><p>Опыт <strong>Python</strong>, <em>SQL</em> и "
    '<a href="/companies/team">работы в команде</a>.</p>'
    "<ul><li>API<ul><li>REST</li></ul></li><li>Базы данных</li></ul>"
    '<ol start="3"><li>Собеседование</li><li>Оффер</li></ol>'
)


def test_preserves_structure_inline_text_and_relative_links():
    result = parse_rich_text(MARKUP, base_url=BASE)
    dom = BeautifulSoup(result.html, "html.parser")
    assert dom.h2.get_text() == "Требования"
    assert dom.strong.get_text() == "Python"
    assert dom.em.get_text() == "SQL"
    assert dom.a["href"] == "https://getmatch.ru/companies/team"
    assert dom.a["rel"] == ["noopener", "noreferrer"]
    assert dom.select_one("ul li ul li").get_text() == "REST"
    assert dom.ol["start"] == "3"
    assert "Опыт Python, SQL и работы в команде." in result.text
    assert "• API\n  • REST" in result.text
    assert "3. Собеседование\n4. Оффер" in result.text


def test_preserves_quotes_code_tables_and_secondary_emphasis():
    result = parse_rich_text(
        "<blockquote><p>Цитата <u>важная</u> <s>старая</s></p></blockquote>"
        "<pre><code>for item in items:\n    run(item)</code></pre>"
        "<table><caption>Условия</caption><tr><th>Формат</th><th>Место</th></tr>"
        '<tr><td colspan="2">Удалённо</td></tr></table><p>x<sup>2</sup> и H<sub>2</sub>O</p>',
        base_url=BASE,
    )
    dom = BeautifulSoup(result.html, "html.parser")
    assert dom.select_one("blockquote p u").get_text() == "важная"
    assert dom.s.get_text() == "старая"
    assert dom.pre.code.get_text() == "for item in items:\n    run(item)"
    assert dom.td["colspan"] == "2"
    assert "Формат | Место" in result.text
    assert "    run(item)" in result.text
    assert "x2 и H2O" in result.text


@pytest.mark.parametrize(
    "markup",
    [
        '<script>alert(1)</script><style>body{display:none}</style><p onclick="x()">Текст</p>',
        '<svg><script>alert(1)</script></svg><img src="https://tracker.test" onerror="x()"><p>Текст</p>',
        '<iframe srcdoc="<script>x()</script>"></iframe><form><input autofocus onfocus="x()"></form>'
        '<p id="results" class="card" style="position:fixed">Текст</p>',
        '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=1 onerror=x()>">'
        "</table></mtext></math><p>Текст</p>",
    ],
)
def test_removes_active_content_and_foreign_layout(markup):
    result = parse_rich_text(markup, base_url=BASE)
    assert result.text == "Текст"
    dom = BeautifulSoup(result.html, "html.parser")
    assert not dom.select("script, style, svg, math, img, iframe, form, input")
    assert all(not node.attrs for node in dom.find_all(True))


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "jav&#x61;script:alert(1)", "data:text/html,x", "vbscript:x"]
)
def test_removes_unsafe_links_without_losing_their_text(url):
    result = parse_rich_text(f'<p><a href="{url}">Ссылка</a></p>', base_url=BASE)
    assert result.text == "Ссылка"
    assert not BeautifulSoup(result.html, "html.parser").a.has_attr("href")


def test_preserves_explicit_list_numbers_and_reversed_lists():
    result = parse_rich_text(
        '<ol reversed start="5"><li>A</li><li value="2">B</li><li>C</li></ol>', base_url=BASE
    )
    assert result.text == "5. A\n2. B\n1. C"


def test_empty_description_has_no_rich_or_plain_content():
    result = parse_rich_text("<script>bad()</script><p> </p>", base_url=BASE)
    assert result.text is None and result.html is None


def test_both_detail_parsers_use_the_same_clean_description():
    vacancy = Vacancy(id="42", title="QA", url=BASE)
    hh = HHVacancyDetails().parse(f'<div data-qa="vacancy-description">{MARKUP}</div>', vacancy)
    gm = GetmatchVacancyDetails().parse(
        f'<link rel="canonical" href="{BASE}"><section class="b-vacancy-description">{MARKUP}</section>',
        vacancy,
    )
    assert hh.description_html == gm.description_html
    assert hh.description == gm.description
    assert "3. Собеседование" in hh.description


def test_getmatch_feed_preserves_clean_summary_and_detail_keeps_it():
    search = GetmatchSearch(10)
    payload = {
        "offers": [{"id": 42, "position": "QA", "url": BASE, "offer_description": MARKUP}],
        "meta": {"total": 1, "offset": 0, "limit": 10},
    }
    vacancy = search.parse(json.dumps(payload), search.build("")).vacancies[0]
    assert "<strong>Python</strong>" in vacancy.summary_html
    assert "• API" in vacancy.summary
    detail = GetmatchVacancyDetails().parse(
        f'<link rel="canonical" href="{BASE}"><section class="b-vacancy-description"><p>Полное</p></section>',
        vacancy,
    )
    assert detail.summary_html == vacancy.summary_html
    assert detail.description_html == "<p>Полное</p>"


def test_rich_description_survives_storage_listing_refresh_and_reuse(tmp_path):
    with_store = Database(tmp_path / "rich.sqlite")
    try:
        brief = with_store.briefs.save_brief(BriefInput(name="QA", queries=["QA"]))
        run = with_store.runs.create_run(brief["id"], {"sources": ["getmatch"]}, {})
        vacancy = Vacancy(
            id="42",
            title="QA",
            url=BASE,
            description="Полное описание",
            description_html="<p>Полное <b>описание</b></p>",
        )
        with_store.vacancies.save_vacancy(
            run["id"], brief["id"], vacancy, "ready", source="getmatch", detail_fetched=True
        )
        listing = Vacancy(id="42", title="QA", url=BASE, summary="Новое", summary_html="<p>Новое</p>")
        with_store.vacancies.save_vacancy(run["id"], brief["id"], listing, "pending", source="getmatch")
        known = with_store.vacancies.known_card("getmatch:42")
        assert known["description_html"] == "<p>Полное <b>описание</b></p>"
        assert known["summary_html"] == "<p>Новое</p>"
        assert with_store.vacancies.pool(brief["id"])[0]["description_html"] == known["description_html"]
    finally:
        with_store.close()
