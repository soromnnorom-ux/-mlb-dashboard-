"""Batch 10 tests: Batter-vs-Pitcher (supporting signal only)."""
from hrplaybook import bvp, featured


def _pa(ev, la, e, date="2025-05-01"):
    return {"events": e, "launch_speed": ev, "launch_angle": la, "game_date": date,
            "hit_distance_sc": (410 if e == "home_run" else 300), "bb_type": "fly_ball",
            "pitch_type": "FF", "balls": 1, "strikes": 2, "inning": 3,
            "release_speed": 93.0, "description": "hit_into_play"}


def _power_history():
    rows = [_pa(105, 28, "home_run"), _pa(105, 28, "home_run"), _pa(101, 20, "double")]
    rows += [_pa(96, 10, "single")] * 3
    rows += [_pa(98, 30, "field_out")]
    rows += [_pa(None, None, "strikeout")] * 4
    rows += [_pa(None, None, "walk")]
    return rows


def test_aggregate_statline():
    s = bvp.aggregate(_power_history())
    assert s["pa"] == 12 and s["ab"] == 11 and s["hits"] == 6
    assert s["hr"] == 2 and s["k"] == 4 and s["bb"] == 1 and s["tb"] == 13
    assert abs(s["slg"] - 13 / 11) < 0.01
    assert s["hardhit"] == 7 and s["ev100"] == 3 and s["barrels"] >= 2
    assert s["max_ev"] == 105.0 and s["sample_size"] == "USEFUL"


def test_sample_and_confidence_labels():
    assert bvp.sample_label(1) == "TOO_SMALL"
    assert bvp.sample_label(5) == "SMALL"
    assert bvp.sample_label(12) == "USEFUL"
    assert bvp.sample_label(20) == "STRONG"
    assert bvp.confidence("STRONG") == "higher"


def test_grade_elite_history():
    g = bvp.grade(bvp.aggregate(_power_history()))
    assert g["grade"] in ("A", "A+") and g["edge_label"] == "ELITE_HISTORY"
    assert "BVP_GOOD_HISTORY" in g["tags"] and "BVP_HARD_CONTACT" in g["tags"]


def test_grade_strikeout_risk():
    rows = [_pa(None, None, "strikeout")] * 5 + [_pa(80, 5, "field_out")] * 5
    g = bvp.grade(bvp.aggregate(rows))
    assert "BVP_STRIKEOUT_RISK" in g["tags"]
    assert g["edge_label"] == "BAD_HISTORY" and g["grade"] == "D"


def test_too_small_sample_contributes_nothing():
    rows = [_pa(108, 28, "home_run"), _pa(105, 26, "double")]   # 2 PA, 2 XBH
    s = bvp.aggregate(rows)
    g = bvp.grade(s)
    assert s["sample_size"] == "TOO_SMALL"
    assert g["grade"] == "TOO_SMALL" and "BVP_TOO_SMALL" in g["tags"]
    assert bvp.adjustment(s, g, "HR") == 0.0          # no bonus despite 2 HR-ish


def test_max_weight_caps():
    assert bvp.max_weight("HR", "STRONG") == 0.05
    assert bvp.max_weight("Hits", "USEFUL") == 0.10
    assert bvp.max_weight("HR", "SMALL") == 0.025
    assert bvp.max_weight("HR", "TOO_SMALL") == 0.0


def test_adjustment_capped_and_signed():
    s = bvp.aggregate(_power_history()); g = bvp.grade(s)
    adj = bvp.adjustment(s, g, "HR")
    assert 0 < adj <= bvp.MAX_WEIGHT["HR"]            # positive, never exceeds cap
    # bad history -> negative, capped
    rows = [_pa(None, None, "strikeout")] * 5 + [_pa(80, 5, "field_out")] * 5
    s2 = bvp.aggregate(rows); g2 = bvp.grade(s2)
    assert -bvp.MAX_WEIGHT["TB"] <= bvp.adjustment(s2, g2, "TB") < 0


def test_build_handles_missing():
    assert bvp.build(None) is None
    assert bvp.build([]) is None


def test_bvp_board_sections():
    matchups = [
        {"batter_id": 1, "batter": "Slug", "team": "NYY", "opp_sp": "X", "opp_team": "BOS",
         "bvp_pa": 12, "bvp_avg": 0.5, "bvp_slg": 1.0, "bvp_hr": 2, "bvp_k": 2,
         "bvp_max_ev": 108, "bvp_barrels": 3, "bvp_grade": "A+",
         "bvp_sample_size": "USEFUL", "bvp_confidence": "medium",
         "bvp_edge_label": "ELITE_HISTORY", "bvp_reasons": "2 HR in 12 PA",
         "tags": "BVP_GOOD_HISTORY|BVP_HARD_CONTACT"},
        {"batter_id": 2, "batter": "Tiny", "team": "SF", "opp_sp": "Y", "opp_team": "SD",
         "bvp_pa": 2, "bvp_avg": 0.5, "bvp_slg": 0.5, "bvp_hr": 0, "bvp_k": 1,
         "bvp_grade": "TOO_SMALL", "bvp_sample_size": "TOO_SMALL",
         "bvp_edge_label": "TOO_SMALL_SAMPLE", "bvp_reasons": "Only 2 PA",
         "tags": "BVP_TOO_SMALL"},
    ]
    board = featured.bvp_board(matchups)
    assert [r["batter"] for r in board["power"]] == ["Slug"]
    assert [r["batter"] for r in board["too_small"]] == ["Tiny"]
    assert board["count"] == 2
