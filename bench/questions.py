"""The question set: 85 questions over the seeded data, ground truth computed by code.

Every expected number comes from ``bench.truth`` (the plugin's own formulas over the store).
Entities are picked deterministically from the seeded data with a seeded RNG; ids that appear in
the tools' own routing examples are never used, and no question repeats a routing example.

Each answerable item names the single call a correct system would make (``expected_tool`` +
``expected_params``) and, where that call's output shows the answer, a *locator*:

* ``agg:KEY``                     the aggregate ``KEY`` of the result;
* ``cell:COL@KEYCOL=KEYVAL``      column ``COL`` of the row whose ``KEYCOL`` is ``KEYVAL``; only
                                  used when ``KEYVAL`` is named in the question, so a reader
                                  can find the row they asked about;
* ``top:COL``                     column ``COL`` of the first row; only used when the tool sorts
                                  by exactly the criterion the question asks about.

``distractors`` are the asked quantity recomputed with one of the question's filters dropped
(or, tagged ``method_distractor``, computed by the other tool's method): the number a system
shows when it answers for the wrong scope. ``bench.score`` explains how both are used.

``python -m bench.questions`` prints a composition summary; ``--jsonl`` dumps every item.
"""

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from twotrack.core.routing.text import normalize
from twotrack.plugins.supply_chain import extract

from .config import RNG_SEED
from .truth import Truth
from .world import World, build_world

Category = Literal["lookup", "aggregation", "formula", "ranking", "multi_condition",
                   "unanswerable"]
CATEGORIES: tuple[str, ...] = ("lookup", "aggregation", "formula", "ranking", "multi_condition",
                               "unanswerable")
ZH_WAREHOUSE = {"WH-EAST": "东仓", "WH-WEST": "西仓", "WH-CENTRAL": "中央仓"}
MISSING_PO = "PO-99999"
MISSING_SKU = "SKU-99999"
MIN_COUNT = 3  # integer answers below this are too easy to hit by coincidence ("1 row")


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    value: float
    tol: float = 0.0  # absolute tolerance on top of the written precision
    locator: str | None = None  # where a correct twotrack response shows it (scoring credit)
    check: str | None = None  # where the expected call's output holds it (GT cross-check only)
    distractors: tuple[float, ...] = ()


