"""§6.3 batter core-metric gate + §6.4 perfect HR profile."""
from __future__ import annotations

from typing import Tuple

from ..config import Config
from ..model.schemas import Batter


def passes_elite(b: Batter, cfg: Config) -> bool:
    e = cfg.thresholds.elite
    return (
        b.avg_ev is not None and b.avg_ev >= e.ev
        and b.barrel_pct is not None and b.barrel_pct >= e.barrel
        and b.hardhit_pct is not None and b.hardhit_pct >= e.hard_hit
    )


def passes_practical(b: Batter, cfg: Config) -> bool:
    g = cfg.thresholds.practical
    return (
        b.barrel_vs_pm is not None and b.barrel_vs_pm >= g.barrel_vs_pm
        # require a trustworthy sample: a barrel_vs_pm spike on a handful of
        # batted balls (or a season-stat fallback) does not pass the gate.
        and b.barrel_vs_pm_bbe >= g.min_bbe
    )


def gate_status(b: Batter, cfg: Config) -> Tuple[bool, str]:
    if cfg.gate == "elite":
        return passes_elite(b, cfg), "elite"
    return passes_practical(b, cfg), "practical"


def perfect_profile(b: Batter, cfg: Config) -> Tuple[int, bool]:
    """+2 in the EV/LA sweet spot; penalty for pop-up or ground-ball profiles."""
    pp = cfg.thresholds.perfect_profile
    bonus, perfect = 0, False
    if (b.avg_ev is not None and b.la_avg is not None
            and pp.ev_min <= b.avg_ev <= pp.ev_max
            and pp.la_min <= b.la_avg <= pp.la_max):
        perfect, bonus = True, 2
    if b.la_avg is not None and (b.la_avg > 40 or b.la_avg < 8):
        bonus -= 1
    return bonus, perfect


def score_batter(b: Batter, cfg: Config) -> dict:
    """Composite batter contribution (gate strength + elite components + profile)."""
    s = 0
    if b.barrel_vs_pm is not None:
        if b.barrel_vs_pm >= 20:
            s += 3
        elif b.barrel_vs_pm >= 15:
            s += 2
        elif b.barrel_vs_pm >= 10:
            s += 1
    e = cfg.thresholds.elite
    if b.avg_ev is not None and b.avg_ev >= e.ev:
        s += 1
    if b.barrel_pct is not None and b.barrel_pct >= e.barrel:
        s += 1
    if b.hardhit_pct is not None and b.hardhit_pct >= e.hard_hit:
        s += 1
    bonus, perfect = perfect_profile(b, cfg)
    s += bonus
    passed, kind = gate_status(b, cfg)
    return {
        "score": s,
        "perfect": perfect,
        "gate_passed": passed,
        "gate_kind": kind,
    }
