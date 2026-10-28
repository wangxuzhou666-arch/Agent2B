"""REST endpoints word their labels and messages in the language ``Accept-Language`` asks for
(``zh*`` is Chinese, anything else English); the query endpoint follows its question instead.

The other server tests use a fixture plugin whose labels are plain strings. These run on the
built-in plugins and the cold-chain example, which declare both languages.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from tests.fakes import NOW
from tests.server.conftest import PASSWORD, Api
from twotrack.core import load_registry
from twotrack.core.params import target_title
from twotrack.core.text import has_cjk
from twotrack.providers import MockProvider
from twotrack.runtime import build_runtime
from twotrack.seed import seed_database
from twotrack.server.app import create_app
from twotrack.settings import Settings

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"
ZH = ["zh", "zh-CN", "zh-CN,zh;q=0.9,en;q=0.8", "ZH-cn", "zh-Hant-TW"]
EN = [None, "", "en", "en-US,en;q=0.9", "fr-FR,fr;q=0.9", "de", "*"]
SEED_SCALE = 0.2  # a small, quick data set: these tests read labels, not figures
# a create_po confirmation for SKU-10001 x 5 in each language: the tool's description in that
# language, then its params (a Chinese one reads entirely in Chinese)
EN_PARAMS = " (SKU: SKU-10001; Quantity: 5; Supplier: best-ranked for this SKU)"
ZH_PARAMS = "（物料编码：SKU-10001；数量：5；供应商：该物料排名最高的供应商）"
EN_SUMMARY = ("Create a draft purchase order for a SKU. Without a supplier it uses the "
              "best-ranked supplier of that SKU." + EN_PARAMS)
ZH_SUMMARY = ("为某个物料新建一张草稿采购单。未指定供应商时，选用该物料排名最高的供应商。"
              + ZH_PARAMS)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def build(path, clock=None):
    registry = load_registry(extra_paths=[EXAMPLES])
    runtime = build_runtime(Settings(data_dir=path), registry=registry, provider=MockProvider(),
                            clock=clock)
    for name, role in (("olga", "operator"), ("omar", "operator"), ("ada", "admin"),
                       ("vera", "viewer")):
        runtime.users.add(name, PASSWORD, [role])
    seed_database(runtime.store, registry, scale=SEED_SCALE)
    return runtime


@pytest.fixture
def builtin(tmp_path, clock):
    runtime = build(tmp_path / "builtin", clock)
    yield Api(runtime)
    runtime.close()


@pytest.fixture(scope="module")
def readonly(tmp_path_factory):
    """The same server, built once for the tests that only read."""
    runtime = build(tmp_path_factory.mktemp("readonly") / "data")
    yield Api(runtime)
    runtime.close()


def headers(api, user, language=None):
    return {**api.headers(user), **({} if language is None else {"Accept-Language": language})}


def get(api, path, user="olga", language=None):
    return api.client.get(path, headers=headers(api, user, language))


def post(api, path, user="olga", language=None, **kw):
    return api.client.post(path, headers=headers(api, user, language), **kw)


def sku_id(api):
    return api.runtime.store.list("sku", sort="sku_id", limit=1).rows[0]["sku_id"]


# ---------------------------------------------------------------- entities


def sku_entity(api, language=None, user="olga"):
    items = get(api, "/api/entities", user, language).json()["items"]
    return next(e for e in items if e["id"] == "sku")


@pytest.mark.parametrize("language", ZH)
def test_entities_are_labelled_and_described_in_chinese(readonly, language):
    sku = sku_entity(readonly, language)
    assert (sku["label"], sku["description"]) == (
        "物料（SKU）", "库存物料。备件、包装材料等类别都继承自它。")
    fields = {f["id"]: f for f in sku["fields"]}
    assert (fields["unit_cost"]["label"], fields["unit_cost"]["description"]) == (
        "单位成本", "每单位的标准成本。")
    lead = fields["lead_time_days"]
    assert (lead["label"], lead["unit"]) == ("交货周期", "days")  # the unit stays as declared
    categories = {c["id"]: c for c in sku["categories"]}
    assert set(categories) == {"packaging", "spare_part", "cold_chain_item"}
    packaging = categories["packaging"]
    assert (packaging["label"], packaging["description"]) == ("包装材料", "包装材料或耗材。")
    assert {a["id"]: a["label"] for a in packaging["attrs"]}["dimensions_mm"] == "尺寸（mm）"
    # inherited fields keep their attr lists: the category's attrs are its parent's plus its own
    assert [a["id"] for a in categories["spare_part"]["attrs"]] == [
        "machine_model", "interchangeable_with"]
    assert categories["cold_chain_item"]["label"] == "冷链物料"


@pytest.mark.parametrize("language", EN)
def test_anything_but_chinese_gets_english(readonly, language):
    sku = sku_entity(readonly, language)
    assert (sku["label"], sku["description"]) == (
        "SKU", "A stocked item. Categories such as spare parts and packaging extend it.")
    fields = {f["id"]: f for f in sku["fields"]}
    assert (fields["unit_cost"]["label"], fields["unit_cost"]["description"]) == (
        "Unit Cost", "Standard cost of one unit.")
    labels = {c["id"]: c["label"] for c in sku["categories"]}
    assert labels == {"packaging": "Packaging", "spare_part": "Spare Part",
                      "cold_chain_item": "Cold-Chain Item"}


def test_every_entity_and_field_the_api_returns_has_text_in_both_languages(readonly):
    for language in ("zh", "en"):
        for entity in get(readonly, "/api/entities", "ada", language).json()["items"]:
            texts = [entity, *entity["categories"]]
            for item in [*entity["fields"], *entity["attrs"],
                         *(a for c in entity["categories"] for a in c["attrs"])]:
                assert item["label"] and item["description"], (language, item["id"])
            assert all(t["label"] and t["description"] for t in texts), language


def test_a_plugin_that_declares_plain_strings_reads_the_same_in_both_languages(api):
    for language in ("zh", "en"):
        items = get(api, "/api/entities", "olga", language).json()["items"]
        item = next(e for e in items if e["id"] == "item")
        assert item["label"] == "Item"
        assert {f["id"]: f["label"] for f in item["fields"]}["qty"] == "Quantity"
        assert item["categories"][0]["description"] == "Threaded fasteners"
        assert item["fields"][0]["description"] == ""  # none declared: empty, never missing


def test_records_carry_ids_and_values_never_labels(readonly):
    for language in ("zh", "en"):
        page = get(readonly, "/api/entities/packaging/records", "olga", language).json()
        assert page["items"] and page["items"][0]["_entity"] == "packaging"
    assert get(readonly, "/api/entities/packaging/records", "olga", "zh").json() == \
        get(readonly, "/api/entities/packaging/records", "olga", "en").json()


# ---------------------------------------------------------------- the query endpoint


def ask(api, query, language=None, user="olga", **body):
    return post(api, "/api/query?stream=false", user, language,
                json={"query": query, **body}).json()


def test_the_query_endpoint_follows_the_question_not_the_header(builtin):
    english = ask(builtin, "which SKUs are low on stock", language="zh")
    assert english["provenance"]["tool"] == "low_stock"
    assert english["provenance"]["label"] == "Low stock"
    assert not has_cjk(english["answer"]) and "rows matched" in english["answer"]
    chinese = ask(builtin, "哪些物料库存不足", language="en")
    assert chinese["provenance"]["tool"] == "low_stock"
    assert chinese["provenance"]["label"] == "低库存"
    assert has_cjk(chinese["answer"])


def test_the_provenance_keeps_the_tool_id_and_adds_its_title(builtin):
    for query, tool, title in (("which shipments are late", "late_shipments", "Late shipments"),
                               ("哪些采购单逾期了", "late_shipments", "逾期采购单")):
        provenance = ask(builtin, query)["provenance"]
        assert (provenance["tool"], provenance["label"]) == (tool, title)
    reorder = ask(builtin, "x", explicit_target="reorder_plan")
    assert reorder["provenance"]["tool"] == "reorder_plan"
    assert reorder["provenance"]["label"] == "Reorder plan"  # a workflow too
    cold = ask(builtin, "x", explicit_target="expiring_soon")["provenance"]
    assert cold["label"] == "Expiring soon"  # the example plugin's own title


def test_a_clarification_labels_every_option_in_the_question_language(builtin):
    """A scope clarification keeps the labels it sets ("Show all categories"); every other
    option is named by its tool's title, where it used to have no label at all. Every option
    is described in the question's language too."""
    registry = builtin.runtime.registry
    for query, language, unfiltered in (
            ("compare suppliers for packaging", "en", "Show all categories"),
            ("对比包装材料的供应商", "zh", "显示全部类别")):
        body = ask(builtin, query)
        options = body["clarify_options"]
        assert options and options[0]["unfiltered"] is True, query
        assert options[0]["label"] == unfiltered
        for option in options[1:]:
            title = target_title(registry.target(option["name"]), language)
            assert option["label"] == title, (query, option["name"])
        assert all(o["label"] for o in options)
        for option in options:
            described = registry.target(option["name"]).description_in(language)
            assert option["description"] == described, (query, option["name"])
            assert has_cjk(option["description"]) is (language == "zh"), option


