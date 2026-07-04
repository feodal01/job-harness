# Workplace And Geography Filtering

This file is the focused post-processing contract for the interaction between
`work_formats`, `remote_scopes`, `vacancy_geographies`, global remote, hybrid,
and onsite or office formats.

## Definitions

- `work_formats` is the requested workplace format set. Valid request values
  are `remote`, `hybrid`, `office`, and `unknown`.
- `remote_scopes` is remote eligibility only. Valid request values are `global`,
  limited scopes such as `country:GB` or `region:EU`, and `unknown`. Physical
  formats never appear here.
- `vacancy_geographies` is the market, office, employer, or vacancy-card
  geography the user wants to search. Valid request values are `country:<code>`,
  `region:<code>`, `city:<name>`, and `unknown`.
- `remote_scope` is the normalized remote eligibility scope. Valid result values
  are `global`, limited scopes such as `country:GB` or `region:EU`, and
  `unknown`.
- `remote_global` and `remote_in_country` are source facts. They are normalized
  into `remote_scope` and are not user-facing filter parameters.
- `hybrid` and `office` are physical formats. They never become remote
  eligibility by themselves.
- `EU` and `europe` mean the European Union country set. This intentionally
  excludes `GB`, `RU`, and non-EU European countries.
- Timezone ranges such as `remote from GMT-7 to GMT+4` are eligibility hints,
  not geography. They can make the work format remote, but they do not create a
  remote country or region scope.
- In requests, `unknown` is an opt-in expansion for an otherwise concrete
  filter. It cannot be the only requested value for `work_formats`,
  `remote_scopes`, or `vacancy_geographies`.

## Core Rules

1. `work_formats` is a positive filter. If it is empty, workplace format does
   not filter rows. If it is present, unknown rows pass only when `unknown` is
   explicitly requested alongside a concrete format.
2. `remote_scopes` applies only to rows whose normalized `work_format` includes
   `remote`. Request `global` by itself for global-only remote. Request
   `country:<code>` or `region:<code>` when that geography is acceptable;
   globally remote rows satisfy country and region requests because `global` is
   a superset.
3. A request cannot include only `unknown` remote scope. To keep rows with
   unknown remote eligibility, request at least one concrete scope plus
   `unknown`.
4. `vacancy_geographies` is an additional market/location constraint. It does
   not make a remote scope compatible by itself.
5. When both `remote_scopes` and `vacancy_geographies` are present, both
   dimensions must pass. `remote_scope=global` satisfies only the remote-scope
   dimension; it does not replace separate vacancy geography evidence.
6. `hybrid` and `office` are requested through `work_formats`. They are then
   checked against `vacancy_geographies` like any other row location.
7. When a source lists remote together with hybrid or onsite options, all
   supported formats are preserved. Remote-scope rules apply to the remote
   branch of the filter AST.
8. A remote vacancy with city-only location evidence gets a country-limited
   remote scope inferred from the city, for example `London` -> `country:GB` or
   `Barcelona` -> `country:ES`. Multi-city locations may produce multiple
   country scopes.
9. A country or city without any remote/work-format evidence remains
   `work_format=unknown` and `remote_scope=unknown`; requested positive
   workplace filters remove it by default.
10. Unknown geography or remote scope is removed for requested positive
   workplace/geography filters unless the generated filter AST explicitly
   includes `unknown` alongside a concrete requested value for that field.

## Required Combinations

| Remote scope request | Vacancy geography request | Requested formats | Vacancy evidence | Decision | Primary reason |
| --- | --- | --- | --- | --- | --- |
| `global` | `region:EU` | remote | scope `global`, vacancy country `PL` | keep | Global remote scope matches and vacancy geography matches EU. |
| `global` | `region:EU` | remote | scope `global`, unknown vacancy country | remove | Vacancy geography requires matching evidence by default. |
| `country:GB` | `region:EU` | remote | scope `country:PL` | remove | Remote scope was not requested. |
| `country:GB` | `region:EU` | remote | scope `region:EU` | remove | `country:GB` was requested exactly; `region:EU` is a different scope. |
| `country:GB` | `region:EU` | remote | scope `country:GB`, vacancy country includes `GB` and `EU` | keep | Both remote scope and vacancy geography match. |
| `country:GB` | `region:EU` | remote | scope `country:GB`, vacancy country only `GB` | remove | Vacancy geography does not match EU. |
| empty | `region:EU` | hybrid | country `PL`, `hybrid` | keep | Physical format and vacancy geography were explicitly requested. |
| empty | `region:EU` | hybrid | country `GB`, `hybrid` | remove | The EU scope excludes `GB`. |
| empty | `country:GB` | office | country `GB`, `office` | keep | Physical format and vacancy geography match. |
| `country:GB` | `country:GB` | remote | scope `country:GB` | keep | Requested country-limited remote scope matches. |
| `country:GB` | `country:GB` | remote | scope `global`, vacancy country `GB` | keep | Global remote is a superset of the requested country scope. |
| `country:GB` | `country:GB` | remote | country `GB`, `hybrid` or `office` | remove | Physical formats were not requested. |
| `country:GB` | empty | remote | scope `country:GB` | keep | Remote scope matches. |
| `country:GB` | empty | remote | scope `country:PL` or `region:EU` | remove | Remote scope was not requested. |
| `country:GB` | empty | remote | country `GB`, no remote/work-format evidence | remove | Country-only evidence is not remote-compatible. |
| empty | `europe` | no remote filter | country `PL` | keep | Vacancy geography alone is a market/location filter. |
| empty | `europe` | no remote filter | country `GB` | remove | The EU scope excludes UK. |
| empty | `europe` | no remote filter | unknown country | remove | Requested vacancy geography requires matching evidence by default. |

## Diagnostics

- Work format mismatch: `work_format_mismatch`.
- Remote scope mismatch: `remote_scope_mismatch`.
- Vacancy geography mismatch: `vacancy_geography_mismatch`.
