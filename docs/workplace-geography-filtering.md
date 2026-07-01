# Workplace And Geography Filtering

This file is the focused post-processing contract for the interaction between
`work_from_geographies`, `vacancy_geographies`, remote eligibility, global
remote, hybrid, and onsite or office formats.

## Definitions

- `work_from_geographies` is where the applicant will physically be while doing
  remote work.
- `vacancy_geographies` is the market, office, employer, or vacancy-card
  geography the user wants to search.
- `remote_global` means the vacancy explicitly allows work from anywhere.
- `remote_in_country` is not a user intent. It is a source fact that becomes a
  limited remote scope such as `country:GB`, `country:PL`, or `region:EU`.
- `hybrid` and `office` are physical formats. They never become remote
  eligibility by themselves.
- `EU` and `europe` mean the European Union country set. This intentionally
  excludes `GB`, `RU`, and non-EU European countries.
- Timezone ranges such as `remote from GMT-7 to GMT+4` are eligibility hints,
  not geography. They can make the work format remote, but they do not create a
  remote country or region scope.

## Core Rules

1. `compatible_remote` requires at least one `work_from_geographies` value.
2. A globally remote vacancy satisfies `compatible_remote` from any
   `work_from_geographies` value.
3. A limited remote vacancy satisfies `compatible_remote` only when its remote
   scope intersects `work_from_geographies`.
4. `vacancy_geographies` is an additional market/location constraint. It does
   not make a limited remote scope compatible with the applicant's work-from
   geography.
5. When both `work_from_geographies` and `vacancy_geographies` are present,
   both dimensions must pass. `remote_global` passes the vacancy geography
   dimension because it is not tied to one vacancy country.
6. If `work_from_geographies` and `vacancy_geographies` do not intersect,
   a limited remote vacancy can still pass when its remote scope intersects
   `work_from_geographies` and the row's vacancy countries separately satisfy
   `vacancy_geographies`.
7. `hybrid_ok` and `office_ok` only allow physical vacancies whose normalized
   country intersects `work_from_geographies`. When `vacancy_geographies` is
   also present, the same vacancy must satisfy that constraint too. Physical
   formats must not bridge non-intersecting request geographies.
8. When a source lists remote together with hybrid or onsite options, the final
   work format is remote and remote-scope rules apply.
9. A remote vacancy with city-only location evidence gets a country-limited
   remote scope inferred from the city, for example `London` -> `country:GB` or
   `Barcelona` -> `country:ES`. Multi-city locations may produce multiple
   country scopes.
10. Unknown geography or remote scope fails positive filters with an explicit
   unknown diagnostic instead of being treated as a match.

## Required Combinations

| Work from | Vacancy geography | Requested formats | Vacancy evidence | Decision | Primary reason |
| --- | --- | --- | --- | --- | --- |
| `UK` | `europe` | remote | `remote_global=true` | keep | Global remote is compatible from UK. |
| `UK` | `europe` | remote | `remote_in_country=true`, scope `country:PL` | remove | Remote scope does not include UK. |
| `UK` | `europe` | remote | `remote_in_country=true`, scope `region:EU` | remove | The EU scope excludes UK. |
| `UK` | `europe` | remote | scope `country:GB`, vacancy country includes `GB` and `EU` | remove | Vacancy geography does not match EU. |
| `UK` | `europe` | remote | scope `country:GB`, vacancy country only `GB` | remove | Vacancy geography does not match Europe. |
| `UK` | `europe` | hybrid allowed | country `PL`, `hybrid` | remove | Physical work in Europe is not workable from UK. |
| `UK` | `europe` | hybrid allowed | country `GB` and `europe`, `hybrid` | remove | Physical formats cannot bridge non-intersecting request geographies. |
| `UK` | `europe` | office allowed | country `PL`, `office` | remove | Physical work in Europe is not workable from UK. |
| `UK` | `UK` | remote | scope `country:GB` | keep | UK remote-in-country matches work-from UK. |
| `UK` | `UK` | remote | `remote_global=true` | keep | Global remote is a superset of UK-compatible remote. |
| `UK` | `UK` | hybrid allowed | country `GB`, `hybrid` | keep | Physical geography matches both work-from and vacancy geography. |
| `UK` | `UK` | office allowed | country `GB`, `office` | keep | Physical geography matches both work-from and vacancy geography. |
| `UK` | `UK` | remote only | country `GB`, `hybrid` or `office` | remove | Physical formats were not requested. |
| `UK` | empty | remote | scope `country:GB` | keep | Remote scope matches work-from UK. |
| `UK` | empty | remote | scope `country:PL` or `region:EU` | remove | Remote scope does not include UK. |
| `UK` | empty | hybrid or office allowed | country `GB` | keep | Physical geography matches work-from UK. |
| `UK` | empty | hybrid or office allowed | country `PL` | remove | Physical geography does not match work-from UK. |
| empty | `europe` | no remote filter | country `PL` | keep | Vacancy geography alone is a market/location filter. |
| empty | `europe` | no remote filter | country `GB` | remove | The EU scope excludes UK. |
| empty | `europe` | no remote filter | unknown country | remove | Positive vacancy geography cannot be proven. |

## Diagnostics

- Remote scope mismatch: `remote_eligibility_mismatch`.
- Unknown remote scope for compatible remote: `remote_eligibility_unknown`.
- Non-global row under global-only search: `remote_global_mismatch`.
- Unknown global evidence under global-only search: `remote_global_unknown`.
- Hybrid or office outside the work-from geography:
  `hybrid_geography_mismatch` or `office_geography_mismatch`.
- Unknown physical geography:
  `hybrid_geography_unknown` or `office_geography_unknown`.
- Vacancy geography mismatch: `vacancy_geography_mismatch`.
- Unknown vacancy geography: `vacancy_geography_unknown`.
