"""Dev phrasing set for zero-key routing (mock provider: the rule arm and BM25 decide).

Written for the multi-target rule-hit fix, independently of the benchmark's held-out questions
and of the tools' own routing examples. Every expectation states what the user asked for, not
what the router happened to do; the expectations were fixed before the fix was written (the
later groups, ``MULTI_HIT_ZH_MORE`` and ``VERBS``, before the bilingual descriptions and the
keyword and scope-vocabulary hygiene they test, with the misses of that time as xfails; the
write groups, ``COMPLETE_WRITES``, ``INCOMPLETE_WRITES`` and ``PLANS_BESIDE_WRITES``, before a
write's phrase with every param it requires became strong evidence; the groups from
``QUESTIONS_ABOUT_WRITES`` to ``PLANS_BEFORE_COMPLETE_WRITES``, before questions stopped being
read as complete writes and a clearly won contest could be; the groups from
``PLANS_WITH_COMPLETE_WRITES`` to ``NEGATIONS_OUTSIDE_THE_RULE``, before a won contest lifted a
complete write only when its rivals matched nothing but names of the write's own params, and
before a negated write stopped being lifted; the groups from ``TWO_PHRASE_WRITES`` to
``EXCLUSIONS_OF_NOTHING``, before a question, a negation or an exclusion kept a write weak on
every path, before 不需要 became a negator and 别 one only where it negates, and before an
excluded id stopped being a param; ``LIST_ADDITIONS``, ``HOLDS_NO_RULE_READS`` and
``NEGATIVE_QUESTIONS``, after it, as the known misses it found, before a later routing batch
read new markers and kept an id right after a contraction a param; the misses that batch found,
after it, as known misses too). Many items are multi-hit: two targets' keywords match
different words of one question ("late" vs "purchase orders"), or one target's phrase contains
another's ("create a purchase order" vs "purchase order").

Modes:
* ``run``: routed straight to the target (tool or workflow decision);
* ``approve``: a write the question asks for completely (it names the write and gives every
  param the write requires): routed straight to the write, with no routing confirmation, so the
  pipeline holds it at once for its one-time approval (checked through the graph below);
* ``confirm``: a one-option confirmation of the target (a write reached on weak evidence);
* ``clarify``: a clarification whose options include every listed target (the question asks
  for two different things, so picking one would be a guess);
* ``no_write``: the question names the listed write and every param it requires but does not
  ask for that write alone and now (it asks about an order, for a plan first, for something
  else alongside, or says not to place it): never routed straight to that write. A read, a
  clarification or a one-option confirmation are all fine; through the graph, no approval is
  issued and nothing is written.

A param given as ``None`` must not be extracted (an excluded supplier, say).

Items the router still gets wrong carry ``known``: the reason, as a strict xfail, so the set
keeps measuring them and a change that fixes one has to say so.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from twotrack.core import RouteKind


@dataclass(frozen=True)
class Item:
    query: str
    target: str | tuple[str, ...]
    mode: str = "run"
    params: dict[str, Any] = field(default_factory=dict)
    known: str = ""  # why the router still misses it


def run(query: str, target: str, known: str = "", **params: Any) -> Item:
    return Item(query, target, "run", params, known)


def approve(query: str, target: str, known: str = "", **params: Any) -> Item:
    return Item(query, target, "approve", params, known)


def confirm(query: str, target: str, known: str = "", **params: Any) -> Item:
    return Item(query, target, "confirm", params, known)


def clarify(query: str, *targets: str, known: str = "") -> Item:
    return Item(query, targets, "clarify", known=known)


def no_write(query: str, target: str, known: str = "") -> Item:
    return Item(query, target, "no_write", known=known)


# targets of different reports hit disjoint words of the question
MULTI_HIT_EN = [
    run("late purchase orders", "late_shipments"),
    run("show delayed purchase orders from SUP-003", "late_shipments", supplier_id="SUP-003"),
    run("purchase orders that are overdue", "late_shipments"),
    run("late shipments by supplier", "late_shipments"),
    run("which supplier has the most late deliveries", "late_shipments"),
    run("which suppliers deliver late", "late_shipments"),
    run("open POs from supplier SUP-001", "po_status", supplier_id="SUP-001", status="open"),
    run("purchase order status for supplier SUP-002", "po_status", supplier_id="SUP-002"),
    run("suggested reorder quantity for low stock items", "reorder_suggestion",
        known="contested and inside the margin (reorder_suggestion explains 0.53 of the rest, "
              "low_stock 0.36 through 'reorder' and 'items'): asks between the two"),
    run("how much should we reorder for the shortages", "reorder_suggestion"),
    run("procurement plan with suppliers for low stock items", "reorder_plan"),
    run("inventory of items running low", "low_stock"),
    clarify("low stock and late shipments", "low_stock", "late_shipments"),
]

MULTI_HIT_ZH = [
    run("延迟的采购订单", "late_shipments"),
    run("逾期的采购订单有多少", "late_shipments"),
    run("逾期的采购单涉及哪些供应商", "late_shipments"),
    run("迟到的采购单", "late_shipments"),
    run("缺货的物料应该订多少", "reorder_suggestion"),
    run("低库存物料的补货建议", "reorder_suggestion"),
    clarify("缺货和逾期的情况", "low_stock", "late_shipments"),
]

# one target's phrase contains the other's: the more specific match wins, as before. The two
# writes name create_po by one phrase (it declares no bare verb that would count as a second)
# and give every param it requires, so they go straight to the approval. They were runs while
# "create" and 新建 were keywords of their own, then confirmations while one phrase alone was
# weak evidence for a write.
CONTAINED = [
    approve("please create a purchase order for 75 units of SKU-10205", "create_po",
            sku_id="SKU-10205", qty=75),
    approve("帮我新建采购单，SKU-10099 数量 60", "create_po", sku_id="SKU-10099", qty=60),
    run("make a reorder plan for class A", "reorder_plan", criticality="A"),
]

SINGLE_EN = [
    run("how many units of SKU-10233 are in stock", "inventory_lookup", sku_id="SKU-10233"),
    run("current on hand quantity at WH-CENTRAL", "inventory_lookup", warehouse="WH-CENTRAL"),
    run("stock levels for packaging materials", "inventory_lookup", category="packaging"),
    run("how much stock is left of SKU-10140 in WH-EAST", "inventory_lookup",
        sku_id="SKU-10140", warehouse="WH-EAST"),
    run("show me the shortages in spare parts", "low_stock", category="spare_part"),
    run("what's running low among class A items", "low_stock", criticality="A"),
    run("stock out items in packaging", "low_stock", category="packaging"),
    run("how many units to replenish for SKU-10061", "reorder_suggestion", sku_id="SKU-10061"),
    run("status of purchase order PO-20488", "po_status", po_id="PO-20488"),
    run("list all shipped purchase orders", "po_status", status="shipped"),
    run("which purchase orders from SUP-005 are still open", "po_status",
        supplier_id="SUP-005", status="open"),
    run("overdue POs for spare parts", "late_shipments", category="spare_part"),
    run("which deliveries are behind schedule", "late_shipments"),
    run("late deliveries of packaging", "late_shipments", category="packaging"),
    run("best vendor for SKU-10120", "supplier_compare", sku_id="SKU-10120"),
    run("compare suppliers on lead time", "supplier_compare"),
    confirm("raise a new PO for SKU-10140, 25 pcs", "create_po", sku_id="SKU-10140", qty=25),
    run("draft a replenishment plan for class B items", "reorder_plan", criticality="B"),
    run("build a purchasing plan for spare parts", "reorder_plan", category="spare_part"),
    # paraphrases that avoid the keywords: carried by BM25 or not at all
    run("which items fall below their reorder point", "low_stock",
        known="paraphrase: low_stock's keyword is 'below reorder point'; 'reorder' alone is "
              "reorder_suggestion's only hit"),
    run("how much should I order of SKU-10150", "reorder_suggestion", sku_id="SKU-10150"),
]

SINGLE_ZH = [
    run("东仓现在有多少库存", "inventory_lookup", warehouse="WH-EAST",
        known="paraphrase: no keyword matches and BM25 stays under its threshold"),
    run("查一下 SKU-10077 的现有库存", "inventory_lookup", sku_id="SKU-10077"),
    run("哪些备件库存不足", "low_stock", category="spare_part"),
    run("SKU-10088 建议采购量是多少", "reorder_suggestion", sku_id="SKU-10088"),
    run("SUP-004 的采购订单有哪些", "po_status", supplier_id="SUP-004"),
    run("已取消的采购单", "po_status", status="cancelled"),
    run("哪些订单已经逾期", "late_shipments"),
    run("延期交货的订单", "late_shipments"),
    run("SKU-10031 哪家供应商评分最高", "supplier_compare", sku_id="SKU-10031"),
    run("做一份采购计划", "reorder_plan"),
    run("包装材料的采购计划", "reorder_plan", category="packaging"),
]

# Chinese contests: two reports' Chinese keywords match different words of one question
MULTI_HIT_ZH_MORE = [
    run("低库存物料应该补多少", "reorder_suggestion"),
    run("缺货物料的补货计划", "reorder_plan",
        known="contested and too close: both reports' Chinese texts explain only the generic "
              "物料, and reorder_plan's says 低库存 where the question says 缺货"),
    run("延迟交货的供应商有哪些", "late_shipments"),
    run("库存不足的备件补货建议", "reorder_suggestion", category="spare_part"),
]

# A verb names no report and no PO status: "create" and "draft" ask for a plan here, and
# "draft" still names the status when it describes purchase orders
VERBS = [
    run("create a reorder plan", "reorder_plan"),
    run("create a replenishment plan for class A items", "reorder_plan", criticality="A"),
    run("新建一个补货计划", "reorder_plan"),
    run("创建一份采购计划", "reorder_plan"),
    run("draft a reorder plan", "reorder_plan"),
    run("make a draft reorder plan for class A", "reorder_plan", criticality="A"),
    run("show draft purchase orders", "po_status", status="draft"),
    run("起草一份补货计划", "reorder_plan"),
    run("显示草稿采购单", "po_status", status="draft"),
]

# A write named by one of its phrases, with every param it requires (a SKU and a quantity): the
# request is complete, so the approval card is the one confirmation it needs. Two name the
# supplier with the word "supplier" (供应商), a keyword of supplier_compare: create_po wins that
# contest clearly (0.27 against 0.01, 0.16 against 0.01)
COMPLETE_WRITES = [
    approve("create a purchase order for 120 units of SKU-10023", "create_po",
            sku_id="SKU-10023", qty=120),
    approve("place an order for 80 units of SKU-20031 from SUP-002", "create_po",
            sku_id="SKU-20031", qty=80, supplier_id="SUP-002"),
    approve("new purchase order: SKU-10057, qty 35", "create_po", sku_id="SKU-10057", qty=35),
    approve("place an order for 25 units of SKU-10036 from supplier SUP-007", "create_po",
            sku_id="SKU-10036", qty=25, supplier_id="SUP-007"),
    approve("SKU-20012 下单 150 件", "create_po", sku_id="SKU-20012", qty=150),
    approve("新建采购单：SKU-10036，数量 45", "create_po", sku_id="SKU-10036", qty=45),
    approve("创建采购单 SKU-20047 共 60 件，供应商 SUP-005", "create_po",
            sku_id="SKU-20047", qty=60, supplier_id="SUP-005"),
]

# The same phrases without a param the write requires: still weak evidence, so routing offers
# the write as one option (with what it did extract) before anything is held for approval
INCOMPLETE_WRITES = [
    confirm("place an order for 60 units", "create_po", qty=60),
    confirm("create a purchase order for SKU-10023", "create_po", sku_id="SKU-10023"),
    confirm("raise a PO for SKU-20031 from SUP-004", "create_po", sku_id="SKU-20031",
            supplier_id="SUP-004"),
    confirm("新建采购单", "create_po"),
    confirm("给 SKU-10057 下单", "create_po", sku_id="SKU-10057"),
    confirm("创建采购单，数量 25", "create_po", qty=25),
]

# A read workflow named next to a write verb, even with a SKU and a quantity a write would take:
# the question asks for the plan, and nothing about it is a write
PLANS_BESIDE_WRITES = [
    run("create a purchasing plan for 40 units of SKU-10023", "reorder_plan"),
    run("before I place an order, build a procurement plan", "reorder_plan"),
    run("新建一份补货计划，SKU-20012 数量 40", "reorder_plan"),
    run("创建采购计划：SKU-10036，数量 25", "reorder_plan"),
]

# A question that names a write and every param it requires asks about an order, or whether to
# place one; it is no request to place one. The last of each language also mentions a supplier,
# so create_po wins a contest there as well, and the question has to keep it weak on its own.
QUESTIONS_ABOUT_WRITES = [
    no_write("did we place an order for 200 units of SKU-10042 last week?", "create_po"),
    no_write("is there already a new purchase order for 60 units of SKU-10023?", "create_po"),
    no_write("has the new purchase order for 90 units of SKU-10061 shipped", "create_po"),
    no_write("how long does it take to create a purchase order for 500 units of SKU-10042",
             "create_po"),
    no_write("should I place an order for 45 units of SKU-10140", "create_po"),
    no_write("we tried to raise a PO for 25 units of SKU-10019 yesterday, did it go through",
             "create_po"),
    no_write("tell me whether I should place an order for 50 units of SKU-10042", "create_po"),
    no_write("I need to place an order for 80 units of SKU-20031, which vendor is cheapest",
             "create_po"),
    no_write("SKU-10042 上周下单 200 件，到货了吗", "create_po"),
    no_write("SKU-20031 的 120 件有没有下单", "create_po"),
    no_write("上周是不是给 SKU-10023 下单了 50 件", "create_po"),
    no_write("SKU-10088 下单 40 件什么时候能到", "create_po"),
    no_write("新建采购单 SKU-10023 数量 50 是否需要审批", "create_po"),
    no_write("创建采购单：SKU-10036，数量 45，供应商选哪家", "create_po"),
]

# A command phrased as a question reads as a question, so routing confirms it once before its
# approval: the price of never taking a question for a complete write
POLITE_WRITES = [
    confirm("can you create a PO for 30 units of SKU-10057", "create_po",
            sku_id="SKU-10057", qty=30),
    confirm("could you place an order for 55 units of SKU-20047?", "create_po",
            sku_id="SKU-20047", qty=55),
    confirm("能帮我给 SKU-10057 下单 30 件吗", "create_po", sku_id="SKU-10057", qty=30),
    confirm("可以新建采购单吗：SKU-20031，数量 90", "create_po", sku_id="SKU-20031", qty=90),
]

# Commands that are no questions, however they are worded: still complete, still straight to
# the approval
COMMANDS_STILL_COMPLETE = [
    approve("please place an order for 35 units of SKU-10061", "create_po",
            sku_id="SKU-10061", qty=35),
    approve("I'd like to place an order for 25 units of SKU-10019", "create_po",
            sku_id="SKU-10019", qty=25),
    approve("给 SKU-10140 下单 25 件", "create_po", sku_id="SKU-10140", qty=25),
    approve("SKU-10057 下单 90 箱吧", "create_po", sku_id="SKU-10057", qty=90),
]

# Complete writes that name their supplier with the word "supplier" (供应商), a keyword of
# supplier_compare: create_po wins the contest by more than the margin (0.27, 0.16 and 0.18 of
# the question against at most 0.03), and the loser matched only the label of create_po's own
# supplier param, which the approval card shows, so the approval is the one confirmation the
# request needs
CONTESTED_COMPLETE_WRITES = [
    approve("place an order for 65 units of SKU-10023 with supplier SUP-001", "create_po",
            sku_id="SKU-10023", qty=65, supplier_id="SUP-001"),
    approve("给 SKU-10088 下单 120 件，供应商用 SUP-004", "create_po",
            sku_id="SKU-10088", qty=120, supplier_id="SUP-004"),
    approve("SKU-10140 下单 40 件，找供应商 SUP-001", "create_po",
            sku_id="SKU-10140", qty=40, supplier_id="SUP-001"),
]

# Contests no target wins by the margin, with a complete write among the contenders: asked
# back, never lifted. Two ask for a write and a report at once; two ask for the write alone, and
# the router cannot tell, so they stay known misses.
CLOSE_CONTESTS_WITH_WRITES = [
    clarify("place an order for 40 units of SKU-10042 and list the low stock items",
            "create_po", "low_stock"),
    approve("new purchase order for 75 units of SKU-10019 because stock is running low",
            "create_po", known="contested and inside the margin (low_stock explains 0.12 of the "
                               "rest, create_po 0.09): asks between the two, and a contest no "
                               "target wins is never lifted", sku_id="SKU-10019", qty=75),
    clarify("给 SKU-10057 下单 30 件，另外列出缺货的物料", "create_po", "low_stock"),
    approve("SKU-10061 缺货了，下单 70 件", "create_po",
            known="contested and inside the margin (create_po explains 0.0088 of the rest, "
                  "low_stock 0.0062): asks between the two, and a contest no target wins is "
                  "never lifted", sku_id="SKU-10061", qty=70),
]

# A plan asked for before a write that the question spells out completely: the plan comes first,
# so the write must not go straight to its approval (the last item was an assertion of
# test_complete_writes.py before). In two of them create_po wins the contest on the write's own
# params ("units", the SKU's number, which its examples spell), but reorder_plan's keyword names
# none of create_po's params, so the write is confirmed first; they went straight to the
# approval while every contest won by the margin lifted a complete write.
PLANS_BEFORE_COMPLETE_WRITES = [
    no_write("before we raise a PO for 60 units of SKU-10057 I want a reorder plan",
             "create_po"),
    no_write("before I place an order for 40 units of SKU-10023, build a procurement plan",
             "create_po"),
    no_write("先做补货计划，再给 SKU-10036 下单 45 件", "create_po"),
    no_write("下单 SKU-20012 共 60 件之前先出一份补货计划", "create_po"),
    no_write("给 SKU-10042 下单 40 件之前，先做一份采购计划", "create_po"),
]

# A plan asked for first, or as the condition of the write, next to a write the question spells
# out completely: the plan comes first, so the write must not go straight to its approval
PLANS_WITH_COMPLETE_WRITES = [
    no_write("I want a replenishment plan before we place an order for 90 units of SKU-10140",
             "create_po"),
    no_write("draw up a purchasing plan first, then raise a PO for 35 units of SKU-10019",
             "create_po"),
    no_write("once the restock plan is ready, place an order for 120 units of SKU-20047",
             "create_po"),
    no_write("下单前先给我一份补货方案：SKU-10061 共 80 件", "create_po"),
    no_write("先出采购计划，然后新建采购单 SKU-20031 数量 50", "create_po"),
    no_write("等补货计划出来再给 SKU-10088 下单 60 件", "create_po"),
]

# A complete write and a report asked for in one breath: the approval card shows the order, not
# the report, so the write must not go straight to its approval
WRITES_WITH_ANOTHER_REQUEST = [
    no_write("place an order for 30 units of SKU-10057 and compare suppliers for it",
             "create_po"),
    no_write("raise a PO for 50 units of SKU-10120 and show me the late shipments", "create_po"),
    no_write("给 SKU-10140 下单 60 件，顺便对比一下供应商", "create_po"),
    no_write("新建采购单 SKU-10233 数量 90，再列一下逾期的采购单", "create_po"),
]

# The supplier named with the label of create_po's supplier param ("supplier", 供应商), before or
# after the phrase: everything the rival keyword matched is on the approval card, so the request
# is as complete as without the contest
WRITES_NAMING_THE_SUPPLIER = [
    approve("raise a PO for 45 units of SKU-10061, supplier SUP-002", "create_po",
            sku_id="SKU-10061", qty=45, supplier_id="SUP-002"),
    approve("supplier SUP-004: new purchase order for 30 units of SKU-10233", "create_po",
            sku_id="SKU-10233", qty=30, supplier_id="SUP-004"),
    approve("给 SKU-20047 下单 75 件，供应商 SUP-003", "create_po",
            sku_id="SKU-20047", qty=75, supplier_id="SUP-003"),
    approve("由供应商 SUP-001 供货，给 SKU-10233 下单 90 件", "create_po",
            sku_id="SKU-10233", qty=90, supplier_id="SUP-001"),
]

# Complete writes beside another target's keyword that names none of create_po's params: a
# vendor (only "supplier" and 供应商 label its supplier param), a shortage given as the reason,
# the plural "suppliers" (a label matches as written). Even when create_po wins the contest, the
# rival word may ask for something the approval card does not show, and the router cannot tell,
# so routing confirms the write first: one extra click. The first three were approvals while
# every contest won by the margin lifted a complete write. Two are decided before any lift: the
# contest is too close, or the shortage report wins it.
CONTESTED_WRITES_CONFIRMED_FIRST = [
    confirm("create a purchase order for 30 units of SKU-10140 from vendor SUP-006",
            "create_po", sku_id="SKU-10140", qty=30, supplier_id="SUP-006"),
    confirm("SKU-10057 is low on stock, place an order for 80 units", "create_po",
            sku_id="SKU-10057", qty=80),
    confirm("库存不足的 SKU-10023 下单 200 件", "create_po", sku_id="SKU-10023", qty=200),
    confirm("SKU-10088 is running low, raise a PO for 150 units", "create_po",
            known="contested and inside the margin (create_po explains 0.109 of the question, "
                  "low_stock 0.081): asks between the two instead of confirming the write",
            sku_id="SKU-10088", qty=150),
    confirm("SKU-20012 快缺货了，新建采购单，数量 60", "create_po",
            known="contested: low_stock wins (0.113 of the question against 0.038 for "
                  "create_po), so the shortage report runs and the order is not offered",
            sku_id="SKU-20012", qty=60),
    confirm("place an order for 50 units of SKU-10019 from one of our suppliers, SUP-005",
            "create_po", sku_id="SKU-10019", qty=50, supplier_id="SUP-005"),
]

# A command that says not to place the write it spells out: no approval of it to click. Every
# Chinese negator of the rule, and the English ones with and without a question's grammar ("don't"
# and "do not" opening a clause also read as a question)
NEGATED_WRITES = [
    no_write("别给 SKU-10042 下单 200 件", "create_po"),
    no_write("不要给 SKU-10057 新建采购单，数量 30", "create_po"),
    no_write("SKU-10023 不用下单 50 件了", "create_po"),
    no_write("无需为 SKU-20031 创建采购单，数量 80", "create_po"),
    no_write("SKU-10061 这次不必下单 70 件", "create_po"),
    no_write("请勿给 SKU-10140 下单 25 件", "create_po"),
    no_write("甭给 SKU-10088 下单 60 件了", "create_po"),
    no_write("不要从供应商 SUP-002 给 SKU-10233 下单 75 件", "create_po"),
    no_write("don't place an order for 200 units of SKU-10042", "create_po"),
    no_write("do not create a purchase order for 60 units of SKU-10023", "create_po"),
    no_write("please don't raise a PO for 25 units of SKU-10019", "create_po"),
    no_write("never place an order for 500 units of SKU-10233", "create_po"),
    no_write("no need to create a PO for 45 units of SKU-10057", "create_po"),
    no_write("I don't want a new purchase order for 35 units of SKU-10061", "create_po"),
    no_write("we do not need to place an order for 80 units of SKU-20047 this week",
             "create_po"),
]

# Chosen, not found: a negator anywhere before the write's phrase withholds the lift, whatever it
# negates (forgetting, worrying, the plan). The order is wanted, but the rule cannot tell, and an
# extra click is cheap where an approval card the user did not ask for is not. (A fifth, "特别急"
# with 别 inside 特别, "especially", is in BIE_INSIDE_WORDS now.)
NEGATIONS_THAT_COST_A_CLICK = [
    confirm("don't forget to place an order for 50 units of SKU-10120", "create_po",
            sku_id="SKU-10120", qty=50),
    confirm("别忘了给 SKU-10057 下单 30 件", "create_po", sku_id="SKU-10057", qty=30),
    confirm("不用担心预算，给 SKU-10036 下单 45 件", "create_po", sku_id="SKU-10036", qty=45),
    confirm("never mind the plan, place an order for 40 units of SKU-10233", "create_po",
            sku_id="SKU-10233", qty=40),
]

# No negator before the write's phrase: a negation after it negates something else, and words
# that only contain one are none ("nevertheless"; 不 outside the rule's negators). Still complete,
# still straight to the approval.
NEGATIONS_OUTSIDE_THE_RULE = [
    approve("给 SKU-10233 下单 90 件，不用加急", "create_po", sku_id="SKU-10233", qty=90),
    approve("place an order for 60 units of SKU-10036, no need to expedite", "create_po",
            sku_id="SKU-10036", qty=60),
    approve("nevertheless, place an order for 30 units of SKU-10120", "create_po",
            sku_id="SKU-10120", qty=30),
    approve("库存不多了，给 SKU-10061 下单 55 件", "create_po", sku_id="SKU-10061", qty=55),
    # Negations the rule did not read when this group was written: 不需要 ("do not need"), one of
    # its no-need negators since (NO_NEED_NEGATIONS), and 不 right before the phrase, still none.
    # Both ask for no order.
    no_write("不需要给 SKU-10019 下单 35 件", "create_po"),
    no_write("这 40 件 SKU-10120 先不下单", "create_po",
             known="a plain 不 right before the phrase is no negator of the rule (先不下单, 'hold "
                   "off ordering'), so the complete write still goes straight to its approval"),
]

# Two phrases of one write make its hit strong by keywords, without any lift: a question, a
# negation or an exclusion must keep it from the approval all the same. Commands with two of its
# phrases still go straight to the approval.
TWO_PHRASE_WRITES = [
    approve("create a PO and place an order for 70 units of SKU-10140", "create_po",
            sku_id="SKU-10140", qty=70),
    approve("新建采购单，给 SKU-10036 下单 80 件", "create_po", sku_id="SKU-10036", qty=80),
    no_write("do we still need to raise a PO and place an order for 120 units of SKU-10019",
             "create_po"),
    no_write("should we raise a PO or place an order for 30 units of SKU-10057 now", "create_po"),
    no_write("上周给 SKU-20031 新建采购单下单 120 件了吗", "create_po"),
    no_write("创建采购单还是直接下单？SKU-10088 共 60 件", "create_po"),
    no_write("先别下单，也不要创建采购单：SKU-10140 数量 70", "create_po"),
    no_write("don't place an order or create a PO for 90 units of SKU-10061", "create_po"),
    no_write("never raise a PO or place an order for 25 units of SKU-10233", "create_po"),
    no_write("不需要下单，也不用创建采购单：SKU-10023 数量 40", "create_po"),
    confirm("新建采购单并下单：SKU-10057 共 30 件，不要用 SUP-003", "create_po",
            sku_id="SKU-10057", qty=30, supplier_id=None),
]

# A read keeps its strength in a question or after a negator: only a target that changes data
# is held back
READS_HELD_BY_NOTHING = [
    run("which late deliveries are overdue?", "late_shipments"),
    run("never mind the totals, show late shipments that are overdue", "late_shipments"),
    run("别等了，哪些采购单逾期延迟了？", "late_shipments"),
]

# 不需要 and its literary 不需 ("do not need") belong to the no-need negators beside 不用 and 不必:
# before the phrase they withhold the lift (also when they negate something else, the price of
# erring towards a negation); after it they negate something else
NO_NEED_NEGATIONS = [
    no_write("SKU-10088 不需要新建采购单了，数量 60", "create_po"),
    no_write("不需下单：SKU-10233 共 40 件", "create_po"),
    approve("给 SKU-20012 下单 50 件，不需要加急", "create_po", sku_id="SKU-20012", qty=50),
    confirm("不需要的旧料先放着，给 SKU-10036 下单 45 件", "create_po",
            sku_id="SKU-10036", qty=45),
]

# 别 inside a word negates nothing: 特别 ("especially"), 类别 ("category"), 级别, 区别, 分别, 个别,
# 识别. Each is a complete write, straight to its approval
BIE_INSIDE_WORDS = [
    approve("特别急，给 SKU-20031 下单 120 件", "create_po", sku_id="SKU-20031", qty=120),
    approve("按类别核对过了，给 SKU-10233 下单 60 件", "create_po", sku_id="SKU-10233", qty=60),
    approve("这几个 SKU 级别不同，给 SKU-10140 下单 25 件", "create_po",
            sku_id="SKU-10140", qty=25),
    approve("区别不大，给 SKU-10088 下单 60 件", "create_po", sku_id="SKU-10088", qty=60),
    approve("分别看过了，给 SKU-10061 下单 70 件", "create_po", sku_id="SKU-10061", qty=70),
    approve("个别规格要确认，先给 SKU-10019 下单 35 件", "create_po", sku_id="SKU-10019", qty=35),
    approve("识别码 SKU-10057，下单 30 件", "create_po", sku_id="SKU-10057", qty=30),
]

# 别 where it negates the command, wherever it stands: opening a clause, after a pronoun, 请, an
# adverb or a time word, and after a noun (这批货别下单). Never lifted. The last item is 别的
# ("other") opening a clause, which negates nothing: complete, straight to the approval
BIE_NEGATORS = [
    no_write("你别给 SKU-10233 下单 90 件", "create_po"),
    no_write("请别新建采购单：SKU-10140，数量 25", "create_po"),
    no_write("先别下单，SKU-10036 共 45 件", "create_po"),
    no_write("SKU-10057 千万别下单 30 件", "create_po"),
    no_write("暂时别给 SKU-20047 下单 60 件", "create_po"),
    no_write("SKU-10088 就别下单了，数量 60", "create_po"),
    no_write("我们别给 SKU-10019 下单 35 件了", "create_po"),
    no_write("今天别给 SKU-10061 下单 30 件", "create_po"),
    no_write("这批货别下单了：SKU-10023 共 50 件", "create_po"),
    approve("别的供应商也行，给 SKU-10140 下单 40 件", "create_po", sku_id="SKU-10140", qty=40),
]

# An id after an exclusion marker is no param, and a write whose request excludes one is
# confirmed first: the approval card shows the order, it cannot show "not SUP-…". The supplier
# the request does want is still extracted.
EXCLUSIONS = [
    confirm("给 SKU-10042 下单 50 件，不要用 SUP-003", "create_po",
            sku_id="SKU-10042", qty=50, supplier_id=None),
    confirm("place an order for 40 units of SKU-10233, not from SUP-002", "create_po",
            sku_id="SKU-10233", qty=40, supplier_id=None),
    confirm("raise a PO for 25 units of SKU-10140 from any supplier except SUP-004", "create_po",
            sku_id="SKU-10140", qty=25, supplier_id=None),
    confirm("place an order for 60 units of SKU-10057 with SUP-001 rather than SUP-003",
            "create_po", sku_id="SKU-10057", qty=60, supplier_id="SUP-001"),
    confirm("use SUP-005 instead of SUP-002 and create a PO for 80 units of SKU-10061",
            "create_po", sku_id="SKU-10061", qty=80, supplier_id="SUP-005"),
    confirm("create a purchase order for 30 units of SKU-10088, excluding SUP-006", "create_po",
            sku_id="SKU-10088", qty=30, supplier_id=None),
    confirm("place an order for 50 units of SKU-10120 and don't use SUP-002", "create_po",
            sku_id="SKU-10120", qty=50, supplier_id=None),
    confirm("create a PO for 55 units of SKU-10233 but not with SUP-002", "create_po",
            sku_id="SKU-10233", qty=55, supplier_id=None),
    confirm("给 SKU-10233 下单 90 件，别用 SUP-001", "create_po",
            sku_id="SKU-10233", qty=90, supplier_id=None),
    confirm("SKU-10140 下单 25 件，不用 SUP-004 的货", "create_po",
            sku_id="SKU-10140", qty=25, supplier_id=None),
    confirm("除了 SUP-002 都行，给 SKU-10036 下单 45 件", "create_po",
            sku_id="SKU-10036", qty=45, supplier_id=None),
    confirm("新建采购单：SKU-20047 数量 60，排除 SUP-005", "create_po",
            sku_id="SKU-20047", qty=60, supplier_id=None),
    confirm("给 SKU-10061 下单 70 件，不选 SUP-003，用 SUP-001", "create_po",
            sku_id="SKU-10061", qty=70, supplier_id="SUP-001"),
    confirm("给 SKU-10057 下单 30 件，不要用供应商 SUP-006", "create_po",
            sku_id="SKU-10057", qty=30, supplier_id=None),
    confirm("给 SKU-10140 下单 25 件，别选 SUP-006", "create_po",
            sku_id="SKU-10140", qty=25, supplier_id=None),
]

# A marker that excludes no id leaves a complete write as complete as it was, and a "not" that
# negates a verb leaves the id after that verb a param
EXCLUSIONS_OF_NOTHING = [
    approve("place an order for 35 units of SKU-10019 from SUP-004, not urgent", "create_po",
            sku_id="SKU-10019", qty=35, supplier_id="SUP-004"),
    approve("rather than wait, place an order for 40 units of SKU-10023", "create_po",
            sku_id="SKU-10023", qty=40),
    approve("先不选供应商了，给 SKU-10088 下单 60 件", "create_po", sku_id="SKU-10088", qty=60),
    run("late shipments that haven't arrived from SUP-006", "late_shipments",
        supplier_id="SUP-006"),
    run("show overdue orders we did not get from SUP-003", "late_shipments",
        supplier_id="SUP-003"),
]

# Found while the groups above were made to pass, as known misses (v0.1), and read since: 不包括
# ("not including"), 不从 ("not from"), "without" and "avoid" are exclusion markers, and 禁止
# ("forbid"), 不准 and 不许 ("not allowed") negators (不许 was added with them). An excluded
# supplier is no param and the write is confirmed first; a forbidden write is never held for its
# approval. The last is what the addition costs: 不准 also negates inside 不准时 ("not on time").
LIST_ADDITIONS = [
    confirm("给 SKU-10233 下单 60 件，不包括 SUP-002", "create_po",
            sku_id="SKU-10233", qty=60, supplier_id=None),
    confirm("给 SKU-10057 下单 30 件，不从 SUP-003 进货", "create_po",
            sku_id="SKU-10057", qty=30, supplier_id=None),
    confirm("place an order for 40 units of SKU-10061 without SUP-005", "create_po",
            sku_id="SKU-10061", qty=40, supplier_id=None),
    confirm("place an order for 70 units of SKU-10019, avoid SUP-006", "create_po",
            sku_id="SKU-10019", qty=70, supplier_id=None),
    no_write("禁止给 SKU-10036 下单 45 件", "create_po"),
    no_write("不准给 SKU-20031 下单 120 件", "create_po"),
    no_write("不许给 SKU-10088 下单 60 件", "create_po"),
    approve("交货不准时也没办法，给 SKU-10042 下单 50 件", "create_po", sku_id="SKU-10042", qty=50,
            known="不准 inside 不准时 ('not on time') reads as the negator 不准 "
                  "('not allowed'), as it would inside 不准确 ('inaccurate'): the complete "
                  "write is confirmed first"),
]

# Found with them, and still known misses with no rule of their own (v0.1): an exclusion after
# its value, or in words that are no marker of the rule ("but" is too broad to be one; "skip"),
# and a question in no grammar it reads. Each names a complete write, so it goes straight to an
# approval: of an order from the supplier the request rules out, or of one it only asks about.
_SUPPLIER_TAKEN = "the supplier is extracted, and the complete write goes straight to its approval"
HOLDS_NO_RULE_READS = [
    confirm("给 SKU-10042 下单 50 件，SUP-003 除外", "create_po",
            sku_id="SKU-10042", qty=50, supplier_id=None,
            known="除外 ('excepted') follows the value it excludes, and a marker is read only "
                  "before one: " + _SUPPLIER_TAKEN),
    confirm("place an order for 30 units of SKU-10088 from anyone but SUP-001", "create_po",
            sku_id="SKU-10088", qty=30, supplier_id=None,
            known="'but' is no exclusion marker: " + _SUPPLIER_TAKEN),
    confirm("place an order for 70 units of SKU-10019, skip SUP-006", "create_po",
            sku_id="SKU-10019", qty=70, supplier_id=None,
            known="'skip' is no exclusion marker ('avoid' is): " + _SUPPLIER_TAKEN),
    no_write("I wonder if we should place an order for 50 units of SKU-10120", "create_po",
             known="an embedded question opened by 'if' is not read (only 'whether' is): the "
                   "complete write goes straight to its approval"),
]

# Found with them, the other way: a negative question's auxiliary right before an id ("didn't
# SUP-003 ...") read as an exclusion of that id, so a read lost the filter and ran wider than it
# was asked. A contraction now excludes an id only through a verb of using or choosing ("don't
# use SUP-003", EXCLUSIONS); right before one it opens a question or negates its own verb.
NEGATIVE_QUESTIONS = [
    run("didn't SUP-003 deliver late last month?", "late_shipments", supplier_id="SUP-003"),
    run("doesn't SKU-10042 have stock in the east warehouse?", "inventory_lookup",
        sku_id="SKU-10042", warehouse="WH-EAST"),
]

# Found with that rule, known misses (v0.1): a contraction that does rule an id out with no verb
# of using or choosing after it ("aren't from", "isn't"), or through another verb ("don't order
# from"), leaves the id a param. The request still excludes it, so a write is confirmed first,
# but its confirmation carries the supplier ruled out; a read is filtered by it.
_CONTRACTION_KEEPS = ("a contraction excludes an id only through a verb of using or choosing, so "
                      "the id after it is extracted")
CONTRACTIONS_THAT_EXCLUDE = [
    run("late shipments that aren't from SUP-003", "late_shipments", supplier_id=None,
        known=_CONTRACTION_KEEPS + ", and the read is filtered by the supplier it rules out"),
    confirm("place an order for 40 units of SKU-10233 from a supplier that isn't SUP-002",
            "create_po", sku_id="SKU-10233", qty=40, supplier_id=None,
            known=_CONTRACTION_KEEPS + ": the write is confirmed with the supplier ruled out"),
    confirm("place an order for 40 units of SKU-10233 and please don't order from SUP-002",
            "create_po", sku_id="SKU-10233", qty=40, supplier_id=None,
            known="'order from' is no verb of using or choosing, so the supplier after it is "
                  "extracted: the write is confirmed with the supplier ruled out"),
]

DEV_SET = [*MULTI_HIT_EN, *MULTI_HIT_ZH, *CONTAINED, *SINGLE_EN, *SINGLE_ZH,
           *MULTI_HIT_ZH_MORE, *VERBS, *COMPLETE_WRITES, *INCOMPLETE_WRITES,
           *PLANS_BESIDE_WRITES, *QUESTIONS_ABOUT_WRITES, *POLITE_WRITES,
           *COMMANDS_STILL_COMPLETE, *CONTESTED_COMPLETE_WRITES, *CLOSE_CONTESTS_WITH_WRITES,
           *PLANS_BEFORE_COMPLETE_WRITES, *PLANS_WITH_COMPLETE_WRITES,
           *WRITES_WITH_ANOTHER_REQUEST, *WRITES_NAMING_THE_SUPPLIER,
           *CONTESTED_WRITES_CONFIRMED_FIRST, *NEGATED_WRITES, *NEGATIONS_THAT_COST_A_CLICK,
           *NEGATIONS_OUTSIDE_THE_RULE, *TWO_PHRASE_WRITES, *READS_HELD_BY_NOTHING,
           *NO_NEED_NEGATIONS, *BIE_INSIDE_WORDS, *BIE_NEGATORS, *EXCLUSIONS,
           *EXCLUSIONS_OF_NOTHING, *LIST_ADDITIONS, *HOLDS_NO_RULE_READS, *NEGATIVE_QUESTIONS,
           *CONTRACTIONS_THAT_EXCLUDE]


def verdict(item: Item, decision) -> str | None:
    """None when the decision is what the item expects, else what went wrong."""
    got = (f"{decision.kind.value} {decision.target} {decision.clarify_options} "
           f"({decision.source.value}: {decision.reason})")
    if item.mode in ("run", "approve"):
        if decision.kind not in (RouteKind.TOOL, RouteKind.WORKFLOW) or \
                decision.target != item.target:
            return f"expected to run {item.target}, got {got}"
    elif item.mode == "confirm":
        if decision.kind is not RouteKind.CLARIFY or decision.clarify_options != [item.target]:
            return f"expected a confirmation of {item.target}, got {got}"
    elif item.mode == "no_write":
        if decision.kind in (RouteKind.TOOL, RouteKind.WORKFLOW) and \
                decision.target == item.target:
            return f"expected anything but a straight route to {item.target}, got {got}"
    elif decision.kind is not RouteKind.CLARIFY or \
            not set(item.target) <= set(decision.clarify_options):
        return f"expected a clarification between {list(item.target)}, got {got}"
    missing = {k: v for k, v in item.params.items() if decision.params.get(k) != v}
    return f"params {missing} not extracted, got {decision.params}" if missing else None


def test_the_dev_set_is_large_and_bilingual():
    zh = [i for i in DEV_SET if any("一" <= c <= "鿿" for c in i.query)]
    assert len(DEV_SET) >= 40 and len(zh) >= 15
    assert len({i.query for i in DEV_SET}) == len(DEV_SET)


def _param(item: Item):
    marks = [pytest.mark.xfail(strict=True, reason=item.known)] if item.known else []
    return pytest.param(item, marks=marks, id=item.query)


@pytest.mark.parametrize("item", [_param(i) for i in DEV_SET])
def test_dev_phrasing(builtin, item):
    problem = verdict(item, builtin.decide(item.query))
    assert problem is None, problem


@pytest.mark.parametrize("item", [_param(i) for i in DEV_SET if i.mode == "approve"])
def test_a_complete_write_waits_for_its_approval_and_nothing_else(builtin, item):
    """Through the whole graph: the answer is the write's one-time approval (needs
    confirmation, with a grant), not a routing question first, and nothing was written."""
    before = builtin.store.count("purchase_order")
    response = builtin.ask(item.query)
    assert response.clarify_options == [] and response.error is None, response.answer
    assert response.grant is not None, response.answer
    held = [s for s in response.trace if s.step == "gate" and s.status.value == "blocked"]
    assert [s.facts.get("stage") for s in held] == ["confirmation"]
    assert builtin.store.count("purchase_order") == before


@pytest.mark.parametrize("item", [_param(i) for i in DEV_SET if i.mode == "no_write"])
def test_a_question_about_a_write_is_never_held_for_its_approval(builtin, item):
    """Through the whole graph: a request that asks for no write gets no approval of one to
    click (no grant), and nothing was written."""
    before = builtin.store.count("purchase_order")
    response = builtin.ask(item.query)
    assert response.grant is None and response.error is None, response.answer
    assert builtin.store.count("purchase_order") == before


# A drop-in read tool. ``supplier_scorecard`` (examples/plugins/supplier_scorecard.py) arrives
# through a plugin path alone, so these run on the built-in plugins plus that one file
# (``with_scorecard``), not on ``builtin``. The groups were written before the tool's keywords,
# examples and param extractor were wired, from what a planner asks for (the scorecard by name,
# the on-time rate, delivery performance and reliability, the average delay, a window in days),
# not from the words the tool declares. What the first wiring still missed carries its reason.
SCORECARD_EN = [
    run("supplier scorecard", "supplier_scorecard"),
    run("show me the scorecard for SUP-003", "supplier_scorecard", supplier_id="SUP-003"),
    run("what is SUP-005's on-time delivery rate", "supplier_scorecard", supplier_id="SUP-005"),
    run("on-time delivery rate of our suppliers over the last 30 days", "supplier_scorecard",
        window_days=30),
    run("supplier delivery performance for the past 60 days", "supplier_scorecard",
        window_days=60),
    run("how reliable is SUP-002 at delivering", "supplier_scorecard", supplier_id="SUP-002"),
    run("what share of SUP-004's orders arrive on time", "supplier_scorecard",
        known="paraphrase: no keyword matches ('arrive on time' is declared by nothing, being as "
              "often asked of one order as of a supplier's record) and BM25 stays under its "
              "threshold",
        supplier_id="SUP-004"),
    run("average delay of deliveries from SUP-006 in the last 90 days", "supplier_scorecard",
        supplier_id="SUP-006", window_days=90),
    run("rate our suppliers on delivery reliability", "supplier_scorecard"),
    run("give me a 120 day scorecard for supplier SUP-001", "supplier_scorecard",
        supplier_id="SUP-001", window_days=120),
    run("OTD by supplier", "supplier_scorecard"),
    run("which supplier delivers most reliably", "supplier_scorecard"),
]

SCORECARD_ZH = [
    run("供应商评分卡", "supplier_scorecard"),
    run("SUP-003 的评分卡", "supplier_scorecard", supplier_id="SUP-003"),
    run("SUP-005 的准时交货率是多少", "supplier_scorecard", supplier_id="SUP-005"),
    run("近 30 天各供应商的准时交货率", "supplier_scorecard", window_days=30),
    run("过去 60 天供应商的交付表现", "supplier_scorecard", window_days=60),
    run("SUP-002 交货靠谱吗", "supplier_scorecard",
        known="paraphrase: 靠谱 ('dependable') is no declared word (可靠 is) and BM25 stays "
              "under its threshold",
        supplier_id="SUP-002"),
    run("SUP-004 的订单有多大比例按时到货", "supplier_scorecard", supplier_id="SUP-004"),
    run("SUP-006 平均晚到几天", "supplier_scorecard",
        known="paraphrase: 晚到 ('arrives late') is declared by no tool, and the scorecard's "
              "words for the average delay are 平均延迟 and 平均逾期; BM25 stays under its "
              "threshold",
        supplier_id="SUP-006"),
    run("最近 90 天 SUP-001 的交货表现", "supplier_scorecard", supplier_id="SUP-001",
        window_days=90),
    run("哪家供应商交货最可靠", "supplier_scorecard"),
    run("给供应商的交付情况打个分", "supplier_scorecard",
        known="paraphrase: the only declared word is 供应商, supplier_compare's, so it runs "
              "that report; the scorecard's own words (评分卡, 交付表现, 履约) are not in the "
              "question"),
]

# Asked for a neighbour that is not the scorecard, in words the scorecard could share (supplier,
# delivery, late, orders): each keeps the tool it had before the plugin was loaded
NEIGHBOURS_OF_THE_SCORECARD = [
    run("which of SUP-002's orders are overdue", "late_shipments", supplier_id="SUP-002"),
    run("any deliveries behind schedule from SUP-004", "late_shipments", supplier_id="SUP-004"),
    run("rank the suppliers by rating", "supplier_compare"),
    run("SUP-006 的逾期订单有哪些", "late_shipments", supplier_id="SUP-006"),
    run("按评分给供应商排个名", "supplier_compare"),
    run("SUP-003 的采购单状态", "po_status", supplier_id="SUP-003"),
]

SCORECARD_SET = [*SCORECARD_EN, *SCORECARD_ZH, *NEIGHBOURS_OF_THE_SCORECARD]


def test_the_scorecard_set_is_bilingual_and_repeats_no_other_phrasing():
    zh = [i for i in SCORECARD_SET if any("一" <= c <= "鿿" for c in i.query)]
    assert len(zh) >= 8 and len(SCORECARD_SET) - len(zh) >= 8
    queries = [i.query for i in [*DEV_SET, *SCORECARD_SET]]
    assert len(set(queries)) == len(queries)


@pytest.mark.parametrize("item", [_param(i) for i in SCORECARD_SET])
def test_scorecard_phrasing(with_scorecard, item):
    problem = verdict(item, with_scorecard.decide(item.query))
    assert problem is None, problem


@pytest.mark.parametrize("item", [_param(i) for i in DEV_SET])
def test_loading_the_scorecard_moves_no_phrasing_of_the_dev_set(with_scorecard, item):
    """The whole dev set again, with the plugin loaded: a new tool may not take a question
    that belongs to a tool already there."""
    problem = verdict(item, with_scorecard.decide(item.query))
    assert problem is None, problem


def test_no_scorecard_phrasing_is_one_of_its_examples(with_scorecard):
    from twotrack.core.routing.text import normalize

    examples = {normalize(e) for e in with_scorecard.registry.tool("supplier_scorecard").examples}
    assert not examples & {normalize(i.query) for i in SCORECARD_SET}