# ---------------------------------------------------------------- approvals


def propose(api, query="x", user="olga", qty=5):
    body = ask(api, query, user=user, explicit_target="create_po",
               explicit_params={"sku_id": sku_id(api), "qty": qty})
    assert body["approval"], body
    return body["approval"]


def test_a_confirmation_is_titled_and_timed_by_the_server(builtin, clock):
    ref = propose(builtin)
    assert ref["label"] == "Create purchase order"
    assert ref["summary"] == EN_SUMMARY
    # the server counts the seconds left: 15 minutes, whatever time the browser thinks it is
    assert ref["expires_in_s"] == 900.0
    assert ref["expires_at"].startswith("2026-01-15T09:15:00")
    zh = propose(builtin, "新建采购单")
    assert zh["label"] == "新建采购单"
    assert zh["summary"] == ZH_SUMMARY
    assert zh["expires_in_s"] == 900.0
    clock.now = NOW + timedelta(minutes=5, seconds=30)
    listed = get(builtin, "/api/approvals").json()["items"]
    assert [i["expires_in_s"] for i in listed] == [570.0, 570.0]  # 9 minutes 30 s to go


def test_the_list_words_a_confirmation_for_the_reader_not_the_asker(builtin):
    propose(builtin)  # asked in English
    propose(builtin, "新建采购单")  # asked in Chinese
    for language, label, summary in (
            ("zh", "新建采购单", ZH_SUMMARY), ("zh-CN,zh;q=0.9", "新建采购单", ZH_SUMMARY),
            ("en", "Create purchase order", EN_SUMMARY),
            (None, "Create purchase order", EN_SUMMARY)):
        items = get(builtin, "/api/approvals", "olga", language).json()["items"]
        assert [i["label"] for i in items] == [label, label], language
        assert [i["summary"] for i in items] == [summary, summary], language
        assert all(i["target"] == "create_po" and i["kind"] == "query" for i in items)


