---
status: current
name: Active 3
---

## Команда nextfest: агрегатная статистика по участникам Steam Next Fest

---
id: SC-5
type: feature
status: todo
order: 100
notes:
  - id: n1
    text: "ИСТОЧНИК ПОДТВЕРЖДЁН ПО СКРИНУ СТРАНИЦЫ (16.06). tagids_featured=1440505 = ровно список «ВСЕ ИГРЫ» страницы феста: nMatchCount=4358 совпал со скрином (4357 показано + «Скрыто:1»), и фасет-счётчики жанров из rgSolrFacetCounts совпали ОДИН-В-ОДИН (Казуальная 597=2015, Экшен 19=1647, Приключение 21=1541, Стратегия 9=1308, Симулятор 599=1273). Источник верный. НЮАНС про Onimusha (поймал владелец): 2638890 присутствует в backend-пуле 4358, но владелец не видит её на странице — расхождение на УРОВНЕ ОТОБРАЖЕНИЯ (страница прячет 1 шт «Скрыто:1»; + режимы ГЛАВНАЯ=discovery/random/«Подобраны для вас» vs ВСЕ ИГРЫ=полный фильтруемый список — Onimusha не всплыла в discovery-выборке). На уровне данных она в списке. Если нужен «чистый» список — учесть, что в пул могут попадать отдельные паблишер/featured-демки. 1440505 в обычном search/results даёт 0 (внутренний sale-маркер, не community-тег). Чистого отдельного key-free реестра нет (куратор 39049601 = Steam Promotions, без app-списка) — но он и не нужен: ajaxgetdynamicsaleitems и есть то, что рендерит сама страница."
    createdAt: "2026-06-16T15:44:42.211Z"
---

ЗАЧЕМ: дать одной командой срез по текущему Next Fest (рецепт-аналитика феста: размер, топ по онлайну/отзывам, гистограмма тегов, распределение цен, даты релизов). Сейчас CLI не умеет перечислить участников события — browse фильтрует по тегам, specials/top-sellers по featuredcategories, страница /sale/nextfest это JS-рендер без appid в HTML.

ПРОВАЛИДИРОВАНО (16.06.2026, реально дёргал key-free обычным curl):
1) Список участников — GET store.steampowered.com/actions/ajaxgetdynamicsaleitems?tagids_featured=<TAGID>&max_results=10000&cc=<cc>&l=<lang> -> JSON {rgCapsules:[{id,type}], nMatchCount, bMoreResultsRemaining}. Июньский фест 2026: nMatchCount=4358, без пагинации. Ключ не нужен.
2) Авто-дискавери TAGID — GET /sale/nextfest, в HTML есть "featured_app_tagid":1440505 (СВОЙ у каждого феста, не хардкодить; regex featured_app_tagid"?\s*:\s*(\d+); плюс флаг --tagid override).
3) Обогащение — key-free батч IStoreBrowseService/GetItems/v1?origin=...&input_protobuf_encoded=<ids> (name/price/tags/release/обзоры пачкой; protobuf-кодирование списка id). Альтернатива — существующие overview/appdetails, но 4358 игр поштучно дорого.

ВАЖНЫЙ НЮАНС appid: в rgCapsules id — это appid ИГРЫ (coming_soon), а живой сигнал феста (онлайн players, отзывы по демо) висит на appid ДЕМКИ — её достаём из appdetails(filters=demos).demos[].appid. Проверено: Echoes of Aincrad игра 2244210 -> демо 4148250 (13054 онлайн в моменте), Onimusha 2638890 -> демо 3974650.

СТАТУС СОБЫТИЯ (из rtime32 на странице): фест ИДЁТ 15.06–22.06.2026 (демки играбельны, онлайн/отзывы копятся live). Заголовок страницы помечен "(предпоказ)" — это ложный сигнал, окно события активно.

КАВЕАТЫ ДИЗАЙНА: 4358 участников — НИКОГДА не обогащать всех по умолчанию; дефолт = список+счётчик+агрегаты из дешёвого батча, --limit/--sort (напр. by-players) для enrich топ-N. Эндпойнты first-party (никакого SteamSpy/SteamDB) — вписывается в правила проекта. Куратор 39049601 get_appids=true НЕ отдаёт appid (тупик). Нужны offline-фикстуры в стиле сьюта + bump __version__/pyproject.

## Команда ai <game>: AI-раскрытие игры со стор-страницы

---
id: SC-6
type: feature
status: todo
order: 200
notes:
  - id: n1
    text: "ГРАБЛИ С КЕШЕМ (поймал владелец): http_get(...) по умолчанию cache_ttl=0, а в steam_cli ttl=0 = НИ чтения, НИ записи кеша (_cache_get/_cache_put оба выходят на ttl==0). http_json передаёт DEFAULT_TTL, поэтому JSON-команды кешируются, а прямой http_get — нет. Команда ai ДОЛЖНА явно передавать положительный cache_ttl (DEFAULT_TTL ~6ч для свежести, или дольше для замороженного снимка) — иначе каждый вызов бьёт Steam заново и не сохраняет HTML. Проверено: с cache_ttl=math.inf 2-й вызов 0мс (кеш), тело пишется. Кеш-дир ~/Library/Caches/steam-cli."
    createdAt: "2026-06-17T05:52:20.601Z"
---

ЗАЧЕМ: AI Generated Content Disclosure — растущий per-game атрибут (Valve обязал с 2024). Переиспользуемый атом: любому анализу игры полезно «раскрыл ли AI и как». Встаёт в общий ряд per-game команд (info/reviews/players/tags). Решено владельцем делать, но НЕ сейчас (приоритет — разовый анализ феста скриптом).

ПРОВАЛИДИРОВАНО на пробе (analysis/nextfest-2026-06/nf_ai_crawl.py — оттуда лифтить парс):
- Источник: HTML стор-страницы /app/<id>/?l=english (GetItems AI-поля НЕ отдаёт — проверено). Прецедент HTML-скрейпа в CLI уже есть: tags/similar/browse.
- Якорь презенса: строка "how their game uses AI Generated Content like this:"; текст разработчика — в следующем <p><i>...</i></p> (вся декларация, в т.ч. многопунктовая, в одном <p>; <br> сохранять как разделитель пунктов).
- Нужна mature-кука (birthtime=0; lastagecheckage=...; wants_mature_content=1) — иначе взрослые игры отдают age-gate-заглушку ~30КБ. С кукой полная страница ~100-146КБ.
- Кеш уже бесплатный через http_get (HTML кешируется как для similar).

ФОРМА: steam-cli ai <game> --json -> {appid, status (ai_disclosed/no_disclosure/age_gated/unreachable), disclosure_text}. ТОЛЬКО по одной игре. Массовый краул по фесту остаётся analysis-скриптом (оркестрация поверх атома), НЕ командой.

ХРУПКОСТЬ (главный риск): якорь — английская фраза, ломается при реверстке/локали. ОБЯЗАТЕЛЬНО offline-фикстур-тест (HTML с декларацией / без / age-gated) в стиле сьюта + мягкая деградация (age-gate -> честный статус, не краш). Не over-generalize в «скрейп произвольных полей» — только этот атрибут. Бамп __version__/pyproject.
