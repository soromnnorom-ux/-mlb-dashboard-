"""Small shared helpers: numeric parsing, dates, names, barrel classifier, geometry."""
from __future__ import annotations

import datetime as _dt
import unicodedata
from typing import Optional


def to_float(v) -> Optional[float]:
    """Parse a Savant/StatsAPI cell into a float. Handles '.353', '39', '', None, '--'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "")
    if s in ("", "--", "null", "None", "NaN", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v) -> Optional[int]:
    f = to_float(v)
    return int(f) if f is not None else None


def parse_ip(ip) -> Optional[float]:
    """Convert MLB innings-pitched notation (e.g. '186.2' = 186 + 2/3) to a true float."""
    f = to_float(ip)
    if f is None:
        return None
    whole = int(f)
    frac = round(f - whole, 1)
    thirds = {0.0: 0.0, 0.1: 1 / 3, 0.2: 2 / 3}.get(frac, 0.0)
    return whole + thirds


def resolve_date(date_str: str) -> str:
    """Accept 'today', 'tomorrow', 'yesterday', or YYYY-MM-DD -> YYYY-MM-DD."""
    s = (date_str or "today").strip().lower()
    today = _dt.date.today()
    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + _dt.timedelta(days=1)).isoformat()
    if s == "yesterday":
        return (today - _dt.timedelta(days=1)).isoformat()
    _dt.date.fromisoformat(s)  # validate
    return s


def window_start(date_str: str, days: int) -> str:
    d = _dt.date.fromisoformat(date_str)
    return (d - _dt.timedelta(days=days)).isoformat()


def now_stamp() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def today_iso() -> str:
    return _dt.date.today().isoformat()


def to_bool(v) -> bool:
    """Parse a CSV/JSON cell into a bool. Handles True/'True'/'true'/1/'1'/'yes'."""
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


def split_tags(v) -> list:
    """Pipe-delimited tag string -> list (drops empties). Accepts a list too."""
    if isinstance(v, (list, tuple)):
        return [t for t in v if t]
    return [t for t in str(v or "").split("|") if t]


# 0-100 model-score -> letter grade (single source of truth for featured/value views)
_GRADE_CUTS = ((85, "A+"), (72, "A"), (58, "B"), (42, "C"), (0, "D"))


def grade_from_score(s) -> str:
    if s is None:
        return "—"
    return next(g for cut, g in _GRADE_CUTS if s >= cut)


def flip_name(name: str) -> str:
    """'Cronenworth, Jake' -> 'Jake Cronenworth'; passthrough if no comma."""
    if not name:
        return ""
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return name.strip()


def normalize_name(name: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes for fuzzy matching."""
    if not name:
        return ""
    if "," in name:
        name = flip_name(name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower()
    for suffix in (" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    out = [ch for ch in name if ch.isalnum() or ch == " "]
    return " ".join("".join(out).split())


def is_barrel(ev: Optional[float], la: Optional[float]) -> bool:
    """Approximate the Statcast 'barrel' classification.

    A barrel requires EV >= 98 mph; the qualifying launch-angle window starts at
    26-30 degrees at 98 mph and widens as EV climbs (toward 8-50 at the top end).
    This is a documented approximation of MLB's lookup table -- good enough for
    edge tagging and the barrel%-vs-pitch-mix floor, and tunable if needed.
    """
    if ev is None or la is None:
        return False
    if ev < 98.0:
        return False
    spread = (ev - 98.0) * 1.4
    low = max(8.0, 26.0 - spread)
    high = min(50.0, 30.0 + spread)
    return low <= la <= high


def wind_is_out(wind_dir_deg: Optional[float], park_orientation_deg: Optional[float],
                tol: float = 70.0) -> Optional[bool]:
    """Is the wind blowing OUT toward the outfield?

    `wind_dir_deg` is the meteorological direction the wind blows FROM, so it
    travels toward (dir + 180). `park_orientation_deg` is the home-plate->CF
    bearing. Wind is "out" when its travel bearing is within `tol` of that
    bearing. Returns None if inputs missing.
    """
    if wind_dir_deg is None or park_orientation_deg is None:
        return None
    blow_toward = (wind_dir_deg + 180.0) % 360.0
    diff = abs((blow_toward - park_orientation_deg + 180.0) % 360.0 - 180.0)
    return diff <= tol


def wind_label(wind_dir_deg: Optional[float], park_orientation_deg: Optional[float]) -> str:
    out = wind_is_out(wind_dir_deg, park_orientation_deg)
    if out is None:
        return "unknown"
    if out:
        return "out"
    blow_toward = (wind_dir_deg + 180.0) % 360.0
    diff = abs((blow_toward - park_orientation_deg + 180.0) % 360.0 - 180.0)
    return "in" if diff >= 110.0 else "cross"
