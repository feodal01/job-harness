---
name: job-resolve
description: Resolve aggregator job listings to direct employer career pages
---

# Job Resolve

Resolve job listings from aggregators to the employer's direct career page.

**Input**: Either a JSON file with search results, or company names/URLs.

**Workflow**:

1. If the user provides a file path, read it and extract listings.
2. Use the `resolve` MCP tool to resolve each listing. Use `cache=true` to leverage the crowdsourced cache.
3. Present results as a table: Company | Aggregator URL | Career Page | Direct Vacancy | ATS Type
4. Save resolved results alongside the original search results.

**Tips**:
- Use the `employer-resolution` skill for detailed resolution strategy.
- Not finding a career page is normal for small companies — don't present it as failure.
- Prefer direct vacancy URLs over career page URLs over aggregator URLs.
