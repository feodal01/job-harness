# Согласование фильтров со спецификацией v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Исправить пункты аудита 1–8; догрузка карточки по открытию (пункт 9) вне задачи.
**Architecture:** Общая оценка профиля, мест и мягких условий; адаптеры нормализуют сведения и строят безопасные запросы. Один неизменяемый режим second_chance на запуск. Миграция пересчитывает текущий пул, сохраняя историю, этапы и заметки.
**Tech Stack:** Python, FastAPI, SQLite, vanilla JS.
**Spec:** docs/product-spec-v2.md §§3–4, 5.3; переводы из search-query-business-rules.md, если не заменены v2.

## Global Constraints
- Нет жёсткого отказа из-за неизвестных данных. Непроверяемое условие явно отмечается.
- Не перезапрашивать известные карточки. Не запускать реальные поиски ради тестов.
- Не менять историю запусков и пользовательские этапы/заметки.
- Рабочее дерево без первого коммита; не коммитить и не включать чужие файлы.

## Review Focus
- Город + удалёнка по стране/миру, включая запрет страны и альтернативные места.
- Неизвестная профессия и синонимы: неоднозначность не даёт скрытие.
- Старые брифы с remote_from, несколькими форматами и отменёнными зарплатными флагами.
- Сохранение заметок/воронки после пересчёта и скрытые вакансии в счётчиках/пагинации.
- Непроверяемые поля Getmatch и одинаковые локальные оценки в обоих режимах second_chance.

## Общие интерфейсы
- SearchFilters.minimum_work_format: office|hybrid|remote_country|remote_worldwide|None. Вместо work_formats/remote_from.
- SalaryFilter: minimum, currency, period. Удалить strict_minimum, salary_published и выбор налоговой базы.
- WorkLocation.format и Vacancy.work_formats хранят нормализованные четыре формата; legacy remote преобразуется в remote_country при загрузке старых данных.
- FilterCheck: field, status(accepted/rejected/unknown), reason, kind(hard/soft), supported(bool).
- FilterResult.status: accepted|maybe|rejected. Неизвестность хранится в checks; если нет отказов, итог accepted.
- evaluate(vacancy, filters, *, complete=False, queries=(), unsupported=frozenset()). Профиль жёсткий; без фраз помечен непроверенным, а не доказанным совпадением.
- Search.prepare(brief, fetch, *, second_chance=False). SearchPlan.application содержит native/local/none; query допускает ignored для общей ленты.
- Settings.second_chance=False; SearchOptions.second_chance: bool|None; фактическое значение в RunSpec и request.
- По умолчанию selection=candidates, исключает rejected; all только явный режим отладки. Счётчики папок считают нескрытые вакансии.

### 1. Общая фильтрация — worker filtering
Files: src/filtering.py, src/profile_matching.py (если нужен), tests/test_filters.py, новые tests/test_filter_spec.py.
- [x] Закрепить сценарии аудита и граничные случаи тестами, убедиться в отказе старой реализации.
- [x] Реализовать profile/географию/ранг формата/зарплату/релокацию и причины для каждого условия.
- [x] Запустить целевые тесты.

### 2. Источники — worker adapters
Files: src/parsers/, tests/test_common_query.py, tests/test_capabilities.py, tests/test_parser.py, tests/test_getmatch*.py, tests/test_interfaces.py, tests/test_adapters.py.
- [x] Проверить планы запросов обоих режимов и получить красные тесты новых требований.
- [x] HH: город + страна/REMOTE, безопасный перевод мягких фильтров; Getmatch: l и pa, без sp/se и непроверенных зарплатных ограничений.
- [x] Нормализовать четыре формата, распознавать явную удалёнку по миру; вычислять доступность условий из извлекаемых полей.
- [x] Прогнать тесты адаптеров и проверки unsupported.

### 3. Интерфейс — worker frontend
Files: src/index.html, src/static/app.js, src/static/app.css.
- [x] Один минимум формата, только три поля зарплаты; second_chance с объяснением.
- [x] Подходит/Может быть подходит/Скрыто; по умолчанию нескрытые, явный отладочный all; причины с результатом и классом.
- [x] Не возвращать сворачиваемые условия. Проверка браузером после интеграции.

### 4. Контракт, движок и хранилище — основной агент
Files: src/criteria.py, src/models.py, src/config.py, src/engine/, src/store.py, src/api/v1/briefs.py, tests/test_funnel.py, tests/test_engine_runs.py, tests/test_service.py, tests/test_pagination.py, tests/test_extension.py.
- [x] Передать профиль/unsupported и режим источникам, поддержать maybe в метриках.
- [x] Миграция schema 8, новые поля, пересчёт пула, история неизменна. Тест на повторное открытие базы и сохранение этапов.
- [x] Скрытие по умолчанию и единая логика счётчиков/пагинации/соседней карточки.
- [x] Проверить полный pytest, Ruff, mypy, JS syntax, браузер на копии базы; перезапустить основной сервис только без активных поисков, с резервной копией.

## Проверка результата

- Полный набор: 255 passed; Ruff, mypy (29 файлов), JS syntax — без ошибок.
- После уточнения текстов причин: 89 тестов фильтрации и mypy повторно прошли.
- Браузер на копии базы: сохранение формы и запуск с подменёнными источниками,
  172 видимых / 28 скрытых, явный показ всех 200, причины и неподдерживаемые условия,
  переход 50 → 51, ссылка на источник, мобильная ширина 390 без переполнения, консоль без ошибок.
- Основной сервис перезапущен без активных запусков. База переведена с v7 на v8;
  хэши пользовательских этапов/заметок, исторических запросов и снимков совпали до/после.
  Резервная копия: data/backups/search-before-filters-v8-20261002-224131.sqlite.
- Основной интерфейс проверен после перезапуска, без запуска реального поиска.
- Дополнительно исправлены запуск старого брифа без search и перезапись текущей оценки
  результатами активного запуска после редактирования брифа; четыре регрессионных теста.
- Живые HH/Getmatch не запрашивались ради проверки. Нативная зарплата остаётся локальной.
- Пункт 9 (догрузка карточки по открытию) не входит в эту доработку.
