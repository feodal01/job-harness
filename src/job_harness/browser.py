"""Stealth browser factory using rebrowser-playwright."""

from __future__ import annotations

from rebrowser_playwright.sync_api import Browser, BrowserContext, sync_playwright


def create_browser(
    pw,
    headless: bool = True,
) -> tuple[Browser, BrowserContext]:
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
