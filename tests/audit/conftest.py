import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution_world import build_world  # noqa: E402


@pytest.fixture
def world():
    return build_world()
