"""Business objects of the built-in supply-chain example.

``sku`` is the root of the item family. Categories (spare parts, packaging, and any drop-in
category) extend it in their own plugins and only add attrs, so a new category never adds a
column. Aliases let common spreadsheet headers (English and Chinese) import without a mapping
step.

Every entity and field is labelled and described in English and Chinese (``{"en": ..., "zh":
...}``); the console shows the language it is set to. The Chinese labels are the terms the
tools already use: 物料编码 for a SKU's code, 供应商 for supplier, 采购单 for purchase order,
类别 for category. A label that carries its unit in English ("Lead Time (days)") drops it in
Chinese when the field declares ``unit``, because the console appends the unit itself.

Three enum fields are scope keys (a question can name them as a constraint, see core.scope):
warehouse, PO status and criticality. Their value synonyms are the same words the
deterministic extractor (``extract.py``) reads, so routing and extraction agree.
"""

from twotrack.core import EntitySpec, FieldSpec, FieldType, Role, Sensitivity

WAREHOUSES = ("WH-EAST", "WH-WEST", "WH-CENTRAL")
CRITICALITY = ("A", "B", "C")
PO_STATUSES = ("draft", "open", "shipped", "received", "cancelled")

# value -> language -> other names. The first Chinese name is the value's Chinese display name.
# Chinese names match as substrings, so none may hide inside common words ("中仓" would match
# "其中仓库" and "集中仓储", so central is only 中央仓 / 中心仓).
WAREHOUSE_NAMES = {
    code: {"en": tuple(f"{word} {site}" for site in ("warehouse", "wh", "site", "dc")),
           "zh": names}
    for code, word, names in (
        ("WH-EAST", "east", ("东仓", "华东仓", "东部仓")),
        ("WH-WEST", "west", ("西仓", "华西仓", "西部仓")),
        ("WH-CENTRAL", "central", ("中央仓", "中心仓")))
}
# "A class" / "A-class" are left out on purpose: lowercased they read as the article "a"
CRITICALITY_NAMES = {level: {"en": (f"class {level}", f"criticality {level}"),
                             "zh": (f"{level}类",)} for level in CRITICALITY}
# Status values are plain words, so only these phrases name them (core.scope): the bare words
# "open" and "draft" are as often verbs ("open the report", "draft a plan"), hence "open orders"
# and "draft orders" rather than "open" and "draft". A one-word phrase ("drafts") matches only
# as written; longer ones also match their singular ("draft order").
PO_STATUS_NAMES = {
    "draft": {"en": ("drafts", "draft orders", "draft purchase orders", "draft PO", "draft POs"),
              "zh": ("草稿",)},
    "open": {"en": ("open orders", "open purchase orders", "open PO", "open POs"),
             "zh": ("已下达", "未结", "待发货")},
    "shipped": {"en": ("shipped", "in transit"), "zh": ("已发货", "在途")},
    "received": {"en": ("received", "delivered"), "zh": ("已收货", "已到货")},
    "cancelled": {"en": ("cancelled", "canceled"), "zh": ("已取消",)},
}
# Ordered but not yet received: counts as incoming supply in every stock calculation.
INCOMING_STATUSES = ("draft", "open", "shipped")
# Committed to a supplier and expected on a date: can be late.
LATE_STATUSES = ("open", "shipped")

ADMIN_ONLY = frozenset({Role.ADMIN})

SKU_CODE_ZH = "物料编码"


def _sku_ref(label: str, description: dict[str, str]) -> FieldSpec:
    """The reference to a SKU that inventory and purchase orders carry (and the SKU's own key)."""
    return FieldSpec(id="sku_id", label={"en": label, "zh": SKU_CODE_ZH}, required=True,
                     description=description,
                     aliases=("sku", "sku code", "item code", "item no", "item number",
                              "part number", "物料编码", "物料号"))


