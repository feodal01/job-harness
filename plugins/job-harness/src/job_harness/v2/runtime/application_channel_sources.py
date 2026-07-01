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
    "teamtailor.com",
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
    company_career_url: str | None = None
    aggregator_profile_url: str | None = None
    aggregator_source: str | None = None
    resolution_policy: ApplicationChannelResolutionPolicy | None = None

    @property
    def has_channel(self) -> bool:
        return bool(self.company_site_url or self.company_career_url or self.aggregator_profile_url)


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
STAFF_AM_APPLICATION_CHANNEL_POLICY = ApplicationChannelResolutionPolicy(
    source_id="staff_am",
    career_markers=_CAREER_MARKERS,
    career_text_markers=_CAREER_TEXT_MARKERS,
    career_text_exact=_CAREER_TEXT_EXACT,
    ats_domains=_ATS_DOMAINS,
    non_career_link_domains=_AGGREGATOR_CAREER_DOMAINS,
    profile_site_text_markers=("website",),
)
HIREHI_APPLICATION_CHANNEL_POLICY = ApplicationChannelResolutionPolicy(
    source_id="hirehi",
    career_markers=_CAREER_MARKERS,
    career_text_markers=_CAREER_TEXT_MARKERS,
    career_text_exact=_CAREER_TEXT_EXACT,
    ats_domains=_ATS_DOMAINS,
    non_career_link_domains=_AGGREGATOR_CAREER_DOMAINS,
    profile_site_text_markers=("сайт компании", "website", "official site"),
)
GEEKJOB_APPLICATION_CHANNEL_POLICY = ApplicationChannelResolutionPolicy(
    source_id="geekjob",
    career_markers=_CAREER_MARKERS,
    career_text_markers=_CAREER_TEXT_MARKERS,
    career_text_exact=_CAREER_TEXT_EXACT,
    ats_domains=_ATS_DOMAINS,
    non_career_link_domains=_AGGREGATOR_CAREER_DOMAINS,
    profile_site_text_markers=("сайт", "website", "official site"),
)
IT_JOBS_UZ_APPLICATION_CHANNEL_POLICY = ApplicationChannelResolutionPolicy(
    source_id="it_jobs_uz",
    career_markers=_CAREER_MARKERS,
    career_text_markers=_CAREER_TEXT_MARKERS,
    career_text_exact=_CAREER_TEXT_EXACT,
    ats_domains=_ATS_DOMAINS,
    non_career_link_domains=_AGGREGATOR_CAREER_DOMAINS,
)
_COMPANY_CAREER_URLS = {
    "career:3commas": "https://jobs.ashbyhq.com/3commas",
    "career:airslate": "https://jobs.lever.co/airslate",
    "career:appfollow": "https://jobs.lever.co/appfollow",
    "career:chainstack": "https://chainstack.bamboohr.com/careers/list",
    "career:outschool": "https://job-boards.greenhouse.io/outschool",
    "career:termius": "https://jobs.lever.co/Termius",
    "career:truv": "https://jobs.lever.co/truv",
    "career:wallarm": "https://wallarm.recruitee.com/",
    "career:wintermute": "https://jobs.lever.co/wintermute-trading",
    "career:zeroavia": "https://apply.workable.com/zeroavia/",
    "career:collectly": "https://jobs.lever.co/CollectlyInc",
    "career:planner5d": "https://jobs.lever.co/planner5d",
    "career:superannotate": "https://jobs.lever.co/superannotate",
    "career:xsolla": "https://jobs.lever.co/xsolla",
    "career:unlimint": "https://jobs.lever.co/unlimit",
    "career:clickhouse": "https://jobs.ashbyhq.com/clickhouse",
    "career:datafold": "https://jobs.ashbyhq.com/datafold",
    "career:inworld": "https://jobs.ashbyhq.com/inworld-ai",
    "career:luminai": "https://jobs.ashbyhq.com/luminai",
    "career:teleport": "https://jobs.ashbyhq.com/goteleport",
    "career:mapbox": "https://jobs.ashbyhq.com/Mapbox",
    "career:joom": "https://apply.workable.com/joom/",
    "career:zeptolab": "https://apply.workable.com/zeptolab/",
    "career:homebuddy": "https://apply.workable.com/homebuddy/",
    "career:lyka": "https://apply.workable.com/lyka/",
    "career:abbyy": "https://job-boards.greenhouse.io/abbyy",
    "career:ahrefs": "https://job-boards.greenhouse.io/ahrefsjobs",
    "career:eqvilent": "https://job-boards.greenhouse.io/eqvilentjobs",
    "career:humansignal": "https://job-boards.greenhouse.io/humansignal",
    "career:lokalise": "https://job-boards.greenhouse.io/lokalise",
    "career:adtech-holding": "https://adtechholding.bamboohr.com/careers/list",
    "career:altenar": "https://altenar.bamboohr.com/careers/list",
    "career:synder": "https://synder.bamboohr.com/careers/list",
    "career:crystal": "https://crystalintelligence.teamtailor.com/jobs",
    "career:synthesized": "https://synthesized.teamtailor.com/jobs",
    "career:tradingview": "https://tradingview.teamtailor.com/jobs",
    "career:osome": "https://careers.osome.com/jobs",
    "career:sumsub": "https://careers.sumsub.com/jobs",
}


def application_channel_seed(listing: RawListing) -> ApplicationChannelSeed:
    if listing.source == "hh_ru":
        return _hh_seed(listing)
    if listing.source == "habr_career":
        return _habr_career_seed(listing)
    if listing.source == "getmatch":
        return _company_fact_seed(listing, aggregator_source="getmatch")
    if listing.source == "staff_am":
        return _company_fact_seed(
            listing,
            aggregator_source="staff_am",
            policy=STAFF_AM_APPLICATION_CHANNEL_POLICY,
        )
    if listing.source == "hirehi":
        return _company_fact_seed(
            listing,
            aggregator_source="hirehi",
            policy=HIREHI_APPLICATION_CHANNEL_POLICY,
        )
    if listing.source == "geekjob":
        return _company_fact_seed(
            listing,
            aggregator_source="geekjob",
            policy=GEEKJOB_APPLICATION_CHANNEL_POLICY,
        )
    if listing.source == "it_jobs_uz":
        return _company_fact_seed(
            listing,
            aggregator_source="it_jobs_uz",
            policy=IT_JOBS_UZ_APPLICATION_CHANNEL_POLICY,
        )
    if listing.source in _COMPANY_CAREER_URLS:
        return ApplicationChannelSeed(
            company_career_url=_COMPANY_CAREER_URLS[listing.source],
            aggregator_source=listing.source,
        )
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


def _company_fact_seed(
    listing: RawListing,
    *,
    aggregator_source: str,
    policy: ApplicationChannelResolutionPolicy | None = None,
) -> ApplicationChannelSeed:
    company = listing.raw.get("company")
    if not isinstance(company, dict):
        return ApplicationChannelSeed()
    return ApplicationChannelSeed(
        company_site_url=_optional_text(company.get("companySiteUrl")),
        company_career_url=_optional_text(company.get("companyVacanciesUrl")),
        aggregator_profile_url=_optional_text(company.get("companyProfileUrl")),
        aggregator_source=aggregator_source,
        resolution_policy=policy,
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
