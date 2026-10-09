# Пробное заполнение специализаций Getmatch агентом

Дата: 28.09.2026. Проверяется один запрос, без изменения поискового движка.

## Результат

Сабагент Луны первым ответом вернул корректный JSON и выбрал `qa_auto` и `load_performance`. Уточнение и исправление ответа не потребовались. Выбор совпал с ожидаемым результатом, записанным до отправки запроса. Ожидаемый ответ агенту не передавался.

- В запросе были все 49 специализаций из сохранённого 28.09.2026 справочника Getmatch, включая Python, QA Manual и управленческие направления. Исходный справочник: https://getmatch.ru/api/specializations . В рамках этого эксперимента он не запрашивался повторно.
- Полный переданный JSON: 6 432 символа, 7 374 байта UTF-8. Включены бриф, общие условия, инструкции, каталог и схема ответа. Каталог не сокращался до релевантных позиций.
- Ответ: 267 символов с завершающим переводом строки. Число токенов и время самого модельного вызова не измерялись; символы не выдаются за токены.
- В ответе нет дополнительных условий зарплаты, географии или грейда; они остаются в общих условиях плана.
- Разбор JSON запрещает повторяющиеся ключи. Серверная проверка использует строгую типизированную модель, допустимые значения из справочника и правила согласованности статуса.
- 13 искусственно испорченных ответов отклонены валидатором. Это проверки валидатора, а не 13 прогонов модели.
- Контрольный неверный по смыслу выбор `python` проходит формальную проверку: категория существует. Проверка смысла отдельно сравнила ответ с заранее заданными ожиданиями.

## Как запускалось и ограничения

Попытка создать новый изолированный сабагент GPT-6 Luna с reasoning=max завершилась ошибкой `agent thread limit reached`. Отдельный Codex CLI также не выполнил запрос: модель `gpt-6-luna` не поддерживается текущим способом входа через ChatGPT.

Фактически запрос выполнил существующий сабагент `/root/luna_filters_finish`. Ему был передан только путь к `request.json` с указанием прочитать этот файл, вернуть JSON, не читать другие файлы и не продолжать старую разработку. Его прежняя история технически не очищалась. API переиспользования агента не сообщает модель и reasoning; версия GPT-6 и режим max для успешного прогона отдельно не подтверждены. Описывать этот результат как чистый изолированный API-бенчмарк GPT-6 Luna max нельзя.

Это один реалистичный пример с достаточно ясным брифом. Он показывает работоспособность маленького задания с полным справочником, но не измеряет стабильность, качество на неоднозначных профессиях или стоимость подготовки всех источников. Ответ агента проверяется после генерации; принудительный structured output на уровне провайдера в успешном запуске не использовался.

## Точный запрос сервиса

В `request.json` сохранён точный переданный JSON. Ниже тот же объект отформатирован для чтения.

