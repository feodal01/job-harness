"""Async fake Playwright Browser/Context/Page for BrowserPool tests.

Mimics just enough of the async Playwright surface for BrowserPool to
exercise:
  • Browser.new_context, is_connected, close
  • BrowserContext.new_page, close
  • Page.goto, title, url, locator, content, close
  • Page locator with `.count()` for the anti-bot probe

Injects controllable failures:
  • hang_seconds — goto sleeps for this many seconds before returning
  • goto_raises — Exception class to raise from goto
  • close_raises — Exception class to raise from close
  • title / url — overrideable to simulate anti-bot detection
  • iframes — list of src attributes to simulate captcha iframes
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class FakeBrowserError(Exception):
    """Raised by fake Playwright operations when configured to fail."""


@dataclass
class FakeFrameLocator:
    selector: str
    matches: int = 0

    async def count(self) -> int:
        return self.matches


@dataclass
class FakePage:
    behaviour: PageBehaviour = field(default_factory=lambda: PageBehaviour())
    _closed: bool = False
    _ops: list[str] = field(default_factory=list)

    async def goto(self, url: str, **_kwargs) -> None:
        self._ops.append(f"goto:{url}")
        self.behaviour.url = url
        if self.behaviour.hang_seconds > 0:
            await asyncio.sleep(self.behaviour.hang_seconds)
        if self.behaviour.goto_raises is not None:
            raise self.behaviour.goto_raises

    async def title(self) -> str:
        return self.behaviour.title

    @property
    def url(self) -> str:
        return self.behaviour.url

    def locator(self, selector: str) -> FakeFrameLocator:
        matches = self.behaviour.iframes.count(selector) if selector in self.behaviour.iframes else 0
        # Allow callers to pass either the exact iframe src or a CSS shape.
        for entry in self.behaviour.iframes:
            if selector == entry:
                matches = max(matches, 1)
        return FakeFrameLocator(selector=selector, matches=matches)

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
class PageBehaviour:
    title: str = "Job listings"
    url: str = "https://example.test/"
    content: str = "<html><body>jobs</body></html>"
    hang_seconds: float = 0.0
    close_hang_seconds: float = 0.0
    goto_raises: Exception | None = None
    close_raises: Exception | None = None
    iframes: list[str] = field(default_factory=list)


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
    # Counters for tests.
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
        """Simulate browser crash."""
        self._connected = False

    async def close(self) -> None:
        self._closed = True
        self._connected = False

    @property
    def closed(self) -> bool:
        return self._closed
