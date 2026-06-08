"""Enrichment tests using the recorded statcast fixture (batter 665489)."""
from hrplaybook.config import load_config
from hrplaybook.model.enrich import attach_arsenal, enrich_batter, primary_pitches
from hrplaybook.model.schemas import Batter, Pitcher
from hrplaybook.sources import savant

CFG = load_config()


def test_enrich_barrel_vs_pm_and_logs(statcast_csv):
    bb = savant.parse_statcast(statcast_csv)
    pa = savant.parse_pa_events(statcast_csv)
    pitcher = Pitcher(player_id=1, name="sp")
    attach_arsenal(pitcher, {"FF": 50.0, "SL": 30.0, "CH": 20.0})

    b = Batter(player_id=665489, name="Vladimir Guerrero Jr.", barrel_pct=9.0)
    enrich_batter(b, bb, pa, pitcher, CFG)

    assert b.recent_window_used
    assert b.barrel_vs_pm is not None
    assert len(b.recent_ev_logs) > 0
    # L30 computed from PA outcomes (hits/at-bats)
    assert b.l30_ab and b.l30_ab > 0
    assert 0 <= (b.l30_h or 0) <= b.l30_ab


def test_enrich_fallback_when_no_arsenal(statcast_csv):
    bb = savant.parse_statcast(statcast_csv)
    pa = savant.parse_pa_events(statcast_csv)
    b = Batter(player_id=665489, name="x", barrel_pct=7.5)
    # no pitcher -> uses all batted balls; still computes a barrel%
    enrich_batter(b, bb, pa, None, CFG)
    assert b.barrel_vs_pm is not None


def test_attach_arsenal_fastball_usage():
    p = Pitcher(player_id=1, name="sp")
    attach_arsenal(p, {"FF": 40.0, "SI": 15.0, "FC": 5.0, "SL": 40.0})
    assert p.fastball_usage == 60.0  # FF+SI+FC


def test_primary_pitches_threshold():
    prim = primary_pitches({"FF": 55.0, "SL": 30.0, "CH": 8.0}, 10.0)
    assert prim == {"FF", "SL"}
    # if none clear the bar, fall back to top-3
    prim2 = primary_pitches({"FF": 5.0, "SL": 4.0, "CH": 3.0, "CU": 2.0}, 10.0)
    assert len(prim2) == 3
