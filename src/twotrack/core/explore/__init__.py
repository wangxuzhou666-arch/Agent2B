"""Exploration: the ``open_task`` branch, the one route where a model plans its own tool calls.

Two runtimes plan it; ``TWOTRACK_EXPLORER`` picks one, and nothing falls back from one to the
other:

* ``builtin`` (the default) - ``core.agent_loop``, a budgeted loop over the request's provider;
* ``pi`` - Pi's agent loop in a locked-down Node worker (``harness/pi``), see ``explore.pi``.

Whichever plans, it is offered the same tools: the request's candidates that declare
``explorable=True`` and that the deployment's allowlist admits (``explore.exposure``; an external
target only when named there). Every call the model makes runs through the same gate
(``explore.calls``): the
request's pipeline with route source ``agent`` (lookup, authorization, preconditions, validation,
write grants, audit), and the model sees only the fenced narrator view of a result, never rows. A
write stops the run with a pending approval; the explorer never executes one. A call refused
before the pipeline (an unknown tool, arguments that fail the schema, a spent budget) is audited
as a refusal.

Modules:

* ``explorer`` - the ``Explorer`` contract, budgets, and the built-in runtime behind it;
* ``exposure`` - which candidates a run may offer the model, and the start-up allowlist check;
* ``calls`` - the shared per-call gate, the record of a refused call, the run's trace facts;
* ``protocol`` - the worker's JSON Lines messages, as Pydantic models;
* ``endpoint`` - the zero-key model endpoint the worker talks to when no real model is set;
* ``pi`` - the Pi runtime: start-up checks, the worker's lockdown, budgets, failures.

Import the submodules directly; this package module imports nothing, so ``core.agent_loop`` can
use ``explore.calls`` without a cycle.
"""
