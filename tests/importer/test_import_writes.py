"""What an import may write: the columns its file has, into rows of its own category."""

import pytest

from execution_world import ADMIN, OPERATOR, VENDOR_PLUGIN, VENDOR_ROW, build_world
from twotrack.core import Principal
from twotrack.core.audit import AuditStatus
from twotrack.core.entities import ImportRejected, ImportService


@pytest.fixture
def world():
    world = build_world(extra_plugins=(VENDOR_PLUGIN,))
    world.store.upsert_many("vendor", [VENDOR_ROW])
    return world


def service(world):
    return ImportService(world.registry, world.store, world.audit, world.grants,
                         clock=world.clock)


def run(world, principal, entity, raw):
    """Preview then commit one CSV as ``principal``; returns (preview, result)."""
    svc = service(world)
    preview = svc.preview(principal, entity, "upload.csv", raw)
    result = svc.commit(principal, entity, "upload.csv", raw, expected_digest=preview.digest,
                        grant_token=preview.grant.token)
    return preview, result


def rejected(code, fn, *args, **kwargs):
    with pytest.raises(ImportRejected) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


# ---------------------------------------------------------------- an update merges


def test_an_update_keeps_the_fields_its_file_has_no_column_for(world):
    preview, result = run(world, OPERATOR, "vendor", b"vendor_id,name\nV-1,Acme Ltd\n")
    assert preview.rows[0].action == "update" and result.written == 1
    row = world.store.get("vendor", "V-1")
    assert row["name"] == "Acme Ltd"
    # the admin-only fields the operator cannot see, and a visible one the file does not carry
    assert (row["contact_email"], row["discount_pct"], row["region"]) == (
        "orders@acme.example", 1.2, "EU")


def test_a_mapped_column_with_an_empty_cell_clears_an_optional_field(world):
    run(world, OPERATOR, "vendor", b"vendor_id,name,region\nV-1,Acme,\n")
    row = world.store.get("vendor", "V-1")
    assert row["region"] is None
    assert (row["contact_email"], row["discount_pct"]) == ("orders@acme.example", 1.2)


def test_a_mapped_column_with_an_empty_cell_for_a_required_field_is_a_row_error(world):
    preview = service(world).preview(OPERATOR, "vendor", "u.csv", b"vendor_id,name\nV-1,\n")
    assert preview.rows[0].errors == ["Name: required"] and preview.grant is None
    assert world.store.get("vendor", "V-1")["name"] == "Acme"


def test_a_new_row_is_created_from_the_columns_given(world):
    preview, result = run(world, OPERATOR, "vendor", b"vendor_id,name\nV-2,Beta\n")
    assert preview.rows[0].action == "new" and result.written == 1
    row = world.store.get("vendor", "V-2")
    assert (row["name"], row["region"], row["contact_email"], row["discount_pct"]) == (
        "Beta", None, None, None)


def test_an_update_inside_a_category_keeps_its_other_fields(world):
    run(world, OPERATOR, "fastener", b"sku,thread_pitch\nF-1,2.5\n")
    row = world.store.get("fastener", "F-1")
    assert (row["thread_pitch"], row["name"], row["qty"], row["_entity"]) == (
        2.5, "Hex bolt", 40, "fastener")
    assert (row["unit_cost"], row["supplier"]) == (0.25, "Supplier Alpha")


# ---------------------------------------------------------------- columns a role may not write

EMAIL_FILE = b"vendor_id,name,email\nV-1,Acme,attacker@evil.example\n"


def test_an_operator_cannot_write_a_column_it_cannot_see(world):
    error = rejected("restricted_column", service(world).preview, OPERATOR, "vendor", "u.csv",
                     EMAIL_FILE)
    assert error.message == ("your role cannot write the column(s) “email”; remove them from "
                             "the file and upload again")
    assert "“email”" in error.text("zh") and "无权" in error.text("zh")
    assert world.grants.pending() == []  # nothing was offered for confirmation
    assert world.store.get("vendor", "V-1")["contact_email"] == "orders@acme.example"
    (entry,) = world.audit.list().items  # refused and on record, like any refused upload
    assert (entry.status, entry.problem, entry.user_id) == (
        AuditStatus.INVALID, "restricted_column", "olga")
    assert [(s.step, s.status.value, s.facts) for s in entry.trace][-1] == (
        "validate", "blocked", {"code": "restricted_column"})


def test_every_restricted_column_is_named_and_the_field_behind_it_is_not(world):
    raw = b"vendor_id,name,email,Discount,Contact Email\nV-1,Acme,a@b.example,5,c@d.example\n"
    # two columns on one restricted field: the refusal comes before the collision message,
    # which would name the field and its id to a role that cannot see it
    error = rejected("restricted_column", service(world).preview, OPERATOR, "vendor", "u.csv",
                     raw)
    assert "“email”, “Discount” and “Contact Email”" in error.message
    assert "contact_email" not in error.message and "discount_pct" not in error.message


