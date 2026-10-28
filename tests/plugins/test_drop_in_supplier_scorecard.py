"""The supplier scorecard is a read tool added through TWOTRACK_PLUGIN_PATHS alone: no core,
server, provider or built-in file changes, and every guard still applies to it."""

import dataclasses
import json
import shutil
from pathlib import Path

import pytest
from world import ADMIN, OPERATOR, VIEWER, build_world, builtin_registry, load, po

from twotrack.core import (
    PLUGIN_PATHS_ENV,
    RegistryError,
    RouteSource,
    build_registry,
    load_registry,
)
from twotrack.core.audit import AuditQuery, AuditStatus
from twotrack.core.graph import Engine, run_query
from twotrack.core.guards.numeric_lock import lock_narrative
from twotrack.core.pipeline import PipelineStatus
from twotrack.providers.mock import MockProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"
SOURCE = EXAMPLES / "supplier_scorecard.py"

SUPPLIERS = [{"supplier_id": f"SUP-00{n}", "name": f"Supplier {n}"} for n in range(1, 5)]
# today is 2026-10-01; the default window starts 2026-07-03
ORDERS = [
    po("PO-11", "SKU-1", "SUP-001", 10, "received", ordered_at="2026-08-10", eta="2026-09-01"),
    po("PO-12", "SKU-1", "SUP-001", 10, "received", ordered_at="2026-08-20", eta="2026-09-10"),
    po("PO-13", "SKU-1", "SUP-001", 10, "shipped", ordered_at="2026-09-01", eta="2026-09-25"),
    po("PO-14", "SKU-1", "SUP-001", 10, "open", ordered_at="2026-08-25", eta="2026-09-21"),
    po("PO-15", "SKU-1", "SUP-001", 10, "open", ordered_at="2026-09-20", eta="2026-10-20"),
    po("PO-16", "SKU-1", "SUP-001", 10, "draft", ordered_at="2026-08-30", eta="2026-09-05"),
    po("PO-17", "SKU-1", "SUP-001", 10, "cancelled", ordered_at="2026-08-28", eta="2026-09-10"),
    po("PO-18", "SKU-1", "SUP-001", 10, "received", ordered_at="2026-04-01", eta="2026-05-01"),
    po("PO-21", "SKU-2", "SUP-002", 5, "received", ordered_at="2026-09-01", eta="2026-09-15"),
    po("PO-22", "SKU-2", "SUP-002", 5, "received", ordered_at="2026-09-05", eta="2026-09-20"),
    po("PO-23", "SKU-2", "SUP-002", 5, "received", ordered_at="2026-09-25", eta="2026-10-10"),
    po("PO-31", "SKU-3", "SUP-003", 7, "open", ordered_at="2026-09-28", eta="2026-10-12"),
]
# SUP-001 in the default window: 6 placed (PO-11 to PO-15 and the cancelled PO-17; the draft
# PO-16 is not placed, PO-18 is older); due = 2 received + 2 past ETA (6 and 10 days) = 4
SUP_001 = {"po_count": 6, "due_count": 4, "received_count": 2, "overdue_count": 2,
           "received_rate_pct": 50.0, "avg_days_overdue": 8.0, "max_days_overdue": 10,
           "window_days": 90}
# 30 days start on 2026-09-01 (inclusive): PO-13 and PO-15 only
SUP_001_30 = {"po_count": 2, "due_count": 1, "received_count": 0, "overdue_count": 1,
              "received_rate_pct": 0.0, "avg_days_overdue": 6.0, "max_days_overdue": 6,
              "window_days": 30}
EVERYONE = {"supplier_count": 3, "po_count": 10, "due_count": 7, "received_count": 5,
            "overdue_count": 2, "received_rate_pct": 71.4, "avg_days_overdue": 8.0,
            "max_days_overdue": 10, "window_days": 90}


@pytest.fixture
def registry(monkeypatch, tmp_path):
    shutil.copy(SOURCE, tmp_path)
    monkeypatch.setenv(PLUGIN_PATHS_ENV, str(tmp_path))
    return load_registry()


@pytest.fixture
def world(registry):
    world = build_world(registry)
    load(world, suppliers=SUPPLIERS, orders=ORDERS)
    return world


class Scripted(MockProvider):
    """The mock provider with a scripted narration, keeping the views it was shown."""

    def __init__(self, narration=None):
        super().__init__()
        self.narration, self.views = narration, []

    def narrate(self, question, view):
        self.views.append(dict(view))
        return self.narration if self.narration is not None else super().narrate(question, view)


def engine(world, provider=None):
    return Engine(registry=world.registry, provider=provider or MockProvider(),
                  pipeline=world.pipeline, audit=world.audit)


def ask(world, query, principal=OPERATOR, provider=None, **kwargs):
    return run_query(engine(world, provider), world.ctx(principal), query, **kwargs)


def audited(world, **filters):
    return world.audit.list(AuditQuery(**filters)).items


# ---------------------------------------------------------------- the registry loads it


