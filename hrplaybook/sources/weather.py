"""Weather: StatsAPI schedule block first, Open-Meteo as authoritative fallback."""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from ..http import Client
from ..model.schemas import Park, Weather
from ..util import to_float, wind_label

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


def fetch_open_meteo(client: Client, lat: float, lon: float) -> Optional[dict]:
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": 3,
    }
    return client.get_json("weather", OPEN_METEO, params)


def _nearest_hour_index(times: list[str], target_local: _dt.datetime) -> int:
    best_i, best_diff = 0, None
    tkey = target_local.strftime("%Y-%m-%dT%H:00")
    for i, t in enumerate(times):
        if t == tkey:
            return i
        try:
            dt = _dt.datetime.fromisoformat(t)
        except ValueError:
            continue
        diff = abs((dt - target_local.replace(tzinfo=None)).total_seconds())
        if best_diff is None or diff < best_diff:
            best_diff, best_i = diff, i
    return best_i


def parse_open_meteo(data: Optional[dict], game_time_utc: Optional[str],
                     park: Optional[Park]) -> Weather:
    w = Weather(source="open-meteo")
    if not data or "hourly" not in data:
        w.source = "none"
        return w
    h = data["hourly"]
    times = h.get("time", [])
    if not times:
        w.source = "none"
        return w
    offset = data.get("utc_offset_seconds", 0)
    if game_time_utc:
        try:
            utc = _dt.datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
            local = (utc + _dt.timedelta(seconds=offset)).replace(tzinfo=None)
        except ValueError:
            local = _dt.datetime.fromisoformat(times[0])
    else:
        base = _dt.datetime.fromisoformat(times[0])
        local = base.replace(hour=19, minute=0)
    i = _nearest_hour_index(times, local)

    def _at(key):
        arr = h.get(key) or []
        return to_float(arr[i]) if i < len(arr) else None

    w.temp_f = _at("temperature_2m")
    w.wind_mph = _at("wind_speed_10m")
    w.wind_dir_deg = _at("wind_direction_10m")
    w.precip_pct = _at("precipitation_probability")
    if park:
        w.wind_out = wind_label(w.wind_dir_deg, park.orientation_deg)
    return w


def get_weather(client: Client, game, park: Optional[Park]) -> Weather:
    """Prefer a populated StatsAPI block; else Open-Meteo by park lat/lon."""
    block = game.weather
    if block and block.source == "statsapi" and block.temp_f is not None:
        return block
    if park and park.lat is not None and park.lon is not None:
        data = fetch_open_meteo(client, park.lat, park.lon)
        wx = parse_open_meteo(data, game.game_time_utc, park)
        if wx.source != "none":
            return wx
    return block or Weather(source="none")
