"""Taleo Business Edition v2 search pages and their rowFrom pagination."""

import re
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from src.models.brief import BriefInput
from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, host, required_text
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import (
    Fetch,
    ParseError,
    RequestSettings,
    SearchPage,
    SearchPlan,
    SearchRequest,
    SourceAdapter,
)
from src.parsers.models import Location

_CUSTOM_FILTER = re.compile(r"CUSTOM_[0-9]+")


class TaleoSearch(BoardSearch):
    multi_filter_names = None
    filter_names = frozenset({"keywords", "title", "location", "region", "state"})
    query_notes = "Поля поиска Taleo определяются по форме конкретной доски при подготовке запроса."
    fields = frozenset(
        {
            "id",
            "title",
            "url",
            "company",
            "location",
            "locations",
            "work_formats",
            "employment",
            "source_data",
        }
    )

    def __init__(self, board: Board, listing_url: str) -> None:
        self.filter_names = self.filter_names | frozenset(
            name for name in board.native_filters if _CUSTOM_FILTER.fullmatch(name)
        )
        super().__init__(board, listing_url)
        self._form_body: dict[str, str | list[str]] | None = None

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        address = urlsplit(self.listing_url)
        board_query = parse_qs(address.query)
        form_url = urlunsplit(
            address._replace(
                path=address.path.rsplit("/", 1)[0] + "/jobSearch",
                query=urlencode({name: board_query[name][0] for name in ("org", "cws")}),
            )
        )
        self._discover_form(await fetch(form_url), form_url)
        queries = (brief.queries or [""]) if self.query_parameter else [""]
        notes = (
            ()
            if self.query_parameter
            else ("В форме этой доски Taleo нет поля keywords; фразы брифа не передаются на сервер.",)
        )
        return SearchPlan(tuple(self.build(query) for query in queries), self.parameters, notes)

    def _discover_form(self, body: str, form_url: str) -> None:
        form = BeautifulSoup(body, "html.parser").select_one("form#TBE_theForm")
        if form is None or str(form.get("method", "")).lower() != "post":
            raise ParseError("Taleo search form is missing or does not use POST")
        action = urljoin(form_url, required_text(form.get("action"), "form action"))
        address, expected = urlsplit(action), urlsplit(self.listing_url)
        query, expected_query = parse_qs(address.query), parse_qs(expected.query)
        if (
            address.scheme != "https"
            or address.netloc != expected.netloc
            or address.path != expected.path
            or any(query.get(name) != expected_query.get(name) for name in ("org", "cws"))
        ):
            raise ParseError("Taleo form action does not belong to this board")
        defaults: dict[str, str | list[str]] = {}
        controls: dict[str, set[str] | None] = {}
        for control in form.select("input[name], select[name], textarea[name]"):
            if control.has_attr("disabled"):
                continue
            name = str(control["name"])
            kind = str(control.get("type", "text")).lower()
            value = str(control.get("value", ""))
            if kind == "hidden":
                defaults[name] = value
            elif name in type(self).filter_names or _CUSTOM_FILTER.fullmatch(name):
                if kind in {"checkbox", "radio"}:
                    options = controls.setdefault(name, set())
                    if options is not None:
                        options.add(value)
                    if control.has_attr("checked"):
                        selected = defaults.setdefault(name, [])
                        if isinstance(selected, list):
                            selected.append(value)
                elif control.name == "select":
                    option_nodes = [
                        option for option in control.select("option") if not option.has_attr("disabled")
                    ]
                    controls[name] = {str(option.get("value", option.get_text())) for option in option_nodes}
                    selected_options = [option for option in option_nodes if option.has_attr("selected")]
                    if not selected_options and not control.has_attr("multiple"):
                        selected_options = option_nodes[:1]
                    values = [str(option.get("value", option.get_text())) for option in selected_options]
                    if values:
                        defaults[name] = values if control.has_attr("multiple") else values[0]
                elif kind in {"text", "search"}:
                    controls[name] = None
                    defaults[name] = control.get_text() if control.name == "textarea" else value
        if any(defaults.get(name) != query[name][0] for name in ("org", "cws")):
            raise ParseError("Taleo form identifiers do not match this board")
        if defaults.get("act") != "search":
            raise ParseError("Taleo form does not declare a search action")
        for name, configured in self.board.native_filters.items():
            if name not in controls:
                raise ValueError(f"Taleo native filter {name} is not available on this board")
            options = controls[name]
            values = configured if isinstance(configured, list) else [configured]
            if options is not None and any(item not in options for item in values):
                raise ValueError(f"Taleo native filter {name} requires an option value from this board")
            if options is None and isinstance(configured, list):
                raise ValueError(f"Taleo text filter {name} requires one value")
            defaults[name] = configured
        self.query_parameter = "keywords" if "keywords" in controls else None
        self.filter_names = frozenset(controls)
        self.query_notes = (
            "Каждая фраза передаётся в keywords формы Taleo; остальные условия проверяются общими фильтрами."
            if self.query_parameter
            else "В форме этой доски Taleo нет keywords; фразы не применяются сервером."
        )
        self.listing_url, self._form_body = action, defaults

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("Taleo requires exact board option values in native_filters")
        if self._form_body is None:
            if query or self.board.native_filters:
                raise ValueError("Prepare the Taleo search form before building a filtered request")
            return SearchRequest(self.listing_url, query)
        values = dict(self._form_body)
        if self.query_parameter and query:
            values[self.query_parameter] = query
        return SearchRequest(self.listing_url, query, method="POST", form_body=values)

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        labels = {}
        for option in soup.select('option[value*="sortColumn="]'):
            column = parse_qs(urlsplit(str(option["value"])).query).get("sortColumn", [""])[0]
            if column.isdigit():
                labels[int(column)] = option.get_text(" ", strip=True)
        vacancies = [
            self._vacancy(row, labels) for row in soup.select(".oracletaleocwsv2-accordion-head-info")
        ]
        count = soup.select_one(".oracletaleocwsv2-panel-number")
        raw_total = count.get_text(strip=True).replace(",", "") if count else ""
        total = int(raw_total) if raw_total.isdigit() else None
        next_node = soup.select_one("a.jscroll-next[href]")
        next_requests: tuple[SearchRequest, ...] = ()
        current = int(parse_qs(urlsplit(request.url).query).get("rowFrom", ["0"])[0])
        if next_node:
            raw_offset = parse_qs(urlsplit(str(next_node["href"])).query).get("rowFrom", [""])[0]
            if not raw_offset.isdigit() or int(raw_offset) <= current:
                raise ParseError("Taleo pagination must advance rowFrom")
            address = urlsplit(request.url)
            query = parse_qs(address.query, keep_blank_values=True)
            query["rowFrom"] = [raw_offset]
            url = urlunsplit(address._replace(query=urlencode(query, doseq=True)))
            next_requests = (replace(request, url=url),)
        if not vacancies:
            explicit_empty = any(
                marker in body.casefold() for marker in ("no jobs found", "no results found")
            )
            terminal = total is not None and current >= total and next_node is None
            if not explicit_empty and not terminal:
                raise ParseError("Taleo response contains no job rows or explicit empty result")
        return SearchPage(vacancies, total=total, next_requests=next_requests)

    def _vacancy(self, row: Tag, labels: dict[int, str]) -> Vacancy:
        link = row.select_one("a.viewJobLink[href]")
        if link is None:
            raise ParseError("Taleo row is missing viewJobLink")
        url = urljoin(self.board.url, str(link["href"]))
        uid = required_text(parse_qs(urlsplit(url).query).get("rid", [None])[0], "rid")
        fields = [node.get_text(" ", strip=True) for node in row.find_all("div", recursive=False)]
        # Sort-column labels describe the displayed columns; their order varies by board.
        # Unlabelled extra columns stay in source_data rather than becoming guessed facts.
        labelled = {label: fields[index - 1] for index, label in labels.items() if 0 < index <= len(fields)}
        location_values = [
            value for label, value in labelled.items() if re.search(r"\blocation\b", label, re.I) and value
        ]
        formats = list(
            dict.fromkeys(
                fmt
                for value in location_values
                for marker in re.findall(r"\b(?:hybrid|remote|on-site|onsite)\b", value, re.I)
                for fmt in work_formats(marker)
            )
        )
        places = []
        for value in location_values:
            city_state = re.sub(r"^(?:Hybrid|Remote)[\s,]+", "", value, flags=re.I)
            if re.fullmatch(r"[^,]+, [A-Z]{2}", city_state):
                places.extend(locations(city=city_state.split(",", 1)[0]))
        declared_employment = [
            value
            for label, value in labelled.items()
            if re.search(r"\b(?:employment|employee work status)\b", label, re.I)
        ]
        return Vacancy(
            id=uid,
            title=required_text(link.get_text(" ", strip=True), "title"),
            url=url,
            company=Company(name=self.board.company, profile_url=self.board.url),
            location="; ".join(location_values) or None,
            locations=places,
            work_formats=formats,
            employment=employment(declared_employment),
            source_data={
                "fields": [value for value in fields],
                "labelled_fields": {label: value for label, value in labelled.items()},
            },
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    query = parse_qs(address.query, keep_blank_values=True)
    if (
        not host(board.url).endswith(".tbe.taleo.net")
        or not re.fullmatch(r"/[^/]+/ats/careers/v2/(?:searchResults|jobSearch)", address.path)
        or not query.get("org", [""])[0]
        or not query.get("cws", [""])[0]
    ):
        raise ValueError("Taleo requires a Business Edition v2 board URL with org and cws")
    if address.path.endswith("/jobSearch") and query.get("act") == ["redirectCwsV2"]:
        query.pop("act")
    listing_url = urlunsplit(
        address._replace(
            path=address.path.rsplit("/", 1)[0] + "/searchResults",
            query=urlencode(query, doseq=True),
        )
    )
    return adapter(
        "taleo",
        board,
        TaleoSearch(board, listing_url),
        key=f"{host(board.url)}:{query['org'][0]}:{query['cws'][0]}",
        resource=host(board.url),
        allowed_hosts=(host(board.url),),
        settings=settings,
    )
