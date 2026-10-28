"""Spare parts: an item category that extends ``sku`` with two attrs and no new columns."""

from twotrack.core import EntitySpec, FieldSpec, Plugin

SPARE_PART = EntitySpec(
    id="spare_part",
    label={"en": "Spare Part", "zh": "备件"},
    description={"en": "A maintenance spare for a machine model.", "zh": "某个机型的维修备件。"},
    extends="sku",
    # how a question names the category, besides its id and its label in each language (the
    # label is also how the console and the scope guard show it)
    synonyms={"en": ("spare parts", "maintenance parts"), "zh": ("备件", "备品备件")},
    attrs=(
        FieldSpec(id="machine_model", label={"en": "Machine Model", "zh": "适用机型"},
                  required=True,
                  description={"en": "Machine model the part fits.", "zh": "该备件适用的机型。"},
                  aliases=("machine", "equipment model", "适用机型")),
        FieldSpec(id="interchangeable_with",
                  label={"en": "Interchangeable With", "zh": "替代料"},
                  description={"en": "SKU of a part that can replace this one.",
                               "zh": "可以替代本备件的物料编码。"},
                  aliases=("alternate sku", "substitute", "替代料")),
    ),
)

PLUGIN = Plugin(name="spare_parts", version="0.1.0", entities=(SPARE_PART,),
                description="Spare-part item category.")
