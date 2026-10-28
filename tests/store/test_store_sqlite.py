"""Storage: root tables, category rows, fail-closed schema, read-only query, visibility."""

import pytest

from execution_world import ADMIN, CABLE, FASTENER, OPERATOR, PART, PLUGIN, VIEWER
from twotrack.core import EntitySpec, FieldSpec, FieldType, Plugin, build_registry
from twotrack.core.database import Database
from twotrack.core.store import (
    ReadOnlyStore,
    SchemaMismatchError,
    SqliteStore,
    StoreError,
    project_for,
    project_rows,
)


def columns(db, table="e_part"):
    with db.read() as conn:
        return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def registry_with(*entities):
    return build_registry([Plugin(name="parts", version="1", entities=entities)])


def test_child_rows_live_in_the_root_table(world):
    assert columns(world.db) == ["sku", "name", "qty", "unit_cost", "supplier", "status",
                                 "_entity", "_attrs"]
    everything = world.store.list("part")
    assert everything.total == 2
    assert {r["_entity"] for r in everything.rows} == {"fastener", "cable"}
    cables = world.store.list("cable")
    assert [r["sku"] for r in cables.rows] == ["C-1"] and cables.rows[0]["length_m"] == 2.0
    assert world.store.get("fastener", "C-1") is None  # wrong category
    assert world.store.get("part", "F-1")["thread_pitch"] == 1.25


def test_new_category_adds_no_columns(world):
    before = columns(world.db)
    sensor = EntitySpec(id="sensor", label="Sensor", extends="part",
                        attrs=(FieldSpec(id="range_c", label="Range", type=FieldType.FLOAT),))
    store = SqliteStore(world.db)
    store.ensure_schema(registry_with(PART, FASTENER, CABLE, sensor))
    assert columns(world.db) == before
    store.upsert_many("sensor", [{"sku": "S-1", "name": "Probe", "range_c": 150}])
    assert store.list("part").total == 3 and store.count("sensor") == 1


def test_changed_core_field_fails_startup(world):
    changed = PART.model_copy(update={"fields": (
        *PART.fields[:2], FieldSpec(id="qty", label="Quantity", type=FieldType.FLOAT),
        *PART.fields[3:], FieldSpec(id="weight", label="Weight", type=FieldType.FLOAT))})
    with pytest.raises(SchemaMismatchError) as caught:
        SqliteStore(world.db).ensure_schema(registry_with(changed, FASTENER, CABLE))
    problems = caught.value.problems
    assert any("'qty' is REAL" in p or "'qty' is INTEGER" in p for p in problems)
    assert any("missing column 'weight'" in p for p in problems)


def test_rows_of_removed_category_fail_startup(world):
    with pytest.raises(SchemaMismatchError, match="undeclared entities"):
        SqliteStore(world.db).ensure_schema(registry_with(PART, FASTENER))


def test_store_refuses_use_before_schema_check():
    with pytest.raises(StoreError, match="ensure_schema"):
        SqliteStore(Database()).list("part")


def test_upsert_validates_category_attrs_all_or_nothing(world):
    with pytest.raises(StoreError) as caught:
        world.store.upsert_many("fastener", [
            {"sku": "F-2", "name": "ok", "thread_pitch": 0.8},
            {"sku": "F-3", "name": "bad", "thread_pitch": "coarse"},
            {"sku": "F-4", "name": "missing pitch"},
            {"sku": "F-5", "length_m": 3},  # an attr of a sibling category
        ])
    text = "\n".join(caught.value.problems)
    assert "record 2: Thread Pitch: must be a number" in text
    assert "record 3: Thread Pitch: required" in text
    assert "record 4: unknown field(s) ['length_m']" in text
    assert world.store.count("fastener") == 1  # nothing written


def test_merge_keeps_the_fields_a_record_leaves_out(world):
    world.store.upsert_many("fastener", [{"sku": "F-1", "name": "Renamed"}], merge=True)
    row = world.store.get("fastener", "F-1")
    assert row["name"] == "Renamed"
    assert (row["qty"], row["unit_cost"], row["supplier"]) == (40, 0.25, "Supplier Alpha")
    assert row["thread_pitch"] == 1.25 and row["_entity"] == "fastener"
    world.store.upsert_many("cable", [{"sku": "C-1", "length_m": 3.0}], merge=True)
    cable = world.store.get("cable", "C-1")
    assert (cable["length_m"], cable["vendor_ref"]) == (3.0, "CW-REF-889")  # an attr is kept too


def test_merge_clears_a_field_the_record_names_with_none(world):
    world.store.upsert_many("cable", [{"sku": "C-1", "qty": None, "length_m": None}], merge=True)
    cable = world.store.get("cable", "C-1")
    assert cable["qty"] is None and "length_m" not in cable  # an empty attr is not stored
    assert (cable["name"], cable["vendor_ref"]) == ("Patch lead", "CW-REF-889")


