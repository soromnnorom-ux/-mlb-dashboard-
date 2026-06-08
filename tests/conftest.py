import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def schedule_json():
    return json.loads(_read("schedule_2025-07-19.json"))


@pytest.fixture
def boxscore_json():
    return json.loads(_read("boxscore_777094.json"))


@pytest.fixture
def people_json():
    return json.loads(_read("people_multi.json"))


@pytest.fixture
def batter_csv():
    return _read("savant_batter_2025.csv")


@pytest.fixture
def pitcher_csv():
    return _read("savant_pitcher_2025.csv")


@pytest.fixture
def arsenals_csv():
    return _read("savant_arsenals_2025.csv")


@pytest.fixture
def statcast_csv():
    return _read("savant_statcast_665489.csv")


@pytest.fixture
def openmeteo_json():
    return json.loads(_read("openmeteo_rogers.json"))
