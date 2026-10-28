"""Fixture plugin: one root entity, a precondition, two tools and a workflow."""

from pydantic import BaseModel, ConfigDict

from twotrack.core import (
    EntitySpec,
    FieldSpec,
    FieldType,
    Plugin,
    Precondition,
    Provenance,
    Sensitivity,
    Step,
    StepKind,
    Tool,
    ToolOutputs,
    ToolResult,
    Workflow,
)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


COUNT_OUTPUT = ToolOutputs(aggregates=("widget_count",), columns=())


def count_widgets(ctx, params):
    return ToolResult(
        aggregates={"widget_count": 3},
        provenance=Provenance(tool="widget_count", row_count=3, as_of=ctx.now()),
    )


WIDGET = EntitySpec(
    id="widget",
    label="Widget",
    primary_key="widget_id",
    label_field="name",
    fields=(
        FieldSpec(id="widget_id", label="Widget ID", required=True, aliases=("code",)),
        FieldSpec(id="name", label="Name"),
        FieldSpec(id="qty", label="Quantity", type=FieldType.INT, aliases=("qty on hand",)),
        FieldSpec(id="cost", label="Unit Cost", type=FieldType.FLOAT,
                  sensitivity=Sensitivity.RESTRICTED, visible_roles=frozenset({"operator"})),
    ),
)

PLUGIN = Plugin(
    name="widgets",
    version="0.1.0",
    entities=(WIDGET,),
    preconditions=(
        Precondition(name="has_upload", description="Upload a file first.",
                     check=lambda ctx: bool(ctx.facts.get("upload"))),
    ),
    tools=(
        Tool(name="widget_count", description="Count widgets.", params_model=NoParams,
             handler=count_widgets, roles=("viewer", "operator"), effect="read",
             approval="none", outputs=COUNT_OUTPUT, explorable=False,
             keywords=("how many widgets",), examples=("how many widgets do we have",),
             entity="widget"),
        Tool(name="widget_import", description="Import widgets from an uploaded file.",
             params_model=NoParams, handler=count_widgets, roles=("operator",), effect="write",
             approval="required", outputs=COUNT_OUTPUT, explorable=False,
             requires=("has_upload",), entity="widget"),
    ),
    workflows=(
        Workflow(name="widget_restock", description="Compute restock quantities, then explain.",
                 roles=("operator",), effect="read", approval="none",
                 outputs=ToolOutputs(aggregates=(), columns=()), explorable=False,
                 steps=(Step(id="compute", kind=StepKind.DETERMINISTIC, description="compute",
                             fn=lambda ctx, data: data),
                        Step(id="narrate", kind=StepKind.LLM, description="explain",
                             fn=lambda ctx, data: data))),
    ),
)
