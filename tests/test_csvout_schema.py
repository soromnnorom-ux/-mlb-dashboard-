"""Schema guard: new slate outputs must persist the fields a future backtest
needs to re-score a slate (power profile + pitch-mix sample size + tags).

If anyone drops one of these columns from the matchups.csv writer, this fails.
"""
import csv

from hrplaybook.model.schemas import Batter, Game, Matchup, Park, Pitcher
from hrplaybook.report.csvout import write_all

# fields that must always be present in matchups.csv for re-scoring/validation
REQUIRED = [
    "barrel_vs_pm_bbe", "iso", "slg", "xslg", "xiso", "barrel_vs_pm",
    "avg_ev", "hardhit_pct", "la_avg", "tags",
]


def _slate():
    park = Park(team="COL", park_name="Coors", lat=0, lon=0, orientation_deg=0,
                roof="open", hr_factor=1.2)
    g = Game(game_pk=1, date="2026-06-30", game_time_utc=None, venue_id=1,
             venue_name="Coors", home_team="COL", away_team="LAD", park=park)
    p = Pitcher(player_id=20, name="SP", throws="R")
    b = Batter(
        player_id=10, name="Power Bat", team="LAD", bats="L", batting_order=3,
        slg=0.520, iso=0.245, xslg=0.498, xiso=0.231,
        barrel_pct=14.0, avg_ev=92.5, hardhit_pct=49.0, la_avg=16.0,
        barrel_vs_pm=14.2, barrel_vs_pm_bbe=33,
    )
    b.tags = ["MISSED_HR"]
    m = Matchup(batter=b, pitcher=p, game=g, side="away", opp_team="COL")
    return [g], {20: p}, [m]


def test_matchups_csv_persists_validation_fields(tmp_path):
    games, pitchers, matchups = _slate()
    write_all(tmp_path, games, pitchers, matchups)
    with (tmp_path / "matchups.csv").open() as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        row = next(reader)

    missing = [c for c in REQUIRED if c not in header]
    assert not missing, f"matchups.csv missing required fields: {missing}"

    # values round-trip (not just present-but-empty) for the power columns
    assert row["slg"] == "0.52"
    assert row["xslg"] == "0.498"
    assert row["iso"] == "0.245"
    assert row["xiso"] == "0.231"
    assert row["barrel_vs_pm_bbe"] == "33"
    assert "MISSED_HR" in row["tags"]


def test_batters_csv_includes_expected_power_stats(tmp_path):
    games, pitchers, matchups = _slate()
    write_all(tmp_path, games, pitchers, matchups)
    with (tmp_path / "batters.csv").open() as f:
        header = csv.DictReader(f).fieldnames
    for c in ("slg", "xslg", "iso", "xiso"):
        assert c in header, f"batters.csv missing {c}"
