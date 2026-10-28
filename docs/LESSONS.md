# Engineering lessons

Short accounts of things that went wrong, or nearly did, while building twotrack: what
happened, how it surfaced, and the guard that now stops it from coming back. Design decisions
and their trade-offs are in [the decision records](decisions/README.md).

## 1. A newer Python hid an import crash on the versions we support

**What happened.** `Store` (`src/twotrack/core/store/base.py`) and `SqliteStore`
(`core/store/sqlite.py`) define a method named `list`. Inside a class body that `def` rebinds the
name `list` for every statement after it. Python 3.11 to 3.13 evaluate a method's annotations
when its `def` runs, so a later `-> list[Any]` subscripted the method and the module failed to
import with `TypeError: 'function' object is not subscriptable`. Python 3.14 evaluates
annotations lazily, so on 3.14 everything imported and every test passed. The package declares
`requires-python = ">=3.11"`; the development environment runs 3.14, and CI ran the suite on a
single interpreter.

**How it surfaced.** Only by importing on an older interpreter; nothing in the repository ran
the declared floor, and 3.14's deferred annotations hid the crash everywhere else. The pattern
reproduces in three lines, a class with `def list` and then `-> list[int]`, which fails on 3.11,
3.12 and 3.13 and imports on 3.14.

**Guard.** The annotations after the method spell `builtins.list`, and both modules say why in
their docstrings. CI runs the whole suite on 3.11, 3.12, 3.13 and 3.14, with `fail-fast` off so
one failing version does not hide the others (`.github/workflows/ci.yml`).
`tests/test_python_compat.py` parses every module under `src/twotrack`, `examples` and `bench`
and fails on any class that rebinds a builtin and then uses it in an expression evaluated at
definition time, so the trap is caught on whichever Python a developer runs.

**Lesson.** A newer interpreter can be more forgiving than the oldest one you promise. Test the
floor, and turn the specific trap into a check that does not depend on the interpreter.

## 2. An open redirect through `next=`

**What happened.** After sign-in the console follows `/login?next=<path>`. The first check
read the string: it had to start with `/` and not with `//` or `/\`. Two inputs passed
and still left the console's origin. `/login?next=/%09/evil.example` arrives decoded as
`/<TAB>/evil.example`; the URL parser drops TAB, CR and LF wherever they appear, so the browser
resolves it as `//evil.example`, a protocol-relative URL to another host. `/.//evil.example`
passes the prefix test too, and dot-segment removal turns its path into `//evil.example`.

**How it surfaced.** Reading the string cannot show it. What exposes the bug is resolving the
value with the parser that will consume it, which is what the tests do:
`new URL("/\t/evil.example", origin).origin` is the attacker's origin.

**Guard.** `safeNext` (`web/src/components/shell/login-view.tsx`) refuses control characters
and backslashes outright, resolves the value against the console's origin with `new URL`,
requires the same origin, parses the resulting path once more to reject one that becomes
protocol-relative on its own, and never sends a signed-in user back to `/login`. Unit tests cover
each bypass (`login-view.test.ts`). An end-to-end test (`web/e2e/login.spec.ts`) intercepts
every request to the attacker host, tries each payload through a real browser, and asserts that
no request reached it.

**Lesson.** Validate a URL with the parser that will use it, and test the effect (no request
leaves the origin), not the shape of the string.

## 3. A dev set for changing the router, a held-out set for measuring it

**What happened.** Two routing changes, keyword contests and bilingual evidence, changed which
tool answers many questions. The wrong-number benchmark already held its question set, with
routing as one of its columns. Tuning the router against those questions would have made the
benchmark measure the tuning.

**How it was kept honest.** Routing changes are driven by a dev phrasing set
(`tests/routing/test_dev_phrasings.py`), written before each change, independently of the
benchmark's questions and of the tools' own routing examples, with each expectation stating what
the user asked for rather than what the router did. An item the router still gets wrong carries
its reason in a `known` field and runs as a strict expected failure (`xfail(strict=True)`), so a
miss stays measured and a fix cannot go unnoticed: an item that starts passing fails the suite
until its mark is removed. The benchmark's own questions are only ever measured. Every result
file records a digest of the whole question set (`meta.question_set.digest`, printed at the top
of the report), so any edit to a question, its expected values or its tags changes the digest and
a result is tied to the exact set it was scored on.

