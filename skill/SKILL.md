---
name: steam
description: Query the public Steam API via the `steam-cli` command — game reviews, store details, prices, live player counts, news/patch notes, and global achievement stats. No Steam API key needed. Use whenever a task needs real data about a Steam game. English triggers — "get Steam reviews for", "what do players think of <game>", "Steam rating / score of", "is <game> any good on Steam", "Steam store info / genres / release date / developer of", "how many players is <game> has right now", "current players online", "latest patch notes / news for <game>", "Steam price of", "achievement completion rates", "download reviews for <game>", "find the appid of", "full profile / overview of <game>", "tell me everything about <game>", "what does <game> look like", "show me screenshots / the banner / art of <game>", "games on sale / Steam specials / discounts right now", "top sellers on Steam", "look up a Steam player / profile", "who wrote this review / the reviewer's profile". Russian triggers — «отзывы в стиме на», «достань стим-отзывы по», «что игроки пишут про», «рейтинг игры в стиме», «какая оценка у игры в стиме», «стоит ли брать <игру> в стиме», «инфа об игре в стиме», «жанр / дата релиза / разработчик игры», «сколько сейчас онлайн в», «текущий онлайн игроков», «последние новости / патчноут по игре», «цена игры в стиме», «проценты выполнения ачивок», «выкачай отзывы по», «найди appid игры», «полный профиль игры», «расскажи всё об игре в стиме», «как выглядит игра», «покажи скриншоты / баннер / арт игры», «что на распродаже в стиме», «скидки / специальные предложения стим», «топ продаж сейчас», «профиль игрока в стиме», «кто оставил этот отзыв», «что за человек написал отзыв». Also use proactively when analyzing a game's reception, doing competitor/market research on Steam, or when any step needs a game's appid, sentiment, or store metadata. **Public Steam Community profiles ARE supported** (key-free, via `profile`). Do NOT use for: non-Steam stores (Epic, GOG, mobile); a user's owned-games library, private-profile details, or per-user achievements (those need a Steam Web API key and stay out of scope); or buying/wishlisting games.
---

# steam — query the public Steam API

`steam-cli` is a single-file Python 3 CLI (stdlib only, no API key) that
pulls real data from Steam's public endpoints. Source:
<https://github.com/dim-s/steam-cli>.

**If `steam-cli --version` fails with "command not found", read
[setup.md](setup.md) and install it first.** One-liner via `pip` / `pipx` /
`uv tool`; works on macOS, Linux, Windows.

Every `<game>` argument is an **appid** (`1145360`) or a **name**
(`"Hades"`). Names are resolved to the top store-search hit and the chosen
appid is printed to **stderr** (so `--json` stdout stays clean). The top hit
isn't always what you meant — `"Hades"` resolves to *Hades II*. When the
exact title matters, resolve it yourself with `search` first, or pass the
appid.

**Always add `--json` when you'll parse the result** — every subcommand
supports it and returns structured data. The default output is
human-formatted for display, not for machines. Add `-q` to silence the
resolution note.

**Errors are machine-readable under `--json`.** On failure the command prints
`{"error": "<message>", "code": "<slug>"}` to **stdout** (not stderr) and
exits non-zero — so a parse of the output always yields a JSON object, never
an empty stream. `code` is one of `not_found` / `http` / `network` / `parse` /
`invalid` (a bad `--lang`/`--cc` value).

**Reliability flags (all subcommands):** transient failures (HTTP 429/5xx,
flaky network) are retried automatically with backoff, so a single blip won't
fail your task. Use `--timeout SECS` (default 30) to bound a slow request.

**Languages & regions are normalized.** Pass `--lang`/`--language` as an ISO
code (`ru`, `ko`, `zh`, `pt-br`) or a Steam name (`russian`, `koreana`,
`schinese`) — both resolve to what Steam expects, so store data really comes
back in that language (a raw `ru` would otherwise silently fall back to
English). `--cc` accepts alpha-2 (`us`), alpha-3 (`usa`), or common aliases
(`uk`→`gb`); a wrong code like `usa` would otherwise return a *wrong* price.
**An unrecognized code is a hard error with a hint** (`{"error": "...", "code":
"invalid"}`), never silent wrong data. For multilingual store text use e.g.
`info <game> --lang ru` or `reviews <game> --language schinese`.

