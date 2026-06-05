"""Shared test fixtures and helpers.

Tests run fully offline: the network is always mocked. Real raw Steam API
responses live in tests/fixtures/ (captured once from the live API) so the
tests assert against the actual response contract, not invented fields.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the single-file module importable without installing the package.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Parse a captured raw Steam response from tests/fixtures/<name>.json."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_fixture_bytes(name: str) -> bytes:
    """Raw bytes of a captured response (for the HTTP layer tests)."""
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def fixture():
    return load_fixture


@pytest.fixture
def fixture_bytes():
    return load_fixture_bytes


@pytest.fixture(autouse=True)
def reset_http_state(monkeypatch, tmp_path):
    """Each test starts from a clean HTTP backend and cache state.

    `_curl_fallback` is a module global mutated by http_get on a TLS fallback,
    and `_HTTP_MODE` is read from the environment at import time. Reset both via
    monkeypatch so they auto-revert even if a test raises or runs under xdist.

    Also neutralise the retry backoff: tests are offline, so the real
    `time.sleep` between retries would only slow the suite. A test that wants
    to assert on backoff timing patches `steam_cli.time.sleep` itself.

    The on-disk cache is disabled by default and pointed at a per-test tmp dir,
    so command tests neither read a developer's real cache nor write to disk.
    Cache tests opt in by setting `_CACHE.enabled = True`.
    """
    import steam_cli
    monkeypatch.setattr(steam_cli, "_curl_fallback", False)
    monkeypatch.setattr(steam_cli, "_HTTP_MODE", "auto")
    monkeypatch.setattr(steam_cli.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(steam_cli._CACHE, "enabled", False)
    monkeypatch.setattr(steam_cli._CACHE, "dir", str(tmp_path / "cache"))
    monkeypatch.setattr(steam_cli._CACHE, "ttl_override", None)
    monkeypatch.setattr(steam_cli._CACHE, "force_refresh", False)
