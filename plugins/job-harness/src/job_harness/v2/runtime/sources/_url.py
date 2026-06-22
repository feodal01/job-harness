"""URL helpers for source scrapers."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


def strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def update_query(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=False))
    query.update(params)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            "",
        )
    )


def absolute_url(base_url: str, href: str) -> str:
    return strip_query(urljoin(base_url, href))