def test_a_summary_keeps_its_stored_text_when_it_cannot_be_worded_again(builtin):
    propose(builtin)
    with builtin.runtime.database.transaction() as conn:  # the plugin lost the target since
        conn.execute("UPDATE pending_request SET target = 'gone_tool'")
    (item,) = get(builtin, "/api/approvals", "olga", "zh").json()["items"]
    assert item["label"] == "Gone tool"  # humanized: no title to look up
    assert item["summary"].startswith("Create a draft purchase order")  # as it was asked


def test_a_pending_write_planned_by_the_agent_is_titled_from_the_stored_request(tmp_path, clock):
    from tests.fakes import ScriptedProvider
    from twotrack.providers.base import AgentStep, ToolCall

    registry = load_registry(extra_paths=[EXAMPLES])
    call = ToolCall(id="c1", name="create_po",
                    arguments={"sku_id": "SKU-10001", "qty": 4})
    runtime = build_runtime(Settings(data_dir=tmp_path / "agent", allow_open_task=True),
                            registry=registry, clock=clock,
                            provider=ScriptedProvider(steps=[AgentStep(tool_calls=[call])]))
    try:
        runtime.users.add("olga", PASSWORD, ["operator"])
        seed_database(runtime.store, registry, scale=SEED_SCALE)
        api = Api(runtime)
        body = ask(api, "zzz qqq unmatched gibberish")
        assert body["decision"]["kind"] == "open_task" and body["approval"]
        assert body["approval"]["label"] == "Create purchase order"
    finally:
        runtime.close()