def test_an_admin_may_write_those_columns(world):
    run(world, ADMIN, "vendor", b"vendor_id,name,email,discount\nV-1,Acme,new@acme.example,2.5\n")
    row = world.store.get("vendor", "V-1")
    assert (row["contact_email"], row["discount_pct"], row["region"]) == (
        "new@acme.example", 2.5, "EU")


def test_an_operator_may_create_a_row_without_those_columns(world):
    run(world, OPERATOR, "vendor", b"vendor_id,name,region\nV-2,Beta,US\n")
    row = world.store.get("vendor", "V-2")
    assert (row["name"], row["region"], row["contact_email"]) == ("Beta", "US", None)


def test_the_restricted_columns_are_refused_at_commit_too(world):
    svc = service(world)
    preview = svc.preview(ADMIN, "vendor", "u.csv", EMAIL_FILE)  # an admin may preview it
    demoted = Principal(user_id=ADMIN.user_id, roles=frozenset({"operator"}))
    rejected("restricted_column", svc.commit, demoted, "vendor", "u.csv", EMAIL_FILE,
             expected_digest=preview.digest, grant_token=preview.grant.token)
    assert world.store.get("vendor", "V-1")["contact_email"] == "orders@acme.example"
    refusal = world.audit.list().items[0]
    assert (refusal.status, refusal.problem) == (AuditStatus.INVALID, "restricted_column")
    assert refusal.grant_id == preview.grant.grant_id  # on the record of the grant it presented


# ---------------------------------------------------------------- the category of a stored row

MOVED = ("“F-1” is stored as Fastener, but this import is for Part; an import cannot change "
         "the category of an existing row")


def test_an_import_cannot_change_the_category_of_a_stored_row(world):
    svc = service(world)
    raw = b"sku,name\nF-1,Renamed\nP-NEW,Fresh\n"  # the root entity: F-1 is a fastener
    preview = svc.preview(OPERATOR, "part", "u.csv", raw)
    assert preview.rows[0].errors == [MOVED] and preview.rows[0].action is None
    assert preview.rows[0].field_errors[0].field_id is None  # about the row, not a cell
    assert [r.action for r in preview.rows] == [None, "new"]
    assert (preview.valid_count, preview.error_count) == (1, 1)
    zh = svc.preview(OPERATOR, "part", "u.csv", raw, language="zh").rows[0].errors[0]
    assert zh == "“F-1”已存储为“Fastener”，而本次导入的类别是“Part”；导入不能更改已有记录的类别"

    result = svc.commit(OPERATOR, "part", "u.csv", raw, expected_digest=preview.digest,
                        grant_token=preview.grant.token)  # the commit skips it as well
    assert (result.written, result.skipped) == (1, 1)
    row = world.store.get("part", "F-1")
    assert (row["_entity"], row["name"], row["thread_pitch"]) == ("fastener", "Hex bolt", 1.25)
    assert world.store.get("part", "P-NEW")["_entity"] == "part"


def test_a_sibling_category_and_a_parent_row_are_refused_too(world):
    world.store.upsert_many("part", [{"sku": "P-1", "name": "Plain"}])
    svc = service(world)
    sibling = svc.preview(OPERATOR, "cable", "u.csv", b"sku,name\nF-1,Moved\n")
    assert "stored as Fastener" in sibling.rows[0].errors[0]
    assert "import is for Cable" in sibling.rows[0].errors[0]
    upward = svc.preview(OPERATOR, "fastener", "u.csv", b"sku,thread_pitch\nP-1,1.5\n")
    assert "stored as Part" in upward.rows[0].errors[0]  # a plain row does not become a fastener
    assert "import is for Fastener" in upward.rows[0].errors[0]
    assert (upward.valid_count, upward.grant) == (0, None)


def test_a_row_that_changed_category_after_the_preview_is_refused_at_commit(world):
    svc = service(world)
    raw = b"sku,name\nR-1,Fresh\n"
    preview = svc.preview(OPERATOR, "part", "u.csv", raw)
    assert preview.rows[0].action == "new"
    world.store.upsert_many("cable", [{"sku": "R-1", "name": "Taken"}])  # another upload won
    rejected("digest_mismatch", svc.commit, OPERATOR, "part", "u.csv", raw,
             expected_digest=preview.digest, grant_token=preview.grant.token)
    assert world.store.get("part", "R-1")["_entity"] == "cable"
