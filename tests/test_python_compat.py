"""The code imports on every supported Python (3.11 and later), not only on the newest.

Python 3.14 evaluates an annotation lazily; 3.11 to 3.13 evaluate it when its ``def`` (or class
attribute) runs. A class that defines a method named like a builtin and annotates a *later*
method with that builtin (``def list(...)`` and then ``-> list[Any]``) therefore imports on 3.14
and fails with ``TypeError: 'function' object is not subscriptable`` on the rest. CI runs the
whole suite on all four interpreters; this test finds the pattern on whichever Python a
developer runs, by reading the source.
"""

import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "src" / "twotrack", ROOT / "examples", ROOT / "bench"]
BUILTINS = frozenset(dir(builtins))


def evaluated_at_definition(stmt: ast.stmt) -> list[ast.AST]:
    """The expressions of a class-body statement that Python < 3.14 evaluates when it runs."""
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        args = stmt.args
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs,
                 *(a for a in (args.vararg, args.kwarg) if a is not None)]
        return [*stmt.decorator_list, *args.defaults,
                *(d for d in args.kw_defaults if d is not None),
                *(a.annotation for a in every if a.annotation is not None),
                *([stmt.returns] if stmt.returns is not None else [])]
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.annotation, *([stmt.value] if stmt.value is not None else [])]
    if isinstance(stmt, ast.Assign):
        return [stmt.value]
    if isinstance(stmt, ast.ClassDef):
        return [*stmt.bases, *(k.value for k in stmt.keywords), *stmt.decorator_list]
    return []


def bound_name(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        return {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return {stmt.target.id}
    return set()


def shadowed_uses(source: str) -> list[str]:
    """'Class.line: name' for each builtin a class body rebinds and then uses in a later
    definition-time expression."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        shadowed: set[str] = set()
        for stmt in node.body:
            for expr in evaluated_at_definition(stmt):
                found += [f"{node.name}.{name.lineno}: {name.id}" for name in ast.walk(expr)
                          if isinstance(name, ast.Name) and name.id in shadowed]
            shadowed |= bound_name(stmt) & BUILTINS
    return found


def test_the_scanner_finds_the_pattern_that_broke_python_311_to_313():
    broken = (
        "class Store:\n"
        "    def list(self) -> None: ...\n"
        "    def column_values(self) -> list[int]: ...\n"
        "    count: dict[str, int] = {}\n"
        "    kind: type = type\n"
        "    def type(self) -> None: ...\n"
        "    def after(self, x: type = int) -> None: ...\n")
    assert shadowed_uses(broken) == ["Store.3: list", "Store.7: type"]


def test_the_scanner_accepts_the_ways_around_it():
    fine = (
        "import builtins\n"
        "class Store:\n"
        "    def values(self) -> list[int]: ...\n"  # before the method that shadows it
        "    def list(self) -> None: ...\n"
        "    def column_values(self) -> builtins.list[int]: ...\n"
        "    def body_uses_it(self):\n"
        "        return list(range(3))\n"  # a body runs later: the builtin is found
        "class Other:\n"
        "    def column_values(self) -> list[int]: ...\n")  # another class: not shadowed
    assert shadowed_uses(fine) == []


@pytest.mark.parametrize("root", SOURCES, ids=lambda p: p.name)
def test_no_class_uses_a_builtin_it_has_just_rebound(root):
    problems = []
    for path in sorted(root.rglob("*.py")):
        problems += [f"{path.relative_to(ROOT)} {hit}" for hit in shadowed_uses(
            path.read_text(encoding="utf-8"))]
    assert not problems, "\n".join(problems)
