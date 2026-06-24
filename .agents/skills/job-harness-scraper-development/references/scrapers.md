# Experience: Scrapers

Generalized, reusable insights from building and fixing scrapers. Each entry must describe a **principle** that applies beyond the specific site or situation where it was learned.

## Rule for adding entries

When you solve a non-trivial problem, do NOT just record what happened. **Generalize it:**

1. What is the underlying pattern or anti-pattern?
2. When will this pattern appear again — on what other sites or in what other contexts?
3. What is the universal strategy to handle it?

If an entry only makes sense in the context of one specific site's current DOM, it belongs in the scraper's code comments — not here. This file is for transferable knowledge.

Format:

```
### [topic]
**Pattern:** the generalized situation
**Strategy:** the universal approach
**Origin:** brief note on where this was learned (optional, for context)
```

---

### Selector drift

**Pattern:** Websites change layouts without notice. A selector that works today returns 0 elements tomorrow, and the scraper can silently produce empty results.

**Strategy:** Keep one current, fixture-backed parsing contract per source shape. When a site changes, capture or minimize the real new artifact, update the parser to the new contract, and remove obsolete selector branches in the same patch.

**Origin:** hh.ru changed vacancy card selectors twice.

### Dynamic attribute suffixes

**Pattern:** Platforms append dynamic identifiers to otherwise stable `data-qa` or `data-*` attributes. Exact attribute matches fail even though the element exists.

**Strategy:** When you know an attribute should exist but exact match fails, try CSS starts-with (`[data-qa^="prefix"]`) or contains (`[data-qa*="substring"]`). This is especially common on platforms that use component-based frameworks where attributes are generated dynamically.

**Origin:** hh.ru appends dynamic suffixes to work-experience data-qa attributes.

### Over-broad selectors

**Pattern:** A class name that sounds specific (e.g., `.card__salary`) actually matches multiple elements or captures adjacent content, producing dirty data.

**Strategy:** When a field returns garbage or mixed content, look for a more specific structural element within the same subtree — one that isolates exactly the data you need. The element closest to the actual text content is usually the right one.

**Origin:** Habr Career `.vacancy-card__salary` captured forecast text alongside salary.

### Text-based detection fragility

**Pattern:** Detecting boolean features (remote, type, etc.) by matching exact text strings is fragile. Text labels change, get localized, use synonyms, or vary across pages.

**Strategy:** Prefer structural selectors (`data-qa`, ARIA roles, dedicated CSS classes) over text content. When no structural selector exists, enumerate all known text variants rather than matching one. Accept that text-based detection will always be incomplete and plan for it.

**Origin:** Remote detection on Habr Career uses multiple Russian phrases; hh.ru has a dedicated data-qa attribute for this.

### URL tracking params

**Pattern:** Job platforms append tracking/analytics query parameters to vacancy URLs. These bloat the URL and may break deduplication.

**Strategy:** Always strip query params from vacancy URLs during parsing (`.split("?")[0]`). Keep URLs clean — tracking params add no value for downstream use.

**Origin:** hh.ru appends tracking params to every vacancy link.

### Anti-bot detection signals

**Pattern:** Some platforms detect automation and serve CAPTCHAs or block pages instead of results. The scraper doesn't crash — it just gets empty or wrong content.

**Strategy:** Check shared access signals such as final URL, status, title, body markers, and captcha iframes. Classify the source as blocked through the runner or shared detector. Do not silently treat blocked pages as `no_results` or parser success.

**Origin:** hh.ru occasionally serves CAPTCHAs to automated browsers.

### Anti-abuse redirects with empty titles

**Pattern:** Anti-bot systems may redirect browser traffic to an abuse-check URL or return an HTTP block status while rendering a page with an empty title and no result cards. The scraper sees a normal navigation completion and can silently return zero results.

**Strategy:** Inspect navigation response status, final URL path, and compact body markers in addition to title and captcha iframes. Treat known block statuses such as 403/451 and abuse-check paths as blocked source states, not successful empty searches.

**Origin:** hh.ru redirected headless search traffic to `/vpncheeck` with zero vacancy cards while direct HTTP showed 451/403-like anti-abuse markers.

### Google search result URL extraction

**Pattern:** Google search results link `href` attributes are either redirect URLs (`/url?q=ACTUAL_URL&...`) or relative paths (`/search?q=...`). Using them directly causes navigation errors or loops back to Google.

**Strategy:** When extracting URLs from Google results: (1) if `/url?q=` is in href, extract the `q` parameter value; (2) skip any URL that doesn't start with `http://` or `https://` — relative paths are Google-internal; (3) filter out known non-employer domains (linkedin.com, facebook.com, etc.) that Google frequently surfaces.

**Origin:** employer_resolver.py found relative Google URLs being passed to `page.goto()`, causing "Cannot navigate to invalid URL" errors.

### Russian company career page prevalence

**Pattern:** Russian tech companies heavily depend on hh.ru as their primary (often sole) recruitment channel. Career page availability correlates strongly with company size:
- Large (1000+ employees): usually have career sites, but many use client-side rendering
- Mid-size (100-1000): may have a career page that just redirects to hh.ru
- Small/startups: almost never have career pages

**Strategy:** Don't treat "no career page found" as a resolver failure — it's market reality. Present aggregator URLs as valid results. Focus resolution effort on companies likely to have career pages (check hh.ru employer profile for size). Cache results to avoid re-resolving.

