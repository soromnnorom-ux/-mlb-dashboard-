"""Batch 5 (Model Performance) tests: join, ROI, breakdowns, calibration."""
import csv
import json

from hrplaybook import performance as perf


def _setup(tmp_path, picks_by_date, ledger_rows):
    for date, picks in picks_by_date.items():
        d = tmp_path / date
        d.mkdir(parents=True, exist_ok=True)
        (d / "picks.json").write_text(json.dumps(picks))
    fields = ["date", "batter_id", "batter", "team", "bet", "line", "tier",
              "stat", "need", "got", "won", "odds", "profit"]
    with (tmp_path / "_ledger.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in ledger_rows:
            w.writerow({k: r.get(k) for k in fields})


def _pick(bid, name, **ctx):
    base = {"date": "2026-06-01", "batter_id": bid, "batter": name, "team": "NYY",
            "opp_team": "BOS", "opp_sp": "SP", "tier": 1, "play_score": 10,
            "platoon": "fav", "bets": {"HR": "HR"}, "odds": {}, "model_prob": {},
            "value": {}, "env_tier": "good", "env_score": 4, "pitcher_score": 5,
            "barrel_vs_pm": 16, "barrel_pct": 14, "hardhit_pct": 46, "avg_ev": 91,
            "l30_avg": 0.30, "order": 2, "opp_bullpen_hr9": 1.2,
            "cluster_label": "HOT", "missed_hr": True, "lineup_state": "confirmed",
            "tags": ["HOT_CONTACT", "MISSED_HR"]}
    base.update(ctx)
    return base


def _led(bid, name, bet, won, odds=None, profit=None, date="2026-06-01"):
    return {"date": date, "batter_id": bid, "batter": name, "team": "NYY",
            "bet": bet, "line": ("HR" if bet == "HR" else "2+ TB"), "tier": 1,
            "stat": bet.lower(), "need": 1, "got": 1 if won else 0,
            "won": won, "odds": odds, "profit": profit}


def test_collect_joins_context_and_computes_edge(tmp_path):
    picks = [_pick(1, "A", model_prob={"HR": 0.20}, odds={"HR": 200})]
    _setup(tmp_path, {"2026-06-01": picks},
           [_led(1, "A", "HR", True, odds=200, profit=2.0)])
    rows = perf.collect(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["model_prob"] == 0.20 and r["implied_prob"] == 0.3333
    assert r["edge"] == round(0.20 - 0.3333, 4)
    assert r["rich"] is True and "HOT_CONTACT" in r["tags"]
    assert r["grade"] is not None        # market_scores computed from rich ctx


def test_record_roi_and_hit_rate():
    rows = [{"won": True, "profit": 2.0, "odds": 200, "model_prob": .2, "edge": .05},
            {"won": False, "profit": -1.0, "odds": 200, "model_prob": .2, "edge": .05},
            {"won": True, "profit": None, "odds": None, "model_prob": None, "edge": None}]
    rec = perf.record(rows)
    assert rec["w"] == 2 and rec["l"] == 1 and rec["n"] == 3
    assert rec["hit_rate"] == round(2 / 3, 3)
    assert rec["staked"] == 2 and rec["roi"] == round((2.0 - 1.0) / 2, 3)


def test_negative_roi_is_shown_not_hidden(tmp_path):
    led = [_led(i, f"P{i}", "HR", False, odds=-110, profit=-1.0) for i in range(3)]
    picks = [_pick(i, f"P{i}", model_prob={"HR": 0.1}, odds={"HR": -110}) for i in range(3)]
    _setup(tmp_path, {"2026-06-01": picks}, led)
    rec = perf.record(perf.collect(tmp_path))
    assert rec["roi"] is not None and rec["roi"] < 0       # losses surfaced


def test_by_signal_and_pitchmix_bucket(tmp_path):
    picks, led = [], []
    for i in range(12):
        picks.append(_pick(i, f"P{i}", model_prob={"HR": 0.2}, odds={"HR": 150},
                           barrel_vs_pm=17))
        led.append(_led(i, f"P{i}", "HR", i % 2 == 0, odds=150,
                        profit=1.5 if i % 2 == 0 else -1.0))
    _setup(tmp_path, {"2026-06-01": picks}, led)
    rows = perf.collect(tmp_path)
    sig = perf.by_signal(rows)
    assert "HOT_CONTACT" in sig and sig["HOT_CONTACT"]["n"] == 12
    assert "PITCH_MIX_EDGE" in sig
    pm = perf.by_pitchmix_bucket(rows)
    assert pm["15-20%"]["_all"]["n"] == 12


def test_calibration_buckets(tmp_path):
    picks, led = [], []
    for i in range(10):
        picks.append(_pick(i, f"P{i}", model_prob={"HR": 0.25}, odds={"HR": 300}))
        led.append(_led(i, f"P{i}", "HR", i < 2, odds=300,
                        profit=3.0 if i < 2 else -1.0))   # 20% actual vs 25% said
    _setup(tmp_path, {"2026-06-01": picks}, led)
    cal = perf.calibration(perf.collect(tmp_path))
    b = next(c for c in cal if c["bucket"] == "20-30%")
    assert b["n"] == 10 and b["avg_prob"] == 0.25 and b["actual"] == 0.2
    assert b["diff"] == round(0.2 - 0.25, 3)


def test_window_range():
    assert perf.window_range("today", "2026-06-08") == ("2026-06-08", "2026-06-08")
    assert perf.window_range("7d", "2026-06-08")[0] == "2026-06-01"
    assert perf.window_range("season", "2026-06-08") == ("2026-01-01", "2026-06-08")


def test_small_sample_and_insights(tmp_path):
    picks = [_pick(1, "A", model_prob={"HR": 0.2}, odds={"HR": 200})]
    _setup(tmp_path, {"2026-06-01": picks}, [_led(1, "A", "HR", True, 200, 2.0)])
    rep = perf.report(tmp_path, window="all")
    assert rep["overall"]["low_sample"] is True
    assert any("not enough" in s.lower() for s in rep["insights"])


def test_snapshot_not_enough(tmp_path):
    snap = perf.snapshot(tmp_path, today="2026-06-08")
    assert snap["enough"] is False


def test_value_alerts_perf(tmp_path):
    picks, led = [], []
    for i in range(10):
        # model .25 vs implied .133 (+650) -> edge ~.12 -> alert
        picks.append(_pick(i, f"P{i}", model_prob={"HR": 0.25}, odds={"HR": 650}))
        led.append(_led(i, f"P{i}", "HR", i < 4, odds=650,
                        profit=6.5 if i < 4 else -1.0))
    _setup(tmp_path, {"2026-06-01": picks}, led)
    va = perf.value_alert_perf(perf.collect(tmp_path))
    assert va["overall"]["n"] == 10 and va["overall"]["roi"] is not None
