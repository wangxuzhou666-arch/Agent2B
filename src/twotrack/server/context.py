"""The ToolContext every request-scoped run starts from."""

from typing import Any

from twotrack.core import Principal, ToolContext
from twotrack.core.store import ReadOnlyStore
from twotrack.runtime import Runtime


def request_context(runtime: Runtime, principal: Principal, **facts: Any) -> ToolContext:
    """Principal, request facts and a read-only view of the store.

    The store is not optional: preconditions (`has_inventory_data`, ...) read it, and a
    precondition that raises counts as failed, so a context without a store would hide every
    tool behind one without any visible error. Used by queries and by approval replays alike;
    the pipeline swaps in the right store (read-only or writable) when it executes.
    """
    return ToolContext(principal=principal, store=ReadOnlyStore(runtime.store), facts=facts,
                       clock=runtime.clock)
