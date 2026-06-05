"""Offline unit tests for steam_cli.

The network is always mocked. Command tests assert against real captured Steam
responses in tests/fixtures/; the cursor pagination loop and a few edge cases
use small synthetic pages so the exact control flow can be pinned down.
"""
from __future__ import annotations

import json
import math
import os
import ssl
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest

import steam_cli


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def make_args(argv):
    """Build a parsed Namespace exactly as the CLI would (also exercises the parser)."""
    return steam_cli.build_parser().parse_args(argv)


class StubJson:
    """Stand-in for steam_cli.http_json that records calls and returns canned data.

    `responses` may be a single dict (returned every call), a list (returned in
    call order; an extra call past the end fails the test — this catches a
    pagination loop that refuses to stop), or a callable(url, params) -> dict.
    """

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def __call__(self, url, params=None, *, timeout=30.0, insecure=False,
                 cache_ttl=steam_cli.DEFAULT_TTL):   # mirror real http_json default
        self.calls.append(SimpleNamespace(url=url, params=dict(params or {}),
                                          timeout=timeout, insecure=insecure,
                                          cache_ttl=cache_ttl))
        r = self._responses
        if callable(r):
            return r(url, params)
        if isinstance(r, list):
            idx = len(self.calls) - 1
            assert idx < len(r), f"unexpected http_json call #{idx + 1} to {url}"
            return r[idx]
        return r


@pytest.fixture
def stub_json(monkeypatch):
    def _install(responses):
        stub = StubJson(responses)
        monkeypatch.setattr(steam_cli, "http_json", stub)
        return stub
    return _install


def appdetails(appid, data, success=True):
    return {str(appid): {"success": success, "data": data}}


def review(rid, up=True, funny=0, lang="english"):
    return {
        "recommendationid": str(rid), "voted_up": up, "review": f"review-{rid}",
        "author": {"playtime_at_review": 600, "playtime_forever": 1200},
        "timestamp_created": 0, "votes_funny": funny, "language": lang,
    }


def review_page(reviews, cursor, success=1, summary=None):
    return {
        "success": success,
        "query_summary": summary or {
            "total_reviews": 10, "total_positive": 8, "total_negative": 2,
            "review_score_desc": "Mostly Positive",
        },
        "reviews": reviews,
        "cursor": cursor,
    }


# --------------------------------------------------------------------------- #
# formatting helpers                                                           #
# --------------------------------------------------------------------------- #

class TestFormatHelpers:
    def test_grouped_int(self):
        assert steam_cli._grouped(152006) == "152,006"

    def test_grouped_float_truncates(self):
        assert steam_cli._grouped(1234.0) == "1,234"

    def test_grouped_non_numeric_string(self):
        assert steam_cli._grouped("n/a") == "n/a"

    def test_grouped_none(self):
        assert steam_cli._grouped(None) == "None"

    def test_flatten_collapses_whitespace(self):
        assert steam_cli._flatten("a\n  b\t c") == "a b c"

    def test_flatten_truncates_with_ellipsis(self):
        out = steam_cli._flatten("x" * 500, limit=10)
        assert len(out) == 10 and out.endswith("…")

    def test_flatten_keeps_short_text(self):
        assert steam_cli._flatten("short", limit=280) == "short"

    def test_flatten_none(self):
        assert steam_cli._flatten(None) == ""

    def test_ts_epoch_zero(self):
        assert steam_cli._ts(0) == "1970-01-01"

    def test_ts_string_epoch(self):
        # Steam often serializes the date as a numeric string.
        assert steam_cli._ts("1600300800") == "2020-09-17"

    def test_ts_none(self):
        assert steam_cli._ts(None) == "?"

    def test_ts_garbage(self):
        assert steam_cli._ts("not-a-date") == "?"

    def test_emit_json_unicode_and_newline(self, capsys):
        steam_cli._emit_json({"name": "café"})
        out = capsys.readouterr().out
        assert "café" in out          # ensure_ascii=False
        assert out.endswith("\n")


# --------------------------------------------------------------------------- #
# _write_out                                                                   #
# --------------------------------------------------------------------------- #

class TestWriteOut:
    def test_writes_to_stdout_when_no_path(self, capsys):
        steam_cli._write_out(None, lambda: print("hello"))
        assert capsys.readouterr().out == "hello\n"

    def test_writes_to_file(self, tmp_path, capsys):
        p = tmp_path / "out.txt"
        steam_cli._write_out(str(p), lambda: print("filedata"))
        assert p.read_text(encoding="utf-8") == "filedata\n"
        assert "written to" in capsys.readouterr().err

    def test_restores_stdout_on_exception(self, tmp_path):
        import sys
        orig = sys.stdout
        p = tmp_path / "out.txt"

        def boom():
            raise ValueError("render failed")

        with pytest.raises(ValueError):
            steam_cli._write_out(str(p), boom)
        assert sys.stdout is orig   # finally-clause restored it


# --------------------------------------------------------------------------- #
# appid resolution                                                            #
# --------------------------------------------------------------------------- #

class TestResolveAppid:
    def test_numeric_passthrough_no_network(self, stub_json):
        stub = stub_json({})  # would record any call
        assert steam_cli.resolve_appid("570") == 570
        assert stub.calls == []   # numeric input never hits the network

    def test_numeric_with_surrounding_whitespace(self, stub_json):
        stub_json({})
        assert steam_cli.resolve_appid("  1145360  ") == 1145360

    def test_name_resolves_to_top_hit(self, stub_json, fixture, capsys):
        stub_json(fixture("storesearch_hades.json"))
        # The real top hit for "hades" is Hades II (id 1145350) — the README quirk.
        assert steam_cli.resolve_appid("hades") == 1145350
        assert "1145350" in capsys.readouterr().err

    def test_name_quiet_suppresses_stderr(self, stub_json, fixture, capsys):
        stub_json(fixture("storesearch_hades.json"))
        steam_cli.resolve_appid("hades", quiet=True)
        assert capsys.readouterr().err == ""

    def test_no_results_raises(self, stub_json):
        stub_json({"items": []})
        with pytest.raises(steam_cli.SteamError, match="No Steam app found"):
            steam_cli.resolve_appid("zzzznotagame")


# --------------------------------------------------------------------------- #
# HTTP layer: http_json                                                        #
# --------------------------------------------------------------------------- #

class TestHttpJson:
    def test_drops_none_params_and_parses(self, monkeypatch):
        captured = {}

        def fake_get(url, timeout=30.0, insecure=False, cache_ttl=0):
            captured["url"] = url
            return b'{"ok": true}'

        monkeypatch.setattr(steam_cli, "http_get", fake_get)
        out = steam_cli.http_json("http://x/api", {"a": 1, "b": None, "c": "hi"})
        assert out == {"ok": True}
        query = urllib.parse.urlparse(captured["url"]).query
        assert urllib.parse.parse_qs(query) == {"a": ["1"], "c": ["hi"]}   # b (None) dropped

    def test_no_params_leaves_url_bare(self, monkeypatch):
        captured = {}

        def fake_get(url, timeout=30.0, insecure=False, cache_ttl=0):
            captured["url"] = url
            return b"{}"

        monkeypatch.setattr(steam_cli, "http_get", fake_get)
        steam_cli.http_json("http://x/api")
        assert captured["url"] == "http://x/api"

    def test_non_json_raises(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "http_get",
                            lambda url, timeout=30.0, insecure=False, cache_ttl=0: b"<html>nope</html>")
        with pytest.raises(steam_cli.SteamError, match="non-JSON"):
            steam_cli.http_json("http://x/api")


# --------------------------------------------------------------------------- #
# HTTP layer: http_get backend selection & TLS fallback                        #
# --------------------------------------------------------------------------- #

