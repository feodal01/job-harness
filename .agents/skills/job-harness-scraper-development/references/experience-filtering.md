# Experience Filtering

job-harness filters grade by exact requested levels, not by minimum seniority.
The public filter is `experience_levels`, a list containing any of:

- `junior`
- `middle`
- `senior`

For example, `experience_levels=["middle"]` means exactly middle. It does not
include senior-only listings as matches.

## Public API

MCP tools:

- `search_start(..., experience_levels=["middle"])`
- `search_refine(..., experience_levels=["middle", "senior"])`

CLI:

- `job-harness search --experience-levels middle`
- `job-harness search --experience-levels middle,senior`

Invalid levels such as `midle` are rejected. An explicit empty list is rejected
by MCP. Omitting `experience_levels` means no grade filter.

## Grade Assessment Fields

Every returned listing has explicit grade assessment fields:

- `experience_levels`: assessed exact levels, such as `["middle"]`;
- `experience_origin`: `native`, `estimated`, or `unknown`;
- `experience_confidence`: `high`, `medium`, `low`, or `none`;
- `experience_evidence`: short deterministic evidence strings.

`native` means the source supplied a structured/server-side grade and the
parsed value is valid. `estimated` means job-harness inferred grade from vacancy
text using deterministic rules. `unknown` means there was not enough reliable
evidence.

The old single-value `experience` request parameter is not part of the public
API. Public results use the assessment fields above.

Internally, `JobListing.experience` is reserved for native structured/server
grade input from sources with `FilterSupport.SERVER` or `FilterSupport.CLIENT`.
Best-effort and unsupported scrapers must leave it empty; they should expose
ordinary vacancy text through `title`, `description`, `requirements`, `skills`,
or `raw`, and the grade engine owns all estimation.

## Filtering Semantics

The filter predicate is `experience_in(levels)`.

- A native or estimated listing passes when its `experience_levels` intersects
  the requested levels.
- A senior-only listing does not pass `experience_levels=["middle"]`.
- A listing with `experience_origin="unknown"` is kept inline, marked as
  unknown, and ranked after matched listings.
- Filter summaries report `native_matched`, `estimated_matched`,
  `unknown_kept`, and `removed`.

This keeps coverage broad without pretending that unknown-grade listings are
strict matches.

## Source Policy

Source capabilities still declare native support through `FilterSupport`:

- `server`: source can receive a URL/API grade parameter;
- `client`: source exposes a structured grade field;
- `best_effort`: source text can contain useful grade signals, but the scraper
  does not normalize them into a grade;
- `unsupported`: no native grade support.

`experience_levels` no longer skips sources whose grade support is
`unsupported`. Those listings go through the grade engine. Strict source skip
still applies to other unsupported requested flags such as `remote_only` or
`has_salary`.

HH-family scrapers and Habr Career use their server-side grade parameter only
for a single requested level. Multi-level requests are fetched more broadly and
filtered locally by the grade engine.

## Deterministic Estimation Rules

The grade engine checks native structured values first. For estimated listings,
it scores evidence from:

- title;
- raw platform fields;
- requirements;
- description;
- skills.

Recognized examples:

- `Lead` -> `senior`;
- `Intern` or `trainee` -> `junior`;
- `No experience`, `без опыта`, `нет опыта` -> `junior`;
- `1-3` years -> `middle`;
- `3-6` years or `6+` -> `senior`;
- explicit `Middle/Senior` style ranges -> multiple levels.

Conflicting strong signals without an explicit range become `unknown`.

For `best_effort` and `unsupported` sources, the engine ignores
`JobListing.experience` even if it is accidentally populated, so scraper-level
grade estimation cannot override centralized engine inference.

## Source Notes

Native/server or native/client sources include HH-family, Habr Career,
Finder.work, and IT-Jobs.uz when they return valid grade data.

Text-estimated sources include sources such as HireHi, Hirify, GeekJob,
Talento, JobTurbo, getmatch, `company_careers`, and per-company career
scrapers when they contain grade-like text. `career:vk` specialty names are not
treated as grades.

`company_directory` returns employer entrypoints, not confirmed vacancy grade
data, so its entries usually remain `unknown`.

## Company Directory And Career Lists

The bundled company lists are not native grade sources.

`data/company-directory.json` is a directory of company profiles. It stores
company-level facts such as career URLs, countries, stack, industry, and remote
signals. It does not store per-vacancy grade data.

`data/company-careers-public.json` is a public career-page cache. It tells the
runtime where to look for employer jobs, not what grade a specific vacancy has.

The `search_company_jobs` MCP lookup returns matching company profiles from the
directory. It does not scrape vacancies, does not accept `experience_levels`,
and does not run grade assessment.

The `company_careers` registered source performs a timeout-aware live crawl over
known employer career URLs and returns normal `JobListing` records. It does not
populate `JobListing.experience`; any grade is assigned later by the centralized
grade engine from vacancy text such as title and matched link text. If the
configured source timeout is not enough to finish every company target, the
source reports `partial` instead of pretending the crawl was complete.

The `company_directory` scraper can expose company profiles as ordinary search
listings. Those listings declare `experience=unsupported` and do not populate
`JobListing.experience`. If a grade filter is active, the centralized grade
engine assesses their title, description, skills, and raw fields. Most directory
entrypoints have no reliable vacancy-grade evidence and therefore remain
`experience_origin="unknown"`; they are kept inline after matched listings.

The live company-career probing code in `company_career_search.py` and
`company_career_batch.py` produces `CompanyVacancyHit` records internally. Its
role terms such as `lead` are used for vacancy-link relevance scoring, not as
grade extraction. When `company_careers` promotes those hits into normal search
listings, they pass through `experience_engine` instead of estimating grade
inside the probing code.

The separate company-live batch still exists for resumable 400+ company audit
workflows and smoke verification. It writes JSONL checkpoints, while
`company_careers` is the source used by ordinary `search_start` runs.
