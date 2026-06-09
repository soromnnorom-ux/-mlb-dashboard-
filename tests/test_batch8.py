"""Batch 8 tests: multi-season split, weighted profiles, trend, pitch-mix change."""
from hrplaybook import seasons
from hrplaybook.model.schemas import Batter, Pitcher


def _ball(date, ev, la=25, bb="fly_ball"):
    return {"game_date": date, "launch_speed": ev, "launch_angle": la, "bb_type": bb}


def test_window_metrics_filters_by_days():
    balls = [_ball("2026-06-07", 105), _ball("2026-06-01", 100),
             _ball("2026-05-01", 80)]   # older than 14d
    w = seasons.window_metrics(balls, "2026-06-08", 14)
    assert w["n"] == 2 and w["max_ev"] == 105.0
    assert w["hardhit_pct"] == 100.0    # both >=95
    w30 = seasons.window_metrics(balls, "2026-06-08", 40)
    assert w30["n"] == 3


def test_window_metrics_empty():
    assert seasons.window_metrics([], "2026-06-08", 30)["n"] == 0


def test_blend_drops_none_and_renormalizes():
    v = seasons.blend({"l30": 10.0, "s2026": None, "s2025": 20.0}, (0.5, 0.3, 0.2))
    # weights 0.5 & 0.2 -> (10*.5 + 20*.2)/.7
    assert v == round((10 * 0.5 + 20 * 0.2) / 0.7, 2)


def test_weighted_profile_small_sample_shifts_to_baseline():
    s2025 = {"barrel_pct": 9.0, "avg_ev": 89.0, "hardhit_pct": 40.0}
    cur = {"barrel_pct": 18.0, "avg_ev": 93.0, "hardhit_pct": 50.0}
    l30 = {"n": 3, "barrel_pct": 20.0, "ev": 94.0, "hardhit_pct": 55.0}
    wp = seasons.weighted_profile(s2025, cur, l30, bet="HR", pa_2026=20)
    assert "SMALL_SAMPLE_2026_BATTER" in wp["warnings"]
    assert "LOW_RECENT_SAMPLE" in wp["warnings"]      # n=3 < 10
    # 2025 weight should have grown beyond the nominal 0.25
    assert wp["weights"]["s2025"] > 0.25


def test_batter_trend_improving_power():
    t = seasons.batter_trend({"barrel_pct": 9.2, "avg_ev": 89.0},
                             {"barrel_pct": 14.8, "avg_ev": 91.5},
                             {"n": 30, "ev": 92.0}, pa_2026=300)
    assert t["label"] == "IMPROVING_POWER_PROFILE"
    assert any("barrel" in r for r in t["reasons"]) and t["grade"] in ("A", "B")


def test_batter_trend_small_sample_label():
    t = seasons.batter_trend({"barrel_pct": 9.0}, {"barrel_pct": 20.0},
                             {"n": 5}, pa_2026=20)
    assert t["label"] == "SMALL_SAMPLE"


def test_batter_trend_missing_2025_is_safe():
    t = seasons.batter_trend(None, {"barrel_pct": 12.0, "avg_ev": 90.0}, {"n": 0})
    assert t["grade"] in ("A", "B", "C", "D") and isinstance(t["reasons"], list)


def test_pitch_mix_change_flagged():
    ch = seasons.pitch_mix_change({"FF": 55, "SL": 18}, {"FF": 42, "SL": 31})
    assert ch["changed"] is True
    assert any("SL" in f for f in ch["flags"])
    assert abs(ch["deltas"]["SL"] - 13.0) < 0.01


def test_pitch_mix_change_no_2025():
    ch = seasons.pitch_mix_change(None, {"FF": 50})
    assert ch["changed"] is False and ch["warning"] == "NO_2025_ARSENAL"


def test_pitcher_trend_regression_risk():
    t = seasons.pitcher_trend({"hr9": 1.0, "barrel_pct_allowed": 7, "k_pct": 24},
                              {"hr9": 1.6, "barrel_pct_allowed": 10, "k_pct": 20},
                              {"changed": False}, ip_2026=90)
    assert t["label"] == "PITCHER_REGRESSION_RISK" and t["more_attackable_2026"] is True


def test_baseline_extractors():
    b = Batter(player_id=1, name="A", barrel_pct=12.0, avg_ev=91.0, slg=0.5)
    assert seasons.batter_baseline(b)["barrel_pct"] == 12.0
    assert seasons.batter_baseline(None) is None
    p = Pitcher(player_id=2, name="P", hr9=1.4, k_pct=22.0)
    assert seasons.pitcher_baseline(p)["hr9"] == 1.4
