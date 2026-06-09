"""Phase 14 — Market vs Model value engine (pure, dict-based, offline-testable).

Answers the two distinct questions:
  1. Who is most likely to hit?      -> "best raw" leaderboard (model score)
  2. Who is most mispriced?          -> "best value" leaderboard (edge)

Odds come from manual entry and/or an API; model probabilities come from the
existing Batch-2/3 model (real for HR/TB, score-derived estimates for HRR/Hits).
No odds -> value is 'Unknown' (never faked).
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from . import featured

MARKETS = ["HR", "TB", "HRR", "Hits", "RBI"]

# staleness thresholds in minutes -> (yellow, red)
STALE = {"manual": (60, 180), "api": (15, 30)}


# --------------------------------------------------------------------------- #
# Odds math
# --------------------------------------------------------------------------- #
def implied_prob(american: Optional[int]) -> Optional[float]:
    if american is None:
        return None
    a = int(american)
    if a > 0:
        return round(100.0 / (a + 100.0), 4)
    if a < 0:
        return round(abs(a) / (abs(a) + 100.0), 4)
    return None


def value_grade(edge: Optional[float]) -> str:
    if edge is None:
        return "Unknown"
    if edge > 0.08:
        return "A+"
    if edge >= 0.05:
        return "A"
    if edge >= 0.03:
        return "B"
    if edge >= 0.01:
        return "C"
    return "D"


def model_prob(m: dict, market: str) -> Optional[float]:
    """Real model prob for HR/TB; transparent score-derived estimate for HRR/Hits.

    RBI has no model -> None (value stays Unknown, never faked).
    """
    def _f(k):
        v = m.get(k)
        try:
            return float(v) if v not in (None, "", "None") else None
        except (TypeError, ValueError):
            return None
    if market == "HR":
        return _f("model_hr_prob")
    if market == "TB":
        return _f("model_tb_prob")
    if market in ("HRR", "Hits"):
        sc = featured.market_scores(m).get(market, {}).get("score")
        if sc is None:
            return None
        if market == "Hits":   # P(1+ hit): ~.35 floor, ~.80 ceiling
            return round(0.35 + sc / 100.0 * 0.45, 4)
        return round(0.20 + sc / 100.0 * 0.40, 4)   # HRR
    return None


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #
def _age_minutes(ts: Optional[str], now: Optional[_dt.datetime]) -> Optional[float]:
    if not ts:
        return None
    try:
        t = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    now = now or _dt.datetime.now()
    return max(0.0, (now - t).total_seconds() / 60.0)


def staleness(ts: Optional[str], source: str, now: Optional[_dt.datetime] = None) -> str:
    """-> 'ok' | 'yellow' | 'red' | 'unknown'."""
    age = _age_minutes(ts, now)
    if age is None:
        return "unknown"
    yellow, red = STALE.get(source, (15, 30))
    if age >= red:
        return "red"
    if age >= yellow:
        return "yellow"
    return "ok"


# --------------------------------------------------------------------------- #
# Odds indexing (manual + api merged)
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    from .util import normalize_name
    return normalize_name(s or "")


def build_odds_index(manual: List[dict], api: Optional[List[dict]] = None) -> Dict[tuple, List[dict]]:
    """{(player_norm, bet_type): [entry, ...]} from manual + api odds."""
    idx: Dict[tuple, List[dict]] = {}
    for e in (manual or []):
        key = (e.get("player_norm") or _norm(e.get("player", "")), e.get("bet_type"))
        idx.setdefault(key, []).append({**e, "source": e.get("source", "manual")})
    for e in (api or []):
        key = (_norm(e.get("player", "")), e.get("bet_type"))
        idx.setdefault(key, []).append({**e, "source": e.get("source", "api")})
    return idx


def _best_price(entries: List[dict]) -> Optional[dict]:
    """Best price for the bettor = lowest implied probability (highest payout)."""
    priced = [e for e in entries if e.get("odds") is not None]
    if not priced:
        return None
    return min(priced, key=lambda e: implied_prob(e["odds"]) or 1.0)


# --------------------------------------------------------------------------- #
# Market vs Model
# --------------------------------------------------------------------------- #
def _row(m: dict, market: str, entries: List[dict], now, tables=None) -> dict:
    raw = model_prob(m, market)
    # calibrated probability drives value/edge; raw is preserved for display
    if tables is not None:
        from . import calibration
        cal = calibration.calibrate(raw, market, tables)
        mp, cal_warn, cal_conf = cal["calibrated"], cal["warning"], cal["confidence"]
    else:
        mp, cal_warn, cal_conf = raw, "NO_CALIBRATION_DATA", "none"
    best = _best_price(entries) if entries else None
    odds = best["odds"] if best else None
    imp = implied_prob(odds)
    edge = round(mp - imp, 4) if (mp is not None and imp is not None) else None
    return {
        "player": m.get("batter"), "batter_id": m.get("batter_id"), "team": m.get("team"),
        "opp_team": m.get("opp_team"), "opp_sp": m.get("opp_sp"),
        "bet_type": market,
        "sportsbook": best.get("sportsbook") if best else None,
        "odds": odds, "implied_prob": imp,
        "model_prob": mp,                 # calibrated (drives value)
        "raw_model_prob": raw,
        "calibration_warning": cal_warn, "calibration_confidence": cal_conf,
        "edge": edge, "value": value_grade(edge),
        "source": best.get("source") if best else "unknown",
        "last_updated": best.get("timestamp") if best else None,
        "stale": staleness(best.get("timestamp"), best.get("source", "manual"), now) if best else "unknown",
        "all_prices": [
            {"sportsbook": e.get("sportsbook"), "odds": e.get("odds"),
             "source": e.get("source"), "best": (best is not None and e is best)}
            for e in (entries or [])
        ],
        "model_score": featured.market_scores(m).get(market, {}).get("score") if market in featured.MARKETS else None,
    }


def market_vs_model(matchups: List[dict], manual: List[dict],
                    api: Optional[List[dict]] = None,
                    now: Optional[_dt.datetime] = None,
                    tables: Optional[dict] = None) -> dict:
    from .featured import markets_of
    idx = build_odds_index(manual, api)
    rows: List[dict] = []
    for m in matchups:
        pnorm = _norm(m.get("batter") or "")
        # markets the player has a bet on, plus any market we have odds for
        mk_set = set(markets_of(m))
        mk_set |= {bt for (pn, bt) in idx if pn == pnorm}
        for market in MARKETS:
            if market not in mk_set:
                continue
            entries = idx.get((pnorm, market), [])
            row = _row(m, market, entries, now, tables)
            if row["odds"] is None and row["model_prob"] is None:
                continue          # nothing to show
            rows.append(row)

    priced = [r for r in rows if r["edge"] is not None]
    best_value = {}
    for market in featured.MARKETS:
        cands = [r for r in priced if r["bet_type"] == market and r["edge"] > 0]
        best_value[market] = max(cands, key=lambda r: r["edge"]) if cands else None
    overall = max(priced, key=lambda r: r["edge"]) if priced else None

    leaderboard_value = sorted(priced, key=lambda r: -r["edge"])[:15]
    leaderboard_raw = sorted(
        [r for r in rows if r["model_score"] is not None],
        key=lambda r: -r["model_score"])[:15]
    alerts = [r for r in priced if r["edge"] is not None and r["edge"] >= 0.05]
    alerts.sort(key=lambda r: -r["edge"])

    return {
        "rows": rows,
        "best_value": best_value,
        "best_overall": overall,
        "leaderboard_value": leaderboard_value,
        "leaderboard_raw": leaderboard_raw,
        "alerts": alerts,
        "has_odds": bool(priced),
    }
