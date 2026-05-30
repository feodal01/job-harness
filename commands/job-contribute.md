---
name: job-contribute
description: Contribute new employer cache entries back to the crowdsourced public cache via PR
---

# Contribute Cache Entries

Help grow the crowdsourced employer career page database.

**Workflow**:

1. Use the `cache_diff` MCP tool to see which local cache entries are not yet in the public cache.
2. Review the diff — only entries with `careers_url` are included in the public cache (entries with no career page are local-only by design).
3. Validate entries: check that `careers_url` values are still live and point to actual career pages.
4. Present the new entries to the user for review.
5. If the user approves, update `data/company-careers-public.json` with the new entries.
6. Create a PR:
   - Branch name: `cache/contrib-YYYY-MM-DD`
   - Title: "Add N new employer cache entries"
   - Body: list the companies and URLs being added
   - Commit only `data/company-careers-public.json`

**Important**: Only add entries where `careers_url` is not null and `ats_type` is not "unknown". These are the high-value entries that help other users.
