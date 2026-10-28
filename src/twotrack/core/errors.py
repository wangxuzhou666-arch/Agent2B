"""Core exception types."""


class InputProblem(ValueError):
    """A request lacks input it needs. The message is safe to show users.

    The pipeline records it as ``invalid`` (the user can fix it), not as an ``error``.
    """


class RegistryError(Exception):
    """The plugin registry is invalid. Raised at load time, never at request time.

    ``problems`` lists every problem found across all plugins, not just the first one,
    so a broken deployment is fixed in one pass.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        lines = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(f"plugin registry is invalid ({len(self.problems)} problem(s)):\n{lines}")


class OutputContractError(Exception):
    """A target returned something its ``outputs`` declaration does not list.

    The pipeline records it as an ``error``: a plugin broke its contract, which the user cannot
    fix. The message names the undeclared keys and never a value; the caller only sees the type."""
