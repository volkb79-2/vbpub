import asyncio
import json
import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest
from textual.widgets import Button, DataTable, Input, Select, Static, Tree

from tnt.actions import apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused
from tnt.policy import is_destructive_method
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.apikeys import ApiKeyReveal, ApiKeysView
from tnt.tui.app import TntApp
from tnt.tui.workflows import PlanReview


async def _wait_for(pilot, predicate, *, count=100):
    for _ in range(count):
        await pilot.pause(0.05)
        if predicate():
            return
    assert predicate(), "condition did not become true before the bounded UI wait ended"


def _row(key_id=1, name="automation"):
    return {"id": key_id, "name": name, "username": "operator", "user_identifier": 1000,
            "iterations": 100000, "salt": "synthetic-salt-material", "stored_key": "synthetic-stored-key",
            "server_key": "synthetic-server-key", "created_at": "2026-10-28T10:00:00Z",
            "expires_at": None, "local": True, "revoked": False, "revoked_reason": None}


def test_api_key_reads_scrub_verifiers_and_create_uses_single_use_plan():
    with FakeServer() as server:
        server.api_keys[1] = _row()
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            rows = session.workflows.api_keys()
            assert rows == [{key: _row()[key] for key in
                             ("id", "name", "username", "created_at", "expires_at", "local", "revoked", "revoked_reason")}]
            assert "synthetic-stored-key" not in json.dumps(rows)
            action = session.workflows.api_key_create_action("automation-next", "operator")
            plan = make_plan(action, session.rpc)
            assert "never expires" in plan.action.title
            assert "synthetic" not in plan.render()
            result = apply_plan(plan, session.rpc, allow_write=True)
            assert len(result) == 1 and isinstance(result[0].get("key"), str)
            key = result[0]["key"]
            assert key.endswith("Q" * 64)
            with pytest.raises(Refused, match="already exists"):
                make_plan(action, session.rpc)
            assert "key" not in json.dumps(session.workflows.api_keys())
            assert "client_key" not in json.dumps(session.workflows.api_keys())
            request = next(row for row in server.requests if row["method"] == "api_key.create")
            assert request["params"][0] == {"name": "automation-next", "username": "operator"}
        finally:
            session.close()


def test_api_key_rename_and_revoke_are_reviewed_and_revoke_is_typed():
    assert is_destructive_method("api_key.delete")
    with FakeServer() as server:
        server.api_keys[1] = _row()
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            rename = session.workflows.api_key_rename_action(1, "renamed")
            rename_plan = make_plan(rename, session.rpc)
            assert "api_key.update(1" in rename_plan.render()
            assert apply_plan(rename_plan, session.rpc, allow_write=True)
            assert server.api_keys[1]["name"] == "renamed"
            update_request = next(row for row in server.requests if row["method"] == "api_key.update")
            assert update_request["params"] == [1, {"name": "renamed", "reset": False}]
            bad_rename = replace(rename, calls=(type(rename.calls[0])(
                "api_key.update", (1, {"name": "renamed-again", "reset": True})),))
            with pytest.raises(Refused, match="must not reset"):
                make_plan(bad_rename, session.rpc)

            revoke = session.workflows.api_key_revoke_action(1)
            revoke_plan = make_plan(revoke, session.rpc)
            assert revoke_plan.action.confirm_target == "renamed"
            assert "api_key.delete(1)" in revoke_plan.render()
            apply_plan(revoke_plan, session.rpc, allow_write=True, typed_confirmation="renamed")
            assert server.api_keys == {}
        finally:
            session.close()


def test_api_key_cli_lists_scrubbed_and_create_shows_once_and_writes_private_file(
        config, opener, capsys, server, tmp_path):
    server.api_keys[1] = _row()
    cli = build_cli(opener=opener)
    code = cli.run(argv=["apikey", "list", "--config", str(config), "--json"])
    output = capsys.readouterr().out
    assert code == 0 and "automation" in output
    assert "synthetic-salt-material" not in output and "synthetic-stored-key" not in output

    target = tmp_path / "new-api.key"
    code = cli.run(argv=["apikey", "create", "automation-next", "operator", "--config", str(config),
                         "--allow-write", "--write-key", str(target)])
    output = capsys.readouterr().out
    key = server.api_key_create_result["key"]
    assert code == 0 and output.count(key) == 1
    assert "WARNING" in output and "tnt.secrets.toml" in output
    assert target.read_text("utf-8").strip() == key
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "client_key" not in output and "synthetic-server-key" not in output

    creates_before = len([row for row in server.requests if row["method"] == "api_key.create"])
    code = cli.run(argv=["apikey", "create", "blocked-json", "operator", "--config", str(config),
                         "--allow-write", "--json"])
    captured = capsys.readouterr()
    assert code != 0 and "omit --json" in (captured.out + captured.err)
    assert len([row for row in server.requests if row["method"] == "api_key.create"]) == creates_before


