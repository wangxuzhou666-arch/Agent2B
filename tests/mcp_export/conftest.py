from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from tests.server.conftest import BOLTS, ITEMS, PASSWORD, USERS
from tests.server.fixtures.stock import PLUGIN
from twotrack.core import build_registry
from twotrack.providers import MockProvider
from twotrack.runtime import Runtime, build_runtime
from twotrack.settings import Settings


@pytest.fixture
def make_runtime(tmp_path: Path) -> Iterator[Callable[..., Runtime]]:
    built: list[Runtime] = []

    def make(provider=None, registry=None, **overrides) -> Runtime:
        settings = replace(Settings(data_dir=tmp_path / f"data{len(built)}"), **overrides)
        runtime = build_runtime(settings, registry=registry or build_registry([PLUGIN]),
                                provider=provider or MockProvider())
        runtime.store.upsert_many("item", ITEMS)
        runtime.store.upsert_many("bolt", BOLTS)
        for name, role in USERS.items():
            runtime.users.add(name, PASSWORD, [role])
        built.append(runtime)
        return runtime

    yield make
    for runtime in built:
        runtime.close()


@pytest.fixture
def runtime(make_runtime) -> Runtime:
    return make_runtime()
