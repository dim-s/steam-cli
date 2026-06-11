---
status: finished
name: Active
---

## Завести boardown-доску + правила проактивного ведения в AGENTS.md

---
id: SC-1
type: tech
status: done
order: 100
---

Инфра: инициализировать .boardown (standing-release Active как в TSB), эпик ideas-морозилка, секция правил ведения доски в AGENTS.md (агент ведёт доску проактивно без прямых просьб). Паттерн и формулировки адаптированы из The Sorting Bureau.

## Починить 8 подтверждённых багов + регрессионные тесты

---
id: SC-3
type: bug
status: done
order: 200
checklist:
  - id: c1
    text: "#1 _print_reviews: крэш на author=null / playtime=null → выровнять по _review_to_row"
    done: true
  - id: c2
    text: "#2 news --maxlength 0 = full: _flatten(text,0) срезал символ — чинить _flatten"
    done: true
  - id: c3
    text: "#3 estimate в чужой валюте маркируется USD — гард по price_overview.currency"
    done: true
  - id: c4
    text: "#4 cache --json: команда нарушает контракт '--json on any command'"
    done: true
  - id: c5
    text: "#5 curl-бэкенд: ретраи (429/5xx) + exit 22 → code=http"
    done: true
  - id: c6
    text: "#6 _get_curl: --max-time 0 при timeout<1 = безлимит → max(1, ceil)"
    done: true
  - id: c7
    text: "#7 _profile_url: под-страница /id/gaben/badges даёт vanity 'badges'"
    done: true
  - id: c8
    text: "#8 _print_browse печатает литеральное 'None' при name=None"
    done: true
notes:
  - id: n1
    text: "Все 8 фиксов выровнены по существующим эталонам в коде (_review_to_row / _filter_reviews / _print_similar), без новых паттернов. #5 потребовал рефактора: retry-цикл вынесен в _retry_get(getter, url) — единая политика 429/5xx/сетевые+backoff для urllib И curl; TLS-fallback теперь даёт curl свежий полный бюджет ретраев. SteamError получил опц. .status (curl exit 22 → code=http). 3 существующих теста (TestGetCurl exit-22/max-time, TestEstimate._resp) обновлены под корректное поведение + фикстура дополнена реальным currency. Доки НЕ трогал: human-вывод cache не изменился, --json уже обещан README/AGENTS. 275 passed (было 239). Прошло code-reviewer + dmitry-manager."
    createdAt: "2026-06-11T14:06:11.321Z"
---

Аудит выявил 8 подтверждённых багов в steam_cli.py (крэши на вырожденных данных, нарушения заявленного контракта CLI, неверная валюта в estimate, отсутствие ретраев в curl-бэкенде). Каждый фикс — с offline регрессионным тестом в стиле сьюта. Контракт-тесты TestProfile/TestReviews/TestBrowse не ломать.

## Чинить discovery доски для Claude Code: проектный CLAUDE.md + @AGENTS.md

---
id: SC-4
type: tech
status: done
order: 300
notes:
  - id: n1
    text: "Создан ./CLAUDE.md с @AGENTS.md (импорт грузит AGENTS.md в контекст Claude Code) + тонкий указатель на правило доски (без дублирования механики — источник истины остаётся AGENTS.md, иначе дрейф). Заголовок AGENTS.md расширен: Claude Code добавлен в адресаты. Прогнал через prompt-atlas: формулировка секции доски сильная, провал был чисто структурный (discovery)."
    createdAt: "2026-06-11T14:11:15.359Z"
---

Правила проактивного ведения доски жили только в AGENTS.md, который Claude Code на старте сессии не авто-загружает (читает только CLAUDE.md). Из-за этого агент не вёл доску, пока владелец не ткнул. Фикс по документированному паттерну CLAUDE.md↔AGENTS.md.
