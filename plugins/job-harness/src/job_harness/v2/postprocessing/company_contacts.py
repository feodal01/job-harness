"""Company contact extraction for processed result rows."""

from __future__ import annotations


def company_contacts(listing: dict[str, object]) -> tuple[dict[str, str], ...]:
    raw = listing.get("raw")
    if not isinstance(raw, dict):
        return ()
    return _raw_company_contacts(raw.get("company_contacts"))


def _raw_company_contacts(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    contacts: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        contact_type = _optional_text(item.get("type"))
        label = _optional_text(item.get("label"))
        contact_value = _optional_text(item.get("value"))
        if not contact_type or not label or not contact_value:
            continue
        contact = {
            "type": contact_type,
            "label": label,
            "value": contact_value,
        }
        for key in ("url", "source"):
            value_text = _optional_text(item.get(key))
            if value_text:
                contact[key] = value_text
        contacts.append(contact)
    return tuple(contacts)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None
