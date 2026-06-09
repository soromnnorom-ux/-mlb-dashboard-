"""Batch 3 tests: weather, pitcher attack, missed-HR, contact cluster, grades."""
from hrplaybook import featured
from hrplaybook.config import load_config
from hrplaybook.model.enrich import enrich_batter
from hrplaybook.model.schemas import Batter

CFG = load_config()


# ---- Phase 6 weather ------------------------------------------------------
def test_weather_hot_wind_out_scores_high():
    g = {"matchup": "A@B", "temp_f": 88, "wind_mph": 14, "wind_out": "out",
         "park_hr_factor": 1.10, "roof": "open"}
    w = featured.weather_scores(g)
    assert w["hr"] >= 80 and w["grade"] in ("A", "A+")
    assert any("out" in r.lower() for r in w["reasons"])


def test_weather_cold_wind_in_scores_low():
    g = {"matchup": "A@B", "temp_f": 52, "wind_mph": 14, "wind_out": "in",
         "park_hr_factor": 0.92, "roof": "open"}
    w = featured.weather_scores(g)
    assert w["hr"] <= 35


def test_weather_dome_is_neutral_on_wind_and_temp():
    g = {"matchup": "A@B", "temp_f": 95, "wind_mph": 20, "wind_out": "out",
         "park_hr_factor": 1.0, "roof": "closed"}
    w = featured.weather_scores(g)
    assert w["wind_label"] == "dome"
    assert 45 <= w["hr"] <= 55          # park-neutral dome ~ 50, weather ignored


def test_weather_unknown_wind_not_overstated():
    g = {"matchup": "A@B", "temp_f": 78, "wind_mph": 18, "wind_out": None,
         "park_hr_factor": 1.0, "roof": "open"}
    w = featured.weather_scores(g)
    assert w["wind_label"] == "unknown"
    # warm temp boost only, no wind boost
    assert w["hr"] == featured.weather_scores(
        {**g, "wind_mph": 0})["hr"]


# ---- Phase 7 pitcher attack ----------------------------------------------
def test_pitcher_attack_ranks_weak_high():
    weak = {"name": "Batting Practice", "hr9": 1.8, "hrfb_pct": 19,
            "barrel_pct_allowed": 11, "hardhit_pct_allowed": 45, "avg_ev_allowed": 91,
            "k_pct": 15, "whiff_pct": 19, "fastball_usage": 64, "fb_pct": 38}
    a = featured.pitcher_attack(weak)
    assert a["attack"] >= 65 and a["grade"] in ("A", "A+", "B")
    assert any("HR/9" in r for r in a["reasons"])


def test_pending_blowup_tag():
    p = {"name": "Lucky", "hr9": 0.7, "barrel_pct_allowed": 10,
         "hardhit_pct_allowed": 44, "avg_ev_allowed": 91, "k_pct": 21}
    a = featured.pitcher_attack(p)
    assert a["pending_blowup"] is True
    assert any("BLOWUP" in r for r in a["reasons"])


def test_pitcher_attack_table_filters_to_probables():
    pitchers = [{"name": "Today SP", "hr9": 1.6, "barrel_pct_allowed": 9, "k_pct": 17},
                {"name": "Bench SP", "hr9": 2.0}]
    games = [{"matchup": "A@B", "away_sp": "X", "home_sp": "Today SP"}]
    t = featured.pitcher_attack_table(pitchers, games)
    assert [r["name"] for r in t["all"]] == ["Today SP"]


# ---- Phase 9 missed HR ----------------------------------------------------
def test_missed_hr_candidate_grade():
    m = {"batter": "Crusher", "team": "X", "opp_team": "Y", "opp_sp": "Z",
         "missed_hr": True, "missed_hr_ev": 107, "missed_hr_dist": 405,
         "missed_hr_la": 27, "missed_hr_pitch": "FF", "missed_hr_date": "2026-06-07"}
    c = featured.missed_hr_candidates([m])
    assert c and c[0]["grade"] == "Extreme" and c[0]["detail"] is True


def test_missed_hr_flagged_without_detail_is_moderate():
    m = {"batter": "X", "tags": "MISSED_HR", "opp_sp": "Z", "opp_team": "Y"}
    c = featured.missed_hr_candidates([m])
    assert c and c[0]["grade"] == "Moderate" and c[0]["detail"] is False


def test_missed_hr_empty_when_none():
    assert featured.missed_hr_candidates([{"batter": "X", "tags": ""}]) == []


# ---- Phase 10 contact cluster --------------------------------------------
def test_contact_cluster_listing():
    m = {"batter": "Hot", "team": "X", "cluster_label": "NUCLEAR",
         "cluster_score": 80, "ev95_w": 12, "ev100_w": 6, "ev105_w": 3}
    c = featured.contact_clusters([m])
    assert c and c[0]["label"] == "NUCLEAR" and c[0]["ev100"] == 6


# ---- per-market slate grades ---------------------------------------------
def test_no_strong_hr_play_caps_hr_grade():
    weak = {"batter": "w", "bets": "TB:1.5 TB", "env_tier": "neutral",
            "env_score": 0, "pitcher_score": 0, "barrel_vs_pm": 4, "order": 8,
            "barrel_pct": 3, "platoon": "neutral"}
    grades = featured.slate_grades([weak])
    assert grades["HR"] != "A+"


# ---- enrich capture (offline, synthetic batted balls) --------------------
def _bb(date, ev, la, dist, events="field_out", pt="FF"):
    return {"game_date": date, "pitch_type": pt, "events": events,
            "launch_speed": ev, "launch_angle": la, "hit_distance_sc": dist,
            "bb_type": "fly_ball", "stand": "R", "p_throws": "R"}


def test_enrich_captures_missed_hr_and_cluster():
    b = Batter(player_id=1, name="Test", barrel_pct=10)
    balls = [
        _bb("2026-06-07", 104, 28, 392),     # missed HR (in park)
        _bb("2026-06-07", 101, 22, 350),
        _bb("2026-06-06", 106, 30, 410, events="home_run"),
        _bb("2026-06-06", 100, 25, 360),
        _bb("2026-06-05", 99, 18, 300),
    ]
    enrich_batter(b, balls, [], None, CFG)
    assert b.missed_hr is True
    assert b.missed_hr_ev == 104 and b.missed_hr_dist == 392
    assert b.ev100_w >= 3 and b.ev105_w >= 1
    assert b.cluster_label in ("HOT", "NUCLEAR")
    assert "MULTIPLE_100_EV" in b.tags
