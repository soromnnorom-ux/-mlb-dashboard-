"""Perf optimization: batched statcast (1 call/game vs 1/batter) building blocks."""
import io

from hrplaybook.sources import savant


def _csv(rows, cols):
    out = io.StringIO()
    out.write(",".join(cols) + "\n")
    for r in rows:
        out.write(",".join(str(r.get(c, "")) for c in cols) + "\n")
    return out.getvalue()


def test_parse_statcast_tags_batter_and_groups():
    cols = ["batter", "game_date", "pitch_type", "events", "launch_speed",
            "launch_angle", "hit_distance_sc", "bb_type", "stand", "p_throws"]
    rows = [
        {"batter": 1, "game_date": "2026-06-08", "launch_speed": 104, "launch_angle": 25,
         "bb_type": "fly_ball", "events": "home_run"},
        {"batter": 2, "game_date": "2026-06-08", "launch_speed": 88, "launch_angle": 10,
         "bb_type": "ground_ball", "events": "field_out"},
        {"batter": 1, "game_date": "2026-06-07", "launch_speed": 99, "launch_angle": 18,
         "bb_type": "line_drive", "events": "single"},
    ]
    parsed = savant.parse_statcast(_csv(rows, cols))
    assert {p["batter"] for p in parsed} == {1, 2}
    grouped = savant.group_statcast_by_batter(parsed)
    assert len(grouped[1]) == 2 and len(grouped[2]) == 1   # split per batter


def test_batched_fetch_sends_all_ids_in_one_request():
    captured = {}

    class FakeClient:
        def get_text(self, ns, url, params=None):
            captured["params"] = params
            return None
    savant.fetch_statcast_batters(FakeClient(), [1, 2, 3], "2026-05-10", "2026-06-09")
    # one request carrying every batter id (replaces 3 separate calls)
    assert captured["params"]["batters_lookup[]"] == [1, 2, 3]


def test_batched_fetch_empty_is_noop():
    class Boom:
        def get_text(self, *a, **k):
            raise AssertionError("should not fetch for empty id list")
    assert savant.fetch_statcast_batters(Boom(), [], "a", "b") is None


def test_pa_events_tagged_and_grouped():
    cols = ["batter", "game_date", "events"]
    rows = [{"batter": 1, "game_date": "2026-06-08", "events": "single"},
            {"batter": 1, "game_date": "2026-06-08", "events": "strikeout"},
            {"batter": 2, "game_date": "2026-06-08", "events": "walk"}]
    parsed = savant.parse_pa_events(_csv(rows, cols))
    g = savant.group_pa_events_by_batter(parsed)
    assert len(g[1]) == 2 and len(g[2]) == 1


def test_build_cron_has_correct_fields():
    from pathlib import Path
    from hrplaybook.cli import build_cron
    snip = build_cron(Path("/proj"), 9, 16, "hrplaybook")
    # cron is `minute hour dom mon dow` -> 0 9 ... = 9:00am (regression: was 9 0 = 12:09am)
    assert "0 9 * * *" in snip and "0 16 * * *" in snip
    assert "grade --date yesterday" in snip and "run --date today" in snip
    assert "refresh --date today" in snip and "odds-refresh --date today" in snip
