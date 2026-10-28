"""The ``twotrack`` command: serve, manage users, seed demo data, export the API contract, run
the scripted demo, and serve the tools to an MCP client.

Settings come from ``TWOTRACK_*`` environment variables; ``--data-dir`` overrides the data
directory for one invocation. Passwords are never taken from the command line (it would show
in the process list): they are prompted for, or read from stdin with ``--password-stdin``.
"""

import argparse
import functools
import getpass
import importlib.util
import json
import logging
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from twotrack.core import ROLES, RegistryError
from twotrack.core.secrets import SecretFileError
from twotrack.core.store import StoreError
from twotrack.settings import Settings, SettingsError

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@contextmanager
def _startup_errors() -> Iterator[None]:
    """A broken plugin, schema, secret file or provider setting is a configuration problem the
    operator fixes: say so in one message instead of a traceback. (ValueError covers
    ``provider_from_env``, which refuses half-configured providers on purpose.)"""
    try:
        yield
    except (RegistryError, StoreError, SecretFileError, ValueError) as exc:
        raise SystemExit(f"error: cannot start: {exc}") from None


def _log_to_stderr() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    package_logger = logging.getLogger("twotrack")
    package_logger.setLevel(logging.INFO)
    package_logger.addHandler(handler)


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    return replace(settings, data_dir=args.data_dir) if args.data_dir else settings


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from twotrack.server.app import create_app

    settings = _settings(args)
    if settings.demo_mode and args.host not in LOOPBACK_HOSTS:
        print("warning: demo mode lets anyone who can reach this port sign in as an admin "
              "without a password; do not expose it to an untrusted network",
              file=sys.stderr)
    _log_to_stderr()
    with _startup_errors():
        app = create_app(settings)
    uvicorn.run(app, host=args.host, port=args.port, server_header=False)
    return 0


def _open_users(settings: Settings):
    from twotrack.core.database import Database
    from twotrack.runtime import prepare_data_dir
    from twotrack.server.auth import UserStore

    prepare_data_dir(settings)
    database = Database(settings.db_path)
    return database, UserStore(database)


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise ValueError("the two passwords differ")
    return first


