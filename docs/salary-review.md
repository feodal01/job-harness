# Зарплата: возможности 23 источников и пробелы отбора

21.10.2026. Аудит возможностей и потерь данных для последующего разбора источников; частота ошибок не измерялась.

## Краткий вывод

- **6/23** теряют поле/текст зарплаты: Ashby, Breezy, Greenhouse, Lever, Personio, Y Combinator — SAL-001.
- **3/23** не доводят подтверждённую сумму из описания до постфильтра: Comeet, Taleo, Workday — SAL-002.
- **5/23** имеют маршрут нормализации: HH, Getmatch, Dreamjob, JOIN, Recruitee. Корректность неполная: SAL-003/005.
- **9/23** требуют дополнительного зарплатного примера: BambooHR, Huntflow, iCIMS, JazzHR, Jobvite, SmartRecruiters, SuccessFactors, Teamtailor, Workable. Отсутствие данных не установлено. Группы непересекающиеся; общий UI-дефект — SAL-004.

## Семантика и границы

Норма — [v2 §3.5](product-spec-v2.md#35-прочие-условия), заменившая старые [правила](search-query-business-rules.md#зарплата): сумма+валюта+период; сравнивается верхняя либо единственная граница. Низкая сумма → второй шанс; зарплаты нет → проходит; валюта/период другие или неизвестны → «не проверено». Gross/net — без пересчёта. Конвертация, `strict_minimum`, `salary_published` осознанно исключены.

**Нативный фильтр** применяет источник; **постфильтр** — наш код. **С/К/Т** — список / карточка / текст. «Не подтверждён» не означает отсутствия функции у всей платформы. Контракт tenant не универсален; закрытый API не доказывает публичный фильтр.

[SalaryFilter](../src/models/filters.py):39–42 принимает конечную положительную сумму, три буквы валюты, hour/month/year. [UI](../src/web/index.html):99–109 предлагает эти периоды и десять валют. Свободный словарь `Vacancy.salary` не моделирует несколько региональных вилок. [evaluate](../src/selection/filtering.py):226–239 не извлекает суммы из текста. Если адаптер не объявил `salary`, движок ставит unknown/unsupported; это не доказанное отсутствие зарплаты ([filter_parameters](../src/parsers/interfaces.py):150–175).

## Покрытие источников

Первичные источники → наш маршрут. Код ATS — `src/parsers/ats/<источник>/parser.py`, агрегаторов — `src/parsers/aggregators/{hh,getmatch}/parser.py`. «Не найден» — только осмотренная доска. Безопасного перевода зарплаты брифа нет у всех 23.

| Источник | Нативный фильтр: платформа → у нас | С/К/Т, нормализация у нас | Нужный маршрут / issue |
|---|---|---|---|
| [HH](https://hh.ru/search/vacancy?text=python) | salary, only_with_salary → не отправляются | С+К compensation; RUR→RUB, MONTH/YEAR/HOUR, gross, hidden | Постфильтр; SAL-004/005 |
| [Getmatch](https://getmatch.ru/vacancies) | sa ₽/мес → any | С display_from/to/currency/taxes/hidden; период из description; К зарплату не обновляет | Постфильтр; salary_is_total теряется, не называть окладом |
| [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) | Порог не документирован; includeCompensation — получение полей → выключен | С compensation tiers/components/interval/currency игнорируется | Компонент/регион → постфильтр; SAL-001 |
| [BambooHR](https://synder.bamboohr.com/careers/list) | Не подтверждён; Synder 404 → нет | С без salary; К нет; текущий salary contract неизвестен | Нужен действующий salary-case |
| [Breezy](https://switchboard.breezy.hr/) | Не найден → нет | С `$140,000–$150,000 / year` только в source_data.salary | Извлечение → постфильтр; `$` не доказывает USD; SAL-001 |
| [Comeet](https://www.comeet.com/jobs/tripleten/98.008) | На Comm-IT не найден → нет | С полные custom_fields.details; TripleTen hourly `$40–$85 USD`, salary пуст | Т → извлечение → постфильтр; SAL-002 |
| [Dreamjob](https://dreamjob.ru/employers/43931/vakansii) | salaryFrom, onlyWithSalary → нет | С суммы/RUB, период неизвестен; К JSON-LD | К → постфильтр; SAL-003; безопасность порога не доказана |
| [Greenhouse](https://docs.greenhouse.io/job-board.html) | Порог не документирован; pay_transparency получает поле → выключен | К pay_input_ranges: cents/currency/title/blurb; content доступен; salary пуст | К → нормализация → постфильтр; SAL-001/002 |
| [Huntflow](https://apicworld.huntflow.io/) | Не найден → нет | С без salary; К нет; в выбранной К суммы не найдены | Нужен salary-case К/Т |
| [iCIMS](https://careers-starr.icims.com/jobs/search?ss=1&in_iframe=1) | Classic Starr: не найден → нет | С metadata/summary без salary; К нет; старая WesternSouthern К:410 | Нужен Classic salary-case; Modern отдельно |
| [JazzHR](https://symend.applytojob.com/apply/jobs) | Не найден → нет | С metadata без salary; К нет; выбранная Pairsoft К без найденной суммы | Нужен salary-case К/Т |
| [Jobvite](https://jobs.jobvite.com/liferay/jobs) | Не найден → нет | С без salary; К нет; выбранная Visionist К без найденной суммы | Нужен salary-case К/Т |
| [JOIN](https://join.com/companies/join) | Не найден → нет | С salaryFrequency в source_data; К JSON-LD → salary | К → постфильтр; только числовой value-объект |
| [Lever](https://github.com/lever/postings-api) | Порог не документирован → нет | С salaryRange min/max/currency/interval, salaryDescription только сырые | Нормализация → постфильтр; SAL-001 |
| [Personio](https://vivid.jobs.personio.de/) | Не найден → нет | С embedded salary_information min/max/currency_code/type игнорируется; К JSON-LD частично | С → постфильтр; SAL-001 |
| [Recruitee](https://docs.recruitee.com/docs/feed) | На Redsky API не подтверждён → нет | С salary min/max/currency/period; hourly/monthly/yearly → hour/month/year | Постфильтр; day/week без пересчёта |
| [SmartRecruiters](https://developers.smartrecruiters.com/docs/endpoints) | Порог не подтверждён → нет | С без salary; ref/К нет; выбранная SmartRecruiters доска пуста | Нужен непустой salary-case К |
| [SuccessFactors](https://jobs.danfoss.com/search/) | Danfoss salary facet не найден → нет | XML содержит Т, RMK только С; salary не нормализуется | Нужны salary-cases XML и RMK |
| [Taleo](https://phh.tbe.taleo.net/phh01/ats/careers/v2/jobSearch?org=INSTITUTEDA&cws=39) | IDA Business Edition: не найден; custom tenant fields → зарплатного перевода нет | С без salary; К Navstar977 annual base `$100,000–$275,000` не загружается | К → Т → извлечение → постфильтр; SAL-002; Enterprise не доказан |
| [Teamtailor](https://career.teamtailor.com/jobs) | Не найден → нет | С без salary; К нет; [partner feed](https://partner.teamtailor.com/job_boards/) описывает min/max/currency/time-unit | Нужна публичная К; partner feed ≠ анонимный API |
| [Workable](https://apply.workable.com/entuitive/jobs.md) | Не подтверждён → нет | Markdown/v3 С без salary; К нет | Нужен публичный salary-case К/Т |
| [Workday](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite) | CXS salary facet не подтверждён → нет | С без суммы; К jobDescription с NVIDIA salary уже загружается, salary пуст | Т → извлечение по уровню/локации → постфильтр; SAL-002 |
| [Y Combinator](https://www.ycombinator.com/companies/airweave/jobs) | Company jobs: не найден; global search не проверен → нет | С salaryRange `€80K–€100K EUR`, equity отдельно; лишь source_data | Извлечение известных частей; SAL-001 |

Неопределённым маршрутам нужен опубликованный salary-case: К/Т → извлечение → постфильтр.

21.10 Getmatch sa=any/350000: total=923/696, скрытых по 40 из первых 50. Сохранение **всех** скрытых/пограничных вилок не доказано. Dreamjob salaryFrom=1000000000: карточки 2→1, остаётся карточка без суммы; одного tenant недостаточно. HH-порог не проверялся. Поэтому отсутствие отсечения соответствует v2.

## Воспроизводимые проблемы

Из корня checkout после `uv sync --group dev`. Даты/URL fixtures — в соседнем `meta.json`; это конкретные входы, не текущее распределение. Синтетика отмечена.

### SAL-001 — опубликованная зарплата не попадает в нормализованные факты

**Факт:** шесть адаптеров теряют зарплатные факты. **Ожидание:** сохранить известные сумму/валюту/период/контекст, разделить bonus/equity/tiers. Код: Ashby:74–101; LeverSearch.parse; PersonioSearch.parse; BreezySearch.parse; YCombinatorSearch._vacancy; Greenhouse Details.url/parse. Источники — таблица. Четыре входа реальные; Ashby/Greenhouse — синтетика официальных схем.

```bash
uv run python - <<'PY'
import importlib, json
from pathlib import Path
from src.parsers.ats.models import Board
root = Path('tests/fixtures/ats')
for platform, case in [('lever','unseen_finom'), ('breezy','switchboard'),
                       ('personio','vivid-money/success'), ('ycombinator','prequel/success')]:
    p = root/platform/case
    url = json.loads((p/'meta.json').read_text())['captured_url']
    a = importlib.import_module(f'src.parsers.ats.{platform}.parser').create_adapter(Board(url,'X'))
    body = next(p.glob('response.*')).read_text()
    vs = a.search.parse(body,a.search.build('')).vacancies
    print(platform, 'salary populated:',sum(bool(v.salary) for v in vs),
          'published evidence:',any(k in body for k in
          ['salaryRange','salary-range','salary_information']))
for platform, job in [
    ('ashby', {'id':'1','title':'X','jobUrl':'https://jobs.ashbyhq.com/demo/1',
               'compensation':{'summaryComponents':[{'compensationType':'Salary',
               'interval':'1 YEAR','currencyCode':'USD','minValue':81000,'maxValue':87000}]}}),
    ('greenhouse', {'id':1,'title':'X','absolute_url':'https://job-boards.greenhouse.io/demo/jobs/1',
                   'content':'<p>Job</p>','pay_input_ranges':[{'min_cents':5000000,
                   'max_cents':7500000,'currency_type':'USD','title':'NYC Salary Range'}]})]:
    module = importlib.import_module(f'src.parsers.ats.{platform}.parser')
    url = 'https://jobs.ashbyhq.com/demo' if platform=='ashby' else 'https://job-boards.greenhouse.io/demo'
    a = module.create_adapter(Board(url,'X')); request = a.search.build('')
    v = a.search.parse(json.dumps({'jobs':[job]}),request).vacancies[0]
    print(platform, request.url, 'salary:',v.salary)
    if a.vacancy_details: print('detail URL:',a.vacancy_details.url(v))
PY
```

**Вывод:** реальные входы: populated=0, evidence=True; синтетические: salary={}, URL без включения зарплаты. YC требует извлечения известных частей, не угадывания USD/year. Personio salary_information опубликован в embedded данных, видимый список может скрывать сумму.

### SAL-002 — зарплата из текста/полной карточки не проверяется

**Факт:** Comeet/Workday получают Т с суммой; Taleo К не загружает. **Ожидание:** извлечение валюты/периода/компонента/локации → постфильтр; неясный контекст → unknown. Код: ComeetSearch._vacancy, WorkdayDetails.parse, TaleoSearch (details нет).

**Шаги:** команда ниже — реальные fixtures. [Navstar977](https://phe.tbe.taleo.net/phe03/ats/careers/v2/viewRequisition?org=NAVSTAR&cws=37&rid=977):21.10 текст `annual base salary range`, `$100,000 - $275,000`. Снимка этой К в репозитории нет; live-case может закрыться.

```bash
uv run python - <<'PY'
import json,re
from pathlib import Path
from src.parsers.ats.models import Board
from src.parsers.ats.comeet.parser import create_adapter
from src.parsers.ats.workday.parser import WorkdayDetails
from src.models.vacancy import Vacancy
p=Path('tests/fixtures/ats/comeet/tripleten/success')
a=create_adapter(Board(json.loads((p/'meta.json').read_text())['captured_url'],'X'))
vs=a.search.parse((p/'response.html').read_text(),a.search.build('')).vacancies
v=next(v for v in vs if 'hourly rate' in (v.description or '').lower())
print('Comeet',re.search(r'.{0,20}hourly rate.{0,90}',v.description,re.I)[0],v.salary)
p=Path('tests/fixtures/ats/workday/nvidia/detail'); url=json.loads((p/'meta.json').read_text())['captured_url']
v=Vacancy(id='X',title='X',url=url.replace('/wday/cxs/nvidia',''))
v=WorkdayDetails('https://nvidia.wd5.myworkdayjobs.com','nvidia','NVIDIAExternalCareerSite').parse((p/'response.json').read_text(),v)
print('Workday',re.search(r'base salary range.{0,180}',v.description,re.I)[0],v.salary)
PY
```

Граница: это конкретные входы, не гарантия зарплаты во всех вакансиях.

### SAL-003 — Dreamjob теряет верхнюю границу записи «от A до B»

**Шаг, синтетика:** `uv run python -c 'from src.parsers.ats.dreamjob.parser import _salary; print(_salary("от 100 000 до 200 000 ₽"))'` → from=100000, to=None; **ожидается to=200000**. [parser.py](../src/parsers/ats/dreamjob/parser.py):30–31 исключает верх при любом начале «от». Период неизвестен: доказана потеря данных, не ошибочное отсечение. Живой пример этой записи не найден; JSON-LD К может восстановить вилку.

### SAL-004 — UI показывает сумму без периода

**Шаг:** выполнить на текущей функции отображения:

```bash
node <<'JS'
const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('src/web/static/app.js','utf8');
vm.runInThisContext(s.slice(s.indexOf('function salaryText('),s.indexOf('function showCards(')));
for(const period of ['hour','month','year']) console.log(period,salaryText({salary:{from:100,currencyCode:'USD',period}}));
JS
```

**Факт:** три одинаковых `от 100 USD`; **ожидание:** час/месяц/год в карточке и деталях. [app.js](../src/web/static/app.js):390–394 игнорирует period; функция общая для двух представлений. Проверена функция, не браузер; backend периоды различает.

### SAL-005 — RUR принимается в брифе, но не приводится к RUB

**Шаг, синтетика:**

```bash
uv run python - <<'PY'
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.selection.filtering import evaluate
v=Vacancy(id='1',title='X',url='https://example.org/1',salary={'to':300000,'currencyCode':'RUB','period':'month'})
for code in ['RUB','RUR']:
    f=SearchFilters(salary={'minimum':250000,'currency':code,'period':'month'})
    print(code,next(c.status for c in evaluate(v,f).checks if c.field=='salary'))
PY
```

**Факт:** RUB accepted, RUR unknown; **ожидание:** alias канонизировать или явно отклонить. [Схема](../src/models/filters.py):41 принимает RUR; [проверка](../src/selection/filtering.py):231 сравнивает строки. HH переводит RUR→RUB, JSON-LD/Recruitee — нет. Это переименование по [исходным правилам](search-query-business-rules.md#зарплата), не конвертация. UI не предлагает RUR: проблема API/сохранённого брифа; RUR у реального ATS не подтверждён.

## Проверенные границы сравнения

`uv run pytest -q tests/test_filters.py tests/test_parser.py tests/test_getmatch.py tests/parsers/ats/test_recruitee.py tests/parsers/ats/test_personio.py tests/parsers/ats/test_dreamjob.py tests/test_ats_contracts.py` → **138 passed**. Покрыты min/max, одна граница, равенство порогу, отсутствие суммы, чужая валюта/период, gross без пересчёта. Это не проверка полноты извлечения/нативных порогов. Несколько вилок требуют сохранения связи с локацией/уровнем; одна пара from/to этого не гарантирует.
