# Protocol: User Briefing

Every new session starts with filling out the search brief. Do not skip questions. Do not assume answers. Ask the user directly.

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
- Target countries/cities: [where you want to work]
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

Once confirmed, create the search folder and save the brief:

- Folder: `searches/YYYY-MM-DD_<job-title-slug>/` (English, kebab-case, no spaces)
- File: `searches/YYYY-MM-DD_<job-title-slug>/brief.md`

The brief is the source of truth. If the search needs to be repeated or adjusted later, the brief has all the parameters.

## Rules

1. **Always fill the brief first** — no searching without it
2. **Ask don't assume** — if the user didn't mention something, ask. Don't fill in defaults on your own.
3. **Confirm relocation scope** — if the user is searching outside their country of residence, explicitly ask: "Do you need vacancies that explicitly mention relocation support, or should I include all vacancies in target countries regardless?"
4. **Salary without listed salary** — always ask whether to include vacancies where salary is not specified. Many good vacancies don't list salary.
5. **Confirm before proceeding** — show the filled brief to the user and get explicit approval before moving to search
6. **Save after confirmation** — write `brief.md` only after the user approves the brief
