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


def _row(m: Matchup, bet: str) -> str:
    b = m.batter
    return "| {name} | {team} | {sp} | {env} | {wk} | {pm} | {ev} | {tags} | {line} | {val} |".format(
        name=b.name,
        team=b.team,
        sp=(m.pitcher.name if m.pitcher else m.opp_team),
        env=f"{m.env_tier}({m.env_score})",
        wk=pitcher_summary(m.pitcher),
        pm=fmt_pct(b.barrel_vs_pm),
        ev=ev_logs_str(b.recent_ev_logs, "md"),
        tags=" ".join(f"`{t}`" for t in m.tags) or "—",
        line=m.bets.get(bet, ""),
        val=m.value,
    )


def _card_md(bet: str, matchups: List[Matchup], cfg: Config, date: str) -> str:
    rows = sorted(
        [m for m in matchups if bet in m.bets],
        key=lambda m: m.play_score, reverse=True,
    )
    if bet == "HR":
        rows = rows[: cfg.max_plays]
    head = (
        f"# {TITLES[bet]} — {date}\n\n"
        f"_Generated {now_stamp()} · {len(rows)} plays_\n\n"
        "| Player | Team | vs SP | Env | SP weakness | B% vs PM | EV logs | Tags | Line | Value |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    if not rows:
        return head + "| _no plays_ | | | | | | | | | |\n"
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
