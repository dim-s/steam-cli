#!/usr/bin/env python3
"""steam-cli — query the public Steam API (no API key needed).

Subcommands: reviews, info, search, players, news, achievements, price.
Stdlib only. Transparently falls back from urllib to `curl` when the host
intercepts TLS (corporate proxies, dev machines with a custom root CA).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.0.0"

STORE = "https://store.steampowered.com"
API = "https://api.steampowered.com"
USER_AGENT = f"steam-cli/{__version__} (+https://github.com/dim-s/steam-cli)"


class SteamError(Exception):
    """User-facing error: printed without a traceback."""


# ----- HTTP layer ----------------------------------------------------------
# Try stdlib urllib first; if the TLS handshake fails because the machine
# intercepts HTTPS (custom root CA not in Python's trust store), fall back to
# the system `curl`, which uses the OS trust store. Force a mode with
# STEAM_CLI_HTTP=urllib|curl.

_HTTP_MODE = os.environ.get("STEAM_CLI_HTTP", "auto").lower()
_curl_fallback = False


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
        raise SteamError(f"HTTP request failed: {msg}")
    return proc.stdout


def _is_tls_error(err: urllib.error.URLError) -> bool:
    reason = getattr(err, "reason", None)
    return isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def http_get(url: str, timeout: float = 30.0, insecure: bool = False) -> bytes:
    global _curl_fallback
    if _HTTP_MODE == "curl" or _curl_fallback or insecure:
        return _get_curl(url, timeout, insecure)
    try:
        return _get_urllib(url, timeout)
    except urllib.error.HTTPError as e:
        raise SteamError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        if _HTTP_MODE == "auto" and _is_tls_error(e) and shutil.which("curl"):
            _curl_fallback = True  # remember for the rest of the run
            return _get_curl(url, timeout, insecure=False)
        raise SteamError(f"Network error: {e}") from e


def http_json(url: str, params: dict | None = None, *, timeout: float = 30.0,
              insecure: bool = False) -> dict:
    if params:
        # drop None values, then urlencode (handles cursor=* etc.)
        clean = {k: v for k, v in params.items() if v is not None}
        url = url + "?" + urllib.parse.urlencode(clean)
    raw = http_get(url, timeout=timeout, insecure=insecure)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SteamError(f"Steam returned non-JSON ({len(raw)} bytes). {e}") from e


# ----- app-id resolution ---------------------------------------------------

def store_search(term: str, *, cc: str = "us", lang: str = "en",
                 insecure: bool = False) -> list[dict]:
    data = http_json(f"{STORE}/api/storesearch/",
                     {"term": term, "cc": cc, "l": lang}, insecure=insecure)
    return data.get("items", []) or []


def resolve_appid(game: str, *, cc: str = "us", lang: str = "en",
                  insecure: bool = False, quiet: bool = False) -> int:
    """Accept a numeric appid as-is; otherwise resolve a name via storesearch."""
    g = game.strip()
    if g.isdigit():
        return int(g)
    items = store_search(g, cc=cc, lang=lang, insecure=insecure)
    if not items:
        raise SteamError(f'No Steam app found for "{game}". Try `steam-cli search "{game}"`.')
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

def cmd_reviews(args) -> int:
    appid = resolve_appid(args.game, cc=args.cc, lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet)

    # --summary: one request, num_per_page=0, just the aggregate counts.
    if args.summary:
        data = http_json(f"{STORE}/appreviews/{appid}", {
            "json": 1, "num_per_page": 0,
            "language": args.language, "review_type": args.review_type,
            "purchase_type": args.purchase_type, "filter": "all",
            "filter_offtopic_activity": 0 if args.offtopic else 1,
        }, insecure=args.insecure)
        if data.get("success") != 1:
            raise SteamError(f"Steam review query failed (success={data.get('success')}).")
        summary = data.get("query_summary", {})
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
        }, insecure=args.insecure)
        if data.get("success") != 1:
            raise SteamError(f"Steam review query failed (success={data.get('success')}).")
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
                insecure: bool = False) -> dict:
    params = {"appids": appid, "cc": cc, "l": lang}
    if filters:
        params["filters"] = filters
    data = http_json(f"{STORE}/api/appdetails", params, insecure=insecure)
    entry = data.get(str(appid), {})
    if not entry.get("success"):
        raise SteamError(f"No store details for appid {appid} (region cc={cc}).")
    return entry.get("data", {})


def cmd_info(args) -> int:
    appid = resolve_appid(args.game, cc=args.cc, lang=args.lang,
                          insecure=args.insecure, quiet=args.json or args.quiet)
    data = _appdetails(appid, args.cc, args.lang, insecure=args.insecure)
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
    print(f"\nStore: {STORE}/app/{appid}/")
    return 0


def cmd_price(args) -> int:
    appid = resolve_appid(args.game, cc=args.cc, lang="en",
                          insecure=args.insecure, quiet=args.json or args.quiet)
    data = _appdetails(appid, args.cc, "en", filters="price_overview,basic",
                       insecure=args.insecure)
    price = data.get("price_overview")
    is_free = data.get("is_free")
    if args.json:
        _emit_json({"appid": appid, "cc": args.cc, "is_free": bool(is_free),
                    "price_overview": price})
        return 0
    name = data.get("name", f"appid {appid}")
    if is_free:
        print(f"{name}: Free to Play  [cc={args.cc}]")
        return 0
    if not price:
        print(f"{name}: no price for region cc={args.cc} (not sold there?)")
        return 0
    line = price.get("final_formatted", "?")
    if price.get("discount_percent"):
        line += (f"  (-{price['discount_percent']}% off, "
                 f"was {price.get('initial_formatted')})")
    print(f"{name}: {line}  [cc={args.cc}]")
    return 0


# ----- subcommand: search --------------------------------------------------

def cmd_search(args) -> int:
    items = store_search(args.term, cc=args.cc, lang=args.lang, insecure=args.insecure)
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

def cmd_players(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure, quiet=args.json or args.quiet)
    data = http_json(f"{API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                     {"appid": appid}, insecure=args.insecure)
    resp = data.get("response", {})
    if resp.get("result") != 1:
        raise SteamError(f"No player-count data for appid {appid}.")
    count = resp.get("player_count", 0)
    if args.json:
        _emit_json({"appid": appid, "player_count": count})
        return 0
    print(f"appid {appid}: {_grouped(count)} players online now")
    return 0


# ----- subcommand: news ----------------------------------------------------

def cmd_news(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure, quiet=args.json or args.quiet)
    data = http_json(f"{API}/ISteamNews/GetNewsForApp/v2/", {
        "appid": appid, "count": args.count, "maxlength": args.maxlength,
    }, insecure=args.insecure)
    items = data.get("appnews", {}).get("newsitems", []) or []
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

def cmd_achievements(args) -> int:
    appid = resolve_appid(args.game, insecure=args.insecure, quiet=args.json or args.quiet)
    data = http_json(f"{API}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
                     {"gameid": appid}, insecure=args.insecure)
    achs = data.get("achievementpercentages", {}).get("achievements", []) or []
    for a in achs:  # Steam sometimes serializes percent as a string
        try:
            a["percent"] = float(a.get("percent", 0))
        except (TypeError, ValueError):
            a["percent"] = 0.0
    achs.sort(key=lambda a: a["percent"], reverse=True)
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


# ----- argument parser -----------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="steam-cli",
        description="Query the public Steam API: reviews, info, search, "
                    "players, news, achievements, price. No API key needed.",
        epilog="A <game> argument is an appid (e.g. 1145360) or a name "
               '(e.g. "Hades") resolved via store search. '
               "STEAM_CLI_HTTP=urllib|curl forces the HTTP backend.",
    )
    p.add_argument("--version", action="version", version=f"steam-cli {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    def add_common(sp):
        sp.add_argument("--json", action="store_true", help="emit raw JSON")
        sp.add_argument("-q", "--quiet", action="store_true",
                        help="suppress the appid-resolution note on stderr")
        sp.add_argument("-k", "--insecure", action="store_true",
                        help="skip TLS verification (curl -k fallback)")

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

    # price
    pr = sub.add_parser("price", help="price and discount for a region")
    pr.add_argument("game", help="appid or game name")
    pr.add_argument("--cc", default="us", help="country code (region), e.g. us, de, ru")
    add_common(pr)
    pr.set_defaults(func=cmd_price)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SteamError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
