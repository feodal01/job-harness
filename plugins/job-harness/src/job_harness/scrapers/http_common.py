"""Small HTTP and HTML helpers for non-browser scrapers."""

from __future__ import annotations

import json
import re
import ssl
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_SECONDS = 15
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


@dataclass(frozen=True)
class Anchor:
    href: str
    text: str
    attrs: dict[str, str]


def fetch_text(url: str, *, verify_ssl: bool = True) -> str:
    context = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS, context=context) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


class AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self._current: dict | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._current is None:
            attr = {key: value or "" for key, value in attrs}
            self._current = {"href": attr.get("href", ""), "attrs": attr, "parts": []}
            self._depth = 1
            return

        if self._current is not None and tag not in VOID_TAGS:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag not in VOID_TAGS:
            self._depth -= 1
        if self._depth == 0:
            self.anchors.append(Anchor(
                href=self._current["href"],
                text=normalize_text(" ".join(self._current["parts"])),
                attrs=self._current["attrs"],
            ))
            self._current = None


def extract_anchors(html: str) -> list[Anchor]:
    parser = AnchorExtractor()
    parser.feed(html)
    return parser.anchors


def extract_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if match is None:
        raise ValueError("Missing __NEXT_DATA__ payload")
    return json.loads(match.group(1))
