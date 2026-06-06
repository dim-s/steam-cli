# AGENTS.md — steam-cli

Guidance for AI coding agents (Codex CLI, opencode, Cursor, Aider, and any
other agent that reads `AGENTS.md`). There's also a portable **Agent Skill** in
[`skill/`](./skill) (open `SKILL.md` format — works in Claude Code, Codex CLI,
opencode, Cursor, …) that loads on demand; see the README to install it.

`steam-cli` is a single-file Python 3 CLI that pulls **real data from Steam's
public API with no key**: reviews, store info, prices, live players, news,
achievements, sales, public profiles, plus market recon (user tags, niche
sizing, similar games, review history, rough sales estimates).

## Using the CLI (the contract you rely on)

- **Always pass `--json` when you'll parse the result.** Every subcommand
  supports it and returns structured data; the default output is for humans.
- **Errors are machine-readable.** On failure the command prints
  `{"error": "<msg>", "code": "<not_found|http|network|parse|invalid>"}` to
  **stdout** and exits non-zero — a parse always yields a JSON object, never an
  empty stream.
- **`<game>` is an appid (`1145360`) or a name (`"Hades"`).** A name resolves
  to the top store hit (printed to **stderr**, so `--json` stdout stays clean).
  The top hit isn't always right (`"Hades"` → *Hades II*) — pass an appid or
  run `search` first when the exact title matters. Add `-q` to silence the note.
- **Languages/regions are normalized** (`--lang ru`, `--cc usa`→`us`); an
  unknown code is a hard `invalid` error, never silently-wrong data.
- **Caching is automatic** (JSON ~6h, images forever, live players never) — query
  freely; repeats are near-instant. `--no-cache` / `--refresh` override per call.
- **Transient HTTP 429/5xx and network blips are retried** with backoff.

## Most useful commands

```bash
steam-cli overview "<game>" --json              # one-shot: info + score + players + price
steam-cli reviews "<game>" --summary --json     # review sentiment (score + totals)
steam-cli reviews "<game>" -n 100 --review-type negative --json   # read complaints
steam-cli tags "<game>" --json                  # discovery tags (Cozy/Roguelike…)
steam-cli browse --tags cozy,roguelike --max-price 15 --json      # size & list a niche
steam-cli similar "<game>" --json               # adjacent games
steam-cli history "<game>" --json               # did the launch spike hold?
steam-cli overview "<game>" --estimate --json   # rough Boxleiter owners/revenue range
steam-cli price "<game>" --cc us,de,ru --json   # compare regional pricing
steam-cli images "<game>" --json                # download art, then open it to *see* the game
```

A fuller command reference and JSON shapes are in [`skill/SKILL.md`](./skill/SKILL.md).

## Don't

- Don't `--all` reviews for a huge game (CS2, Dota 2 → millions) unless asked.
- Don't trust the auto-resolved appid for sequels without checking `search`.
- Don't try to read a user's owned games / private profile / per-user
  achievements — those need a Steam Web API key and aren't supported.
- Don't fabricate wishlists/followers or concurrent-player history — they
  aren't reachable key-free; `history` (review velocity) is the closest proxy.

## Working ON this repo (contributors)

- **Single file, stdlib only.** All code lives in `steam_cli.py` — no runtime
  dependencies. Don't add a third-party import to the runtime path.
- **No third-party data sources.** Only Steam's own first-party endpoints
  (storefront API + public HTML/search). No SteamSpy / SteamDB scraping.
- **Run the tests:** `python3 -m pytest -q` (offline — the network is mocked
  against captured fixtures in `tests/fixtures/`). Keep them green.
- **Version bump:** update `__version__` in `steam_cli.py` **and** `version` in
  `pyproject.toml` together.
