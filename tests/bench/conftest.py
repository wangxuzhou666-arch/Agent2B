"""Shared fixtures: one seeded world and the question set built from it (read-only use)."""

import pytest
from bench.questions import build_questions
from bench.world import build_world


@pytest.fixture(scope="session")
def world():
    w = build_world()
    yield w
    w.close()


@pytest.fixture(scope="session")
def items(world):
    return build_questions(world)


@pytest.fixture(scope="session")
def by_id(items):
    return {item.id: item for item in items}
