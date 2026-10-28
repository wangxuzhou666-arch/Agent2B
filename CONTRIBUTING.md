# Contributing

Read [ARCHITECTURE.md](docs/ARCHITECTURE.md) and the [decision records](docs/decisions/README.md)
first: a change that works against one of them needs a new record, not just a patch.

## Setup

| Tool | Version | Where it is set |
|---|---|---|
| Python | 3.11 or newer; CI runs 3.11, 3.12, 3.13 and 3.14 | `requires-python` in `pyproject.toml`, `.github/workflows/ci.yml` |
| uv | any recent release; CI installs the default of `astral-sh/setup-uv`, the API image pins 0.13.5 | `ci.yml`, `Dockerfile` |
| Node | 24 or newer for the console | `engines` in `web/package.json`, `ci.yml` |
| pnpm | 10.33.0 | `packageManager` in `web/package.json` |
| Node (Pi worker) | 22.19 or newer, only to run or test the exploration worker | `engines` in `harness/pi/package.json`, `MIN_NODE` in `src/twotrack/core/explore/pi.py` |

```sh
uv sync                          # Python dependencies, from uv.lock
cd web && pnpm install --frozen-lockfile
```

The Pi worker is optional; set it up as described under [The Pi worker](#the-pi-worker) when you
work on exploration.

## Run the checks

CI runs exactly these, so run them before you push.

```sh
uv run ruff check .              # lint
uv run pytest                    # Python tests (tests/); the Pi worker's tests skip without it
uv run pytest -m pi              # the tests that run the real worker (see below)

cd web
pnpm lint                        # eslint, no warnings allowed
pnpm typecheck
pnpm check:api                   # the console's API types must match openapi.json
pnpm test                        # vitest
pnpm build
pnpm exec playwright install chromium   # once
pnpm e2e                         # fixed flows, then the exploration case (needs the Pi worker, below); each starts the API (demo mode, mock model) and a production build
```

`make test`, `make lint` and `make e2e` wrap the Python and end-to-end runs. No test needs a model
API key.

The `docker` job builds both images, runs two image guards, starts the API container and runs
`make smoke` against it. The guards fail the job if the console image holds any sharp, `@img` or
libvips files, or if the API image lacks Node's LICENSE. `make up` followed by `make smoke` does
the start-and-smoke part on your machine (Docker and python3, no key):
one question a fixed flow answers, and one that no fixed flow covers, which the real Pi worker
runs against the mock model. `make down` stops the stack.

## The API contract

`openapi.json` at the repository root is the contract between the API and the console. The
console's types are generated from it, so change them in this order:

```sh
uv run twotrack export-openapi   # writes openapi.json from the app (run at the repository root)
cd web && pnpm gen:api           # regenerates web/src/lib/api-types.ts from ../openapi.json
pnpm check:api                   # fails when api-types.ts differs from what gen:api would write
```

Never edit `web/src/lib/api-types.ts` by hand. CI runs `pnpm check:api`, so a change to an
endpoint that does not regenerate the types fails the `web` job.

## The Pi worker

Exploration, the route for a question that no fixed flow covers, can run in
[Pi](https://www.npmjs.com/package/@earendil-works/pi-agent-core) inside a restricted Node
process: `harness/pi` (its README has the line protocol, how the host restricts the worker and
the budgets).
`TWOTRACK_EXPLORER=pi` selects it; the default is the built-in loop, which needs no Node. The
version of Pi is pinned, and the host refuses to start on any other install, so install it from
the lockfile and nothing else:

```sh
cd harness/pi
npm ci --ignore-scripts          # the lockfile's exact tree, install scripts skipped
```

Use `npm ci`, not `npm install`: a changed tree is a different Pi. To bump Pi, change the pins
in `harness/pi/package.json`, regenerate the lockfile, and run the worker's tests.

- **Run the API on it** with no key (the worker talks to the mock model):
  `TWOTRACK_DEMO_MODE=1 TWOTRACK_ALLOW_OPEN_TASK=1 TWOTRACK_EXPLORER=pi uv run twotrack serve`.
  With the install missing, Node too old or the packages not the pinned ones, the API stops at
  start-up and says which.
- **Run its tests**: `uv run pytest -m pi`. Without Node 22.19 or newer and the install above,
  the tests marked `pi` skip, so the rest of the suite stays green on a machine without Node.
  `TWOTRACK_REQUIRE_PI=1` turns that skip into a failure, so a broken install can never pass as
  skipped: CI's `pi` job runs `uv run pytest -m pi` with it set, and so should you when you
  touch the worker.
- **Run the console's exploration specs** (the documented case, approved and rejected, in a
  browser): `cd web && pnpm e2e:explore`. It starts its own API with `TWOTRACK_EXPLORER=pi` and
  `TWOTRACK_ALLOW_OPEN_TASK=1` on the mock model, as `docker-compose.yml` does. `pnpm e2e:fixed`
  runs the other specs, which need no Node worker; `pnpm e2e` runs both. With the install above
  missing, the API of the exploration suite refuses to start and the run fails; it never skips.
- **Point at another install** with `TWOTRACK_PI_HARNESS_DIR=/path/to/harness/pi`; the tests
  read it too.
- The tests use the zero-key mock model or a scripted one. No test needs a model key, and none
  claims anything about a real model.

## Changing the router

Routing changes are measured, and the measuring set must not be the tuning set
([LESSONS.md, section 3](docs/LESSONS.md)):

1. Write the dev phrasings for the change first, in `tests/routing/test_dev_phrasings.py`, each
   expectation stating what the user asked for, not what the router does now.
2. Items the router still gets wrong stay as strict expected failures with their reason; a fix
   that makes one pass fails the suite until its mark is removed.
3. Never tune on the held-out wrong-number benchmark. It changes only when a capability
   changes, with a changelog entry; see [bench/README.md](bench/README.md).

A guard that holds for a question, a negation or an exclusion has to hold on every path to a
strong write, not on the one path you are fixing ([LESSONS.md, section 7](docs/LESSONS.md)).

## Commits and pull requests

Conventional commits: `type(scope): summary` in the imperative, for example
`feat(routing): resolve keyword contests by evidence` or `fix(web): close open redirect`. Types in
use: `feat`, `fix`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`. Keep a commit to one change, with its
tests.

Unless you explicitly state otherwise, a contribution you submit for inclusion is licensed under Apache-2.0, on the terms of section 5 of the [LICENSE](LICENSE).

## The leak scan

`scripts/leak_scan.sh` blocks home paths, public IPv4 addresses, key and token shapes (model
providers, cloud, GitHub, npm, PyPI and a few more) and Gmail addresses, and, for the maintainer,
a private word list. You do not need that list.

- CI runs `bash scripts/leak_scan.sh --all --generic-only`, so a pull request passes without it.
  `make leak-scan` runs the whole-tree scan on your machine.
- Without the private list, which only the maintainer has, the script without `--generic-only`
  and `make leak-scan` print one warning on stderr and scan with the generic patterns; they do
  not fail. `--generic-only` runs the same scan without the warning.
- `.githooks/pre-commit` is optional and a fresh clone does not enable it (that takes
  `git config core.hooksPath .githooks`). It refuses to commit local `.env`, database and Compose
  override files, and runs the scan on the staged changes, which without the private list means
  the warning and the generic patterns. `git commit --no-verify` skips it.
- A hit is printed as `path:line: [pattern-id]` and never shows the matched text. Exit code 0 is
  clean, 1 is a hit, 2 is a configuration error.

The private list is for the maintainer: a file of extended regular expressions, one per line
(blank lines and lines starting with `#` are skipped), at `~/.config/twotrack/denylist.txt` or
wherever `TWOTRACK_DENYLIST` points. It stays out of the repository. On a machine that has it, make a missing, unreadable or
empty list an error (exit code 2) instead of a warning, once for every clone:

```sh
git config --global twotrack.requireDenylist true
```

## Good first issues

- **Write a category plugin.** A new item category is one file, with no core change. Follow
  [docs/ADD_A_CATEGORY.md](docs/ADD_A_CATEGORY.md) and add yours under `examples/plugins/` with
  a test like `tests/plugins/test_drop_in_cold_chain.py`.
- **Write a tool plugin.** A new read tool is one file too, and the same contract applies to a
  fixed flow and to exploration. Follow [docs/ADD_A_TOOL.md](docs/ADD_A_TOOL.md); the model is
  `examples/plugins/supplier_scorecard.py` with `tests/plugins/test_drop_in_supplier_scorecard.py`.
- **Console polish** from the [ROADMAP](ROADMAP.md), the items marked "good first issue": small,
  self-contained changes in `web/src/`, each with a Vitest test.
