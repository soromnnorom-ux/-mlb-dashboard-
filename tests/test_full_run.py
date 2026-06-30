"""Full-slate Run: the odds step (_run_odds) is non-fatal and returns a SAFE
status dict (env-var NAME only, never a raw key). Offline — odds_api.pull and
odds_keys.has_any_key are monkeypatched, so no network and no real key."""
import hrplaybook.odds_api as odds_api
import hrplaybook.odds_keys as odds_keys
from hrplaybook.config import Config
from hrplaybook.model.schemas import Batter, Game, Matchup, Pitcher
from hrplaybook.web import app as webapp


def _mus():
    g = Game(game_pk=1, date="2026-06-30", game_time_utc=None, venue_id=1,
             venue_name="X", home_team="COL", away_team="LAD")
    return [Matchup(batter=Batter(player_id=10, name="Test Bat"),
                    pitcher=Pitcher(player_id=20, name="SP"),
                    game=g, side="away", opp_team="COL")]


def _cfg(provider="the-odds-api"):
    c = Config()
    c.odds.provider = provider
    return c


def test_skipped_when_no_odds_opt():
    r = webapp._run_odds(None, _cfg(), "2026-06-30", _mus(), {"no_odds": True})
    assert r["status"] == "skipped"


def test_no_provider():
    r = webapp._run_odds(None, _cfg(provider=""), "2026-06-30", _mus(), {})
    assert r["status"] == "no-provider"


def test_no_key(monkeypatch):
    monkeypatch.setattr(odds_keys, "has_any_key", lambda: False)
    r = webapp._run_odds(None, _cfg(), "2026-06-30", _mus(), {})
    assert r["status"] == "no-key" and "ODDS_API_KEY_1" in r["message"]


def test_ok(monkeypatch):
    monkeypatch.setattr(odds_keys, "has_any_key", lambda: True)
    monkeypatch.setattr(odds_api, "pull", lambda *a, **k: {
        "ok": True, "records_saved": 3, "books": ["DK", "FD"],
        "active_key_name": "ODDS_API_KEY_1"})
    r = webapp._run_odds("client", _cfg(), "2026-06-30", _mus(), {})
    assert r["status"] == "ok"
    assert r["records_saved"] == 3 and "3 odds rows" in r["message"]
    assert r["active_key_name"] == "ODDS_API_KEY_1"


def test_failed_quota(monkeypatch):
    monkeypatch.setattr(odds_keys, "has_any_key", lambda: True)
    monkeypatch.setattr(odds_api, "pull", lambda *a, **k: {"ok": False, "error": "quota_exhausted"})
    r = webapp._run_odds("client", _cfg(), "2026-06-30", _mus(), {})
    assert r["status"] == "failed" and "429" in r["message"]


def test_failed_bad_key(monkeypatch):
    monkeypatch.setattr(odds_keys, "has_any_key", lambda: True)
    monkeypatch.setattr(odds_api, "pull", lambda *a, **k: {"ok": False, "error": "no_valid_key"})
    r = webapp._run_odds("client", _cfg(), "2026-06-30", _mus(), {})
    assert r["status"] == "failed" and "ODDS_API_KEY_1" in r["message"]


def test_pull_exception_is_nonfatal(monkeypatch):
    monkeypatch.setattr(odds_keys, "has_any_key", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(odds_api, "pull", boom)
    r = webapp._run_odds("client", _cfg(), "2026-06-30", _mus(), {})
    assert r["status"] == "failed" and "error" in r["message"].lower()


def test_status_never_contains_raw_key(monkeypatch):
    """Sanity: the returned dict exposes only the env-var NAME, never a value."""
    monkeypatch.setattr(odds_keys, "has_any_key", lambda: True)
    monkeypatch.setattr(odds_api, "pull", lambda *a, **k: {
        "ok": True, "records_saved": 1, "books": ["DK"], "active_key_name": "ODDS_API_KEY_1"})
    r = webapp._run_odds("client", _cfg(), "2026-06-30", _mus(), {})
    blob = str(r)
    assert "ODDS_API_KEY_1" in blob          # the NAME is fine
    assert "secret" not in blob.lower()      # no raw value leaked
