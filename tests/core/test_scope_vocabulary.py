"""The constraint vocabulary: built from declarations only, conservative when it matches."""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from twotrack.core import (
    PLUGIN_PATHS_ENV,
    EntitySpec,
    FieldSpec,
    FieldType,
    Plugin,
    RegistryError,
    Tool,
    ToolOutputs,
    build_registry,
    load_registry,
)
from twotrack.core.scope import code_like, plural

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"
CATEGORY = "category:sku"
WAREHOUSE = "field:inventory.warehouse"
STATUS = "field:purchase_order.status"
CRITICALITY = "field:sku.criticality"


@pytest.fixture(scope="module")
def builtin():
    return load_registry(extra_paths=[])


def found(registry, text):
    return [(m.options[0][0], m.options[0][1], m.negated)
            for m in registry.vocabulary.find(text)]


# ---------------------------------------------------------------- build


def test_the_vocabulary_comes_from_categories_and_scope_keys(builtin):
    vocab = builtin.vocabulary
    assert set(vocab.kinds) == {CATEGORY, WAREHOUSE, STATUS, CRITICALITY}
    assert set(vocab.values(CATEGORY)) == {"spare_part", "packaging"}
    assert vocab.values(WAREHOUSE) == ("WH-EAST", "WH-WEST", "WH-CENTRAL")
    assert (vocab.name(CATEGORY, "en"), vocab.name(CATEGORY, "zh")) == ("category", "类别")
    assert (vocab.name(WAREHOUSE, "en"), vocab.name(WAREHOUSE, "zh")) == ("warehouse", "仓库")
    assert vocab.label(CATEGORY, "spare_part", "en") == "Spare Part"
    assert vocab.label(CATEGORY, "spare_part", "zh") == "备件"  # its Chinese label
    assert vocab.label(WAREHOUSE, "WH-EAST", "zh") == "东仓"
    assert vocab.label(CRITICALITY, "A", "en") == "A"


def test_a_drop_in_category_joins_the_vocabulary_without_core_changes(monkeypatch):
    monkeypatch.setenv(PLUGIN_PATHS_ENV, str(EXAMPLES))
    registry = load_registry()
    assert set(registry.vocabulary.values(CATEGORY)) == {"spare_part", "packaging",
                                                         "cold_chain_item"}
    assert found(registry, "refrigerated items running low") == [
        (CATEGORY, "cold_chain_item", False)]
    assert found(registry, "冷链物料还有多少") == [(CATEGORY, "cold_chain_item", False)]
    # the built-in tools bind the category kind, so they filter the new category too
    assert registry.bindings["late_shipments"] == {"category": CATEGORY}
    assert registry.bindings["expiring_soon"] == {}
    assert registry.tool("expiring_soon").implied_scope == {CATEGORY: "cold_chain_item"}


def test_every_builtin_binding_is_declared_where_the_filter_is(builtin):
    assert dict(builtin.bindings["inventory_lookup"]) == {"warehouse": WAREHOUSE,
                                                          "category": CATEGORY}
    assert dict(builtin.bindings["low_stock"]) == {"criticality": CRITICALITY,
                                                   "category": CATEGORY}
    assert dict(builtin.bindings["po_status"]) == {"status": STATUS, "category": CATEGORY}
    assert dict(builtin.bindings["reorder_plan"]) == {"criticality": CRITICALITY,
                                                      "category": CATEGORY}
    assert dict(builtin.bindings["reorder_suggestion"]) == {"category": CATEGORY}
    assert dict(builtin.bindings["supplier_compare"]) == {}
    assert builtin.tool("create_po").implied_scope == {STATUS: "draft"}


# ---------------------------------------------------------------- matching


@pytest.mark.parametrize("text", [
    "a",
    "is it a class or not",  # the article never reads as class A
    "what is on hand for item a",
    "SKU-10042 and PO-20015 from SUP-007",  # identifiers are never mentions
    "open the report",  # a plain-word status needs a declared phrase ("open orders")
    "the an is of",
    "how is the data类 table built",  # a Latin letter glued to a Chinese phrase
    "其中仓库的盘点日期",  # no warehouse name hides inside common words
    "一",
    "draft a reorder plan",  # "draft" the verb is not the PO status ...
    "make a draft plan for the buyer",  # ... nor a draft of something else
    "起草一份补货计划",
])
def test_no_false_mentions(builtin, text):
    assert builtin.vocabulary.find(text) == []


