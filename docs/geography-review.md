# География: возможности источников и журнал проблем

Дата проверки: 21 октября 2026 года.

Назначение: исходные данные для отдельного планирования доработок каждого источника.
В начале — краткая аналитика с маршрутами фильтрации и зависимостями; затем — багрепорты
с воспроизведением; подробные возможности и доказательства источников сохраняются отдельно.
Связанный отчёт: [формат работы и занятость](work-format-employment-review.md).

Поддержка фильтра платформой, наличие географии в вакансии и наша реализация — три разные вещи.
Страна задаётся кодом ISO 3166-1 alpha-2; город и административный регион требуют собственной
идентичности. Название, алиас и ID источника нельзя считать взаимозаменяемыми.

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

## Зачем регион, если страна выбрана по ISO

ISO-код `RU` однозначно задаёт страну, но не различает несколько городов Троицк внутри неё.
Пользователь выбирает **страну и конкретный город**. Регион из подсказки помогает понять,
какой одноимённый город выбран, и затем найти его в справочнике источника.
Например, локальный справочник даёт «Троицк / Челябинская», а HH —
«Троицк (Челябинская область)». Сейчас `region_matches` пытается признать эти подписи одним регионом,
убирая слова «область», «республика» и т. п. «Край» — часть этой внутренней эвристики.

Это не самостоятельный фильтр пользователя и не нормализация ISO-кода страны.
Продуктовое требование — сохранить однозначный выбор места. Удаление четырёх слов является
текущей хрупкой реализацией сравнения подписей, а не обязательной архитектурой.
Планы должны определить идентичность города/региона и перевод в справочник источника;
исправление одного слова «край» само по себе эту задачу не решает.

В текущем фронтенде текст города запускает подсказки, но код также разрешает сохранить
невыбранную строку с region=None. Несколько мест уже добавляются кнопкой
«Добавить место работы»; API допускает до 20. Целевое требование владельца — понятный выбор
**до 5** мест с объединением через «или» — зафиксировано в продуктовой спецификации,
а не описано как уже реализованное ограничение.

## Краткая аналитика для планирования

Нативный фильтр применяется источником. Постфильтр проверяет уже полученную вакансию.
Если нативного фильтра нет, но страна/город доступны в списке или карточке, используется постфильтр.
«Не подтверждено» требует проверки контракта, а не вывода, что платформа не поддерживает функцию.
Общие зависимости ниже учитываются отдельно от работ конкретного адаптера.

| Общая область | Суть ограничения | Что требуется определить при планировании | Проблемы |
|---|---|---|---|
| Страны | 32 страны одновременно служат UI-каталогом, валидатором запроса и нормализатором внешних значений | Разделить продуктовый выбор, проверку назначенных ISO-кодов и многоязычное распознавание | GEO-003, GEO-004, GEO-013 |
| Города и регионы | Строковое сопоставление, утрата ID и региона, региональные эвристики | Идентичность места, сохранение уточнений, границы равенства/неизвестности | GEO-002, GEO-006, GEO-007, GEO-019…022 |
| Запрос источника | Общая география ATS не переводится автоматически даже в реализованные ручные параметры | Перевод значений конкретной доски, безопасные ветви и повторная проверка широких категорий | GEO-010, GEO-018 |
| Результат фильтра | Точное нативное условие и широкая ветвь не различаются; строка места не заменяет структурированные факты | Сохранять подтверждённые условия конкретного запроса и источники фактов для постфильтра | GEO-011, GEO-012 |

Следующая таблица — вход для планирования по каждому источнику. Колонка «маршрут» показывает,
какие данные уже доступны и на каком этапе нужна доработка; номера связывают работу с багрепортами.
Общие проблемы из таблицы выше не нужно независимо исправлять в каждом парсере.

| Источник | Что доступно у источника / у нас | Маршрут доработки | Проблемы |
|---|---|---|---|
| HH | Динамический area; native-поиск реализован, широкая remote-ветвь остаётся | Сопоставление справочников и постпроверка remote-ограничений | GEO-005, GEO-006, GEO-012 |
| Getmatch | Категории объединяют несколько мест; отдельные места есть в ответе | Исправить перевод города; категория → постфильтр конкретной страны/города | GEO-001, GEO-004 |
| Ashby | Страна/город в структурированном списке; public native geography не подтверждена | Постфильтр по списку; сохранить регион, нормализовать страны | GEO-003, GEO-007 |
| BambooHR | Страна/город в поддержанном формате; текущие доски недоступны | Сначала подтвердить живой контракт; затем постфильтр по доступным полям | GEO-003, GEO-007 |
| Breezy | Native Country/Location; у нас только location, ответ — строка | Расширить native; структурировать страну/несколько мест для постпроверки | GEO-014, GEO-018 |
| Comeet | UI фильтрует полный embedded-список; структурированные места уже читаются | Постфильтр по списку; сохранить штат/регион | GEO-003, GEO-007 |
| Dreamjob | Город списка, JSON-LD карточки; native geography не установлена | Постфильтр после списка/карточки; неизвестную страну не угадывать | Ограничение данных; отдельный дефект страны не доказан |
| Greenhouse | Public offices API; у нас только location string | Выбор офиса native; извлечение мест/проверка неприменённых условий | GEO-011, GEO-018 |
| Huntflow | Места в строке, проверенные адреса не принимает конструктор | Сначала поддержка адресов; затем явные факты → постфильтр | GEO-011, GEO-017 |
| iCIMS | Native Location; параметры URL сохраняются, текущий разбор мест теряет варианты | Поддержать формат нескольких мест; использовать значения формы | GEO-014, GEO-015, GEO-018 |
| JazzHR | Native city/state, но у нас только строка | Передать native; извлечь недостающие факты для постпроверки | GEO-011, GEO-018 |
| Jobvite | Native Location/Region с tenant-значениями; у нас строка | Получать допустимые значения, передавать и сохранять при пагинации; постпроверять широкие группы | GEO-011, GEO-012, GEO-018 |
| JOIN | Структурированные места и ограничения remote; public filter не подтверждён | Постфильтр по списку/карточке с сохранением городского remote-ограничения | GEO-008 |
| Lever | Native location уже вручную; страна читается, город из label нет | Перевод в реальные location; извлечение города для неприменённых условий | GEO-003, GEO-010, GEO-011 |
| Personio | UI office filter; места JSON-LD из карточки | Постфильтр после карточки; проверить контракт native office отдельно | GEO-003, GEO-010 |
| Recruitee | Структурированные несколько мест уже читаются; API native не подтверждён | Постфильтр по списку, сохранить регион | GEO-003, GEO-007 |
| SmartRecruiters | Country/region/city уже вручную передаются; ответ структурирован | Автоперевод запроса; сохранить регион и подтверждение точного условия | GEO-007, GEO-010, GEO-012 |
| SuccessFactors | Новый RMK location/facets вручную; XML и classic отличаются | Разделить варианты; извлечь факты для неприменённых условий | GEO-010, GEO-011 |
| Taleo TBE | Native geography формы уже вручную; ответ частично строковый | Перевод по форме и структурирование мест; Enterprise отдельно | GEO-010, GEO-014 |
| Teamtailor | Native geography; у нас сломан разбор текущего шаблона | Восстановить список; добавить native и факты для постфильтра | GEO-011, GEO-016, GEO-018 |
| Workable | Native JSON location и справочник; у нас эти параметры не передаются | Native location + постпроверка структурированных полей; отдельный Markdown-путь | GEO-010, GEO-014, GEO-018 |
| Workday | Tenant facets; страна карточки, город не структурирован | Native facets и точность запроса; при широком поиске нужны факты для постфильтра | GEO-010, GEO-011, GEO-018 |
| Y Combinator | Company jobs даёт строку/remote-коды, global jobs — другой интерфейс | Нормализовать коды, извлечь офисные места; постфильтр company jobs | GEO-009, GEO-011 |

### Итоги аудита общего модуля geography

Проверены все файлы `src/geography`, сборщик, поставляемые данные, модели запроса/вакансии,
сервис подсказок и их использование в постфильтре и HH. Эти задачи относятся к общему модулю,
поэтому их планируют совместно с зависимостями адаптеров, а не независимо 23 раза.

| Область | Проверенный факт | Вывод для планирования |
|---|---|---|
| Страны | 32 действительных alpha-2 кода; IN/EE/ZA/FI/BR/JP и другие назначенные коды запрос не принимает; города собраны для тех же 32 | GEO-013: разделить покрытие продукта, проверку страны, справочник внешних названий и покрытие данных |
| Русские стоп-слова региона | `край` нормализуется в `краи`, но сравнивается с исходным `край`; английские типы не разрешаются | GEO-019: конкретный дефект плюс граница многоязычного сопоставления; добавление ещё нескольких слов не даёт общей идентичности региона |
| Сравнение города | Два разных места VN Yên Phụ/Yên Phú превращаются в один ключ и принимаются как совпавшие без региона | GEO-020: отделить неточный поисковый ключ от подтверждённой идентичности |
| Выбор одноимённого города | Clinton есть в 24 регионах, API отдаёт только 20; уточнение штатом в строке поиска не работает | GEO-021: нужен доступный способ уточнить/получить скрытые варианты |
| Сборка регионов | Равенство имён города и admin1 стирает регион и за пределами федеральных городов; реальные NYC/Utrecht/Zug | GEO-022: сохранять регион по административной идентичности, а не равенству подписи |
| Точность данных | 112357 записей, 1851 группа одинаковых country/name/region; 2344 дополнительные строки, ID удалены | GEO-007: одна уникальная пара подписей не доказывает единственный населённый пункт |

