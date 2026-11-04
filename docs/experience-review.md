# Аудит опыта: 23 источника

## Вывод

- **3/23** нормализуют минимальный стаж: HH, DreamJob, Y Combinator (YC). Границы HH/DreamJob корректны; YC пропускает «любой опыт» — EXP001.
- **1/23** выводит сырой уровень в поле experience: Comeet. **10/23** получают текст/аннотацию или имеют подключённую деталь, но годы не извлекают. **9/23** читают краткие карточки без подключённой детали. Группы не пересекаются.
- Personio XML с годами не читается — EXP002. Текстовые требования не разбираются — EXP003: пробел покрытия, не ошибка языковой модели.
- Грейд исключён спецификацией; фильтр не нужен.

## Смысл и границы

Где проверяется стаж кандидата, какие данные нужны? Код `25593be`, 21.10.2026, 21 ATS + HH/Getmatch. [В обычном поиске](../src/parsers/registry.py) подключены только HH/Getmatch.

[«Ваш опыт, лет»](../src/web/index.html#L108) — `experience_years` кандидата, 0–80, допускаются дроби. [Вакансия](../src/models/vacancy.py#L24) содержит `min_experience` и `min_experience_exclusive`. [Проверка](../src/selection/filtering.py#L209): стаж ≥ минимум, при строгом пороге — >. Неизвестное требование — `unknown`; мягкое несоответствие — «Может быть подходит». Unknown не отбрасывает вакансию.

По [правилу](search-query-business-rules.md#L103) и [спецификации](product-spec-v2.md#L173) 8 лет проходят диапазон 3–6: проверяется минимум. `Grade=intern/junior/middle/senior/lead` отсутствует в SearchFilters. Уровни и стажировка как занятость не переводятся в годы.

## Карта источников

Нативный фильтр работает до выдачи; наш `local` проверяет минимум, `none` — годы не проверяются. **НП** — публичный фильтр лет не подтверждён, не доказано его отсутствие. API с ключом — не анонимный поиск. Маршрут — необходимые данные.

| Источник | Нативный опыт платформы → у нас | Данные списка/детали и нормализация | Маршрут / проблема |
|---|---|---|---|
| [HH](../src/parsers/aggregators/hh/parser.py#L97) | `experience` → native; второй шанс → local | Список/деталь `workExperience`: noExperience→0, between1And3→1, between3And6→3, moreThan6→>6. [Справочник](https://api.hh.ru/dictionaries) | Категории + локальный минимум; дефект границ не найден |
| [DreamJob](../src/parsers/ats/dreamjob/parser.py#L87) | Годы НП → local | Теги списка: нет опыта→0, 1–3→1, 3–6→3, более 6→>6; текст в детали | Теги; конкретные требования из детали → EXP003 |
| [YC](../src/parsers/ats/ycombinator/parser.py#L82) | Годы НП → local | Список `minExperience`: N year(s)/N+ years→N включительно; Any→None | Исправить нормализацию → EXP001; детали не подключены |
| [Comeet](../src/parsers/ats/comeet/parser.py#L81) | Годы НП → none | Список: raw `experience_level`, текст `custom_fields.details`; не годы. [Careers API](https://developers.comeet.com/reference/retrieve-a-position-new) требует token | Текст уже получен → EXP003 |
| [Ashby](../src/parsers/ats/ashby/parser.py#L55) | В [Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) лет нет → none | Список: `descriptionHtml`, числового опыта нет | Текст списка → EXP003 |
| [Greenhouse](../src/parsers/ats/greenhouse/parser.py#L35) | В [Job Board API](https://docs.greenhouse.io/job-board) лет нет → none | `content=true`: полный текст; hosted-поиск: отдельная деталь. `metadata` — поля работодателя | Текст/metadata с установленным смыслом → EXP003 |
| [iCIMS](../src/parsers/ats/icims/parser.py#L19) | Годы НП → none | Только summary. `experienceyears` есть в [служебном API](https://developer-community.icims.com/applications/applicant-tracking/ats-data-models/job-profile), не в нашем списке | Нужен полный текст публичной детали → EXP003 |
| [JOIN](../src/parsers/ats/join/parser.py#L123) | `categoryIds` — профессия; годы НП → none | Список без опыта; деталь: текст/JobPosting | Текст детали → EXP003 |
| [Lever](../src/parsers/ats/lever/parser.py#L49) | [API](https://github.com/lever/postings-api) имеет `level`; передаём явно, но годы → none | Полный текст в списке; level не переводится из стажа кандидата | Текст списка → EXP003; level не равен годам |
| [Personio](../src/parsers/ats/personio/parser.py#L38) | [XML](https://developer.personio.de/v1.0/reference/get_xml): поле лет, фильтра нет → none | HTML-список без лет; деталь с текстом. XML `yearsOfExperience` не читается | XML → EXP002; текст детали → EXP003 |
| [Recruitee](../src/parsers/ats/recruitee/parser.py#L64) | [API](https://docs.recruitee.com/reference/offers): department/tag, не годы → none | Список: description+requirements; raw `experience_code`: entry_level/mid_level/experienced/senior_manager/executive | Текст списка → EXP003; коды — уровни |
| [SmartRecruiters](../src/parsers/ats/smartrecruiters/parser.py#L30) | В [postings](https://developers.smartrecruiters.com/docs/endpoints) годы не указаны → none | Raw `experienceLevel` — уровень; текст в неподключённой детали `jobAd.sections` | Нужна деталь → EXP003 |
| [SuccessFactors](../src/parsers/ats/successfactors/parser.py#L39) | Годы НП; RMK filter1/2/3 зависят от сайта → none | Legacy XML: описание; RMK: краткие карточки, детали нет | XML-текст или деталь RMK → EXP003; facet не универсален |
| [Workday](../src/parsers/ats/workday/parser.py#L106) | Годы НП; наш `appliedFacets={}` → none | Список без лет; подключённая деталь `jobDescription` | Текст детали → EXP003; категории tenant не обобщать |
| [Getmatch](../src/parsers/aggregators/getmatch/parser.py#L42) | [UI](https://getmatch.ru/vacancies): Junior…C-level — грейд; годы НП → none | Аннотация списка и подключённое описание; минимум не извлекается | Текст детали → EXP003; фильтр грейда не нужен |
| [BambooHR](../src/parsers/ats/bamboohr/parser.py#L23) | Годы НП → none | `/careers/list` без опыта/текста. [ATS API](https://documentation.bamboohr.com/reference/get-job-summaries) требует авторизации | Подтвердить публичную деталь, затем текст → EXP003 |
| [Breezy](../src/parsers/ats/breezy/parser.py#L22) | В [продукте](https://help.breezy.hr/en/articles/9237171-working-with-positions) есть level; публичные годы НП → none | Наш список без опыта/текста; отдельная `/p/...` может дать JobPosting | Нужна деталь → EXP003; текст подтверждён на Themis Insight |
| [Huntflow](../src/parsers/ats/huntflow/parser.py#L17) | Годы НП → none | Список без опыта/текста/детали | Нужен текст `/vacancy/...` → EXP003 |
| [JazzHR](../src/parsers/ats/jazzhr/parser.py#L17) | Годы НП → none | Список/raw metadata; текста нет | Нужен текст `/apply/jobs/details/...` → EXP003 |
| [Jobvite](../src/parsers/ats/jobvite/parser.py#L38) | Годы НП → none | Список без опыта/текста/детали | Нужен текст `/job/...` → EXP003 |
| [Taleo](../src/parsers/ats/taleo/parser.py#L30) | Обнаруженные поля формы/CUSTOM_N; годы НП → none | Business Edition v2: карточки без текста. CUSTOM_N не обязательно опыт; Enterprise не проверен | Нужен текст `viewRequisition` → EXP003 |
| [Teamtailor](../src/parsers/ats/teamtailor/parser.py#L28) | Годы НП → none | Список без опыта/текста/детали | Нужен текст `/jobs/<id>...` → EXP003 |
| [Workable](../src/parsers/ats/workable/parser.py#L31) | Годы НП → none | Наш POST jobs/jobs.md: краткие карточки. [Клиентский API](https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page) требует token | Нужна публичная деталь/текст → EXP003 |

## EXP001 — YC теряет явное «любой опыт»

**Дефект.** [Парсер](../src/parsers/ats/ycombinator/parser.py#L82) преобразует `minExperience="Any (new grads ok)"` в None/unknown. Ожидание: 0 включительно/accepted — разрешён любой стаж.

**Повторить:** EXP001 ниже, сохранённая страница Veryfi. Живые ответы 21.10.2026: [YC](https://www.ycombinator.com/companies/y-combinator/jobs) — 2 таких записи из 10; [Whatnot](https://www.ycombinator.com/companies/whatnot/jobs) — 61 из 147 (`data-page→props.jobPostings[].minExperience`). 

**Границы:** теряется подтверждение, не вакансия. Пустое поле/New Grad не означают 0. Числовые N+ years работают.

## EXP002 — Personio: опубликованные годы не используются

**Пробел данных.** [XML-контракт](https://developer.personio.de/v1.0/reference/get_xml) содержит yearsOfExperience: lt-1, 1-2, 2-5, 5-7, 7-10, 10-15, gt-15; seniority отдельно. [XML публичен](https://support.personio.de/hc/en-us/articles/29375445597725-Frequently-asked-questions-on-XML-job-integration), но наш адаптер его не запрашивает.

**Повторить из корня:**

```sh
uv run python -c 'import httpx,xml.etree.ElementTree as E,collections; from src.parsers.ats.personio.parser import create_adapter; from src.parsers.ats.models import Board; r=httpx.get("https://vivid.jobs.personio.de/xml?language=en",follow_redirects=True); r.raise_for_status(); print(collections.Counter(x.findtext("yearsOfExperience") for x in E.fromstring(r.text).findall("position"))); print(create_adapter(Board("https://vivid.jobs.personio.de/","Vivid")).capabilities.query_parameters["experience_years"])'
```

**Факт 21.10.2026:** 24 позиции: 2-5 — 9, 5-7 — 8, 1-2 — 1, пусто — 6; наш режим none. **Ожидание:** использовать нижнюю границу; gt-15 — >15. lt-1 не означает минимум 1.

**Границы:** подтверждён только Vivid; другие две XML-ссылки дали HTML. Schema.org [monthsOfExperience](https://schema.org/OccupationalExperienceRequirements) — минимум месяцев; [enrich](../src/parsers/ats/jsonld.py#L40) его пропускает. В проверенных деталях DreamJob/Personio/JOIN поля нет: ниже синтетика, не живой дефект.

## EXP003 — текст требований не участвует в проверке

**Пробел покрытия.** [Проверка](../src/selection/filtering.py#L209) и [capabilities](../src/parsers/interfaces.py#L151) используют min_experience. У остальных 20 источников режим none независимо от полученного текста.

**Повторить:** EXP003 ниже: 16 синтетических RU/EN описаний, стаж 2 года → unknown. Подтверждено отсутствие обработки, не ошибка языковой модели.

**Ожидание покрытия:** «от N»/at least N/N+ — включительно; «более N»/more than N — строго; N–M — минимум N. «Нет опыта»/no experience required — отсутствие порога. «До N» не равно минимуму N. preferred/«желательно»/not required не становятся обязательным минимумом. «N лет или диплом» сохраняет альтернативу: стажа недостаточно для отказа. Неясная область опыта и противоречия остаются unknown: общий стаж не доказывает стаж управления или Python.

**Границы:** девяти кратким источникам и RMK нужна деталь; iCIMS-summary может быть неполным. HTTP 200 не доказывает получение текста.

## Локальная проверка

Из корня: `uv run python - <<'PY'`, код, затем строка `PY`. **61 случай:** 40 границ HH/DreamJob, 4 YC, 16 текстов, 1 Schema.org. Assert фиксирует текущее поведение. Ещё 10 проверок запросов HH: стаж 0/1/3/6/6.5 в обычном режиме/втором шансе; moreThan6 только после 6, второй шанс снимает experience. НП: живые положительная/нулевая проверки лет не установлены; level Lever — по контракту.

```python
from pathlib import Path
from src.models.vacancy import Vacancy
from src.models.filters import SearchFilters
from src.selection.filtering import evaluate
from src.parsers.aggregators.hh.parser import _normalize
from src.parsers.ats.models import Board
from src.parsers.ats.dreamjob.parser import create_adapter as dream
from src.parsers.ats.ycombinator.parser import create_adapter as yc
from src.parsers.ats.jsonld import enrich

def result(v, years):
    checks = evaluate(v, SearchFilters(experience_years=years), complete=True).checks
    return next(c.status for c in checks if c.field == "experience_years")

def vacancy(**extra):
    return Vacancy(id="1", title="Engineer", url="https://example.com/1", **extra)

# HH/DreamJob: синтетика.
d = dream(Board("https://dreamjob.ru/employers/1/vakansii", "Example"))
categories = [
    ("noExperience", "Нет опыта", 0),
    ("between1And3", "От 1 года до 3 лет", 1),
    ("between3And6", "От 3 до 6 лет", 3),
    ("moreThan6", "Более 6 лет", 6),
]
for hh_label, dj_label, minimum in categories:
    a = vacancy(experience=hh_label)
    _normalize(a, {})
    body = ("<div class='vacancy'><div class='vacancy__head' "
            "data-link='/employers/1/vakansii/1'>Engineer</div>"
            f"<span class='tags__item'>{dj_label}</span></div>")
    b = d.search.parse(body, d.search.build("")).vacancies[0]
    for years in [0, 1, 3, 6, 6.5]:
        ok = years > minimum if minimum == 6 else years >= minimum
        expected = "accepted" if ok else "rejected"
        assert result(a, years) == result(b, years) == expected

# EXP001: HTML Veryfi + числовая синтетика.
s = yc(Board("https://www.ycombinator.com/companies/veryfi/jobs", "Veryfi"))
body = Path("tests/fixtures/ats/ycombinator/veryfi/success/response.html").read_text()
v = next(v for v in s.search.parse(body, s.search.build("")).vacancies
         if v.experience == "Any (new grads ok)")
assert v.min_experience is None and result(v, 0) == "unknown"
for label, minimum in [("1 year", 1), ("3+ years", 3), ("6+ years", 6)]:
    v = s.search._vacancy({"id": "1", "title": "Engineer", "url": "/jobs/1",
                           "minExperience": label})
    assert v.min_experience == minimum and result(v, minimum) == "accepted"

# EXP003: синтетика.
phrases = [
    "At least 3 years of experience required.", "More than 3 years required.",
    "3+ years of experience required.", "3–5 years of experience required.",
    "Up to 3 years of experience.", "No experience required.",
    "3 years preferred, not required.", "3 years experience or a relevant degree.",
    "Требуется от 3 лет опыта.", "Требуется более 3 лет опыта.",
    "Опыт работы 3–5 лет.", "Опыт работы до 3 лет.",
    "Опыт работы не требуется.", "3 года опыта желательны.",
    "3 года опыта либо профильный диплом.", "Три года опыта не обязательны.",
]
assert all(result(vacancy(description=p), 2) == "unknown" for p in phrases)

# EXP002: синтетика.
v = enrich(vacancy(), {"@type": "JobPosting", "description": "Engineer",
    "experienceRequirements": {"@type": "OccupationalExperienceRequirements",
                               "monthsOfExperience": 36}})
assert v.min_experience is None and result(v, 2) == "unknown"
print("61 cases: observed behavior confirmed")
```
