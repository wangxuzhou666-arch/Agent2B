"""A small registry with one target of every kind the exploration boundary treats differently.

* ``read_ok`` - a read, explorable: exposed by default;
* ``read_hidden`` - a read its plugin keeps out of exploration (``explorable=False``);
* ``ext_lookup`` - an explorable external call (approval required, as the registry insists):
  exposed only when the deployment's allowlist names it; its handler counts its runs;
* ``write_po`` - an explorable write: exposed, and every call stops at an approval.

``ask`` sends a question no target matches through the whole graph with open tasks on, so it
reaches the ``open_task`` node; the engine's pipeline is ``FakePipeline`` over this registry.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from tests.fakes import FakePipeline, ScriptedProvider, fake_lock, make_ctx, tool
from twotrack.core.audit import AuditEntry, AuditError
from twotrack.core.contract import Plugin, Provenance, ToolResult, TraceStep
from twotrack.core.explore.explorer import Explorer
from twotrack.core.graph import Engine, GraphSettings, QueryResponse, run_query
from twotrack.core.registry import build_registry
from twotrack.providers.base import AgentStep, ToolCall

QUERY = "zzz qqq unmatched gibberish"  # no keyword, example or description is close to it
EXTERNAL_RUNS: list[dict] = []  # every time ext_lookup's handler really ran


def _ext_lookup(ctx: Any, params: Any) -> ToolResult:
    EXTERNAL_RUNS.append({"user": ctx.principal.user_id})
    return ToolResult(aggregates={"count": 1},
                      provenance=Provenance(tool="ext_lookup", row_count=0, as_of=ctx.now()))


BOUNDARY = build_registry([Plugin(name="boundary", version="0.1.0", tools=(
    tool("read_ok", explorable=True, keywords=("stock snapshot",)),
    tool("read_hidden", keywords=("hidden ledger",)),
    tool("ext_lookup", effect="external", roles=("operator",), approval="required",
         explorable=True, handler=_ext_lookup, keywords=("outside lookup",)),
    tool("write_po", effect="write", roles=("operator",), explorable=True,
         keywords=("raise purchase order",)),
))])


def call(name: str, **args: Any) -> AgentStep:
    return AgentStep(tool_calls=[ToolCall(name=name, arguments=args)])


def engine(provider: Any, *, allowlist: tuple[str, ...] | None = None,
           explorer: Explorer | None = None, pipeline: FakePipeline | None = None,
           **settings: Any) -> Engine:
    return Engine(registry=BOUNDARY, provider=provider,
                  pipeline=pipeline if pipeline is not None else FakePipeline(BOUNDARY),
                  lock=fake_lock, explorer=explorer,
                  settings=GraphSettings(allow_open_task=True, explore_tools=allowlist,
                                         **settings))


def ask(eng: Engine, *roles: str, query: str = QUERY,
        sink: Callable | None = None, **options: Any) -> QueryResponse:
    ctx = make_ctx(*(roles or ("operator",)), sink=sink)
    return run_query(eng, ctx, query, **options)


def scripted(*steps: AgentStep) -> ScriptedProvider:
    return ScriptedProvider(steps=list(steps))


def start_step(response: QueryResponse):
    """The step that starts exploration: the runtime and the tools it was given."""
    (step,) = [s for s in response.trace
               if s.step == "execute" and s.facts.get("loop") == "start"]
    return step


def end_step(response: QueryResponse):
    """The step that ends the exploration run: the one carrying its stop reason."""
    (step,) = [s for s in response.trace if s.step == "execute" and "stop_reason" in s.facts]
    return step


@dataclass
class MemorySink:
    """An audit sink that keeps its records in a list; ``broken`` makes every write fail."""

    entries: list[AuditEntry] = field(default_factory=list)
    broken: bool = False

    def record(self, entry: AuditEntry) -> int:
        if self.broken:
            raise AuditError("audit write failed: the log is down")
        self.entries.append(entry)
        return len(self.entries)

    def append_trace(self, entry_id: int, steps: Sequence[TraceStep]) -> None:
        entry = self.entries[entry_id - 1]
        self.entries[entry_id - 1] = entry.model_copy(update={"trace": [*entry.trace, *steps]})
