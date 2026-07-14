#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""steam-cli — query the public Steam API (no API key needed).

Subcommands: overview, reviews, info, search, players, news, achievements,
price, images, specials, top-sellers, profile, tags, browse, similar, history.
Stdlib only. Transparently falls back from urllib to `curl` when the host
intercepts TLS (corporate proxies, dev machines with a custom root CA).
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

__version__ = "1.4.0"

STORE = "https://store.steampowered.com"
API = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com"
USER_AGENT = f"steam-cli/{__version__} (+https://github.com/dim-s/steam-cli)"


class SteamError(Exception):
    """User-facing error: printed without a traceback.

    `code` is a short machine-readable slug (not_found / http / network /
    parse / error) surfaced in the JSON error envelope so an agent can branch
    on the failure kind without parsing the message.
    """

    def __init__(self, message: str, code: str = "error",
                 status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        # HTTP status when known (curl path maps exit 22 → an http error); lets
        # the retry loop decide retriability without re-parsing the message.
        self.status = status


# ----- locale normalization ------------------------------------------------
# Steam wants its OWN language names (english/russian/koreana/schinese), and an
# ISO code like "ru" passed straight through silently falls back to English.
# Countries are worse: a wrong cc (alpha-3 "usa", or junk) silently yields a
# bad price rather than an error. So normalise both to what Steam expects and
# reject the truly unknown with a hint, instead of returning quietly-wrong data.

_STEAM_LANGUAGES = frozenset((
    "arabic bulgarian schinese tchinese czech danish dutch english finnish "
    "french german greek hungarian indonesian italian japanese koreana "
    "norwegian polish portuguese brazilian romanian russian spanish latam "
    "swedish thai turkish ukrainian vietnamese"
).split())

# ISO-639 (and common variants) -> Steam language name. Language namespace is
# separate from country, so "uk" -> ukrainian here is fine (uk -> gb below).
_LANG_ALIASES = {
    "ar": "arabic", "bg": "bulgarian",
    "zh": "schinese", "zh-cn": "schinese", "zh-hans": "schinese",
    "zh-chs": "schinese", "chs": "schinese",
    "zh-tw": "tchinese", "zh-hant": "tchinese", "zh-cht": "tchinese",
    "zh-hk": "tchinese", "cht": "tchinese",
    "cs": "czech", "cz": "czech", "da": "danish", "nl": "dutch",
    "en": "english", "fi": "finnish", "fr": "french", "de": "german",
    "el": "greek", "gr": "greek", "hu": "hungarian", "id": "indonesian",
    "it": "italian", "ja": "japanese", "jp": "japanese",
    "ko": "koreana", "kr": "koreana",
    "no": "norwegian", "nb": "norwegian", "nn": "norwegian",
    "pl": "polish", "pt": "portuguese", "pt-pt": "portuguese",
    "pt-br": "brazilian", "ro": "romanian", "ru": "russian",
    "es": "spanish", "es-es": "spanish",
    "es-419": "latam", "es-mx": "latam", "es-la": "latam",
    "sv": "swedish", "th": "thai", "tr": "turkish",
    "uk": "ukrainian", "ua": "ukrainian", "vi": "vietnamese",
    # natural English names that differ from Steam's spelling
    "korean": "koreana", "chinese": "schinese",
    "simplified-chinese": "schinese", "traditional-chinese": "tchinese",
}

# Valid ISO-3166-1 alpha-2 codes (used to accept/reject a country).
_VALID_CC = frozenset((
    "ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi "
    "bj bl bm bn bo bq br bs bt bv bw by bz ca cc cd cf cg ch ci ck cl cm cn "
    "co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi fj fk "
    "fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm "
    "hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn "
    "kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk "
    "ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np "
    "nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw "
    "sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz tc td tf "
    "tg th tj tk tl tm tn to tr tt tv tw tz ua ug um us uy uz va vc ve vg vi "
    "vn vu wf ws ye yt za zm zw"
).split())

# alpha-3 and common colloquial forms -> alpha-2 (a pragmatic subset; obscure
# alpha-3 codes fall through to an error rather than bloating the table).
_CC_ALIASES = {
    "usa": "us", "gbr": "gb", "uk": "gb", "deu": "de", "ger": "de",
    "fra": "fr", "rus": "ru", "jpn": "jp", "chn": "cn", "kor": "kr",
    "bra": "br", "can": "ca", "aus": "au", "esp": "es", "spa": "es",
    "ita": "it", "mex": "mx", "nld": "nl", "hol": "nl", "swe": "se",
    "nor": "no", "dnk": "dk", "den": "dk", "fin": "fi", "pol": "pl",
    "tur": "tr", "ukr": "ua", "ind": "in", "idn": "id", "tha": "th",
    "vnm": "vn", "vie": "vn", "are": "ae", "uae": "ae", "sau": "sa",
    "ksa": "sa", "zaf": "za", "rsa": "za", "arg": "ar", "chl": "cl",
    "col": "co", "per": "pe", "prt": "pt", "por": "pt", "che": "ch",
    "sui": "ch", "swi": "ch", "aut": "at", "bel": "be", "irl": "ie",
    "ire": "ie", "nzl": "nz", "sgp": "sg", "hkg": "hk", "twn": "tw",
    "phl": "ph", "phi": "ph", "grc": "gr", "gre": "gr", "isr": "il",
    "egy": "eg", "mys": "my", "pak": "pk", "bgd": "bd", "ban": "bd",
    "cze": "cz", "hun": "hu", "rou": "ro", "rom": "ro", "bgr": "bg",
    "bul": "bg", "hrv": "hr", "srb": "rs", "svk": "sk", "svn": "si",
    "ltu": "lt", "lva": "lv", "est": "ee", "isl": "is", "ice": "is",
}


def _suggest(value: str, canon) -> str | None:
    """Cheap (no-Levenshtein) hint: prefer a prefix match, then a substring."""
    ranked = sorted(canon)
    for c in ranked:
        if c.startswith(value):
            return c
    for c in ranked:
        if value in c:
            return c
    return None


def normalize_language(value: str | None) -> str | None:
    """Map any language code/variant to the Steam name; 'all' is passed through."""
    if value is None:
        return value
    v = value.strip().lower().replace("_", "-")
    if v == "all" or v in _STEAM_LANGUAGES:
        return v
    if v in _LANG_ALIASES:
        return _LANG_ALIASES[v]
    base = v.split("-")[0]                       # en-us -> en, zh-foo -> zh
    if base in _LANG_ALIASES:
        return _LANG_ALIASES[base]
    if base in _STEAM_LANGUAGES:
        return base
    hint = _suggest(v, _STEAM_LANGUAGES)
    raise SteamError(
        f"unrecognized language {value!r}"
        + (f'; did you mean "{hint}"?' if hint else
           "; use a Steam name (english, russian, schinese) or ISO code (en, ru, zh)"),
        code="invalid")


def normalize_country(value: str | None) -> str | None:
    """Map any country code/variant to ISO-3166 alpha-2; reject the unknown."""
    if value is None:
        return value
    v = value.strip().lower()
    if v in _VALID_CC:
        return v
    if v in _CC_ALIASES:
        return _CC_ALIASES[v]
    raise SteamError(
        f"unrecognized country {value!r}; use an ISO-3166 alpha-2 code "
        "like us, de, gb, br",
        code="invalid")


# ----- on-disk cache -------------------------------------------------------
# A single cache layer keyed on the full URL sits in front of http_get, so it
# serves both JSON responses and downloaded image bytes. TTL is chosen per
# request by the caller: 0 = bypass (live data like player counts), a finite
# number of seconds for normal JSON, and math.inf for version-keyed image URLs
# that never change (Steam puts a ?t=<hash> cache-buster on them). The cache is
# best-effort: a read/write failure never breaks the actual request.

DEFAULT_TTL = 6 * 3600  # default freshness for JSON responses (6 hours)


def _default_cache_dir() -> str:
    env = os.environ.get("STEAM_CLI_CACHE_DIR")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(base, "steam-cli", "cache")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/steam-cli")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "steam-cli")


class _CacheConfig:
    """Runtime cache settings; mutated once in main() from CLI flags / env."""

    def __init__(self) -> None:
        self.dir = _default_cache_dir()
        self.enabled = os.environ.get("STEAM_CLI_NO_CACHE", "").lower() \
            not in ("1", "true", "yes")
        self.ttl_override: float | None = None   # --cache-ttl
        self.force_refresh = False               # --refresh: skip read, still write


_CACHE = _CacheConfig()


def _cache_paths(url: str) -> tuple[str, str]:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(_CACHE.dir, h + ".body"), os.path.join(_CACHE.dir, h + ".meta")


def _atomic_write(path: str, data: bytes) -> None:
    # tmp lives in the SAME directory as the target so os.replace stays atomic
    # on Windows (cross-volume replace raises). os.replace is atomic on POSIX.
    # The pid+thread suffix keeps two concurrent writers of the same URL from
    # truncating each other's in-progress tmp file — including two THREADS of one
    # process, now that overview/price fan requests out over a thread pool.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _cache_get(url: str, ttl: float) -> bytes | None:
    if not _CACHE.enabled or ttl == 0 or _CACHE.force_refresh:
        return None
    eff = ttl
    if _CACHE.ttl_override is not None and ttl != math.inf:
        eff = _CACHE.ttl_override
    if eff == 0:
        return None  # --cache-ttl 0 means always-fresh (age > 0 would misfire)
    body_path, meta_path = _cache_paths(url)
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        if eff != math.inf and (time.time() - float(meta["fetched_at"])) > eff:
            return None
        with open(body_path, "rb") as fh:
            return fh.read()
    except (OSError, ValueError, KeyError):
        return None  # missing/corrupt entry → treat as miss


def _cache_put(url: str, body: bytes, ttl: float) -> None:
    if not _CACHE.enabled or ttl == 0:
        return
    body_path, meta_path = _cache_paths(url)
    try:
        os.makedirs(_CACHE.dir, exist_ok=True)
        _atomic_write(body_path, body)
        # meta written last: a reader requires it, so a half-written body alone
        # reads as a miss rather than as valid data.
        _atomic_write(meta_path, json.dumps(
            {"url": url, "fetched_at": time.time()}).encode("utf-8"))
    except OSError:
        pass  # caching is best-effort; never fail the request over it


_CACHE_SUFFIXES = (".body", ".meta", ".tmp")


def _cache_clear() -> int:
    """Delete only this tool's cache files; return how many were removed.

    Suffix-filtered so that pointing STEAM_CLI_CACHE_DIR at a shared directory
    and running --clear can't wipe unrelated files.
    """
    removed = 0
    try:
        entries = os.listdir(_CACHE.dir)
    except OSError:
        return 0
    for name in entries:
        if not name.endswith(_CACHE_SUFFIXES):
            continue
        try:
            os.remove(os.path.join(_CACHE.dir, name))
            removed += 1
        except OSError:
            pass
    return removed


# ----- HTTP layer ----------------------------------------------------------
# Try stdlib urllib first; if the TLS handshake fails because the machine
# intercepts HTTPS (custom root CA not in Python's trust store), fall back to
# the system `curl`, which uses the OS trust store. Force a mode with
# STEAM_CLI_HTTP=urllib|curl.

_HTTP_MODE = os.environ.get("STEAM_CLI_HTTP", "auto").lower()
_curl_fallback = False

# Transient failures (rate-limit, server hiccups, flaky network) are retried
# with exponential backoff so an agent doesn't fail a whole task on a blip.
_MAX_RETRIES = 3


def _get_urllib(url: str, timeout: float, cookie: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_curl(url: str, timeout: float, insecure: bool,
              cookie: str | None = None) -> bytes:
    curl = shutil.which("curl")
    if not curl:
        raise SteamError(
            "TLS verification failed via Python and no `curl` is available "
            "to fall back to. Set STEAM_CLI_HTTP=urllib and fix the cert "
            "store, or install curl."
        )
    # int(timeout) floors a sub-second timeout to 0, which curl reads as "no
    # limit" — the opposite of intent. ceil to whole seconds, never below 1.
    max_time = str(max(1, math.ceil(timeout)))
    cmd = [curl, "-fsSL", "--max-time", max_time, "-A", USER_AGENT]
    if insecure:
        cmd.append("-k")
    if cookie:
        cmd += ["-b", cookie]
    cmd.append(url)
    # Force curl's own messages to the C locale so its exit-22 stderr carries the
    # stable English "returned error: NNN" phrasing that _curl_http_status keys
    # on — a localized curl would otherwise translate it and hide the status.
    proc = subprocess.run(cmd, capture_output=True,
                          env={**os.environ, "LC_ALL": "C"})
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        if proc.returncode == 22:        # curl -f: server returned HTTP >= 400
            status = _curl_http_status(err)
            raise SteamError(
                f"HTTP {status} for {url}" if status else (err or "curl HTTP error"),
                code="http", status=status)
        raise SteamError(f"HTTP request failed: {err or f'curl exit {proc.returncode}'}",
                         code="network")
    return proc.stdout


def _curl_http_status(stderr: str) -> int | None:
    """Pull the HTTP status out of curl's exit-22 stderr.

    curl -f (under the forced C locale) writes "curl: (22) The requested URL
    returned error: 404" — key strictly on that phrasing. A blind fall-back to
    any standalone 3-digit number is deliberately avoided: it would misread an
    appid/port echoed elsewhere in the message as the status and mis-classify
    retriability. Unknown → None (status unknown, not a fabricated number)."""
    m = re.search(r"returned error:\s*(\d{3})", stderr)
    return int(m.group(1)) if m else None


def _is_tls_error(err: urllib.error.URLError) -> bool:
    reason = getattr(err, "reason", None)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _retriable_status(code: int) -> bool:
    return code == 429 or 500 <= code < 600


def http_get(url: str, timeout: float = 30.0, insecure: bool = False,
             *, cache_ttl: float = 0, cookie: str | None = None) -> bytes:
    cached = _cache_get(url, cache_ttl)
    if cached is not None:
        return cached
    raw = _http_get_network(url, timeout, insecure, cookie=cookie)
    _cache_put(url, raw, cache_ttl)
    return raw


def _http_get_network(url: str, timeout: float, insecure: bool,
                      cookie: str | None = None) -> bytes:
    global _curl_fallback
    # Pass `cookie` only when set, so the default path keeps calling
    # _get_urllib/_get_curl with their original argument lists (tests stub
    # those with 2-/3-arg lambdas).
    def urllib_get():
        return _get_urllib(url, timeout, cookie=cookie) if cookie \
            else _get_urllib(url, timeout)

    def curl_get(insec):
        return _get_curl(url, timeout, insec, cookie=cookie) if cookie \
            else _get_curl(url, timeout, insec)

    # Forced curl / sticky fallback / insecure: curl owns the request and gets
    # the full retry budget.
    if _HTTP_MODE == "curl" or _curl_fallback or insecure:
        return _retry_get(lambda: curl_get(insecure), url)

    # urllib first, with a one-time TLS fallback to curl. The fallback hands a
    # FRESH retry budget to curl (not the leftover urllib attempts), so a curl
    # blip after the switch still gets the full 429/5xx/network retry schedule.
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return urllib_get()
        except urllib.error.HTTPError as e:
            if _retriable_status(e.code) and attempt < _MAX_RETRIES - 1:
                last = e
                time.sleep(2 ** attempt)
                continue
            raise SteamError(f"HTTP {e.code} for {url}", code="http") from e
        except urllib.error.URLError as e:
            if _HTTP_MODE == "auto" and _is_tls_error(e) and shutil.which("curl"):
                _curl_fallback = True  # remember for the rest of the run
                return _retry_get(lambda: curl_get(False), url)
            if _is_tls_error(e):
                # TLS interception with no curl to fall back to — retrying
                # the same handshake won't help.
                raise SteamError(f"Network error: {e}", code="network") from e
            if attempt < _MAX_RETRIES - 1:
                last = e
                time.sleep(2 ** attempt)
                continue
            raise SteamError(f"Network error: {e}", code="network") from e
    raise SteamError(f"Network error: {last}", code="network")  # pragma: no cover


def _retry_get(getter, url: str) -> bytes:
    """Run `getter` under the shared retry/backoff policy.

    Transient HTTP (429/5xx) and network blips back off (2**attempt) and retry;
    a hard failure raises immediately. The curl path raises a SteamError (code
    http/network, with `.status`); the urllib path raises urllib.error.* — both
    are normalised here so either backend retries identically."""
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return getter()
        except urllib.error.HTTPError as e:
            if _retriable_status(e.code) and attempt < _MAX_RETRIES - 1:
                last = e
                time.sleep(2 ** attempt)
                continue
            raise SteamError(f"HTTP {e.code} for {url}", code="http") from e
        except urllib.error.URLError as e:
            if attempt < _MAX_RETRIES - 1:
                last = e
                time.sleep(2 ** attempt)
                continue
            raise SteamError(f"Network error: {e}", code="network") from e
        except SteamError as e:
            # retry a transient HTTP (429/5xx) or any network error; a hard HTTP
            # (4xx) or a config error (no curl, code="error") raises straight out.
            retriable = (e.code == "network"
                         or (e.code == "http" and _retriable_status(e.status or 0)))
            if retriable and attempt < _MAX_RETRIES - 1:
                last = e
                time.sleep(2 ** attempt)
                continue
            raise
    raise SteamError(f"Network error: {last}", code="network")  # pragma: no cover


def http_json(url: str, params: dict | None = None, *, timeout: float = 30.0,
              insecure: bool = False, cache_ttl: float = DEFAULT_TTL) -> dict:
    if params:
        # drop None values, then urlencode (handles cursor=* etc.)
        clean = {k: v for k, v in params.items() if v is not None}
        url = url + "?" + urllib.parse.urlencode(clean)
    raw = http_get(url, timeout=timeout, insecure=insecure, cache_ttl=cache_ttl)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SteamError(f"Steam returned non-JSON ({len(raw)} bytes). {e}",
                         code="parse") from e


# ----- app-id resolution ---------------------------------------------------

def store_search(term: str, *, cc: str = "us", lang: str = "en",
                 insecure: bool = False, timeout: float = 30.0) -> list[dict]:
    data = http_json(f"{STORE}/api/storesearch/",
                     {"term": term, "cc": cc, "l": lang},
                     timeout=timeout, insecure=insecure)
    return data.get("items", []) or []


def resolve_appid(game: str, *, cc: str = "us", lang: str = "en",
                  insecure: bool = False, quiet: bool = False,
                  timeout: float = 30.0) -> int:
    """Accept a numeric appid as-is; otherwise resolve a name via storesearch."""
    g = game.strip()
    if g.isdigit():
        return int(g)
    items = store_search(g, cc=cc, lang=lang, insecure=insecure, timeout=timeout)
    if not items:
        raise SteamError(f'No Steam app found for "{game}". Try `steam-cli search "{game}"`.',
                         code="not_found")
    top = items[0]
    # storesearch normally yields a numeric "id", but a malformed/edge item
    # (bundle, missing field, non-numeric id) must surface as a machine-readable
    # parse error — not a bare KeyError/ValueError escaping into an empty stdout.
    try:
        appid = int(top["id"])
    except (KeyError, ValueError, TypeError) as e:
        raise SteamError(
            f'Steam store search returned an unusable result for "{game}" '
            f"(no numeric appid).", code="parse") from e
    if not quiet:
        print(f'→ resolved "{game}" to appid {appid} ({top.get("name", "?")})',
              file=sys.stderr)
    return appid


# ----- formatting helpers --------------------------------------------------

def _emit_json(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _grouped(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _flatten(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    # limit <= 0 means "no truncation" (the documented `--maxlength 0` = full);
    # a positive limit clips and appends an ellipsis.
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _ts(epoch) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(epoch)))
    except (TypeError, ValueError):
        return "?"


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """Steam serves pc_requirements as small HTML blobs; flatten to plain text.

    Strip tags first, then decode entities (&amp;, &reg;, …) so escaped
    angle brackets in the text can't reintroduce a tag.
    """
    return html.unescape(" ".join(_TAG_RE.sub(" ", s or "").split()))


def _parse_date(val: str) -> int:
    """YYYY-MM-DD -> UTC epoch seconds (for --since filtering)."""
    try:
        return calendar.timegm(time.strptime(val, "%Y-%m-%d"))
    except (TypeError, ValueError):
        raise SteamError(f"invalid date {val!r}; expected YYYY-MM-DD.", code="parse")


def _write_out(path: str | None, render) -> None:
    """render() prints to stdout; redirect to a file if path is given."""
    if not path:
        render()
        return
    with open(path, "w", encoding="utf-8") as fh:
        old = sys.stdout
        try:
            sys.stdout = fh
            render()
        finally:
            sys.stdout = old
    print(f"written to {path}", file=sys.stderr)


# ----- subcommand: reviews -------------------------------------------------

def _review_summary(appid: int, *, language: str = "all", review_type: str = "all",
                    purchase_type: str = "all", offtopic: bool = False,
                    insecure: bool = False, timeout: float = 30.0) -> dict:
    """One request, num_per_page=0 — just the aggregate review counts."""
    data = http_json(f"{STORE}/appreviews/{appid}", {
        "json": 1, "num_per_page": 0, "language": language,
        "review_type": review_type, "purchase_type": purchase_type,
        "filter": "all", "filter_offtopic_activity": 0 if offtopic else 1,
    }, timeout=timeout, insecure=insecure)
    if data.get("success") != 1:
        raise SteamError(f"Steam review query failed (success={data.get('success')}).",
                         code="not_found")
    return data.get("query_summary", {})


_REVIEW_CSV_COLUMNS = [
    "recommendationid", "sentiment", "date",
    "playtime_at_review_hours", "playtime_forever_hours",
    "playtime_last_two_weeks_hours",
    "votes_up", "votes_funny", "weighted_vote_score", "comment_count",
    "steam_purchase", "received_for_free", "refunded",
    "early_access", "steam_deck", "language",
    "author_steamid", "author_personaname", "author_num_games_owned",
    "author_num_reviews", "review",
]

def _csv_cell(v):
    """Neutralize spreadsheet formula injection from user-controlled text:
    a review can start with =/+/-/@ and would execute on open in Excel/Sheets."""
    if isinstance(v, str) and v and v[0] in "=+-@|\t\r":
        return "'" + v
    return v


def _review_to_row(r: dict) -> dict:
    a = r.get("author", {}) or {}
    voted = r.get("voted_up")
    return {
        "recommendationid": r.get("recommendationid"),
        "sentiment": "positive" if voted is True else ("negative" if voted is False else ""),
        "date": _ts(r.get("timestamp_created")),
        "playtime_at_review_hours": round((a.get("playtime_at_review") or 0) / 60, 1),
        "playtime_forever_hours": round((a.get("playtime_forever") or 0) / 60, 1),
        "playtime_last_two_weeks_hours":
            round((a.get("playtime_last_two_weeks") or 0) / 60, 1),
        "votes_up": r.get("votes_up"),
        "votes_funny": r.get("votes_funny"),
        "weighted_vote_score": r.get("weighted_vote_score"),
        "comment_count": r.get("comment_count"),
        # 1/0, not Python "True"/"False" — spreadsheets treat those as text
        "steam_purchase": 1 if r.get("steam_purchase") else 0,
        "received_for_free": 1 if r.get("received_for_free") else 0,
        "refunded": 1 if r.get("refunded") else 0,
        "early_access": 1 if r.get("written_during_early_access") else 0,
        "steam_deck": 1 if r.get("primarily_steam_deck") else 0,
        "language": r.get("language"),
        "author_steamid": a.get("steamid"),
        "author_personaname": a.get("personaname"),
        "author_num_games_owned": a.get("num_games_owned"),
        "author_num_reviews": a.get("num_reviews"),
        # flatten whitespace so each review is one tidy single-line cell
        "review": " ".join((r.get("review") or "").split()),
    }


def _csv_row(row: dict, *, excel: bool) -> dict:
    out = {}
    for k, v in row.items():
        # The 17-digit steamid is the one field Excel corrupts — it parses the
        # number as a float and rounds past 15 significant digits. ="12345…"
        # forces text in the Excel-targeted (BOM) file; the stdout path
        # (excel=False) stays a clean bare number for pipes/pandas.
        if excel and k == "author_steamid" and v is not None:
            out[k] = f'="{v}"'
        else:
            out[k] = _csv_cell(v)
    return out


def _dump_reviews_csv(fh, reviews: list[dict], lineterminator: str,
                      *, excel: bool = False) -> None:
    w = csv.DictWriter(fh, fieldnames=_REVIEW_CSV_COLUMNS,
                       lineterminator=lineterminator)
    w.writeheader()
    for r in reviews:
        w.writerow(_csv_row(_review_to_row(r), excel=excel))


def _write_reviews_csv(reviews: list[dict], output_path: str | None) -> None:
    if output_path:
        # utf-8-sig writes a BOM so Excel detects UTF-8; newline="" + the
        # explicit \r\n terminator give clean Excel rows without CRLF doubling.
        with open(output_path, "w", encoding="utf-8-sig", newline="") as fh:
            _dump_reviews_csv(fh, reviews, "\r\n", excel=True)
        print(f"written to {output_path}", file=sys.stderr)
    else:
        # stdout: no BOM (breaks pipes); plain \n avoids \r\r\n on Windows text
        # streams while staying pipe/grep-friendly.
        _dump_reviews_csv(sys.stdout, reviews, "\n")


def cmd_reviews(args) -> int:
    if sum((args.json, args.jsonl, args.csv)) > 1:
        raise SteamError("pick one output format: --json, --jsonl, or --csv",
                         code="invalid")
    if args.csv and args.summary:
        raise SteamError("--csv needs the review list; drop --summary", code="invalid")
    args.cc = normalize_country(args.cc)
    args.language = normalize_language(args.language)
    appid = resolve_appid(args.game, cc=args.cc, lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet,
                          timeout=args.timeout)

    # --summary: one request, num_per_page=0, just the aggregate counts.
    if args.summary:
        summary = _review_summary(appid, language=args.language,
                                  review_type=args.review_type,
                                  purchase_type=args.purchase_type,
                                  offtopic=args.offtopic,
                                  insecure=args.insecure, timeout=args.timeout)
        if args.json:
            _write_out(args.output,
                       lambda: _emit_json({"appid": appid, "query_summary": summary}))
            return 0
        if args.jsonl:
            # honour the machine-parse flag: one JSON object on one line, rather
            # than silently dropping to human text for `--summary --jsonl`.
            _write_out(args.output, lambda: print(json.dumps(
                {"appid": appid, "query_summary": summary}, ensure_ascii=False)))
            return 0
        _print_review_summary(appid, summary, args.language)
        return 0

    per_page = min(100, args.num) if args.num else 100
    target = args.num if not args.all else float("inf")
    collected: list[dict] = []
    summary: dict = {}
    cursor = "*"
    seen = set()

    while len(collected) < target:
        data = http_json(f"{STORE}/appreviews/{appid}", {
            "json": 1, "num_per_page": per_page, "cursor": cursor,
            "language": args.language, "review_type": args.review_type,
            "purchase_type": args.purchase_type, "filter": args.filter,
            "day_range": args.day_range,
            "filter_offtopic_activity": 0 if args.offtopic else 1,
        }, timeout=args.timeout, insecure=args.insecure, cache_ttl=0)
        if data.get("success") != 1:
            raise SteamError(f"Steam review query failed (success={data.get('success')}).",
                             code="not_found")
        if not summary:
            summary = data.get("query_summary", {})
        batch = data.get("reviews", []) or []
        if not batch:
            break
        collected.extend(batch)
        nxt = data.get("cursor")
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
        if len(collected) < target and args.delay > 0:
            time.sleep(args.delay)

    if not args.all:
        collected = collected[: args.num]

    # `-n 0` (or any run where no page was fetched) skips the loop, so the
    # aggregate never loads — a bare `{"query_summary": {}}` is indistinguishable
    # from "this game has zero reviews". Backfill it with one num_per_page=0 call
    # so the count is always real, not silently empty.
    if not summary:
        summary = _review_summary(appid, language=args.language,
                                  review_type=args.review_type,
                                  purchase_type=args.purchase_type,
                                  offtopic=args.offtopic,
                                  insecure=args.insecure, timeout=args.timeout)

    if args.min_playtime is not None or args.since:
        before = len(collected)
        collected = _filter_reviews(collected, min_playtime=args.min_playtime,
                                    since=args.since)
        if len(collected) < before and not (args.json or args.quiet):
            print(f"→ filters kept {len(collected)} of {before} fetched reviews "
                  f"(applied to the fetched window; raise -n or use --all for more)",
                  file=sys.stderr)

    if args.csv:
        _write_reviews_csv(collected, args.output)
        return 0
    if args.json:
        _write_out(args.output, lambda: _emit_json({
            "appid": appid, "query_summary": summary,
            "count": len(collected), "reviews": collected}))
        return 0
    if args.jsonl:
        _write_out(args.output, lambda: [print(json.dumps(r, ensure_ascii=False))
                                         for r in collected])
        return 0
    _write_out(args.output, lambda: _print_reviews(appid, summary, collected, args))
    return 0


def _filter_reviews(reviews: list[dict], *, min_playtime: float | None = None,
                    since: str | None = None) -> list[dict]:
    """Client-side filtering on the fetched window — no extra requests."""
    since_ts = _parse_date(since) if since else None
    out = []
    for r in reviews:
        if since_ts is not None and int(r.get("timestamp_created", 0) or 0) < since_ts:
            continue
        if min_playtime is not None:
            author = r.get("author", {}) or {}
            # present key wins even when it's 0 (0h at review != lifetime hours),
            # matching how _print_reviews reads playtime.
            mins = author.get("playtime_at_review",
                              author.get("playtime_forever", 0)) or 0
            if mins / 60 < min_playtime:
                continue
        out.append(r)
    return out


def _print_review_summary(appid: int, s: dict, language: str) -> None:
    tot = s.get("total_reviews", 0)
    pos = s.get("total_positive", 0)
    neg = s.get("total_negative", 0)
    pct = f"{round(100 * pos / tot)}%" if tot else "n/a"
    desc = s.get("review_score_desc", "?")
    score = s.get("review_score")
    # review_score is Steam's own 0-9 sentiment tier; surfacing it lets a human
    # rank games without eyeballing the description (--json already carries it).
    score_txt = f" [score {score}/9]" if score is not None else ""
    print(f"appid {appid} — {desc} ({pct} positive){score_txt}")
    print(f"{_grouped(tot)} reviews · {_grouped(pos)} ▲ / {_grouped(neg)} ▼"
          f"  [language={language}]")


def _print_reviews(appid: int, s: dict, reviews: list[dict], args) -> None:
    _print_review_summary(appid, s, args.language)
    print(f"[showing {len(reviews)} · filter={args.filter}]\n")
    for r in reviews:
        mark = "▲" if r.get("voted_up") else "▼"
        # author may be null and either playtime field may be null — mirror
        # _review_to_row / _filter_reviews: coalesce to {} and `or 0` so a
        # degenerate review renders instead of crashing with a bare traceback.
        author = r.get("author", {}) or {}
        mins = author.get("playtime_at_review", author.get("playtime_forever", 0)) or 0
        hours = round(mins / 60)
        funny = r.get("votes_funny", 0)
        extra = f" · {funny} funny" if funny else ""
        head = f"{mark} {_ts(r.get('timestamp_created'))} · {hours}h{extra}"
        if r.get("language") and args.language == "all":
            head += f" · {r['language']}"
        print(head)
        print(f"   {_flatten(r.get('review', ''))}\n")


# ----- subcommand: info / price -------------------------------------------

def _appdetails(appid: int, cc: str, lang: str, *, filters: str | None = None,
                insecure: bool = False, timeout: float = 30.0) -> dict:
    params = {"appids": appid, "cc": cc, "l": lang}
    if filters:
        params["filters"] = filters
    data = http_json(f"{STORE}/api/appdetails", params,
                     timeout=timeout, insecure=insecure)
    entry = data.get(str(appid), {})
    if not entry.get("success"):
        raise SteamError(f"No store details for appid {appid} (region cc={cc}).",
                         code="not_found")
    return entry.get("data", {})


def cmd_info(args) -> int:
    args.cc = normalize_country(args.cc)
    args.lang = normalize_language(args.lang)
    appid = resolve_appid(args.game, cc=args.cc, lang=args.lang,
                          insecure=args.insecure, quiet=args.json or args.quiet,
                          timeout=args.timeout)
    data = _appdetails(appid, args.cc, args.lang, insecure=args.insecure,
                       timeout=args.timeout)
    if args.json:
        _emit_json(data)
        return 0

    def names(items):
        return ", ".join(x.get("description", x.get("name", "")) for x in items) or "—"

    print(f"{data.get('name', '?')}  (appid {appid}, {data.get('type', '?')})")
    rd = data.get("release_date", {})
    rel = rd.get("date") or "?"
    if rd.get("coming_soon"):
        rel += " (coming soon)"
    print(f"Released:    {rel}")
    print(f"Developer:   {', '.join(data.get('developers', [])) or '—'}")
    print(f"Publisher:   {', '.join(data.get('publishers', [])) or '—'}")
    print(f"Genres:      {names(data.get('genres', []))}")
    cats = names(data.get("categories", []))
    print(f"Categories:  {_flatten(cats, 120)}")
    plats = [p for p, on in (data.get("platforms", {}) or {}).items() if on]
    print(f"Platforms:   {', '.join(plats) or '—'}")
    mc = data.get("metacritic", {})
    if mc:
        print(f"Metacritic:  {mc.get('score')}  {mc.get('url', '')}")
    price = data.get("price_overview")
    if data.get("is_free"):
        print("Price:       Free to Play")
    elif price:
        line = price.get("final_formatted", "?")
        if price.get("discount_percent"):
            line += f"  (-{price['discount_percent']}% from {price.get('initial_formatted')})"
        print(f"Price:       {line}  [cc={args.cc}]")
    web = data.get("website")
    if web:
        print(f"Website:     {web}")
    short = data.get("short_description")
    if short:
        print(f"\n{_flatten(short, 400)}")
    if args.dlc:
        _print_dlc(data)
    if args.screenshots:
        _print_screenshots(data)
    if args.sysreqs:
        _print_sysreqs(data)
    print(f"\nStore: {STORE}/app/{appid}/")
    return 0


def _print_dlc(data: dict) -> None:
    dlc = data.get("dlc") or []
    print(f"\nDLC ({len(dlc)}):")
    if not dlc:
        print("  —")
        return
    for appid in dlc:
        print(f"  appid {appid}  {STORE}/app/{appid}/")


def _print_screenshots(data: dict) -> None:
    shots = data.get("screenshots") or []
    print(f"\nScreenshots ({len(shots)}):")
    if not shots:
        print("  —")
        return
    for s in shots:
        print(f"  {s.get('path_full', s.get('path_thumbnail', '?'))}")


def _print_sysreqs(data: dict) -> None:
    print("\nSystem requirements:")
    found = False
    for plat in ("pc_requirements", "mac_requirements", "linux_requirements"):
        req = data.get(plat)
        if not isinstance(req, dict):
            continue
        label = plat.split("_")[0]
        for tier in ("minimum", "recommended"):
            body = _strip_html(req.get(tier, ""))
            if body:
                found = True
                print(f"  [{label} · {tier}] {_flatten(body, 300)}")
    if not found:
        print("  —")


def cmd_price(args) -> int:
    # De-dup regions preserving first-seen order: `--cc us,de,us` (a typo, or an
    # agent building the list programmatically) must not fetch the same region
    # twice — and, once fetches run on a thread pool, two threads writing the
    # same cache URL would race the tmp file.
    regions: list[str] = []
    seen_cc: set[str] = set()
    for c in args.cc.split(","):
        if not c.strip():
            continue
        cc = normalize_country(c)
        if cc not in seen_cc:
            seen_cc.add(cc)
            regions.append(cc)
    if not regions:
        regions = ["us"]
    appid = resolve_appid(args.game, cc=regions[0], lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet,
                          timeout=args.timeout)

    def fetch_region(cc: str) -> dict:
        try:
            data = _appdetails(appid, cc, "en", filters="price_overview,basic",
                               insecure=args.insecure, timeout=args.timeout)
        except SteamError as e:
            # one unavailable region must not sink the whole comparison
            return {"cc": cc, "error": str(e)}
        return {
            "cc": cc,
            "name": data.get("name", f"appid {appid}"),
            "is_free": bool(data.get("is_free")),
            "price_overview": data.get("price_overview"),
        }

    # regions are independent appdetails calls — fetch them concurrently (via the
    # shared soft-fetch primitive) so a multi-region comparison costs one
    # round-trip of latency, not one per cc. The helper preserves job order, so
    # output stays deterministic; regions were de-duped above so no two jobs race
    # the same cache URL. fetch_region handles its own errors (returns a {cc,error}
    # row), so the None-on-SteamError degrade path is simply never taken here.
    jobs = {cc: (lambda c=cc: fetch_region(c)) for cc in regions}
    results = list(_fetch_soft_concurrent(jobs).values())
    if args.json:
        _emit_json({"appid": appid, "regions": results})
        return 0
    _print_price(appid, results)
    return 0


def _print_price(appid: int, results: list[dict]) -> None:
    name = next((r["name"] for r in results if "name" in r), f"appid {appid}")
    for r in results:
        cc = r["cc"]
        if "error" in r:
            print(f"{name} [{cc}]: error — {r['error']}")
            continue
        if r["is_free"]:
            print(f"{name} [{cc}]: Free to Play")
            continue
        price = r["price_overview"]
        if not price:
            print(f"{name} [{cc}]: no price for this region (not sold there?)")
            continue
        line = price.get("final_formatted", "?")
        if price.get("discount_percent"):
            line += (f"  (-{price['discount_percent']}% off, "
                     f"was {price.get('initial_formatted')})")
        print(f"{name} [{cc}]: {line}")


# ----- subcommand: search --------------------------------------------------

def cmd_search(args) -> int:
    args.cc = normalize_country(args.cc)
    # normalization sends the Steam name (e.g. "english"), aligning search's
    # `l` param with info's; storesearch accepts both forms.
    args.lang = normalize_language(args.lang)
    items = store_search(args.term, cc=args.cc, lang=args.lang,
                         insecure=args.insecure, timeout=args.timeout)
    # match every sibling command: positive limit caps, 0 = all, negative ignored
    # (a bare items[:args.limit] would silently slice from the end on --limit -1).
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    if args.json:
        _emit_json(items)
        return 0
    if not items:
        print(f'No results for "{args.term}".')
        return 0
    for it in items:
        price = it.get("price")
        if it.get("type") and "price" in it and isinstance(price, dict):
            cents = price.get("final")
            ptxt = f"{cents / 100:.2f} {price.get('currency', '')}" if cents else "free/—"
        else:
            ptxt = "—"
        print(f"{str(it.get('id') or '?'):>8}  {it.get('name', '?')}  "
              f"[{it.get('type', '?')}]  {ptxt}")
    return 0


# ----- subcommand: players -------------------------------------------------

def _current_players(appid: int, *, insecure: bool = False,
                     timeout: float = 30.0) -> int:
    # live data — never cache it
    data = http_json(f"{API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                     {"appid": appid}, timeout=timeout, insecure=insecure, cache_ttl=0)
    resp = data.get("response", {})
    if resp.get("result") != 1:
        raise SteamError(f"No player-count data for appid {appid}.", code="not_found")
    return resp.get("player_count", 0)


def cmd_players(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure,
                          quiet=args.json or args.quiet, timeout=args.timeout)
    count = _current_players(appid, insecure=args.insecure, timeout=args.timeout)
    if args.json:
        _emit_json({"appid": appid, "player_count": count})
        return 0
    print(f"appid {appid}: {_grouped(count)} players online now")
    return 0


# ----- subcommand: news ----------------------------------------------------

def _news_items(appid: int, *, count: int, maxlength: int = 300,
                insecure: bool = False, timeout: float = 30.0) -> list[dict]:
    data = http_json(f"{API}/ISteamNews/GetNewsForApp/v2/", {
        "appid": appid, "count": count, "maxlength": maxlength,
    }, timeout=timeout, insecure=insecure)
    return data.get("appnews", {}).get("newsitems", []) or []


def cmd_news(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure,
                          quiet=args.json or args.quiet, timeout=args.timeout)
    items = _news_items(appid, count=args.count, maxlength=args.maxlength,
                        insecure=args.insecure, timeout=args.timeout)
    if args.json:
        _emit_json(items)
        return 0
    if not items:
        print(f"No news for appid {appid}.")
        return 0
    for it in items:
        print(f"{_ts(it.get('date'))} · {it.get('feedlabel', '')}")
        print(f"  {it.get('title', '')}")
        print(f"  {it.get('url', '')}")
        body = _flatten(it.get("contents", ""), args.maxlength)
        if body:
            print(f"  {body}")
        print()
    return 0


# ----- subcommand: achievements --------------------------------------------

def _achievement_percentages(appid: int, *, insecure: bool = False,
                             timeout: float = 30.0) -> list[dict]:
    data = http_json(f"{API}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
                     {"gameid": appid}, timeout=timeout, insecure=insecure)
    achs = data.get("achievementpercentages", {}).get("achievements", []) or []
    for a in achs:  # Steam sometimes serializes percent as a string
        try:
            a["percent"] = float(a.get("percent", 0))
        except (TypeError, ValueError):
            a["percent"] = 0.0
    achs.sort(key=lambda a: a["percent"], reverse=True)
    return achs


def cmd_achievements(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure,
                          quiet=args.json or args.quiet, timeout=args.timeout)
    achs = _achievement_percentages(appid, insecure=args.insecure, timeout=args.timeout)
    if args.limit and args.limit > 0:
        shown = achs[: args.limit]
    else:
        shown = achs
    if args.json:
        _emit_json(shown)
        return 0
    if not achs:
        print(f"No global achievement data for appid {appid} "
              "(game may have no achievements).")
        return 0
    print(f"appid {appid}: {len(achs)} achievements "
          f"(showing {len(shown)}, rarest last)\n")
    for a in shown:
        print(f"  {a.get('percent', 0):6.2f}%  {a.get('name', '?')}")
    if len(shown) < len(achs):
        print(f"\n… {len(achs) - len(shown)} more (use --limit 0 for all). "
              "Names are internal API ids; display names need a Web API key.")
    return 0


# ----- subcommand: overview ------------------------------------------------

# Cap on concurrent Steam sockets from one command: a caller that builds a huge
# job set (e.g. `price --cc` with dozens of regions) must not open them all at
# once and self-inflict a 429 on a key-free API — the same "stay a good citizen"
# guard `browse --count` applies to its own fan-out. It bounds concurrency, not
# the total number of results the caller asked for.
_MAX_CONCURRENT_FETCHES = 8


def _fetch_soft_concurrent(jobs: dict) -> dict:
    """Run independent fetches on a thread pool, each degrading to None on
    SteamError — same contract as a sequential soft() call, but the network
    round-trips overlap. Steam calls are I/O-bound (urllib/curl release the GIL
    while waiting), so N independent requests cost ~one round-trip of wall-clock
    instead of N. Results come back keyed by the caller's job keys, in the SAME
    order the jobs were given (handy when the caller wants an ordered list).

    Thread-safety note: every job MUST hit a DISTINCT cache URL. The on-disk
    cache tolerates concurrent writers of *different* URLs (per-URL paths, a
    pid+thread tmp suffix), but two jobs writing the SAME URL would race — so
    callers de-dup first (see cmd_price)."""
    if not jobs:
        return {}

    def soft(fn):
        try:
            return fn()
        except SteamError:
            return None

    out = {key: None for key in jobs}          # seed in caller's order
    workers = min(len(jobs), _MAX_CONCURRENT_FETCHES)
    if workers <= 1:
        for key, fn in jobs.items():
            out[key] = soft(fn)
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(soft, fn): key for key, fn in jobs.items()}
        for fut in concurrent.futures.as_completed(futures):
            out[futures[fut]] = fut.result()
    return out


def cmd_overview(args) -> int:
    """One resolve, one JSON: store card + live players + review headline +
    price — so an agent answering "tell me about <game>" needs a single call
    instead of three or four."""
    args.cc = normalize_country(args.cc)
    appid = resolve_appid(args.game, cc=args.cc, lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet,
                          timeout=args.timeout)
    # appdetails is the hard request: without it there is no overview at all.
    data = _appdetails(appid, args.cc, "english", insecure=args.insecure,
                       timeout=args.timeout)

    # players / reviews (and opt-in news / achievements) only need the resolved
    # appid, not each other — fetch them concurrently so the flagship command
    # pays one round-trip of latency, not three-to-five sequential ones.
    jobs = {
        "players": lambda: _current_players(
            appid, insecure=args.insecure, timeout=args.timeout),
        "review_summary": lambda: _review_summary(
            appid, insecure=args.insecure, timeout=args.timeout),
    }
    if args.news:
        jobs["news"] = lambda: _news_items(
            appid, count=args.news, insecure=args.insecure, timeout=args.timeout)
    if args.top_achievements:
        jobs["top_achievements"] = lambda: _achievement_percentages(
            appid, insecure=args.insecure, timeout=args.timeout
        )[: args.top_achievements]
    fetched = _fetch_soft_concurrent(jobs)
    players = fetched["players"]
    review_summary = fetched["review_summary"]
    is_free = bool(data.get("is_free"))
    rd = data.get("release_date", {}) or {}
    overview = {
        "appid": appid,
        "name": data.get("name"),
        "type": data.get("type"),
        "release_date": rd.get("date"),
        "coming_soon": bool(rd.get("coming_soon")),
        "developers": data.get("developers", []),
        "publishers": data.get("publishers", []),
        "genres": [g.get("description") for g in data.get("genres", [])
                   if g.get("description")],
        "metacritic": (data.get("metacritic") or {}).get("score"),
        "is_free": is_free,
        "price_cc": args.cc,
        "price": None if is_free else data.get("price_overview"),
        "players_online": players,
        "review_summary": review_summary,
        "store_url": f"{STORE}/app/{appid}/",
    }
    # opt-in soft sections: included only when requested, null on failure.
    # Already fetched concurrently above — just surface them under their keys.
    if args.news:
        overview["news"] = fetched.get("news")
    if args.top_achievements:
        overview["top_achievements"] = fetched.get("top_achievements")
    if args.estimate:
        rs = overview.get("review_summary") or {}
        p = overview.get("price") or {}
        currency = p.get("currency")
        # appdetails returns price in the --cc region's currency; a non-USD
        # figure must NOT be fed to a USD revenue proxy (e.g. cc=ru gives RUB
        # kopecks). Only pass the price through when it's actually USD; otherwise
        # revenue stays n/a and we say why. full (pre-discount) price is the
        # right baseline for the proxy.
        price_cents = None
        if not is_free and currency == "USD":
            price_cents = p.get("initial") or p.get("final")
        est = _boxleiter_estimate(rs.get("total_reviews"), price_cents=price_cents,
                                  multiplier=args.multiplier)
        if est and price_cents is None and not is_free and currency and currency != "USD":
            est["revenue_note"] = (
                f"revenue omitted: price is in {currency}, not USD "
                f"(re-run with `--cc us` for a revenue estimate)")
        overview["sales_estimate"] = est
    if args.json:
        _emit_json(overview)
        return 0
    _print_overview(overview)
    return 0


def _print_overview(o: dict) -> None:
    print(f"{o.get('name') or '?'}  (appid {o['appid']}, {o.get('type') or '?'})")
    rel = o.get("release_date") or "?"
    if o.get("coming_soon"):
        rel += " (coming soon)"
    print(f"Released:    {rel}")
    print(f"Developer:   {', '.join(o.get('developers') or []) or '—'}")
    print(f"Genres:      {', '.join(o.get('genres') or []) or '—'}")
    mc = o.get("metacritic")
    print(f"Metacritic:  {mc if mc is not None else 'n/a'}")
    if o.get("is_free"):
        print("Price:       Free to Play")
    elif o.get("price"):
        p = o["price"]
        line = p.get("final_formatted", "?")
        if p.get("discount_percent"):
            line += f"  (-{p['discount_percent']}%)"
        print(f"Price:       {line}  [cc={o.get('price_cc')}]")
    else:
        print("Price:       n/a")
    pc = o.get("players_online")
    print(f"Players now: {_grouped(pc) if pc is not None else 'n/a'}")
    rs = o.get("review_summary")
    if rs:
        tot = rs.get("total_reviews", 0)
        pos = rs.get("total_positive", 0)
        pct = f"{round(100 * pos / tot)}%" if tot else "n/a"
        print(f"Reviews:     {rs.get('review_score_desc', '?')} "
              f"({pct} of {_grouped(tot)})")
    else:
        print("Reviews:     n/a")
    if "news" in o:
        print("\nNews:")
        for it in (o["news"] or []):
            print(f"  {_ts(it.get('date'))} · {_flatten(it.get('title', ''), 100)}")
        if not o["news"]:
            print("  n/a")
    if "top_achievements" in o:
        print("\nTop achievements:")
        for a in (o["top_achievements"] or []):
            print(f"  {a.get('percent', 0):6.2f}%  {a.get('name', '?')}")
        if not o["top_achievements"]:
            print("  n/a")
    if "sales_estimate" in o:
        _print_estimate(o["sales_estimate"])
    print(f"\nStore: {o['store_url']}")


def _print_estimate(est: dict | None) -> None:
    print("\nSales estimate (Boxleiter — rough, external heuristic):")
    if not est:
        print("  n/a (no review count to extrapolate from)")
        return
    rev = est.get("revenue_usd")
    for label, info in est["owners"].items():
        line = f"  {label:13} ×{str(info['multiplier']):<4} ≈ {_grouped(info['owners'])} owners"
        if rev is not None:
            line += f"  ·  ≈ ${_grouped(rev[label])} gross"
        print(line)
    if rev is None:
        note = est.get("revenue_note")
        print(f"  ({note})" if note else
              "  (revenue n/a — game is free or price unknown)")
    print(f"  from {_grouped(est['total_reviews'])} reviews"
          + (f" × ${est['price_usd']:.2f}" if est.get("price_usd") else "")
          + "; owners/revenue are order-of-magnitude, not exact.")


# ----- subcommand: images --------------------------------------------------

def _collect_image_urls(data: dict, what: list[str], limit: int) -> list[tuple]:
    """Build a (kind, index, url) list for the requested image kinds.

    URLs come straight from appdetails (official Steam CDN). Single-art kinds
    yield index 0; screenshots/movie-thumbs are capped at `limit`.
    """
    out: list[tuple] = []
    if "header" in what and data.get("header_image"):
        out.append(("header", 0, data["header_image"]))
    if "capsule" in what and data.get("capsule_image"):
        out.append(("capsule", 0, data["capsule_image"]))
    if "background" in what and (data.get("background_raw") or data.get("background")):
        out.append(("background", 0, data.get("background_raw") or data["background"]))
    if "screenshots" in what:
        for i, s in enumerate((data.get("screenshots") or [])[:limit]):
            url = s.get("path_full") or s.get("path_thumbnail")
            if url:
                out.append(("screenshot", i, url))
    if "movies" in what:
        for i, m in enumerate((data.get("movies") or [])[:limit]):
            if m.get("thumbnail"):
                out.append(("movie_thumb", i, m["thumbnail"]))
    return out


def _image_filename(kind: str, idx: int, url: str) -> str:
    base = os.path.basename(urllib.parse.urlsplit(url).path) or f"{kind}.jpg"
    if "." not in base:               # e.g. the background URL has no extension
        base += ".jpg"
    return f"{kind}_{idx}_{base}"


def cmd_images(args) -> int:
    """Download a game's header / screenshots / art to local files so a
    multimodal agent can open them with its own image reader and *see* the
    game, not just read about it."""
    appid = resolve_appid(args.game, cc="us", lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet,
                          timeout=args.timeout)
    data = _appdetails(appid, "us", "english", insecure=args.insecure,
                       timeout=args.timeout)
    targets = _collect_image_urls(data, args.what, args.limit)
    if not targets:
        if args.json:
            _emit_json({"appid": appid, "out": None, "images": []})
        else:
            print(f"appid {appid}: no images of kinds {args.what} available.")
        return 0

    outdir = args.out or f"steam-{appid}-media"
    try:
        os.makedirs(outdir, exist_ok=True)
    except OSError as e:
        raise SteamError(f"cannot create output dir {outdir!r}: {e}")
    results: list[dict] = []
    for kind, idx, url in targets:
        try:
            # version-keyed image URLs never change → cache forever
            raw = http_get(url, timeout=args.timeout, insecure=args.insecure,
                           cache_ttl=math.inf)
            path = os.path.join(outdir, _image_filename(kind, idx, url))
            with open(path, "wb") as fh:
                fh.write(raw)
        except (SteamError, OSError) as e:
            # a broken asset OR a write failure must not sink the rest
            results.append({"kind": kind, "url": url, "error": str(e)})
            continue
        # absolute path so an agent can open it regardless of its own cwd
        results.append({"kind": kind, "url": url, "path": os.path.abspath(path)})

    if args.json:
        _emit_json({"appid": appid, "out": outdir, "images": results})
        return 0
    _print_images(appid, outdir, results)
    return 0


def _print_images(appid: int, outdir: str, results: list[dict]) -> None:
    ok = [r for r in results if "path" in r]
    print(f"appid {appid}: saved {len(ok)}/{len(results)} images to {outdir}/")
    for r in results:
        if "path" in r:
            print(f"  {r['kind']:12} {r['path']}")
        else:
            print(f"  {r['kind']:12} FAILED — {r['error']}")


# ----- subcommand: cache ---------------------------------------------------

def cmd_cache(args) -> int:
    if args.path:                    # script-friendly: just the directory
        if args.json:
            _emit_json({"dir": _CACHE.dir})
        else:
            print(_CACHE.dir)
        return 0
    if args.clear:
        n = _cache_clear()
        if args.json:
            _emit_json({"cleared": n, "dir": _CACHE.dir})
        else:
            print(f"cleared {n} cache files from {_CACHE.dir}")
        return 0
    # default: human summary of where the cache is and how big it is
    files, total, entries = 0, 0, 0
    try:
        for name in os.listdir(_CACHE.dir):
            if not name.endswith(_CACHE_SUFFIXES):
                continue
            p = os.path.join(_CACHE.dir, name)
            if not os.path.isfile(p):
                continue
            files += 1
            total += os.path.getsize(p)
            if name.endswith(".body"):     # one .body == one cached entry
                entries += 1
    except OSError:
        pass
    if args.json:
        _emit_json({"dir": _CACHE.dir, "entries": entries,
                    "files": files, "bytes": total})
        return 0
    print(f"cache dir: {_CACHE.dir}")
    print(f"entries:   {entries}  ({files} files, {_grouped(total)} bytes)")
    print("clear with `steam-cli cache --clear`")
    return 0


# ----- subcommands: specials / top-sellers ---------------------------------
# Both read the storefront's featuredcategories (key-free, region-curated front
# page) and surface one section each. Prices are integer minor units (cents).

def _featured_section(section: str, cc: str, lang: str, *, insecure: bool = False,
                      timeout: float = 30.0) -> list[dict]:
    data = http_json(f"{STORE}/api/featuredcategories", {"cc": cc, "l": lang},
                     timeout=timeout, insecure=insecure)
    items = (data.get(section) or {}).get("items", []) or []
    return [{
        "id": it.get("id"),
        "name": it.get("name"),
        "discounted": it.get("discounted"),
        "discount_percent": it.get("discount_percent"),
        "original_price": it.get("original_price"),
        "final_price": it.get("final_price"),
        "currency": it.get("currency"),
        "header_image": it.get("header_image"),
    } for it in items]


def _run_featured(args, section: str, label: str) -> int:
    cc = normalize_country(args.cc)
    lang = normalize_language(args.lang)
    items = _featured_section(section, cc, lang, insecure=args.insecure,
                              timeout=args.timeout)
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    if args.json:
        _emit_json({"section": section, "cc": cc, "count": len(items),
                    "items": items})
        return 0
    print(f"{label}  [cc={cc}]  ({len(items)})\n")
    for it in items:
        price = it.get("final_price")
        if price is None:
            ptxt = "—"                       # price unknown
        elif price == 0:
            ptxt = "free"                     # genuinely free (0 cents)
        else:
            ptxt = f"{price / 100:.2f} {it.get('currency', '')}"
        if it.get("discount_percent"):
            ptxt += f"  (-{it['discount_percent']}%)"
        print(f"{str(it.get('id') or '?'):>8}  {it.get('name', '?')}  —  {ptxt}")
    return 0


def cmd_specials(args) -> int:
    return _run_featured(args, "specials", "On sale now (featured specials)")


def cmd_top_sellers(args) -> int:
    return _run_featured(args, "top_sellers", "Top sellers")


def cmd_coming_soon(args) -> int:
    return _run_featured(args, "coming_soon", "Coming soon (pre-release)")


def cmd_new_releases(args) -> int:
    return _run_featured(args, "new_releases", "New releases")


# ----- subcommand: profile -------------------------------------------------
# Public Steam Community profiles via the key-free ?xml=1 endpoint. Public data
# only — owned-games libraries and per-user achievements still need a Web API
# key and stay out of scope. A reviewer's steamid (review.author.steamid) feeds
# straight in, so an agent can look up who wrote a review.

def _profile_url(ident: str) -> str:
    ident = ident.strip()
    if "steamcommunity.com" in ident:        # accept a pasted profile URL
        # urlparse drops any ?query / #fragment so they can't leak into the id.
        parts = [p for p in urllib.parse.urlparse(ident).path.split("/") if p]
        # The id is the segment right AFTER the /id/ or /profiles/ marker — not
        # the last path segment, which may be a sub-page (/badges, /screenshots,
        # /id/gaben/badges → "gaben", not "badges"). Fall back to the last
        # segment for a bare /<something>/ URL with no marker.
        picked = ""
        for marker in ("id", "profiles"):
            if marker in parts:
                i = parts.index(marker)
                if i + 1 < len(parts):
                    picked = parts[i + 1]
                break
        ident = picked or (parts[-1] if parts else "")
    ident = ident.strip("/")
    if not ident:
        raise SteamError("profile id is empty; pass a steamID64 or vanity name",
                         code="invalid")
    if ident.isdigit() and len(ident) == 17:
        return f"{COMMUNITY}/profiles/{ident}/?xml=1"
    return f"{COMMUNITY}/id/{urllib.parse.quote(ident)}/?xml=1"


def _parse_profile_xml(raw: bytes) -> dict:
    # Steam's profile XML never carries a DTD; reject one rather than feed it
    # to expat, which would otherwise expand internal entities (billion-laughs)
    # from a MITM or compromised response. External entities are already off.
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise SteamError("profile response contained an unexpected DTD", code="parse")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise SteamError(f"could not parse profile XML: {e}", code="parse")
    if root.tag == "response":               # bad vanity / missing profile
        raise SteamError(root.findtext("error") or "profile not found",
                         code="not_found")

    def text(tag):
        el = root.find(tag)
        return el.text if el is not None and el.text else None

    summary = text("summary")
    sid = text("steamID64")
    return {
        "steamid64": sid,
        "name": text("steamID"),
        "private": text("privacyState") != "public",
        "privacy_state": text("privacyState"),
        "online_state": text("onlineState"),
        "state_message": text("stateMessage"),
        "member_since": text("memberSince"),
        "location": text("location"),
        "real_name": text("realname"),
        "summary": _strip_html(summary) if summary else None,
        "vac_banned": text("vacBanned") == "1",
        "avatar": text("avatarFull"),
        "profile_url": f"{COMMUNITY}/profiles/{sid}" if sid else None,
    }


def cmd_profile(args) -> int:
    raw = http_get(_profile_url(args.user), timeout=args.timeout,
                   insecure=args.insecure, cache_ttl=DEFAULT_TTL)
    prof = _parse_profile_xml(raw)
    if args.json:
        _emit_json(prof)
        return 0
    _print_profile(prof)
    return 0


def _print_profile(p: dict) -> None:
    print(f"{p.get('name') or '?'}  ({p.get('steamid64')})")
    if p.get("private"):
        print(f"Profile:     private ({p.get('privacy_state')})")
    print(f"Status:      {p.get('state_message') or p.get('online_state') or '—'}")
    if p.get("member_since"):
        print(f"Member since:{p['member_since']}")
    if p.get("location"):
        print(f"Location:    {p['location']}")
    if p.get("real_name"):
        print(f"Real name:   {p['real_name']}")
    print(f"VAC banned:  {'yes' if p.get('vac_banned') else 'no'}")
    if p.get("summary"):
        print(f"\n{_flatten(p['summary'], 300)}")
    if p.get("profile_url"):
        print(f"\n{p['profile_url']}")


# ----- subcommands: market recon (tags / browse / similar / history) -------
# These read Steam's own storefront surfaces *beyond* the JSON web API: user
# tags (the app page's InitAppTagModal blob), the faceted /search/results feed
# (the only key-free way to size a niche by tag/price/date), the "more like
# this" recommendation grid, and the review-volume histogram. All first-party
# Steam — no third-party aggregators. Markup-derived data is fragile by nature:
# when a parser finds nothing where Steam says there are results, it raises a
# parse error rather than returning a silently-empty list.

_AGE_GATE_COOKIE = "birthtime=0; mature_content=1"  # bypass the store age check


def _extract_js_array(text: str, marker: str) -> str | None:
    """Return the first JS array literal following `marker`, matched with
    balanced brackets that respect (double-quoted) string literals — so a `]`
    inside a tag name can't prematurely end the array. Assumes Steam's
    JSON-style double quotes; None if not found. A genuinely malformed slice
    surfaces later as a json.loads parse error, which is the intended failure."""
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("[", i)
    if j < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for k in range(j, len(text)):
        c = text[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
    return None


def _app_tags(appid: int, *, insecure: bool = False,
              timeout: float = 30.0) -> list[dict]:
    """User tags with vote counts, parsed from the app page's InitAppTagModal."""
    raw = http_get(f"{STORE}/app/{appid}/?cc=us&l=english", timeout=timeout,
                   insecure=insecure, cache_ttl=DEFAULT_TTL,
                   cookie=_AGE_GATE_COOKIE)
    html_text = raw.decode("utf-8", "replace")
    blob = _extract_js_array(html_text, "InitAppTagModal(")
    if blob is None:
        raise SteamError(
            f"could not find user tags for appid {appid} (Steam page format "
            "changed, or the app has no store page).", code="parse")
    try:
        tags = json.loads(blob)
    except json.JSONDecodeError as e:
        raise SteamError(f"could not parse user tags for appid {appid}: {e}",
                         code="parse")
    out = [{
        "tagid": t.get("tagid"), "name": t.get("name"),
        "count": t.get("count"), "browseable": bool(t.get("browseable")),
    } for t in tags if isinstance(t, dict) and t.get("tagid") is not None]
    # Steam already serves these vote-ranked, but sort defensively so the
    # contract ("rarest concern last, top tag first") holds even if it stops.
    out.sort(key=lambda t: t["count"] or 0, reverse=True)
    return out


def cmd_tags(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure,
                          quiet=args.json or args.quiet, timeout=args.timeout)
    tags = _app_tags(appid, insecure=args.insecure, timeout=args.timeout)
    if args.limit and args.limit > 0:
        tags = tags[: args.limit]
    if args.json:
        _emit_json({"appid": appid, "count": len(tags), "tags": tags})
        return 0
    _print_tags(appid, tags)
    return 0


def _print_tags(appid: int, tags: list[dict]) -> None:
    print(f"appid {appid}: {len(tags)} user tags (by votes)\n")
    for t in tags:
        cnt = _grouped(t["count"]) if t.get("count") is not None else "—"
        print(f"  {cnt:>8}  {t.get('name', '?')}  (id {t.get('tagid')})")


# --- browse: faceted store search (the niche map) ---

_SORT_MAP = {
    "reviews": "Reviews_DESC",      # most-reviewed (proxy for most-owned)
    "released": "Released_DESC",    # newest first
    "price-asc": "Price_ASC",
    "price-desc": "Price_DESC",
    "name": "Name_ASC",
}

# Robust parsing: anchor on the /app/<id>/<slug>/ store URL — Steam's most
# stable contract (unchanged for years) — and on the data-ds-* attributes its
# dynamic-store JS depends on, NOT on presentational CSS classes like
# "search_result_row"/"title"/"similar_grid_capsule". A restyle then degrades
# gracefully (the name falls back to the URL slug) instead of breaking, and a
# row is bounded by the next anchor so one game's id can't borrow another's name.
_APP_HREF_RE = re.compile(
    r'href="https?://store\.steampowered\.com/app/(\d+)/([^/"?]*)', re.I)
_SEARCH_ANCHOR_RE = re.compile(r'<a\b[^>]*?' + _APP_HREF_RE.pattern + r'[^>]*>', re.I)
_TITLE_SPAN_RE = re.compile(r'<span class="title">([^<]+)</span>')
_TAGIDS_RE = re.compile(r'data-ds-tagids="(\[[^\]]*\])"')


def _slug_to_name(slug: str) -> str | None:
    """Turn a store-URL slug (A_Short_Hike) into a readable name (A Short Hike)."""
    return urllib.parse.unquote(slug).replace("_", " ").strip() or None


def _tagids_in(tag: str) -> list:
    m = _TAGIDS_RE.search(tag)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def _parse_search_rows(html_text: str) -> list[dict]:
    anchors = list(_SEARCH_ANCHOR_RE.finditer(html_text))
    rows = []
    for i, m in enumerate(anchors):
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(html_text)
        # prefer the visible title; fall back to the URL slug if the span moved
        title_m = _TITLE_SPAN_RE.search(html_text, m.end(), end)
        name = html.unescape(title_m.group(1).strip()) if title_m \
            else _slug_to_name(m.group(2))
        rows.append({"appid": int(m.group(1)), "name": name,
                     "tagids": _tagids_in(m.group(0))})
    return rows


def _tag_dictionary(*, insecure: bool = False, timeout: float = 30.0) -> dict:
    """name(lowercased) -> tagid, from Steam's public tag dictionary."""
    data = http_json(f"{STORE}/tagdata/populartags/english", {},
                     timeout=timeout, insecure=insecure)
    return {str(t.get("name", "")).strip().lower(): t.get("tagid")
            for t in (data or []) if t.get("name")}


def _resolve_tag_ids(names: list[str], *, insecure: bool = False,
                     timeout: float = 30.0) -> list[int]:
    """Map tag names (or raw numeric ids) to Steam tag ids; reject the unknown."""
    dictionary = None
    ids: list[int] = []
    unknown: list[str] = []
    for name in names:
        n = name.strip()
        if not n:
            continue
        if n.isdigit():
            ids.append(int(n))
            continue
        if dictionary is None:
            dictionary = _tag_dictionary(insecure=insecure, timeout=timeout)
        tid = dictionary.get(n.lower())
        if tid is None:
            unknown.append(name)
        else:
            ids.append(int(tid))
    if unknown:
        hint = _suggest(unknown[0].strip().lower(), dictionary or {})
        raise SteamError(
            f"unknown Steam tag(s): {', '.join(unknown)}"
            + (f'; did you mean "{hint}"?' if hint else
               "; use `steam-cli tags <game>` to discover real tag names"),
            code="invalid")
    return ids


def _search_results(tag_ids: list[int], *, sort: str = "reviews",
                    max_price=None, count: int = 20, cc: str = "us",
                    lang: str = "english", insecure: bool = False,
                    timeout: float = 30.0) -> tuple[list[dict], int]:
    """Faceted storefront search → (items, total_count). total_count is the
    full niche size matching the filters, independent of how many we page in."""
    per_page = min(100, max(1, count))
    items: list[dict] = []
    seen: set[int] = set()
    total = 0
    start = 0
    while len(items) < count:
        params = {
            "query": "", "start": start, "count": per_page, "dynamic_data": "",
            "sort_by": _SORT_MAP.get(sort, "Reviews_DESC"),
            "infinite": 1, "json": 1, "cc": cc, "l": lang,
        }
        if tag_ids:
            params["tags"] = ",".join(str(t) for t in tag_ids)
        if max_price is not None:
            params["maxprice"] = "free" if max_price == 0 else max_price
        data = http_json(f"{STORE}/search/results/", params,
                         timeout=timeout, insecure=insecure)
        if data.get("success") != 1:
            raise SteamError(f"Steam search failed (success={data.get('success')}).",
                             code="not_found")
        total = data.get("total_count", total)
        rows = _parse_search_rows(data.get("results_html", "") or "")
        if not rows:
            # Page 1 empty while Steam claims matches → the markup shifted under
            # us; fail loudly, don't return a false "empty". On a *later* page an
            # empty result just means the niche ran out (total_count is
            # approximate and often exceeds what Steam actually paginates) — the
            # markup is identical across pages, so this is never a parse failure.
            if start == 0 and total:
                raise SteamError("Steam search returned results but none could "
                                 "be parsed (page format changed).", code="parse")
            break
        added = False
        for r in rows:
            if r["appid"] in seen:
                continue
            seen.add(r["appid"])
            items.append(r)
            added = True
            if len(items) >= count:
                break
        if not added:                    # a page of all-dupes → no more new rows
            break
        start += per_page
    return items[:count], total


_BROWSE_COUNT_CAP = 1000  # safety valve: each 100 rows is one sequential request


def cmd_browse(args) -> int:
    args.cc = normalize_country(args.cc)
    names = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    tag_ids = _resolve_tag_ids(names, insecure=args.insecure,
                               timeout=args.timeout) if names else []
    # cap --count so an over-large value (e.g. --count 100000) can't fan out into
    # hundreds of sequential HTTP round-trips — same class of guard the project
    # applies to `reviews --all`. niche_size still reports the true full total.
    count = args.count if args.count > 0 else 20
    if count > _BROWSE_COUNT_CAP:
        if not (args.json or args.quiet):
            print(f"→ capping --count to {_BROWSE_COUNT_CAP} (niche_size still "
                  "reports the full match total)", file=sys.stderr)
        count = _BROWSE_COUNT_CAP
    items, total = _search_results(
        tag_ids, sort=args.sort, max_price=args.max_price, count=count,
        cc=args.cc, insecure=args.insecure, timeout=args.timeout)
    if args.json:
        _emit_json({"tags": names, "tag_ids": tag_ids, "sort": args.sort,
                    "max_price": args.max_price, "cc": args.cc,
                    "niche_size": total, "count": len(items), "items": items})
        return 0
    _print_browse(items, total, names, args)
    return 0


def _print_browse(items: list[dict], total: int, names: list[str], args) -> None:
    filt = []
    if names:
        filt.append("tags=" + ",".join(names))
    if args.max_price is not None:
        filt.append("free only" if args.max_price == 0 else f"≤ ${args.max_price}")
    filt.append(f"sort={args.sort}")
    print(f"Niche size: {_grouped(total)} games matching "
          f"[{' · '.join(filt)}]  [cc={args.cc}]")
    print(f"Showing top {len(items)}:\n")
    for it in items:
        print(f"{it['appid']:>8}  {it.get('name') or '—'}")
    if total > len(items):
        print(f"\n… {_grouped(total - len(items))} more (raise --count).")


# --- similar: the "more like this" recommendation grid ---
# Recommendation capsules are <a> tags carrying data-ds-appid (the stable
# dynamic-store attribute); we key on that rather than the
# "similar_grid_capsule" CSS class, so a restyle doesn't break the parse. The
# source app rides the page as a <div> (no <a>), so it's skipped naturally and
# by the explicit id check.
_REC_ANCHOR_RE = re.compile(r'<a\b[^>]*?\bdata-ds-appid="\d+"[^>]*?>', re.I)


def _similar_apps(appid: int, *, insecure: bool = False,
                  timeout: float = 30.0) -> list[dict]:
    raw = http_get(f"{STORE}/recommended/morelike/app/{appid}", timeout=timeout,
                   insecure=insecure, cache_ttl=DEFAULT_TTL,
                   cookie=_AGE_GATE_COOKIE)
    html_text = raw.decode("utf-8", "replace")
    out: list[dict] = []
    seen: set[int] = set()
    for tag in _REC_ANCHOR_RE.findall(html_text):
        rid = int(re.search(r'data-ds-appid="(\d+)"', tag).group(1))
        if rid == appid or rid in seen:    # drop the source app and dupes
            continue
        seen.add(rid)
        # the capsule href carries the title slug: /app/<id>/Hades_II/?...
        m_slug = _APP_HREF_RE.search(tag)
        name = _slug_to_name(m_slug.group(2)) if m_slug else None
        out.append({"appid": rid, "name": name, "tagids": _tagids_in(tag)})
    if not out:
        raise SteamError(
            f"no similar games parsed for appid {appid} (Steam page format "
            "changed, or none are recommended).", code="parse")
    return out


def cmd_similar(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure,
                          quiet=args.json or args.quiet, timeout=args.timeout)
    items = _similar_apps(appid, insecure=args.insecure, timeout=args.timeout)
    if args.limit and args.limit > 0:
        items = items[: args.limit]
    if args.json:
        _emit_json({"appid": appid, "count": len(items), "similar": items})
        return 0
    _print_similar(appid, items)
    return 0


def _print_similar(appid: int, items: list[dict]) -> None:
    print(f"appid {appid}: {len(items)} algorithmically similar games")
    print("(Steam 'more like this' — buyers-also-viewed recommendations, "
          "not a curated competitor list)\n")
    for it in items:
        print(f"{it['appid']:>8}  {it.get('name') or '—'}")


# --- history: review-volume velocity over time ---

def _review_histogram(appid: int, *, insecure: bool = False,
                      timeout: float = 30.0) -> dict:
    data = http_json(f"{STORE}/appreviewhistogram/{appid}", {"l": "english"},
                     timeout=timeout, insecure=insecure)
    if data.get("success") != 1:
        raise SteamError(f"no review histogram for appid {appid} "
                         f"(success={data.get('success')}).", code="not_found")
    return data.get("results", {}) or {}


def _agg_rollups(items: list[dict]) -> dict:
    up = sum(int(r.get("recommendations_up", 0) or 0) for r in items)
    down = sum(int(r.get("recommendations_down", 0) or 0) for r in items)
    tot = up + down
    return {"up": up, "down": down, "total": tot,
            "pct_positive": round(100 * up / tot) if tot else None}


def _histogram_summary(rollups: list[dict], recent: list[dict],
                       rollup_type: str | None) -> dict:
    n = len(rollups)
    win = min(3, n)
    peak = None
    if rollups:
        pk = max(rollups, key=lambda r: int(r.get("recommendations_up", 0) or 0)
                 + int(r.get("recommendations_down", 0) or 0))
        peak = {"date": pk.get("date"),
                "up": pk.get("recommendations_up"),
                "down": pk.get("recommendations_down"),
                "total": int(pk.get("recommendations_up", 0) or 0)
                + int(pk.get("recommendations_down", 0) or 0)}
    return {
        "buckets": n, "rollup_type": rollup_type, "window": win,
        "overall": _agg_rollups(rollups),
        "launch": _agg_rollups(rollups[:win]),
        "tail": _agg_rollups(rollups[-win:]),
        "recent_30d": _agg_rollups(recent),
        "peak": peak,
    }


def cmd_history(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure,
                          quiet=args.json or args.quiet, timeout=args.timeout)
    results = _review_histogram(appid, insecure=args.insecure, timeout=args.timeout)
    rollups = results.get("rollups", []) or []
    recent = results.get("recent", []) or []
    summary = _histogram_summary(rollups, recent, results.get("rollup_type"))
    if args.json:
        _emit_json({"appid": appid, "summary": summary,
                    "rollups": rollups, "recent_30d": recent})
        return 0
    _print_history(appid, summary, rollups, args)
    return 0


def _print_history(appid: int, s: dict, rollups: list[dict], args) -> None:
    rt = s.get("rollup_type") or "period"
    print(f"appid {appid}: review velocity — {s['buckets']} {rt} buckets\n")

    def line(label, a):
        pct = f"{a['pct_positive']}% pos" if a["pct_positive"] is not None else "n/a"
        print(f"  {label:14} {_grouped(a['total']):>9} reviews  "
              f"({_grouped(a['up'])}▲/{_grouped(a['down'])}▼, {pct})")

    line(f"Launch (first {s['window']})", s["launch"])
    line(f"Recent (last {s['window']})", s["tail"])
    line("Last 30 days", s["recent_30d"])
    line("All time", s["overall"])
    pk = s.get("peak")
    if pk:
        print(f"  {'Peak ' + rt:14} {_grouped(pk['total']):>9} reviews  "
              f"({_ts(pk['date'])})")
    tail = rollups[-args.months:] if args.months > 0 else rollups
    if tail:
        print(f"\nLast {len(tail)} {rt}s:")
        for r in tail:
            up = int(r.get("recommendations_up", 0) or 0)
            dn = int(r.get("recommendations_down", 0) or 0)
            print(f"  {_ts(r.get('date'))}  {_grouped(up):>7}▲ {_grouped(dn):>6}▼")


# --- sales estimate: the Boxleiter method (folded into `overview`) ---
# Owners ≈ review_count × multiplier. The multiplier varies wildly by genre,
# price and era (published estimates span ~20–80×), so we report a range, not a
# single false-precision number. This is an external heuristic on top of a real
# Steam figure (the review count), not a Steam-reported sales number.

_BOXLEITER_TIERS = (("conservative", 20), ("mid", 40), ("optimistic", 80))


def _boxleiter_estimate(total_reviews, *, price_cents=None,
                        multiplier=None) -> dict | None:
    if not total_reviews:
        return None
    if multiplier is not None:
        # argparse hands floats; show a whole number as an int (30, not 30.0)
        m = int(multiplier) if float(multiplier).is_integer() else multiplier
        tiers = (("custom", m),)
    else:
        tiers = _BOXLEITER_TIERS
    owners = {label: {"multiplier": m, "owners": int(total_reviews * m)}
              for label, m in tiers}
    out = {"method": "boxleiter (external heuristic)",
           "data": "Steam review count", "total_reviews": total_reviews,
           "owners": owners, "revenue_usd": None}
    if price_cents and price_cents > 0:
        price = price_cents / 100
        out["price_usd"] = price
        out["revenue_usd"] = {label: round(total_reviews * m * price)
                              for label, m in tiers}
    return out


# ----- argument parser -----------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="steam-cli",
        description="Query the public Steam API — overview, reviews, store "
                    "info, prices, player counts, news, achievements, and "
                    "images. No API key needed.",
        epilog="Add --json to any command for machine-readable output (errors "
               "too, as {\"error\",\"code\"}). A <game> argument is an appid "
               '(e.g. 1145360) or a name (e.g. "Hades") resolved via store '
               "search. Env: STEAM_CLI_HTTP=urllib|curl forces the HTTP "
               "backend; STEAM_CLI_CACHE_DIR sets the cache dir; "
               "STEAM_CLI_NO_CACHE=1 disables caching.",
    )
    p.add_argument("--version", action="version", version=f"steam-cli {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    def add_common(sp):
        sp.add_argument("--json", action="store_true", help="emit raw JSON")
        sp.add_argument("-q", "--quiet", action="store_true",
                        help="suppress the appid-resolution note on stderr")
        sp.add_argument("-k", "--insecure", action="store_true",
                        help="skip TLS verification (curl -k fallback)")
        sp.add_argument("--timeout", type=float, default=30.0,
                        help="per-request timeout in seconds (default 30)")
        sp.add_argument("--no-cache", action="store_true",
                        help="bypass the on-disk cache (always fetch fresh)")
        sp.add_argument("--cache-ttl", type=float, default=None, metavar="SECS",
                        help="override cache freshness for JSON responses")
        sp.add_argument("--refresh", action="store_true",
                        help="ignore cached entries but refresh them from network")

    # reviews
    r = sub.add_parser("reviews", help="download user reviews for a game")
    r.add_argument("game", help="appid or game name")
    r.add_argument("-n", "--num", type=int, default=100,
                   help="max reviews to fetch (default 100)")
    r.add_argument("--all", action="store_true",
                   help="fetch every available review (overrides --num)")
    r.add_argument("--summary", action="store_true",
                   help="only the score + totals, do not download reviews")
    r.add_argument("--language", default="all",
                   help="Steam language code or 'all' (default all)")
    r.add_argument("--review-type", choices=["all", "positive", "negative"],
                   default="all")
    r.add_argument("--purchase-type", choices=["all", "steam", "non_steam_purchase"],
                   default="all")
    r.add_argument("--filter", choices=["recent", "updated", "all"], default="recent",
                   help="recent/updated paginate; 'all' sorts by helpfulness "
                        "within --day-range")
    r.add_argument("--day-range", type=int, default=None,
                   help="for --filter all: window in days (1-365)")
    r.add_argument("--offtopic", action="store_true",
                   help="include review-bomb (off-topic) activity")
    r.add_argument("--min-playtime", type=float, default=None, metavar="HOURS",
                   help="keep only reviews whose author had >= HOURS at review "
                        "time (filters the fetched -n window; raise -n for more)")
    r.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                   help="keep only reviews created on/after this date "
                        "(filters the fetched -n window; raise -n for more)")
    r.add_argument("--delay", type=float, default=0.3,
                   help="seconds between pages (default 0.3)")
    r.add_argument("--jsonl", action="store_true",
                   help="one review JSON object per line")
    r.add_argument("--csv", action="store_true",
                   help="emit reviews as CSV (with --output: UTF-8 BOM for Excel)")
    r.add_argument("--output", help="write to FILE instead of stdout")
    r.add_argument("--cc", default="us", help="country code for name resolution")
    add_common(r)
    r.set_defaults(func=cmd_reviews)

    # info
    i = sub.add_parser("info", help="store details: genres, release, devs, metacritic")
    i.add_argument("game", help="appid or game name")
    i.add_argument("--cc", default="us", help="country code (affects price)")
    i.add_argument("--lang", default="english", help="store language")
    i.add_argument("--dlc", action="store_true", help="also list DLC appids")
    i.add_argument("--screenshots", action="store_true",
                   help="also list screenshot URLs")
    i.add_argument("--sysreqs", action="store_true",
                   help="also show system requirements")
    add_common(i)
    i.set_defaults(func=cmd_info)

    # search
    s = sub.add_parser("search", help="resolve a name to appid candidates")
    s.add_argument("term", help="search term")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--cc", default="us")
    s.add_argument("--lang", default="en")
    add_common(s)
    s.set_defaults(func=cmd_search)

    # players
    pl = sub.add_parser("players", help="current concurrent players")
    pl.add_argument("game", help="appid or game name")
    add_common(pl)
    pl.set_defaults(func=cmd_players)

    # news
    nw = sub.add_parser("news", help="latest news / patch notes")
    nw.add_argument("game", help="appid or game name")
    nw.add_argument("-c", "--count", type=int, default=5)
    nw.add_argument("--maxlength", type=int, default=300,
                    help="truncate each item's body (default 300; 0 = full)")
    add_common(nw)
    nw.set_defaults(func=cmd_news)

    # achievements
    ac = sub.add_parser("achievements", help="global achievement completion %%")
    ac.add_argument("game", help="appid or game name")
    ac.add_argument("--limit", type=int, default=25,
                    help="top N by completion (default 25; 0 = all)")
    add_common(ac)
    ac.set_defaults(func=cmd_achievements)

    # overview
    ov = sub.add_parser("overview",
                        help="one-shot snapshot: info + reviews + players + price")
    ov.add_argument("game", help="appid or game name")
    ov.add_argument("--cc", default="us",
                    help="country code for price and name resolution")
    ov.add_argument("--news", type=int, default=0, metavar="N",
                    help="also include the latest N news items (0 = off)")
    ov.add_argument("--top-achievements", type=int, default=0, metavar="N",
                    help="also include the top N achievements by completion %% "
                         "(0 = off)")
    ov.add_argument("--estimate", action="store_true",
                    help="add a rough Boxleiter sales estimate (owners/revenue "
                         "range from the review count — external heuristic)")
    ov.add_argument("--multiplier", type=float, default=None, metavar="N",
                    help="override the Boxleiter range with a single "
                         "owners-per-review multiplier")
    add_common(ov)
    ov.set_defaults(func=cmd_overview)

    # images
    im = sub.add_parser("images",
                        help="download header / screenshots / art to local files")
    im.add_argument("game", help="appid or game name")
    im.add_argument("--what", nargs="+",
                    choices=["header", "capsule", "background", "screenshots", "movies"],
                    default=["header", "screenshots"], metavar="KIND",
                    help="image kinds to download (default: header screenshots; "
                         "choices: header capsule background screenshots movies)")
    im.add_argument("--limit", type=int, default=4, metavar="N",
                    help="max screenshots / movie-thumbs to download (default 4)")
    im.add_argument("--out", default=None, metavar="DIR",
                    help="output directory (default ./steam-<appid>-media/)")
    add_common(im)
    im.set_defaults(func=cmd_images)

    # specials / top-sellers
    for name, fn, helptext in (
        ("specials", cmd_specials, "games currently on sale (featured specials)"),
        ("top-sellers", cmd_top_sellers, "current top-selling games"),
        ("coming-soon", cmd_coming_soon, "upcoming pre-release games (front page)"),
        ("new-releases", cmd_new_releases, "recently released games (front page)"),
    ):
        fp = sub.add_parser(name, help=helptext)
        fp.add_argument("--cc", default="us", help="country code (region for prices)")
        fp.add_argument("--lang", default="english", help="store language")
        fp.add_argument("--limit", type=int, default=0,
                        help="cap the list (0 = all returned)")
        add_common(fp)
        fp.set_defaults(func=fn)

    # profile
    pf = sub.add_parser("profile",
                        help="public Steam Community profile (no key; public only)")
    pf.add_argument("user", help="steamID64 (17 digits), vanity name, or profile URL")
    add_common(pf)
    pf.set_defaults(func=cmd_profile)

    # tags
    tg = sub.add_parser("tags",
                        help="user (community) tags with vote counts")
    tg.add_argument("game", help="appid or game name")
    tg.add_argument("--limit", type=int, default=0,
                    help="cap to the top N tags by votes (0 = all)")
    add_common(tg)
    tg.set_defaults(func=cmd_tags)

    # browse
    br = sub.add_parser("browse",
                        help="faceted store search: size and list a niche by "
                             "tag / price / sort")
    br.add_argument("--tags", default="", metavar="T1,T2",
                    help="comma-separated tag names or ids (e.g. cozy,roguelike); "
                         "names resolved via Steam's tag dictionary")
    br.add_argument("--max-price", type=int, default=None, metavar="USD",
                    help="cap price in the cc's currency units (0 = free only)")
    br.add_argument("--sort", choices=list(_SORT_MAP), default="reviews",
                    help="ordering (default reviews = most-reviewed)")
    br.add_argument("--count", type=int, default=20, metavar="N",
                    help="how many results to list (default 20; niche_size is "
                         "always the full match count)")
    br.add_argument("--cc", default="us", help="country code (region for prices)")
    add_common(br)
    br.set_defaults(func=cmd_browse)

    # similar
    sm = sub.add_parser("similar",
                        help="Steam 'more like this' recommendations for a game")
    sm.add_argument("game", help="appid or game name")
    sm.add_argument("--limit", type=int, default=0,
                    help="cap to N recommendations (0 = all returned)")
    add_common(sm)
    sm.set_defaults(func=cmd_similar)

    # history
    hi = sub.add_parser("history",
                        help="review-volume velocity over time (launch vs now)")
    hi.add_argument("game", help="appid or game name")
    hi.add_argument("--months", type=int, default=12, metavar="N",
                    help="how many recent buckets to tabulate (0 = all)")
    add_common(hi)
    hi.set_defaults(func=cmd_history)

    # cache
    ca = sub.add_parser("cache", help="show or clear the on-disk cache")
    ca.add_argument("--path", action="store_true",
                    help="print only the cache directory path")
    ca.add_argument("--clear", action="store_true",
                    help="delete all cached files")
    # cache is local-only, so the network flags from add_common would be
    # meaningless — but --json must work on every command (epilog contract).
    ca.add_argument("--json", action="store_true", help="emit raw JSON")
    ca.set_defaults(func=cmd_cache)

    # price
    pr = sub.add_parser("price", help="price and discount for one or more regions")
    pr.add_argument("game", help="appid or game name")
    pr.add_argument("--cc", default="us",
                    help="country code(s), comma-separated for a comparison, "
                         "e.g. us or us,de,ru")
    add_common(pr)
    pr.set_defaults(func=cmd_price)

    return p


def _force_utf8_io() -> None:
    """Make stdout/stderr emit UTF-8 regardless of the host console encoding.

    On a legacy Windows console (cp1252/cp437) the default encoding raises
    UnicodeEncodeError on non-ASCII game names, review text in any language, or
    the ▲ ▼ → · glyphs this CLI prints — so the command would crash on most of
    the Steam catalog. Reconfiguring to UTF-8 with errors="replace" means output
    degrades to a placeholder char in the rare un-encodable case instead of
    aborting. No-op where the stream can't be reconfigured (a pytest capture
    buffer, a plain StringIO), so it's safe everywhere."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_io()
    args = build_parser().parse_args(argv)
    if getattr(args, "no_cache", False):
        _CACHE.enabled = False
    if getattr(args, "cache_ttl", None) is not None:
        _CACHE.ttl_override = args.cache_ttl
    if getattr(args, "refresh", False):
        _CACHE.force_refresh = True
    try:
        return args.func(args)
    except SteamError as e:
        if getattr(args, "json", False):
            # Machine-readable failure on stdout so an agent parsing --json
            # output gets a valid object instead of an empty stream.
            _emit_json({"error": str(e), "code": e.code})
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # last-resort contract guard
        # An unexpected bug (unforeseen response shape, KeyError, TypeError…)
        # must NOT leave a --json consumer with an empty stdout + traceback:
        # the "--json always yields a JSON object" contract holds even here.
        if getattr(args, "json", False):
            _emit_json({"error": f"{type(e).__name__}: {e}", "code": "internal"})
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
