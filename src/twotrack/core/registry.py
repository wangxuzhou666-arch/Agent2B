"""Plugin discovery and the fail-closed registry.

A plugin is a module (or package) exporting ``PLUGIN: Plugin``. Sources, in load order:

1. ``module_names`` if given, else every non-underscore submodule of ``twotrack.plugins``;
2. ``extra_paths`` if given, else the directories in ``$TWOTRACK_PLUGIN_PATHS``
   (``os.pathsep``-separated). Each ``*.py`` file or package directory in them is a plugin.
   Dropping a file into such a directory and restarting adds its entities/tools/workflows;
   core code does not change.

Load order is sorted by name, never filesystem order, so the registry (and every prompt or
index built from it) is identical on every machine. Any problem stops the load with a
``RegistryError`` listing all problems found.

The load also builds the constraint vocabulary (``Registry.vocabulary``, from categories and
scope-key fields) and each target's param bindings (``Registry.bindings``, see core.params and
core.scope); a malformed declaration of either is a load problem like any other.
"""

import functools
import hashlib
import importlib
import importlib.util
import os
import pkgutil
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

from .contract import (
    EntitySpec,
    FieldSpec,
    Plugin,
    Precondition,
    ResolvedEntity,
    Routable,
    Tool,
    ToolContext,
    TraceStatus,
    TraceStep,
    Workflow,
    normalize_header,
    resolve_entity,
)
from .errors import RegistryError
from .scope import Vocabulary, build_vocabulary, target_bindings

BUILTIN_PACKAGE = "twotrack.plugins"
PLUGIN_PATHS_ENV = "TWOTRACK_PLUGIN_PATHS"


@dataclass(frozen=True)
class Candidates:
    """Targets a request may be routed to: allowed for the principal, preconditions met.
    ``entities`` are the registry's, read-only, so routing can name a target's params in
    every language (core.routing.semantic)."""

    tools: Mapping[str, Tool]
    workflows: Mapping[str, Workflow]
    entities: Mapping[str, ResolvedEntity] = field(
        default_factory=lambda: MappingProxyType({}))

    def __contains__(self, name: object) -> bool:
        return name in self.tools or name in self.workflows

    def names(self) -> list[str]:
        return [*self.tools, *self.workflows]

    def get(self, name: str) -> Routable | None:
        return self.tools.get(name) or self.workflows.get(name)


