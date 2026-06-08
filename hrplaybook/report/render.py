"""Display helpers + view builders shared by the cheat sheet and cards."""
from __future__ import annotations

from typing import List, Optional

from ..model.schemas import Game, Matchup, Pitcher


def fmt3(x: Optional[float]) -> str:
    """Baseball rate, leading-zero stripped: 0.312 -> '.312'."""
    if x is None:
        return "--"
    s = f"{x:.3f}"
    return s[1:] if s.startswith("0.") else s


def fmt_pct(x: Optional[float], digits: int = 1) -> str:
    if x is None:
        return "--"
    return f"{x:.{digits}f}%"


def fmt_num(x: Optional[float], digits: int = 1) -> str:
    if x is None:
        return "--"
    return f"{x:.{digits}f}"


def ev_logs_str(logs: List[float], bold: str = "md") -> str:
    if not logs:
        return "--"
    parts = []
    for v in logs:
        s = str(int(round(v)))
        if v >= 100:
            s = f"**{s}**" if bold == "md" else (f"<b>{s}</b>" if bold == "html" else s)
        parts.append(s)
    return ", ".join(parts)


def l30_str(m: Matchup) -> str:
    b = m.batter
    if b.l30_ab:
        return f"({b.l30_h}/{b.l30_ab} {fmt3(b.l30_avg)})"
    return "(L30 n/a)"


def pitcher_summary(p: Optional[Pitcher]) -> str:
    if not p:
        return "SP TBD"
    bits = []
    if p.hr9 is not None:
        bits.append(f"HR/9 {p.hr9}")
    if p.hrfb_pct is not None:
        bits.append(f"HR/FB {p.hrfb_pct}%")
    if p.whiff_pct is not None:
        bits.append(f"{p.whiff_pct}% whiff")
    if p.barrel_pct_allowed is not None:
        bits.append(f"brl {p.barrel_pct_allowed}%")
    flags = []
    if p.fastball_usage is not None and p.fastball_usage >= 55:
        flags.append("FB-heavy")
    if p.regression_flag:
        flags.append("🔥REGRESSION")
    if p.small_sample:
        flags.append("small-sample")
    out = ", ".join(bits) if bits else "no SP data"
    if flags:
        out += " [" + ", ".join(flags) + "]"
    return out


def matchup_view(m: Matchup, fmt: str = "md") -> dict:
    b = m.batter
    p = m.pitcher
    return {
        "name": b.name,
        "team": b.team,
        "order": b.batting_order,
        "opp_team": m.opp_team,
        "opp_sp": p.name if p else (m.game.home_pitcher_name if m.side == "away"
                                    else m.game.away_pitcher_name) or "TBD",
        "throws": p.throws if p else None,
        "env_tier": m.env_tier,
        "env_score": m.env_score,
        "pitcher_score": m.pitcher_score,
        "pitcher_summary": pitcher_summary(p),
        "barrel_vs_pm": fmt_pct(b.barrel_vs_pm),
        "barrel_vs_pm_bbe": b.barrel_vs_pm_bbe,
        "barrel_pct": fmt_pct(b.barrel_pct),
        "avg_ev": fmt_num(b.avg_ev),
        "hardhit_pct": fmt_pct(b.hardhit_pct),
        "iso": fmt3(b.iso),
        "slg": fmt3(b.slg),
        "la_avg": fmt_num(b.la_avg),
        "ev_logs": ev_logs_str(b.recent_ev_logs, fmt),
        "l30": l30_str(m),
        "tags": m.tags,
        "tags_str": " ".join(f"`{t}`" if fmt == "md" else t for t in m.tags),
        "play_score": m.play_score,
        "tier": m.tier,
        "bets": m.bets,
        "value": m.value,
        "lineup_state": b.lineup_state,
        "pulled_at": b.pulled_at,
        "perfect": m.perfect_profile,
    }


def slate_banner(games: List[Game]) -> dict:
    played = [g for g in games if g.home_pitcher_id or g.away_pitcher_id or g.env_tier]
    n = len(games)
    elite = sum(1 for g in games if g.env_tier == "elite")
    good = sum(1 for g in games if g.env_tier == "good")
    dead = sum(1 for g in games if g.env_tier == "dead-air")
    if elite + good == 0:
        note = "Thin slate -- lean TB, don't force HRs."
    elif elite >= 3:
        note = f"{elite} elite-env games -- HR-friendly slate."
    else:
        note = "Moderate slate -- be selective, cap HR plays."
    return {
        "n_games": n,
        "n_elite": elite,
        "n_good": good,
        "n_dead": dead,
        "note": note,
    }
