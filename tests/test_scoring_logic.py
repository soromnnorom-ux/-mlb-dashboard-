"""Logic tests for the HR Playbook scoring MODEL (not a smoke test).

Each test builds mock MLB data, runs it through the real scoring pipeline
(score_environment -> score_pitcher -> score_matchup -> map_bets, with
finalize_tiers for the cap), and asserts the resulting TIER and MARKET make
baseball/betting sense.

Pipeline wiring mirrors hrplaybook/pipeline.py lines ~288-297:
    m.env_score   = game.env_score
    m.env_tier    = game.env_tier
    m.pitcher_score = opp_pitcher.pitcher_score
    score_matchup(m); map_bets(m); ... finalize_tiers(all)

Tests named `test_NN_...` map to the 10 requested cases. The `test_NN_gap_...`
tests cover three model weaknesses that were found and then FIXED:
  - gap 7:  recency guard fades a just-homered bat from Tier 1 -> Tier 2
            (tiering._assign_tier) unless it also has a missed-HR signal.
  - gap 9:  the practical gate now requires barrel_vs_pm_bbe >= min_bbe
            (config.PracticalGate / batter.passes_practical).
  - gap 10: finalize_tiers drops bets["HR"] on a capped-out (demoted) play.
"""
from __future__ import annotations

from hrplaybook.config import Config
from hrplaybook.model.schemas import Batter, Game, Matchup, Park, Pitcher, Weather
from hrplaybook.score.bettypes import map_bets
from hrplaybook.score.environment import score_environment
from hrplaybook.score.pitcher import score_pitcher
from hrplaybook.score.tiering import finalize_tiers, score_matchup

CFG = Config()  # defaults: gate="practical" (barrel_vs_pm>=10), max_plays=5


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def make_game(home="COL", hr_factor=1.20, roof="open",
              temp=88, wind_mph=12, wind_out="out") -> Game:
    park = Park(team=home, park_name=home, lat=0, lon=0, orientation_deg=0,
                roof=roof, hr_factor=hr_factor)
    w = Weather(temp_f=temp, wind_mph=wind_mph, wind_out=wind_out)
    g = Game(game_pk=1, date="2026-06-29", game_time_utc=None, venue_id=1,
             venue_name=home, home_team=home, away_team="OPP",
             park=park, weather=w)
    return score_environment(g, CFG)


def make_pitcher(throws="R", **stats) -> Pitcher:
    p = Pitcher(player_id=99, name="SP", throws=throws, **stats)
    return score_pitcher(p, CFG)


# weak HR pitcher: blow-up profile -> pitcher_score ~8
def weak_hr_pitcher(throws="R") -> Pitcher:
    return make_pitcher(throws=throws, ip=80, hr=20, hr9=1.9, hrfb_pct=18,
                        barrel_pct_allowed=11, avg_ev_allowed=91.0,
                        k_pct=18, whiff_pct=22, fastball_usage=60)


# tough pitcher: nothing to attack -> pitcher_score 0
def tough_pitcher(throws="R") -> Pitcher:
    return make_pitcher(throws=throws, ip=120, hr=8, hr9=0.7, hrfb_pct=8,
                        barrel_pct_allowed=6, avg_ev_allowed=86.0,
                        k_pct=28, whiff_pct=28, fastball_usage=40, fb_pct=18)


def make_batter(**kw) -> Batter:
    kw.setdefault("name", "Bat")
    kw.setdefault("player_id", 1)
    # default to a trustworthy pitch-mix sample; cases testing thin samples
    # override barrel_vs_pm_bbe explicitly.
    kw.setdefault("barrel_vs_pm_bbe", 30)
    return Batter(**kw)


def score_play(batter, pitcher, game, *, opp_team=None, bullpen_hr9=None) -> Matchup:
    m = Matchup(batter=batter, pitcher=pitcher, game=game, side="away",
                opp_team=opp_team or game.home_team)
    m.env_score = game.env_score
    m.env_tier = game.env_tier
    m.pitcher_score = pitcher.pitcher_score if pitcher else 0
    m.opp_bullpen_hr9 = bullpen_hr9
    score_matchup(m, CFG)
    map_bets(m, CFG)
    return m


# --------------------------------------------------------------------------- #
# sanity: the builders produce the environments/pitchers we think they do
# --------------------------------------------------------------------------- #
def test_builders_sanity():
    assert make_game(home="COL").env_tier == "elite"          # COL park + hot + wind out
    assert make_game(home="SF", temp=55, wind_out="in").env_tier == "dead-air"
    assert make_game(home="LAD", temp=78, wind_out="calm").env_tier == "good"
    assert weak_hr_pitcher().pitcher_score >= 3
    assert tough_pitcher().pitcher_score == 0


