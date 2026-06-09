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
            "grades": slate_grades(matchups), "summary": s}


# --------------------------------------------------------------------------- #
# Per-market slate grades (Batch-2 correction)
# --------------------------------------------------------------------------- #
def slate_grades(matchups: List[dict]) -> Dict[str, str]:
    """HR / TB / HRR / Hits slate grades from the quality+depth of eligible plays.

    A market with no strong play can never grade A+ (the explicit rule).
    """
    out = {}
    for mk in MARKETS:
        top = top_by_market(matchups, mk, 5)
        if not top:
            out[mk] = "D"
            continue
        q = sum(r["score"] for r in top) / len(top)
        best = top[0]["score"]
        g = grade_from_score(q)
        if len(top) < 3:                 # thin board -> knock down a notch
            g = _notch_down(g)
        if best < 75 and g == "A+":      # no strong play -> cap below A+
            g = "A"
        if best < 60:
            g = _notch_down(g)
        out[mk] = g
    return out


_LADDER = ["A+", "A", "B", "C", "D"]


def _notch_down(g: str) -> str:
    i = _LADDER.index(g) if g in _LADDER else len(_LADDER) - 1
    return _LADDER[min(i + 1, len(_LADDER) - 1)]


# --------------------------------------------------------------------------- #
# Phase 6 — Weather Intelligence
# --------------------------------------------------------------------------- #
def _temp_pts(t: Optional[float]) -> float:
    if t is None:
        return 0.0
    if t >= 85:
        return 18
    if t >= 75:
        return 10
    if t >= 68:
        return 3
    if t >= 60:
        return 0
    return -12


def _wind_pts(mph: Optional[float], label: Optional[str]) -> float:
    if not label or label in ("unknown", "cross", "calm") or mph is None:
        return 0.0                       # do not overstate when direction unknown
    if label == "out":
        return 20 if mph >= 15 else 12 if mph >= 10 else 6 if mph >= 5 else 2
    if label == "in":
        return -18 if mph >= 15 else -12 if mph >= 10 else -6 if mph >= 5 else -2
    return 0.0


def _park_pts(hr_factor: Optional[float]) -> float:
    if hr_factor is None:
        return 0.0
    return max(-12.0, min(12.0, (hr_factor - 1.0) * 60.0))


def weather_scores(g: dict) -> dict:
    roof = (g.get("roof") or "open").lower()
    dome = roof == "closed"
    temp = _f(g, "temp_f")
    wind = _f(g, "wind_mph")
    label = (g.get("wind_out") or "unknown")
    hrf = _f(g, "park_hr_factor")
    wind_label = "dome" if dome else (label if label in ("out", "in", "cross", "calm") else "unknown")

    tp = 0.0 if dome else _temp_pts(temp)
    wp = 0.0 if dome else _wind_pts(wind, label)
    pp = _park_pts(hrf)

    hr = int(_clip(50 + tp + wp + pp, 0, 100))
    tb = int(_clip(50 + tp * 0.7 + wp * 0.6 + pp, 0, 100))
    run = int(_clip(50 + tp * 0.8 + wp * 0.7 + pp, 0, 100))
    overall = (hr + tb + run) / 3.0

    reasons = []
    if dome:
        reasons.append("Dome / closed roof — weather neutral")
    else:
        if temp is not None and temp >= 85:
            reasons.append(f"Hot ({temp:.0f}°F) — elite for carry")
        elif temp is not None and temp >= 75:
            reasons.append(f"Warm ({temp:.0f}°F)")
        elif temp is not None and temp < 60:
            reasons.append(f"Cold ({temp:.0f}°F) — suppresses carry")
        if wind_label == "out" and wind:
            reasons.append(f"Wind out {wind:.0f} mph")
        elif wind_label == "in" and wind:
            reasons.append(f"Wind in {wind:.0f} mph")
        elif wind_label == "unknown":
            reasons.append("Wind direction unknown")
    if hrf is not None and hrf >= 1.05:
        reasons.append(f"Hitter-friendly park ({hrf:.2f})")
    elif hrf is not None and hrf <= 0.95:
        reasons.append(f"Pitcher-friendly park ({hrf:.2f})")

    return {
        "matchup": g.get("matchup"), "venue": g.get("venue"), "time_utc": g.get("time_utc"),
        "temp_f": temp, "wind_mph": wind, "wind_label": wind_label,
        "wind_dir_deg": _f(g, "wind_dir_deg"), "precip_pct": _f(g, "precip_pct"),
        "roof": roof, "park_hr_factor": hrf,
        "hr": hr, "tb": tb, "run": run,
        "grade": grade_from_score(overall), "overall": int(round(overall)),
        "reasons": reasons,
    }