**Origin:** Field test resolving 15 QA listings — only 3-4 companies had meaningful career pages.

### Bitrix SEF filter URLs

**Pattern:** Bitrix CMS career sites use AJAX-powered checkbox filters. Clicking checkboxes through Playwright sets the DOM state but doesn't trigger the Bitrix AJAX reload — the vacancy list stays unchanged. The `BX` global object is inaccessible from Playwright's `page.evaluate()` because rebrowser-playwright runs in an isolated world.

**Strategy:** Don't fight the JavaScript. Bitrix Smart Filter generates SEF (Search Engine Friendly) URLs like `/vacancies/filter/property-is-value/apply/`. Construct these URLs directly from query keywords → URL_ID mappings. Each filter property (direction, format, city) is a separate slash-separated path segment. The AJAX response (POST with `ajax=y`) also contains `SEF_SET_FILTER_URL` and all `URL_ID` values for building these URLs programmatically.

**Origin:** IBS (ibs.ru) — checkbox clicks had no effect; SEF URL `/career/vacancies/filter/napravlenie-is-testirovanie/apply/` returns pre-filtered results server-side.

### Next.js SSR JSON extraction

**Pattern:** Next.js apps embed structured page data in `<script id="__NEXT_DATA__">` as JSON. This data is far more complete and reliable than DOM scraping — it includes fields not rendered on the page (IDs, nested objects, tags).

**Strategy:** Always check for `__NEXT_DATA__` before falling back to DOM selectors. Extract with `document.getElementById('__NEXT_DATA__').textContent` and parse as JSON. Navigate to the vacancy list in the parsed structure (usually `props.pageProps`). Also check for server-side filter parameters in the URL (e.g., `?specialty=284`) — these return pre-filtered SSR data.

**Origin:** VK (team.vk.company) — DOM had 25 unfiltered items; `__NEXT_DATA__` had 12 QA-filtered items with full structured data including tags, work format, and group name.

### Isolated world vs page JS context

**Pattern:** `rebrowser-playwright`'s `page.evaluate()` runs in an isolated JavaScript world that cannot access page-defined globals like `BX`, `jQuery`, `$`, or custom functions like `submitJobsFilter()`. Calling these from evaluate throws `ReferenceError`.

**Strategy:** When you need to trigger page JS, don't call it from `evaluate()`. Instead: (1) construct the equivalent HTTP request yourself (using `context.request.post()` or URL parameters), (2) use DOM manipulation that triggers native browser events which page JS listens for, or (3) use `page.dispatchEvent()` with proper event construction. For API calls, `context.request` runs in the browser context with cookies/session.

**Origin:** IBS filter — `submitJobsFilter()` not defined in evaluate scope; VK — `BX` object not accessible.

### Search input visibility before fill

**Pattern:** Many career sites have search inputs that exist in the DOM but are not visible (hidden behind tabs, in mobile nav, or in modal overlays). Calling `fill()` on a non-visible element throws a timeout error.

**Strategy:** Before filling a search input, check `is_visible()`. If the input isn't visible, skip the search and fall back to scanning all links on the page for matching vacancies.

**Origin:** employer_resolver.py tried to fill LinkedIn and Монетка search inputs that existed in DOM but were hidden.

### Country-aware source metadata

**Pattern:** Regional job search works poorly when every scraper is treated as globally relevant. The agent wastes calls on country-mismatched sources and may present irrelevant remote/global listings as local results.

**Strategy:** Give every scraper an explicit `countries` tuple with normalized country codes. Filter `sources=all` by country before instantiating scrapers. For sources that cover many countries but cannot server-filter by country, expose the countries as source metadata but keep listing-level `country` empty unless the platform provides a concrete location.

**Origin:** CIS expansion added RU-only sources, Armenia/Uzbekistan-specific sources, regional HH hosts, and multi-country remote aggregators.

### Public API before browser scraping

**Pattern:** Modern job boards often render with Next/Nuxt/Vue, but the page exposes a public JSON endpoint or SSR payload. Browser scraping those pages is slower and more fragile than using the underlying data contract.

**Strategy:** Probe for JSON in this order: `__NEXT_DATA__`, Nuxt config API base, JSON-LD `ItemList`, and public `/api/...` endpoints visible in chunks or network-like URL patterns. Add a scraper only after a stable endpoint or structured payload is confirmed. Leave auth-only or timeout-heavy sources in backlog instead of shipping a fake scraper.

**Origin:** Hirify (`api.hirify.me/api/vacancies`), Finder.work (`api.finder.work/api/v1/vacancies`), IT-Jobs.uz (`/api/jobs`), Staff.am (`__NEXT_DATA__`), JobTurbo (JSON-LD `ItemList`), and Getmatch (`/api/offers` found through browser network capture).

### Specialization APIs beat keyword guessing

**Pattern:** Some job boards ignore free-text query parameters on vacancy APIs but expose separate taxonomy endpoints for roles, specializations, or categories. Guessing `q=...`/`search=...` silently returns a generic feed.

**Strategy:** If a keyword parameter is ignored, inspect browser network traffic for taxonomy endpoints. Match the user's query against specialization/category names and slugs, then pass the platform's native category parameter. Add tests for multiple IT roles, not only the role used in smoke testing.

**Origin:** Getmatch ignores `q/search/query` on `/api/offers`; the working route is `/api/specializations` plus `/api/offers?sp=<specialization_slug>`.
