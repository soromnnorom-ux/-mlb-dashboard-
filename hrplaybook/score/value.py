"""§6.7 value filter. Compares transparent empirical model probabilities to the
implied probabilities from odds, and tags each bet +EV / fair / -EV.

The model is intentionally simple and inspectable (no training): a league base
rate scaled by batter contact quality, pitcher HR-proneness, park factor and the
platoon edge, projected over an expected-PA count. Without odds the value stays
'unknown' but the model probabilities are still emitted.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from ..config import Config
from ..model.schemas import Matchup


def implied_prob(american_odds: int) -> float:
    if american_odds < 0:
        return (-american_odds) / ((-american_odds) + 100.0)
    return 100.0 / (american_odds + 100.0)


def _profit_per_unit(american_odds: int) -> float:
    return american_odds / 100.0 if american_odds > 0 else 100.0 / (-american_odds)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def expected_pa(order: Optional[int], cfg: Config) -> float:
    vm = cfg.value_model
    slot = order if order and 1 <= order <= 9 else 5
    return max(vm.pa_floor, vm.pa_top - vm.pa_decay * (slot - 1))


def _multipliers(m: Matchup, cfg: Config):
    vm = cfg.value_model
    b = m.batter
    barrel = b.barrel_vs_pm if b.barrel_vs_pm is not None else (b.barrel_pct or vm.league_barrel)
    b_mult = _clamp(barrel / vm.league_barrel, 0.4, 2.5)
    hr9 = m.pitcher.hr9 if (m.pitcher and m.pitcher.hr9) else vm.league_hr9
    p_mult = _clamp(hr9 / vm.league_hr9, 0.5, 2.0)
    park_mult = m.game.park.hr_factor if (m.game and m.game.park) else 1.0
    plat = (vm.platoon_fav if m.platoon == "fav"
            else vm.platoon_unfav if m.platoon == "unfav" else 1.0)
    return b_mult, p_mult, park_mult, plat


def model_hr_prob(m: Matchup, cfg: Config) -> float:
    vm = cfg.value_model
    b_mult, p_mult, park_mult, plat = _multipliers(m, cfg)
    rate = vm.league_hr_pa * b_mult * p_mult * park_mult * plat
    # cap per-PA HR rate at a realistic ceiling (elite is ~0.08-0.09/PA)
    pa = expected_pa(m.batter.batting_order, cfg)
    return round(1 - (1 - _clamp(rate, 0.001, 0.09)) ** pa, 3)


def model_tb2_prob(m: Matchup, cfg: Config) -> float:
    """P(>=2 total bases). Poisson approximation on expected TB -- transparent,
    slightly conservative since real TB are clustered (a HR is 4 at once)."""
    _, p_mult, park_mult, plat = _multipliers(m, cfg)
    b = m.batter
    slg = b.xslg if b.xslg is not None else (b.slg if b.slg is not None else 0.400)
    pa = expected_pa(b.batting_order, cfg)
    ab = pa * 0.88
    lam = max(0.05, slg * ab * park_mult * (0.95 + 0.05 * p_mult) * plat)
    p_ge2 = 1 - math.exp(-lam) * (1 + lam)
    return round(_clamp(p_ge2, 0.0, 0.99), 3)


def _verdict(model_p: float, odds: int, cfg: Config) -> tuple[str, float]:
    imp = implied_prob(odds)
    ev = model_p * _profit_per_unit(odds) - (1 - model_p)
    thr = cfg.value_model.edge_threshold
    if model_p > imp * (1 + thr):
        v = "+EV"
    elif model_p < imp * (1 - thr):
        v = "-EV"
    else:
        v = "fair"
    return v, round(ev, 3)


def apply_value(matchups: List[Matchup], odds_maps: Dict[str, Dict[int, int]],
                cfg: Config) -> None:
    """odds_maps: {"HR": {pid: american}, "TB": {pid: american}}.

    Always fills model_prob/prob_by_bet; fills implied/value/EV where odds exist.
    """
    hr_odds = odds_maps.get("HR", {}) if odds_maps else {}
    tb_odds = odds_maps.get("TB", {}) if odds_maps else {}
    for m in matchups:
        pid = m.batter.player_id
        hp = model_hr_prob(m, cfg)
        tp = model_tb2_prob(m, cfg)
        m.model_prob = hp
        m.prob_by_bet["HR"] = hp
        m.prob_by_bet["TB"] = tp

        if pid in hr_odds:
            m.odds_by_bet["HR"] = hr_odds[pid]
            m.implied_prob = round(implied_prob(hr_odds[pid]), 3)
            v, ev = _verdict(hp, hr_odds[pid], cfg)
            m.value_by_bet["HR"], m.ev_by_bet["HR"] = v, ev
            m.value = v  # headline value tracks the HR bet
        if pid in tb_odds:
            m.odds_by_bet["TB"] = tb_odds[pid]
            v, ev = _verdict(tp, tb_odds[pid], cfg)
            m.value_by_bet["TB"], m.ev_by_bet["TB"] = v, ev
            if m.value == "unknown":
                m.value = v  # fall back to TB verdict when no HR odds
