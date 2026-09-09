"""Tests for scp-api-explore.py: presentation helpers, the confirm-gate on
mutating actions, and dispatch against a FakeClient - all local, no live
netcup calls."""
from __future__ import annotations

import json
import types

import pytest


# --- presentation helpers ---------------------------------------------------

def test_stringify_none_bool_dict(explore_mod):
    assert explore_mod._stringify(None) == ""
    assert explore_mod._stringify(True) == "yes"
    assert explore_mod._stringify(False) == "no"
    assert explore_mod._stringify(123) == "123"
    assert json.loads(explore_mod._stringify({"a": 1})) == {"a": 1}


def test_stringify_strips_control_chars_and_newlines(explore_mod):
    # A hostname/name field containing a raw ANSI escape or embedded
    # newline must never reach the terminal / break table alignment
    # (review finding, 2026-09-09).
    assert explore_mod._stringify("evil\x1b[2J\x1b[31mPWNED") == "evil[2J[31mPWNED"
    assert explore_mod._stringify("normal\nhost\ttabbed") == "normalhosttabbed"
    assert explore_mod._stringify("plain text") == "plain text"


def test_color_enabled_respects_no_color_env(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert explore_mod._color_enabled(no_color_flag=False) is False


def test_color_enabled_respects_flag(explore_mod, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: True)
    assert explore_mod._color_enabled(no_color_flag=True) is False


def test_color_enabled_requires_tty(explore_mod, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: False)
    assert explore_mod._color_enabled(no_color_flag=False) is False
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: True)
    assert explore_mod._color_enabled(no_color_flag=False) is True


def test_palette_noop_when_disabled(explore_mod):
    pal = explore_mod._Palette(enabled=False)
    assert pal.bold("x") == "x"
    assert pal.red("x") == "x"


def test_palette_wraps_when_enabled(explore_mod):
    pal = explore_mod._Palette(enabled=True)
    assert pal.bold("x") == "\033[1mx\033[0m"


def test_print_table_empty_message(explore_mod, capsys):
    pal = explore_mod._Palette(enabled=False)
    explore_mod.print_table([], ["id", "name"], pal, "nothing here")
    assert "nothing here" in capsys.readouterr().out


def test_print_table_renders_rows(explore_mod, capsys):
    pal = explore_mod._Palette(enabled=False)
    rows = [{"id": 1, "name": "a"}, {"id": 22, "name": "bb"}]
    explore_mod.print_table(rows, ["id", "name"], pal, "empty")
    out = capsys.readouterr().out
    assert "id" in out and "name" in out
    assert "1" in out and "a" in out
    assert "22" in out and "bb" in out


def test_print_kv_nests_dicts(explore_mod, capsys):
    pal = explore_mod._Palette(enabled=False)
    explore_mod.print_kv({"active": False, "template": {"name": "X"}}, pal)
    out = capsys.readouterr().out
    assert "active" in out and "no" in out
    assert "template" in out and "name" in out and "X" in out


def test_emit_json_mode(explore_mod, capsys):
    explore_mod.emit({"a": 1}, as_json=True)
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1}


# --- confirm() ---------------------------------------------------------------

def test_confirm_yes_flag_skips_prompt(explore_mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("should not prompt")))
    pal = explore_mod._Palette(enabled=False)
    assert explore_mod.confirm("do it?", yes=True, pal=pal) is True


@pytest.mark.parametrize("reply,expected", [("y", True), ("yes", True), ("n", False), ("", False), ("nah", False)])
def test_confirm_prompts_and_parses_reply(explore_mod, monkeypatch, reply, expected):
    monkeypatch.setattr("builtins.input", lambda *a: reply)
    pal = explore_mod._Palette(enabled=False)
    assert explore_mod.confirm("do it?", yes=False, pal=pal) is expected


# --- build_client() ------------------------------------------------------------

