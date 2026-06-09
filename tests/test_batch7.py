"""Batch 7 tests: calibration coverage + local snapshot backfill."""
import csv
import json

from hrplaybook import backfill_snapshots as bf
from hrplaybook import calibration


# ---- coverage status ------------------------------------------------------
def test_coverage_statuses():
    tables = {
        "TB": {"_baseline": 0.36, "_n": 1000, "buckets": {
            "50+": {"n": 600, "avg_raw": 0.57, "actual": 0.42},
            "40-50": {"n": 400, "avg_raw": 0.45, "actual": 0.36}}},
        "HRR": {"_baseline": 0.40, "_n": 300, "buckets": {
            "20-30": {"n": 250, "avg_raw": 0.27, "actual": 0.24},
            "30-40": {"n": 50, "avg_raw": 0.36, "actual": 0.30}}},
        "Hits": {"_baseline": 0.60, "_n": 50, "buckets": {
            "50+": {"n": 50, "avg_raw": 0.65, "actual": 0.60}}},
    }
    cov = calibration.coverage(tables)
    assert cov["TB"]["status"] == "CALIBRATED"
    assert cov["HRR"]["status"] == "PARTIAL"
    assert cov["Hits"]["status"] == "RAW_FALLBACK"
    assert cov["HR"]["status"] == "NO_DATA"


# ---- backfill -------------------------------------------------------------
def _mrow(bid):
    return {"batter_id": str(bid), "batter": "A", "team": "NYY", "opp_team": "BOS",
            "opp_sp": "X", "model_hr_prob": "0.10", "model_tb_prob": "0.50",
            "env_score": "4", "env_tier": "good", "pitcher_score": "5",
            "barrel_vs_pm": "14", "barrel_pct": "12", "hardhit_pct": "46",
            "avg_ev": "91", "order": "2", "platoon": "fav", "l30_avg": "0.30",
            "opp_bullpen_hr9": "1.2", "tags": "HOT_CONTACT", "cluster_label": "HOT",
            "missed_hr": "True", "lineup_state": "confirmed"}


def _setup_date(tmp_path, date, picks, mrows):
    d = tmp_path / date
    d.mkdir(parents=True)
    (d / "picks.json").write_text(json.dumps(picks))
    cols = list(_mrow(1).keys())
    with (d / "matchups.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in mrows:
            w.writerow(r)
    return d


def test_backfill_adds_all_market_probs_and_backs_up(tmp_path):
    picks = [{"date": "2026-06-01", "batter_id": 1, "batter": "A", "team": "NYY",
              "bets": {"HR": "HR", "TB": "2+ TB", "HRR": "1.5 HRR", "Hits": "1+ Hits"},
              "odds": {}, "value": {}, "model_prob": {"HR": 0.10, "TB": 0.50}}]
    d = _setup_date(tmp_path, "2026-06-01", picks, [_mrow(1)])
    res = bf.backfill(tmp_path)
    assert res["picks_enriched"] == 1 and res["dates_enriched"] == 1
    out = json.loads((d / "picks.json").read_text())
    mp = out[0]["model_prob"]
    assert "HRR" in mp and "Hits" in mp           # newly reconstructed
    assert mp["HR"] == 0.10 and mp["TB"] == 0.50  # originals preserved
    assert (d / "picks.json.bak").exists()        # backup created
    assert out[0]["backfilled"] is True


def test_backfill_does_not_invent_when_no_matchup_row(tmp_path):
    picks = [{"date": "2026-06-01", "batter_id": 9, "batter": "Ghost", "team": "X",
              "bets": {"HR": "HR"}, "odds": {}, "value": {}, "model_prob": {"HR": 0.1}}]
    d = _setup_date(tmp_path, "2026-06-01", picks, [_mrow(1)])  # row for id 1, not 9
    res = bf.backfill(tmp_path)
    out = json.loads((d / "picks.json").read_text()) if (d / "picks.json.bak").exists() else picks
    # nothing reconstructed for the ghost -> not counted as enriched
    assert res["picks_enriched"] == 0
    assert "no_matchup_row" in res["reasons"]


def test_backfill_default_is_local_only(tmp_path, monkeypatch):
    # if anything tried to hit the network, importing httpx.get would be used;
    # assert local backfill runs to completion with no network call.
    import hrplaybook.http as http
    monkeypatch.setattr(http.Client, "_raw_get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    picks = [{"date": "2026-06-01", "batter_id": 1, "batter": "A", "team": "NYY",
              "bets": {"TB": "2+ TB"}, "odds": {}, "value": {}, "model_prob": {"TB": 0.5}}]
    _setup_date(tmp_path, "2026-06-01", picks, [_mrow(1)])
    res = bf.backfill(tmp_path, network=False)     # must not raise
    assert res["scanned"] == 1


def test_calibration_sample_increases_after_backfill(tmp_path):
    # picks with HRR raw prob + a ledger HRR result -> HRR calibration sample > 0
    picks = [{"date": "2026-06-01", "batter_id": i, "batter": f"P{i}", "team": "NYY",
              "bets": {"HRR": "1.5 HRR"}, "odds": {}, "value": {},
              "model_prob": {"TB": 0.5}} for i in range(5)]
    _setup_date(tmp_path, "2026-06-01", picks, [_mrow(i) for i in range(5)])
    # ledger with HRR results
    fields = ["date", "batter_id", "batter", "team", "bet", "line", "tier",
              "stat", "need", "got", "won", "odds", "profit"]
    with (tmp_path / "_ledger.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i in range(5):
            w.writerow({"date": "2026-06-01", "batter_id": i, "batter": f"P{i}",
                        "team": "NYY", "bet": "HRR", "line": "1.5 HRR", "tier": 1,
                        "stat": "hrr", "need": 2, "got": 2, "won": True,
                        "odds": "", "profit": ""})
    before = calibration.build_tables(calibration.collect_rows(tmp_path))
    assert "HRR" not in before                      # no HRR raw prob yet
    bf.backfill(tmp_path)
    after = calibration.build_tables(calibration.collect_rows(tmp_path))
    assert "HRR" in after and after["HRR"]["_n"] == 5   # now calibratable
