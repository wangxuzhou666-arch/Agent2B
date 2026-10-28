"""Whether a request excludes a value it names (core/routing/exclusion.py), and the extractor that
never takes an excluded id as a param (plugins/supply_chain/extract.py).

``excluded_at`` reads one value: an exclusion marker right before it, with only connecting words,
the words naming its kind and the values of a list in between. ``excludes`` reads the request:
an identifier anywhere after a marker in its clause, so it is the wider of the two on purpose
(a write such a request names is confirmed before its approval, rule_arm)."""

import pytest

from twotrack.core.routing import excluded_at, excludes
from twotrack.plugins.supply_chain import extract

SUPPLIER = ("supplier", "vendor", "供应商")


def excluded(text: str, value: str, kinds=SUPPLIER) -> bool:
    assert value in text, (value, text)
    return excluded_at(text, text.index(value), kinds)


# ---------------------------------------------------------------- excluded_at


@pytest.mark.parametrize("text, value", [
    # Chinese markers, right before the value or with a verb of using or choosing between
    ("不要用 SUP-003", "SUP-003"),
    ("别用 SUP-003", "SUP-003"),
    ("别选 SUP-006", "SUP-006"),
    ("不用 SUP-004 的货", "SUP-004"),
    ("除了 SUP-002 都行", "SUP-002"),
    ("排除 SUP-005", "SUP-005"),
    ("不选 SUP-003，用 SUP-001", "SUP-003"),
    ("不要从 SUP-002 进货", "SUP-002"),
    ("不包括 SUP-002", "SUP-002"),
    ("不从 SUP-003 进货", "SUP-003"),
    # every negator is a marker too, the prohibitions among them
    ("禁止用 SUP-003", "SUP-003"),
    ("不准选 SUP-006", "SUP-006"),
    ("不许用 SUP-004 的货", "SUP-004"),
    # the words naming the value's kind
    ("不要用供应商 SUP-006", "SUP-006"),
    ("excluding supplier SUP-006", "SUP-006"),
    ("not from vendors SUP-002", "SUP-002"),
    # English markers: whole words, "not" contracted, and the phrases
    ("not from SUP-002", "SUP-002"),
    ("but not with SUP-002", "SUP-002"),
    ("and don't use SUP-002", "SUP-002"),
    ("and don’t use the SUP-002", "SUP-002"),
    ("from any supplier except SUP-004", "SUP-004"),
    ("with SUP-001 rather than SUP-003", "SUP-003"),
    ("use SUP-005 instead of SUP-002", "SUP-002"),
    ("anyone other than SUP-001", "SUP-001"),
    ("an order without SUP-005", "SUP-005"),
    ("and avoid SUP-006", "SUP-006"),
    ("avoid using vendor SUP-006", "SUP-006"),
    # "n't" excludes a value through a verb of using or choosing between them
    ("we aren't using SUP-002", "SUP-002"),
    ("don't choose SUP-002", "SUP-002"),
    ("please don't pick vendor SUP-002", "SUP-002"),
    ("we can't select SUP-002", "SUP-002"),
    # every value of an excluded list
    ("except SUP-003 and SUP-004", "SUP-003"),
    ("except SUP-003 and SUP-004", "SUP-004"),
    ("除了 SUP-002、SUP-004", "SUP-004"),
    ("除了 SUP-002 和 SUP-004", "SUP-004"),
])
def test_a_marker_right_before_a_value_excludes_it(text, value):
    assert excluded(text, value)


@pytest.mark.parametrize("text, value", [
    ("place an order from SUP-004", "SUP-004"),
    ("with SUP-001 rather than SUP-003", "SUP-001"),  # the one that is wanted
    ("use SUP-005 instead of SUP-002", "SUP-005"),
    ("不选 SUP-003，用 SUP-001", "SUP-001"),  # another clause
    # a marker further away negates something else: the value is what the request asks about
    ("late shipments that haven't arrived from SUP-006", "SUP-006"),
    ("show overdue orders we did not get from SUP-003", "SUP-003"),
    ("don't cancel order SUP-003", "SUP-003"),
    ("不需要给 SUP-003 下单", "SUP-003"),
    # "n't" right before the value, no verb of using or choosing between: it opens a question or
    # negates its own verb, and the value is what the request asks about
    ("didn't SUP-003 deliver late last month?", "SUP-003"),
    ("didn’t SUP-003 deliver late last month?", "SUP-003"),
    ("doesn't SKU-10042 have stock?", "SKU-10042"),
    # 别 inside a listed word is no marker
    ("别的供应商 SUP-003 也行", "SUP-003"),
    ("特别用 SUP-003", "SUP-003"),
    # whole words only
    ("nothing from SUP-003", "SUP-003"),
    ("notify SUP-003", "SUP-003"),
    ("avoiding SUP-003 was a mistake", "SUP-003"),
])
def test_anything_else_leaves_a_value_alone(text, value):
    assert not excluded(text, value)


def test_kinds_are_the_callers():
    assert excluded("不要用供应商 SUP-006", "SUP-006")
    assert not excluded("不要用供应商 SUP-006", "SUP-006", kinds=())
    assert excluded("except warehouse WH-EAST", "WH-EAST", kinds=("warehouse",))
    assert not excluded("except warehouse WH-EAST", "WH-EAST", kinds=SUPPLIER)


