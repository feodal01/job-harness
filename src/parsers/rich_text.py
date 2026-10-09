"""Очищенная HTML-разметка для показа и обычный текст с той же структурой для отбора."""

import re
from dataclasses import dataclass

import nh3
from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import PageElement

_TAGS = {
    "p",
    "div",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "del",
    "a",
    "blockquote",
    "pre",
    "code",
    "sup",
    "sub",
    "dl",
    "dt",
    "dd",
    "table",
    "caption",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
}
_BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "dt", "dd", "caption"}
_ATTRIBUTES = {
    "a": {"href", "title"},
    "ol": {"start", "reversed"},
    "li": {"value"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


@dataclass(frozen=True)
class RichText:
    text: str | None
    html: str | None


def _attribute(tag: str, name: str, value: str) -> str | None:
    if name in {"start", "value", "colspan", "rowspan"}:
        if not re.fullmatch(r"-?\d{1,6}", value):
            return None
        number = int(value)
        if name in {"colspan", "rowspan"} and not 1 <= number <= 100:
            return None
        return str(number)
    return value


def parse_rich_text(markup: str, *, base_url: str) -> RichText:
    """Сохраняет структуру описания; стили, активные элементы и изображения не допускаются."""
    cleaned = nh3.clean(
        markup,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        attribute_filter=_attribute,
        clean_content_tags={"script", "style", "iframe", "object", "embed", "svg", "math", "template"},
        url_schemes={"http", "https", "mailto"},
        url_relative=("rewrite_with_base", base_url),
        link_rel="noopener noreferrer",
    ).strip()
    soup = BeautifulSoup(cleaned, "html.parser")
    text = "".join(_text(node) for node in soup.children)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return RichText(text or None, cleaned if text else None)


def _text(node: PageElement, depth: int = 0) -> str:
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    if node.name == "br":
        return "\n"
    if node.name == "hr":
        return "\n\n"
    if node.name == "pre":
        return "\n\n" + node.get_text().strip("\n") + "\n\n"
    if node.name in {"ul", "ol"}:
        items = node.find_all("li", recursive=False)
        reversed_list = node.has_attr("reversed")
        number = int(str(node.get("start", str(len(items) if reversed_list else 1))))
        lines = []
        for item in items:
            if item.has_attr("value"):
                number = int(str(item["value"]))
            marker = f"{number}. " if node.name == "ol" else "• "
            body = "".join(_text(child, depth + 1) for child in item.children).strip()
            lines.append("  " * depth + marker + body)
            number += -1 if reversed_list else 1
        return "\n" + "\n".join(lines) + "\n"
    if node.name == "tr":
        cells = [
            "".join(_text(child, depth) for child in cell.children).strip()
            for cell in node.find_all(["td", "th"], recursive=False)
        ]
        return "\n" + " | ".join(cells) + "\n"
    content = "".join(_text(child, depth) for child in node.children)
    return "\n\n" + content.strip() + "\n\n" if node.name in _BLOCKS else content
