"""Tests for the added features: platoon, value model, odds parsing, grading,
bullpen aggregation, hand-aware enrichment."""
from __future__ import annotations

from hrplaybook.config import Config
from hrplaybook.grade import grade_picks, parse_line, summarize
from hrplaybook.model.enrich import bullpen_hr9, enrich_batter
from hrplaybook.model.schemas import Batter, Game, Matchup, Park, Pitcher
from hrplaybook.score.edges import determine_platoon
from hrplaybook.score.value import apply_value, model_hr_prob, model_tb2_prob
from hrplaybook.sources.odds import parse_event_odds

CFG = Config()


def _game():
    return Game(game_pk=1, date="2025-07-19", game_time_utc=None, venue_id=1,
                venue_name="X", home_team="TOR", away_team="BOS",
                park=Park(team="TOR", park_name="X", lat=0, lon=0,
                          orientation_deg=0, roof="open", hr_factor=1.1))


def _matchup(barrel_vs_pm=12.0, hr9=1.5, order=2, bats="L", throws="R"):
    b = Batter(player_id=10, name="Test Bat", bats=bats, batting_order=order,
               barrel_vs_pm=barrel_vs_pm, barrel_pct=10.0, slg=0.480, xslg=0.470)
    p = Pitcher(player_id=20, name="Test SP", throws=throws, hr9=hr9)
    m = Matchup(batter=b, pitcher=p, game=_game(), side="away", opp_team="TOR")
    m.platoon = determine_platoon(bats, throws)
    return m


# --- platoon ---------------------------------------------------------------
def test_platoon():
    assert determine_platoon("L", "R") == "fav"
    assert determine_platoon("R", "L") == "fav"
    assert determine_platoon("S", "R") == "fav"
    assert determine_platoon("R", "R") == "unfav"
    assert determine_platoon("L", "L") == "unfav"
    assert determine_platoon(None, "R") == "neutral"


# --- value model -----------------------------------------------------------
def test_model_prob_monotonic():
    lo = model_hr_prob(_matchup(barrel_vs_pm=5, hr9=0.8), CFG)
    hi = model_hr_prob(_matchup(barrel_vs_pm=20, hr9=1.8), CFG)
    assert 0 < lo < hi < 0.5
    # TB prob is a real probability
    assert 0 < model_tb2_prob(_matchup(), CFG) < 1


def test_apply_value_plus_ev():
    m = _matchup(barrel_vs_pm=22, hr9=1.9, order=1)
    # generous +600 price on a batter the model likes -> +EV
    apply_value([m], {"HR": {10: 600}, "TB": {}}, CFG)
    assert m.value == "+EV"
    assert m.ev_by_bet["HR"] > 0
    # stingy price -> -EV
    m2 = _matchup(barrel_vs_pm=22, hr9=1.9, order=1)
    apply_value([m2], {"HR": {10: -300}, "TB": {}}, CFG)
    assert m2.value == "-EV"


def test_apply_value_no_odds_still_sets_model():
    m = _matchup()
    apply_value([m], {"HR": {}, "TB": {}}, CFG)
    assert m.value == "unknown"
    assert m.prob_by_bet["HR"] is not None


# --- odds parsing ----------------------------------------------------------
def test_parse_event_odds():
    event = {"bookmakers": [{"key": "dk", "markets": [
        {"key": "batter_home_runs", "outcomes": [
            {"name": "Over", "description": "Aaron Judge", "price": 250, "point": 0.5},
            {"name": "Under", "description": "Aaron Judge", "price": -350, "point": 0.5},
        ]},
        {"key": "batter_total_bases", "outcomes": [
            {"name": "Over", "description": "Aaron Judge", "price": 120, "point": 1.5},
        ]},
    ]}]}
    idx = {"aaron judge": 99}
    hr = parse_event_odds(event, "batter_home_runs", 0.5, idx)
    tb = parse_event_odds(event, "batter_total_bases", 1.5, idx)
    assert hr == {99: 250}
    assert tb == {99: 120}


