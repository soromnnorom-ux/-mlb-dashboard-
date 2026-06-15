"""Model Performance — did the model actually win? (Phase 19, honest edition)

Joins the rolling result ledger (out/_ledger.csv, written by `grade`) with the
rich per-pick snapshots (out/<date>/picks.json, written by `run`) so we can slice
realized W/L/ROI by bet type, grade, signal/tag, value alert, pitch-mix bucket,
contact cluster, environment, and probability calibration.

Pure/dict-based and offline-testable. Never hides losses; flags low samples and
clearly marks breakdowns that need the rich snapshot (older thin picks are still
counted for bet-type/ROI/calibration but excluded from signal/grade slices).
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from . import featured, value_center
from .util import to_bool as _as_bool

MIN_SAMPLE = 10                      # below this -> low-sample warning
SIGNAL_TAGS = ["PITCH_MIX_EDGE", "MISSED_HR", "HOT_CONTACT", "NUCLEAR_CONTACT",
               "MULTIPLE_100_EV", "PENDING_BLOWUP", "WEAK_BULLPEN", "LATE_HR",
               "REGRESSION_SPOT", "RECENT_HR", "COLD_CONTACT", "SMALL_PM_SAMPLE"]


def _as_float(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Load + join
# --------------------------------------------------------------------------- #
def load_ledger(out_root: str | Path = "out") -> List[dict]:
    p = Path(out_root) / "_ledger.csv"
    if not p.exists():
        return []
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def _picks_index(date: str, out_root: str | Path) -> Dict[int, dict]:
    from .report.picks import load_picks
    out = {}
    for pk in load_picks(Path(out_root) / date):
        out[int(pk["batter_id"])] = pk
    return out


def collect(out_root: str | Path = "out", start: Optional[str] = None,
            end: Optional[str] = None) -> List[dict]:
    """Enriched, decided rows joining ledger result + pick snapshot context."""
    ledger = load_ledger(out_root)
    rows: List[dict] = []
    pidx_cache: Dict[str, Dict[int, dict]] = {}
    for lr in ledger:
        date = lr.get("date")
        if start and date < start:
            continue
        if end and date > end:
            continue
        won = lr.get("won")
        if won in (None, "", "None"):
            continue                              # void / ungraded
        won = _as_bool(won)
        bet = lr.get("bet")
        if date not in pidx_cache:
            pidx_cache[date] = _picks_index(date, out_root)
        pick = pidx_cache[date].get(int(lr["batter_id"])) if lr.get("batter_id") else None
        odds = _as_float(lr.get("odds"))
        odds = int(odds) if odds is not None else None
        profit = _as_float(lr.get("profit"))
        # model prob from the snapshot (per-bet)
        mprob = None
        rich = False
        tags: List[dict] = []
        ms = {}
        if pick:
            mp = (pick.get("model_prob") or {})
            mprob = _as_float(mp.get(bet))
            tags = pick.get("tags") or []
            rich = "env_score" in pick or bool(tags)
            if rich and bet in featured.MARKETS:
                ms = featured.market_scores(pick).get(bet, {})
        implied = value_center.implied_prob(odds)
        edge = round(mprob - implied, 4) if (mprob is not None and implied is not None) else None
        row = {
            "date": date, "batter": lr.get("batter"), "batter_id": lr.get("batter_id"),
            "team": lr.get("team"), "bet": bet, "line": lr.get("line"),
            "won": won, "profit": profit, "odds": odds,
            "model_prob": mprob, "implied_prob": implied, "edge": edge,
            "value_grade": value_center.value_grade(edge) if edge is not None else "Unknown",
            "tier": lr.get("tier"),
            "rich": rich, "tags": tags,
            "grade": ms.get("grade"), "model_score": ms.get("score"),
            "env_tier": (pick or {}).get("env_tier"),
            "pitcher_score": _as_float((pick or {}).get("pitcher_score")),
            "barrel_vs_pm": _as_float((pick or {}).get("barrel_vs_pm")),
            "cluster_label": (pick or {}).get("cluster_label"),
            "missed_hr": _as_bool((pick or {}).get("missed_hr")),
            "lineup_state": (pick or {}).get("lineup_state"),
            "opp_sp": (pick or {}).get("opp_sp"),
            "is_alert": (edge is not None and edge >= 0.05),
        }
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Aggregations
# --------------------------------------------------------------------------- #
def record(rows: List[dict]) -> dict:
    w = sum(1 for r in rows if r["won"])
    l = sum(1 for r in rows if not r["won"])
    n = w + l
    staked = [r for r in rows if r["profit"] is not None]
    profit = sum(r["profit"] for r in staked)
    odds = [r["odds"] for r in rows if r["odds"] is not None]
    mps = [r["model_prob"] for r in rows if r["model_prob"] is not None]
    edges = [r["edge"] for r in rows if r["edge"] is not None]
    return {
        "w": w, "l": l, "n": n,
        "hit_rate": round(w / n, 3) if n else None,
        "staked": len(staked),
        "profit": round(profit, 2),
        "roi": round(profit / len(staked), 3) if staked else None,
        "avg_odds": round(sum(odds) / len(odds)) if odds else None,
        "avg_model_prob": round(sum(mps) / len(mps), 3) if mps else None,
        "avg_edge": round(sum(edges) / len(edges), 4) if edges else None,
        "low_sample": n < MIN_SAMPLE,
    }


def _group(rows, keyfn, only_rich=False):
    out: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        if only_rich and not r["rich"]:
            continue
        k = keyfn(r)
        if k is None:
            continue
        out[str(k)].append(r)
    return {k: record(v) for k, v in out.items()}


def by_bet_type(rows):
    return _group(rows, lambda r: r["bet"])


def by_grade(rows):
    return _group(rows, lambda r: r["grade"], only_rich=True)


def by_value_grade(rows):
    return _group([r for r in rows if r["edge"] is not None], lambda r: r["value_grade"])


def by_signal(rows):
    """Per-tag performance + best/worst bet type for that tag."""
    out = {}
    for tag in SIGNAL_TAGS:
        sub = [r for r in rows if r["rich"] and _has_signal(r, tag)]
        if not sub:
            continue
        rec = record(sub)
        per_bet = {b: v for b, v in _group(sub, lambda r: r["bet"]).items()}
        ranked = sorted([(b, v) for b, v in per_bet.items() if v["n"] >= 3],
                        key=lambda x: (x[1]["roi"] if x[1]["roi"] is not None else -9,
                                       x[1]["hit_rate"] or 0))
        rec["best_bet"] = ranked[-1][0] if ranked else None
        rec["worst_bet"] = ranked[0][0] if ranked else None
        out[tag] = rec
    return out


def _has_signal(r, tag):
    if tag == "PITCH_MIX_EDGE":
        return (r["barrel_vs_pm"] or 0) >= 10
    if tag == "WEAK_BULLPEN":
        return "LATE_HR" in (r["tags"] or [])
    return tag in (r["tags"] or [])


def value_alert_perf(rows):
    alerts = [r for r in rows if r["is_alert"]]
    out = {"overall": record(alerts),
           "by_value_grade": by_value_grade(alerts),
           "by_bet": _group(alerts, lambda r: r["bet"])}
    return out


def by_pitchmix_bucket(rows):
    def bucket(r):
        v = r["barrel_vs_pm"]
        if v is None:
            return None
        if v >= 20:
            return "20%+"
        if v >= 15:
            return "15-20%"
        if v >= 10:
            return "10-15%"
        return None
    out = {}
    for bk in ("10-15%", "15-20%", "20%+"):
        sub = [r for r in rows if r["rich"] and bucket(r) == bk]
        out[bk] = {b: record([r for r in sub if r["bet"] == b])
                   for b in ("HR", "TB", "HRR", "Hits") if any(r["bet"] == b for r in sub)}
        out[bk]["_all"] = record(sub)
    return out


def by_cluster(rows):
    out = {}
    for lab in ("COLD", "NORMAL", "HOT", "NUCLEAR"):
        sub = [r for r in rows if r["rich"] and r["cluster_label"] == lab]
        if not sub:
            continue
        out[lab] = {"_all": record(sub),
                    **{b: record([r for r in sub if r["bet"] == b])
                       for b in ("HR", "TB", "HRR", "Hits") if any(r["bet"] == b for r in sub)}}
    return out


def by_env_grade(rows):
    return _group([r for r in rows if r["rich"] and r["env_tier"]],
                  lambda r: r["env_tier"])


def by_pitcher_attack(rows):
    def grade(r):
        s = r["pitcher_score"]
        if s is None:
            return None
        sc = int(min(100, max(0, s / 8.0 * 100)))
        return featured.grade_from_score(sc)
    return _group([r for r in rows if r["rich"] and r["pitcher_score"] is not None], grade)


def calibration(rows):
    buckets = [(0, .10), (.10, .20), (.20, .30), (.30, .40), (.40, .50), (.50, 1.01)]
    out = []
    for lo, hi in buckets:
        sub = [r for r in rows if r["model_prob"] is not None and lo <= r["model_prob"] < hi]
        if not sub:
            out.append({"bucket": f"{int(lo*100)}-{int(hi*100) if hi<=1 else 100}%",
                        "n": 0, "avg_prob": None, "actual": None, "diff": None})
            continue
        n = len(sub)
        avg = sum(r["model_prob"] for r in sub) / n
        actual = sum(1 for r in sub if r["won"]) / n
        out.append({"bucket": f"{int(lo*100)}-{min(100,int(hi*100))}%", "n": n,
                    "avg_prob": round(avg, 3), "actual": round(actual, 3),
                    "diff": round(actual - avg, 3)})
    return out


# --------------------------------------------------------------------------- #
# Time windows
# --------------------------------------------------------------------------- #
def window_range(name: str, today: str) -> tuple:
    t = _dt.date.fromisoformat(today)
    name = (name or "all").strip()
    if name in ("today", "daily"):
        return today, today
    if name == "yesterday":
        y = (t - _dt.timedelta(days=1)).isoformat()
        return y, y
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):     # an explicit single date
        return name, name
    m = re.fullmatch(r"[Ll](\d+)", name)             # L3 / L5 / L10 ... last N days
    if m:
        n = int(m.group(1))
        return (t - _dt.timedelta(days=n - 1)).isoformat(), today
    if name in ("7d", "14d", "30d"):
        days = int(name[:-1])
        return (t - _dt.timedelta(days=days)).isoformat(), today
    if name == "season":
        return f"{t.year}-01-01", today
    return "0000-01-01", "9999-12-31"   # all-time


def graded_dates(out_root: str | Path = "out") -> List[str]:
    """Sorted distinct dates that have at least one decided (graded) ledger row."""
    seen = set()
    for r in load_ledger(out_root):
        w = r.get("won")
        if w not in (None, "", "None"):
            seen.add(r.get("date"))
    return sorted(d for d in seen if d)


def resolve_yesterday(out_root: str | Path, today: str) -> tuple:
    """Return (resolved_date, label, warning).

    Yesterday = previous calendar date if it has graded data; otherwise the most
    recent graded slate strictly before today. Never a future date.
    """
    gd = graded_dates(out_root)
    y = (_dt.date.fromisoformat(today) - _dt.timedelta(days=1)).isoformat()
    if y in gd:
        return y, f"Yesterday Review: {y}", None
    before = [d for d in gd if d < today]
    if before:
        d = before[-1]
        return (d, f"Previous Graded Slate: {d}",
                "No graded data for yesterday. Showing previous graded slate instead.")
    return None, "No previous graded slate found.", "No previous graded slate found."


def _signal_ranking(rows: List[dict]):
    sig = by_signal(rows)
    ranked = sorted(
        [{"tag": t, **v} for t, v in sig.items() if v["n"] >= 1],
        key=lambda x: (x["roi"] if x["roi"] is not None else -9, x["hit_rate"] or 0))
    return sig, list(reversed(ranked))[:3], ranked[:3]   # sig, best, worst


def yesterday_report(out_root: str | Path = "out", today: Optional[str] = None) -> dict:
    """Single-date review of yesterday / the most recent graded slate."""
    today = today or _dt.date.today().isoformat()
    rd, label, warning = resolve_yesterday(out_root, today)
    if rd is None:
        return {"resolved_date": None, "label": label, "warning": warning,
                "total_picks": 0, "graded_picks": 0, "ungraded_picks": 0,
                "by_market": {}, "by_grade": {}, "by_signal": {},
                "best_signals": [], "worst_signals": [], "rows": []}
    rows = collect(out_root, rd, rd)                  # ONE date only
    from .report.picks import load_picks
    picks = [p for p in load_picks(Path(out_root) / rd) if p.get("bets")]
    total = len(picks)
    # cap graded at total so the counts can never contradict (ledger may carry
    # rows from an earlier build of the same slate)
    graded_players = min(len({r["batter_id"] for r in rows}), total) if total else \
        len({r["batter_id"] for r in rows})
    sig, best, worst = _signal_ranking(rows)
    return {
        "resolved_date": rd, "label": label, "warning": warning,
        "total_picks": total, "graded_picks": graded_players,
        "ungraded_picks": max(0, total - graded_players),
        "by_market": by_bet_type(rows), "by_grade": by_grade(rows),
        "by_signal": sig, "best_signals": best, "worst_signals": worst,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# Auto insights (only from real results, with sample guards)
# --------------------------------------------------------------------------- #
def auto_insights(rows: List[dict]) -> List[str]:
    out: List[str] = []
    if len(rows) < MIN_SAMPLE:
        out.append(f"Only {len(rows)} graded bets — not enough to draw conclusions yet.")
        return out
    bt = by_bet_type(rows)
    best = max((b for b in bt.items() if b[1]["roi"] is not None),
               key=lambda x: x[1]["roi"], default=None)
    worst = min((b for b in bt.items() if b[1]["roi"] is not None),
                key=lambda x: x[1]["roi"], default=None)
    if best:
        out.append(f"Best bet type by ROI: {best[0]} ({best[1]['roi']*100:+.0f}% over {best[1]['staked']} priced bets).")
    if worst and worst[0] != (best or [None])[0]:
        out.append(f"Worst bet type: {worst[0]} ({worst[1]['roi']*100:+.0f}%).")
    for tag, rec in by_signal(rows).items():
        if rec["n"] >= MIN_SAMPLE and rec["roi"] is not None:
            verdict = "profitable" if rec["roi"] > 0 else "losing money"
            best_bet = f" — best on {rec['best_bet']}" if rec.get("best_bet") else ""
            out.append(f"{tag}: {rec['hit_rate']*100:.0f}% hit, ROI {rec['roi']*100:+.0f}% over {rec['n']} bets ({verdict}{best_bet}).")
    va = value_alert_perf(rows)["overall"]
    if va["n"] >= MIN_SAMPLE and va["roi"] is not None:
        out.append(f"Value alerts (edge≥5%): ROI {va['roi']*100:+.0f}% over {va['n']} bets.")
    for c in calibration(rows):
        if c["n"] >= MIN_SAMPLE and c["diff"] is not None and abs(c["diff"]) >= 0.07:
            d = "overconfident" if c["diff"] < 0 else "underconfident"
            out.append(f"Model is {d} in the {c['bucket']} bucket (said {c['avg_prob']*100:.0f}%, hit {c['actual']*100:.0f}%).")
    out.append("BvP is not built yet, so it is not included.")
    return out


# --------------------------------------------------------------------------- #
# Top-level report + snapshot
# --------------------------------------------------------------------------- #
def report(out_root: str | Path = "out", window: str = "all",
           today: Optional[str] = None) -> dict:
    today = today or _dt.date.today().isoformat()
    resolved_date = label = warning = None
    if window == "yesterday":
        rd, label, warning = resolve_yesterday(out_root, today)
        resolved_date = rd
        start, end = (rd, rd) if rd else ("9999-99-99", "9999-99-99")
    else:
        start, end = window_range(window, today)
    rows = collect(out_root, start, end)
    rich_n = sum(1 for r in rows if r["rich"])
    from . import calibration as calib
    _tables = calib.load_tables(out_root)
    cal_status = calib.calibration_status(rows, _tables)
    return {
        "window": window, "n": len(rows), "rich_n": rich_n,
        "resolved_date": resolved_date, "label": label, "warning": warning,
        "calibration_status": cal_status,
        "calibration_coverage": calib.coverage(_tables),
        "overall": record(rows),
        "by_bet_type": by_bet_type(rows),
        "by_grade": by_grade(rows),
        "by_signal": by_signal(rows),
        "value_alerts": value_alert_perf(rows),
        "by_pitchmix": by_pitchmix_bucket(rows),
        "by_cluster": by_cluster(rows),
        "by_env_grade": by_env_grade(rows),
        "by_pitcher_attack": by_pitcher_attack(rows),
        "calibration": calibration(rows),
        "insights": auto_insights(rows),
        "rows": rows,
    }


def snapshot(out_root: str | Path = "out", today: Optional[str] = None) -> dict:
    today = today or _dt.date.today().isoformat()
    start, end = window_range("7d", today)
    rows = collect(out_root, start, end)
    if len(rows) < MIN_SAMPLE:
        return {"enough": False, "n": len(rows),
                "message": "Not enough graded results yet."}
    bt = by_bet_type(rows)
    sig = by_signal(rows)
    best_bet = max((b for b in bt.items() if b[1]["roi"] is not None),
                   key=lambda x: x[1]["roi"], default=(None, {}))
    sig_ranked = sorted([(t, v) for t, v in sig.items() if v["roi"] is not None and v["n"] >= 5],
                        key=lambda x: x[1]["roi"])
    va = value_alert_perf(rows)["overall"]
    return {
        "enough": True, "n": len(rows),
        "roi_7d": record(rows)["roi"],
        "best_bet": best_bet[0], "best_bet_roi": best_bet[1].get("roi"),
        "best_signal": sig_ranked[-1][0] if sig_ranked else None,
        "worst_signal": sig_ranked[0][0] if sig_ranked else None,
        "value_alert_roi": va["roi"],
    }
