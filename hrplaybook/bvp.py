"""Batter-vs-Pitcher intelligence (Batch 10) — a SUPPORTING signal only.

BvP is seasoning, not the meal. It explains a matchup and adds a small capped
bonus/penalty, but it can never turn a bad play into a top play. Small samples
are flagged loudly and contribute nothing. Pure/leaf module (no network).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .util import is_barrel

HIT_EVENTS = {"single", "double", "triple", "home_run"}
NON_AB = {"walk", "intent_walk", "hit_by_pitch", "sac_fly", "sac_bunt",
          "sac_fly_double_play", "sac_bunt_double_play", "catcher_interf"}
BB_EVENTS = {"walk", "intent_walk"}
K_EVENTS = {"strikeout", "strikeout_double_play"}

# max share of a market's probability BvP may swing (guardrail; never exceeded)
MAX_WEIGHT = {"HR": 0.05, "TB": 0.08, "Hits": 0.10, "HRR": 0.08}


def sample_label(pa: int) -> str:
    if pa <= 2:
        return "TOO_SMALL"
    if pa <= 7:
        return "SMALL"
    if pa <= 15:
        return "USEFUL"
    return "STRONG"


def confidence(label: str) -> str:
    return {"TOO_SMALL": "very low", "SMALL": "low",
            "USEFUL": "medium", "STRONG": "higher"}.get(label, "very low")


def aggregate(rows: List[dict]) -> dict:
    """Aggregate pitch-level BvP rows into a stat line + contact profile."""
    pa_rows = [r for r in rows if r.get("events")]
    pa = len(pa_rows)
    singles = doubles = triples = hr = bb = k = hbp = non_ab = 0
    for r in pa_rows:
        e = r["events"]
        if e == "single":
            singles += 1
        elif e == "double":
            doubles += 1
        elif e == "triple":
            triples += 1
        elif e == "home_run":
            hr += 1
        if e in BB_EVENTS:
            bb += 1
        if e in K_EVENTS:
            k += 1
        if e == "hit_by_pitch":
            hbp += 1
        if e in NON_AB:
            non_ab += 1
    hits = singles + doubles + triples + hr
    ab = max(0, pa - non_ab)
    tb = singles + 2 * doubles + 3 * triples + 4 * hr
    bb_rows = [r for r in rows if r.get("launch_speed") is not None]
    evs = [r["launch_speed"] for r in bb_rows]
    las = [r["launch_angle"] for r in bb_rows if r.get("launch_angle") is not None]
    dists = [r["hit_distance_sc"] for r in bb_rows if r.get("hit_distance_sc") is not None]
    barrels = sum(1 for r in bb_rows if is_barrel(r["launch_speed"], r.get("launch_angle")))
    hardhit = sum(1 for r in bb_rows if r["launch_speed"] >= 95)
    ev100 = sum(1 for r in bb_rows if r["launch_speed"] >= 100)
    dates = [r["game_date"] for r in rows if r.get("game_date")]

    def _r(x, d=3):
        return round(x, d) if x is not None else None
    avg = _r(hits / ab) if ab else None
    slg = _r(tb / ab) if ab else None
    obp = _r((hits + bb + hbp) / pa) if pa else None
    ops = _r((obp or 0) + (slg or 0)) if (obp is not None and slg is not None) else None
    iso = _r((slg or 0) - (avg or 0)) if (slg is not None and avg is not None) else None
    label = sample_label(pa)
    return {
        "pa": pa, "ab": ab, "hits": hits, "singles": singles, "doubles": doubles,
        "triples": triples, "hr": hr, "bb": bb, "k": k, "tb": tb,
        "avg": avg, "slg": slg, "obp": obp, "ops": ops, "iso": iso,
        "avg_ev": round(sum(evs) / len(evs), 1) if evs else None,
        "max_ev": round(max(evs), 1) if evs else None,
        "avg_la": round(sum(las) / len(las), 1) if las else None,
        "best_distance": round(max(dists), 0) if dists else None,
        "barrels": barrels, "hardhit": hardhit, "ev100": ev100,
        "pitch_types": sorted({r["pitch_type"] for r in rows if r.get("pitch_type")}),
        "last_faced": max(dates) if dates else None,
        "sample_size": label, "confidence": confidence(label),
    }


def _score(s: dict) -> int:
    sc = 0
    if s["hr"] >= 1:
        sc += 2
    if s["doubles"] + s["triples"] + s["hr"] >= 2:
        sc += 1
    if s["barrels"] >= 1:
        sc += 1
    if s["ev100"] >= 2:
        sc += 1
    if s["hardhit"] >= 3:
        sc += 1
    if s["ab"] >= 5 and (s["slg"] or 0) >= 0.600:
        sc += 1
    if s["pa"] >= 5 and s["k"] / s["pa"] >= 0.40:
        sc -= 2
    if s["avg_ev"] is not None and s["avg_ev"] < 86 and s["hardhit"] == 0 and s["pa"] >= 4:
        sc -= 1
    if s["ab"] >= 5 and (s["slg"] or 0) < 0.250:
        sc -= 1
    return sc


def grade(s: dict) -> dict:
    """Return {score, grade, edge_label, reasons, tags}. TOO_SMALL contributes 0."""
    label = s["sample_size"]
    reasons: List[str] = []
    tags: List[str] = []
    if label == "TOO_SMALL":
        reasons.append(f"Only {s['pa']} career PA — too small to trust.")
        tags.append("BVP_TOO_SMALL")
        return {"score": 0, "grade": "TOO_SMALL", "edge_label": "TOO_SMALL_SAMPLE",
                "reasons": reasons, "tags": tags}
    sc = _score(s)
    if s["hr"] >= 1:
        reasons.append(f"{s['hr']} HR in {s['pa']} PA vs this pitcher")
    xbh = s["doubles"] + s["triples"] + s["hr"]
    if xbh >= 2:
        reasons.append(f"{xbh} extra-base hits in {s['pa']} PA")
    if s["ev100"] >= 2:
        reasons.append(f"{s['ev100']} balls 100+ EV vs this pitcher")
        tags.append("BVP_HARD_CONTACT")
    elif s["hardhit"] >= 3:
        reasons.append(f"{s['hardhit']} hard-hit balls vs this pitcher")
        tags.append("BVP_HARD_CONTACT")
    if s["pa"] >= 5 and s["k"] / s["pa"] >= 0.40:
        reasons.append(f"{s['k']} strikeouts in {s['pa']} PA — strikeout risk")
        tags.append("BVP_STRIKEOUT_RISK")
    if s["ab"] >= 5 and (s["slg"] or 0) < 0.250 and not reasons:
        reasons.append(f"weak history (SLG {s['slg']:.3f} in {s['ab']} AB)")
    g = ("A+" if sc >= 5 else "A" if sc >= 4 else "B" if sc >= 2
         else "C" if sc >= 0 else "D")
    if sc >= 4:
        edge = "ELITE_HISTORY"
        tags.append("BVP_GOOD_HISTORY")
    elif sc >= 2:
        edge = "GOOD_HISTORY"
        tags.append("BVP_GOOD_HISTORY")
    elif sc <= -2:
        edge = "BAD_HISTORY"
        tags.append("BVP_BAD_HISTORY")
    else:
        edge = "NEUTRAL"
    if not reasons:
        reasons.append(f"{s['pa']} PA, {s['hits']}-for-{s['ab']} — no standout signal")
    return {"score": sc, "grade": g, "edge_label": edge, "reasons": reasons, "tags": tags}


def max_weight(market: str, label: str) -> float:
    """Cap on BvP's probability swing for a market, scaled down for small samples."""
    cap = MAX_WEIGHT.get(market, 0.05)
    if label == "TOO_SMALL":
        return 0.0
    if label == "SMALL":
        return cap * 0.5
    return cap