@pytest.mark.parametrize(("text", "expected"), [
    ("overdue orders for maintenance parts", [(CATEGORY, "spare_part", False)]),
    ("Spare-Part stock levels", [(CATEGORY, "spare_part", False)]),
    ("packing materials at the central site", [(CATEGORY, "packaging", False),
                                               (WAREHOUSE, "WH-CENTRAL", False)]),
    ("stock in wh-west", [(WAREHOUSE, "WH-WEST", False)]),
    ("criticality b items", [(CRITICALITY, "B", False)]),
    ("open purchase orders still in transit", [(STATUS, "open", False),
                                               (STATUS, "shipped", False)]),
    ("备品备件的库存", [(CATEGORY, "spare_part", False)]),
    ("华西仓的包材", [(WAREHOUSE, "WH-WEST", False), (CATEGORY, "packaging", False)]),
    ("B 类物料", [(CRITICALITY, "B", False)]),
    ("C类物料", [(CRITICALITY, "C", False)]),
    ("已取消的订单", [(STATUS, "cancelled", False)]),
    ("show draft purchase orders", [(STATUS, "draft", False)]),
    ("raise a draft PO for SKU-10001", [(STATUS, "draft", False)]),
    ("list the drafts", [(STATUS, "draft", False)]),
    ("显示草稿采购单", [(STATUS, "draft", False)]),
])
def test_mentions_in_english_and_chinese(builtin, text, expected):
    assert found(builtin, text) == expected


def _statuses(**synonyms) -> EntitySpec:
    return EntitySpec(id="doc", label="Document", primary_key="code", fields=(
        FieldSpec(id="code", label="Code", required=True),
        FieldSpec(id="state", label="State", type=FieldType.ENUM, enum_values=("draft", "final"),
                  scope_key=True, value_synonyms=synonyms)))


def test_a_one_word_value_synonym_matches_only_as_written():
    """Plural folding would turn "drafts" into "draft", the bare value, which a plain-word
    value never matches; a longer phrase still matches its singular."""
    registry = build_registry([_plugin(_statuses(draft={"en": ("drafts", "draft reports")}))])
    state = "field:doc.state"
    assert found(registry, "draft a memo") == []
    assert found(registry, "show the drafts") == [(state, "draft", False)]
    assert found(registry, "Show the DRAFTS") == [(state, "draft", False)]
    assert found(registry, "the draft report") == [(state, "draft", False)]
    assert found(registry, "the draft reports") == [(state, "draft", False)]


def test_a_category_is_shown_and_named_by_its_label_in_every_language():
    """A category's display name in a language is its label there (not its first synonym),
    and that label names it in a question like its English label does."""
    base = EntitySpec(id="part", label="Part", primary_key="code",
                      fields=(FieldSpec(id="code", label="Code", required=True),))
    cable = EntitySpec(id="cable", label={"en": "Cable", "zh": "线缆"}, extends="part",
                       synonyms={"zh": ("电缆",), "en": ("wires",)})
    registry = build_registry([_plugin(base, cable)])
    vocab = registry.vocabulary
    assert vocab.label("category:part", "cable", "zh") == "线缆"
    assert vocab.label("category:part", "cable", "en") == "Cable"
    for text in ("线缆库存", "电缆库存", "cables in stock", "wires in stock"):
        assert found(registry, text) == [("category:part", "cable", False)], text


def test_the_longest_overlapping_phrase_wins(builtin):
    (mention,) = builtin.vocabulary.find("华东仓的库存")  # "东仓" inside "华东仓" is one mention
    assert (mention.text, mention.options) == ("华东仓", ((WAREHOUSE, "WH-EAST"),))


@pytest.mark.parametrize("text", [
    "everything except packaging",
    "non-packaging items",
    "orders not for the spare parts",
    "stock other than packing materials",
    "除了备件以外的订单",
    "包材以外的物料",
])
def test_excluded_constraints_are_negated(builtin, text):
    (mention,) = builtin.vocabulary.find(text)
    assert mention.negated


def test_canonical_turns_labels_and_synonyms_into_value_ids(builtin):
    vocab = builtin.vocabulary
    assert vocab.canonical(CATEGORY, "Spare Parts") == "spare_part"
    assert vocab.canonical(CATEGORY, "PACKAGING") == "packaging"
    assert vocab.canonical(CATEGORY, "包装材料") == "packaging"
    assert vocab.canonical(WAREHOUSE, "wh-east") == "WH-EAST"
    for unknown in ("widgets", "not packaging", "packaging stock", 7, None):
        assert vocab.canonical(CATEGORY, unknown) is None