# ---------------------------------------------------------------- import

INVENTORY = (b"Record ID,SKU,Warehouse,On Hand,Reserved\n"
             b"INV-X1,SKU-10001,WH-EAST,12,3\n"
             b"INV-X2,SKU-10001,WH-WEST,abc,1\n")


def upload(api, sheet=INVENTORY, name="inv.csv", entity="inventory", language=None, user="olga"):
    return api.client.post(f"/api/entities/{entity}/import/preview",
                           headers=headers(api, user, language),
                           files={"file": (name, sheet, "text/csv")})


def test_a_preview_labels_its_columns_and_problems_and_the_confirmation(builtin):
    zh = upload(builtin, language="zh-CN,zh;q=0.9").json()
    assert [(m["header"], m["field_id"], m["label"]) for m in zh["mapping"]] == [
        ("Record ID", "id", "记录编号"), ("SKU", "sku_id", "物料编码"),
        ("Warehouse", "warehouse", "仓库"), ("On Hand", "on_hand", "在库数量"),
        ("Reserved", "reserved", "预留数量")]
    assert zh["rows"][1]["errors"] == ["在库数量：必须是数字"]
    assert zh["rows"][1]["field_errors"] == [{"field_id": "on_hand", "message": "必须是数字"}]
    assert zh["grant"]["label"] == "导入库存"
    assert zh["grant"]["summary"] == "将 1 行导入“库存”，来源文件：inv.csv"
    assert zh["grant"]["expires_in_s"] == 900.0
    en = upload(builtin).json()
    assert [m["label"] for m in en["mapping"]] == [
        "Record ID", "SKU", "Warehouse", "On Hand", "Reserved"]
    assert en["rows"][1]["errors"] == ["On Hand: must be a number"]
    assert en["grant"]["label"] == "Import Inventory"
    assert en["grant"]["summary"] == "Import 1 row(s) into Inventory from inv.csv"
    assert en["digest"] == zh["digest"]  # the language never changes what is valid


def test_a_chinese_sheet_previews_by_its_chinese_headers(builtin):
    sheet = ("记录编号,物料编码,仓库,在库数量\nINV-X3,SKU-10001,WH-EAST,7\n").encode()
    preview = upload(builtin, sheet, language="zh").json()
    assert preview["valid_count"] == 1
    assert [m["field_id"] for m in preview["mapping"]] == ["id", "sku_id", "warehouse", "on_hand"]
    assert [m["label"] for m in preview["mapping"]] == ["记录编号", "物料编码", "仓库", "在库数量"]


@pytest.mark.parametrize(("sheet", "name", "code", "en", "zh"), [
    (b"Record ID,SKU\nINV-1,SKU-10001\n", "a.csv", "missing_columns",
     "required columns not found: Warehouse", "缺少必需的列：仓库"),
    (b"Record ID,SKU,Item Code,Warehouse\nINV-1,A,B,WH-EAST\n", "b.csv", "header_collision",
     "several columns map to the same field: “SKU” and “Item Code” map to SKU (sku_id). "
     "Rename or remove the extra columns.",
     "多个列对应到了同一个字段：“SKU” 和 “Item Code”都对应到“物料编码”（sku_id）。"
     "请重命名或删除多余的列。"),
    (b"", "c.csv", "empty", "the file is empty", "文件是空的"),
    (b"%PDF-1.7\n", "d.csv", "file_type", "this looks like a PDF, not a CSV or XLSX",
     "这看起来是“PDF 文件”，不是 CSV 或 XLSX"),
    (INVENTORY, "e.xlsx", "file_type",
     "the file is named .xlsx but its content is not an Excel workbook; save it as a real "
     ".xlsx or as a CSV",
     "文件名是 .xlsx，但内容不是 Excel 工作簿；请另存为真正的 .xlsx 或 CSV"),
])
def test_a_refused_file_says_why_in_the_requested_language(builtin, sheet, name, code, en, zh):
    for language, message in (("zh", zh), ("zh-CN,zh;q=0.9", zh), ("en", en), (None, en),
                              ("fr", en)):
        response = upload(builtin, sheet, name, language=language)
        assert response.status_code == 400, language
        assert response.json()["error"] == {"code": code, "message": message}, language


