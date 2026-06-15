"""Regression: odds must only be priced against the matching line threshold.

Bug: manual/API odds for a different line (e.g. 2.5 TB = 3+) were compared to the
model's 2+ TB probability, producing a fake edge / wrong value grade.
"""
import datetime as dt

from hrplaybook import value_center as vc


def _m():
    return {"batter": "Slugger", "team": "NYY", "opp_team": "BOS", "opp_sp": "SP",
            "model_tb_prob": 0.55, "model_hr_prob": 0.20, "bets": "TB:2+ TB",
            "env_tier": "good", "env_score": 4, "pitcher_score": 5,
            "barrel_vs_pm": 14, "barrel_pct": 12, "order": 2, "platoon": "fav"}


def _odds(line, odds):
    return [{"player": "Slugger", "player_norm": "slugger", "bet_type": "TB",
             "line": line, "sportsbook": "DK", "odds": odds, "source": "manual",
             "timestamp": dt.datetime.now().isoformat()}]


def test_matching_line_is_priced():
    res = vc.market_vs_model([_m()], _odds("2+ TB", 120))   # implied .4545
    row = next(r for r in res["rows"] if r["bet_type"] == "TB")
    assert row["odds"] == 120
    assert row["edge"] == round(0.55 - 0.4545, 4)           # priced correctly
    assert row["value"] in ("A+", "A")


def test_mismatched_line_not_priced():
    # 2.5 TB (=3+) must NOT be priced against the 2+ model probability
    res = vc.market_vs_model([_m()], _odds("2.5 TB", 120))
    row = next(r for r in res["rows"] if r["bet_type"] == "TB")
    assert row["odds"] is None                # no usable same-line price
    assert row["edge"] is None and row["value"] == "Unknown"
    # but the mismatched price is still visible, flagged
    ap = row["all_prices"]
    assert ap and ap[0]["line_ok"] is False and ap[0]["odds"] == 120


def test_blank_line_assumed_canonical():
    res = vc.market_vs_model([_m()], _odds("", -110))
    row = next(r for r in res["rows"] if r["bet_type"] == "TB")
    assert row["odds"] == -110                # blank line treated as canonical


def test_entry_threshold_parsing():
    assert vc._entry_thr({"line": "2.5 TB"}) == 3
    assert vc._entry_thr({"line": "2+ TB"}) == 2
    assert vc._entry_thr({"line": "1.5 HRR"}) == 2
    assert vc._entry_thr({"line": ""}) is None
