"""Phase 2 + 16 tests: freshness meta, slate validation, cache bypass, glossary."""
import json
from pathlib import Path

from hrplaybook.cache import DiskCache
from hrplaybook.config import load_config
from hrplaybook.freshness import build_meta, validate_slate
from hrplaybook.http import Client
from hrplaybook.model.schemas import Batter, Game, Matchup, Weather

CFG = load_config()


def _game(**kw):
    g = Game(game_pk=1, date="2026-06-08", game_time_utc=None, venue_id=1,
             venue_name="Park", home_team="TOR", away_team="BOS",
             home_pitcher_id=111, away_pitcher_id=222,
             weather=Weather(temp_f=80, source="statsapi"))
    for k, v in kw.items():
        setattr(g, k, v)
    return g


def _matchup(state="confirmed", pulled=True):
    b = Batter(player_id=5, name="Tester", lineup_state=state)
    b.recent_window_used = pulled
    return Matchup(batter=b, pitcher=None, game=_game(), side="home", opp_team="BOS")


def test_build_meta_sections_green_when_complete():
    meta = build_meta("2026-06-08", [_game()], [_matchup()], [], CFG)
    s = meta["sections"]
    assert s["schedule"]["state"] == "green"
    assert s["lineups"]["state"] == "green"      # all confirmed
    assert s["weather"]["state"] == "green"      # temp present
    assert s["pitchers"]["state"] == "green"     # both probables
    assert s["statcast"]["state"] == "green"     # pulled
    assert meta["stale_minutes"]["lineups"] == 15


def test_build_meta_flags_projected_and_missing():
    g = _game(away_pitcher_id=None)              # missing a probable
    meta = build_meta("2026-06-08", [g],
                      [_matchup(state="projected", pulled=False)], [], CFG)
    assert meta["sections"]["lineups"]["state"] == "yellow"
    assert meta["sections"]["pitchers"]["state"] == "yellow"
    assert meta["sections"]["statcast"]["state"] == "yellow"


def test_validate_fail_when_no_games():
    r = validate_slate({}, [], [])
    assert r["overall"] == "FAIL"


def test_validate_pass_paths():
    meta = build_meta("2026-06-08", [_game()], [_matchup()], [], CFG)
    games = [{"home_sp": "A", "away_sp": "B"}]
    r = validate_slate(meta, games, [{}])
    names = {c["name"]: c["status"] for c in r["checks"]}
    assert names["Schedule"] == "PASS"
    assert names["Probable pitchers"] == "PASS"
    assert names["Lineups"] == "PASS"
    assert names["Odds"] == "WARNING"           # no odds -> warning, not fail
    assert r["overall"] in ("PASS", "WARNING")


def test_validate_postponed_fails():
    g = _game(status="Postponed")
    meta = build_meta("2026-06-08", [g], [_matchup()], [], CFG)
    r = validate_slate(meta, [{"home_sp": "A", "away_sp": "B"}], [{}])
    assert r["overall"] == "FAIL"
    assert any(c["name"] == "Game status" and c["status"] == "FAIL" for c in r["checks"])


def test_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    cache = DiskCache(tmp_path, {"savant": 9999})
    cache.set("savant", "http://x", "OLD")
    # normal client serves cache
    c1 = Client(cache, rate_limit_per_sec=0)
    assert c1.get_text("savant", "http://x") == "OLD"
    # force_refresh client skips the cache read and hits the network
    c2 = Client(cache, rate_limit_per_sec=0, force_refresh=True)
    monkeypatch.setattr(c2, "_raw_get", lambda url, params: "FRESH")
    assert c2.get_text("savant", "http://x") == "FRESH"


def test_glossary_present_and_has_core_terms():
    p = Path("hrplaybook/web/static/glossary.json")
    data = json.loads(p.read_text())
    abbrs = {t["abbr"] for g in data["groups"] for t in g["terms"]}
    for need in ("B%PM", "ENV", "HR%", "TB%", "VAL", "SPΔ"):
        assert need in abbrs, f"glossary missing {need}"
    # every term has the required explainer fields
    for g in data["groups"]:
        for t in g["terms"]:
            assert t["meaning"] and "name" in t
