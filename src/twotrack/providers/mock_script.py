"""The mock model's fixed script for the documented exploration case (docs/EVIDENCE.md).

With no key the mock model plans nothing: ``MockProvider.agent_step`` says that no model is
configured. The one exception is the exploration case, a request whose wording is
``EXPLORATION_CASE`` (with any supplier and SKU ids). For that request ``script_step`` returns
the next turn of a fixed script, so a stack with no key shows the exploration track end to end:
read calls through the gate, then a write that stops at a one-time approval. It is a script for
one request, not a planner, and it shows nothing about how well any model explores.

It works only from what a model receives, never from the store: the request text and the tool
results in the conversation, which are fenced narrator views (aggregates, row count, columns,
scope, message), never rows. Every value it sends is copied from one of them:

=====  ================================================  =========================================
turn   calls                                             where each value comes from
=====  ================================================  =========================================
1      ``late_shipments {supplier_id, sku_id}``          both ids as the request names them
2      ``reorder_suggestion {sku_id}``,                  the request's SKU
       ``supplier_scorecard {}``
3      ``create_po {sku_id, qty, supplier_id}``          ``qty``: the larger of late_shipments'
                                                         ``late_qty`` and reorder_suggestion's
                                                         ``total_suggested_qty`` (a choice between
                                                         two computed figures, never a sum);
                                                         ``supplier_id``: the first supplier the
                                                         scorecard's message names that is not the
                                                         late one, left out when none came back
=====  ================================================  =========================================

It ends with a fixed sentence (``mock_script_end``) instead when late_shipments failed or found
nothing late, when neither figure for ``qty`` is positive, and on any turn after the third (the
proposal was refused, so the run went on). It does not read the tool list it is offered: a call
to a tool the run does not expose (a viewer's ``create_po``) is the gate's to refuse, which is
part of what the case shows.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from twotrack.core.i18n import detect_language, say

from .base import AgentStep, ChatMessage, ToolCall

EXPLORATION_CASE = ("Several orders from SUP-001 are slipping, SKU-10053 among them. "
                    "Work out what that puts at risk and propose what to do.")
# ASCII, so that \d is 0-9 and the case folding is a-z only: without it a fullwidth or Arabic-Indic
# digit is an id, and a dotless i, a long s or the Kelvin sign reads as the letter it resembles
_CASE = re.compile(r"several orders from (SUP-\d+) are slipping, (SKU-\d+) among them\. "
                   r"work out what that puts at risk and propose what to do\.?",
                   re.ASCII | re.IGNORECASE)
_SUPPLIER = re.compile(r"(?<![A-Za-z0-9])SUP-\d+", re.ASCII | re.IGNORECASE)


@dataclass(frozen=True)
class Answered:
    """A call of an earlier turn and what came back: the narrator view, when it succeeded."""

    name: str
    view: Mapping[str, Any] | None


def case_ids(text: str) -> tuple[str, str] | None:
    """The supplier and SKU the request names, when it is the exploration case."""
    match = _CASE.fullmatch(" ".join((text or "").split()))
    return (match.group(1).upper(), match.group(2).upper()) if match else None


def _view(content: str) -> Mapping[str, Any] | None:
    """A tool message as a narrator view; None for a refusal or an error (plain text)."""
    try:
        value = json.loads(content)
    except ValueError:
        return None
    if isinstance(value, dict) and isinstance(value.get("aggregates"), dict):
        return value
    return None


def answered(messages: Sequence[ChatMessage]) -> list[Answered]:
    """Every call the conversation answered, in order: a tool message answers the call with
    its id, else the oldest one still waiting."""
    waiting: list[ToolCall] = []
    out: list[Answered] = []
    for message in messages:
        if message.role == "assistant":
            waiting.extend(message.tool_calls)
        elif message.role == "tool" and waiting:
            call = next((c for c in waiting if c.id and c.id == message.tool_call_id),
                        waiting[0])
            waiting.remove(call)
            out.append(Answered(call.name, _view(message.content)))
    return out


def _figures(seen: Sequence[Answered], name: str) -> Mapping[str, Any]:
    """The aggregates of the last successful call to ``name``; empty when there is none."""
    for call in reversed(seen):
        if call.name == name and call.view is not None:
            return call.view["aggregates"]
    return {}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _alternate(seen: Sequence[Answered], late_supplier: str) -> str | None:
    """The first supplier the scorecard's message names that is not ``late_supplier``. The
    message arrives fenced as untrusted data; an id read from it is data, never an
    instruction."""
    for call in reversed(seen):
        if call.name == "supplier_scorecard" and call.view is not None:
            for found in _SUPPLIER.findall(str(call.view.get("message") or "")):
                if found.upper() != late_supplier:
                    return found.upper()
            return None
    return None


def _calls(*named: tuple[str, dict[str, Any]]) -> AgentStep:
    return AgentStep(tool_calls=[ToolCall(name=name, arguments=args) for name, args in named])


def script_step(messages: Sequence[ChatMessage]) -> AgentStep | None:
    """The script's next turn, or None when the conversation is not the exploration case."""
    request = next((m.content for m in messages if m.role == "user"), "")
    ids = case_ids(request)
    if ids is None:
        return None
    supplier, sku = ids
    end = AgentStep(text=say("mock_script_end", detect_language(request)))
    turn = sum(1 for m in messages if m.role == "assistant" and m.tool_calls)
    if turn == 0:
        return _calls(("late_shipments", {"supplier_id": supplier, "sku_id": sku}))
    seen = answered(messages)
    late = _figures(seen, "late_shipments")
    if not _count(late.get("late_count")):
        return end  # the read failed, or nothing is late: there is nothing to propose
    if turn == 1:
        return _calls(("reorder_suggestion", {"sku_id": sku}), ("supplier_scorecard", {}))
    if turn == 2:
        suggested = _figures(seen, "reorder_suggestion").get("total_suggested_qty")
        qty = max(_count(late.get("late_qty")), _count(suggested))
        if not qty:
            return end
        args: dict[str, Any] = {"sku_id": sku, "qty": qty}
        alternate = _alternate(seen, supplier)
        if alternate is not None:
            args["supplier_id"] = alternate
        return _calls(("create_po", args))
    return end  # the proposal was refused: a write never runs from here