def test_not_loaded_without_the_plugin_path(monkeypatch):
    monkeypatch.delenv(PLUGIN_PATHS_ENV, raising=False)
    assert "supplier_scorecard" not in load_registry().tools


def test_plugin_path_adds_one_read_tool_and_nothing_else(registry):
    builtin = builtin_registry()
    assert [p.name for p in registry.plugins] == [*(p.name for p in builtin.plugins),
                                                  "supplier_scorecard"]
    assert set(registry.tools) - set(builtin.tools) == {"supplier_scorecard"}
    assert set(registry.entities) == set(builtin.entities) and not registry.workflows.keys() - \
        builtin.workflows.keys()
    tool = registry.tool("supplier_scorecard")
    assert tool.effect.value == "read" and tool.roles == ("admin", "operator")
    assert set(tool.descriptions) == {"en", "zh"} and set(tool.title) == {"en", "zh"}
    assert any(ord(c) > 0x4E00 for k in tool.keywords for c in k)  # Chinese keywords too


def test_both_examples_load_side_by_side():
    registry = load_registry(extra_paths=[EXAMPLES])
    assert {"expiring_soon", "supplier_scorecard"} <= set(registry.tools)


def test_the_registry_refuses_a_scorecard_that_widens_access_or_lacks_its_entities(registry):
    plugin = next(p for p in registry.plugins if p.name == "supplier_scorecard")
    others = [p for p in registry.plugins if p is not plugin]
    widened = dataclasses.replace(plugin.tools[0], roles=("admin", "auditor"))
    with pytest.raises(RegistryError, match="unknown role"):
        build_registry([*others, dataclasses.replace(plugin, tools=(widened,))])
    with pytest.raises(RegistryError, match="entity 'supplier' is not declared by any plugin"):
        build_registry([plugin])


# ---------------------------------------------------------------- aggregates, never rows


@pytest.mark.parametrize(("principal", "params", "expected"), [
    (OPERATOR, {"supplier_id": "SUP-001"}, SUP_001),
    (ADMIN, {"supplier_id": "SUP-001"}, SUP_001),
    (OPERATOR, {"supplier_id": "SUP-001", "window_days": 30}, SUP_001_30),
    (OPERATOR, {"supplier_id": "SUP-002"},
     {"po_count": 3, "due_count": 3, "received_count": 3, "overdue_count": 0,
      "received_rate_pct": 100.0, "window_days": 90}),
    (OPERATOR, {"supplier_id": "SUP-003"},
     {"po_count": 1, "due_count": 0, "received_count": 0, "overdue_count": 0,
      "window_days": 90}),
    (OPERATOR, {}, EVERYONE),
])
def test_operators_and_admins_get_aggregates_and_no_rows(world, principal, params, expected):
    outcome = world.run(principal, "supplier_scorecard", params)
    assert outcome.status is PipelineStatus.OK
    assert outcome.result.aggregates == expected
    assert outcome.result.rows == [] and outcome.result.columns == []
    assert outcome.result.provenance.row_count == expected["po_count"]
    entry = world.audit.get(outcome.audit_ids[-1])
    assert entry.status is AuditStatus.OK and entry.effect == "read"


def test_what_the_figures_cannot_say_is_a_message_of_identifiers(world):
    everyone = world.run(OPERATOR, "supplier_scorecard", {}).result
    assert everyone.message == "Highest share of due orders received: SUP-002, SUP-001."
    none_due = world.run(OPERATOR, "supplier_scorecard", {"supplier_id": "SUP-003"}).result
    assert "no share to report" in none_due.message
    nothing = world.run(OPERATOR, "supplier_scorecard", {"supplier_id": "SUP-004"}).result
    assert nothing.aggregates["po_count"] == 0 and "No purchase order" in nothing.message


def test_the_narrator_view_carries_no_rows(world):
    result = world.run(OPERATOR, "supplier_scorecard", {}).result
    assert set(result.narrator_view()) == {"aggregates", "row_count", "columns", "scope",
                                           "message"}
    provider = Scripted()
    ask(world, "supplier scorecard for SUP-001", provider=provider)
    (view,) = provider.views
    assert "rows" not in view and view["columns"] == [] and view["aggregates"] == SUP_001
    assert "PO-1" not in json.dumps(view)  # no order id reaches a model


# ---------------------------------------------------------------- params with validation


@pytest.mark.parametrize("params", [{"window_days": 0}, {"window_days": 366},
                                    {"window_days": "many"}, {"category": "packaging"},
                                    {"supplier_id": ""}])
def test_bad_params_are_refused_and_audited(world, params):
    outcome = world.run(OPERATOR, "supplier_scorecard", params)
    assert outcome.status is PipelineStatus.INVALID and outcome.result is None
    assert audited(world, target="supplier_scorecard")[0].status is AuditStatus.INVALID


def test_the_window_runs_from_one_to_365_days_and_an_unknown_supplier_is_refused(world):
    for days in (1, 365):
        assert world.run(OPERATOR, "supplier_scorecard", {"window_days": days}).status \
            is PipelineStatus.OK
    outcome = world.run(OPERATOR, "supplier_scorecard", {"supplier_id": "SUP-999"})
    assert outcome.status is PipelineStatus.INVALID and "SUP-999" in outcome.problem


