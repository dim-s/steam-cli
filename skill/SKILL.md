---
name: steam
description: Real Steam data via the `steam-cli` command, no API key needed — reviews and sentiment, store info (genres, release, devs, price, metacritic), live player counts, news and patch notes, achievement stats, screenshots and art, current specials and top sellers, public player profiles, plus market recon: user tags, niche sizing by tag and price, similar games, review-velocity history, rough sales estimates. Use for any question about a Steam game, its reception, its niche or its competitors — "is <game> good on Steam", "how many copies did it sell", "players online now", "what's it tagged as", "games like X", "how big is the cozy/roguelike niche", "find the appid", "who wrote this review", "did its launch hold up". Russian — «отзывы/оценка в стиме», «жанр/дата/цена игры», «онлайн игроков», «патчноут», «ачивки», «найди appid», «теги игры», «похожие игры», «размер ниши», «сколько копий продала», «профиль игрока». ALSO, when the user SHIPS games on Steam and has set a publisher API key — wishlist reporting, sales and revenue, and the app portfolio for THEIR OWN games: "my wishlists", "how many wishlists did I get", "my sales / revenue", «мои вишлисты», «мои продажи/выручка», «статистика по моей игре». Do NOT use for non-Steam stores (Epic/GOG/mobile), another player's library or private profile, wishlist counts for a game the user does NOT ship (impossible at any permission level), page traffic or impressions (Steamworks web UI only), or buying/wishlisting.
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
`invalid` (a bad `--lang`/`--cc` value) / `auth` (a key-only command was run
without a publisher key, or the key lacks the needed permission — the message
says which).

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
| `steam-cli coming-soon` | **Upcoming** pre-release games (front page) |
| `steam-cli new-releases` | **Recently released** games (front page) |
| `steam-cli tags <game>` | **User (community) tags** with vote counts — Cozy, Roguelike, Wholesome… (what `info` genres/categories miss) |
| `steam-cli browse --tags …` | **Faceted niche search** — size *and* list a niche by tag / price / sort (the only key-free "map the niche" query) |
| `steam-cli similar <game>` | Steam **"more like this"** recommendations (buyers-also-viewed) |
| `steam-cli history <game>` | **Review-volume velocity over time** — launch spike vs now, peak month |
| `steam-cli overview <game> --estimate` | Add a rough **Boxleiter sales estimate** (owners/revenue range) to the overview |
| `steam-cli profile <id>` | **Public** Steam Community profile (steamID64 / vanity); pairs with a reviewer's `author.steamid` |
| `steam-cli cache [--path\|--clear]` | Inspect or clear the on-disk cache |

**Publisher-key commands** — only for games the key's account ships. Without a
key they fail with `code: "auth"` and a message saying how to set one up:

| Command | Use it for |
|---|---|
| `steam-cli auth [--set\|--clear\|--path]` | Configure the optional publisher key (`--set` reads it from **stdin**) |
| `steam-cli mygames [--type game]` | **Every app your key's account ships**, grouped by type (game / demo / dlc / music / beta) |
| `steam-cli wishlist <game>` | **Wishlist reporting for YOUR game** — adds / deletes / purchases / gifts per day, by country, by language, by OS |
| `steam-cli sales` | **Units and revenue for YOUR games** — gross/net USD by package, platform, country |

## JSON output shapes

What each command returns under `--json`, so you can parse without guessing.
**Note the shape varies** — some commands return a bare array, others an
object; only `overview`, `reviews`, `price`, `images`, `players` carry the
`appid` at the top level. A failure is always `{"error", "code"}` instead.

