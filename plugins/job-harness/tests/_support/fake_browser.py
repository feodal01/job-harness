"""Async fake Playwright Browser/Context/Page for BrowserPool tests.

Mimics enough of the async Playwright surface for two use cases:

  1. BrowserPool unit tests — needs Browser.new_context, Context.new_page,
     Page.goto/title/url/close, Page.locator(...).count() for the
     anti-bot probe, controllable hang/error injection.

  2. Scraper tests (e.g. hh_ru) — needs a fake DOM. PageBehaviour holds
     a `dom` mapping from selector string to a list of FakeElement
     objects, each with `text`, `attrs`, and visibility/click hooks.

Both use cases share the same FakePage class; PageBehaviour drives
which features each test exercises.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class FakeBrowserError(Exception):
    """Raised by fake Playwright operations when configured to fail."""


# ---------------------------------------------------------------------------
# DOM-style fakes for scraper tests
# ---------------------------------------------------------------------------


@dataclass
class FakeElement:
    """One DOM-like element behind a selector."""

    text: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    visible: bool = True
    on_click: Callable[[], None] | None = None
    # Nested selectors: locator(...).locator(...) — maps selector to elements.
    children: dict[str, list[FakeElement]] = field(default_factory=dict)


@dataclass
class FakeLocator:
    """Async Playwright Locator stand-in.

    Backed by a `_elements: list[FakeElement]` slice and a `_selector`
    label so chained `.locator(...)` calls can scope into the children
    of the first matching element.
    """

    _elements: list[FakeElement]
    _selector: str = ""

    async def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> FakeLocator:
        if 0 <= index < len(self._elements):
            return FakeLocator(_elements=[self._elements[index]], _selector=self._selector)
        return FakeLocator(_elements=[], _selector=self._selector)

    @property
    def first(self) -> FakeLocator:
        return self.nth(0)

    async def inner_text(self) -> str:
        if not self._elements:
            raise FakeBrowserError(f"no element for {self._selector!r}")
        return self._elements[0].text

    async def get_attribute(self, name: str) -> str | None:
        if not self._elements:
            return None
        return self._elements[0].attrs.get(name)

    async def is_visible(self) -> bool:
        return bool(self._elements) and self._elements[0].visible

    async def click(self) -> None:
        if not self._elements:
            raise FakeBrowserError(f"cannot click missing {self._selector!r}")
        cb = self._elements[0].on_click
        if cb is not None:
            cb()

    def locator(self, selector: str) -> FakeLocator:
        # Scope into the first element's children mapping.
        if not self._elements:
            return FakeLocator(_elements=[], _selector=selector)
        children = self._elements[0].children.get(selector, [])
        return FakeLocator(_elements=list(children), _selector=selector)


# ---------------------------------------------------------------------------
# PageBehaviour — what the page does on goto/title/url/locator calls
# ---------------------------------------------------------------------------


@dataclass
class PageBehaviour:
    title: str = "Job listings"
    url: str = "https://example.test/"
    content: str = "<html><body>jobs</body></html>"
    hang_seconds: float = 0.0
    close_hang_seconds: float = 0.0
    goto_raises: Exception | None = None
    close_raises: Exception | None = None
    # Anti-bot probe: list of selectors that "exist" (count() > 0).
    iframes: list[str] = field(default_factory=list)
    # DOM contents: selector → list of elements. Hh.ru tests populate
    # this; pool tests leave it empty.
    dom: dict[str, list[FakeElement]] = field(default_factory=dict)
    # Optional hook fired before navigating, useful for pagination
    # tests that need to swap the DOM between page loads.
    on_goto: Callable[[str], None] | None = None


@dataclass
class FakePage:
    behaviour: PageBehaviour = field(default_factory=lambda: PageBehaviour())
    _closed: bool = False
    _ops: list[str] = field(default_factory=list)

    async def goto(self, url: str, **_kwargs) -> None:
        self._ops.append(f"goto:{url}")
        self.behaviour.url = url
        if self.behaviour.on_goto is not None:
            self.behaviour.on_goto(url)
        if self.behaviour.hang_seconds > 0:
            await asyncio.sleep(self.behaviour.hang_seconds)
        if self.behaviour.goto_raises is not None:
            raise self.behaviour.goto_raises

    async def title(self) -> str:
        return self.behaviour.title

    @property
    def url(self) -> str:
        return self.behaviour.url

    async def wait_for_timeout(self, ms: int) -> None:
        # Real pages would sleep ms milliseconds; fake just records.
        self._ops.append(f"wait_for_timeout:{ms}")

    def locator(self, selector: str) -> FakeLocator:
        # Anti-bot iframe probes go through this same locator API; if a
        # selector is in `iframes` the locator pretends to find one match.
        if selector in self.behaviour.iframes:
            return FakeLocator(_elements=[FakeElement(text="", attrs={"src": selector})], _selector=selector)
        elements = self.behaviour.dom.get(selector, [])
        return FakeLocator(_elements=list(elements), _selector=selector)

    async def content(self) -> str:
        return self.behaviour.content

    async def close(self, timeout: float | None = None) -> None:
        if self.behaviour.close_raises is not None:
            raise self.behaviour.close_raises
        if self.behaviour.close_hang_seconds > 0:
            await asyncio.sleep(self.behaviour.close_hang_seconds)
        self._closed = True

    def set_default_timeout(self, ms: int) -> None:
        self._ops.append(f"set_default_timeout:{ms}")


@dataclass
class FakeContext:
    pages: list[FakePage] = field(default_factory=list)
    page_factory: Any = None
    closed: bool = False
    accept_downloads: bool = False

    async def new_page(self) -> FakePage:
        page = self.page_factory() if self.page_factory else FakePage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeBrowser:
    """Records create/close stats for assertions."""

    context_factory: Any = None
    _contexts: list[FakeContext] = field(default_factory=list)
    _connected: bool = True
    _closed: bool = False
    new_context_calls: int = 0

    async def new_context(self, **kwargs) -> FakeContext:
        self.new_context_calls += 1
        if self.context_factory is not None:
            ctx = self.context_factory(**kwargs)
        else:
            ctx = FakeContext(accept_downloads=kwargs.get("accept_downloads", False))
        self._contexts.append(ctx)
        return ctx

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    async def close(self) -> None:
        self._closed = True
        self._connected = False

    @property
    def closed(self) -> bool:
        return self._closed


# ---------------------------------------------------------------------------
# DOM builder helpers — used by hh.ru tests to declare fake cards
# ---------------------------------------------------------------------------


def card_dom(*cards: dict[str, Any]) -> dict[str, list[FakeElement]]:
    """Convert a sequence of declarative card dicts into the `dom`
    mapping FakePage expects.

    Each card dict may contain:
      title, link_href, company, salary, experience_raw, remote (bool)

    The cards are exposed under the hh.ru data-qa card selector, with
    per-card children keyed by the selectors hh_ru.py uses.
    """
    from job_harness.scrapers.hh_ru import (
        _CARD_SELECTOR,
        _COMPANY_PRIMARY,
        _EXPERIENCE_SELECTOR,
        _LINK_PRIMARY,
        _REMOTE_LABEL,
        _SALARY_SELECTOR,
        _TITLE_PRIMARY,
    )

    elements: list[FakeElement] = []
    for card in cards:
        children: dict[str, list[FakeElement]] = {}
        if title := card.get("title"):
            children[_TITLE_PRIMARY] = [FakeElement(text=title)]
        if href := card.get("link_href"):
            children[_LINK_PRIMARY] = [FakeElement(text=card.get("title", ""), attrs={"href": href})]
        if company := card.get("company"):
            children[_COMPANY_PRIMARY] = [FakeElement(text=company)]
        if salary := card.get("salary"):
            children[_SALARY_SELECTOR] = [FakeElement(text=salary)]
        if exp := card.get("experience_raw"):
            children[_EXPERIENCE_SELECTOR] = [FakeElement(text=exp)]
        if card.get("remote"):
            children[_REMOTE_LABEL] = [FakeElement(text="remote")]
        elements.append(FakeElement(text="", children=children))

    return {_CARD_SELECTOR: elements}
