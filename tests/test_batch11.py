"""Batch 11 tests: live odds auto-pull (explicit only) + merge + key safety."""
import datetime as dt
import json
import os

from hrplaybook import odds_api, odds_keys, value_center
from hrplaybook.config import Config

SECRET = "SUPERSECRETKEY123"


class _DummyClient:
    """Stand-in Client: serves canned events/odds, records if used."""
    def __init__(self, events=None, event_odds=None):
        self.force_refresh = False
        self._events = events or []
        self._eo = event_odds or {}
        self.calls = 0

    def get_json(self, ns, url, params=None):
        self.calls += 1
        return self._events if url.endswith("/events") else self._eo

    def close(self):
        pass


def _event_odds():
    return {"id": "evt1", "bookmakers": [
        {"key": "draftkings", "title": "DraftKings", "markets": [
            {"key": "batter_home_runs", "outcomes": [
                {"name": "Over", "point": 0.5, "price": 150, "description": "Aaron Judge"}]},
            {"key": "batter_total_bases", "outcomes": [
                {"name": "Over", "point": 1.5, "price": -120, "description": "Aaron Judge"},
                {"name": "Over", "point": 2.5, "price": 320, "description": "Aaron Judge"}]}]},
        {"key": "fanduel", "title": "FanDuel", "markets": [
            {"key": "batter_home_runs", "outcomes": [
                {"name": "Over", "point": 0.5, "price": 175, "description": "Aaron Judge"}]}]}]}


def test_market_map_and_reverse():
    assert odds_api.MARKETS["HR"][0] == "batter_home_runs"
    assert odds_api._KEY_TO_BET["batter_total_bases"][0] == "TB"


def test_parse_event_rows_per_book_and_canonical_line():
    rows = odds_api.parse_event_rows(_event_odds(), {"batter_home_runs", "batter_total_bases"},
                                     {"aaron judge": 99}, game="X @ Y")
    hr = [r for r in rows if r["bet_type"] == "HR"]
    tb = [r for r in rows if r["bet_type"] == "TB"]
    assert len(hr) == 2 and {r["sportsbook"] for r in hr} == {"DraftKings", "FanDuel"}
    assert all(r["batter_id"] == 99 and r["source"] == "api" for r in rows)
    # the 2.5 TB outcome is NOT pulled (canonical 1.5 only -> no line merge)
    assert len(tb) == 1 and tb[0]["odds"] == -120 and tb[0]["line"] == "2+ TB"


def test_pull_no_key(monkeypatch):
    monkeypatch.setattr(odds_keys, "active_key", lambda t: None)
    monkeypatch.setattr(odds_keys, "check_keys", lambda t: [])
    c = _DummyClient()
    res = odds_api.pull(c, Config(), "2026-06-08", {})
    assert res["ok"] is False and res["error"] == "no_key"
    assert res["records_saved"] == 0 and c.calls == 0      # no network attempted


def test_pull_quota_exhausted(monkeypatch):
    monkeypatch.setattr(odds_keys, "active_key", lambda t: None)
    monkeypatch.setattr(odds_keys, "check_keys",
                        lambda t: [{"env": "ODDS_API_KEY_1", "valid": False, "error": "http_429"}])
    res = odds_api.pull(_DummyClient(), Config(), "2026-06-08", {})
    assert res["error"] == "quota_exhausted"


def test_pull_dry_run_no_save_no_key_leak(monkeypatch, tmp_path):
    monkeypatch.setattr(odds_keys, "active_key", lambda t: ("ODDS_API_KEY_1", SECRET))
    monkeypatch.setattr(odds_api, "live_key_tester", lambda: (lambda k: (True, 42, None)))
    res = odds_api.pull(_DummyClient(), Config(), "2026-06-08", {}, dry_run=True, out_root=tmp_path)
    assert res["ok"] and res["dry_run"] and res["quota_remaining"] == 42
    assert not (tmp_path / "2026-06-08" / "api_odds.json").exists()
    assert SECRET not in json.dumps(res)                   # key never returned


