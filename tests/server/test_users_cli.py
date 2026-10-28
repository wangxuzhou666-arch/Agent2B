"""``twotrack users disable | enable | roles | passwd``, run against the data directory of a live
runtime. The server reads the users table on every request, so each change must show on the very
next one, for the token the user already holds."""

import io
import sys

import pytest

from tests.server.conftest import PASSWORD, USERS, Api
from twotrack import cli
from twotrack.core import Role
from twotrack.settings import Settings

NEW_PASSWORD = "a-brand-new-secret"


def run_cli(*argv, stdin=""):
    sys.stdin = io.StringIO(stdin)
    return cli.main(list(argv))


@pytest.fixture(autouse=True)
def restore_stdin():
    original = sys.stdin
    yield
    sys.stdin = original


def users_cli(api: Api, *argv, stdin=""):
    """``twotrack users ...`` on the data directory the runtime under test is serving."""
    return run_cli("--data-dir", str(api.runtime.settings.data_dir), "users", *argv, stdin=stdin)


def sign_in(api: Api, username, password):
    return api.client.post("/api/auth/login", json={"username": username, "password": password})


def me(api: Api, headers):
    return api.client.get("/api/auth/me", headers=headers)


def audit_status(api: Api, headers):
    return api.client.get("/api/audit", headers=headers).status_code


def answer_prompts(monkeypatch, *answers):
    """Make ``getpass`` hand out ``answers`` in turn; returns the prompts it was shown."""
    shown, remaining = [], iter(answers)

    def fake_getpass(prompt="Password: "):
        shown.append(prompt)
        return next(remaining)

    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)
    return shown


# ---------------------------------------------------------------- disable and enable


def test_disable_refuses_the_token_the_user_holds_and_enable_brings_it_back(api, capsys):
    headers = api.headers("olga")
    assert me(api, headers).status_code == 200

    assert users_cli(api, "disable", "olga") == 0
    assert capsys.readouterr().out == "disabled olga\n"
    refused = me(api, headers)  # the same token, on the very next request
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "invalid_token"
    assert sign_in(api, "olga", PASSWORD).status_code == 401  # the right password does not help
    assert me(api, api.headers("omar")).status_code == 200  # nobody else is affected
    assert users_cli(api, "list") == 0
    listed = {line.split("\t")[0]: line for line in capsys.readouterr().out.splitlines()}
    assert listed["olga"].endswith("(disabled)") and "(disabled)" not in listed["omar"]

    assert users_cli(api, "enable", "olga") == 0
    assert capsys.readouterr().out == "enabled olga\n"
    assert me(api, headers).status_code == 200  # disabling refused the token, it did not revoke it
    assert sign_in(api, "olga", PASSWORD).status_code == 200


def test_repeating_disable_or_enable_changes_nothing_and_is_not_an_error(api, capsys):
    headers = api.headers("olga")
    assert users_cli(api, "enable", "olga") == 0  # already on
    assert me(api, headers).status_code == 200
    for _ in range(2):
        assert users_cli(api, "disable", "olga") == 0
        assert me(api, headers).status_code == 401
    for _ in range(2):
        assert users_cli(api, "enable", "olga") == 0
        assert me(api, headers).status_code == 200
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- unknown users


@pytest.mark.parametrize("command", [
    ["disable", "ghost"],
    ["enable", "ghost"],
    ["roles", "ghost", "viewer"],
    ["passwd", "ghost", "--password-stdin"],
], ids=lambda command: command[0])
def test_an_unknown_user_is_exit_1_and_nothing_is_created(api, capsys, command):
    assert users_cli(api, *command, stdin=NEW_PASSWORD + "\n") == 1
    captured = capsys.readouterr()
    assert captured.err == "error: no user 'ghost'\n" and captured.out == ""
    assert [u.user_id for u in api.runtime.users.list()] == sorted(USERS)


def test_passwd_for_an_unknown_user_does_not_ask_for_a_password(api, monkeypatch, capsys):
    prompts = answer_prompts(monkeypatch)  # no answers: a prompt would end in StopIteration
    assert users_cli(api, "passwd", "ghost") == 1
    assert capsys.readouterr().err == "error: no user 'ghost'\n" and prompts == []


# ---------------------------------------------------------------- roles


def test_roles_reach_a_token_the_user_already_holds_and_replace_the_old_ones(api, capsys):
    headers = api.headers("vera")  # signed in as a viewer; /api/audit is for operators and admins
    assert audit_status(api, headers) == 403
    steps = [(["operator"], 200), (["viewer"], 403), (["admin"], 200),
             (["viewer", "operator"], 200)]
    for roles, audit_expected in steps:
        assert users_cli(api, "roles", "vera", *roles) == 0
        assert capsys.readouterr().out == f"vera now has roles {', '.join(sorted(roles))}\n"
        assert me(api, headers).json()["roles"] == sorted(roles)  # exactly these, not added to
        assert audit_status(api, headers) == audit_expected
    assert api.runtime.users.get("omar").roles == (Role.OPERATOR,)  # nobody else changed