def _users_add(args: argparse.Namespace) -> int:
    from twotrack.server.auth import UserExistsError

    database, users = _open_users(_settings(args))
    try:
        user = users.add(args.name, _read_password(args.password_stdin), args.role)
    except (ValueError, UserExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        database.close()
    print(f"created {user.user_id} ({', '.join(r.value for r in user.roles)})")
    return 0


def _users_list(args: argparse.Namespace) -> int:
    database, users = _open_users(_settings(args))
    try:
        records = users.list()
    finally:
        database.close()
    for user in records:
        flag = " (disabled)" if user.disabled else ""
        roles = ",".join(r.value for r in user.roles)
        print(f"{user.user_id}\t{roles}\t{user.created_at:%Y-%m-%d}{flag}")
    return 0


def _run_user_change(change: Callable[..., str], args: argparse.Namespace) -> int:
    """Open the users table, apply ``change(users, args)`` (it returns the line to print) and
    close it. A refusal is one ``error:`` line and status 1, like ``users add``."""
    from twotrack.server.auth import UserNotFoundError

    database, users = _open_users(_settings(args))
    try:
        done = change(users, args)
    except (ValueError, UserNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        database.close()
    print(done)
    return 0


def _disable(users, args: argparse.Namespace) -> str:
    return f"disabled {users.set_disabled(args.name, True).user_id}"


def _enable(users, args: argparse.Namespace) -> str:
    return f"enabled {users.set_disabled(args.name, False).user_id}"


def _set_roles(users, args: argparse.Namespace) -> str:
    user = users.set_roles(args.name, args.roles)
    return f"{user.user_id} now has roles {', '.join(r.value for r in user.roles)}"


def _set_password(users, args: argparse.Namespace) -> str:
    from twotrack.server.auth import UserNotFoundError

    if users.get(args.name) is None:  # before asking for a password nobody can use
        raise UserNotFoundError(f"no user '{args.name}'")
    users.set_password(args.name, _read_password(args.password_stdin))
    return f"changed the password of {args.name}"


def _seed(args: argparse.Namespace) -> int:
    from twotrack.core import load_registry
    from twotrack.runtime import open_store

    if importlib.util.find_spec("twotrack.seed") is None:
        print("error: the seed module is not installed", file=sys.stderr)
        return 1
    from twotrack.seed import seed_database

    settings = _settings(args)
    with _startup_errors():
        registry = load_registry()
        database, store = open_store(settings, registry)
    try:
        counts = seed_database(store, registry, rng_seed=args.rng_seed, scale=args.scale)
    finally:
        database.close()
    print(json.dumps(counts, sort_keys=True))
    return 0


def _export_openapi(args: argparse.Namespace) -> int:
    from twotrack.server.app import create_app

    spec = create_app(None).openapi()
    Path(args.out).write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def _demo(args: argparse.Namespace) -> int:
    from twotrack.demo import run_demo

    return run_demo()


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _mcp(args: argparse.Namespace) -> int:
    """stdio MCP server. stdout carries the protocol, so everything else goes to stderr."""
    import anyio

    from twotrack.mcp import IdentityError, McpOptions, TwotrackMcp, resolve_identity, serve_stdio
    from twotrack.runtime import build_runtime

    if not args.user and not args.role:  # refuse before touching any data
        print("error: twotrack mcp needs an identity: --user <username>, or --role "
              "admin|operator|viewer in demo mode", file=sys.stderr)
        return 2
    options = McpOptions.from_env(os.environ)
    options = replace(options, include_rows=options.include_rows or args.rows,
                      max_rows=args.max_rows or options.max_rows, ask_tool=not args.no_ask)
    settings = _settings(args)
    if args.role and not settings.demo_mode:
        print("error: cannot start: --role is only accepted in demo mode "
              "(TWOTRACK_DEMO_MODE=1); outside it use --user <username>", file=sys.stderr)
        return 2
    _log_to_stderr()
    with _startup_errors():
        runtime = build_runtime(settings)
    try:
        try:
            identity = resolve_identity(runtime, user=args.user, role=args.role)
        except IdentityError as exc:
            print(f"error: cannot start: {exc}", file=sys.stderr)
            return 2
        anyio.run(serve_stdio, TwotrackMcp(runtime, identity, options))
    finally:
        runtime.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="twotrack", description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, help="override TWOTRACK_DATA_DIR")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(run=_serve)

    users = sub.add_parser("users", help="manage sign-in accounts").add_subparsers(
        dest="users_command", required=True)
    add = users.add_parser("add", help="create a user")
    add.add_argument("name")
    add.add_argument("--role", action="append", required=True, choices=sorted(ROLES),
                     help="repeat for several roles")
    add.add_argument("--password-stdin", action="store_true",
                     help="read the password from the first line of stdin")
    add.set_defaults(run=_users_add)
    users.add_parser("list", help="list users").set_defaults(run=_users_list)

    # The changes below take effect on the next request of a running server (it reads the users
    # table every time), and each says what happens to tokens already issued.
    disable = users.add_parser(
        "disable", help="turn an account off, at once",
        description="Turn an account off. From the next request on, the user cannot sign in and "
                    "every token already issued to them is refused. Nothing is deleted or "
                    "revoked: `users enable` turns the account back on, and the tokens that "
                    "have not expired work again.")
    disable.add_argument("name")
    disable.set_defaults(run=functools.partial(_run_user_change, _disable))
    enable = users.add_parser(
        "enable", help="turn a disabled account back on",
        description="Turn a disabled account back on, at once. Tokens issued to the user before "
                    "it was disabled work again if they have not expired: disabling refuses "
                    "them, it does not revoke them.")
    enable.add_argument("name")
    enable.set_defaults(run=functools.partial(_run_user_change, _enable))
    roles = users.add_parser(
        "roles", help="replace a user's roles",
        description="Replace the user's roles with exactly those listed. A token names the user "
                    "and carries no roles, so the user's existing tokens get the new roles from "
                    "the next request on.")
    roles.add_argument("name")
    roles.add_argument("roles", nargs="+", choices=sorted(ROLES), metavar="ROLE",
                       help="one or more of: " + ", ".join(sorted(ROLES)))
    roles.set_defaults(run=functools.partial(_run_user_change, _set_roles))
    passwd = users.add_parser(
        "passwd", help="set a user's password",
        description="Set a new password for the user: prompted for twice, or the first line of "
                    "stdin with --password-stdin, under the same rules as `users add`. Existing "
                    "tokens are not revoked: a token issued before the change stays valid until "
                    "it expires (TWOTRACK_TOKEN_TTL_SECONDS, 8 hours by default). To refuse "
                    "them meanwhile, run `users disable` first; `users enable` brings back the "
                    "ones that have not expired.")
    passwd.add_argument("name")
    passwd.add_argument("--password-stdin", action="store_true",
                        help="read the password from the first line of stdin")
    passwd.set_defaults(run=functools.partial(_run_user_change, _set_password))

    seed = sub.add_parser("seed", help="load deterministic demo data (idempotent)")
    seed.add_argument("--rng-seed", type=int, default=42)
    seed.add_argument("--scale", type=float, default=1.0)
    seed.set_defaults(run=_seed)

    export = sub.add_parser("export-openapi", help="write the OpenAPI contract")
    export.add_argument("--out", default="openapi.json")
    export.set_defaults(run=_export_openapi)

    sub.add_parser("demo", help="scripted walkthrough in process (mock provider, temporary "
                                "data, no server)").set_defaults(run=_demo)

    mcp = sub.add_parser("mcp", help="serve the tools to an MCP client over stdio")
    who = mcp.add_mutually_exclusive_group()
    who.add_argument("--user", help="act as this existing user; roles come from the users table")
    who.add_argument("--role", choices=sorted(ROLES),
                     help="demo mode only: act as the built-in demo user of this role")
    mcp.add_argument("--rows", action="store_true",
                     help="include table rows in results (capped; restricted fields always "
                          "removed). Default: no rows. Env: TWOTRACK_MCP_ROWS=1")
    mcp.add_argument("--max-rows", type=_positive, default=None,
                     help="row cap with --rows (default 50; env TWOTRACK_MCP_MAX_ROWS)")
    mcp.add_argument("--no-ask", action="store_true",
                     help="do not offer the natural-language 'ask' tool")
    mcp.set_defaults(run=_mcp)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
