---
name: steam
description: Query the public Steam API via the `steam-cli` command — game reviews, store details, prices, live player counts, news/patch notes, and global achievement stats. No Steam API key needed. Use whenever a task needs real data about a Steam game. English triggers — "get Steam reviews for", "what do players think of <game>", "Steam rating / score of", "is <game> any good on Steam", "Steam store info / genres / release date / developer of", "how many players is <game> has right now", "current players online", "latest patch notes / news for <game>", "Steam price of", "achievement completion rates", "download reviews for <game>", "find the appid of". Russian triggers — «отзывы в стиме на», «достань стим-отзывы по», «что игроки пишут про», «рейтинг игры в стиме», «какая оценка у игры в стиме», «стоит ли брать <игру> в стиме», «инфа об игре в стиме», «жанр / дата релиза / разработчик игры», «сколько сейчас онлайн в», «текущий онлайн игроков», «последние новости / патчноут по игре», «цена игры в стиме», «проценты выполнения ачивок», «выкачай отзывы по», «найди appid игры». Also use proactively when analyzing a game's reception, doing competitor/market research on Steam, or when any step needs a game's appid, sentiment, or store metadata. Do NOT use for: non-Steam stores (Epic, GOG, mobile), a specific user's profile / owned-games library / personal achievements (those need a Steam Web API key and are out of scope), or buying/wishlisting games.
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

## Subcommands

| Command | Use it for |
|---|---|
| `steam-cli reviews <game>` | User reviews + aggregate score/totals |
| `steam-cli info <game>` | Store card: genres, release, devs, metacritic, price, description |
| `steam-cli search <term>` | Resolve a name → appid candidates |
| `steam-cli players <game>` | Live concurrent player count |
| `steam-cli news <game>` | News / patch notes |
| `steam-cli achievements <game>` | Global achievement completion % |
| `steam-cli price <game> [--cc REGION]` | Price + discount for a region |

## `reviews` — the main tool

```bash
# Fast sentiment read: score + totals only, no bodies downloaded.
steam-cli reviews "Hades" --summary --json

# A sample of reviews to actually read (cursor-paginated).
steam-cli reviews 1145360 -n 200 --language english --json -q

# Every review to a file (large games = many pages; be patient).
steam-cli reviews 1145360 --all --output reviews.json

# Only negative reviews, to find complaints.
steam-cli reviews 1145360 --review-type negative -n 100 --json -q
```

Key flags: `-n N` (cap, default 100) · `--all` (everything) · `--summary`
(score + totals, one request) · `--language CODE|all` · `--review-type
all|positive|negative` · `--filter recent|updated|all` · `--jsonl`
(one object per line) · `--output FILE`.

A review object includes `voted_up`, `review` (text), `timestamp_created`,
`votes_up`, `votes_funny`, `weighted_vote_score`, and `author`
(`playtime_at_review` etc.). `query_summary` carries `review_score_desc`,
`total_reviews`, `total_positive`, `total_negative`.

## Typical agent flows

- **"Is <game> well received?"** → `reviews <game> --summary --json` for the
  headline numbers; if asked *why*, follow with `reviews <game> -n 100
  --review-type negative --json -q` and summarize the complaints.
- **"Tell me about <game>"** → `info <game>` (add `players` for live online).
- **Market/competitor research** → `search` to enumerate appids, then `info`
  + `reviews --summary` per candidate; compare scores and player counts.
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
