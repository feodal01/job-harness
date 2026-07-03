"""Company career sources backed by common public ATS surfaces."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

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

AtsPlatform = Literal[
    "lever",
    "ashby",
    "workable",
    "greenhouse",
    "bamboohr",
    "recruitee",
    "breezy",
    "huntflow",
    "smartrecruiters",
    "teamtailor",
    "workday",
    "personio",
    "join",
    "dreamjob",
    "jsonld_jobposting",
    "ycombinator",
    "comeet",
    "jobvite",
    "jazzhr",
    "icims",
    "taleo",
    "successfactors",
]

_SECTION_LABEL_RE = re.compile(
    r"<(?P<tag>h[1-6]|strong|b)[^>]*>(?P<label>.*?)</(?P=tag)>",
    re.I | re.S,
)
_REMOTE_SCOPE_RE = re.compile(r"remote\s*\((?P<scope>[^)]+)\)", re.I)
_LINKEDIN_TAG_RE = re.compile(r"#LI-[A-Za-z0-9_-]+", re.I)
_LINKEDIN_WORKPLACE_TAGS = frozenset({"#li-hybrid", "#li-onsite", "#li-remote"})
_SALARY_CURRENCY_CODE_RE = r"USD|CAD|EUR|GBP|HUF"
_SALARY_AMOUNT_RE = re.compile(
    rf"([$£€]\s?\d|\b(?:{_SALARY_CURRENCY_CODE_RE})\b|\b\d[\d,]*(?:k|K)?\s?(?:{_SALARY_CURRENCY_CODE_RE})\b)",
    re.I,
)
_SALARY_MARKER_RE = re.compile(rf"(\$|£|€|\b(?:{_SALARY_CURRENCY_CODE_RE})\b|hourly rate)", re.I)
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
_JOBVITE_PENDING_SHOW_MORE_HEADER = "x-jobvite-pending-show-more-urls"
_JOBVITE_JOB_PATH_PARTS_MIN = 3
_JAZZHR_DETAIL_PATH_PARTS_MIN = 4
_US_STATE_CODE_LENGTH = 2
_TALEO_DIRECT_FIELD_DEPTH = 2
_DEFAULT_TALEO_PARALLEL_PAGINATION_WINDOW = 1
_YCOMBINATOR_DATA_PAGE_RE = re.compile(
    r'<div[^>]+id="WaasShowJobsPage[^"]*"[^>]+data-page="(?P<body>.*?)"',
    re.I | re.S,
)
_BREEZY_CARD_RE = re.compile(
    r'<li[^>]+class="[^"]*\bposition\b[^"]*\btransition\b[^"]*"[^>]*>'
    r"(?P<body>.*?)</ul>\s*</li>",
    re.S,
)
_BREEZY_HREF_RE = re.compile(r'<a[^>]+href="(?P<href>/p/(?P<id>[^"]+))"')
_BREEZY_TITLE_RE = re.compile(r"<h2[^>]*>(?P<title>.*?)</h2>", re.S)
_BREEZY_LOCATION_RE = re.compile(
    r'<li[^>]+class="[^"]*\blocation\b[^"]*"[^>]*>.*?<span[^>]*>(?P<value>.*?)</span>',
    re.S,
)
_BREEZY_TYPE_RE = re.compile(
    r'<li[^>]+class="[^"]*\btype\b[^"]*"[^>]*>.*?<span[^>]*>(?P<value>.*?)</span>',
    re.S,
)
_BREEZY_SALARY_RE = re.compile(
    r'<li[^>]+class="[^"]*\bsalary-range\b[^"]*"[^>]*>.*?<span[^>]*>(?P<value>.*?)</span>',
    re.S,
)
_HUNTFLOW_CARD_RE = re.compile(
    r'<article[^>]+class="[^"]*\b_item_[^"]*"[^>]*>.*?'
    r'<h3>\s*<a[^>]+href="(?P<href>/vacancy/(?P<slug>[^"]+))"[^>]*>'
    r"(?P<title>.*?)</a>\s*</h3>\s*"
    r'<div[^>]+class="[^"]*\b_info_[^"]*"[^>]*>(?P<location>.*?)</div>',
    re.S,
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
class AtsCompanySourceConfig:
    source_id: str
    company: str
    platform: AtsPlatform
    board_url: str
    career_url: str
    requirements_label_markers: tuple[str, ...] = _DEFAULT_REQUIREMENTS_LABEL_MARKERS
    detail_mode: Literal["jobposting_jsonld"] | None = None
    strong_section_labels: tuple[str, ...] = ()
    lever_posted_at_date_only: bool = False
    lever_remote_work_format_from_location: bool = False
    lever_country_city_from_location: bool = False
    lever_city_location_names: tuple[str, ...] = ()
    lever_description_from_description_plain: bool = False
    remote_in_country_from_any_remote_location: bool = False
    non_remote_in_country: bool | None = None
    hybrid_remote_global: bool | None = None
    greenhouse_office_format_from_non_remote_location: bool = False
    greenhouse_parse_location_parts: bool = False
    greenhouse_strip_linkedin_workplace_tags: bool = False
    workable_slug: str | None = None
    bamboohr_detail_url_template: str | None = None
    workday_base_url: str | None = None
    workday_tenant: str | None = None
    workday_site: str | None = None
    source_limit: int = 200
    smartrecruiters_parallel_pagination_window: int = 2
    taleo_parallel_pagination_window: int = _DEFAULT_TALEO_PARALLEL_PAGINATION_WINDOW


ATS_COMPANY_SOURCE_CONFIGS: dict[str, AtsCompanySourceConfig] = {
    "career:jetbrains": AtsCompanySourceConfig(
        source_id="career:jetbrains",
        company="JetBrains",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/jetbrains/jobs?content=true",
        career_url="https://job-boards.eu.greenhouse.io/jetbrains",
        greenhouse_parse_location_parts=True,
        greenhouse_strip_linkedin_workplace_tags=True,
    ),
    "career:coinspaid": AtsCompanySourceConfig(
        source_id="career:coinspaid",
        company="CoinsPaid",
        platform="lever",
        board_url="https://api.eu.lever.co/v0/postings/coinspaid",
        career_url="https://jobs.eu.lever.co/coinspaid",
        lever_remote_work_format_from_location=True,
        lever_country_city_from_location=True,
        lever_city_location_names=("New York",),
        lever_description_from_description_plain=True,
        remote_in_country_from_any_remote_location=True,
        non_remote_in_country=False,
        hybrid_remote_global=False,
    ),
    "career:appfollow": AtsCompanySourceConfig(
        source_id="career:appfollow",
        company="AppFollow",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/appfollow?mode=json",
        career_url="https://jobs.lever.co/appfollow",
        detail_mode="jobposting_jsonld",
        requirements_label_markers=(
            "about you",
            "experience",
            "languages",
            "nice to have",
            "qualification",
            "requirements",
            "skills",
            "tools",
        ),
        strong_section_labels=(
            "about the role",
            "about you",
            "benefits we offer",
            "hiring process",
            "it would be nice to have",
        ),
        lever_posted_at_date_only=True,
        remote_in_country_from_any_remote_location=True,
    ),
    "career:airslate": AtsCompanySourceConfig(
        source_id="career:airslate",
        company="airSlate",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/airslate?mode=json",
        career_url="https://jobs.lever.co/airslate",
        requirements_label_markers=("need", "requirements", "expect", "looking for"),
        remote_in_country_from_any_remote_location=True,
        non_remote_in_country=False,
        hybrid_remote_global=False,
    ),
    "career:wintermute": AtsCompanySourceConfig(
        source_id="career:wintermute",
        company="Wintermute",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/wintermute-trading",
        career_url="https://jobs.lever.co/wintermute-trading",
        remote_in_country_from_any_remote_location=True,
        non_remote_in_country=False,
        hybrid_remote_global=False,
    ),
    "career:truv": AtsCompanySourceConfig(
        source_id="career:truv",
        company="Truv",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/truv?mode=json",
        career_url="https://jobs.lever.co/truv",
        requirements_label_markers=("required", "requirements", "who you are", "looking for", "preferred skills"),
        remote_in_country_from_any_remote_location=True,
    ),
    "career:termius": AtsCompanySourceConfig(
        source_id="career:termius",
        company="Termius",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/Termius?mode=json",
        career_url="https://jobs.lever.co/Termius",
        requirements_label_markers=("qualifications", "requirements", "required", "looking for"),
        remote_in_country_from_any_remote_location=True,
        non_remote_in_country=False,
    ),
    "career:outschool": AtsCompanySourceConfig(
        source_id="career:outschool",
        company="Outschool",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/outschool/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/outschool",
        requirements_label_markers=("desired experience", "required", "requirements", "skills"),
        greenhouse_office_format_from_non_remote_location=True,
    ),
    "career:zeroavia": AtsCompanySourceConfig(
        source_id="career:zeroavia",
        company="ZeroAvia",
        platform="workable",
        board_url="https://apply.workable.com/zeroavia/jobs.md",
        career_url="https://apply.workable.com/zeroavia/",
        workable_slug="zeroavia",
    ),
    "career:wallarm": AtsCompanySourceConfig(
        source_id="career:wallarm",
        company="Wallarm",
        platform="recruitee",
        board_url="https://wallarm.recruitee.com/api/offers",
        career_url="https://wallarm.recruitee.com/",
        remote_in_country_from_any_remote_location=True,
    ),
    "career:chainstack": AtsCompanySourceConfig(
        source_id="career:chainstack",
        company="Chainstack",
        platform="bamboohr",
        board_url="https://chainstack.bamboohr.com/careers/list",
        career_url="https://chainstack.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://chainstack.bamboohr.com/careers/{id}",
    ),
    "career:3commas": AtsCompanySourceConfig(
        source_id="career:3commas",
        company="3Commas",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/3commas",
        career_url="https://jobs.ashbyhq.com/3commas",
        remote_in_country_from_any_remote_location=True,
    ),
    "career:switchboard": AtsCompanySourceConfig(
        source_id="career:switchboard",
        company="Switchboard",
        platform="breezy",
        board_url="https://switchboard.breezy.hr/",
        career_url="https://switchboard.breezy.hr/",
    ),
    "career:themis-insight": AtsCompanySourceConfig(
        source_id="career:themis-insight",
        company="Themis Insight",
        platform="breezy",
        board_url="https://themis-insight.breezy.hr/",
        career_url="https://themis-insight.breezy.hr/",
    ),
    "career:apicworld": AtsCompanySourceConfig(
        source_id="career:apicworld",
        company="Apicworld",
        platform="huntflow",
        board_url="https://apicworld.huntflow.io/",
        career_url="https://apicworld.huntflow.io/",
    ),
    "career:smartrecruiters": AtsCompanySourceConfig(
        source_id="career:smartrecruiters",
        company="SmartRecruiters",
        platform="smartrecruiters",
        board_url="https://api.smartrecruiters.com/v1/companies/SmartRecruiters/postings?limit=100",
        career_url="https://jobs.smartrecruiters.com/smartrecruiters",
    ),
    "career:bosch": AtsCompanySourceConfig(
        source_id="career:bosch",
        company="Bosch",
        platform="smartrecruiters",
        board_url="https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100",
        career_url="https://jobs.smartrecruiters.com/boschgroup",
    ),
    "career:visa": AtsCompanySourceConfig(
        source_id="career:visa",
        company="Visa",
        platform="smartrecruiters",
        board_url="https://api.smartrecruiters.com/v1/companies/Visa/postings?limit=100",
        career_url="https://jobs.smartrecruiters.com/visa",
    ),
    "career:bunq": AtsCompanySourceConfig(
        source_id="career:bunq",
        company="bunq",
        platform="recruitee",
        board_url="https://bunq.recruitee.com/api/offers",
        career_url="https://bunq.recruitee.com/",
    ),
    "career:tripleten": AtsCompanySourceConfig(
        source_id="career:tripleten",
        company="TripleTen",
        platform="comeet",
        board_url="https://www.comeet.com/jobs/tripleten/98.008",
        career_url="https://www.comeet.com/jobs/tripleten/98.008",
        remote_in_country_from_any_remote_location=True,
    ),
    "career:comm-it": AtsCompanySourceConfig(
        source_id="career:comm-it",
        company="CommIT",
        platform="comeet",
        board_url="https://www.comeet.com/jobs/comm-it/76.008",
        career_url="https://www.comeet.com/jobs/comm-it/76.008",
        remote_in_country_from_any_remote_location=True,
    ),
    "career:progress": AtsCompanySourceConfig(
        source_id="career:progress",
        company="Progress",
        platform="jobvite",
        board_url="https://jobs.jobvite.com/progress/jobs",
        career_url="https://jobs.jobvite.com/progress/jobs",
        remote_in_country_from_any_remote_location=True,
    ),
    "career:visionist": AtsCompanySourceConfig(
        source_id="career:visionist",
        company="Visionist",
        platform="jobvite",
        board_url="https://jobs.jobvite.com/visionist",
        career_url="https://jobs.jobvite.com/visionist",
        remote_in_country_from_any_remote_location=True,
    ),
    "career:foundation-ai": AtsCompanySourceConfig(
        source_id="career:foundation-ai",
        company="Foundation AI",
        platform="jazzhr",
        board_url="https://foundationai.applytojob.com/apply/jobs",
        career_url="https://foundationai.applytojob.com/apply/jobs",
    ),
    "career:imanage": AtsCompanySourceConfig(
        source_id="career:imanage",
        company="iManage",
        platform="jazzhr",
        board_url="https://imanagecom.applytojob.com/apply/jobs",
        career_url="https://imanagecom.applytojob.com/apply/jobs",
    ),
    "career:pairsoft": AtsCompanySourceConfig(
        source_id="career:pairsoft",
        company="PairSoft",
        platform="jazzhr",
        board_url="https://pairsoft.applytojob.com/apply/jobs",
        career_url="https://pairsoft.applytojob.com/apply/jobs",
    ),
    "career:expleo": AtsCompanySourceConfig(
        source_id="career:expleo",
        company="Expleo",
        platform="icims",
        board_url="https://expleo-jobs-ie-en.icims.com/jobs/search?ss=1&in_iframe=1",
        career_url="https://expleo-jobs-ie-en.icims.com/jobs/search?ss=1",
    ),
    "career:epe-consulting": AtsCompanySourceConfig(
        source_id="career:epe-consulting",
        company="Electric Power Engineers",
        platform="icims",
        board_url="https://careers-epeconsulting.icims.com/jobs/search?ss=1&in_iframe=1",
        career_url="https://careers-epeconsulting.icims.com/jobs/search?ss=1",
    ),
    "career:western-southern": AtsCompanySourceConfig(
        source_id="career:western-southern",
        company="Western & Southern",
        platform="icims",
        board_url="https://careers-westernsouthern.icims.com/jobs/search?ss=1&in_iframe=1",
        career_url="https://careers-westernsouthern.icims.com/jobs/search?ss=1",
    ),
    "career:keylogic": AtsCompanySourceConfig(
        source_id="career:keylogic",
        company="KeyLogic",
        platform="taleo",
        board_url="https://phg.tbe.taleo.net/phg02/ats/careers/v2/searchResults?cws=37&org=KEYLOGIC",
        career_url="https://phg.tbe.taleo.net/phg02/ats/careers/v2/searchResults?cws=37&org=KEYLOGIC",
        remote_in_country_from_any_remote_location=True,
        taleo_parallel_pagination_window=2,
    ),
    "career:navstar": AtsCompanySourceConfig(
        source_id="career:navstar",
        company="Navstar",
        platform="taleo",
        board_url="https://phe.tbe.taleo.net/phe03/ats/careers/v2/searchResults?cws=37&org=NAVSTAR",
        career_url="https://phe.tbe.taleo.net/phe03/ats/careers/v2/searchResults?cws=37&org=NAVSTAR",
        taleo_parallel_pagination_window=3,
    ),
    "career:aurora-flight-sciences": AtsCompanySourceConfig(
        source_id="career:aurora-flight-sciences",
        company="Aurora Flight Sciences",
        platform="taleo",
        board_url="https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?cws=37&org=AURORA",
        career_url="https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?cws=37&org=AURORA",
    ),
    "career:pictet": AtsCompanySourceConfig(
        source_id="career:pictet",
        company="Pictet",
        platform="successfactors",
        board_url="https://career012.successfactors.eu/career?company=banquepict&career_ns=job_listing_summary&resultType=XML",
        career_url="https://career012.successfactors.eu/career?company=banquepict&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH",
    ),
    "career:brevard-county": AtsCompanySourceConfig(
        source_id="career:brevard-county",
        company="Brevard County",
        platform="successfactors",
        board_url="https://career8.successfactors.com/career?company=brevardcou&career_ns=job_listing_summary&resultType=XML",
        career_url="https://career8.successfactors.com/career?company=brevardcou&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH",
    ),
    "career:mindray": AtsCompanySourceConfig(
        source_id="career:mindray",
        company="Mindray",
        platform="successfactors",
        board_url="https://api2.successfactors.eu/career?company=Mindray&career_ns=job_listing_summary&resultType=XML",
        career_url="https://api2.successfactors.eu/career?company=Mindray&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH",
    ),
    "career:integrate": AtsCompanySourceConfig(
        source_id="career:integrate",
        company="Integrate",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/integrate",
        career_url="https://jobs.lever.co/integrate",
    ),
    "career:avalanche-studios": AtsCompanySourceConfig(
        source_id="career:avalanche-studios",
        company="Avalanche Studios",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/avalanchestudios?mode=json",
        career_url="https://jobs.lever.co/avalanchestudios",
    ),
    "career:teramind": AtsCompanySourceConfig(
        source_id="career:teramind",
        company="Teramind",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/teramind",
        career_url="https://jobs.lever.co/teramind",
    ),
    "career:filevine": AtsCompanySourceConfig(
        source_id="career:filevine",
        company="Filevine",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/filevine?mode=json",
        career_url="https://jobs.lever.co/filevine",
    ),
    "career:skydance": AtsCompanySourceConfig(
        source_id="career:skydance",
        company="Skydance",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/skydance?mode=json",
        career_url="https://jobs.lever.co/skydance",
    ),
    "career:ramp": AtsCompanySourceConfig(
        source_id="career:ramp",
        company="Ramp",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/ramp",
        career_url="https://jobs.ashbyhq.com/ramp",
    ),
    "career:street-child": AtsCompanySourceConfig(
        source_id="career:street-child",
        company="Street Child",
        platform="workable",
        board_url="https://apply.workable.com/streetchildcareers/jobs.md",
        career_url="https://apply.workable.com/streetchildcareers/",
        workable_slug="streetchildcareers",
    ),
    "career:pepperstone": AtsCompanySourceConfig(
        source_id="career:pepperstone",
        company="Pepperstone",
        platform="workable",
        board_url="https://apply.workable.com/pepperstone/jobs.md",
        career_url="https://apply.workable.com/pepperstone/",
        workable_slug="pepperstone",
    ),
    "career:obrela": AtsCompanySourceConfig(
        source_id="career:obrela",
        company="Obrela",
        platform="workable",
        board_url="https://apply.workable.com/obrela-security-industries-sa/jobs.md",
        career_url="https://apply.workable.com/obrela-security-industries-sa/",
        workable_slug="obrela-security-industries-sa",
    ),
    "career:grid": AtsCompanySourceConfig(
        source_id="career:grid",
        company="GRID",
        platform="recruitee",
        board_url="https://grid.recruitee.com/api/offers",
        career_url="https://grid.recruitee.com/",
    ),
    "career:hygraph": AtsCompanySourceConfig(
        source_id="career:hygraph",
        company="Hygraph",
        platform="recruitee",
        board_url="https://hygraph.recruitee.com/api/offers",
        career_url="https://hygraph.recruitee.com/",
    ),
    "career:great-minds": AtsCompanySourceConfig(
        source_id="career:great-minds",
        company="Great Minds",
        platform="recruitee",
        board_url="https://greatminds.recruitee.com/api/offers",
        career_url="https://greatminds.recruitee.com/",
    ),
    "career:apify": AtsCompanySourceConfig(
        source_id="career:apify",
        company="Apify",
        platform="bamboohr",
        board_url="https://apify.bamboohr.com/careers/list",
        career_url="https://apify.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://apify.bamboohr.com/careers/{id}",
    ),
    "career:nielseniq": AtsCompanySourceConfig(
        source_id="career:nielseniq",
        company="NielsenIQ",
        platform="smartrecruiters",
        board_url="https://api.smartrecruiters.com/v1/companies/NielsenIQ/postings?limit=100",
        career_url="https://jobs.smartrecruiters.com/NielsenIQ",
        smartrecruiters_parallel_pagination_window=4,
    ),
    "career:software-finder": AtsCompanySourceConfig(
        source_id="career:software-finder",
        company="Software Finder",
        platform="teamtailor",
        board_url="https://softwarefinder.na.teamtailor.com/jobs",
        career_url="https://softwarefinder.na.teamtailor.com/jobs",
    ),
    "career:the-studio": AtsCompanySourceConfig(
        source_id="career:the-studio",
        company="THE/STUDIO",
        platform="teamtailor",
        board_url="https://thestudio.na.teamtailor.com/jobs",
        career_url="https://thestudio.na.teamtailor.com/jobs",
    ),
    "career:realitymine": AtsCompanySourceConfig(
        source_id="career:realitymine",
        company="RealityMine",
        platform="teamtailor",
        board_url="https://realitymine.teamtailor.com/jobs",
        career_url="https://realitymine.teamtailor.com/jobs",
    ),
    "career:tixtrack": AtsCompanySourceConfig(
        source_id="career:tixtrack",
        company="TixTrack",
        platform="teamtailor",
        board_url="https://tixtrack.teamtailor.com/jobs",
        career_url="https://tixtrack.teamtailor.com/jobs",
    ),
    "career:stark": AtsCompanySourceConfig(
        source_id="career:stark",
        company="STARK",
        platform="personio",
        board_url="https://stark.jobs.personio.de/",
        career_url="https://stark.jobs.personio.de/",
    ),
    "career:entrix": AtsCompanySourceConfig(
        source_id="career:entrix",
        company="Entrix",
        platform="personio",
        board_url="https://entrix.jobs.personio.de/",
        career_url="https://entrix.jobs.personio.de/",
    ),
    "career:360t": AtsCompanySourceConfig(
        source_id="career:360t",
        company="360 Treasury Systems",
        platform="personio",
        board_url="https://360t.jobs.personio.de/",
        career_url="https://360t.jobs.personio.de/",
    ),
    "career:agile-robots": AtsCompanySourceConfig(
        source_id="career:agile-robots",
        company="Agile Robots",
        platform="personio",
        board_url="https://agile-robots-se.jobs.personio.de/",
        career_url="https://agile-robots-se.jobs.personio.de/",
    ),
    "career:moser-consulting": AtsCompanySourceConfig(
        source_id="career:moser-consulting",
        company="Moser Consulting",
        platform="breezy",
        board_url="https://moser-consulting.breezy.hr/",
        career_url="https://moser-consulting.breezy.hr/",
    ),
    "career:notably": AtsCompanySourceConfig(
        source_id="career:notably",
        company="Notably",
        platform="breezy",
        board_url="https://notably.breezy.hr/",
        career_url="https://notably.breezy.hr/",
    ),
    "career:hioperator": AtsCompanySourceConfig(
        source_id="career:hioperator",
        company="HiOperator",
        platform="breezy",
        board_url="https://hioperator.breezy.hr/",
        career_url="https://hioperator.breezy.hr/",
    ),
    "career:egnyte": AtsCompanySourceConfig(
        source_id="career:egnyte",
        company="Egnyte",
        platform="jobvite",
        board_url="https://jobs.jobvite.com/egnyte/jobs",
        career_url="https://jobs.jobvite.com/egnyte/jobs",
    ),
    "career:point-of-rental": AtsCompanySourceConfig(
        source_id="career:point-of-rental",
        company="Point of Rental",
        platform="jobvite",
        board_url="https://jobs.jobvite.com/pointofrental",
        career_url="https://jobs.jobvite.com/pointofrental",
    ),
    "career:webmd": AtsCompanySourceConfig(
        source_id="career:webmd",
        company="WebMD",
        platform="jobvite",
        board_url="https://jobs.jobvite.com/webmd",
        career_url="https://jobs.jobvite.com/webmd",
    ),
    "career:reveal": AtsCompanySourceConfig(
        source_id="career:reveal",
        company="Reveal",
        platform="jobvite",
        board_url="https://jobs.jobvite.com/reveal",
        career_url="https://jobs.jobvite.com/reveal",
    ),
    "career:nro": AtsCompanySourceConfig(
        source_id="career:nro",
        company="NRO",
        platform="jazzhr",
        board_url="https://nro.applytojob.com/apply/jobs",
        career_url="https://nro.applytojob.com/apply/jobs",
    ),
    "career:sphere": AtsCompanySourceConfig(
        source_id="career:sphere",
        company="Sphere",
        platform="jazzhr",
        board_url="https://sphere.applytojob.com/apply/jobs",
        career_url="https://sphere.applytojob.com/apply/jobs",
    ),
    "career:public-citizen": AtsCompanySourceConfig(
        source_id="career:public-citizen",
        company="Public Citizen",
        platform="jazzhr",
        board_url="https://publiccitizen.applytojob.com/apply/jobs",
        career_url="https://publiccitizen.applytojob.com/apply/jobs",
    ),
    "career:labelmaster": AtsCompanySourceConfig(
        source_id="career:labelmaster",
        company="Labelmaster",
        platform="jazzhr",
        board_url="https://labelmaster.applytojob.com/apply/jobs",
        career_url="https://labelmaster.applytojob.com/apply/jobs",
    ),
    "career:sfo": AtsCompanySourceConfig(
        source_id="career:sfo",
        company="San Francisco Airport",
        platform="icims",
        board_url="https://external-flysfo.icims.com/jobs/search?ss=1&in_iframe=1",
        career_url="https://external-flysfo.icims.com/jobs/search?ss=1",
    ),
    "career:carecentrix": AtsCompanySourceConfig(
        source_id="career:carecentrix",
        company="CareCentrix",
        platform="icims",
        board_url="https://careers-carecentrix.icims.com/jobs/search?ss=1&in_iframe=1",
        career_url="https://careers-carecentrix.icims.com/jobs/search?ss=1",
    ),
    "career:rambus": AtsCompanySourceConfig(
        source_id="career:rambus",
        company="Rambus",
        platform="icims",
        board_url="https://careers-rambus.icims.com/jobs/search?ss=1&in_iframe=1",
        career_url="https://careers-rambus.icims.com/jobs/search?ss=1",
    ),
    "career:nvidia": AtsCompanySourceConfig(
        source_id="career:nvidia",
        company="NVIDIA",
        platform="workday",
        board_url="https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs",
        career_url="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        workday_base_url="https://nvidia.wd5.myworkdayjobs.com",
        workday_tenant="nvidia",
        workday_site="NVIDIAExternalCareerSite",
    ),
    "career:instacart": AtsCompanySourceConfig(
        source_id="career:instacart",
        company="Instacart",
        platform="ycombinator",
        board_url="https://www.ycombinator.com/companies/instacart/jobs",
        career_url="https://www.ycombinator.com/companies/instacart/jobs",
    ),
    "career:vast-data": AtsCompanySourceConfig(
        source_id="career:vast-data",
        company="VAST Data",
        platform="comeet",
        board_url="https://www.comeet.com/jobs/vastdata/43.001",
        career_url="https://www.comeet.com/jobs/vastdata/43.001",
    ),
    "career:outerbox": AtsCompanySourceConfig(
        source_id="career:outerbox",
        company="OuterBox",
        platform="comeet",
        board_url="https://www.comeet.com/jobs/outerbox/49.00D",
        career_url="https://www.comeet.com/jobs/outerbox/49.00D",
    ),
    "career:surecomp": AtsCompanySourceConfig(
        source_id="career:surecomp",
        company="Surecomp",
        platform="comeet",
        board_url="https://www.comeet.com/jobs/Surecomp/24.00E",
        career_url="https://www.comeet.com/jobs/Surecomp/24.00E",
    ),
    "career:routine-labs": AtsCompanySourceConfig(
        source_id="career:routine-labs",
        company="Routine Labs",
        platform="join",
        board_url="https://join.com/companies/routinelabs",
        career_url="https://join.com/companies/routinelabs",
    ),
    "career:goodweek": AtsCompanySourceConfig(
        source_id="career:goodweek",
        company="Goodweek",
        platform="join",
        board_url="https://join.com/companies/goodweekcom",
        career_url="https://join.com/companies/goodweekcom",
    ),
    "career:yld": AtsCompanySourceConfig(
        source_id="career:yld",
        company="YLD",
        platform="join",
        board_url="https://join.com/companies/yld2",
        career_url="https://join.com/companies/yld2",
    ),
    "career:openhc": AtsCompanySourceConfig(
        source_id="career:openhc",
        company="OpenHC",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/3281388/vakansii",
        career_url="https://dreamjob.ru/employers/3281388/vakansii",
    ),
    "career:plus8soft": AtsCompanySourceConfig(
        source_id="career:plus8soft",
        company="Plus8Soft",
        platform="huntflow",
        board_url="https://plus8soft.global.huntflow.io/",
        career_url="https://plus8soft.global.huntflow.io/",
    ),
    "career:fjx-group": AtsCompanySourceConfig(
        source_id="career:fjx-group",
        company="FJX Group",
        platform="huntflow",
        board_url="https://thefjx.global.huntflow.io/",
        career_url="https://thefjx.global.huntflow.io/",
    ),
    "career:overgear": AtsCompanySourceConfig(
        source_id="career:overgear",
        company="Overgear",
        platform="huntflow",
        board_url="https://overgear.huntflow.io/",
        career_url="https://overgear.huntflow.io/",
    ),
    "career:sakura-games": AtsCompanySourceConfig(
        source_id="career:sakura-games",
        company="Sakura Games",
        platform="huntflow",
        board_url="https://sakuragames.huntflow.io/",
        career_url="https://sakuragames.huntflow.io/",
    ),
    "career:mediacom": AtsCompanySourceConfig(
        source_id="career:mediacom",
        company="Mediacom",
        platform="taleo",
        board_url="https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?cws=46&org=MEDIACOMCC",
        career_url="https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?cws=46&org=MEDIACOMCC",
    ),
    "career:internews": AtsCompanySourceConfig(
        source_id="career:internews",
        company="Internews",
        platform="taleo",
        board_url="https://phf.tbe.taleo.net/phf04/ats/careers/v2/searchResults?org=INTERNEWS&cws=38",
        career_url="https://phf.tbe.taleo.net/phf04/ats/careers/v2/searchResults?org=INTERNEWS&cws=38",
    ),
    "career:great-hearts": AtsCompanySourceConfig(
        source_id="career:great-hearts",
        company="Great Hearts",
        platform="taleo",
        board_url="https://phg.tbe.taleo.net/phg04/ats/careers/v2/searchResults?cws=40&org=GREATHEARTS",
        career_url="https://phg.tbe.taleo.net/phg04/ats/careers/v2/searchResults?cws=40&org=GREATHEARTS",
    ),
    "career:almarai": AtsCompanySourceConfig(
        source_id="career:almarai",
        company="Almarai",
        platform="successfactors",
        board_url="https://career5.successfactors.eu/career?company=AlMaraiP&career_ns=job_listing_summary&resultType=XML",
        career_url="https://career5.successfactors.eu/career?company=AlMaraiP&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH",
    ),
    "career:esa": AtsCompanySourceConfig(
        source_id="career:esa",
        company="ESA",
        platform="successfactors",
        board_url="https://career2.successfactors.eu/career?company=esa&career_ns=job_listing_summary&resultType=XML",
        career_url="https://career2.successfactors.eu/career?company=esa&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH",
    ),
    "career:collectly": AtsCompanySourceConfig(
        source_id="career:collectly",
        company="Collectly",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/CollectlyInc?mode=json",
        career_url="https://jobs.lever.co/CollectlyInc",
    ),
    "career:planner5d": AtsCompanySourceConfig(
        source_id="career:planner5d",
        company="Planner 5D",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/planner5d?mode=json",
        career_url="https://jobs.lever.co/planner5d",
    ),
    "career:superannotate": AtsCompanySourceConfig(
        source_id="career:superannotate",
        company="SuperAnnotate",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/superannotate?mode=json",
        career_url="https://jobs.lever.co/superannotate",
    ),
    "career:xsolla": AtsCompanySourceConfig(
        source_id="career:xsolla",
        company="Xsolla",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/xsolla?mode=json",
        career_url="https://jobs.lever.co/xsolla",
    ),
    "career:unlimint": AtsCompanySourceConfig(
        source_id="career:unlimint",
        company="Unlimint",
        platform="lever",
        board_url="https://api.lever.co/v0/postings/unlimit",
        career_url="https://jobs.lever.co/unlimit",
    ),
    "career:clickhouse": AtsCompanySourceConfig(
        source_id="career:clickhouse",
        company="ClickHouse",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/clickhouse",
        career_url="https://jobs.ashbyhq.com/clickhouse",
    ),
    "career:datafold": AtsCompanySourceConfig(
        source_id="career:datafold",
        company="Datafold",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/datafold",
        career_url="https://jobs.ashbyhq.com/datafold",
    ),
    "career:inworld": AtsCompanySourceConfig(
        source_id="career:inworld",
        company="Inworld AI",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/inworld-ai",
        career_url="https://jobs.ashbyhq.com/inworld-ai",
    ),
    "career:luminai": AtsCompanySourceConfig(
        source_id="career:luminai",
        company="Luminai",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/luminai",
        career_url="https://jobs.ashbyhq.com/luminai",
    ),
    "career:teleport": AtsCompanySourceConfig(
        source_id="career:teleport",
        company="Teleport",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/goteleport",
        career_url="https://jobs.ashbyhq.com/goteleport",
    ),
    "career:mapbox": AtsCompanySourceConfig(
        source_id="career:mapbox",
        company="Mapbox",
        platform="ashby",
        board_url="https://api.ashbyhq.com/posting-api/job-board/Mapbox",
        career_url="https://jobs.ashbyhq.com/Mapbox",
    ),
    "career:joom": AtsCompanySourceConfig(
        source_id="career:joom",
        company="Joom",
        platform="workable",
        board_url="https://apply.workable.com/joom/jobs.md",
        career_url="https://apply.workable.com/joom/",
        workable_slug="joom",
    ),
    "career:zeptolab": AtsCompanySourceConfig(
        source_id="career:zeptolab",
        company="ZeptoLab",
        platform="workable",
        board_url="https://apply.workable.com/zeptolab/jobs.md",
        career_url="https://apply.workable.com/zeptolab/",
        workable_slug="zeptolab",
    ),
    "career:homebuddy": AtsCompanySourceConfig(
        source_id="career:homebuddy",
        company="HomeBuddy",
        platform="workable",
        board_url="https://apply.workable.com/homebuddy/jobs.md",
        career_url="https://apply.workable.com/homebuddy/",
        workable_slug="homebuddy",
    ),
    "career:lyka": AtsCompanySourceConfig(
        source_id="career:lyka",
        company="Lyka",
        platform="workable",
        board_url="https://apply.workable.com/lyka/jobs.md",
        career_url="https://apply.workable.com/lyka/",
        workable_slug="lyka",
    ),
    "career:thesoul-publishing": AtsCompanySourceConfig(
        source_id="career:thesoul-publishing",
        company="TheSoul Publishing",
        platform="workable",
        board_url="https://apply.workable.com/thesoul-publishing-1/jobs.md",
        career_url="https://apply.workable.com/thesoul-publishing-1/",
        workable_slug="thesoul-publishing-1",
    ),
    "career:abbyy": AtsCompanySourceConfig(
        source_id="career:abbyy",
        company="ABBYY",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/abbyy/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/abbyy",
    ),
    "career:ahrefs": AtsCompanySourceConfig(
        source_id="career:ahrefs",
        company="Ahrefs",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/ahrefsjobs/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/ahrefsjobs",
    ),
    "career:eqvilent": AtsCompanySourceConfig(
        source_id="career:eqvilent",
        company="Eqvilent",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/eqvilentjobs/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/eqvilentjobs",
    ),
    "career:humansignal": AtsCompanySourceConfig(
        source_id="career:humansignal",
        company="HumanSignal",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/humansignal/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/humansignal",
    ),
    "career:lokalise": AtsCompanySourceConfig(
        source_id="career:lokalise",
        company="Lokalise",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/lokalise/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/lokalise",
    ),
    "career:flo-health": AtsCompanySourceConfig(
        source_id="career:flo-health",
        company="Flo Health",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/flohealth/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/flohealth",
    ),
    "career:pandadoc": AtsCompanySourceConfig(
        source_id="career:pandadoc",
        company="PandaDoc",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/pandadoc/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/pandadoc",
    ),
    "career:wrike": AtsCompanySourceConfig(
        source_id="career:wrike",
        company="Wrike",
        platform="greenhouse",
        board_url="https://boards-api.greenhouse.io/v1/boards/wrike/jobs?content=true",
        career_url="https://job-boards.greenhouse.io/wrike",
    ),
    "career:adtech-holding": AtsCompanySourceConfig(
        source_id="career:adtech-holding",
        company="AdTech Holding",
        platform="bamboohr",
        board_url="https://adtechholding.bamboohr.com/careers/list",
        career_url="https://adtechholding.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://adtechholding.bamboohr.com/careers/{id}",
    ),
    "career:altenar": AtsCompanySourceConfig(
        source_id="career:altenar",
        company="Altenar",
        platform="bamboohr",
        board_url="https://altenar.bamboohr.com/careers/list",
        career_url="https://altenar.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://altenar.bamboohr.com/careers/{id}",
    ),
    "career:synder": AtsCompanySourceConfig(
        source_id="career:synder",
        company="Synder",
        platform="bamboohr",
        board_url="https://synder.bamboohr.com/careers/list",
        career_url="https://synder.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://synder.bamboohr.com/careers/{id}",
    ),
    "career:onemarketdata": AtsCompanySourceConfig(
        source_id="career:onemarketdata",
        company="OneMarketData",
        platform="bamboohr",
        board_url="https://onemarketdata.bamboohr.com/careers/list",
        career_url="https://onemarketdata.bamboohr.com/careers/list",
        bamboohr_detail_url_template="https://onemarketdata.bamboohr.com/careers/{id}",
    ),
    "career:crystal": AtsCompanySourceConfig(
        source_id="career:crystal",
        company="Crystal Blockchain",
        platform="teamtailor",
        board_url="https://crystalintelligence.teamtailor.com/jobs",
        career_url="https://crystalintelligence.teamtailor.com/jobs",
    ),
    "career:synthesized": AtsCompanySourceConfig(
        source_id="career:synthesized",
        company="Synthesized",
        platform="teamtailor",
        board_url="https://synthesized.teamtailor.com/jobs",
        career_url="https://synthesized.teamtailor.com/jobs",
    ),
    "career:tradingview": AtsCompanySourceConfig(
        source_id="career:tradingview",
        company="TradingView",
        platform="teamtailor",
        board_url="https://tradingview.teamtailor.com/jobs",
        career_url="https://tradingview.teamtailor.com/jobs",
    ),
    "career:osome": AtsCompanySourceConfig(
        source_id="career:osome",
        company="Osome",
        platform="teamtailor",
        board_url="https://careers.osome.com/jobs",
        career_url="https://careers.osome.com/jobs",
    ),
    "career:sumsub": AtsCompanySourceConfig(
        source_id="career:sumsub",
        company="Sumsub",
        platform="teamtailor",
        board_url="https://careers.sumsub.com/jobs",
        career_url="https://careers.sumsub.com/jobs",
    ),
    "career:semrush": AtsCompanySourceConfig(
        source_id="career:semrush",
        company="Semrush",
        platform="workday",
        board_url="https://semrush.wd5.myworkdayjobs.com/wday/cxs/semrush/semrushcareers/jobs",
        career_url="https://careers.semrush.com/en/jobs/",
        workday_base_url="https://semrush.wd5.myworkdayjobs.com",
        workday_tenant="semrush",
        workday_site="semrushcareers",
    ),
    "career:quadcode": AtsCompanySourceConfig(
        source_id="career:quadcode",
        company="Quadcode",
        platform="lever",
        board_url="https://api.eu.lever.co/v0/postings/quadcode?mode=json",
        career_url="https://jobs.quadcode.com/jobs",
    ),
    "career:vivid-money": AtsCompanySourceConfig(
        source_id="career:vivid-money",
        company="Vivid Money",
        platform="personio",
        board_url="https://vivid.jobs.personio.de/",
        career_url="https://careers.vivid.money/#vacancies",
    ),
    "career:sidestream": AtsCompanySourceConfig(
        source_id="career:sidestream",
        company="Sidestream",
        platform="join",
        board_url="https://join.com/companies/sidestream",
        career_url="https://sidestream.tech/jobs",
    ),
    "career:sbk-parus": AtsCompanySourceConfig(
        source_id="career:sbk-parus",
        company="ООО СБК Парус",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/6225686/vakansii",
        career_url="https://dreamjob.ru/employers/6225686/vakansii",
    ),
    "career:softmall": AtsCompanySourceConfig(
        source_id="career:softmall",
        company="ООО СофтМолл",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/133227/vakansii",
        career_url="https://dreamjob.ru/employers/133227/vakansii",
    ),
    "career:retnnet": AtsCompanySourceConfig(
        source_id="career:retnnet",
        company="РетнНет",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/43931/vakansii",
        career_url="https://dreamjob.ru/employers/43931/vakansii",
    ),
    "career:znanie": AtsCompanySourceConfig(
        source_id="career:znanie",
        company="Российское общество Знание",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/198144/vakansii",
        career_url="https://dreamjob.ru/employers/198144/vakansii",
    ),
    "career:nii-spetsvuzavtomatika": AtsCompanySourceConfig(
        source_id="career:nii-spetsvuzavtomatika",
        company="ФГАНУ НИИ Спецвузавтоматика",
        platform="dreamjob",
        board_url="https://dreamjob.ru/employers/121279/vakansii",
        career_url="https://dreamjob.ru/employers/121279/vakansii",
    ),
    "career:social-discovery-group": AtsCompanySourceConfig(
        source_id="career:social-discovery-group",
        company="Social Discovery Group",
        platform="jsonld_jobposting",
        board_url="https://socialdiscoverygroup.com/vacancies",
        career_url="https://socialdiscoverygroup.com/vacancies",
    ),
    "career:prequel": AtsCompanySourceConfig(
        source_id="career:prequel",
        company="Prequel",
        platform="ycombinator",
        board_url="https://www.ycombinator.com/companies/prequel/jobs",
        career_url="https://www.ycombinator.com/companies/prequel/jobs",
    ),
    "career:veryfi": AtsCompanySourceConfig(
        source_id="career:veryfi",
        company="Veryfi",
        platform="ycombinator",
        board_url="https://www.ycombinator.com/companies/veryfi-inc/jobs",
        career_url="https://www.ycombinator.com/companies/veryfi-inc/jobs",
    ),
}


def ats_company_source(source_id: str) -> SourceScraper:
    try:
        config = ATS_COMPANY_SOURCE_CONFIGS[source_id]
    except KeyError as exc:
        raise ValueError(f"unknown ATS company source: {source_id}") from exc
    return ats_company_source_from_config(config)


def ats_company_source_from_config(config: AtsCompanySourceConfig) -> SourceScraper:
    if config.detail_mode == "jobposting_jsonld":
        return AtsJobPostingDetailCompanyCareerSource(config)
    if config.platform == "workday":
        return AtsWorkdayCompanyCareerSource(config)
    if config.platform == "personio":
        return AtsPersonioCompanyCareerSource(config)
    if config.platform == "join":
        return AtsJoinCompanyCareerSource(config)
    if config.platform == "dreamjob":
        return AtsDreamJobCompanyCareerSource(config)
    return AtsCompanyCareerSource(config)


def _config_source_limit(config: AtsCompanySourceConfig) -> int:
    if config.source_id in ATS_COMPANY_SOURCE_CONFIGS:
        return source_descriptor(config.source_id).source_limit
    return config.source_limit


def _static_board_query_variants(request: SearchRequest) -> tuple[str, ...]:
    return request.query_variants[:1]


def ats_company_initial_request(
    config: AtsCompanySourceConfig,
    *,
    query_variant: str,
) -> SourceFetchRequest:
    if config.platform == "workday":
        return SourceFetchRequest(
            source_id=config.source_id,
            query_variant=query_variant,
            url=config.board_url,
            method=HttpMethod.POST,
            headers=dict(_WORKDAY_SEARCH_HEADERS),
            body=_workday_search_body(offset=0, search_text=query_variant),
        )
    return SourceFetchRequest(
        source_id=config.source_id,
        query_variant=query_variant,
        url=config.board_url,
    )


def ats_company_career_urls() -> dict[str, str]:
    return {
        source_id: config.career_url
        for source_id, config in ATS_COMPANY_SOURCE_CONFIGS.items()
    }


class AtsCompanyCareerSource(SourceScraper):
    def __init__(self, config: AtsCompanySourceConfig) -> None:
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
            for query_variant in _static_board_query_variants(request)
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        if self._config.platform == "teamtailor":
            return _parse_teamtailor(response.body, self._config, _request)
        if self._config.platform == "jobvite":
            return _parse_jobvite(response.body, self._config, _request)
        if self._config.platform == "icims":
            return _parse_icims(response.body, self._config, _request)
        if self._config.platform == "smartrecruiters":
            return _parse_smartrecruiters(response.body, self._config, _request)
        if self._config.platform == "taleo":
            return _parse_taleo(response.body, self._config, _request)
        parser = _ATS_SEARCH_PARSERS.get(self._config.platform)
        if parser is not None:
            return parser(response.body, self._config)
        raise ValueError(f"unsupported ATS company platform: {self._config.platform}")


class AtsJobPostingDetailCompanyCareerSource(AtsCompanyCareerSource, DetailEnrichmentScraper):
    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=listing.url,
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        posting = _first_matching_jsonld_job_posting(response.body, listing)
        return _jsonld_detail_listing(posting, listing, self._config)


class AtsWorkdayCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: AtsCompanySourceConfig) -> None:
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
                body=_workday_search_body(offset=0, search_text=query_variant),
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


class AtsPersonioCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: AtsCompanySourceConfig) -> None:
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
            for query_variant in _static_board_query_variants(request)
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


class AtsJoinCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: AtsCompanySourceConfig) -> None:
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
            for query_variant in _static_board_query_variants(request)
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


class AtsDreamJobCompanyCareerSource(DetailEnrichmentScraper):
    def __init__(self, config: AtsCompanySourceConfig) -> None:
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
            for query_variant in _static_board_query_variants(request)
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


def _parse_lever(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    postings = _json_array(body, f"{config.company} Lever response")
    if not postings:
        return _no_results()
    listings = tuple(_lever_listing(posting, config) for posting in postings if isinstance(posting, dict))
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _lever_listing(posting: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
    posting_id = _required_text(posting.get("id"), "id", config)
    title = _required_text(posting.get("text"), "text", config)
    categories = _dict_value(posting.get("categories"))
    all_locations = _text_values(categories.get("allLocations"))
    location_text = _lever_location_text(categories.get("location"), all_locations)
    workplace_type = _text(posting.get("workplaceType")).strip().casefold() or None
    work_formats = _lever_work_formats(workplace_type=workplace_type, location_text=location_text, config=config)
    remote_locations = _lever_remote_locations(
        workplace_type=workplace_type,
        all_locations=all_locations,
        location_text=location_text,
        config=config,
    )
    sections = _lever_sections(posting.get("lists"))
    requirements = _requirements(sections, config.requirements_label_markers)
    description = _lever_description(posting, config)
    country, city = _lever_country_city(location_text, config)
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
        country=country,
        city=city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_lever_posted_at(posting.get("createdAt"), config),
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=_work_format_for_remote_flags(work_formats),
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=_work_format_for_remote_flags(work_formats),
            remote_locations=remote_locations,
        ),
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


def _parse_ashby(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} Ashby response")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError(f"{config.company} Ashby response jobs field is not a JSON array")
    visible_jobs = tuple(job for job in jobs if isinstance(job, dict) and job.get("isListed") is not False)
    if not visible_jobs:
        return _no_results()
    listings = tuple(_ashby_listing(job, config) for job in visible_jobs)
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _ashby_listing(job: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
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
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(config, work_format=work_format, remote_locations=remote_locations),
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


def _parse_workable(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    rows = _workable_table_rows(body)
    if not rows:
        return _no_results()
    listings = tuple(_workable_listing(row, config) for row in rows)
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _workable_listing(row: dict[str, str], config: AtsCompanySourceConfig) -> RawListing:
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


def _parse_greenhouse(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
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


def _greenhouse_listing(job: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
    source_listing_id = str(job.get("id") or "")
    title = _required_text(job.get("title"), "title", config)
    location_text = _nested_text(job, "location", "name")
    content = _text(job.get("content"))
    linkedin_workplace_tags = (
        _linkedin_workplace_tags(content) if config.greenhouse_strip_linkedin_workplace_tags else ()
    )
    visible_content = (
        _remove_linkedin_tags(content) if config.greenhouse_strip_linkedin_workplace_tags else html.unescape(content)
    )
    description = html_to_text(visible_content)
    additional_sections = _html_sections(visible_content)
    requirements = _requirements(additional_sections, config.requirements_label_markers)
    salary_text = _greenhouse_salary_text(additional_sections)
    departments = _names(job.get("departments"))
    offices = _names(job.get("offices"))
    remote_locations = _greenhouse_remote_locations(location_text)
    work_formats = _greenhouse_work_formats(
        location_text,
        office_format_from_non_remote_location=config.greenhouse_office_format_from_non_remote_location,
    )
    country, city = _greenhouse_country_city(
        location_text,
        parse_parts=config.greenhouse_parse_location_parts,
    )
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
    if linkedin_workplace_tags:
        raw["linkedin_workplace_tags"] = linkedin_workplace_tags

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


def _parse_recruitee(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} Recruitee response")
    offers = payload.get("offers")
    if not isinstance(offers, list):
        raise ValueError(f"{config.company} Recruitee response does not contain an offers list")
    if not offers:
        return _no_results()
    listings = tuple(_recruitee_listing(offer, config) for offer in offers if isinstance(offer, dict))
    if not listings:
        raise ValueError(f"{config.company} Recruitee offers list contains no valid offer objects")
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _recruitee_listing(offer: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
    source_listing_id = str(_required_text(str(offer.get("id") or ""), "id", config))
    title = _required_text(offer.get("title"), "title", config)
    url = strip_query(_required_text(offer.get("careers_url"), "careers_url", config))
    description = html_to_text(_text(offer.get("description")))
    requirements = html_to_text(_text(offer.get("requirements")))
    salary = _dict_value(offer.get("salary"))
    salary_min = _int_or_none(salary.get("min"))
    salary_max = _int_or_none(salary.get("max"))
    salary_currency = _text(salary.get("currency")).strip() or None
    locations = _recruitee_locations(offer.get("locations"))
    cities = tuple(city for location in locations for city in (location["city"],) if city)
    remote_locations = tuple(
        country_code
        for location in locations
        for country_code in (location["country_code"],)
        if country_code
    )
    work_formats = _recruitee_work_formats(offer)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": offer.get("id"),
            "guid": _text(offer.get("guid")).strip() or None,
            "slug": _text(offer.get("slug")).strip() or None,
            "category_code": _text(offer.get("category_code")).strip() or None,
            "department": _text(offer.get("department")).strip() or None,
            "education_code": _text(offer.get("education_code")).strip() or None,
            "employment_type_code": _text(offer.get("employment_type_code")).strip() or None,
            "experience_code": _text(offer.get("experience_code")).strip() or None,
            "location": _text(offer.get("location")).strip() or None,
            "country": _text(offer.get("country")).strip() or None,
            "country_code": _text(offer.get("country_code")).strip() or None,
            "city": _text(offer.get("city")).strip() or None,
            "cities": cities,
            "state_name": _text(offer.get("state_name")).strip() or None,
            "remote": _optional_bool(offer.get("remote")),
            "hybrid": _optional_bool(offer.get("hybrid")),
            "on_site": _optional_bool(offer.get("on_site")),
            "locations": locations,
            "salary": {
                "min": salary_min,
                "max": salary_max,
                "currency": salary_currency,
                "period": _text(salary.get("period")).strip() or None,
            },
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    work_format = "remote" if "remote" in work_formats else None
    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=config.source_id,
        company=config.company,
        country=_text(offer.get("country_code")).strip() or None,
        city=", ".join(cities) or _text(offer.get("city")).strip() or None,
        location_text=_text(offer.get("location")).strip() or None,
        salary_text=_salary_text(
            minimum=salary_min,
            maximum=salary_max,
            currency=salary_currency,
            period=_text(salary.get("period")).strip() or None,
        ),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        posted_at=_recruitee_timestamp(_text(offer.get("published_at")).strip()),
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            title,
            _text(offer.get("department")),
            _text(offer.get("location")),
            " ".join(cities),
            " ".join(remote_locations),
            description,
            requirements,
        ),
        raw=raw,
    )


def _recruitee_locations(value: object) -> tuple[dict[str, str | None], ...]:
    if not isinstance(value, list):
        return ()
    locations: list[dict[str, str | None]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        locations.append(
            {
                "id": str(item.get("id")) if item.get("id") is not None else None,
                "name": _text(item.get("name")).strip() or None,
                "state": _text(item.get("state")).strip() or None,
                "country": _text(item.get("country")).strip() or None,
                "country_code": _text(item.get("country_code")).strip() or None,
                "city": _text(item.get("city")).strip() or None,
            }
        )
    return tuple(locations)


def _recruitee_work_formats(offer: dict[str, Any]) -> tuple[str, ...]:
    formats: list[str] = []
    if _optional_bool(offer.get("remote")):
        formats.append("remote")
    if _optional_bool(offer.get("hybrid")):
        formats.append("hybrid")
    if _optional_bool(offer.get("on_site")):
        formats.append("office")
    return tuple(formats)


def _recruitee_timestamp(value: str) -> str | None:
    if not value:
        return None
    if value.endswith(" UTC"):
        return value[:-4].replace(" ", "T") + "Z"
    return value


def _salary_text(
    *,
    minimum: int | None,
    maximum: int | None,
    currency: str | None,
    period: str | None,
) -> str | None:
    if minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None:
        amount = f"{minimum}-{maximum}"
    elif minimum is not None:
        amount = f"from {minimum}"
    else:
        amount = f"up to {maximum}"
    return " ".join(part for part in (currency, amount, period) if part) or None


def _parse_breezy(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    listings = tuple(
        listing
        for match in _BREEZY_CARD_RE.finditer(body)
        for listing in (_breezy_listing(match.group("body"), config),)
        if listing is not None
    )
    if listings:
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)
    if "%LABEL_NO_POSITIONS%" in body or "no open positions" in body.casefold():
        return _no_results()
    raise ValueError(f"{config.company} Breezy page contains no position cards")


def _breezy_listing(card_html: str, config: AtsCompanySourceConfig) -> RawListing | None:
    href_match = _BREEZY_HREF_RE.search(card_html)
    title_match = _BREEZY_TITLE_RE.search(card_html)
    if href_match is None or title_match is None:
        return None
    source_listing_id = href_match.group("id")
    title = html_to_text(title_match.group("title"))
    if not title:
        return None

    location_text = _breezy_optional_text(_BREEZY_LOCATION_RE, card_html)
    employment_type = _breezy_employment_type(_breezy_optional_text(_BREEZY_TYPE_RE, card_html))
    salary_text = _breezy_optional_text(_BREEZY_SALARY_RE, card_html)
    work_format = _breezy_work_format(card_html, location_text)
    raw: dict[str, object] = _source_raw(config)
    raw.update({"id": source_listing_id, "location": location_text, "employment_type": employment_type})
    if salary_text:
        raw["salary"] = salary_text
    if work_format:
        raw["work_format"] = (work_format,)

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=urljoin(config.career_url, href_match.group("href")),
        source=config.source_id,
        company=config.company,
        country=None,
        city=_city_from_simple_location(location_text),
        location_text=location_text,
        salary_text=salary_text,
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
        raw_text=_join_text(title, location_text, employment_type, salary_text, work_format),
        raw=raw,
    )


def _parse_huntflow(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    listings = tuple(_huntflow_listing(match, config) for match in _HUNTFLOW_CARD_RE.finditer(body))
    if listings:
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)
    if "current openings" in body.casefold():
        return _no_results()
    raise ValueError(f"{config.company} Huntflow page contains no vacancy cards")


def _huntflow_listing(match: re.Match[str], config: AtsCompanySourceConfig) -> RawListing:
    title = html_to_text(match.group("title")) or ""
    location_text = html_to_text(match.group("location"))
    work_format = "remote" if location_text and "remote" in location_text.casefold() else None
    remote_locations = _huntflow_remote_locations(location_text)
    raw: dict[str, object] = _source_raw(config)
    raw.update({"slug": match.group("slug"), "location": location_text})
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=match.group("slug"),
        title=title,
        url=urljoin(config.career_url, match.group("href")),
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
        remote_in_country=True if work_format == "remote" and remote_locations else None,
        remote_global=None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, location_text, work_format),
        raw=raw,
    )


def _parse_smartrecruiters(
    body: str,
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    payload = _json_object(body, f"{config.company} SmartRecruiters response")
    raw_jobs = payload.get("content")
    if not isinstance(raw_jobs, list):
        raise ValueError(f"{config.company} SmartRecruiters response content field is malformed")
    if not raw_jobs and payload.get("totalFound") == 0:
        return _no_results()
    listings = tuple(_smartrecruiters_listing(job, config) for job in raw_jobs if isinstance(job, dict))
    if not listings:
        raise ValueError(f"{config.company} SmartRecruiters response contains no valid postings")
    parallel_requests = _smartrecruiters_parallel_requests(
        payload,
        request,
        source_limit=_config_source_limit(config),
        parallel_window=config.smartrecruiters_parallel_pagination_window,
    )
    return SourceSearchParseResult(
        outcome=SourceOutcome.SUCCESS,
        listings=listings,
        next_request=None if parallel_requests else _smartrecruiters_next_request(payload, request),
        parallel_requests=parallel_requests,
    )


def _smartrecruiters_next_request(
    payload: dict[str, Any],
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    total = _int_or_none(payload.get("totalFound"))
    limit = _int_or_none(payload.get("limit"))
    offset = _int_or_none(payload.get("offset")) or _smartrecruiters_request_offset(request.url)
    content = payload.get("content")
    if total is None or limit is None or limit < 1 or not isinstance(content, list) or not content:
        return None
    next_offset = offset + len(content)
    if next_offset >= total:
        return None
    next_url = _smartrecruiters_page_url(request.url, offset=next_offset, limit=limit)
    if next_url == request.url:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=next_url,
    )


def _smartrecruiters_parallel_requests(
    payload: dict[str, Any],
    request: SourceFetchRequest,
    *,
    source_limit: int,
    parallel_window: int,
) -> tuple[SourceFetchRequest, ...]:
    total = _int_or_none(payload.get("totalFound"))
    limit = _int_or_none(payload.get("limit"))
    offset = _int_or_none(payload.get("offset")) or _smartrecruiters_request_offset(request.url)
    content = payload.get("content")
    if (
        parallel_window < 1
        or offset != 0
        or total is None
        or limit is None
        or limit < 1
        or not isinstance(content, list)
        or not content
    ):
        return ()
    page_step = len(content)
    if page_step < 1:
        return ()
    max_records = min(total, source_limit)
    max_records = min(max_records, offset + page_step * (parallel_window + 1))
    return tuple(
        SourceFetchRequest(
            source_id=request.source_id,
            query_variant=request.query_variant,
            url=_smartrecruiters_page_url(request.url, offset=next_offset, limit=limit),
        )
        for next_offset in range(offset + page_step, max_records, page_step)
    )


def _smartrecruiters_request_offset(url: str) -> int:
    query = parse_qs(urlparse(url).query)
    values = query.get("offset", ())
    if not values:
        return 0
    return _int_or_none(values[0]) or 0


def _smartrecruiters_page_url(url: str, *, offset: int, limit: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["offset"] = [str(offset)]
    query["limit"] = [str(limit)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _smartrecruiters_listing(job: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
    source_listing_id = _required_text(job.get("id"), "id", config)
    title = _required_text(job.get("name"), "name", config)
    location = _dict_value(job.get("location"))
    location_text = _text(location.get("fullLocation")).strip() or None
    work_format = _smartrecruiters_work_format(location)
    remote_locations = _smartrecruiters_remote_locations(location, work_format)
    department = _label(job.get("department"))
    function = _label(job.get("function"))
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": source_listing_id,
            "uuid": job.get("uuid"),
            "job_ad_id": job.get("jobAdId"),
            "ref_number": job.get("refNumber"),
            "released_date": job.get("releasedDate"),
            "location": location,
            "department": department,
            "function": function,
            "employment_type": _label(job.get("typeOfEmployment")),
            "experience_level": _label(job.get("experienceLevel")),
            "api_ref": job.get("ref"),
        }
    )
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=_smartrecruiters_posting_url(job, source_listing_id, title),
        source=config.source_id,
        company=_text(_dict_value(job.get("company")).get("name")).strip() or config.company,
        country=_text(location.get("country")).strip() or None,
        city=_text(location.get("city")).strip() or None,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(job.get("releasedDate")).strip() or None,
        remote_in_country=True if work_format == "remote" and remote_locations else None,
        remote_global=False if work_format == "remote" and remote_locations else None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, location_text, department, function, work_format),
        raw=raw,
    )


def _breezy_optional_text(pattern: re.Pattern[str], value: str) -> str | None:
    match = pattern.search(value)
    if match is None:
        return None
    return html_to_text(match.group("value"))


def _breezy_employment_type(value: str | None) -> str | None:
    if value == "%LABEL_POSITION_TYPE_FULL_TIME%":
        return "Full-time"
    return value


def _breezy_work_format(card_html: str, location_text: str | None) -> str | None:
    normalized = " ".join((card_html, location_text or "")).casefold()
    if "remote_hybrid" in normalized or "hybrid" in normalized:
        return "hybrid"
    if "remote" in normalized:
        return "remote"
    return None


def _city_from_simple_location(value: str | None) -> str | None:
    if not value or "remote" in value.casefold():
        return None
    return value.split(",", 1)[0].strip() or None


def _huntflow_remote_locations(location_text: str | None) -> tuple[str, ...]:
    if not location_text:
        return ()
    values = tuple(part.strip() for part in re.split(r"[/,]", location_text) if part.strip())
    return tuple(value for value in values if value.casefold() != "remote")


def _smartrecruiters_work_format(location: dict[str, object]) -> str | None:
    if _optional_bool(location.get("remote")) is True:
        return "remote"
    if _optional_bool(location.get("hybrid")) is True:
        return "hybrid"
    return None


def _smartrecruiters_remote_locations(location: dict[str, object], work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    candidates = (
        _text(location.get("city")).strip(),
        _text(location.get("fullLocation")).strip(),
        _text(location.get("country")).strip(),
    )
    return tuple(dict.fromkeys(value for value in candidates if value and "remote" not in value.casefold()))


def _smartrecruiters_posting_url(job: dict[str, Any], source_listing_id: str, title: str) -> str:
    posting_url = _text(job.get("postingUrl")).strip()
    if posting_url:
        return strip_query(posting_url)
    company = _dict_value(job.get("company"))
    identifier = _text(company.get("identifier")).strip() or "company"
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return f"https://jobs.smartrecruiters.com/{identifier}/{source_listing_id}-{slug}"


def _label(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return _text(value.get("label")).strip() or None


def _parse_workday(
    body: str,
    config: AtsCompanySourceConfig,
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
    parallel_requests = _workday_parallel_requests(
        payload,
        request,
        source_limit=_config_source_limit(config),
    )
    return SourceSearchParseResult(
        outcome=SourceOutcome.SUCCESS,
        listings=listings,
        next_request=None if parallel_requests else _workday_next_request(payload, request),
        parallel_requests=parallel_requests,
    )


def _workday_listing(posting: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
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
    config: AtsCompanySourceConfig,
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


def _parse_personio(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
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


def _personio_listing(attrs: str, item_html: str, config: AtsCompanySourceConfig) -> RawListing | None:
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
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(config, work_format=work_format, remote_locations=remote_locations),
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
    config: AtsCompanySourceConfig,
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


def _parse_bamboohr(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
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


def _bamboohr_listing(item: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
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
    config: AtsCompanySourceConfig,
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


def _teamtailor_listing(item_html: str, config: AtsCompanySourceConfig) -> RawListing | None:
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


def _personio_job_posting(body: str, config: AtsCompanySourceConfig) -> dict[str, Any]:
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


def _parse_join(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    payload = _join_next_data(body, config)
    jobs = _join_jobs(payload, config)
    if not jobs:
        return _no_results()
    listings = tuple(_join_listing(job, config) for job in jobs if isinstance(job, dict))
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _join_listing(item: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
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
    config: AtsCompanySourceConfig,
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


def _join_next_data(body: str, config: AtsCompanySourceConfig) -> dict[str, Any]:
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


def _join_initial_state(payload: dict[str, Any], config: AtsCompanySourceConfig) -> dict[str, Any]:
    props = _dict_value(payload.get("props"))
    page_props = _dict_value(props.get("pageProps"))
    initial_state = page_props.get("initialState")
    if not isinstance(initial_state, dict):
        raise ValueError(f"{config.company} JOIN __NEXT_DATA__ is missing initialState")
    return initial_state


def _join_jobs(payload: dict[str, Any], config: AtsCompanySourceConfig) -> list[Any]:
    jobs = _join_initial_state(payload, config).get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError(f"{config.company} JOIN initialState is missing jobs")
    items = jobs.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{config.company} JOIN jobs.items is not a JSON array")
    return items


def _join_detail_job(payload: dict[str, Any], config: AtsCompanySourceConfig) -> dict[str, Any]:
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


def _parse_jsonld_job_postings(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    postings = tuple(_jsonld_job_postings(body))
    if not postings:
        raise ValueError(f"{config.company} JSON-LD response contains no JobPosting objects")
    listings = tuple(
        listing
        for posting in postings
        for listing in (
            _jsonld_job_posting_listing(
                posting,
                config,
                strong_section_labels=config.strong_section_labels,
            ),
        )
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


def _first_matching_jsonld_job_posting(body: str, listing: RawListing) -> dict[str, Any]:
    postings = _jsonld_job_postings(body)
    if not postings:
        raise ValueError(f"{listing.source} detail response contains no JobPosting objects")
    listing_url = strip_query(listing.url).rstrip("/")
    listing_id = listing.source_listing_id
    for posting in postings:
        posting_url = strip_query(_text(posting.get("url")).strip()).rstrip("/")
        posting_id = _jsonld_identifier(posting)
        if (posting_url and posting_url == listing_url) or (listing_id and posting_id == listing_id):
            return posting
    if len(postings) == 1:
        return postings[0]
    raise ValueError(f"{listing.source} detail response contains no matching JobPosting")


def _jsonld_detail_listing(
    posting: dict[str, Any],
    listing: RawListing,
    config: AtsCompanySourceConfig,
) -> RawListing:
    description_html = _text(posting.get("description")).strip()
    description = html_to_text(description_html)
    if description is None:
        raise ValueError(f"{config.company} detail response is missing JobPosting description")

    sections = _html_sections(description_html, strong_section_labels=config.strong_section_labels)
    locations = _jsonld_locations(posting.get("jobLocation"))
    location_text = _location_text(locations) or listing.location_text
    employment_type = _text(posting.get("employmentType")).strip()
    work_format = _jsonld_work_format(posting)
    if work_format is None and "remote" in employment_type.casefold():
        work_format = "remote"
    remote_locations = _jsonld_remote_locations(posting, work_format)
    remote_global = _is_global_location(location_text) if work_format == "remote" else None
    remote_in_country = True if work_format == "remote" and not remote_global else None
    detail_raw = {
        "date_posted": _text(posting.get("datePosted")).strip() or None,
        "employment_type": employment_type or None,
        "job_location": posting.get("jobLocation"),
        "job_location_type": posting.get("jobLocationType"),
        "applicant_location_requirements": posting.get("applicantLocationRequirements"),
    }
    if remote_locations:
        detail_raw["remote_locations"] = remote_locations

    return replace(
        listing,
        company=_jsonld_hiring_organization_name(posting) or listing.company,
        country=_joined_unique(location.country for location in locations) or listing.country,
        city=_joined_unique(location.city for location in locations) or listing.city,
        location_text=location_text,
        posted_at=_text(posting.get("datePosted")).strip() or listing.posted_at,
        remote_in_country=remote_in_country if remote_in_country is not None else listing.remote_in_country,
        remote_global=remote_global if remote_global is not None else listing.remote_global,
        description=description,
        requirements=_requirements(sections, config.requirements_label_markers),
        additional_sections=sections,
        raw_text=_join_text(listing.raw_text, description),
        raw={**listing.raw, "detail": detail_raw},
    )


def _jsonld_job_posting_listing(
    posting: dict[str, Any],
    config: AtsCompanySourceConfig,
    *,
    strong_section_labels: tuple[str, ...] = (),
) -> RawListing | None:
    title = _text(posting.get("title")).strip() or _text(posting.get("name")).strip()
    url = _text(posting.get("url")).strip()
    source_listing_id = _jsonld_identifier(posting)
    if not title or not url or not source_listing_id:
        return None

    description_html = _text(posting.get("description")).strip()
    description = None if description_html == "~" else html_to_text(description_html)
    sections = (
        _html_sections(description_html, strong_section_labels=strong_section_labels)
        if description_html and description_html != "~"
        else {}
    )
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


def _parse_ycombinator(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
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


def _ycombinator_data_page(body: str, config: AtsCompanySourceConfig) -> dict[str, Any]:
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


def _ycombinator_listing(item: dict[str, Any], config: AtsCompanySourceConfig) -> RawListing:
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


def _parse_comeet(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    company = _json_assignment_object(body, "COMPANY_DATA", config)
    positions = _json_assignment_array(body, "COMPANY_POSITIONS_DATA", config)
    if not positions:
        return _no_results()
    listings = tuple(
        _comeet_listing(position, config, company)
        for position in positions
        if isinstance(position, dict)
    )
    if not listings:
        raise ValueError(f"{config.company} Comeet positions data contains no valid posting objects")
    return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _comeet_listing(
    position: dict[str, Any],
    config: AtsCompanySourceConfig,
    company: dict[str, Any],
) -> RawListing:
    source_listing_id = _required_text(position.get("uid"), "uid", config)
    title = _required_text(position.get("name"), "name", config)
    url = strip_query(_required_text(position.get("url_comeet_hosted_page"), "url_comeet_hosted_page", config))
    location = _dict_value(position.get("location"))
    location_text = _comeet_location_text(location)
    country = _text(location.get("country")).strip() or None
    city = _comeet_city(location.get("city"))
    work_formats = _comeet_work_formats(position, location)
    work_format = _work_format_for_remote_flags(work_formats)
    remote_locations = _comeet_remote_locations(location, work_format)
    sections = _comeet_sections(position.get("custom_fields"))
    description = _comeet_description(sections, config.requirements_label_markers)
    requirements = _requirements(sections, config.requirements_label_markers)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "uid": source_listing_id,
            "department": _text(position.get("department")).strip() or None,
            "employment_type": _text(position.get("employment_type")).strip() or None,
            "experience_level": _text(position.get("experience_level")).strip() or None,
            "workplace_type": _text(position.get("workplace_type")).strip() or None,
            "time_updated": _text(position.get("time_updated")).strip() or None,
            "company_name": _text(position.get("company_name")).strip() or None,
            "comeet_company_uid": _text(company.get("company_uid")).strip() or None,
            "location": {
                "name": _text(location.get("name")).strip() or None,
                "country": country,
                "city": city,
                "state": _text(location.get("state")).strip() or None,
                "timezone": _text(location.get("timezone")).strip() or None,
                "is_remote": _optional_bool(location.get("is_remote")),
            },
            "section_labels": tuple(sections),
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=config.source_id,
        company=_text(position.get("company_name")).strip() or _text(company.get("name")).strip() or config.company,
        country=country,
        city=city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(position.get("time_updated")).strip() or None,
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections=sections,
        skills=(),
        raw_text=_join_text(
            title,
            _text(position.get("department")),
            _text(position.get("employment_type")),
            _text(position.get("experience_level")),
            location_text,
            country,
            city,
            " ".join(work_formats),
            description,
            requirements,
        ),
        raw=raw,
    )


def _comeet_sections(value: object) -> dict[str, str]:
    custom_fields = _dict_value(value)
    details = custom_fields.get("details")
    if not isinstance(details, list):
        return {}
    sections: dict[str, str] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("name")).strip().rstrip(":")
        content = html_to_text(_text(item.get("value")))
        if not label or not content:
            continue
        previous = sections.get(label)
        sections[label] = f"{previous}\n\n{content}" if previous else content
    return sections


def _comeet_description(sections: dict[str, str], requirements_label_markers: tuple[str, ...]) -> str | None:
    for label, content in sections.items():
        if label.casefold() == "description":
            return content
    return "\n\n".join(
        content
        for label, content in sections.items()
        if not any(marker in label.casefold() for marker in requirements_label_markers)
    ) or None


def _comeet_location_text(location: dict[str, object]) -> str | None:
    name = _text(location.get("name")).strip()
    if name:
        return name
    return ", ".join(
        value
        for value in (
            _comeet_city(location.get("city")),
            _text(location.get("state")).strip() or None,
            _text(location.get("country")).strip() or None,
        )
        if value
    ) or None


def _comeet_city(value: object) -> str | None:
    city = _text(value).strip()
    if not city or city.casefold() == "remote":
        return None
    return city


def _comeet_work_formats(
    position: dict[str, Any],
    location: dict[str, object],
) -> tuple[str, ...]:
    work_format = _work_format_from_workplace_type(_text(position.get("workplace_type")).strip())
    if work_format:
        return (work_format,)
    if _optional_bool(location.get("is_remote")) is True:
        return ("remote",)
    return ()


def _comeet_remote_locations(location: dict[str, object], work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    country = _text(location.get("country")).strip()
    return (country,) if country else ()


@dataclass(frozen=True)
class _JobvitePosting:
    source_listing_id: str
    title: str
    relative_url: str
    department: str | None
    location_text: str | None


def _parse_jobvite(
    body: str,
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    parser = _JobviteListParser(config=config)
    parser.feed(body)
    postings = parser.postings()
    if postings:
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_jobvite_listing(posting, config) for posting in postings),
            next_request=_jobvite_next_request(parser.show_more_hrefs(), config, request),
        )
    if "no open positions" in body.casefold() or "no jobs found" in body.casefold():
        return _no_results()
    raise ValueError(f"{config.company} Jobvite response contains no job list rows")


def _jobvite_listing(posting: _JobvitePosting, config: AtsCompanySourceConfig) -> RawListing:
    work_formats = _jobvite_work_formats(posting.location_text)
    work_format = _work_format_for_remote_flags(work_formats)
    remote_locations = _jobvite_remote_locations(posting.location_text, work_format)
    city = _jobvite_city(posting.location_text)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": posting.source_listing_id,
            "department": posting.department,
            "location": posting.location_text,
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting.source_listing_id,
        title=posting.title,
        url=strip_query(urljoin("https://jobs.jobvite.com", posting.relative_url)),
        source=config.source_id,
        company=config.company,
        country=None,
        city=city,
        location_text=posting.location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            posting.title,
            posting.department,
            posting.location_text,
            " ".join(work_formats),
            " ".join(remote_locations),
        ),
        raw=raw,
    )


def _jobvite_work_formats(location_text: str | None) -> tuple[str, ...]:
    if not location_text:
        return ()
    normalized = location_text.casefold()
    if "hybrid" in normalized:
        return ("hybrid",)
    formats: list[str] = []
    if "remote" in normalized:
        formats.append("remote")
    if "on-site" in normalized or "onsite" in normalized:
        formats.append("office")
    return tuple(formats)


def _jobvite_remote_locations(location_text: str | None, work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote" or not location_text:
        return ()
    values = [
        item.strip()
        for item in location_text.split(",")
        if item.strip() and item.strip().casefold() not in {"remote", "hybrid remote"}
    ]
    return tuple(dict.fromkeys(values))


def _jobvite_city(location_text: str | None) -> str | None:
    if not location_text:
        return None
    values = [
        item.strip()
        for item in location_text.split(",")
        if item.strip() and "remote" not in item.casefold()
    ]
    return values[0] if values else None


class _JobviteListParser(HTMLParser):
    def __init__(self, *, config: AtsCompanySourceConfig) -> None:
        super().__init__()
        self._config = config
        self._postings: list[_JobvitePosting] = []
        self._department: str | None = None
        self._collecting_department = False
        self._department_parts: list[str] = []
        self._in_job_table = False
        self._table_depth = 0
        self._in_row = False
        self._cell: Literal["name", "location"] | None = None
        self._row_href: str | None = None
        self._row_title_parts: list[str] = []
        self._row_location_parts: list[str] = []
        self._show_more_hrefs: list[str] = []

    def postings(self) -> tuple[_JobvitePosting, ...]:
        return tuple(self._postings)

    def show_more_hrefs(self) -> tuple[str, ...]:
        return tuple(self._show_more_hrefs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = _attrs(attrs)
        if tag == "a":
            href = attributes.get("href")
            if href and _jobvite_is_show_more_href(href):
                self._show_more_hrefs.append(href)
        if tag == "h3" and _has_class(attributes, "h2"):
            self._collecting_department = True
            self._department_parts = []
            return
        if tag == "table" and _has_class(attributes, "jv-job-list"):
            self._in_job_table = True
            self._table_depth = 1
            return
        if not self._in_job_table:
            return
        if tag == "table":
            self._table_depth += 1
        elif tag == "tr":
            self._start_row()
        elif self._in_row and tag == "td":
            self._start_cell(attributes)
        elif self._in_row and self._cell == "name" and tag == "a":
            self._row_href = attributes.get("href")

    def handle_data(self, data: str) -> None:
        if self._collecting_department:
            self._department_parts.append(data)
        elif self._in_row and self._cell == "name":
            self._row_title_parts.append(data)
        elif self._in_row and self._cell == "location":
            self._row_location_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._collecting_department:
            self._department = _normalize_jobvite_text(" ".join(self._department_parts))
            self._collecting_department = False
        if not self._in_job_table:
            return
        if tag == "td":
            self._cell = None
        elif tag == "tr":
            self._finish_row()
        elif tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_job_table = False

    def _start_row(self) -> None:
        self._in_row = True
        self._cell = None
        self._row_href = None
        self._row_title_parts = []
        self._row_location_parts = []

    def _start_cell(self, attributes: dict[str, str]) -> None:
        if _has_class(attributes, "jv-job-list-name"):
            self._cell = "name"
        elif _has_class(attributes, "jv-job-list-location"):
            self._cell = "location"

    def _finish_row(self) -> None:
        title = _normalize_jobvite_text(" ".join(self._row_title_parts))
        location_text = _normalize_jobvite_text(" ".join(self._row_location_parts))
        if self._row_href and title:
            self._postings.append(
                _JobvitePosting(
                    source_listing_id=_jobvite_id(self._row_href, self._config),
                    title=title,
                    relative_url=self._row_href,
                    department=self._department,
                    location_text=location_text,
                )
            )
        self._in_row = False
        self._cell = None


def _jobvite_id(relative_url: str, config: AtsCompanySourceConfig) -> str:
    parsed = urlparse(relative_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= _JOBVITE_JOB_PATH_PARTS_MIN and parts[-2] == "job":
        return parts[-1]
    raise ValueError(f"{config.company} Jobvite posting URL does not contain a job id")


def _jobvite_is_show_more_href(href: str) -> bool:
    parsed = urlparse(html.unescape(href))
    if not parsed.path.endswith("/search"):
        return False
    params = parse_qs(parsed.query)
    return bool(params.get("c") and params.get("p"))


def _jobvite_next_request(
    hrefs: tuple[str, ...],
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    pending_urls = _jobvite_pending_show_more_urls(request)
    discovered_urls = tuple(urljoin(config.board_url, html.unescape(href)) for href in hrefs)
    candidate_urls = tuple(
        url
        for url in dict.fromkeys((*pending_urls, *discovered_urls))
        if url != request.url
    )
    if not candidate_urls:
        return None
    next_url, *remaining_urls = candidate_urls
    headers = dict(request.headers)
    if remaining_urls:
        headers[_JOBVITE_PENDING_SHOW_MORE_HEADER] = json.dumps(remaining_urls, separators=(",", ":"))
    else:
        headers.pop(_JOBVITE_PENDING_SHOW_MORE_HEADER, None)
    return SourceFetchRequest(
        source_id=config.source_id,
        query_variant=request.query_variant,
        url=next_url,
        headers=headers,
    )


def _jobvite_pending_show_more_urls(request: SourceFetchRequest) -> tuple[str, ...]:
    raw_value = request.headers.get(_JOBVITE_PENDING_SHOW_MORE_HEADER)
    if not raw_value:
        return ()
    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, str) and value)


def _normalize_jobvite_text(value: str) -> str | None:
    text = _normalize_space(value).replace(" ,", ",")
    return text or None


def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key: value or "" for key, value in attrs}


def _has_class(attributes: dict[str, str], class_name: str) -> bool:
    return class_name in attributes.get("class", "").split()


def _has_rel(attributes: dict[str, str], rel_name: str) -> bool:
    return rel_name in attributes.get("rel", "").split()


@dataclass(frozen=True)
class _JazzHrPosting:
    source_listing_id: str
    title: str
    relative_url: str
    department: str | None
    location_text: str | None


def _parse_jazzhr(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    parser = _JazzHrListParser(config=config)
    parser.feed(body)
    postings = parser.postings()
    if postings:
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_jazzhr_listing(posting, config) for posting in postings),
        )
    if "no jobs found" in body.casefold() or "no positions" in body.casefold():
        return _no_results()
    raise ValueError(f"{config.company} JazzHR response contains no job rows")


def _jazzhr_listing(posting: _JazzHrPosting, config: AtsCompanySourceConfig) -> RawListing:
    work_formats = _jazzhr_work_formats(posting.location_text)
    work_format = _work_format_for_remote_flags(work_formats)
    remote_locations = _jazzhr_remote_locations(posting.location_text, work_format)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": posting.source_listing_id,
            "department": posting.department,
            "location": posting.location_text,
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting.source_listing_id,
        title=posting.title,
        url=strip_query(urljoin(config.board_url, posting.relative_url)),
        source=config.source_id,
        company=config.company,
        country=None,
        city=_jazzhr_city(posting.location_text),
        location_text=posting.location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            posting.title,
            posting.department,
            posting.location_text,
            " ".join(work_formats),
            " ".join(remote_locations),
        ),
        raw=raw,
    )


def _jazzhr_work_formats(location_text: str | None) -> tuple[str, ...]:
    if not location_text:
        return ()
    normalized = location_text.casefold()
    if "hybrid" in normalized:
        return ("hybrid",)
    if "remote" in normalized:
        return ("remote",)
    if "on-site" in normalized or "onsite" in normalized:
        return ("office",)
    return ()


def _jazzhr_remote_locations(location_text: str | None, work_format: str | None) -> tuple[str, ...]:
    if work_format != "remote" or not location_text:
        return ()
    values = [
        item.strip()
        for item in location_text.split(",")
        if item.strip() and item.strip().casefold() != "remote"
    ]
    return tuple(dict.fromkeys(values))


def _jazzhr_city(location_text: str | None) -> str | None:
    if not location_text or "remote" in location_text.casefold() or "," not in location_text:
        return None
    city = location_text.split(",", 1)[0].strip()
    return city or None


class _JazzHrListParser(HTMLParser):
    def __init__(self, *, config: AtsCompanySourceConfig) -> None:
        super().__init__()
        self._config = config
        self._postings: list[_JazzHrPosting] = []
        self._in_jobs_column = False
        self._jobs_column_depth = 0
        self._department: str | None = None
        self._collecting_department = False
        self._department_parts: list[str] = []
        self._in_row = False
        self._row_depth = 0
        self._row_href: str | None = None
        self._row_title_parts: list[str] = []
        self._row_metadata: dict[str, str] = {}
        self._collecting_title = False
        self._collecting_metadata = False
        self._metadata_parts: list[str] = []

    def postings(self) -> tuple[_JazzHrPosting, ...]:
        return tuple(self._postings)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = _attrs(attrs)
        if tag == "div" and attributes.get("id") == "jobs_column" and not self._in_jobs_column:
            self._in_jobs_column = True
            self._jobs_column_depth = 1
            return
        if not self._in_jobs_column:
            return
        if tag == "div":
            self._jobs_column_depth += 1
            if _has_class(attributes, "jobs_row"):
                self._start_row()
            elif self._in_row:
                self._row_depth += 1
            return
        if tag == "h3" and not self._in_row:
            self._collecting_department = True
            self._department_parts = []
            return
        if not self._in_row:
            return
        if tag == "a" and _has_class(attributes, "job_title_link"):
            self._collecting_title = True
            self._row_href = attributes.get("href")
            self._row_title_parts = []
        elif tag == "span" and _has_class(attributes, "resumator_description"):
            self._collecting_metadata = True
            self._metadata_parts = []

    def handle_data(self, data: str) -> None:
        if self._collecting_department:
            self._department_parts.append(data)
        elif self._collecting_title:
            self._row_title_parts.append(data)
        elif self._collecting_metadata:
            self._metadata_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._collecting_department:
            self._department = _normalize_space(" ".join(self._department_parts)) or None
            self._collecting_department = False
            return
        if tag == "a" and self._collecting_title:
            self._collecting_title = False
            return
        if tag == "span" and self._collecting_metadata:
            self._finish_metadata()
            return
        if tag != "div" or not self._in_jobs_column:
            return
        if self._in_row:
            self._row_depth -= 1
            if self._row_depth == 0:
                self._finish_row()
        self._jobs_column_depth -= 1
        if self._jobs_column_depth == 0:
            self._in_jobs_column = False

    def _start_row(self) -> None:
        self._in_row = True
        self._row_depth = 1
        self._row_href = None
        self._row_title_parts = []
        self._row_metadata = {}
        self._collecting_title = False
        self._collecting_metadata = False
        self._metadata_parts = []

    def _finish_metadata(self) -> None:
        self._collecting_metadata = False
        text = _normalize_space(" ".join(self._metadata_parts))
        if ":" not in text:
            return
        label, value = (part.strip() for part in text.split(":", 1))
        if label and value:
            self._row_metadata[label.casefold()] = value

    def _finish_row(self) -> None:
        title = _normalize_space(" ".join(self._row_title_parts))
        if self._row_href and title:
            self._postings.append(
                _JazzHrPosting(
                    source_listing_id=_jazzhr_id(self._row_href, self._config),
                    title=title,
                    relative_url=self._row_href,
                    department=self._row_metadata.get("department") or self._department,
                    location_text=self._row_metadata.get("location"),
                )
            )
        self._in_row = False
        self._collecting_title = False
        self._collecting_metadata = False


def _jazzhr_id(relative_url: str, config: AtsCompanySourceConfig) -> str:
    parsed = urlparse(relative_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= _JAZZHR_DETAIL_PATH_PARTS_MIN and parts[-2] == "details":
        return parts[-1]
    raise ValueError(f"{config.company} JazzHR posting URL does not contain a details id")


@dataclass(frozen=True)
class _IcimsPosting:
    source_listing_id: str
    title: str
    url: str
    metadata: dict[str, str]


def _parse_icims(
    body: str,
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    parser = _IcimsListParser(config=config)
    parser.feed(body)
    postings = parser.postings()
    if postings:
        next_url = parser.next_url()
        pagination_urls = ((next_url,) if next_url is not None else ()) + parser.page_urls()
        parallel_requests = _icims_parallel_requests(pagination_urls, request)
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_icims_listing(posting, config) for posting in postings),
            next_request=None if parallel_requests else _icims_next_request(parser.next_url(), request),
            parallel_requests=parallel_requests,
        )
    if "no jobs found" in body.casefold() or "no matching jobs" in body.casefold():
        return _no_results()
    raise ValueError(f"{config.company} iCIMS response contains no job cards")


def _icims_listing(posting: _IcimsPosting, config: AtsCompanySourceConfig) -> RawListing:
    location_text = _icims_location_text(posting.metadata)
    country, city = _icims_country_city(location_text)
    workplace = _icims_metadata_value(posting.metadata, "workplace")
    work_formats = _icims_work_formats(workplace=workplace, title=posting.title, location_text=location_text)
    work_format = _work_format_for_remote_flags(work_formats)
    remote_locations = _icims_remote_locations(country=country, location_text=location_text, work_format=work_format)
    department = _icims_department(posting.metadata)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": posting.source_listing_id,
            "department": department,
            "location": location_text,
            "metadata": posting.metadata,
        }
    )
    if workplace:
        raw["workplace"] = workplace
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting.source_listing_id,
        title=posting.title,
        url=strip_query(posting.url),
        source=config.source_id,
        company=config.company,
        country=country,
        city=city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            posting.title,
            department,
            location_text,
            workplace,
            _icims_metadata_value(posting.metadata, "employment type"),
            _icims_metadata_value(posting.metadata, "position type"),
        ),
        raw=raw,
    )


def _icims_location_text(metadata: dict[str, str]) -> str | None:
    return (
        _icims_metadata_value(metadata, "job locations")
        or _icims_metadata_value(metadata, "location")
        or None
    )


def _icims_department(metadata: dict[str, str]) -> str | None:
    return (
        _icims_metadata_value(metadata, "job area")
        or _icims_metadata_value(metadata, "category")
        or _icims_metadata_value(metadata, "function")
        or None
    )


def _icims_metadata_value(metadata: dict[str, str], key: str) -> str | None:
    value = metadata.get(key)
    return value or None


def _icims_country_city(location_text: str | None) -> tuple[str | None, str | None]:
    if not location_text:
        return None, None
    text = location_text.strip()
    if "-" in text:
        parts = [part.strip() for part in text.split("-") if part.strip()]
        if parts and len(parts[0]) == _US_STATE_CODE_LENGTH and parts[0].isalpha():
            country = parts[0].upper()
            city = parts[-1].replace("_", " ").title() if len(parts) > 1 else None
            return country, city
    if "," in text and "remote" not in text.casefold():
        city = text.split(",", 1)[0].strip()
        return None, city or None
    return None, None


def _icims_work_formats(
    *,
    workplace: str | None,
    title: str,
    location_text: str | None,
) -> tuple[str, ...]:
    evidence = " ".join(part for part in (workplace, title, location_text) if part).casefold()
    if "hybrid" in evidence:
        return ("hybrid",)
    if "remote" in evidence or "work from home" in evidence:
        return ("remote",)
    if "on-site" in evidence or "onsite" in evidence:
        return ("office",)
    return ()


def _icims_remote_locations(
    *,
    country: str | None,
    location_text: str | None,
    work_format: str | None,
) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    if country:
        return (country,)
    if not location_text:
        return ()
    values = [
        item.strip()
        for item in location_text.split(",")
        if item.strip() and item.strip().casefold() != "remote"
    ]
    return tuple(dict.fromkeys(values))


class _IcimsListParser(HTMLParser):
    def __init__(self, *, config: AtsCompanySourceConfig) -> None:
        super().__init__()
        self._config = config
        self._postings: list[_IcimsPosting] = []
        self._in_table = False
        self._table_depth = 0
        self._in_row = False
        self._row_depth = 0
        self._row_href: str | None = None
        self._title_parts: list[str] = []
        self._metadata: dict[str, str] = {}
        self._collecting_title = False
        self._collecting_header_label = False
        self._collecting_header_value = False
        self._header_label_parts: list[str] = []
        self._header_value_parts: list[str] = []
        self._pending_header_label: str | None = None
        self._collecting_field_label = False
        self._collecting_field_value = False
        self._field_label_parts: list[str] = []
        self._field_value_parts: list[str] = []
        self._pending_field_label: str | None = None
        self._next_url: str | None = None
        self._page_urls: list[str] = []

    def postings(self) -> tuple[_IcimsPosting, ...]:
        return tuple(self._postings)

    def next_url(self) -> str | None:
        return self._next_url

    def page_urls(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._page_urls))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = _attrs(attrs)
        if tag == "link" and _has_rel(attributes, "next"):
            href = attributes.get("href")
            if href:
                self._next_url = href
            return
        if tag == "a":
            href = attributes.get("href")
            if href and _icims_page_number(href) is not None:
                self._page_urls.append(href)
        if tag == "ul" and _has_class(attributes, "iCIMS_JobsTable"):
            self._in_table = True
            self._table_depth = 1
            return
        if not self._in_table:
            return
        if tag == "ul":
            self._table_depth += 1
            return
        if tag == "li" and _has_class(attributes, "iCIMS_JobCardItem"):
            self._start_row()
            return
        if not self._in_row:
            return
        self._handle_row_starttag(tag, attributes)

    def _handle_row_starttag(self, tag: str, attributes: dict[str, str]) -> None:
        if tag == "li":
            self._row_depth += 1
        elif tag == "a" and _has_class(attributes, "iCIMS_Anchor"):
            href = attributes.get("href")
            if href and "/jobs/" in href:
                self._row_href = href
        elif tag == "h3" and self._row_href:
            self._collecting_title = True
            self._title_parts = []
        elif tag == "span" and _has_class(attributes, "field-label"):
            self._collecting_header_label = True
            self._header_label_parts = []
        elif tag == "span" and self._pending_header_label and not self._collecting_field_value:
            self._collecting_header_value = True
            self._header_value_parts = []
        elif tag == "dt" and _has_class(attributes, "iCIMS_JobHeaderField"):
            self._collecting_field_label = True
            self._field_label_parts = []
        elif tag == "dd" and _has_class(attributes, "iCIMS_JobHeaderData"):
            self._collecting_field_value = True
            self._field_value_parts = []

    def handle_data(self, data: str) -> None:
        if self._collecting_title:
            self._title_parts.append(data)
        elif self._collecting_field_label:
            self._field_label_parts.append(data)
        elif self._collecting_field_value:
            self._field_value_parts.append(data)
        elif self._collecting_header_label:
            self._header_label_parts.append(data)
        elif self._collecting_header_value:
            self._header_value_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._collecting_title:
            self._collecting_title = False
            return
        if tag == "dt" and self._collecting_field_label:
            self._pending_field_label = _icims_label(" ".join(self._field_label_parts))
            self._collecting_field_label = False
            return
        if tag == "dd" and self._collecting_field_value:
            self._store_metadata(self._pending_field_label, " ".join(self._field_value_parts))
            self._pending_field_label = None
            self._collecting_field_value = False
            return
        if tag == "span" and self._collecting_header_label:
            self._pending_header_label = _icims_label(" ".join(self._header_label_parts))
            self._collecting_header_label = False
            return
        if tag == "span" and self._collecting_header_value:
            self._store_metadata(self._pending_header_label, " ".join(self._header_value_parts))
            self._pending_header_label = None
            self._collecting_header_value = False
            return
        if tag == "li" and self._in_row:
            self._row_depth -= 1
            if self._row_depth == 0:
                self._finish_row()
            return
        if tag == "ul" and self._in_table:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_table = False

    def _start_row(self) -> None:
        self._in_row = True
        self._row_depth = 1
        self._row_href = None
        self._title_parts = []
        self._metadata = {}
        self._pending_header_label = None
        self._pending_field_label = None

    def _store_metadata(self, label: str | None, raw_value: str) -> None:
        value = _normalize_space(raw_value)
        if label and value and label != "title":
            self._metadata[label] = value

    def _finish_row(self) -> None:
        title = _normalize_space(" ".join(self._title_parts))
        if self._row_href and title:
            self._postings.append(
                _IcimsPosting(
                    source_listing_id=_icims_id(self._row_href, self._config),
                    title=title,
                    url=self._row_href,
                    metadata=dict(self._metadata),
                )
            )
        self._in_row = False


def _icims_label(raw_label: str) -> str | None:
    label = _normalize_space(raw_label).rstrip(":").casefold()
    if not label or label == "job post information* : posted date":
        return None
    return label


def _icims_id(url: str, config: AtsCompanySourceConfig) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if "jobs" in parts:
        index = parts.index("jobs")
        if len(parts) > index + 1:
            return parts[index + 1]
    raise ValueError(f"{config.company} iCIMS posting URL does not contain a job id")


def _icims_next_request(next_url: str | None, request: SourceFetchRequest) -> SourceFetchRequest | None:
    if not next_url:
        return None
    url = urljoin(request.url, html.unescape(next_url))
    if url == request.url:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=url,
    )


def _icims_parallel_requests(
    page_urls: tuple[str, ...],
    request: SourceFetchRequest,
) -> tuple[SourceFetchRequest, ...]:
    current_page = _icims_page_number(request.url) or 0
    seen_pages = {current_page}
    requests: list[SourceFetchRequest] = []
    for raw_url in page_urls:
        page_number = _icims_page_number(raw_url)
        if page_number is None or page_number <= current_page or page_number in seen_pages:
            continue
        seen_pages.add(page_number)
        url = _icims_page_url(raw_url, request)
        if url == request.url:
            continue
        requests.append(
            SourceFetchRequest(
                source_id=request.source_id,
                query_variant=request.query_variant,
                url=url,
            )
        )
    return tuple(requests)


def _icims_page_url(raw_url: str, request: SourceFetchRequest) -> str:
    url = urljoin(request.url, html.unescape(raw_url))
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    page_number = _query_int((query.get("pr") or [""])[0])
    if page_number is None:
        return url
    normalized: dict[str, list[str]] = {"pr": [str(page_number)]}
    for key in ("in_iframe", "searchRelation"):
        values = query.get(key)
        if values and values[0].strip():
            normalized[key] = [values[0]]
    return urlunparse(parsed._replace(query=urlencode(normalized, doseq=True)))


def _icims_page_number(url: str) -> int | None:
    values = parse_qs(urlparse(html.unescape(url)).query).get("pr")
    if not values or not values[0].strip():
        return None
    return _query_int(values[0])


@dataclass(frozen=True)
class _TaleoPosting:
    source_listing_id: str
    title: str
    url: str
    fields: tuple[str, ...]


def _parse_taleo(
    body: str,
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceSearchParseResult:
    parser = _TaleoListParser(config=config)
    parser.feed(body)
    postings = parser.postings()
    next_href = parser.next_href()
    if postings:
        parallel_requests = _taleo_parallel_requests(
            next_href,
            config,
            request,
            source_limit=_config_source_limit(config),
            parallel_window=config.taleo_parallel_pagination_window,
        )
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_taleo_listing(posting, config) for posting in postings),
            next_request=None if parallel_requests else _taleo_next_request(next_href, config, request),
            parallel_requests=parallel_requests,
        )
    if "no jobs found" in body.casefold() or "no results found" in body.casefold():
        if _taleo_request_row_from(request.url) > 0:
            return SourceSearchParseResult(
                outcome=SourceOutcome.SUCCESS,
                listings=(),
                evidence=AttemptEvidence(multi_step_terminal=True),
            )
        return _no_results()
    if _taleo_terminal_page_without_rows(body, request, next_href=next_href):
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=(),
            evidence=AttemptEvidence(multi_step_terminal=True),
        )
    raise ValueError(f"{config.company} Taleo response contains no job rows")


def _taleo_listing(posting: _TaleoPosting, config: AtsCompanySourceConfig) -> RawListing:
    location_values = _taleo_location_values(posting)
    location_text = _taleo_location_text(location_values)
    department = _taleo_department(posting, location_values)
    employment_type = _taleo_employment_type(posting)
    work_formats = _taleo_work_formats(location_values)
    work_format = _work_format_for_remote_flags(work_formats)
    remote_locations = _taleo_remote_locations(location_values=location_values, work_format=work_format)
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": posting.source_listing_id,
            "department": department,
            "employment_type": employment_type,
            "fields": posting.fields,
            "location": location_text,
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting.source_listing_id,
        title=posting.title,
        url=urljoin(config.board_url, posting.url),
        source=config.source_id,
        company=config.company,
        country=None,
        city=_taleo_city(location_values),
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            posting.title,
            department,
            location_text,
            employment_type,
            " ".join(work_formats),
            " ".join(remote_locations),
        ),
        raw=raw,
    )


def _taleo_location_values(posting: _TaleoPosting) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            field
            for field in posting.fields
            if field != posting.source_listing_id and _taleo_is_location_field(field)
        )
    )


def _taleo_location_text(location_values: tuple[str, ...]) -> str | None:
    return ", ".join(location_values) or None


def _taleo_department(posting: _TaleoPosting, location_values: tuple[str, ...]) -> str | None:
    ignored_values = set(location_values)
    for field in posting.fields:
        if (
            field == posting.source_listing_id
            or field in ignored_values
            or _taleo_is_employment_type(field)
            or _taleo_is_clearance_field(field)
        ):
            continue
        return field
    return None


def _taleo_employment_type(posting: _TaleoPosting) -> str | None:
    for field in posting.fields:
        if _taleo_is_employment_type(field):
            return field
    return None


def _taleo_work_formats(location_values: tuple[str, ...]) -> tuple[str, ...]:
    evidence = " ".join(location_values).casefold()
    if "hybrid" in evidence:
        return ("hybrid",)
    if "remote" in evidence or "work from home" in evidence:
        return ("remote",)
    if "on-site" in evidence or "onsite" in evidence:
        return ("office",)
    return ()


def _taleo_remote_locations(
    *,
    location_values: tuple[str, ...],
    work_format: str | None,
) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    remote_markers = {"remote", "hybrid", "hybrid remote", "work from home"}
    values: list[str] = []
    for location in location_values:
        for part in location.split(","):
            value = part.strip()
            if value and value.casefold() not in remote_markers:
                values.append(value)
    return tuple(dict.fromkeys(values))


def _taleo_city(location_values: tuple[str, ...]) -> str | None:
    for location in location_values:
        if "remote" in location.casefold() or "," not in location:
            continue
        return location.split(",", 1)[0].strip() or None
    return None


def _taleo_is_location_field(value: str) -> bool:
    normalized = value.casefold()
    if "remote" in normalized or "hybrid" in normalized or "work from home" in normalized:
        return True
    if "," not in value:
        return False
    last_part = value.rsplit(",", 1)[1].strip()
    return len(last_part) == _US_STATE_CODE_LENGTH and last_part.isalpha()


def _taleo_is_employment_type(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in (
            "full time",
            "part time",
            "contract",
            "temporary",
            "intern",
            "regular",
            "seasonal",
        )
    )


def _taleo_is_clearance_field(value: str) -> bool:
    normalized = value.casefold()
    return "clearance" in normalized or "polygraph" in normalized or "ts/sci" in normalized


class _TaleoListParser(HTMLParser):
    def __init__(self, *, config: AtsCompanySourceConfig) -> None:
        super().__init__()
        self._config = config
        self._postings: list[_TaleoPosting] = []
        self._in_head_info = False
        self._head_info_depth = 0
        self._row_href: str | None = None
        self._title_parts: list[str] = []
        self._fields: list[str] = []
        self._collecting_title = False
        self._collecting_field = False
        self._field_depth = 0
        self._field_parts: list[str] = []
        self._next_href: str | None = None

    def postings(self) -> tuple[_TaleoPosting, ...]:
        return tuple(self._postings)

    def next_href(self) -> str | None:
        return self._next_href

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = _attrs(attrs)
        if tag == "a" and _has_class(attributes, "jscroll-next"):
            href = attributes.get("href")
            if href:
                self._next_href = href
            return
        if tag == "div" and _has_class(attributes, "oracletaleocwsv2-accordion-head-info"):
            self._start_head_info()
            return
        if not self._in_head_info:
            return
        if tag == "div":
            self._head_info_depth += 1
            if self._head_info_depth == _TALEO_DIRECT_FIELD_DEPTH:
                self._collecting_field = True
                self._field_depth = 1
                self._field_parts = []
            elif self._collecting_field:
                self._field_depth += 1
        elif tag == "a" and _has_class(attributes, "viewJobLink"):
            self._row_href = attributes.get("href")
            self._collecting_title = True
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._collecting_title:
            self._title_parts.append(data)
        elif self._collecting_field:
            self._field_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._collecting_title:
            self._collecting_title = False
            return
        if tag != "div" or not self._in_head_info:
            return
        if self._collecting_field:
            self._field_depth -= 1
            if self._field_depth == 0:
                self._finish_field()
        self._head_info_depth -= 1
        if self._head_info_depth == 0:
            self._finish_head_info()

    def _start_head_info(self) -> None:
        self._in_head_info = True
        self._head_info_depth = 1
        self._row_href = None
        self._title_parts = []
        self._fields = []
        self._collecting_title = False
        self._collecting_field = False
        self._field_parts = []

    def _finish_field(self) -> None:
        field = _normalize_space(" ".join(self._field_parts))
        if field:
            self._fields.append(field)
        self._collecting_field = False
        self._field_parts = []

    def _finish_head_info(self) -> None:
        title = _normalize_space(" ".join(self._title_parts))
        if self._row_href and title:
            self._postings.append(
                _TaleoPosting(
                    source_listing_id=_taleo_id(self._row_href, self._config),
                    title=title,
                    url=self._row_href,
                    fields=tuple(self._fields),
                )
            )
        self._in_head_info = False


def _taleo_id(url: str, config: AtsCompanySourceConfig) -> str:
    rid_values = parse_qs(urlparse(url).query).get("rid")
    if rid_values and rid_values[0].strip():
        return rid_values[0].strip()
    raise ValueError(f"{config.company} Taleo posting URL does not contain a rid")


def _taleo_next_request(
    next_href: str | None,
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    row_from = _taleo_next_row_from(next_href)
    if row_from is None:
        return None
    next_url = _taleo_row_from_url(config.board_url, str(row_from))
    if next_url == request.url:
        return None
    return SourceFetchRequest(
        source_id=config.source_id,
        query_variant=request.query_variant,
        url=next_url,
    )


def _taleo_terminal_page_without_rows(
    body: str,
    request: SourceFetchRequest,
    *,
    next_href: str | None,
) -> bool:
    if _taleo_request_row_from(request.url) <= 0 or next_href is not None:
        return False
    normalized = body.casefold()
    return "oracletaleocwsv2-wrapper" in normalized and "job search results" in normalized


def _taleo_parallel_requests(
    next_href: str | None,
    config: AtsCompanySourceConfig,
    request: SourceFetchRequest,
    *,
    source_limit: int,
    parallel_window: int,
) -> tuple[SourceFetchRequest, ...]:
    next_row_from = _taleo_next_row_from(next_href)
    current_row_from = _taleo_request_row_from(request.url)
    if (
        parallel_window < 1
        or current_row_from != 0
        or next_row_from is None
        or next_row_from <= current_row_from
    ):
        return ()
    page_step = next_row_from - current_row_from
    max_exclusive = min(
        source_limit,
        current_row_from + page_step * (parallel_window + 1),
    )
    return tuple(
        SourceFetchRequest(
            source_id=config.source_id,
            query_variant=request.query_variant,
            url=_taleo_row_from_url(config.board_url, str(row_from)),
        )
        for row_from in range(next_row_from, max_exclusive, page_step)
    )


def _taleo_next_row_from(next_href: str | None) -> int | None:
    if not next_href:
        return None
    row_from_values = parse_qs(urlparse(html.unescape(next_href)).query).get("rowFrom")
    if not row_from_values or not row_from_values[0].strip():
        return None
    return _query_int(row_from_values[0])


def _taleo_request_row_from(url: str) -> int:
    row_from_values = parse_qs(urlparse(html.unescape(url)).query).get("rowFrom")
    if not row_from_values or not row_from_values[0].strip():
        return 0
    return _query_int(row_from_values[0]) or 0


def _query_int(value: str) -> int | None:
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _taleo_row_from_url(board_url: str, row_from: str) -> str:
    parsed = urlparse(board_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["rowFrom"] = [row_from]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


@dataclass(frozen=True)
class _SuccessFactorsPosting:
    source_listing_id: str
    title: str
    url: str
    posted_at: str | None
    description: str | None
    metadata: dict[str, str]


def _parse_successfactors(body: str, config: AtsCompanySourceConfig) -> SourceSearchParseResult:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"{config.company} SuccessFactors response is not valid XML") from exc
    postings = tuple(_successfactors_postings(root, config))
    if postings:
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_successfactors_listing(posting, config) for posting in postings),
        )
    if root.tag == "Job-Listing":
        return _no_results()
    raise ValueError(f"{config.company} SuccessFactors response contains no Job-Listing root")


def _successfactors_postings(
    root: ET.Element,
    config: AtsCompanySourceConfig,
) -> tuple[_SuccessFactorsPosting, ...]:
    postings: list[_SuccessFactorsPosting] = []
    for element in root.findall("Job"):
        title = _normalize_space(element.findtext("JobTitle") or "")
        req_id = _normalize_space(element.findtext("ReqId") or "")
        if not title or not req_id:
            continue
        description_html = element.findtext("Job-Description") or ""
        postings.append(
            _SuccessFactorsPosting(
                source_listing_id=req_id,
                title=title,
                url=_successfactors_detail_url(config, req_id),
                posted_at=_successfactors_posted_at(element.findtext("Posted-Date")),
                description=html_to_text(description_html) or None,
                metadata=_successfactors_metadata(element),
            )
        )
    return tuple(postings)


def _successfactors_listing(posting: _SuccessFactorsPosting, config: AtsCompanySourceConfig) -> RawListing:
    country, city, location_text = _successfactors_location(posting)
    department = _successfactors_department(posting.metadata)
    work_formats = _successfactors_work_formats(
        title=posting.title,
        location_text=location_text,
        metadata=posting.metadata,
    )
    work_format = _work_format_for_remote_flags(work_formats)
    remote_locations = _successfactors_remote_locations(
        country=country,
        location_text=location_text,
        work_format=work_format,
    )
    raw: dict[str, object] = _source_raw(config)
    raw.update(
        {
            "id": posting.source_listing_id,
            "department": department,
            "location": location_text,
            "metadata": posting.metadata,
        }
    )
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting.source_listing_id,
        title=posting.title,
        url=posting.url,
        source=config.source_id,
        company=config.company,
        country=country,
        city=city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=posting.posted_at,
        remote_in_country=_remote_in_country_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        remote_global=_remote_global_for_config(
            config,
            work_format=work_format,
            remote_locations=remote_locations,
        ),
        relocation=None,
        native_grade=None,
        description=posting.description,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            posting.title,
            country,
            city,
            department,
            posting.posted_at,
            " ".join(work_formats),
            " ".join(remote_locations),
            posting.description,
        ),
        raw=raw,
    )


def _successfactors_metadata(element: ET.Element) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for child in element:
        label = _normalize_space(child.findtext("label") or "")
        value = _normalize_space(child.findtext("value") or "")
        if not label or not value:
            continue
        key = label.rstrip(":").casefold()
        previous = metadata.get(key)
        metadata[key] = f"{previous}, {value}" if previous and value not in previous else value
    return metadata


def _successfactors_detail_url(config: AtsCompanySourceConfig, req_id: str) -> str:
    parsed = urlparse(config.board_url)
    company_values = parse_qs(parsed.query).get("company")
    if not company_values or not company_values[0].strip():
        raise ValueError(f"{config.company} SuccessFactors board URL does not contain company")
    query = urlencode(
        {
            "career_ns": "job_listing",
            "company": company_values[0],
            "career_job_req_id": req_id,
        }
    )
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def _successfactors_location(posting: _SuccessFactorsPosting) -> tuple[str | None, str | None, str | None]:
    country = _successfactors_metadata_value(posting.metadata, "country", "country/region")
    city = _successfactors_metadata_value(posting.metadata, "city")
    description_location = _successfactors_description_location(posting.description)
    if description_location:
        inferred_city, inferred_country = _successfactors_city_country_from_location(description_location)
        return country or inferred_country, city or inferred_city, description_location
    location_text = _successfactors_location_text(city=city, country=country)
    return country, city, location_text


def _successfactors_description_location(description: str | None) -> str | None:
    if not description:
        return None
    for line in description.splitlines():
        text = _normalize_space(line)
        if not text or not text.casefold().startswith("location:"):
            continue
        return text.split(":", 1)[1].strip() or None
    return None


def _successfactors_city_country_from_location(location_text: str) -> tuple[str | None, str | None]:
    if "," not in location_text:
        return None, None
    city, country = (part.strip() for part in location_text.rsplit(",", 1))
    return city or None, country or None


def _successfactors_location_text(*, city: str | None, country: str | None) -> str | None:
    if city and country:
        return f"{city}, {country}"
    return city or country


def _successfactors_department(metadata: dict[str, str]) -> str | None:
    return _successfactors_metadata_value(
        metadata,
        "activity area",
        "job function",
        "department",
        "category",
        "organizational unit",
    )


def _successfactors_metadata_value(metadata: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value:
            return value
    return None


def _successfactors_work_formats(
    *,
    title: str,
    location_text: str | None,
    metadata: dict[str, str],
) -> tuple[str, ...]:
    evidence = " ".join(
        part
        for part in (
            title,
            location_text,
            _successfactors_metadata_value(metadata, "workplace", "work arrangement", "remote"),
        )
        if part
    ).casefold()
    if "hybrid" in evidence:
        return ("hybrid",)
    if "remote" in evidence or "work from home" in evidence:
        return ("remote",)
    if "on-site" in evidence or "onsite" in evidence:
        return ("office",)
    return ()


def _successfactors_remote_locations(
    *,
    country: str | None,
    location_text: str | None,
    work_format: str | None,
) -> tuple[str, ...]:
    if work_format != "remote":
        return ()
    if country:
        return (country,)
    if not location_text:
        return ()
    values = [
        value.strip()
        for value in location_text.split(",")
        if value.strip() and value.strip().casefold() != "remote"
    ]
    return tuple(dict.fromkeys(values))


def _successfactors_posted_at(value: str | None) -> str | None:
    text = _normalize_space(value or "")
    if not text:
        return None
    for date_format in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return text


_AtsSearchParser = Callable[[str, AtsCompanySourceConfig], SourceSearchParseResult]
_ATS_SEARCH_PARSERS: dict[str, _AtsSearchParser] = {
    "lever": _parse_lever,
    "ashby": _parse_ashby,
    "workable": _parse_workable,
    "greenhouse": _parse_greenhouse,
    "bamboohr": _parse_bamboohr,
    "recruitee": _parse_recruitee,
    "breezy": _parse_breezy,
    "huntflow": _parse_huntflow,
    "jsonld_jobposting": _parse_jsonld_job_postings,
    "ycombinator": _parse_ycombinator,
    "comeet": _parse_comeet,
    "jazzhr": _parse_jazzhr,
    "successfactors": _parse_successfactors,
}


def _joined_unique(values: Any) -> str | None:
    unique: list[str] = []
    for value in values:
        text = _text(value).strip()
        if text and text not in unique:
            unique.append(text)
    return ", ".join(unique) or None


def _parse_dreamjob(
    body: str,
    config: AtsCompanySourceConfig,
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


def _dreamjob_listing(card: _DreamJobCard, config: AtsCompanySourceConfig) -> RawListing:
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
    config: AtsCompanySourceConfig,
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
    def __init__(self, *, config: AtsCompanySourceConfig) -> None:
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
    config: AtsCompanySourceConfig,
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


def _dreamjob_job_posting(body: str, config: AtsCompanySourceConfig) -> dict[str, Any]:
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


def _json_assignment_array(
    body: str,
    variable_name: str,
    config: AtsCompanySourceConfig,
) -> list[Any]:
    value = _json_assignment_value(body, variable_name, config)
    if not isinstance(value, list):
        raise ValueError(f"{config.company} Comeet {variable_name} assignment is not a JSON array")
    return value


def _json_assignment_object(
    body: str,
    variable_name: str,
    config: AtsCompanySourceConfig,
) -> dict[str, Any]:
    value = _json_assignment_value(body, variable_name, config)
    if not isinstance(value, dict):
        raise ValueError(f"{config.company} Comeet {variable_name} assignment is not a JSON object")
    return {str(key): item for key, item in value.items()}


def _json_assignment_value(
    body: str,
    variable_name: str,
    config: AtsCompanySourceConfig,
) -> object:
    marker = f"{variable_name} ="
    marker_start = body.find(marker)
    if marker_start == -1:
        raise ValueError(f"{config.company} Comeet response is missing {variable_name} assignment")
    start = marker_start + len(marker)
    while start < len(body) and body[start].isspace():
        start += 1
    if start >= len(body) or body[start] not in "[{":
        raise ValueError(f"{config.company} Comeet {variable_name} assignment does not start with JSON")
    try:
        value, _end = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"{config.company} Comeet {variable_name} assignment is not valid JSON") from exc
    return value


def _no_results() -> SourceSearchParseResult:
    return SourceSearchParseResult(
        outcome=SourceOutcome.NO_RESULTS,
        listings=(),
        evidence=AttemptEvidence(no_results=True),
    )


def _source_raw(config: AtsCompanySourceConfig) -> dict[str, object]:
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


def _lever_work_formats(
    *,
    workplace_type: str | None,
    location_text: str | None,
    config: AtsCompanySourceConfig,
) -> tuple[str, ...]:
    formats: list[str] = []
    if config.lever_remote_work_format_from_location and _location_text_has_remote_marker(location_text):
        formats.append("remote")
    for work_format in _work_formats_from_workplace_type(workplace_type):
        if work_format not in formats:
            formats.append(work_format)
    return tuple(formats)


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


def _work_format_for_remote_flags(values: tuple[str, ...]) -> str | None:
    if "remote" in values:
        return "remote"
    return _single_format(values)


def _lever_remote_locations(
    *,
    workplace_type: str | None,
    all_locations: tuple[str, ...],
    location_text: str | None,
    config: AtsCompanySourceConfig,
) -> tuple[str, ...]:
    if workplace_type != "remote" and not (
        config.lever_remote_work_format_from_location and _location_text_has_remote_marker(location_text)
    ):
        return ()
    return tuple(
        cleaned
        for location in all_locations
        for cleaned in (_clean_remote_scope(location),)
        if cleaned is not None
    )


def _location_text_has_remote_marker(value: str | None) -> bool:
    return bool(value and value.strip().casefold().startswith("remote"))


def _lever_location_text(primary_location: object, all_locations: tuple[str, ...]) -> str | None:
    if all_locations:
        return ", ".join(all_locations)
    return _text(primary_location).strip() or None


def _lever_description(posting: dict[str, Any], config: AtsCompanySourceConfig) -> str | None:
    if config.lever_description_from_description_plain:
        return _plain_text(posting.get("descriptionPlain"))
    return _join_text(
        _plain_text(posting.get("openingPlain")),
        _plain_text(posting.get("descriptionBodyPlain")),
        _plain_text(posting.get("additionalPlain")),
    )


def _lever_country_city(location_text: str | None, config: AtsCompanySourceConfig) -> tuple[str | None, str | None]:
    if not config.lever_country_city_from_location or not location_text:
        return None, None
    cleaned = location_text.strip()
    if not cleaned or ";" in cleaned or _location_text_has_remote_marker(cleaned):
        return None, None
    if "," in cleaned:
        city, country = (part.strip() for part in cleaned.rsplit(",", 1))
        return country or None, city or None
    if cleaned in config.lever_city_location_names:
        return None, cleaned
    return cleaned, None


def _remote_in_country(*, work_format: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if work_format != "remote":
        return None
    if _has_global_remote_scope(remote_locations):
        return None
    return True if any(_has_structured_remote_scope(location) for location in remote_locations) else None


def _remote_global(*, work_format: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if work_format != "remote":
        return None
    if _has_global_remote_scope(remote_locations):
        return True
    return False if remote_locations else None


def _remote_in_country_for_config(
    config: AtsCompanySourceConfig,
    *,
    work_format: str | None,
    remote_locations: tuple[str, ...],
) -> bool | None:
    if work_format == "remote" and config.remote_in_country_from_any_remote_location:
        if _has_global_remote_scope(remote_locations):
            return None
        return True if remote_locations else None
    if work_format in {"office", "hybrid"}:
        return config.non_remote_in_country
    return _remote_in_country(work_format=work_format, remote_locations=remote_locations)


def _remote_global_for_config(
    config: AtsCompanySourceConfig,
    *,
    work_format: str | None,
    remote_locations: tuple[str, ...],
) -> bool | None:
    if work_format == "hybrid" and config.hybrid_remote_global is not None:
        return config.hybrid_remote_global
    return _remote_global(work_format=work_format, remote_locations=remote_locations)


def _posted_at_from_millis(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _lever_posted_at(value: object, config: AtsCompanySourceConfig) -> str | None:
    posted_at = _posted_at_from_millis(value)
    if posted_at is None or not config.lever_posted_at_date_only:
        return posted_at
    return posted_at.split("T", 1)[0]


def _has_global_remote_scope(remote_locations: tuple[str, ...]) -> bool:
    return any(
        location.strip().casefold() in {"anywhere", "global", "remote", "worldwide"}
        for location in remote_locations
    )


def _is_global_location(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"anywhere", "global", "remote", "worldwide"}


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


def _html_sections(value: str, *, strong_section_labels: tuple[str, ...] = ()) -> dict[str, str]:
    raw_html = html.unescape(value)
    labels = tuple(
        match
        for match in _SECTION_LABEL_RE.finditer(raw_html)
        if _section_label(match, strong_section_labels=strong_section_labels)
    )
    sections: dict[str, str] = {}
    for index, match in enumerate(labels):
        label = _section_label(match, strong_section_labels=strong_section_labels)
        if label is None:
            continue
        body_start = match.end()
        body_end = labels[index + 1].start() if index + 1 < len(labels) else len(raw_html)
        body = html_to_text(raw_html[body_start:body_end])
        if body:
            sections[label] = body
    return sections


def _section_label(match: re.Match[str], *, strong_section_labels: tuple[str, ...] = ()) -> str | None:
    tag = match.group("tag").casefold()
    label = (html_to_text(match.group("label")) or "").rstrip(":").strip()
    if not label:
        return None
    normalized_labels = frozenset(item.casefold() for item in strong_section_labels)
    if (
        tag == "strong"
        and not match.group("label").strip().endswith(":")
        and label.casefold() not in normalized_labels
    ):
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


def _workable_detail_url_and_id(value: str, config: AtsCompanySourceConfig) -> tuple[str, str]:
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
    marker = "on_site"
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
    skipping_us_tier_descriptions = False
    for line in body.splitlines():
        normalized = line.strip().casefold()
        if any(normalized.startswith(marker) for marker in _SALARY_STOP_LINE_MARKERS):
            break
        if normalized == "us tiers":
            skipping_us_tier_descriptions = True
            continue
        if skipping_us_tier_descriptions and normalized == "us pay zones":
            skipping_us_tier_descriptions = False
            if lines and lines[-1] != "":
                lines.append("")
        if skipping_us_tier_descriptions:
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    return text or None


def _greenhouse_work_formats(
    location_text: str,
    *,
    office_format_from_non_remote_location: bool,
) -> tuple[str, ...]:
    work_formats: list[str] = []
    normalized = location_text.casefold()
    if "remote" in normalized:
        work_formats.append("remote")
    if "hybrid" in normalized:
        work_formats.append("hybrid")
    has_office_marker = "office" in normalized or "on-site" in normalized or "onsite" in normalized
    if has_office_marker or (
        office_format_from_non_remote_location
        and any(part.strip() and "remote" not in part.casefold() for part in location_text.split(";"))
    ):
        work_formats.append("office")
    return tuple(work_formats)


def _greenhouse_remote_locations(location_text: str) -> tuple[str, ...]:
    locations: list[str] = []
    for part in location_text.split(";"):
        cleaned_part = _remote_role_scope(part)
        if cleaned_part:
            locations.extend(_split_remote_scope(cleaned_part))
        comma_scope = _remote_comma_scope(part)
        if comma_scope:
            locations.extend(_split_remote_scope(comma_scope))
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


def _remote_comma_scope(value: str) -> str | None:
    stripped = value.strip()
    match = re.match(r"remote\s*,\s*(?P<scope>.+)", stripped, re.I)
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
    if not cleaned:
        return None
    normalized_parts = _split_remote_scope(cleaned)
    if len(normalized_parts) == 1:
        return normalized_parts[0]
    return cleaned


def _normalize_remote_scope_part(value: str) -> str:
    part = value.strip()
    if part.casefold().replace(".", "") in {"us", "usa"}:
        return "US"
    if part.casefold() == "european region":
        return "Europe"
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


def _greenhouse_country_city(location_text: str, *, parse_parts: bool) -> tuple[str | None, str | None]:
    if parse_parts:
        for part in location_text.split(";"):
            country, city = _greenhouse_country_city(part.strip(), parse_parts=False)
            if country or city:
                return country, city
        return None, None

    cleaned = _strip_workplace_marker(location_text)
    if cleaned is None or _is_region_label(cleaned):
        return None, None
    normalized = cleaned.casefold()
    if normalized.startswith("remote,"):
        country = cleaned.split(",", 1)[1].strip()
        return country or None, None
    if ";" in cleaned or "remote" in normalized:
        return None, None
    if "," not in cleaned:
        return None, cleaned or None
    city, country = (part.strip() for part in cleaned.rsplit(",", 1))
    return country or None, city or None


def _linkedin_workplace_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    for match in _LINKEDIN_TAG_RE.findall(html.unescape(value)):
        tag = match.upper()
        if tag.casefold() in _LINKEDIN_WORKPLACE_TAGS and tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _remove_linkedin_tags(value: str) -> str:
    return _LINKEDIN_TAG_RE.sub("", html.unescape(value))


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
    config: AtsCompanySourceConfig,
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


def _workday_search_body(*, offset: int, search_text: str) -> bytes:
    return json.dumps(
        {
            "appliedFacets": {},
            "limit": _WORKDAY_PAGE_LIMIT,
            "offset": offset,
            "searchText": search_text,
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
        body=_workday_search_body(offset=next_offset, search_text=_workday_request_search_text(request)),
    )


def _workday_parallel_requests(
    payload: dict[str, Any],
    request: SourceFetchRequest,
    *,
    source_limit: int,
) -> tuple[SourceFetchRequest, ...]:
    total = _int_value(payload, "total")
    postings = payload.get("jobPostings")
    current_offset = _workday_request_offset(request)
    if current_offset != 0 or total is None or not isinstance(postings, list) or not postings:
        return ()
    page_step = len(postings)
    max_records = min(total, source_limit)
    return tuple(
        SourceFetchRequest(
            source_id=request.source_id,
            query_variant=request.query_variant,
            url=_workday_page_url(request.url, next_offset),
            method=HttpMethod.POST,
            headers=dict(_WORKDAY_SEARCH_HEADERS),
            body=_workday_search_body(offset=next_offset, search_text=_workday_request_search_text(request)),
        )
        for next_offset in range(current_offset + page_step, max_records, page_step)
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


def _workday_request_search_text(request: SourceFetchRequest) -> str:
    if request.body is None:
        return ""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    search_text = payload.get("searchText")
    return search_text if isinstance(search_text, str) else ""


def _workday_source_listing_id(posting: dict[str, Any], external_path: str) -> str:
    bullet_fields = _text_values(posting.get("bulletFields"))
    if bullet_fields:
        return bullet_fields[0]
    return external_path.rstrip("/").rsplit("/", 1)[-1]


def _workday_page_url(url: str, offset: int) -> str:
    base_url = url.split("?", 1)[0]
    return f"{base_url}?offset={offset}"


def _workday_cxs_detail_url(config: AtsCompanySourceConfig, external_path: str) -> str:
    base_url = _workday_config_value(config.workday_base_url, "workday_base_url", config)
    tenant = _workday_config_value(config.workday_tenant, "workday_tenant", config)
    site = _workday_config_value(config.workday_site, "workday_site", config)
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{base_url}/wday/cxs/{tenant}/{site}{path}"


def _workday_public_job_url(config: AtsCompanySourceConfig, external_path: str) -> str:
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


def _workday_config_value(value: str | None, field_name: str, config: AtsCompanySourceConfig) -> str:
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


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _required_text(value: object, field_name: str, config: AtsCompanySourceConfig) -> str:
    text = _text(value).strip()
    if not text:
        raise ValueError(f"{config.company} {config.platform} posting is missing {field_name}")
    return text


def _required_cell(row: dict[str, str], key: str, config: AtsCompanySourceConfig) -> str:
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
