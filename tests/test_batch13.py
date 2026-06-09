"""Batch 13: Yesterday / Previous Graded Slate review (single date)."""
import csv
import json

from hrplaybook import performance as perf

FIELDS = ["date", "batter_id", "batter", "team", "bet", "line", "tier",
          "stat", "need", "got", "won", "odds", "profit"]


def _ledger(tmp, rows):
    with (tmp / "_ledger.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in FIELDS})


def _row(date, bid, won=True, bet="TB"):
    return {"date": date, "batter_id": bid, "batter": f"P{bid}", "team": "NYY",
            "bet": bet, "line": "2+ TB", "tier": 1, "stat": "tb", "need": 2,
            "got": 2 if won else 0, "won": won, "odds": "", "profit": ""}


def _picks(tmp, date, ids):
    d = tmp / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "picks.json").write_text(json.dumps(
        [{"date": date, "batter_id": i, "batter": f"P{i}", "team": "NYY",
          "bets": {"TB": "2+ TB"}, "model_prob": {"TB": 0.5}} for i in ids]))


def test_yesterday_uses_previous_calendar_date_when_graded(tmp_path):
    _ledger(tmp_path, [_row("2026-06-07", 1), _row("2026-06-08", 2)])
    rd, label, warn = perf.resolve_yesterday(tmp_path, "2026-06-09")
    assert rd == "2026-06-08" and "Yesterday Review" in label and warn is None


def test_previous_graded_slate_fallback(tmp_path):
    # today 06-10, only 06-08 graded (06-09 has none) -> fall back to 06-08
    _ledger(tmp_path, [_row("2026-06-08", 1)])
    rd, label, warn = perf.resolve_yesterday(tmp_path, "2026-06-10")
    assert rd == "2026-06-08" and "Previous Graded Slate" in label
    assert warn == "No graded data for yesterday. Showing previous graded slate instead."


def test_no_previous_graded_slate_message(tmp_path):
    _ledger(tmp_path, [])
    rd, label, warn = perf.resolve_yesterday(tmp_path, "2026-06-09")
    assert rd is None and label == "No previous graded slate found."


def test_no_future_dates_included(tmp_path):
    # a future-dated ledger row must never be chosen
    _ledger(tmp_path, [_row("2026-06-08", 1), _row("2026-06-30", 2)])
    rd, _, _ = perf.resolve_yesterday(tmp_path, "2026-06-09")
    assert rd == "2026-06-08"


def test_yesterday_report_uses_one_date_only(tmp_path):
    _ledger(tmp_path, [_row("2026-06-07", 1), _row("2026-06-08", 2), _row("2026-06-08", 3)])
    _picks(tmp_path, "2026-06-08", [2, 3, 4])
    rep = perf.yesterday_report(tmp_path, today="2026-06-09")
    assert rep["resolved_date"] == "2026-06-08"
    assert {r["date"] for r in rep["rows"]} == {"2026-06-08"}     # ONE date only
    assert rep["graded_picks"] == 2 and rep["total_picks"] == 3
    assert rep["ungraded_picks"] == 1


def test_summary_matches_resolved_date_rows(tmp_path):
    _ledger(tmp_path, [_row("2026-06-08", 1, won=True), _row("2026-06-08", 2, won=False),
                       _row("2026-06-07", 9, won=True)])
    _picks(tmp_path, "2026-06-08", [1, 2])
    rep = perf.yesterday_report(tmp_path, today="2026-06-09")
    tb = rep["by_market"]["TB"]
    assert tb["w"] == 1 and tb["l"] == 1 and tb["n"] == 2   # excludes 06-07 row


def test_window_range_ln_and_literal_date():
    assert perf.window_range("L3", "2026-06-09") == ("2026-06-07", "2026-06-09")
    assert perf.window_range("L1", "2026-06-09") == ("2026-06-09", "2026-06-09")
    assert perf.window_range("2026-06-05", "2026-06-09") == ("2026-06-05", "2026-06-05")
    assert perf.window_range("daily", "2026-06-09") == ("2026-06-09", "2026-06-09")


def test_report_yesterday_surfaces_resolved_date(tmp_path):
    _ledger(tmp_path, [_row("2026-06-08", 1)])
    _picks(tmp_path, "2026-06-08", [1])
    rep = perf.report(tmp_path, window="yesterday", today="2026-06-09")
    assert rep["resolved_date"] == "2026-06-08"
    assert "Yesterday Review" in (rep["label"] or "")