```json
{
  "task": "Заполни одно недостающее решение поискового плана: специализации Getmatch.",
  "instructions": [
    "Верни только JSON по response_schema, без Markdown и текста вокруг.",
    "Выбирай существующие slug из каталога по смыслу всего брифа. Можно выбрать несколько специализаций.",
    "Категории определяют охват поиска, а не доказывают выполнение остальных требований. Технология сама по себе не определяет профессию.",
    "Общие условия уже сохранены; не добавляй новые поля, не меняй эти условия и не пытайся выразить их через специализацию.",
    "Если смысла брифа достаточно для выбора, заполни решение самостоятельно. Если необходимо уточнение, верни needs_clarification и один конкретный вопрос. Если подходящих специализаций нет, верни unsupported.",
    "Для ready список specializations непустой, question=null. Для needs_clarification список пустой, question содержит вопрос. Для unsupported список пустой, question=null. Во всех случаях reason — краткое объяснение решения. Повторения slug запрещены."
  ],
  "plan_id": "demo-qa-python-001",
  "brief": "Ищу senior-позицию в автоматизации тестирования на Python. Также рассматриваю нагрузочное тестирование. Хочу работать удалённо из Казахстана, от 3500 USD в месяц на руки. Позиции, где основная работа — ручное тестирование, не интересуют. Руководящие позиции тоже не рассматриваю.",
  "confirmed_conditions": {
    "grade": "senior",
    "work_format": "remote",
    "remote_from_country": "KZ",
    "compensation": {
      "min": 3500,
      "currency": "USD",
      "period": "month",
      "basis": "net"
    }
  },
  "pending_decision": {
    "source": "getmatch",
    "field": "specializations",
    "meaning": "Выбранные специализации объединяются в один пул кандидатов; общие условия проверяются отдельно.",
    "catalog": [
      {
        "slug": "qa_auto",
        "name": "QA Auto",
        "category": "QA"
      },
      {
        "slug": "project_management",
        "name": "Project Manager",
        "category": "Product, Project, Architecture"
      },
      {
        "slug": "engineering_management",
        "name": "CTO / CIO",
        "category": "Top Management"
      },
      {
        "slug": "qa_manual",
        "name": "QA Manual",
        "category": "QA"
      },
      {
        "slug": "information_security",
        "name": "Information Security",
        "category": "Information Security"
      },
      {
        "slug": "data_science",
        "name": "Data Scientist & Machine Learning",
        "category": "Data & ML"
      },
      {
        "slug": "architect",
        "name": "Architect",
        "category": "Product, Project, Architecture"
      },
      {
        "slug": "c_language",
        "name": "C",
        "category": "Backend"
      },
      {
        "slug": "sales",
        "name": "Sales",
        "category": "Sales"
      },
      {
        "slug": "marketing",
        "name": "Marketing",
        "category": "Marketing"
      },
      {
        "slug": "systems_network_engineering",
        "name": "Systems & Network Engineering",
        "category": "Technical Engineering"
      },
      {
        "slug": "embedded",
        "name": "Embedded",
        "category": "Backend"
      },
      {
        "slug": "dev_ops",
        "name": "DevOps",
        "category": "Infrastructure & DevOps"
      },
      {
        "slug": "sre",
        "name": "SRE",
        "category": "Infrastructure & DevOps"
      },
      {
        "slug": "js_frontend",
        "name": "JavaScript / TypeScript",
        "category": "Frontend"
      },
      {
        "slug": "tech_recruitment",
        "name": "HR & Recruitment",
        "category": "HR & Recruitment"
      },
      {
        "slug": "php",
        "name": "PHP",
        "category": "Backend"
      },
      {
        "slug": "java_scala",
        "name": "Java / Scala",
        "category": "Backend"
      },
      {
        "slug": "c_sharp",
        "name": "C#",
        "category": "Backend"
      },
      {
        "slug": "golang",
        "name": "Golang",
        "category": "Backend"
      },
      {
        "slug": "ruby",
        "name": "Ruby",
        "category": "Backend"
      },
      {
        "slug": "python",
        "name": "Python",
        "category": "Backend"
      },
      {
        "slug": "js_backend",
        "name": "Node.js",
        "category": "Backend"
      },
      {
        "slug": "ios",
        "name": "iOS",
        "category": "Mobile"
      },
      {
        "slug": "c_cpp",
        "name": "C++",
        "category": "Backend"
      },
      {
        "slug": "android",
        "name": "Android",
        "category": "Mobile"
      },
      {
        "slug": "blockchain_crypto",
        "name": "Blockchain & Crypto",
        "category": "Backend"
      },
      {
        "slug": "kotlin",
        "name": "Kotlin",
        "category": "Backend"
      },
      {
        "slug": "typescript",
        "name": "TypeScript",
        "category": "Backend"
      },
      {
        "slug": "dba",
        "name": "DBA",
        "category": "Infrastructure & DevOps"
      },
      {
        "slug": "dwh_data_warehouse",
        "name": "DWH (Data warehouse)",
        "category": "Data & ML"
      },
      {
        "slug": "devrel",
        "name": "DevRel",
        "category": "HR & Recruitment"
      },
      {
        "slug": "telecom_electronics_engineering",
        "name": "Telecom & Electronics Engineering",
        "category": "Technical Engineering"
      },
      {
        "slug": "load_performance",
        "name": "Load / Performance",
        "category": "QA"
      },
      {
        "slug": "cross_platform_flutter_react_native",
        "name": "Cross-Platform: Flutter & React Native",
        "category": "Mobile"
      },
      {
        "slug": "fullstack",
        "name": "Fullstack",
        "category": "Backend"
      },
      {
        "slug": "product_management",
        "name": "Product Management",
        "category": "Product, Project, Architecture"
      },
      {
        "slug": "product_design",
        "name": "Product Design",
        "category": "Design & UX"
      },
      {
        "slug": "product_analyst",
        "name": "Product Analytics",
        "category": "Analytics"
      },
      {
        "slug": "system_analyst",
        "name": "System Analytics",
        "category": "Analytics"
      },
      {
        "slug": "one_c",
        "name": "1C Development",
        "category": "1C"
      },
      {
        "slug": "data_engineering",
        "name": "Data Engineering",
        "category": "Data & ML"
      },
      {
        "slug": "business_analyst",
        "name": "Business Analytics",
        "category": "Analytics"
      },
      {
        "slug": "support_engineer",
        "name": "Support Engineering",
        "category": "Support"
      },
      {
        "slug": "ux_writer",
        "name": "UX Writing",
        "category": "UX & Tech Writers"
      },
      {
        "slug": "data_analyst_bi",
        "name": "Data Analytics & BI",
        "category": "Analytics"
      },
      {
        "slug": "system_administrator",
        "name": "System Administration",
        "category": "Infrastructure & DevOps"
      },
      {
        "slug": "one_c_analyst",
        "name": "1C Analytics",
        "category": "1C"
      },
      {
        "slug": "tech_writer",
        "name": "Tech Writing",
        "category": "UX & Tech Writers"
      }
    ]
  },
  "response_schema": {
    "additionalProperties": false,
    "properties": {
      "status": {
        "enum": [
          "ready",
          "needs_clarification",
          "unsupported"
        ],
        "title": "Status",
        "type": "string"
      },
      "specializations": {
        "items": {
          "enum": [
            "qa_auto",
            "project_management",
            "engineering_management",
            "qa_manual",
            "information_security",
            "data_science",
            "architect",
            "c_language",
            "sales",
            "marketing",
            "systems_network_engineering",
            "embedded",
            "dev_ops",
            "sre",
            "js_frontend",
            "tech_recruitment",
            "php",
            "java_scala",
            "c_sharp",
            "golang",
            "ruby",
            "python",
            "js_backend",
            "ios",
            "c_cpp",
            "android",
            "blockchain_crypto",
            "kotlin",
            "typescript",
            "dba",
            "dwh_data_warehouse",
            "devrel",
            "telecom_electronics_engineering",
            "load_performance",
            "cross_platform_flutter_react_native",
            "fullstack",
            "product_management",
            "product_design",
            "product_analyst",
            "system_analyst",
            "one_c",
            "data_engineering",
            "business_analyst",
            "support_engineer",
            "ux_writer",
            "data_analyst_bi",
            "system_administrator",
            "one_c_analyst",
            "tech_writer"
          ],
          "type": "string"
        },
        "title": "Specializations",
        "type": "array",
        "uniqueItems": true
      },
      "reason": {
        "minLength": 1,
        "title": "Reason",
        "type": "string"
      },
      "question": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Question"
      }
    },
    "required": [
      "status",
      "specializations",
      "reason",
      "question"
    ],
    "title": "Decision",
    "type": "object"
  }
}
```