# ---------------------------------------------------------------- a plugin cannot widen access


def test_a_viewer_is_denied_and_the_denial_is_audited(world):
    outcome = world.run(VIEWER, "supplier_scorecard", {})
    assert outcome.status is PipelineStatus.BLOCKED and outcome.result is None
    assert outcome.trace[-1].facts["stage"] == "authz"
    (entry,) = audited(world, target="supplier_scorecard")
    assert entry.status is AuditStatus.BLOCKED and entry.user_id == VIEWER.user_id
    assert entry.roles == ["viewer"] and entry.effect == "read"


def test_asking_for_it_by_name_as_a_viewer_is_blocked_before_the_pipeline_and_audited(world):
    response = ask(world, "supplier scorecard", VIEWER, explicit_target="supplier_scorecard")
    assert response.aggregates == {} and response.error is None
    assert "not available to you" in response.answer
    (entry,) = audited(world, target="supplier_scorecard")
    assert entry.status is AuditStatus.BLOCKED and entry.user_id == VIEWER.user_id


def test_installing_it_gives_nobody_more_than_they_had(world):
    plain = build_world()
    load(plain, suppliers=SUPPLIERS, orders=ORDERS)

    def names(w, principal):
        return set(w.registry.candidates(w.ctx(principal)).names())

    assert names(world, VIEWER) == names(plain, VIEWER)
    assert names(world, OPERATOR) == names(plain, OPERATOR) | {"supplier_scorecard"}


def test_a_tool_declared_read_cannot_write(registry):
    def sneaky(ctx, params):
        ctx.store.upsert_many("supplier", [{"supplier_id": "SUP-999", "name": "Intruder"}])

    world = build_world(broken(registry, sneaky))
    load(world, suppliers=SUPPLIERS, orders=ORDERS)
    outcome = world.run(OPERATOR, "supplier_scorecard", {})
    assert outcome.status is PipelineStatus.ERROR
    assert world.store.get("supplier", "SUP-999") is None


# ---------------------------------------------------------------- routing through the graph


@pytest.mark.parametrize(("query", "expected", "needle"), [
    ("supplier scorecard for SUP-001", SUP_001, "Share of due orders received (%): 50."),
    ("supplier scorecard for SUP-001 over the last 30 days", SUP_001_30,
     "Share of due orders received (%): 0."),
    ("SUP-001 近 30 天的准时交货率", SUP_001_30, "到期采购单已收货占比（%）：0。"),
    ("供应商评分卡", EVERYONE, "到期采购单已收货占比（%）：71.4。"),
])
def test_english_and_chinese_phrasings_route_to_it_with_the_mock_provider(
        world, query, expected, needle):
    response = ask(world, query)
    assert response.decision.target == "supplier_scorecard"
    assert response.decision.source is RouteSource.RULE and response.error is None
    assert response.aggregates == expected and response.rows == []
    assert needle in response.answer
    assert world.audit.get(response.audit_ids[-1]).status is AuditStatus.OK


# ---------------------------------------------------------------- the number lock


def test_every_number_in_the_answer_is_backed_by_the_aggregates(world):
    for query in ("supplier scorecard", "供应商评分卡"):
        response = ask(world, query)
        allowed = [*response.aggregates.values(), response.provenance.row_count]
        assert lock_narrative(response.answer, allowed).stripped_count == 0


def test_a_number_the_model_invents_is_stripped_from_the_answer(world):
    provider = Scripted("SUP-001 received 50% of its due orders. "
                        "97.5% of its orders arrived on time.")
    response = ask(world, "supplier scorecard for SUP-001", provider=provider)
    assert response.answer == "SUP-001 received 50% of its due orders."
    assert response.narrative_stripped == 1


# ---------------------------------------------------------------- plugin errors


def broken(registry, handler):
    plugin = next(p for p in registry.plugins if p.name == "supplier_scorecard")
    tool = dataclasses.replace(plugin.tools[0], handler=handler)
    return build_registry([*(p for p in registry.plugins if p is not plugin),
                           dataclasses.replace(plugin, tools=(tool,))])


def test_a_tool_that_raises_is_an_audited_error_with_no_fallback(registry):
    def explode(ctx, params):
        raise RuntimeError("scorecard exploded")

    world = build_world(broken(registry, explode))
    load(world, suppliers=SUPPLIERS, orders=ORDERS)
    response = ask(world, "supplier scorecard for SUP-001")
    assert response.decision.target == "supplier_scorecard"
    assert response.error == "execution failed (RuntimeError)"  # the type, not the message
    assert response.aggregates == {} and response.rows == []
    assert len(response.audit_ids) == 1
    (entry,) = audited(world)  # one record in all: no other tool ran in its place
    assert entry.target == "supplier_scorecard" and entry.status is AuditStatus.ERROR
    assert "scorecard exploded" in entry.problem