def test_code_like_values_and_plurals():
    assert code_like("WH-EAST") and code_like("EOL") and code_like("V2")
    assert not code_like("open") and not code_like("Open")
    assert [plural(w) for w in ("category", "warehouse", "status", "day")] == [
        "categories", "warehouses", "statuses", "days"]


# ---------------------------------------------------------------- fail-closed declarations


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colour: str | None = Field(default=None, json_schema_extra={"binds": "field:item.colour"})


def _item(**colour) -> EntitySpec:
    return EntitySpec(id="item", label="Item", primary_key="code", fields=(
        FieldSpec(id="code", label="Code", required=True),
        FieldSpec(id="colour", label="Colour", type=FieldType.ENUM,
                  enum_values=("RED-1", "BLUE-2"), **colour)))


def _tool(name="pick", model=Pick, **kw) -> Tool:
    return Tool(name=name, description="d", roles=("viewer",), effect="read", approval="none",
                outputs=ToolOutputs(aggregates=(), columns=()), explorable=False,
                params_model=model, handler=lambda ctx, p: None, **kw)


def _plugin(*entities, tools=()) -> Plugin:
    return Plugin(name="p", version="1", entities=entities, tools=tools)


def problems(plugin) -> str:
    with pytest.raises(RegistryError) as caught:
        build_registry([plugin])
    return "\n".join(caught.value.problems)


def test_a_binding_needs_a_scope_key_field():
    assert "not declared with scope_key=True" in problems(_plugin(_item(), tools=(_tool(),)))
    registry = build_registry([_plugin(_item(scope_key=True), tools=(_tool(),))])
    assert registry.bindings["pick"] == {"colour": "field:item.colour"}


@pytest.mark.parametrize(("binds", "needle"), [
    ("category:nothing", "unknown entity 'nothing'"),
    ("field:item", "must be a string"),
    (7, "binds must be"),
    ("field:item.size", "declares no field 'size'"),
])
def test_malformed_bindings_fail_the_load(binds, needle):
    class Bad(BaseModel):
        x: str | None = Field(default=None, json_schema_extra={"binds": binds})

    assert needle in problems(_plugin(_item(scope_key=True), tools=(_tool(model=Bad),)))


def test_two_params_may_not_bind_one_constraint():
    class Twice(Pick):
        hue: str | None = Field(default=None, json_schema_extra={"binds": "field:item.colour"})

    text = problems(_plugin(_item(scope_key=True), tools=(_tool(model=Twice),)))
    assert "both bind field:item.colour" in text


def test_implied_scope_is_checked_against_the_vocabulary():
    entity = _item(scope_key=True)
    assert "has no value 'GREEN'" in problems(_plugin(entity, tools=(
        _tool(model=BaseModel, implied_scope={"field:item.colour": "GREEN"}),)))
    assert "also bound by a param" in problems(_plugin(entity, tools=(
        _tool(implied_scope={"field:item.colour": "RED-1"}),)))


def test_ambiguous_or_unmatchable_phrases_fail_the_load():
    one = EntitySpec(id="kit_a", label="Kit A", extends="item", synonyms={"en": ("kits",)})
    two = EntitySpec(id="kit_b", label="Kit B", extends="item", synonyms={"en": ("kits",)})
    assert "names more than one value of category:item" in problems(_plugin(_item(), one, two))
    short = EntitySpec(id="kit_c", label="Kit C", extends="item", synonyms={"en": ("ab",)})
    assert "can never be matched" in problems(_plugin(_item(), short))


@pytest.mark.parametrize("kwargs", [
    dict(type=FieldType.STR, scope_key=True),
    dict(type=FieldType.ENUM, enum_values=("X-1",), value_synonyms={"X-1": {"en": ("x",)}}),
    dict(type=FieldType.ENUM, enum_values=("X-1",), scope_key=True,
         value_synonyms={"Y-2": {"en": ("why",)}}),
    dict(type=FieldType.ENUM, enum_values=("X-1",), scope_key=True,
         value_synonyms={"X-1": {"fr": ("ex",)}}),
])
def test_field_scope_declarations_are_validated(kwargs):
    with pytest.raises(ValidationError):
        FieldSpec(id="f", label="F", **kwargs)


def test_entity_synonyms_are_validated():
    with pytest.raises(ValidationError):
        EntitySpec(id="kit", label="Kit", extends="item", synonyms={"de": ("Bausatz",)})
    with pytest.raises(ValidationError):
        EntitySpec(id="kit", label="Kit", extends="item", synonyms={"en": ("  ",)})
