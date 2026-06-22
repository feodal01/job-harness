---
name: job-search
description: Search for job listings across aggregators and employer career pages using the v2 CLI, and present curated results
---

# Job Search

Delegate this request to the plugin's `job-searcher` agent. The canonical
workflow lives in the plugin runtime skill `job-search-workflow` (v2 /
`job-harness-v2` CLI).

Pass the user's request and any provided constraints exactly as given. The agent
handles briefing, `list-sources`, v2 search, append, filtering, presentation,
and saved artifacts under `.job-harness/v2/runs/`.

If agent dispatch is unavailable in the host, activate `job-search-workflow` or
follow `skills/job-search-workflow/SKILL.md` manually.
