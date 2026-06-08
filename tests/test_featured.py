"""Phase 12/13/18 tests: market scores, additivity, best5, slate read."""
from hrplaybook import featured

STRONG = {
    "batter": "Slugger", "batter_id": 1, "team": "NYY", "opp_team": "BOS",
    "opp_sp": "Weak Arm", "order": 2, "platoon": "fav", "lineup_state": "confirmed",
    "env_tier": "good", "env_score": 5, "pitcher_score": 6,
    "barrel_pct": 14, "hardhit_pct": 48, "avg_ev": 92,
    "barrel_vs_pm": 18, "barrel_vs_pm_bbe": 60, "opp_bullpen_hr9": 1.4,
    "l30_avg": 0.31, "tags": "HOT_CONTACT|MISSED_HR",
    "bets": "HR:HR|TB:2+ TB|HRR:2.5 HRR|Hits:1+ Hits",
    "model_hr_prob": 0.18, "model_tb_prob": 0.62, "value": "unknown",
}
WEAK = {
    "batter": "Slappy", "batter_id": 2, "team": "SF", "opp_team": "SD",
    "opp_sp": "Ace", "order": 8, "platoon": "unfav", "lineup_state": "projected",
    "env_tier": "dead-air", "env_score": -1, "pitcher_score": 0,
    "barrel_pct": 2, "hardhit_pct": 28, "avg_ev": 85,
    "barrel_vs_pm": 4, "barrel_vs_pm_bbe": 6, "opp_bullpen_hr9": 0.8,
    "l30_avg": 0.18, "tags": "", "bets": "TB:1.5 TB", "value": "unknown",
}


def test_score_is_exactly_sum_of_breakdown():
    for mk, sc in featured.market_scores(STRONG).items():
        if sc["score"] < 100:  # only when not clamped
            assert sc["score"] == sum(b["pts"] for b in sc["breakdown"]), mk


def test_strong_outscores_weak():
    s = featured.market_scores(STRONG)["HR"]["score"]
    w = featured.market_scores(WEAK)["HR"]["score"]
    assert s > w
    assert featured.market_scores(STRONG)["HR"]["grade"] in ("A", "A+", "B")


def test_dead_air_caps_hr():
    assert featured.market_scores(WEAK)["HR"]["score"] <= 35


def test_grade_and_stars_thresholds():
    assert featured.grade_from_score(90) == "A+"
    assert featured.grade_from_score(60) == "B"
    assert featured.grade_from_score(10) == "D"
    assert featured.stars(90) == 5 and featured.stars(10) == 1


def test_top_by_market_respects_eligibility():
    # WEAK has no HR bet -> excluded from HR tops
    top = featured.top_by_market([STRONG, WEAK], "HR", 5)
    assert [r["batter"] for r in top] == ["Slugger"]


def test_reasons_and_flags():
    rs = featured.reasons(STRONG, "HR")
    assert any("pitch-mix" in r.lower() for r in rs)
    fl = featured.red_flags(WEAK, "HR")
    assert any("dead-air" in f.lower() for f in fl)
    assert any("platoon" in f.lower() for f in fl)


def test_best5_shape():
    b = featured.best5([STRONG, WEAK], [], [])
    assert set(b) == {"HR", "TB", "HRR", "Hits", "pitcher"}
    assert b["HR"]["batter"] == "Slugger"


def test_slate_read_weak_flags_warning():
    games = [{"matchup": "SF@SD", "env_tier": "dead-air", "env_score": -1}]
    r = featured.slate_read(games, [WEAK], [])
    assert r["weak"] is True
    assert "don't force" in r["text"].lower() or "selective" in r["text"].lower()


def test_pitchers_to_attack_ranks_and_filters():
    pitchers = [
        {"name": "Weak Arm", "pitcher_score": 7, "hr9": 1.8, "hrfb_pct": 18,
         "barrel_pct_allowed": 11, "k_pct": 16, "regression_flag": "True"},
        {"name": "Nobody", "pitcher_score": 5},  # not a probable today -> filtered
    ]
    games = [{"matchup": "NYY@BOS", "away_sp": "CC", "home_sp": "Weak Arm"}]
    pa = featured.pitchers_to_attack(pitchers, games, 5)
    assert [p["name"] for p in pa] == ["Weak Arm"]
    assert "PENDING BLOWUP" in pa[0]["reasons"]
