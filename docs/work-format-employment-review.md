# Формат работы и тип занятости: возможности источников и проблемы

Дата проверки: 21 октября 2026 года. Область: все 21 ATS из `src/parsers/ats`, HH и Getmatch.
Связанный разбор географии: [geography-review.md](geography-review.md).

## Управленческая сводка

**Вывод:** получение вакансий и часть структурированных фильтров уже работают, но точности отбора
пока нельзя доверять одинаково во всех источниках. По совокупности двух аудитов у **10 из 23**
источников воспроизведены конкретные дефекты, ещё у **8** есть пробелы интеграции/извлечения.
У **4** основной проверенный маршрут данных работает без отдельного подтверждённого дефекта адаптера;
**1** нельзя полноценно оценить из-за недоступности публичной доски.

Классификация ниже взаимоисключающая: сначала учитывается конкретный дефект, затем пробел,
затем рабочий основной маршрут, затем недостаток доказательств. Источник считается один раз.
Это результат аудита кода и отдельных реальных/минимальных случаев, а не доля ошибочных вакансий
и не утверждение о полном отказе источника.

| Итог по источнику | Количество | Источники | Практический смысл |
|---|---:|---|---|
| Воспроизведён конкретный дефект адаптера/перевода | 10 / 23 | HH, Getmatch, Breezy, Dreamjob, Greenhouse, Huntflow, iCIMS, JOIN, Teamtailor, Y Combinator | Неверный отбор, потеря опубликованных полей или отказ поддержанного адреса; это первые кандидаты на исправление |
| Есть пробел реализации без отдельного подтверждённого дефекта этой группы | 8 / 23 | JazzHR, Jobvite, Lever, SmartRecruiters, SuccessFactors, Taleo, Workable, Workday | Не передаётся имеющийся фильтр, не извлекается нужное поле/карточка или теряется контекст точного запроса |
| Основной проверенный маршрут уже работает | 4 / 23 | Ashby, Comeet, Personio, Recruitee | Структурированные факты доступны для постфильтра; общие дефекты географии и непроверенные варианты остаются |
| Полноценная текущая проверка заблокирована | 1 / 23 | BambooHR | Публичная доска/лента недоступна; исторические данные и закрытая документация не доказывают текущую работоспособность |

**Общие проблемы считаются отдельно.** Ошибки сопоставления городов/стран, ограниченный каталог,
утрата административной идентичности и неточный контекст запроса могут затрагивать любой адаптер.
Они не означают 23 независимые ошибки парсеров и не превращают четыре рабочих маршрута в полностью
проверенные источники. Нет подтверждённой метрики полноты или точности по всем вакансиям.

| Тип проблемы | Где проявляется | Пример / основание |
|---|---|---|
| Неверное совпадение/отклонение географии | Общий модуль и отдельные переводчики | GEO-001/002/005/008/009/019/020: алиасы, исключения remote, городская remote-граница, Unicode |
| Потеря уже опубликованного факта | Парсинг списка или полной карточки | WF-003/005/006/007/008/010/014: стажировка HH, занятость, metadata, изменённый шаблон |
| Недостающий маршрут применения фильтра | Нативный запрос, карточка, постфильтр | GEO-010/011/012/018, WF-001/002/009/011/012/013: данные у источника есть, у нас используются частично |
| Каталог, идентичность места и продуктовый смысл условия | Общие справочники, сборка, UI и модель запроса | GEO-007/013/021/022, WF-004: неполное покрытие, потеря региона/ID, недоступные города и неоднозначный contract |
| Риск или недостаток доказательств | Устойчивость labels, закрытые API, недоступная доска | GEO-004/006, BambooHR: не учитывать как подтверждённый текущий отказ |