class TestHttpGet:
    def test_urllib_success(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "_get_urllib", lambda url, timeout: b"URLLIB")
        monkeypatch.setattr(steam_cli, "_get_curl",
                            lambda *a, **k: pytest.fail("curl must not be called"))
        assert steam_cli.http_get("http://x") == b"URLLIB"

    def test_mode_curl_forces_curl(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "_HTTP_MODE", "curl")
        monkeypatch.setattr(steam_cli, "_get_urllib",
                            lambda *a, **k: pytest.fail("urllib must not be called"))
        monkeypatch.setattr(steam_cli, "_get_curl", lambda url, timeout, insecure: b"CURL")
        assert steam_cli.http_get("http://x") == b"CURL"

    def test_insecure_forces_curl_with_insecure_flag(self, monkeypatch):
        seen = {}

        def fake_curl(url, timeout, insecure):
            seen["insecure"] = insecure
            return b"CURL"

        monkeypatch.setattr(steam_cli, "_get_curl", fake_curl)
        assert steam_cli.http_get("http://x", insecure=True) == b"CURL"
        assert seen["insecure"] is True

    def test_tls_error_falls_back_to_curl_and_sticks(self, monkeypatch):
        def raise_tls(url, timeout):
            raise urllib.error.URLError(ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))

        monkeypatch.setattr(steam_cli, "_get_urllib", raise_tls)
        monkeypatch.setattr(steam_cli.shutil, "which", lambda _: "/usr/bin/curl")
        monkeypatch.setattr(steam_cli, "_get_curl", lambda url, timeout, insecure: b"FELLBACK")
        assert steam_cli.http_get("http://x") == b"FELLBACK"
        # the fallback is remembered for the rest of the run
        assert steam_cli._curl_fallback is True

    def test_tls_error_without_curl_raises(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "_get_urllib",
                            lambda url, timeout: (_ for _ in ()).throw(
                                urllib.error.URLError(ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))))
        monkeypatch.setattr(steam_cli.shutil, "which", lambda _: None)
        with pytest.raises(steam_cli.SteamError, match="Network error"):
            steam_cli.http_get("http://x")

    def test_http_error_raises_steamerror(self, monkeypatch):
        def raise_http(url, timeout):
            raise urllib.error.HTTPError("http://x", 404, "Not Found", hdrs=None, fp=None)

        monkeypatch.setattr(steam_cli, "_get_urllib", raise_http)
        with pytest.raises(steam_cli.SteamError, match="HTTP 404"):
            steam_cli.http_get("http://x")

    def test_non_tls_urlerror_raises_network_error(self, monkeypatch):
        # A non-TLS URLError must surface as a network error without ever
        # consulting curl: _is_tls_error returns False for a plain connection
        # error, so neither branch that calls shutil.which is entered (it's
        # deliberately left unpatched here). Retries are exhausted first.
        monkeypatch.setattr(steam_cli, "_get_urllib",
                            lambda url, timeout: (_ for _ in ()).throw(
                                urllib.error.URLError("Connection refused")))
        with pytest.raises(steam_cli.SteamError, match="Network error"):
            steam_cli.http_get("http://x")

    def test_sticky_fallback_uses_curl_directly(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "_curl_fallback", True)
        monkeypatch.setattr(steam_cli, "_get_urllib",
                            lambda *a, **k: pytest.fail("urllib must not be called"))
        monkeypatch.setattr(steam_cli, "_get_curl", lambda url, timeout, insecure: b"CURL")
        assert steam_cli.http_get("http://x") == b"CURL"


    def test_get_urllib_sends_user_agent(self, monkeypatch):
        seen = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"BODY"

        def fake_urlopen(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            seen["timeout"] = timeout
            return FakeResp()

        monkeypatch.setattr(steam_cli.urllib.request, "urlopen", fake_urlopen)
        assert steam_cli._get_urllib("http://x", 9.0) == b"BODY"
        assert seen["ua"] == steam_cli.USER_AGENT
        assert seen["timeout"] == 9.0


class TestIsTlsError:
    def test_sslerror_reason(self):
        assert steam_cli._is_tls_error(urllib.error.URLError(ssl.SSLError("x"))) is True

    def test_certificate_string_reason(self):
        err = urllib.error.URLError("CERTIFICATE_VERIFY_FAILED: unable to get cert")
        assert steam_cli._is_tls_error(err) is True

    def test_unrelated_reason(self):
        assert steam_cli._is_tls_error(urllib.error.URLError("Connection refused")) is False


# --------------------------------------------------------------------------- #
# HTTP layer: _get_curl                                                         #
# --------------------------------------------------------------------------- #

class TestGetCurl:
    def test_missing_curl_raises(self, monkeypatch):
        monkeypatch.setattr(steam_cli.shutil, "which", lambda _: None)
        with pytest.raises(steam_cli.SteamError, match="no `curl`"):
            steam_cli._get_curl("http://x", 30.0, insecure=False)

    def test_success_returns_stdout(self, monkeypatch):
        monkeypatch.setattr(steam_cli.shutil, "which", lambda _: "/usr/bin/curl")
        monkeypatch.setattr(steam_cli.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"BODY", stderr=b""))
        assert steam_cli._get_curl("http://x", 30.0, insecure=False) == b"BODY"

    def test_nonzero_exit_raises_with_stderr(self, monkeypatch):
        monkeypatch.setattr(steam_cli.shutil, "which", lambda _: "/usr/bin/curl")
        monkeypatch.setattr(steam_cli.subprocess, "run",
                            lambda *a, **k: SimpleNamespace(returncode=22, stdout=b"", stderr=b"404 Not Found"))
        with pytest.raises(steam_cli.SteamError, match="404 Not Found"):
            steam_cli._get_curl("http://x", 30.0, insecure=False)

    def test_insecure_adds_k_flag_and_timeout(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

        monkeypatch.setattr(steam_cli.shutil, "which", lambda _: "/usr/bin/curl")
        monkeypatch.setattr(steam_cli.subprocess, "run", fake_run)
        steam_cli._get_curl("http://x", 12.7, insecure=True)
        assert "-k" in seen["cmd"]
        assert "12" in seen["cmd"]          # --max-time int(12.7)
        assert seen["cmd"][-1] == "http://x"


# --------------------------------------------------------------------------- #
# command: reviews                                                             #
# --------------------------------------------------------------------------- #

class TestReviews:
    def test_summary_json(self, stub_json, fixture, capsys):
        stub_json(fixture("appreviews_1145360.json"))
        rc = steam_cli.cmd_reviews(make_args(["reviews", "1145360", "--summary", "--json"]))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["appid"] == 1145360
        assert out["query_summary"]["total_reviews"] == 152006

    def test_summary_text(self, stub_json, fixture, capsys):
        stub_json(fixture("appreviews_1145360.json"))
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "--summary"]))
        out = capsys.readouterr().out
        assert "Overwhelmingly Positive" in out
        assert "152,006 reviews" in out

    def test_summary_failure_raises(self, stub_json):
        stub_json({"success": 2})
        with pytest.raises(steam_cli.SteamError, match="review query failed"):
            steam_cli.cmd_reviews(make_args(["reviews", "1145360", "--summary"]))

    def test_full_reviews_json_single_page(self, stub_json, fixture, capsys):
        stub = stub_json(fixture("appreviews_1145360.json"))
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "-n", "3", "--delay", "0", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 3
        assert len(out["reviews"]) == 3
        assert len(stub.calls) == 1   # target reached in one page

    def test_pagination_stops_on_duplicate_cursor(self, stub_json, capsys):
        pages = [
            review_page([review(1), review(2)], cursor="C1"),
            review_page([review(3), review(4)], cursor="C1"),  # repeated cursor
        ]
        stub = stub_json(pages)
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "-n", "10", "--delay", "0", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert len(stub.calls) == 2     # stops after seeing the repeated cursor
        assert out["count"] == 4

    def test_pagination_stops_on_empty_batch(self, stub_json, capsys):
        pages = [
            review_page([review(1), review(2)], cursor="C1"),
            review_page([], cursor="C2"),   # empty batch ends the loop
        ]
        stub = stub_json(pages)
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "-n", "10", "--delay", "0", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert len(stub.calls) == 2
        assert out["count"] == 2

    def test_repeated_star_cursor_terminates(self, stub_json, capsys):
        # The loop seeds cursor="*" but does not pre-seed `seen`, so an API that
        # echoes cursor="*" costs one extra request, then stops on the repeat
        # (no infinite loop). Pin that contract.
        pages = [
            review_page([review(1)], cursor="*"),
            review_page([review(2)], cursor="*"),   # repeat → break
        ]
        stub = stub_json(pages)
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "--all", "--delay", "0", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert len(stub.calls) == 2
        assert out["count"] == 2

    def test_all_fetches_until_empty(self, stub_json, capsys):
        pages = [
            review_page([review(1)], cursor="C1"),
            review_page([review(2)], cursor="C2"),
            review_page([], cursor="C3"),
        ]
        stub = stub_json(pages)
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "--all", "--delay", "0", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 2
        assert len(stub.calls) == 3

    def test_delay_sleeps_between_pages(self, stub_json, monkeypatch, capsys):
        slept = []
        monkeypatch.setattr(steam_cli.time, "sleep", lambda s: slept.append(s))
        pages = [
            review_page([review(1)], cursor="C1"),
            review_page([review(2)], cursor="C2"),
            review_page([], cursor="C3"),
        ]
        stub_json(pages)
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "--all", "--delay", "0.5", "--json"]))
        assert slept == [0.5, 0.5]   # paused after each non-final page

    def test_full_failure_raises(self, stub_json):
        stub_json({"success": 2})
        with pytest.raises(steam_cli.SteamError, match="review query failed"):
            steam_cli.cmd_reviews(make_args(["reviews", "1145360", "-n", "5"]))

    def test_jsonl_to_file(self, stub_json, tmp_path):
        stub_json([review_page([review(1), review(2)], cursor="C1")])
        path = tmp_path / "r.jsonl"
        steam_cli.cmd_reviews(make_args(
            ["reviews", "1145360", "-n", "2", "--delay", "0", "--jsonl", "--output", str(path)]))
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["recommendationid"] == "1"

    def test_text_output_renders_bodies(self, stub_json, capsys):
        stub_json([review_page([review(1, up=True, funny=3, lang="english")], cursor="C1")])
        steam_cli.cmd_reviews(make_args(["reviews", "1145360", "-n", "1", "--delay", "0"]))
        out = capsys.readouterr().out
        assert "review-1" in out
        assert "3 funny" in out
        assert "▲" in out
        assert "· english" in out   # language tag shown when --language all (default)