# ---------------------------------------------------------------- excludes


@pytest.mark.parametrize("text", [
    "给 SKU-10042 下单 50 件，不要用 SUP-003",
    "place an order for 40 units of SKU-10233, not from SUP-002",
    "place an order for 60 units of SKU-10057 with SUP-001 rather than SUP-003",
    "除了 SUP-002 都行，给 SKU-10036 下单 45 件",
    "给 SKU-10233 下单 60 件，不包括 SUP-002",
    "给 SKU-10057 下单 30 件，不从 SUP-003 进货",
    "place an order for 40 units of SKU-10061 without SUP-005",
    "place an order for 70 units of SKU-10019, avoid SUP-006",
    "except WH-EAST",  # capitals joined by a hyphen
    # wider on purpose: a negator before any identifier of its clause
    "don't order from SUP-002",
    "不用担心 SUP-003 的交期",
    "never raise a PO for 25 units",
    # a contraction too, whether or not a verb of using or choosing follows it
    "didn't SUP-003 deliver late last month?",
    "a supplier that isn't SUP-002",
])
def test_an_identifier_after_a_marker_in_its_clause_excludes(text):
    assert excludes(text)


@pytest.mark.parametrize("text", [
    "place an order for 35 units of SKU-10019 from SUP-004, not urgent",  # before the marker
    "rather than wait, place an order for 40 units of SKU-10023",  # another clause
    "先不选供应商了，给 SKU-10088 下单 60 件",  # nothing after the marker is an identifier
    "别的供应商也行，给 SKU-10140 下单 40 件",  # 别 inside 别的
    "给 SKU-10042 下单 50 件",
    "not the usual one",  # words, no identifier
])
def test_no_identifier_after_a_marker_excludes_nothing(text):
    assert not excludes(text)


# ---------------------------------------------------------------- the extractor


@pytest.mark.parametrize("query, expected", [
    ("给 SKU-10042 下单 50 件，不要用 SUP-003", {"sku_id": "SKU-10042", "qty": 50}),
    ("place an order for 40 units of SKU-10233, not from SUP-002",
     {"sku_id": "SKU-10233", "qty": 40}),
    ("给 SKU-10061 下单 70 件，不选 SUP-003，用 SUP-001",
     {"sku_id": "SKU-10061", "qty": 70, "supplier_id": "SUP-001"}),
    ("use SUP-005 instead of SUP-002 and create a PO for 80 units of SKU-10061",
     {"sku_id": "SKU-10061", "qty": 80, "supplier_id": "SUP-005"}),
    ("except SUP-003 and SUP-004, order from SUP-001",
     {"supplier_id": "SUP-001"}),
    ("给 SKU-10057 下单 30 件，不要用供应商 SUP-006", {"sku_id": "SKU-10057", "qty": 30}),
    ("给 SKU-10233 下单 60 件，不包括 SUP-002", {"sku_id": "SKU-10233", "qty": 60}),
    ("给 SKU-10057 下单 30 件，不从 SUP-003 进货", {"sku_id": "SKU-10057", "qty": 30}),
    ("place an order for 40 units of SKU-10061 without SUP-005",
     {"sku_id": "SKU-10061", "qty": 40}),
    ("place an order for 70 units of SKU-10019, avoid SUP-006",
     {"sku_id": "SKU-10019", "qty": 70}),
    # still a param: the marker negates another word
    ("late shipments that haven't arrived from SUP-006", {"supplier_id": "SUP-006"}),
    ("didn't SUP-003 deliver late last month?", {"supplier_id": "SUP-003"}),
    ("不需要给 SKU-10019 下单 35 件", {"sku_id": "SKU-10019", "qty": 35}),
])
def test_an_excluded_supplier_is_never_a_param(query, expected):
    names = ("sku_id", "supplier_id", "qty")
    assert extract.extractor(*names)(query) == expected


def test_every_id_param_skips_what_the_query_excludes():
    assert extract.sku_id("not SKU-10042, SKU-10043 instead") == "SKU-10043"
    assert extract.sku_id("除了 SKU-10042") is None
    assert extract.po_id("except PO-20015 and PO-20016, cancel PO-20017") == "PO-20017"
    assert extract.po_id("除了采购单 PO-20015，都取消") is None  # its kind, 采购单, in between
    assert extract.supplier_id("excluding vendor SUP-006") is None
    assert extract.warehouse("not from WH-EAST, from WH-WEST") == "WH-WEST"
    assert extract.warehouse("except the east warehouse") is None
    assert extract.warehouse("除了东仓，从西仓调") == "WH-WEST"
    assert extract.warehouse("除了华东仓都行") is None  # the whole name, not its 东仓


def test_without_an_exclusion_the_extractor_is_unchanged():
    assert extract.supplier_id("PO-20015 from SUP-007") == "SUP-007"
    assert extract.sku_id("查询SKU-10042库存") == "SKU-10042"
    assert extract.warehouse("西仓还有多少") == "WH-WEST"
    assert extract.warehouse("华东仓还有多少") == "WH-EAST"
    assert extract.warehouse("东仓和西仓") == "WH-EAST"  # the table's order, as before
    assert extract.warehouse("sku-77 in wh-central") == "WH-CENTRAL"
