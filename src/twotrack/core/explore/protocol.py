"""The Pi worker's line protocol (``harness/pi/README.md``), as Pydantic models.

Every line the worker writes is parsed into one of the ``WorkerLine`` models; a line that is too
long, is not JSON, has an unknown ``type`` or event ``name``, or breaks a field rule raises
``ProtocolError``, and the run stops as a failure. Unknown fields are refused too: the worker is
pinned in this repository, so any drift is a bug, not a newer peer.

The host's own lines are built from the models at the bottom and never carry a key: the model
config names the environment variable that holds it (``api_key_env``).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

PROTOCOL = 1
MAX_LINE_BYTES = 1024 * 1024  # per line, either direction (the worker checks the same cap)

# why Pi refused a call itself (event ``tool_blocked``)
BlockReason = Literal["unknown_tool", "invalid_args", "truncated", "max_turns", "max_tool_calls",
                      "aborted"]
# how a run ended, as the worker saw it (``final.stop_reason``)
WorkerStop = Literal["stop", "length", "max_turns", "max_tool_calls", "wall_clock", "host_abort",
                     "host_gone", "terminated", "error", "aborted"]


class ProtocolError(ValueError):
    """A worker line the host cannot accept. The message names the rule, never the line."""


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- worker to host


class Hello(_Line):
    type: Literal["hello"]
    protocol: Literal[1]
    versions: dict[str, str | None]
    node: str = Field(max_length=64)


class ToolCallLine(_Line):
    type: Literal["tool_call"]
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    args: dict[str, JsonValue]


class EventLine(_Line):
    type: Literal["event"]
    name: Literal["assistant", "tool_blocked", "tool_timeout"]
    # assistant: a model turn ended
    turn: int | None = Field(default=None, ge=1)
    stop: str | None = Field(default=None, max_length=64)
    calls: list[Annotated[str, Field(max_length=256)]] = []
    # tool_blocked / tool_timeout: one call
    id: str | None = Field(default=None, max_length=64)
    tool: str | None = Field(default=None, max_length=256)
    reason: BlockReason | None = None
    detail: str | None = Field(default=None, max_length=1000)
    args: dict[str, JsonValue] | None = None


class Usage(_Line):
    input: int = Field(default=0, ge=0)
    output: int = Field(default=0, ge=0)


class FinalLine(_Line):
    type: Literal["final"]
    text: str = ""
    stop_reason: WorkerStop
    error: str | None = Field(default=None, max_length=1000)
    http_status: int | None = Field(default=None, ge=100, le=599)
    usage: Usage | None = None
    turns: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    forwarded: int = Field(default=0, ge=0)


WorkerLine = Hello | ToolCallLine | EventLine | FinalLine
_WORKER_LINE: TypeAdapter[WorkerLine] = TypeAdapter(
    Annotated[WorkerLine, Field(discriminator="type")])


def parse_worker_line(raw: bytes) -> WorkerLine:
    if len(raw) > MAX_LINE_BYTES:
        raise ProtocolError("a worker line exceeds the size limit")
    try:
        return _WORKER_LINE.validate_json(raw)
    except ValidationError as exc:
        kinds = sorted({e["type"] for e in exc.errors()})
        raise ProtocolError(f"a worker line breaks the protocol ({', '.join(kinds)})") from None


# ---------------------------------------------------------------- host to worker


class ToolSpec(_Line):
    name: str
    description: str
    parameters: dict[str, JsonValue]  # JSON Schema of the tool's params model


class ModelSpec(_Line):
    id: str
    base_url: str
    api_key_env: str  # the variable holding the key in the worker's environment
    max_tokens: int | None = None
    context_window: int | None = None
    headers: dict[str, str | None] = {}


class BudgetSpec(_Line):
    max_turns: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)
    wall_ms: int = Field(ge=1)
    request_timeout_ms: int = Field(ge=1)
    tool_timeout_ms: int = Field(ge=1)


class StartLine(_Line):
    type: Literal["start"] = "start"
    task: str
    system: str
    tools: list[ToolSpec]
    model: ModelSpec
    budget: BudgetSpec


class ToolResultLine(_Line):
    type: Literal["tool_result"] = "tool_result"
    id: str
    ok: bool
    content: str | None = None  # what the model sees, when ok
    code: str | None = None  # when not ok: the model sees "code: error"
    error: str | None = None
    terminate: bool = False


class AbortLine(_Line):
    type: Literal["abort"] = "abort"
    reason: str


def encode(line: StartLine | ToolResultLine | AbortLine) -> bytes:
    data = line.model_dump_json(exclude_none=True).encode() + b"\n"
    if len(data) > MAX_LINE_BYTES:
        raise ProtocolError(f"a {line.type} line exceeds the size limit")
    return data