def test_a_repeated_role_is_stored_once(api, capsys):
    assert users_cli(api, "roles", "vera", "viewer", "operator", "viewer", "operator") == 0
    assert capsys.readouterr().out == "vera now has roles operator, viewer\n"
    # the session's role set would hide a stored repeat, so look at the record and the listing
    assert api.runtime.users.get("vera").roles == (Role.OPERATOR, Role.VIEWER)
    assert users_cli(api, "list") == 0
    assert "vera\toperator,viewer\t" in capsys.readouterr().out


@pytest.mark.parametrize(("roles", "complaint"), [
    (["root"], "invalid choice: 'root'"),
    (["viewer", "root"], "invalid choice: 'root'"),
    ([], "the following arguments are required: ROLE"),
], ids=["unknown", "one good and one unknown", "none"])
def test_roles_refuses_an_unknown_role_or_none_and_changes_nothing(api, capsys, roles, complaint):
    with pytest.raises(SystemExit) as stopped:
        users_cli(api, "roles", "ada", *roles)
    assert stopped.value.code == 2 and complaint in capsys.readouterr().err
    assert api.runtime.users.get("ada").roles == (Role.ADMIN,)


# ---------------------------------------------------------------- passwd


def test_passwd_replaces_the_password_and_leaves_issued_tokens_alone(api, capsys):
    headers = api.headers("olga")
    assert users_cli(api, "passwd", "olga", "--password-stdin", stdin=NEW_PASSWORD + "\n") == 0
    captured = capsys.readouterr()
    assert captured.out == "changed the password of olga\n"
    assert NEW_PASSWORD not in captured.out + captured.err
    assert sign_in(api, "olga", PASSWORD).status_code == 401
    assert sign_in(api, "olga", NEW_PASSWORD).status_code == 200
    assert me(api, headers).status_code == 200  # issued before the change, still good
    assert sign_in(api, "omar", PASSWORD).status_code == 200  # nobody else's password moved


@pytest.mark.parametrize("typed", ["", "short", "a" * 9], ids=["empty", "short", "9 characters"])
def test_passwd_refuses_nine_characters_or_fewer_and_keeps_the_old_password(api, capsys, typed):
    assert users_cli(api, "passwd", "olga", "--password-stdin", stdin=typed + "\n") == 1
    assert capsys.readouterr().err == "error: password must be at least 10 characters\n"
    assert sign_in(api, "olga", PASSWORD).status_code == 200


def test_passwd_accepts_ten_characters(api, capsys):
    ten = "a" * 10
    assert users_cli(api, "passwd", "olga", "--password-stdin", stdin=ten + "\n") == 0
    assert capsys.readouterr().err == ""
    assert sign_in(api, "olga", PASSWORD).status_code == 401
    assert sign_in(api, "olga", ten).status_code == 200


def test_passwd_without_the_flag_prompts_twice_and_takes_a_matching_pair(
        api, monkeypatch, capsys):
    prompts = answer_prompts(monkeypatch, NEW_PASSWORD, NEW_PASSWORD)
    assert users_cli(api, "passwd", "olga", stdin="never read\n") == 0
    assert prompts == ["Password: ", "Repeat password: "]
    captured = capsys.readouterr()
    assert captured.out == "changed the password of olga\n" and captured.err == ""
    assert sign_in(api, "olga", NEW_PASSWORD).status_code == 200


def test_passwd_with_two_different_entries_changes_nothing(api, monkeypatch, capsys):
    answer_prompts(monkeypatch, NEW_PASSWORD, NEW_PASSWORD + "x")
    assert users_cli(api, "passwd", "olga") == 1
    assert capsys.readouterr().err == "error: the two passwords differ\n"
    assert sign_in(api, "olga", NEW_PASSWORD).status_code == 401
    assert sign_in(api, "olga", PASSWORD).status_code == 200


def test_the_prompted_password_meets_the_same_length_rule(api, monkeypatch, capsys):
    answer_prompts(monkeypatch, "a" * 9, "a" * 9)
    assert users_cli(api, "passwd", "olga") == 1
    assert capsys.readouterr().err == "error: password must be at least 10 characters\n"
    assert sign_in(api, "olga", PASSWORD).status_code == 200


# ---------------------------------------------------------------- the help says what tokens do

# Each claim is one the tests above hold the command to: a disabled user's token is refused and
# comes back on enable, the roles of an existing token change, an old token outlives passwd.
HELP_CLAIMS = {
    "disable": ["every token already issued to them is refused", "Nothing is deleted or revoked",
                "the tokens that have not expired work again"],
    "enable": ["work again if they have not expired", "it does not revoke them"],
    "roles": ["carries no roles", "get the new roles from the next request on"],
    "passwd": ["Existing tokens are not revoked", "stays valid until it expires",
               "TWOTRACK_TOKEN_TTL_SECONDS, {hours} hours by default", "run `users disable` first"],
}


@pytest.mark.parametrize("command", sorted(HELP_CLAIMS))
def test_the_help_says_what_happens_to_tokens(command, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "80")  # argparse wraps at the terminal width; pin it
    with pytest.raises(SystemExit) as stopped:
        run_cli("users", command, "-h")
    assert stopped.value.code == 0
    text = " ".join(capsys.readouterr().out.split())  # undo the wrapping
    hours = Settings.from_env({}).token_ttl_seconds // 3600  # the default the help quotes
    for claim in HELP_CLAIMS[command]:
        assert claim.format(hours=hours) in text, claim