class Registry:
    """Read-only view over every loaded plugin. Build it with load_registry/build_registry."""

    def __init__(
        self,
        plugins: tuple[Plugin, ...],
        tools: dict[str, Tool],
        workflows: dict[str, Workflow],
        entities: dict[str, ResolvedEntity],
        preconditions: dict[str, Precondition],
        *,
        vocabulary: Vocabulary | None = None,
        bindings: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.plugins = plugins
        self.tools: Mapping[str, Tool] = MappingProxyType(tools)
        self.workflows: Mapping[str, Workflow] = MappingProxyType(workflows)
        self.entities: Mapping[str, ResolvedEntity] = MappingProxyType(entities)
        self.preconditions: Mapping[str, Precondition] = MappingProxyType(preconditions)
        if vocabulary is None:
            vocabulary, _ = build_vocabulary(entities)
        if bindings is None:
            bindings = {t.name: target_bindings(t, entities)[0]
                        for t in [*tools.values(), *workflows.values()]}
        # every phrase a question can use to name a category or a scope-key value
        self.vocabulary: Vocabulary = vocabulary
        # target name -> {param: binding key}: which params apply which constraint
        self.bindings: Mapping[str, Mapping[str, str]] = MappingProxyType(
            {name: MappingProxyType(dict(found)) for name, found in bindings.items()})

    def tool(self, name: str) -> Tool:
        return _lookup(self.tools, "tool", name)

    def workflow(self, name: str) -> Workflow:
        return _lookup(self.workflows, "workflow", name)

    def entity(self, entity_id: str) -> ResolvedEntity:
        return _lookup(self.entities, "entity", entity_id)

    def target(self, name: str) -> Routable:
        """A tool or workflow by name (they share one namespace)."""
        if name in self.workflows:
            return self.workflows[name]
        return _lookup(self.tools, "tool or workflow", name)

    def unmet(self, target: Routable, ctx: ToolContext) -> list[str]:
        """Names of ``target``'s preconditions that fail for ``ctx``, in declared order."""
        return [name for name in target.requires if not self._passes(name, ctx, {})]

    def candidates(self, ctx: ToolContext) -> Candidates:
        """The candidate-set gate: routing only ever considers what this returns.

        Each precondition runs at most once per call. A check that raises counts as failed
        (fail closed) and is reported to the trace sink.
        """
        cache: dict[str, bool] = {}

        def ok(t: Routable) -> bool:
            return t.allows(ctx.principal) and all(self._passes(n, ctx, cache) for n in t.requires)

        return Candidates(
            tools=MappingProxyType({n: t for n, t in self.tools.items() if ok(t)}),
            workflows=MappingProxyType({n: w for n, w in self.workflows.items() if ok(w)}),
            entities=self.entities,
        )

    def _passes(self, name: str, ctx: ToolContext, cache: dict[str, bool]) -> bool:
        if name not in cache:
            try:
                cache[name] = bool(self.preconditions[name].check(ctx))
            except Exception as exc:
                cache[name] = False
                error = f"{type(exc).__name__}: {exc}"[:200]
                ctx.emit(TraceStep(step="candidates", status=TraceStatus.ERROR,
                                   facts={"precondition": name, "error": error}))
        return cache[name]


def _lookup(table: Mapping[str, Any], kind: str, key: str) -> Any:
    try:
        return table[key]
    except KeyError:
        raise KeyError(f"unknown {kind} '{key}'") from None


# ---------------------------------------------------------------- loading


def load_registry(
    module_names: list[str] | None = None,
    extra_paths: list[Path] | None = None,
) -> Registry:
    problems: list[str] = []
    plugins: list[Plugin] = []
    names = _builtin_modules() if module_names is None else list(module_names)
    for name in names:
        _collect(name, functools.partial(importlib.import_module, name), plugins, problems)
    for directory in _env_paths() if extra_paths is None else [Path(p) for p in extra_paths]:
        if not directory.is_dir():
            problems.append(f"plugin path '{directory}' is not a directory")
            continue
        for entry in sorted(directory.iterdir()):
            is_package = entry.is_dir() and (entry / "__init__.py").is_file()
            if entry.name.startswith(("_", ".")) or not (entry.suffix == ".py" or is_package):
                continue
            _collect(str(entry), functools.partial(_import_path, entry), plugins, problems)
    return _build(plugins, problems)


def build_registry(plugins: Iterable[Plugin]) -> Registry:
    """Validate already-constructed plugins (no discovery). Raises RegistryError."""
    return _build(list(plugins), [])


def _builtin_modules() -> list[str]:
    package = importlib.import_module(BUILTIN_PACKAGE)
    found = pkgutil.iter_modules(package.__path__)
    return sorted(f"{BUILTIN_PACKAGE}.{m.name}" for m in found if not m.name.startswith("_"))


def _env_paths() -> list[Path]:
    raw = os.environ.get(PLUGIN_PATHS_ENV, "")
    return [Path(p.strip()) for p in raw.split(os.pathsep) if p.strip()]


def _import_path(path: Path) -> ModuleType:
    is_package = path.is_dir()
    digest = hashlib.sha1(str(path.parent.resolve()).encode(), usedforsecurity=False)
    name = f"twotrack_ext_{digest.hexdigest()[:8]}_{path.stem}"
    spec = importlib.util.spec_from_file_location(
        name,
        path / "__init__.py" if is_package else path,
        submodule_search_locations=[str(path)] if is_package else None,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _collect(
    source: str, load: Callable[[], ModuleType], plugins: list[Plugin], problems: list[str]
) -> None:
    try:
        module = load()
    except Exception as exc:
        problems.append(f"plugin source '{source}' failed to import: {type(exc).__name__}: {exc}")
        return
    plugin = getattr(module, "PLUGIN", None)
    if plugin is None:
        problems.append(f"plugin source '{source}' does not export PLUGIN")
    elif not isinstance(plugin, Plugin):
        problems.append(f"plugin source '{source}' exports PLUGIN of type "
                        f"{type(plugin).__name__}, expected Plugin")
    else:
        plugins.append(plugin)


# ---------------------------------------------------------------- validation


def _build(plugins: list[Plugin], problems: list[str]) -> Registry:
    tools: dict[str, Tool] = {}
    workflows: dict[str, Workflow] = {}
    specs: dict[str, EntitySpec] = {}
    preconditions: dict[str, Precondition] = {}
    owners: dict[tuple[str, str], str] = {}  # (namespace, name) -> plugin that declared it

    def register(table: dict, namespace: str, key: str, item: Any, plugin: str) -> None:
        if (namespace, key) in owners:
            problems.append(f"duplicate {namespace} name '{key}': declared by plugin "
                            f"'{owners[namespace, key]}' and plugin '{plugin}'")
            return
        owners[namespace, key] = plugin
        table[key] = item

    for plugin in plugins:
        problems.extend(plugin.problems())
        if ("plugin", plugin.name) in owners:
            problems.append(f"duplicate plugin name '{plugin.name}'")
        owners["plugin", plugin.name] = plugin.name
        groups = (
            (plugin.entities, EntitySpec, specs, "entity", "id"),
            (plugin.preconditions, Precondition, preconditions, "precondition", "name"),
            (plugin.tools, Tool, tools, "tool/workflow", "name"),
            (plugin.workflows, Workflow, workflows, "tool/workflow", "name"),
        )
        for items, expected, table, namespace, key_attr in groups:
            for item in items:
                if not isinstance(item, expected):
                    problems.append(f"plugin '{plugin.name}': {item!r:.60} is not "
                                    f"{expected.__name__}")
                    continue
                register(table, namespace, getattr(item, key_attr), item, plugin.name)

    for target in [*tools.values(), *workflows.values()]:
        problems.extend(target.problems())
        where = f"{target.route_kind.value} '{target.name}'"
        for name in target.requires:
            if name not in preconditions:
                problems.append(f"{where}: requires unknown precondition '{name}'")
        if isinstance(target, Tool) and target.entity is not None and target.entity not in specs:
            problems.append(f"{where}: entity '{target.entity}' is not declared by any plugin")
    for pre in preconditions.values():
        problems.extend(pre.problems())
    entities = _resolve_entities(specs, problems)
    vocabulary, vocabulary_problems = build_vocabulary(entities)
    problems.extend(vocabulary_problems)
    bindings: dict[str, dict[str, str]] = {}
    for target in [*tools.values(), *workflows.values()]:
        bindings[target.name], binding_problems = target_bindings(target, entities)
        problems.extend(binding_problems)

    if problems:
        raise RegistryError(problems)
    return Registry(tuple(plugins), tools, workflows, entities, preconditions,
                    vocabulary=vocabulary, bindings=bindings)


def _resolve_entities(
    specs: dict[str, EntitySpec], problems: list[str]
) -> dict[str, ResolvedEntity]:
    resolved: dict[str, ResolvedEntity] = {}
    failed: set[str] = set()

    def visit(eid: str, chain: list[str]) -> ResolvedEntity | None:
        if eid in resolved or eid in failed:
            return resolved.get(eid)
        spec, chain = specs[eid], [*chain, eid]
        parent = None
        if spec.extends is not None:
            if spec.extends not in specs:
                problems.append(f"entity '{eid}' extends unknown entity '{spec.extends}'")
                failed.add(eid)
                return None
            if spec.extends in chain:
                cycle = chain[chain.index(spec.extends):]
                problems.append(f"entity extends cycle: {' -> '.join([*cycle, spec.extends])}")
                failed.update(cycle)
                return None
            parent = visit(spec.extends, chain)
            if parent is None:  # root cause already reported
                failed.add(eid)
                return None
        problems.extend(_entity_problems(spec, parent))
        resolved[eid] = resolve_entity(spec, parent)
        return resolved[eid]

    for eid in specs:
        visit(eid, [])
    return {eid: resolved[eid] for eid in specs if eid in resolved}


def _entity_problems(spec: EntitySpec, parent: ResolvedEntity | None) -> list[str]:
    """Checks for what ``spec`` itself adds; the parent's own problems are reported once."""
    where = f"entity '{spec.id}'"
    out = []
    inherited = parent.all_fields if parent else ()
    own = spec.fields + spec.attrs
    if parent is None:
        if spec.primary_key not in {f.id for f in spec.fields}:
            out.append(f"{where}: primary_key {spec.primary_key!r} is not one of its core fields")
    else:
        if spec.fields:
            out.append(f"{where}: extends '{parent.id}' so it may only add attrs; core fields "
                       "come from the root entity")
        if spec.primary_key not in (None, parent.primary_key):
            out.append(f"{where}: primary_key must match parent's '{parent.primary_key}'")

    ids = {f.id for f in inherited}
    header_owner = {normalize_header(t): f.id for f in inherited for t in f.header_texts()}
    clean: list[FieldSpec] = []
    for f in own:
        if f.id in ids:
            out.append(f"{where}: duplicate field id '{f.id}'")
        else:
            ids.add(f.id)
            clean.append(f)
    for f in clean:
        for text in f.header_texts():
            other = header_owner.setdefault(normalize_header(text), f.id)
            if other != f.id:
                out.append(f"{where}: header alias '{text}' of field '{f.id}' collides with "
                           f"field '{other}'")
    if spec.label_field is not None and spec.label_field not in ids:
        out.append(f"{where}: label_field '{spec.label_field}' is not a field")
    return out
