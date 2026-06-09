"""Batch 6 tests: empirical probability calibration (raw preserved)."""
import datetime as dt

from hrplaybook import calibration, value_center


def test_bucket_assignment():
    assert calibration.bucket_of(0.05) == "0-10"
    assert calibration.bucket_of(0.57) == "50+"
    assert calibration.bucket_of(0.30) == "30-40"
    assert calibration.bucket_of(None) is None


def test_build_tables_actual_hit_rate():
    rows = [{"bet": "TB", "model_prob": 0.55, "won": i < 42} for i in range(100)]
    t = calibration.build_tables(rows)
    assert t["TB"]["buckets"]["50+"]["n"] == 100
    assert t["TB"]["buckets"]["50+"]["actual"] == 0.42
    assert t["TB"]["_baseline"] == 0.42


def _tables(n, actual, baseline=0.36):
    return {"TB": {"_baseline": baseline, "_n": n,
                   "buckets": {"50+": {"n": n, "avg_raw": 0.57, "actual": actual}}}}


def test_calibrate_large_sample_blend():
    c = calibration.calibrate(0.57, "TB", _tables(1000, 0.42))
    assert c["raw"] == 0.57                                   # raw preserved
    assert c["calibrated"] == round(0.8 * 0.42 + 0.2 * 0.57, 4)  # 0.45
    assert c["calibrated"] < 0.57 and c["confidence"] == "high"


def test_calibrate_medium_sample_blend():
    c = calibration.calibrate(0.57, "TB", _tables(200, 0.42))
    assert c["calibrated"] == round(0.6 * 0.42 + 0.4 * 0.57, 4)
    assert c["confidence"] == "medium"


def test_calibrate_low_sample_shrinks_to_baseline():
    c = calibration.calibrate(0.57, "TB", _tables(50, 0.42, baseline=0.36))
    assert c["calibrated"] == round(0.3 * 0.42 + 0.7 * 0.36, 4)
    assert c["warning"] == "LOW_SAMPLE_CALIBRATION"


def test_calibrate_no_data_falls_back_to_raw():
    c = calibration.calibrate(0.57, "HRR", {})
    assert c["calibrated"] == 0.57 and c["warning"] == "NO_CALIBRATION_DATA"


def test_overconfident_bucket_warning():
    c = calibration.calibrate(0.57, "TB", _tables(1000, 0.42))
    assert c["warning"] == "OVERCONFIDENT_BUCKET"


def test_market_vs_model_uses_calibrated_probability():
    m = {"batter": "A", "team": "NYY", "opp_team": "BOS", "opp_sp": "X",
         "model_tb_prob": 0.57, "bets": "TB:2+ TB", "env_tier": "good",
         "env_score": 4, "pitcher_score": 5, "barrel_vs_pm": 14, "barrel_pct": 12,
         "order": 2, "platoon": "fav"}
    manual = [{"player": "A", "player_norm": "a", "bet_type": "TB",
               "sportsbook": "DK", "odds": 100, "source": "manual",
               "timestamp": dt.datetime.now().isoformat()}]   # implied .50
    res = value_center.market_vs_model([m], manual, tables=_tables(1000, 0.42))
    row = next(r for r in res["rows"] if r["bet_type"] == "TB")
    assert row["raw_model_prob"] == 0.57
    assert row["model_prob"] == 0.45                 # calibrated drives value
    assert row["edge"] == round(0.45 - 0.50, 4)      # edge uses calibrated, not raw
    assert row["calibration_warning"] == "OVERCONFIDENT_BUCKET"


def test_calibration_status_improvement():
    rows = [{"bet": "TB", "model_prob": 0.55, "won": i < 42} for i in range(100)]
    tables = calibration.build_tables(rows)
    st = calibration.calibration_status(rows, tables)
    tb = next(s for s in st if s["bet"] == "TB")
    # calibrated avg should be closer to actual than raw avg
    assert tb["calibrated_error"] <= tb["raw_error"]
    assert tb["improvement"] >= 0
