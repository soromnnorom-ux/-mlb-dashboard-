"""Parser tests against recorded fixtures (offline)."""
from hrplaybook.sources import savant, statsapi, weather, rotowire


def test_parse_schedule(schedule_json):
    games = statsapi.parse_schedule(schedule_json)
    assert len(games) == 15
    g = next(g for g in games if g.game_pk == 777094)
    assert g.home_team == "TOR" and g.away_team
    assert g.home_pitcher_id == 641778
    assert g.weather.source == "statsapi"
    assert g.weather.temp_f == 74.0
    assert g.weather.wind_out == "out"          # "5 mph, Out To CF"
    assert g.weather.wind_mph == 5.0


def test_parse_lineup(boxscore_json):
    home = statsapi.parse_lineup(boxscore_json, "home")
    assert len(home) == 9
    assert home[0]["order"] == 1
    assert home[0]["player_id"] == 664770
    assert all(e["name"] for e in home)


def test_parse_people(people_json):
    people = statsapi.parse_people(people_json)
    assert 641778 in people
    assert people[641778]["throws"] == "L"      # Eric Lauer throws left


def test_parse_batter_leaderboard(batter_csv):
    pool = savant.parse_batter_leaderboard(batter_csv, 2025)
    assert len(pool) > 100
    b = next(iter(pool.values()))
    assert b.barrel_pct is not None
    assert b.avg_ev is not None and 70 < b.avg_ev < 100
    assert b.name and "," not in b.name          # flipped to "First Last"


def test_parse_pitcher_leaderboard(pitcher_csv):
    pool = savant.parse_pitcher_leaderboard(pitcher_csv, 2025)
    assert len(pool) > 30
    p = next(iter(pool.values()))
    assert p.hr9 is not None and p.hr9 > 0
    assert p.hrfb_pct is not None                 # derived
    assert p.ip is not None and p.ip > 1


def test_parse_arsenals(arsenals_csv):
    ars = savant.parse_arsenals(arsenals_csv)
    assert len(ars) > 100
    mix = next(m for m in ars.values() if m)
    assert abs(sum(mix.values()) - 100) < 25      # usages roughly sum to ~100%


def test_parse_statcast(statcast_csv):
    bb = savant.parse_statcast(statcast_csv)
    assert len(bb) > 50
    assert all(r["launch_speed"] is not None for r in bb)
    pa = savant.parse_pa_events(statcast_csv)
    assert len(pa) >= len(bb)                      # PA events include strikeouts


def test_parse_open_meteo(openmeteo_json):
    from hrplaybook.model.schemas import Park
    park = Park(team="TOR", park_name="Rogers Centre", lat=43.64, lon=-79.39,
                orientation_deg=0)
    w = weather.parse_open_meteo(openmeteo_json, "2025-07-19T19:07:00Z", park)
    assert w.source == "open-meteo"
    assert w.temp_f is not None
    assert w.wind_out in ("out", "in", "cross", "unknown")


def test_parse_rotowire_inline():
    html = (
        'x class="lineup is-mlb">'
        '<div class="lineup__abbr">BOS</div><div class="lineup__abbr">NYY</div>'
        '<ul class="lineup__list is-visit">'
        '<li class="lineup__player"><div class="lineup__pos">LF</div>'
        '<a title="Jarren Duran" href="/x">Jarren Duran</a>'
        '<span class="lineup__bats">L</span></li></ul>'
        '<ul class="lineup__list is-home">'
        '<li class="lineup__player"><div class="lineup__pos">DH</div>'
        '<a title="Aaron Judge" href="/y">Aaron Judge</a>'
        '<span class="lineup__bats">R</span></li></ul>'
    )
    out = rotowire.parse_rotowire(html)
    assert out["BOS"][0]["name"] == "Jarren Duran"
    assert out["NYY"][0]["name"] == "Aaron Judge"
    assert out["NYY"][0]["bats"] == "R"
