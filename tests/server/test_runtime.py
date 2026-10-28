"""Assembly, settings and the CLI."""

import importlib.machinery
import importlib.util
import io
import logging
import sys
import types
from pathlib import Path

import pytest

from tests.server.conftest import PASSWORD, Api
from tests.server.fixtures.stock import PLUGIN
from twotrack import cli
from twotrack.core import EntitySpec, FieldSpec, Plugin, build_registry
from twotrack.core.store import SchemaMismatchError
from twotrack.runtime import build_runtime
from twotrack.settings import Settings, SettingsError, parse_origins

# ---------------------------------------------------------------- settings


def test_defaults_are_safe():
    settings = Settings.from_env({})
    assert settings.demo_mode is False and settings.cors_origins == ()
    assert settings.allowed_hosts == ()
    assert settings.allow_open_task is False
    assert settings.data_dir == Path("data") and settings.sse_heartbeat_seconds == 15.0
    assert settings.token_ttl_seconds == 8 * 3600


def test_env_overrides():
    settings = Settings.from_env({
        "TWOTRACK_DATA_DIR": "/srv/tt", "TWOTRACK_DEMO_MODE": "1",
        "TWOTRACK_CORS_ORIGINS": "https://a.example.test, http://localhost:3000/",
        "TWOTRACK_ALLOWED_HOSTS": "App.Example.test, 10.0.0.5,[fd00::5],, app.example.test",
        "TWOTRACK_ALLOW_OPEN_TASK": "true", "TWOTRACK_MAX_UPLOAD_BYTES": "5000",
        "TWOTRACK_MAX_BODY_BYTES": "9000", "TWOTRACK_TOKEN_TTL_SECONDS": "600",
        "TWOTRACK_SSE_HEARTBEAT_SECONDS": "2.5"})
    assert settings.data_dir == Path("/srv/tt") and settings.demo_mode and settings.allow_open_task
    assert settings.cors_origins == ("https://a.example.test", "http://localhost:3000")
    assert settings.allowed_hosts == ("app.example.test", "10.0.0.5", "[fd00::5]")
    assert (settings.max_upload_bytes, settings.max_body_bytes) == (5000, 9000)
    assert settings.token_ttl_seconds == 600 and settings.sse_heartbeat_seconds == 2.5
    assert settings.db_path == Path("/srv/tt/twotrack.db")


@pytest.mark.parametrize("env", [
    {"TWOTRACK_DEMO_MODE": "maybe"},
    {"TWOTRACK_CORS_ORIGINS": "*"},
    {"TWOTRACK_CORS_ORIGINS": "https://a.example.test/path"},
    {"TWOTRACK_CORS_ORIGINS": "ftp://a.example.test"},
    {"TWOTRACK_ALLOWED_HOSTS": "*"},  # a wildcard would switch the check off
    {"TWOTRACK_ALLOWED_HOSTS": "*.example.test"},
    {"TWOTRACK_ALLOWED_HOSTS": "https://app.example.test"},
    {"TWOTRACK_ALLOWED_HOSTS": "app.example.test:8000"},  # any port is accepted: no port here
    {"TWOTRACK_ALLOWED_HOSTS": "app.example.test/path"},
    {"TWOTRACK_ALLOWED_HOSTS": "good.example.test, ::1"},  # an IPv6 address needs brackets
    {"TWOTRACK_ALLOWED_HOSTS": "[::1]:8000"},
    {"TWOTRACK_ALLOWED_HOSTS": "-bad.example"},
    {"TWOTRACK_MAX_UPLOAD_BYTES": "lots"},
    {"TWOTRACK_TOKEN_TTL_SECONDS": "5"},
    {"TWOTRACK_TOKEN_TTL_SECONDS": "inf"},  # no ttl is not a ttl (and int(inf) would crash)
    {"TWOTRACK_MAX_BODY_BYTES": "nan"},
    {"TWOTRACK_SSE_HEARTBEAT_SECONDS": "nan"},  # nan passes every `<` check
])
def test_bad_values_name_the_variable(env):
    with pytest.raises(SettingsError, match="TWOTRACK_"):
        Settings.from_env(env)