**Caching is automatic and transparent.** Responses and downloaded images are
cached on disk (platform cache dir), so repeating a query is near-instant and
doesn't re-hit Steam — fetch freely. Freshness: JSON is cached ~6h; downloaded
images are cached forever (their URLs are version-stamped); **live player
counts are never cached**. Override per call: `--no-cache` (always fresh),
`--refresh` (re-fetch and update the cache), `--cache-ttl SECS` (custom JSON
freshness). Manage it with `steam-cli cache` (show size/path), `cache --path`,
`cache --clear`.

## Subcommands

| Command | Use it for |
|---|---|
| `steam-cli overview <game>` | **One-shot snapshot** — info + review score + live players + price in a single call |
| `steam-cli reviews <game>` | User reviews + aggregate score/totals |
| `steam-cli info <game>` | Store card: genres, release, devs, metacritic, price, description |
| `steam-cli images <game>` | **Download header / screenshots / art** so you can *see* the game |
| `steam-cli search <term>` | Resolve a name → appid candidates |
| `steam-cli players <game>` | Live concurrent player count |
| `steam-cli news <game>` | News / patch notes |
| `steam-cli achievements <game>` | Global achievement completion % |
| `steam-cli price <game> [--cc us,de,ru]` | Price + discount for one or more regions |
| `steam-cli specials` | Games currently **on sale** (featured front-page specials) |
| `steam-cli top-sellers` | Current **top-selling** games |
| `steam-cli profile <id>` | **Public** Steam Community profile (steamID64 / vanity); pairs with a reviewer's `author.steamid` |
| `steam-cli cache [--path\|--clear]` | Inspect or clear the on-disk cache |

## JSON output shapes

What each command returns under `--json`, so you can parse without guessing.
**Note the shape varies** — some commands return a bare array, others an
object; only `overview`, `reviews`, `price`, `images`, `players` carry the
`appid` at the top level. A failure is always `{"error", "code"}` instead.

| Command | Shape |
|---|---|
| `overview` | `{appid, name, type, release_date, coming_soon, developers, publishers, genres, metacritic, is_free, price_cc, price, players_online, review_summary, store_url, news?, top_achievements?}` (`price_cc` = the region the `price` is for) |
| `reviews` | `{appid, query_summary:{review_score_desc, total_reviews, total_positive, total_negative}, count, reviews:[…]}` · `--summary` → `{appid, query_summary}` · `--jsonl` → one review object per line |
| `info` | raw Steam appdetails object: `{steam_appid, name, type, is_free, genres:[{id, description}], release_date:{date, coming_soon}, developers, price_overview?, …}` (large) |
| `search` | **bare array** `[{id, name, type, price?}]` — no `appid` wrapper |
| `players` | `{appid, player_count}` |
| `news` | **bare array** `[{gid, title, url, author, contents, feedlabel, date}]` |
| `achievements` | **bare array** `[{name, percent}]`, sorted by `percent` desc (names are internal API ids) |
| `price` | `{appid, regions:[{cc, name, is_free, price_overview} \| {cc, error}]}` |
| `images` | `{appid, out, images:[{kind, url, path} \| {kind, url, error}]}` |
| `specials` / `top-sellers` | `{section, cc, count, items:[{id, name, discounted, discount_percent, original_price, final_price, currency, header_image}]}` — **prices are integer minor units** (cents): `final_price: 899` = $8.99 |
| `profile` | `{steamid64, name, private, privacy_state, online_state, state_message, member_since, location, real_name, summary, vac_banned, avatar, profile_url}` — non-`public` profiles set `private:true` and null the hidden fields |

## `images` — see the game, don't just read about it

```bash
# Download the header banner + 4 screenshots into ./steam-1145360-media/.
steam-cli images "Hades" --json -q

# Pick kinds and where to save; cap the screenshot count.
steam-cli images 1145360 --what header screenshots --limit 3 --out ./shots --json -q
```

`--json` returns `{appid, out, images:[{kind, url, path}]}`. **Then open each
`path` with your own image-reading capability** to actually look at the
game — useful for "what does <game> look like?", judging art style, or
verifying a title visually. `--what` kinds: `header` `capsule` `background`
`screenshots` `movies` (movie = trailer thumbnail). A broken asset is reported
as `{kind, url, error}` and doesn't sink the rest. All assets come from the
official Steam CDN.

## `overview` — start here for "tell me about <game>"