def weather_board(games: List[dict]) -> dict:
    scored = [weather_scores(g) for g in games]
    return {
        "games": sorted(scored, key=lambda x: -x["overall"]),
        "top_hr": sorted(scored, key=lambda x: -x["hr"])[:5],
        "top_tb": sorted(scored, key=lambda x: -x["tb"])[:5],
        "top_run": sorted(scored, key=lambda x: -x["run"])[:5],
        "bottom": sorted(scored, key=lambda x: x["overall"])[:5],
    }


# --------------------------------------------------------------------------- #
# Phase 7 — Pitchers To Attack (full)
# --------------------------------------------------------------------------- #
def pitcher_attack(p: dict) -> dict:
    hr9 = _f(p, "hr9")
    hrfb = _f(p, "hrfb_pct")
    barrel = _f(p, "barrel_pct_allowed")
    hh = _f(p, "hardhit_pct_allowed")
    ev = _f(p, "avg_ev_allowed")
    k = _f(p, "k_pct")
    whiff = _f(p, "whiff_pct")
    fbu = _f(p, "fastball_usage")
    fb = _f(p, "fb_pct")
    small = str(p.get("small_sample")).lower() in ("true", "1")

    def fr(x, lo, hi):
        if x is None:
            return None
        return _clip((x - lo) / (hi - lo))

    # HR-specific attackability
    hr_comp = [(_pick(fr(hr9, 0.8, 1.8), 0.4), 30), (_pick(fr(hrfb, 9, 20), 0.4), 22),
               (_pick(fr(barrel, 5, 12), 0.4), 18), (_pick(fr(fbu, 45, 70), 0.4), 14),
               (_pick(fr(fb, 22, 40), 0.4), 8), (_pick(1 - (fr(k, 14, 30) or .5), 0.4), 8)]
    hr_attack = int(_clip(sum(f * c for f, c in hr_comp), 0, 100))
    # TB / overall contact attackability
    tb_comp = [(_pick(fr(barrel, 5, 12), 0.4), 26), (_pick(fr(hh, 32, 46), 0.4), 24),
               (_pick(fr(ev, 86, 92), 0.4), 20), (_pick(1 - (fr(k, 14, 30) or .5), 0.4), 16),
               (_pick(1 - (fr(whiff, 18, 32) or .5), 0.4), 14)]
    tb_attack = int(_clip(sum(f * c for f, c in tb_comp), 0, 100))
    attack = int(round((hr_attack + tb_attack) / 2.0))

    reasons, flags = [], []
    if hr9 is not None and hr9 >= 1.3:
        reasons.append(f"HR/9 {hr9:.2f}")
    if hrfb is not None and hrfb >= 13:
        reasons.append(f"HR/FB {hrfb:.0f}%")
    if barrel is not None and barrel >= 8:
        reasons.append(f"barrels allowed {barrel:.0f}%")
    if hh is not None and hh >= 42:
        reasons.append(f"hard-hit allowed {hh:.0f}%")
    if ev is not None and ev >= 90:
        reasons.append(f"EV allowed {ev:.1f}")
    if k is not None and k < 20:
        reasons.append(f"low K {k:.0f}%")
    if fbu is not None and fbu >= 60:
        reasons.append(f"fastball-heavy {fbu:.0f}%")
    if k is not None and k >= 27:
        flags.append(f"high K {k:.0f}% (misses bats)")
    if barrel is not None and barrel < 6:
        flags.append("limits hard contact")
    if small:
        flags.append("small sample")

    # PENDING_BLOWUP: hard contact allowed but HR results lagging
    hard = ((barrel is not None and barrel >= 8) or (hh is not None and hh >= 42)
            or (ev is not None and ev >= 90))
    pending = bool(hard and (hr9 is not None and hr9 < 1.1))
    if pending:
        reasons.append("PENDING BLOWUP (hard contact, HRs lagging)")

    return {"attack": attack, "hr_attack": hr_attack, "tb_attack": tb_attack,
            "grade": grade_from_score(attack), "stars": stars(attack),
            "reasons": reasons, "red_flags": flags, "pending_blowup": pending}


