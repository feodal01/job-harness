"""Extract application-channel seed facts from aggregator raw listings."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import RawListing

_CAREER_MARKERS = (
    "career",
    "careers",
    "jobs",
    "job",
    "vacanc",
    "ваканс",
    "карьер",
    "работа",
    "join us",
    "join-us",
    "open positions",
    "rabota",
)
_CAREER_TEXT_MARKERS = (
    "career",
    "careers",
    "jobs",
    "ваканс",
    "карьер",
    "работа у нас",
    "работать у нас",
    "присоединяйтесь",
    "join us",
    "open positions",
)
_CAREER_TEXT_EXACT = frozenset(
    {
        "career",
        "careers",
        "jobs",
        "вакансии",
        "карьера",
        "работа",
    }
)
_ATS_DOMAINS = (
    "ashbyhq.com",
    "boards.greenhouse.io",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "workable.com",
)
_AGGREGATOR_CAREER_DOMAINS = (
    "career.habr.com",
    "getmatch.ru",
    "hh.ru",
    "rabota.ru",
    "superjob.ru",
)


@dataclass(frozen=True)
class ApplicationChannelResolutionPolicy:
    source_id: str
    career_markers: tuple[str, ...]
    career_text_markers: tuple[str, ...]
    career_text_exact: frozenset[str]
    ats_domains: tuple[str, ...]
    non_career_link_domains: tuple[str, ...]
    profile_site_text_markers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")
        if not self.career_markers:
            raise ValueError("career_markers must be non-empty")


@dataclass(frozen=True)
class ApplicationChannelSeed:
    company_site_url: str | None = None
    aggregator_profile_url: str | None = None
    aggregator_source: str | None = None
    resolution_policy: ApplicationChannelResolutionPolicy | None = None

    @property
    def has_channel(self) -> bool:
        return bool(self.company_site_url or self.aggregator_profile_url)


HH_APPLICATION_CHANNEL_POLICY = ApplicationChannelResolutionPolicy(
    source_id="hh_ru",
    career_markers=_CAREER_MARKERS,
    career_text_markers=_CAREER_TEXT_MARKERS,
    career_text_exact=_CAREER_TEXT_EXACT,
    ats_domains=_ATS_DOMAINS,
    non_career_link_domains=_AGGREGATOR_CAREER_DOMAINS,
    profile_site_text_markers=(
        "официальный сайт",
        "official site",
        "website",
    ),
)
HABR_CAREER_APPLICATION_CHANNEL_POLICY = ApplicationChannelResolutionPolicy(
    source_id="habr_career",
    career_markers=_CAREER_MARKERS,
    career_text_markers=_CAREER_TEXT_MARKERS,
    career_text_exact=_CAREER_TEXT_EXACT,
    ats_domains=_ATS_DOMAINS,
    non_career_link_domains=_AGGREGATOR_CAREER_DOMAINS,
)


def application_channel_seed(listing: RawListing) -> ApplicationChannelSeed:
    if listing.source == "hh_ru":
        return _hh_seed(listing)
    if listing.source == "habr_career":
        return _habr_career_seed(listing)
    return ApplicationChannelSeed()


def _hh_seed(listing: RawListing) -> ApplicationChannelSeed:
    company = listing.raw.get("company")
    if not isinstance(company, dict):
        return ApplicationChannelSeed()
    return ApplicationChannelSeed(
        company_site_url=_optional_text(company.get("companySiteUrl")),
        aggregator_profile_url=_optional_text(company.get("employerUrl")),
        aggregator_source="hh_ru",
        resolution_policy=HH_APPLICATION_CHANNEL_POLICY,
    )


def _habr_career_seed(listing: RawListing) -> ApplicationChannelSeed:
    company = listing.raw.get("company")
    if not isinstance(company, dict):
        return ApplicationChannelSeed()
    return ApplicationChannelSeed(
        company_site_url=_optional_text(company.get("companySiteUrl")),
        aggregator_profile_url=_optional_text(company.get("companyProfileUrl")),
        aggregator_source="habr_career",
        resolution_policy=HABR_CAREER_APPLICATION_CHANNEL_POLICY,
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
