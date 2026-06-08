"""Bet-type cards: cards_TB.md / cards_HR.md / cards_HRR.md / cards_Hits.md."""
from __future__ import annotations

from pathlib import Path
from typing import List

from ..config import Config
from ..model.schemas import Matchup
from ..util import now_stamp
from .render import ev_logs_str, fmt_pct, pitcher_summary

BET_TYPES = ["TB", "HR", "HRR", "Hits"]
TITLES = {
    "TB": "Total Bases (primary edge)",
    "HR": "Home Run (capped premium)",
    "HRR": "Home Run/Run (lower variance)",
    "Hits": "Hits (contact-stable)",
}


def _pct(p) -> str:
    return f"{p*100:.0f}%" if p is not None else "—"


def _odds_cell(m: Matchup, bet: str) -> str:
    o = m.odds_by_bet.get(bet)
    if o is None:
        return "—"
    ev = m.ev_by_bet.get(bet)
    return f"{o:+d} ({ev:+.2f})" if ev is not None else f"{o:+d}"


def _row(m: Matchup, bet: str) -> str:
    b = m.batter
    val = m.value_by_bet.get(bet, m.value)
    return ("| {name} | {team} | {plat} | {sp} | {env} | {wk} | {pm} | {model} "
            "| {ev} | {tags} | {line} | {odds} | {val} |").format(
        name=b.name,
        team=b.team,
        plat={"fav": "✓", "unfav": "✗", "neutral": "·"}.get(m.platoon, "·"),
        sp=(m.pitcher.name if m.pitcher else m.opp_team),
        env=f"{m.env_tier}({m.env_score})",
        wk=pitcher_summary(m.pitcher),
        pm=fmt_pct(b.barrel_vs_pm),
        model=_pct(m.prob_by_bet.get(bet)),
        ev=ev_logs_str(b.recent_ev_logs, "md"),
        tags=" ".join(f"`{t}`" for t in m.tags) or "—",
        line=m.bets.get(bet, ""),
        odds=_odds_cell(m, bet),
        val=val,
    )


def _card_md(bet: str, matchups: List[Matchup], cfg: Config, date: str) -> str:
    pool = [m for m in matchups if bet in m.bets]
    priced = any(m.ev_by_bet.get(bet) is not None for m in pool)
    # rank by EV when odds are priced; otherwise by the composite play score
    rows = sorted(
        pool,
        key=lambda m: (m.ev_by_bet.get(bet, float("-inf")) if priced else m.play_score),
        reverse=True,
    )
    if bet == "HR":
        rows = rows[: cfg.max_plays]
    rank_note = "ranked by EV" if priced else "ranked by play score"
    head = (
        f"# {TITLES[bet]} — {date}\n\n"
        f"_Generated {now_stamp()} · {len(rows)} plays · {rank_note}_\n\n"
        "| Player | Team | Plt | vs SP | Env | SP weakness | B% vs PM | Model | "
        "EV logs | Tags | Line | Odds (EV) | Value |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    if not rows:
        return head + "| _no plays_ |" + " |" * 12 + "\n"
    return head + "\n".join(_row(m, bet) for m in rows) + "\n"


def write_cards(outdir: str | Path, matchups: List[Matchup], cfg: Config,
                date: str) -> List[str]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for bet in BET_TYPES:
        path = outdir / f"cards_{bet}.md"
        path.write_text(_card_md(bet, matchups, cfg, date))
        written.append(str(path))
    return written