@pytest.mark.parametrize("raw", ["", " ", " , ,"])
def test_an_empty_host_list_is_no_list(raw):
    """Compose passes an unset variable as an empty one; that must read as unset."""
    assert Settings.from_env({"TWOTRACK_ALLOWED_HOSTS": raw}).allowed_hosts == ()


def test_origins_are_deduplicated():
    assert parse_origins("https://a.test,https://a.test/") == ("https://a.test",)


# ---------------------------------------------------------------- assembly


def test_one_database_is_shared_so_a_write_and_its_audit_commit_together(runtime):
    shared = {runtime.store.db, runtime.audit.db, runtime.grants.db, runtime.users.db,
              runtime.approvals.db, runtime.database}
    assert len(shared) == 1


def test_the_guarded_pieces_are_wired_in(runtime):
    assert runtime.engine.scrubber is runtime.egress
    assert runtime.engine.registry is runtime.registry
    assert runtime.importer.store is runtime.store and runtime.pipeline.store is runtime.store
    assert runtime.engine.pipeline.inner is runtime.pipeline


def test_a_bad_database_stops_the_start(runtime):
    slim = EntitySpec(id="item", label="Item", primary_key="sku",
                      fields=(FieldSpec(id="sku", label="SKU"),))
    with pytest.raises(SchemaMismatchError, match="does not match the registry"):
        build_runtime(runtime.settings, registry=build_registry(
            [Plugin(name="slim", version="0.1.0", entities=(slim,))]))


def test_provider_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TWOTRACK_PROVIDER", "openai_compat")
    monkeypatch.delenv("TWOTRACK_LLM_MODEL", raising=False)
    with pytest.raises(ValueError, match="TWOTRACK_LLM_MODEL"):
        build_runtime(Settings(data_dir=tmp_path / "d"), registry=build_registry([PLUGIN]))


def test_open_task_setting_reaches_the_engine(make_runtime):
    assert make_runtime().engine.settings.allow_open_task is False


# ---------------------------------------------------------------- demo seeding


def stub_seed(monkeypatch, behaviour):
    calls = []
    module = types.ModuleType("twotrack.seed")
    module.__spec__ = importlib.machinery.ModuleSpec("twotrack.seed", None)

    def seed_database(store, registry, **kwargs):
        calls.append((store, registry, kwargs))
        return behaviour(store)

    module.seed_database = seed_database
    monkeypatch.setitem(sys.modules, "twotrack.seed", module)
    return calls


def test_demo_mode_seeds_an_empty_store_once(tmp_path, monkeypatch):
    calls = stub_seed(monkeypatch, lambda store: {"item": 1})
    settings = Settings(data_dir=tmp_path / "d", demo_mode=True)
    registry = build_registry([PLUGIN])
    runtime = build_runtime(settings, registry=registry)
    assert len(calls) == 1 and calls[0][1] is registry
    runtime.store.upsert_many("item", [{"sku": "S-1", "name": "x"}])
    runtime.close()
    build_runtime(settings, registry=registry).close()  # the store has rows now
    assert len(calls) == 1


def test_seeding_is_for_demo_mode_only(tmp_path, monkeypatch):
    calls = stub_seed(monkeypatch, lambda store: {})
    build_runtime(Settings(data_dir=tmp_path / "d"), registry=build_registry([PLUGIN])).close()
    assert calls == []


def test_a_missing_seed_module_is_skipped(tmp_path, monkeypatch, caplog):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "twotrack.seed" else real(name, *a))
    caplog.set_level(logging.WARNING)
    runtime = build_runtime(Settings(data_dir=tmp_path / "d", demo_mode=True),
                            registry=build_registry([PLUGIN]))
    runtime.close()
    assert "not installed" in caplog.text


