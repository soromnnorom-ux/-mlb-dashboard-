"""Scoring-rule unit tests (pure, offline)."""
from hrplaybook.config import load_config
from hrplaybook.model.schemas import Batter, Game, Matchup, Park, Pitcher, Weather
from hrplaybook.score import env_tier_rank
from hrplaybook.score.batter import gate_status, perfect_profile, score_batter
from hrplaybook.score.bettypes import map_bets
from hrplaybook.score.environment import score_environment, tier_for
from hrplaybook.score.pitcher import score_pitcher
from hrplaybook.score.tiering import finalize_tiers, score_matchup
from hrplaybook.score.value import implied_prob
from hrplaybook.util import is_barrel, wind_label

CFG = load_config()


def _game(home="CIN", temp=88, wind_out="out", wind_mph=12, factor=1.12, roof="open"):
    park = Park(team=home, park_name="x", lat=0, lon=0, orientation_deg=0,
                roof=roof, hr_factor=factor)
    w = Weather(temp_f=temp, wind_mph=wind_mph, wind_out=wind_out, source="test")
    return Game(game_pk=1, date="2025-07-19", game_time_utc=None, venue_id=None,
                venue_name="x", home_team=home, away_team="PIT", weather=w, park=park)


# ---- environment ----------------------------------------------------------
def test_env_elite():
    g = score_environment(_game(), CFG)
    assert g.env_breakdown == {"temp_pts": 2, "wind_pts": 2, "park_pts": 2, "roof_closed": 0}
    assert g.env_score == 6 and g.env_tier == "elite"


def test_env_dead_air():
    g = score_environment(_game(home="SF", temp=55, wind_out="in", wind_mph=15,
                                 factor=0.88), CFG)
    assert g.env_score < 0 and g.env_tier == "dead-air"


def test_env_closed_roof_neutralizes_weather():
    g = score_environment(_game(home="MIA", temp=95, wind_out="out", wind_mph=20,
                                 factor=0.94, roof="closed"), CFG)
    assert g.env_breakdown["temp_pts"] == 0 and g.env_breakdown["wind_pts"] == 0


def test_tier_for_boundaries():
    assert tier_for(4) == "elite" and tier_for(2) == "good"
    assert tier_for(0) == "neutral" and tier_for(-1) == "dead-air"


# ---- pitcher --------------------------------------------------------------
def test_pitcher_attackable():
    p = Pitcher(player_id=1, name="x", hr9=1.7, hrfb_pct=16, k_pct=18, whiff_pct=20,
                barrel_pct_allowed=10, avg_ev_allowed=90, fastball_usage=60, fb_pct=30, ip=180)
    score_pitcher(p, CFG)
    assert p.pitcher_score >= 6
    assert p.score_breakdown["hr9"] == 2 and p.score_breakdown["hrfb"] == 2


def test_pitcher_regression_flag():
    p = Pitcher(player_id=2, name="y", hr9=0.6, avg_ev_allowed=91, barrel_pct_allowed=10,
                ip=120)
    score_pitcher(p, CFG)
    assert p.regression_flag and p.score_breakdown["regression"] == 2


def test_pitcher_small_sample():
    p = Pitcher(player_id=3, name="z", ip=12)
    score_pitcher(p, CFG)
    assert p.small_sample


# ---- batter ---------------------------------------------------------------
def test_elite_gate():
    b = Batter(player_id=1, name="b", avg_ev=103, barrel_pct=26, hardhit_pct=55)
    cfg = load_config()
    cfg.gate = "elite"
    passed, kind = gate_status(b, cfg)
    assert passed and kind == "elite"


def test_practical_gate():
    b = Batter(player_id=1, name="b", barrel_vs_pm=12)
    passed, kind = gate_status(b, CFG)  # default practical
    assert passed and kind == "practical"


def test_perfect_profile_bonus_and_penalty():
    good = Batter(player_id=1, name="b", avg_ev=105, la_avg=28)
    bonus, perfect = perfect_profile(good, CFG)
    assert perfect and bonus == 2
    popup = Batter(player_id=2, name="c", avg_ev=105, la_avg=45)
    bonus2, perfect2 = perfect_profile(popup, CFG)
    assert not perfect2 and bonus2 < 0


# ---- tiering & caps --------------------------------------------------------
def _matchup(env_tier, pscore, b, perfect_setup=True):
    g = _game()
    g.env_tier = env_tier
    g.env_score = {"elite": 5, "good": 2, "neutral": 0, "dead-air": -2}[env_tier]
    p = Pitcher(player_id=9, name="sp", pitcher_score=pscore)
    p.pitcher_score = pscore
    m = Matchup(batter=b, pitcher=p, game=g, side="home", opp_team="PIT")
    m.env_tier = env_tier
    m.env_score = g.env_score
    m.pitcher_score = pscore
    return m


def test_dead_air_suppresses_hr_tier():
    b = Batter(player_id=1, name="slug", barrel_vs_pm=25, avg_ev=104, barrel_pct=26,
               hardhit_pct=55, missed_hr=True)
    b.tags.append("MISSED_HR")
    m = _matchup("dead-air", 5, b)
    score_matchup(m, CFG)
    assert m.tier is None  # no HR play in dead air


def test_tier1_requires_gate_env_pitcher_edge():
    b = Batter(player_id=1, name="slug", barrel_vs_pm=22, missed_hr=True)
    b.tags.append("MISSED_HR")
    m = _matchup("good", 4, b)
    score_matchup(m, CFG)
    assert m.tier == 1


def test_max_plays_cap():
    cfg = load_config()
    cfg.max_plays = 2
    ms = []
    for i in range(5):
        b = Batter(player_id=i, name=f"b{i}", barrel_vs_pm=22, missed_hr=True)
        b.tags.append("MISSED_HR")
        m = _matchup("good", 4, b)
        score_matchup(m, cfg)
        m.play_score = 10 - i  # descending
        ms.append(m)
    finalize_tiers(ms, cfg)
    assert sum(1 for m in ms if m.tier == 1) == 2


# ---- bet types ------------------------------------------------------------
def test_tb_primary_in_good_env():
    b = Batter(player_id=1, name="b", hardhit_pct=52, barrel_vs_pm=16, barrel_pct=12)
    m = _matchup("good", 3, b)
    score_matchup(m, CFG)
    map_bets(m, CFG)
    assert "TB" in m.bets and "2+" in m.bets["TB"]


def test_hr_card_only_tier1():
    b = Batter(player_id=1, name="b", barrel_vs_pm=22, missed_hr=True)
    b.tags.append("MISSED_HR")
    m = _matchup("good", 4, b)
    score_matchup(m, CFG)
    map_bets(m, CFG)
    assert m.tier == 1 and "HR" in m.bets


# ---- value & util ---------------------------------------------------------
def test_implied_prob():
    assert abs(implied_prob(-110) - 0.5238) < 0.001
    assert abs(implied_prob(+300) - 0.25) < 0.001


def test_is_barrel():
    assert is_barrel(100, 28) is True
    assert is_barrel(95, 28) is False        # below 98 EV
    assert is_barrel(100, 5) is False        # ground ball


def test_wind_label():
    # park CF at 0deg (north); wind FROM south (180) blows north -> out
    assert wind_label(180, 0) == "out"
    # wind FROM north (0) blows south -> in
    assert wind_label(0, 0) == "in"
    assert env_tier_rank("elite") > env_tier_rank("good")
