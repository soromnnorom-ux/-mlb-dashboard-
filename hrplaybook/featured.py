"""Phase 12/13/18 — decision-layer logic (pure, dict-based so it tests offline).

Turns the flat matchup/pitcher/game records (as written to the CSVs and served
by the web layer) into:
  * market_scores()  -> transparent 0-100 HR/TB/HRR/Hit scores WITH breakdowns
  * reasons() / red_flags()
  * best5()          -> Today's Best 5 featured cards
  * top_by_market()  -> Top-5 lists
  * pitchers_to_attack()
  * slate_summary() / slate_read()  -> Phase 18 plain-English read

Everything is additive: a score is the sum of its breakdown points, so the UI
can always show exactly how a number was built (non-negotiable rule).
"""
from __future__ import annotations

from typing import Dict, List, Optional

MARKETS = ["HR", "TB", "HRR", "Hits"]

# component cap allocations per market (each set sums to ~100)
CAPS = {
    "HR":   {"pitchmix": 25, "pitcher": 20, "env": 18, "contact": 20, "edges": 12, "bullpen": 5},
    "TB":   {"contact": 28, "pitchmix": 22, "env": 16, "pitcher": 14, "edges": 12, "spot": 8},
    "HRR":  {"contact": 22, "spot": 18, "env": 16, "bullpen": 16, "pitcher": 14, "edges": 14},
    "Hits": {"contact": 30, "form": 22, "platoon": 14, "pitchmix": 16, "spot": 10, "pitcher": 8},
}
LABELS = {
    "pitchmix": "Pitch mix", "pitcher": "Pitcher weakness", "env": "Environment",
    "contact": "Contact quality", "edges": "Recent/edges", "bullpen": "Bullpen",
    "spot": "Lineup spot", "form": "Recent form (L30)", "platoon": "Platoon",
}
SPOT_FRAC = {1: 1.0, 2: .95, 3: .9, 4: .82, 5: .7, 6: .5, 7: .35, 8: .2, 9: .1}


def _f(d: dict, k: str) -> Optional[float]:
    v = d.get(k)
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _split(s) -> List[str]:
    return [x for x in str(s or "").split("|") if x]


def tags_of(m: dict) -> List[str]:
    return _split(m.get("tags"))


def markets_of(m: dict) -> List[str]:
    return [seg.split(":")[0] for seg in _split(m.get("bets"))]


def line_for(m: dict, market: str) -> str:
    for seg in _split(m.get("bets")):
        if seg.split(":")[0] == market:
            return seg.split(":", 1)[1] if ":" in seg else ""
    return ""


def _fracs(m: dict) -> Dict[str, float]:
    env = _f(m, "env_score")
    pit = _f(m, "pitcher_score")
    bvpm = _f(m, "barrel_vs_pm")
    barrel = _f(m, "barrel_pct")
    hh = _f(m, "hardhit_pct")
    ev = _f(m, "avg_ev")
    bp = _f(m, "opp_bullpen_hr9")
    l30 = _f(m, "l30_avg")
    order = _f(m, "order")
    tags = tags_of(m)
    plat = (m.get("platoon") or "neutral")

    contact_candidates = []
    if barrel is not None:
        contact_candidates.append(barrel / 16.0)
    if hh is not None:
        contact_candidates.append(hh / 55.0)
    if ev is not None:
        contact_candidates.append((ev - 86.0) / 16.0)
    contact = max(contact_candidates) if contact_candidates else 0.0

    edges = (0.34 * ("MISSED_HR" in tags) + 0.34 * ("HOT_CONTACT" in tags)
             + 0.16 * ("REGRESSION_SPOT" in tags) + 0.16 * ("LATE_HR" in tags))

    return {
        "env": _clip(((env if env is not None else 0) + 3) / 9.0),
        "pitcher": _clip((pit or 0) / 7.0),
        "pitchmix": _clip(((bvpm if bvpm is not None else 5) - 5) / 15.0),
        "contact": _clip(contact),
        "edges": _clip(edges),
        "bullpen": _clip((((bp if bp is not None else 1.0)) - 1.0) / 0.6),
        "spot": SPOT_FRAC.get(int(order), 0.4) if order else 0.4,
        "form": _clip(((l30 if l30 is not None else .22) - .22) / .13),
        "platoon": {"fav": 1.0, "neutral": 0.5, "unfav": 0.0}.get(plat, 0.5),
    }


def grade_from_score(s: float) -> str:
    return ("A+" if s >= 85 else "A" if s >= 72 else "B" if s >= 58
            else "C" if s >= 42 else "D")


def stars(s: float) -> int:
    return (5 if s >= 85 else 4 if s >= 70 else 3 if s >= 55 else 2 if s >= 40 else 1)


