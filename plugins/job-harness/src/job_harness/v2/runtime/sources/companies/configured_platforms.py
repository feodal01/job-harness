"""Configured company career sources backed by common public ATS surfaces."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qs, urljoin, urlparse

from job_harness.v2.contracts import (
    AttemptEvidence,
    DetailEnrichmentScraper,
    HttpMethod,
    RawListing,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
)
from job_harness.v2.runtime.sources._html import html_to_text
from job_harness.v2.runtime.sources._url import strip_query
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

Platform = Literal[
    "lever",
    "ashby",
    "workable",
    "greenhouse",
    "bamboohr",
    "teamtailor",
    "workday",
    "personio",
    "join",
    "dreamjob",
    "jsonld_jobposting",
    "ycombinator",
]

_SECTION_LABEL_RE = re.compile(
    r"<(?P<tag>h[1-6]|strong|b)[^>]*>(?P<label>.*?)</(?P=tag)>",
    re.I | re.S,
)
_REMOTE_SCOPE_RE = re.compile(r"remote\s*\((?P<scope>[^)]+)\)", re.I)
_SALARY_AMOUNT_RE = re.compile(r"([$£€]\s?\d|\b(?:USD|GBP|EUR)\b|\b\d[\d,]*(?:k|K)?\s?(?:USD|GBP|EUR)\b)")
_SALARY_MARKER_RE = re.compile(r"(\$|CAD|USD|EUR|GBP|hourly rate|compensation)", re.I)
_REMOTE_TITLE_MARKER_RE = re.compile(r"(?:\(\s*remote\s*\)|[-\u2013\u2014]\s*remote\s*$)", re.I)
_TEAMTAILOR_ITEM_RE = re.compile(r"<li\b[^>]*>(?P<body>.*?)</li>", re.S)
_TEAMTAILOR_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<url>https://[^"]+/jobs/(?P<id>\d+)-[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S,
)
_TEAMTAILOR_SHOW_MORE_RE = re.compile(r'href="(?P<href>[^"]*/jobs/show_more\?page=\d+)"')
_TEAMTAILOR_SPAN_RE = re.compile(r'<span(?P<attrs>[^>]*)>(?P<body>.*?)</span>', re.S)
_PERSONIO_ITEM_RE = re.compile(r"<li>\s*<a(?P<attrs>[^>]+)>(?P<body>.*?)</a>\s*</li>", re.S)
_PERSONIO_HREF_RE = re.compile(r'href="(?P<href>/job/(?P<id>\d+))"')
_PERSONIO_TITLE_RE = re.compile(r'<h3[^>]+class="[^"]*\bjb-title\b[^"]*"[^>]*>(?P<title>.*?)</h3>', re.S)
_PERSONIO_META_RE = re.compile(r'<span[^>]+class="[^"]*jobMetaText[^"]*"[^>]*>(?P<body>.*?)</span>', re.S)
_PERSONIO_JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(?P<body>.*?)</script>',
    re.I | re.S,
)
_JOIN_NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(?P<body>.*?)</script>', re.I | re.S)
_JOIN_JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(?P<body>.*?)</script>',
    re.I | re.S,
)
_JOB_POSTING_JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(?P<body>.*?)</script>',
    re.I | re.S,
)
_YCOMBINATOR_DATA_PAGE_RE = re.compile(
    r'<div[^>]+id="WaasShowJobsPage[^"]*"[^>]+data-page="(?P<body>.*?)"',
    re.I | re.S,
)
_DREAMJOB_VACANCIES_PATH_RE = re.compile(r"/employers/(?P<employer_id>\d+)/vakansii(?:/(?P<id>\d+))?")
_DREAMJOB_REMOTE_TAGS = frozenset({"можно удаленно", "можно удалённо"})
_DREAMJOB_RUB_CURRENCY_MARKERS = frozenset({"₽", "руб", "rub"})
_YCOMBINATOR_REMOTE_LOCATION_RE = re.compile(r"remote\s*\((?P<locations>[^)]+)\)", re.I)
_TITLE_ATTR_RE = re.compile(r'title="(?P<title>[^"]+)"')
_HTML_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_WORKDAY_PAGE_LIMIT = 20
_WORKDAY_SEARCH_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
_DEFAULT_REQUIREMENTS_LABEL_MARKERS = ("requirements", "looking for", "what you bring", "who you are", "about you")
_GREENHOUSE_SALARY_LABEL_MARKERS = ("compensation", "pay", "salary")
_SALARY_STOP_LINE_MARKERS = (
    "we use covey",
    "please see the independent bias audit",
    "benefits & culture",
    "equal opportunity",
)
_DEPARTMENT_AND_LOCATION_VALUE_COUNT = 2
_PERSONIO_EMPLOYMENT_TYPE_INDEX = 1
_PERSONIO_CONTRACT_TYPE_INDEX = 2
_SALARY_RANGE_VALUE_COUNT = 2


@dataclass(frozen=True)
class ConfiguredCompanySourceConfig:
    source_id: str
    company: str
    platform: Platform
    board_url: str
    career_url: str
    requirements_label_markers: tuple[str, ...] = _DEFAULT_REQUIREMENTS_LABEL_MARKERS
    workable_slug: str | None = None
    bamboohr_detail_url_template: str | None = None
    workday_base_url: str | None = None
    workday_tenant: str | None = None
    workday_site: str | None = None


CONFIGURED_COMPANY_SOURCE_CONFIGS: dict[str, ConfiguredCompanySourceConfig] = {
    "career:collectly": ConfiguredCompanySourceConfig(
        source_id="career:collectly",
        company="Collectly",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/CollectlyInc?mode=json",
        career_url="https://jobs.lever.co/CollectlyInc",
    ),
    "career:planner5d": ConfiguredCompanySourceConfig(
        source_id="career:planner5d",
        company="Planner 5D",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/planner5d?mode=json",
        career_url="https://jobs.lever.co/planner5d",
    ),
    "career:superannotate": ConfiguredCompanySourceConfig(
        source_id="career:superannotate",
        company="SuperAnnotate",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/superannotate?mode=json",
        career_url="https://jobs.lever.co/superannotate",
    ),
    "career:xsolla": ConfiguredCompanySourceConfig(
        source_id="career:xsolla",
        company="Xsolla",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/xsolla?mode=json",
        career_url="https://jobs.lever.co/xsolla",
    ),
    "career:unlimint": ConfiguredCompanySourceConfig(
        source_id="career:unlimint",
        company="Unlimint",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/unlimit?mode=json",
        career_url="https://jobs.lever.co/unlimit",
    ),
    "career:clickhouse": ConfiguredCompanySourceConfig(
        source_id="career:clickhouse",
        company="ClickHouse",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/clickhouse",
        career_url="https://jobs.ashbyhq.com/clickhouse",
    ),
    "career:datafold": ConfiguredCompanySourceConfig(
        source_id="career:datafold",
        company="Datafold",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/datafold",
        career_url="https://jobs.ashbyhq.com/datafold",
    ),
    "career:inworld": ConfiguredCompanySourceConfig(
        source_id="career:inworld",
        company="Inworld AI",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/inworld-ai",
        career_url="https://jobs.ashbyhq.com/inworld-ai",
    ),
    "career:luminai": ConfiguredCompanySourceConfig(
        source_id="career:luminai",
        company="Luminai",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/luminai",
        career_url="https://jobs.ashbyhq.com/luminai",
    ),
    "career:teleport": ConfiguredCompanySourceConfig(
        source_id="career:teleport",
        company="Teleport",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/goteleport",
        career_url="https://jobs.ashbyhq.com/goteleport",
    ),
    "career:mapbox": ConfiguredCompanySourceConfig(
        source_id="career:mapbox",
        company="Mapbox",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/Mapbox",
        career_url="https://jobs.ashbyhq.com/Mapbox",
    ),
    "career:joom": ConfiguredCompanySourceConfig(
        source_id="career:joom",
        company="Joom",
        platform="workable",
        board_url="https://apply.workable.com/joom/jobs.md",
        career_url="https://apply.workable.com/joom/",
        workable_slug="joom",
    ),
    "career:zeptolab": ConfiguredCompanySourceConfig(
        source_id="career:zeptolab",
        company="ZeptoLab",
        platform="workable",
        board_url="https://apply.workable.com/zeptolab/jobs.md",
        career_url="https://apply.workable.com/zeptolab/",
        workable_slug="zeptolab",
    ),
    "career:homebuddy": ConfiguredCompanySourceConfig(
        source_id="career:homebuddy",
        company="HomeBuddy",
        platform="workable",
        board_url="https://apply.workable.com/homebuddy/jobs.md",
        career_url="https://apply.workable.com/homebuddy/",
        workable_slug="homebuddy",
    ),
    "career:lyka": ConfiguredCompanySourceConfig(
        source_id="career:lyka",
        company="Lyka",
        platform="workable",
        board_url="https://apply.workable.com/lyka/jobs.md",
        career_url="https://apply.workable.com/lyka/",
        workable_slug="lyka",
    ),
    "career:thesoul-publishing": ConfiguredCompanySourceConfig(
        source_id="career:thesoul-publishing",
        company="TheSoul Publishing",
        platform="workable",
        board_url="https://apply.workable.com/thesoul-publishing-1/jobs.md",
        career_url="https://apply.workable.com/thesoul-publishing-1/",
        workable_slug="thesoul-publishing-1",
    ),
    "career:abbyy": ConfiguredCompanySourceConfig(
        source_id="career:abbyy",
        company="ABBYY",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/abbyy/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/abbyy",
    ),
    "career:ahrefs": ConfiguredCompanySourceConfig(
        source_id="career:ahrefs",
        company="Ahrefs",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/ahrefsjobs/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/ahrefsjobs",
    ),
    "career:eqvilent": ConfiguredCompanySourceConfig(
        source_id="career:eqvilent",
        company="Eqvilent",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/eqvilentjobs/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/eqvilentjobs",
    ),
    "career:humansignal": ConfiguredCompanySourceConfig(
        source_id="career:humansignal",
        company="HumanSignal",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/humansignal/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/humansignal",
    ),
    "career:lokalise": ConfiguredCompanySourceConfig(
        source_id="career:lokalise",
        company="Lokalise",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/lokalise/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/lokalise",
    ),
    "career:flo-health": ConfiguredCompanySourceConfig(
        source_id="career:flo-health",
        company="Flo Health",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/flohealth/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/flohealth",
    ),
    "career:pandadoc": ConfiguredCompanySourceConfig(
        source_id="career:pandadoc",
        company="PandaDoc",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/pandadoc/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/pandadoc",
    ),
    "career:wrike": ConfiguredCompanySourceConfig(
        source_id="career:wrike",
        company="Wrike",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/wrike/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/wrike",
    ),
    "career:adtech-holding": ConfiguredCompanySourceConfig(
        source_id="career:adtech-holding",
        company="AdTech Holding",
        platform="bamboohr",
        board_url="https://adtechholding.bamboohr.com/careers/list",
        career_url="https://adtechholding.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://adtechholding.bamboohr.com/careers/{id}",
    ),
    "career:altenar": ConfiguredCompanySourceConfig(
        source_id="career:altenar",
        company="Altenar",
        platform="bamboohr",
        board_url="https://altenar.bamboohr.com/careers/list",
        career_url="https://altenar.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://altenar.bamboohr.com/careers/{id}",
    ),
    "career:synder": ConfiguredCompanySourceConfig(
        source_id="career:synder",
        company="Synder",
        platform="bamboohr",
        board_url="https://synder.bamboohr.com/careers/list",
        career_url="https://synder.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://synder.bamboohr.com/careers/{id}",
    ),
    "career:onemarketdata": ConfiguredCompanySourceConfig(
        source_id="career:onemarketdata",
        company="OneMarketData",
        platform="bamboohr",
        board_url="https://onemarketdata.bamboohr.com/careers/list",
        career_url="https://onemarketdata.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://onemarketdata.bamboohr.com/careers/{id}",
    ),
    "career:crystal": ConfiguredCompanySourceConfig(
        source_id="career:crystal",
        company="Crystal Blockchain",
        platform="teamtailor",
        board_url="https://crystalintelligence.teamtailor.com/jobs",
        career_url="https://crystalintelligence.teamtailor.com/jobs",
    ),
    "career:synthesized": ConfiguredCompanySourceConfig(
        source_id="career:synthesized",
        company="Synthesized",
        platform="teamtailor",
        board_url="https://synthesized.teamtailor.com/jobs",
        career_url="https://synthesized.teamtailor.com/jobs",
    ),
    "career:tradingview": ConfiguredCompanySourceConfig(
        source_id="career:tradingview",
        company="TradingView",
        platform="teamtailor",
        board_url="https://tradingview.teamtailor.com/jobs",
        career_url="https://tradingview.teamtailor.com/jobs",
    ),
    "career:osome": ConfiguredCompanySourceConfig(
        source_id="career:osome",
        company="Osome",
        platform="teamtailor",
        board_url="https://careers.osome.com/jobs",
        career_url="https://careers.osome.com/jobs",
    ),
    "career:sumsub": ConfiguredCompanySourceConfig(
        source_id="career:sumsub",
        company="Sumsub",
        platform="teamtailor",
        board_url="https://careers.sumsub.com/jobs",
        career_url="https://careers.sumsub.com/jobs",
    ),
    "career:semrush": ConfiguredCompanySourceConfig(
        source_id="career:semrush",
        company="Semrush",
        platform="workday",
        board_url="https://semrush.wd5.myworkdayjobs.com/wday/cxs/semrush/semrushcareers/jobs",
        career_url="https://careers.semrush.com/en/jobs/",
        workday_base_url="https://semrush.wd5.myworkdayjobs.com",
        workday_tenant="semrush",
        workday_site="semrushcareers",
    ),
    "career:quadcode": ConfiguredCompanySourceConfig(
        source_id="career:quadcode",
        company="Quadcode",
        platform="lever",
        board_url="https://api.eu.lever.co/v0/postings/quadcode?mode=json",
        career_url="https://jobs.quadcode.com/jobs",
    ),
    "career:vivid-money": ConfiguredCompanySourceConfig(
        source_id="career:vivid-money",
        company="Vivid Money",
        platform="personio",
        board_url="https://vivid.jobs.personio.de/",
        career_url="https://careers.vivid.money/#vacancies",
    ),
    "career:sidestream": ConfiguredCompanySourceConfig(
        source_id="career:sidestream",
        company="Sidestream",
        platform="join",
        board_url="https://join.com/companies/sidestream",
        career_url="https://sidestream.tech/jobs",
    ),
    "career:sbk-parus": ConfiguredCompanySourceConfig(
        source_id="career:sbk-parus",
        company="ООО СБК Парус",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/6225686/vakansii",
        career_url="https://dreamjob.ru/employers/6225686/vakansii",
    ),
    "career:softmall": ConfiguredCompanySourceConfig(
        source_id="career:softmall",
        company="ООО СофтМолл",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/133227/vakansii",
        career_url="https://dreamjob.ru/employers/133227/vakansii",
    ),
    "career:retnnet": ConfiguredCompanySourceConfig(
        source_id="career:retnnet",
        company="РетнНет",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/43931/vakansii",
        career_url="https://dreamjob.ru/employers/43931/vakansii",
    ),
    "career:znanie": ConfiguredCompanySourceConfig(
        source_id="career:znanie",
        company="Российское общество Знание",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/198144/vakansii",
        career_url="https://dreamjob.ru/employers/198144/vakansii",
    ),
    "career:nii-spetsvuzavtomatika": ConfiguredCompanySourceConfig(
        source_id="career:nii-spetsvuzavtomatika",
        company="ФГАНУ НИИ Спецвузавтоматика",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/121279/vakansii",
        career_url="https://dreamjob.ru/employers/121279/vakansii",
    ),
    "career:social-discovery-group": ConfiguredCompanySourceConfig(
        source_id="career:social-discovery-group",
        company="Social Discovery Group",
        platform="jsonld_jobposting",
        board_url="https://socialdiscoverygroup.com/vacancies",
        career_url="https://socialdiscoverygroup.com/vacancies",
    ),
    "career:prequel": ConfiguredCompanySourceConfig(
        source_id="career:prequel",
        company="Prequel",
        platform="ycombinator",
        board_url="https://www.ycombinator.com/companies/prequel/jobs",
        career_url="https://www.ycombinator.com/companies/prequel/jobs",
    ),
    "career:veryfi": ConfiguredCompanySourceConfig(
        source_id="career:veryfi",
        company="Veryfi",
        platform="ycombinator",
        board_url="https://www.ycombinator.com/companies/veryfi-inc/jobs",
        career_url="https://www.ycombinator.com/companies/veryfi-inc/jobs",
    ),
}


def configured_company_source(source_id: str) -> SourceScraper:
    try:
        config = CONFIGURED_COMPANY_SOURCE_CONFIGS[source_id]
    except KeyError as exc:
        raise ValueError(f"unknown configured company source: {source_id}") from exc
    if config.platform == "workday":
        return ConfiguredWorkdayCompanyCareerSource(config)
    if config.platform == "personio":
        return ConfiguredPersonioCompanyCareerSource(config)
    if config.platform == "join":
        return ConfiguredJoinCompanyCareerSource(config)
    if config.platform == "dreamjob":
        return ConfiguredDreamJobCompanyCareerSource(config)
    return ConfiguredCompanyCareerSource(config)


def configured_company_career_urls() -> dict[str, str]:
    return {
        source_id: config.career_url
        for source_id, config in CONFIGURED_COMPANY_SOURCE_CONFIGS.items()
    }


class ConfiguredCompanyCareerSource(SourceScraper):
    def __init__(self, config: ConfiguredCompanySourceConfig) -> None:
        self._config = config

    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(self._config.source_id)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(self._config.source_id)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=self._config.board_url,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        if self._config.platform == "lever":
            return _parse_lever(response.body, self._config)
        if self._config.platform == "ashby":
            return _parse_ashby(response.body, self._config)
        if self._config.platform == "workable":
            return _parse_workable(response.body, self._config)
        if self._config.platform == "greenhouse":
            return _parse_greenhouse(response.body, self._config)
        if self._config.platform == "bamboohr":
            return _parse_bamboohr(response.body, self._config)
        if self._config.platform == "teamtailor":
            return _parse_teamtailor(response.body, self._config, _request)
        if self._config.platform == "jsonld_jobposting":
            return _parse_jsonld_job_postings(response.body, self._config)
        if self._config.platform == "ycombinator":
            return _parse_ycombinator(response.body, self._config)
        raise ValueError(f"unsupported configured company platform: {self._config.platform}")


class ConfiguredWorkdayCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: ConfiguredCompanySourceConfig) -> None:
        self._config = config

    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(self._config.source_id)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(self._config.source_id)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=self._config.board_url,
                method=HttpMethod.POST,
                headers=dict(_WORKDAY_SEARCH_HEADERS),
                body=_workday_search_body(offset=0),
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        return _parse_workday(response.body, self._config, request)

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        detail_url = _text(listing.raw.get("workday_cxs_detail_url")).strip()
        if not detail_url:
            raise ValueError(f"{self._config.company} Workday listing is missing detail URL")
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=detail_url,
            headers={"Accept": "application/json"},
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        return _workday_detail_listing(response.body, listing, self._config)


class ConfiguredPersonioCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: ConfiguredCompanySourceConfig) -> None:
        self._config = config

    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(self._config.source_id)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(self._config.source_id)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=self._config.board_url,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        return _parse_personio(response.body, self._config)

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        detail_url = _text(listing.raw.get("personio_detail_url")).strip()
        if not detail_url:
            raise ValueError(f"{self._config.company} Personio listing is missing detail URL")
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=detail_url,
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        return _personio_detail_listing(response.body, listing, self._config)


class ConfiguredJoinCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: ConfiguredCompanySourceConfig) -> None:
        self._config = config

    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(self._config.source_id)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(self._config.source_id)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=self._config.board_url,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        return _parse_join(response.body, self._config)

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        detail_url = _text(listing.raw.get("join_detail_url")).strip()
        if not detail_url:
            raise ValueError(f"{self._config.company} JOIN listing is missing detail URL")
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=detail_url,
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        return _join_detail_listing(response.body, listing, self._config)


class ConfiguredDreamJobCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: ConfiguredCompanySourceConfig) -> None:
        self._config = config

    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(self._config.source_id)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(self._config.source_id)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=self._config.board_url,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        return _parse_dreamjob(response.body, self._config, request)

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        detail_url = _text(listing.raw.get("dreamjob_detail_url")).strip()
        if not detail_url:
            raise ValueError(f"{self._config.company} DreamJob listing is missing detail URL")
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=detail_url,
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        return _dreamjob_detail_listing(response.body, listing, self._config)


def _parse_lever(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    postings = _json_array(body, f"{config.company} Lever response")
    if not postings:
        return _no_results()
    listings = tuple(_lever_listing(posting, config) for posting in postings if isinstance(posting, dict))
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _lever_listing(posting: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    posting_id = _required_text(posting.get("id"), "id", config)
    title = _required_text(posting.get("text"), "text", config)
    categories = _dict_value(posting.get("categories"))
    all_locations = _text_values(categories.get("allLocations"))
    location_text = _lever_location_text(categories.get("location"), all_locations)
    workplace_type = _text(posting.get("workplaceType")).strip().casefold() or None
    work_formats = _work_formats_from_workplace_type(workplace_type)
    remote_locations = _lever_remote_locations(workplace_type=workplace_type, all_locations=all_locations)
    sections = _lever_sections(posting.get("lists"))
    requirements = _requirements(sections, config.requirements_label_markers)
    description = _join_text(
        _plain_text(posting.get("openingPlain")),
        _plain_text(posting.get("descriptionBodyPlain")),
        _plain_text(posting.get("additionalPlain")),
    )
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": posting_id,
            "apply_url": _text(posting.get("applyUrl")).strip() or None,
            "created_at": posting.get("createdAt"),
            "department": _text(categories.get("department")).strip() or None,
            "team": _text(categories.get("team")).strip() or None,
            "commitment": _text(categories.get("commitment")).strip() or None,
            "all_locations": all_locations,
            "lever_country": _text(posting.get("country")).strip() or None,
            "workplace_type": workplace_type,
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting_id,
        title=title,
        url=strip_query(_required_text(posting.get("hostedUrl"), "hostedUrl", config)),
        source=config.source_id,
        company=config.company,
        country=None,
        city=None,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_posted_at_from_millis(posting.get("createdAt")),
        remote_in_country=_remote_in_country(
            work_format=_single_format(work_formats),
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global(work_format=_single_format(work_formats), remote_locations=remote_locations),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections=sections,
        skills=(),
        raw_text=_join_text(
            title,
            _text(categories.get("department")),
            _text(categories.get("team")),
            location_text,
            " ".join(all_locations),
            description,
            requirements,
        ),
        raw=raw,
    )


def _parse_ashby(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} Ashby response")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError(f"{config.company} Ashby response jobs field is not a JSON array")
    visible_jobs = tuple(job for job in jobs if isinstance(job, dict) and job.get("isListed") is not False)
    if not visible_jobs:
        return _no_results()
    listings = tuple(_ashby_listing(job, config) for job in visible_jobs)
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _ashby_listing(job: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    source_listing_id = _text(job.get("id")).strip()
    title = _text(job.get("title")).strip()
    url = _text(job.get("jobUrl")).strip()
    if not source_listing_id or not title or not url:
        raise ValueError(f"{config.company} Ashby job is missing id, title, or jobUrl")

    description_html = _text(job.get("descriptionHtml"))
    description = html_to_text(description_html)
    additional_sections = _html_sections(description_html)
    primary_location = _ashby_primary_location(job)
    secondary_locations = _ashby_secondary_locations(job.get("secondaryLocations"))
    locations = (primary_location, *secondary_locations)
    location_text = _location_text(locations)
    workplace_type = _text(job.get("workplaceType")).strip()
    work_format = _work_format_from_workplace_type(workplace_type)
    if work_format is None and job.get("isRemote") is True:
        work_format = "remote"
    if work_format is None and _title_has_remote_marker(title):
        work_format = "remote"
    remote_locations = _ashby_remote_locations(locations, work_format)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": source_listing_id,
            "job_id": _text(job.get("jobId")).strip() or None,
            "department": _text(job.get("department")).strip() or None,
            "team": _text(job.get("team")).strip() or None,
            "employment_type": _text(job.get("employmentType")).strip() or None,
            "workplace_type": workplace_type or None,
            "is_remote": job.get("isRemote"),
            "locations": tuple(location.raw for location in locations),
            "should_display_compensation": job.get("shouldDisplayCompensationOnJobBoard"),
            "compensation_tier_summary": job.get("compensationTierSummary"),
        }
    )
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=config.source_id,
        company=config.company,
        country=primary_location.country,
        city=primary_location.city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(job.get("publishedAt")).strip() or None,
        remote_in_country=_remote_in_country(work_format=work_format, remote_locations=remote_locations),
        remote_global=_remote_global(work_format=work_format, remote_locations=remote_locations),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=_requirements(additional_sections, config.requirements_label_markers),
        additional_sections=additional_sections,
        skills=(),
        raw_text=_join_text(
            title,
            _text(job.get("department")),
            _text(job.get("team")),
            _text(job.get("employmentType")),
            workplace_type,
            location_text,
            description,
        ),
        raw=raw,
    )


def _parse_workable(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    rows = _workable_table_rows(body)
    if not rows:
        return _no_results()
    listings = tuple(_workable_listing(row, config) for row in rows)
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _workable_listing(row: dict[str, str], config: ConfiguredCompanySourceConfig) -> RawListing:
    title = _required_cell(row, "Title", config)
    detail_url, source_listing_id = _workable_detail_url_and_id(_required_cell(row, "Details", config), config)
    location = _workable_location(_required_cell(row, "Location", config))
    work_format = _work_format_from_location_marker(location.workplace)
    salary_text = _salary_text_from_workable(row.get("Salary", ""))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "detail_markdown_url": detail_url,
            "department": _normalized_cell(row.get("Department", "")),
            "employment_type": _normalized_cell(row.get("Type", "")),
            "salary": _normalized_cell(row.get("Salary", "")),
            "workplace": location.workplace,
            "work_format": (work_format,),
            "location": row["Location"],
        }
    )
    if location.workplace == "remote" and location.cleaned_location:
        raw["remote_locations"] = (location.cleaned_location,)

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=f"https://apply.workable.com/{config.workable_slug}/j/{source_listing_id}",
        source=config.source_id,
        company=config.company,
        country=location.country,
        city=location.city,
        location_text=location.location_text,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_normalized_cell(row.get("Posted", "")),
        remote_in_country=bool(location.workplace == "remote" and location.cleaned_location),
        remote_global=False,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            title,
            raw["department"],
            location.location_text,
            raw["employment_type"],
            location.workplace,
        ),
        raw=raw,
    )


def _parse_greenhouse(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} Greenhouse response")
    raw_jobs = payload.get("jobs")
    meta = payload.get("meta")
    if not isinstance(raw_jobs, list):
        raise ValueError(f"{config.company} Greenhouse payload jobs field is malformed")
    if not raw_jobs and isinstance(meta, dict) and meta.get("total") == 0:
        return _no_results()
    if not raw_jobs:
        return _no_results()
    listings = tuple(_greenhouse_listing(job, config) for job in raw_jobs if isinstance(job, dict))
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _greenhouse_listing(job: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    source_listing_id = str(job.get("id") or "")
    title = _required_text(job.get("title"), "title", config)
    location_text = _nested_text(job, "location", "name")
    content = _text(job.get("content"))
    visible_content = html.unescape(content)
    description = html_to_text(visible_content)
    additional_sections = _html_sections(visible_content)
    requirements = _requirements(additional_sections, config.requirements_label_markers)
    salary_text = _greenhouse_salary_text(additional_sections)
    departments = _names(job.get("departments"))
    offices = _names(job.get("offices"))
    remote_locations = _greenhouse_remote_locations(location_text)
    work_formats = _greenhouse_work_formats(location_text)
    country, city = _greenhouse_country_city(location_text)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": job.get("id"),
            "internal_job_id": job.get("internal_job_id"),
            "requisition_id": job.get("requisition_id"),
            "updated_at": job.get("updated_at"),
            "departments": departments,
            "offices": offices,
            "location": location_text,
        }
    )
    if remote_locations:
        raw["remote_locations"] = remote_locations
    if work_formats:
        raw["work_format"] = work_formats

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=_required_text(job.get("absolute_url"), "absolute_url", config),
        source=config.source_id,
        company=_text(job.get("company_name")).strip() or config.company,
        country=country,
        city=city,
        location_text=location_text or None,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(job.get("first_published")).strip() or _text(job.get("updated_at")).strip() or None,
        remote_in_country=_greenhouse_remote_in_country(location_text),
        remote_global=_greenhouse_remote_global(location_text),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections=additional_sections,
        skills=(),
        raw_text=_join_text(title, location_text, " ".join(departments), description, requirements, salary_text),
        raw=raw,
    )


def _parse_workday(
    body: str,
    config: ConfiguredCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} Workday response")
    postings = payload.get("jobPostings")
    if not isinstance(postings, list):
        raise ValueError(f"{config.company} Workday response jobPostings field is not a JSON array")
    total = _int_value(payload, "total")
    if not postings and total == 0:
        return _no_results()
    if not postings:
        raise ValueError(f"{config.company} Workday response has no postings without an explicit empty total")

    listings = tuple(_workday_listing(posting, config) for posting in postings if isinstance(posting, dict))
    if not listings:
        raise ValueError(f"{config.company} Workday response contains no valid posting objects")
    return SourceSearchParseResult(
        outcome=SourceOutcome.SUCCESS,
        listings=listings,
        next_request=_workday_next_request(payload, request),
    )


def _workday_listing(posting: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    title = _required_text(posting.get("title"), "title", config)
    external_path = _required_text(posting.get("externalPath"), "externalPath", config)
    source_listing_id = _workday_source_listing_id(posting, external_path)
    location_text = _text(posting.get("locationsText")).strip() or None
    bullet_fields = _text_values(posting.get("bulletFields"))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "external_path": external_path,
            "workday_cxs_detail_url": _workday_cxs_detail_url(config, external_path),
            "locations_text": location_text,
            "posted_on": _text(posting.get("postedOn")).strip() or None,
            "time_type": _text(posting.get("timeType")).strip() or None,
            "bullet_fields": bullet_fields,
        }
    )
    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=_workday_public_job_url(config, external_path),
        source=config.source_id,
        company=config.company,
        country=None,
        city=None,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=None,
        remote_global=None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, location_text, raw["time_type"], raw["posted_on"], " ".join(bullet_fields)),
        raw=raw,
    )


def _workday_detail_listing(
    body: str,
    listing: RawListing,
    config: ConfiguredCompanySourceConfig,
) -> RawListing:
    payload = _json_object(body, f"{config.company} Workday detail response")
    info = payload.get("jobPostingInfo")
    if not isinstance(info, dict):
        raise ValueError(f"{config.company} Workday detail response is missing jobPostingInfo")
    description_html = _text(info.get("jobDescription"))
    description = html_to_text(description_html)
    if not description:
        raise ValueError(f"{config.company} Workday detail response is missing jobDescription")

    sections = _html_sections(description_html)
    locations = _workday_detail_locations(info, listing)
    country = _workday_country_descriptor(info)
    remote_type = _text(info.get("remoteType")).strip() or None
    raw_detail = {
        "id": _text(info.get("id")).strip() or None,
        "job_posting_id": _text(info.get("jobPostingId")).strip() or None,
        "job_req_id": _text(info.get("jobReqId")).strip() or None,
        "posted_on": _text(info.get("postedOn")).strip() or None,
        "start_date": _text(info.get("startDate")).strip() or None,
        "time_type": _text(info.get("timeType")).strip() or None,
        "location": _text(info.get("location")).strip() or None,
        "additional_locations": _text_values(info.get("additionalLocations")),
        "country": info.get("country"),
        "remote_type": remote_type,
        "hiring_organization": payload.get("hiringOrganization"),
    }
    raw = {**listing.raw, "detail": raw_detail}
    if locations:
        raw["locations"] = locations
    if country:
        raw["country"] = country
    if remote_type:
        raw["remote_type"] = remote_type
    if remote_type and remote_type.casefold() == "remote" and locations:
        raw["remote_locations"] = locations

    location_text = "; ".join(locations) or listing.location_text
    return replace(
        listing,
        url=_text(info.get("externalUrl")).strip() or listing.url,
        country=country or listing.country,
        city=_workday_city(info, country),
        location_text=location_text,
        posted_at=_text(info.get("startDate")).strip() or listing.posted_at,
        description=description,
        requirements=_requirements(sections, config.requirements_label_markers),
        additional_sections=sections,
        raw_text=_join_text(listing.raw_text, location_text, remote_type, description),
        raw=raw,
    )


def _parse_personio(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    listings = tuple(
        listing
        for match in _PERSONIO_ITEM_RE.finditer(body)
        for listing in (_personio_listing(match.group("attrs"), match.group("body"), config),)
        if listing is not None
    )
    if not listings and _personio_no_results(body):
        return _no_results()
    if not listings:
        raise ValueError(f"{config.company} Personio response contains no job links")
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _personio_listing(attrs: str, item_html: str, config: ConfiguredCompanySourceConfig) -> RawListing | None:
    href_match = _PERSONIO_HREF_RE.search(attrs)
    if href_match is None:
        return None
    title_match = _PERSONIO_TITLE_RE.search(item_html)
    if title_match is None:
        return None

    source_listing_id = href_match.group("id")
    title = html_to_text(title_match.group("title"))
    if not title:
        return None

    metadata = tuple(
        text
        for match in _PERSONIO_META_RE.finditer(item_html)
        for text in (html_to_text(match.group("body")),)
        if text
    )
    location_text = metadata[0] if metadata else None
    employment_type = _metadata_value(metadata, _PERSONIO_EMPLOYMENT_TYPE_INDEX)
    contract_type = _metadata_value(metadata, _PERSONIO_CONTRACT_TYPE_INDEX)
    work_format = _personio_work_format(title=title, location_text=location_text)
    remote_locations = _personio_remote_locations(location_text, work_format)
    url = strip_query(urljoin(config.board_url, href_match.group("href")))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": source_listing_id,
            "personio_detail_url": url,
            "location": location_text,
            "employment_type": employment_type,
            "contract_type": contract_type,
        }
    )
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=config.source_id,
        company=config.company,
        country=None,
        city=_personio_single_city(location_text),
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=_remote_in_country(work_format=work_format, remote_locations=remote_locations),
        remote_global=_remote_global(work_format=work_format, remote_locations=remote_locations),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, location_text, employment_type, contract_type),
        raw=raw,
    )


def _personio_detail_listing(
    body: str,
    listing: RawListing,
    config: ConfiguredCompanySourceConfig,
) -> RawListing:
    posting = _personio_job_posting(body, config)
    description_html = _required_text(posting.get("description"), "description", config)
    description = html_to_text(description_html)
    if not description:
        raise ValueError(f"{config.company} Personio detail response has empty description")

    sections = _html_sections(description_html)
    locations = _personio_job_locations(posting.get("jobLocation"))
    location_text = _location_text(locations) or listing.location_text
    country = _personio_country_text(locations) or listing.country
    city = _personio_city_text(locations) or listing.city
    title = _text(posting.get("title")).strip() or listing.title
    date_posted = _text(posting.get("datePosted")).strip() or listing.posted_at
    work_format = _personio_work_format(title=title, location_text=location_text)
    remote_locations = _personio_remote_locations_from_detail(locations, work_format) or _personio_remote_locations(
        location_text,
        work_format,
    )
    salary_text = _personio_salary_text(sections)
    raw_detail = {
        "identifier": posting.get("identifier"),
        "hiring_organization": posting.get("hiringOrganization"),
        "employment_type": posting.get("employmentType"),
        "date_posted": date_posted,
        "job_locations": tuple(location.raw for location in locations),
    }
    raw = {**listing.raw, "detail": raw_detail}
    if locations:
        raw["locations"] = tuple(location.raw for location in locations)
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations
    if country:
        raw["country"] = country

    return replace(
        listing,
        title=title,
        country=country,
        city=city,
        location_text=location_text,
        salary_text=salary_text,
        posted_at=date_posted,
        remote_in_country=_remote_in_country(work_format=work_format, remote_locations=remote_locations),
        remote_global=_remote_global(work_format=work_format, remote_locations=remote_locations),
        description=description,
        requirements=_requirements(sections, config.requirements_label_markers),
        additional_sections=sections,
        raw_text=_join_text(listing.raw_text, location_text, work_format, description, salary_text),
        raw=raw,
    )


def _parse_bamboohr(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} BambooHR response")
    items = payload.get("result")
    if not isinstance(items, list):
        raise ValueError(f"{config.company} BambooHR response result is not a JSON array")
    total_count = _int_value(payload.get("meta"), "totalCount")
    if not items and total_count == 0:
        return _no_results()
    if not items:
        raise ValueError(f"{config.company} BambooHR response has no result rows without an explicit empty count")
    listings = tuple(_bamboohr_listing(item, config) for item in items if isinstance(item, dict))
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _bamboohr_listing(item: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    listing_id = str(item.get("id") or "").strip()
    title = _text(item.get("jobOpeningName")).strip()
    if not listing_id or not title:
        raise ValueError(f"{config.company} BambooHR listing is missing id or jobOpeningName")

    department = _text(item.get("departmentLabel")).strip() or None
    employment_status = _text(item.get("employmentStatusLabel")).strip() or None
    work_format = _bamboohr_work_format(item, employment_status)
    location_text = _bamboohr_location_text(item)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "department": department,
            "employment_status": employment_status,
            "location": item.get("location"),
            "ats_location": item.get("atsLocation"),
            "is_remote": item.get("isRemote"),
            "location_type": item.get("locationType"),
        }
    )
    if work_format:
        raw["work_format"] = (work_format,)

    detail_template = config.bamboohr_detail_url_template
    if not detail_template:
        raise ValueError(f"{config.company} BambooHR config is missing detail URL template")

    return RawListing(
        source_listing_id=listing_id,
        title=title,
        url=detail_template.format(id=listing_id),
        source=config.source_id,
        company=config.company,
        country=None,
        city=None,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=None,
        remote_global=None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, department, employment_status, location_text),
        raw=raw,
    )


def _parse_teamtailor(
    body: str,
    config: ConfiguredCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    listings = tuple(
        listing
        for item in _TEAMTAILOR_ITEM_RE.finditer(body)
        for listing in (_teamtailor_listing(item.group("body"), config),)
        if listing is not None
    )
    if not listings:
        return _no_results()
    return SourceSearchParseResult(
        outcome=SourceOutcome.SUCCESS,
        listings=listings,
        next_request=_teamtailor_next_request(body, config, request),
    )


def _teamtailor_listing(item_html: str, config: ConfiguredCompanySourceConfig) -> RawListing | None:
    link_match = _TEAMTAILOR_LINK_RE.search(item_html)
    if link_match is None:
        return None
    title = _teamtailor_title(link_match.group("title"))
    if not title:
        return None
    metadata = _teamtailor_metadata(item_html)
    work_format = _teamtailor_work_format(metadata.workplace)
    remote_locations = _teamtailor_remote_locations(metadata.location_text, work_format)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "department": metadata.department,
            "location": metadata.location_text,
            "workplace": metadata.workplace,
        }
    )
    if work_format:
        raw["work_format"] = (work_format,)
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=link_match.group("id"),
        title=title,
        url=link_match.group("url"),
        source=config.source_id,
        company=config.company,
        country=None,
        city=None,
        location_text=metadata.location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=True if work_format == "remote" and remote_locations else None,
        remote_global=False if work_format == "remote" and remote_locations else None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, metadata.department, metadata.location_text, metadata.workplace),
        raw=raw,
    )


@dataclass(frozen=True)
class _Location:
    name: str | None
    country: str | None
    city: str | None

    @property
    def raw(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "country": self.country,
            "city": self.city,
        }


@dataclass(frozen=True)
class _WorkableLocation:
    city: str | None
    country: str | None
    location_text: str
    cleaned_location: str | None
    workplace: str


@dataclass(frozen=True)
class _TeamtailorMetadata:
    department: str | None
    location_text: str | None
    workplace: str | None


@dataclass(frozen=True)
class _DreamJobCard:
    source_listing_id: str
    title: str
    url: str
    salary_text: str | None
    city: str | None
    location_text: str | None
    posted_text: str | None
    tags: tuple[str, ...]
    snippet: str | None


def _personio_no_results(body: str) -> bool:
    return "no positions at the moment" in (html_to_text(body) or "").casefold()


def _metadata_value(values: tuple[str, ...], index: int) -> str | None:
    return values[index] if len(values) > index else None


def _personio_work_format(*, title: str, location_text: str | None) -> str | None:
    combined = f"{title} {location_text or ''}".casefold()
    if "remote" in combined:
        return "remote"
    if "hybrid" in combined:
        return "hybrid"
    return None


def _personio_remote_locations(location_text: str | None, work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote" or not location_text:
        return ()
    values: list[str] = []
    for part in re.split(r"[,;]", location_text):
        cleaned = part.strip()
        if not cleaned or cleaned.casefold() in {"remote", "hybrid"}:
            continue
        if cleaned not in values:
            values.append(cleaned)
    return tuple(values)


def _personio_single_city(location_text: str | None) -> str | None:
    if not location_text or "," in location_text or ";" in location_text:
        return None
    if location_text.casefold() in {"remote", "hybrid"}:
        return None
    return location_text


def _personio_job_posting(body: str, config: ConfiguredCompanySourceConfig) -> dict[str, Any]:
    for match in _PERSONIO_JSON_LD_RE.finditer(body):
        try:
            value = json.loads(html.unescape(match.group("body")))
        except json.JSONDecodeError:
            continue
        posting = _personio_job_posting_from_json(value)
        if posting is not None:
            return posting
    raise ValueError(f"{config.company} Personio detail response is missing JobPosting JSON-LD")


def _personio_job_posting_from_json(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        item_type = value.get("@type")
        if item_type == "JobPosting":
            return value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                posting = _personio_job_posting_from_json(item)
                if posting is not None:
                    return posting
    if isinstance(value, list):
        for item in value:
            posting = _personio_job_posting_from_json(item)
            if posting is not None:
                return posting
    return None


def _personio_job_locations(value: object) -> tuple[_Location, ...]:
    items = value if isinstance(value, list) else [value]
    locations: list[_Location] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not isinstance(address, dict):
            continue
        city = _text(address.get("addressLocality")).strip() or None
        country = _text(address.get("addressCountry")).strip() or None
        name = _personio_location_name(city=city, country=country)
        if name or country or city:
            locations.append(_Location(name=name, country=country, city=city))
    return tuple(locations)


def _personio_location_name(*, city: str | None, country: str | None) -> str | None:
    if city and country:
        return f"{city}, {country}"
    return city or country


def _personio_country_text(locations: tuple[_Location, ...]) -> str | None:
    countries: list[str] = []
    for location in locations:
        if location.country and location.country not in countries:
            countries.append(location.country)
    return ", ".join(countries) or None


def _personio_city_text(locations: tuple[_Location, ...]) -> str | None:
    cities: list[str] = []
    for location in locations:
        if location.city and location.city.casefold() != "remote" and location.city not in cities:
            cities.append(location.city)
    return ", ".join(cities) or None


def _personio_remote_locations_from_detail(
    locations: tuple[_Location, ...],
    work_format: str | None,
) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    values: list[str] = []
    for location in locations:
        value = location.country or location.city or location.name
        if value and value.casefold() != "remote" and value not in values:
            values.append(value)
    return tuple(values)


def _personio_salary_text(sections: dict[str, str]) -> str | None:
    parts: list[str] = []
    for label, body in sections.items():
        if "compensation" not in label.casefold():
            continue
        salary_body = _salary_body(body)
        if salary_body:
            parts.append(f"{label}\n{salary_body}")
    return "\n\n".join(parts) or None


def _parse_join(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    payload = _join_next_data(body, config)
    jobs = _join_jobs(payload, config)
    if not jobs:
        return _no_results()
    listings = tuple(_join_listing(job, config) for job in jobs if isinstance(job, dict))
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _join_listing(item: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    source_listing_id = str(item.get("id") or "").strip()
    if not source_listing_id:
        raise ValueError(f"{config.company} JOIN posting is missing id")
    id_param = _required_text(item.get("idParam"), "idParam", config)
    title = _required_text(item.get("title"), "title", config)
    workplace_type = _text(item.get("workplaceType")).strip() or None
    remote_type = _text(item.get("remoteType")).strip() or None
    work_format = _work_format_from_workplace_type(workplace_type or "")
    city = _join_city(item)
    country = _join_country_code(item) or _join_country_name(item)
    country_name = _join_country_name(item)
    remote_locations = _join_remote_locations(
        work_format=work_format,
        remote_type=remote_type,
        city=city,
        country=country,
        country_name=country_name,
    )
    location_text = _join_location_text(
        work_format=work_format,
        city=city,
        country=country,
        country_name=country_name,
        remote_locations=remote_locations,
    )
    category = _join_nested_name(item.get("category"))
    employment_type = _join_nested_name(item.get("employmentType"))
    url = strip_query(urljoin(config.board_url.rstrip("/") + "/", id_param))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": source_listing_id,
            "id_param": id_param,
            "join_detail_url": url,
            "workplace_type": workplace_type,
            "remote_type": remote_type,
            "city": item.get("city"),
            "country": item.get("country"),
            "employment_type": employment_type,
            "category": category,
            "salary_frequency": _text(item.get("salaryFrequency")).strip() or None,
            "settings": item.get("settings"),
        }
    )
    if work_format:
        raw["work_format"] = (work_format,)
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=config.source_id,
        company=config.company,
        country=country,
        city=city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(item.get("createdAt")).strip() or None,
        remote_in_country=_join_remote_in_country(
            work_format=work_format,
            remote_type=remote_type,
            remote_locations=remote_locations,
        ),
        remote_global=_join_remote_global(
            work_format=work_format,
            remote_type=remote_type,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, location_text, workplace_type, remote_type, category, employment_type),
        raw=raw,
    )


def _join_detail_listing(
    body: str,
    listing: RawListing,
    config: ConfiguredCompanySourceConfig,
) -> RawListing:
    payload = _join_next_data(body, config)
    job = _join_detail_job(payload, config)
    posting = _join_job_posting(body)
    title = _text(job.get("title")).strip() or _text(posting.get("title")).strip() or listing.title
    description_html = _text(job.get("schemaDescription")).strip() or _text(posting.get("description")).strip()
    description = (
        html_to_text(html.unescape(description_html))
        if description_html
        else _text(job.get("description")).strip()
    )
    if not description:
        raise ValueError(f"{config.company} JOIN detail response is missing description")
    sections = _html_sections(description_html) if description_html else {}
    workplace_type = _text(job.get("workplaceType")).strip() or _text(listing.raw.get("workplace_type")).strip() or None
    remote_type = _text(job.get("remoteType")).strip() or _text(listing.raw.get("remote_type")).strip() or None
    work_format = _work_format_from_workplace_type(workplace_type or "")
    city = _join_city(job) or listing.city
    country = _join_country_code(job) or listing.country or _join_country_name(job)
    country_name = _join_country_name(job) or _join_applicant_country(posting)
    remote_locations = _join_remote_locations(
        work_format=work_format,
        remote_type=remote_type,
        city=city,
        country=country,
        country_name=country_name,
    )
    location_text = _join_location_text(
        work_format=work_format,
        city=city,
        country=country,
        country_name=country_name,
        remote_locations=remote_locations,
    )
    raw_detail = {
        "id": job.get("id"),
        "id_param": job.get("idParam"),
        "employment_type": job.get("employmentType"),
        "category": job.get("category"),
        "country": job.get("country"),
        "city": job.get("city"),
        "office": job.get("office"),
        "salary_frequency": job.get("salaryFrequency"),
        "settings": job.get("settings"),
        "job_location_type": posting.get("jobLocationType"),
        "applicant_location_requirements": posting.get("applicantLocationRequirements"),
    }
    raw = {**listing.raw, "detail": raw_detail}
    if work_format:
        raw["work_format"] = (work_format,)
    if remote_locations:
        raw["remote_locations"] = remote_locations
    if country:
        raw["country"] = country

    return replace(
        listing,
        title=title,
        url=strip_query(_text(posting.get("url")).strip() or listing.url),
        country=country,
        city=city,
        location_text=location_text,
        posted_at=_text(job.get("createdAt")).strip() or _text(posting.get("datePosted")).strip() or listing.posted_at,
        remote_in_country=_join_remote_in_country(
            work_format=work_format,
            remote_type=remote_type,
            remote_locations=remote_locations,
        ),
        remote_global=_join_remote_global(
            work_format=work_format,
            remote_type=remote_type,
            remote_locations=remote_locations,
        ),
        description=description,
        requirements=_text(job.get("requirements")).strip()
        or _requirements(sections, config.requirements_label_markers),
        additional_sections=sections,
        raw_text=_join_text(listing.raw_text, location_text, workplace_type, remote_type, description),
        raw=raw,
    )


def _join_next_data(body: str, config: ConfiguredCompanySourceConfig) -> dict[str, Any]:
    match = _JOIN_NEXT_DATA_RE.search(body)
    if match is None:
        raise ValueError(f"{config.company} JOIN response is missing __NEXT_DATA__")
    try:
        value = json.loads(html.unescape(match.group("body")))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{config.company} JOIN __NEXT_DATA__ is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{config.company} JOIN __NEXT_DATA__ is not a JSON object")
    return value


def _join_initial_state(payload: dict[str, Any], config: ConfiguredCompanySourceConfig) -> dict[str, Any]:
    props = _dict_value(payload.get("props"))
    page_props = _dict_value(props.get("pageProps"))
    initial_state = page_props.get("initialState")
    if not isinstance(initial_state, dict):
        raise ValueError(f"{config.company} JOIN __NEXT_DATA__ is missing initialState")
    return initial_state


def _join_jobs(payload: dict[str, Any], config: ConfiguredCompanySourceConfig) -> list[Any]:
    jobs = _join_initial_state(payload, config).get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError(f"{config.company} JOIN initialState is missing jobs")
    items = jobs.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{config.company} JOIN jobs.items is not a JSON array")
    return items


def _join_detail_job(payload: dict[str, Any], config: ConfiguredCompanySourceConfig) -> dict[str, Any]:
    job = _join_initial_state(payload, config).get("job")
    if not isinstance(job, dict):
        raise ValueError(f"{config.company} JOIN detail initialState is missing job")
    return job


def _join_job_posting(body: str) -> dict[str, Any]:
    for match in _JOIN_JSON_LD_RE.finditer(body):
        try:
            value = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        posting = _personio_job_posting_from_json(value)
        if posting is not None:
            return posting
    return {}


def _join_city(item: dict[str, Any]) -> str | None:
    city = _dict_value(item.get("city"))
    value = _text(city.get("cityName")).strip()
    if value:
        return value
    office = _dict_value(item.get("office"))
    office_city = _dict_value(office.get("city"))
    return _text(office_city.get("cityName")).strip() or None


def _join_country_code(item: dict[str, Any]) -> str | None:
    country = _dict_value(item.get("country"))
    value = _text(country.get("iso3166")).strip()
    if value:
        return value
    office = _dict_value(item.get("office"))
    office_city = _dict_value(office.get("city"))
    return _text(office_city.get("countryCode")).strip().upper() or None


def _join_country_name(item: dict[str, Any]) -> str | None:
    country = _dict_value(item.get("country"))
    value = _text(country.get("name")).strip()
    if value:
        return value
    city = _dict_value(item.get("city"))
    value = _text(city.get("countryName")).strip()
    if value:
        return value
    office = _dict_value(item.get("office"))
    office_city = _dict_value(office.get("city"))
    return _text(office_city.get("countryName")).strip() or None


def _join_nested_name(value: object) -> str | None:
    item = _dict_value(value)
    return _text(item.get("name")).strip() or None


def _join_applicant_country(posting: dict[str, Any]) -> str | None:
    requirements = posting.get("applicantLocationRequirements")
    items = requirements if isinstance(requirements, list) else [requirements]
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _text(item.get("name")).strip()
        if value:
            return value
    return None


def _join_remote_locations(
    *,
    work_format: str | None,
    remote_type: str | None,
    city: str | None,
    country: str | None,
    country_name: str | None,
) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    normalized_remote_type = (remote_type or "").casefold()
    if normalized_remote_type in {"worldwide", "global"}:
        return ()
    if normalized_remote_type == "city" and city:
        return (city,)
    value = country_name or country
    return (value,) if value else ()


def _join_location_text(
    *,
    work_format: str | None,
    city: str | None,
    country: str | None,
    country_name: str | None,
    remote_locations: tuple[str, ...],
) -> str | None:
    if work_format == "remote":
        if remote_locations:
            return f"Remote ({', '.join(remote_locations)})"
        return "Remote"
    if city and country_name:
        return f"{city}, {country_name}"
    if city and country:
        return f"{city}, {country}"
    return city or country_name or country


def _join_remote_in_country(
    *,
    work_format: str | None,
    remote_type: str | None,
    remote_locations: tuple[str, ...],
) -> bool | None:
    if work_format != "remote":
        return None
    normalized_remote_type = (remote_type or "").casefold()
    if normalized_remote_type in {"worldwide", "global"}:
        return False
    return True if remote_locations else None


def _join_remote_global(
    *,
    work_format: str | None,
    remote_type: str | None,
    remote_locations: tuple[str, ...],
) -> bool | None:
    if work_format != "remote":
        return None
    normalized_remote_type = (remote_type or "").casefold()
    if normalized_remote_type in {"worldwide", "global"}:
        return True
    return False if remote_locations else None


def _parse_jsonld_job_postings(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    postings = tuple(_jsonld_job_postings(body))
    if not postings:
        raise ValueError(f"{config.company} JSON-LD response contains no JobPosting objects")
    listings = tuple(
        listing
        for posting in postings
        for listing in (_jsonld_job_posting_listing(posting, config),)
        if listing is not None
    )
    if not listings:
        raise ValueError(f"{config.company} JSON-LD response contains no valid JobPosting listings")
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _jsonld_job_postings(body: str) -> tuple[dict[str, Any], ...]:
    postings: list[dict[str, Any]] = []
    for match in _JOB_POSTING_JSON_LD_RE.finditer(body):
        try:
            value = json.loads(html.unescape(match.group("body").strip()))
        except json.JSONDecodeError:
            continue
        postings.extend(_jsonld_job_postings_from_value(value))
    return tuple(postings)


def _jsonld_job_postings_from_value(value: object) -> tuple[dict[str, Any], ...]:
    postings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        item_type = value.get("@type")
        item_types = item_type if isinstance(item_type, list) else [item_type]
        if "JobPosting" in item_types:
            postings.append(value)
        for item in value.values():
            postings.extend(_jsonld_job_postings_from_value(item))
    elif isinstance(value, list):
        for item in value:
            postings.extend(_jsonld_job_postings_from_value(item))
    return tuple(postings)


def _jsonld_job_posting_listing(
    posting: dict[str, Any],
    config: ConfiguredCompanySourceConfig,
) -> RawListing | None:
    title = _text(posting.get("title")).strip() or _text(posting.get("name")).strip()
    url = _text(posting.get("url")).strip()
    source_listing_id = _jsonld_identifier(posting)
    if not title or not url or not source_listing_id:
        return None

    description_html = _text(posting.get("description")).strip()
    description = None if description_html == "~" else html_to_text(description_html)
    sections = _html_sections(description_html) if description_html and description_html != "~" else {}
    locations = _jsonld_locations(posting.get("jobLocation"))
    location_text = _location_text(locations)
    work_format = _jsonld_work_format(posting)
    remote_locations = _jsonld_remote_locations(posting, work_format)
    salary_text = _jsonld_salary_text(posting.get("baseSalary"))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "identifier": posting.get("identifier"),
            "date_posted": _text(posting.get("datePosted")).strip() or None,
            "valid_through": _text(posting.get("validThrough")).strip() or None,
            "employment_type": posting.get("employmentType"),
            "direct_apply": posting.get("directApply"),
            "hiring_organization": posting.get("hiringOrganization"),
            "job_location": posting.get("jobLocation"),
            "job_location_type": posting.get("jobLocationType"),
            "applicant_location_requirements": posting.get("applicantLocationRequirements"),
        }
    )
    if locations:
        raw["locations"] = tuple(location.raw for location in locations)
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations
    if salary_text:
        raw["salary"] = salary_text

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=strip_query(url),
        source=config.source_id,
        company=_jsonld_hiring_organization_name(posting) or config.company,
        country=_joined_unique(location.country for location in locations),
        city=_joined_unique(location.city for location in locations),
        location_text=location_text,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(posting.get("datePosted")).strip() or None,
        remote_in_country=_remote_in_country(work_format=work_format, remote_locations=remote_locations),
        remote_global=_remote_global(work_format=work_format, remote_locations=remote_locations),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=_requirements(sections, config.requirements_label_markers),
        additional_sections=sections,
        skills=(),
        raw_text=_join_text(
            title,
            location_text,
            _text(posting.get("employmentType")),
            description,
            salary_text,
        ),
        raw=raw,
    )


def _jsonld_identifier(posting: dict[str, Any]) -> str | None:
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        value = _text(identifier.get("value")).strip()
        if value and value != "~":
            return value
    value = _text(identifier).strip()
    if value and value != "~":
        return value
    url = _text(posting.get("url")).strip()
    if url:
        return strip_query(url).rstrip("/").rsplit("/", 1)[-1] or None
    return None


def _jsonld_locations(value: object) -> tuple[_Location, ...]:
    items = value if isinstance(value, list) else [value]
    locations: list[_Location] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = _dict_value(item.get("address"))
        city = _text(address.get("addressLocality")).strip() or None
        country = _text(address.get("addressCountry")).strip() or None
        region = _text(address.get("addressRegion")).strip()
        name = _text(item.get("name")).strip() or None
        if name is None:
            name = _jsonld_location_name(city=city, country=country, region=region)
        if name or country or city:
            locations.append(_Location(name=name, country=country, city=city))
    return tuple(locations)


def _jsonld_location_name(*, city: str | None, country: str | None, region: str) -> str | None:
    values = tuple(value for value in (city, region or None, country) if value)
    return ", ".join(values) or None


def _jsonld_work_format(posting: dict[str, Any]) -> str | None:
    location_type = _text(posting.get("jobLocationType")).strip().casefold()
    if location_type == "telecommute":
        return "remote"
    return None


def _jsonld_remote_locations(posting: dict[str, Any], work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    locations = _jsonld_locations(posting.get("applicantLocationRequirements"))
    return tuple(
        dict.fromkeys(
            value
            for location in locations
            for value in (location.name or location.country or location.city,)
            if value
        )
    )


def _jsonld_salary_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    currency = _text(value.get("currency")).strip()
    amount = value.get("value")
    if isinstance(amount, dict):
        min_value = _text(amount.get("minValue")).strip()
        max_value = _text(amount.get("maxValue")).strip()
        unit = _text(amount.get("unitText")).strip()
        values = " - ".join(value for value in (min_value, max_value) if value)
        return " ".join(value for value in (currency, values, unit) if value) or None
    text = _text(amount).strip()
    return " ".join(value for value in (currency, text) if value) or None


def _jsonld_hiring_organization_name(posting: dict[str, Any]) -> str | None:
    organization = posting.get("hiringOrganization")
    if not isinstance(organization, dict):
        return None
    return _text(organization.get("name")).strip() or None


def _parse_ycombinator(body: str, config: ConfiguredCompanySourceConfig) -> SourceSearchParseResult:
    payload = _ycombinator_data_page(body, config)
    props = _dict_value(payload.get("props"))
    jobs = props.get("jobPostings")
    if not isinstance(jobs, list):
        raise ValueError(f"{config.company} Y Combinator data-page is missing jobPostings")
    if not jobs:
        return _no_results()
    listings = tuple(_ycombinator_listing(job, config) for job in jobs if isinstance(job, dict))
    if not listings:
        raise ValueError(f"{config.company} Y Combinator data-page contains no valid posting objects")
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _ycombinator_data_page(body: str, config: ConfiguredCompanySourceConfig) -> dict[str, Any]:
    match = _YCOMBINATOR_DATA_PAGE_RE.search(body)
    if match is None:
        raise ValueError(f"{config.company} Y Combinator response is missing WaasShowJobsPage data-page")
    try:
        value = json.loads(html.unescape(match.group("body")))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{config.company} Y Combinator data-page is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{config.company} Y Combinator data-page is not a JSON object")
    return value


def _ycombinator_listing(item: dict[str, Any], config: ConfiguredCompanySourceConfig) -> RawListing:
    source_listing_id = str(item.get("id") or "").strip()
    if not source_listing_id:
        raise ValueError(f"{config.company} {config.platform} posting is missing id")
    title = _required_text(item.get("title"), "title", config)
    relative_url = _required_text(item.get("url"), "url", config)
    location_text = _text(item.get("location")).strip() or None
    salary_text = _text(item.get("salaryRange")).strip() or None
    work_format = "remote" if location_text and "remote" in location_text.casefold() else None
    remote_locations = _ycombinator_remote_locations(location_text, work_format)
    skills = _text_values(item.get("skills"))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": item.get("id"),
            "apply_url": _text(item.get("applyUrl")).strip() or None,
            "location": location_text,
            "type": _text(item.get("type")).strip() or None,
            "role": _text(item.get("role")).strip() or None,
            "role_specific_type": _text(item.get("roleSpecificType")).strip() or None,
            "pretty_role": _text(item.get("prettyRole")).strip() or None,
            "salary_range": salary_text,
            "equity_range": _text(item.get("equityRange")).strip() or None,
            "min_experience": _text(item.get("minExperience")).strip() or None,
            "visa": _text(item.get("visa")).strip() or None,
            "created_at": _text(item.get("createdAt")).strip() or None,
            "last_active": _text(item.get("lastActive")).strip() or None,
            "company_url": _text(item.get("companyUrl")).strip() or None,
            "company_batch_name": _text(item.get("companyBatchName")).strip() or None,
            "company_one_liner": _text(item.get("companyOneLiner")).strip() or None,
        }
    )
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=strip_query(urljoin("https://www.ycombinator.com", relative_url)),
        source=config.source_id,
        company=_text(item.get("companyName")).strip() or config.company,
        country=None,
        city=None,
        location_text=location_text,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=True if work_format == "remote" and remote_locations else None,
        remote_global=_remote_global(work_format=work_format, remote_locations=remote_locations),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=skills,
        raw_text=_join_text(
            title,
            location_text,
            _text(item.get("type")),
            _text(item.get("prettyRole")),
            salary_text,
            _text(item.get("minExperience")),
            " ".join(skills),
        ),
        raw=raw,
    )


def _ycombinator_remote_locations(location_text: str | None, work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote" or not location_text:
        return ()
    match = _YCOMBINATOR_REMOTE_LOCATION_RE.search(location_text)
    if match is None:
        return ()
    values: list[str] = []
    for item in re.split(r"[/;]", match.group("locations")):
        value = item.strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _joined_unique(values: Any) -> str | None:
    unique: list[str] = []
    for value in values:
        text = _text(value).strip()
        if text and text not in unique:
            unique.append(text)
    return ", ".join(unique) or None


def _parse_dreamjob(
    body: str,
    config: ConfiguredCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    parser = _DreamJobListParser(config=config)
    parser.feed(body)
    cards = parser.cards()
    if not cards:
        raise ValueError(f"{config.company} DreamJob response contains no vacancy cards")
    listings = tuple(_dreamjob_listing(card, config) for card in cards)
    return SourceSearchParseResult(
        outcome=SourceOutcome.SUCCESS,
        listings=listings,
        next_request=_dreamjob_next_request(parser.page_links, config, request),
    )


def _dreamjob_listing(card: _DreamJobCard, config: ConfiguredCompanySourceConfig) -> RawListing:
    salary_min, salary_max, salary_currency = _dreamjob_salary_parts(card.salary_text)
    work_format = _dreamjob_work_format(card)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": card.source_listing_id,
            "dreamjob_detail_url": card.url,
            "city": card.city,
            "published_text": card.posted_text,
            "tags": card.tags,
            "snippet": card.snippet,
            "salary_text": card.salary_text,
        }
    )
    if work_format:
        raw["work_format"] = work_format

    return RawListing(
        source_listing_id=card.source_listing_id,
        title=card.title,
        url=card.url,
        source=config.source_id,
        company=config.company,
        country=None,
        city=card.city,
        location_text=card.location_text,
        salary_text=card.salary_text,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=None,
        remote_in_country=None,
        remote_global=None,
        relocation=None,
        native_grade=None,
        description=card.snippet,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            card.title,
            card.salary_text,
            card.location_text,
            card.posted_text,
            " ".join(card.tags),
            card.snippet,
        ),
        raw=raw,
    )


def _dreamjob_detail_listing(
    body: str,
    listing: RawListing,
    config: ConfiguredCompanySourceConfig,
) -> RawListing:
    posting = _dreamjob_job_posting(body, config)
    title = _text(posting.get("title")).strip() or listing.title
    description_html = _text(posting.get("description")).strip()
    description = html_to_text(description_html)
    if not description:
        raise ValueError(f"{config.company} DreamJob detail response is missing description")
    sections = _html_sections(description_html)
    city, country = _dreamjob_job_location(posting)
    date_posted = _text(posting.get("datePosted")).strip() or listing.posted_at
    hiring_organization = posting.get("hiringOrganization")
    raw = {
        **listing.raw,
        "detail": {
            "date_posted": date_posted,
            "job_location": posting.get("jobLocation"),
            "hiring_organization": hiring_organization,
        },
    }
    if city:
        raw["city"] = city
    if country:
        raw["country"] = country

    return replace(
        listing,
        title=title,
        country=country or listing.country,
        city=city or listing.city,
        location_text=_dreamjob_location_text(city=city or listing.city, country=country or listing.country)
        or listing.location_text,
        posted_at=date_posted,
        description=description,
        requirements=_requirements(sections, config.requirements_label_markers),
        additional_sections=sections,
        raw_text=_join_text(listing.raw_text, city, country, date_posted, description),
        raw=raw,
    )


class _DreamJobListParser(HTMLParser):
    def __init__(self, *, config: ConfiguredCompanySourceConfig) -> None:
        super().__init__(convert_charrefs=True)
        self.page_links: list[str] = []
        self._config = config
        self._cards: list[_DreamJobCard] = []
        self._card: dict[str, object] | None = None
        self._card_depth = 0
        self._capture_field: str | None = None
        self._capture_depth = 0
        self._capture_buffer: list[str] = []

    def cards(self) -> tuple[_DreamJobCard, ...]:
        return tuple(self._cards)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        self._collect_page_link(tag, attrs_dict)
        classes = set(attrs_dict.get("class", "").split())
        if self._card is None:
            if tag == "div" and "vacancy" in classes:
                self._card = {"tags": []}
                self._card_depth = 1
            return

        if tag not in _HTML_VOID_TAGS:
            self._card_depth += 1

        if self._capture_field is not None:
            if tag not in _HTML_VOID_TAGS:
                self._capture_depth += 1
            return

        if tag == "h3" and "vacancy__head" in classes:
            self._card["detail_path"] = attrs_dict.get("data-link", "")
            self._start_capture("title")
        elif tag == "div" and "vacancy__salary" in classes:
            self._start_capture("salary")
        elif tag == "div" and "tags__item" in classes:
            self._start_capture("tag")
        elif tag == "p" and "line-clamp-3" in classes:
            self._start_capture("snippet")
        elif tag == "div" and "vacancy__city" in classes:
            self._start_capture("city")
        elif tag == "div" and "vacancy__date" in classes:
            self._start_capture("date")

    def handle_data(self, data: str) -> None:
        if self._capture_field is not None:
            self._capture_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if self._capture_field is not None and tag not in _HTML_VOID_TAGS:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                self._finish_capture()

        if tag not in _HTML_VOID_TAGS:
            self._card_depth -= 1
        if self._card_depth == 0:
            self._finish_card()

    def _collect_page_link(self, tag: str, attrs: dict[str, str]) -> None:
        if tag != "a":
            return
        href = attrs.get("href", "")
        if "/vakansii?page=" not in href:
            return
        url = urljoin(self._config.board_url, html.unescape(href))
        if url not in self.page_links:
            self.page_links.append(url)

    def _start_capture(self, field: str) -> None:
        self._capture_field = field
        self._capture_depth = 1
        self._capture_buffer = []

    def _finish_capture(self) -> None:
        assert self._card is not None
        assert self._capture_field is not None
        text = _normalize_space(" ".join(self._capture_buffer))
        if self._capture_field == "tag":
            tags = self._card.get("tags")
            if isinstance(tags, list) and text:
                tags.append(text)
        elif text:
            self._card[self._capture_field] = text
        self._capture_field = None
        self._capture_buffer = []

    def _finish_card(self) -> None:
        assert self._card is not None
        detail_path = _text(self._card.get("detail_path")).strip()
        title = _text(self._card.get("title")).strip()
        match = _DREAMJOB_VACANCIES_PATH_RE.search(detail_path)
        if not match or not title:
            self._card = None
            return
        city_raw = _text(self._card.get("city")).strip()
        city = _dreamjob_city(city_raw)
        location_text = _dreamjob_location_text(city=city, country=None) or _normalize_city_text(city_raw)
        raw_tags = self._card.get("tags")
        tags = tuple(
            _text(tag).strip()
            for tag in (raw_tags if isinstance(raw_tags, list) else [])
            if _text(tag).strip()
        )
        card = _DreamJobCard(
            source_listing_id=match.group("id") or "",
            title=title,
            url=strip_query(urljoin(self._config.board_url, html.unescape(detail_path))),
            salary_text=_normalize_salary_text(_text(self._card.get("salary"))),
            city=city,
            location_text=location_text,
            posted_text=_normalize_dreamjob_posted_text(_text(self._card.get("date"))),
            tags=tags,
            snippet=_normalize_space(_text(self._card.get("snippet"))) or None,
        )
        if card.source_listing_id:
            self._cards.append(card)
        self._card = None


def _dreamjob_next_request(
    page_links: list[str],
    config: ConfiguredCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    current_page = _dreamjob_page_number(request.url)
    candidates = [
        (page, link)
        for link in page_links
        for page in (_dreamjob_page_number(link),)
        if page > current_page
    ]
    if not candidates:
        return None
    _page, url = min(candidates, key=lambda item: item[0])
    return SourceFetchRequest(
        source_id=config.source_id,
        query_variant=request.query_variant,
        url=url,
    )


def _dreamjob_page_number(url: str) -> int:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("page", [])
    for value in values:
        try:
            page = int(value)
        except ValueError:
            continue
        return page
    return 1


def _dreamjob_salary_parts(salary_text: str | None) -> tuple[int | None, int | None, str | None]:
    if not salary_text:
        return None, None, None
    currency = "RUB" if any(marker in salary_text.casefold() for marker in _DREAMJOB_RUB_CURRENCY_MARKERS) else None
    values = [_int_from_salary_token(token) for token in re.findall(r"\d[\d\s]*", salary_text)]
    values = [value for value in values if value is not None]
    if not values:
        return None, None, currency
    normalized = salary_text.casefold()
    if normalized.startswith("от "):
        return values[0], None, currency
    if normalized.startswith("до "):
        return None, values[0], currency
    if len(values) >= _SALARY_RANGE_VALUE_COUNT:
        return values[0], values[1], currency
    return values[0], values[0], currency


def _int_from_salary_token(value: str) -> int | None:
    digits = "".join(char for char in value if char.isdigit())
    return int(digits) if digits else None


def _dreamjob_work_format(card: _DreamJobCard) -> str | None:
    tag_keys = {tag.casefold() for tag in card.tags}
    if tag_keys & _DREAMJOB_REMOTE_TAGS:
        return "remote"
    if card.location_text and card.location_text.casefold() in {"удаленно", "удалённо", "remote"}:
        return "remote"
    return None


def _dreamjob_job_posting(body: str, config: ConfiguredCompanySourceConfig) -> dict[str, Any]:
    for match in _JOIN_JSON_LD_RE.finditer(body):
        try:
            value = json.loads(match.group("body").strip())
        except json.JSONDecodeError:
            continue
        posting = _personio_job_posting_from_json(value)
        if posting is not None:
            return posting
    raise ValueError(f"{config.company} DreamJob detail response is missing JobPosting JSON-LD")


def _dreamjob_job_location(posting: dict[str, Any]) -> tuple[str | None, str | None]:
    locations = posting.get("jobLocation")
    items = locations if isinstance(locations, list) else [locations]
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not isinstance(address, dict):
            continue
        city = _text(address.get("addressLocality")).strip() or None
        country = _text(address.get("addressCountry")).strip() or None
        if city or country:
            return city, country
    return None, None


def _dreamjob_city(value: str) -> str | None:
    normalized = _normalize_city_text(value)
    if not normalized:
        return None
    if normalized.casefold() in {"удаленно", "удалённо", "remote"}:
        return None
    return normalized


def _dreamjob_location_text(*, city: str | None, country: str | None) -> str | None:
    if city and country:
        return f"{city}, {country}"
    return city or country


def _normalize_city_text(value: str) -> str | None:
    text = _normalize_space(value).rstrip(",")
    return text or None


def _normalize_salary_text(value: str) -> str | None:
    text = _normalize_space(value)
    return text or None


def _normalize_dreamjob_posted_text(value: str) -> str | None:
    text = _normalize_space(value)
    return text or None


def _normalize_space(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _json_array(body: str, label: str) -> list[Any]:
    value = json.loads(body)
    if not isinstance(value, list):
        raise ValueError(f"{label} is not a JSON array")
    return value


def _json_object(body: str, label: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object")
    return value


def _no_results() -> SourceSearchParseResult:
    return SourceSearchParseResult(
        outcome=SourceOutcome.NO_RESULTS,
        listings=(),
        evidence=AttemptEvidence(no_results=True),
    )


def _source_raw(config: ConfiguredCompanySourceConfig) -> dict[str, object]:
    return {
        "ats_platform": config.platform,
        "board_url": config.board_url,
        "career_url": config.career_url,
    }


def _dict_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _lever_sections(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    sections: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("text")).strip().rstrip(":")
        content = html_to_text(_text(item.get("content")))
        if label and content:
            sections[label] = content
    return sections


def _requirements(sections: dict[str, str], markers: tuple[str, ...]) -> str | None:
    parts = [body for label, body in sections.items() if any(marker in label.casefold() for marker in markers)]
    return "\n".join(parts) or None


def _work_formats_from_workplace_type(workplace_type: str | None) -> tuple[str, ...]:
    work_format = _work_format_from_workplace_type(workplace_type or "")
    return (work_format,) if work_format else ()


def _work_format_from_workplace_type(value: str) -> str | None:
    normalized = value.casefold()
    if normalized == "remote":
        return "remote"
    if normalized == "hybrid":
        return "hybrid"
    if normalized in {"onsite", "on-site", "office"}:
        return "office"
    return None


def _title_has_remote_marker(title: str) -> bool:
    return bool(_REMOTE_TITLE_MARKER_RE.search(title))


def _single_format(values: tuple[str, ...]) -> str | None:
    return values[0] if len(values) == 1 else None


def _lever_remote_locations(*, workplace_type: str | None, all_locations: tuple[str, ...]) -> tuple[str, ...]:
    if workplace_type != "remote":
        return ()
    return tuple(
        cleaned
        for location in all_locations
        for cleaned in (_clean_remote_scope(location),)
        if cleaned is not None
    )


def _lever_location_text(primary_location: object, all_locations: tuple[str, ...]) -> str | None:
    if all_locations:
        return ", ".join(all_locations)
    return _text(primary_location).strip() or None


def _remote_in_country(*, work_format: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if work_format != "remote":
        return None
    return True if any(_has_structured_remote_scope(location) for location in remote_locations) else None


def _remote_global(*, work_format: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if work_format != "remote":
        return None
    return False if remote_locations else None


def _posted_at_from_millis(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _ashby_primary_location(job: dict[str, Any]) -> _Location:
    return _ashby_location(
        name=_text(job.get("location")).strip() or None,
        address=job.get("address"),
    )


def _ashby_secondary_locations(value: object) -> tuple[_Location, ...]:
    if not isinstance(value, list):
        return ()
    locations: list[_Location] = []
    for item in value:
        if isinstance(item, dict):
            locations.append(
                _ashby_location(
                    name=_text(item.get("location")).strip() or None,
                    address=item.get("address"),
                )
            )
    return tuple(locations)


def _ashby_location(*, name: str | None, address: object) -> _Location:
    postal_address = _postal_address(address)
    country = _text(postal_address.get("addressCountry")).strip() or None
    city = _text(postal_address.get("addressLocality")).strip() or None
    cleaned_name = _strip_workplace_marker(name)
    country = _ashby_visible_country(cleaned_name=cleaned_name, address_country=country)
    if cleaned_name and _is_region_label(cleaned_name):
        city = None
    if city is None and country and cleaned_name and cleaned_name != country and not _is_region_label(cleaned_name):
        city = cleaned_name
    return _Location(name=name, country=country, city=city)


def _ashby_visible_country(*, cleaned_name: str | None, address_country: str | None) -> str | None:
    if not cleaned_name:
        return address_country
    if _is_region_label(cleaned_name):
        return None
    if address_country and _has_hidden_country_conflict(cleaned_name=cleaned_name, address_country=address_country):
        return cleaned_name
    if address_country:
        return address_country
    return None


def _postal_address(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    postal_address = value.get("postalAddress")
    return postal_address if isinstance(postal_address, dict) else {}


def _location_text(locations: tuple[_Location, ...]) -> str | None:
    values: list[str] = []
    for location in locations:
        text = location.name or location.country or location.city
        if text and text not in values:
            values.append(text)
    return "; ".join(values) or None


def _ashby_remote_locations(locations: tuple[_Location, ...], work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    values: list[str] = []
    for location in locations:
        value = location.country or location.name
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _html_sections(value: str) -> dict[str, str]:
    raw_html = html.unescape(value)
    labels = tuple(match for match in _SECTION_LABEL_RE.finditer(raw_html) if _section_label(match))
    sections: dict[str, str] = {}
    for index, match in enumerate(labels):
        label = _section_label(match)
        if label is None:
            continue
        body_start = match.end()
        body_end = labels[index + 1].start() if index + 1 < len(labels) else len(raw_html)
        body = html_to_text(raw_html[body_start:body_end])
        if body:
            sections[label] = body
    return sections


def _section_label(match: re.Match[str]) -> str | None:
    tag = match.group("tag").casefold()
    label = (html_to_text(match.group("label")) or "").rstrip(":").strip()
    if not label:
        return None
    if tag == "strong" and not match.group("label").strip().endswith(":"):
        return None
    return label


def _workable_table_rows(body: str) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    headers: tuple[str, ...] = ()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        if not headers:
            headers = cells
            continue
        if all(set(cell) <= {"-"} for cell in cells):
            continue
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells, strict=True)))
    return tuple(rows)


def _workable_detail_url_and_id(value: str, config: ConfiguredCompanySourceConfig) -> tuple[str, str]:
    if config.workable_slug is None:
        raise ValueError(f"{config.company} Workable config is missing workable_slug")
    pattern = re.compile(
        r"\[View\]\((?P<url>https://apply\.workable\.com/"
        + re.escape(config.workable_slug)
        + r"/jobs/view/(?P<id>[^/)]+)\.md)\)"
    )
    match = pattern.fullmatch(value)
    if match is None:
        raise ValueError(f"{config.company} Workable row has malformed detail link: {value}")
    return match.group("url"), match.group("id")


def _workable_location(value: str) -> _WorkableLocation:
    marker = "office"
    if "(Remote)" in value:
        marker = "remote"
    elif "(Hybrid)" in value:
        marker = "hybrid"
    cleaned = value.replace("(Remote)", "").replace("(Hybrid)", "").strip()
    if "," in cleaned:
        city, country = (part.strip() for part in cleaned.rsplit(",", 1))
        return _WorkableLocation(
            city=city or None,
            country=country or None,
            location_text=value,
            cleaned_location=cleaned or None,
            workplace=marker,
        )
    if marker == "remote":
        return _WorkableLocation(
            city=None,
            country=cleaned or None,
            location_text=value,
            cleaned_location=cleaned or None,
            workplace=marker,
        )
    return _WorkableLocation(
        city=cleaned or None,
        country=None,
        location_text=value,
        cleaned_location=cleaned or None,
        workplace=marker,
    )


def _work_format_from_location_marker(marker: str) -> str:
    if marker == "remote":
        return "remote"
    if marker == "hybrid":
        return "hybrid"
    return "office"


def _salary_text_from_workable(value: str) -> str | None:
    normalized = _normalized_cell(value)
    if normalized is None or not _SALARY_AMOUNT_RE.search(normalized):
        return None
    return normalized


def _greenhouse_salary_text(sections: dict[str, str]) -> str | None:
    parts: list[str] = []
    for label, body in sections.items():
        if not any(marker in label.casefold() for marker in _GREENHOUSE_SALARY_LABEL_MARKERS):
            continue
        salary_body = _salary_body(body)
        if salary_body and _SALARY_MARKER_RE.search(salary_body):
            parts.append(f"{label}\n{salary_body}")
    return "\n\n".join(parts) or None


def _salary_body(body: str) -> str | None:
    lines: list[str] = []
    for line in body.splitlines():
        normalized = line.strip().casefold()
        if any(normalized.startswith(marker) for marker in _SALARY_STOP_LINE_MARKERS):
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    return text or None


def _greenhouse_work_formats(location_text: str) -> tuple[str, ...]:
    work_formats: list[str] = []
    normalized = location_text.casefold()
    if "remote" in normalized:
        work_formats.append("remote")
    if "hybrid" in normalized:
        work_formats.append("hybrid")
    if "office" in normalized or "on-site" in normalized or "onsite" in normalized:
        work_formats.append("office")
    return tuple(work_formats)


def _greenhouse_remote_locations(location_text: str) -> tuple[str, ...]:
    locations: list[str] = []
    for part in location_text.split(";"):
        cleaned_part = _remote_role_scope(part)
        if cleaned_part:
            locations.extend(_split_remote_scope(cleaned_part))
    for match in _REMOTE_SCOPE_RE.finditer(location_text):
        locations.extend(_split_remote_scope(match.group("scope")))
    normalized = location_text.casefold()
    for separator in (" - ", " \u2013 ", " \u2014 "):
        marker = f"remote{separator}"
        if normalized.startswith(marker):
            locations.extend(_split_remote_scope(location_text[len(marker) :]))
    return tuple(locations)


def _remote_role_scope(value: str) -> str | None:
    stripped = value.strip()
    match = re.match(r"remote\s+role\s+(?P<scope>.+)", stripped, re.I)
    if match is None:
        return None
    scope = match.group("scope").strip()
    return scope or None


def _split_remote_scope(value: str) -> tuple[str, ...]:
    normalized = value.replace("&", ",").replace("/", ",")
    return tuple(_normalize_remote_scope_part(part) for part in normalized.split(",") if part.strip())


def _clean_remote_scope(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped.casefold() == "remote":
        return None
    cleaned = re.sub(r"^remote\s*[-:]*\s*", "", stripped, flags=re.I).strip()
    return cleaned or None


def _normalize_remote_scope_part(value: str) -> str:
    part = value.strip()
    if part.casefold().replace(".", "") in {"us", "usa"}:
        return "US"
    return part


def _greenhouse_remote_in_country(location_text: str) -> bool | None:
    if "remote" not in location_text.casefold():
        return False
    if _greenhouse_remote_locations(location_text):
        return True
    return None


def _greenhouse_remote_global(location_text: str) -> bool | None:
    if "remote" not in location_text.casefold():
        return False
    return False if _greenhouse_remote_locations(location_text) else None


def _greenhouse_country_city(location_text: str) -> tuple[str | None, str | None]:
    cleaned = _strip_workplace_marker(location_text)
    if cleaned is None or _is_region_label(cleaned):
        return None, None
    if ";" in cleaned or "remote" in cleaned.casefold():
        return None, None
    if "," not in cleaned:
        return None, cleaned or None
    city, country = (part.strip() for part in cleaned.rsplit(",", 1))
    return country or None, city or None


def _bamboohr_location_text(item: dict[str, Any]) -> str | None:
    values: list[str] = []
    for container_key in ("atsLocation", "location"):
        container = item.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in ("city", "state", "province", "country"):
            value = _text(container.get(key)).strip()
            if value and value not in values:
                values.append(value)
    return ", ".join(values) or None


def _bamboohr_work_format(item: dict[str, Any], employment_status: str | None) -> str | None:
    if item.get("isRemote") is True:
        return "remote"
    normalized = (employment_status or "").casefold()
    if "remote" in normalized:
        return "remote"
    return None


def _teamtailor_metadata(item_html: str) -> _TeamtailorMetadata:
    values: list[str] = []
    titled_locations: list[str] = []
    for match in _TEAMTAILOR_SPAN_RE.finditer(item_html):
        attrs = match.group("attrs")
        if "company-link-style" in attrs or "text-block-base-link" in attrs:
            continue
        text = html_to_text(match.group("body"))
        if not text or text == "·":
            continue
        title_match = _TITLE_ATTR_RE.search(attrs)
        if title_match is not None:
            titled_locations.append(html.unescape(title_match.group("title")).strip())
        values.append(text.strip())

    workplace = next((value for value in values if _teamtailor_work_format(value) is not None), None)
    candidates = [value for value in values if value not in {workplace, "·"}]
    location_text: str | None = None
    department: str | None = None
    if titled_locations:
        location_text = titled_locations[0]
        if candidates and candidates[0] != "Multiple locations":
            department = candidates[0]
    elif len(candidates) >= _DEPARTMENT_AND_LOCATION_VALUE_COUNT:
        department = candidates[0]
        location_text = candidates[1]
    elif candidates:
        location_text = candidates[0]
    return _TeamtailorMetadata(
        department=department,
        location_text=location_text,
        workplace=workplace,
    )


def _teamtailor_title(anchor_html: str) -> str | None:
    title_match = _TITLE_ATTR_RE.search(anchor_html)
    if title_match is not None:
        return html.unescape(title_match.group("title")).strip() or None
    return html_to_text(anchor_html)


def _teamtailor_next_request(
    body: str,
    config: ConfiguredCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    match = _TEAMTAILOR_SHOW_MORE_RE.search(body)
    if match is None:
        return None
    next_url = urljoin(config.board_url, html.unescape(match.group("href")))
    return SourceFetchRequest(
        source_id=config.source_id,
        query_variant=request.query_variant,
        url=next_url,
    )


def _teamtailor_work_format(value: str | None) -> str | None:
    normalized = (value or "").casefold()
    if "fully remote" in normalized or normalized == "remote":
        return "remote"
    if "hybrid" in normalized:
        return "hybrid"
    return None


def _teamtailor_remote_locations(location_text: str | None, work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote" or not location_text:
        return ()
    if "," not in location_text:
        return ()
    return tuple(
        part.strip()
        for part in location_text.split(",")
        if part.strip() and part.strip() != "Multiple locations"
    )


def _workday_search_body(*, offset: int) -> bytes:
    return json.dumps(
        {
            "appliedFacets": {},
            "limit": _WORKDAY_PAGE_LIMIT,
            "offset": offset,
            "searchText": "",
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _workday_next_request(payload: dict[str, Any], request: SourceFetchRequest) -> SourceFetchRequest | None:
    total = _int_value(payload, "total")
    if total is None:
        return None
    postings = payload.get("jobPostings")
    if not isinstance(postings, list):
        return None
    next_offset = _workday_request_offset(request) + len(postings)
    if next_offset >= total:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=_workday_page_url(request.url, next_offset),
        method=HttpMethod.POST,
        headers=dict(_WORKDAY_SEARCH_HEADERS),
        body=_workday_search_body(offset=next_offset),
    )


def _workday_request_offset(request: SourceFetchRequest) -> int:
    if request.body is None:
        return 0
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    offset = payload.get("offset")
    return offset if isinstance(offset, int) and offset >= 0 else 0


def _workday_source_listing_id(posting: dict[str, Any], external_path: str) -> str:
    bullet_fields = _text_values(posting.get("bulletFields"))
    if bullet_fields:
        return bullet_fields[0]
    return external_path.rstrip("/").rsplit("/", 1)[-1]


def _workday_page_url(url: str, offset: int) -> str:
    base_url = url.split("?", 1)[0]
    return f"{base_url}?offset={offset}"


def _workday_cxs_detail_url(config: ConfiguredCompanySourceConfig, external_path: str) -> str:
    base_url = _workday_config_value(config.workday_base_url, "workday_base_url", config)
    tenant = _workday_config_value(config.workday_tenant, "workday_tenant", config)
    site = _workday_config_value(config.workday_site, "workday_site", config)
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{base_url}/wday/cxs/{tenant}/{site}{path}"


def _workday_public_job_url(config: ConfiguredCompanySourceConfig, external_path: str) -> str:
    base_url = _workday_config_value(config.workday_base_url, "workday_base_url", config)
    site = _workday_config_value(config.workday_site, "workday_site", config)
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{base_url}/{site}{path}"


def _workday_detail_locations(info: dict[str, Any], listing: RawListing) -> tuple[str, ...]:
    values: list[str] = []
    for value in (_text(info.get("location")).strip(), *_text_values(info.get("additionalLocations"))):
        if value and value not in values:
            values.append(value)
    if values:
        return tuple(values)
    return (listing.location_text,) if listing.location_text else ()


def _workday_country_descriptor(info: dict[str, Any]) -> str | None:
    country = info.get("country")
    if not isinstance(country, dict):
        return None
    return _text(country.get("descriptor")).strip() or None


def _workday_city(info: dict[str, Any], country: str | None) -> str | None:
    location = _text(info.get("location")).strip()
    if not location:
        return None
    city = _workday_city_without_country_prefix(location=location, country=country)
    if country and city.casefold() == country.casefold():
        return None
    if city.casefold() == "remote" or _is_region_label(city):
        return None
    return city


def _workday_city_without_country_prefix(*, location: str, country: str | None) -> str:
    if " - " not in location:
        return location
    country_prefix, city = (part.strip() for part in location.split(" - ", 1))
    country_keys = {country.casefold()} if country else set()
    if country and country.casefold() == "united states of america":
        country_keys.add("united states")
    if country_prefix.casefold() in country_keys and city:
        return city
    return location


def _workday_config_value(value: str | None, field_name: str, config: ConfiguredCompanySourceConfig) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{config.company} Workday config is missing {field_name}")
    return value.rstrip("/")


def _strip_workplace_marker(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s*\((?:Remote|Hybrid|On-site|Onsite|Office)\)\s*", " ", value, flags=re.I)
    text = " ".join(cleaned.split())
    return text or None


def _is_region_label(value: str) -> bool:
    return value.casefold() in {"apac", "asia", "emea", "eu", "europe", "european union", "latam", "mena"}


def _has_structured_remote_scope(value: str) -> bool:
    return "," in value


def _has_hidden_country_conflict(*, cleaned_name: str, address_country: str) -> bool:
    if not _is_single_location_label(cleaned_name):
        return False
    if cleaned_name.casefold() == address_country.casefold():
        return False
    return {cleaned_name.casefold(), address_country.casefold()} == {"switzerland", "swaziland"}


def _is_single_location_label(value: str) -> bool:
    return "," not in value and ";" not in value


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        name
        for item in value
        if isinstance(item, dict)
        for name in (_text(item.get("name")).strip(),)
        if name
    )


def _nested_text(value: dict[str, Any], key: str, nested_key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return ""
    return _text(nested.get(nested_key)).strip()


def _int_value(value: object, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, int) else None


def _required_text(value: object, field_name: str, config: ConfiguredCompanySourceConfig) -> str:
    text = _text(value).strip()
    if not text:
        raise ValueError(f"{config.company} {config.platform} posting is missing {field_name}")
    return text


def _required_cell(row: dict[str, str], key: str, config: ConfiguredCompanySourceConfig) -> str:
    value = _normalized_cell(row.get(key, ""))
    if value is None:
        raise ValueError(f"{config.company} Workable row is missing {key}")
    return value


def _normalized_cell(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped == "—":
        return None
    return stripped


def _plain_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _join_text(*parts: object) -> str | None:
    text = "\n".join(str(part).strip() for part in parts if part and str(part).strip())
    return text or None
