# Experience: Scrapers

Reusable insights from building and fixing scrapers. Add entries when you solve a non-trivial problem — whether independently or with human help.

Format: `### [date] brief description` followed by what happened, what was tried, and the takeaway.

---

### 2026-05-23 hh.ru returning 0 results after layout change

**Problem:** hh.ru updated their search results layout. Old selectors (`data-qa="vacancy-serp__vacancy-title"`) no longer matched anything, returning 0 listings.

**Fix:** Discovered new selectors (`data-qa="serp-item__title-text"`) by inspecting the actual DOM. Added fallback pattern: try new selector first, fall back to old one.

**Takeaway:** Always code scrapers with fallback selectors. Aggregator sites change layouts without notice. The pattern `if el.count() == 0: el = fallback_selector` is standard practice now.

### 2026-05-23 hh.ru experience attribute has dynamic suffix

**Problem:** `data-qa="vacancy-serp__vacancy-work-experience"` exact match failed because hh.ru appends a dynamic suffix to the attribute.

**Fix:** Use CSS starts-with selector: `data-qa^="vacancy-serp__vacancy-work-experience"`.

**Takeaway:** When a `data-qa` selector fails on an attribute you know should exist, try starts-with (`^=`) — the platform may append dynamic identifiers.

### 2026-05-23 Habr Career salary capturing extra text

**Problem:** `.vacancy-card__salary` selector captured salary forecast text in addition to the actual salary.

**Fix:** Switched to `.basic-salary` which contains only the salary value.

**Takeaway:** Generic class names (`.vacancy-card__salary`) may match more elements than intended. When a field captures garbage, look for a more specific structural element that isolates just the data you need.

### 2026-05-23 Remote detection via text content is fragile

**Problem:** Checking for exact Russian text like "Удаленка" or "Можно из дома" missed the actual variants used on the page.

**Fix:** For hh.ru, use `data-qa="vacancy-label-work-schedule-remote"`. For Habr, check both known variants: `text="Можно удалённо"` and `text="Можно из дома"`.

**Takeaway:** Prefer `data-qa` attributes or structural selectors over text content matching. Text labels change, get localized, or use synonyms. When text matching is unavoidable, check all known variants.
