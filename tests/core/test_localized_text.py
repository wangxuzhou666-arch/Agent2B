"""Labels and descriptions of entities and fields come in several languages, declared the way a
tool's ``title`` is: a plain string for every language, or ``{"en": ..., "zh": ...}``.

``label`` and ``description`` keep holding the English text (code that reads them is not
affected); ``labels`` and ``descriptions`` hold every language, and ``label_in`` and
``description_in`` pick one."""

import pytest
from pydantic import ValidationError

from twotrack.core import (
    EntitySpec,
    FieldSpec,
    Plugin,
    RegistryError,
    build_registry,
    load_registry,
    resolve_entity,
)
from twotrack.core.entities import check_record


def test_a_plain_string_is_the_text_of_every_language():
    field = FieldSpec(id="qty", label="Quantity")
    assert field.label == "Quantity"
    assert field.labels == {"en": "Quantity", "zh": "Quantity"}
    assert [field.label_in(lang) for lang in ("en", "zh", None, "fr")] == ["Quantity"] * 4
    assert (field.description, field.descriptions) == ("", {})
    assert field.description_in("zh") == ""
    described = FieldSpec(id="qty", label="Quantity", description="Units ordered.")
    assert described.description == "Units ordered."
    assert described.description_in("zh") == "Units ordered."


def test_a_table_gives_each_language_its_own_text_and_english_stands_in_for_the_rest():
    field = FieldSpec(id="qty", label={"en": "Quantity", "zh": "数量"},
                      description={"en": "Units ordered."})
    assert field.label == "Quantity" and field.labels == {"en": "Quantity", "zh": "数量"}
    assert (field.label_in("en"), field.label_in("zh")) == ("Quantity", "数量")
    assert field.label_in("fr") == "Quantity" and field.label_in(None) == "Quantity"
    assert field.description == "Units ordered."
    assert field.description_in("zh") == "Units ordered."  # untranslated: the English text
    only_english = FieldSpec(id="qty", label={"en": "Quantity"})
    assert only_english.label_in("zh") == "Quantity"


def test_surrounding_space_is_dropped_from_a_plain_string():
    field = FieldSpec(id="qty", label="  Quantity ")
    assert (field.label, field.labels["zh"]) == ("Quantity", "Quantity")


def test_an_entity_keeps_reading_as_before_and_resolves_with_the_table():
    spec = EntitySpec(id="packaging", label={"en": "Packaging", "zh": "包装材料"},
                      description={"en": "A packaging material.", "zh": "包装材料。"},
                      primary_key="code", fields=(FieldSpec(id="code", label="Code"),))
    assert (spec.label, spec.description) == ("Packaging", "A packaging material.")
    entity = resolve_entity(spec, None)
    # code that does not care about the language keeps reading plain strings
    assert (entity.label, entity.description) == ("Packaging", "A packaging material.")
    assert entity.labels == {"en": "Packaging", "zh": "包装材料"}
    assert (entity.label_in("zh"), entity.description_in("zh")) == ("包装材料", "包装材料。")
    assert entity.label_in("fr") == "Packaging"
    bare = resolve_entity(EntitySpec(id="thing", label="Thing", primary_key="code",
                                     fields=(FieldSpec(id="code", label="Code"),)), None)
    assert bare.description == "" and bare.description_in("zh") == ""
    assert bare.label_in("zh") == "Thing"


def test_a_declaration_survives_a_round_trip_through_json():
    spec = EntitySpec(id="thing", label={"en": "Thing", "zh": "东西"}, description="A thing.",
                      primary_key="code", fields=(FieldSpec(
                          id="code", label={"en": "Code", "zh": "编码"}),))
    back = EntitySpec.model_validate_json(spec.model_dump_json())
    assert back == spec and back.fields[0].label_in("zh") == "编码"
    assert EntitySpec.model_validate(spec.model_dump()) == spec


@pytest.mark.parametrize(("label", "problem"), [
    ({"en": "Quantity", "fr": "Quantité"}, "language 'fr' is not supported"),
    ({"en": "Quantity", "zh": "  "}, "label for 'zh' is empty"),
    ({"en": "  ", "zh": "数量"}, "label for 'en' is empty"),
    ({"zh": "数量"}, "label needs a 'en' text"),
    ("", "label needs a 'en' text"),
    ("   ", "label needs a 'en' text"),
    ({}, "label needs a 'en' text"),
])
def test_a_bad_label_is_refused_and_says_which_field(label, problem):
    with pytest.raises(ValidationError, match=problem) as caught:
        FieldSpec(id="qty", label=label)
    assert "field 'qty'" in str(caught.value)


