"""Batch 9 tests: multi-season coverage cleanup (2025 min config + coverage)."""
from hrplaybook import featured
from hrplaybook.config import Config, load_config


def test_config_defaults_for_2025_minimums():
    c = Config()
    assert c.savant_batter_min_2025 == "25"
    assert c.savant_pitcher_min_2025 == "10"


def test_config_yaml_loads_2025_minimums():
    c = load_config()
    assert hasattr(c, "savant_batter_min_2025")
    assert c.savant_batter_min_2025 and c.savant_pitcher_min_2025


def test_baseline_coverage_counts_and_dedupes():
    matchups = [
        {"batter_id": 1, "batter_2025_stats": '{"barrel_pct": 10}'},
        {"batter_id": 2, "batter_2025_stats": ""},
        {"batter_id": 3, "batter_2025_stats": None},
        {"batter_id": 1, "batter_2025_stats": '{"barrel_pct": 10}'},  # dup -> ignored
    ]
    pitchers = [{"name": "A", "pitcher_2025_stats": '{"hr9": 1.2}'},
                {"name": "B", "pitcher_2025_stats": ""}]
    cov = featured.baseline_coverage(matchups, pitchers)
    assert cov["batters_total"] == 3
    assert cov["batters_with_2025"] == 1
    assert cov["batters_without_2025"] == 2
    assert cov["pitchers_total"] == 2 and cov["pitchers_with_2025"] == 1


def test_low_coverage_warning_fires_below_70pct():
    matchups = [{"batter_id": i, "batter_2025_stats": ('{"x":1}' if i == 0 else "")}
                for i in range(5)]   # 1/5 = 20%
    cov = featured.baseline_coverage(matchups, [])
    assert "LOW_2025_BASELINE_COVERAGE" in cov["warnings"]


def test_high_coverage_no_warning():
    matchups = [{"batter_id": i, "batter_2025_stats": '{"x":1}'} for i in range(10)]
    cov = featured.baseline_coverage(matchups, [])
    assert cov["warnings"] == [] and cov["batter_pct"] == 1.0


def test_trend_labels_are_known_to_badge_map():
    # the UI badge map must cover every label seasons can emit
    known = {"IMPROVING_POWER_PROFILE", "IMPROVING_CONTACT_PROFILE",
             "DECLINING_POWER_PROFILE", "DECLINING_CONTACT_PROFILE",
             "SAME_PROFILE", "SMALL_SAMPLE", "PITCH_MIX_CHANGE",
             "PITCHER_REGRESSION_RISK"}
    from hrplaybook import seasons
    bt = seasons.batter_trend({"barrel_pct": 9}, {"barrel_pct": 20}, {"n": 5}, pa_2026=20)
    assert bt["label"] in known
    pt = seasons.pitcher_trend({"hr9": 1.0}, {"hr9": 1.6}, {"changed": False}, ip_2026=90)
    assert pt["label"] in known
