"""Result projection removes what is hidden from the user, and nothing else."""

from tests.server.conftest import Api
from tests.server.fixtures.stock import PLUGIN
from twotrack.core import (
    EntitySpec,
    FieldSpec,
    Plugin,
    Principal,
    Sensitivity,
    Tool,
    ToolOutputs,
    build_registry,
)
from twotrack.server.projection import hidden_keys, project_result

from .fixtures.stock import NoParams

VIEWER = Principal(user_id="vera", roles=frozenset({"viewer"}))
OPERATOR = Principal(user_id="olga", roles=frozenset({"operator"}))
ADMIN = Principal(user_id="ada", roles=frozenset({"admin"}))
LOCATIONS = {"query": "where", "explicit_target": "item_locations"}


def locations(api, user):
    return api.post("/api/query?stream=false", user, json=LOCATIONS).json()


# ---------------------------------------------------------------- over HTTP


def test_a_public_field_of_another_entity_stays_for_a_viewer(api):
    body = locations(api, "vera")
    assert body["rows"], "the tool returns rows"
    for row in body["rows"]:
        assert {"sku", "name", "bin_id", "zone", "qty"} <= set(row)
    assert {"bin_id", "zone"} <= set(body["columns"])


def test_a_restricted_field_of_another_entity_is_removed_for_a_viewer_only(api):
    viewer = locations(api, "vera")
    assert all("rent" not in row for row in viewer["rows"])
    assert "rent" not in viewer["columns"] and "12.5" not in str(viewer["rows"])
    for user in ("olga", "ada"):  # rent is visible to operators, and admins pass every list
        body = locations(api, user)
        assert {row["rent"] for row in body["rows"]} == {12.5, 7.0}
        assert "rent" in body["columns"]


def test_an_approval_result_is_projected_the_same_way(make_runtime):
    # the replayed request goes through the same projection as the live one
    runtime = make_runtime()
    api = Api(runtime)
    write = {"query": "x", "explicit_target": "set_item_qty",
             "explicit_params": {"sku": "A-1", "qty": 12}}
    grant = api.post("/api/query?stream=false", "olga", json=write).json()["approval"]["grant_id"]
    done = api.post(f"/api/approvals/{grant}/approve", "olga").json()
    assert done["status"] == "ok" and done["aggregates"] == {"new_qty": 12}


# ---------------------------------------------------------------- the rule itself


def registry():
    return build_registry([PLUGIN])


def test_hidden_keys_for_each_role():
    reg = registry()
    item = reg.entity("item")
    assert hidden_keys(VIEWER, reg, item) == {"unit_cost", "supplier", "rent", "vendor_ref"}
    assert hidden_keys(OPERATOR, reg, item) == frozenset()
    assert hidden_keys(ADMIN, reg, item) == frozenset()


def test_computed_columns_and_the_category_marker_always_stay():
    reg = registry()
    rows = [{"sku": "A-1", "reorder_flag": "yes", "_entity": "item", "unit_cost": 0.25}]
    columns, projected = project_result(VIEWER, reg, "item_list", ["sku", "reorder_flag",
                                                                  "unit_cost"], rows)
    assert columns == ["sku", "reorder_flag"]
    assert projected == [{"sku": "A-1", "reorder_flag": "yes", "_entity": "item"}]


def test_a_category_row_is_governed_by_its_own_category():
    reg = registry()
    row = {"sku": "B-1", "_entity": "bolt", "thread_pitch": 1.25, "vendor_ref": "VR-77",
           "unit_cost": 0.3}
    _, viewer = project_result(VIEWER, reg, "item_list", [], [row])
    assert viewer == [{"sku": "B-1", "_entity": "bolt", "thread_pitch": 1.25}]
    _, operator = project_result(OPERATOR, reg, "item_list", [], [row])
    assert operator == [row]


def test_a_target_without_an_entity_hides_only_what_some_entity_hides_from_the_user():
    reg = registry()
    row = {"anything": 1, "zone": "north", "rent": 5.0, "supplier": "S"}
    columns, projected = project_result(VIEWER, reg, "slow_report", list(row), [row])
    assert projected == [{"anything": 1, "zone": "north"}] and columns == ["anything", "zone"]
    assert project_result(ADMIN, reg, None, list(row), [row])[1] == [row]
    assert project_result(VIEWER, reg, "no_such_tool", [], [row])[1] == [{"anything": 1,
                                                                         "zone": "north"}]


def test_an_id_reused_with_different_rules_follows_the_entity_that_owns_the_row():
    shared_public = FieldSpec(id="code", label="Code", sensitivity=Sensitivity.PUBLIC)
    shared_secret = FieldSpec(id="code", label="Code", sensitivity=Sensitivity.RESTRICTED,
                              visible_roles=frozenset({"admin"}))
    key = FieldSpec(id="key", label="Key", required=True)
    plugin = Plugin(
        name="clash", version="0.1.0",
        entities=(EntitySpec(id="open_thing", label="Open", primary_key="key",
                             fields=(key, shared_public)),
                  EntitySpec(id="secret_thing", label="Secret", primary_key="key",
                             fields=(key, shared_secret)),
                  EntitySpec(id="plain_thing", label="Plain", primary_key="key", fields=(key,))),
        tools=tuple(Tool(name=name, description=name, params_model=NoParams,
                         handler=lambda ctx, params: None, roles=("viewer",), effect="read",
                         approval="none", outputs=ToolOutputs(aggregates=(), columns=()),
                         explorable=False, entity=entity)
                    for name, entity in (("open_tool", "open_thing"),
                                         ("secret_tool", "secret_thing"),
                                         ("plain_tool", "plain_thing"))))
    reg = build_registry([plugin])
    row = {"key": "k", "code": "c-1"}
    assert project_result(VIEWER, reg, "open_tool", [], [row])[1] == [row]  # its own rule: public
    assert project_result(VIEWER, reg, "secret_tool", [], [row])[1] == [{"key": "k"}]
    # an entity that does not declare `code` gets the cautious reading: some entity hides it
    assert project_result(VIEWER, reg, "plain_tool", [], [row])[1] == [{"key": "k"}]
    assert project_result(ADMIN, reg, "plain_tool", [], [row])[1] == [row]


def test_projection_copies_rows_instead_of_editing_them():
    reg = registry()
    row = {"sku": "A-1", "unit_cost": 0.25}
    project_result(VIEWER, reg, "item_list", ["sku", "unit_cost"], [row])
    assert row == {"sku": "A-1", "unit_cost": 0.25}