**Guard.** The dev set with strict expected failures; the question-set digest in every result
file; a changelog in `bench/README.md` for any change to the held-out set, made only when a
capability changes and never to fit a result; tests in `tests/bench/` that rebuild the set from a
fresh store and require the same questions and the same digest, and that enforce the
benchmark's selection rules, so no question repeats a routing example or uses its ids.

**Lesson.** Keep the set you tune on apart from the set you report, write expectations before
the change, and make known misses fail loudly when they stop being misses.

## 4. Two console tests that measured the machine

**What happened.** Two console tests, the audit view reading its filters, page and open entry
from the URL, and the import wizard's walk from upload to done, awaited the screen with Testing
Library's `findBy*` queries, which poll on the real clock until a timeout, and drove it with
`userEvent` on real timers. Whether they passed depended on how quickly the machine got through
the mocked requests, not on the code.

**How it surfaced.** As load-sensitive tests: a run on a slow or busy machine could fail where
a fast one passed. The rewritten tests say what they no longer do; each carries the comment
"nothing here waits on a clock".

**Guard.** Both tests (`web/src/components/audit/audit-view.test.tsx`,
`web/src/components/import/import-wizard.test.tsx`) now run on fake timers and hand over each
mocked server answer explicitly through a `deferred()` helper (`web/test/deferred.ts`), then read
the screen once. The end-to-end spec `web/e2e/hub.spec.ts` does assert wording: the server's
chat-fallback sentence ("No language model is configured, so I can only run the reports this
console knows about."), the trace lines the console words from the server's steps, and the scope
text of a result (`not filtered` or `unfiltered`). A change to any of them should fail the spec.
For the narration of a tool's answer it checks only that a paragraph sits above the table
(`getByRole("paragraph").first()` is not empty), plus the table's region name and its rows, so a
change in the template's wording cannot fail it.

**Lesson.** A test that waits on wall time is partly a benchmark of the machine. Let the test
decide when the server answers, and assert what the code controls.

## 5. A model that quietly became the mock

**What happened.** The routing call of the OpenAI-compatible provider treated every failure as
"no opinion", and a failed narration fell back to the deterministic template. Both are the right
degradations: they are what the zero-key mock does on purpose, abstaining from routing and
narrating from the template ([ADR 0009](decisions/0009-honest-zero-key-mode.md)). But the answer
looked the same whichever way it got there. A configured model that failed on every call, for
instance one that answers HTTP 400 to the temperature twotrack sends, produced answers
indistinguishable from a deployment with no model at all.

**How it surfaced.** By the shape of the code rather than by a failing test: a fallback that
returned the same value as "nothing to say" leaves no mark that a call failed. The tests now
build the case on purpose: a client that refuses every call with HTTP 400, as a model that
rejects the temperature does.

**Guard.** A failed call (an HTTP error, a timeout, a reply that cannot be parsed, which now
counts as a failure and no longer as "no opinion") is recorded with the call, the error class
and the HTTP status, never the message, which can echo the request. The answer carries it as
`model_errors`; the narrate step records it; the console says "Model call failed (narration)…"
or "Model call failed (routing)…" under the answer and "Model call failed: routing (…)" in the
trace, and keeps that apart from "no model configured" (the "No model" pill in the header and
"Template narration (no model configured)" in the trace); MCP results carry it and a benchmark
run that saw one ends as degraded. A half-configured provider stops the server at start-up
instead of falling back to the mock. The exploration runtime follows the same rule: a crash, a
timeout, a malformed line or a failed model call ends the run as an error, and nothing falls
back to the other runtime. Tests: `tests/server/test_model_failures.py`,
`tests/graph/test_model_failures.py`, `tests/bench/test_model_failures.py`,
`tests/explore/test_pi_runs.py`.

**Lesson.** Falling back is fine; falling back silently is not. When a fallback can look like
the success path, or like the "feature is off" path, make it leave a mark that the next layer
can read, and keep "failed" apart from "not configured".

## 6. A build that downloaded its fonts

**What happened.** The console loaded its web fonts through `next/font/google`, which fetches the
font files from a font host while `next build` runs. A build somewhere that cannot reach that
host (a runner without outside access, a container build behind a proxy) failed, although
nothing in the code was wrong. The same build also depended on a third party being up.

**How it surfaced.** As a roadmap item (`next/font/local`) before it was fixed.

**Guard.** The one web font that remains, Libre Baskerville, comes from the pinned npm package
`@fontsource/libre-baskerville` through `next/font/local`, so it arrives with
`pnpm install --frozen-lockfile` and ships inside the build; the Content-Security-Policy says
`font-src 'self'`. The two CJK web fonts gave way to system font stacks, so switching language
downloads nothing. The font's licence travels in `NOTICE`. CI builds both images (the `docker`
job runs `docker compose build`, nothing is pushed). No test builds with the network off: the
guard is that the build's inputs are the lockfile's packages and nothing else it has to fetch.

