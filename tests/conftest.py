import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpd.well import build_case, load_config, make_segments  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def base():
    """The base case and its segments."""
    case = build_case(load_config(ROOT / "config" / "base_case.yaml"))
    return case, make_segments(case)


@pytest.fixture
def vertical():
    """The simple vertical validation well and its segments."""
    case = build_case(load_config(ROOT / "config" / "validation_case.yaml"))
    return case, make_segments(case)