Хеш снимка совпадает с metadata; порог `cities500`, выбор языков и стран явно описаны в README.
Это не полный мировой справочник городов. Реальных коллизий разных сохранённых регионов только из-за
четырёх стоп-слов не найдено: заявлять такую отдельную ошибку было бы необоснованно.
[ISO различает](https://www.iso.org/iso-3166-country-codes.html) коды стран и подразделений;
ISO 3166-1 включает также территории. Русская подпись для UI не обязана буквально совпадать с ISO-именем.

## Как читать результаты

- **Документация** — возможность описана самим поставщиком платформы.
- **Интерфейс** — фильтр наблюдался на указанной карьерной доске.
- **Запрос** — проверено конкретное HTTP-обращение и его результат.
- **Код** — проверена текущая реализация репозитория.
- **Не подтверждено** — доказательств недостаточно. Это не утверждение об отсутствии возможности.

Карьерная доска — список вакансий конкретного работодателя на платформе ATS.
Наличие фильтра на одной доске не доказывает, что каждый работодатель его включает.
Закрытый API управления вакансиями не равен публичному поиску для кандидата.

## Багрепорты

Дефекты, пробелы интеграции и риски помечены отдельно. Синтетические входы воспроизводят механизм,
но не доказывают наличие такой записи в текущей живой выдаче. Все 22 локальных блока ниже
выполнены на рабочей копии. Для примеров с ранее наблюдавшимися live-строками указан их источник
в подробных доказательствах. Номер GEO сохраняется при дальнейшем планировании и исправлении.

### GEO-001. Getmatch выбирает категорию по неполному списку написаний города

**Статус:** исправлено 03.11.2026: зона выбирается по `geoname_id`, см. [план Getmatch](getmatch-geography-plan.md). **Тип:** дефект перевода. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Разные известные написания Петербурга дают разные категории Getmatch. Вариант St. Petersburg ошибочно направляется в другие города РФ.

**Затронутый код:** [GetmatchSearch.prepare](../src/parsers/aggregators/getmatch/parser.py#L66).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Дефект переводчика; синтетический бриф, реальные локальные справочники.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
import asyncio
from urllib.parse import parse_qs, urlsplit
from src.models.brief import BriefInput
from src.geography.directory import CityDirectory
from src.parsers.aggregators.getmatch.parser import GetmatchSearch
async def no_fetch(url):
    raise AssertionError('Unexpected network request: ' + url)
async def main():
    print('catalog:', CityDirectory().search('RU', 'St. Petersburg')[:1])
    for city in ['St. Petersburg', 'Saint Petersburg']:
        brief = BriefInput(name='geo repro', filters={'locations':[{'country':'RU','city':city}]})
        plan = await GetmatchSearch(20).prepare(brief, no_fetch)
        print(city, parse_qs(urlsplit(plan.requests[0].url).query).get('l'))
asyncio.run(main())
PY
```

**Фактический результат:**

```text
catalog: [{'name': 'Санкт-Петербург', 'region': None, 'path': ['Россия']}]
St. Petersburg ['regions_ru', 'remote']
Saint Petersburg ['saints_p', 'remote']
```

**Ожидаемый результат:** Оба варианта должны выбирать saints_p + remote, либо идентичность города должна разрешаться до выбора source category.

**Влияние и границы доказательства:** Можно потерять офисные вакансии Петербурга; масштаб живой потери этой локальной проверкой не измеряется.

**Критерий закрытия:** Оба известных алиаса выбирают saints_p; прочие города РФ сохраняют свою категорию.

### GEO-002. Постфильтр не использует алиасы городов

**Статус:** открыто. **Тип:** дефект сопоставления. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Сравнение возвращает False для известных алиасов одного города, хотя локальный справочник находит правильное место. Постфильтр отклоняет подходящую вакансию.

**Затронутый код:** [city_matches](../src/geography/names.py#L28), [match_city](../src/geography/directory.py#L81).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый алгоритмический дефект; синтетические вакансии.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.geography.directory import CityDirectory, match_city
from src.models.filters import SearchFilters, WorkLocation
from src.models.vacancy import Vacancy
from src.selection.filtering import evaluate
for country,wanted,actual in [('RU','Москва','Moscow'),('RU','Санкт-Петербург','Saint Petersburg'),('DE','Мюнхен','Munich')]:
    print('catalog:', country, actual, CityDirectory().search(country, actual)[:1])
    v=Vacancy(id='1',title='QA Engineer',url='https://example.com/jobs/1',work_formats=['office'],locations=[WorkLocation(country=country,city=actual,format='office')])
    f=SearchFilters(locations=[{'country':country,'city':wanted}])
    result=evaluate(v,f,queries=['QA'])
    print(wanted,actual,'match=',match_city(country,wanted,actual),'locations=',next(c.status for c in result.checks if c.field=='locations'),'overall=',result.status)
PY
```

**Фактический результат:**

```text
catalog: RU Moscow [{'name': 'Москва', 'region': None, 'path': ['Россия']}]
Москва Moscow match= False locations= rejected overall= rejected
catalog: RU Saint Petersburg [{'name': 'Санкт-Петербург', 'region': None, 'path': ['Россия']}]
Санкт-Петербург Saint Petersburg match= False locations= rejected overall= rejected
catalog: DE Munich [{'name': 'Мюнхен', 'region': 'Бавария', 'path': ['Германия', 'Бавария']}]
Мюнхен Munich match= False locations= rejected overall= rejected
```

**Ожидаемый результат:** match=True, locations=accepted для каждой пары известных алиасов. Текущее False не является unknown.

**Влияние и границы доказательства:** Ложноотрицательный отбор между русскими/английскими алиасами.

**Критерий закрытия:** Три известные пары алиасов принимаются; разные и неоднозначные города не приравниваются.

### GEO-003. Общий нормализатор стран ATS неполон и принимает непроверенные двухбуквенные значения

**Статус:** открыто. **Тип:** дефект нормализации. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Нормализатор теряет действительные названия стран вне ограниченного набора и принимает произвольные две буквы за страну без проверки назначения кода.

**Затронутый код:** [country_code](../src/parsers/ats/normalization.py#L48).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый дефект нормализации внешних стран.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.normalization import country_code
for value in ['India','Estonia','Russian Federation','IN','EE','RU','UK','ZZ','OO']:
    print(repr(value),'->',repr(country_code(value)))
PY
```

**Фактический результат:**

```text
'India' -> None
'Estonia' -> None
'Russian Federation' -> None
'IN' -> 'IN'
'EE' -> 'EE'
'RU' -> 'RU'
'UK' -> 'GB'
'ZZ' -> 'ZZ'
'OO' -> 'OO'
```

**Ожидаемый результат:** India→IN, Estonia→EE, Russian Federation→RU. В строгом поле реальной страны OO не должен приниматься как подтверждённая страна. Для user-assigned ZZ нужна явная политика custom values, а не молчаливое подтверждение страны.

**Влияние и границы доказательства:** Неизвестная или неподтверждённая страна попадает в общую проверку; один вход India теряется, IN сохраняется.

**Критерий закрытия:** Названия переводятся в назначенные коды; неназначенные значения не становятся подтверждённой страной. Для user-assigned значений задана отдельная политика.

### GEO-004. Нормализация ответов Getmatch зависит от точного русского названия страны

**Статус:** открыто. **Тип:** риск устойчивости. **Приоритет:** средний.

**Суть ошибки или ограничения:** Нормализация country зависит от точного русского label. Это риск переносимости, а не доказательство, что живой Getmatch изменил язык.

**Затронутый код:** [getmatch_locations](../src/parsers/normalization.py#L96).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Проверяемая хрупкость; смена языка реального источника не доказана.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.normalization import getmatch_locations
for country in ['Россия','Russia']:
    raw={'location_requirements':[{'country':country,'city':'Москва','format':'ON_SITE'}]}
    print(country,[p.model_dump() for p in getmatch_locations(raw)])
PY
```

**Фактический результат:**

```text
Россия [{'country': 'RU', 'city': 'Москва', 'format': 'office', 'excluded': False, 'worldwide': False}]
Russia [{'country': None, 'city': 'Москва', 'format': 'office', 'excluded': False, 'worldwide': False}]
```

**Ожидаемый результат:** Если источник официально допускает обе подписи, обе должны нормализоваться в RU. Сейчас доказана только зависимость алгоритма; live regression не доказан.

**Влияние и границы доказательства:** Зависимость от языка внешнего label. До подтверждения альтернативного контракта нельзя заявлять живую регрессию.

**Критерий закрытия:** Установлена граница source-контракта; поддерживаемые варианты label дают одну страну, неподтверждённые явно остаются unknown.

### GEO-005. Исключения стран для мировой удалёнки пропускаются

**Статус:** открыто. **Тип:** дефект фильтрации. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Распознанная мировая удалёнка сохраняется, но исключения United States/United Kingdom теряются. Вакансия принимается для явно запрещённой страны.

**Затронутый код:** [_excluded_countries](../src/parsers/normalization.py#L75).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый алгоритмический дефект; синтетический HH текст.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.parsers.normalization import normalize_stored_vacancy
from src.selection.filtering import evaluate
for country,name in [('US','United States'),('GB','United Kingdom')]:
    raw=Vacancy(id='1',title='QA Engineer',url='https://example.com/jobs/1',description='Remote worldwide except '+name,work_formats=['remote_country'])
    v=normalize_stored_vacancy(raw,'hh_ru')
    f=SearchFilters(locations=[{'country':country}],minimum_work_format='remote_worldwide')
    result=evaluate(v,f,queries=['QA'])
    print(name,'formats=',v.work_formats,'locations=',[p.model_dump() for p in v.locations])
    print('checks=',[(c.field,c.status) for c in result.checks],'overall=',result.status)
PY
```

**Фактический результат:**

```text
United States formats= ['remote_worldwide'] locations= [{'country': None, 'city': None, 'format': 'remote_worldwide', 'excluded': False, 'worldwide': True}]
checks= [('profile', 'accepted'), ('locations', 'accepted'), ('minimum_work_format', 'accepted')] overall= accepted
United Kingdom formats= ['remote_worldwide'] locations= [{'country': None, 'city': None, 'format': 'remote_worldwide', 'excluded': False, 'worldwide': True}]
checks= [('profile', 'accepted'), ('locations', 'accepted'), ('minimum_work_format', 'accepted')] overall= accepted
```

**Ожидаемый результат:** Должно сохраниться excluded WorkLocation для US/GB, locations=rejected и overall=rejected. Сейчас формату worldwide доверяют, но исключение страны теряется.

**Влияние и границы доказательства:** Ложноположительное совпадение по запрещённой стране.

**Критерий закрытия:** Исключение сохраняется, locations для запрещённой страны rejected; не запрещённая страна не отклоняется.

### GEO-006. HH связывает два справочника по названиям

**Статус:** открыто. **Тип:** риск устойчивости. **Приоритет:** средний.

**Суть ошибки или ограничения:** Перевод в динамические ID HH зависит от совпадения текстовых подписей двух справочников. Смена допустимого label ломает перевод; текущий живой сбой не подтверждён.

**Затронутый код:** [HHSearch.prepare](../src/parsers/aggregators/hh/parser.py#L149), [resolve_city](../src/parsers/aggregators/hh/geography.py#L99).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Архитектурная хрупкость; не доказанный сбой текущего HH справочника.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
import asyncio
from src.models.brief import BriefInput
from src.models.filters import SearchPlace
from src.parsers.models import Place
from src.parsers.aggregators.hh.parser import HHSearch
from src.parsers.aggregators.hh.geography import resolve_city
class FakeGeography:
    async def countries(self,fetch):
        return [Place(id='113',name='Russia')]
    async def cities(self,fetch,country_id,text):
        return [Place(id='1',name='Moscow')]
async def no_fetch(url):
    raise AssertionError(url)
async def main():
    brief=BriefInput(name='geo repro',queries=['QA'],filters={'locations':[{'country':'RU'}]})
    try:
        await HHSearch(FakeGeography()).prepare(brief,no_fetch)
    except ValueError as exc:
        print('country label mismatch:',exc)
    try:
        resolve_city([Place(id='1',name='Moscow')],SearchPlace(country='RU',city='Москва'))
    except ValueError as exc:
        print('city alias mismatch:',exc)
asyncio.run(main())
PY
```

**Фактический результат:**

```text
country label mismatch: HH cannot resolve country Россия
city alias mismatch: HH: не удалось однозначно сопоставить город Москва, регион: не указан
```

**Ожидаемый результат:** При контракте, допускающем иные подписи, идентичность страны/города должна переживать изменение label. Текущая live ошибка этими mock-ответами не доказана.

**Влияние и границы доказательства:** При изменении подписей поиск завершается ошибкой подготовки вместо корректного разрешения места.

**Критерий закрытия:** Принятая схема идентичности выдерживает допустимые алиасы и сохраняет динамические HH ID; неоднозначность остаётся явной.

### GEO-007. Идентичность города и регион теряются между этапами

**Статус:** открыто. **Тип:** пробел модели данных. **Приоритет:** средний.

**Суть ошибки или ограничения:** Сборка и модели не сохраняют ID города и регион вакансии. Разные места приходится повторно различать строками, а полученный штат невозможно передать в WorkLocation.

**Затронутый код:** [сборка справочника](../scripts/build_geography.py#L60), [SearchPlace](../src/models/filters.py#L19), [WorkLocation](../src/models/filters.py#L63).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Архитектурный пробел; не отдельная доказанная ошибка отбора.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
import gzip,json
from pathlib import Path
from pydantic import ValidationError
from src.models.filters import SearchPlace,WorkLocation
from src.geography.directory import City, uniquely_identifies_city
rows=json.loads(gzip.decompress(Path('src/geography/data/cities.json.gz').read_bytes()))
print('snapshot first row length:',len(rows[0]),'row:',rows[0])
print('City fields:',list(City.__dataclass_fields__))
print('SearchPlace fields:',list(SearchPlace.model_fields))
print('WorkLocation fields:',list(WorkLocation.model_fields))
from collections import Counter
groups=Counter(tuple(row[:3]) for row in rows)
print('duplicate groups / extra rows:',sum(n>1 for n in groups.values()),sum(n-1 for n in groups.values()))
print('Ararat rows:',[row[:4] for row in rows if row[0]=='AM' and row[1]=='Арарат'])
print('Ararat unique pair:',uniquely_identifies_city('AM','Арарат','Араратская область'))
for model,data in [(SearchPlace,{'country':'RU','city':'Москва','geonames_id':'524901'}),(WorkLocation,{'country':'US','city':'Springfield','region':'Illinois'})]:
    try:
        model(**data)
    except ValidationError as exc:
        print(model.__name__,[(x['loc'],x['type']) for x in exc.errors()])
PY
```

**Фактический результат:**

```text
snapshot first row length: 5 row: ['CY', 'Протарас', 'Фамагуста', 20230, ['Protaras', 'Протарас']]
City fields: ['name', 'region', 'population', 'aliases']
SearchPlace fields: ['country', 'city', 'region']
WorkLocation fields: ['country', 'city', 'format', 'excluded', 'worldwide']
duplicate groups / extra rows: 1851 2344
Ararat rows: [['AM', 'Арарат', 'Араратская область', 17598], ['AM', 'Арарат', 'Араратская область', 8287]]
Ararat unique pair: True
SearchPlace [(('geonames_id',), 'extra_forbidden')]
WorkLocation [(('region',), 'extra_forbidden')]
```

**Ожидаемый результат:** разные физические населённые пункты с одной парой name/region должны
оставаться различимыми на заявленном уровне точности. Регион, полученный от источника, должен
проходить в факт вакансии, если его требуется проверять. Конкретную модель идентификаторов
и административную точность определяет план общего модуля.

**Влияние и границы доказательства:** Нельзя надёжно выразить одноимённые места и проверить регион результата.
Две реальные записи Арарат одного региона объединяются одной парой названий;
`uniquely_identifies_city` подтверждает уникальную пару, как заявлено в её docstring,
а не единственный физический населённый пункт. Это зависимость планов источников, а не требование угадать место.

**Критерий закрытия:** Выбранное место и полученный регион проходят весь путь без потери идентичности; добавлены проверки одноимённых мест.

### GEO-008. JOIN теряет смысл удалёнки только в конкретном городе

**Статус:** открыто. **Тип:** дефект нормализации смысла. **Приоритет:** высокий.

**Суть ошибки или ограничения:** JOIN remoteType=CITY превращается в country-wide remote. Город сохраняется строкой, но постфильтр его игнорирует и принимает другой город той же страны.

**Затронутый код:** [_listing](../src/parsers/ats/join/parser.py#L94), [_place_matches](../src/selection/filtering.py#L62).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый дефект смысловой нормализации; синтетический provider record.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.join.parser import _listing
from src.parsers.ats.models import Board
from src.models.filters import SearchFilters
from src.selection.filtering import evaluate
board=Board('https://join.com/companies/example','Example')
item={'id':1,'idParam':'1-qa-engineer','title':'QA Engineer','workplaceType':'remote','remoteType':'CITY','city':{'cityName':'Moscow','countryCode':'RU','countryName':'Russia'}}
v=_listing(item,board,board.url)
result=evaluate(v,SearchFilters(locations=[{'country':'RU','city':'Томск'}]),queries=['QA'])
print('locations=',[p.model_dump() for p in v.locations])
print('checks=',[(c.field,c.status) for c in result.checks],'overall=',result.status)
PY
```

**Фактический результат:**

```text
locations= [{'country': 'RU', 'city': 'Moscow', 'format': 'remote_country', 'excluded': False, 'worldwide': False}]
checks= [('profile', 'accepted'), ('locations', 'accepted')] overall= accepted
```

**Ожидаемый результат:** Сохранённое CITY-ограничение должно отклонять Томск. Сейчас city=Moscow сохранён, но mode=remote_country заставляет _place_matches игнорировать город.

**Влияние и границы доказательства:** Ложноположительный отбор вакансии remote только в конкретном городе.

**Критерий закрытия:** Томск отклоняется для remoteType=CITY Moscow, допустимый город принимается; country-wide remote не становится городским.

### GEO-009. Y Combinator обходит общий нормализатор кодов стран

**Статус:** открыто. **Тип:** дефект нормализации. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Парсер YC копирует UK напрямую, хотя общий запрос использует GB. Подходящая удалённая вакансия отклоняется по стране.

**Затронутый код:** [YCombinatorSearch._vacancy](../src/parsers/ats/ycombinator/parser.py#L68).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый дефект; синтетический embedded record.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.ycombinator.parser import YCombinatorSearch
from src.parsers.ats.models import Board
from src.models.filters import SearchFilters
from src.selection.filtering import evaluate
b=Board('https://www.ycombinator.com/companies/example/jobs','Example')
s=YCombinatorSearch(b,b.url)
v=s._vacancy({'id':1,'title':'QA Engineer','url':'/companies/example/jobs/test','location':'Remote (UK)'})
result=evaluate(v,SearchFilters(locations=[{'country':'GB'}]),queries=['QA'])
print('locations=',[p.model_dump() for p in v.locations])
print('checks=',[(c.field,c.status) for c in result.checks],'overall=',result.status)
PY
```

**Фактический результат:**

```text
locations= [{'country': 'UK', 'city': None, 'format': 'remote_country', 'excluded': False, 'worldwide': False}]
checks= [('profile', 'accepted'), ('locations', 'rejected')] overall= rejected
```

**Ожидаемый результат:** country=GB и locations=accepted. Сейчас UK копируется напрямую, а сравнение с GB даёт rejected.

**Влияние и границы доказательства:** Ложноотрицательный отбор для Великобритании.

**Критерий закрытия:** UK приводится к GB перед сравнением; произвольные две буквы не обходят общую проверку.

### GEO-010. Нативные фильтры ATS не связаны с общей географией запроса

**Статус:** открыто. **Тип:** пробел интеграции. **Приоритет:** средний.

**Суть ошибки или ограничения:** Разные общие условия locations дают одинаковый запрос ATS. Ручные source filters и перевод SearchFilters — разные возможности; автоматического перевода пока нет.

**Затронутый код:** [BoardSearch.prepare](../src/parsers/ats/common.py#L64), [Board.native_filters](../src/parsers/ats/models.py#L10).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый integration gap; не доказательство неверного результата.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
import asyncio
from src.models.brief import BriefInput
from src.parsers.ats.models import Board
from src.parsers.ats.breezy.parser import create_adapter
async def no_fetch(url):
    raise AssertionError(url)
async def main():
    source=create_adapter(Board('https://example.breezy.hr/','Example'))
    for country,city in [('RU','Москва'),('GB','London')]:
        brief=BriefInput(name='geo repro',queries=['QA'],filters={'locations':[{'country':country,'city':city}]})
        plan=await source.search.prepare(brief,no_fetch)
        print(country,city,[r.url for r in plan.requests],plan.application.get('locations'))
asyncio.run(main())
PY
```

**Фактический результат:**

```text
RU Москва ['https://example.breezy.hr/'] local
GB London ['https://example.breezy.hr/'] local
```

**Ожидаемый результат:** Доказан одинаковый native запрос для разных common locations. Если source предоставляет безопасный native геофильтр, нужен translator либо явно выбранная стратегия postfilter.

**Влияние и границы доказательства:** Не используется нативное сужение. При надёжном постфильтре одинаковый запрос сам по себе не означает неверный результат.

**Критерий закрытия:** Для каждого источника определён нативный перевод либо подтверждённый маршрут постфильтрации; широкая ветвь не помечается точной.

### GEO-011. Наличие строки локации ошибочно принимать за готовую постфильтрацию

**Статус:** открыто. **Тип:** пробел извлечения. **Приоритет:** средний.

**Суть ошибки или ограничения:** Ряд адаптеров хранит location для отображения без нормализованных страны и города. Наличие строки не делает общий географический постфильтр работоспособным.

**Затронутый код:** [JazzHR](../src/parsers/ats/jazzhr/parser.py#L43), [Jobvite](../src/parsers/ats/jobvite/parser.py#L72), [Teamtailor](../src/parsers/ats/teamtailor/parser.py#L62), [Greenhouse](../src/parsers/ats/greenhouse/parser.py#L129), [Workday](../src/parsers/ats/workday/parser.py#L160), [RMK](../src/parsers/ats/successfactors/parser.py#L237).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённые пробелы извлечения; несколько сохранённых repo fixtures + синтетические Workday/RMK.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from importlib import import_module
import json
from src.parsers.ats.models import Board
from src.models.vacancy import Vacancy
from src.parsers.ats.workday.parser import WorkdayDetails
from src.parsers.ats.successfactors.parser import RecruitingMarketingSearch
cases=[('jazzhr','foundation-ai/success','response.html'),('jobvite','visionist/success','response.html'),('teamtailor','tradingview/success','response.html'),('greenhouse','jetbrains','response.json')]
for platform,case,filename in cases:
    root=Path('tests/fixtures/ats')/platform/case
    meta=json.loads((root/'meta.json').read_text())
    source=import_module('src.parsers.ats.'+platform+'.parser').create_adapter(Board(meta['captured_url'],platform))
    v=source.search.parse((root/filename).read_text(),source.search.build('')).vacancies[0]
    print(platform,'location=',v.location,'normalized=',[p.model_dump() for p in v.locations])
v=Vacancy(id='1',title='QA Engineer',url='https://example.wd1.myworkdayjobs.com/External/job/x/QA_1')
raw={'jobPostingInfo':{'jobDescription':'<p>QA work</p>','location':'Boston, Massachusetts, United States','country':{'descriptor':'United States'}}}
x=WorkdayDetails('https://example.wd1.myworkdayjobs.com','example','External').parse(json.dumps(raw),v)
print('workday','location=',x.location,'normalized=',[p.model_dump() for p in x.locations])
s=RecruitingMarketingSearch(Board('https://jobs.example.com/search/','Example',{'locale':'en_GB'}))
y=s._vacancy({'response':{'id':'1','unifiedUrlTitle':'qa','unifiedStandardTitle':'QA Engineer','jobLocationShort':['London, United Kingdom']}},'en_GB')
print('successfactors-rmk','location=',y.location,'normalized=',[p.model_dump() for p in y.locations])
PY
```

**Фактический результат:**

```text
jazzhr location= Hyderabad, India normalized= []
jobvite location= Fort Meade, MD normalized= []
teamtailor location= Cyprus, Tbilisi normalized= []
greenhouse location= Shanghai, China normalized= []
workday location= Boston, Massachusetts, United States normalized= [{'country': 'US', 'city': None, 'format': None, 'excluded': False, 'worldwide': False}]
successfactors-rmk location= London, United Kingdom normalized= []
```

**Ожидаемый результат:** Наблюдение: строки сохранены, но отдельные country/city не получены. Надёжный country/city postfilter требует source structured fields, directory resolution с ambiguity handling либо точный native context.

**Влияние и границы доказательства:** Условие остаётся unknown или не поддержанным; нужна нормализация фактов либо подтверждение точного запроса.

**Критерий закрытия:** Для каждого заявленного условия известен источник факта: список, карточка, текст либо точный запрос; неизвестное не подменяется выводом.

### GEO-012. Нет использования точности конкретного запроса как доказательства условия

**Статус:** открыто. **Тип:** пробел контекста запроса. **Приоритет:** средний.

**Суть ошибки или ограничения:** Контекст точного нативного запроса не поступает в FilterStage. Точное условие и широкая категория одинаково остаются unknown, если источник не повторяет факт в вакансии.

**Затронутый код:** [SearchPlan](../src/parsers/interfaces.py#L72), [SourceRun](../src/engine/source_run.py#L29), [FilterStage](../src/engine/filter_stage.py#L16).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый архитектурный пробел; синтетический контекст, без утверждения о native semantics example.com.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from dataclasses import fields
import inspect
from src.parsers.interfaces import SearchPlan,SearchRequest
from src.engine.filter_stage import FilterStage
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
f=SearchFilters(locations=[{'country':'GB','city':'London'}])
v=Vacancy(id='1',title='QA Engineer',url='https://example.com/jobs/1')
print('SearchPlan fields:',[x.name for x in fields(SearchPlan)])
print('SearchRequest fields:',[x.name for x in fields(SearchRequest)])
print('FilterStage signature:',inspect.signature(FilterStage))
for name,url in [('synthetic exact-city','https://example.com/search?city=London'),('synthetic broad-country','https://example.com/search?country=GB')]:
    plan=SearchPlan((SearchRequest(url,'QA'),),{'locations':'native'})
    stage=FilterStage(f,queries=('QA',))
    result=stage.evaluate(v)
    print(name,plan.application,next(c.status for c in result.checks if c.field=='locations'))
PY
```

**Фактический результат:**

```text
SearchPlan fields: ['requests', 'application', 'notes']
SearchRequest fields: ['url', 'query', 'stage', 'location', 'method', 'json_body', 'form_body']
FilterStage signature: (filters: src.models.filters.SearchFilters, queries: tuple[str, ...] = (), unsupported: frozenset[str] = frozenset()) -> None
synthetic exact-city {'locations': 'native'} unknown
synthetic broad-country {'locations': 'native'} unknown
```

**Ожидаемый результат:** Для реально подтверждённой exact-ветви должен существовать request-specific evidence; broad-ветвь при этом остаётся unknown до локальной проверки. Этот snippet доказывает отсутствие канала контекста, не доказывает, что произвольный city query реально exact.

**Влияние и границы доказательства:** Теряется подтверждение уже применённого условия; небезопасно исправлять безусловным доверием всем native/HTTP200.

**Критерий закрытия:** Точное подтверждение относится к конкретному условию и ветви; широкая ветвь не наследует его автоматически.

### GEO-013. Каталог выбора стран используется как полный справочник нормализации

**Статус:** открыто. **Тип:** ограничение покрытия и зависимостей. **Приоритет:** средний.

**Суть ошибки или ограничения:** Один список из 32 стран одновременно определяет продуктовый выбор, валидирует запрос, ограничивает сборку городов и участвует в распознавании внешних стран.

**Затронутый код:** [COUNTRIES](../src/geography/countries.py#L4), [COUNTRY_EN](../src/parsers/normalization.py#L25), [SearchPlace](../src/models/filters.py#L24).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Дизайн/coverage issue, частично дублирует GEO003; сам input limit не обязательно баг.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from pydantic import ValidationError
from src.geography.countries import COUNTRIES
from src.models.filters import SearchPlace
from src.parsers.normalization import COUNTRY_EN
from src.parsers.ats.normalization import country_code
print('country count=',len(COUNTRIES),'english count=',len(COUNTRY_EN))
import gzip,json
from pathlib import Path
rows=json.loads(gzip.decompress(Path('src/geography/data/cities.json.gz').read_bytes()))
print('snapshot country keys equal catalog=',{r[0] for r in rows}==set(COUNTRIES))
print('IN in product catalog=', 'IN' in COUNTRIES)
print('normalize India=',country_code('India'),'normalize IN=',country_code('IN'))
try:
    SearchPlace(country='IN',city='Mumbai')
except ValidationError as exc:
    print('SearchPlace IN:',[(e['loc'],e['msg']) for e in exc.errors()])
print('sample paired labels=',[(k,COUNTRIES[k],COUNTRY_EN[k]) for k in ['RU','GB','US']])
PY
```

**Фактический результат:**

```text
country count= 32 english count= 32
snapshot country keys equal catalog= True
IN in product catalog= False
normalize India= None normalize IN= IN
SearchPlace IN: [(('country',), 'Value error, Choose a country from the catalog')]
sample paired labels= [('RU', 'Россия', 'Russia'), ('GB', 'Великобритания', 'UK'), ('US', 'США', 'USA')]
```

**Ожидаемый результат:** Ограниченный product catalog можно сохранить, но внешний валидный country/label нужно нормализовать независимо от выбора стран пользователем. Порядковая zip-связь списков наблюдается в src/parsers/normalization.py, но текущие пары не перепутаны.

**Влияние и границы доказательства:** IN/EE/ZA и другие назначенные страны нельзя задать в текущем запросе; в bundled snapshot также нет их городов. Неполнота внешней нормализации описана отдельно в GEO-003.

**Критерий закрытия:** Продуктовый каталог отделён от валидации и нормализации стран; данные городов имеют явное покрытие. Расширение одной структуры не требует угадывать порядок английских названий.

### GEO-014. Часть парсеров предполагает конкретный формат строки локации

**Статус:** открыто. **Тип:** ограничение покрытия. **Приоритет:** средний.

**Суть ошибки или ограничения:** Часть парсеров предполагает один строковый формат места и выдаёт неполные либо смешанные country/city. Это ограничение покрытия контрактов, а не доказанный сбой на всех ATS.

**Затронутый код:** [Breezy](../src/parsers/ats/breezy/parser.py#L50), [Taleo](../src/parsers/ats/taleo/parser.py#L204), [Workable Markdown](../src/parsers/ats/workable/parser.py#L143), [iCIMS](../src/parsers/ats/icims/parser.py#L48).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Ограничение покрытия; синтетические строки, не доказанные live regression всех четырёх ATS.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from bs4 import BeautifulSoup
from src.parsers.ats.models import Board
from src.parsers.ats.breezy.parser import create_adapter as breezy
from src.parsers.ats.taleo.parser import TaleoSearch
from src.parsers.ats.workable.parser import create_adapter as workable
from src.parsers.ats.icims.parser import create_adapter as icims
s=breezy(Board('https://example.breezy.hr/','Example')).search
html='<li class="position"><a href="/p/qa"><h2>QA Engineer</h2></a><div class="location"><span>Boston, Massachusetts, United States</span></div></li>'
v=s.parse(html,s.build('')).vacancies[0]
print('Breezy',v.location,[p.model_dump() for p in v.locations])
b=Board('https://phh.tbe.taleo.net/phh01/ats/careers/v2/jobSearch?org=EXAMPLE&cws=1','Example')
s=TaleoSearch(b,b.url)
for value in ['Berlin, Germany','Berlin, DE']:
    row=BeautifulSoup('<div><div><a class="viewJobLink" href="requisition.jsp?rid=1">QA Engineer</a></div><div>'+value+'</div></div>','html.parser').div
    v=s._vacancy(row,{2:'Location'})
    print('Taleo',value,[p.model_dump() for p in v.locations])
s=workable(Board('https://apply.workable.com/example/','Example')).search
md='| Title | Location | Details |\n| --- | --- | --- |\n| QA Engineer | Portland, Oregon, United States | [View](https://apply.workable.com/example/jobs/view/ABC.md) |'
v=s.parse(md,s.build('')).vacancies[0]
print('Workable Markdown',v.location,[p.model_dump() for p in v.locations])
s=icims(Board('https://example.icims.com/jobs/search','Example')).search
h='<ul class="iCIMS_JobsTable"><li class="iCIMS_JobCardItem"><a class="iCIMS_Anchor" href="/jobs/1/qa/job"><h3>QA Engineer</h3></a><dt class="iCIMS_JobHeaderField">Job Locations</dt><dd>UK-London</dd></li></ul>'
v=s.parse(h,s.build('')).vacancies[0]
print('iCIMS',v.location,[p.model_dump() for p in v.locations])
PY
```

**Фактический результат:**

```text
Breezy Boston, Massachusetts, United States [{'country': None, 'city': 'Boston', 'format': None, 'excluded': False, 'worldwide': False}]
Taleo Berlin, Germany []
Taleo Berlin, DE [{'country': None, 'city': 'Berlin', 'format': None, 'excluded': False, 'worldwide': False}]
Workable Markdown Portland, Oregon, United States [{'country': 'US', 'city': 'Portland, Oregon', 'format': None, 'excluded': False, 'worldwide': False}]
iCIMS UK-London []
```

**Ожидаемый результат:** Показанные факты нельзя считать общим parser географии. Workable сохраняет регион внутри city; Taleo требует city, XX и не получает страну; Breezy не получает явную страну; iCIMS теряет two-component location.

**Влияние и границы доказательства:** Регион может попасть внутрь city, страна потеряться; точность постфильтра нельзя предполагать без проверки формата источника.

**Критерий закрытия:** Поддерживаемые форматы описаны и проверены; непокрытые строки не создают ложных фактов. Live iCIMS отдельно закрывается по GEO-015.

### GEO-015. iCIMS теряет двухкомпонентные и множественные локации

**Статус:** открыто. **Тип:** дефект разбора. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Текущие строки iCIMS UK-London/UK-Swansea не разбираются; в множественной строке остаётся только последнее место.

**Затронутый код:** [IcimsSearch.parse](../src/parsers/ats/icims/parser.py#L23).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый live defect; автономная synthetic копия наблюдавшихся строк.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.models import Board
from src.parsers.ats.icims.parser import create_adapter
s=create_adapter(Board('https://careers-starr.icims.com/jobs/search?in_iframe=1&ss=1','Starr')).search
for raw in ['UK-London','UK-Swansea','UK-Swansea | UK-London']:
    h='<ul class="iCIMS_JobsTable"><li class="iCIMS_JobCardItem"><a class="iCIMS_Anchor" href="/jobs/1/qa/job"><h3>QA Engineer</h3></a><dt class="iCIMS_JobHeaderField">Job Locations</dt><dd>'+raw+'</dd></li></ul>'
    v=s.parse(h,s.build('')).vacancies[0]
    print(raw,'->',[p.model_dump() for p in v.locations])
PY
```

**Фактический результат:**

```text
UK-London -> []
UK-Swansea -> []
UK-Swansea | UK-London -> [{'country': 'GB', 'city': 'London', 'format': None, 'excluded': False, 'worldwide': False}]
```

**Ожидаемый результат:** UK-London→GB/London; UK-Swansea→GB/Swansea; multi→две независимые локации GB/Swansea и GB/London.

**Влияние и границы доказательства:** Теряются реальные места вакансии и возможность точной постфильтрации.

**Критерий закрытия:** Все три строки дают ожидаемые отдельные GB/London и GB/Swansea без потерь.

### GEO-016. Teamtailor не извлекает локацию и формат из текущего шаблона карточек

**Статус:** открыто. **Тип:** дефект разбора. **Приоритет:** средний.

**Суть ошибки или ограничения:** Изменившийся шаблон Teamtailor теряет раньше извлекаемые location и work_formats. Отсутствие нормализованных locations — отдельный давний пробел, не результат этого селектора.

**Затронутый код:** [TeamtailorSearch.parse](../src/parsers/ats/teamtailor/parser.py#L30).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый live defect; минимальная копия реальной карточки8118064.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.models import Board
from src.parsers.ats.teamtailor.parser import create_adapter
s=create_adapter(Board('https://career.teamtailor.com/jobs','Teamtailor')).search
h='<li class="w-full"><div><a href="https://career.teamtailor.com/jobs/8118064-uk-account-executive-smb">UK Account Executive - SMB</a><div class="text-md mt-4"><span>Sales</span><span>·</span><span>London</span><span>·</span><span class="inline-flex">Hybrid<i class="fas fa-wifi"></i></span></div></div></li>'
v=s.parse(h,s.build('')).vacancies[0]
print('id=',v.id,'location=',v.location,'work_formats=',v.work_formats,'locations=',[p.model_dump() for p in v.locations])
print('metadata=',v.source_data)
PY
```

**Фактический результат:**

```text
id= 8118064 location= None work_formats= [] locations= []
metadata= {'platform': 'teamtailor', 'id': '8118064', 'department': None, 'workplace': None}
```

**Ожидаемый результат:** Минимум восстановить location=London и work_formats=[hybrid] — прежняя функциональность template. Нормализовать country из London без дополнительных надёжных данных нельзя.

**Влияние и границы доказательства:** у всех 14 проверенных карточек location=None и work_formats=[];
у 11 из них исходный HTML явно содержит Hybrid, у остальных трёх метка формата отсутствует.
Потеря явного формата доказана для 11, потеря отображаемого места — для 14. Подсчёт относится к снимку.

**Критерий закрытия:** Текущий и прежний шаблоны сохраняют location и format; страна London не выдумывается без дополнительных данных.

### GEO-017. Huntflow отклоняет действующие доски на `.global.huntflow.io`

**Статус:** открыто. **Тип:** дефект поддержки адресов. **Приоритет:** высокий.

**Суть ошибки или ограничения:** Конструктор Huntflow отклоняет действующие native-доски с дополнительным компонентом .global до выполнения HTTP-запроса.

**Затронутый код:** [create_adapter](../src/parsers/ats/huntflow/parser.py#L56).

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый URL coverage defect; current live boards ранее отдали200.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from src.parsers.ats.models import Board
from src.parsers.ats.huntflow.parser import create_adapter
for url in ['https://cryptopay.global.huntflow.io/','https://b2broker.global.huntflow.io/']:
    try:
        source=create_adapter(Board(url,'Example'))
        print(url,'accepted',source.search.build('').url)
    except ValueError as exc:
        print(url,type(exc).__name__,str(exc))
PY
```

**Фактический результат:**

```text
https://cryptopay.global.huntflow.io/ ValueError Expected a native Huntflow board root URL
https://b2broker.global.huntflow.io/ ValueError Expected a native Huntflow board root URL
```

**Ожидаемый результат:** Обе подтверждённые native boards должны успешно создавать адаптер при проверке разрешённого hostname/path. Сейчас ValueError возникает до HTTP.

**Влияние и границы доказательства:** Недоступны сами вакансии и любые дальнейшие фильтры этих досок.

**Критерий закрытия:** Оба native URL создают адаптер, а посторонние домены/неподдержанные пути продолжают отклоняться.

### GEO-018. Подтверждённые нативные геофильтры не доступны через интерфейс адаптеров

**Статус:** открыто. **Тип:** пробел интерфейса. **Приоритет:** средний.

**Суть ошибки или ограничения:** Подтверждённые возможности геофильтрации нельзя задать через текущий интерфейс адаптера. Для JSON-фильтров мешает также строковая модель Board.native_filters.

**Затронутый код:** [проверка параметров BoardSearch](../src/parsers/ats/common.py#L43), [модель Board](../src/parsers/ats/models.py#L10); также create_adapter/build соответствующих платформ.

**Предусловия:** рабочая копия репозитория с установленной `.venv`; bundled-данные и fixture-файлы из этой копии. Подтверждённый transport gap, независим от автоперевода GEO010.

**Шаги воспроизведения:**

1. Открыть терминал в корне репозитория.
2. Выполнить блок ниже. Сеть не используется; примеры HTML/JSON внутри блока — минимальные заданные входы.

```bash
.venv/bin/python - <<'PY'
from importlib import import_module
from src.parsers.ats.models import Board
cases=[
 ('breezy','https://sa-global.breezy.hr/',{'country':'Australia'}),
 ('breezy','https://sa-global.breezy.hr/',{'remote':'hybrid'}),
 ('jazzhr','https://symend.applytojob.com/apply/jobs',{'s_city':'Calgary','s_state':'AB'}),
 ('jobvite','https://jobs.jobvite.com/liferay/jobs',{'l':'Spain, Madrid','r':'Europe'}),
 ('teamtailor','https://career.teamtailor.com/jobs',{'country':'Sweden','location':'London','remote_status_id':'hybrid'}),
 ('workable','https://apply.workable.com/entuitive/',{'workplace':['hybrid']}),
 ('smartrecruiters','https://careers.smartrecruiters.com/Dailymotion',{'locationType':'REMOTE'}),
 ('icims','https://careers-starr.icims.com/jobs/search',{'searchLocation':'--London'}),
]
for platform,url,params in cases:
    try:
        source=import_module('src.parsers.ats.'+platform+'.parser').create_adapter(Board(url,'Example',params))
        print(platform,'accepted')
    except ValueError as exc:
        print(platform,str(exc))
for platform,url,value in [('workday','https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite',{'appliedFacets':{'locationHierarchy1':['source-specific-id']}}),('workable','https://apply.workable.com/entuitive/',{'location':[{'country':'Canada','region':'Ontario','city':'Toronto'}]})]:
    try:
        Board(url,'Example',value)
    except ValueError as exc:
        print(platform,'JSON-shaped Board.native_filters rejected:',exc)
from src.parsers.ats.icims.parser import create_adapter as icims
s=icims(Board('https://careers-starr.icims.com/jobs/search?searchLocation=--London','Starr')).search
print('iCIMS URL workaround:',s.build('').url)
from src.parsers.ats.greenhouse.parser import create_adapter as greenhouse
try:
    greenhouse(Board('https://boards-api.greenhouse.io/v1/boards/dept/offices/227972','DEPT'))
except ValueError as exc:
    print('Greenhouse selected office endpoint:',exc)
PY
```

**Фактический результат:**

```text
breezy Unsupported native filters: country
breezy Unsupported native filters: remote
jazzhr Unsupported native filters: s_city, s_state
jobvite Unsupported native filters: l, r
teamtailor Unsupported native filters: country, location, remote_status_id
workable Unsupported native filters: workplace
smartrecruiters Unsupported native filters: locationType
icims Unsupported native filters: searchLocation
workday JSON-shaped Board.native_filters rejected: Native filters require named, nonempty string values
workable JSON-shaped Board.native_filters rejected: Native filters require named, nonempty string values
iCIMS URL workaround: https://careers-starr.icims.com/jobs/search?searchLocation=--London&in_iframe=1&ss=1
Greenhouse selected office endpoint: Expected a Greenhouse board URL or public board API URL
```

**Ожидаемый результат:** Поддержать только подтверждённые source contracts, реальный тип JSON/form тела, допустимые значения и перенос pagination. iCIMS workaround query URL уже работает; это не полная невозможность native filtering.

**Влияние и границы доказательства:** Невозможно использовать подтверждённое сужение даже вручную; workaround iCIMS через query URL уже работает.

**Критерий закрытия:** Подтверждённые параметры/endpoint/body доступны с реальными значениями и сохранением пагинации; неподтверждённые ключи не разрешаются произвольно.

### GEO-019. Список типов региона несовместим с собственной нормализацией

**Статус:** открыто. **Тип:** дефект сопоставления и ограничение языкового покрытия. **Приоритет:** средний.

**Суть ошибки:** `region_key` удаляет четыре русских слова, но делает это после Unicode-нормализации.
Она превращает `край` в `краи`, поэтому этот элемент списка фактически не работает.
Остальной список также не является универсальным справочником административных типов и алиасов.

**Затронутый код:** [normalize и region_key](../src/geography/names.py#L7),
[HH matches_place](../src/parsers/aggregators/hh/geography.py#L83).

**Предусловия:** установленная `.venv`, корень репозитория. Внешние варианты без «край» и с Republic
ниже заданы синтетически; текущая живая ошибка HH с такими подписями не заявляется.

**Шаги воспроизведения:**

1. Выполнить локальные вызовы нормализации и сравнения.

```bash
.venv/bin/python - <<'PY'
from src.geography.names import normalize, region_key, region_matches
print(normalize('край'))
print(sorted(region_key('Краснодарский край')))
for a,b in [('Краснодарский край','Краснодарский'),
            ('Хакасия Республика','Хакасия'), ('Khakassia Republic','Khakassia')]:
    print(a,b,region_matches(a,b))
PY
```

**Фактический результат:** `краи`; ключ `['краи','краснодарскии']`;
для трёх пар соответственно False / True / False.

**Ожидаемый результат:** объявленное правило удаления «край» должно быть согласовано с нормализацией.
Для английских/межъязыковых подписей нужен установленный источник административной идентичности
либо явное unknown; четыре русских слова не подтверждают универсальное равенство регионов.

**Влияние и границы доказательства:** одинаковые допустимые подписи могут не сопоставиться;
в bundled-данных есть Краснодарский/Пермский край. Коллизий разных сохранённых регионов только
из-за этого stoplist не найдено. Москва и Московская область этим примером не приравниваются.

**Критерий закрытия:** проверены согласованность нормализации, допустимые региональные алиасы
и отказ от подтверждения неоднозначного места; словарь не пополняется только значениями одной выборки.

### GEO-020. Поисковый Unicode-ключ принимается за идентичность разных городов

**Статус:** открыто. **Тип:** дефект сопоставления. **Приоритет:** высокий.

**Суть ошибки:** разные реальные записи Yên Phụ/Hanoi и Yên Phú/Tuyen Quang после снятия диакритики
получают один ключ. Для допустимого запроса без региона постфильтр подтверждает совпадение.

**Затронутый код:** [city_matches](../src/geography/names.py#L28),
[match_city](../src/geography/directory.py#L81).

**Предусловия:** текущий bundled snapshot, установленная `.venv`, корень репозитория.
Вакансия синтетическая; её два названия взяты из разных реальных записей локального справочника.

**Шаги воспроизведения:**

1. Проверить исходные записи и сравнить общий фильтр с регионом и без него.

```bash
.venv/bin/python - <<'PY'
import gzip,json
from pathlib import Path
from src.geography.directory import match_city
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.selection.filtering import evaluate
rows=json.loads(gzip.decompress(Path('src/geography/data/cities.json.gz').read_bytes()))
print([r[:4] for r in rows if r[0]=='VN' and r[1] in ('Yên Phụ','Yên Phú')])
v=Vacancy(id='repro',title='QA',url='https://example.org/job',work_formats=['office'],
          locations=[{'country':'VN','city':'Yên Phú','format':'office'}])
for region in (None,'Hanoi'):
    f=SearchFilters(locations=[{'country':'VN','city':'Yên Phụ','region':region}])
    result=evaluate(v,f)
    print(region,match_city('VN','Yên Phụ','Yên Phú',region),
          next(c.status for c in result.checks if c.field=='locations'))
PY
```

**Фактический результат:** разные исходные регионы; без региона `True, accepted`,
с регионом Hanoi — `None, unknown`.

**Ожидаемый результат:** снятие диакритики может помогать поиску, но не должно само по себе
доказывать равенство разных мест. Без достаточного уточнения совпадение не подтверждается.

**Влияние и границы доказательства:** ложноположительный отбор для запроса без региона.
Не заявляется, что UI-запрос с сохранённым Hanoi тоже всегда принимается.
`й`/`и` также объединяются, но отдельной реальной RU-пары только с таким различием в снимке не найдено.

**Критерий закрытия:** различаются поиск по неточному ключу и проверка идентичности;
две приведённые записи не дают подтверждённого совпадения без разрешения неоднозначности.

### GEO-021. Лимит подсказок делает часть одноимённых городов недоступной для выбора

**Статус:** открыто. **Тип:** дефект доступности выбора. **Приоритет:** средний.

**Суть ошибки:** подсказки ограничены 20 результатами, ищут только алиасы города и не имеют
пагинации/отдельного уточнения региона. Даже полное точное имя не даёт доступ к остальным вариантам.

**Затронутый код:** [CityDirectory.search](../src/geography/directory.py#L37),
[GeographyService.cities](../src/services/geography.py#L12).

**Предусловия:** текущий bundled snapshot, установленная `.venv`, корень репозитория.

**Шаги воспроизведения:**

1. Посчитать регионы точного имени Clinton и сравнить с доступными подсказками.
2. Попробовать уточнить скрытый вариант регионом в строке поиска.

```bash
.venv/bin/python - <<'PY'
import gzip,json
from pathlib import Path
from src.geography.directory import CityDirectory
rows=json.loads(gzip.decompress(Path('src/geography/data/cities.json.gz').read_bytes()))
c=CityDirectory()
all_rows=[r for r in rows if r[0]=='US' and r[1]=='Клинтон']
offered=c.search('US','Clinton')
shown={(r['name'],r['region']) for r in offered}
print('all regions',len({r[2] for r in all_rows}),'offered',len(offered))
print('hidden',[(r[2],r[4]) for r in all_rows if (r[1],r[2]) not in shown])
print('qualified query',c.search('US','Clinton Washington'))
PY
```

**Фактический результат:** 24 региона против 20 подсказок; скрыты Кентукки, Огайо, Монтана,
Вашингтон. Алиасы этих записей — только Clinton/Клинтон; qualified query возвращает [].

**Ожидаемый результат:** пользователь может получить нужный вариант точного имени
с уточнением региона либо продолжением выдачи. Ограниченный размер одной страницы допустим.

**Влияние и границы доказательства:** нельзя выбрать четыре существующих варианта через подсказки.
Ручная запись country/city/region в JSON брифа остаётся возможной; полного запрета API создания нет.

**Критерий закрытия:** все 24 разные пары доступны через поддержанный интерфейс уточнения/продолжения,
без необходимости угадывать внутренний порядок по населению.

### GEO-022. Сборщик удаляет регион по одному совпадению названия с городом

**Статус:** открыто. **Тип:** дефект сборки данных. **Приоритет:** средний.

**Суть ошибки:** правило с комментарием о федеральных городах применяется ко всем странам:
`region_name == name` безусловно заменяет регион на None. Город с названием штата/провинции/кантона
не обязательно является отдельным административным исключением.

**Затронутый код:** [build](../scripts/build_geography.py#L57).

**Предусловия:** установленная `.venv`, корень репозитория. Команда создаёт минимальные
синтетические GeoNames-файлы только во временном каталоге и удаляет их после выполнения.

**Шаги воспроизведения:**

1. Создать город Utrecht с явно заданным admin1 Utrecht и запустить настоящий сборщик.
2. Сравнить результат с явным административным полем входных данных.

```bash
.venv/bin/python - <<'PY'
import gzip,json,tempfile
from datetime import date
from pathlib import Path
from zipfile import ZipFile
from scripts.build_geography import build
with tempfile.TemporaryDirectory() as name:
    source=Path(name)
    row=['1','Utrecht','Utrecht','','52.09','5.12','P','PPLA','NL','','09','','','',
         '376435','','','Europe/Amsterdam','2026-10-21']
    with ZipFile(source/'cities500.zip','w') as z:
        z.writestr('cities500.txt','\t'.join(row)+'\n')
    (source/'admin1CodesASCII.txt').write_text('NL.09\tUtrecht\tUtrecht\t2\n')
    with ZipFile(source/'alternateNamesV2.zip','w') as z:
        z.writestr('alternateNamesV2.txt','')
    output=source/'output'
    build(source,output,date(2026,10,21))
    print(json.loads(gzip.decompress((output/'cities.json.gz').read_bytes())))
PY
```

**Фактический результат:** `[['NL','Utrecht',None,376435,['Utrecht']]]`.
В реальном снимке этот механизм уже оставил без региона Нью-Йорк, Утрехт, Гронинген, Цуг
и другие города; соседние населённые пункты сохраняют одноимённые штаты/провинции/кантоны.

**Ожидаемый результат:** у Utrecht сохраняется явно заданный регион Utrecht.
Административное исключение определяется подтверждённым типом/идентичностью, а не одной подписью.

**Влияние и границы доказательства:** информация исчезает ещё при сборке, до парсеров;
для `uniquely_identifies_city('US','Нью-Йорк','Нью-Йорк')` результат False.
Живой HH-сбой именно этого случая не проверялся. Moscow/Berlin сами по себе не объявляются ошибками.

**Критерий закрытия:** сборщик сохраняет действительные регионы в приведённых случаях;
отдельно проверена семантика административных городов, пересобраны и проверены затронутые данные.

## Подробные возможности источников и доказательства

### Контрольные источники: HH и Getmatch

Эти два источника подключены к обычному реестру приложения. ATS рассматриваются отдельно.

| Источник | География самого источника | Что реализовано у нас | Что остаётся проверить по вакансии |
|---|---|---|---|
| HH | Иерархия стран, регионов и городов; поиск по `area`; отдельный формат удалёнки | Получение справочников и подсказок; перевод в `area`; ветви местного поиска и удалёнки | Доступность мировой удалёнки и явные ограничения стран в широкой удалённой ветви |
| Getmatch | Пять категорий: Москва, Петербург, другие города РФ, другие страны, удалёнка | Перевод в набор `l`; несколько категорий объединяются | Конкретный город внутри `regions_ru`; страна/город внутри `relocate`; разрешённая география удалёнки |

**HH: документация и запрос.** [Справочник регионов](https://github.com/hhru/api/blob/master/docs/areas.md)
описывает динамическое дерево и предупреждает о возможных изменениях значений.
[Поиск вакансий](https://api.hh.ru/openapi/redoc#tag/Poisk-vakansij/operation/get-vacancies)
принимает `area` и `work_format`. 21 октября прямой GET
[`/areas/1`](https://api.hh.ru/areas/1) вернул Москву с родителем `113`,
а [`/areas/countries`](https://api.hh.ru/areas/countries) — 165 записей справочника.
Это проверка справочника, не проверка полноты выдачи вакансий.

**Getmatch: интерфейс.** На [действующей странице вакансий](https://getmatch.ru/vacancies)
21 октября присутствует блок «Регион и формат работы» с перечисленными пятью категориями.
Сами категории — часть модели источника. Ошибка GEO-001 относится к переводу названий городов
в эти категории. `regions_ru` не означает всю Россию: для всей страны физические категории
Москвы, Петербурга и других городов объединяются.

### Сравнение всех 21 ATS

Вторая колонка описывает платформу или явно указанный вариант её интерфейса.
Третья — передачу географических параметров нашим кодом; «нет» означает отсутствие реализации,
а не отсутствие функции у поставщика. Четвёртая — факты, пригодные для общей постфильтрации.
В последней колонке стоят ссылки на подробные доказательства ниже.

Автоматического перевода `SearchFilters.locations` в параметры ATS сейчас нет у всех 21 адаптеров.
Даже поддержанные параметры третьей колонки задаются явно через `Board.native_filters`.

| ATS | География самой платформы | Геофильтр в нашем адаптере | География ответа у нас | Проверка |
|---|---|---|---|---|
| Ashby | Location и Location Type; интерфейс фильтрует список. Публичный серверный геофильтр ленты не подтверждён | Нет | Страна/город, несколько мест; регион теряется | [Документация, интерфейс](#ashby) |
| BambooHR | Геофильтры стандартных досок не подтверждены: проверенные доски недоступны. Корпоративный сайт BambooHR имеет свой фильтр | Нет | Страна/город, штат только в строке | [Ограничение проверки](#bamboohr) |
| Breezy | Country, Location, Remote Type; серверные фильтры подтверждены | Только `location`; нет `country` и `remote` | Частично город из строки; страна и несколько мест не извлекаются | [Интерфейс, запрос](#breezy) |
| Comeet | Множественный выбор Location, фильтрация полного списка в браузере | Нет | Страна/город; штат теряется | [Интерфейс, запрос](#comeet--spark-hire-recruit) |
| Dreamjob | Города отображаются; отдельный геофильтр вакансий на двух досках не обнаружен, возможность платформы не установлена | Нет | Город в списке; география JSON-LD из карточки при наличии | [Ограниченное наблюдение](#dreamjob) |
| Greenhouse | Иерархия офисов; публичный API вакансий выбранного офиса. Видимые фильтры зависят от доски | Нет; API офисов не используется | Строка локации и признаки формата; страна/город не нормализуются | [Документация, запрос](#greenhouse) |
| Huntflow | На двух досках фильтров не обнаружено. Корпоративная API-модель регионов не доказывает публичный геопоиск | Нет; проверенные `.global.huntflow.io` ещё и не принимаются конструктором | Страны из строки; города не определяются | [Ограниченное наблюдение](#huntflow) |
| iCIMS | Classic: Location; другие витрины: место, расстояние, Remote/Hybrid. Авторизованный Search API имеет city/state/country/ZIP/radius | Нет выделенного перевода; исходные query-параметры URL сохраняются | Страна/город только для части форматов; текущие двухкомпонентные и множественные строки теряются | [Документация, интерфейс, запрос](#icims) |
| JazzHR | Advanced: город и штат/провинция; серверные `s_city`, `s_state` | Нет | Только строка локации | [Интерфейс, запрос](#jazzhr) |
| Jobvite | Location и Region, включая офисы и произвольные группы; серверные `l`, `r` | Нет | Только строка; возможна примесь подразделения | [Документация, запрос](#jobvite) |
| JOIN | Location в интерфейсе компании; публичный серверный геофильтр не подтверждён. Редактор вакансии поддерживает географию remote | Нет | Страна/город; ограничение remote по городу интерпретируется неверно | [Интерфейс, модель данных](#join) |
| Lever | Location в JSON API; hosted HTML дополнительно фильтрует Location type | `location` в JSON API | Страна; города из меток не нормализуются | [Документация, запрос](#lever) |
| Personio | Location/office в настраиваемом интерфейсе, несколько значений и URL фильтра | Нет | Строка в списке; география JSON-LD из карточки | [Документация, интерфейс](#personio) |
| Recruitee | Country, City, Work model в CareersHub; геопараметры публичной ленты не подтверждены | Нет | Страна/город и несколько мест; регион теряется | [Интерфейс](#recruitee) |
| SmartRecruiters | Country, region, city и документированный Location type в публичном Posting API | `country`, `region`, `city`; нет `locationType` | Страна/город, признаки remote/hybrid; регион теряется | [Документация, запрос](#smartrecruiters) |
| SuccessFactors | Несколько вариантов RMK: Location, country/city-фасеты; по документации также расстояние. Legacy XML — отдельный фид | XML: нет. Новый RMK: `location` и геофасеты вручную; classic SSR не поддержан | XML: страна/город. Новый RMK: только строка | [Варианты платформы](#successfactors) |
| Taleo | TBE v2: поля State/Location/Region конкретной формы. Enterprise отдельно имеет иерархию мест и радиус | TBE v2: `location`, `region`, `state` при наличии в форме; Enterprise вне адаптера | Частично город из строки; страна/регион не структурируются | [Версии, форма, запрос](#taleo) |
| Teamtailor | Country, Region, Location, Remote Status, карта; публичные фильтры подтверждены | Нет | На текущей проверенной доске теряются даже строка и формат; нормализованные места не реализованы | [Документация, запрос](#teamtailor) |
| Workable | Location со страной/регионом/городом и Workplace type; публичные фильтры и справочник значений подтверждены | Нет, хотя используется тот же JSON endpoint | Страна/город из JSON; Markdown разбирается эвристикой | [Интерфейс, запрос](#workable) |
| Workday | Настраиваемые географические фасеты, Location/Distance и remote; CXS принимает ID фасетов конкретной доски | Нет: всегда `appliedFacets={}` | Страна из карточки; город не нормализуется | [Документация, запрос](#workday) |
| Y Combinator | У глобального jobs есть страницы городов/стран и Remote. Геофильтр company jobs на двух досках не подтверждён | Нет; адаптер работает с company jobs | Коды стран из Remote-строк; офисные места не нормализуются | [Разные области поиска](#y-combinator) |

У платформ есть больше возможностей, чем реализовано в адаптерах. Часть возможностей — серверные
фильтры, часть — фильтрация уже загруженного списка в браузере. Эти случаи требуют разных переводчиков.

### Доказательства возможностей ATS

#### Ashby

**Документация, интерфейс, запрос.** [Настройки доски](https://docs.ashbyhq.com/job-board-configuring-your-setup)
описывают настраиваемые фильтры. На [Harvey](https://jobs.ashbyhq.com/harvey) и
[Modal](https://jobs.ashbyhq.com/modal) наблюдались Location и Location Type. Выбор Bengaluru на Harvey
сократил список с 344 до 6 карточек и добавил `locationId` в URL.
Это подтверждает фильтрацию интерфейса, но не серверный фильтр публичной ленты.
[Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)
документирует полную ленту с `includeCompensation`.
[jobPosting.list](https://developers.ashbyhq.com/reference/jobpostinglist) имеет фильтр location,
но требует авторизацию и разрешение `jobsRead`.
Фильтрация по стране и городу может быть представлена настроенными локациями; отдельные универсальные
country/city-параметры публичной ленты не подтверждены.

#### BambooHR

**Документация; проверка двух досок ограничена недоступностью.**
[ATS API](https://documentation.bamboohr.com/reference/applicant-tracking) и
[Job Summaries](https://documentation.bamboohr.com/reference/get-job-summaries) описывают данные ATS
и авторизованный доступ. Из этого нельзя заключать наличие анонимного геопоиска.
[G2](https://g2.bamboohr.com/careers) и [Meedan](https://meedan.bamboohr.com/careers)
В первом проходе 21 октября показали страницу обслуживания, а `/careers/list` вернул 403.
При отдельном аудите формата/занятости тот же путь на проверенных досках возвращал 404 Route Not Found;
успешный действующий анонимный контракт ни в одном проходе не подтверждён.
Географические фильтры стандартных досок в текущей проверке **не подтверждены**.
[Корпоративный сайт самого BambooHR](https://www.bamboohr.com/careers/) показывает Job Location и формат
работы, но это отдельный интерфейс работодателя, который нельзя приписывать каждой native-доске BambooHR.

#### Breezy

**Документация, интерфейс, запрос.** На [sa.global](https://sa-global.breezy.hr/)
доступны отдельные Country, Location и Remote Type.
Проверены серверные GET-фильтры: `country=Australia` оставил 1 уникальную вакансию,
несуществующая страна — 0; `remote=hybrid` — 9, `remote=remote` — 7,
`remote=remote-location` — 6 против 37 уникальных вакансий исходного HTML.
Исходные 62 карточки не равны 62 вакансиям: несколько карточек могут вести на одну позицию.
Последние два remote-значения различают удалёнку без ограничения места и в выбранных местах.
[Настройки портала](https://help.breezy.hr/en/articles/5307157-customizing-your-career-portal) и
[несколько локаций](https://help.breezy.hr/en/articles/8891875-multiple-locations-for-positions)
описывают конфигурацию; [старый Simple layout](https://help.breezy.hr/en/articles/13833746-simple-career-portal-layout)
может отличаться. У нас разрешён только `location`.

#### Comeet / Spark Hire Recruit

**Документация, интерфейс, запрос.** На [Hypernative](https://www.comeet.com/jobs/hypernative/8A.00E)
и [Gett](https://www.comeet.com/jobs/gett/A0.002) есть фильтр Location.
На Hypernative это множественный выбор настроенных городов и стран. Выбор United States
уменьшил видимый список с 15 до 4 и создал `?location=United%20States`.
Однако HTTP-ответы без фильтра, с этим значением и с несуществующей локацией содержали
одни и те же 15 записей `COMPANY_POSITIONS_DATA`. Значит, добавление URL-параметра само по себе
не сузит выдачу нашего парсера: фильтрация здесь происходит в браузере.
[Careers API](https://developers.comeet.com/reference/careers-api-overview) требует UID компании и token;
[схема позиции](https://developers.comeet.com/reference/retrieve-a-position-new) содержит отдельные адресные поля.
Серверный геофильтр этого API не подтверждён.

#### Greenhouse

**Документация, интерфейс, запрос.** Платформа предоставляет публичную
[иерархию офисов и вакансии выбранного офиса](https://docs.greenhouse.io/job-board#offices).
На [DEPT](https://job-boards.greenhouse.io/dept) проверен
[`/offices`](https://boards-api.greenhouse.io/v1/boards/dept/offices): 101 запись.
[`/offices/227972`](https://boards-api.greenhouse.io/v1/boards/dept/offices/227972)
вернул 20 вакансий офиса CMNL против 185 вакансий общей ленты.
Это рабочий серверный путь отбора по офису, даже без универсального country/city-параметра `/jobs`.
На DEPT и [Attain](https://job-boards.greenhouse.io/attaintalent) наблюдался только текстовый поиск.
[Stripe](https://stripe.com/careers/search) имеет фильтр Office location на собственном сайте;
его интерфейс нельзя считать стандартным hosted UI Greenhouse.
Наш адаптер не использует ресурс офисов и не нормализует их иерархию.

#### Lever

**Документация, интерфейс, запрос.** [Официальный Postings API](https://github.com/lever/postings-api)
поддерживает фильтр `location`, в том числе несколько допустимых значений. Country и workplaceType
в документации этого JSON API не являются фильтруемыми полями.
На [Doxel](https://jobs.lever.co/doxel) API вернул 15 вакансий без ограничения,
1 для `location=Arlington, VA`, 0 для несуществующей локации.
При этом hosted HTML поддержал `workplaceType=remote` — 4 вакансии и `workplaceType=onsite` — 2.
Это разные способы доступа: параметр, работающий в HTML-интерфейсе, нельзя автоматически переносить
в JSON API. Наш адаптер использует JSON API и передаёт `location`.

#### Recruitee

**Документация, интерфейс, запрос полной ленты.** На [Redsky](https://redsky.recruitee.com/)
наблюдались вкладки городов, на [TransPerfect](https://transperfect.recruitee.com/) — страна,
город и Work model. Выбор London на Redsky создал параметр конкретного блока
`jobs-56b136cc[tab]=London`; дополнительного запроса ленты в проверенном действии не обнаружено.
Это не универсальный параметр `/api/offers`.
[Документация публичной ленты](https://docs.recruitee.com/reference/offers)
описывает department/tag, но не подтверждает серверные country/city-фильтры.
Полная лента Redsky вернула 21 позицию. Наш адаптер читает несколько отдельных локаций,
но не использует состояние геофильтров CareersHub и не сохраняет штат/регион.

#### Dreamjob

**Интерфейс и запрос; поддержка отдельного геофильтра не установлена.** Проверены
[Гринатом](https://dreamjob.ru/employers/32100/vakansii) и
[Банк ДОМ.РФ](https://dreamjob.ru/employers/104931/vakansii): видны текст, доход и сортировка,
но географические элементы формы не найдены ни в HTML, ни после загрузки JavaScript.
Это наблюдение двух досок, а не доказательство отсутствия функции в платформе.
В [руководстве](https://dreamjob.ru/docs/for_clients.pdf) фильтр города встречается и для других
разделов сайта; его нельзя автоматически приписывать вакансиям.
У [проверенной вакансии](https://dreamjob.ru/employers/32100/vakansii/138722188)
есть JSON-LD с `addressLocality=Москва`, но отсутствует `addressCountry`.
Домен `.ru` и название работодателя не использовались как доказательство страны.

#### Huntflow

**Интерфейс и запрос; публичный геопоиск не установлен.**
[Cryptopay](https://cryptopay.global.huntflow.io/) и [B2BROKER](https://b2broker.global.huntflow.io/)
вернули действующие доски без наблюдаемых поисковых контролов. Местоположение в карточках есть,
например `Remote (Europe)` и `Luxembourg / Remote`.
[Корпоративная API-модель](https://github.com/huntflow/api/blob/master/en/webhooks.md)
содержит `account_region` при включённых регионах;
[API вакансий](https://github.com/huntflow/api/blob/master/en/vacancies.md) относится к аккаунту.
Они не подтверждают анонимный фильтр карьерной доски.
Оба проверенных адреса не принимает наш конструктор из-за дополнительного `.global` — GEO-017.

#### iCIMS

**Документация, интерфейс, запрос.** На Classic-доске
[Starr](https://careers-starr.icims.com/jobs/search?in_iframe=1&ss=1)
форма отдаёт точные значения Location: `--London` и `--Swansea`.
GET с [`searchLocation=--London`](https://careers-starr.icims.com/jobs/search?in_iframe=1&ss=1&searchLocation=--London)
вернул 11 карточек из 17, несуществующее значение — 0.
Значения принадлежат форме конкретного работодателя, это не ISO-коды.
[Современная витрина iCIMS](https://careers.icims.com/careers-home/jobs) показывает место,
расстояние и Remote/Hybrid; [каталог мест](https://careers.icims.com/careers-home/jobs/locations)
различает города, штаты и страны. Это другой интерфейс, не контракт Classic `/jobs/search`.
[Search API](https://developer-community.icims.com/applications/applicant-tracking/search-api)
документирует country/state/city/ZIP/radius, но требует Integration User.
Существующие query-параметры iCIMS URL наш конструктор сохраняет; специального перевода из брифа нет.
Потеря двухкомпонентных и множественных локаций подтверждена в GEO-015.

#### JazzHR

**Интерфейс и запрос.** На [Symend](https://symend.applytojob.com/apply/jobs) и
[Codekeeper](https://codekeeper.applytojob.com/apply/jobs) Advanced раскрывает City or town и
State or province. GET-форма передаёт `form_id=advanced_search_jobs`, `s_city`, `s_state`.
На Symend исходно 2 вакансии;
[`s_city=Calgary`](https://symend.applytojob.com/apply/jobs?form_id=advanced_search_jobs&s_city=Calgary)
оставил 1, [`s_state=AB`](https://symend.applytojob.com/apply/jobs?form_id=advanced_search_jobs&s_state=AB)
— 1, несуществующий город — 0.
Положительная карточка отображает только `Remote`, хотя геопоиск находит её по Calgary/AB.
Значит, отображаемая строка не исчерпывает географические данные источника.
[Официальный материал о карьерной странице](https://www.jazzhr.com/onboarding/careers-page)
описывает варианты интеграции; точные геопараметры подтверждены живой формой.
На проверенных формах штаты/провинции относятся к США и Канаде, не ко всем регионам мира.

#### Jobvite

**Документация, интерфейс, запрос.**
[Hosted Page Filtering](https://careers.jobvite.com/careersite/iframe_filtering.html)
документирует `l` для Location, `r` для Region и несколько значений через `-%7C-`.
Location может означать офис, Region — страну, континент или другую группу работодателя.
На [Liferay](https://jobs.jobvite.com/liferay/jobs) исходно 18 вакансий;
[`l=Spain, Madrid`](https://jobs.jobvite.com/liferay/search?l=Spain%2C%20Madrid)
вернул 4, [`r=Europe`](https://jobs.jobvite.com/liferay/search?r=Europe) — 5.
Несуществующее `l=zxqnonexistent` вернуло все 18, хотя заголовок страницы показывал выбранную строку.
Это риск будущей интеграции: неизвестный параметр нельзя считать доказательством точного отбора.
У нас геофильтр отсутствует, а пагинация переносит только `q`; при добавлении `l/r` их нужно сохранять
на каждой странице. На проверенной доске строка локации также может включать подразделение.

#### JOIN

**Интерфейс; серверный геофильтр не подтверждён.**
На [собственной доске JOIN](https://join.com/companies/join) наблюдался Location / All locations
отдельно от подразделения и 5 вакансий. Значения выпадающего меню и влияние выбора на серверный
запрос не удалось надёжно проверить; наличие видимых городов в карточках не заменяет эту проверку.
Наш GraphQL использует `categoryIds`, который не является географическим фильтром.
Отдельно [документация создания вакансии](https://help.join.com/jobs/job-creation)
описывает Remote Worldwide / Region / Country, включая EMEA/APAC/DACH.
Это подтверждает модель допустимой географии, но не публичный поиск по региону.
GEO-008 относится к уже существующей локальной интерпретации `remoteType=CITY`.
Поддержку региональной удалёнки из новых полей нужно сверять с конкретным публичным ответом,
а не выводить из документации редактора.

#### Personio

**Документация и интерфейс.**
[Настройка карьерной страницы](https://support.personio.de/hc/en-us/articles/8758636857629-Set-up-the-Personio-career-page)
описывает Location filter и ссылку на отфильтрованный список.
На [deeploi](https://deeploi.jobs.personio.de/) видны Ort и варианты München/Berlin.
Выбор Berlin уменьшил список с 4 до 3 вакансий. URL получил `filters` с Base64-кодированным
JSON `{"office_id":[1601072]}`. Это проверенная сериализация состояния интерфейса,
а не универсальный ID Берлина и не доказанный отдельный серверный API.
На [Tandem](https://tandem.jobs.personio.de/) виден только Department: конфигурация зависит от работодателя.
[XML открытых позиций](https://developer.personio.de/docs/retrieving-open-job-positions)
и [FAQ](https://support.personio.de/hc/en-us/articles/29375445597725-Frequently-asked-questions-on-XML-job-integration)
описывают доступный без credentials фид с полем office, который требуется включить.
Наличие office в фиде не доказывает фильтр `/xml?office=...`.

#### SmartRecruiters

**Документация, интерфейс, запрос.**
[Текущий Posting API](https://developers.smartrecruiters.com/reference/v1listpostings)
документирует country, region, city и массив `locationType` со значениями
REMOTE/HYBRID/ONSITE/ANY. Публичные postings доступны без авторизации;
внутренние публикации имеют другой режим доступа.
На [Dailymotion](https://careers.smartrecruiters.com/Dailymotion) исходно 2 вакансии;
[публичный API](https://api.smartrecruiters.com/v1/companies/Dailymotion/postings?limit=100)
с `country=fr` вернул 2, `country=zz` — 0, `city=Paris` — 1.
`locationType=REMOTE` вернул 0; положительное совпадение remote на этой доске не проверено.
Позднейший [аудит формата](work-format-employment-review.md) получил на Nexthink
REMOTE17/HYBRID45/ONSITE6 из68: это отдельное положительное подтверждение.
У нас country/region/city уже передаются вручную, но `locationType` не разрешён.
Примеры параметров также есть в [описании endpoints](https://developers.smartrecruiters.com/docs/endpoints).

#### SuccessFactors

**Документация, интерфейс и несколько вариантов реализации.**
[SAP Search Settings](https://help.sap.com/docs/successfactors-recruiting/setting-up-and-maintaining-sap-successfactors-recruiting/career-site-admin-search-settings)
описывает Location по city/state/ZIP/country и расстояние; точность поиска по радиусу вне США ограничена.
[Настройки UDM](https://help.sap.com/docs/successfactors-recruiting/setting-up-and-maintaining-sap-successfactors-recruiting/configuring-layout-of-search-results-for-unified-data-model?locale=en-US&source=redirect)
описывают Location Search, карту и настраиваемые фасеты.
Legacy XML, classic RMK SSR и новый RMK endpoint — разные варианты доступа.

[Nordea](https://careers.nordea.com/search/) и
[Giesecke+Devrient](https://careers.gi-de.com/search/) вернули classic SSR.
У G+D видны country/city controls `optionsFacetsDD_country` и `optionsFacetsDD_city`.
GET с `DE` и с `Munich` изменил выдачу до 8 позиций, но точные допустимые значения
не были извлечены из динамического меню: это наблюдение изменения выдачи, не готовый переводчик.
[Port Rotterdam](https://werkenbij.portofrotterdam.com/search/) использует новый `appParams`
и `/services/recruiting/v1/jobs`; пустые фасеты этой доски не означают отсутствия географии у платформы.
У нас поддержаны legacy XML и этот новый контракт RMK, но не произвольный classic SSR-шаблон.

#### Taleo

**Форма и запрос Business Edition v2; документация Enterprise отдельно.**
На [Three Saints Bay / GATEWAYVENT](https://phg.tbe.taleo.net/phg01/ats/careers/v2/jobSearch?org=GATEWAYVENT&cws=55)
форма имеет несколько State-checkbox и пользовательское поле компании.
POST той же формы на searchResults с `state=Virginia` вернул 10 карточек первой страницы,
с несуществующим штатом — 0. Это не утверждение о полном количестве вакансий Virginia.
[TBE release reference](https://www.oracle.com/webfolder/technetwork/tutorials/tutorial/cloud/tbe/releases/23D/23D-tbe-wn.htm)
описывает настраиваемые поля; версия этого документа не определяет версию проверенной доски.
В отдельном продукте [Taleo Enterprise](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/22b/otrcg/c-taleo10supportedurls.html)
документированы location ID и параметры радиуса;
[Career Section](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24c/otcug/implementing-career-section.pdf)
описывает иерархию географии. Живая Enterprise-доска в этой проверке не открывалась,
и эти параметры нельзя приписывать нашему TBE v2 адаптеру.

#### Teamtailor

**Документация, интерфейс, запрос.**
[Jobs block](https://support.teamtailor.com/en/articles/5227868-content-block-jobs)
позволяет включать Countries, Regions, Locations, Remote Status и карту;
[Locations & Regions](https://support.teamtailor.com/en/articles/3478067-set-up-locations-regions)
различает адрес офиса и произвольную группу, вплоть до континента.
На [доске Teamtailor](https://career.teamtailor.com/jobs) исходно 14 вакансий:
`country=Sweden` вернул 7, `location=London` — 2, `remote_status_id=hybrid` — 11,
`remote_status_id=fully` — 0; несуществующая страна — 0.
Карта использует ограничивающий прямоугольник; отдельный радиус вакансий не подтверждён.
[API](https://docs.teamtailor.com/) документирует у jobs `filter[locations]`, `filter[regions]`,
`filter[remote-status]`, но требует ключ и версию API.
Public scope ключа не означает анонимный доступ. City/country/state-фильтры раздела candidates
не использовались как доказательство возможностей jobs.

#### Workable

**Интерфейс и запрос публичной доски.** На [Entuitive](https://apply.workable.com/entuitive/)
есть Location и Workplace type. Публичный браузерный endpoint
`POST /api/v3/accounts/entuitive/jobs` вернул 13 вакансий без ограничения и 5 с телом
`{"location":[{"country":"Canada","region":"Ontario","city":"Toronto"}]}`.
Несуществующий city в таком объекте вернул 0. `workplace=["hybrid"]` вернул 12,
`workplace=["remote"]` — 0.
`GET /api/v3/accounts/entuitive/jobs/filters` возвращает доступные места и форматы.
Это фактически работающий анонимный endpoint интерфейса, а не обещание стабильности публичного API.
[Документированный accounts API](https://workable.readme.io/reference/jobs-1)
описывает country/state/city и workplace как данные; его параметр `details` не доказывает геопоиск.
[API аккаунта](https://workable.readme.io/reference/jobs) также нельзя смешивать с публичной доской.
Наш адаптер уже использует браузерный JSON endpoint, но передаёт только `query`.

#### Workday

**Документация, интерфейс, запрос CXS.**
[Career Sites](https://doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/career-sites/san1394588983205.html)
описывает настраиваемые Location Hierarchy,
[Remote Jobs](https://stage.doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/job-postings/cck1660565105414.html?toc=3.6.2)
— Remote в Location/Distance и Remote Type.
На [NVIDIA](https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite)
проверен `POST /wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs` с `appliedFacets`.
Значение Singapore из фасета `locationHierarchy1` дало 24 вакансии;
значение Remote из `locationHierarchy2` — 561.
Исходный `total=2000` — наблюдаемый предел ответа, не полное число всех вакансий компании.
Ключи и ID берутся из фасетов конкретной доски; их нельзя переносить между работодателями.
Точный HTTP-параметр радиуса не проверен. Recruiting REST из общей документации — другой контракт,
не доказательство параметров CXS. Наш код всегда отправляет пустой `appliedFacets`.

#### Y Combinator

**Интерфейс и запрос; различаются глобальные и корпоративные страницы.**
На [Airweave](https://www.ycombinator.com/companies/airweave/jobs) и
[Tavus](https://www.ycombinator.com/companies/tavus/jobs) геоконтролы не обнаружены.
В данных карточек есть текст места и remote-ограничения, например `San Francisco / Remote (US)`.
При этом [глобальный jobs](https://www.ycombinator.com/jobs) имеет страницы
[San Francisco](https://www.ycombinator.com/jobs/location/san-francisco),
[India](https://www.ycombinator.com/jobs/location/india) и
[Remote jobs в списке ролей](https://www.ycombinator.com/jobs/role/all).
Наш адаптер работает с company jobs. Глобальные URL нельзя автоматически считать параметрами
корпоративной страницы. В локальном разборе 5 офисных вакансий Airweave сохранили строку,
но не получили нормализованных мест. Ошибка `Remote (UK)` описана в GEO-009.

## Граница уже выполненной проверки кода

Проверены все 21 `src/parsers/ats/*/parser.py` и общие модули пакета.
477 существующих тестов `tests/parsers/ats` и `tests/test_ats_contracts.py` прошли в первом проходе 21 октября 2026 года.
После аудита общего модуля повторно выполнены `tests/test_local_geography.py`, `tests/test_geography.py`,
`tests/test_getmatch_geography.py` и `tests/test_filters.py`: **84 passed**.
Все **22** команды воспроизведения из багрепортов также исполнены. Это выполнение текущего поведения,
а не утверждение, что ошибки исправлены.
Этот результат не доказывает полноту географии: воспроизведения GEO-008 и GEO-009 показывают
ошибки на входах, которые существующие тесты не покрывают.

## Правило дальнейшего ведения

Новые проблемы получают следующий номер `GEO-023` и далее. Исправленная проблема сохраняет номер,
получает статус «исправлено», ссылку на изменение и проверку исходного воспроизведения.
Изменение статуса требует проверки кода или источника. Наличие идеи исправления не закрывает проблему.