def test_api_key_cli_dry_run_refusal_and_optional_save_failure_preserve_the_one_time_key(
        config, opener, capsys, server, monkeypatch, tmp_path):
    import tnt.keyfiles as keyfiles
    from tnt.errors import Refused

    cli = build_cli(opener=opener)
    code = cli.run(argv=["apikey", "create", "preview-key", "operator", "--config", str(config), "--dry-run"])
    output = capsys.readouterr().out
    assert code == 0 and "api_key.create" in output
    assert not any(row["method"] == "api_key.create" for row in server.requests)

    code = cli.run(argv=["apikey", "create", "blocked-key", "operator", "--config", str(config)])
    captured = capsys.readouterr()
    assert code != 0 and "--allow-write" in (captured.out + captured.err)
    assert not any(row["method"] == "api_key.create" for row in server.requests)

    monkeypatch.setattr(keyfiles, "write_api_key_file", lambda *_: (_ for _ in ()).throw(
        Refused("api-key-file", "synthetic write failure")))
    target = tmp_path / "private.key"
    code = cli.run(argv=["apikey", "create", "save-failure", "operator", "--config", str(config),
                         "--allow-write", "--write-key", str(target)])
    output = capsys.readouterr().out
    key = server.api_key_create_result["key"]
    assert code == 0 and output.count(key) == 1 and "synthetic write failure" in output
    assert "WARNING" in output and not target.exists()


def test_api_key_cli_refuses_a_create_result_without_plaintext_key(config, opener, capsys, server, monkeypatch):
    server._api_key_create = lambda _params: {"id": 99, "name": "unrecoverable", "key": None}
    cli = build_cli(opener=opener)
    code = cli.run(argv=["apikey", "create", "unrecoverable", "operator", "--config", str(config), "--allow-write"])
    captured = capsys.readouterr()
    assert code != 0 and "no one-time key value" in (captured.out + captured.err)
    assert "None" not in (captured.out + captured.err)


def test_api_key_cli_rename_and_revoke_require_typed_name(config, opener, capsys, server):
    server.api_keys[1] = _row()
    cli = build_cli(opener=opener)
    assert cli.run(argv=["apikey", "rename", "1", "renamed", "--config", str(config), "--allow-write"]) == 0
    capsys.readouterr()
    assert server.api_keys[1]["name"] == "renamed"
    code = cli.run(argv=["apikey", "revoke", "1", "--config", str(config), "--allow-write",
                         "--confirm", "wrong-name"])
    captured = capsys.readouterr()
    assert code != 0 and "renamed" in (captured.out + captured.err)
    assert 1 in server.api_keys
    assert cli.run(argv=["apikey", "revoke", "1", "--config", str(config), "--allow-write",
                         "--confirm", "renamed"]) == 0
    capsys.readouterr()
    assert server.api_keys == {}


def test_api_key_file_is_exclusive_and_mode_0600(tmp_path):
    from tnt.keyfiles import write_api_key_file
    target = tmp_path / "saved.key"
    write_api_key_file(str(target), "1-" + "Q" * 64)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text("utf-8") == "1-" + "Q" * 64 + "\n"
    try:
        write_api_key_file(str(target), "other")
    except Exception as exc:
        assert "existing" in str(exc)
    else:
        raise AssertionError("writing over an existing API-key file must be refused")


@pytest.mark.parametrize(("path", "key", "message"), [
    (None, "key", "provide an absolute path"),
    ("", "key", "provide an absolute path"),
    ("relative.key", "key", "must be absolute"),
    ("/tmp/key", None, "value is empty"),
    ("/tmp/key", "", "value is empty"),
])
def test_api_key_file_rejects_bad_paths_and_values(path, key, message):
    from tnt.keyfiles import write_api_key_file

    with pytest.raises(Refused, match=message):
        write_api_key_file(path, key)


def test_api_key_file_reports_open_and_finish_errors_and_cleans_partial_files(monkeypatch, tmp_path):
    import os
    from tnt import keyfiles

    target = tmp_path / "save.key"
    monkeypatch.setattr(keyfiles.os, "open", lambda *args: (_ for _ in ()).throw(OSError("open failed")))
    with pytest.raises(Refused, match="cannot create API key file"):
        keyfiles.write_api_key_file(str(target), "secret")
    assert not target.exists()
    monkeypatch.undo()

    original_fchmod = os.fchmod
    monkeypatch.setattr(keyfiles.os, "fchmod", lambda *_: (_ for _ in ()).throw(OSError("chmod failed")))
    with pytest.raises(Refused, match="cannot finish writing"):
        keyfiles.write_api_key_file(str(target), "secret")
    assert not target.exists()
    monkeypatch.setattr(keyfiles.os, "fchmod", original_fchmod)

    monkeypatch.setattr(keyfiles.os, "write", lambda *_: 0)
    with pytest.raises(Refused, match="cannot finish writing"):
        keyfiles.write_api_key_file(str(target), "secret")
    assert not target.exists()
    monkeypatch.undo()

    monkeypatch.setattr(keyfiles.os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("sync failed")))
    with pytest.raises(Refused, match="cannot finish writing"):
        keyfiles.write_api_key_file(str(target), "secret")
    assert not target.exists()