## Полученный ответ

```json
{
  "status": "ready",
  "specializations": [
    "qa_auto",
    "load_performance"
  ],
  "reason": "QA Auto соответствует автоматизации тестирования; Load / Performance охватывает также интерес к нагрузочному тестированию. Python и остальные условия проверяются отдельно.",
  "question": null
}
```

## Проверки

```json
{
  "response_validation": {
    "formal_validation": "passed",
    "status_matches": true,
    "selected_specializations_match": true,
    "question_matches": true,
    "response_characters": 267
  },
  "validator_checks": {
    "negative_checks": {
      "unknown_slug": "rejected",
      "extra_field": "rejected",
      "wrong_list_type": "rejected",
      "empty_ready": "rejected",
      "duplicate_slugs": "rejected",
      "missing_reason": "rejected",
      "blank_reason": "rejected",
      "clarification_without_question": "rejected",
      "unsupported_with_selection": "rejected",
      "unknown_status": "rejected",
      "ready_with_question": "rejected",
      "invalid_json": "rejected",
      "duplicate_json_keys": "rejected"
    },
    "accepted_unresolved_statuses": [
      "needs_clarification",
      "unsupported"
    ],
    "semantically_wrong_python_choice": "passes formal validation, fails preregistered expectation"
  }
}
```

## Вывод для устройства сервиса

Для такого шага агенту достаточно одного недостающего поля и полного компактного справочника. Объединённая схема всех источников не потребовалась. Сервис должен сохранять подтверждённые общие условия сам и принимать только допустимые изменения конкретного источника.

Схема закрывает формальные ошибки, но не выбор неподходящей существующей категории. Последний должен оставаться видимым в плане и отдельно проверяться на примерах. Следующие полезные проверки: неоднозначный «аналитик», профессия вне справочника и несколько допустимых направлений с исключениями.
