"""Empirical probability calibration (Batch 6).

The Performance page showed the model's probabilities are overconfident (e.g. the
50%+ bucket hit ~42%). This module recalibrates *probabilities only* — the raw
ranking scores are left completely untouched. It needs nothing but bet type +
raw model probability + result, so it works on the existing 35k-row history.

Two clearly separated numbers downstream:
  * raw_model_probability        (unchanged; from the model)
  * calibrated_model_probability (this module; used for value / EV / edge)

Pure/leaf module: imports nothing from value_center/performance (no cycles).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

BUCKETS = [(0.0, 0.10, "0-10"), (0.10, 0.20, "10-20"), (0.20, 0.30, "20-30"),
           (0.30, 0.40, "30-40"), (0.40, 0.50, "40-50"), (0.50, 1.01, "50+")]
MIN_SAMPLE = 100
_CACHE: Dict[str, dict] = {}


def bucket_of(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    for lo, hi, name in BUCKETS:
        if lo <= p < hi:
            return name
    return "50+"


def _as_float(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Minimal history collector (bet, raw prob, won) — no rich context needed
# --------------------------------------------------------------------------- #
def collect_rows(out_root: str | Path = "out") -> List[dict]:
    out_root = Path(out_root)
    led = out_root / "_ledger.csv"
    if not led.exists():
        return []
    # index picks.json model_prob per (date, batter_id)
    pcache: Dict[str, Dict[str, dict]] = {}

    def probs(date, bid):
        if date not in pcache:
            pj = out_root / date / "picks.json"
            idx = {}
            if pj.exists():
                try:
                    for pk in json.loads(pj.read_text()):
                        idx[str(pk["batter_id"])] = pk.get("model_prob") or {}
                except (json.JSONDecodeError, OSError):
                    pass
            pcache[date] = idx
        return pcache[date].get(str(bid), {})

    rows = []
    with led.open(newline="") as f:
        for r in csv.DictReader(f):
            won = r.get("won")
            if won in (None, "", "None"):
                continue
            bet = r.get("bet")
            mp = _as_float(probs(r.get("date"), r.get("batter_id")).get(bet))
            if mp is None:
                continue
            rows.append({"bet": bet, "model_prob": mp,
                         "won": won is True or str(won).lower() == "true"})
    return rows


# --------------------------------------------------------------------------- #
# Build tables
# --------------------------------------------------------------------------- #
def build_tables(rows: List[dict]) -> dict:
    """{bet: {"_baseline":x, "_n":N, "buckets": {bucket: {n,avg_raw,actual}}}}."""
    by_bet: Dict[str, List[dict]] = {}
    for r in rows:
        if r.get("model_prob") is None:
            continue
        by_bet.setdefault(r["bet"], []).append(r)
    tables = {}
    for bet, rs in by_bet.items():
        n = len(rs)
        baseline = sum(1 for r in rs if r["won"]) / n if n else None
        buckets = {}
        for lo, hi, name in BUCKETS:
            sub = [r for r in rs if lo <= r["model_prob"] < hi
                   or (name == "50+" and r["model_prob"] >= 0.50)]
            if not sub:
                continue
            bn = len(sub)
            buckets[name] = {
                "n": bn,
                "avg_raw": round(sum(r["model_prob"] for r in sub) / bn, 4),
                "actual": round(sum(1 for r in sub if r["won"]) / bn, 4),
            }
        tables[bet] = {"_baseline": round(baseline, 4) if baseline is not None else None,
                       "_n": n, "buckets": buckets}
    return tables


# --------------------------------------------------------------------------- #
# Calibrate a single raw probability
# --------------------------------------------------------------------------- #
def calibrate(raw: Optional[float], bet: str, tables: dict) -> dict:
    """Return {raw, calibrated, bucket, confidence, warning} (transparent blend)."""
    if raw is None:
        return {"raw": None, "calibrated": None, "bucket": None,
                "confidence": "none", "warning": "NO_MODEL_PROB"}
    bk = bucket_of(raw)
    bt = tables.get(bet)
    if not bt or not bt.get("buckets"):
        return {"raw": round(raw, 4), "calibrated": round(raw, 4), "bucket": bk,
                "confidence": "none", "warning": "NO_CALIBRATION_DATA"}
    baseline = bt.get("_baseline") if bt.get("_baseline") is not None else raw
    entry = bt["buckets"].get(bk)
    if not entry:
        # bucket unseen -> shrink toward bet-type baseline
        cal = 0.3 * raw + 0.7 * baseline
        return {"raw": round(raw, 4), "calibrated": round(cal, 4), "bucket": bk,
                "confidence": "low", "warning": "LOW_SAMPLE_CALIBRATION"}
    n, actual = entry["n"], entry["actual"]
    warning = None
    if n >= 500:
        cal = 0.8 * actual + 0.2 * raw
        conf = "high"
    elif n >= MIN_SAMPLE:
        cal = 0.6 * actual + 0.4 * raw
        conf = "medium"
    else:
        cal = 0.3 * actual + 0.7 * baseline
        conf = "low"
        warning = "LOW_SAMPLE_CALIBRATION"
    if entry["avg_raw"] - actual >= 0.04:
        warning = warning or "OVERCONFIDENT_BUCKET"
    return {"raw": round(raw, 4), "calibrated": round(cal, 4), "bucket": bk,
            "confidence": conf, "warning": warning}


# --------------------------------------------------------------------------- #
# Persistence + cache
# --------------------------------------------------------------------------- #
COVERAGE_MARKETS = ["HR", "TB", "HRR", "Hits"]


def coverage(tables: dict) -> Dict[str, dict]:
    """Per-market calibration coverage + status for the dashboard.

    CALIBRATED   = bet-type sample big and every populated bucket >= MIN_SAMPLE
    PARTIAL      = some buckets calibrated, others fall back to raw/baseline
    RAW_FALLBACK = bet-type sample too small to trust
    NO_DATA      = no historical prob+result rows for this market
    """
    out = {}
    for bet in COVERAGE_MARKETS:
        bt = tables.get(bet)
        if not bt or not bt.get("buckets"):
            out[bet] = {"n": 0, "buckets_calibrated": 0, "buckets_total": 0,
                        "status": "NO_DATA",
                        "message": f"{bet} has no graded probability history yet — using raw probability."}
            continue
        bks = bt["buckets"]
        cal = sum(1 for e in bks.values() if e["n"] >= MIN_SAMPLE)
        total = len(bks)
        n = bt.get("_n", 0)
        if n < MIN_SAMPLE:
            status = "RAW_FALLBACK"
            msg = f"{bet} is using raw fallback until enough graded results exist (n={n})."
        elif cal == total:
            status = "CALIBRATED"
            msg = f"{bet} is empirically calibrated ({n} graded bets)."
        elif cal > 0:
            status = "PARTIAL"
            msg = f"{bet} is partially calibrated — {cal}/{total} buckets have enough sample; others use raw fallback."
        else:
            status = "RAW_FALLBACK"
            msg = f"{bet} is using raw fallback until buckets reach {MIN_SAMPLE} samples."
        out[bet] = {"n": n, "buckets_calibrated": cal, "buckets_total": total,
                    "status": status, "message": msg}
    return out


def _path(out_root: str | Path) -> Path:
    return Path(out_root) / "_calibration.json"


def save_tables(out_root: str | Path = "out") -> dict:
    tables = build_tables(collect_rows(out_root))
    p = _path(out_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tables, indent=2))
    _CACHE[str(p)] = tables
    return tables


def load_tables(out_root: str | Path = "out", rebuild: bool = False) -> dict:
    p = _path(out_root)
    key = str(p)
    if not rebuild and key in _CACHE:
        return _CACHE[key]
    if not rebuild and p.exists():
        try:
            _CACHE[key] = json.loads(p.read_text())
            return _CACHE[key]
        except (json.JSONDecodeError, OSError):
            pass
    return save_tables(out_root)


# --------------------------------------------------------------------------- #
# Before/after status for the Performance page
# --------------------------------------------------------------------------- #
def calibration_status(rows: List[dict], tables: dict) -> List[dict]:
    """Per bet type: raw avg vs calibrated avg vs actual, and error improvement.

    `rows` are graded rows with bet/model_prob/won.
    """
    out = []
    by_bet: Dict[str, List[dict]] = {}
    for r in rows:
        if r.get("model_prob") is None or r.get("won") is None:
            continue
        by_bet.setdefault(r["bet"], []).append(r)
    for bet, rs in sorted(by_bet.items()):
        n = len(rs)
        raw_avg = sum(r["model_prob"] for r in rs) / n
        cal_avg = sum(calibrate(r["model_prob"], bet, tables)["calibrated"] for r in rs) / n
        actual = sum(1 for r in rs if r["won"]) / n
        raw_err = abs(raw_avg - actual)
        cal_err = abs(cal_avg - actual)
        out.append({
            "bet": bet, "n": n,
            "raw_avg": round(raw_avg, 3), "calibrated_avg": round(cal_avg, 3),
            "actual": round(actual, 3),
            "raw_error": round(raw_err, 3), "calibrated_error": round(cal_err, 3),
            "improvement": round(raw_err - cal_err, 3),
        })
    return out
