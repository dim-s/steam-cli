# steam-cli

[🇬🇧 English](./README.md) · [🇷🇺 Русский](./README.ru.md)

Query the **public Steam API** from the command line — game reviews, store
details, prices, current player counts, news/patch notes, and global
achievement stats. Single-file Python 3 CLI, **stdlib only**, no runtime
dependencies, **no Steam Web API key required**.

Inspired by [woctezuma/download-steam-reviews](https://github.com/woctezuma/download-steam-reviews);
the `reviews` subcommand covers that use case and the rest of the public,
key-free storefront API is exposed alongside it.

## Install

Works on macOS, Linux, and Windows. Requires Python 3.8+.

```bash
pip install git+https://github.com/dim-s/steam-cli.git
```

`pip` creates a `steam-cli` executable on your PATH. To upgrade, add `-U`.
Alternatives: `pipx install …` (isolated) or `uv tool install …` (fastest).

**With uv, no install** — the script has PEP 723 inline metadata:

```bash
uvx --from git+https://github.com/dim-s/steam-cli.git steam-cli reviews "Hades" --summary
uv run steam_cli.py reviews "Hades" --summary   # single file, from a checkout
```

**Manual** (single file, no install):

```bash
curl -fsSL https://raw.githubusercontent.com/dim-s/steam-cli/main/steam_cli.py \
  -o ~/.local/bin/steam-cli
chmod +x ~/.local/bin/steam-cli
```

## Quick start

Every `<game>` is an **appid** (`1145360`) or a **name** (`"Hades"`,
resolved via store search — the chosen appid is printed to stderr).

```bash
# one-shot snapshot: info + review score + live players + price in one call
steam-cli overview "Hades" --json

# full profile in one call: also fold in news and top achievements
steam-cli overview "Hades" --news 3 --top-achievements 5 --json

# quick sentiment: score + totals, no download
steam-cli reviews "Hades" --summary

# 200 recent English reviews as JSON for an agent to digest
steam-cli reviews 1145360 -n 200 --language english --json

# recent reviews, but only from players with 20+ hours, since this year
steam-cli reviews 1145360 -n 200 --min-playtime 20 --since 2025-01-01 --json

# everything to a JSON file
steam-cli reviews 1145360 --all --json --output hades-reviews.json

# everything to a CSV for Excel / Numbers / Sheets
steam-cli reviews 1145360 --all --csv --output hades-reviews.csv

# store card: genres, release, devs, metacritic, price
steam-cli info "Baldur's Gate 3"

# …with DLC, screenshots and system requirements
steam-cli info "Baldur's Gate 3" --dlc --screenshots --sysreqs

# download header + screenshots so an agent can *see* the game
steam-cli images 1145360 --what header screenshots --limit 4 --out ./media

# resolve a name to appid candidates
steam-cli search "elden ring"

# live concurrent players
steam-cli players 570

# latest news / patch notes
steam-cli news 1145360 -c 5

# global achievement completion %
steam-cli achievements 1145360

# compare price across regions in one call
steam-cli price 1145360 --cc us,de,ru,br

# what's on sale / what's selling right now
steam-cli specials
steam-cli top-sellers

# market recon: user tags, niche sizing, similar set, review velocity, sales scale
steam-cli tags "Hades"                                   # discovery tags + vote weight
steam-cli browse --tags cozy,roguelike --max-price 15    # size & list a niche
steam-cli similar "Hades"                                # "more like this" set
steam-cli history "Hades"                                # launch spike vs now
steam-cli overview "Hades" --estimate                    # rough Boxleiter owners/revenue

# public profile of a Steam user (e.g. a reviewer's author.steamid)
steam-cli profile 76561197960287930
```

## Subcommands

| Command | Purpose | Steam endpoint |
|---|---|---|
| `overview <game>` | Snapshot: info + review score + players + price, one call | `appdetails` + `appreviews` + `GetNumberOfCurrentPlayers` |
| `reviews <game>` | User reviews + aggregate score | `store/appreviews` |
| `info <game>` | Store details (genres, devs, metacritic, price…) | `store/api/appdetails` |
| `images <game>` | Download header / screenshots / art to local files | `appdetails` + Steam CDN |
| `search <term>` | Name → appid candidates | `store/api/storesearch` |
| `players <game>` | Current concurrent players | `ISteamUserStats/GetNumberOfCurrentPlayers` |
| `news <game>` | News / patch notes | `ISteamNews/GetNewsForApp` |
| `achievements <game>` | Global achievement completion % | `…/GetGlobalAchievementPercentagesForApp` |
| `price <game>` | Price + discount for one or more regions | `store/api/appdetails` |
| `specials` | Games currently on sale (featured specials) | `store/api/featuredcategories` |
| `top-sellers` | Current top-selling games | `store/api/featuredcategories` |
| `tags <game>` | User (community) tags with vote counts | `store/app` page (`InitAppTagModal`) |
| `browse --tags …` | Faceted niche search: size + list by tag/price/sort | `store/search/results` + `store/tagdata` |
| `similar <game>` | "More like this" recommendation set | `store/recommended/morelike` |
| `history <game>` | Review-volume velocity over time | `store/appreviewhistogram` |
| `profile <id>` | Public Steam Community profile (no key) | `steamcommunity.com/…?xml=1` |
| `cache [--path/--clear]` | Inspect or clear the on-disk cache | — (local) |

Add `--json` to any subcommand for raw structured output (ideal for scripts
and agents). Add `-q/--quiet` to suppress the appid-resolution note. All
subcommands accept `--timeout SECS` (default 30) and retry transient HTTP
429/5xx and network errors automatically with backoff.

Under `--json`, failures are reported as a JSON object on **stdout** —
`{"error": "<message>", "code": "<not_found|http|network|parse>"}` — with a
non-zero exit code, so a parser always gets valid JSON instead of an empty
stream.

### `reviews` options

| Flag | Default | Meaning |
|---|---|---|
| `-n, --num N` | `100` | Max reviews to fetch (paginates via cursor) |
| `--all` | off | Fetch every available review |
| `--summary` | off | Only the score + totals, no review bodies |
| `--language CODE` | `all` | Steam language code (`english`, `russian`, …) or `all` |
| `--review-type` | `all` | `all` / `positive` / `negative` |
| `--purchase-type` | `all` | `all` / `steam` / `non_steam_purchase` |
| `--filter` | `recent` | `recent` / `updated` (paginate) or `all` (by helpfulness within `--day-range`) |
| `--day-range N` | — | Window for `--filter all` (1–365) |
| `--offtopic` | off | Include review-bomb (off-topic) activity |
| `--min-playtime HOURS` | — | Keep only reviews whose author had ≥ HOURS at review time |
| `--since YYYY-MM-DD` | — | Keep only reviews created on/after this date |
| `--jsonl` | off | One review JSON object per line |
| `--csv` | off | Flat CSV for Excel / Numbers / Sheets (UTF-8 BOM when written to a file) |
| `--output FILE` | stdout | Write to a file |
| `--delay SECS` | `0.3` | Pause between pages (politeness) |

## Language & country codes

`--lang` / `--language` and `--cc` accept flexible input and normalize it to
what Steam actually expects, so you get the data you asked for instead of a
silent wrong answer:

- **Language**: pass an ISO code (`en`, `ru`, `ko`, `zh`, `pt-br`, `zh-tw`) or
  a Steam name (`english`, `russian`, `koreana`, `schinese`, `brazilian`). A
  raw ISO code like `ru` sent straight to Steam would silently fall back to
  English — normalization maps it to `russian` so store text is really
  localized. `reviews --language all` is preserved.
- **Country**: pass alpha-2 (`us`), alpha-3 (`usa`), or a common alias
  (`uk`→`gb`). A wrong code like `usa` sent raw returns a *wrong* price, not an
  error — normalization fixes it to `us`.
- **Unknown codes are a hard error** with a hint (`unrecognized language 'germ';
  did you mean "german"?`) and exit code 1 — never quietly-wrong data.

```bash
steam-cli info "Hades" --lang ru          # Russian store text
steam-cli reviews "Hades" --language ko   # Korean reviews
steam-cli price "Hades" --cc usa,uk,br    # normalized to us, gb, br
```

## Caching

Responses and downloaded images are cached on disk so repeated queries are
near-instant and don't re-hit Steam. The cache directory is platform-native
(override with `STEAM_CLI_CACHE_DIR`):

| OS | Default cache directory |
|---|---|
| Linux | `$XDG_CACHE_HOME/steam-cli` or `~/.cache/steam-cli` |
| macOS | `~/Library/Caches/steam-cli` |
| Windows | `%LOCALAPPDATA%\steam-cli\cache` |

Freshness rules: JSON responses are cached for **6 hours**; downloaded images
are cached **forever** (their URLs carry a `?t=` version stamp, so a changed
asset is a new URL); **live player counts and review pages are never cached**.

Per-command overrides (all subcommands): `--no-cache` (always fetch fresh,
don't write), `--refresh` (ignore cached entries but refresh them), `--cache-ttl
SECS` (custom JSON freshness). Set `STEAM_CLI_NO_CACHE=1` to disable globally.

Manage the cache:

```bash
steam-cli cache            # show its location and size
steam-cli cache --path     # print just the directory (script-friendly)
steam-cli cache --clear    # delete all cached files
```

## TLS interception / corporate proxies

If the host intercepts HTTPS with a custom root CA that isn't in Python's
trust store, urllib's cert check fails. `steam-cli` automatically retries
the request through the system `curl` (which uses the OS trust store), so it
keeps working on such machines. Force a backend with
`STEAM_CLI_HTTP=urllib|curl`, or pass `-k/--insecure` to skip verification.

## Notes & limits

- **No API key** — only Steam's public, key-free endpoints are used. Player
  profiles, a user's owned-games library, and per-user achievements need a
  Steam Web API key and are intentionally out of scope.
- **Name resolution picks the top store-search hit** — `"Hades"` may resolve
  to *Hades II*. Pass an explicit appid (or check `steam-cli search`) when it
  matters; the resolved appid is always printed to stderr.
- **`achievements` returns internal API ids**, not display names —
  human-readable names/descriptions require a Web API key (`GetSchemaForGame`).
- **Aggregate totals** (`reviews --summary`) reflect the active `--language`
  filter; use `--language all` (the default) for global numbers.
- **Recon commands read storefront markup** — `tags`, `browse` and `similar`
  parse Steam's own HTML/search feed (still first-party, no third-party
  aggregators). If Steam reshapes a page the parser raises a `parse` error
  rather than returning silently-empty data.
- **`overview --estimate` is a Boxleiter heuristic** (owners ≈ reviews ×
  multiplier), reported as a range — order-of-magnitude only, not a Steam
  figure.
- **Wishlists, followers, and historical player/price curves are not
  available** key-free (they live only in SteamDB or the Steamworks partner
  backend). `history` (review velocity) is the nearest public momentum proxy.

## Use it from an AI agent

`steam-cli` runs fine by hand, but it's **built to be driven by an AI agent**
(Claude Code, Codex CLI, opencode, …): you ask in plain language and the agent
picks the commands and parses the result. The whole design serves machine
consumption — `--json` on every subcommand, machine-readable errors as
`{"error", "code"}` on stdout, the appid-resolution note kept on stderr, and a
self-install path so an agent can bootstrap the tool when it's missing.

**Claude Code** — the [`skill/`](./skill) directory is a ready-to-use skill
(`SKILL.md` + `setup.md`): the agent knows when to apply it, invokes
`steam-cli`, and self-installs it when missing. Symlink it into your skills dir,
then restart Claude Code (or `/reload`):

```bash
ln -s "$(pwd)/skill" ~/.claude/skills/steam
```

**Codex CLI / opencode / other `AGENTS.md`-aware agents** — they read
[`AGENTS.md`](./AGENTS.md) from the repo root, so an agent working inside this
repo picks up the contract automatically. To make the agent aware of
`steam-cli` **in your own project** (e.g. your game's repo), drop a short block
into that project's `AGENTS.md`:

```markdown
## Steam data (steam-cli)

`steam-cli` is installed and gives real Steam data with no API key.
Use it for market research; always pass `--json` when you'll parse the output.

- steam-cli overview "<game>" --json            # info + score + players + price
- steam-cli reviews "<game>" --summary --json   # review sentiment
- steam-cli browse --tags cozy,roguelike --max-price 15 --json   # size a niche
- steam-cli similar "<game>" --json             # adjacent games
- steam-cli overview "<game>" --estimate --json # rough owners/revenue
```

**Any other agent** — anything that can run a shell command and read JSON works:
install the CLI (above) and call commands with `--json`. The contract is the
same `{"error", "code"}`-on-failure, appid-note-on-stderr, normalized
`--lang`/`--cc` described throughout this README.

## License

[MIT](./LICENSE) © 2026 Дмитрий Зайцев (Dmitry Zaytsev)
