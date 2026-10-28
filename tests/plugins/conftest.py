"""Fixtures: the handcrafted store (see handcrafted.py) and a seeded store."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from handcrafted import INVENTORY, ORDERS, SKUS, SUPPLIERS  # noqa: E402
from world import build_world, load  # noqa: E402


@pytest.fixture
def small():
    world = build_world()
    load(world, skus=SKUS, inventory=INVENTORY, suppliers=SUPPLIERS, orders=ORDERS)
    return world


@pytest.fixture(scope="module")
def seeded():
    return build_world(seed=True)