Нормативное требование для планирования: [география в продуктовой спецификации](product-spec-v2.md#34-география-и-формат-работы):
полный каталог стран по ISO и до пяти альтернатив «страна + необязательный город».
Ниже перечислены факты по каждому источнику и самостоятельные багрепорты для проверки и планирования.

## Главные выводы

Нужно различать три возможности: источник умеет искать по условию, источник сообщает условие
в вакансии, наш код извлекает и проверяет это условие. Они часто не совпадают.
Например, SmartRecruiters фильтрует формат работы на сервере, а тип занятости сообщает отдельным
полем для постфильтрации. JazzHR и Jobvite имеют нативный фильтр занятости, который наши адаптеры
не используют, и структурированные данные в полной карточке, которую они сейчас не загружают.

Обнаружены потери уже доступных данных: HH игнорирует признак стажировки, Breezy — служебные метки
контрактной занятости, iCIMS — составной тип `Permanent Full-Time`, Teamtailor — формат в текущем
шаблоне списка. Поэтому отсутствие нормализованного значения у нас не означает отсутствия данных
у источника. Журнал ниже отделяет подтверждённые дефекты от пробелов реализации и ограничений проверки.

## Термины и границы проверки

- **Формат:** `remote` — удалённо, `hybrid` — гибрид, `onsite` — на месте работодателя.
  Удалёнка сама по себе не доказывает доступность из любой страны.
- **Занятость:** `fulltime` / `parttime` — полная / частичная; `contract` — контрактная;
  `intern` — стажировка; `volunteer` — волонтёрство. Точные значения каждого источника указаны отдельно.
  Полная занятость и стажировка могут сочетаться. Форма договора и количество часов — разные признаки.
- **Нативный фильтр:** условие применяется самим источником. Фильтрация в браузере и фильтрация
  HTTP-ответа сервером различаются. Параметр, который сервер молча игнорирует, фильтром не считается.
- **Постфильтр:** условие проверяется у нас по полученным данным вакансии.
- **Список / карточка / текст:** структурированное поле списка; структурированное поле полной
  вакансии; сведения только в описании. Последний случай требует отдельного извлечения фактов.
- **Не подтверждено:** доказательств недостаточно; это не утверждение, что платформа не умеет это вообще.

Прочитан текущий код каждого адаптера. Возможности платформ сверены по официальной документации,
формам реальных карьерных досок и HTTP-запросам. Наличие поля в закрытом API не выдаётся за
анонимный поиск, а отсутствие переключателя на одной доске — за ограничение всей ATS.
Настраиваемые поля и значения принадлежат конкретному работодателю.

Числа ниже — снимок наблюдения, а не оценка качества всей ATS. Вакансии и настройки сайтов меняются.
Проверка не доказывает полноту всех вариантов оформления и всех работодателей платформы.

## Что сейчас означает запрос и результат у нас

В [модели запроса](../src/models/filters.py#L44) формат задаётся через `minimum_work_format`.
Это минимальная свобода формата: `office < hybrid < remote_country < remote_worldwide`.
Поэтому `office` допускает все известные форматы, `hybrid` допускает также удалёнку.
Точного запроса «только onsite» или «только hybrid» в этой модели нет. Это текущий контракт,
а не ошибка конкретного парсера.

`employment` — список допустимых значений `full_time`, `part_time`, `contract`, `internship`,
`volunteer`; совпадения объединяются через «или». В самой вакансии можно сохранить несколько типов.
Сейчас нет отдельных осей для часов, вида договора и стажировки.

[Общий постфильтр](../src/selection/filtering.py#L163) уже сравнивает нормализованные формат
и занятость, но не извлекает их из описания. Оба условия мягкие: несовпадение переводит итог
в `maybe`, а неизвестное значение само по себе не мешает итогу `accepted`.
`accepted` означает прохождение текущей политики отбора, а не подтверждение каждого условия.
Нужно читать статусы отдельных проверок `accepted / rejected / unknown`.
Например, для QA-вакансии без данных о занятости запрос `contract` даёт проверку `unknown`;
при отсутствии других препятствий итог остаётся `accepted`.

Способности источника декларируются через извлекаемые поля в
[filter_parameters](../src/parsers/interfaces.py#L165). Указание `employment` в объявлении
не доказывает, что каждое фактическое значение источника распознаётся.
[Общая подготовка ATS-запроса](../src/parsers/ats/common.py#L64) не переводит условия брифа
в нативные значения доски. Явно заданные параметры `Board.native_filters` — отдельный путь.

## Краткая таблица для раздельной работы по источникам

Задача таблицы — определить границы последующего разбора каждого источника.
**FT/PT/C/I/V** — полная/частичная/контрактная занятость, стажировка/волонтёрство.
**Tenant** — настройки доски конкретного работодателя. Здесь **N** — нативный
отбор, **L** — поле списка, **D** — поле полной карточки, **T** — факт только в тексте,
**?** — данных пока недостаточно. «Постфильтр» означает проверку уже извлечённого факта;
сам по себе он не читает описание. Столбец работ называет оставшийся маршрут, а не готовый план.
Общие зависимости: **модель** — смысл contract, независимость часов/договора/стажировки и minimum;
**порядок** — проверка после обогащения D; **контекст N** — доказательство точного серверного отбора.

| Источник | Фактически: нативный отбор и данные | Что отсутствует / нужный маршрут | WF и общие зависимости |
|---|---|---|---|
| HH | N формата и FT/PT/PROJECT есть; L/D дополнительно internship и условия договора | Сохранить internship из L/D → постфильтр; сначала решить смысл contract | WF-003, WF-004; модель, OR-ветви |
| Getmatch | N удалёнки есть; L форматы с географией/исключениями читаются; employment иногда только T | Формат уже проверяется по L; занятость извлекать из явного T либо оставлять ? | Нового WF нет; модель, извлечение T |
| Ashby | N формата/занятости в hosted UI; выбранная API отдаёт L и игнорирует проверенные фильтры | Постфильтр по L уже работает; неизвестный формат не считать onsite | WF-002 при выборе native; модель |
| BambooHR | Авторизованная API описывает поля; текущая публичная лента недоступна; исторические L имеют locationType | Сначала подтвердить публичный контракт; затем L → постфильтр. I/V пока ? | Открытый блокер проверки; модель |
| Breezy | N remote работает; L HTML/JSON содержит формат и type, часть фактов теряется | N remote либо L → постфильтр; декодировать type и различие unrestricted remote | WF-001, WF-005, WF-014; WF-002, модель |
| Comeet | N embedded-ленты не подтверждён; L явно даёт три формата и настраиваемую занятость | Постфильтр по L уже работает для известных меток; custom значения проверять отдельно | Нового WF нет; модель |
| Dreamjob | N условий не найден на двух досках; L remote; D содержит подписанные FT/PT | Извлечь D FT/PT → повторный постфильтр; hybrid/V в T проверять отдельно | WF-007; порядок, извлечение T |
| Greenhouse | Hosted N custom полей возможен; L metadata сообщает формат/занятость | Сопоставить опубликованную схему доски → L → постфильтр; сохранить конфликты | WF-008; WF-002, схема tenant |
| Huntflow | N и employment не установлены; L remote есть, но `.global` URL отвергаются | Сначала доступный адаптер; затем L remote → постфильтр; остальное ? | GEO-017; извлечение T только с доказательством |
| iCIMS | N Position Type tenant-specific; query URL сохраняется; L составные типы теряются | N через разрешённый транспорт; нормализовать L → постфильтр; modern UI отдельно | WF-001, WF-006; WF-002, схема tenant |
| JazzHR | N всех пяти типов формы есть; D JSON-LD FT/remote; handler отсутствует | N занятости либо загрузка D → постфильтр; формат по D | WF-001, WF-009; WF-002, порядок |
| Jobvite | N точных employer labels есть; неверная метка может вернуть всё; D JSON-LD FT | N после проверки valid/negative; либо D → постфильтр; формат из L/T частично | WF-001, WF-009; WF-002, WF-013 |
| JOIN | N этих условий не подтверждён; L формат читается; D уже восстанавливает FT | Сохранить маршрут L формата и D занятости → постфильтр; полнота остальных типов ? | Нового дефекта нет; порядок, модель |
| Lever | N commitment уже вручную есть; формат JSON не фильтрует; L обоих условий читаются | Постфильтр L уже работает; при выборе N нужен перевод меток доски | WF-002; схема tenant, OR-ветви |
| Personio | N employment UI настраивается; L часы читает; D уже восстанавливает PT+I/PT+C | Постфильтр после D либо обе оси L; onsite/volunteer не додумывать | Нового полного отказа нет; порядок, модель |
| Recruitee | N Work model в UI; API проверенные параметры игнорирует; L boolean/типы читаются | Постфильтр L уже работает; freelance/temporary — отдельное решение модели | WF-002 при выборе native; модель |
| SmartRecruiters | N трёх форматов есть; L remote/hybrid и employment читаются; onsite-контекст теряется | N либо L → постфильтр; onsite подтверждать полем/проверенным контекстом N | WF-001, WF-013; WF-002, контекст N |
| SuccessFactors | Новый RMK уже передаёт tenant facets; категория Student/Internship шире I; D FT/PT/формат | Извлечь подписанные D → постфильтр; сохранить широкую категорию без превращения в I | WF-012; WF-002, модель, порядок; XML отдельно |
| Taleo TBE | N формы уже есть; IDA L не повторяет выбранный Intern; D Non-Exempt иной оси | Сохранить проверенный контекст N либо извлечь явный D/T → постфильтр | WF-013; WF-002, модель; Enterprise вне scope |
| Teamtailor | N remote работает, employment UI настраивается; L селектор сломан; D FT есть | Восстановить L формата; N либо D занятости → постфильтр | WF-001, WF-009, WF-010; WF-002, порядок |
| Workable | N workplace/worktype есть; L форматы и FT/PT/C уже читаются | Постфильтр L работает; N пока недоступен в параметрах; I/V не выводить из Other | WF-001; WF-002, модель |
| Workday | N фасеты tenant есть; D optional remoteType, часы; NVIDIA D не даёт subtype/remoteType | N с проверенным смыслом либо явный L/D/T; без факта ?; FT/PT уже проверяются после D | WF-001, WF-011, WF-013; WF-002, порядок |
| Y Combinator | Company jobs N не подтверждён; L FT/C/I/remote есть; часть нагрузки/hybrid только T | Сохранить L → постфильтр; T извлекать отдельно, не менять тип по названию | Нового WF нет; модель, извлечение T |

Подробные платформенные ограничения и полнота пяти типов приведены в приложениях.
Ни один пункт таблицы не разрешает автоматически считать город офисным форматом,
Fixed Term контрактором или широкую студенческую категорию точной стажировкой.

## Журнал проблем: самостоятельные баг-репорты

Все пункты открыты. **Дефект** — воспроизведённая потеря или неверная интерпретация доступного факта;
**пробел** — отсутствующий маршрут передачи/извлечения; **семантическое ограничение** — решение
о смысле условия ещё необходимо. Код ниже исполняется из корня репозитория; синтетические примеры
помечены и не подменяют ссылки на реальные источники. Критерий приёмки описывает будущую проверку,
а не уже выполненное исправление. Общая мягкая политика и минимум свободы формата не объявляются багами.

### WF-001. Подтверждённые фильтры платформ недоступны в параметрах адаптеров

**Статус:** открыто. **Тип:** пробел реализации. **Приоритет:** средний.
**Затронуто:** `Board.native_filters`, `BoardSearch.__init__`, адаптеры Workable, Breezy, JazzHR, Jobvite, Teamtailor, SmartRecruiters, Workday, iCIMS.

**Суть.** Часть рабочих нативных условий нельзя выразить через текущую конфигурацию адаптера. Это отсутствие пути передачи; корректное отклонение неизвестного ключа само по себе не дефект валидации.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Доступ к внешним сайтам не нужен. В Workday строка probe лишь проверяет отсутствие ключа в интерфейсе; она не имитирует допустимое JSON-тело CXS.
Код читает текущую конфигурацию адаптеров; сеть не используется.

**Шаги воспроизведения.**

1. Вызвать конструктор каждого адаптера с именем подтверждённого фильтра:

```bash
.venv/bin/python - <<'PY'
from importlib import import_module
from src.parsers.ats.models import Board
cases = [
    ("workable", "https://apply.workable.com/entuitive/", "workplace", ["hybrid"]),
    ("workable", "https://apply.workable.com/entuitive/", "worktype", ["part"]),
    ("breezy", "https://sa-global.breezy.hr/", "remote", "hybrid"),
    ("jazzhr", "https://symend.applytojob.com/apply/jobs", "s_type", "1"),
    ("jobvite", "https://jobs.jobvite.com/liferay/jobs", "t", "Part-Time"),
    ("teamtailor", "https://career.teamtailor.com/jobs", "remote_status_id", "hybrid"),
    ("smartrecruiters", "https://careers.smartrecruiters.com/Nexthink", "locationType", "REMOTE"),
    ("workday", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "appliedFacets", "probe"),
    ("icims", "https://careers-starr.icims.com/jobs/search", "searchPositionType", "2049"),
]
for name, url, key, value in cases:
    create = import_module(f"src.parsers.ats.{name}.parser").create_adapter
    try:
        create(Board(url, name, native_filters={key: value}))
    except ValueError as error:
        print(name, key, str(error))
    else:
        print(name, key, "accepted")
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Все девять вариантов печатают `Unsupported native filters`. iCIMS сохраняет `searchPositionType`, если он заранее включён в query board URL, но не принимает его отдельным native_filters. У Workday build всегда формирует пустой appliedFacets.

**Ожидается.** Для выбранных подтверждённых контрактов должен существовать явный путь передачи параметров нужного типа. Допустимо сначала оставить постфильтр по полям L/D; отсутствие native при этом должно быть явным. Точный query employment Teamtailor пока неизвестен и в этот список не включён.

**Влияние.** Нельзя сузить загрузку доступным условием источника; при отсутствии извлечённых L/D фактов условие остаётся unknown. Нативный отбор не заменяет проверку точности собственного значения.

**Доказательства и код.** [Валидация ключей](../src/parsers/ats/common.py#L43), [Workday body](../src/parsers/ats/workday/parser.py#L59). Источники для независимой проверки: [Breezy remote](https://sa-global.breezy.hr/?remote=hybrid), [JazzHR s_type](https://symend.applytojob.com/apply/jobs?form_id=advanced_search_jobs&s_type=1), [Jobvite t](https://jobs.jobvite.com/liferay/search?t=Part-Time), [Teamtailor remote_status_id](https://career.teamtailor.com/jobs?remote_status_id=hybrid), [SmartRecruiters locationType](https://api.smartrecruiters.com/v1/companies/Nexthink/postings?limit=100&locationType=REMOTE), [Workable browser filters](https://apply.workable.com/api/v3/accounts/entuitive/jobs/filters), [Workday board](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite).

**Критерий приёмки.** Для каждого добавляемого интерфейса отдельно: valid/negative пробы показывают реальный отбор; исходное условие сохраняется при пагинации. Недействительные Jobvite/iCIMS значения, возвращающие всё, не объявляются точными фильтрами. Связи: WF-002 и WF-013.

### WF-002. Общий запрос ATS не переводится в нативные значения источника

**Статус:** открыто. **Тип:** пробел интеграции; выбор native или postfilter требует решения. **Приоритет:** средний.
**Затронуто:** `BoardSearch.prepare`, `BriefInput.filters`, Lever commitment; аналогичный маршрут tenant facets Taleo/RMK.

**Суть.** Ручной native уже может работать, но условие общего брифа не становится соответствующим параметром источника. Это не означает, что условие совсем не проверяется: объявленные факты проверяет общий постфильтр.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Достаточно локального кода. Пример использует реальную метку Field AI `Internship` и не подменяет её глобальным ID.
Код читает текущую конфигурацию адаптеров; сеть не используется.

**Шаги воспроизведения.**

1. Сравнить подготовку пустого и internship-брифа с отдельным ручным commitment:

```bash
.venv/bin/python - <<'PY'
import asyncio
from src.models.brief import BriefInput
from src.models.filters import SearchFilters
from src.parsers.ats.lever.parser import create_adapter
from src.parsers.ats.models import Board
async def no_fetch(url):
    raise AssertionError("Lever prepare should not fetch a form")
async def main():
    board = "https://jobs.lever.co/field-ai"
    source = create_adapter(Board(board, "Field AI"))
    for employment in ([], ["internship"]):
        brief = BriefInput(name="audit", queries=["QA"], filters=SearchFilters(
            employment=employment, minimum_work_format="hybrid"))
        plan = await source.search.prepare(brief, no_fetch)
        print(employment, plan.requests[0].url, plan.application["employment"])
    manual = create_adapter(Board(board, "Field AI", {"commitment": "Internship"}))
    print("manual", manual.search.build("").url)
asyncio.run(main())
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Оба общих брифа получают один и тот же URL без commitment; application.employment=`local`. Ручной Board.native_filters добавляет `commitment=Internship`. У minimum_work_format нет автоматического перевода в source-specific фильтр.

**Ожидается.** Должен быть явно выбран маршрут для источника: корректный перевод по его справочнику либо достаточное извлечение L/D и постфильтр. Минимум hybrid обязан сохранять remote-варианты, а FT или I — объединяться через OR, не превращаться в AND.

**Влияние.** Источник может загружаться шире необходимого. Если L/D покрывают условие, это дополнительная работа сети, а не ложное отсутствие поддержки источника.

**Доказательства и код.** [prepare](../src/parsers/ats/common.py#L64), [Lever commitment](../src/parsers/ats/lever/parser.py#L49), [рабочая нативная проба](https://api.lever.co/v0/postings/field-ai?mode=json&commitment=Internship).

**Критерий приёмки.** Для выбранного маршрута есть проверка сформированных запросов или локальных фактов. Нельзя выводить решение только из появления URL-параметра; minimum и OR-семантика сохраняются.

### WF-003. HH теряет независимый признак стажировки при FULL

**Статус:** открыто. **Тип:** дефект извлечения. **Приоритет:** высокий.
**Затронуто:** `HHSearch.parse`, `_normalize`, `HHVacancyDetails.parse`, общий employment-постфильтр.

**Суть.** HH может сообщить полную занятость и стажировку одновременно. Код читает employmentForm, но выбрасывает internship из списка source_data и не добавляет internship в нормализованные факты.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Синтетическая строка использует существенные поля живой вакансии 138502888; описание и работодатель для дефекта не нужны.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать одну HH SSR-строку FULL+internship=true и проверить запрос стажировки:

```bash
.venv/bin/python - <<'PY'
import json
from src.models.filters import SearchFilters
from src.parsers.aggregators.hh.parser import HHSearch
from src.parsers.interfaces import SearchRequest
from src.selection.filtering import evaluate
row = {"vacancyId": 138502888, "name": "QA Engineer", "employmentForm": "FULL", "internship": True}
state = {"vacancySearchResult": {"vacancies": [row], "totalResults": 1,
    "paging": {"next": {"disabled": True}}}}
body = '<template id="HH-Lux-InitialState">' + json.dumps(state) + '</template>'
v = HHSearch().parse(body, SearchRequest("https://hh.ru/search/vacancy?text=QA", "QA")).vacancies[0]
result = evaluate(v, SearchFilters(employment=["internship"]), queries=["QA"])
print(v.employment, "internship" in v.source_data)
print([(c.field, c.status) for c in result.checks], result.status)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Вывод: `["full_time"]`, `False` для сохранения internship; проверка employment=`rejected`, общий итог=`maybe`.

**Ожидается.** Сохранить исходный boolean и оба независимых факта `full_time`+`internship`. Запрос internship должен совпасть. Поле нельзя выводить лишь из названия.

**Влияние.** Подходящая стажировка получает отрицательную проверку. Мягкая продуктовая политика превращает итог в maybe; это отдельный существующий контракт, а не причина ошибки парсера.

**Доказательства и код.** [Нормализация](../src/parsers/aggregators/hh/parser.py#L100), [список source_data](../src/parsers/aggregators/hh/parser.py#L268), [вакансия](https://hh.ru/vacancy/138502888), [живой поиск стажировок](https://hh.ru/search/vacancy?text=QA&page=0&label=internship). При изменении live-вакансии механизм остаётся воспроизводимым на литерале.

**Критерий приёмки.** FULL+true даёт оба типа и accepted для I; PART+true сохраняет part_time+internship; отсутствие/false не создаёт I. Список и карточка согласованы. Нативный label=internship при OR FT/I не применяется как обязательный AND.

### WF-004. HH PROJECT не исчерпывает контрактное оформление

**Статус:** открыто. **Тип:** семантическое ограничение модели; не готовый маппинг-фикс. **Приоритет:** средний.
**Затронуто:** `SearchFilters.employment`, HH `_normalize`, `HHVacancyDetails.parse`, employment-постфильтр.

**Суть.** Сейчас contract означает PROJECT в HH. Другая вакансия может иметь FULL и одновременно допустимые гражданско-правовые договоры. До определения смысла contract нельзя оценивать её как заведомо неподходящую контрактному запросу.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Литерал повторяет существенные поля 138598704. Никакого предположения, что все виды срочного/гражданского договора равны contractor, не делается.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать HH-карточку FULL с civilLawContracts и оценить нынешний contract:

```bash
.venv/bin/python - <<'PY'
import json
from src.models.vacancy import Vacancy
from src.models.filters import SearchFilters
from src.parsers.aggregators.hh.parser import HHVacancyDetails
from src.selection.filtering import evaluate
raw = {"vacancyId": 138598704, "name": "QA Engineer", "employmentForm": "FULL",
    "description": "<p>QA automation</p>",
    "civilLawContracts": [{"civilLawContractsElement": ["SELF_EMPLOYED", "INDIVIDUAL_ENTREPRENEUR", "INDIVIDUAL_PERSON"]}]}
body = '<template id="HH-Lux-InitialState">' + json.dumps({"vacancyView": raw}) + '</template>'
v = Vacancy(id="138598704", title="QA Engineer", url="https://hh.ru/vacancy/138598704")
v = HHVacancyDetails().parse(body, v)
result = evaluate(v, SearchFilters(employment=["contract"]), queries=["QA"])
print(v.source_data["civilLawContracts"], v.employment)
print([(c.field, c.status) for c in result.checks], result.status)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Условия договора остаются в source_data, но employment=`[full_time]`; contract-проверка rejected, итог maybe. Нынешний PROJECT→contract покрывает другое значение.

**Ожидается.** Сначала определить, означает contract проектную работу, форму оформления или отдельную ось. Затем зафиксировать ожидаемые ответы на FULL+ГПХ, PROJECT, Fixed Term и смешанные случаи. Автоматически добавлять contract любому FULL нельзя.

**Влияние.** Один фильтр объединяет часы и форму договора; результат нельзя трактовать как надёжное отрицание контрактного оформления.

**Доказательства и код.** [Текущий mapping](../src/parsers/aggregators/hh/parser.py#L100), [сохранение договоров в details](../src/parsers/aggregators/hh/parser.py#L339), [вакансия 138598704](https://hh.ru/vacancy/138598704), [модель пяти типов](../src/models/filters.py#L11).

**Критерий приёмки.** Есть явное решение о смысле contract и проверяемые примеры обеих осей. Реализация соответствует решению, сохраняет исходные договоры и не считает permanent/Fixed Term синонимами нагрузки или contractor.

### WF-005. Breezy декодирует лишь один служебный token занятости

**Статус:** открыто. **Тип:** дефект извлечения. **Приоритет:** высокий.
**Затронуто:** `BreezySearch.parse`, общий employment mapper.

**Суть.** HTML передаёт тип через служебную метку. Код заменяет FULL_TIME, но CONTRACT оставляет нераспознанной строкой, хотя источник явно сообщает контрактную занятость.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Минимальная HTML-карточка повторяет селекторы и CONTRACT-token четырёх живых sa.global позиций.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать одинаковую карточку с контрольным FULL_TIME и с CONTRACT:

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.breezy.parser import create_adapter
from src.parsers.ats.models import Board
source = create_adapter(Board("https://sa-global.breezy.hr/", "sa.global"))
for token in ("FULL_TIME", "CONTRACT"):
    body = f'''<li class="position"><a href="/p/34db9c359b3201-contract"><h2>QA Engineer</h2></a>
    <ul class="meta"><li class="type"><span>%LABEL_POSITION_TYPE_{token}%</span></li></ul></li>'''
    v = source.search.parse(body, source.search.build("")).vacancies[0]
    print(token, v.source_data["employment_type"], v.employment)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** FULL_TIME→`[full_time]`; CONTRACT остаётся `%LABEL_POSITION_TYPE_CONTRACT%` и даёт employment=[]. В live-проверке потеряны четыре уникальные контрактные позиции.

**Ожидается.** CONTRACT должен дать contract. Поддерживаемые type.id/служебные значения разбираются по контракту источника, сохраняя неизвестные значения как unknown.

**Влияние.** Контрактная вакансия не может подтвердить соответствующее условие постфильтра, хотя факт доступен уже в списке.

**Доказательства и код.** [Разбор type](../src/parsers/ats/breezy/parser.py#L41), [конкретная позиция](https://sa-global.breezy.hr/p/34db9c359b3201-d365-f-o-finance-functional-consultant-contract), [public JSON type.id](https://sa-global.breezy.hr/json), [документированный type enum](https://developer.breezy.hr/reference/createposition).

**Критерий приёмки.** Контроль FT и явный Contract распознаются; PT-token проверяется отдельным примером, поскольку live PT здесь не встречен. Other/experience=internship не превращаются в I/V без факта. Одинаковые URL не удваивают оценку числа потерянных вакансий.

### WF-006. iCIMS теряет составные Position Type работодателя

**Статус:** открыто. **Тип:** дефект нормализации. **Приоритет:** высокий.
**Затронуто:** `IcimsSearch.parse`, `ats.normalization.employment`.

**Суть.** Три специальных alias не покрывают опубликованные составные типы. Метка Permanent Full-Time остаётся в metadata, но факт полной занятости теряется.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Синтетический HTML использует фактическую структуру iCIMS JobCard и метки Starr. Contract Part-Time — дополнительный пример объявленного типа, а не доказательство live-числа.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Сравнить существующий alias Regular Full-Time с Permanent Full-Time и Contract Part-Time:

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.icims.parser import create_adapter
from src.parsers.ats.models import Board
source = create_adapter(Board("https://careers-starr.icims.com/jobs/search", "Starr"))
for label in ("Regular Full-Time", "Permanent Full-Time", "Contract Part-Time"):
    body = f'''<ul class="iCIMS_JobsTable"><li class="iCIMS_JobCardItem">
    <a class="iCIMS_Anchor" href="/jobs/2496/talent-manager/job"><h3>QA Engineer</h3></a>
    <dl><dt class="iCIMS_JobHeaderField">Position Type</dt><dd>{label}</dd></dl>
    </li></ul>'''
    v = source.search.parse(body, source.search.build("")).vacancies[0]
    print(v.source_data["metadata"]["position type"], v.employment)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Regular Full-Time→full_time; Permanent Full-Time→[]; Contract Part-Time→[]. Все 17 текущих Starr-строк с Permanent Full-Time при аудите дали пустую занятость.

**Ожидается.** Сопоставлять объявленные значения конкретной доски, сохраняя независимо часы и контрактный/стажёрский признак. Permanent сам по себе не доказывает full_time.

**Влияние.** Полная занятость по явной составной метке становится unknown, даже когда metadata уже доступна общему парсеру.

**Доказательства и код.** [Mapping](../src/parsers/ats/icims/parser.py#L61), [Starr с Position Type](https://careers-starr.icims.com/jobs/search?in_iframe=1&ss=1&searchPositionType=2049), [дополнительная detail FULL_TIME](https://careers-starr.icims.com/jobs/2496/talent-manager/job?in_iframe=1).

**Критерий приёмки.** Permanent Full-Time даёт full_time; согласованный Contract Part-Time сохраняет оба признака; неполная метка Permanent не создаёт часы. Tenant ID 2049/2052 и чужие IDs не становятся глобальным справочником.

### WF-007. Dreamjob игнорирует подписанное HTML-поле занятости карточки

**Статус:** открыто. **Тип:** дефект извлечения. **Приоритет:** высокий.
**Затронуто:** `dreamjob.Details.parse`, shared JSON-LD enrichment.

**Суть.** Полная карточка явно сообщает Полная/Частичная в HTML, но обработчик читает только JSON-LD. Если employmentType в JSON-LD отсутствует, тип остаётся пустым.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Синтетический JSON-LD намеренно не содержит employmentType, как две проверенные карточки; HTML содержит отдельное подписанное значение основной вакансии.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Загрузить литерал карточки через действующий Details.parse для Полная и Частичная:

```bash
.venv/bin/python - <<'PY'
import json
from src.models.vacancy import Vacancy
from src.parsers.ats.dreamjob.parser import Details
for uid, label in (("138722188", "Полная"), ("138052865", "Частичная")):
    posting = {"@context": "https://schema.org", "@type": "JobPosting",
        "title": "QA Engineer", "description": "<p>QA automation</p>"}
    body = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
    body += f'<main><div class="job-vacancy__info">{label}</div></main>'
    v = Vacancy(id=uid, title="QA Engineer", url=f"https://dreamjob.ru/employers/32100/vakansii/{uid}")
    v = Details().parse(body, v)
    print(label, v.employment, v.description)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** В обоих случаях description извлечён, employment=[]. Получение описания само не выполняет извлечение HTML-поля.

**Ожидается.** По явному полю основной вакансии Полная→full_time, Частичная→part_time; после обогащения условие проверяется повторно. Соседние рекомендации не считаются фактами вакансии.

**Влияние.** Данные доступны на D-этапе, но постфильтр их не получает. Произвольный текст «Подработка» не компенсирует эту потерю и не доказывает V.

**Доказательства и код.** [Details](../src/parsers/ats/dreamjob/parser.py#L44), [Полная](https://dreamjob.ru/employers/32100/vakansii/138722188), [Частичная](https://dreamjob.ru/employers/127990/vakansii/138052865).

**Критерий приёмки.** Две реальные формы D без JSON-LD employmentType дают верные FT/PT; отсутствующее поле остаётся unknown. EmploymentType JSON-LD по-прежнему работает; данные рекомендаций не влияют на основную вакансию. Повторный постфильтр запускается после D.

### WF-008. Greenhouse игнорирует опубликованную metadata формата и занятости

**Статус:** открыто. **Тип:** дефект извлечения. **Приоритет:** высокий.
**Затронуто:** `GreenhouseSearch.parse`, `Details.parse`, обработка custom metadata.

**Суть.** Ответ уже содержит явные пользовательские поля работодателя. Код ищет remote/hybrid только в строке места, а занятость вообще не извлекает; metadata остаётся сырой.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Минимальные строки повторяют DEPT Employment Type=Intern и Anthropic Location Type=Remote. Числовой id поля сокращён до 1; он не используется текущим парсером и не является предлагаемым глобальным ID.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать по одной строке двух досок:

```bash
.venv/bin/python - <<'PY'
import json
from src.parsers.ats.greenhouse.parser import create_adapter
from src.parsers.ats.models import Board
cases = [("dept", 8195418, "Employment Type", "Intern", "Amsterdam"),
    ("anthropic", 5387009008, "Location Type", "Remote", "San Francisco, CA | New York City, NY | Seattle, WA")]
for board, uid, name, value, location in cases:
    source = create_adapter(Board(f"https://job-boards.greenhouse.io/{board}", board))
    row = {"id": uid, "title": "QA Engineer", "absolute_url": f"https://job-boards.greenhouse.io/{board}/jobs/{uid}",
        "location": {"name": location}, "metadata": [{"id": 1, "name": name, "value": value, "value_type": "single_select"}]}
    v = source.search.parse(json.dumps({"jobs": [row]}), source.search.build("")).vacancies[0]
    print(board, v.source_data["metadata"], v.employment, v.work_formats)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** В обоих source_data.metadata есть исходная метка. DEPT employment=[], Anthropic work_formats=[]. Простое получение content через нынешний Details не добавляет извлечение этих полей.

**Ожидается.** По опубликованной/явно заданной схеме конкретной доски извлекать Intern и Remote для постфильтра. Сохранять конфликт со строкой location, а не глобально назначать приоритет любому полю с похожим именем.

**Влияние.** Подходящие стажировки и удалённые роли не подтверждаются; слово Remote-Friendly в location может расходиться с metadata и требовать отдельного разбора конфликта.

**Доказательства и код.** [Код](../src/parsers/ats/greenhouse/parser.py#L129), [DEPT Intern8195418](https://job-boards.greenhouse.io/dept/jobs/8195418), [Anthropic5387009008](https://job-boards.greenhouse.io/anthropic/jobs/5387009008), [DEPT API](https://boards-api.greenhouse.io/v1/boards/dept/jobs?content=true), [Anthropic API](https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true).

**Критерий приёмки.** При подтверждённой схеме два примера дают internship и remote_country. Неизвестное поле и отсутствующее значение не создают факт; Indefinite/Fixed term не превращаются в FT/PT/C. Проверяются schema variation, multi-select и конфликт metadata/location.

### WF-009. JazzHR, Jobvite и Teamtailor не используют занятость публичной карточки

**Статус:** открыто. **Тип:** пробел извлечения. **Приоритет:** средний.
**Затронуто:** `jazzhr`, `jobvite`, `teamtailor` create_adapter; engine detail-enrichment; shared JSON-LD.

**Суть.** Публичная D-страница имеет структурированный employmentType, но у трёх адаптеров нет vacancy_details. Отсутствие поля в списке ошибочно ограничивает доступный локальный маршрут проверки.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Нужен интернет для трёх публичных GET. Ключи не нужны; HTTP-ошибка или отсутствие JSON-LD после изменения страницы не означает, что тип отсутствовал в снимке аудита.
Код читает текущую конфигурацию адаптеров и публичные карточки; изменившаяся страница или HTTP-ошибка означает ограничение повторной проверки.

**Шаги воспроизведения.**

1. Для каждой реальной D-страницы сравнить JSON-LD с подключённым detail-handler:

```bash
.venv/bin/python - <<'PY'
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import httpx
from src.parsers.ats.jsonld import job_postings
from src.parsers.ats.models import Board
cases = [
    ("jazzhr", "https://symend.applytojob.com/apply/jobs", "https://symend.applytojob.com/apply/jobs/details/Xgn0DsdwR1"),
    ("jobvite", "https://jobs.jobvite.com/liferay/jobs", "https://jobs.jobvite.com/liferay/job/oOVaAfwd"),
    ("teamtailor", "https://career.teamtailor.com/jobs", "https://career.teamtailor.com/jobs/8118064-uk-account-executive-smb"),
]
def inspect(case):
    name, board, url = case
    source = import_module(f"src.parsers.ats.{name}.parser").create_adapter(Board(board, name))
    response = httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    facts = [p.get("employmentType") for p in job_postings(response.text)]
    print(name, "detail handler:", source.vacancy_details,
          "employment declared:", "employment" in source.search.fields, "JSON-LD:", facts)
with ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(inspect, cases))
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Все три печатают detail handler=None, employment declared=False; карточки дают FULL_TIME/FULL_TIME/Full-Time. Встреченный в JSON-LD факт не проходит через адаптер в Vacancy.employment.

**Ожидается.** Подключить подходящий D-разбор и запускать постфильтр после обогащения, либо использовать отдельно подтверждённый достаточный N-отбор. Personio/JOIN уже имеют работающий D-маршрут; к этой проблеме они не относятся.

**Влияние.** Условие занятости не подтверждается по существующим публичным данным. Для JazzHR карточка также даёт TELECOMMUTE.

**Доказательства и код.** [JazzHR](../src/parsers/ats/jazzhr/parser.py), [Jobvite](../src/parsers/ats/jobvite/parser.py), [Teamtailor](../src/parsers/ats/teamtailor/parser.py), [shared JSON-LD](../src/parsers/ats/jsonld.py#L47). Три исходных URL полностью указаны в коде воспроизведения.

**Критерий приёмки.** Каждая из трёх D-форм проходит через свой адаптер до нормализованного full_time и повторной проверки. Пустой/ошибочный JSON-LD не даёт ложный FT. Нативные ветви сохраняют отдельное доказательство точности; не обещается универсальное наличие всех пяти типов.

### WF-010. Teamtailor теряет формат в текущем HTML-шаблоне списка

**Статус:** открыто. **Тип:** дефект селектора. **Приоритет:** высокий.
**Затронуто:** `TeamtailorSearch.parse`.

**Суть.** Парсер ищет metadata в div.mt-1. Текущий шаблон использует div.text-md.mt-4, поэтому явная метка Hybrid не читается.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Два синтетических варианта различаются только классами контейнера и повторяют прежнюю/текущую структуру карточки.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать оба варианта одним текущим адаптером:

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.models import Board
from src.parsers.ats.teamtailor.parser import create_adapter
source = create_adapter(Board("https://career.teamtailor.com/jobs", "Teamtailor"))
for css in ("mt-1", "text-md mt-4"):
    body = f'''<ul><li><a href="/jobs/8118064-uk-account-executive-smb"><span title="QA Engineer">QA Engineer</span></a>
    <div class="{css}"><span>London</span><span>Hybrid</span></div></li></ul>'''
    v = source.search.parse(body, source.search.build("")).vacancies[0]
    print(css, v.work_formats)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** mt-1→hybrid, text-md mt-4→[]. В live-аудите пустой формат у 14/14 карточек; native hybrid отбирал 11 из них.

**Ожидается.** Явный формат должен извлекаться из обоих поддерживаемых шаблонов, без определения onsite по отсутствию remote.

**Влияние.** Доступный в L факт теряется ещё до постфильтра. D-путь сейчас также не подключён, но это отдельный WF-009.

**Доказательства и код.** [Селектор](../src/parsers/ats/teamtailor/parser.py#L47), [исходная доска](https://career.teamtailor.com/jobs), [native hybrid](https://career.teamtailor.com/jobs?remote_status_id=hybrid). Совпадает с GEO-016; не учитывать исправление дважды.

**Критерий приёмки.** Старый и текущий шаблоны дают одинаковый hybrid; отдельные remote/onsite метки читаются по своему контракту, пустой формат остаётся unknown. Дедупликация ссылок и пагинация сохраняются.

### WF-011. Workday не передаёт фасеты и не всегда получает заменяющий факт в деталях

**Статус:** открыто. **Тип:** пробел покрытия. **Приоритет:** средний.
**Затронуто:** `WorkdaySearch._request`, `WorkdayDetails.parse`, контекст CXS-фасетов.

**Суть.** Запрос всегда использует appliedFacets={}. На NVIDIA карточка выбранной стажировки не повторяет workerSubType, а remote-карточка не содержит remoteType. Поддержка optional поля в коде не гарантирует данные на всех tenant.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Литералы сокращают реальные NVIDIA D-ответы, сохраняя отсутствие двух optional полей. QA_ slug и QA title синтетические; из слова Intern или Remote новый факт автоматически не выводится.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Вывести текущий request body и разобрать два D-ответа без optional полей:

```bash
.venv/bin/python - <<'PY'
import json
from src.models.vacancy import Vacancy
from src.parsers.ats.models import Board
from src.parsers.ats.workday.parser import create_adapter
base = "https://nvidia.wd5.myworkdayjobs.com"
source = create_adapter(Board(base + "/NVIDIAExternalCareerSite", "NVIDIA"))
print("request", source.search.build("").json_body)
for uid, title, location in (("JR2026546", "QA Intern", "China, Shanghai"),
    ("JR2024283", "QA Engineer", "UK, Remote")):
    slug = "QA_" + uid
    v = Vacancy(id=uid, title=title, url=base + "/NVIDIAExternalCareerSite/job/Test/" + slug)
    info = {"jobPostingId": slug, "title": title, "location": location,
        "jobDescription": "<p>QA automation</p>", "timeType": "Full time"}
    v = source.vacancy_details.parse(json.dumps({"jobPostingInfo": info}), v)
    print(uid, v.employment, v.work_formats, "remoteType" in v.source_data["detail"],
          "workerSubType" in v.source_data["detail"])
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** appliedFacets пуст; оба результата содержат full_time без internship, work_formats=[]; исходные remoteType/workerSubType отсутствуют. Это не сбой чтения существующего remoteType.

**Ожидается.** Предоставить путь к реальным фасетам доски или извлечь иной явный факт L/D/T с доказательством. Когда поля/надёжного контекста нет, сохранять unknown. Office-фасет не объявлять onsite без проверки смысла.

**Влияние.** Нельзя подтвердить I/remote по одному успешному D fetch, хотя сервер умеет отдельный отбор. FT/PT при этом уже читаются после D.

**Доказательства и код.** [Запрос](../src/parsers/ats/workday/parser.py#L59), [detail](../src/parsers/ats/workday/parser.py#L161), [NVIDIA](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite), реальные [Intern D](https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/China-Shanghai/GPU-Computing-Software-Engineering-Intern--Autonomous-Driving---2027_JR2026546) и [Remote D](https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/UK-Remote/Project-Technical-Delivery-Manager_JR2024283).

**Критерий приёмки.** Фасеты получаются из текущего tenant и проверяются valid/negative пробами; unknown не становится отрицанием. Существующее remoteType на другом tenant читается по-прежнему. FT/PT не теряются; отдельный subtype добавляется лишь при явном подтверждении. Связи: WF-001/002/013.

### WF-012. SuccessFactors RMK не извлекает доступные факты полной карточки

**Статус:** открыто. **Тип:** пробел извлечения. **Приоритет:** средний.
**Затронуто:** `RecruitingMarketingSearch._vacancy`, RMK create_adapter, отсутствующий vacancy_details.

**Суть.** Ручные фасеты уже передаются. Список сохраняет широкую категорию Student/Internship только как исходные данные; карточка сообщает точные Part Time и On-site, но этап её разбора не подключён.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Литерал сокращает строку RMK 51689. Student/Internship — категория с apprentice/alternance, поэтому ожидаемого internship для всего этого входа нет.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать RMK-строку и независимо вывести тело ручного запроса:

```bash
.venv/bin/python - <<'PY'
import json
from src.parsers.ats.models import Board
from src.parsers.ats.successfactors.parser import create_adapter
source = create_adapter(Board("https://jobs.danfoss.com/search/", "Danfoss", {"locale": "en_GB"}))
row = {"response": {"id": "51689", "unifiedUrlTitle": "Queretaro-Finance-Intern",
    "unifiedStandardTitle": "Finance Intern (Credit Management)", "jobLocationShort": ["Queretaro, MX"],
    "filter3": ["Student/Internship"]}}
request = source.search.build("")
v = source.search.parse(json.dumps({"totalJobs": 1, "jobSearchResult": [row]}), request).vacancies[0]
print(v.source_data["filter3"], v.employment, v.work_formats, source.vacancy_details)
manual = create_adapter(Board("https://jobs.danfoss.com/search/", "Danfoss",
    {"locale": "en_GB", "cust_WorkLocationType": ["On-site"], "filter3": ["Student/Internship"]}))
print(manual.search.build("").json_body["facetFilters"])
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Сохранена `[Student/Internship]`, employment=[] и work_formats=[], handler=None. Второй вывод содержит оба настроенных facetFilters: транспорт N работает.

**Ожидается.** Сохранять широкую исходную категорию без приписывания точной I. Из подписанных D-полей конкретной карточки извлекать part_time и office и затем проверять локально, если подходящий точный N не использован.

**Влияние.** Нет локальной проверки FT/PT и формата по уже опубликованным D-данным. Превращение любой Student/Internship в I создало бы ложноположительные результаты.

**Доказательства и код.** [RMK-парсер](../src/parsers/ats/successfactors/parser.py#L237), [карточка51689](https://jobs.danfoss.com/job/Queretaro-Finance-Intern-%28-Credit-Management%29/51689-en_GB/): Employment Type:Part Time, Work Location Type:On-site, Job Experience Level:Student/Internship. [Danfoss search](https://jobs.danfoss.com/search/) объявляет tenant facets; filter3 не универсальный employment key.

**Критерий приёмки.** D51689 даёт part_time/office; широкие категории остаются широкими. Apprentice/alternance49357/51500 не становятся I только по filter3. Ручные JSON-фасеты, locale, пагинация и отдельный legacy XML путь не меняют смысл.

### WF-013. Контекст проверенного точного нативного отбора теряется при локальной проверке

**Статус:** открыто. **Тип:** пробел модели доказательств. **Приоритет:** средний.
**Затронуто:** `SearchRequest`/`SearchPlan`, source-specific parse, `Vacancy`, общий постфильтр; Taleo IDA, SmartRecruiters, Workday.

**Суть.** Источник может точно отобрать тип, но не повторить его в строке списка. Нынешний путь не передаёт уже проверенный контекст такого отбора локальной проверке. Сам факт отправки параметра ещё не доказывает, что отбор сработал.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Синтетическая форма повторяет IDA CUSTOM_1066=846 (Intern), а ответ не содержит employment. Она проверяет механизм передачи/потери контекста, не доказывает серверную точность. Live-точность устанавливается отдельно valid/negative пробами.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Подготовить запрос с полем формы, разобрать минимальный ответ и выполнить постфильтр:

```bash
.venv/bin/python - <<'PY'
import asyncio
from src.models.brief import BriefInput
from src.models.filters import SearchFilters
from src.parsers.ats.models import Board
from src.parsers.ats.taleo.parser import create_adapter
from src.selection.filtering import evaluate
url = "https://phh.tbe.taleo.net/phh01/ats/careers/v2/searchResults?org=INSTITUTEDA&cws=39"
source = create_adapter(Board(url, "IDA", {"CUSTOM_1066": "846"}))
form = f'''<form id="TBE_theForm" method="post" action="{url}">
<input type="hidden" name="org" value="INSTITUTEDA"><input type="hidden" name="cws" value="39">
<input type="hidden" name="act" value="search">
<select name="CUSTOM_1066"><option value="">All</option><option value="846">Intern</option></select></form>'''
async def fetch_form(address):
    return form
plan = asyncio.run(source.search.prepare(BriefInput(name="audit"), fetch_form))
request = plan.requests[0]
body = '''<div class="oracletaleocwsv2-panel-number">1</div>
<div class="oracletaleocwsv2-accordion-head-info"><div><a class="viewJobLink" href="jobDetail?org=INSTITUTEDA&amp;cws=39&amp;rid=3744">QA Engineer</a></div></div>'''
v = source.search.parse(body, request).vacancies[0]
result = evaluate(v, SearchFilters(employment=["internship"]), queries=["QA"])
print(request.method, request.form_body["CUSTOM_1066"], v.employment)
print([(c.field, c.status) for c in result.checks], result.status)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** POST сохраняет 846, Vacancy.employment=[], проверка employment=unknown, общий итог accepted. Существующий итог accepted не означает, что internship подтверждён.

**Ожидается.** Хранить различие между доказанным точным N-отбором, широкой категорией и неприменённым условием. Только первый может подтвердить условие своей ветви; иначе нужны L/D/T-факты либо unknown. Не добавлять тип по одному совпавшему query string.

**Влияние.** Подтверждённый сервером факт становится unknown; либо при наивном исправлении возникнут ложные гарантии для игнорируемых параметров и широких категорий.

**Доказательства и код.** [Taleo prepare/parse](../src/parsers/ats/taleo/parser.py#L50), [IDA форма](https://phh.tbe.taleo.net/phh01/ats/careers/v2/jobSearch?org=INSTITUTEDA&cws=39): при аудите43→35 для844/3 для931/2 для846/0 для неизвестного ID. [SmartRecruiters ONSITE](https://api.smartrecruiters.com/v1/companies/Nexthink/postings?limit=100&locationType=ONSITE) и NVIDIA Intern-фасет — аналогичные случаи. Связь с GEO-012.

**Критерий приёмки.** Точные ветви подтверждаются после valid/negative проверки и сохраняют provenance при пагинации/объединении; Student/Internship не подтверждает I. Неверные Jobvite labels/iCIMS IDs, возвращающие всё, не подтверждают тип. OR-ветви не переносят доказательство на соседние результаты.

### WF-014. Breezy схлопывает явную неограниченную удалёнку в remote_country

**Статус:** открыто. **Тип:** дефект потери точности. **Приоритет:** средний.
**Затронуто:** `BreezySearch.parse`, `work_formats`, локальная проверка minimum_work_format.

**Суть.** Источник различает полностью удалённую работу без ограничений места и remote в выбранных локациях. Поиск слова remote в HTML превращает обе категории в remote_country.

**Предусловия.** Запуск из корня репозитория с установленной `.venv`. Синтетические метки воспроизводят REMOTE_ANY и ограниченный remote-вариант; доступное unrestricted подтверждение для живой позиции приведено ниже.
Минимальный **синтетический** вход повторяет форму опубликованных данных; он воспроизводит механизм, а не историческое число вакансий.

**Шаги воспроизведения.**

1. Разобрать карточку с каждой из двух явных remote-меток:

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.breezy.parser import create_adapter
from src.parsers.ats.models import Board
source = create_adapter(Board("https://sa-global.breezy.hr/", "sa.global"))
for token in ("REMOTE_ANY", "REMOTE_LOCATION"):
    body = f'''<li class="position"><a href="/p/735374fd397501-audit"><h2>QA Engineer</h2></a>
    <ul class="meta"><li class="location"><span>%LABEL_POSITION_TYPE_{token}%</span></li>
    <li class="type"><span>%LABEL_POSITION_TYPE_FULL_TIME%</span></li></ul></li>'''
    v = source.search.parse(body, source.search.build("")).vacancies[0]
    print(token, v.work_formats)
PY
```

2. Сопоставить вывод с фактическим и ожидаемым результатом ниже.

**Фактически.** Оба варианта дают `[remote_country]`. Для minimum remote_worldwide явный unrestricted вариант не подтверждает условие.

**Ожидается.** Сохранять объявленное отсутствие географических ограничений как remote_worldwide, ограниченный remote — отдельно. Обычный remote без явных сведений остаётся remote_country/неуточнённым.

**Влияние.** Теряется опубликованная точность, необходимая для корректного сравнения с minimum. Сам порядок `office < hybrid < remote_country < remote_worldwide` намеренный; проблема в потерянном факте.

**Доказательства и код.** [Разбор metadata](../src/parsers/ats/breezy/parser.py#L45), [Global Head of Revenue Operations](https://sa-global.breezy.hr/p/735374fd397501-global-head-of-revenue-operations-commercial-excellence), [public JSON](https://sa-global.breezy.hr/json): remote_details.value=remote и label Fully remote, no location restrictions; remote-location имеет другое значение.

**Критерий приёмки.** Явный REMOTE_ANY/unrestricted даёт worldwide, remote-location сохраняет ограничение, plain remote не повышается автоматически. Формат и WorkLocation согласованы; source evidence сохраняется.

## Как выбирать нативную и локальную проверку

1. Если есть подходящий нативный фильтр, получить его реальные значения от доски и применить
   с учётом смысла запроса. Например, минимум hybrid у нас включает remote, а не только hybrid.
2. Если нативный фильтр отсутствует или шире условия, использовать структурированные поля списка.
   Это уже рабочий путь для Ashby, Comeet, Lever, Recruitee и занятости SmartRecruiters.
3. Если нужный факт есть только в карточке, загрузить её и повторить общий постфильтр.
   Это уже реализовано для ряда типов Personio/JOIN, но ещё требуется JazzHR/Jobvite/Teamtailor.
4. Если факт есть только в тексте, нужен отдельный этап извлечения с исходной цитатой/полем.
   Одно получение description ничего не меняет: текущий employment-постфильтр текст не анализирует.
5. Если надёжного факта нет, сохранить unknown. Адрес не доказывает onsite;
   permanent не доказывает fulltime; временный договор не доказывает contractor;
   remote не доказывает worldwide.

Общий JSON-LD обработчик уже читает employmentType и TELECOMMUTE.
[Google JobPosting](https://developers.google.com/search/docs/appearance/structured-data/job-posting)
допускает несколько типов занятости одновременно и отделяет TELECOMMUTE от географии доступности.
[Schema.org employmentType](https://schema.org/employmentType) имеет текстовый тип:
наличие JSON-LD не гарантирует строгое пятизначное перечисление у каждого сайта.
Поэтому нельзя дополнять единый словарь случайными словами работодателя и объявлять задачу решённой.

## Приложение А. Подробные матрицы возможностей

### Формат работы: все 21 ATS

Таблица разделяет фильтр платформы и факты, доступные без такого фильтра.
«Нет» в колонке нашего запроса означает отсутствие реализации, а не ограничение платформы.
Последняя колонка прямо указывает, откуда брать данные для постфильтрации.
Доказательства и ограничения каждого вывода — в одноимённом разделе ниже.

| ATS | Фильтр у платформы | Наш нативный запрос | Факты и текущий разбор → путь проверки |
|---|---|---|---|
| Ashby | Hosted Location Type; параметры публичной posting-ленты не фильтруют проверенную выдачу | Нет | Структурированный список `workplaceType`: все три читаются. **Постфильтр уже возможен по списку** |
| BambooHR | Keyed API имеет три формата; текущий анонимный поиск не подтверждён | Нет | `isRemote`/remote в label читаются. `locationType` сохранённых ответов игнорируется. Для трёх форматов нужен подтверждённый публичный контракт; пока частичная постпроверка |
| Breezy | Серверный `remote`: hybrid / remote / remote-location | Нет; только location | HTML даёт hybrid/remote; JSON даёт явные ограничения удалёнки. **Постфильтр частично работает**, но различие двух remote теряется; JSON не используется |
| Comeet | Нативный фильтр публичного embedded-списка не подтверждён | Нет | `workplace_type` и legacy remote: все три читаются. **Постфильтр уже возможен по списку** |
| Dreamjob | На двух досках отдельный фильтр не найден; вся платформа не ограничена этим наблюдением | Нет | Remote из тегов списка и JSON-LD карточки. Description уже загружается, но hybrid/onsite из текста не извлекаются: **нужно извлечение факта → постфильтр** |
| Greenhouse | Настраиваемые поля и до пяти custom filters; стандартное поле формата API не гарантирует | Нет | Public metadata может явно сообщать формат; мы её игнорируем, ищем слова в location. **Нужно сопоставление объявленного поля доски → постфильтр** |
| Huntflow | На двух досках не найден; универсальный контракт не установлен | Нет | Remote в строке списка, читается при допустимом адресе. Проверенные `.global` блокирует конструктор. **Постфильтр требует сначала доступного адаптера**, hybrid/onsite неизвестны |
| iCIMS | Зависит от Classic/modern и настроек работодателя; modern UI показывает Remote/Hybrid | Нет; query Classic URL сохраняется | Classic читает точный `workplace` из списка; составные подписи не распознаёт. **Нужен разбор явного поля → постфильтр**; modern UI вне текущего адаптера |
| JazzHR | Отдельный переключатель формата на двух досках не найден | Нет | Remote/hybrid ищутся в Location; карточка имеет JSON-LD TELECOMMUTE. **Для карточки нужны загрузка и постфильтр**, details сейчас отсутствует |
| Jobvite | Отдельный универсальный фильтр не установлен; формат может быть частью Location/Region работодателя | Нет | Remote/hybrid из строки списка; onsite и сведения только в описании не извлекаются. **Нужен постфильтр по явно извлечённому факту** |
| JOIN | Поля On-site/Hybrid/Remote есть; публичный фильтр company search не подтверждён | Нет | Структурированный `workplaceType` читается. **Постфильтр уже возможен по списку**, ограничения remote проверяются отдельно |
| Lever | Hosted HTML фильтрует все три; public JSON workplaceType не фильтрует | Нет для выбранного JSON API | Структурированный `workplaceType` читается. **Постфильтр уже возможен по списку**, не надо передавать HTML-параметр в JSON API |
| Personio | Отдельный универсальный переключатель не подтверждён; remote бывает названием офиса | Нет | Список: remote/hybrid по строке; карточка: TELECOMMUTE через JSON-LD. **Постфильтр после нужного этапа**; отсутствие метки не означает onsite |
| Recruitee | CareersHub Work model; проверенные параметры `/api/offers` не фильтруют | Нет | В списке независимые `remote/hybrid/on_site`, читаются вместе. **Постфильтр уже возможен по списку** |
| SmartRecruiters | Публичный `locationType=REMOTE/HYBRID/ONSITE/ANY` | Нет | Список даёт remote/hybrid, они читаются. В проверенной ONSITE-выдаче наш формат пуст. **Постфильтр частично возможен**; onsite требует надёжного поля либо контекста точного запроса |
| SuccessFactors | Новый RMK: настраиваемый фасет, у Danfoss все три; XML и classic SSR — другие контракты | **Да вручную**: `cust_WorkLocationType` в новом RMK | Новый RMK не извлекает формат; отдельная HTML-карточка его сообщает, но details нет. **Нативный отбор уже доступен; без него нужны детали и постфильтр**. XML читает English metadata |
| Taleo TBE | Возможны настраиваемые поля; отдельный формат-фильтр на двух досках не подтверждён | Транспорт custom fields есть; конкретное поле формата не установлено | В списке явные remote/hybrid/onsite из подписанных полей читаются. Если факт только в карточке — **нужны её разбор и постфильтр**, сейчас details нет |
| Teamtailor | Hosted Remote Status настраивается; API имеет отдельный контракт | Нет | На текущем шаблоне все 14 форматов пусты; у 11 потеряна явная Hybrid-метка. Формат есть также в карточке. **Сначала исправить извлечение → постфильтр**, либо реализовать native |
| Workable | Публичный browser POST `workplace=[remote/hybrid/on_site]` | Нет | Все три из JSON/Markdown читаются при наличии. **Постфильтр уже возможен**, можно добавить нативный отбор |
| Workday | CXS-фасеты конкретной доски: Remote/Office подтверждены; hybrid зависит от настройки | Нет: `appliedFacets={}` | В карточке optional `remoteType` читается, но NVIDIA его не отдаёт даже для remote. **Нужно использовать фасет или иной явный факт**, иначе постфильтру не хватает данных |
| Y Combinator | В company jobs не подтверждён; global jobs — другая область поиска | Нет | Remote из embedded location читается; hybrid/onsite могут быть только в тексте. **Для них нужно извлечение**, текущий постфильтр их не получает |

### Занятость: все 21 ATS

FT = fulltime, PT = parttime, C = contract, I = intern, V = volunteer.
Колонка значений описывает подтверждённые поля/настройки источника, а не только встреченные вакансии.
`?` означает, что конкретный тип не подтверждён этой проверкой. Custom — значения настраивает
работодатель; из этого не следует наличие всех пяти на каждой доске.

| ATS | Подтверждённые типы и границы | Фильтр платформы → у нас | Факты → постфильтрация сейчас |
|---|---|---|---|
| Ashby | FT/PT/C/I; V не входит в документированный enum; есть Temporary | Hosted Employment Type; public feed не фильтрует проверенные параметры → нет | Структурированный `employmentType`: четыре типа читаются. **Постфильтр по списку уже возможен** |
| BambooHR | Keyed string с примерами FT/PT/C; public сохранённый FT. I/V ?; полный enum неизвестен | Анонимный фильтр не установлен → нет | `employmentStatusLabel` читается для известных вариантов; сложные подписи теряются. **Постфильтр по распознанной метке**, текущая доступность public board ограничена |
| Breezy | Стандартный type FT/PT/C/Temporary/Other; I отдельно как experience, V ? | Проверенные hosted type-параметры не фильтруют → нет | В HTML FT читается, CONTRACT token теряется; JSON содержит type. **Нужно восстановить тип → постфильтр**; I/V из Other не выводить |
| Comeet | Настраиваемый string; preset FT/PT/Temporary/Hourly, C/I/V могут задаваться отдельно | Публичный фильтр embedded-ленты не подтверждён → нет | `employment_type`: известные FT/PT/C/I/V-подписи читаются. **Постфильтр по списку**, Hourly не доказывает FT/PT |
| Dreamjob | FT/PT в HTML карточки; V встречается в тексте при верхней категории «Подработка»; C/I ? | На двух досках не найден → нет | Поле HTML не читается; в проверенных JSON-LD employmentType нет. **Нужно извлечь FT/PT из карточки → постфильтр**; V требует явного факта текста |
| Greenhouse | Custom Employment Type, фиксированного enum пяти типов нет; live Intern подтверждён | Hosted custom filters при настройке → нет | Metadata Employment Type сохраняется только в сыром ответе. **Нужен разбор поля доски → постфильтр**, простой fetch описания не помогает |
| Huntflow | Все пять ? в проверенных public boards; это не запрет платформы | Не установлен → нет | Employment не извлекается; отдельный стандартизированный факт не установлен. **Пока нет основания для надёжной постпроверки** |
| iCIMS | FT/PT/C/I в настройках реальных досок; V ?; составные типы | `searchPositionType=<ID доски>` → только через заранее заданный query URL, не native_filters | Position Type списка читается частично; `Permanent Full-Time` теряется. **Нужна нормализация объявленных значений → постфильтр**; карточки пока не используются |
| JazzHR | Все пять явно есть в hosted форме; также переходные/временные варианты | `s_type` → нет | В деталях есть JSON-LD, но details нет; employment списка не читается. **Нативный отбор либо загрузка карточки → постфильтр** |
| Jobvite | FT/PT live; I как допустимая employer label; C custom, V ? | `t=<точная метка работодателя>` → нет | Detail JSON-LD есть, но details нет; список employment не заполняет. **Нативный отбор либо карточка → постфильтр** |
| JOIN | FT подтверждён; полнота PT/C/I/V этой проверкой не установлена | Публичный employment-фильтр не подтверждён → нет | List `Employee` не доказывает FT; **действующий detail JSON-LD восстанавливает FT**. Постфильтр после карточки |
| Lever | Настраиваемые commitment; live FT/I, остальные зависят от меток | Публичный JSON `commitment`, несколько через OR → **уже вручную поддержан** | Commitment нормализуется для известных FT/PT/C/I/V. **Постфильтр по списку уже возможен**, автоперевода общего запроса нет |
| Personio | XML раздельно schedule FT/PT и employmentType I/freelance(C); V ? | Hosted employment type настраивается → нет | Список теряет вторую ось; **карточка уже восстанавливает PT+I и PT+C**. Постфильтр должен дождаться деталей либо извлечения обеих осей списка |
| Recruitee | Все пять документированы; есть дополнительные типы | У public offers подтверждены другие фильтры; employment-параметры в проверке игнорируются → нет | `employment_type_code` читается для пяти типов и FT/PT с suffix. **Постфильтр по списку уже возможен**; freelance/temporary требуют отдельного решения о смысле |
| SmartRecruiters | Live FT/PT/I; C/V ?; схема id+label не задаёт универсальный enum | Нативный employment-фильтр не подтверждён; guessed параметр игнорируется → нет | Структурированный `typeOfEmployment.label` уже читается. **Нужна постфильтрация по списку, она уже возможна** |
| SuccessFactors | FT/PT в tenant-полях карточек; Student/Internship — широкая категория опыта, не только I; C/V ? | Новый RMK `filter3` и другие настроенные фасеты → **вручную поддержаны**; универсального employment enum нет | Новый RMK теряет L filter3 и D Part Time; details нет. **Нативное сужение Student/Internship доступно; I требует явного факта и постпроверки**, FT/PT — извлечения карточки; XML читает English metadata |
| Taleo TBE | Реальная форма FT/PT/I; C/V зависят от tenant, здесь ? | Tenant `CUSTOM_1066` → **обнаружение и ручная передача уже работают** | На IDA список не показывает тип: даже отобранные Intern имеют у нас employment=[]. **Нужен контекст применённого фильтра либо явный факт из карточки**, затем проверка |
| Teamtailor | Все пять документированы как attributes; карточка FULL_TIME подтверждена | Hosted Employment Types включается настройкой; точный query не установлен → нет | Employment не извлекается; JSON-LD есть в публичной карточке. **Нужна загрузка карточки → постфильтр**, ключ API для этого не нужен |
| Workable | Browser worktype FT/PT/C/Temporary/Other; отдельные I/V не входят в проверенный enum | Публичный `worktype` → нет | List `type`: FT/PT/C читаются. **Постфильтр по списку уже возможен**; I/V могут быть в иных полях/тексте, но не выводятся из Other |
| Workday | FT/PT через timeType; I через отдельный workerSubType; C/V ? на проверенной доске | Фасеты CXS конкретного работодателя → нет | FT/PT из карточки читаются; у NVIDIA Intern карточка говорит только Full time. **FT/PT — постфильтр после карточки; I — нужен фасет/дополнительный явный факт** |
| Y Combinator | Embedded FT/C/I live; PT в нашем mapping, но live type не подтверждён; V ? | Company jobs native не подтверждён → нет | Структурированный `type` FT/C/I читается. **Постфильтр по списку уже возможен**; part-time в названии при type=Contract — другая ось, сейчас не извлекается |

## Приложение Б. Подробные доказательства по источникам

### HH и Getmatch

| Источник | Фильтры формата у источника → у нас | Фильтры занятости у источника → у нас | Данные вакансии и нужный путь |
|---|---|---|---|
| HH | `ON_SITE`, `HYBRID`, `REMOTE`, `FIELD_WORK`; мы передаём HYBRID/REMOTE согласно минимуму, точного onsite нет | `employment_form=FULL/PART/PROJECT/FLY_IN_FLY_OUT`, `label=internship`; мы передаём только FULL/PART. Старое `employment` помечено устаревшим | В списке есть формат, `employmentForm`, отдельный `internship`, сведения о договоре. Формат и FULL/PART/PROJECT извлекаем; стажировку и договор полноценно не учитываем. Нужны нативный отбор и постпроверка отдельных признаков |
| Getmatch | Интерфейс объединяет офис/гибрид по регионам и отдельно удалёнку; мы используем категории `l`, затем нормализованные локации | В проверенных UI и `/api/offers` отдельный фильтр и структурированное поле занятости не обнаружены; у нас нет | Формат есть в структурированных `location_requirements`; постфильтр уже применим. Занятость может быть только в тексте карточки: нужно извлечение факта, которого сейчас нет; без явного указания — `unknown` |

### HH: подтверждения и воспроизведение

[Документация поиска](https://api.hh.ru/openapi/redoc#tag/Poisk-vakansij/operation/get-vacancies)
и [действующий справочник](https://api.hh.ru/dictionaries) различают старое `employment`,
новое `employment_form`, признак стажировки и вид договора. У `employment_form` текущие значения
FULL, PART, PROJECT, FLY_IN_FLY_OUT, SIDE_JOB; поисковый справочник содержит первые четыре.
В действующем `vacancy_label` есть `internship`, а в старом `employment` — `probation` и `volunteer`.
Наличие legacy-значения не доказывает корректную фильтрацию текущего HTML-поиска.

Прямой API-поиск `/vacancies` в этой проверке вернул 403, но HTML-поиск отдал 200 и разбираемый
`HH-Lux-InitialState`. Для `text=QA` наблюдались:

| Параметр HTML-поиска | `totalResults` | Проверенные данные первых строк |
|---|---:|---|
| `work_format=ON_SITE` | 1092 | `workFormatsElement` содержит ON_SITE |
| `work_format=HYBRID` | 518 | HYBRID, иногда вместе с другими форматами |
| `work_format=REMOTE` | 784 | REMOTE, иногда вместе с другими форматами |
| `employment_form=FULL` | 1763 | `employmentForm=FULL` |
| `employment_form=PART` | 15 | `employmentForm=PART` |
| `employment_form=PROJECT` | 32 | `employmentForm=PROJECT` |
| `label=internship` | 70 | `employmentForm=FULL` и одновременно `internship=true` |

Адрес для повторения: `https://hh.ru/search/vacancy?text=QA&page=0&` плюс параметр из таблицы.
Это наблюдаемые счётчики HH, не независимо пересчитанные полные выборки.
При старом `employment=volunteer` HTML-состояние выбрало PART и вернуло 15 результатов;
его нельзя считать подтверждённым точным поиском волонтёрства.

Конкретный дефект: [вакансия 138502888](https://hh.ru/vacancy/138502888) в ответе стажировок имела
`employmentForm=FULL, internship=true`. Наш парсер сохранил `employment=["full_time"]`,
выбросил `internship` из `source_data`, а постфильтр `employment=["internship"]` дал `rejected`
для этого условия и общий итог `maybe`. Причина — чтение только `employmentForm`, где текущая
стажировка не является значением перечисления. Подробности — WF-003.

Ещё пример: [138598704](https://hh.ru/vacancy/138598704) имеет FULL и одновременно
`civilLawContractsElement=[SELF_EMPLOYED, INDIVIDUAL_ENTREPRENEUR, INDIVIDUAL_PERSON]`.
Следовательно, проверка контрактного оформления не сводится к `employmentForm=PROJECT`.

### Getmatch: данные для постфильтрации

[Интерфейс](https://getmatch.ru/vacancies) показывает именно «Офис или гибрид» и отдельную удалёнку.
Запрос `/api/offers?sa=any&p=1&offset=0&limit=30&pa=all` вернул `meta.total=923`;
добавление `l=remote` — 388, `l=moscow` — 507. Выдача remote может содержать у вакансии
также офисные и гибридные варианты: список допустимых альтернатив надо сохранять.
Несуществующее `l=zxq_unknown` вернуло все 923 — принятие строки не доказывает фильтр.

В первых 30 объектах есть `location_requirements` с отдельными `format`, страной, городом
и исключениями; отдельного поля занятости нет. Это позволяет проверять формат локально уже сейчас.
В пяти проверенных текущих карточках явного указания занятости в описании не найдено.
Однако [архивная карточка CMS Digital](https://getmatch.ru/vacancies/25362-sistemnyi-administrator)
явно указывает полную занятость и гибрид. Она подтверждает возможность данных в тексте,
но не универсальное заполнение и не актуальность самой вакансии.
Наш адаптер загружает описание, однако не извлекает из него `employment`.

### Доказательства по ATS

### Ashby

[Public Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api),
[job.create](https://developers.ashbyhq.com/reference/jobcreate) и
[настройки доски](https://docs.ashbyhq.com/job-board-configuring-your-setup)
различают поля вакансии и фильтры интерфейса. Enum занятости:
FullTime/PartTime/Intern/Contract/Temporary; формата — OnSite/Remote/Hybrid.
[Harvey API](https://api.ashbyhq.com/posting-api/job-board/harvey): 344 вакансии,
FT341/I2/C1; Hybrid263/Remote12/OnSite3/формат не задан 66.
[Modal](https://api.ashbyhq.com/posting-api/job-board/modal): 43 FT, OnSite24/Remote2/не задан 17.
Наш локальный разбор сохранил эти распределения. У Harvey `isRemote=true` также встречается у hybrid:
приоритет трёхзначного `workplaceType` в нашем коде корректен.
В браузере [Remote](https://jobs.ashbyhq.com/harvey?workplaceType=Remote) дал 12,
[Intern](https://jobs.ashbyhq.com/harvey?employmentType=Intern) —2.
Те же параметры и несуществующие значения в публичном API оставили все 344 ID.
Следовательно, для выбранной ленты пригоден постфильтр по уже извлекаемым полям.

### BambooHR

[Create Job Opening](https://documentation.bamboohr.com/reference/create-job-opening)
документирует `locationType=0/1/2` для onsite/remote/hybrid и employmentType string с примерами
FT/PT/Contractor. Это авторизованная API записи, не контракт анонимной доски.
В этой проверке `/careers/list` G2, Meedan, Chainstack, Synder и AdTechHolding вернул 404
`Route Not Found`; live-сравнение формата и занятости заблокировано.
Сохранённые в репозитории ответы июня–июля 2026 содержат `locationType` строкой:
AdTech430=`"0"`,431=`"2"`, Synder121/122=`"1"`, при `isRemote=null`.
Текущий парсер игнорирует это поле и возвращает пустой формат.
Соответствие чисел закрытой документации — гипотеза о пропущенном извлечении;
её нельзя выдавать за подтверждённый текущий public-контракт.
[Текущий код](../src/parsers/ats/bamboohr/parser.py#L41) использует boolean и слово remote в label.

### Breezy

[Create Position](https://developer.breezy.hr/reference/createposition) различает type
fullTime/partTime/contract/temporary/other и experience; internship в этом контракте — experience.
На [sa.global](https://sa-global.breezy.hr/) HTML содержит 62 карточки, но 37 уникальных ссылок;
[публичный JSON](https://sa-global.breezy.hr/json) —37 позиций, FT33/C4.
GET `?remote=remote` дал 7 уникальных вакансий, `hybrid`9, `remote-location`6,
несуществующий remote —0. GET `?type=contract` и ошибочный type оба оставили 37:
это не рабочий фильтр занятости в проверенном интерфейсе.
У [контрактной D365-позиции](https://sa-global.breezy.hr/p/34db9c359b3201-d365-f-o-finance-functional-consultant-contract)
HTML type=`%LABEL_POSITION_TYPE_CONTRACT%`, JSON `type.id=contract`, а наш employment пуст.
У [Global Head of Revenue Operations](https://sa-global.breezy.hr/p/735374fd397501-global-head-of-revenue-operations-commercial-excellence)
явно указано отсутствие ограничений места (`REMOTE_ANY`, JSON remote_details), но наш результат
только remote_country. Сведения для более точной постпроверки уже доступны в источнике.

### Comeet / Spark Hire Recruit

[Модель позиции](https://developers.comeet.com/reference/careers-position-model) задаёт workplace_type;
[glossary](https://recruit-support.sparkhire.com/hc/en-us/articles/40045118026779-Glossary)
описывает настраиваемую занятость с исходными FT/PT/Temporary/Hourly.
[LinkedIn FAQ](https://recruit-support.sparkhire.com/hc/en-us/articles/40044991142683-LinkedIn-integration-FAQ)
обсуждает передачу C/I/V и сопоставление других значений, но не вводит глобальный enum Comeet.
[Hypernative](https://www.comeet.com/jobs/hypernative/8A.00E):15 записей, FT15, Hybrid8/Remote7;
[Gett](https://www.comeet.com/jobs/gett/A0.002):6, FT5/Hourly1, Hybrid5/On-site1.
Наш парсер корректно сохраняет форматы и известные типы. Hourly не превращается в FT/PT.
`workplace_type=Remote` и ошибочное значение оставили 15 embedded-записей;
`employment_type=Hourly` и ошибочное значение —6. Эти URL не заменяют постфильтр по данным списка.

### Dreamjob

На досках [Гринатома](https://dreamjob.ru/employers/32100/vakansii) и
[Банка ДОМ.РФ](https://dreamjob.ru/employers/104931/vakansii) отдельные контролы формата/занятости
не найдены. Это не доказательство отсутствия функции во всём сервисе.
[Полная занятость](https://dreamjob.ru/employers/32100/vakansii/138722188) и
[частичная занятость](https://dreamjob.ru/employers/127990/vakansii/138052865)
размечены в HTML `.job-vacancy__info`; во встроенном JSON-LD employmentType нет.
Обе карточки после действующего detail-разбора имеют employment=[].
Вторая описывает гибрид, но он также не извлекается.
[Волонтёрская вакансия](https://dreamjob.ru/employers/3878104/vakansii/136586740)
объясняет неоплачиваемую деятельность в тексте, тогда как верхняя категория — «Подработка».
Для FT/PT нужен разбор подписанного HTML-поля, для этого V — извлечение явного факта текста.
Нельзя сканировать remote-теги всей страницы: они бывают у рекомендованных соседних вакансий.

### Greenhouse

[Job Board API](https://docs.greenhouse.io/job-board#list-jobs) возвращает custom metadata при
включённой API exposure; [настройка доски](https://support.greenhouse.io/hc/en-us/articles/18996861758363-Customize-your-job-board)
позволяет до пяти custom filters. Это условная возможность работодателя, не общий query enum.
[Dice integration](https://support.greenhouse.io/hc/en-us/articles/56002945650587-Dice-integration)
прямо отличает внутренний Work setting от доступного интеграции custom field.
[DEPT API](https://boards-api.greenhouse.io/v1/boards/dept/jobs?content=true):185 записей с
Employment Type: Indefinite98/Fixed term82/Freelance4/Intern1.
У [Intern8195418](https://job-boards.greenhouse.io/dept/jobs/8195418) наш employment=[], хотя метка явная.
Indefinite и Fixed term сами по себе не доказывают полную или частичную нагрузку.
[Anthropic API](https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true):676;
Location Type=On-Site584/Remote24/Hybrid (Travel-Required)9/не задан 59.
Наш разбор оставил формат пустым у 540 On-Site,8 Remote и 7 Hybrid.
Например, [5387009008](https://job-boards.greenhouse.io/anthropic/jobs/5387009008) имеет metadata Remote,
но в location.name перечислены города. Есть также противоречия metadata и слова Remote-Friendly
в location: нужен явный контракт поля и обработка конфликта, а не универсальный приоритет названия.
Загрузка описания действующим details-handler эти поля не восстанавливает.

### Huntflow

[Cryptopay](https://cryptopay.global.huntflow.io/) и [B2BROKER](https://b2broker.global.huntflow.io/)
дают действующие доски без наблюдаемых контролов; remote виден в строках вроде `Remote (Europe)`.
Все пять типов занятости и отдельные hybrid/onsite-поля на этих досках не установлены.
[Текущая API v2](https://api.huntflow.ai/v2/docs) требует Bearer; настраиваемые поля аккаунта
не означают одноимённый public filter. Старую документацию v1 нельзя считать текущим контрактом.
Наш конструктор отклоняет оба `.global` адреса — [GEO-017](geography-review.md#geo-017-huntflow-отклоняет-действующие-доски-на-globalhuntflowio).
Поэтому наблюдение raw HTML не выдаётся за успешный полный путь нашего адаптера.

### iCIMS

[Job Profile](https://developer-community.icims.com/applications/applicant-tracking/ats-data-models/job-profile)
содержит настраиваемый positiontype, а telecommute Yes/No не равен трём форматам.
[Modern UI](https://careers.icims.com/careers-home/jobs) отличается от Classic `/jobs/search`;
его параметры в текущем адаптере не реализованы.
На [Starr](https://careers-starr.icims.com/jobs/search?in_iframe=1&ss=1) есть Position Type:
2049 Permanent Full-Time;2050 Permanent Part-Time;2051 Fixed-Term Full-Time;
8709 Fixed-Term Part-Time;2052 Contract Full-Time;131382 Contract Part-Time.
`searchPositionType=2049` дал 17 карточек,2050/2052 —0, неизвестный 987654321 —снова 17.
На [WesternSouthern](https://careers-westernsouthern.icims.com/jobs/search?in_iframe=1&ss=1&searchPositionType=8710)
8710 означает Intern и дал 16 карточек первой страницы. Это ID работодателя, не универсальные коды.
У всех 17 Starr наш employment пуст из-за непонятой метки Permanent Full-Time;
[полная карточка](https://careers-starr.icims.com/jobs/2496/talent-manager/job?in_iframe=1)
дополнительно имеет JSON-LD FULL_TIME, но details-handler нет.

### JazzHR

На [Symend](https://symend.applytojob.com/apply/jobs) и
[Codekeeper](https://codekeeper.applytojob.com/apply/jobs) Advanced-форма публикует точный `s_type`:
1 Full Time,2 Part Time,6 Contracted,8 Internship,10 Volunteer.
Дополнительно:3 Part Time to Full Time,4 Temporary,5 Temporary to Full Time,
7 Contracted to Full Time,9 Internship to Full Time,11 Seasonal;0 Any type.
Переходные варианты нельзя безусловно свести к одному из пяти типов.
GET `?form_id=advanced_search_jobs&s_type=1` на Symend дал 2, на Codekeeper30;
2/6/8/10/несуществующий 987654321 —0 на обеих досках.
Опция с нулём текущих результатов всё равно доказывает поддерживаемую категорию формы.
[Карточка Symend](https://symend.applytojob.com/apply/jobs/details/Xgn0DsdwR1)
имеет JSON-LD FULL_TIME и TELECOMMUTE. Наш адаптер не подключает детали и не извлекает employment.
Отдельный native format filter на этих двух досках не обнаружен: для такого факта пригодна карточка.

### Jobvite

[Официальный hosted-контракт](https://careers.jobvite.com/careersite/iframe_filtering.html)
задаёт `t` с точными Job Type работодателя. Универсальный пятизначный enum не установлен.
[Liferay](https://jobs.jobvite.com/liferay/jobs):18 вакансий;
`/liferay/search?t=Full-Time` дал 16, `t=Part-Time`2, `t=Intern`0.
Ошибочные `Internship`, `Full Time` и несуществующая метка вернули все 18.
На Uplight аналогично:12 всего/Full-Time12/Part-Time0/неизвестный 12.
Отражение неверной метки в заголовке не является подтверждением отбора.
[Карточка Liferay](https://jobs.jobvite.com/liferay/job/oOVaAfwd) имеет JSON-LD Full-Time,
но у нас нет details и все 18 employment пусты.
Отдельный workplace filter не описан в этом hosted-контракте; формат может быть частью
настроенных Location/Region или описания. Сейчас читаются только слова remote/hybrid в строке.

### JOIN

[Редактор вакансии](https://help.join.com/jobs/job-creation) подтверждает On-site/Hybrid/Remote,
но не public query-фильтр. [Employment Types API](https://docs.join.com/reference/getemploymenttypes-1)
авторизованный и не доказывает анонимный поиск по занятости.
На [доске JOIN](https://join.com/companies/join) preview содержит четыре HYBRID из пяти вакансий;
по выбранной записи 16805344 list label=Employee, а карточка содержит FULL_TIME.
Наш список правильно не превращает Employee в fulltime, действующий detail JSON-LD восстанавливает
full_time. Следовательно, локальная проверка занятости требует карточки; формат доступен уже в списке.
Контракт, weeklyWorkingHours и internship следует проверять раздельно, когда источник их сообщает.

### Lever

[Postings API](https://github.com/lever/postings-api) поддерживает commitment, в том числе OR,
но прямо не разрешает фильтр workplaceType в JSON. Commitment — метки работодателя.
[Field AI JSON](https://api.lever.co/v0/postings/field-ai?mode=json):112,
Full time104/Internship6/не задан 2; `&commitment=Internship` дал 6, неизвестный commitment0.
Наш ручной native commitment и разбор этих значений работают.
[Doxel JSON](https://api.lever.co/v0/postings/doxel?mode=json):15, Remote4/Hybrid9/Onsite2.
Добавление workplaceType=remote или ошибочного значения оставляет 15,
тогда как [hosted HTML](https://jobs.lever.co/doxel?workplaceType=remote) даёт 4,
hybrid9/onsite2/ошибочный 0. Для текущего JSON-пути нужен уже доступный постфильтр по workplaceType.

### Personio

[XML-документация](https://developer.personio.de/docs/retrieving-open-job-positions)
разделяет schedule и employmentType; [настройки страницы](https://support.personio.de/hc/en-us/articles/8758636857629-Set-up-the-Personio-career-page)
подтверждают настраиваемый Employment Type, но не единый workplace query enum.
На [deeploi](https://deeploi.jobs.personio.de/) у 2813975 metadata:
Berlin / Part-time / Intern / Student. Наш список даёт только part_time;
JSON-LD карточки `[PART_TIME,INTERN]` уже восстанавливает part_time+internship.
На [Tandem](https://tandem.jobs.personio.de/)2792207:
remote / Part-time / Freelancer; карточка `[PART_TIME,CONTRACTOR]` восстанавливает part_time+contract.
Это не полный отказ парсера: существующее обогащение работает.
Для C/I постфильтр должен дождаться карточки либо извлечения обеих структурированных осей списка.

### Recruitee

[Offers API](https://docs.recruitee.com/reference/offers) описывает department/tag filters;
[feed reference](https://docs.recruitee.com/docs/feed) перечисляет все пять нужных типов
и дополнительные freelance/temporary/apprenticeship/seasonal.
[Redsky](https://redsky.recruitee.com/api/offers):21, FT19/C2;
remote20/hybrid5/onsite1 — варианты пересекаются.
`remote=true` и ошибочное значение, `employment_type_code=contract` и ошибочное значение оставили 21.
[TransPerfect](https://transperfect.recruitee.com/api/offers):677,
FT517/PT3/C32/I8/freelance113/temporary4; remote123/hybrid102/onsite497.
В [CareersHub UI](https://transperfect.recruitee.com/) выбор Remote уменьшил 677 до 123,
URL стал `?jobs-c88dea0d[workModel][]=remote`; нового offers/XHR-запроса не наблюдалось.
Наш код сохраняет все три boolean и встреченные четыре типа; V есть в документации и mapping,
но в этих двух живых лентах не встретился. Для текущего API достаточно постфильтра по списку.

### SmartRecruiters

[Posting API](https://developers.smartrecruiters.com/reference/v1listpostings)
документирует locationType. [Nexthink API](https://api.smartrecruiters.com/v1/companies/Nexthink/postings?limit=100):68,
`locationType=REMOTE`17/HYBRID45/ONSITE6.
У ONSITE-selected744000154505529 обе booleans remote/hybrid явно false, наш формат=[];
это показывает потерю контекста точного отбора, но не доказывает универсальное правило
«нет remote/hybrid → onsite» для произвольного неполного ответа.
`typeOfEmployment=part-time` и `=impossible` оставили 68: этот guessed filter игнорируется.
Зато [BoschGroup](https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100)
в первой сотне вернул typeOfEmployment labels Full-time94/Part-time3/Intern3.
Наш нормализатор их читает; для занятости уже возможен постфильтр без полной карточки.

### SuccessFactors

[UDM-настройки](https://help.sap.com/docs/successfactors-recruiting/setting-up-and-maintaining-sap-successfactors-recruiting/configuring-layout-of-search-results-for-unified-data-model?locale=en-US&source=redirect)
разрешают поля шаблона работодателя. На [Danfoss](https://jobs.danfoss.com/search/)
новый `/services/recruiting/v1/jobs` с facetFilters:
`cust_WorkLocationType=["On-site"]` дал 535, Hybrid105, Remote31;
`filter3=["Student/Internship"]`72. Ошибочные значения дали 0; регистр значим.
Оба ключа **уже принимаются нашим новым RMK-адаптером вручную**.
Категория Student/Internship шире стажировки: среди первых десяти есть alternance49357
и apprentice51500. Её нельзя целиком нормализовать в internship.
В JSON позиции 51689 есть filter3 Student/Internship, но исходная категория остаётся только в source_data.
[Карточка 51689](https://jobs.danfoss.com/job/Queretaro-Finance-Intern-%28-Credit-Management%29/51689-en_GB/)
публикует Employment Type:Part Time, Work Location Type:On-site,
Job Experience Level:Student/Internship. Действующего RMK details-handler нет.
Legacy XML читает English metadata; classic SSR — другой, пока не покрытый вариант.
Нельзя считать filter3 универсальным обозначением стажировки у всех работодателей.

### Taleo

[Business Edition IDA](https://phh.tbe.taleo.net/phh01/ats/careers/v2/jobSearch?org=INSTITUTEDA&cws=39)
имеет CUSTOM_1066:844 Regular Full-Time,931 Part-time/On-call,846 Intern.
POST найденной формы на её searchResults:43 исходно →35 FT/3 PT/2 Intern/0 неизвестный ID.
Наше обнаружение формы и передача CUSTOM_1066 через native_filters уже работают.
Но в списке IDA тип отсутствует, поэтому локальный employment остаётся пуст даже после отбора Intern.
Карточка Intern3744 содержит JSON-LD Non-Exempt — это категория другого измерения, не тип занятости;
в тексте есть internship. Автоматически переводить Non-Exempt в один из пяти типов нельзя.
[Taleo Enterprise guide](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24c/otcug/implementing-career-section.pdf)
отдельно описывает Job Type/Employee Status/Schedule. Наш адаптер работает с TBE v2;
возможности Enterprise ему не приписываются. Формат работы нельзя выводить из обычного адреса офиса.

### Teamtailor

[Jobs block](https://support.teamtailor.com/en/articles/5227868-content-block-jobs)
подтверждает настраиваемые Employment Types и Remote Status.
[Jobs API](https://docs.teamtailor.com/) и [partner object](https://partner.teamtailor.com/job_boards/)
описывают среди значений employment-type fully/part/contract/internship/volunteer
(также есть none/temporary/apprenticeship);
remote-status none/temporary/hybrid/fully. API требует ключ;
наличие атрибута employment-type не доказывает параметр `filter[employment-type]` — в таблице GET jobs его нет.
На шести проверенных досках включённый employment control не найден, его точный query неизвестен.
[Доска Teamtailor](https://career.teamtailor.com/jobs):14;
`remote_status_id=hybrid`11, none3, fully/temporary/onsite/ошибочный 0.
Hosted select также содержит onsite: это не повод отождествлять его с API enum.
Наш парсер оставляет формат пустым у 14/14, причём у 11 теряется явная Hybrid-метка.
Он ищет старый `div.mt-1`, текущая карточка использует `div.text-md.mt-4`.
[Публичная деталь 8118064](https://career.teamtailor.com/jobs/8118064-uk-account-executive-smb)
имеет подписанный Remote status:Hybrid и JSON-LD FULL_TIME, но details-handler отсутствует.

### Workable

На [Entuitive](https://apply.workable.com/entuitive/) анонимный browser endpoint
`POST /api/v3/accounts/entuitive/jobs` принимает workplace и worktype;
`GET /api/v3/accounts/entuitive/jobs/filters` возвращает значения.
Из 13 вакансий workplace hybrid12/on_site1/remote0, worktype full11/part0.
На [Braven](https://apply.workable.com/braven/) из 26 part1, contract0; ошибочный worktype дал 400.
Browser enum full/part/contract/temporary/other, intern/volunteer как worktype дали 400.
Это не запрет стажировок в experience/тексте.
[Авторизованный requisition API](https://workable.readme.io/reference/requisitions-post)
имеет свой контракт и не заменяет доказательства публичного browser POST.
Наш JSON и Markdown уже нормализуют формат и FT/PT/C; build передаёт только query.
Нужные факты в списке доступны для постфильтра без добавления native filters.

### Workday

[Remote Jobs](https://stage.doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/job-postings/cck1660565105414.html?toc=3.6.2)
описывает настройки платформы; это не универсальные CXS ID.
На [NVIDIA](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite)
POST `/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs` с appliedFacets:
timeType Part time дал 2; workerSubType Intern (Fixed Term)182; Remote562.
Исходный `total=2000` ограничен, не доказывает полное число вакансий компании.
В другой географической проверке Remote дал 561: счётчики изменяются даже между проходами.
Ключи/ID получаются из фасетов конкретной доски; Office не переименовывается в onsite без проверки смысла.
NVIDIA list не даёт timeType/workerSubType; detail Intern JR2026546 сообщает только Full time,
а remote JR2024283 — location UK, Remote без remoteType. Наши результаты соответственно
employment=[full_time] без internship и work_formats=[].
FT/PT уже постфильтруются после деталей; для I/remote здесь нужны фасеты или другие явно подтверждённые факты.

### Y Combinator

Company jobs [Airweave](https://www.ycombinator.com/companies/airweave/jobs) и
[Tavus](https://www.ycombinator.com/companies/tavus/jobs) не показали native controls этих условий.
[Poka Labs](https://www.ycombinator.com/companies/poka-labs/jobs) содержит embedded Contract,
[Voltair](https://www.ycombinator.com/companies/voltair/jobs) — Internship; FT также подтверждён.
Точные FT/C/I уже читаются и подходят для постфильтра.
PT не доказан как текущее значение type:
[AirMyne part-time](https://www.ycombinator.com/companies/airmyne/jobs/n98XdDP-social-media-manager-part-time)
имеет type=Contract, а [Kastle part-time](https://www.ycombinator.com/companies/kastle/jobs/SLxK5Ru-ai-trainer-part-time)
— Full-time. Название не доказывает одноимённый enum поля.
[Zentail](https://www.ycombinator.com/companies/zentail/jobs) публикует Hybrid Preferred в названии,
но location Columbia, MD / Remote (US): текущий парсер видит лишь remote.
Детали и отдельное извлечение нагрузки/гибрида сейчас отсутствуют; global jobs — иной интерфейс.

## Воспроизведение и ограничения

Все исследования выполнялись чтением публичных страниц/API и текущего рабочего дерева.
Параметры API с ключом изучались по документации; авторизованные запросы с ключом не выполнялись.
Формат, занятость, доступность страны и форма договора проверялись как разные признаки.

Для GET-проб выше достаточно выполнить GET по указанной ссылке и добавить описанный параметр.
Сравниваются ID вакансий и количество результатов, а не только успешный HTTP-статус.
Для HTML с пагинацией число карточек относится к первой странице, если не приведён счётчик total.
Breezy считался по уникальным ссылкам; Workday total2000 не интерпретируется как полная выдача.
Исторические HTML/JSON целиком в этот документ не включены; точные прошлые количества после изменения
сайта не гарантируются. Минимальные исходные факты для дефектов приведены рядом с их описанием.

Примеры повторения публичных запросов:

```bash
curl -sS 'https://api.lever.co/v0/postings/field-ai?mode=json&commitment=Internship'
curl -sS 'https://symend.applytojob.com/apply/jobs?form_id=advanced_search_jobs&s_type=1'
curl -sS 'https://jobs.jobvite.com/liferay/search?t=Part-Time'
curl -sS 'https://career.teamtailor.com/jobs?remote_status_id=hybrid'
curl -sS 'https://api.smartrecruiters.com/v1/companies/Nexthink/postings?limit=100&locationType=REMOTE'
curl -sS -H 'Content-Type: application/json' \
  -d '{"workplace":["hybrid"]}' \
  'https://apply.workable.com/api/v3/accounts/entuitive/jobs'
```

Для Taleo сначала открывается указанная форма, затем её action получает POST с исходными скрытыми
полями и выбранным CUSTOM_1066. Для Workday/SAP нужно сначала прочитать bootstrap/фасеты работодателя:
ID не переносятся между досками. Указанные значения Danfoss работают в JSON facetFilters,
а не как произвольные GET-параметры; отправляется locale доски и остальная форма нового RMK.

Текущий набор `tests/parsers/ats`, `tests/test_ats_contracts.py`, `tests/test_filters.py`:
**525 passed**. Это базовая проверка существующих контрактов, не подтверждение отсутствия описанных
дефектов: они воспроизведены отдельным разбором реальных ответов и прямыми вызовами текущего кода.
Код приложения и тесты при этом аудите не менялись.

Новые проблемы получают WF-015 и далее. Закрытие пункта требует изменения реализации
и повторения исходного случая. Идея исправления сама по себе не меняет статус.

