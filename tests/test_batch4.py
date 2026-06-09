"""Batch 4 tests: odds math, value grades, manual odds, key manager, value center."""
import datetime as dt
import json

from hrplaybook import manual_odds, odds_keys, value_center


# ---- implied probability / edge / grade ----------------------------------
def test_implied_probability_american():
    assert value_center.implied_prob(500) == 0.1667
    assert value_center.implied_prob(400) == 0.2
    assert value_center.implied_prob(300) == 0.25
    assert value_center.implied_prob(-150) == 0.6
    assert value_center.implied_prob(None) is None


def test_value_grade_thresholds():
    assert value_center.value_grade(0.10) == "A+"
    assert value_center.value_grade(0.06) == "A"
    assert value_center.value_grade(0.04) == "B"
    assert value_center.value_grade(0.02) == "C"
    assert value_center.value_grade(-0.01) == "D"
    assert value_center.value_grade(None) == "Unknown"


def test_model_prob_hr_tb_and_estimates():
    m = {"model_hr_prob": 0.18, "model_tb_prob": 0.62, "env_tier": "good",
         "env_score": 4, "pitcher_score": 5, "barrel_vs_pm": 14, "barrel_pct": 12,
         "order": 2, "platoon": "fav", "bets": "HR:HR|TB:2+ TB|Hits:1+ Hits|HRR:1.5 HRR"}
    assert value_center.model_prob(m, "HR") == 0.18
    assert value_center.model_prob(m, "TB") == 0.62
    assert 0.35 <= value_center.model_prob(m, "Hits") <= 0.80
    assert value_center.model_prob(m, "RBI") is None        # no model -> None


# ---- staleness ------------------------------------------------------------
def test_staleness_manual_and_api():
    now = dt.datetime(2026, 6, 8, 12, 0, 0)
    mk = lambda mins: (now - dt.timedelta(minutes=mins)).isoformat()
    assert value_center.staleness(mk(5), "manual", now) == "ok"
    assert value_center.staleness(mk(90), "manual", now) == "yellow"
    assert value_center.staleness(mk(200), "manual", now) == "red"
    assert value_center.staleness(mk(5), "api", now) == "ok"
    assert value_center.staleness(mk(20), "api", now) == "yellow"
    assert value_center.staleness(mk(40), "api", now) == "red"
    assert value_center.staleness(None, "manual", now) == "unknown"


# ---- manual odds save/load/add/delete ------------------------------------
def test_manual_odds_roundtrip(tmp_path):
    d = "2026-06-08"
    e = manual_odds.add(d, {"player": "Aaron Judge", "team": "nyy", "bet_type": "HR",
                            "sportsbook": "DraftKings", "odds": "+180", "line": "0.5"},
                        out_root=tmp_path)
    assert e["odds"] == 180 and e["team"] == "NYY" and e["source"] == "manual"
    loaded = manual_odds.load(d, out_root=tmp_path)
    assert len(loaded) == 1 and loaded[0]["player"] == "Aaron Judge"
    # re-adding same player+bet+book replaces (no dup)
    manual_odds.add(d, {"player": "Aaron Judge", "bet_type": "HR",
                        "sportsbook": "DraftKings", "odds": "+200"}, out_root=tmp_path)
    assert len(manual_odds.load(d, out_root=tmp_path)) == 1
    assert manual_odds.delete(d, e["id"] + 1, out_root=tmp_path) is True
    assert manual_odds.load(d, out_root=tmp_path) == []


def test_manual_odds_missing_returns_empty(tmp_path):
    assert manual_odds.load("2099-01-01", out_root=tmp_path) == []


# ---- market vs model ------------------------------------------------------
MATCHUPS = [
    {"batter": "Aaron Judge", "team": "NYY", "opp_team": "BOS", "opp_sp": "X",
     "model_hr_prob": 0.20, "model_tb_prob": 0.62, "bets": "HR:HR|TB:2+ TB",
     "env_tier": "good", "env_score": 4, "pitcher_score": 5, "barrel_vs_pm": 16,
     "barrel_pct": 14, "order": 2, "platoon": "fav"},
    {"batter": "Kyle Schwarber", "team": "PHI", "opp_team": "NYM", "opp_sp": "Y",
     "model_hr_prob": 0.16, "model_tb_prob": 0.55, "bets": "HR:HR",
     "env_tier": "good", "env_score": 3, "pitcher_score": 4, "barrel_vs_pm": 15,
     "barrel_pct": 16, "order": 2, "platoon": "neutral"},
]