class ExpectedText(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    alternatives: tuple[str, ...]  # any one counts (an id or a display name)
    locator: str | None = None


class Item(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    lang: Literal["en", "zh"]
    category: Category
    answerable: bool
    expected: tuple[Expected, ...] = ()
    expected_text: tuple[ExpectedText, ...] = ()
    expected_tool: str | None = None
    expected_params: dict[str, JsonValue] = Field(default_factory=dict)
    # other targets whose output measures the same quantity by definition (not by accident)
    also_accepted: tuple[str, ...] = ()
    variant_of: str | None = None  # zh items translate an en item with the same entities
    tags: tuple[str, ...] = ()
    note: str = ""

    @property
    def accepted_tools(self) -> frozenset[str]:
        return frozenset(t for t in (self.expected_tool, *self.also_accepted) if t)


# ---------------------------------------------------------------- helpers


def question_numbers(text: str) -> list[float]:
    """Every digit run in a question (identifiers included): numbers a system may echo."""
    return [float(m) for m in re.findall(r"\d+(?:\.\d+)?", text)]


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def scope_distractors(value: float, candidates: Iterable[float | None],
                      question: str) -> tuple[float, ...]:
    """Wrong-scope values worth flagging: nonzero, distinct from the answer, from each other
    and from every number the question itself contains."""
    echoes = question_numbers(question)
    out: list[float] = []
    for c in candidates:
        if c is None or c == 0:
            continue
        c = float(c)
        if _same(c, value) or any(_same(c, e) for e in echoes) or any(_same(c, o) for o in out):
            continue
        out.append(c)
    return tuple(out)


def parse_locator(locator: str) -> tuple[str, str, str | None, str | None]:
    """'agg:k' -> ('agg', k, None, None); 'cell:c@k=v' -> ('cell', c, k, v); 'top:c'."""
    kind, _, rest = locator.partition(":")
    if kind == "cell":
        column, _, key = rest.partition("@")
        key_col, _, key_val = key.partition("=")
        return kind, column, key_col, key_val
    return kind, rest, None, None


def example_ids(world: World) -> set[str]:
    """SKU, PO and supplier ids named in any routing example; questions avoid them."""
    out: set[str] = set()
    targets = [*world.registry.tools.values(), *world.registry.workflows.values()]
    for target in targets:
        for example in target.examples:
            for find in (extract.sku_id, extract.po_id, extract.supplier_id):
                found = find(example)
                if found:
                    out.add(found)
    return out


def routing_examples(world: World) -> set[str]:
    targets = [*world.registry.tools.values(), *world.registry.workflows.values()]
    return {normalize(e) for t in targets for e in t.examples}


# ---------------------------------------------------------------- builder


class _Builder:
    def __init__(self, truth: Truth, rng: random.Random, reserved: set[str]) -> None:
        self.t = truth
        self.rng = rng
        self.used = set(reserved)
        self.items: list[Item] = []
        self.by_id: dict[str, Item] = {}
        skus = sorted(truth.positions)
        self.spare = [s for s in skus if truth.category(s) == "spare_part"]
        self.packaging = [s for s in skus if truth.category(s) == "packaging"]
        self.low_ids = [p.sku_id for p in truth.low()]

    # ------------------------------------------------------------ plumbing

    def pick(self, candidates: Sequence[str]) -> str:
        """A seeded choice among entities no earlier question used (and no routing example
        names)."""
        pool = sorted({c for c in candidates if c not in self.used})
        if not pool:
            raise RuntimeError("the seeded data has no entity left for a question template")
        choice = self.rng.choice(pool)
        self.used.add(choice)
        return choice

    def add(self, **fields) -> Item:
        item = Item(**fields)
        for e in item.expected:
            if e.locator and e.locator.startswith("cell:"):
                _, _, _, key_val = parse_locator(e.locator)
                if not key_val or key_val.casefold() not in item.text.casefold():
                    raise RuntimeError(f"{item.id}: cell locator key {key_val!r} is not in the "
                                       "question")
            integral = float(e.value).is_integer()
            if item.answerable and integral and e.value < MIN_COUNT and not item.expected_text:
                raise RuntimeError(f"{item.id}: expected {e.value} is below {MIN_COUNT}")
            if e.value == 0:
                raise RuntimeError(f"{item.id}: zero answers are not machine-checkable")
        if item.id in self.by_id:
            raise RuntimeError(f"duplicate item id {item.id}")
        self.items.append(item)
        self.by_id[item.id] = item
        return item

    def variant(self, of: str, item_id: str, text: str) -> Item:
        """A zh translation of an en item: same entities, same truth, same expected call."""
        base = self.by_id[of]
        fields = base.model_dump()
        fields.update(id=item_id, text=text, lang="zh", variant_of=of,
                      tags=(*base.tags, "variant"))
        expected = []
        for e in base.expected:  # distractors must also avoid the new text's numbers
            data = e.model_dump()
            data["distractors"] = scope_distractors(e.value, e.distractors, text)
            expected.append(Expected(**data))
        fields["expected"] = tuple(expected)
        return self.add(**fields)

    def num(self, name: str, value: float, question: str, *, locator: str | None = None,
            check: str | None = None, scope: Iterable[float | None] = (),
            tol: float = 0.0) -> Expected:
        return Expected(name=name, value=float(value), tol=tol, locator=locator,
                        check=check or locator,
                        distractors=scope_distractors(float(value), scope, question))

    def supplier_text(self, supplier_id: str, locator: str | None) -> ExpectedText:
        name = self.t.supplier_names.get(supplier_id)
        return ExpectedText(name="supplier",
                            alternatives=tuple(x for x in (supplier_id, name) if x),
                            locator=locator)

    # ------------------------------------------------------------ helpers over truth

    def stock_pairs(self, minimum: int = MIN_COUNT) -> list[tuple[str, str]]:
        """(sku, warehouse) with stock in at least two warehouses and >= minimum here."""
        pairs = []
        for sku in self.spare + self.packaging:
            stocked = self.t.stocked_warehouses(sku)
            if len(stocked) < 2:
                continue
            pairs += [(sku, w) for w in stocked if self.t.stock(sku=sku, warehouse=w) >= minimum]
        return pairs

    def pick_pair(self, pairs: list[tuple[str, str]], *,
                  warehouse: str | None = None) -> tuple[str, str]:
        pool = sorted(p for p in pairs if p[0] not in self.used
                      and (warehouse is None or p[1] == warehouse))
        if not pool:
            raise RuntimeError("no (sku, warehouse) pair left")
        sku, wh = self.rng.choice(pool)
        self.used.add(sku)
        return sku, wh

    def ranking_skus(self) -> list[str]:
        """SKUs with at least two suppliers in history, a strict best and a best that is not
        the overall best (so the drop-the-SKU answer differs)."""
        overall = self.t.ranked()[0]
        out = []
        for sku in self.spare + self.packaging:
            ranked = self.t.ranked_for(sku)
            if len(ranked) < 2 or ranked[0]["supplier_id"] == overall["supplier_id"]:
                continue
            if ranked[0].get("rating") is None or ranked[0]["rating"] == ranked[1].get("rating"):
                continue
            out.append(sku)
        return out

    # ------------------------------------------------------------ categories

    def lookups(self) -> None:
        t = self.t
        not_low = [s for s in self.spare + self.packaging if s not in self.low_ids]
        spread = {s for s, _ in self.stock_pairs()}

        def avail_item(item_id: str, sku: str, text: str, tags: tuple[str, ...] = ()) -> None:
            q = text.format(sku=sku)
            self.add(id=item_id, text=q, lang="en", category="lookup", answerable=True,
                     expected=(self.num("available units", t.stock(sku=sku), q,
                                        locator="agg:total_available", scope=[t.stock()]),),
                     expected_tool="inventory_lookup", expected_params={"sku_id": sku},
                     also_accepted=("reorder_suggestion", "low_stock"), tags=tags)

        avail_item("lk-01", self.pick([s for s in not_low if s in spread
                                       and t.category(s) == "spare_part"]),
                   "How many units of {sku} are available across all warehouses?")
        avail_item("lk-02", self.pick([s for s in not_low if s in spread
                                       and t.category(s) == "packaging"]),
                   "What is the available quantity of {sku} right now?")
        avail_item("lk-03", self.pick([s for s in self.low_ids if t.stock(sku=s) >= MIN_COUNT]),
                   "How much {sku} is free to use, meaning on hand minus reserved, over all "
                   "sites?", tags=("paraphrase",))

        pairs = self.stock_pairs()

        def wh_item(item_id: str, sku: str, wh: str, text: str,
                    tags: tuple[str, ...] = ()) -> None:
            q = text.format(sku=sku, wh=wh, word=wh.split("-")[1].lower())
            value = t.stock(sku=sku, warehouse=wh)
            self.add(id=item_id, text=q, lang="en", category="lookup", answerable=True,
                     expected=(self.num("available units in the warehouse", value, q,
                                        locator="agg:total_available",
                                        scope=[t.stock(sku=sku), t.stock(warehouse=wh),
                                               t.stock()]),),
                     expected_tool="inventory_lookup",
                     expected_params={"sku_id": sku, "warehouse": wh}, tags=tags)

        s, w = self.pick_pair(pairs, warehouse="WH-EAST")
        wh_item("lk-04", s, w, "How many units of {sku} are available in {wh}?")
        s, w = self.pick_pair(pairs, warehouse="WH-WEST")
        wh_item("lk-05", s, w, "What's the available stock of {sku} at the {word} warehouse?")
        s, w = self.pick_pair(pairs, warehouse="WH-CENTRAL")
        wh_item("lk-06", s, w, "Is there any {sku} left in {wh}? How many units are free to "
                "use there?", tags=("paraphrase",))

        sku = self.pick([s for s in not_low if t.stock("on_hand", sku=s) >= MIN_COUNT])
        q = f"What is the on-hand quantity of {sku} across all warehouses?"
        self.add(id="lk-07", text=q, lang="en", category="lookup", answerable=True,
                 expected=(self.num("units on hand", t.stock("on_hand", sku=sku), q,
                                    locator="agg:total_on_hand",
                                    scope=[t.stock("on_hand")]),),
                 expected_tool="inventory_lookup", expected_params={"sku_id": sku})

        sku = self.pick([s for s in not_low if t.stock("reserved", sku=s) >= MIN_COUNT])
        q = f"How many units of {sku} are reserved?"
        self.add(id="lk-08", text=q, lang="en", category="lookup", answerable=True,
                 expected=(self.num("reserved units", t.stock("reserved", sku=sku), q,
                                    locator="agg:total_reserved",
                                    scope=[t.stock("reserved")]),),
                 expected_tool="inventory_lookup", expected_params={"sku_id": sku})

        q = "How many purchase orders are late right now?"
        self.add(id="lk-09", text=q, lang="en", category="lookup", answerable=True,
                 expected=(self.num("late purchase orders", len(t.late()), q,
                                    locator="agg:late_count"),),
                 expected_tool="late_shipments")

        late_by_supplier = Counter(o["supplier_id"] for o in t.late())
        busy = sorted(s for s, n in late_by_supplier.items() if n >= MIN_COUNT)

        def late_item(item_id: str, supplier: str, text: str,
                      tags: tuple[str, ...] = ()) -> None:
            q = text.format(sup=supplier)
            self.add(id=item_id, text=q, lang="en", category="lookup", answerable=True,
                     expected=(self.num("late purchase orders of the supplier",
                                        len(t.late(supplier=supplier)), q,
                                        locator="agg:late_count", scope=[len(t.late())]),),
                     expected_tool="late_shipments", expected_params={"supplier_id": supplier},
                     tags=tags)

        sup_a = self.pick(busy)
        late_item("lk-10", sup_a, "How many late purchase orders does {sup} have?")
        sup_b = self.pick(busy)
        late_item("lk-11", sup_b, "How many of {sup}'s orders are past their ETA?",
                  tags=("paraphrase",))

        late_ids = {o["po_id"] for o in t.late()}
        po = self.pick([o["po_id"] for o in t.orders
                        if o["po_id"] not in late_ids and (o.get("qty") or 0) >= MIN_COUNT])
        q = f"What quantity was ordered on {po}?"
        self.add(id="lk-12", text=q, lang="en", category="lookup", answerable=True,
                 expected=(self.num("ordered quantity", t.qty(t.orders_where(po=po)), q,
                                    locator="agg:total_qty", scope=[t.qty(t.orders)]),),
                 expected_tool="po_status", expected_params={"po_id": po})

        oldest = t.late()[0]["po_id"]
        po = self.pick([o["po_id"] for o in t.late()
                        if o["po_id"] != oldest and o["days_late"] >= MIN_COUNT])
        days = next(o["days_late"] for o in t.late() if o["po_id"] == po)
        q = f"How many days late is {po}?"
        self.add(id="lk-13", text=q, lang="en", category="lookup", answerable=True,
                 expected=(self.num("days late", days, q,
                                    locator=f"cell:days_late@po_id={po}"),),
                 expected_tool="late_shipments", tags=("row_answer",),
                 note="late_shipments cannot filter by PO, so the answer is one row of the "
                      "full late list")

        q = "How many purchase orders do we have in total?"
        self.add(id="lk-14", text=q, lang="en", category="lookup", answerable=True,
                 expected=(self.num("purchase orders", len(t.orders), q,
                                    locator="agg:po_count"),),
                 expected_tool="po_status")

        lk01 = self.by_id["lk-01"].expected_params
        lk04 = self.by_id["lk-04"].expected_params
        self.variant("lk-01", "lk-zh-01", f"{lk01['sku_id']} 在所有仓库一共有多少可用库存？")
        self.variant("lk-04", "lk-zh-02",
                     f"{ZH_WAREHOUSE[lk04['warehouse']]}里 {lk04['sku_id']} 的可用库存是多少？")
        self.variant("lk-09", "lk-zh-03", "现在一共有多少张采购单逾期了？")
        self.variant("lk-10", "lk-zh-04", f"{sup_a} 有几张逾期的采购单？")

    def aggregations(self) -> None:
        t = self.t

        def item(item_id: str, text: str, name: str, value: float, locator: str, tool: str,
                 params: dict | None = None, scope: Iterable[float | None] = (),
                 tags: tuple[str, ...] = (), also: tuple[str, ...] = ()) -> None:
            self.add(id=item_id, text=text, lang="en", category="aggregation", answerable=True,
                     expected=(self.num(name, value, text, locator=locator, scope=scope),),
                     expected_tool=tool, expected_params=params or {}, also_accepted=also,
                     tags=tags)

        item("ag-01", "What is the total available stock in WH-EAST across all SKUs?",
             "available units in the warehouse", t.stock(warehouse="WH-EAST"),
             "agg:total_available", "inventory_lookup", {"warehouse": "WH-EAST"},
             [t.stock()])
        item("ag-02", "Across all SKUs, how many units are available in the central warehouse?",
             "available units in the warehouse", t.stock(warehouse="WH-CENTRAL"),
             "agg:total_available", "inventory_lookup", {"warehouse": "WH-CENTRAL"},
             [t.stock()])
        item("ag-03", "What is our total on-hand quantity across all warehouses?",
             "units on hand", t.stock("on_hand"), "agg:total_on_hand", "inventory_lookup")
        item("ag-04", "How many units are reserved in WH-WEST in total?",
             "reserved units in the warehouse", t.stock("reserved", warehouse="WH-WEST"),
             "agg:total_reserved", "inventory_lookup", {"warehouse": "WH-WEST"},
             [t.stock("reserved")])
        everything = len(t.orders)
        for item_id, text, status in (
                ("ag-05", "How many purchase orders are in shipped status?", "shipped"),
                ("ag-06", "How many purchase orders have been cancelled?", "cancelled"),
                ("ag-07", "How many purchase orders are still drafts?", "draft"),
                ("ag-08", "How many purchase orders currently have status open?", "open")):
            item(item_id, text, f"{status} purchase orders",
                 len(t.orders_where(status=status)), "agg:po_count", "po_status",
                 {"status": status}, [everything])
        item("ag-09", "What is the total quantity on purchase orders with status open?",
             "quantity on open purchase orders", t.qty(t.orders_where(status="open")),
             "agg:total_qty", "po_status", {"status": "open"}, [t.qty(t.orders)])
        item("ag-10", "What is the total quantity across all late purchase orders?",
             "late quantity", t.qty(t.late()), "agg:late_qty", "late_shipments")
        item("ag-11", "How many different suppliers have at least one late purchase order?",
             "suppliers with late orders", len({o["supplier_id"] for o in t.late()}),
             "agg:supplier_count", "late_shipments")
        # reorder_suggestion without a SKU covers exactly the low-stock SKUs (its sku_count)
        item("ag-12", "How many SKUs are below their reorder point?",
             "SKUs below reorder point", len(t.low()), "agg:low_stock_count", "low_stock",
             also=("reorder_suggestion",))
        item("ag-13", "What is the total shortfall across all SKUs below their reorder point?",
             "total shortfall", sum(p.shortfall for p in t.low()), "agg:total_shortfall",
             "low_stock")
        item("ag-14", "How many suppliers do we have on file?", "suppliers",
             len(t.suppliers), "agg:supplier_count", "supplier_compare")
        item("ag-15", "Before subtracting reservations, how many units do we hold in all three "
             "warehouses combined?", "units on hand", t.stock("on_hand"),
             "agg:total_on_hand", "inventory_lookup", tags=("paraphrase",))

        self.variant("ag-12", "ag-zh-01", "目前有多少个 SKU 低于再订货点？")
        self.variant("ag-01", "ag-zh-02", "东仓的可用库存总量是多少？")
        self.variant("ag-05", "ag-zh-03", "状态为已发货的采购单有多少张？")

    def formulas(self) -> None:
        t = self.t
        low_priced = [s for s in self.low_ids if t.positions[s].unit_cost is not None
                      and t.suggested(s) >= MIN_COUNT]
        healthy = [s for s in self.spare + self.packaging
                   if s not in self.low_ids and t.suggested(s) >= MIN_COUNT]
        all_low_qty = sum(t.suggested(s) for s in self.low_ids)
        all_low_cost = t.standard_cost(self.low_ids)
        plan = t.plan_lines()
        plan_cost = t.plan_cost()

        def qty_item(item_id: str, sku: str, text: str, tags: tuple[str, ...] = ()) -> None:
            q = text.format(sku=sku)
            self.add(id=item_id, text=q, lang="en", category="formula", answerable=True,
                     expected=(self.num("suggested order quantity", t.suggested(sku), q,
                                        locator="agg:total_suggested_qty",
                                        scope=[all_low_qty]),),
                     expected_tool="reorder_suggestion", expected_params={"sku_id": sku},
                     tags=tags)

        qty_item("fm-01", self.pick(low_priced), "How much should we reorder for {sku}?")
        qty_item("fm-02", self.pick(low_priced), "What order quantity is suggested for {sku}?")
        qty_item("fm-03", self.pick(healthy), "What is the suggested order quantity for {sku}?")
        qty_item("fm-04", self.pick(healthy), "Given its demand and lead time, how many units of "
                 "{sku} would you suggest ordering?", tags=("paraphrase",))
        qty_item("fm-05", self.pick(low_priced), "If we restock {sku} today, what quantity "
                 "does the replenishment formula give?", tags=("paraphrase",))

        sku = self.pick(low_priced)
        q = f"What would the suggested reorder of {sku} cost at standard unit cost?"
        self.add(id="fm-06", text=q, lang="en", category="formula", answerable=True,
                 expected=(self.num("estimated cost", t.standard_cost([sku]), q,
                                    locator="agg:estimated_cost", scope=[all_low_cost]),),
                 expected_tool="reorder_suggestion", expected_params={"sku_id": sku})

        sku = self.pick(low_priced)
        q = (f"How much should we order of {sku}, and what would that order cost at standard "
             "unit cost?")
        self.add(id="fm-07", text=q, lang="en", category="formula", answerable=True,
                 expected=(self.num("suggested order quantity", t.suggested(sku), q,
                                    locator="agg:total_suggested_qty", scope=[all_low_qty]),
                           self.num("estimated cost", t.standard_cost([sku]), q,
                                    locator="agg:estimated_cost", scope=[all_low_cost])),
                 expected_tool="reorder_suggestion", expected_params={"sku_id": sku})

        q = "What is the total suggested order quantity across all low-stock SKUs?"
        self.add(id="fm-08", text=q, lang="en", category="formula", answerable=True,
                 expected=(self.num("total suggested quantity", all_low_qty, q,
                                    locator="agg:total_suggested_qty"),),
                 expected_tool="reorder_suggestion", also_accepted=("reorder_plan",))
        q = ("At standard unit cost, what would the suggested orders for all low-stock SKUs cost "
             "in total?")
        self.add(id="fm-09", text=q, lang="en", category="formula", answerable=True,
                 expected=(self.num("estimated cost", all_low_cost, q,
                                    locator="agg:estimated_cost", scope=[plan_cost]),),
                 expected_tool="reorder_suggestion", tags=("method_distractor",),
                 note="distractor: the reorder plan's supplier-priced estimate")
        q = "How many SKUs would the full reorder plan include?"
        self.add(id="fm-10", text=q, lang="en", category="formula", answerable=True,
                 expected=(self.num("SKUs in the plan", len(plan), q, locator="agg:sku_count"),),
                 expected_tool="reorder_plan", also_accepted=("reorder_suggestion",),
                 note="reorder_suggestion's skus_to_order counts the same SKUs by definition")
        q = ("What is the estimated spend of the full reorder plan, pricing each SKU at its "
             "chosen supplier?")
        self.add(id="fm-11", text=q, lang="en", category="formula", answerable=True,
                 expected=(self.num("plan spend", plan_cost, q, locator="agg:estimated_cost",
                                    scope=[all_low_cost]),),
                 expected_tool="reorder_plan", tags=("method_distractor",),
                 note="distractor: the standard-cost estimate of reorder_suggestion")

        self.variant("fm-01", "fm-zh-01",
                     f"{self.by_id['fm-01'].expected_params['sku_id']} 建议补货多少？")
        self.variant("fm-06", "fm-zh-02",
                     f"按标准单位成本计算，{self.by_id['fm-06'].expected_params['sku_id']} "
                     "的建议补货要花多少钱？")

    def rankings(self) -> None:
        t = self.t
        candidates = self.ranking_skus()
        overall_top = t.ranked()[0]

        def best_item(item_id: str, sku: str, text: str) -> None:
            q = text.format(sku=sku)
            top = t.ranked_for(sku)[0]
            self.add(id=item_id, text=q, lang="en", category="ranking", answerable=True,
                     expected=(self.num("top rating", top["rating"], q, locator="agg:top_rating",
                                        scope=[overall_top["rating"]]),),
                     expected_text=(self.supplier_text(top["supplier_id"], "top:supplier_id"),),
                     expected_tool="supplier_compare", expected_params={"sku_id": sku})

        best_item("rk-01", self.pick(candidates),
                  "Which supplier is rated highest for {sku}, and what is its rating?")
        best_item("rk-02", self.pick(candidates),
                  "Who is the best supplier for {sku}? Give me its rating.")
        best_item("rk-03", self.pick(candidates), "Which supplier of {sku} has the best rating?")

        q = "Which supplier has the highest rating overall, and what is that rating?"
        self.add(id="rk-04", text=q, lang="en", category="ranking", answerable=True,
                 expected=(self.num("top rating", overall_top["rating"], q,
                                    locator="agg:top_rating"),),
                 expected_text=(self.supplier_text(overall_top["supplier_id"],
                                                   "top:supplier_id"),),
                 expected_tool="supplier_compare")

        def fastest(suppliers: list[dict]) -> dict | None:
            timed = [s for s in suppliers if s.get("avg_lead_time_days") is not None]
            timed.sort(key=lambda s: s["avg_lead_time_days"])
            if len(timed) >= 2 and timed[0]["avg_lead_time_days"] < timed[1]["avg_lead_time_days"]:
                return timed[0]
            return None

        own = [s for s in self.spare + self.packaging
               if (f := fastest(t.ranked_for(s))) is not None
               and f["supplier_id"] != t.ranked_for(s)[0]["supplier_id"]]
        sku = self.pick(own)
        quick = fastest(t.ranked_for(sku))
        q = (f"Among the suppliers of {sku}, which one has the shortest average lead time, and "
             "how many days is it?")
        self.add(id="rk-05", text=q, lang="en", category="ranking", answerable=True,
                 expected=(self.num("shortest lead time (days)", quick["avg_lead_time_days"], q,
                                    check=f"cell:avg_lead_time_days@supplier_id="
                                          f"{quick['supplier_id']}"),),
                 expected_text=(self.supplier_text(quick["supplier_id"], None),),
                 expected_tool="supplier_compare", expected_params={"sku_id": sku},
                 tags=("row_answer",), note="the tool ranks by rating, not lead time")

        quick_all = fastest(t.ranked())
        if quick_all is None:
            raise RuntimeError("no unique fastest supplier in the seeded data")
        q = "Which supplier has the shortest average lead time overall, and how many days is it?"
        self.add(id="rk-06", text=q, lang="en", category="ranking", answerable=True,
                 expected=(self.num("shortest lead time (days)", quick_all["avg_lead_time_days"],
                                    q, check=f"cell:avg_lead_time_days@supplier_id="
                                             f"{quick_all['supplier_id']}"),),
                 expected_text=(self.supplier_text(quick_all["supplier_id"], None),),
                 expected_tool="supplier_compare", tags=("row_answer",),
                 note="the tool ranks by rating, not lead time")

        short = sorted(t.low(), key=lambda p: (-p.shortfall, p.sku_id))
        if len(short) < 2 or short[0].shortfall == short[1].shortfall:
            raise RuntimeError("no unique largest shortfall in the seeded data")
        q = ("Which SKU below its reorder point has the largest shortfall, and how many units "
             "short is it?")
        self.add(id="rk-07", text=q, lang="en", category="ranking", answerable=True,
                 expected=(self.num("largest shortfall", short[0].shortfall, q,
                                    check=f"cell:shortfall@sku_id={short[0].sku_id}"),),
                 expected_text=(ExpectedText(name="sku", alternatives=(short[0].sku_id,)),),
                 expected_tool="low_stock", tags=("row_answer",),
                 note="low_stock sorts by criticality first, so the answer is not the top row")

        late = t.late()
        if len(late) < 2 or late[0]["days_late"] == late[1]["days_late"]:
            raise RuntimeError("no unique most-overdue order in the seeded data")
        q = "Which late purchase order is the most overdue, and by how many days?"
        self.add(id="rk-08", text=q, lang="en", category="ranking", answerable=True,
                 expected=(self.num("days overdue", late[0]["days_late"], q,
                                    locator="agg:oldest_days_late"),),
                 expected_text=(ExpectedText(name="purchase order",
                                             alternatives=(late[0]["po_id"],),
                                             locator="top:po_id"),),
                 expected_tool="late_shipments")

        by_qty = sorted(late, key=lambda o: (-(o.get("qty") or 0), o["po_id"]))
        if by_qty[0]["qty"] == by_qty[1]["qty"]:
            raise RuntimeError("no unique largest late order in the seeded data")
        q = ("Which late purchase order has the largest quantity still outstanding, and how "
             "many units is it?")
        self.add(id="rk-09", text=q, lang="en", category="ranking", answerable=True,
                 expected=(self.num("late order quantity", by_qty[0]["qty"], q,
                                    check=f"cell:qty@po_id={by_qty[0]['po_id']}"),),
                 expected_text=(ExpectedText(name="purchase order",
                                             alternatives=(by_qty[0]["po_id"],)),),
                 expected_tool="late_shipments", tags=("row_answer",),
                 note="late_shipments sorts by days late, not quantity")

        rk01 = self.by_id["rk-01"].expected_params
        self.variant("rk-01", "rk-zh-01", f"{rk01['sku_id']} 哪个供应商评分最高？评分是多少？")
        self.variant("rk-08", "rk-zh-02", "逾期最久的采购单是哪一张？逾期了多少天？")

    def multi_conditions(self) -> None:
        t = self.t
        late = t.late()

        # --- filters the tools accept
        busy = Counter(o["supplier_id"] for o in late)
        pairs = sorted({(o["supplier_id"], o["sku_id"]) for o in late
                        if busy[o["supplier_id"]] >= 2 and (o.get("qty") or 0) >= MIN_COUNT})
        sup, sku = self.rng.choice([p for p in pairs if p[1] not in self.used])
        self.used.add(sku)
        q = f"What is the total late quantity from {sup} for {sku}?"
        self.add(id="mc-01", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("late quantity", t.qty(t.late(sku=sku, supplier=sup)), q,
                                    locator="agg:late_qty",
                                    scope=[t.qty(t.late(supplier=sup)), t.qty(t.late(sku=sku)),
                                           t.qty(late)]),),
                 expected_tool="late_shipments",
                 expected_params={"sku_id": sku, "supplier_id": sup})

        open_by_sup = {s["supplier_id"]: t.qty(t.orders_where(supplier=s["supplier_id"],
                                                              status="open"))
                       for s in t.suppliers}
        with_open = sorted(s for s, v in open_by_sup.items() if v >= MIN_COUNT)
        sup_open = self.rng.choice(with_open)
        q = f"What is the total quantity on open-status purchase orders from {sup_open}?"
        self.add(id="mc-02", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("open quantity of the supplier", open_by_sup[sup_open], q,
                                    locator="agg:total_qty",
                                    scope=[t.qty(t.orders_where(supplier=sup_open)),
                                           t.qty(t.orders_where(status="open"))]),),
                 expected_tool="po_status",
                 expected_params={"supplier_id": sup_open, "status": "open"})

        shipped = [o["sku_id"] for o in t.orders_where(status="shipped")
                   if t.qty(t.orders_where(sku=o["sku_id"], status="shipped")) >= MIN_COUNT
                   and len(t.orders_where(sku=o["sku_id"])) >= 2]
        sku = self.pick(shipped)
        q = f"What quantity is on shipped purchase orders for {sku}?"
        self.add(id="mc-03", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("shipped quantity of the SKU",
                                    t.qty(t.orders_where(sku=sku, status="shipped")), q,
                                    locator="agg:total_qty",
                                    scope=[t.qty(t.orders_where(sku=sku)),
                                           t.qty(t.orders_where(status="shipped"))]),),
                 expected_tool="po_status", expected_params={"sku_id": sku, "status": "shipped"})

        q = "How many class B SKUs are below their reorder point?"
        self.add(id="mc-04", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("class B SKUs below reorder point",
                                    len(t.low(criticality="B")), q,
                                    locator="agg:low_stock_count", scope=[len(t.low())]),),
                 expected_tool="low_stock", expected_params={"criticality": "B"})

        # --- the item-category filter: the tools accept ``category`` since the 2026-10-26
        # changelog entry (bench/README.md); until then these five were beyond_params items
        note = "the tool filters by item category (category param, changelog 2026-10-26)"
        q = "How many late purchase orders are for packaging SKUs?"
        self.add(id="mc-05", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("late orders for packaging",
                                    len(t.late(category="packaging")), q,
                                    locator="agg:late_count", scope=[len(late)]),),
                 expected_tool="late_shipments", expected_params={"category": "packaging"},
                 note=note)
        q = "How many late purchase orders are for spare-part SKUs?"
        self.add(id="mc-06", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("late orders for spare parts",
                                    len(t.late(category="spare_part")), q,
                                    locator="agg:late_count", scope=[len(late)]),),
                 expected_tool="late_shipments", expected_params={"category": "spare_part"},
                 note=note)
        q = "How many packaging SKUs are below their reorder point?"
        # reorder_suggestion without a SKU covers exactly the low-stock SKUs (as in ag-12), now
        # within the category too
        self.add(id="mc-07", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("packaging SKUs below reorder point",
                                    len(t.low(category="packaging")), q,
                                    locator="agg:low_stock_count", scope=[len(t.low())]),),
                 expected_tool="low_stock", expected_params={"category": "packaging"},
                 also_accepted=("reorder_suggestion",), note=note)
        q = "How many class C spare-part SKUs are below their reorder point?"
        self.add(id="mc-08", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("class C spare parts below reorder point",
                                    len(t.low(criticality="C", category="spare_part")), q,
                                    locator="agg:low_stock_count",
                                    scope=[len(t.low(criticality="C")),
                                           len(t.low(category="spare_part")), len(t.low())]),),
                 expected_tool="low_stock",
                 expected_params={"criticality": "C", "category": "spare_part"}, note=note)
        q = "What is the total shortfall of spare-part SKUs below their reorder point?"
        self.add(id="mc-09", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("spare-part shortfall",
                                    sum(p.shortfall for p in t.low(category="spare_part")), q,
                                    locator="agg:total_shortfall",
                                    scope=[sum(p.shortfall for p in t.low())]),),
                 expected_tool="low_stock", expected_params={"category": "spare_part"},
                 note=note)

        # --- filters no tool accepts (added 2026-10-26, see the changelog): the answer needs the
        # closest call's rows filtered again, so a correct system says it cannot filter instead
        # of showing the unfiltered figure. The class is the one with the most late orders.
        beyond = ("beyond_params",)
        crit_of = {sku: p.criticality for sku, p in t.positions.items()}
        by_class = Counter(crit_of[o["sku_id"]] for o in late if crit_of[o["sku_id"]])
        level = min(by_class, key=lambda c: (-by_class[c], c))
        q = f"How many late purchase orders are for class {level} SKUs?"
        self.add(id="mc-10", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num(f"late orders for class {level}",
                                    sum(1 for o in late if crit_of[o["sku_id"]] == level), q,
                                    scope=[len(late)]),),
                 expected_tool="late_shipments", tags=beyond,
                 note="late_shipments cannot filter by criticality; it returns every class")
        q = "How many of the late purchase orders are already shipped?"
        self.add(id="mc-11", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("late orders already shipped",
                                    sum(1 for o in late if o["status"] == "shipped"), q,
                                    scope=[len(late)]),),
                 expected_tool="late_shipments", tags=beyond,
                 note="late_shipments cannot filter by status; it returns open and shipped")
        class_b = sorted(s for s, c in crit_of.items() if c == "B")
        q = "What is the total on-hand quantity of class B SKUs across all warehouses?"
        self.add(id="mc-12", text=q, lang="en", category="multi_condition", answerable=True,
                 expected=(self.num("units on hand of class B",
                                    sum(t.stock("on_hand", sku=s) for s in class_b), q,
                                    scope=[t.stock("on_hand")]),),
                 expected_tool="inventory_lookup", tags=beyond,
                 note="inventory_lookup cannot filter by criticality; it returns every class")

        self.variant("mc-07", "mc-zh-01", "包装类 SKU 中有多少个低于再订货点？")
        self.variant("mc-02", "mc-zh-02", f"{sup_open} 状态为未结的采购单总数量是多少？")
        self.variant("mc-10", "mc-zh-03", f"{level}类 SKU 的逾期采购单有多少张？")

    def unanswerables(self) -> None:
        t = self.t
        if t.orders_where(po=MISSING_PO) or MISSING_SKU in t.positions:
            raise RuntimeError("the 'missing' ids exist in the seeded data")
        sku_a = self.pick(self.spare)
        sku_b = self.pick(self.packaging)
        sku_c = self.pick(self.spare + self.packaging)
        rows = (
            ("un-01", f"What will demand for {sku_a} be next quarter?", "out_of_scope",
             "forecasting is out of scope; only average daily demand is stored"),
            ("un-02", "What was our total sales revenue last month?", "out_of_scope",
             "there is no sales data"),
            ("un-03", f"How many units of {sku_b} did we sell last week?", "out_of_scope",
             "there is no sales history"),
            ("un-04", f"What is the profit margin on {sku_c}?", "out_of_scope",
             "there are no selling prices"),
            ("un-05", "How many employees work in WH-EAST?", "out_of_scope",
             "there is no staffing data"),
            ("un-06", f"What is the status of {MISSING_PO}?", "nonexistent",
             "no such purchase order"),
            ("un-07", f"How much stock of {MISSING_SKU} do we have?", "nonexistent",
             "no such SKU"),
            ("un-08", "What's the status of the order?", "ambiguous", "no order is named"),
        )
        for item_id, text, kind, note in rows:
            self.add(id=item_id, text=text, lang="en", category="unanswerable",
                     answerable=False, tags=(kind,), note=note)
        self.variant("un-01", "un-zh-01", f"预测一下 {sku_a} 下个季度的需求量。")
        self.variant("un-02", "un-zh-02", "上个月我们的销售收入是多少？")


