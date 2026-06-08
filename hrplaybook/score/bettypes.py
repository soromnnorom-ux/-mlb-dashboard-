"""§6.8 bet-type mapping. TB is the bread-and-butter; HR is the capped premium
layer; HRR/Hits are the lower-variance layer."""
from __future__ import annotations

from ..config import Config
from ..model.schemas import Matchup
from . import env_tier_rank


def _contact_ok(b) -> bool:
    return any([
        b.hardhit_pct is not None and b.hardhit_pct >= 45,
        b.barrel_pct is not None and b.barrel_pct >= 8,
        b.barrel_vs_pm is not None and b.barrel_vs_pm >= 10,
    ])


def _strong_contact(b) -> bool:
    return any([
        b.hardhit_pct is not None and b.hardhit_pct >= 50,
        b.barrel_vs_pm is not None and b.barrel_vs_pm >= 15,
        b.barrel_pct is not None and b.barrel_pct >= 12,
    ])


def map_bets(m: Matchup, cfg: Config) -> Matchup:
    b = m.batter
    env_rank = env_tier_rank(m.env_tier)
    bets: dict[str, str] = {}

    # TB -- primary edge. Any batter clearing the contact gate in good+ env.
    if env_rank >= 2 and _contact_ok(b):
        bets["TB"] = "2+ TB" if _strong_contact(b) else "1.5 TB"
    elif _contact_ok(b) and env_rank >= 1:
        bets["TB"] = "1.5 TB"

    # HR -- Tier 1 only (cap enforced upstream in finalize_tiers).
    if m.tier == 1:
        bets["HR"] = "HR"

    # HRR -- good lineup spot + good env, less volatile than HR.
    good_spot = b.batting_order is not None and 1 <= b.batting_order <= 6
    if env_rank >= 2 and good_spot and _contact_ok(b):
        if m.tier == 1 and _strong_contact(b):
            bets["HRR"] = "2.5 HRR"
        else:
            bets["HRR"] = "1.5 HRR"

    # Hits -- contact-stable bats vs low-whiff... (high contact, good matchup).
    low_whiff_opp = m.pitcher and m.pitcher.whiff_pct is not None and m.pitcher.whiff_pct < 24
    stable = (b.l30_avg is not None and b.l30_avg >= 0.260) or (b.ba is not None and b.ba >= 0.270)
    if stable and _contact_ok(b) and (low_whiff_opp or env_rank >= 1):
        bets["Hits"] = "1+ Hits"

    m.bets = bets
    return m
