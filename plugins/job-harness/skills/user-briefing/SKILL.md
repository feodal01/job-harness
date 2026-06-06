---
name: user-briefing
description: Activate when starting a job search session to collect user preferences and requirements
version: 1.0.0
---

# User Briefing

Every new job search workflow starts by choosing an artifact root and filling out or reusing a search brief. Do not skip questions. Do not assume answers. Ask the user directly.

## Artifact Root Confirmation

Before creating any files or folders, tell the user where artifacts will be saved:

```
I will save job-harness working files in:
<current-directory>/.job-harness/

This will create:
- briefs/ — confirmed reusable search briefs and their search runs
- companies/ — local memory of employer career pages for this project

Is this directory OK? If not, tell me which directory to use.
```

Do not create `.job-harness/` until the user confirms. If the user gives another directory, use `<chosen-directory>/.job-harness/` and confirm that path before continuing.

After confirmation, initialize the artifact root with the helper script when available:

```
sh "$PLUGIN_ROOT/scripts/init-artifacts.sh" "<chosen-directory>"
```

In Claude Code, use `CLAUDE_PLUGIN_ROOT` instead of `PLUGIN_ROOT` if that is the available plugin root environment variable. If neither environment variable is available, create the same base structure manually:

```
.job-harness/
  briefs/
  companies/
    careers.json
```

## Brief Template

Present these questions to the user. They can answer all at once or one by one — adapt to their style.

```
## Job Search Brief

### Position
- Job title: [what role are you looking for? e.g. "QA engineer", "product manager"]
- Alternative titles: [other names this role goes by? e.g. "тестировщик", "QA manual"]

### Experience
- Years of experience: [e.g. 3, 5+, no matter]
- Level: [junior / middle / senior / lead — or let the agent infer from years]

### Location & Relocation
- Country of residence: [where you live now]
- Target countries/cities: [where you want to work; use CIS country names or codes when possible so search can pass `country`]
- Relocation: [willing to relocate? if yes, to which countries?]
- Relocation requirement: [must have explicit relocation support in the vacancy, or any vacancy in target countries is fine?]

### Work format
- Format: [office / remote / hybrid]
- If remote: [remote within your country, or worldwide?]

### Compensation
- Salary expectations: [range or "not a priority"]
- Include vacancies without salary listed: [yes / no]

### Exclusions
- Companies to ignore: [any companies you don't want to see?]
- Keywords to exclude: [e.g. "python" if you don't want programming-heavy roles]
- Context-aware exclusions: [keywords that are OK in "nice to have" context? e.g. "плюсом,желательн"]

### Additional notes
- [anything else the user mentions — schedule, visa needs, industry preferences, etc.]
```

## Confirmation

After filling the brief, **show it to the user and ask for confirmation before proceeding**. Do not start searching until the user approves. If they want changes, update the brief and confirm again.

## Saving the Brief

Once confirmed, create the brief folder and save the brief:

- Folder: `.job-harness/briefs/YYYY-MM-DD_<brief-name>/` (English, kebab-case, no spaces)
- File: `.job-harness/briefs/YYYY-MM-DD_<brief-name>/brief.md`
- Runs folder: `.job-harness/briefs/YYYY-MM-DD_<brief-name>/runs/`

The brief is a reusable search profile. If the same search needs to be repeated later, create a new run under the existing brief instead of creating a duplicate brief.

## Artifact Layout

Each search run under a brief gets its own timestamped folder:

```
.job-harness/briefs/YYYY-MM-DD_<brief-name>/
  brief.md
  runs/
    YYYY-MM-DD_HHMM_<run-name>/
      run.md
      results.json
      report.md
      raw/
```

- `brief.md`: confirmed reusable search profile.
- `run.md`: what this run did, including sources, query variants, filters, and resolve settings.
- `results.json`: machine-readable vacancies and metadata. Copy from the MCP export (`search_results(run_id)` → `data/.runs/<run_id>/results.json`) after filtering/ranking; do not treat inline `search_results(format=inline)` slices as the final artifact.
- `report.md`: human-readable final report.
- `raw/`: intermediate source outputs and resolver data for audit.
- `.job-harness/companies/careers.json`: local memory of employer career pages for this project.

## Rules

1. **Confirm the artifact root first** — no file or folder creation without user approval.
2. **Always fill or select a brief before searching** — no searching without a confirmed brief.
3. **Ask don't assume** — if the user didn't mention something, ask. Don't fill in defaults on your own.
4. **Confirm relocation scope** — if the user is searching outside their country of residence, explicitly ask: "Do you need vacancies that explicitly mention relocation support, or should I include all vacancies in target countries regardless?"
5. **Salary without listed salary** — always ask whether to include vacancies where salary is not specified. Many good vacancies don't list salary.
6. **Confirm before proceeding** — show the filled brief to the user and get explicit approval before moving to search.
7. **Save after confirmation** — write `brief.md` only after the user approves the brief.