def test_a_registry_the_seed_does_not_fit_starts_empty(tmp_path, monkeypatch, caplog):
    def refuse(store):
        raise ValueError("the registry lacks the built-in entities ['sku']")

    stub_seed(monkeypatch, refuse)
    caplog.set_level(logging.WARNING)
    runtime = build_runtime(Settings(data_dir=tmp_path / "d", demo_mode=True),
                            registry=build_registry([PLUGIN]))
    try:
        assert runtime.store.count("item") == 0 and "not seeding" in caplog.text
    finally:
        runtime.close()


def test_other_seed_failures_stop_the_start(tmp_path, monkeypatch):
    def broken(store):
        raise RuntimeError("seed bug")

    stub_seed(monkeypatch, broken)
    with pytest.raises(RuntimeError, match="seed bug"):
        build_runtime(Settings(data_dir=tmp_path / "d", demo_mode=True),
                      registry=build_registry([PLUGIN]))


# ---------------------------------------------------------------- CLI


def run_cli(*argv, stdin=""):
    sys.stdin = io.StringIO(stdin)
    return cli.main(list(argv))


@pytest.fixture(autouse=True)
def restore_stdin():
    original = sys.stdin
    yield
    sys.stdin = original


def test_users_add_and_list_then_login(tmp_path, capsys):
    data = str(tmp_path / "cli")
    assert run_cli("--data-dir", data, "users", "add", "alice", "--role", "operator",
                   "--role", "viewer", "--password-stdin", stdin=PASSWORD + "\n") == 0
    assert "created alice" in capsys.readouterr().out
    assert run_cli("--data-dir", data, "users", "list") == 0
    line = capsys.readouterr().out.strip().split("\t")
    assert line[0] == "alice" and line[1] == "operator,viewer"
    runtime = build_runtime(Settings(data_dir=Path(data)), registry=build_registry([PLUGIN]))
    try:
        ok = Api(runtime).client.post("/api/auth/login",
                                      json={"username": "alice", "password": PASSWORD})
        assert ok.status_code == 200 and ok.json()["user"]["roles"] == ["operator", "viewer"]
    finally:
        runtime.close()


def test_users_add_refuses_bad_input(tmp_path, capsys):
    data = str(tmp_path / "cli")
    args = ("--data-dir", data, "users", "add", "bob", "--role", "viewer", "--password-stdin")
    assert run_cli(*args, stdin="short\n") == 1
    assert "at least" in capsys.readouterr().err
    assert run_cli(*args, stdin=PASSWORD + "\n") == 0
    assert run_cli(*args, stdin=PASSWORD + "\n") == 1
    assert "already exists" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # an unknown role never reaches the store
        run_cli("--data-dir", data, "users", "add", "eve", "--role", "root", "--password-stdin")


def test_the_password_is_not_a_command_line_option(tmp_path):
    with pytest.raises(SystemExit):
        run_cli("--data-dir", str(tmp_path), "users", "add", "eve", "--role", "viewer",
                "--password", "x")


def test_serve_binds_to_loopback_by_default_and_warns_about_demo(tmp_path, monkeypatch, capsys):
    import uvicorn

    started = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw))
    monkeypatch.setattr("twotrack.runtime.load_registry", lambda: build_registry([PLUGIN]))
    monkeypatch.setattr("twotrack.cli.Settings.from_env",
                        classmethod(lambda cls, env=None: Settings(data_dir=tmp_path / "d")))
    assert run_cli("serve") == 0
    assert started == {"host": "127.0.0.1", "port": 8000, "server_header": False}
    assert capsys.readouterr().err == ""
    monkeypatch.setattr("twotrack.cli.Settings.from_env", classmethod(
        lambda cls, env=None: Settings(data_dir=tmp_path / "d2", demo_mode=True)))
    assert run_cli("serve", "--host", "0.0.0.0") == 0  # noqa: S104 - the case under test
    assert "demo mode" in capsys.readouterr().err


def test_a_bad_environment_exits_with_the_variable_name(monkeypatch, capsys):
    monkeypatch.setenv("TWOTRACK_DEMO_MODE", "perhaps")
    assert run_cli("users", "list") == 2
    assert "TWOTRACK_DEMO_MODE" in capsys.readouterr().err


