"""Small HTML extraction helpers for recorded source artifacts."""

from __future__ import annotations

from html.parser import HTMLParser


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[dict[str, str], str]] = []
        self._attrs: dict[str, str] | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        self._attrs = {key: value or "" for key, value in attrs}
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or self._attrs is None:
            return
        self.scripts.append((self._attrs, "".join(self._buffer).strip()))
        self._attrs = None
        self._buffer = []


class ClassTextCollector(HTMLParser):
    def __init__(self, *, tag_name: str, class_name: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._tag_name = tag_name
        self._class_name = class_name
        self._depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if self._depth is None:
            if tag == self._tag_name and self._class_name in classes:
                self._depth = 1
            return
        if tag not in _VOID_TAGS:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._depth is None:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._depth is None:
            return
        if tag not in _VOID_TAGS:
            self._depth -= 1
        if self._depth == 0:
            self._depth = None

    def text(self) -> str | None:
        if not self.parts:
            return None
        return "\n".join(self.parts)


_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
