from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.widgets import Button, DataTable, Input, Select, Static

from tnt.actions import Action, Kind, Plan, RpcCall
from tnt.host_store import load_host_store
from tnt.menu import MENU
from tnt.profile import resolve_paths
from tnt.profile import Profile
from tnt.tui.apikeys import ApiKeysView
from tnt.tui.app import TntApp


async def _wait_for(pilot, predicate, *, count=120):
    for _ in range(count):
        await pilot.pause(0.05)
        if predicate():
            return
    assert predicate(), "API-key view did not settle before the bounded wait ended"


def _create_plan():
    return Plan(Action("TNT-99", Kind.MUTATE, "Create API key", (RpcCall("api_key.create"),)), "fingerprint")


def test_api_key_output_is_saved_privately_and_only_the_path_is_shown(config, opener, tmp_path, monkeypatch):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)

    def configured_opener(profile, **kwargs):
        session = opener(profile, **kwargs)
        session.profile = profile
        return session

    session = configured_opener(hosts["lab"], secret=entries["lab"].api_key)
    output_path = tmp_path / "created.key"
    one_time_key = "1-tui-placeholder-key-material"

    async def go():
        app = TntApp(session, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=configured_opener)
        async with app.run_test(size=(180, 55)) as pilot:
            item = next(item for item in MENU if item.key == "credentials").children[1]
            app.show(item)
            await _wait_for(pilot, lambda: "API key(s)" in str(app.query_one("#apikey-note", Static).render()))
            view = app.query_one(ApiKeysView)
            view.action_refresh = lambda: None

            view.pending_key_path = str(output_path)
            view._plan_applied(_create_plan(), [{"id": 41, "key": one_time_key}])
            assert output_path.read_text("utf-8").strip() == one_time_key
            assert output_path.stat().st_mode & 0o777 == 0o600
            note = str(view.query_one("#apikey-note", Static).render())
            assert str(output_path) in note and one_time_key not in note
            saved = load_host_store(paths)[2]["lab"]
            assert saved.api_key is None and saved.api_key_file == str(output_path)
            assert app.session_controller.secret_entries["lab"].api_key_file == str(output_path)

            view.pending_key_path = "/tmp/unused-key-path"
            view._plan_applied(_create_plan(), [{"id": 42}])
            assert "did not contain its one-time value" in str(view.query_one("#apikey-note", Static).render())

            view.pending_key_path = None
            view._plan_applied(_create_plan(), [{"id": 43, "key": one_time_key}])
            assert "could not be saved (ValueError)" in str(view.query_one("#apikey-note", Static).render())

            import tnt.tui.apikeys as apikeys
            monkeypatch.setattr(apikeys, "write_api_key_file", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
            view.pending_key_path = str(tmp_path / "cannot-save.key")
            view._plan_applied(_create_plan(), [{"id": 43, "key": one_time_key}])
            note = str(view.query_one("#apikey-note", Static).render())
            assert "could not be saved (OSError)" in note and one_time_key not in note
            assert view.pending_key_path is None
            app.session_controller.close()

    asyncio.run(go())


def test_api_key_form_refusals_selection_failure_and_action_completion(config, opener, monkeypatch, tmp_path):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    session = opener(hosts["lab"], secret=entries["lab"].api_key)

    async def go():
        app = TntApp(session, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(180, 55)) as pilot:
            item = next(item for item in MENU if item.key == "credentials").children[1]
            app.show(item)
            await _wait_for(pilot, lambda: "API key(s)" in str(app.query_one("#apikey-note", Static).render()))
            view = app.query_one(ApiKeysView)
            view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="unknown")))
            view.query_one("#apikey-operation", Select).value = "create"
            view.query_one("#apikey-name", Input).value = "new-key"
            view.query_one("#apikey-username", Input).value = "operator"
            view.query_one("#apikey-file", Input).value = "relative.key"
            view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="apikey-plan")))
            assert "requires an absolute output path" in str(view.query_one("#apikey-note", Static).render())

            view._loaded([{"id": 1, "name": "existing", "username": "operator"}])
            view.query_one("#apikey-operation", Select).value = "rename"
            view.query_one("#apikey-file", Input).value = ""
            with monkeypatch.context() as patch:
                patch.setattr(DataTable, "coordinate_to_cell_key",
                              lambda *_: (_ for _ in ()).throw(ValueError("bad cursor")))
                assert view._selected() is None
                view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="apikey-plan")))
                assert "select an API key" in str(view.query_one("#apikey-note", Static).render())

            view._plan_failed("synthetic plan refusal")
            assert "synthetic plan refusal" in str(view.query_one("#apikey-note", Static).render())
            view.pending_key_path = "/tmp/private.key"
            view._plan_cancelled(_create_plan())
            assert view.pending_key_path is None
            assert "cancelled" in str(view.query_one("#apikey-note", Static).render())

            rename_plan = Plan(Action("TNT-99", Kind.MUTATE, "Rename key", (RpcCall("api_key.update"),)), "fp")
            view.action_refresh = lambda: None
            view._plan_applied(rename_plan, [{"ok": True}])
            assert "API key renamed" in str(view.query_one("#apikey-note", Static).render())
            revoke_plan = Plan(Action("TNT-99", Kind.MUTATE, "Revoke key", (RpcCall("api_key.delete"),)), "fp")
            view._plan_applied(revoke_plan, [True])
            assert "API key revoked" in str(view.query_one("#apikey-note", Static).render())

            current = view.session.profile
            view.session.profile = Profile(current.name, current.url, current.username, auth="password")
            password_key_path = tmp_path / "password-auth-key"
            view.pending_key_path = str(password_key_path)
            view._plan_applied(_create_plan(), [{"key": "1-password-auth-placeholder"}])
            assert "uses password auth" in str(view.query_one("#apikey-note", Static).render())
            assert password_key_path.exists()
            app.session_controller.close()

    asyncio.run(go())
