---
name: job-search
description: Search for job listings across aggregators, resolve to employer career pages, and present curated results
---

# Job Search

Delegate this request to the plugin's `job-searcher` agent. The canonical
workflow lives in the plugin runtime skill `job-search-workflow`.

Pass the user's request and any provided constraints exactly as given. The agent
handles briefing, artifact layout, MCP search, retry, employer-page expansion,
filtering, presentation, and saved outputs.

If agent dispatch is unavailable in the host, activate `job-search-workflow` or
follow `skills/job-search-workflow/SKILL.md` manually.
