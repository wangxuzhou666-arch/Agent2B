"""Deterministic parameter extraction from a raw query (no model involved).

Each extractor returns only the keys it found; the pipeline validates the result against the
tool's params model, so a wrong guess fails validation instead of running. Identifiers are
matched with explicit non-alphanumeric guards rather than ``\\b`` so they are found inside
Chinese text too ("查询SKU-10042库存").

A SKU, a PO, a supplier or a warehouse that the query excludes is never a param: each is the
first mention that no exclusion rules out (``core.routing.excluded_at``). "下单 50 件，不要用
SUP-003" names no supplier, and in "not SUP-002, use SUP-005" the supplier is SUP-005. The words
that name an id's kind may stand between the exclusion and the id ("excluding supplier SUP-006",
不要用供应商 SUP-006).
"""

import re
from collections.abc import Callable, Iterable
from typing import Any

from twotrack.core.routing import excluded_at

from .entities import WAREHOUSE_NAMES

_EDGE = r"(?<![A-Za-z0-9])"
_SKU = re.compile(_EDGE + r"SKU-?(\d+)", re.IGNORECASE)
_PO = re.compile(_EDGE + r"PO-(\d+)", re.IGNORECASE)
_SUP = re.compile(_EDGE + r"SUP-(\d+)", re.IGNORECASE)

_WAREHOUSE_CODE = re.compile(_EDGE + r"WH-(EAST|WEST|CENTRAL)(?![A-Za-z])", re.IGNORECASE)
_WAREHOUSE_WORD = re.compile(r"\b(east|west|central)\s+(?:warehouse|wh|site|dc)\b",
                             re.IGNORECASE)
# the Chinese warehouse names are the warehouse field's value synonyms (one table for both);
# the longest first, so a mention starts where its longest name does (华东仓, not its 东仓)
_WAREHOUSE_CJK = tuple(
    (code, re.compile("|".join(map(re.escape, sorted(names["zh"], key=len, reverse=True)))))
    for code, names in WAREHOUSE_NAMES.items())

# the words that name each kind of id (excluded_at)
_SKU_KINDS = ("sku", "item", "物料")
_PO_KINDS = ("po", "purchase order", "order", "采购单", "订单")
_SUPPLIER_KINDS = ("supplier", "vendor", "供应商")
_WAREHOUSE_KINDS = ("warehouse", "wh", "site", "dc", "仓库")

_QTY_UNIT = re.compile(r"(?<![A-Za-z0-9.-])(\d{1,3}(?:,\d{3})+|\d+)\s*"
                       r"(?:units?|pcs|pieces?|ea|each|件|个|只|箱)(?![A-Za-z])", re.IGNORECASE)
_QTY_WORD = re.compile(r"(?:qty|quantity|数量)\s*[:=：]?\s*(\d{1,3}(?:,\d{3})+|\d+)",
                       re.IGNORECASE)

_CRIT_WORD = re.compile(r"\b(?:class|criticality|tier)\s*([ABC])\b", re.IGNORECASE)
_CRIT_TAG = re.compile(r"(?<![A-Za-z])([ABC])(?:-class\b|\s+class\b|\s*类)")

_STATUS_WORDS = (
    ("cancelled", r"\bcancell?ed\b|已取消"),
    ("received", r"\breceived\b|\bdelivered\b|已收货|已到货"),
    ("shipped", r"\bshipped\b|\bin transit\b|已发货|在途"),
    ("draft", r"\bdrafts?\b|草稿"),
    ("open", r"\bopen\b|未结|待发货"),
)
_STATUS = [(status, re.compile(pattern, re.IGNORECASE)) for status, pattern in _STATUS_WORDS]


def _first(pattern: re.Pattern[str], query: str, kinds: Iterable[str]) -> re.Match[str] | None:
    """The first match of ``pattern`` in ``query`` that the query does not exclude."""
    return next((m for m in pattern.finditer(query)
                 if not excluded_at(query, m.start(), kinds)), None)


def sku_id(query: str) -> str | None:
    m = _first(_SKU, query, _SKU_KINDS)
    return f"SKU-{m.group(1)}" if m else None


def po_id(query: str) -> str | None:
    m = _first(_PO, query, _PO_KINDS)
    return f"PO-{m.group(1)}" if m else None


def supplier_id(query: str) -> str | None:
    m = _first(_SUP, query, _SUPPLIER_KINDS)
    return f"SUP-{m.group(1)}" if m else None


def warehouse(query: str) -> str | None:
    m = (_first(_WAREHOUSE_CODE, query, _WAREHOUSE_KINDS)
         or _first(_WAREHOUSE_WORD, query, _WAREHOUSE_KINDS))
    if m:
        return f"WH-{m.group(1).upper()}"
    for code, names in _WAREHOUSE_CJK:
        if _first(names, query, _WAREHOUSE_KINDS):
            return code
    return None


def qty(query: str) -> int | None:
    m = _QTY_UNIT.search(query) or _QTY_WORD.search(query)
    return int(m.group(1).replace(",", "")) if m else None


def criticality(query: str) -> str | None:
    m = _CRIT_WORD.search(query) or _CRIT_TAG.search(query)
    return m.group(1).upper() if m else None


def status(query: str) -> str | None:
    found = [(m.start(), name) for name, rx in _STATUS if (m := rx.search(query))]
    return min(found)[1] if found else None


FIELDS: dict[str, Callable[[str], Any]] = {
    "sku_id": sku_id, "po_id": po_id, "supplier_id": supplier_id, "warehouse": warehouse,
    "qty": qty, "criticality": criticality, "status": status,
}


def extractor(*names: str) -> Callable[[str], dict[str, Any]]:
    """A param_extractor that fills ``names`` from the query, skipping what it cannot find."""
    unknown = [n for n in names if n not in FIELDS]
    if unknown:
        raise ValueError(f"no extractor for {unknown}")

    def extract(query: str) -> dict[str, Any]:
        found = {name: FIELDS[name](query) for name in names}
        return {k: v for k, v in found.items() if v is not None}

    extract.__name__ = f"extract_{'_'.join(names)}"
    return extract
