"""Fixtures for the explorer tests.

Tests marked ``pi`` run the real worker (harness/pi) under Node. ``pi_harness`` finds and checks
the install once per session: without Node 22.19+ or ``npm ci --ignore-scripts`` in harness/pi
those tests skip, so the suite stays green on a machine without Node. ``TWOTRACK_REQUIRE_PI=1``
(the CI job that installs the worker) turns that skip into a failure, so a broken install can
never pass as skipped. ``TWOTRACK_PI_HARNESS_DIR`` points the tests at another install.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.explore.world import PiWorld, build_world
from twotrack.core.explore.endpoint import ModelEndpoint
from twotrack.core.explore.pi import ExplorerUnavailable, PiHarness, check_worker, locate_harness
from twotrack.settings import PI_HARNESS_ENV

REQUIRE_PI_ENV = "TWOTRACK_REQUIRE_PI"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "pi: runs the Pi worker (Node 22.19+ and `npm ci --ignore-scripts` in "
                   f"harness/pi); skipped without it unless {REQUIRE_PI_ENV}=1")


@pytest.fixture(scope="session")
def pi_harness() -> PiHarness:
    """The installed worker, checked the way the server checks it at start-up (files, Node,
    pinned versions, a live hello)."""
    try:
        harness = locate_harness(os.environ.get(PI_HARNESS_ENV, "").strip() or None)
        check_worker(harness)
    except ExplorerUnavailable as exc:
        if os.environ.get(REQUIRE_PI_ENV, "").strip() == "1":
            pytest.fail(f"{REQUIRE_PI_ENV}=1 but the Pi worker cannot run: {exc}",
                        pytrace=False)
        pytest.skip(f"the Pi worker is not installed: {exc}")
    return harness


@pytest.fixture
def make_world(tmp_path: Path) -> Iterator[Callable[..., PiWorld]]:
    """``make_world(harness, steps, **options)``: see ``world.build_world``."""
    built: list[PiWorld] = []

    def make(harness: PiHarness, steps: Any = (), **options: Any) -> PiWorld:
        world = build_world(tmp_path / f"data-{len(built)}", harness, steps, **options)
        built.append(world)
        return world

    yield make
    for world in built:
        world.runtime.close()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Every request the zero-key endpoint receives, as the worker sent it."""
    received: list[dict] = []
    answer = ModelEndpoint.answer

    def recording(self: ModelEndpoint, request: dict) -> bytes:
        received.append(request)
        return answer(self, request)

    monkeypatch.setattr(ModelEndpoint, "answer", recording)
    return received