def test_merge_into_a_missing_row_creates_it_from_the_fields_given(world):
    world.store.upsert_many("fastener", [{"sku": "F-9", "thread_pitch": 1.0}], merge=True)
    row = world.store.get("fastener", "F-9")
    assert (row["thread_pitch"], row["name"], row["qty"]) == (1.0, None, None)
    with pytest.raises(StoreError, match="nothing was written"):  # a new row still needs its key
        world.store.upsert_many("fastener", [{"thread_pitch": 1.0}], merge=True)


def test_merge_checks_the_fields_given_and_all_or_nothing(world):
    with pytest.raises(StoreError) as caught:
        world.store.upsert_many("fastener", [
            {"sku": "F-1", "name": "ok"},
            {"sku": "F-1", "qty": "lots"},
            {"sku": "F-1", "thread_pitch": None},  # clearing a required attr
            {"sku": "F-1", "length_m": 3},  # an attr of a sibling category
        ], merge=True)
    text = "\n".join(caught.value.problems)
    assert "record 2: Quantity: must be a number" in text
    assert "record 3: Thread Pitch: required" in text
    assert "record 4: unknown field(s) ['length_m']" in text
    assert world.store.get("fastener", "F-1")["name"] == "Hex bolt"  # the valid one was not kept


def test_merge_never_changes_the_category_of_a_stored_row(world):
    for entity in ("part", "cable"):
        with pytest.raises(StoreError, match="nothing was written") as caught:
            world.store.upsert_many(entity, [{"sku": "F-1", "name": "Moved"}], merge=True)
        assert "'fastener'" in "\n".join(caught.value.problems)
    row = world.store.get("part", "F-1")
    assert (row["_entity"], row["name"], row["thread_pitch"]) == ("fastener", "Hex bolt", 1.25)


def test_without_merge_a_record_still_replaces_the_whole_row(world):
    world.store.upsert_many("fastener", [{"sku": "F-1", "name": "Replaced", "thread_pitch": 2.0}])
    row = world.store.get("fastener", "F-1")
    assert (row["name"], row["thread_pitch"]) == ("Replaced", 2.0)
    assert (row["qty"], row["unit_cost"], row["supplier"]) == (None, None, None)


def test_filters_sort_and_paging(world):
    world.store.upsert_many("fastener", [
        {"sku": f"F-{i}", "name": f"bolt {i}", "qty": i, "thread_pitch": 1.0 + i / 10,
         "status": "ACTIVE" if i % 2 else "retired"} for i in range(2, 7)])
    page = world.store.list("fastener", filters={"status": "active"}, sort="-qty", limit=2)
    assert page.total == 2 and [r["sku"] for r in page.rows] == ["F-5", "F-3"]
    by_attr = world.store.list("fastener", filters={"thread_pitch": [1.2, 1.25]})
    assert sorted(r["sku"] for r in by_attr.rows) == ["F-1", "F-2"]
    second = world.store.list("part", sort="sku", limit=3, offset=3)
    assert [r["sku"] for r in second.rows] == ["F-3", "F-4", "F-5"]  # after C-1, F-1, F-2
    with pytest.raises(StoreError, match="no field 'nope'"):
        world.store.list("part", filters={"nope": 1})


def test_query_is_read_only_and_limited_to_entity_tables(world):
    rows = world.store.query("WITH t AS (SELECT sku, qty FROM e_part) "
                             "SELECT sku FROM t WHERE qty > ? ORDER BY sku", (10,))
    assert rows == [{"sku": "F-1"}]
    for sql in ("DELETE FROM e_part", "UPDATE e_part SET qty = 0",
                "SELECT * FROM audit_log", "SELECT * FROM confirmation_grant",
                "SELECT name FROM sqlite_master", "SELECT 1; DROP TABLE e_part",
                "/* hi */ INSERT INTO e_part (sku) VALUES ('x')", "PRAGMA table_info(e_part)"):
        with pytest.raises(StoreError):
            world.store.query(sql)
    assert world.store.count("part") == 2


def test_read_only_view_blocks_writes(world):
    view = ReadOnlyStore(world.store)
    assert view.count("part") == 2
    with pytest.raises(StoreError, match="read-only"):
        view.upsert_many("part", [{"sku": "Z"}])
    with pytest.raises(StoreError, match="read-only"):
        view.upsert_many("part", [{"sku": "Z"}], merge=True)
    with pytest.raises(StoreError):
        view.transaction()


def test_visibility_projection_hides_restricted_fields(world):
    registry = build_registry([PLUGIN])
    row = world.store.get("cable", "C-1")
    cable = registry.entity("cable")
    seen_by_viewer = project_for(VIEWER, cable, {**row, "smuggled": "x"})
    assert "unit_cost" not in seen_by_viewer and "supplier" not in seen_by_viewer
    assert "vendor_ref" not in seen_by_viewer and "smuggled" not in seen_by_viewer
    assert seen_by_viewer["name"] == "Patch lead" and seen_by_viewer["_entity"] == "cable"
    assert project_for(OPERATOR, cable, row)["vendor_ref"] == "CW-REF-889"
    assert project_for(ADMIN, cable, row)["supplier"] == "Supplier Beta"
    listing = project_rows(VIEWER, registry, world.store.list("part").rows, "part")
    assert all("unit_cost" not in r for r in listing)
    assert {"thread_pitch", "length_m"} <= {k for r in listing for k in r}
