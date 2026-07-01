"""Extract public company contacts from company profile and site HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+\d[\d\s().-]{7,}\d)")
_MAX_CONTACTS = 12
_MAX_SOCIAL_TEXT_LENGTH = 60
_MIN_PHONE_DIGITS = 8
_CONTACT_PAGE_MARKERS = (
    "contacts",
    "contact",
    "kontakty",
    "kontakt",
    "контакт",
    "about",
    "о компании",
    "company",
)
_CONTACT_LABELS = {
    "email": ("email", "Email"),
    "e-mail": ("email", "Email"),
    "почта": ("email", "Email"),
    "телефон": ("phone", "Phone"),
    "phone": ("phone", "Phone"),
    "tel": ("phone", "Phone"),
    "vk": ("vk", "VK"),
    "вк": ("vk", "VK"),
    "telegram": ("telegram", "Telegram"),
    "телеграм": ("telegram", "Telegram"),
    "tg": ("telegram", "Telegram"),
    "youtube": ("youtube", "YouTube"),
    "ютуб": ("youtube", "YouTube"),
    "дзен": ("dzen", "Dzen"),
    "dzen": ("dzen", "Dzen"),
}


@dataclass(frozen=True)
class _Anchor:
    href: str
    text: str


@dataclass(frozen=True)
class _ContactRow:
    label: str
    value: str
    href: str | None


class _HabrProfileContactCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_ContactRow] = []
        self._row_depth: int | None = None
        self._field: str | None = None
        self._field_depth: int | None = None
        self._label_parts: list[str] = []
        self._value_parts: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.casefold(): value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if self._row_depth is None:
            if tag == "div" and classes.intersection({"contact", "link"}):
                self._row_depth = 1
                self._label_parts = []
                self._value_parts = []
                self._href = None
            return

        if tag != "br":
            self._row_depth += 1
        if tag == "div" and "type" in classes:
            self._field = "label"
            self._field_depth = self._row_depth
            return
        if tag == "div" and "value" in classes:
            self._field = "value"
            self._field_depth = self._row_depth
            return
        if tag == "a" and self._field == "value":
            self._href = attr_map.get("href", "").strip() or self._href

    def handle_data(self, data: str) -> None:
        if self._field is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._field == "label":
            self._label_parts.append(text)
        else:
            self._value_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._row_depth is None:
            return
        if self._field_depth == self._row_depth:
            self._field = None
            self._field_depth = None
        if tag != "br":
            self._row_depth -= 1
        if self._row_depth == 0:
            label = " ".join(self._label_parts)
            value = " ".join(self._value_parts)
            if label and value:
                self.rows.append(_ContactRow(label=label, value=value, href=self._href))
            self._row_depth = None
            self._field = None
            self._field_depth = None


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = {key.casefold(): value or "" for key, value in attrs}
        href = attr_map.get("href", "").strip()
        if not href:
            return
        self._href = href
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is None:
            return
        text = " ".join(data.split())
        if text:
            self._text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        self.anchors.append(_Anchor(href=self._href, text=" ".join(self._text_parts)))
        self._href = None
        self._text_parts = []


class _VisibleTextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def text(self) -> str:
        return " ".join(self.parts)


def supports_profile_contacts(source_id: str) -> bool:
    return source_id == "habr_career"


def profile_contacts_from_html(*, source_id: str, base_url: str, html: str) -> tuple[dict[str, str], ...]:
    if source_id != "habr_career":
        return ()
    collector = _HabrProfileContactCollector()
    collector.feed(html)
    contacts: list[dict[str, str]] = []
    for row in collector.rows:
        contact = _contact_from_labeled_row(row, base_url=base_url, source=f"{source_id}.company_profile")
        if contact is not None:
            contacts.append(contact)
    return merge_company_contacts(tuple(contacts))


def site_contacts_from_html(*, base_url: str, html: str, source: str) -> tuple[dict[str, str], ...]:
    contacts: list[dict[str, str]] = []
    anchors = _anchors_from_html(html)
    for anchor in anchors:
        contact = _contact_from_anchor(anchor, base_url=base_url, source=source)
        if contact is not None:
            contacts.append(contact)

    visible_text = _visible_text(html)
    for email in _EMAIL_RE.findall(visible_text):
        contacts.append(_company_contact("email", "Email", email.lower(), f"mailto:{email.lower()}", source))
    for phone in _PHONE_RE.findall(visible_text):
        phone_url = _tel_url(phone)
        if phone_url is not None:
            contacts.append(_company_contact("phone", "Phone", _normalize_spaces(phone), phone_url, source))
    return merge_company_contacts(tuple(contacts))


def best_contact_page_url(*, base_url: str, html: str) -> str | None:
    base_host = urlparse(base_url).netloc.casefold()
    scored: list[tuple[int, str]] = []
    for anchor in _anchors_from_html(html):
        url = urljoin(base_url, anchor.href)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != base_host:
            continue
        if _same_document(url, base_url):
            continue
        haystack = f"{parsed.path} {anchor.text}".casefold()
        if not any(marker in haystack for marker in _CONTACT_PAGE_MARKERS):
            continue
        score = 1
        if "contact" in haystack or "контакт" in haystack:
            score += 4
        if "about" in haystack or "о компании" in haystack:
            score += 2
        scored.append((score, _canonical_url(url)))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def merge_company_contacts(*groups: tuple[dict[str, str], ...]) -> tuple[dict[str, str], ...]:
    contacts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for contact in group:
            contact_type = contact.get("type", "").strip()
            value = contact.get("value", "").strip()
            url = contact.get("url", "").strip()
            if not contact_type or not value:
                continue
            key = (contact_type.casefold(), (url or value).casefold())
            if key in seen:
                continue
            seen.add(key)
            contacts.append(contact)
            if len(contacts) >= _MAX_CONTACTS:
                return tuple(contacts)
    return tuple(contacts)


def _contact_from_labeled_row(row: _ContactRow, *, base_url: str, source: str) -> dict[str, str] | None:
    label_key = _normalize_label(row.label)
    mapping = _CONTACT_LABELS.get(label_key)
    if mapping is None:
        return None
    contact_type, label = mapping
    href = urljoin(base_url, row.href) if row.href else None
    value = _normalize_contact_value(contact_type, row.value, href)
    if not value:
        return None
    url = _contact_url(contact_type=contact_type, value=value, href=href)
    return _company_contact(contact_type, label, value, url, source)


def _contact_from_anchor(anchor: _Anchor, *, base_url: str, source: str) -> dict[str, str] | None:
    href = urljoin(base_url, anchor.href)
    parsed = urlparse(href)
    scheme = parsed.scheme.casefold()
    host = parsed.netloc.casefold()
    text = anchor.text.strip()
    if scheme == "mailto":
        email = unquote(parsed.path).strip().lower()
        if _EMAIL_RE.fullmatch(email):
            return _company_contact("email", "Email", email, f"mailto:{email}", source)
    if scheme == "tel":
        phone = _normalize_spaces(parsed.path)
        phone_url = _tel_url(phone)
        if phone and phone_url is not None:
            return _company_contact("phone", "Phone", phone, phone_url, source)
    if _is_vk_host(host):
        value = _path_handle(parsed.path) or text
        if value:
            return _company_contact("vk", "VK", value, _canonical_url(href), source)
    if _is_telegram_host(host):
        value = _telegram_handle(parsed.path) or _clean_social_text(text)
        if value:
            return _company_contact("telegram", "Telegram", value, _canonical_url(href), source)
    if _is_youtube_host(host):
        value = _youtube_handle(parsed.path) or _clean_social_text(text) or "YouTube"
        return _company_contact("youtube", "YouTube", value, _canonical_url(href), source)
    if _is_dzen_host(host):
        value = _path_handle(parsed.path) or _clean_social_text(text)
        if value:
            return _company_contact("dzen", "Dzen", value, _canonical_url(href), source)
    return None


def _company_contact(
    contact_type: str,
    label: str,
    value: str,
    url: str | None,
    source: str,
) -> dict[str, str]:
    contact = {
        "type": contact_type,
        "label": label,
        "value": value,
        "source": source,
    }
    if url:
        contact["url"] = url
    return contact


def _contact_url(*, contact_type: str, value: str, href: str | None) -> str | None:
    if contact_type == "email":
        return f"mailto:{value}"
    if contact_type == "phone":
        return _tel_url(value)
    if href:
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https", "mailto", "tel"}:
            return _canonical_url(href) if parsed.scheme in {"http", "https"} else href
    return None


def _normalize_contact_value(contact_type: str, value: str, href: str | None) -> str:
    cleaned = _normalize_spaces(value)
    if contact_type == "email":
        match = _EMAIL_RE.search(cleaned)
        return match.group(0).lower() if match else ""
    if contact_type == "phone":
        return cleaned if _tel_url(cleaned) is not None else ""
    if contact_type == "telegram" and href:
        parsed = urlparse(href)
        if _is_telegram_host(parsed.netloc.casefold()):
            return cleaned or _telegram_handle(parsed.path)
    return cleaned


def _normalize_label(value: str) -> str:
    return value.replace(":", "").strip().casefold()


def _normalize_spaces(value: str) -> str:
    return " ".join(value.split())


def _tel_url(value: str) -> str | None:
    if len(re.sub(r"\D", "", value)) < _MIN_PHONE_DIGITS:
        return None
    compact = re.sub(r"[^\d+]", "", value)
    return f"tel:{compact}" if compact else None


def _path_handle(path: str) -> str:
    return path.strip("/").split("/", 1)[0]


def _telegram_handle(path: str) -> str:
    handle = _path_handle(path)
    if not handle:
        return ""
    return handle if handle.startswith("@") else f"@{handle}"


def _youtube_handle(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return ""
    if parts[0] in {"c", "channel", "user"} and len(parts) > 1:
        return parts[1]
    return parts[0]


def _clean_social_text(text: str) -> str:
    cleaned = _normalize_spaces(text)
    if not cleaned or len(cleaned) > _MAX_SOCIAL_TEXT_LENGTH or any(marker in cleaned for marker in "{};"):
        return ""
    return cleaned


def _is_vk_host(host: str) -> bool:
    return host == "vk.com" or host.endswith(".vk.com")


def _is_telegram_host(host: str) -> bool:
    return host in {"t.me", "telegram.me"} or host.endswith(".t.me") or host.endswith(".telegram.me")


def _is_youtube_host(host: str) -> bool:
    return host in {"youtube.com", "www.youtube.com", "youtu.be"} or host.endswith(".youtube.com")


def _is_dzen_host(host: str) -> bool:
    return host == "dzen.ru" or host.endswith(".dzen.ru")


def _anchors_from_html(html: str) -> tuple[_Anchor, ...]:
    collector = _AnchorCollector()
    collector.feed(html)
    return tuple(collector.anchors)


def _visible_text(html: str) -> str:
    collector = _VisibleTextCollector()
    collector.feed(html)
    return collector.text()


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url
    path = parsed.path or "/"
    return parsed._replace(path=path, fragment="").geturl()


def _same_document(left: str, right: str) -> bool:
    left_parsed = urlparse(_canonical_url(left))
    right_parsed = urlparse(_canonical_url(right))
    return (
        left_parsed.scheme,
        left_parsed.netloc,
        left_parsed.path.rstrip("/") or "/",
        left_parsed.query,
    ) == (
        right_parsed.scheme,
        right_parsed.netloc,
        right_parsed.path.rstrip("/") or "/",
        right_parsed.query,
    )
