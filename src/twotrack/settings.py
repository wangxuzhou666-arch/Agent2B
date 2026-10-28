"""Environment-driven settings for the server and CLI.

Every knob is a ``TWOTRACK_*`` variable; a malformed value stops startup with a
``SettingsError`` that names the variable. LLM provider variables are read by
``providers.provider_from_env`` (the model timeouts are parsed by ``_number`` below and the
model temperature by ``_temperature``, so they fail the same way) and plugin directories by
``core.load_registry``; this module only holds what the server itself needs.

Nothing here has a secret default: the signing key is generated on first start into the data
directory (see ``runtime.build_runtime``) and users are created with the CLI.
"""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DATA_DIR_ENV = "TWOTRACK_DATA_DIR"
DEMO_MODE_ENV = "TWOTRACK_DEMO_MODE"
CORS_ORIGINS_ENV = "TWOTRACK_CORS_ORIGINS"
ALLOWED_HOSTS_ENV = "TWOTRACK_ALLOWED_HOSTS"
ALLOW_OPEN_TASK_ENV = "TWOTRACK_ALLOW_OPEN_TASK"
MAX_UPLOAD_ENV = "TWOTRACK_MAX_UPLOAD_BYTES"
MAX_BODY_ENV = "TWOTRACK_MAX_BODY_BYTES"
TOKEN_TTL_ENV = "TWOTRACK_TOKEN_TTL_SECONDS"
HEARTBEAT_ENV = "TWOTRACK_SSE_HEARTBEAT_SECONDS"
# exploration (the open_task route): which runtime plans it, and its budgets
EXPLORER_ENV = "TWOTRACK_EXPLORER"
EXPLORE_MAX_TURNS_ENV = "TWOTRACK_EXPLORE_MAX_TURNS"
EXPLORE_MAX_CALLS_ENV = "TWOTRACK_EXPLORE_MAX_CALLS"
EXPLORE_TIMEOUT_ENV = "TWOTRACK_EXPLORE_TIMEOUT_SECONDS"
EXPLORE_TOOL_TIMEOUT_ENV = "TWOTRACK_EXPLORE_TOOL_TIMEOUT_SECONDS"
EXPLORE_STARTUP_TIMEOUT_ENV = "TWOTRACK_EXPLORE_STARTUP_TIMEOUT_SECONDS"
# the deployment's allowlist of what exploration may offer a model (core.explore.exposure)
EXPLORE_TOOLS_ENV = "TWOTRACK_EXPLORE_TOOLS"
PI_HARNESS_ENV = "TWOTRACK_PI_HARNESS_DIR"
EXPLORERS = ("builtin", "pi")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


class SettingsError(ValueError):
    """A TWOTRACK_* variable holds a value the server cannot use."""


