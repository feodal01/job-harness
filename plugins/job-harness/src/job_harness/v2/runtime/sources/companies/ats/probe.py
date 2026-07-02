"""Ad-hoc ATS career board detection."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from job_harness.v2.runtime.sources.companies.ats.source import (
    AtsCompanySourceConfig,
    AtsPlatform,
)

_DEFAULT_SOURCE_ID = "adhoc:ats"
_LEVER_API_PATH_PARTS = 3
_GREENHOUSE_API_PATH_PARTS = 4
_ASHBY_API_PATH_PARTS = 3
_SMARTRECRUITERS_API_PATH_PARTS = 4
_COMEET_PATH_PARTS = 3
_WORKDAY_CXS_PATH_PARTS = 5
_JOIN_PATH_PARTS = 2
_DREAMJOB_PATH_PARTS = 3
_YCOMBINATOR_PATH_PARTS = 3


def detect_ats_company_config(
    url: str,
    *,
    company: str | None = None,
    source_id: str = _DEFAULT_SOURCE_ID,
    platform: AtsPlatform | None = None,
) -> AtsCompanySourceConfig:
    parsed = _parse_http_url(url)
    config = _detect_known_ats_config(parsed, company=company, source_id=source_id)
    if config is not None:
        if platform is not None and platform != config.platform:
            raise ValueError(f"URL matches {config.platform}, not requested platform {platform}")
        return config
    if platform is None:
        raise ValueError(f"unsupported ATS URL pattern: {url}")
    return AtsCompanySourceConfig(
        source_id=source_id,
        company=_company_name(company, _host_slug(parsed)),
        platform=platform,
        board_url=_url_without_fragment(parsed),
        career_url=_url_without_fragment(parsed),
    )


def _detect_known_ats_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    for detector in (
        _lever_config,
        _greenhouse_config,
        _ashby_config,
        _workable_config,
        _recruitee_config,
        _bamboohr_config,
        _breezy_config,
        _huntflow_config,
        _smartrecruiters_config,
        _teamtailor_config,
        _comeet_config,
        _jobvite_config,
        _jazzhr_config,
        _icims_config,
        _taleo_config,
        _successfactors_config,
        _workday_config,
        _personio_config,
        _join_config,
        _dreamjob_config,
        _ycombinator_config,
    ):
        config = detector(parsed, company=company, source_id=source_id)
        if config is not None:
            return config
    return None


@dataclass(frozen=True)
class _ParsedUrl:
    scheme: str
    host: str
    path: str
    query: str

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(part for part in self.path.split("/") if part)


def _parse_http_url(url: str) -> _ParsedUrl:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    return _ParsedUrl(
        scheme="https",
        host=parsed.netloc.lower(),
        path=parsed.path or "/",
        query=parsed.query,
    )


def _lever_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host in {"jobs.lever.co", "jobs.eu.lever.co"} and parts:
        slug = parts[0]
        api_host = "api.eu.lever.co" if parsed.host == "jobs.eu.lever.co" else "api.lever.co"
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="lever",
            board_url=f"https://{api_host}/v0/postings/{slug}?mode=json",
            career_url=f"https://{parsed.host}/{slug}",
        )
    if (
        parsed.host in {"api.lever.co", "api.eu.lever.co"}
        and len(parts) >= _LEVER_API_PATH_PARTS
        and parts[:2] == ("v0", "postings")
    ):
        slug = parts[2]
        career_host = "jobs.eu.lever.co" if parsed.host == "api.eu.lever.co" else "jobs.lever.co"
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="lever",
            board_url=_url_with_required_query(parsed, {"mode": "json"}),
            career_url=f"https://{career_host}/{slug}",
        )
    return None


def _greenhouse_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host in {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.greenhouse.io"} and parts:
        slug = parts[0]
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="greenhouse",
            board_url=f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
            career_url=f"https://{parsed.host}/{slug}",
        )
    if (
        parsed.host == "boards-api.greenhouse.io"
        and len(parts) >= _GREENHOUSE_API_PATH_PARTS
        and parts[:2] == ("v1", "boards")
    ):
        slug = parts[2]
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="greenhouse",
            board_url=_url_with_required_query(parsed, {"content": "true"}),
            career_url=f"https://job-boards.greenhouse.io/{slug}",
        )
    return None


def _ashby_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host == "jobs.ashbyhq.com" and parts:
        slug = parts[0]
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="ashby",
            board_url=f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            career_url=f"https://jobs.ashbyhq.com/{slug}",
        )
    if (
        parsed.host == "api.ashbyhq.com"
        and len(parts) >= _ASHBY_API_PATH_PARTS
        and parts[:2] == ("posting-api", "job-board")
    ):
        slug = parts[2]
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="ashby",
            board_url=_url_without_fragment(parsed),
            career_url=f"https://jobs.ashbyhq.com/{slug}",
        )
    return None


def _workable_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host != "apply.workable.com" or not parts:
        return None
    slug = parts[0]
    return AtsCompanySourceConfig(
        source_id=source_id,
        company=_company_name(company, slug),
        platform="workable",
        board_url=f"https://apply.workable.com/{slug}/jobs.md",
        career_url=f"https://apply.workable.com/{slug}/",
        workable_slug=slug,
    )


def _recruitee_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".recruitee.com"):
        return None
    slug = parsed.host.removesuffix(".recruitee.com")
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="recruitee",
        board_url=f"https://{parsed.host}/api/offers",
        career_url=f"https://{parsed.host}/",
    )


def _bamboohr_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".bamboohr.com"):
        return None
    slug = parsed.host.removesuffix(".bamboohr.com")
    return AtsCompanySourceConfig(
        source_id=source_id,
        company=_company_name(company, slug),
        platform="bamboohr",
        board_url=f"https://{parsed.host}/careers/list",
        career_url=f"https://{parsed.host}/careers/list",
        bamboohr_detail_url_template=f"https://{parsed.host}/careers/{{id}}",
    )


def _breezy_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".breezy.hr"):
        return None
    slug = parsed.host.removesuffix(".breezy.hr")
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="breezy",
        board_url=f"https://{parsed.host}/",
        career_url=f"https://{parsed.host}/",
    )


def _huntflow_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".huntflow.io"):
        return None
    slug = parsed.host.removesuffix(".huntflow.io")
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="huntflow",
        board_url=f"https://{parsed.host}/",
        career_url=f"https://{parsed.host}/",
    )


def _smartrecruiters_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host == "jobs.smartrecruiters.com" and parts:
        slug = parts[0]
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="smartrecruiters",
            board_url=f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
            career_url=f"https://jobs.smartrecruiters.com/{slug}",
        )
    if (
        parsed.host == "api.smartrecruiters.com"
        and len(parts) >= _SMARTRECRUITERS_API_PATH_PARTS
        and parts[:2] == ("v1", "companies")
    ):
        slug = parts[2]
        return _simple_config(
            source_id=source_id,
            company=company,
            slug=slug,
            platform="smartrecruiters",
            board_url=_url_with_required_query(parsed, {"limit": "100"}),
            career_url=f"https://jobs.smartrecruiters.com/{slug}",
        )
    return None


def _teamtailor_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".teamtailor.com"):
        return None
    slug = parsed.host.removesuffix(".teamtailor.com")
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="teamtailor",
        board_url=f"https://{parsed.host}/jobs",
        career_url=f"https://{parsed.host}/jobs",
    )


def _comeet_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if (
        parsed.host not in {"comeet.com", "www.comeet.com"}
        or len(parts) < _COMEET_PATH_PARTS
        or parts[0] != "jobs"
    ):
        return None
    slug = parts[1]
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="comeet",
        board_url=_url_without_fragment(parsed),
        career_url=_url_without_fragment(parsed),
    )


def _jobvite_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host != "jobs.jobvite.com" or not parts:
        return None
    slug = parts[0]
    board_path = "/" + "/".join(parts[:2]) if len(parts) > 1 and parts[1] == "jobs" else f"/{slug}"
    board_url = urlunparse(("https", parsed.host, board_path, "", "", ""))
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="jobvite",
        board_url=board_url,
        career_url=board_url,
    )


def _jazzhr_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".applytojob.com"):
        return None
    slug = parsed.host.removesuffix(".applytojob.com")
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="jazzhr",
        board_url=f"https://{parsed.host}/apply/jobs",
        career_url=f"https://{parsed.host}/apply/jobs",
    )


def _icims_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".icims.com") or parsed.parts[:2] != ("jobs", "search"):
        return None
    slug = parsed.host.removesuffix(".icims.com")
    board_url = _url_with_required_query(parsed, {"ss": "1", "in_iframe": "1"})
    career_url = _url_with_required_query(parsed, {"ss": "1"})
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="icims",
        board_url=board_url,
        career_url=career_url,
    )


def _taleo_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".taleo.net") or not parsed.path.endswith("/ats/careers/v2/searchResults"):
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    slug = query.get("org") or parsed.host.split(".", 1)[0]
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="taleo",
        board_url=_url_without_fragment(parsed),
        career_url=_url_without_fragment(parsed),
    )


def _successfactors_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if "successfactors" not in parsed.host or parsed.path != "/career":
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    slug = query.get("company")
    if not slug:
        return None
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="successfactors",
        board_url=_url_with_required_query(
            parsed,
            {"career_ns": "job_listing_summary", "resultType": "XML"},
        ),
        career_url=_url_with_required_query(
            parsed,
            {"career_ns": "job_listing_summary", "navBarLevel": "JOB_SEARCH"},
        ),
    )


def _workday_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    if not parsed.host.endswith(".myworkdayjobs.com"):
        return None
    base_url = f"https://{parsed.host}"
    parts = parsed.parts
    if len(parts) >= _WORKDAY_CXS_PATH_PARTS and parts[:2] == ("wday", "cxs"):
        tenant = parts[2]
        site = parts[3]
        career_url = f"{base_url}/{site}"
    elif parts:
        tenant = parsed.host.split(".", 1)[0]
        site = parts[-1]
        career_url = _url_without_fragment(parsed)
    else:
        return None
    return AtsCompanySourceConfig(
        source_id=source_id,
        company=_company_name(company, tenant),
        platform="workday",
        board_url=f"{base_url}/wday/cxs/{tenant}/{site}/jobs",
        career_url=career_url,
        workday_base_url=base_url,
        workday_tenant=tenant,
        workday_site=site,
    )


def _personio_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    for suffix in (".jobs.personio.de", ".jobs.personio.com"):
        if parsed.host.endswith(suffix):
            slug = parsed.host.removesuffix(suffix)
            return _simple_config(
                source_id=source_id,
                company=company,
                slug=slug,
                platform="personio",
                board_url=f"https://{parsed.host}/",
                career_url=f"https://{parsed.host}/",
            )
    return None


def _join_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if parsed.host != "join.com" or len(parts) < _JOIN_PATH_PARTS or parts[0] != "companies":
        return None
    slug = parts[1]
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="join",
        board_url=f"https://join.com/companies/{slug}",
        career_url=f"https://join.com/companies/{slug}",
    )


def _dreamjob_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if (
        parsed.host != "dreamjob.ru"
        or len(parts) < _DREAMJOB_PATH_PARTS
        or parts[0] != "employers"
        or parts[2] != "vakansii"
    ):
        return None
    slug = parts[1]
    board_url = f"https://dreamjob.ru/employers/{slug}/vakansii"
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="dreamjob",
        board_url=board_url,
        career_url=board_url,
    )


def _ycombinator_config(
    parsed: _ParsedUrl,
    *,
    company: str | None,
    source_id: str,
) -> AtsCompanySourceConfig | None:
    parts = parsed.parts
    if (
        parsed.host != "www.ycombinator.com"
        or len(parts) < _YCOMBINATOR_PATH_PARTS
        or parts[0] != "companies"
        or parts[2] != "jobs"
    ):
        return None
    slug = parts[1]
    board_url = f"https://www.ycombinator.com/companies/{slug}/jobs"
    return _simple_config(
        source_id=source_id,
        company=company,
        slug=slug,
        platform="ycombinator",
        board_url=board_url,
        career_url=board_url,
    )


def _simple_config(
    *,
    source_id: str,
    company: str | None,
    slug: str,
    platform: AtsPlatform,
    board_url: str,
    career_url: str,
) -> AtsCompanySourceConfig:
    return AtsCompanySourceConfig(
        source_id=source_id,
        company=_company_name(company, slug),
        platform=platform,
        board_url=board_url,
        career_url=career_url,
    )


def _company_name(company: str | None, slug: str) -> str:
    if company is not None and company.strip():
        return company.strip()
    return slug.replace("-", " ").replace("_", " ").strip().title() or "Unknown Company"


def _host_slug(parsed: _ParsedUrl) -> str:
    parts = parsed.parts
    if parts:
        return parts[0]
    return parsed.host.split(".", 1)[0]


def _url_without_fragment(parsed: _ParsedUrl) -> str:
    return urlunparse((parsed.scheme, parsed.host, parsed.path, "", parsed.query, ""))


def _url_with_required_query(parsed: _ParsedUrl, required: dict[str, str]) -> str:
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in required
    ]
    pairs.extend(required.items())
    return urlunparse((parsed.scheme, parsed.host, parsed.path, "", urlencode(pairs), ""))
