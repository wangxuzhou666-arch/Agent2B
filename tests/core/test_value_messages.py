"""Value problems read as sentences in both reply languages, never as a Python list."""

import pytest

from twotrack.core import FieldSpec, FieldType, resolve_entity
from twotrack.core.entities import ValueProblem, check_record, coerce_value, validate_record
from twotrack.core.i18n import join_or, language_from_header
from twotrack.plugins.supply_chain.entities import SKU

CRITICALITY = FieldSpec(id="criticality", label="Criticality", type=FieldType.ENUM,
                        enum_values=("A", "B", "C"))


def test_a_list_in_a_sentence():
    assert [join_or(["A"]), join_or(["A", "B"]), join_or(["A", "B", "C"])] == [
        "A", "A or B", "A, B or C"]
    assert join_or(["A", "B", "C"], "zh") == "A、B 或 C"


def test_an_enum_problem_names_the_choices_naturally():
    with pytest.raises(ValueProblem) as caught:
        coerce_value(CRITICALITY, "D")
    assert str(caught.value) == "must be one of A, B or C"  # a plain ValueError text too
    assert caught.value.text("zh") == "必须是 A、B 或 C 之一"


def test_record_problems_carry_the_field_and_its_name_in_the_language():
    sku = resolve_entity(SKU, None)
    record = {"sku_id": "SKU-1", "name": "Bolt", "criticality": "D", "unit_cost": "cheap"}
    _, problems = check_record(sku, record, "zh")
    assert [(p.field_id, p.label, p.message) for p in problems] == [
        ("unit_cost", "单位成本", "必须是数字"),
        ("criticality", "重要等级", "必须是 A、B 或 C 之一")]
    _, errors = validate_record(sku, record)  # English by default, keyed by field
    assert errors == {"unit_cost": "Unit Cost: must be a number",
                      "criticality": "Criticality: must be one of A, B or C"}


@pytest.mark.parametrize(("header", "language"), [
    ("zh-CN,zh;q=0.9,en;q=0.8", "zh"),
    ("en-US,en;q=0.9,zh-CN;q=0.8", "en"),
    ("fr-FR,zh;q=0.5", "zh"),  # the first supported one
    ("*", None),
    (None, None),
])
def test_the_language_an_accept_language_header_asks_for(header, language):
    assert language_from_header(header) == language
