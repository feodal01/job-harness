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

### Fallback selectors

**Pattern:** Websites change layouts without notice. A selector that works today returns 0 elements tomorrow, and the scraper silently produces empty results.

**Strategy:** Always code with fallback chains: try the current selector, fall back to the previous one. Pattern: `if el.count() == 0: el = fallback_selector`. When you discover a new layout, add it as the primary and keep the old one as fallback — don't replace.

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

**Strategy:** Check page title or key elements for known block signals (e.g., "Доступ ограничен", "verify you are human"). Log a warning when detected. Do not silently treat blocked pages as "no results."

**Origin:** hh.ru occasionally serves CAPTCHAs to automated browsers.