def _pick(v, default):
    return default if v is None else v


def pitcher_attack_table(pitchers: List[dict], games: List[dict]) -> dict:
    sp_game = {}
    for g in games:
        for k in ("away_sp", "home_sp"):
            if g.get(k):
                sp_game[g[k]] = g.get("matchup", "")
    rows = []
    for p in pitchers:
        if p.get("name") not in sp_game:     # only today's probables
            continue
        a = pitcher_attack(p)
        rows.append({"name": p.get("name"), "game": sp_game.get(p.get("name"), ""),
                     "throws": p.get("throws"), "hr9": _f(p, "hr9"), **a})
    rows.sort(key=lambda r: -r["attack"])
    return {
        "all": rows,
        "top10": rows[:10],
        "best_hr": sorted(rows, key=lambda r: -r["hr_attack"])[:5],
        "best_tb": sorted(rows, key=lambda r: -r["tb_attack"])[:5],
        "avoid": [r for r in sorted(rows, key=lambda r: r["attack"]) if r["attack"] < 40][:5],
        "pending": [r for r in rows if r["pending_blowup"]],
    }


# --------------------------------------------------------------------------- #
# Phase 9 — Missed HR candidates
# --------------------------------------------------------------------------- #
def _missed_grade(ev, dist) -> str:
    if ev is None or dist is None:
        return "Moderate"            # flagged but detail not captured
    if ev >= 106 and dist >= 400:
        return "Extreme"
    if ev >= 103 and dist >= 385:
        return "High"
    return "Moderate"


def missed_hr_candidates(matchups: List[dict]) -> List[dict]:
    out = []
    for m in matchups:
        flagged = (str(m.get("missed_hr")).lower() in ("true", "1")
                   or "MISSED_HR" in tags_of(m))
        if not flagged:
            continue
        ev = _f(m, "missed_hr_ev")
        dist = _f(m, "missed_hr_dist")
        out.append({
            "batter": m.get("batter"), "team": m.get("team"), "batter_id": m.get("batter_id"),
            "opp_team": m.get("opp_team"), "opp_sp": m.get("opp_sp"),
            "date": m.get("missed_hr_date"), "ev": ev, "dist": dist,
            "la": _f(m, "missed_hr_la"), "pitch": m.get("missed_hr_pitch"),
            "grade": _missed_grade(ev, dist),
            "detail": ev is not None,
            "reasons": [r for r in [
                f"Smoked one {ev:.0f} mph / {dist:.0f} ft that stayed in" if ev else
                "Flagged missed-HR in recent window (detail not stored)",
                f"vs {m.get('opp_sp') or m.get('opp_team')} today",
            ] if r],
        })
    order = {"Extreme": 3, "High": 2, "Moderate": 1}
    out.sort(key=lambda r: (order.get(r["grade"], 0), r["ev"] or 0), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Phase 10 — Recent contact cluster
# --------------------------------------------------------------------------- #
def contact_clusters(matchups: List[dict]) -> List[dict]:
    out = []
    for m in matchups:
        label = m.get("cluster_label")
        e95, e100, e105 = _f(m, "ev95_w"), _f(m, "ev100_w"), _f(m, "ev105_w")
        if not label and e100 is None and "HOT_CONTACT" not in tags_of(m):
            continue
        out.append({
            "batter": m.get("batter"), "team": m.get("team"), "batter_id": m.get("batter_id"),
            "opp_team": m.get("opp_team"), "opp_sp": m.get("opp_sp"),
            "label": label or ("HOT" if "HOT_CONTACT" in tags_of(m) else "NORMAL"),
            "score": int(_f(m, "cluster_score") or 0),
            "ev95": int(e95) if e95 is not None else None,
            "ev100": int(e100) if e100 is not None else None,
            "ev105": int(e105) if e105 is not None else None,
            "ev100_l5g": int(_f(m, "ev100_l5g")) if _f(m, "ev100_l5g") is not None else None,
        })
    rank = {"NUCLEAR": 4, "HOT": 3, "NORMAL": 2, "COLD": 1}
    out.sort(key=lambda r: (rank.get(r["label"], 0), r["score"]), reverse=True)
    return out