SKU = EntitySpec(
    id="sku",
    label={"en": "SKU", "zh": "物料（SKU）"},
    description={"en": "A stocked item. Categories such as spare parts and packaging extend it.",
                 "zh": "库存物料。备件、包装材料等类别都继承自它。"},
    primary_key="sku_id",
    label_field="name",
    fields=(
        _sku_ref("SKU ID", {"en": "Unique code of the item.", "zh": "物料的唯一编码。"}),
        FieldSpec(id="name", label={"en": "Name", "zh": "品名"}, required=True,
                  description={"en": "Display name of the item.", "zh": "物料的显示名称。"},
                  sensitivity=Sensitivity.PUBLIC,
                  aliases=("item name", "description", "品名", "物料名称")),
        FieldSpec(id="unit", label={"en": "Unit", "zh": "单位"},
                  description={"en": "Unit of measure, such as pcs or kg.",
                               "zh": "计量单位，如 pcs、kg。"},
                  aliases=("uom", "unit of measure", "单位")),
        FieldSpec(id="unit_cost", label={"en": "Unit Cost", "zh": "单位成本"},
                  description={"en": "Standard cost of one unit.", "zh": "每单位的标准成本。"},
                  type=FieldType.FLOAT, aliases=("cost", "standard cost", "单位成本")),
        FieldSpec(id="avg_daily_demand", label={"en": "Avg Daily Demand", "zh": "日均需求"},
                  description={"en": "Average number of units used per day.",
                               "zh": "平均每天消耗的数量。"},
                  type=FieldType.FLOAT, unit="units/day",
                  aliases=("daily demand", "avg demand", "日均需求")),
        FieldSpec(id="reorder_point", label={"en": "Reorder Point", "zh": "再订货点"},
                  description={"en": "Stock level below which the item needs reordering.",
                               "zh": "库存低于该数量时，物料需要补货。"},
                  type=FieldType.INT, aliases=("rop", "reorder level", "再订货点")),
        FieldSpec(id="lead_time_days", label={"en": "Lead Time (days)", "zh": "交货周期"},
                  description={"en": "Days from placing an order to receiving the goods.",
                               "zh": "从下单到收货所需的天数。"},
                  type=FieldType.INT, unit="days",
                  aliases=("lead time", "lead time days", "交货周期", "采购周期")),
        FieldSpec(id="criticality", label={"en": "Criticality", "zh": "重要等级"},
                  description={"en": "ABC class of the item; A is the most critical.",
                               "zh": "物料的 ABC 重要等级，A 类最关键。"},
                  type=FieldType.ENUM, enum_values=CRITICALITY,
                  aliases=("abc class", "abc", "class", "重要等级"),
                  scope_key=True, value_synonyms=CRITICALITY_NAMES),
    ),
)

INVENTORY = EntitySpec(
    id="inventory",
    label={"en": "Inventory", "zh": "库存"},
    description={"en": "On-hand and reserved quantity of one SKU in one warehouse.",
                 "zh": "某个 SKU 在某个仓库的在库数量与预留数量。"},
    primary_key="id",
    label_field="sku_id",
    fields=(
        FieldSpec(id="id", label={"en": "Record ID", "zh": "记录编号"}, required=True,
                  description={"en": "Unique id of the inventory record.",
                               "zh": "库存记录的唯一编号。"},
                  aliases=("inventory id",)),
        _sku_ref("SKU", {"en": "The item this record counts.", "zh": "本记录所统计的物料。"}),
        FieldSpec(id="warehouse", label={"en": "Warehouse", "zh": "仓库"}, required=True,
                  description={"en": "Warehouse that holds the stock.",
                               "zh": "存放这批库存的仓库。"},
                  type=FieldType.ENUM, enum_values=WAREHOUSES,
                  aliases=("wh", "location", "site", "仓库"),
                  scope_key=True, value_synonyms=WAREHOUSE_NAMES),
        FieldSpec(id="on_hand", label={"en": "On Hand", "zh": "在库数量"},
                  description={"en": "Units physically in the warehouse.",
                               "zh": "仓库中实际在库的数量。"},
                  type=FieldType.INT,
                  aliases=("qty on hand", "quantity on hand", "stock", "库存", "现有库存")),
        FieldSpec(id="reserved", label={"en": "Reserved", "zh": "预留数量"},
                  description={"en": "Units set aside for orders and no longer available.",
                               "zh": "已为订单预留、不能再使用的数量。"},
                  type=FieldType.INT, aliases=("allocated", "committed", "预留", "已分配")),
        FieldSpec(id="as_of", label={"en": "As Of", "zh": "盘点日期"},
                  description={"en": "Date of the stock count.", "zh": "盘点库存的日期。"},
                  type=FieldType.DATE, aliases=("snapshot date", "count date", "盘点日期")),
    ),
)

