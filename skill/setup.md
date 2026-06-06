# setup — install `steam-cli` when missing

Read this only when `steam-cli --version` fails with "command not found"
(or the Windows equivalent). Otherwise the tool is already installed — skip
this file.

## Pick one install command

All install the same thing; pick the first available on the host. Requires
**Python 3.8+** (check with `python3 --version`).

```bash
# (1) pip — universal, present in every Python install
pip install git+https://github.com/dim-s/steam-cli.git

# (2) pipx — isolates the install in its own venv (recommended if available)
pipx install git+https://github.com/dim-s/steam-cli.git

# (3) uv tool — fastest; needs `uv` installed
uv tool install git+https://github.com/dim-s/steam-cli.git
```

### uv without installing

`steam_cli.py` carries PEP 723 inline metadata, so `uv` can run it with zero
install:

```bash
# run the published package as a one-off (no install)
uvx --from git+https://github.com/dim-s/steam-cli.git steam-cli reviews "Hades" --summary

# run the single file directly (after curl-ing it, or from a checkout)
uv run steam_cli.py reviews "Hades" --summary
```

### Local checkout

If the repo is already cloned (e.g. `~/dev/steam-cli`), install from the
path — editable keeps the command in sync with edits:

```bash
pip install -e ~/dev/steam-cli
```

## Verify

```bash
steam-cli --version   # → steam-cli 1.3.1
steam-cli --help
```

If the command still isn't found, `pip`'s scripts directory may not be on
`PATH`:

- macOS / Linux: `~/.local/bin` (user install) or the venv's `bin/`.
- Windows: `%APPDATA%\Python\PythonXY\Scripts` or the venv's `Scripts\`.

Add that directory to `PATH`.

## Manual fallback (no pip)

Single-file script, runs anywhere `python3` is on `PATH`:

```bash
curl -fsSL https://raw.githubusercontent.com/dim-s/steam-cli/main/steam_cli.py \
  -o ~/.local/bin/steam-cli
chmod +x ~/.local/bin/steam-cli
```

On Windows without WSL, save the script and invoke as
`python steam_cli.py <command> …`.

## TLS interception note

On machines that intercept HTTPS with a custom root CA, Python's cert check
can fail. `steam-cli` auto-falls back to the system `curl`, so it should
just work. If needed, force a backend with `STEAM_CLI_HTTP=curl` (or
`urllib`), or pass `-k/--insecure`.

## After install

Return to [SKILL.md](SKILL.md) and proceed with the requested task. No
account, token, or API key is needed — the CLI uses Steam's public
endpoints directly.
