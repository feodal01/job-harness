Настоящие публичные ответы Getmatch, сохранены 2026-09-28 через HTTPX.

specializations.json: https://getmatch.ru/api/specializations

qa_auto.json и qa_manual.json: /api/offers?sa=any&p=1&offset=0&limit=10&pa=all&sp=<имя>

qa_auto_page2.json: https://getmatch.ru/api/offers?sa=any&p=1&offset=10&limit=10&pa=all&sp=qa_auto

Ответы сохранены целиком, без редактирования.

Общая лента без `sp`, сохранена 2026-09-28:

- `feed_page1.json`: https://getmatch.ru/api/offers?sa=any&p=1&offset=0&limit=10&pa=all
- `feed_page2.json`: https://getmatch.ru/api/offers?sa=any&p=1&offset=10&limit=10&pa=all
- `feed_last.json`: https://getmatch.ru/api/offers?sa=any&p=1&offset=920&limit=10&pa=all

В первой странице 12 записей при `meta.limit=10`: две промопубликации не занимают
обычные места пагинации. Следующая страница начинается с offset=10, не 12.

detail-34499.html: https://getmatch.ru/vacancies/34499-starshii-razrabotchik-instrumentov-testirovaniia-java — HTML полной карточки, 2026-09-28.

Промопубликации из общей ленты (`offer_type=one_day_offer_v3`), HTML от 2026-09-28:

- `detail-36107.html`: https://getmatch.ru/vacancies/36107-alfa-risk-day
- `detail-36316.html`: https://getmatch.ru/vacancies/36316-komanda-razrabotki-c-c-sberlinux-pangolin-db
- `vacancies_form.html`: location filter block of https://getmatch.ru/vacancies, 2026-11-02 (zone values for the form check).