| Command | Shape |
|---|---|
| `overview` | `{appid, name, type, release_date, coming_soon, developers, publishers, genres, metacritic, is_free, price_cc, price, players_online, review_summary, store_url, news?, top_achievements?, sales_estimate?}` (`price_cc` = the region the `price` is for; `sales_estimate` only with `--estimate`) |
| `reviews` | `{appid, query_summary:{review_score, review_score_desc, total_reviews, total_positive, total_negative}, count, reviews:[…]}` (`review_score` = Steam's 0-9 sentiment tier) · `--summary` → `{appid, query_summary}` (also `--summary --jsonl` → one JSON line) · `--jsonl` → one review object per line · `--csv` → columns incl. `refunded`, `early_access`, `steam_deck`, `author_personaname`, `author_num_games_owned`, `playtime_last_two_weeks_hours` |
| `info` | raw Steam appdetails object: `{steam_appid, name, type, is_free, genres:[{id, description}], release_date:{date, coming_soon}, developers, price_overview?, …}` (large) |
| `search` | **bare array** `[{id, name, type, price?}]` — no `appid` wrapper |
| `players` | `{appid, player_count}` |
| `news` | **bare array** `[{gid, title, url, author, contents, feedlabel, date}]` |
| `achievements` | **bare array** `[{name, percent}]`, sorted by `percent` desc (names are internal API ids) |
| `price` | `{appid, regions:[{cc, name, is_free, price_overview} \| {cc, error}]}` |
| `images` | `{appid, out, images:[{kind, url, path} \| {kind, url, error}]}` |
| `specials` / `top-sellers` / `coming-soon` / `new-releases` | `{section, cc, count, items:[{id, name, discounted, discount_percent, original_price, final_price, currency, header_image}]}` — **prices are integer minor units** (cents): `final_price: 899` = $8.99 (a pre-release item may have `null` prices) |
| `tags` | `{appid, count, tags:[{tagid, name, count, browseable}]}` — `count` is the user vote weight; sorted high→low |
| `browse` | `{tags, tag_ids, sort, max_price, cc, niche_size, count, items:[{appid, name, tagids}]}` — **`niche_size` is the full match count** (the niche size), `count`/`items` are the page you asked for |
| `similar` | `{appid, count, similar:[{appid, name, tagids}]}` — `name` is derived from the store URL slug (may be null); recommendations, **not** a curated competitor set |
| `history` | `{appid, summary:{buckets, rollup_type, window, overall, launch, tail, recent_30d, peak}, rollups:[{date, recommendations_up, recommendations_down}], recent_30d:[…]}` — each agg is `{up, down, total, pct_positive}`; `date` is epoch seconds |
| `overview --estimate` | adds `sales_estimate:{method, data, total_reviews, owners:{conservative,mid,optimistic:{multiplier,owners}}, revenue_usd, price_usd}` — `revenue_usd` null for free/unknown-price; **rough order-of-magnitude, not a Steam figure** |
| `profile` | `{steamid64, name, private, privacy_state, online_state, state_message, member_since, location, real_name, summary, vac_banned, avatar, profile_url}` — non-`public` profiles set `private:true` and null the hidden fields |
| `wishlist` | `{appid, days:[{date, adds, deletes, purchases, gifts, adds_by_os:{windows,mac,linux}, countries:[{cc, country, region, adds, deletes, purchases, gifts}], languages:[…]}], total:{adds, deletes, purchases, gifts, net_adds}, data_since}` — **`adds_by_os` does NOT sum to `adds`**: Steam attributes only some adds to an OS |
| `sales` | `{appid, days:[{date, units, gross_usd, net_usd, rows:[{packageid, package, appid, game, platform, cc, units, gross_usd, net_usd}]}], total:{units, gross_usd, net_usd}}` — `net_usd` is after Steam's share and refunds; money arrives from Steam as strings and is normalised to numbers here |
| `mygames` | `{count, by_type:{game, demo, dlc, music, beta}, apps:[{appid, name, type}]}` — includes UNRELEASED and unannounced entries, so treat the list as confidential |
| `auth` (no flags) | `{configured, source, key, config_path, warning}` — `key` is masked (`····8871`), never the real value |

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

## Market recon — `tags` / `browse` / `similar` / `history` / `--estimate`

For competitor and niche research, these reach Steam's *own* storefront
surfaces that the JSON web API doesn't expose (user tags, the faceted search
feed, the recommendation grid, the review histogram). First-party Steam only —
no third-party aggregators.

```bash
# What discovery tags is a game actually surfaced under (with vote weight)?
# info gives valve genres/categories; THIS gives Cozy/Wholesome/Roguelike etc.
steam-cli tags "Hades" --json -q

# Map a niche: how big is it, and what's in it?  niche_size = full match count.
steam-cli browse --tags cozy,roguelike --max-price 15 --sort reviews --count 30 --json
#   --tags  comma-separated names (resolved via Steam's tag dict) or raw ids
#   --max-price USD (0 = free only)  --sort reviews|released|price-asc|price-desc|name

# Algorithmic "more like this" set for a game (buyers-also-viewed).
steam-cli similar "Hades" --json -q

# Did a game's launch spike die or hold?  Review velocity over time.
steam-cli history "Hades" --months 12 --json -q

# Rough sales/owners scale from the review count (Boxleiter), folded into overview.
steam-cli overview "Hades" --estimate --json -q
```

**`tags`** — the discovery tags a game ranks under, with vote counts. This is
what `info` (valve genres + categories) misses and what the cozy/casual niche
actually runs on. Use the tag **names/ids** here to feed `browse`.

