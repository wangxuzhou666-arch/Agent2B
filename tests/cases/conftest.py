"""Fixtures for the reference cases and the abuse-scenario matrix.

``pi_harness`` and ``wire`` are the explorer tests' own (tests/explore/conftest.py): the
installed Pi worker, checked once per session (a skip without Node or the install, a failure
under ``TWOTRACK_REQUIRE_PI=1``), and every request the zero-key model endpoint receives.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.cases.world import CaseWorld, build_case_world
from tests.explore.conftest import (
    REQUIRE_PI_ENV,
    pi_harness,  # noqa: F401  (a fixture, re-exported)
    wire,  # noqa: F401  (a fixture, re-exported)
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "pi: runs the Pi worker (Node 22.19+ and `npm ci --ignore-scripts` in "
                   f"harness/pi); skipped without it unless {REQUIRE_PI_ENV}=1")


@pytest.fixture
def make_case_world(tmp_path: Path) -> Iterator[Callable[..., CaseWorld]]:
    """``make_case_world(runtime="pi" | "builtin", **options)``: see ``build_case_world``."""
    built: list[CaseWorld] = []

    def make(**options: Any) -> CaseWorld:
        world = build_case_world(tmp_path / f"data-{len(built)}", **options)
        built.append(world)
        return world

    yield make
    for world in built:
        world.runtime.close()
