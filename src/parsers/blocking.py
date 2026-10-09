"""Block pages that HTTP status alone does not reveal. Shared by sources; pure functions, no network.

Signals were collected by the original job-harness on live sites (browser_pool.py there):
hh.ru redirected traffic from a VPN to `/vpncheeck` (sic) with an empty page and no cards.
"""

import re
from urllib.parse import urljoin, urlsplit

from src.parsers.interfaces import ResponseClass, ResponseInfo

_PATHS: dict[str, ResponseClass] = {
    "vpncheck": "vpn_blocked",
    "vpncheeck": "vpn_blocked",
    "captcha": "captcha",
    "blocked": "blocked",
    "access-denied": "blocked",
}
_PATH_RE = re.compile(r"^/(" + "|".join(map(re.escape, _PATHS)) + r")(?:/|$)", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_ANTI_BOT_TITLE_RE = re.compile(
    r"доступ ограничен|just a moment|verify you are human|подтвердите.+человек|"
    r"attention required|checking your browser|access denied",
    re.I,
)
_CAPTCHA_TITLE_RE = re.compile(r"recaptcha|hcaptcha|captcha|prove you are human", re.I)


def _path_class(url: str) -> ResponseClass | None:
    match = _PATH_RE.match(urlsplit(url).path)
    return _PATHS[match.group(1).lower()] if match else None


def classify_common(response: ResponseInfo) -> ResponseClass | None:
    """A block page reached directly or about to be reached by a redirect; or an anti-bot interstitial."""
    target = response.headers.get("location")
    for url in (response.url, urljoin(response.url, target) if target else None):
        if url and (kind := _path_class(url)):
            return kind
    title = _TITLE_RE.search(response.text[:20_000])  # The title is near the top; pages are ~1 MB.
    if title:
        text = title.group(1)
        if _ANTI_BOT_TITLE_RE.search(text):
            return "blocked"
        if _CAPTCHA_TITLE_RE.search(text):
            return "captcha"
    return None