```bash
# Everything an agent needs about a game in ONE request — instead of calling
# info + reviews --summary + players + price separately.
steam-cli overview "Hades" --json -q

# Full profile in one call: also fold in recent news and top achievements.
steam-cli overview "Hades" --news 3 --top-achievements 5 --json -q
```

Returns one object: `name`, `type`, `release_date`, `developers`, `genres`,
`metacritic`, `is_free`, `price`, `players_online`, `review_summary`
(`review_score_desc` + totals), `store_url`. Soft parts degrade to `null`
(e.g. `players_online: null` if the count is unavailable) rather than failing
the whole call — only the store card is required.

Opt-in fields, added only when their flag is set: `--news N` → `news` (latest
N items, `null` on failure) and `--top-achievements N` → `top_achievements`
(top N by completion %, `null` on failure). With both, `overview` is a
one-call "full profile" — no need to also hit `news` / `achievements`.

## `reviews` — the main tool

```bash
# Fast sentiment read: score + totals only, no bodies downloaded.
steam-cli reviews "Hades" --summary --json

# A sample of reviews to actually read (cursor-paginated).
steam-cli reviews 1145360 -n 200 --language english --json -q

# Every review to a JSON file (large games = many pages; be patient).
steam-cli reviews 1145360 --all --json --output reviews.json

# Every review to a CSV for Excel / Numbers / Sheets (UTF-8 BOM, flat columns).
steam-cli reviews 1145360 --all --csv --output reviews.csv
# (author_steamid is a 17-digit id — Excel truncates big numbers on auto-open;
#  import it as text, or use --json if you need the exact id.)

# Only negative reviews, to find complaints.
steam-cli reviews 1145360 --review-type negative -n 100 --json -q
```

Key flags: `-n N` (cap, default 100) · `--all` (everything) · `--summary`
(score + totals, one request) · `--language CODE|all` · `--review-type
all|positive|negative` · `--filter recent|updated|all` · `--min-playtime
HOURS` (only reviewers with ≥ N hours) · `--since YYYY-MM-DD` (only reviews
from that date on) · `--jsonl` (one object per line) · `--csv` (flat CSV for
spreadsheets) · `--output FILE`. Pick one of `--json` / `--jsonl` / `--csv`.

`--min-playtime` and `--since` filter the fetched window client-side — combine
with `-n` to e.g. "100 recent reviews, but only from players with 20+ hours".

A review object includes `voted_up`, `review` (text), `timestamp_created`,
`votes_up`, `votes_funny`, `weighted_vote_score`, and `author`
(`playtime_at_review` etc.). `query_summary` carries `review_score_desc`,
`total_reviews`, `total_positive`, `total_negative`.

## Typical agent flows

- **"Tell me about <game>"** → `overview <game> --json -q` — one call gives the
  store card, review score, live players and price together. Prefer this over
  stitching `info` + `players` + `reviews --summary` yourself. For a full
  profile add `--news 3 --top-achievements 5`.
- **"Is <game> well received?"** → `reviews <game> --summary --json` for the
  headline numbers; if asked *why*, follow with `reviews <game> -n 100
  --review-type negative --json -q` and summarize the complaints.
- **Market/competitor research** → `search` to enumerate appids, then
  `overview` per candidate; compare scores, player counts and price in one
  pass.
- **"Is it cheaper elsewhere?"** → `price <game> --cc us,de,ru,br --json`.
- **"What does <game> look like?" / judging art style** → `images <game> --json
  -q`, then open the returned `path`s with your own image-reading capability
  and describe what you see.
- **"Who wrote this review? / understand the reviewer"** → each review object
  carries `author.steamid`; feed it to `profile <steamid> --json` to see the
  player (member-since, playtime context, bio, VAC) — public profiles only.
- **"What's on sale / what's hot right now?"** → `specials --json` for discounts,
  `top-sellers --json` for what's selling.
- **"What changed recently?"** → `news <game> -c 5`.

## What not to do

- Don't fetch `--all` reviews for a huge game (CS2, Dota 2 → millions) unless
  the user explicitly wants the full corpus — it's many requests and a large
  file. Default to `-n` or `--summary`.
- Don't trust the auto-resolved appid for ambiguous or sequel-bearing titles
  without checking `search` — confirm the appid is the one the user means.
- Don't try to fetch a specific user's profile, library, or personal
  achievements — that needs a Steam Web API key and isn't supported here.
- Don't treat `achievements` names as display strings — they're internal API
  ids (`AchClearTartarus`); real names need a Web API key.