SUPPLIER = EntitySpec(
    id="supplier",
    label={"en": "Supplier", "zh": "供应商"},
    description={"en": "A vendor. Contact details and contract terms are visible to admins only.",
                 "zh": "供应商。联系方式和合同条款仅管理员可见。"},
    primary_key="supplier_id",
    label_field="name",
    fields=(
        FieldSpec(id="supplier_id", label={"en": "Supplier ID", "zh": "供应商编码"},
                  required=True,
                  description={"en": "Unique code of the supplier.", "zh": "供应商的唯一编码。"},
                  aliases=("vendor id", "supplier code", "供应商编码")),
        FieldSpec(id="name", label={"en": "Name", "zh": "供应商名称"}, required=True,
                  description={"en": "Name of the supplier.", "zh": "供应商的名称。"},
                  sensitivity=Sensitivity.PUBLIC,
                  aliases=("supplier name", "vendor name", "供应商名称")),
        FieldSpec(id="region", label={"en": "Region", "zh": "地区"},
                  description={"en": "Region the supplier ships from.",
                               "zh": "供应商所在或发货的地区。"},
                  aliases=("area", "地区")),
        FieldSpec(id="rating", label={"en": "Rating", "zh": "评分"},
                  description={"en": "Overall score of the supplier; higher is better.",
                               "zh": "供应商的综合评分，越高越好。"},
                  type=FieldType.FLOAT, aliases=("score", "supplier rating", "评分")),
        FieldSpec(id="avg_lead_time_days", label={"en": "Avg Lead Time (days)", "zh": "平均交期"},
                  description={"en": "Average days from order to delivery.",
                               "zh": "从下单到交货的平均天数。"},
                  type=FieldType.INT, unit="days",
                  aliases=("avg lead time", "lead time", "平均交期")),
        FieldSpec(id="contact_email", label={"en": "Contact Email", "zh": "联系邮箱"},
                  description={"en": "Address to reach the supplier at (admins only).",
                               "zh": "联系供应商的邮箱地址（仅管理员可见）。"},
                  sensitivity=Sensitivity.RESTRICTED, visible_roles=ADMIN_ONLY,
                  aliases=("email", "contact", "邮箱")),
        FieldSpec(id="contract_discount_pct",
                  label={"en": "Contract Discount %", "zh": "合同折扣"},
                  description={"en": "Discount agreed in the contract, in percent (admins only).",
                               "zh": "合同约定的折扣，单位为百分比（仅管理员可见）。"},
                  type=FieldType.FLOAT, unit="%", sensitivity=Sensitivity.RESTRICTED,
                  visible_roles=ADMIN_ONLY, aliases=("discount", "discount pct", "合同折扣")),
    ),
)

PURCHASE_ORDER = EntitySpec(
    id="purchase_order",
    label={"en": "Purchase Order", "zh": "采购单"},
    description={"en": "An order for one SKU from one supplier.",
                 "zh": "向某个供应商订购某个 SKU 的订单。"},
    primary_key="po_id",
    label_field="po_id",
    fields=(
        FieldSpec(id="po_id", label={"en": "PO ID", "zh": "采购单号"}, required=True,
                  description={"en": "Unique number of the purchase order.",
                               "zh": "采购单的唯一单号。"},
                  aliases=("po", "po number", "po no", "order no", "采购单号")),
        _sku_ref("SKU", {"en": "The item ordered.", "zh": "所订购的物料。"}),
        FieldSpec(id="supplier_id", label={"en": "Supplier ID", "zh": "供应商编码"},
                  required=True,
                  description={"en": "The supplier the order is placed with.",
                               "zh": "接收这张订单的供应商。"},
                  aliases=("supplier", "vendor", "vendor id", "供应商编码")),
        FieldSpec(id="qty", label={"en": "Quantity", "zh": "数量"}, required=True,
                  description={"en": "Units ordered.", "zh": "订购的数量。"},
                  type=FieldType.INT, aliases=("qty", "order qty", "数量")),
        FieldSpec(id="unit_price", label={"en": "Unit Price", "zh": "单价"},
                  description={"en": "Price of one unit on this order.",
                               "zh": "本订单中每单位的价格。"},
                  type=FieldType.FLOAT, aliases=("price", "单价")),
        FieldSpec(id="status", label={"en": "Status", "zh": "状态"}, required=True,
                  description={"en": "Where the order stands: draft, open, shipped, received "
                                     "or cancelled.",
                               "zh": "订单所处的阶段：草稿、已下达、已发货、已收货或已取消。"},
                  type=FieldType.ENUM, enum_values=PO_STATUSES, aliases=("po status", "状态"),
                  scope_key=True, value_synonyms=PO_STATUS_NAMES),
        FieldSpec(id="ordered_at", label={"en": "Ordered At", "zh": "下单日期"},
                  description={"en": "Date the order was placed.", "zh": "下单的日期。"},
                  type=FieldType.DATE, aliases=("order date", "ordered", "下单日期")),
        FieldSpec(id="eta", label={"en": "ETA", "zh": "预计到货日期"},
                  description={"en": "Date the supplier expects to deliver.",
                               "zh": "供应商预计到货的日期。"},
                  type=FieldType.DATE, aliases=("expected arrival", "due date", "预计到货")),
    ),
)

ENTITIES = (SKU, INVENTORY, SUPPLIER, PURCHASE_ORDER)