def test_build_client_missing_refresh_token_exits(explore_mod, monkeypatch):
    monkeypatch.delenv("NETCUP_SCP_API_REFRESH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        explore_mod.build_client()


def test_build_client_happy_path(explore_mod, monkeypatch):
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(explore_mod, "get_access_token", lambda rt: "tok")
    client = explore_mod.build_client()
    assert isinstance(client, explore_mod.NetcupSCPClient)
    assert client.access_token == "tok"
    assert client.refresh_token == "rt"


# --- subcommands against a FakeClient ------------------------------------------

def _ns(**kw):
    return types.SimpleNamespace(json=False, **kw)


def test_cmd_servers_list(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[[{"id": 1, "hostname": "h", "nickname": "n", "name": "x", "disabled": False}]])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_servers(client, _ns(server_id=None), pal)
    assert client.calls == [("get", "/api/v1/servers", None)]
    assert "h" in capsys.readouterr().out


def test_cmd_servers_one(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[{"id": 1, "hostname": "h"}])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_servers(client, _ns(server_id=1), pal)
    assert client.calls == [("get", "/api/v1/servers/1", None)]
    assert "hostname" in capsys.readouterr().out


def test_cmd_imageflavours_flattens_nested_image_name(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[[{"id": 5, "alias": "Minimal", "image": {"name": "Debian 13"}}]])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_imageflavours(client, _ns(server_id=1), pal)
    out = capsys.readouterr().out
    assert "Debian 13" in out
    assert "Minimal" in out


def test_cmd_iso_detach_declined_never_calls_delete(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    client = fake_client(allow=())  # any get/post/patch/put/delete raises
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_iso(client, _ns(server_id=1, detach=True, yes=False), pal)
    assert client.calls == []


def test_cmd_iso_detach_with_yes_calls_delete(explore_mod, fake_client):
    client = fake_client(allow=("delete",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_iso(client, _ns(server_id=1, detach=True, yes=True), pal)
    assert client.calls == [("delete", "/api/v1/servers/1/iso", None)]


def test_cmd_rescuesystem_deactivate_gated_by_confirm(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_rescuesystem(client, _ns(server_id=1, deactivate=True, yes=False), pal)
    assert client.calls == []


def test_cmd_tasks_cancel_with_yes(explore_mod, fake_client):
    client = fake_client(allow=("put",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_tasks(client, _ns(uuid="abc", cancel=True, yes=True), pal)
    assert client.calls == [("put", "/api/v1/tasks/abc:cancel", None, None)]


def test_cmd_snapshots_dryrun_uses_post(explore_mod, fake_client):
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, dryrun=True, create=False, name=None, yes=False), pal)
    assert client.calls == [("post", "/api/v1/servers/1/snapshots:dryrun", {})]


def test_cmd_snapshots_create_declined_never_posts(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, dryrun=False, create=True, name=None, yes=False), pal)
    assert client.calls == []


def test_cmd_snapshots_create_without_name_gets_a_default(explore_mod, fake_client):
    # ServerSnapshotCreate requires "name" server-side -- omitting --name
    # used to send {} and always fail AFTER the confirm prompt (review
    # finding, 2026-09-09).
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, dryrun=False, create=True, name=None, yes=True), pal)
    assert len(client.calls) == 1
    _, endpoint, payload = client.calls[0]
    assert endpoint == "/api/v1/servers/1/snapshots"
    assert payload.get("name")


def test_cmd_snapshots_create_with_explicit_name(explore_mod, fake_client):
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, dryrun=False, create=True, name="pre-upgrade", yes=True), pal)
    assert client.calls == [("post", "/api/v1/servers/1/snapshots", {"name": "pre-upgrade"})]


def test_cmd_tasks_cancel_without_uuid_errors_instead_of_silently_listing(explore_mod, fake_client):
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(SystemExit):
        explore_mod.cmd_tasks(client, _ns(uuid=None, cancel=True, yes=True), pal)
    assert client.calls == []


# --- main()/--help must not require a working settings file -------------------

def test_help_short_circuits_before_configure(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api-explore.py", "--help"])
    monkeypatch.setattr(
        explore_mod, "_configure",
        lambda: (_ for _ in ()).throw(AssertionError("_configure() must not run for --help")),
    )
    with pytest.raises(SystemExit) as exc:
        explore_mod.main()
    assert exc.value.code == 0


# --- CLI wiring ----------------------------------------------------------------

def test_parse_args_servers_no_id(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api-explore.py", "servers"])
    args = explore_mod.parse_args()
    assert args.command == "servers"
    assert args.server_id is None


def test_parse_args_iso_detach_yes(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api-explore.py", "iso", "42", "--detach", "--yes"])
    args = explore_mod.parse_args()
    assert args.command == "iso"
    assert args.server_id == 42
    assert args.detach is True
    assert args.yes is True