# --------------------------------------------------------------------------- #
# command: info                                                                #
# --------------------------------------------------------------------------- #

class TestInfo:
    def test_json(self, stub_json, fixture, capsys):
        stub_json(fixture("appdetails_1145360.json"))
        steam_cli.cmd_info(make_args(["info", "1145360", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out["name"] == "Hades"
        assert "genres" in out

    def test_text_fields(self, stub_json, fixture, capsys):
        stub_json(fixture("appdetails_1145360.json"))
        steam_cli.cmd_info(make_args(["info", "1145360"]))
        out = capsys.readouterr().out
        assert "Hades" in out
        assert "Sep 17, 2020" in out
        assert "Metacritic:  93" in out
        assert "$24.99" in out

    def test_free_to_play(self, stub_json, capsys):
        stub_json(appdetails(570, {"name": "Dota 2", "type": "game", "is_free": True,
                                   "release_date": {"date": "Jul 9, 2013"}}))
        steam_cli.cmd_info(make_args(["info", "570"]))
        assert "Free to Play" in capsys.readouterr().out

    def test_discount_rendered(self, stub_json, capsys):
        data = {"name": "X", "type": "game",
                "price_overview": {"final_formatted": "$24.00", "discount_percent": 20,
                                   "initial_formatted": "$30.00"}}
        stub_json(appdetails(999, data))
        steam_cli.cmd_info(make_args(["info", "999"]))
        out = capsys.readouterr().out
        assert "-20%" in out
        assert "$30.00" in out

    def test_coming_soon_marked(self, stub_json, capsys):
        stub_json(appdetails(999, {"name": "Upcoming", "type": "game",
                                   "release_date": {"coming_soon": True, "date": "Q4 2099"}}))
        steam_cli.cmd_info(make_args(["info", "999"]))
        assert "(coming soon)" in capsys.readouterr().out

    def test_missing_store_details_raises(self, stub_json):
        stub_json(appdetails(123, {}, success=False))
        with pytest.raises(steam_cli.SteamError, match="No store details"):
            steam_cli.cmd_info(make_args(["info", "123"]))


# --------------------------------------------------------------------------- #
# command: price                                                               #
# --------------------------------------------------------------------------- #

class TestPrice:
    def test_json(self, stub_json, fixture, capsys):
        stub_json(fixture("appdetails_1145360.json"))
        steam_cli.cmd_price(make_args(["price", "1145360", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out["appid"] == 1145360
        assert len(out["regions"]) == 1
        reg = out["regions"][0]
        assert reg["cc"] == "us"
        assert reg["is_free"] is False
        assert reg["price_overview"]["final_formatted"] == "$24.99"

    def test_free(self, stub_json, capsys):
        stub_json(appdetails(570, {"name": "Dota 2", "is_free": True}))
        steam_cli.cmd_price(make_args(["price", "570"]))
        assert "Free to Play" in capsys.readouterr().out

    def test_discount(self, stub_json, capsys):
        data = {"name": "X", "price_overview": {"final_formatted": "$5.00",
                "discount_percent": 50, "initial_formatted": "$10.00"}}
        stub_json(appdetails(999, data))
        steam_cli.cmd_price(make_args(["price", "999"]))
        out = capsys.readouterr().out
        assert "-50% off" in out
        assert "$10.00" in out

    def test_no_price_for_region(self, stub_json, capsys):
        stub_json(appdetails(999, {"name": "X"}))   # not free, no price_overview
        steam_cli.cmd_price(make_args(["price", "999"]))
        assert "no price for this region" in capsys.readouterr().out

    def test_multi_region(self, stub_json, capsys):
        # one appid resolved once, one appdetails call per region
        def respond(url, params):
            cc = params["cc"]
            cents = {"us": "$24.99", "de": "24,99€"}[cc]
            return appdetails(1145360, {"name": "Hades",
                              "price_overview": {"final_formatted": cents}})
        stub_json(respond)
        steam_cli.cmd_price(make_args(["price", "1145360", "--cc", "us,de", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert [r["cc"] for r in out["regions"]] == ["us", "de"]
        assert out["regions"][1]["price_overview"]["final_formatted"] == "24,99€"

    def test_one_region_unavailable_does_not_sink_others(self, stub_json, capsys):
        def respond(url, params):
            if params["cc"] == "ru":
                return appdetails(1145360, {}, success=False)
            return appdetails(1145360, {"name": "Hades",
                              "price_overview": {"final_formatted": "$24.99"}})
        stub_json(respond)
        steam_cli.cmd_price(make_args(["price", "1145360", "--cc", "us,ru", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out["regions"][0]["price_overview"]["final_formatted"] == "$24.99"
        assert "error" in out["regions"][1]


# --------------------------------------------------------------------------- #
# command: search                                                              #
# --------------------------------------------------------------------------- #

class TestSearch:
    def test_json(self, stub_json, fixture, capsys):
        stub_json(fixture("storesearch_hades.json"))
        steam_cli.cmd_search(make_args(["search", "hades", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out[0]["id"] == 1145350

    def test_text(self, stub_json, fixture, capsys):
        stub_json(fixture("storesearch_hades.json"))
        steam_cli.cmd_search(make_args(["search", "hades"]))
        assert "Hades II" in capsys.readouterr().out

    def test_limit(self, stub_json, fixture, capsys):
        stub_json(fixture("storesearch_hades.json"))
        steam_cli.cmd_search(make_args(["search", "hades", "--limit", "1"]))
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_empty(self, stub_json, capsys):
        stub_json({"items": []})
        steam_cli.cmd_search(make_args(["search", "zzz"]))
        assert "No results" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# command: players                                                             #
# --------------------------------------------------------------------------- #

class TestPlayers:
    def test_json(self, stub_json, fixture, capsys):
        stub_json(fixture("players_570.json"))
        steam_cli.cmd_players(make_args(["players", "570", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out == {"appid": 570, "player_count": 437832}

    def test_text(self, stub_json, fixture, capsys):
        stub_json(fixture("players_570.json"))
        steam_cli.cmd_players(make_args(["players", "570"]))
        assert "437,832 players online now" in capsys.readouterr().out

    def test_no_data_raises(self, stub_json):
        stub_json({"response": {"result": 42}})
        with pytest.raises(steam_cli.SteamError, match="No player-count data"):
            steam_cli.cmd_players(make_args(["players", "570"]))


# --------------------------------------------------------------------------- #
# command: news                                                                #
# --------------------------------------------------------------------------- #

class TestNews:
    def test_json(self, stub_json, fixture, capsys):
        stub_json(fixture("news_570.json"))
        steam_cli.cmd_news(make_args(["news", "570", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert isinstance(out, list) and len(out) == 3

    def test_text(self, stub_json, fixture, capsys):
        data = fixture("news_570.json")
        stub_json(data)
        steam_cli.cmd_news(make_args(["news", "570"]))
        title = data["appnews"]["newsitems"][0]["title"]
        assert title in capsys.readouterr().out

    def test_empty(self, stub_json, capsys):
        stub_json({"appnews": {"newsitems": []}})
        steam_cli.cmd_news(make_args(["news", "570"]))
        assert "No news" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# command: achievements                                                        #
# --------------------------------------------------------------------------- #

class TestAchievements:
    def test_json_sorted_desc_floats(self, stub_json, fixture, capsys):
        stub_json(fixture("achievements_440.json"))
        steam_cli.cmd_achievements(make_args(["achievements", "440", "--json", "--limit", "5"]))
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 5
        percents = [a["percent"] for a in out]
        assert all(isinstance(p, float) for p in percents)   # coerced from strings
        assert percents == sorted(percents, reverse=True)

    def test_text_shows_counts(self, stub_json, fixture, capsys):
        stub_json(fixture("achievements_440.json"))
        steam_cli.cmd_achievements(make_args(["achievements", "440", "--limit", "5"]))
        out = capsys.readouterr().out
        assert "520 achievements" in out
        assert "showing 5" in out
        assert "515 more" in out

    def test_limit_zero_shows_all(self, stub_json, fixture, capsys):
        stub_json(fixture("achievements_440.json"))
        steam_cli.cmd_achievements(make_args(["achievements", "440", "--limit", "0"]))
        out = capsys.readouterr().out
        assert "showing 520" in out

    def test_malformed_percent_coerced_to_zero(self, stub_json, capsys):
        data = {"achievementpercentages": {"achievements": [
            {"name": "A", "percent": "bad"},
            {"name": "B", "percent": "10.0"},
        ]}}
        stub_json(data)
        steam_cli.cmd_achievements(make_args(["achievements", "440", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out[0]["name"] == "B" and out[0]["percent"] == 10.0
        assert out[1]["name"] == "A" and out[1]["percent"] == 0.0

    def test_empty(self, stub_json, capsys):
        stub_json({"achievementpercentages": {"achievements": []}})
        steam_cli.cmd_achievements(make_args(["achievements", "440"]))
        assert "No global achievement data" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# parser & main                                                                #
# --------------------------------------------------------------------------- #

class TestParser:
    def test_reviews_defaults(self):
        args = make_args(["reviews", "570"])
        assert args.num == 100
        assert args.filter == "recent"
        assert args.language == "all"
        assert args.delay == 0.3
        assert args.func is steam_cli.cmd_reviews

    def test_subcommand_required(self):
        with pytest.raises(SystemExit):
            make_args([])

    def test_invalid_choice_rejected(self):
        with pytest.raises(SystemExit):
            make_args(["reviews", "570", "--review-type", "bogus"])

    def test_help_is_self_describing_for_agents(self):
        # --help must tell a cold LLM the key conventions, or discoverability
        # regresses. Guards the LLM-friendliness intent against silent edits.
        help_text = steam_cli.build_parser().format_help()
        assert "--json" in help_text                    # machine-output convention
        assert "STEAM_CLI_CACHE_DIR" in help_text        # cache location env
        # the one-line description must not advertise a stale subset of commands
        assert "overview" in help_text and "images" in help_text


class TestMain:
    def test_happy_path_returns_zero(self, stub_json, fixture):
        stub_json(fixture("players_570.json"))
        assert steam_cli.main(["players", "570", "--json"]) == 0

    def test_steamerror_returns_one(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise steam_cli.SteamError("kaboom")

        monkeypatch.setattr(steam_cli, "http_json", boom)
        rc = steam_cli.main(["players", "570"])
        assert rc == 1
        assert "error: kaboom" in capsys.readouterr().err

    def test_steamerror_json_envelope_on_stdout(self, monkeypatch, capsys):
        # With --json the failure must be a valid JSON object on stdout (not an
        # empty stream + a stderr line), so an agent parsing the output doesn't
        # choke. Exit code stays 1.
        def boom(*a, **k):
            raise steam_cli.SteamError("kaboom", code="not_found")

        monkeypatch.setattr(steam_cli, "http_json", boom)
        rc = steam_cli.main(["players", "570", "--json"])
        cap = capsys.readouterr()
        assert rc == 1
        assert cap.err == ""
        assert json.loads(cap.out) == {"error": "kaboom", "code": "not_found"}

    def test_broken_pipe_returns_zero(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "http_json",
                            lambda *a, **k: (_ for _ in ()).throw(BrokenPipeError()))
        assert steam_cli.main(["players", "570"]) == 0

    def test_keyboard_interrupt_returns_130(self, monkeypatch):
        monkeypatch.setattr(steam_cli, "http_json",
                            lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        assert steam_cli.main(["players", "570"]) == 130

    def test_runs_as_script_version(self):
        # Smoke-test the real entry point; --version never touches the network.
        import subprocess
        import sys
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent / "steam_cli.py"
        proc = subprocess.run([sys.executable, str(script), "--version"],
                              capture_output=True, text=True)
        assert proc.returncode == 0
        assert proc.stdout.strip() == f"steam-cli {steam_cli.__version__}"

    def test_end_to_end_through_http_get(self, monkeypatch, fixture_bytes, capsys):
        # Mock only the lowest layer (raw bytes); exercise http_get→http_json→command.
        monkeypatch.setattr(steam_cli, "http_get",
                            lambda url, timeout=30.0, insecure=False, cache_ttl=0: fixture_bytes("players_570.json"))
        rc = steam_cli.main(["players", "570", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["player_count"] == 437832


# --------------------------------------------------------------------------- #
# HTTP layer: retry with backoff                                               #
# --------------------------------------------------------------------------- #

class TestRetry:
    def _http_error(self, url, code):
        return urllib.error.HTTPError(url, code, "err", hdrs=None, fp=None)

    def test_retries_on_500_then_succeeds(self, monkeypatch):
        calls = {"n": 0}
        sleeps = []

        def flaky(url, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise self._http_error(url, 500)
            return b"OK"

        monkeypatch.setattr(steam_cli.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(steam_cli, "_get_urllib", flaky)
        assert steam_cli.http_get("http://x") == b"OK"
        assert calls["n"] == 3
        assert sleeps == [1, 2]  # 2**0, 2**1 — backoff only between retries

    def test_retries_on_429(self, monkeypatch):
        calls = {"n": 0}

        def flaky(url, timeout):
            calls["n"] += 1
            if calls["n"] < 2:
                raise self._http_error(url, 429)
            return b"OK"

        monkeypatch.setattr(steam_cli, "_get_urllib", flaky)
        assert steam_cli.http_get("http://x") == b"OK"
        assert calls["n"] == 2

    def test_no_retry_on_404(self, monkeypatch):
        calls = {"n": 0}

        def fail(url, timeout):
            calls["n"] += 1
            raise self._http_error(url, 404)

        monkeypatch.setattr(steam_cli, "_get_urllib", fail)
        with pytest.raises(steam_cli.SteamError, match="HTTP 404"):
            steam_cli.http_get("http://x")
        assert calls["n"] == 1  # 4xx other than 429 is not retried

    def test_exhausts_retries_on_persistent_5xx(self, monkeypatch):
        calls = {"n": 0}

        def always(url, timeout):
            calls["n"] += 1
            raise self._http_error(url, 503)

        monkeypatch.setattr(steam_cli, "_get_urllib", always)
        with pytest.raises(steam_cli.SteamError, match="HTTP 503") as ei:
            steam_cli.http_get("http://x")
        assert ei.value.code == "http"
        assert calls["n"] == steam_cli._MAX_RETRIES  # 3 total attempts

    def test_transient_network_error_retried(self, monkeypatch):
        calls = {"n": 0}

        def flaky(url, timeout):
            calls["n"] += 1
            if calls["n"] < 2:
                raise urllib.error.URLError("Connection reset by peer")
            return b"OK"

        monkeypatch.setattr(steam_cli, "_get_urllib", flaky)
        assert steam_cli.http_get("http://x") == b"OK"
        assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# reviews: client-side filters (--min-playtime / --since)                       #
# --------------------------------------------------------------------------- #

class TestReviewFilters:
    def test_min_playtime_filters(self, stub_json, capsys):
        revs = [
            {"recommendationid": "long", "voted_up": True, "review": "a",
             "author": {"playtime_at_review": 1200}, "timestamp_created": 0},   # 20h
            {"recommendationid": "short", "voted_up": True, "review": "b",
             "author": {"playtime_at_review": 120}, "timestamp_created": 0},    # 2h
        ]
        stub_json(review_page(revs, cursor="*"))
        steam_cli.cmd_reviews(make_args(["reviews", "570", "-n", "2",
                                         "--min-playtime", "10", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 1
        assert out["reviews"][0]["recommendationid"] == "long"

    def test_since_filters(self, stub_json, capsys):
        revs = [
            {"recommendationid": "new", "voted_up": True, "review": "x",
             "author": {"playtime_at_review": 600}, "timestamp_created": 1_700_000_000},
            {"recommendationid": "old", "voted_up": True, "review": "y",
             "author": {"playtime_at_review": 600}, "timestamp_created": 1_500_000_000},
        ]
        stub_json(review_page(revs, cursor="*"))
        steam_cli.cmd_reviews(make_args(["reviews", "570", "-n", "2",
                                         "--since", "2023-06-01", "--json"]))
        out = json.loads(capsys.readouterr().out)
        assert [r["recommendationid"] for r in out["reviews"]] == ["new"]

    def test_bad_since_date_raises(self, stub_json):
        stub_json(review_page([review(1)], cursor="*"))
        with pytest.raises(steam_cli.SteamError, match="invalid date"):
            steam_cli.cmd_reviews(make_args(["reviews", "570", "-n", "1",
                                             "--since", "not-a-date", "--json"]))

    def test_zero_playtime_at_review_not_treated_as_lifetime(self, stub_json, capsys):
        # 0 hours at review time must be honoured, not fall through to the
        # (large) lifetime playtime and sneak past --min-playtime.
        revs = [{
            "recommendationid": "rookie", "voted_up": True, "review": "early",
            "author": {"playtime_at_review": 0, "playtime_forever": 6000},
            "timestamp_created": 0,
        }]
        stub_json(review_page(revs, cursor="*"))
        steam_cli.cmd_reviews(make_args(["reviews", "570", "-n", "1",
                                         "--min-playtime", "10", "--json", "-q"]))
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 0


# --------------------------------------------------------------------------- #
# command: overview                                                            #
# --------------------------------------------------------------------------- #

class TestOverview:
    def test_json_aggregates(self, stub_json, capsys):
        def respond(url, params):
            if "appdetails" in url:
                return appdetails(1145360, {
                    "name": "Hades", "type": "game",
                    "release_date": {"date": "17 Sep, 2020", "coming_soon": False},
                    "developers": ["Supergiant Games"],
                    "genres": [{"description": "Action"}, {"description": "Indie"}],
                    "is_free": False,
                    "price_overview": {"final_formatted": "$24.99"},
                })
            if "GetNumberOfCurrentPlayers" in url:
                return {"response": {"result": 1, "player_count": 1234}}
            if "appreviews" in url:
                return {"success": 1, "query_summary": {
                    "review_score_desc": "Overwhelmingly Positive",
                    "total_reviews": 100, "total_positive": 98}}
            raise AssertionError(f"unexpected url {url}")

        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "1145360", "--json"]))
        o = json.loads(capsys.readouterr().out)
        assert o["name"] == "Hades"
        assert o["players_online"] == 1234
        assert o["review_summary"]["total_reviews"] == 100
        assert o["price"]["final_formatted"] == "$24.99"
        assert o["genres"] == ["Action", "Indie"]
        # opt-in sections are absent unless their flag is passed
        assert "news" not in o and "top_achievements" not in o

    def test_degrades_when_soft_parts_unavailable(self, stub_json, capsys):
        def respond(url, params):
            if "appdetails" in url:
                return appdetails(999, {"name": "X", "is_free": True})
            if "GetNumberOfCurrentPlayers" in url:
                return {"response": {"result": 0}}     # → _current_players raises
            if "appreviews" in url:
                return {"success": 0}                  # → _review_summary raises
            raise AssertionError(f"unexpected url {url}")

        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "999", "--json"]))
        o = json.loads(capsys.readouterr().out)
        assert o["players_online"] is None
        assert o["review_summary"] is None
        assert o["is_free"] is True
        assert o["price"] is None

    def test_appdetails_failure_is_hard(self, stub_json):
        def respond(url, params):
            if "appdetails" in url:
                return appdetails(999, {}, success=False)
            raise AssertionError("soft parts must not be reached")

        stub_json(respond)
        with pytest.raises(steam_cli.SteamError, match="No store details"):
            steam_cli.cmd_overview(make_args(["overview", "999", "--json"]))

    def test_text_render(self, stub_json, capsys):
        def respond(url, params):
            if "appdetails" in url:
                return appdetails(570, {"name": "Dota 2", "type": "game",
                                        "is_free": True,
                                        "genres": [{"description": "MOBA"}]})
            if "GetNumberOfCurrentPlayers" in url:
                return {"response": {"result": 1, "player_count": 500000}}
            if "appreviews" in url:
                return {"success": 1, "query_summary": {
                    "review_score_desc": "Very Positive",
                    "total_reviews": 2_000_000, "total_positive": 1_700_000}}
            raise AssertionError(f"unexpected url {url}")

        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "570"]))
        out = capsys.readouterr().out
        assert "Dota 2" in out
        assert "Free to Play" in out
        assert "Players now:" in out

    def _base(self, url, params):
        """Minimal happy responses for the core overview requests."""
        if "appdetails" in url:
            return appdetails(570, {"name": "X", "is_free": True})
        if "GetNumberOfCurrentPlayers" in url:
            return {"response": {"result": 1, "player_count": 1}}
        if "appreviews" in url:
            return {"success": 1, "query_summary": {}}
        return None

    def test_news_section_included(self, stub_json, fixture, capsys):
        def respond(url, params):
            if "GetNewsForApp" in url:
                return fixture("news_570.json")
            return self._base(url, params)
        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "570", "--news", "3", "--json"]))
        o = json.loads(capsys.readouterr().out)
        assert len(o["news"]) == 3
        assert o["news"][0]["title"] == "7.41d Gameplay Patch"

    def test_news_degrades_to_null(self, stub_json, capsys):
        def respond(url, params):
            if "GetNewsForApp" in url:
                raise steam_cli.SteamError("news down", code="http")
            return self._base(url, params)
        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "570", "--news", "3", "--json"]))
        assert json.loads(capsys.readouterr().out)["news"] is None

    def test_top_achievements_section(self, stub_json, fixture, capsys):
        def respond(url, params):
            if "GetGlobalAchievementPercentagesForApp" in url:
                return fixture("achievements_440.json")
            return self._base(url, params)
        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "570",
                                          "--top-achievements", "3", "--json"]))
        o = json.loads(capsys.readouterr().out)
        assert len(o["top_achievements"]) == 3
        pcts = [a["percent"] for a in o["top_achievements"]]
        assert pcts == sorted(pcts, reverse=True)   # rarest-last, highest first

    def test_top_achievements_degrades_to_null(self, stub_json, capsys):
        def respond(url, params):
            if "GetGlobalAchievementPercentagesForApp" in url:
                raise steam_cli.SteamError("no data", code="not_found")
            return self._base(url, params)
        stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "570",
                                          "--top-achievements", "3", "--json"]))
        assert json.loads(capsys.readouterr().out)["top_achievements"] is None


# --------------------------------------------------------------------------- #
# command: info optional sections                                              #
# --------------------------------------------------------------------------- #

class TestInfoSections:
    def test_dlc_listed(self, stub_json, capsys):
        stub_json(appdetails(1, {"name": "G", "dlc": [11, 22]}))
        steam_cli.cmd_info(make_args(["info", "1", "--dlc"]))
        out = capsys.readouterr().out
        assert "DLC (2)" in out
        assert "appid 11" in out

    def test_screenshots_listed(self, stub_json, capsys):
        stub_json(appdetails(1, {"name": "G",
                  "screenshots": [{"path_full": "http://img/1.jpg"}]}))
        steam_cli.cmd_info(make_args(["info", "1", "--screenshots"]))
        assert "http://img/1.jpg" in capsys.readouterr().out

    def test_sysreqs_stripped_of_html(self, stub_json, capsys):
        stub_json(appdetails(1, {"name": "G", "pc_requirements":
                  {"minimum": "<strong>Minimum:</strong><br>OS: Win10"}}))
        steam_cli.cmd_info(make_args(["info", "1", "--sysreqs"]))
        out = capsys.readouterr().out
        assert "OS: Win10" in out
        assert "<strong>" not in out

    def test_sysreqs_decodes_html_entities(self, stub_json, capsys):
        stub_json(appdetails(1, {"name": "G", "pc_requirements":
                  {"minimum": "OS: Win10 &amp; up, Sound &reg; card"}}))
        steam_cli.cmd_info(make_args(["info", "1", "--sysreqs"]))
        out = capsys.readouterr().out
        assert "Win10 & up" in out
        assert "&amp;" not in out

    def test_sysreqs_handles_empty_list(self, stub_json, capsys):
        # Steam serves pc_requirements as [] when there are none — must not crash.
        stub_json(appdetails(1, {"name": "G", "pc_requirements": []}))
        steam_cli.cmd_info(make_args(["info", "1", "--sysreqs"]))
        assert "System requirements" in capsys.readouterr().out

    def test_sections_off_by_default(self, stub_json, capsys):
        stub_json(appdetails(1, {"name": "G", "dlc": [11]}))
        steam_cli.cmd_info(make_args(["info", "1"]))
        assert "DLC" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# --timeout is threaded all the way to the HTTP layer                           #
# --------------------------------------------------------------------------- #

class TestTimeoutThreading:
    def test_timeout_reaches_http(self, stub_json):
        stub = stub_json({"response": {"result": 1, "player_count": 5}})
        steam_cli.cmd_players(make_args(["players", "570", "--timeout", "7", "--json"]))
        assert stub.calls[0].timeout == 7.0

    def test_timeout_reaches_resolve(self, stub_json):
        def respond(url, params):
            if "storesearch" in url:
                return {"items": [{"id": 570, "name": "Dota 2"}]}
            return {"response": {"result": 1, "player_count": 5}}

        stub = stub_json(respond)
        steam_cli.cmd_players(make_args(["players", "Dota", "--timeout", "3",
                                         "--json", "-q"]))
        # the name-resolution request must carry the timeout too
        assert stub.calls[0].url.endswith("/api/storesearch/")
        assert stub.calls[0].timeout == 3.0


# --------------------------------------------------------------------------- #
# command: images                                                              #
# --------------------------------------------------------------------------- #

class TestImages:
    def test_downloads_default_set(self, stub_json, monkeypatch, tmp_path, capsys):
        stub_json(appdetails(1145360, {
            "name": "Hades",
            "header_image": "https://cdn/apps/1145360/header.jpg?t=1",
            "screenshots": [
                {"path_full": "https://cdn/apps/1145360/ss_aaa.1920x1080.jpg?t=1"},
                {"path_full": "https://cdn/apps/1145360/ss_bbb.1920x1080.jpg?t=1"},
            ],
        }))
        monkeypatch.setattr(steam_cli, "http_get",
                            lambda url, timeout=30.0, insecure=False, cache_ttl=0: b"PNGDATA")
        steam_cli.cmd_images(make_args(["images", "1145360",
                                        "--out", str(tmp_path), "--json", "-q"]))
        o = json.loads(capsys.readouterr().out)
        assert [i["kind"] for i in o["images"]] == ["header", "screenshot", "screenshot"]
        for i in o["images"]:                       # files actually written
            assert os.path.isabs(i["path"])         # absolute, cwd-independent
            assert os.path.exists(i["path"])
            with open(i["path"], "rb") as f:
                assert f.read() == b"PNGDATA"

    def test_what_selection_and_limit(self, stub_json, monkeypatch, tmp_path, capsys):
        stub_json(appdetails(1, {
            "name": "G",
            "header_image": "http://cdn/header.jpg",
            "capsule_image": "http://cdn/capsule_231x87.jpg",
            "screenshots": [{"path_full": f"http://cdn/ss_{i}.jpg"} for i in range(10)],
        }))
        monkeypatch.setattr(steam_cli, "http_get", lambda *a, **k: b"X")
        steam_cli.cmd_images(make_args(["images", "1", "--what", "capsule",
                                        "screenshots", "--limit", "2",
                                        "--out", str(tmp_path), "--json", "-q"]))
        o = json.loads(capsys.readouterr().out)
        # header excluded (not requested), screenshots capped at --limit 2
        assert [i["kind"] for i in o["images"]] == ["capsule", "screenshot", "screenshot"]

    def test_partial_failure_does_not_sink_others(self, stub_json, monkeypatch,
                                                  tmp_path, capsys):
        stub_json(appdetails(1, {
            "name": "G",
            "header_image": "http://cdn/header.jpg",
            "screenshots": [{"path_full": "http://cdn/ss_bad.jpg"}],
        }))

        def flaky(url, timeout=30.0, insecure=False, cache_ttl=0):
            if "bad" in url:
                raise steam_cli.SteamError("HTTP 404", code="http")
            return b"OK"

        monkeypatch.setattr(steam_cli, "http_get", flaky)
        steam_cli.cmd_images(make_args(["images", "1", "--out", str(tmp_path),
                                        "--json", "-q"]))
        o = json.loads(capsys.readouterr().out)
        assert o["images"][0]["kind"] == "header" and "path" in o["images"][0]
        assert "error" in o["images"][1]

    def test_no_images_available_downloads_nothing(self, stub_json, monkeypatch,
                                                   tmp_path, capsys):
        stub_json(appdetails(1, {"name": "G"}))   # no header, no screenshots
        called = {"n": 0}

        def counter(*a, **k):
            called["n"] += 1
            return b"X"

        monkeypatch.setattr(steam_cli, "http_get", counter)
        steam_cli.cmd_images(make_args(["images", "1", "--out", str(tmp_path),
                                        "--json", "-q"]))
        o = json.loads(capsys.readouterr().out)
        assert o["images"] == []
        assert called["n"] == 0

    def test_text_render(self, stub_json, monkeypatch, tmp_path, capsys):
        stub_json(appdetails(1, {"name": "G", "header_image": "http://cdn/header.jpg"}))
        monkeypatch.setattr(steam_cli, "http_get", lambda *a, **k: b"X")
        steam_cli.cmd_images(make_args(["images", "1", "--out", str(tmp_path), "-q"]))
        out = capsys.readouterr().out
        assert "saved 1/1 images" in out
        assert "header" in out

    def test_write_failure_recorded_not_raised(self, stub_json, monkeypatch,
                                               tmp_path, capsys):
        # An OSError on write must be isolated per-item (recorded as error),
        # never escape as a traceback and break the --json contract.
        stub_json(appdetails(1, {"name": "G", "header_image": "http://cdn/header.jpg"}))
        monkeypatch.setattr(steam_cli, "http_get", lambda *a, **k: b"X")

        def boom(*a, **k):
            raise OSError("disk full")

        # bare open() in the module resolves to this module-global stand-in
        monkeypatch.setattr(steam_cli, "open", boom, raising=False)
        steam_cli.cmd_images(make_args(["images", "1", "--out", str(tmp_path),
                                        "--json", "-q"]))
        o = json.loads(capsys.readouterr().out)
        assert "error" in o["images"][0]
        assert "disk full" in o["images"][0]["error"]

    def test_bad_out_dir_raises_clean_error(self, stub_json, monkeypatch, tmp_path):
        stub_json(appdetails(1, {"name": "G", "header_image": "http://cdn/h.jpg"}))
        monkeypatch.setattr(steam_cli, "http_get", lambda *a, **k: b"X")
        afile = tmp_path / "not-a-dir"
        afile.write_text("x")           # --out points at an existing file
        with pytest.raises(steam_cli.SteamError, match="cannot create output dir"):
            steam_cli.cmd_images(make_args(["images", "1", "--out", str(afile),
                                            "--json", "-q"]))

    def test_filename_defaults_extension_for_extensionless_url(self):
        name = steam_cli._image_filename("background", 0,
                                         "https://cdn/images/app/570?t=9")
        assert name.endswith(".jpg")

    def test_filename_keeps_real_extension(self):
        name = steam_cli._image_filename("screenshot", 2,
                                         "https://cdn/ss_abc.1920x1080.jpg?t=1")
        assert name == "screenshot_2_ss_abc.1920x1080.jpg"


# --------------------------------------------------------------------------- #
# on-disk cache layer                                                          #
# --------------------------------------------------------------------------- #

class TestCache:
    def _enable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(steam_cli._CACHE, "enabled", True)
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(tmp_path / "c"))

    def _net_counter(self, monkeypatch, payload=b"DATA"):
        calls = {"n": 0}

        def net(url, timeout, insecure):
            calls["n"] += 1
            return payload

        monkeypatch.setattr(steam_cli, "_http_get_network", net)
        return calls

    def test_hit_skips_network(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        a = steam_cli.http_get("http://x", cache_ttl=100)
        b = steam_cli.http_get("http://x", cache_ttl=100)
        assert a == b == b"DATA"
        assert calls["n"] == 1                       # second served from cache

    def test_ttl_zero_always_hits_network(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        steam_cli.http_get("http://x", cache_ttl=0)
        steam_cli.http_get("http://x", cache_ttl=0)
        assert calls["n"] == 2

    def test_expired_entry_refetched(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        clock = [1000.0]
        monkeypatch.setattr(steam_cli.time, "time", lambda: clock[0])
        steam_cli.http_get("http://x", cache_ttl=10)   # fetched at t=1000
        clock[0] = 1005.0
        steam_cli.http_get("http://x", cache_ttl=10)   # age 5 < 10 → hit
        assert calls["n"] == 1
        clock[0] = 1020.0
        steam_cli.http_get("http://x", cache_ttl=10)   # age 20 > 10 → miss
        assert calls["n"] == 2

    def test_inf_ttl_never_expires(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch, b"IMG")
        clock = [1000.0]
        monkeypatch.setattr(steam_cli.time, "time", lambda: clock[0])
        steam_cli.http_get("http://img", cache_ttl=math.inf)
        clock[0] = 10 ** 12                            # far future
        steam_cli.http_get("http://img", cache_ttl=math.inf)
        assert calls["n"] == 1

    def test_disabled_bypasses(self, monkeypatch, tmp_path):
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(tmp_path / "c"))
        # _CACHE.enabled is already False (conftest default)
        calls = self._net_counter(monkeypatch)
        steam_cli.http_get("http://x", cache_ttl=100)
        steam_cli.http_get("http://x", cache_ttl=100)
        assert calls["n"] == 2

    def test_force_refresh_skips_read_but_writes(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        steam_cli.http_get("http://x", cache_ttl=100)               # writes entry
        monkeypatch.setattr(steam_cli._CACHE, "force_refresh", True)
        steam_cli.http_get("http://x", cache_ttl=100)               # forced network
        assert calls["n"] == 2
        monkeypatch.setattr(steam_cli._CACHE, "force_refresh", False)
        steam_cli.http_get("http://x", cache_ttl=100)               # refreshed → hit
        assert calls["n"] == 2

    def test_ttl_override_applies_to_finite(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        clock = [1000.0]
        monkeypatch.setattr(steam_cli.time, "time", lambda: clock[0])
        monkeypatch.setattr(steam_cli._CACHE, "ttl_override", 5)
        steam_cli.http_get("http://x", cache_ttl=10_000)
        clock[0] = 1008.0
        steam_cli.http_get("http://x", cache_ttl=10_000)   # age 8 > override 5 → miss
        assert calls["n"] == 2

    def test_override_does_not_shrink_immutable(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch, b"IMG")
        clock = [1000.0]
        monkeypatch.setattr(steam_cli.time, "time", lambda: clock[0])
        monkeypatch.setattr(steam_cli._CACHE, "ttl_override", 5)
        steam_cli.http_get("http://img", cache_ttl=math.inf)
        clock[0] = 10 ** 9
        steam_cli.http_get("http://img", cache_ttl=math.inf)
        assert calls["n"] == 1                         # inf not overridden

    def test_corrupt_meta_is_miss(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        steam_cli.http_get("http://x", cache_ttl=100)
        _, meta_p = steam_cli._cache_paths("http://x")
        with open(meta_p, "w") as f:
            f.write("not json{")
        steam_cli.http_get("http://x", cache_ttl=100)   # corrupt → treated as miss
        assert calls["n"] == 2

    def test_writes_body_and_meta_no_tmp_left(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        self._net_counter(monkeypatch, b"BODY")
        steam_cli.http_get("http://x", cache_ttl=100)
        body_p, meta_p = steam_cli._cache_paths("http://x")
        assert os.path.exists(body_p) and os.path.exists(meta_p)
        with open(body_p, "rb") as f:
            assert f.read() == b"BODY"
        # os.replace consumed the tmp file — none left behind in the dir
        assert not [n for n in os.listdir(steam_cli._CACHE.dir) if n.endswith(".tmp")]

    def test_ttl_override_zero_always_misses(self, monkeypatch, tmp_path):
        # --cache-ttl 0 must always go to network, even though a fresh entry's
        # age can be exactly 0.0 (0.0 > 0 is False — the bypass guards it).
        self._enable(monkeypatch, tmp_path)
        calls = self._net_counter(monkeypatch)
        monkeypatch.setattr(steam_cli._CACHE, "ttl_override", 0)
        steam_cli.http_get("http://x", cache_ttl=10_000)
        steam_cli.http_get("http://x", cache_ttl=10_000)
        assert calls["n"] == 2

    def test_write_failure_does_not_break_request(self, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        self._net_counter(monkeypatch, b"OK")
        monkeypatch.setattr(steam_cli, "_atomic_write",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        # caching is best-effort: a write error must not propagate
        assert steam_cli.http_get("http://x", cache_ttl=100) == b"OK"


# --------------------------------------------------------------------------- #
# cache: per-endpoint TTL routing                                              #
# --------------------------------------------------------------------------- #

class TestCacheRouting:
    def test_players_bypasses_cache(self, stub_json):
        stub = stub_json({"response": {"result": 1, "player_count": 5}})
        steam_cli.cmd_players(make_args(["players", "570", "--json", "-q"]))
        assert stub.calls[0].cache_ttl == 0

    def test_appdetails_uses_default_ttl(self, stub_json):
        stub = stub_json(appdetails(1, {"name": "G"}))
        steam_cli.cmd_info(make_args(["info", "1", "--json", "-q"]))
        assert stub.calls[0].cache_ttl == steam_cli.DEFAULT_TTL

    def test_reviews_pagination_bypasses_cache(self, stub_json):
        stub = stub_json(review_page([review(1)], cursor="*"))
        steam_cli.cmd_reviews(make_args(["reviews", "570", "-n", "1", "--json", "-q"]))
        assert stub.calls[0].cache_ttl == 0

    def test_main_no_cache_flag_disables(self, monkeypatch):
        monkeypatch.setattr(steam_cli._CACHE, "enabled", True)
        monkeypatch.setattr(steam_cli, "http_json",
                            lambda *a, **k: {"response": {"result": 1, "player_count": 1}})
        steam_cli.main(["players", "570", "--no-cache", "--json"])
        assert steam_cli._CACHE.enabled is False


# --------------------------------------------------------------------------- #
# command: cache                                                               #
# --------------------------------------------------------------------------- #

class TestCacheCommand:
    def test_path_prints_only_dir(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(tmp_path / "c"))
        steam_cli.cmd_cache(make_args(["cache", "--path"]))
        assert capsys.readouterr().out.strip() == str(tmp_path / "c")

    def test_clear_removes_files(self, monkeypatch, tmp_path, capsys):
        d = tmp_path / "c"
        d.mkdir()
        (d / "a.body").write_bytes(b"x")
        (d / "a.meta").write_text("{}")
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(d))
        steam_cli.cmd_cache(make_args(["cache", "--clear"]))
        assert list(d.iterdir()) == []
        assert "cleared 2" in capsys.readouterr().out

    def test_default_shows_stats(self, monkeypatch, tmp_path, capsys):
        d = tmp_path / "c"
        d.mkdir()
        (d / "a.body").write_bytes(b"xx")
        (d / "a.meta").write_text("{}")
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(d))
        steam_cli.cmd_cache(make_args(["cache"]))
        out = capsys.readouterr().out
        assert "cache dir:" in out
        assert "entries:   1" in out          # 2 files = 1 entry

    def test_clear_on_missing_dir_is_safe(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(tmp_path / "nope"))
        steam_cli.cmd_cache(make_args(["cache", "--clear"]))
        assert "cleared 0" in capsys.readouterr().out

    def test_clear_spares_non_cache_files(self, monkeypatch, tmp_path, capsys):
        # if STEAM_CLI_CACHE_DIR points at a shared dir, --clear must not nuke
        # unrelated files — only *.body / *.meta / *.tmp.
        d = tmp_path / "shared"
        d.mkdir()
        (d / "a.body").write_bytes(b"x")
        (d / "a.meta").write_text("{}")
        (d / "important.txt").write_text("keep me")
        monkeypatch.setattr(steam_cli._CACHE, "dir", str(d))
        steam_cli.cmd_cache(make_args(["cache", "--clear"]))
        assert "cleared 2" in capsys.readouterr().out
        assert (d / "important.txt").exists()


# --------------------------------------------------------------------------- #
# locale normalization                                                         #
# --------------------------------------------------------------------------- #

class TestNormalizeLanguage:
    def test_iso_to_steam_name(self):
        assert steam_cli.normalize_language("ru") == "russian"
        assert steam_cli.normalize_language("ko") == "koreana"
        assert steam_cli.normalize_language("zh") == "schinese"
        assert steam_cli.normalize_language("zh-tw") == "tchinese"
        assert steam_cli.normalize_language("pt-br") == "brazilian"
        assert steam_cli.normalize_language("es-419") == "latam"

    def test_already_canonical_passthrough(self):
        assert steam_cli.normalize_language("english") == "english"
        assert steam_cli.normalize_language("koreana") == "koreana"

    def test_case_and_underscore_insensitive(self):
        assert steam_cli.normalize_language("EN_US") == "english"
        assert steam_cli.normalize_language("ZH-Hans") == "schinese"

    def test_all_is_passthrough(self):
        assert steam_cli.normalize_language("all") == "all"

    def test_region_falls_back_to_base(self):
        assert steam_cli.normalize_language("en-GB") == "english"

    def test_none_passthrough(self):
        assert steam_cli.normalize_language(None) is None

    def test_unknown_raises_with_hint(self):
        with pytest.raises(steam_cli.SteamError, match='did you mean "german"') as ei:
            steam_cli.normalize_language("germ")
        assert ei.value.code == "invalid"

    def test_unknown_without_hint_still_errors(self):
        with pytest.raises(steam_cli.SteamError, match="unrecognized language"):
            steam_cli.normalize_language("qwxz")


class TestNormalizeCountry:
    def test_alpha3_to_alpha2(self):
        assert steam_cli.normalize_country("usa") == "us"
        assert steam_cli.normalize_country("deu") == "de"
        assert steam_cli.normalize_country("bra") == "br"

    def test_common_alias_uk_to_gb(self):
        assert steam_cli.normalize_country("uk") == "gb"

    def test_case_insensitive(self):
        assert steam_cli.normalize_country("DE") == "de"

    def test_valid_alpha2_passthrough(self):
        assert steam_cli.normalize_country("br") == "br"

    def test_none_passthrough(self):
        assert steam_cli.normalize_country(None) is None

    def test_unknown_raises(self):
        with pytest.raises(steam_cli.SteamError, match="unrecognized country") as ei:
            steam_cli.normalize_country("zz")
        assert ei.value.code == "invalid"


class TestNormalizeIntegration:
    def test_info_sends_steam_language_name(self, stub_json):
        stub = stub_json(appdetails(1, {"name": "G"}))
        steam_cli.cmd_info(make_args(["info", "1", "--lang", "ru", "--json", "-q"]))
        # the appdetails request must carry "russian", not the raw "ru"
        assert stub.calls[-1].params["l"] == "russian"

    def test_price_normalizes_each_region(self, stub_json):
        def respond(url, params):
            return appdetails(1, {"name": "G",
                              "price_overview": {"final_formatted": "x"}})
        stub = stub_json(respond)
        steam_cli.cmd_price(make_args(["price", "1", "--cc", "usa,uk", "--json", "-q"]))
        ccs = [c.params["cc"] for c in stub.calls]
        assert "us" in ccs and "gb" in ccs        # usa->us, uk->gb

    def test_price_invalid_region_errors_whole_call(self, stub_json):
        stub_json(appdetails(1, {"name": "G"}))
        with pytest.raises(steam_cli.SteamError, match="unrecognized country"):
            steam_cli.cmd_price(make_args(["price", "1", "--cc", "us,zz", "--json", "-q"]))

    def test_reviews_normalizes_language(self, stub_json):
        stub = stub_json(review_page([review(1)], cursor="*"))
        steam_cli.cmd_reviews(make_args(["reviews", "1", "-n", "1",
                                         "--language", "ko", "--json", "-q"]))
        rev_call = [c for c in stub.calls if "appreviews" in c.url][0]
        assert rev_call.params["language"] == "koreana"

    def test_search_normalizes_lang_and_cc(self, stub_json):
        stub = stub_json({"items": []})
        steam_cli.cmd_search(make_args(["search", "x", "--lang", "ru",
                                        "--cc", "usa", "--json"]))
        assert stub.calls[0].params["l"] == "russian"
        assert stub.calls[0].params["cc"] == "us"

    def test_overview_normalizes_cc(self, stub_json):
        def respond(url, params):
            if "appdetails" in url:
                return appdetails(1, {"name": "G", "is_free": True})
            if "GetNumberOfCurrentPlayers" in url:
                return {"response": {"result": 1, "player_count": 1}}
            return {"success": 1, "query_summary": {}}
        stub = stub_json(respond)
        steam_cli.cmd_overview(make_args(["overview", "1", "--cc", "usa", "--json", "-q"]))
        ad_call = [c for c in stub.calls if "appdetails" in c.url][0]
        assert ad_call.params["cc"] == "us"

    def test_natural_english_names(self):
        assert steam_cli.normalize_language("chinese") == "schinese"
        assert steam_cli.normalize_language("korean") == "koreana"
        assert steam_cli.normalize_language("Traditional-Chinese") == "tchinese"
