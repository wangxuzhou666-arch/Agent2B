"""Workflow runner: execute a Workflow's steps in order.

Step semantics (the registry already enforces the shape rules):

* deterministic step: ``fn(ctx, data)`` is plain code. ``input_model`` / ``output_model``
  validate what goes in and out.
* first llm step (understand): ``fn(ctx, data)`` returns the text the model may see (a str, or
  a JSON-able value that is serialized). The model must reply with JSON matching the step's
  ``output_model``; anything else raises ``WorkflowInputError`` (a user-facing problem that
  asks for a restated request). The raw query is in ``ctx.facts["query"]`` when run by the
  graph.
* last llm step (narrate): the previous step must have produced a ToolResult. The model sees
  only ``narrator_view()``; its text goes through the numeric lock and, on any failure,
  degrades to the deterministic template. ``fn(ctx, result)`` may return a str to use as the
  question; otherwise the step description is the question. The narration is stored in the
  returned result's ``message`` and handed to ``on_narration``.

This is a plain runner rather than a compiled LangGraph subgraph: a workflow is a straight
line with schema gates and no branching, so a subgraph would add state plumbing without
adding control flow. Each step still emits an ``execute`` trace step; an llm step's facts also
name the provider (``provider``, and ``mock`` when it is the zero-key mock, whose "model"
narration is the deterministic template).
"""

import json
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from twotrack.providers.base import ChatMessage, Provider, Scrubber, guarded, provider_info

from .contract import Step, StepKind, ToolContext, ToolResult, TraceStatus, TraceStep, Workflow
from .errors import InputProblem
from .i18n import language_of, say
from .narration import Narration, narrate_result
from .ports import NumericLock, default_lock

UNDERSTAND_SYSTEM = """\
You turn a user's request into the structured input of a business workflow.
Reply with one JSON object that matches this JSON schema, and nothing else:
{schema}
Use only values the request states. Omit any field the request does not give."""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class WorkflowInputError(InputProblem):
    """The workflow could not obtain the input it needs. The message is safe to show users."""


def _understand(ctx: ToolContext, workflow: Workflow, step: Step, data: Any,
                model: Provider) -> Any:
    payload = step.fn(ctx, data)
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    schema = json.dumps(step.output_model.model_json_schema())
    try:
        raw = model.chat([ChatMessage(role="system",
                                      content=UNDERSTAND_SYSTEM.format(schema=schema)),
                          ChatMessage(role="user", content=text)])
        return step.output_model.model_validate_json(_FENCE_RE.sub("", raw or "").strip())
    except Exception as exc:
        raise WorkflowInputError(say("workflow_needs_details", language_of(ctx.facts),
                                     workflow=workflow.name, step=step.description)) from exc


def _deterministic(ctx: ToolContext, step: Step, data: Any) -> Any:
    out = step.fn(ctx, data)
    if step.output_model is not None and not isinstance(out, step.output_model):
        out = step.output_model.model_validate(out)
    return out


def _narrate(ctx: ToolContext, workflow: Workflow, step: Step, data: Any, model: Provider,
             lock: NumericLock) -> Narration:
    if not isinstance(data, ToolResult):
        raise TypeError(f"workflow '{workflow.name}': step before '{step.id}' must return "
                        f"a ToolResult, got {type(data).__name__}")
    question = step.fn(ctx, data)
    if not isinstance(question, str) or not question.strip():
        question = step.description
    # the reply language is the user's, not the language the step's question is written in
    return narrate_result(model, question, data, lock, language=language_of(ctx.facts),
                          labels=workflow.aggregate_labels)


def run_workflow(
    ctx: ToolContext,
    workflow: Workflow,
    params: Any,
    provider: Provider,
    scrubber: Scrubber | None = None,
    *,
    lock: NumericLock | None = None,
    on_narration: Callable[[Narration], None] | None = None,
) -> ToolResult:
    model = guarded(provider, scrubber)
    info = provider_info(model)
    data: Any = params if isinstance(params, BaseModel) else dict(params)
    last = len(workflow.steps) - 1
    for i, step in enumerate(workflow.steps):
        start = time.perf_counter()
        if step.input_model is not None and not isinstance(data, step.input_model):
            try:
                data = step.input_model.model_validate(data)
            except ValidationError as exc:
                if i > 0:
                    raise
                raise WorkflowInputError(say("workflow_bad_input", language_of(ctx.facts),
                                             workflow=workflow.name,
                                             count=exc.error_count())) from exc
        facts: dict[str, Any] = {"workflow": workflow.name, "step": step.id,
                                 "kind": str(step.kind)}
        if step.kind == StepKind.LLM:  # which provider answered (the mock: template text)
            facts.update(provider=info.name, mock=info.mock)
        if step.kind == StepKind.LLM and i == 0 and last > 0:
            data = _understand(ctx, workflow, step, data, model)
        elif step.kind == StepKind.LLM:
            narration = _narrate(ctx, workflow, step, data, model, lock or default_lock())
            facts.update(narration=narration.source, stripped=narration.stripped)
            if on_narration is not None:
                on_narration(narration)
            data = data.model_copy(update={"message": narration.text})
        else:
            data = _deterministic(ctx, step, data)
        ctx.emit(TraceStep(step="execute", status=TraceStatus.OK, facts=facts,
                           duration_ms=round((time.perf_counter() - start) * 1000, 3)))
    if not isinstance(data, ToolResult):
        raise TypeError(f"workflow '{workflow.name}' must end with a ToolResult, "
                        f"got {type(data).__name__}")
    return data
