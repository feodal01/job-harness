"""Stealth browser factory using rebrowser-playwright."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

from job_harness.v1.rebrowser_stderr import install_rebrowser_stderr_filter

if TYPE_CHECKING:
    from rebrowser_playwright.sync_api import Browser, BrowserContext


def configure_playwright_tmpdir(path: Path | None = None) -> Path:
    """Point Playwright artifact temp files at a user-writable directory."""
    tmpdir = Path(
        os.environ.get("JOB_HARNESS_TMPDIR")
        or path
        or Path.home() / ".cache" / "job-harness" / "tmp"
    )
    tmpdir.mkdir(parents=True, exist_ok=True)
    tmpdir = tmpdir.resolve()
    os.environ["TMPDIR"] = str(tmpdir)
    os.environ["TEMP"] = str(tmpdir)
    os.environ["TMP"] = str(tmpdir)
    return tmpdir


def create_browser(
    pw,
    headless: bool = True,
) -> tuple[Browser, BrowserContext]:
    install_rebrowser_stderr_filter()
    browser = pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        java_script_enabled=True,
        bypass_csp=True,
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        delete navigator.__proto__.webdriver;
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
    """)
    return browser, context


async def create_browser_async(pw, headless: bool = True):
    install_rebrowser_stderr_filter()
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        java_script_enabled=True,
        bypass_csp=True,
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        delete navigator.__proto__.webdriver;
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
    """)
    return browser, context
