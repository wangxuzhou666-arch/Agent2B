"""What exploration may offer a model: the exposed tools, the same for every explorer runtime.

The routing candidates (``Registry.candidates``: the principal's roles, met preconditions) are
narrowed twice before a runtime sees them:

* a target must declare ``explorable=True``; a target that says ``False`` is never offered to a
  model planning on its own, whatever else allows it;
* the deployment's allowlist ``TWOTRACK_EXPLORE_TOOLS`` (comma-separated names), when set, keeps
  only the targets it names. Unset, it keeps every explorable target except an ``external`` one:
  a call that sends data out of the deployment is offered only when the deployer named it. Such
  a target also declares ``approval='required'`` (the registry refuses an explorable external
  target without it), so data egress takes both that opt-in and a person's approval per call.

A tool the model names outside the exposed set is refused before the pipeline as an unknown
tool (``explore.calls``), and audited. ``check_allowlist`` runs at start-up: an allowlist that
names a target no plugin provides, or one that is not explorable, stops the server
(``SettingsError``) instead of silently exposing less than it says.
"""

from collections.abc import Collection
from types import MappingProxyType

from twotrack.settings import EXPLORE_TOOLS_ENV, SettingsError

from ..contract import ResourceEffect, Routable
from ..registry import Candidates, Registry


def is_exposed(target: Routable, allowlist: Collection[str] | None) -> bool:
    """Whether exploration may offer ``target`` to a model under ``allowlist``."""
    if target.explorable is not True:
        return False
    if allowlist is None:
        return target.effect is not ResourceEffect.EXTERNAL
    return target.name in allowlist


def exposed(candidates: Candidates, allowlist: Collection[str] | None) -> Candidates:
    """The candidates an explorer is given: explorable, and allowed by the deployment."""
    return Candidates(
        tools=MappingProxyType({n: t for n, t in candidates.tools.items()
                                if is_exposed(t, allowlist)}),
        workflows=MappingProxyType({n: w for n, w in candidates.workflows.items()
                                    if is_exposed(w, allowlist)}),
        entities=candidates.entities,
    )


def check_allowlist(registry: Registry, allowlist: Collection[str] | None) -> None:
    """Fail closed at start-up: every name in the allowlist is an explorable tool or workflow
    of this registry. Raises ``SettingsError`` naming the variable."""
    if allowlist is None:
        return
    unknown = sorted(n for n in allowlist
                     if n not in registry.tools and n not in registry.workflows)
    if unknown:
        raise SettingsError(f"{EXPLORE_TOOLS_ENV} names {', '.join(unknown)}, which no plugin "
                            "provides as a tool or workflow")
    hidden = sorted(n for n in allowlist if registry.target(n).explorable is not True)
    if hidden:
        raise SettingsError(f"{EXPLORE_TOOLS_ENV} names {', '.join(hidden)}, which its plugin "
                            "declares explorable=False, so it is never offered to a model")
