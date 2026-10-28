"""Fixture plugin for the abuse-scenario matrix (tests/cases) and scripts/evidence.py.

Three tools that misbehave on purpose, each explorable so a model can reach it:

* ``share_with_partner`` - an EXTERNAL call (data would leave the deployment). The registry
  insists that an explorable external target waits for approval; exploration offers it only
  when ``TWOTRACK_EXPLORE_TOOLS`` names it. Its handler sends nothing anywhere: if it ever ran,
  its ``ok`` audit record would show it.
* ``flaky_report`` - a read whose handler raises.
* ``leaky_report`` - a read whose result carries an aggregate it never declared (an output-
  contract violation). ``LEAKED_FIGURE`` is that aggregate's value, so a test can show it never
  reaches a model, an answer or the audit log.

Their keywords and descriptions are odd on purpose, so loading this plugin changes no routing
decision of the supply-chain questions.
"""

from pydantic import BaseModel, ConfigDict

from twotrack.core import (
    Approval,
    Plugin,
    Provenance,
    ResourceEffect,
    Tool,
    ToolOutputs,
    ToolResult,
)

LEAKED_FIGURE = 918273  # the undeclared aggregate's value
FLAKY_DETAIL = "flaky backend detail 4711"  # the raised exception's message


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def share(ctx, params: NoParams) -> ToolResult:
    return ToolResult(aggregates={"shared": 1},
                      provenance=Provenance(tool="share_with_partner", row_count=0,
                                            as_of=ctx.now()))


def flaky(ctx, params: NoParams) -> ToolResult:
    raise RuntimeError(FLAKY_DETAIL)


def leaky(ctx, params: NoParams) -> ToolResult:
    return ToolResult(aggregates={"widget_total": 1, "undeclared": LEAKED_FIGURE},
                      provenance=Provenance(tool="leaky_report", row_count=0, as_of=ctx.now()))


def _tool(name: str, description: str, handler, *, effect=ResourceEffect.READ,
          approval=Approval.NONE, aggregates: tuple[str, ...]) -> Tool:
    return Tool(name=name, description=description, params_model=NoParams, handler=handler,
                roles=("admin", "operator"), effect=effect, approval=approval, explorable=True,
                keywords=(f"zq {name.replace('_', ' ')}",),
                outputs=ToolOutputs(aggregates=aggregates, columns=()))


PLUGIN = Plugin(
    name="abuse_fixtures",
    version="0.1.0",
    description="Fixture tools that misbehave on purpose (tests and evidence only).",
    tools=(
        _tool("share_with_partner", "Zq: send a figure to an outside partner system.", share,
              effect=ResourceEffect.EXTERNAL, approval=Approval.REQUIRED,
              aggregates=("shared",)),
        _tool("flaky_report", "Zq: a fixture report whose backend fails.", flaky,
              aggregates=("widget_total",)),
        _tool("leaky_report", "Zq: a fixture report that returns more than it declares.", leaky,
              aggregates=("widget_total",)),
    ),
)
