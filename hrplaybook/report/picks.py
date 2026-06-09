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
        b = m.batter
        picks.append({
            "date": date,
            "batter_id": b.player_id,
            "batter": b.name,
            "team": b.team,
            "opp_team": m.opp_team,
            "opp_sp": m.pitcher.name if m.pitcher else None,
            "tier": m.tier,
            "play_score": m.play_score,
            "platoon": m.platoon,
            "bets": dict(m.bets),
            "odds": dict(m.odds_by_bet),
            "model_prob": dict(m.prob_by_bet),
            "value": dict(m.value_by_bet),
            # rich context snapshot for Model Performance (signal/grade analytics)
            "lineup_state": b.lineup_state,
            "env_tier": m.env_tier,
            "env_score": m.env_score,
            "pitcher_score": m.pitcher_score,
            "barrel_vs_pm": b.barrel_vs_pm,
            "barrel_pct": b.barrel_pct,
            "hardhit_pct": b.hardhit_pct,
            "avg_ev": b.avg_ev,
            "l30_avg": b.l30_avg,
            "order": b.batting_order,
            "opp_bullpen_hr9": m.opp_bullpen_hr9,
            "cluster_label": b.cluster_label,
            "missed_hr": b.missed_hr,
            "tags": list(dict.fromkeys(list(m.tags) + list(b.tags))),
        })
        # persist raw probs for ALL markets so HRR/Hits can calibrate over time
        # (HR/TB come from the model; HRR/Hits are score-derived in value_center).
        from .. import value_center
        pk = picks[-1]
        mp = dict(pk["model_prob"])
        for mk in ("HRR", "Hits"):
            if mk not in mp:
                rp = value_center.model_prob(pk, mk)
                if rp is not None:
                    mp[mk] = rp
        pk["model_prob"] = mp
    path = outdir / "picks.json"
    path.write_text(json.dumps(picks, indent=2))
    return str(path)


def load_picks(outdir: str | Path) -> List[dict]:
    path = Path(outdir) / "picks.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())
