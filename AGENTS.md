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

## Task board (boardown) — keep it current proactively

This repo carries a markdown task board in `.boardown/`, driven by the
`boardown` CLI. The board is the agent's persistent work queue — **the agent
keeps it, the owner does not.** Mechanics and error handling live in the
`boardown` skill; the project conventions on top of it:

- **Drive it only through the CLI with `--json`** (it assigns ids, validates the
  schema, refuses to clobber a changed file). Never hand-edit files in
  `.boardown/`.
- **Keep it current proactively — including in ordinary chat, not just on an
  autopilot run.** When the owner hands you work and you take it on, record it
  on the board without being asked, moving it through the same status cycle.
  **The bar is *work*, not *talk*:** log a substantive unit (feature / bug fix /
  refactor / notable change); skip questions, reading, design discussion with no
  implementation, and trivial one-liners — otherwise the board becomes a diary.
  Logging is housekeeping, so don't ask permission; just say it in one phrase
  ("logged `SC-NN`"). A short edit that grew into real work — file it after the
  fact straight as `done`.
- **Status discipline:** taking a task → set `in-progress` atomically (not "about
  to"); shipped and green → `done`; spot an adjacent bug/debt mid-task → file a
  `todo` immediately (don't fix it silently, don't derail the current task).
- **Closing note before `done`.** Add a short `note` (1–3 sentences): the key
  decision or trade-off, what's left or needs runtime checking, a non-obvious
  nuance. Not a diff recap (git has that), not a `description` echo (that's the
  "why") — it's "what we learned in doing it".
- **`description` carries the "why", not just the "what"** — the board is often
  the only persistent home of a task between sessions.
- **One standing release + backlog (standing-release).** Hold exactly one
  `current` release — `Active` — as the whole working board. Steam/PyPI versions
  live in **git tags + `__version__`/`pyproject.toml`**, not in releases. The CLI
  never deletes releases: retire spent ones to `finished` (read-only archive).
  At a rare real release, sweep (see the `boardown` skill, standing-release §).
- **Idea ≠ task.** Feedback and "might-not-do" notions are **candidates** in the
  `ideas` epic (a freezer the agent never pulls work from — the live queue is the
  `current` release only). Promote a candidate with `task edit` (move into the
  release, set the type) once it's decided. Never drop an idea into the
  release/backlog as a plain `todo` — the agent would pick it up unprompted.
- **Task content is written in Russian** (the owner's working language); code
  identifiers, endpoints and flags stay verbatim. Task types: `bug`, `tech`,
  `feature`, `docs` (`docs` only for shipped docs like the README / `SKILL.md`).
- **Don't duplicate sources of truth.** The README and `skill/SKILL.md` stay the
  reference; a board task **links** to them rather than copying them.

## Working ON this repo (contributors)

- **Single file, stdlib only.** All code lives in `steam_cli.py` — no runtime
  dependencies. Don't add a third-party import to the runtime path.
- **No third-party data sources.** Only Steam's own first-party endpoints
  (storefront API + public HTML/search). No SteamSpy / SteamDB scraping.
- **Run the tests:** `python3 -m pytest -q` (offline — the network is mocked
  against captured fixtures in `tests/fixtures/`). Keep them green.
- **Version bump:** update `__version__` in `steam_cli.py` **and** `version` in
  `pyproject.toml` together.
