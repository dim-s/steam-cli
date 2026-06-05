# steam-cli

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
# quick sentiment: score + totals, no download
steam-cli reviews "Hades" --summary

# 200 recent English reviews as JSON for an agent to digest
steam-cli reviews 1145360 -n 200 --language english --json

# everything to a file
steam-cli reviews 1145360 --all --output hades-reviews.json

# store card: genres, release, devs, metacritic, price
steam-cli info "Baldur's Gate 3"

# resolve a name to appid candidates
steam-cli search "elden ring"

# live concurrent players
steam-cli players 570

# latest news / patch notes
steam-cli news 1145360 -c 5

# global achievement completion %
steam-cli achievements 1145360

# price in a specific region
steam-cli price 1145360 --cc de
```

## Subcommands

| Command | Purpose | Steam endpoint |
|---|---|---|
| `reviews <game>` | User reviews + aggregate score | `store/appreviews` |
| `info <game>` | Store details (genres, devs, metacritic, price…) | `store/api/appdetails` |
| `search <term>` | Name → appid candidates | `store/api/storesearch` |
| `players <game>` | Current concurrent players | `ISteamUserStats/GetNumberOfCurrentPlayers` |
| `news <game>` | News / patch notes | `ISteamNews/GetNewsForApp` |
| `achievements <game>` | Global achievement completion % | `…/GetGlobalAchievementPercentagesForApp` |
| `price <game>` | Price + discount for a region | `store/api/appdetails` |

Add `--json` to any subcommand for raw structured output (ideal for scripts
and agents). Add `-q/--quiet` to suppress the appid-resolution note.

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
| `--jsonl` | off | One review JSON object per line |
| `--output FILE` | stdout | Write to a file |
| `--delay SECS` | `0.3` | Pause between pages (politeness) |

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

## Claude Code skill

The [`skill/`](./skill) directory holds a ready-to-use skill (`SKILL.md` +
`setup.md`) so a Claude Code agent can invoke `steam-cli` on demand and
self-install it when missing. Symlink it into your skills dir:

```bash
ln -s "$(pwd)/skill" ~/.claude/skills/steam
```

Then restart Claude Code (or `/reload`) so the skill is indexed.

## License

[MIT](./LICENSE) © 2026 Дмитрий Зайцев (Dmitry Zaytsev)