**Lesson.** A build step that reaches for the network is a dependency nobody declared. Make the
lockfile the only thing a build fetches, and an offline build stops being a special case.

## 7. A guard on one path is not a guard

**What happened.** The design says that a write with weak evidence is confirmed once before its
approval card appears, and that a question, a negation or an exclusion makes the evidence weak
("did we order 200 of SKU-… last week?", "don't order …", "not from SUP-…"). The first fix
stopped complete params from lifting a question to a strong write. The next reports came
through other doors: a negated write lifted by a contest win; a write named by two keywords,
which was strong by keywords whatever the request said, and a write that a model agreed with,
which was strong whatever the request said; a negated write tied below a read, which reached the
approval through the model's pick because negation was read against the top hit alone. Four
fixes closed one invariant, one path at a time.

**How it surfaced.** Through the dev phrasing set (`tests/routing/test_dev_phrasings.py`), which
is written before each change. Each fix left the paths it did not cover as strict expected
failures with their reasons, and the next change closed them.

**Guard.** The cap sits where strength is decided, not on each route to it
(`core/routing/rule_arm.py`): a request that reads as a question, negates the write's phrase or
excludes a value caps a target that changes data at weak on every path to strength (an example,
two keywords, complete params), and the reconcile step keeps the cap when a model agrees
(`core/routing/reconcile.py`). The trace records what held the request (`rule.capped`, with the
path that made it strong and the reason that held it). `tests/routing/test_complete_writes.py`
states the invariant once and drives it down two of the three paths: its `HELD_WRITES` make
`create_po` strong by "keywords" (two cases, a negation and a question) and by "complete_params"
(one case, an excluded supplier) and assert `facts["capped"] == {"from": strong_by, "by": holds}`,
and an external target in the same file is driven by "keywords" only. Nothing there makes a write
strong by an example and then holds it back, and no test asserts a `from` of "example". The
invariant is a shortcut removed, not the authorization control: the control is the one-time
grant.

**Lesson.** Enforce an invariant where the outcome is decided, not at each route that leads to
it, or every new route is a new bypass and the bypasses arrive one report at a time. Write the
test as "for every path to X, Y holds", and list the paths from the code, not from the last bug.

## 8. Budgets are ours, not the harness's

**What happened.** Exploration runs a model in an agent loop, and an unbounded loop is a bill
and an outage. The built-in loop had a budget from the start. Pi's agent loop, as pinned
(Pi 1.2.0), has no turn or step limit at all: left alone, a model can keep calling tools for as
long as it likes, and there was nothing in Pi to configure.

**How it surfaced.** When the worker was written, by reading what the loop offers
(`harness/pi/README.md` records it). Nothing failed first.

**Guard.** The worker adds the limits itself. Every call the model asks for counts against the
call budget, refused ones included. On the last allowed turn, and once the call budget is spent,
a hook blocks the call with a stop instruction, so the host never runs a call whose result no
turn would read; the run ends after the turn that spent a budget; a wall clock aborts the whole
run. The host enforces the call budget and the wall clock again on its side. A forwarded call
past the call budget is refused (traced and audited) and the host sends the worker an abort;
past the clock it sends an abort too. Only then does `worker.stop(GRACE_SECONDS)` close the
worker's stdin and kill its process group, if the worker has not exited, so a worker that
ignores its own limits is stopped after a short grace (`tests/explore/test_fake_worker.py`: the
host enforces the call budget when the worker does not; a silent worker is stopped at the wall
clock). Both runtimes read the same settings (`TWOTRACK_EXPLORE_*`), a spent turn or call budget
is a stop reason on the run, and a run past its clock is a failure. Tests with the real worker:
`tests/explore/test_pi_runs.py`.

**Lesson.** A limit you depend on has to live in code you own. Check that the framework has the
limit you need before assuming it does, enforce it where the work happens and again at the
boundary of the process that does the work, and test the second enforcement with a worker that
cheats.