def market_scores(m: dict) -> Dict[str, dict]:
    """{market: {score, grade, stars, breakdown:[{label,pts}]}} for all 4 markets."""
    fr = _fracs(m)
    out: Dict[str, dict] = {}
    dead = (m.get("env_tier") == "dead-air")
    for mk in MARKETS:
        bd = []
        total = 0
        for comp, cap in CAPS[mk].items():
            pts = round(cap * fr[comp])
            if pts:
                bd.append({"label": LABELS[comp], "pts": pts})
                total += pts            # score == sum(breakdown), exactly additive
        score = int(_clip(total, 0, 100))
        if dead and mk == "HR":          # never tout HR in dead air
            score = min(score, 35)
        bd.sort(key=lambda x: -x["pts"])
        out[mk] = {"score": score, "grade": grade_from_score(score),
                   "stars": stars(score), "breakdown": bd}
    return out


def reasons(m: dict, market: str) -> List[str]:
    out = []
    bvpm = _f(m, "barrel_vs_pm")
    if bvpm is not None and bvpm >= 10:
        tier = "elite" if bvpm >= 20 else "strong" if bvpm >= 15 else "good"
        out.append(f"{tier.capitalize()} pitch-mix edge (B%PM {bvpm:.0f}%)")
    if (m.get("env_tier") in ("elite", "good")):
        out.append(f"{m.get('env_tier').capitalize()} hitting environment")
    pit = _f(m, "pitcher_score")
    if pit is not None and pit >= 3:
        out.append(f"Attackable SP ({m.get('opp_sp','')})")
    tags = tags_of(m)
    if "HOT_CONTACT" in tags:
        out.append("Hot contact cluster (recent 95+ EV)")
    if "MISSED_HR" in tags:
        out.append("Missed-HR candidate (smoked one that stayed in)")
    if "REGRESSION_SPOT" in tags:
        out.append("Regression spot — pitcher due for a blow-up")
    if "LATE_HR" in tags:
        out.append("Weak opposing bullpen (late HR chance)")
    if m.get("platoon") == "fav":
        out.append("Favorable platoon")
    l30 = _f(m, "l30_avg")
    if market == "Hits" and l30 is not None and l30 >= .3:
        out.append(f"Hot at the plate (L30 {l30:.3f})")
    return out[:5]


def red_flags(m: dict, market: str) -> List[str]:
    out = []
    if m.get("lineup_state") == "projected":
        out.append("Projected lineup (not confirmed)")
    if m.get("lineup_state") in ("none", "unknown"):
        out.append("No lineup yet")
    if market == "HR" and m.get("env_tier") == "dead-air":
        out.append("Dead-air park (HR suppressed)")
    order = _f(m, "order")
    if order and order >= 7:
        out.append("Bottom of the order (fewer PAs)")
    if m.get("platoon") == "unfav":
        out.append("Unfavorable platoon")
    bvpm_n = _f(m, "barrel_vs_pm_bbe")
    if bvpm_n is not None and bvpm_n < 15:
        out.append(f"Small pitch-mix sample (n={int(bvpm_n)})")
    if "NO_METRICS" in tags_of(m):
        out.append("No Statcast match")
    if m.get("value") == "-EV":
        out.append("Negative value vs odds")
    return out[:4]


def _eligible(m: dict, market: str) -> bool:
    return market in markets_of(m)


def top_by_market(matchups: List[dict], market: str, n: int = 5) -> List[dict]:
    rows = []
    for m in matchups:
        if not _eligible(m, market):
            continue
        sc = market_scores(m)[market]
        rows.append({
            "batter": m.get("batter"), "team": m.get("team"), "opp_team": m.get("opp_team"),
            "opp_sp": m.get("opp_sp"), "batter_id": m.get("batter_id"),
            "line": line_for(m, market), "value": m.get("value"),
            "score": sc["score"], "grade": sc["grade"], "stars": sc["stars"],
            "breakdown": sc["breakdown"], "reasons": reasons(m, market),
            "red_flags": red_flags(m, market),
            "model_prob": _f(m, "model_hr_prob") if market == "HR"
            else _f(m, "model_tb_prob") if market == "TB" else None,
        })
    rows.sort(key=lambda r: -r["score"])
    return rows[:n]


