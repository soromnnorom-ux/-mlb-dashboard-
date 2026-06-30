"""Config.odds_api_key() must resolve the numbered odds keys (ODDS_API_KEY_1/2/3),
not just the legacy singular ODDS_API_KEY -- otherwise the pipeline / auto-pull
path silently gets no key on a deploy that only sets ODDS_API_KEY_1.

Uses dummy values only; never a real key.
"""
from hrplaybook.config import Config

_ALL = ["ODDS_API_KEY", "ODDS_API_KEY_1", "ODDS_API_KEY_2", "ODDS_API_KEY_3"]


def _clear(monkeypatch):
    for n in _ALL:
        monkeypatch.delenv(n, raising=False)


def test_resolves_numbered_key(monkeypatch):
    _clear(monkeypatch)
    cfg = Config()
    cfg.odds.provider = "the-odds-api"
    assert cfg.odds_api_key() is None                 # nothing set yet
    monkeypatch.setenv("ODDS_API_KEY_1", "dummy-1")
    assert cfg.odds_api_key() == "dummy-1"            # numbered key now found


def test_numbered_takes_priority_over_legacy(monkeypatch):
    _clear(monkeypatch)
    cfg = Config()
    cfg.odds.provider = "the-odds-api"
    monkeypatch.setenv("ODDS_API_KEY", "legacy")
    monkeypatch.setenv("ODDS_API_KEY_1", "numbered")
    assert cfg.odds_api_key() == "numbered"


def test_legacy_still_works(monkeypatch):
    _clear(monkeypatch)
    cfg = Config()
    cfg.odds.provider = "the-odds-api"
    monkeypatch.setenv("ODDS_API_KEY", "legacy-only")
    assert cfg.odds_api_key() == "legacy-only"


def test_disabled_without_provider(monkeypatch):
    _clear(monkeypatch)
    cfg = Config()
    cfg.odds.provider = ""                            # provider off
    monkeypatch.setenv("ODDS_API_KEY_1", "dummy-1")
    assert cfg.odds_api_key() is None                 # no key when provider unset


def test_blank_values_skipped(monkeypatch):
    _clear(monkeypatch)
    cfg = Config()
    cfg.odds.provider = "the-odds-api"
    monkeypatch.setenv("ODDS_API_KEY_1", "   ")       # whitespace-only ignored
    monkeypatch.setenv("ODDS_API_KEY_2", "real-2")
    assert cfg.odds_api_key() == "real-2"