def _flag(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name, "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SettingsError(f"{name} must be one of 1/0/true/false/yes/no/on/off, got {value!r}")


def _count(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    """Unset or empty gives ``default``; anything else must be a whole number >= ``minimum``."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a whole number, got {raw!r}") from None
    if value < minimum:
        raise SettingsError(f"{name} must be at least {minimum}, got {raw}")
    return value


def _choice(env: Mapping[str, str], name: str, choices: tuple[str, ...]) -> str:
    """Unset or empty gives the first choice; anything else must be one of them."""
    value = env.get(name, "").strip().lower() or choices[0]
    if value not in choices:
        raise SettingsError(f"{name} must be one of {'/'.join(choices)}, got {value!r}")
    return value


def _number(env: Mapping[str, str], name: str, default: float, *, minimum: float) -> float:
    """Unset or empty gives ``default``; anything else must be a finite number >= ``minimum``."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from None
    # float() also accepts "nan" and "inf": nan passes every `<` check, and an infinite limit
    # is no limit at all
    if not math.isfinite(value):
        raise SettingsError(f"{name} must be a finite number, got {raw!r}")
    if value < minimum:
        raise SettingsError(f"{name} must be at least {minimum:g}, got {raw}")
    return value


# The temperature setting's word for "send no temperature at all": some models reject any value
# a caller sends (Claude models from 4.7 on refuse a temperature below 1 with HTTP 400).
OMIT_TEMPERATURE = "omit"
MAX_TEMPERATURE = 2.0  # the top of the OpenAI-compatible range


def _temperature(env: Mapping[str, str], name: str) -> float | str | None:
    """Unset or empty gives None (every model call keeps its own temperature); ``omit`` gives
    ``OMIT_TEMPERATURE``; anything else must be a number from 0 to 2, used for every call."""
    raw = env.get(name, "").strip()
    if not raw:
        return None
    if raw.lower() == OMIT_TEMPERATURE:
        return OMIT_TEMPERATURE
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or not 0 <= value <= MAX_TEMPERATURE:
        raise SettingsError(f"{name} must be '{OMIT_TEMPERATURE}' or a number from 0 to "
                            f"{MAX_TEMPERATURE:g}, got {raw!r}")
    return value


def _names(env: Mapping[str, str], name: str) -> tuple[str, ...] | None:
    """Unset or empty gives None; anything else is a comma-separated list that names at least
    one item (each once, in the order given)."""
    raw = env.get(name, "").strip()
    if not raw:
        return None
    names = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not names:
        raise SettingsError(f"{name} must name at least one tool, got {raw!r}")
    return names


def parse_origins(raw: str) -> tuple[str, ...]:
    """Comma-separated browser origins. A wildcard is refused: list the real origins."""
    origins = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        parts = urlsplit(item)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SettingsError(f"{CORS_ORIGINS_ENV}: {item!r} is not an http(s) origin; "
                                "wildcards are not accepted")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise SettingsError(f"{CORS_ORIGINS_ENV}: {item!r} must be scheme://host[:port] "
                                "with no path")
        origins.append(f"{parts.scheme}://{parts.netloc}")
    return tuple(dict.fromkeys(origins))


# A host as a Host header spells it, lower-cased and without its port: a DNS name or an IPv4
# address, or an IPv6 address in brackets. Both ends of a name are a letter or a digit.
HOST_PATTERN = r"\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"


def parse_hosts(raw: str) -> tuple[str, ...]:
    """Comma-separated host names a request's Host header may carry, on any port. Bare names
    only: a scheme, port, path or wildcard is refused, because each would make the list mean
    something other than what it says."""
    hosts = []
    for item in (part.strip().lower() for part in raw.split(",")):
        if not item:
            continue
        if not re.fullmatch(HOST_PATTERN, item):
            raise SettingsError(f"{ALLOWED_HOSTS_ENV}: {item!r} is not a bare host name; give "
                                "names only (no scheme, port, path or wildcard), and an IPv6 "
                                "address in brackets")
        hosts.append(item)
    return tuple(dict.fromkeys(hosts))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    demo_mode: bool = False
    cors_origins: tuple[str, ...] = ()
    # The Host header is checked in demo mode, and in every mode once this is set; localhost,
    # 127.0.0.1 and [::1] are always accepted (server.hosts).
    allowed_hosts: tuple[str, ...] = ()
    allow_open_task: bool = False
    max_upload_bytes: int = 2 * 1024 * 1024
    max_body_bytes: int = 1024 * 1024  # every request except an upload
    token_ttl_seconds: int = 8 * 3600
    sse_heartbeat_seconds: float = 15.0  # below the browser client's 45 s idle gate
    # exploration: ``builtin`` (core.agent_loop) or ``pi`` (core.explore.pi, needs Node and
    # harness/pi installed; the server refuses to start without them). The budgets apply to both.
    explorer: str = "builtin"
    explore_max_turns: int = 4
    explore_max_calls: int = 6
    explore_timeout_seconds: float = 120.0  # wall clock of one run
    explore_tool_timeout_seconds: float = 30.0  # one host answer to a Pi tool call
    # a Pi worker's start-up, from launch to its hello; the run's wall clock starts after it
    explore_startup_timeout_seconds: float = 20.0
    # what exploration may offer a model, among the explorable targets; None: every explorable
    # target except external ones (core.explore.exposure). Checked against the registry at
    # start-up (runtime.build_runtime).
    explore_tools: tuple[str, ...] | None = None
    pi_harness_dir: Path | None = None  # None: harness/pi of this checkout

    @property
    def db_path(self) -> Path:
        return self.data_dir / "twotrack.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "jwt.secret"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        defaults = cls()
        harness = env.get(PI_HARNESS_ENV, "").strip()
        return cls(
            data_dir=Path(env.get(DATA_DIR_ENV, "").strip() or defaults.data_dir),
            demo_mode=_flag(env, DEMO_MODE_ENV),
            cors_origins=parse_origins(env.get(CORS_ORIGINS_ENV, "")),
            allowed_hosts=parse_hosts(env.get(ALLOWED_HOSTS_ENV, "")),
            allow_open_task=_flag(env, ALLOW_OPEN_TASK_ENV),
            max_upload_bytes=int(_number(env, MAX_UPLOAD_ENV, defaults.max_upload_bytes,
                                         minimum=1024)),
            max_body_bytes=int(_number(env, MAX_BODY_ENV, defaults.max_body_bytes,
                                       minimum=1024)),
            token_ttl_seconds=int(_number(env, TOKEN_TTL_ENV, defaults.token_ttl_seconds,
                                          minimum=60)),
            sse_heartbeat_seconds=_number(env, HEARTBEAT_ENV, defaults.sse_heartbeat_seconds,
                                          minimum=0.05),
            explorer=_choice(env, EXPLORER_ENV, EXPLORERS),
            explore_max_turns=_count(env, EXPLORE_MAX_TURNS_ENV, defaults.explore_max_turns,
                                     minimum=1),
            explore_max_calls=_count(env, EXPLORE_MAX_CALLS_ENV, defaults.explore_max_calls,
                                     minimum=1),
            explore_timeout_seconds=_number(env, EXPLORE_TIMEOUT_ENV,
                                            defaults.explore_timeout_seconds, minimum=1),
            explore_tool_timeout_seconds=_number(env, EXPLORE_TOOL_TIMEOUT_ENV,
                                                 defaults.explore_tool_timeout_seconds,
                                                 minimum=0.1),
            explore_startup_timeout_seconds=_number(
                env, EXPLORE_STARTUP_TIMEOUT_ENV, defaults.explore_startup_timeout_seconds,
                minimum=0.1),
            explore_tools=_names(env, EXPLORE_TOOLS_ENV),
            pi_harness_dir=Path(harness) if harness else None,
        )
