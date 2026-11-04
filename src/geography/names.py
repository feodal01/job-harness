"""Split a place label into a name and its parenthesized qualifier, without guessing places."""

import re


def split_city(value: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", value.strip())
    return (match[1].strip(), match[2].strip()) if match else (value.strip(), None)