def adjustment(s: dict, g: dict, market: str) -> float:
    """A small, capped signed probability nudge (display/context only).

    Magnitude never exceeds max_weight(market). Returns 0 for too-small samples.
    """
    cap = max_weight(market, s["sample_size"])
    if cap <= 0 or g["edge_label"] in ("NEUTRAL", "TOO_SMALL_SAMPLE"):
        return 0.0
    direction = 1 if "GOOD" in g["edge_label"] or "ELITE" in g["edge_label"] else -1
    strength = min(1.0, abs(g["score"]) / 5.0)
    return round(direction * cap * strength, 4)


def pitch_history(rows: List[dict], limit: int = 40) -> List[dict]:
    """Pitch-by-pitch rows for display (newest first), capped."""
    out = []
    for r in sorted(rows, key=lambda x: (x.get("game_date") or "", x.get("inning") or 0),
                    reverse=True)[:limit]:
        out.append({
            "date": r.get("game_date"), "inning": r.get("inning"),
            "count": f"{r.get('balls', '')}-{r.get('strikes', '')}",
            "pitch_type": r.get("pitch_type"), "speed": r.get("release_speed"),
            "result": r.get("events") or r.get("description"),
            "ev": r.get("launch_speed"), "la": r.get("launch_angle"),
            "distance": r.get("hit_distance_sc"),
            "hard_hit": (r.get("launch_speed") or 0) >= 95 if r.get("launch_speed") else None,
            "barrel": is_barrel(r.get("launch_speed"), r.get("launch_angle"))
            if r.get("launch_speed") is not None else None,
            "xwoba": r.get("xwoba"),
        })
    return out


def build(rows: Optional[List[dict]]) -> Optional[dict]:
    """Full BvP record from pitch rows, or None when no history exists."""
    if not rows:
        return None
    s = aggregate(rows)
    if s["pa"] == 0:
        return None
    g = grade(s)
    return {**s, **g, "pitch_history": pitch_history(rows)}
