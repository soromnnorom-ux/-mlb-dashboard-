"""Machine-readable picks ledger written at run time so `grade` can score them
later against actual box-score results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..model.schemas import Matchup


def write_picks(outdir: str | Path, matchups: List[Matchup], date: str) -> str:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    picks = []
    for m in matchups:
        if not m.bets:
            continue
        picks.append({
            "date": date,
            "batter_id": m.batter.player_id,
            "batter": m.batter.name,
            "team": m.batter.team,
            "opp_team": m.opp_team,
            "opp_sp": m.pitcher.name if m.pitcher else None,
            "tier": m.tier,
            "play_score": m.play_score,
            "platoon": m.platoon,
            "bets": dict(m.bets),
            "odds": dict(m.odds_by_bet),
            "model_prob": dict(m.prob_by_bet),
            "value": dict(m.value_by_bet),
        })
    path = outdir / "picks.json"
    path.write_text(json.dumps(picks, indent=2))
    return str(path)


def load_picks(outdir: str | Path) -> List[dict]:
    path = Path(outdir) / "picks.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())
