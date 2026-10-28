"""Packaging materials: an item category that extends ``sku`` with three attrs."""

from twotrack.core import EntitySpec, FieldSpec, FieldType, Plugin

MATERIALS = ("cardboard", "plastic", "paper", "metal")

PACKAGING = EntitySpec(
    id="packaging",
    label={"en": "Packaging", "zh": "包装材料"},
    description={"en": "A packaging material or consumable.", "zh": "包装材料或耗材。"},
    extends="sku",
    # how a question names the category, besides its id and its label in each language (the
    # label is also how the console and the scope guard show it)
    synonyms={"en": ("packaging materials", "packing materials"),
              "zh": ("包装材料", "包装", "包材")},
    attrs=(
        FieldSpec(id="material", label={"en": "Material", "zh": "材质"}, required=True,
                  description={"en": "Main material the packaging is made of.",
                               "zh": "包装的主要材质。"},
                  type=FieldType.ENUM, enum_values=MATERIALS, aliases=("材质",)),
        FieldSpec(id="dimensions_mm", label={"en": "Dimensions (mm)", "zh": "尺寸（mm）"},
                  description={"en": "Size of the packaging in millimetres.",
                               "zh": "包装的尺寸，单位毫米。"},
                  aliases=("dimensions", "size", "尺寸")),
        FieldSpec(id="recyclable", label={"en": "Recyclable", "zh": "可回收"},
                  description={"en": "Whether the packaging can be recycled.",
                               "zh": "该包装是否可以回收。"},
                  type=FieldType.BOOL, aliases=("可回收",)),
    ),
)

PLUGIN = Plugin(name="packaging", version="0.1.0", entities=(PACKAGING,),
                description="Packaging item category.")