def test_a_configuration_problem_at_startup_is_one_line_not_a_traceback(
        tmp_path, monkeypatch, capsys):
    import uvicorn

    from twotrack.core import RegistryError

    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append(app))
    monkeypatch.setattr("twotrack.cli.Settings.from_env",
                        classmethod(lambda cls, env=None: Settings(data_dir=tmp_path / "d")))

    def broken():
        raise RegistryError(["plugin 'x' failed to import"])

    monkeypatch.setattr("twotrack.runtime.load_registry", broken)
    with pytest.raises(SystemExit) as stopped:
        run_cli("serve")
    assert "cannot start" in str(stopped.value) and "failed to import" in str(stopped.value)
    assert served == []

    monkeypatch.setattr("twotrack.runtime.load_registry", lambda: build_registry([PLUGIN]))
    monkeypatch.setenv("TWOTRACK_PROVIDER", "openai_compat")
    monkeypatch.delenv("TWOTRACK_LLM_MODEL", raising=False)
    with pytest.raises(SystemExit) as stopped:
        run_cli("serve")
    assert "TWOTRACK_LLM_MODEL" in str(stopped.value) and served == []


def test_a_bad_model_timeout_stops_the_server_and_names_the_variable(
        tmp_path, monkeypatch):
    import uvicorn

    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append(app))
    monkeypatch.setattr("twotrack.runtime.load_registry", lambda: build_registry([PLUGIN]))
    monkeypatch.setattr("twotrack.cli.Settings.from_env",
                        classmethod(lambda cls, env=None: Settings(data_dir=tmp_path / "d")))
    monkeypatch.setenv("TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS", "0")  # the mock provider is selected
    with pytest.raises(SystemExit) as stopped:
        run_cli("serve")
    assert "cannot start" in str(stopped.value)
    assert "TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS must be at least 0.1" in str(stopped.value)
    assert served == []


@pytest.mark.parametrize("env", [
    pytest.param({"TWOTRACK_PROVIDER": "openai_compat"}, id="real provider without a model"),
    pytest.param({"TWOTRACK_PROVIDER": "nonesuch"}, id="unknown provider"),
    pytest.param({"TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS": "0"}, id="bad timeout"),
    pytest.param({"TWOTRACK_LLM_TEMPERATURE": "9"}, id="bad temperature"),
])
def test_a_start_the_provider_settings_refuse_creates_no_data_directory(
        tmp_path, monkeypatch, env):
    """The provider is checked before the database is opened: a refused start leaves neither
    the data directory nor twotrack.db behind (the next start would find an empty database)."""
    monkeypatch.delenv("TWOTRACK_LLM_MODEL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(ValueError):
        build_runtime(settings, registry=build_registry([PLUGIN]))
    assert not settings.data_dir.exists()


def test_a_start_the_exploration_settings_refuse_creates_no_data_directory(tmp_path):
    for options in ({"explore_tools": ("nonesuch",)},
                    {"explorer": "pi", "pi_harness_dir": tmp_path / "no-worker"}):
        settings = Settings(data_dir=tmp_path / "data", **options)
        with pytest.raises(SettingsError):
            build_runtime(settings, registry=build_registry([PLUGIN]))
        assert not settings.data_dir.exists(), options


def test_seed_command_passes_its_options_and_reports_counts(tmp_path, monkeypatch, capsys):
    calls = stub_seed(monkeypatch, lambda store: {"item": 3})
    monkeypatch.setattr("twotrack.core.load_registry", lambda: build_registry([PLUGIN]))
    assert run_cli("--data-dir", str(tmp_path / "s"), "seed", "--rng-seed", "7",
                   "--scale", "0.5") == 0
    assert calls[0][2] == {"rng_seed": 7, "scale": 0.5}
    assert capsys.readouterr().out.strip() == '{"item": 3}'


def test_seed_command_without_the_seed_module_says_so(tmp_path, monkeypatch, capsys):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "twotrack.seed" else real(name, *a))
    assert run_cli("--data-dir", str(tmp_path / "s"), "seed") == 1
    assert "seed module is not installed" in capsys.readouterr().err