def test_pull_full_saves_and_hides_key(monkeypatch, tmp_path):
    monkeypatch.setattr(odds_keys, "active_key", lambda t: ("ODDS_API_KEY_1", SECRET))
    monkeypatch.setattr(odds_api, "live_key_tester", lambda: (lambda k: (True, 40, None)))
    c = _DummyClient(events=[{"id": "evt1", "commence_time": "2026-06-08T22:00:00Z",
                              "home_team": "Y", "away_team": "X"}],
                     event_odds=_event_odds())
    res = odds_api.pull(c, Config(), "2026-06-08", {"aaron judge": 99}, out_root=tmp_path)
    assert res["ok"] and res["records_saved"] == 3
    assert set(res["books"]) == {"DraftKings", "FanDuel"}
    saved = json.loads((tmp_path / "2026-06-08" / "api_odds.json").read_text())
    assert len(saved) == 3 and all(r["source"] == "api" for r in saved)
    assert SECRET not in json.dumps(res) and SECRET not in json.dumps(saved)


def test_load_roundtrip(tmp_path):
    assert odds_api.load("2026-06-08", tmp_path) == []     # missing -> [] (no network)
    d = tmp_path / "2026-06-08"; d.mkdir(parents=True)
    (d / "api_odds.json").write_text(json.dumps([{"player": "A", "source": "api"}]))
    assert odds_api.load("2026-06-08", tmp_path)[0]["player"] == "A"


def test_manual_and_api_merge_best_price():
    manual = [{"player": "Aaron Judge", "player_norm": "aaron judge", "bet_type": "TB",
               "sportsbook": "DK", "odds": -120, "source": "manual",
               "timestamp": dt.datetime.now().isoformat()}]
    api = [{"player": "Aaron Judge", "bet_type": "TB", "sportsbook": "FanDuel",
            "odds": 150, "source": "api", "timestamp": dt.datetime.now().isoformat()}]
    idx = value_center.build_odds_index(manual, api)
    entries = idx[("aaron judge", "TB")]
    assert len(entries) == 2
    best = value_center._best_price(entries)
    assert best["odds"] == 150 and best["source"] == "api"   # +150 lower implied than -120


def test_market_vs_model_uses_best_and_calibrated():
    m = {"batter": "Aaron Judge", "team": "NYY", "opp_team": "BOS", "model_tb_prob": 0.55,
         "bets": "TB:2+ TB", "env_score": 4, "pitcher_score": 5}
    manual = [{"player": "Aaron Judge", "player_norm": "aaron judge", "bet_type": "TB",
               "sportsbook": "DK", "odds": -120, "source": "manual",
               "timestamp": dt.datetime.now().isoformat()}]
    api = [{"player": "Aaron Judge", "bet_type": "TB", "sportsbook": "FanDuel",
            "odds": 150, "source": "api", "timestamp": dt.datetime.now().isoformat()}]
    tables = {"TB": {"_baseline": 0.36, "_n": 1000,
                     "buckets": {"50+": {"n": 600, "avg_raw": 0.57, "actual": 0.42}}}}
    res = value_center.market_vs_model([m], manual, api=api, tables=tables)
    row = next(r for r in res["rows"] if r["bet_type"] == "TB")
    assert row["source"] == "api" and row["odds"] == 150
    assert row["model_prob"] != row["raw_model_prob"]       # calibrated drives value
    assert len(row["all_prices"]) == 2                      # sportsbook comparison


def test_api_odds_stale_warning():
    old = (dt.datetime.now() - dt.timedelta(minutes=45)).isoformat()
    assert value_center.staleness(old, "api") == "red"       # >30m api = stale
    recent = (dt.datetime.now() - dt.timedelta(minutes=5)).isoformat()
    assert value_center.staleness(recent, "api") == "ok"
    assert value_center.staleness((dt.datetime.now() - dt.timedelta(minutes=20)).isoformat(),
                                  "api") == "yellow"


def test_auto_pull_default_false():
    assert Config().odds.auto_pull is False


def test_status_never_exposes_raw_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY_1", SECRET)
    st = odds_keys.status(lambda k: (True, 99, None))
    assert SECRET not in json.dumps(st)
    assert st["active_key_name"] == "ODDS_API_KEY_1" and st["connected"]
