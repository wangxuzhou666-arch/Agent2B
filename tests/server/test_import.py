"""Import: preview -> commit -> approval -> rows, and the ways it must refuse."""

import io
import zipfile

import pytest

from tests.execution_world import VENDOR_PLUGIN, VENDOR_ROW
from tests.server.conftest import PASSWORD, USERS, Api
from tests.server.fixtures.stock import PLUGIN
from twotrack.core import Principal, build_registry
from twotrack.core.audit import AuditQuery
from twotrack.providers import MockProvider
from twotrack.runtime import build_runtime
from twotrack.server.deps import Session
from twotrack.server.routes.imports import _clean_filename, _visible_values
from twotrack.settings import Settings

CSV = (b"Item Code,Name,Qty On Hand,Unit Cost,Status\n"
       b"N-1,Nut,10,0.5,active\n"
       b"N-2,Cap,abc,0.1,active\n"
       b"A-1,Hex bolt,41,0.25,active\n")


@pytest.fixture
def vendor_api(tmp_path):
    """An API whose registry also holds ``vendor``: two fields that only an admin may see."""
    settings = Settings(data_dir=tmp_path / "data", max_upload_bytes=4096, max_body_bytes=8192,
                        sse_heartbeat_seconds=15.0)
    runtime = build_runtime(settings, registry=build_registry([PLUGIN, VENDOR_PLUGIN]),
                            provider=MockProvider())
    runtime.store.upsert_many("vendor", [VENDOR_ROW])
    for name, role in USERS.items():
        runtime.users.add(name, PASSWORD, [role])
    yield Api(runtime)
    runtime.close()


def upload(api, user, data=CSV, entity="item", name="items.csv", **kw):
    return api.post(f"/api/entities/{entity}/import/preview", user,
                    files={"file": (name, data, "text/csv")}, **kw)


def commit(api, user, preview, entity="item", grant=False):
    body = {"digest": preview["digest"]}
    if grant:
        body["grant_id"] = preview["grant"]["grant_id"]
    return api.post(f"/api/entities/{entity}/import/commit", user, json=body)


def test_preview_reports_mapping_row_problems_and_a_digest_without_writing(api):
    response = upload(api, "olga")
    assert response.status_code == 200
    preview = response.json()
    assert preview["file_type"] == "csv" and preview["unmapped"] == []
    assert {m["header"]: m["field_id"] for m in preview["mapping"]}["Item Code"] == "sku"
    assert (preview["valid_count"], preview["error_count"]) == (2, 1)
    rows = {r["row"]: r for r in preview["rows"]}
    assert rows[3]["errors"] == ["Quantity: must be a number"] and rows[3]["values"]["qty"] == "abc"
    assert rows[3]["field_errors"] == [{"field_id": "qty", "message": "must be a number"}]
    assert rows[2]["field_errors"] == []
    assert [rows[2]["action"], rows[4]["action"]] == ["new", "update"]
    assert len(preview["digest"]) == 64 and preview["grant"]["grant_id"]
    assert preview["grant"]["target"] == "import:item"
    assert "token" not in preview["grant"]
    assert api.runtime.store.get("item", "N-1") is None


def test_preview_commit_approve_inserts_rows(api):
    preview = upload(api, "olga").json()
    waiting = commit(api, "olga", preview)
    assert waiting.status_code == 200
    body = waiting.json()
    assert body["status"] == "needs_confirmation" and body["result"] is None
    assert body["approval"]["grant_id"] == preview["grant"]["grant_id"]
    assert body["approval"]["target"] == "import:item"
    assert api.runtime.store.get("item", "N-1") is None  # still nothing written

    listed = api.get("/api/approvals", "olga").json()["items"]
    assert [(i["grant_id"], i["kind"], i["target"]) for i in listed] == [
        (preview["grant"]["grant_id"], "import", "import:item")]

    done = api.post(f"/api/approvals/{body['approval']['grant_id']}/approve", "olga").json()
    assert done["status"] == "ok" and done["kind"] == "import"
    assert (done["import_result"]["written"], done["import_result"]["skipped"]) == (2, 1)
    assert api.runtime.store.get("item", "N-1")["name"] == "Nut"
    assert api.runtime.store.get("item", "A-1")["qty"] == 41
    assert api.runtime.store.get("item", "N-2") is None  # the bad row was skipped
    statuses = [e.status.value for e in reversed(api.runtime.audit.list(
        AuditQuery(target="import:item")).items)]
    assert statuses == ["needs_confirmation", "started", "ok"]
    assert api.get("/api/approvals", "olga").json()["items"] == []
    again = api.post(f"/api/approvals/{body['approval']['grant_id']}/approve", "olga")
    assert again.status_code == 409 and again.json()["error"]["code"] == "grant_used"


