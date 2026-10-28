import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.server.fixtures.stock import PLUGIN
from twotrack.core import build_registry
from twotrack.providers import MockProvider
from twotrack.runtime import Runtime, build_runtime
from twotrack.server.app import create_app
from twotrack.settings import Settings

PASSWORD = "correct-horse-1"
USERS = {"ada": "admin", "olga": "operator", "omar": "operator", "vera": "viewer"}

ITEMS = [
    {"sku": "A-1", "name": "Hex bolt", "qty": 40, "unit_cost": 0.25,
     "supplier": "Supplier Alpha", "status": "active"},
    {"sku": "A-2", "name": "Washer", "qty": 5, "unit_cost": 0.05,
     "supplier": "Supplier Alpha", "status": "active"},
    {"sku": "A-3", "name": "Spacer", "qty": 120, "unit_cost": 0.1,
     "supplier": "Supplier Beta", "status": "retired"},
    {"sku": "A-4", "name": "Clip", "qty": 8, "unit_cost": 0.02,
     "supplier": "Supplier Beta", "status": "active"},
    {"sku": "A-5", "name": "Plug", "qty": 60, "unit_cost": 0.4,
     "supplier": "Supplier Gamma", "status": "active"},
]
BOLTS = [
    {"sku": "B-1", "name": "Carriage bolt", "qty": 25, "unit_cost": 0.3,
     "supplier": "Supplier Alpha", "status": "active", "thread_pitch": 1.25,
     "vendor_ref": "VR-77"},
]


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Frames of an SSE body as (event, data); comment lines are skipped."""
    frames = []
    for block in text.split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event is not None:
            frames.append((event, data))
    return frames


class Api:
    """A TestClient plus tokens for the standard users (issued directly: login is tested
    on its own, and scrypt on every test would only slow them down)."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        # Host: localhost, not the client's default "testserver": a demo-mode app answers only
        # to the loopback names and the ones in TWOTRACK_ALLOWED_HOSTS (server.hosts)
        self.client = TestClient(create_app(runtime), base_url="http://localhost")

    def headers(self, user: str) -> dict[str, str]:
        token, _ = self.runtime.tokens.issue(user)
        return {"Authorization": f"Bearer {token}"}

    def get(self, path: str, user: str | None = None, **kw):
        return self.client.get(path, headers=self.headers(user) if user else None, **kw)

    def post(self, path: str, user: str | None = None, **kw):
        return self.client.post(path, headers=self.headers(user) if user else None, **kw)


@pytest.fixture
def make_runtime(tmp_path: Path) -> Iterator[Callable[..., Runtime]]:
    built: list[Runtime] = []

    def make(provider=None, clock=None, seed=True, **overrides) -> Runtime:
        settings = replace(Settings(data_dir=tmp_path / "data", max_upload_bytes=4096,
                                    max_body_bytes=8192, sse_heartbeat_seconds=15.0),
                           **overrides)
        runtime = build_runtime(settings, registry=build_registry([PLUGIN]),
                                provider=provider or MockProvider(), clock=clock)
        if seed:
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


@pytest.fixture
def api(runtime) -> Api:
    return Api(runtime)
