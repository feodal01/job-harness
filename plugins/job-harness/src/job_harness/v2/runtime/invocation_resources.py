"""Pure resource-key derivation from scraper network actions."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from job_harness.v2.contracts import ParserInput, ParserRef, ParserRegistry
from job_harness.v2.ports import HttpAction


def invocation_resource_key(
    registry: ParserRegistry,
    parser_ref: ParserRef,
    parser_input: ParserInput,
    resource_key_for_host: Callable[[str], str],
) -> str | None:
    bundle = registry.get(parser_ref)
    build_action = getattr(bundle, "build_action", None)
    if not callable(build_action):
        return None
    action = build_action(parser_input)
    if not isinstance(action, HttpAction):
        raise TypeError("bundle build_action must return HttpAction")
    if action.resource_key is not None:
        if not action.resource_key.strip():
            raise ValueError("HttpAction.resource_key must be non-empty")
        return action.resource_key
    parsed = urlsplit(action.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HttpAction URL must be absolute HTTP(S)")
    resource_key = resource_key_for_host(parsed.hostname.casefold().rstrip("."))
    if not resource_key.strip():
        raise ValueError("resource key resolver returned an empty key")
    return resource_key
