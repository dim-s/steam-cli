#!/usr/bin/env python3
"""steam-cli — query the public Steam API (no API key needed).

Subcommands: reviews, info, search, players, news, achievements, price.
Stdlib only. Transparently falls back from urllib to `curl` when the host
intercepts TLS (corporate proxies, dev machines with a custom root CA).
"""
from __future__ import annotations

import argparse
import calendar
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
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.1.0"

STORE = "https://store.steampowered.com"
API = "https://api.steampowered.com"
USER_AGENT = f"steam-cli/{__version__} (+https://github.com/dim-s/steam-cli)"


class SteamError(Exception):
    """User-facing error: printed without a traceback.

    `code` is a short machine-readable slug (not_found / http / network /
    parse / error) surfaced in the JSON error envelope so an agent can branch
    on the failure kind without parsing the message.
    """

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


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
    # The pid suffix keeps two concurrent writers of the same URL from
    # truncating each other's in-progress tmp file.
    tmp = f"{path}.{os.getpid()}.tmp"
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


def _get_urllib(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_curl(url: str, timeout: float, insecure: bool) -> bytes:
    curl = shutil.which("curl")
    if not curl:
        raise SteamError(
            "TLS verification failed via Python and no `curl` is available "
            "to fall back to. Set STEAM_CLI_HTTP=urllib and fix the cert "
            "store, or install curl."
        )
    cmd = [curl, "-fsSL", "--max-time", str(int(timeout)), "-A", USER_AGENT]
    if insecure:
        cmd.append("-k")
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip() or f"curl exit {proc.returncode}"
        raise SteamError(f"HTTP request failed: {msg}", code="network")
    return proc.stdout


def _is_tls_error(err: urllib.error.URLError) -> bool:
    reason = getattr(err, "reason", None)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _retriable_status(code: int) -> bool:
    return code == 429 or 500 <= code < 600


def http_get(url: str, timeout: float = 30.0, insecure: bool = False,
             *, cache_ttl: float = 0) -> bytes:
    cached = _cache_get(url, cache_ttl)
    if cached is not None:
        return cached
    raw = _http_get_network(url, timeout, insecure)
    _cache_put(url, raw, cache_ttl)
    return raw


def _http_get_network(url: str, timeout: float, insecure: bool) -> bytes:
    global _curl_fallback
    if _HTTP_MODE == "curl" or _curl_fallback or insecure:
        return _get_curl(url, timeout, insecure)
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _get_urllib(url, timeout)
        except urllib.error.HTTPError as e:
            if _retriable_status(e.code) and attempt < _MAX_RETRIES - 1:
                last = e
                time.sleep(2 ** attempt)
                continue
            raise SteamError(f"HTTP {e.code} for {url}", code="http") from e
        except urllib.error.URLError as e:
            if _HTTP_MODE == "auto" and _is_tls_error(e) and shutil.which("curl"):
                _curl_fallback = True  # remember for the rest of the run
                return _get_curl(url, timeout, insecure=False)
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
    if not quiet:
        print(f'→ resolved "{game}" to appid {top["id"]} ({top.get("name", "?")})',
              file=sys.stderr)
    return int(top["id"])


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
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


def cmd_reviews(args) -> int:
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
            _emit_json({"appid": appid, "query_summary": summary})
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

    if args.min_playtime is not None or args.since:
        before = len(collected)
        collected = _filter_reviews(collected, min_playtime=args.min_playtime,
                                    since=args.since)
        if len(collected) < before and not (args.json or args.quiet):
            print(f"→ filters kept {len(collected)} of {before} fetched reviews "
                  f"(applied to the fetched window; raise -n or use --all for more)",
                  file=sys.stderr)

    if args.json:
        _emit_json({"appid": appid, "query_summary": summary,
                    "count": len(collected), "reviews": collected})
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
    print(f"appid {appid} — {desc} ({pct} positive)")
    print(f"{_grouped(tot)} reviews · {_grouped(pos)} ▲ / {_grouped(neg)} ▼"
          f"  [language={language}]")


def _print_reviews(appid: int, s: dict, reviews: list[dict], args) -> None:
    _print_review_summary(appid, s, args.language)
    print(f"[showing {len(reviews)} · filter={args.filter}]\n")
    for r in reviews:
        mark = "▲" if r.get("voted_up") else "▼"
        author = r.get("author", {})
        hours = round(author.get("playtime_at_review", author.get("playtime_forever", 0)) / 60)
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
    regions = [normalize_country(c) for c in args.cc.split(",") if c.strip()] or ["us"]
    appid = resolve_appid(args.game, cc=regions[0], lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet,
                          timeout=args.timeout)
    results: list[dict] = []
    for cc in regions:
        try:
            data = _appdetails(appid, cc, "en", filters="price_overview,basic",
                               insecure=args.insecure, timeout=args.timeout)
        except SteamError as e:
            # one unavailable region must not sink the whole comparison
            results.append({"cc": cc, "error": str(e)})
            continue
        results.append({
            "cc": cc,
            "name": data.get("name", f"appid {appid}"),
            "is_free": bool(data.get("is_free")),
            "price_overview": data.get("price_overview"),
        })
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
        print(f"{it.get('id'):>8}  {it.get('name', '?')}  [{it.get('type', '?')}]  {ptxt}")
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

    def soft(fn):
        # players / reviews are nice-to-have: degrade to null, don't fail.
        try:
            return fn()
        except SteamError:
            return None

    players = soft(lambda: _current_players(appid, insecure=args.insecure,
                                            timeout=args.timeout))
    review_summary = soft(lambda: _review_summary(appid, insecure=args.insecure,
                                                   timeout=args.timeout))
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
    if args.news:
        overview["news"] = soft(lambda: _news_items(
            appid, count=args.news, insecure=args.insecure, timeout=args.timeout))
    if args.top_achievements:
        overview["top_achievements"] = soft(lambda: _achievement_percentages(
            appid, insecure=args.insecure, timeout=args.timeout
        )[: args.top_achievements])
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
    print(f"\nStore: {o['store_url']}")


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
        print(_CACHE.dir)
        return 0
    if args.clear:
        n = _cache_clear()
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
    print(f"cache dir: {_CACHE.dir}")
    print(f"entries:   {entries}  ({files} files, {_grouped(total)} bytes)")
    print("clear with `steam-cli cache --clear`")
    return 0


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

    # cache
    ca = sub.add_parser("cache", help="show or clear the on-disk cache")
    ca.add_argument("--path", action="store_true",
                    help="print only the cache directory path")
    ca.add_argument("--clear", action="store_true",
                    help="delete all cached files")
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


def main(argv: list[str] | None = None) -> int:
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


if __name__ == "__main__":
    sys.exit(main())