def pitchers_to_attack(pitchers: List[dict], games: List[dict], n: int = 5) -> List[dict]:
    sp_game = {}
    for g in games:
        for k in ("away_sp", "home_sp"):
            if g.get(k):
                sp_game[g[k]] = g.get("matchup", "")
    rows = []
    for p in pitchers:
        sc = _f(p, "pitcher_score") or 0
        reasons_p = []
        hr9 = _f(p, "hr9")
        if hr9 is not None and hr9 >= 1.3:
            reasons_p.append(f"HR/9 {hr9:.2f}")
        hrfb = _f(p, "hrfb_pct")
        if hrfb is not None and hrfb >= 13:
            reasons_p.append(f"HR/FB {hrfb:.0f}%")
        ba = _f(p, "barrel_pct_allowed")
        if ba is not None and ba >= 8:
            reasons_p.append(f"barrels allowed {ba:.0f}%")
        k = _f(p, "k_pct")
        if k is not None and k < 20:
            reasons_p.append(f"low K {k:.0f}%")
        if str(p.get("regression_flag")).lower() in ("true", "1"):
            reasons_p.append("PENDING BLOWUP")
        # 0-100 attack score from pitcher_score (0..~9)
        ascore = int(round(_clip(sc / 8.0) * 100))
        rows.append({
            "name": p.get("name"), "game": sp_game.get(p.get("name"), ""),
            "score": ascore, "grade": grade_from_score(ascore), "stars": stars(ascore),
            "hr9": hr9, "reasons": reasons_p,
        })
    rows = [r for r in rows if r["game"]]      # only today's probables
    rows.sort(key=lambda r: -r["score"])
    return rows[:n]


def best5(matchups: List[dict], pitchers: List[dict], games: List[dict]) -> dict:
    out = {}
    for mk in MARKETS:
        top = top_by_market(matchups, mk, 1)
        out[mk] = top[0] if top else None
    pa = pitchers_to_attack(pitchers, games, 1)
    out["pitcher"] = pa[0] if pa else None
    return out


def slate_summary(games: List[dict], matchups: List[dict], pitchers: List[dict]) -> dict:
    elite = [g for g in games if g.get("env_tier") == "elite"]
    good = [g for g in games if g.get("env_tier") == "good"]
    dead = [g for g in games if g.get("env_tier") == "dead-air"]
    attackable = [p for p in pitchers if (_f(p, "pitcher_score") or 0) >= 3]
    best_env = max(games, key=lambda g: _f(g, "env_score") or -99, default=None)
    worst_env = min(games, key=lambda g: _f(g, "env_score") or 99, default=None)
    best_hr = top_by_market(matchups, "HR", 1)
    best_tb = top_by_market(matchups, "TB", 1)
    top_pitcher = pitchers_to_attack(pitchers, games, 1)
    # slate grade from #good-env + #attackable
    pts = len(elite) * 2 + len(good) + len(attackable)
    grade = ("A+" if pts >= 12 else "A" if pts >= 9 else "B" if pts >= 6
             else "C" if pts >= 3 else "D")
    return {
        "grade": grade, "games": len(games),
        "elite_env": len(elite), "good_env": len(good), "dead_air": len(dead),
        "attackable_pitchers": len(attackable),
        "best_env_game": (best_env or {}).get("matchup"),
        "worst_env_game": (worst_env or {}).get("matchup"),
        "best_hr": best_hr[0] if best_hr else None,
        "best_tb": best_tb[0] if best_tb else None,
        "top_pitcher": top_pitcher[0] if top_pitcher else None,
    }


def slate_read(games: List[dict], matchups: List[dict], pitchers: List[dict]) -> dict:
    s = slate_summary(games, matchups, pitchers)
    hr_plays = [m for m in matchups if _eligible(m, "HR")
                and m.get("env_tier") != "dead-air"]
    tb_plays = [m for m in matchups if _eligible(m, "TB")]
    # compare quality: avg of top-5 HR vs TB scores
    hr_top = top_by_market(matchups, "HR", 5)
    tb_top = top_by_market(matchups, "TB", 5)
    hr_q = sum(r["score"] for r in hr_top) / len(hr_top) if hr_top else 0
    tb_q = sum(r["score"] for r in tb_top) / len(tb_top) if tb_top else 0
    lean = "HR" if hr_q >= tb_q + 4 else "TB" if tb_q >= hr_q + 4 else "balanced"

    parts = [f"Slate grade **{s['grade']}** — {s['games']} games, "
             f"{s['elite_env']} elite + {s['good_env']} good hitting environments, "
             f"{s['attackable_pitchers']} attackable starters."]
    if s["best_env_game"]:
        parts.append(f"Best environment: **{s['best_env_game']}**.")
    if s["top_pitcher"]:
        tp = s["top_pitcher"]
        parts.append(f"Most attackable pitcher: **{tp['name']}** "
                     f"({', '.join(tp['reasons']) or 'weak profile'}).")
    if s["dead_air"]:
        parts.append(f"{s['dead_air']} dead-air game(s) — fade HRs there, TB still live.")
    if lean == "HR":
        parts.append("HR spots look stronger than TB today.")
    elif lean == "TB":
        parts.append("Total Bases look stronger than HR today — lean TB.")
    else:
        parts.append("HR and TB opportunities look comparable.")
    weak = s["grade"] in ("C", "D") or (s["elite_env"] + s["good_env"] == 0)
    if weak:
        parts.append("⚠️ Not a strong HR slate — be selective or lean TB/HRR; don't force plays.")

    return {"grade": s["grade"], "lean": lean, "weak": weak, "text": " ".join(parts),
            "summary": s}
