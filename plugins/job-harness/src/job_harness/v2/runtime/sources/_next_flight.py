"""Helpers for Next.js flight payloads embedded in HTML responses."""

from __future__ import annotations

import json

from job_harness.v2.runtime.sources._html import ScriptCollector, html_to_text

_NEXT_FLIGHT_PREFIX = "self.__next_f.push("
_NEXT_FLIGHT_PAYLOAD_INDEX = 1
_NEXT_FLIGHT_MIN_LENGTH = 2


def next_flight_payloads(body: str) -> tuple[str, ...]:
    collector = ScriptCollector()
    collector.feed(body)
    payloads: list[str] = []
    for _attrs, text in collector.scripts:
        if not text.startswith(_NEXT_FLIGHT_PREFIX):
            continue
        inner = text[len(_NEXT_FLIGHT_PREFIX) :].strip()
        if inner.endswith(";"):
            inner = inner[:-1].strip()
        if inner.endswith(")"):
            inner = inner[:-1].strip()
        value = json.loads(inner)
        if (
            isinstance(value, list)
            and len(value) >= _NEXT_FLIGHT_MIN_LENGTH
            and isinstance(value[_NEXT_FLIGHT_PAYLOAD_INDEX], str)
        ):
            payloads.append(value[_NEXT_FLIGHT_PAYLOAD_INDEX])
    return tuple(payloads)


def longest_html_description_from_payloads(payloads: tuple[str, ...]) -> str | None:
    decoder = json.JSONDecoder()
    best: str | None = None
    marker = '"description":'
    for payload in payloads:
        start = 0
        while True:
            index = payload.find(marker, start)
            if index == -1:
                break
            try:
                value, _end = decoder.raw_decode(payload[index + len(marker) :])
            except json.JSONDecodeError:
                start = index + len(marker)
                continue
            if isinstance(value, str) and value.startswith("<"):
                text = html_to_text(value)
                if text and (best is None or len(text) > len(best)):
                    best = text
            start = index + len(marker)
    return best