def build_questions(world: World, *, rng_seed: int = RNG_SEED) -> list[Item]:
    """The full question set for a seeded world. Pure function of the world's data."""
    truth = Truth(world.store, world.today)
    builder = _Builder(truth, random.Random(f"twotrack-bench:{rng_seed}"), example_ids(world))
    builder.lookups()
    builder.aggregations()
    builder.formulas()
    builder.rankings()
    builder.multi_conditions()
    builder.unanswerables()
    examples = routing_examples(world)
    for item in builder.items:
        if normalize(item.text) in examples:
            raise RuntimeError(f"{item.id} repeats a routing example verbatim")
    return builder.items


def question_digest(items: Sequence[Item]) -> str:
    blob = json.dumps([i.model_dump(mode="json") for i in items], sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def composition(items: Sequence[Item]) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    for item in items:
        row = table.setdefault(item.category, {"en": 0, "zh": 0, "total": 0})
        row[item.lang] += 1
        row["total"] += 1
    return {c: table[c] for c in CATEGORIES if c in table}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.questions",
                                     description="Print the benchmark question set.")
    parser.add_argument("--jsonl", action="store_true", help="dump every item as JSON lines")
    args = parser.parse_args(argv)
    world = build_world()
    try:
        items = build_questions(world)
    finally:
        world.close()
    if args.jsonl:
        for item in items:
            sys.stdout.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return 0
    print(f"{len(items)} questions, digest {question_digest(items)}")
    print(f"{'category':<16}{'en':>4}{'zh':>4}{'total':>7}")
    for category, row in composition(items).items():
        print(f"{category:<16}{row['en']:>4}{row['zh']:>4}{row['total']:>7}")
    unanswerable = sum(1 for i in items if not i.answerable)
    print(f"answerable {len(items) - unanswerable}, unanswerable {unanswerable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
