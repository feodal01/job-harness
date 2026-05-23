# Protocol: Aggregator Scrapers

## Location & Registry

Scrapers live in `src/job_harness/scrapers/`. Each is a subclass of `BaseScraper` decorated with `@register_scraper("name")`. The registry in `src/job_harness/registry.py` auto-discovers them — no other file needs changes when adding a new scraper.

Current scrapers:
- `hh_ru.py` — hh.ru (`hh_ru`)
- `habr_career.py` — Habr Career (`habr_career`)

## Maintenance Rule

Websites evolve. Selectors break. New UI layouts appear. If you detect that a scraper is returning empty results, crashing, or missing data — **you must fix it**. This is not optional.

When fixing a scraper:
1. Run with `--no-headless --debug` to see the actual page and get screenshots
2. Inspect the current DOM to find correct selectors
3. Prefer `data-qa` attributes over CSS classes — they are more stable
4. When `data-qa` is not available, use the most structural selectors (IDs, ARIA roles, semantic elements) over brittle class names
5. Test with a small `--max-results` run before declaring it fixed

## Universality Rule

Every scraper must be a **universal tool**, not a one-off solution for a specific user request.

Do:
- Return all available fields the platform provides (title, salary, experience, remote, skills, etc.)
- Keep filtering logic out of scrapers — that's what `filters.py` is for
- Use `raw` dict for platform-specific data that doesn't map to universal `JobListing` fields
- Make selectors resilient: try new layout first, fall back to old layout

Don't:
- Hardcode keywords, job types, or domain-specific logic into a scraper
- Skip fields just because the current user request doesn't need them
- Add query-specific URL parameters or filters into `_build_search_url`

## Practical Notes from Building Current Scrapers

### hh.ru

- Layout changes frequently. Always try new selectors first, fall back to old ones:
  - Title: `data-qa="serp-item__title-text"` (new), fallback `data-qa="vacancy-serp__vacancy-title"`
  - Link: `data-qa="serp-item__title"` (new), fallback `data-qa="vacancy-serp__vacancy-title"`
  - Company: `data-qa="vacancy-serp__vacancy-employer-text"` (new), fallback `data-qa="vacancy-serp__vacancy-employer"`
- Experience selector uses starts-with: `data-qa^="vacancy-serp__vacancy-work-experience"` — the suffix varies, exact match fails
- Remote flag: `data-qa="vacancy-label-work-schedule-remote"` — not text-based
- URL cleanup: strip query params with `.split("?")[0]` — hh.ru appends tracking params
- Anti-bot detection: page title may contain "Доступ ограничен" or "подтвердите" — log a warning if detected
- Pagination: `data-qa="pager-next"`

### Habr Career

- Company selector: `.vacancy-card__company a` — the `<a>` tag is important, parent div has no text
- Salary: `.basic-salary` — NOT `.vacancy-card__salary` (that one captures forecast text too)
- Skills on card: `.vacancy-card__skills-chip .basic-chip__text`
- Skills on detail: `.skill__name`
- Remote detection: check for `text="Можно удалённо"` or `text="Можно из дома"` — both variants exist
- Experience: `.chip-with-icon__text` — then normalize via `BaseScraper.normalize_experience()`
- Pagination: `a[rel="next"], .with-pagination__side-button--next` — try both
- Description on detail: `.vacancy-description__content`
- Requirements on detail: `.vacancy-description__requirements`

### General

- Always `wait_until="domcontentloaded"` + `wait_for_timeout(2000)` after navigation — pages render dynamically
- Close pages in `finally` blocks to avoid browser memory leaks
- Wrap per-card parsing in try/except and `continue` — one broken card shouldn't kill the whole page
- Detail pages are fetched one at a time (sequential) — be mindful of rate limits
- `rebrowser-patches` warnings like `cannot get world` are non-critical — ignore them
- Salary strings are kept as-is (platform-native format) — don't try to parse them into numbers