@pytest.mark.parametrize(("description", "problem"), [
    ({"zh": "订购的数量。"}, "description needs a 'en' text"),
    ({"en": "Units.", "de": "Einheiten."}, "language 'de' is not supported"),
    ({"en": ""}, "description for 'en' is empty"),
])
def test_a_description_may_be_absent_but_not_broken(description, problem):
    with pytest.raises(ValidationError, match=problem):
        FieldSpec(id="qty", label="Quantity", description=description)
    with pytest.raises(ValidationError, match=problem) as caught:
        EntitySpec(id="thing", label="Thing", description=description)
    assert "entity 'thing'" in str(caught.value)


def test_an_entity_label_is_checked_like_a_field_label():
    for bad in ({"zh": "东西"}, {"en": "Thing", "fr": "Chose"}, ""):
        with pytest.raises(ValidationError, match="entity 'thing'"):
            EntitySpec(id="thing", label=bad)


def test_a_label_that_is_not_text_is_refused():
    for bad in ({"en": 3}, 3, None, ["Quantity"]):
        with pytest.raises(ValidationError):
            FieldSpec(id="qty", label=bad)
    with pytest.raises(ValidationError):  # and a label is not optional
        FieldSpec(id="qty")


def test_a_table_given_beside_the_label_must_still_have_english():
    with pytest.raises(ValidationError, match="label needs a 'en' text"):
        FieldSpec(id="qty", label="Quantity", labels={"zh": "数量"})


def test_a_bad_declaration_stops_the_plugin_load_with_the_reason(tmp_path):
    (tmp_path / "french.py").write_text(
        "from twotrack.core import EntitySpec, FieldSpec, Plugin\n"
        "PLUGIN = Plugin(name='french', version='0.1.0', entities=(EntitySpec(\n"
        "    id='thing', label='Thing', primary_key='code',\n"
        "    fields=(FieldSpec(id='code', label={'en': 'Code', 'fr': 'Code'}),)),))\n",
        encoding="utf-8")
    with pytest.raises(RegistryError) as caught:
        load_registry(module_names=[], extra_paths=[tmp_path])
    assert "failed to import" in str(caught.value)
    assert "language 'fr' is not supported" in str(caught.value)


def thing(**qty) -> EntitySpec:
    return EntitySpec(id="thing", label="Thing", primary_key="code", fields=(
        FieldSpec(id="code", label="Code"), FieldSpec(id="qty", **qty)))


def test_the_label_of_every_language_is_an_import_header():
    entity = resolve_entity(thing(label={"en": "Quantity", "zh": "数量"},
                                  aliases=("qty on hand",)), None)
    assert entity.field("qty").header_texts() == ("qty", "Quantity", "数量", "qty on hand")
    assert entity.match_header("数量").id == "qty" and entity.match_header(" QUANTITY ").id == "qty"
    # a plain string is one header, not two
    plain = resolve_entity(thing(label="Quantity"), None)
    assert plain.field("qty").header_texts() == ("qty", "Quantity")


def test_a_chinese_label_that_collides_with_another_field_is_a_registry_problem():
    clash = EntitySpec(id="thing", label="Thing", primary_key="code", fields=(
        FieldSpec(id="code", label="Code"),
        FieldSpec(id="qty", label={"en": "Quantity", "zh": "数量"}),
        FieldSpec(id="amount", label="Amount", aliases=("数量",))))
    with pytest.raises(RegistryError, match="header alias '数量' of field 'amount' collides"):
        build_registry([Plugin(name="p", version="0.1.0", entities=(clash,))])


def test_problems_name_the_field_in_the_language_asked_for():
    entity = resolve_entity(thing(label={"en": "Quantity", "zh": "数量"}, type="int"), None)
    record = {"code": "T-1", "qty": "many"}
    assert check_record(entity, record)[1][0].text() == "Quantity: must be a number"
    assert check_record(entity, record, "zh")[1][0].text("zh") == "数量：必须是数字"
    assert check_record(entity, record, "fr")[1][0].text("fr") == "Quantity: must be a number"
