"""Application channel extraction for processed result rows."""

from __future__ import annotations


def application_channels(listing: dict[str, object]) -> tuple[dict[str, str], ...]:
    raw = listing.get("raw")
    if not isinstance(raw, dict):
        return ()

    return _raw_application_channels(raw.get("application_channels"))


def _raw_application_channels(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    channels: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _optional_text(item.get("url"))
        label = _optional_text(item.get("label"))
        channel_type = _optional_text(item.get("type"))
        if not url or not label or not channel_type:
            continue
        channel = {
            "type": channel_type,
            "label": label,
            "url": url,
        }
        for key in ("status", "source"):
            value_text = _optional_text(item.get(key))
            if value_text:
                channel[key] = value_text
        channels.append(channel)
    return tuple(channels)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None