# --------------------------------------------------------------------------- #
# 1. Elite HR play  ->  Tier 1
# --------------------------------------------------------------------------- #
def test_01_elite_hr_play_is_tier1():
    g = make_game(home="COL")                                  # elite env
    p = weak_hr_pitcher()                                       # weak HR pitcher
    b = make_batter(bats="L", batting_order=3, barrel_vs_pm=16,
                    barrel_pct=15, hardhit_pct=52, avg_ev=104, la_avg=28,
                    iso=0.260, slg=0.560)                       # perfect profile + edge
    m = score_play(b, p, g)
    assert m.tier == 1, f"expected Tier 1, got {m.tier}"
    assert m.bets.get("HR") == "HR"
    assert m.perfect_profile is True


# --------------------------------------------------------------------------- #
# 2. Bad environment trap -> no HR play (even with an elite bat)
# --------------------------------------------------------------------------- #
def test_02_bad_environment_suppresses_hr():
    g = make_game(home="SF", temp=55, wind_out="in", hr_factor=0.90)  # dead-air
    p = tough_pitcher()
    b = make_batter(bats="R", batting_order=3, barrel_vs_pm=18, barrel_pct=16,
                    hardhit_pct=53, avg_ev=105, la_avg=27, iso=0.280)  # elite bat
    m = score_play(b, p, g)
    assert m.env_tier == "dead-air"
    assert m.tier != 1, "dead-air must never be a Tier-1 HR play"
    assert "HR" not in m.bets, "HR must be suppressed in dead air"
    # but the playbook still allows a TB play on a real bat in dead air
    assert "TB" in m.bets


# --------------------------------------------------------------------------- #
# 3. Name-value trap -> not Tier 1 (model ignores reputation; weak metrics)
# --------------------------------------------------------------------------- #
def test_03_name_value_trap_not_tier1():
    g = make_game(home="COL")                                  # great spot
    p = weak_hr_pitcher()                                       # great matchup
    b = make_batter(name="Famous Slugger", bats="R", batting_order=3,
                    barrel_vs_pm=6, barrel_pct=4, hardhit_pct=30, avg_ev=88,
                    la_avg=14, iso=0.120)                       # weak RECENT form
    m = score_play(b, p, g)
    assert m.tier != 1, f"weak metrics must not be Tier 1, got {m.tier}"
    assert "HR" not in m.bets
    # weak contact also earns no TB -- reputation buys nothing
    assert "TB" not in m.bets


# --------------------------------------------------------------------------- #
# 4. Total-bases play, not HR  (good EV/AVG, not enough barrel/FB for HR)
# --------------------------------------------------------------------------- #
def test_04_total_bases_not_hr():
    g = make_game(home="LAD", temp=80, wind_out="calm")        # good env (rank 2)
    p = weak_hr_pitcher()
    b = make_batter(bats="L", batting_order=2, barrel_vs_pm=12, barrel_pct=9,
                    hardhit_pct=50, avg_ev=95, la_avg=12, ba=0.295,
                    l30_h=34, l30_ab=110, iso=0.150)            # gap power, low LA
    m = score_play(b, p, g)
    assert m.tier != 1, "no HR edge (no perfect/missed/hot) -> not Tier 1"
    assert "HR" not in m.bets
    assert m.bets.get("TB") == "2+ TB", f"expected 2+ TB, got {m.bets.get('TB')}"


# --------------------------------------------------------------------------- #
# 5. HRR play (top spot, good env, decent contact, low HR power) -> HRR not HR
# --------------------------------------------------------------------------- #
def test_05_hrr_not_hr():
    g = make_game(home="LAD", temp=80, wind_out="calm")        # good env, good team spot
    p = weak_hr_pitcher()
    b = make_batter(bats="R", batting_order=2, barrel_vs_pm=11, barrel_pct=7,
                    hardhit_pct=46, avg_ev=93, la_avg=11, ba=0.285,
                    l30_h=33, l30_ab=110, iso=0.130)
    m = score_play(b, p, g)
    assert "HRR" in m.bets, f"expected an HRR play, got bets={m.bets}"
    assert "HR" not in m.bets, "low HR power must not produce an HR leg"


# --------------------------------------------------------------------------- #
# 6. Pitcher blow-up spot -> hitter upgraded (Tier 1) vs the same bat on a tough SP
# --------------------------------------------------------------------------- #
def test_06_pitcher_blowup_upgrades_hitter():
    g = make_game(home="COL")
    b_kwargs = dict(bats="L", batting_order=3, barrel_vs_pm=18, barrel_pct=16,
                    hardhit_pct=51, avg_ev=103, la_avg=27, iso=0.270)
    hot = score_play(make_batter(**b_kwargs), weak_hr_pitcher(), g)
    cold = score_play(make_batter(**b_kwargs), tough_pitcher(), g)
    assert hot.tier == 1 and hot.bets.get("HR") == "HR"
    assert cold.tier != 1, "vs a tough SP the same bat should NOT be a Tier-1 HR"
    assert hot.play_score > cold.play_score, "weak pitcher must lift the play score"


