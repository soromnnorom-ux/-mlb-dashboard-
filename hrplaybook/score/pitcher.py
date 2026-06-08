"""§6.2 Pitcher-weakness score -- attack pitchers, not hitters."""
from __future__ import annotations

from ..config import Config
from ..model.schemas import Pitcher


def score_pitcher(p: Pitcher, cfg: Config) -> Pitcher:
    t = cfg.thresholds.pitcher
    bd: dict[str, int] = {}

    # HR/9
    a = 0
    if p.hr9 is not None:
        a = 2 if p.hr9 >= t.hr9_high else (1 if p.hr9 >= t.hr9_mid else 0)
    bd["hr9"] = a

    # HR/FB%
    b = 0
    if p.hrfb_pct is not None:
        b = 2 if p.hrfb_pct >= t.hrfb_high else (1 if p.hrfb_pct >= t.hrfb_mid else 0)
    bd["hrfb"] = b

    # contact quality allowed
    bd["barrel_allowed"] = 1 if (p.barrel_pct_allowed is not None
                                 and p.barrel_pct_allowed >= t.barrel_allowed_high) else 0
    bd["ev_allowed"] = 1 if (p.avg_ev_allowed is not None
                             and p.avg_ev_allowed >= t.ev_allowed_high) else 0

    # low K / whiff (easier to square up)
    low_k = (p.k_pct is not None and p.k_pct < t.k_low)
    low_whiff = (p.whiff_pct is not None and p.whiff_pct < t.whiff_low)
    bd["low_k_whiff"] = 1 if (low_k or low_whiff) else 0

    # fastball-heavy / fly-ball-prone (most HRs come off fastballs)
    fb_heavy = (p.fastball_usage is not None and p.fastball_usage >= t.fastball_heavy)
    fly_prone = (p.fb_pct is not None and p.fb_pct >= t.fb_allowed_high)
    bd["fb_heavy"] = 1 if (fb_heavy or fly_prone) else 0

    # regression flag: hard contact allowed but HRs haven't shown up yet
    hard_contact = ((p.avg_ev_allowed is not None and p.avg_ev_allowed >= t.ev_allowed_high)
                    or (p.barrel_pct_allowed is not None
                        and p.barrel_pct_allowed >= t.barrel_allowed_high))
    low_hr = (p.hr9 is not None and p.hr9 < t.regression_hr9_low)
    if hard_contact and low_hr:
        p.regression_flag = True
        bd["regression"] = 2
    else:
        bd["regression"] = 0

    if p.ip is not None and p.ip < t.small_sample_ip:
        p.small_sample = True

    p.score_breakdown = bd
    p.pitcher_score = sum(bd.values())
    return p