ENUMS = (b"Item Code,Name,Status\n"
         b"N-1,Nut,paused\n"
         b"N-1,Cap,active\n")


def test_row_problems_read_as_sentences_per_cell_and_in_the_users_language(api):
    rows = upload(api, "olga", data=ENUMS).json()["rows"]
    assert rows[0]["errors"] == ["Status: must be one of active or retired"]
    assert rows[0]["field_errors"] == [{"field_id": "status",
                                        "message": "must be one of active or retired"}]
    assert rows[1]["field_errors"] == [{"field_id": None,
                                        "message": "duplicate key; first seen in row 2"}]
    zh = api.client.post("/api/entities/item/import/preview",
                         headers={**api.headers("olga"),
                                  "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                         files={"file": ("items.csv", ENUMS, "text/csv")})
    rows = zh.json()["rows"]
    assert rows[0]["field_errors"][0]["message"] == "必须是 active 或 retired 之一"
    assert rows[1]["errors"] == ["主键重复；首次出现在第 2 行"]


def test_the_records_of_one_import_share_a_request_and_replay_their_steps(api):
    preview = upload(api, "olga").json()
    done = commit(api, "olga", preview, grant=True)
    assert done.json()["status"] == "ok"
    chain = list(reversed(api.runtime.audit.list(AuditQuery(target="import:item")).items))
    asked, started, ok = chain
    assert [e.status.value for e in chain] == ["needs_confirmation", "started", "ok"]
    assert asked.request_id and {e.request_id for e in chain} == {asked.request_id}
    assert (asked.parent_id, started.parent_id, ok.parent_id) == (None, asked.id, asked.id)
    steps = lambda e: [(s.step, s.status.value) for s in e.trace]  # noqa: E731
    assert steps(asked) == [("parse", "ok"), ("validate", "ok"), ("gate", "blocked")]
    assert asked.trace[-1].facts == {"stage": "confirmation",
                                     "grant_id": preview["grant"]["grant_id"]}
    assert steps(started) == [("parse", "ok"), ("validate", "ok"), ("gate", "ok")]
    assert steps(ok) == [*steps(started), ("execute", "ok")]
    assert ok.trace[-1].facts == {"written": 2, "skipped": 1}
    detail = api.get(f"/api/audit/{ok.id}", "ada").json()
    assert detail["duration_ms"] == round(sum(s.duration_ms for s in ok.trace), 3)


def test_a_refused_upload_is_audited_with_the_step_that_refused_it(api):
    assert upload(api, "olga", data=b"").status_code == 400
    (refused,) = api.runtime.audit.list(AuditQuery(target="import:item")).items
    assert refused.status.value == "invalid" and refused.request_id
    assert [(s.step, s.status.value, s.facts) for s in refused.trace] == [
        ("parse", "blocked", {"code": "empty"})]


def test_commit_with_the_grant_id_writes_immediately(api):
    preview = upload(api, "olga").json()
    done = commit(api, "olga", preview, grant=True)
    assert done.status_code == 200 and done.json()["status"] == "ok"
    assert done.json()["result"]["written"] == 2
    assert commit(api, "olga", preview, grant=True).status_code == 404  # staged file is spent


def test_admin_can_import_and_viewer_cannot(api):
    assert upload(api, "ada").status_code == 200
    refused = upload(api, "vera")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    entry = api.runtime.audit.list(AuditQuery(user_id="vera")).items[0]
    assert (entry.status.value, entry.problem) == ("blocked", "forbidden")


def test_only_the_previewer_can_commit_it(api):
    preview = upload(api, "olga").json()
    for other in ("omar", "ada"):
        assert commit(api, other, preview).status_code == 404
        assert commit(api, other, preview, grant=True).status_code == 404
    assert commit(api, "olga", preview).status_code == 200


def test_the_staged_bytes_are_what_gets_written(api):
    preview = upload(api, "olga").json()
    waiting = commit(api, "olga", preview).json()
    with api.runtime.database.transaction() as conn:  # someone tampers with the staged file
        conn.execute("UPDATE pending_request SET payload = ?", (CSV.replace(b"Nut", b"Evil"),))
    response = api.post(f"/api/approvals/{waiting['approval']['grant_id']}/approve", "olga")
    assert response.status_code == 409 and response.json()["error"]["code"] == "digest_mismatch"
    assert api.runtime.store.get("item", "N-1") is None
    assert api.runtime.grants.pending() == []


def test_approving_an_uncommitted_preview_is_refused(api):
    preview = upload(api, "olga").json()
    response = api.post(f"/api/approvals/{preview['grant']['grant_id']}/approve", "olga")
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_submitted"
    assert api.get("/api/approvals", "olga").json()["items"] == []  # previews are not approvals


def test_rejecting_a_staged_preview_discards_it(api):
    preview = upload(api, "olga").json()
    grant_id = preview["grant"]["grant_id"]
    assert api.post(f"/api/approvals/{grant_id}/reject", "olga").status_code == 200
    assert commit(api, "olga", preview).status_code == 404
    assert api.runtime.grants.pending() == []


def test_a_preview_with_no_valid_rows_stages_nothing(api):
    preview = upload(api, "olga", b"Item Code,Name,Qty On Hand\nX-1,Thing,abc\n").json()
    assert preview["grant"] is None and preview["valid_count"] == 0
    assert commit(api, "olga", preview).status_code == 404


def test_category_import_requires_its_attrs(api):
    data = b"Item Code,Name,Thread Pitch\nB-9,Stud,1.5\nB-10,Rod,\n"
    preview = upload(api, "olga", data, entity="bolt").json()
    assert preview["valid_count"] == 1 and "Thread Pitch: required" in preview["rows"][1]["errors"]
    commit(api, "olga", preview, entity="bolt", grant=True)
    assert api.runtime.store.get("bolt", "B-9")["thread_pitch"] == 1.5


def test_unknown_entity_and_bad_commit_bodies(api):
    assert upload(api, "olga", entity="nope").status_code == 404
    bad = api.post("/api/entities/item/import/commit", "olga", json={"digest": "short"})
    assert bad.status_code == 422
    unknown = api.post("/api/entities/item/import/commit", "olga", json={"digest": "0" * 64})
    assert unknown.status_code == 404


def test_missing_file_field_is_a_validation_error(api):
    response = api.post("/api/entities/item/import/preview", "olga", data={"x": "y"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


# ---------------------------------------------------------------- hostile uploads


def test_oversize_upload_is_413_from_the_importer_and_audited(api):
    big = b"Item Code,Name\n" + b"Z-1,Thing\n" * 600  # 6 KB > the 4 KB test limit
    response = upload(api, "olga", big)
    assert response.status_code == 413 and response.json()["error"]["code"] == "too_large"
    entry = api.runtime.audit.list(AuditQuery(user_id="olga")).items[0]
    assert entry.problem == "too_large"


def test_huge_upload_is_cut_off_before_it_is_read(api):
    response = upload(api, "olga", b"x" * 300_000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert api.runtime.audit.list().total == 0  # never reached the application


def test_chunked_upload_without_content_length_is_cut_off_too(api):
    def body():
        yield b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n\r\n"
        for _ in range(100):
            yield b"x" * 4096
        yield b"\r\n--b--\r\n"

    response = api.client.post(
        "/api/entities/item/import/preview", content=body(),
        headers={**api.headers("olga"), "Content-Type": "multipart/form-data; boundary=b"})
    assert response.status_code == 413


def test_wrong_file_types_are_400(api):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    response = upload(api, "olga", png, name="items.csv")  # the name says csv; the bytes decide
    assert response.status_code == 400 and response.json()["error"]["code"] == "file_type"
    fake_xlsx = io.BytesIO()
    with zipfile.ZipFile(fake_xlsx, "w") as z:
        z.writestr("hello.txt", "hi")
    response = upload(api, "olga", fake_xlsx.getvalue(), name="items.xlsx")
    assert response.status_code == 400 and response.json()["error"]["code"] == "file_type"
    assert upload(api, "olga", b"MZ\x90\x00" + b"\x00" * 32).status_code == 400


def test_empty_and_headerless_files_are_400(api):
    assert upload(api, "olga", b"").json()["error"]["code"] == "empty"
    assert upload(api, "olga", b"\n\n").json()["error"]["code"] == "no_header"
    assert upload(api, "olga", b"Colour,Size\nred,L\n").json()["error"]["code"] == \
        "missing_columns"


def test_rows_read_and_columns_are_refused_like_data_rows_always_were(make_runtime):
    """Data rows, rows read (empty ones too) and header columns are each a plain 400 that
    carries its own code, in the language asked for, and is audited."""
    api = Api(make_runtime(max_upload_bytes=64 * 1024))
    wide = b",".join([b"a"] * 1025) + b"\n"
    refusals = (
        (b"Item Code,Name\n" + b"Z-1,Thing\n" * 2001, "too_many_rows", "more than 2000 data rows"),
        (b"Item Code,Name\n" + b"\n" * 4001 + b"Z-1,Thing\n", "too_many_rows",
         "more than 4001 rows, empty rows included"),
        (wide, "too_many_columns", "more than 1024 columns in the header row"),
    )
    for raw, code, said in refusals:
        refused = upload(api, "olga", raw)
        assert refused.status_code == 400
        error = refused.json()["error"]
        assert error["code"] == code and said in error["message"]
    entries = api.runtime.audit.list(AuditQuery(user_id="olga")).items
    assert [(e.status.value, e.problem) for e in reversed(entries)] == [
        ("invalid", "too_many_rows"), ("invalid", "too_many_rows"),
        ("invalid", "too_many_columns")]
    zh = api.client.post("/api/entities/item/import/preview",
                         headers={**api.headers("olga"), "Accept-Language": "zh"},
                         files={"file": ("items.csv", wide, "text/csv")})
    assert zh.status_code == 400 and zh.json()["error"]["message"] == "表头行超过了 1024 列"
    assert api.runtime.grants.pending() == []  # a refused upload leaves nothing to confirm


def test_xlsx_round_trip(make_runtime):
    import openpyxl

    api = Api(make_runtime(max_upload_bytes=64 * 1024))

    book = openpyxl.Workbook()
    for row in (["Item Code", "Name", "Qty On Hand"], ["X-9", "Xylophone", 3]):
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    preview = upload(api, "olga", buffer.getvalue(), name="items.xlsx").json()
    assert preview["file_type"] == "xlsx" and preview["valid_count"] == 1
    commit(api, "olga", preview, grant=True)
    assert api.runtime.store.get("item", "X-9")["qty"] == 3


def test_hostile_filenames_are_reduced_to_a_base_name(api):
    assert _clean_filename("../../etc/passwd.csv") == "passwd.csv"
    assert _clean_filename("C:\\Users\\x\\items.csv") == "items.csv"
    assert _clean_filename("a\x00b\nc.csv") == "abc.csv"
    assert _clean_filename(None) == "upload" and len(_clean_filename("n" * 500)) == 120
    upload(api, "olga", name="../../etc/passwd.csv")
    summary = api.runtime.grants.pending()[0].summary
    assert "passwd.csv" in summary and ".." not in summary


def another_csv(number):
    """A different file each time, so every preview has a digest of its own."""
    return b"Item Code,Name\nN-%d,Nut %d\n" % (number, number)


def stored_rows(api, where="1"):
    with api.runtime.database.read() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM pending_request WHERE {where}").fetchone()[0]


@pytest.mark.parametrize("submit", [False, True], ids=["previewed", "submitted"])
def test_uploads_per_user_are_capped_whether_previewed_or_submitted(api, submit):
    """A submitted upload (waiting in the approvals list) counts like a previewed one: a loop of
    preview-and-submit must not grow the table without bound."""
    previews = []
    for number in range(12):
        preview = upload(api, "olga", another_csv(number)).json()
        if submit:
            assert commit(api, "olga", preview).json()["status"] == "needs_confirmation"
        previews.append(preview)
    assert stored_rows(api, "user_id = 'olga'") == 10
    assert len(api.runtime.grants.pending()) == 10  # the two oldest grants were cancelled
    approvals = api.get("/api/approvals", "olga").json()["items"]
    assert len(approvals) == (10 if submit else 0)  # a preview not submitted is no approval
    for pushed_out in previews[:2]:
        assert commit(api, "olga", pushed_out, grant=True).status_code == 404
        gone = api.post(f"/api/approvals/{pushed_out['grant']['grant_id']}/approve", "olga")
        assert gone.status_code == 404
    kept = commit(api, "olga", previews[2], grant=True)  # the oldest of the ten still commits
    assert kept.status_code == 200 and kept.json()["result"]["written"] == 1


def test_the_import_cap_counts_only_the_users_own_imports(api):
    """Neither a confirmation of a question (a ``query`` approval) nor another user's upload
    takes a place among the ten, or is pushed out by the eleventh."""
    upload(api, "omar", another_csv(100))
    asked = api.post("/api/query?stream=false", "olga", json={
        "query": "set it", "explicit_target": "set_item_qty",
        "explicit_params": {"sku": "A-1", "qty": 12}}).json()["approval"]["grant_id"]
    for number in range(11):
        upload(api, "olga", another_csv(number))
    assert stored_rows(api, "user_id = 'olga' AND kind = 'import'") == 10
    assert stored_rows(api, "user_id = 'olga' AND kind = 'query'") == 1
    assert stored_rows(api, "user_id = 'omar'") == 1
    assert api.post(f"/api/approvals/{asked}/approve", "olga").json()["status"] == "ok"


def test_an_operator_import_keeps_the_admin_only_fields_it_cannot_see(vendor_api):
    api = vendor_api
    preview = upload(api, "olga", b"vendor_id,name\nV-1,Acme Ltd\n", entity="vendor").json()
    assert preview["rows"][0]["action"] == "update"
    done = commit(api, "olga", preview, entity="vendor", grant=True)
    assert done.status_code == 200 and done.json()["status"] == "ok"
    row = api.get("/api/entities/vendor/records/V-1", "ada").json()  # the admin sees them all
    assert (row["name"], row["contact_email"], row["discount_pct"], row["region"]) == (
        "Acme Ltd", "orders@acme.example", 1.2, "EU")


def test_an_operator_cannot_import_a_column_it_cannot_see(vendor_api):
    api = vendor_api
    raw = b"vendor_id,name,email\nV-1,Acme,attacker@evil.example\n"
    refused = upload(api, "olga", raw, entity="vendor")
    assert refused.status_code == 400
    error = refused.json()["error"]
    assert error["code"] == "restricted_column" and "“email”" in error["message"]
    assert api.runtime.grants.pending() == []
    entry = api.runtime.audit.list(AuditQuery(user_id="olga")).items[0]
    assert (entry.status.value, entry.problem) == ("invalid", "restricted_column")
    assert upload(api, "ada", raw, entity="vendor").status_code == 200  # an admin may


def test_the_staged_bytes_are_checked_for_restricted_columns_when_approved(vendor_api):
    api = vendor_api
    preview = upload(api, "olga", b"vendor_id,name\nV-1,Acme Ltd\n", entity="vendor").json()
    waiting = commit(api, "olga", preview, entity="vendor").json()
    with api.runtime.database.transaction() as conn:  # the staged file is swapped
        conn.execute("UPDATE pending_request SET payload = ?",
                     (b"vendor_id,name,email\nV-1,Acme Ltd,attacker@evil.example\n",))
    response = api.post(f"/api/approvals/{waiting['approval']['grant_id']}/approve", "olga")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "restricted_column"
    row = api.get("/api/entities/vendor/records/V-1", "ada").json()
    assert (row["name"], row["contact_email"]) == ("Acme", "orders@acme.example")
    assert api.runtime.grants.pending() == []  # the refused grant is cancelled


def test_an_import_cannot_change_the_category_of_a_stored_row_over_http(api):
    preview = upload(api, "olga", b"Item Code,Name\nB-1,Renamed\n").json()  # B-1 is a bolt
    (row,) = preview["rows"]
    assert row["action"] is None and row["errors"] == [
        "“B-1” is stored as Bolt, but this import is for Item; an import cannot change the "
        "category of an existing row"]
    assert preview["valid_count"] == 0 and preview["grant"] is None
    assert api.runtime.store.get("item", "B-1")["_entity"] == "bolt"


def test_preview_values_are_limited_to_fields_the_role_may_see(api):
    preview = api.runtime.importer.preview(
        Principal(user_id="olga", roles=frozenset({"operator"})), "item", "items.csv", CSV)
    viewer = Session(Principal(user_id="vera", roles=frozenset({"viewer"})), False,
                     api.runtime.clock())
    rows = _visible_values(viewer, api.runtime, "item", preview)
    assert all(not {"unit_cost"} & set(r.values) for r in rows)
    assert all("sku" in r.values for r in rows)


def test_import_needs_a_token(api):
    assert api.post("/api/entities/item/import/preview",
                    files={"file": ("a.csv", CSV)}).status_code == 401
    assert api.post("/api/entities/item/import/commit",
                    json={"digest": "0" * 64}).status_code == 401