def test_api_key_file_cleanup_failure_does_not_hide_short_write(monkeypatch, tmp_path):
    from pathlib import Path

    from tnt import keyfiles

    target = tmp_path / "partial.key"
    with monkeypatch.context() as patcher:
        patcher.delattr(keyfiles.os, "O_NOFOLLOW")
        patcher.setattr(keyfiles.os, "write", lambda _fd, _view: 0)
        patcher.setattr(Path, "unlink", lambda _path, **_kwargs: (_ for _ in ()).throw(OSError("busy")))
        with pytest.raises(Refused, match="cannot finish writing"):
            keyfiles.write_api_key_file(str(target), "synthetic-key")
    assert target.exists()
    target.unlink()


def test_api_key_tui_has_one_time_reveal_dialog_and_clears_it_on_close(tmp_path):
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                tree = app.query_one("#nav", Tree)
                api_keys = None

                def find(node):
                    nonlocal api_keys
                    for child in node.children:
                        if child.data and child.data.key == "api-keys":
                            api_keys = child
                            return
                        find(child)

                find(tree.root)
                assert api_keys is not None
                tree.select_node(api_keys)
                for _ in range(60):
                    await pilot.pause(0.05)
                    note = str(app.query_one("#apikey-note", Static).render()) if app.query("#apikey-note") else ""
                    if "API key(s)" in note:
                        break
                await pilot.pause(0.1)
                view = app.query_one("ApiKeysView", ApiKeysView)
                app.query_one("#apikey-operation", Select).value = "create"
                app.query_one("#apikey-name", Input).value = "temporary"
                app.query_one("#apikey-username", Input).value = "operator"
                plan_button = app.query_one("#apikey-plan", Button)
                assert plan_button.visible and plan_button.region.height > 0, str(plan_button.region)
                clicked = await pilot.click(plan_button)
                assert clicked, f"plan button was obscured by {app.screen.get_widget_at(*plan_button.region.center)}"
                for _ in range(20):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, PlanReview):
                        break
                assert isinstance(app.screen, PlanReview), (
                    str(app.query_one("#apikey-note", Static).render()),
                    [type(screen).__name__ for screen in app.screen_stack],
                )
                await pilot.click("#plan-apply")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, ApiKeyReveal):
                        break
                dialog = app.screen
                assert isinstance(dialog, ApiKeyReveal)
                raw_key = dialog.key
                secret_widget = dialog.query_one("#apikey-secret", Static)
                assert raw_key and raw_key in str(secret_widget.render())
                assert "synthetic-client-material" not in str(dialog.render())
                dialog.query_one("#apikey-save-path", Input).value = "relative.key"
                await pilot.click("#apikey-save")
                assert "File was not written" in str(dialog.query_one("#apikey-reveal-note", Static).render())
                target = tmp_path / "tui-api.key"
                dialog.query_one("#apikey-save-path", Input).value = str(target)
                dialog.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="apikey-save")))
                assert target.read_text("utf-8").strip() == raw_key
                dialog.action_close()
                assert dialog.key == ""
                assert raw_key not in str(secret_widget.render())
                assert stat.S_IMODE(target.stat().st_mode) == 0o600

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_api_key_tui_handles_empty_selection_bad_create_result_and_revoke():
    import asyncio

    with FakeServer() as server:
        server._api_key_create = lambda params: {"id": 9, "name": params[0]["name"]}
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                tree = app.query_one("#nav", Tree)
                key_node = None

                def find(node):
                    nonlocal key_node
                    for child in node.children:
                        if child.data and child.data.key == "api-keys":
                            key_node = child
                            return
                        find(child)

                find(tree.root)
                tree.select_node(key_node)
                await _wait_for(pilot, lambda: "API key(s)." in str(
                    app.query_one("#apikey-note", Static).render()), count=300)
                view = app.query_one(ApiKeysView)
                assert view._selected() is None
                view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="other")))
                app.query_one("#apikey-operation", Select).value = "create"
                app.query_one("#apikey-name", Input).value = "one-time"
                app.query_one("#apikey-username", Input).value = "operator"
                await pilot.click("#apikey-plan")
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview), count=300)
                await pilot.click("#plan-apply")
                await _wait_for(pilot, lambda: "returned no one-time key" in str(
                    app.query_one("#apikey-note", Static).render()))

                server.api_keys[1] = _row()
                view.action_refresh()
                await _wait_for(pilot, lambda: view.query_one("#apikey-table", DataTable).row_count == 1)
                app.query_one("#apikey-operation", Select).value = "rename"
                app.query_one("#apikey-name", Input).value = "automation"
                await pilot.click("#apikey-plan")
                assert "already has that name" in str(app.query_one("#apikey-note", Static).render())
                app.query_one("#apikey-operation", Select).value = "revoke"
                view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="apikey-plan")))
                await pilot.pause(0.1)
                assert isinstance(app.screen, PlanReview), str(app.query_one("#apikey-note", Static).render())
                app.screen.query_one("#plan-confirm", Input).value = "automation"
                await pilot.click("#plan-apply")
                await _wait_for(pilot, lambda: server.api_keys == {})
                assert "api_key.delete" in server.called()

        try:
            asyncio.run(go())
        finally:
            session.close()
