"""Batch 12 cleanup: BvP sample guardrails + in-play-only contact counts."""
from hrplaybook import bvp


def _pa(ev=None, la=None, bb="", events="field_out"):
    return {"events": events, "launch_speed": ev, "launch_angle": la,
            "bb_type": bb, "game_date": "2026-06-01"}


def test_too_small_grade_and_zero_contribution():
    rows = [_pa(105, 28, "fly_ball", "home_run"), _pa(events="strikeout")]  # 2 PA
    rec = bvp.build(rows)
    assert rec["sample_size"] == "TOO_SMALL"
    assert rec["grade"] == "TOO_SMALL"
    assert bvp.adjustment(rec, rec, "HR") == 0.0
    assert bvp.max_weight("HR", "TOO_SMALL") == 0.0


def test_small_sample_grade_capped_at_b():
    # 5 PA with elite power -> would be A/A+, must cap to B with flags
    rows = ([_pa(106, 28, "fly_ball", "home_run") for _ in range(2)]
            + [_pa(102, 20, "line_drive", "double")]
            + [_pa(events="field_out") for _ in range(2)])
    rec = bvp.build(rows)
    assert rec["sample_size"] == "SMALL"
    assert rec["grade"] == "B"            # capped
    assert rec["grade_capped"] is True and rec["small_sample"] is True
    assert rec["edge_label"] in ("ELITE_HISTORY", "GOOD_HISTORY")  # label preserved


def test_confidence_labels():
    assert bvp.confidence("TOO_SMALL") == "very low"
    assert bvp.confidence("SMALL") == "low"
    assert bvp.confidence("USEFUL") == "medium"
    assert bvp.confidence("STRONG") == "higher"


def test_sample_labels():
    assert bvp.sample_label(2) == "TOO_SMALL"
    assert bvp.sample_label(5) == "SMALL"
    assert bvp.sample_label(12) == "USEFUL"
    assert bvp.sample_label(20) == "STRONG"


def test_fouls_excluded_from_contact_counts():
    # 11 PA worth of outs + many 100+ EV FOULS (bb_type empty) -> ev100 must be 0
    rows = [_pa(events="field_out", ev=85, la=10, bb="ground_ball") for _ in range(11)]
    rows += [_pa(events="", ev=104, la=15, bb="") for _ in range(12)]  # fouls
    rec = bvp.build(rows)
    assert rec["ev100"] == 0           # fouls not counted as balls in play
    assert rec["hardhit"] == 0
    # contact reason should never claim more 100+ balls than makes sense
    assert not any("100+" in r for r in rec["reasons"])


def test_inplay_hardhit_counted():
    rows = [_pa(101, 12, "line_drive", "single") for _ in range(4)] + \
           [_pa(events="field_out") for _ in range(8)]   # 12 PA, USEFUL
    rec = bvp.build(rows)
    assert rec["sample_size"] == "USEFUL"
    assert rec["ev100"] == 4 and rec["hardhit"] == 4