def test_list_syntax_never_shows_a_python_list(builtin):
    sheet = b"Record ID\nINV-1\n"  # SKU and Warehouse are both missing
    en = upload(builtin, sheet).json()["error"]["message"]
    zh = upload(builtin, sheet, language="zh").json()["error"]["message"]
    assert en == "required columns not found: SKU and Warehouse"
    assert zh == "缺少必需的列：物料编码 和 仓库"


def test_a_staged_upload_is_worded_again_for_whoever_reads_the_approval(builtin, clock):
    preview = upload(builtin, language="zh").json()  # staged and worded in Chinese
    commit = post(builtin, "/api/entities/inventory/import/commit", language="en",
                  json={"digest": preview["digest"]}).json()
    assert commit["status"] == "needs_confirmation"
    assert commit["approval"]["label"] == "Import Inventory"
    assert commit["approval"]["summary"] == "Import 1 row(s) into Inventory from inv.csv"
    clock.now = NOW + timedelta(minutes=1)
    for language, label, summary in (
            ("zh", "导入库存", "将 1 行导入“库存”，来源文件：inv.csv"),
            ("en", "Import Inventory", "Import 1 row(s) into Inventory from inv.csv")):
        (item,) = get(builtin, "/api/approvals", "olga", language).json()["items"]
        assert (item["kind"], item["target"]) == ("import", "import:inventory")
        assert (item["label"], item["summary"]) == (label, summary)
        assert item["expires_in_s"] == 840.0
    done = post(builtin, f"/api/approvals/{commit['approval']['grant_id']}/approve",
                language="zh").json()
    assert done["status"] == "ok" and done["answer"] == "已导入 1 行，跳过 1 行。"


def test_the_outcome_of_an_import_is_worded_in_the_requested_language(builtin):
    for language, answer in ((None, "Imported 1 row(s); 1 skipped."),
                             ("zh", "已导入 1 行，跳过 1 行。")):
        preview = upload(builtin, language=language).json()
        waiting = post(builtin, "/api/entities/inventory/import/commit", language=language,
                       json={"digest": preview["digest"]}).json()
        done = post(builtin, f"/api/approvals/{waiting['approval']['grant_id']}/approve",
                    language=language).json()
        assert (done["status"], done["answer"]) == ("ok", answer)


def test_an_approved_question_is_narrated_in_the_language_it_was_asked_in(builtin):
    ref = propose(builtin, "新建采购单")
    done = post(builtin, f"/api/approvals/{ref['grant_id']}/approve", language="en").json()
    assert done["status"] == "ok"
    assert done["provenance"]["label"] == "新建采购单"
    assert done["provenance"]["scope"].startswith("新建草稿")
    ref = propose(builtin, "create it")
    done = post(builtin, f"/api/approvals/{ref['grant_id']}/approve", language="zh").json()
    assert done["provenance"]["label"] == "Create purchase order"


# ---------------------------------------------------------------- the rule is documented


def operations():
    return create_app(None).openapi()


LOCALIZED = [
    ("get", "/api/entities"), ("get", "/api/approvals"),
    ("post", "/api/approvals/{grant_id}/approve"),
    ("post", "/api/entities/{entity_id}/import/preview"),
    ("post", "/api/entities/{entity_id}/import/commit"),
]


@pytest.mark.parametrize(("method", "path"), LOCALIZED)
def test_every_operation_that_words_something_states_the_language_rule(method, path):
    description = operations()["paths"][path][method]["description"]
    assert "Accept-Language" in description, path
    assert "zh" in description and "English" in description, path


def test_the_query_operation_says_it_follows_the_question():
    description = operations()["paths"]["/api/query"]["post"]["description"]
    assert "not from `Accept-Language`" in " ".join(description.split())


def test_the_api_description_states_the_rule_once_for_everything():
    description = operations()["info"]["description"]
    assert "Accept-Language" in description and "language of the question" in description