def test_parse_event_odds_best_price():
    event = {"bookmakers": [
        {"key": "a", "markets": [{"key": "batter_home_runs", "outcomes": [
            {"name": "Over", "description": "X Y", "price": 200, "point": 0.5}]}]},
        {"key": "b", "markets": [{"key": "batter_home_runs", "outcomes": [
            {"name": "Over", "description": "X Y", "price": 260, "point": 0.5}]}]},
    ]}
    out = parse_event_odds(event, "batter_home_runs", 0.5, {"x y": 1})
    assert out == {1: 260}  # best (highest) price kept


# --- grading ---------------------------------------------------------------
def test_parse_line():
    assert parse_line("2+ TB") == ("tb", 2)
    assert parse_line("1.5 TB") == ("tb", 2)
    assert parse_line("HR") == ("hr", 1)
    assert parse_line("2.5 HRR") == ("hrr", 3)
    assert parse_line("1+ Hits") == ("h", 1)


def test_grade_and_summarize():
    picks = [
        {"date": "2025-07-19", "batter_id": 1, "batter": "A", "team": "TOR",
         "bets": {"HR": "HR", "TB": "2+ TB"}, "odds": {"HR": 300}},
        {"date": "2025-07-19", "batter_id": 2, "batter": "B", "team": "BOS",
         "bets": {"HR": "HR"}, "odds": {}},
        {"date": "2025-07-19", "batter_id": 3, "batter": "C", "team": "NYY",
         "bets": {"HR": "HR"}, "odds": {}},  # DNP -> void
    ]
    results = {
        1: {"hr": 1, "tb": 4, "h": 1, "rbi": 2, "r": 1, "ab": 4},  # HR win, TB win
        2: {"hr": 0, "tb": 1, "h": 1, "rbi": 0, "r": 0, "ab": 4},  # HR loss
    }
    rows = grade_picks(picks, results)
    by = {(r["batter_id"], r["bet"]): r for r in rows}
    assert by[(1, "HR")]["won"] is True
    assert by[(1, "TB")]["won"] is True
    assert by[(2, "HR")]["won"] is False
    assert by[(3, "HR")]["won"] is None      # void
    s = summarize(rows)
    assert s["HR"]["w"] == 1 and s["HR"]["l"] == 1 and s["HR"]["void"] == 1
    # ROI only from the priced HR bet (+300 win): profit 3 on 1 unit
    assert s["HR"]["roi"] == 3.0


# --- bullpen ---------------------------------------------------------------
def test_bullpen_hr9():
    pool = {
        100: Pitcher(player_id=100, name="RP1", ip=50.0, hr=10, gs=0),   # reliever
        101: Pitcher(player_id=101, name="RP2", ip=40.0, hr=5, gs=0),    # reliever
        102: Pitcher(player_id=102, name="SP1", ip=180.0, hr=25, gs=30),  # starter (excluded)
    }
    # (10+5)/(50+40)*9 = 1.5
    assert bullpen_hr9([100, 101, 102], pool, CFG) == 1.5
    assert bullpen_hr9([102], pool, CFG) is None  # only a starter -> no bullpen data


# --- hand-aware enrichment -------------------------------------------------
def test_enrich_hand_aware_barrel():
    b = Batter(player_id=5, name="Slugger", barrel_pct=8.0)
    p = Pitcher(player_id=6, name="RHP", throws="R", arsenal={"FF": 60.0})
    # 10 batted balls vs RHP on FF: 5 barrels (EV/LA in the barrel zone), 5 weak
    bb = []
    for _ in range(5):
        bb.append({"game_date": "2025-07-18", "launch_speed": 104.0, "launch_angle": 28.0,
                   "hit_distance_sc": 400, "events": "home_run", "pitch_type": "FF",
                   "p_throws": "R", "stand": "R"})
    for _ in range(5):
        bb.append({"game_date": "2025-07-18", "launch_speed": 80.0, "launch_angle": 5.0,
                   "hit_distance_sc": 100, "events": "field_out", "pitch_type": "FF",
                   "p_throws": "R", "stand": "R"})
    enrich_batter(b, bb, [], p, CFG)
    assert b.bats == "R"                       # inferred from `stand`
    assert b.barrel_vs_pm == 50.0              # 5/10 on FF vs RHP
    assert b.barrel_vs_hand == 50.0
