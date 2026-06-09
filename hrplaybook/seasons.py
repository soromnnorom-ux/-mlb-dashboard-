"""Multi-season split (Batch 8): 2025 baseline + 2026 + recent windows, weighted
profiles, trend grades, and pitch-mix change detection.

Pure/leaf module (no network, no scoring imports). It produces *display + trend*
fields only — it does NOT change the raw ranking scores. Weighted profiles exist
to explain the player (long-term skill vs current form vs recent), not to inflate
scores. Metrics are computed only when the underlying data exists; missing inputs
stay None (never invented).
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

from .util import is_barrel

# (L30, 2026-season, 2025-season) weights
BAT_WEIGHTS = {"HR": (0.40, 0.35, 0.25), "TB": (0.45, 0.35, 0.20),
               "Hits": (0.50, 0.35, 0.15), "HRR": (0.45, 0.35, 0.20)}
PIT_WEIGHTS = (0.30, 0.50, 0.20)          # pitcher weakness: 2026 season heaviest
SMALL_PA_2026 = 50
SMALL_IP_2026 = 20
LOW_RECENT_BBE = 10


def _f(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Recent windows from statcast batted balls
# --------------------------------------------------------------------------- #
def window_metrics(balls: List[dict], asof: str, days: int) -> dict:
    """Contact metrics over the last `days` (by game_date) ending at `asof`."""
    try:
        cutoff = (_dt.date.fromisoformat(asof) - _dt.timedelta(days=days)).isoformat()
    except (ValueError, TypeError):
        cutoff = None
    sub = [b for b in balls if b.get("launch_speed") is not None
           and (cutoff is None or (b.get("game_date") or "") >= cutoff)]
    n = len(sub)
    if not n:
        return {"n": 0, "ev": None, "max_ev": None, "barrel_pct": None,
                "hardhit_pct": None, "la": None, "fb_pct": None}
    evs = [b["launch_speed"] for b in sub]
    las = [b["launch_angle"] for b in sub if b.get("launch_angle") is not None]
    barrels = sum(1 for b in sub if is_barrel(b["launch_speed"], b.get("launch_angle")))
    hard = sum(1 for b in sub if b["launch_speed"] >= 95)
    fb = sum(1 for b in sub if b.get("bb_type") == "fly_ball")
    return {
        "n": n,
        "ev": round(sum(evs) / n, 1),
        "max_ev": round(max(evs), 1),
        "barrel_pct": round(barrels / n * 100, 1),
        "hardhit_pct": round(hard / n * 100, 1),
        "la": round(sum(las) / len(las), 1) if las else None,
        "fb_pct": round(fb / n * 100, 1),
    }


# --------------------------------------------------------------------------- #
# Weighted blend
# --------------------------------------------------------------------------- #
def _adjust(weights, small_2026: bool, low_recent: bool):
    l30, s26, s25 = weights
    if small_2026:                 # trust 2026 less, lean on 2025 baseline
        s25 += s26 * 0.5
        s26 *= 0.5
    if low_recent:                 # trust the tiny recent window less
        s26 += l30 * 0.5
        l30 *= 0.5
    return (l30, s26, s25)


def blend(vals: dict, weights) -> Optional[float]:
    """Weighted mean of {'l30','s2026','s2025'} dropping None and renormalizing."""
    pairs = [(vals.get("l30"), weights[0]), (vals.get("s2026"), weights[1]),
             (vals.get("s2025"), weights[2])]
    pairs = [(v, w) for v, w in pairs if v is not None and w > 0]
    tot = sum(w for _, w in pairs)
    if not tot:
        return None
    return round(sum(v * w for v, w in pairs) / tot, 2)


def weighted_profile(s2025: Optional[dict], cur: dict, l30: dict, bet: str = "HR",
                     pa_2026: Optional[int] = None) -> dict:
    """Weighted barrel%/EV/hard-hit% across windows (power profile by default)."""
    small_2026 = (pa_2026 is not None and pa_2026 < SMALL_PA_2026)
    low_recent = (l30.get("n", 0) or 0) < LOW_RECENT_BBE
    w = _adjust(BAT_WEIGHTS.get(bet, BAT_WEIGHTS["HR"]), small_2026, low_recent)
    s2025 = s2025 or {}
    out = {}
    for metric, k25, kcur, kl30 in (("barrel", "barrel_pct", "barrel_pct", "barrel_pct"),
                                    ("ev", "avg_ev", "avg_ev", "ev"),
                                    ("hardhit", "hardhit_pct", "hardhit_pct", "hardhit_pct")):
        out[metric] = blend({"s2025": _f(s2025.get(k25)), "s2026": _f(cur.get(kcur)),
                             "l30": _f(l30.get(kl30))}, w)
    warnings = []
    if small_2026:
        warnings.append("SMALL_SAMPLE_2026_BATTER")
    if low_recent:
        warnings.append("LOW_RECENT_SAMPLE")
    out["weights"] = {"l30": round(w[0], 3), "s2026": round(w[1], 3), "s2025": round(w[2], 3)}
    out["warnings"] = warnings
    return out


# --------------------------------------------------------------------------- #
# Trend grade
# --------------------------------------------------------------------------- #
def batter_trend(s2025: Optional[dict], cur: dict, l30: dict,
                 pa_2026: Optional[int] = None) -> dict:
    reasons: List[str] = []
    score = 0
    small = (pa_2026 is not None and pa_2026 < SMALL_PA_2026)
    b25, b26 = _f((s2025 or {}).get("barrel_pct")), _f(cur.get("barrel_pct"))
    e25, e26 = _f((s2025 or {}).get("avg_ev")), _f(cur.get("avg_ev"))
    label = "SAME_PROFILE"
    if b25 is not None and b26 is not None:
        d = b26 - b25
        if d >= 3:
            score += 2
            reasons.append(f"2026 barrel rate up from {b25:.1f}% to {b26:.1f}%")
            label = "IMPROVING_POWER_PROFILE"
        elif d <= -3:
            score -= 2
            reasons.append(f"2026 barrel rate down from {b25:.1f}% to {b26:.1f}%")
            label = "DECLINING_POWER_PROFILE"
    if e25 is not None and e26 is not None:
        d = e26 - e25
        if d >= 1.5:
            score += 1
            reasons.append(f"2026 EV up from {e25:.1f} to {e26:.1f}")
        elif d <= -1.5:
            score -= 1
            reasons.append(f"2026 EV down from {e25:.1f} to {e26:.1f}")
    le = l30.get("ev")
    if le is not None and e26 is not None:
        if le >= e26 + 1.5:
            reasons.append(f"recent EV ({le:.1f}) above season baseline")
        elif le <= e26 - 1.5:
            reasons.append(f"recent EV ({le:.1f}) below season baseline")
            score -= 1
    if small:
        label = "SMALL_SAMPLE"
        reasons.append(f"small 2026 sample ({pa_2026} PA) — leaning on 2025 baseline")
    grade = "A" if score >= 3 else "B" if score >= 1 else "C" if score >= -1 else "D"
    return {"grade": grade, "label": label, "score": score,
            "reasons": reasons[:5]}


def pitcher_trend(s2025: Optional[dict], cur: dict, mix_change: dict,
                  ip_2026: Optional[float] = None) -> dict:
    reasons: List[str] = []
    score = 0
    small = (ip_2026 is not None and ip_2026 < SMALL_IP_2026)
    h25, h26 = _f((s2025 or {}).get("hr9")), _f(cur.get("hr9"))
    b25, b26 = _f((s2025 or {}).get("barrel_pct_allowed")), _f(cur.get("barrel_pct_allowed"))
    k25, k26 = _f((s2025 or {}).get("k_pct")), _f(cur.get("k_pct"))
    label = "SAME_PROFILE"
    if h25 is not None and h26 is not None and h26 - h25 >= 0.3:
        score += 2
        reasons.append(f"more HR-prone in 2026 (HR/9 {h25:.2f} -> {h26:.2f})")
        label = "PITCHER_REGRESSION_RISK"
    if b25 is not None and b26 is not None and b26 - b25 >= 2:
        score += 1
        reasons.append(f"more hard contact in 2026 (barrels {b25:.1f}% -> {b26:.1f}%)")
    if k25 is not None and k26 is not None and k26 - k25 <= -2:
        score += 1
        reasons.append(f"strikeouts down in 2026 (K% {k25:.1f}% -> {k26:.1f}%)")
    if mix_change.get("changed"):
        score += 1
        reasons.append("pitch mix shifted vs 2025 " + mix_change.get("summary", ""))
        if label == "SAME_PROFILE":
            label = "PITCH_MIX_CHANGE"
    if small:
        reasons.append(f"small 2026 sample ({ip_2026} IP)")
    grade = "A" if score >= 3 else "B" if score >= 1 else "C"
    return {"grade": grade, "label": label, "score": score, "reasons": reasons[:5],
            "more_attackable_2026": score >= 2}


# --------------------------------------------------------------------------- #
# Pitch-mix change
# --------------------------------------------------------------------------- #
def pitch_mix_change(a2025: Optional[Dict[str, float]],
                     a2026: Optional[Dict[str, float]], thresh: float = 8.0) -> dict:
    a2025 = a2025 or {}
    a2026 = a2026 or {}
    if not a2025 or not a2026:
        return {"changed": False, "deltas": {}, "flags": [], "summary": "",
                "warning": "NO_2025_ARSENAL" if not a2025 else None}
    deltas = {}
    flags = []
    for pt in set(a2025) | set(a2026):
        d = round((a2026.get(pt, 0.0)) - (a2025.get(pt, 0.0)), 1)
        deltas[pt] = d
        if abs(d) >= thresh:
            flags.append(f"{pt} {'+' if d > 0 else ''}{d:.0f}pp")
    changed = bool(flags)
    return {"changed": changed, "deltas": deltas, "flags": flags,
            "summary": ", ".join(flags), "warning": None}


# --------------------------------------------------------------------------- #
# Extract a compact baseline dict from a season Batter/Pitcher object
# --------------------------------------------------------------------------- #
def batter_baseline(b) -> Optional[dict]:
    if b is None:
        return None
    return {"pa": b.pa, "ba": b.ba, "slg": b.slg, "iso": b.iso, "woba": b.woba,
            "xwoba": b.xwoba, "xslg": b.xslg, "barrel_pct": b.barrel_pct,
            "avg_ev": b.avg_ev, "hardhit_pct": b.hardhit_pct, "la_avg": b.la_avg,
            "fb_pct": b.fb_pct, "pull_pct": b.pull_pct}


def pitcher_baseline(p) -> Optional[dict]:
    if p is None:
        return None
    return {"ip": p.ip, "era": p.era, "hr9": p.hr9, "hrfb_pct": p.hrfb_pct,
            "barrel_pct_allowed": p.barrel_pct_allowed, "avg_ev_allowed": p.avg_ev_allowed,
            "hardhit_pct_allowed": p.hardhit_pct_allowed, "k_pct": p.k_pct,
            "whiff_pct": p.whiff_pct, "fb_pct": p.fb_pct, "gb_pct": p.gb_pct}
