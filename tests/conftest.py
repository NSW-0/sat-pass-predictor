from pathlib import Path

import pytest

from satpass.tle import load_tle_file

DATA = Path(__file__).parent / "data"
ISS_TLE_FILE = DATA / "iss_2026-05-28.tle"


@pytest.fixture
def iss_tle():
    """A real ISS TLE with epoch 28 May 2026, so the tests never need the internet."""
    return load_tle_file(ISS_TLE_FILE)
