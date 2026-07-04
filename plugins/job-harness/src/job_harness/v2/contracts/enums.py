"""Closed enums used by the strict search contracts."""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    AGGREGATOR = "aggregator"
    COMPANY_CAREER = "company_career"


class Transport(StrEnum):
    HTTP = "http"
    BROWSER = "browser"
    HYBRID = "hybrid"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


class Grade(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"


class WorkFormat(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    OFFICE = "office"
    UNKNOWN = "unknown"


class TextExclusionMode(StrEnum):
    SUBSTRING = "substring"
    REGEX = "regex"


class TextField(StrEnum):
    TITLE = "title"
    DESCRIPTION = "description"
    REQUIREMENTS = "requirements"
    SKILLS = "skills"
    RAW_TEXT = "raw_text"


class SearchCriterion(StrEnum):
    QUERY = "query"
    GRADES = "grades"
    SALARY_FROM = "salary_from"
    PUBLISHED_SINCE = "published_since"
    RELOCATION = "relocation"
    WORK_FORMATS = "work_formats"
    REMOTE_SCOPES = "remote_scopes"
    VACANCY_GEOGRAPHIES = "vacancy_geographies"


class CriterionCapability(StrEnum):
    NATIVE_REQUEST = "native_request"
    STRUCTURED_OUTPUT = "structured_output"
    UNSUPPORTED = "unsupported"


class SourceOutcome(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    PARTIAL_SUCCESS = "partial_success"
    SKIPPED_BY_POLICY = "skipped_by_policy"
    CANCELLED = "cancelled"
    SOURCE_TIMEOUT = "source_timeout"
    RUN_TIMEOUT = "run_timeout"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    HTTP_CLIENT_ERROR = "http_client_error"
    HTTP_SERVER_ERROR = "http_server_error"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    INVALID_SOURCE_OUTPUT = "invalid_source_output"
    RESOURCE_FAILURE = "resource_failure"


class ProcessingDecision(StrEnum):
    KEPT = "kept"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class DescriptionAvailability(StrEnum):
    PRESENT = "present"
    NOT_EXPOSED = "not_exposed"
    DETAIL_TIMEOUT = "detail_timeout"
    DETAIL_BLOCKED = "detail_blocked"
    DETAIL_PARSE_ERROR = "detail_parse_error"
    DETAIL_RATE_LIMITED = "detail_rate_limited"
    NOT_REQUESTED = "not_requested"


class ParserFixtureKind(StrEnum):
    SUCCESS_NON_EMPTY = "success_non_empty"
    NO_RESULTS = "no_results"
    PAGINATION = "pagination"
    DETAIL = "detail"
    OPTIONAL_FIELDS = "optional_fields"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    LOGIN = "login"
    GEO_BLOCKED = "geo_blocked"
    MALFORMED_SOURCE = "malformed_source"


class RetryNextAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    STOP = "stop"


ALL_SEARCH_CRITERIA: tuple[SearchCriterion, ...] = tuple(SearchCriterion)