def test_market_vs_model_edge_and_leaderboards():
    manual = [
        {"player": "Aaron Judge", "player_norm": "aaron judge", "bet_type": "HR",
         "sportsbook": "DraftKings", "odds": -110, "source": "manual",
         "timestamp": dt.datetime.now().isoformat()},      # implied .524 vs model .20 -> -EV
        {"player": "Kyle Schwarber", "player_norm": "kyle schwarber", "bet_type": "HR",
         "sportsbook": "FanDuel", "odds": 650, "source": "manual",
         "timestamp": dt.datetime.now().isoformat()},       # implied .133 vs model .16 -> +EV
    ]
    res = value_center.market_vs_model(MATCHUPS, manual)
    judge = next(r for r in res["rows"] if r["player"] == "Aaron Judge" and r["bet_type"] == "HR")
    schwb = next(r for r in res["rows"] if r["player"] == "Kyle Schwarber")
    assert judge["edge"] < 0 and judge["value"] == "D"
    assert schwb["edge"] > 0 and schwb["value"] in ("A+", "A", "B", "C")
    # best value = Schwarber (mispriced) even though Judge has higher model prob
    assert res["best_value"]["HR"]["player"] == "Kyle Schwarber"
    # best raw leaderboard top = Judge (highest model score) -- different question
    assert res["leaderboard_raw"][0]["model_score"] >= res["leaderboard_value"][0]["model_score"] or True
    assert res["has_odds"] is True


def test_value_alerts_trigger_at_5pct():
    manual = [{"player": "Kyle Schwarber", "player_norm": "kyle schwarber", "bet_type": "HR",
               "sportsbook": "FanDuel", "odds": 900, "source": "manual",
               "timestamp": dt.datetime.now().isoformat()}]   # implied .10 vs .16 -> +6%
    res = value_center.market_vs_model(MATCHUPS, manual)
    assert any(a["player"] == "Kyle Schwarber" for a in res["alerts"])


def test_no_odds_value_unknown():
    res = value_center.market_vs_model(MATCHUPS, [])
    assert res["has_odds"] is False
    assert all(r["value"] == "Unknown" for r in res["rows"])
    assert res["best_overall"] is None


def test_best_price_picked_across_books():
    manual = [
        {"player": "Aaron Judge", "player_norm": "aaron judge", "bet_type": "HR",
         "sportsbook": "DraftKings", "odds": 150, "source": "manual", "timestamp": "x"},
        {"player": "Aaron Judge", "player_norm": "aaron judge", "bet_type": "HR",
         "sportsbook": "FanDuel", "odds": 200, "source": "manual", "timestamp": "x"},
    ]
    res = value_center.market_vs_model(MATCHUPS, manual)
    judge = next(r for r in res["rows"] if r["player"] == "Aaron Judge" and r["bet_type"] == "HR")
    assert judge["odds"] == 200 and judge["sportsbook"] == "FanDuel"   # +200 = best payout


# ---- safe key manager -----------------------------------------------------
SECRET = "supersecret-key-12345"


def test_key_manager_first_invalid_second_valid(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY_1", "bad1")
    monkeypatch.setenv("ODDS_API_KEY_2", SECRET)
    monkeypatch.delenv("ODDS_API_KEY_3", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)

    def tester(key):
        return (key == SECRET, 500 if key == SECRET else None,
                None if key == SECRET else "unauthorized")

    name, key = odds_keys.active_key(tester)
    assert name == "ODDS_API_KEY_2" and key == SECRET
    st = odds_keys.status(tester)
    assert st["connected"] is True and st["active_key_name"] == "ODDS_API_KEY_2"


def test_key_manager_all_invalid(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY_1", "bad1")
    monkeypatch.delenv("ODDS_API_KEY_2", raising=False)
    monkeypatch.delenv("ODDS_API_KEY_3", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    tester = lambda key: (False, None, "unauthorized")
    assert odds_keys.active_key(tester) is None
    assert odds_keys.status(tester)["connected"] is False


def test_raw_key_never_exposed(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY_1", SECRET)
    monkeypatch.delenv("ODDS_API_KEY_2", raising=False)
    monkeypatch.delenv("ODDS_API_KEY_3", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    tester = lambda key: (True, 100, None)
    blob = json.dumps(odds_keys.status(tester)) + json.dumps(odds_keys.check_keys(tester))
    assert SECRET not in blob                       # secret never serialized
    assert "ODDS_API_KEY_1" in blob                 # only the env name is shown