# --------------------------------------------------------------------------- #
# 7. Recency-bias filter -> a bat that just homered but has weak current
#    indicators must NOT be over-upgraded.
# --------------------------------------------------------------------------- #
def test_07_recency_weak_indicators_not_tier1():
    g = make_game(home="COL")
    p = weak_hr_pitcher()
    b = make_batter(bats="R", batting_order=3, barrel_vs_pm=7, barrel_pct=5,
                    hardhit_pct=33, avg_ev=89, la_avg=15, recent_hr=True)
    m = score_play(b, p, g)
    assert m.tier != 1, "weak current form must not ride a stale HR into Tier 1"
    assert "RECENT_HR" in m.batter.tags or m.batter.recent_hr  # fade signal present


def test_07_gap_recency_fade_does_not_downgrade_tier():
    # FIXED: a strong bat that homered yesterday (no missed-HR signal) is faded
    # from Tier 1 to Tier 2 by the recency guard in _assign_tier.
    g = make_game(home="COL")
    strong = make_batter(bats="L", batting_order=3, barrel_vs_pm=18,
                         barrel_pct=16, hardhit_pct=52, avg_ev=104, la_avg=28,
                         recent_hr=True)
    ms = score_play(strong, weak_hr_pitcher(), g)
    assert ms.tier != 1
    assert ms.tier == 2, "recency guard should fade CORE -> ENV-BOOSTED, not kill it"


# --------------------------------------------------------------------------- #
# 8. Missed-HR edge -> regression candidate gets upgraded.
# --------------------------------------------------------------------------- #
def test_08_missed_hr_regression_upgrade():
    g = make_game(home="COL")
    p = weak_hr_pitcher()
    # missed_hr would be set by enrich for a 100+ EV, 380+ ft, LA 20-38 out.
    b = make_batter(bats="L", batting_order=4, barrel_vs_pm=12, barrel_pct=11,
                    hardhit_pct=48, avg_ev=98, la_avg=22, missed_hr=True,
                    missed_hr_ev=106.0, missed_hr_dist=402)
    m = score_play(b, p, g)
    # NB: the MISSED_HR *tag* is attached by enrich_batter, not the scorer; the
    # scorer consumes batter.missed_hr to grant has_edge (+1 edge_bonus).
    assert m.batter.missed_hr is True
    assert m.edge_bonus >= 1, "missed-HR must add an edge bonus"
    assert m.tier == 1, f"missed-HR + good spot should upgrade, got {m.tier}"
    assert m.bets.get("HR") == "HR"


# --------------------------------------------------------------------------- #
# 9. Weak sample size -> elite L5 spike on a tiny sample over poor season
#    should be a CAUTION, not an automatic Tier 1.
# --------------------------------------------------------------------------- #
def _small_sample_play():
    g = make_game(home="COL")
    # barrel_vs_pm=22 from only 3 batted balls; poor season barrel/EV; the L5
    # cluster set hot_contact. enrich would tag SMALL_PM_SAMPLE here.
    b = make_batter(bats="L", batting_order=3, barrel_vs_pm=22,
                    barrel_vs_pm_bbe=3, barrel_pct=5, hardhit_pct=40,
                    avg_ev=97, la_avg=20, hot_contact=True,
                    tags=["SMALL_PM_SAMPLE"])
    return score_play(b, weak_hr_pitcher(), g)


def test_09_small_sample_carries_caution_tag():
    # the only safeguard present today is the tag carried through from enrich
    assert "SMALL_PM_SAMPLE" in _small_sample_play().tags


def test_09_gap_small_sample_not_automatic_tier1():
    # FIXED by the barrel_vs_pm_bbe gate: 3 batted balls < min_bbe -> gate
    # fails -> not Tier 1, despite hot_contact + a 22% mix barrel spike.
    assert _small_sample_play().tier != 1


# --------------------------------------------------------------------------- #
# 10. Parlay/HR-cap discipline.
#    There is NO parlay builder in the codebase. The only discipline is
#    finalize_tiers capping HR (Tier-1) plays to max_plays. Test that cap, and
#    the HR-leak it exposes.
# --------------------------------------------------------------------------- #
def _seven_tier1_plays():
    g = make_game(home="COL")
    p = weak_hr_pitcher()
    plays = []
    for i in range(7):                                         # 7 genuine Tier-1 bats
        b = make_batter(player_id=100 + i, name=f"B{i}", bats="L",
                        batting_order=3, barrel_vs_pm=16 + i * 0.1, barrel_pct=15,
                        hardhit_pct=52, avg_ev=104, la_avg=28)
        plays.append(score_play(b, p, g))
    return plays


def test_10_hr_plays_capped_to_max_plays():
    plays = _seven_tier1_plays()
    assert all(m.tier == 1 for m in plays)                    # all Tier 1 pre-cap
    finalize_tiers(plays, CFG)
    assert sum(m.tier == 1 for m in plays) == CFG.max_plays   # capped to 5
    assert sum(m.tier == 2 for m in plays) == 2               # overflow demoted


def test_10_gap_capped_out_play_drops_hr_bet():
    plays = _seven_tier1_plays()
    finalize_tiers(plays, CFG)
    demoted = [m for m in plays if m.tier == 2]
    assert demoted and all("HR" not in m.bets for m in demoted)