**`browse`** — the only key-free way to *size and list* a niche. `niche_size`
is the full count of games matching your filters (e.g. "1,056 cozy games ≤
$15"); `items` is just the page you paged in (`--count`). Use it to build a
"map of the niche" and compare competitor density. Tag names are resolved via
Steam's public tag dictionary; an unknown tag is a hard `invalid` error with a
hint — run `tags <game>` to discover real names.

**`similar`** — Steam's "more like this" recommendations. These are
*buyers-also-viewed*, **not** a curated competitor list — useful for an outreach
set or to find adjacent games, but treat the list as recommendations, not "my
exact competitors." `name` comes from the URL slug and may be null.

**`history`** — review-volume velocity. `summary` compares the **launch**
window (first 3 buckets) to the **recent** window (last 3) and the last 30 days,
plus the **peak** bucket — so you can answer "did the launch spike hold or
die?". It's review *volume* over time, the closest key-free proxy for momentum
(true wishlist/follower and concurrent-player history live only in SteamDB /
the partner backend and are **not** reachable here).

**`overview --estimate`** — a Boxleiter owners/revenue estimate (owners ≈
reviews × multiplier) as a **range** (conservative 20× / mid 40× / optimistic
80×), or a single `--multiplier N`. This is an external heuristic on top of a
real Steam figure (the review count), **not** a Steam-reported sales number —
the error bars are large; use it for order-of-magnitude, never precision.

> **Not reachable key-free (don't fabricate it):** wishlists & followers, and
> historical concurrent-player / price curves. These live only in SteamDB or
> the Steamworks partner backend. `history` (review velocity) is the nearest
> public proxy for pre-launch/retention momentum.

## Your own games — the optional publisher key

Everything else in this skill is key-free and works for **any** game. This
section is different: it reports on games the key's Steam account **ships**.

```bash
# Is a key configured, and where did it come from? (never prints the key)
steam-cli auth --json

# Which games are on this key's account? (start here — gives you the appids)
steam-cli mygames --json
steam-cli mygames --type game --json

# Wishlist numbers for your game — yesterday by default (today is partial)
steam-cli wishlist "My Game" --json
steam-cli wishlist "My Game" --days 30 --json     # a month, aggregated + per day
steam-cli wishlist <your-appid> --date 2026-07-10 --json

# Units and revenue across your whole account, or one game
steam-cli sales --days 7 --json
steam-cli sales --game <your-appid> --date 2026-07-10 --json
```

**Setting the key** — environment `STEAM_CLI_API_KEY` (or `STEAM_API_KEY`), or
`steam-cli auth --set`, which reads it from **stdin** and stores it `0600`.
There is deliberately **no `--api-key` flag**: an argument is visible to other
users via `ps` and lands in shell history. Never echo a user's key back to
them, never write it into a file you create, and never put it in a command
line.

**Two permission traps, both cost real time:**

1. `wishlist` needs only a normal publisher key. **`sales` additionally needs a
   Financial API Group with the "Sales Data" permission** (Steamworks → Users &
   Permissions → Manage Groups). This is *not* the "Financial" checkbox on the
   key itself — that one only covers `ISteamMicroTxn` (in-game purchases).
2. When the permission is missing, Steam returns an empty response rather than
   an error. `steam-cli` translates that into `code: "auth"` with an
   explanation — **do not report it to the user as "zero sales"**, it means
   "no permission".

**Timezones differ between the two reports** and steam-cli passes `--date`
through as given: wishlist reporting is **GMT**, detailed sales is **US
Pacific**. A day-level comparison of the two is off by up to 8 hours.

**What no key can give you** — save the user the search:

- wishlist counts for a game they don't own (does not exist at any level);
- page traffic / impressions / UTM stats (Steamworks web UI only, no API);
- another player's library or private profile.

**When hunting for a Steam endpoint, don't trust
`ISteamWebAPIUtil/GetSupportedAPIList`.** It is a shop window, not a registry:
`IPartnerFinancialsService` (wishlists and sales) is absent from it entirely,
and other interfaces list a fraction of their real methods. Check the
[partner docs](https://partner.steamgames.com/doc/webapi) instead.

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
- **Market/competitor research** → `browse --tags <niche> --max-price <cap>` to
  size and list the niche (`niche_size` = how crowded it is), then `overview`
  per candidate; compare scores, player counts and price in one pass. Add
  `similar <game>` for the algorithmic neighbour set.
- **"What niche/tags is <game> in? / position it"** → `tags <game>` for the
  discovery tags it ranks under (with vote weight) — what genres/categories miss.
- **"Did <game>'s launch hold or fade?"** → `history <game>` — compare the launch
  window to recent in `summary` (review velocity is the public momentum proxy;
  wishlist/follower history is SteamDB-only, not available here).
- **"Roughly how many copies did <game> sell?"** → `overview <game> --estimate`
  for a Boxleiter owners/revenue *range* — order-of-magnitude only, not exact.
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
