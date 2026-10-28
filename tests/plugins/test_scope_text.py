"""A provenance scope names its filters in plain words of the request language, with the labels
the registry already has (core.scope.describe_filters); codes and ids stay as they are."""

import dataclasses

from world import OPERATOR

from twotrack.core import describe_filters
from twotrack.core.narration import result_view, template_narration


def test_filters_read_as_labels_in_both_languages(small):
    values = {"category": "packaging", "warehouse": "WH-EAST", "sku_id": "SKU-2"}
    assert describe_filters(small.registry, "inventory_lookup", values, "en") == \
        "Category: Packaging, Warehouse: WH-EAST, SKU: SKU-2"
    assert describe_filters(small.registry, "inventory_lookup", values, "zh") == \
        "类别：包装材料，仓库：WH-EAST，物料编码：SKU-2"


def test_a_plain_word_value_takes_its_label_and_a_code_stays_a_code(small):
    orders = {"status": "open", "po_id": "PO-6", "supplier_id": None}
    assert describe_filters(small.registry, "po_status", orders, "en") == \
        "Status: open, PO ID: PO-6"
    assert describe_filters(small.registry, "po_status", orders, "zh") == \
        "状态：已下达，采购单号：PO-6"
    plan = {"criticality": "A", "category": "spare_part"}
    assert describe_filters(small.registry, "reorder_plan", plan, "zh") == "重要等级：A，类别：备件"


def test_without_a_registry_keys_are_humanized_and_nothing_is_set_reads_empty(small):
    assert describe_filters(None, "low_stock", {"category": "packaging"}, "en") == \
        "Category: packaging"
    assert describe_filters(small.registry, "low_stock", {"category": None}, "en") == ""


def test_the_pipeline_hands_targets_the_registry_and_the_answer_reads_naturally(small):
    bare = dataclasses.replace(small.ctx(OPERATOR, language="zh"), registry=None)
    outcome = small.pipeline.run(bare, "low_stock", {"category": "packaging"})
    assert outcome.status == "ok"
    assert outcome.result.provenance.scope == "类别：包装材料"
    narration = template_narration(result_view(outcome.result, language="zh"))
    assert narration.startswith("共 1 条记录（类别：包装材料）。")
    english = small.pipeline.run(dataclasses.replace(small.ctx(OPERATOR), registry=None),
                                 "late_shipments", {"category": "spare_part"})
    assert english.result.provenance.scope == "Category: Spare Part"
    assert "=" not in english.result.provenance.scope
