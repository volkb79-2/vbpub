from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, Tree

from tnt.errors import ProfileError
from tnt.host_store import HostStore, load_host_store
from tnt.menu import MENU, MenuItem
from tnt.profile import resolve_paths
from tnt.session_controller import SessionController
from tnt.tui.app import TntApp, WorkerDrainDecision
from tnt.tui.hosts import HostForm, HostPicker, HostRemoveConfirm


async def _wait_for(pilot, predicate, *, count=160):
    for _ in range(count):
        await pilot.pause(0.05)
        if predicate():
            return
    assert predicate(), "condition did not become true before the bounded UI wait ended"


def _select_host(table: DataTable, name: str) -> None:
    row_names = [str(key.value) for key in table.rows]
    table.move_cursor(row=row_names.index(name), column=0)


def test_picker_switches_hosts_tests_without_replacing_session_and_tracks_write_mode(config, opener):
    hosts, _default, entries = load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))

    async def go():
        app = TntApp(None, hosts=hosts, default_host=None, secret_entries=entries,
                     host_paths=resolve_paths(config, config.with_name("tnt.secrets.toml")), session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            picker = app.query_one("HostPicker")
            table = picker.query_one("#hosts-table", DataTable)
            assert table.row_count == 2
            assert "id_ed25519" not in str(table.render())
            assert "1-fakefakefake" not in str(table.render())

            table.move_cursor(row=0, column=0)
            assert picker.selected_name() == "lab"
            picker.on_button_pressed(SimpleNamespace(button=picker.query_one("#hosts-switch", Button)))
            await _wait_for(pilot, lambda: app.session is not None)
            await _wait_for(pilot, lambda: app.session.profile.name == "lab")
            original = app.session
            assert original.rpc.read_only is True
            assert "lab" in str(app.query_one("#banner", Static).render())

            await pilot.press("w")
            await _wait_for(pilot, lambda: app.session is not None and not app.session.rpc.read_only)
            assert app.session.profile.name == "lab"
            assert "WRITE ENABLED" in str(app.query_one("#banner", Static).render())
            write_session = app.session

            await pilot.press("h")
            picker = app.query_one("HostPicker")
            table = picker.query_one("#hosts-table", DataTable)
            table.move_cursor(row=0, column=0)
            picker.on_button_pressed(SimpleNamespace(button=picker.query_one("#hosts-test", Button)))
            await _wait_for(pilot, lambda: "lab: connected" in str(
                picker.query_one("#hosts-note", Static).render()))
            assert app.session.profile.name == "lab" and not app.session.rpc.read_only
            assert app.session is write_session
            app.session_controller.close()

    asyncio.run(go())


def test_tui_host_form_adds_securely_renames_sets_default_and_removes(config, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    raw_password = "TUI-ONLY-PLACEHOLDER-PASSWORD"

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(180, 55)) as pilot:
            picker = app.query_one(HostPicker)
            picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="hosts-add")))
            await pilot.pause(0.05)
            form = app.screen
            assert isinstance(form, HostForm)
            assert form.query_one("#host-secret", Input).password
            form.query_one("#host-name", Input).value = "throwaway-ui"
            form.query_one("#host-url", Input).value = "wss://throwaway-ui.example.invalid/api/current"
            form.query_one("#host-username", Input).value = "operator"
            form.query_one("#host-auth", Select).value = "password"
            form.query_one("#host-tls", Select).value = "insecure"
            form.on_select_changed(SimpleNamespace(select=form.query_one("#host-tls", Select)))
            assert "WARNING" in str(form.query_one("#host-form-warning", Static).render())
            form.query_one("#host-secret", Input).value = raw_password
            with pytest.raises(ValueError, match="check the insecure TLS warning"):
                form._save()
            form.query_one("#host-insecure-ack", Checkbox).value = True
            form._save()
            await _wait_for(pilot, lambda: bool(app.query(HostPicker)))
            assert app.session is None
            assert app.session_controller.active_host is None
            saved_hosts, saved_default, saved_entries = load_host_store(paths)
            assert saved_default == "lab"
            assert saved_hosts["throwaway-ui"].tls == "insecure"
            assert saved_entries["throwaway-ui"].password.reveal() == raw_password
            assert raw_password not in str(app.query_one(HostPicker).query_one("#hosts-table", DataTable).render())

            picker = app.query_one(HostPicker)
            _select_host(picker.query_one("#hosts-table", DataTable), "throwaway-ui")
            picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="hosts-default")))
            assert load_host_store(paths)[1] == "throwaway-ui"

            picker = app.query_one(HostPicker)
            _select_host(picker.query_one("#hosts-table", DataTable), "throwaway-ui")
            picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="hosts-edit")))
            await pilot.pause(0.05)
            edit = app.screen
            assert isinstance(edit, HostForm)
            edit.query_one("#host-name", Input).value = "renamed-ui"
            edit.query_one("#host-description", Input).value = "Renamed through TUI"
            edit.query_one("#host-insecure-ack", Checkbox).value = True
            edit._save()
            await _wait_for(pilot, lambda: bool(app.query(HostPicker)))
            saved_hosts, saved_default, saved_entries = load_host_store(paths)
            assert "throwaway-ui" not in saved_hosts and saved_default == "renamed-ui"
            assert saved_hosts["renamed-ui"].description == "Renamed through TUI"
            assert saved_entries["renamed-ui"].password.reveal() == raw_password

            picker = app.query_one(HostPicker)
            table = picker.query_one("#hosts-table", DataTable)
            _select_host(table, "renamed-ui")
            picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="hosts-remove")))
            await pilot.pause(0.05)
            confirmation = app.screen
            assert isinstance(confirmation, HostRemoveConfirm)
            confirmation.query_one("#host-remove-confirm", Input).value = "wrong"
            confirmation.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="host-remove-apply")))
            assert "renamed-ui" in load_host_store(paths)[0]
            confirmation.query_one("#host-remove-confirm", Input).value = "renamed-ui"
            confirmation.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="host-remove-apply")))
            await pilot.pause(0.05)
            saved_hosts, saved_default, saved_entries = load_host_store(paths)
            assert "renamed-ui" not in saved_hosts and saved_default is None
            assert "renamed-ui" not in saved_entries
            app.session_controller.close()

    asyncio.run(go())


def test_tui_host_forms_report_invalid_values_and_cancel_without_writing(config, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    before = paths.config.read_bytes()

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            form = HostForm()
            app.push_screen(form)
            await pilot.pause(0.05)
            form.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="unrelated")))
            assert paths.config.read_bytes() == before
            form.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="host-cancel")))
            await pilot.pause(0.05)
            picker = app.query_one(HostPicker)
            picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="hosts-remove")))
            await pilot.pause(0.05)
            confirmation = app.screen
            assert isinstance(confirmation, HostRemoveConfirm)
            confirmation.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="host-remove-cancel")))
            await pilot.pause(0.05)
            assert paths.config.read_bytes() == before
            app.session_controller.close()

    asyncio.run(go())


def test_callback_guard_rejects_stale_or_detached_work_and_commands_show_host_picker(config, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    session = opener(hosts["lab"], secret=entries["lab"].api_key)
    delivered = []

    async def go():
        app = TntApp(session, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            app.action_select_host()
            assert isinstance(app.query_one(HostPicker), HostPicker)
            assert "lab" in str(app.query_one("#banner", Static).render())
            lease = app.begin_session_work(session)
            assert lease is not None
            attached = SimpleNamespace(is_attached=True, accept=lambda value: delivered.append(value))
            app.deliver_session_callback(lease, attached, "accept", ("current",))
            assert delivered == ["current"]
            detached = SimpleNamespace(is_attached=False, accept=lambda value: delivered.append(value))
            app.deliver_session_callback(lease, detached, "accept", ("detached",))
            assert delivered == ["current"]
            app.session_controller.begin_switch("throwaway")
            app.deliver_session_callback(lease, attached, "accept", ("stale",))
            assert delivered == ["current"]
            app.finish_session_work(lease)
            app.action_refresh()
            app.action_help()
            commands = list(app.get_system_commands(app.screen))
            assert any("Select or switch TrueNAS host" in command.title for command in commands)
            app.action_toggle_write_mode()
            await pilot.pause(0.05)
            app.session_controller.close()

    asyncio.run(go())


def test_worker_drain_cancel_restores_the_current_host_after_a_switch_timeout(config, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    session = opener(hosts["lab"], secret=entries["lab"].api_key)
    target = next(name for name in hosts if name != "lab")

    async def go():
        app = TntApp(session, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener, worker_drain_timeout=0.01)
        async with app.run_test(size=(160, 50)) as pilot:
            lease = app.begin_session_work(session)
            assert lease is not None
            app.switch_host(target)
            await _wait_for(pilot, lambda: isinstance(app.screen, WorkerDrainDecision))
            decision = app.screen
            assert decision.pending == 1 and decision.transition_kind == "switch"
            decision.on_button_pressed(Button.Pressed(decision.query_one("#worker-drain-cancel", Button)))
            await _wait_for(pilot, lambda: app.session is session and app.session_controller.is_current(
                app.session_controller.generation, session))
            assert app.session_controller.active_host == "lab"
            assert lease.cancelled.is_set()
            app.finish_session_work(lease)

    try:
        asyncio.run(go())
    finally:
        session.close()


def test_empty_host_picker_has_no_selection(config):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))

    async def go():
        app = TntApp(None, hosts={}, default_host=None, secret_entries={}, host_paths=paths)
        async with app.run_test(size=(160, 50)) as pilot:
            picker = app.query_one(HostPicker)
            assert not app.query_one("#nav", Tree).display
            assert picker.query_one("#hosts-add", Button).has_focus
            assert picker.query_one("#hosts-cancel", Button).label == "Quit"
            for button_id in ("hosts-switch", "hosts-test", "hosts-edit", "hosts-default", "hosts-remove"):
                assert picker.query_one(f"#{button_id}", Button).disabled
            assert picker.selected_name() is None
            picker.refresh_rows()
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert app.current == "__hosts__" and picker.query_one("#hosts-add", Button).has_focus
            for button_id in ("hosts-switch", "hosts-test", "hosts-edit", "hosts-default", "hosts-remove"):
                picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id=button_id)))
            assert app.session is None
            app.test_quit_requested = False
            app.action_quit = lambda: setattr(app, "test_quit_requested", True)
            picker.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="hosts-cancel")))
            assert app.test_quit_requested
            app.session_controller.close()

    asyncio.run(go())


def test_picker_test_failure_and_host_form_validation_errors_are_shown(config, opener, monkeypatch):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            picker = app.query_one(HostPicker)
            _select_host(picker.query_one("#hosts-table", DataTable), "lab")

            def fail_test(_name):
                raise ProfileError("synthetic connection refusal")

            monkeypatch.setattr(app.session_controller, "test_host", fail_test)
            picker.on_button_pressed(SimpleNamespace(button=picker.query_one("#hosts-test", Button)))
            await _wait_for(pilot, lambda: "lab: connection failed" in
                            str(picker.query_one("#hosts-note", Static).render()))
            form = HostForm(current=hosts["lab"])
            app.push_screen(form)
            await pilot.pause(0.05)
            form.query_one("#host-auth", Select).value = "password"
            with pytest.raises(ValueError, match="enter the new credential"):
                form._save()
            form.query_one("#host-api-key-file", Input).value = "/tmp/key"
            with pytest.raises(ValueError, match="available only with api_key auth"):
                form._save()
            form.query_one("#host-api-key-file", Input).value = ""
            form.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="host-save")))
            assert "Host was not saved" in str(form.query_one("#host-form-note", Static).render())
            await pilot.pause(0.05)
            app.session_controller.close()

    asyncio.run(go())


def test_remove_confirmation_keeps_modal_when_store_rejects(config, opener, monkeypatch):
    from tnt.tui.hosts import HostRemoveConfirm

    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            modal = HostRemoveConfirm("lab")
            app.push_screen(modal)
            await pilot.pause(0.05)
            modal.query_one("#host-remove-confirm", Input).value = "lab"
            monkeypatch.setattr(HostStore, "remove", lambda _self, _name: (_ for _ in ()).throw(
                ProfileError("synthetic store refusal")))
            modal.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="host-remove-apply")))
            assert app.screen is modal
            app.session_controller.close()

    asyncio.run(go())


def test_switch_failure_recovers_to_picker_and_disconnect_closes_session(config, server, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener, reconnect_delays=(0,))
        async with app.run_test(size=(160, 50)) as pilot:
            app.switch_host("lab")
            await _wait_for(pilot, lambda: app.session is not None)
            assert app.session.profile.name == "lab"
            app.switch_host("throwaway")
            await _wait_for(pilot, lambda: app.session is None and "connection failed" in
                            str(app.query_one("#banner", Static).render()))
            assert isinstance(app.query_one(HostPicker), HostPicker)
            app.switch_host("lab")
            await _wait_for(pilot, lambda: app.session is not None)
            old = app.session
            closed = []

            def close_old():
                closed.append(True)
                old.rpc.close()

            old.close = close_old
            app.disconnect_to_picker()
            await _wait_for(pilot, lambda: app.session is None and app.session_controller.active_host is None)
            assert closed == [True]
            assert "No host selected" in str(app.query_one("#banner", Static).render())
            app.session_controller.close()

    asyncio.run(go())


def test_active_host_rename_reopens_that_host_and_removal_disconnects(config, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    session = opener(hosts["lab"], secret=entries["lab"].api_key)

    async def go():
        app = TntApp(session, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener, reconnect_delays=(0,))
        async with app.run_test(size=(160, 50)) as pilot:
            HostStore(paths).rename("lab", "renamed-lab")
            app.host_configuration_changed("lab", "renamed-lab")
            await _wait_for(pilot, lambda: app.session is not None and app.session.profile.name == "renamed-lab")
            assert app.session_controller.default_host == "renamed-lab"
            HostStore(paths).remove("renamed-lab")
            app.host_configuration_changed("renamed-lab", None, removed=True)
            await _wait_for(pilot, lambda: app.session is None and app.session_controller.active_host is None)
            assert isinstance(app.query_one(HostPicker), HostPicker)
            app.session_controller.close()

    asyncio.run(go())


def test_app_restores_navigation_updates_tls_banner_and_opens_palette_action(config, opener, monkeypatch):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    session = opener(hosts["lab"], secret=entries["lab"].api_key)
    planned = MenuItem("planned-test", "Planned test", milestone="It3", matrix=("A-99",), conditional=True)
    containers = next(item for item in MENU if item.key == "containers")

    async def go():
        app = TntApp(session, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(180, 55)) as pilot:
            session.insecure_tls = True
            app._update_banner()
            banner = app.query_one("#banner", Static)
            assert "TLS VERIFICATION OFF" in str(banner.render()) and "insecure" in banner.classes
            session.insecure_tls = False
            app._update_banner()
            assert "insecure" not in banner.classes

            app.show(planned)
            await pilot.pause(0.05)
            text = str(app.query_one("#view-planned", Static).render())
            assert "Action-matrix rows: A-99" in text and "only when the host supports it" in text
            dashboard = next(item for item in MENU if item.key == "dashboard")
            await app.query_one("#content").remove_children()
            app.show(dashboard)
            await pilot.pause(0.05)
            app.action_select_host()
            await pilot.pause(0.05)
            assert isinstance(app.query_one(HostPicker), HostPicker)
            app.close_host_picker()
            await pilot.pause(0.05)
            assert app.current == "dashboard"

            callbacks = []
            monkeypatch.setattr(app, "call_after_refresh", lambda callback: callbacks.append(callback))
            app._open_action(containers, "delete")
            await _wait_for(pilot, lambda: len(app.query("#workflow-operation")) > 0)
            callbacks[0]()
            assert str(app.query_one("#workflow-operation", Select).value) == "delete"
            monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("detached")))
            callbacks[0]()
            monkeypatch.undo()

            class Invalidatable(ModalScreen[None]):
                def __init__(self):
                    super().__init__()
                    self.invalidated = False

                def compose(self):
                    yield Static("modal")

                def invalidate_for_host_change(self):
                    self.invalidated = True
                    self.dismiss()

            custom = Invalidatable()
            app.push_screen(custom)
            await pilot.pause(0.05)
            app._dismiss_top_modal()
            await pilot.pause(0.05)
            assert custom.invalidated

            ordinary = HostRemoveConfirm("lab")
            app.push_screen(ordinary)
            await pilot.pause(0.05)
            app._dismiss_top_modal()
            await pilot.pause(0.05)
            assert app.screen is not ordinary
            app.session_controller.close()

    asyncio.run(go())


def test_app_host_status_callbacks_default_errors_and_reload_failure(config, opener):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            picker = app.query_one(HostPicker)
            app.set_default_host("throwaway")
            assert app.session_controller.default_host == "throwaway"
            assert "Default host: throwaway" in str(picker.query_one("#hosts-note", Static).render())
            generation = app.session_controller.generation
            app.host_test_finished(generation - 1, "lab", "stale result")
            assert "stale result" not in str(picker.query_one("#hosts-note", Static).render())
            app.host_test_finished(generation, "lab", "connected")
            assert "lab: connected" in str(picker.query_one("#hosts-note", Static).render())

            app.query_one("#content").remove_children()
            app.host_test_finished(generation, "lab", "no picker")
            app._host_switch_finished(generation - 1, None, None)
            app._host_switch_failed(generation - 1, "lab", "stale failure")
            app.switch_host("not-configured")

            paths.config.write_text("invalid TOML =")
            paths.config.chmod(0o600)
            app.host_configuration_changed("lab", "lab")
            await pilot.pause(0.05)
            assert isinstance(app.query_one(HostPicker), HostPicker)

            app.disconnect_to_picker()
            await _wait_for(pilot, lambda: not app.session_controller._switching)
            assert app.session is None
            app.session_controller.close()

    asyncio.run(go())


def test_host_picker_selection_errors_and_api_key_file_form_path(config, opener, monkeypatch):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)

    async def go():
        app = TntApp(None, hosts=hosts, default_host=default, secret_entries=entries,
                     host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            picker = app.query_one(HostPicker)
            table = picker.query_one("#hosts-table", DataTable)
            monkeypatch.setattr(DataTable, "coordinate_to_cell_key",
                                lambda *_: (_ for _ in ()).throw(ValueError("invalid cursor")))
            assert picker.selected_name() is None

            empty_form = HostForm()
            empty_form._update_tls_warning()  # Unmounted forms safely ignore absent warning widgets.
            app.push_screen(empty_form)
            await pilot.pause(0.05)
            empty_form.query_one("#host-name", Input).value = "missing-credential"
            empty_form.query_one("#host-url", Input).value = "wss://missing-credential.example.invalid/api/current"
            empty_form.query_one("#host-username", Input).value = "operator"
            with pytest.raises(ValueError, match="enter the new credential"):
                empty_form._save()
            empty_form.dismiss()
            await pilot.pause(0.05)

            form = HostForm()
            app.push_screen(form)
            await pilot.pause(0.05)
            form.query_one("#host-name", Input).value = "file-key-host"
            form.query_one("#host-url", Input).value = "wss://file-key-host.example.invalid/api/current"
            form.query_one("#host-username", Input).value = "operator"
            form.query_one("#host-api-key-file", Input).value = "/tmp/tnt-placeholder-key"
            form._save()
            await pilot.pause(0.05)
            saved = load_host_store(paths)[2]["file-key-host"]
            assert saved.api_key is None and saved.api_key_file == "/tmp/tnt-placeholder-key"

            current = load_host_store(paths)[0]["lab"]
            edit = HostForm(current=current)
            app.push_screen(edit)
            await pilot.pause(0.05)
            edit.query_one("#host-description", Input).value = "edited without replacing secret"
            edit._save()
            await pilot.pause(0.05)
            saved_hosts, _, saved_entries = load_host_store(paths)
            assert saved_hosts["lab"].description == "edited without replacing secret"
            assert saved_entries["lab"].api_key.reveal() == entries["lab"].api_key.reveal()
            app.session_controller.close()

    asyncio.run(go())


def test_app_accepts_injected_controller_and_refuses_invalid_host_state_changes(config, opener, monkeypatch):
    paths = resolve_paths(config, config.with_name("tnt.secrets.toml"))
    hosts, default, entries = load_host_store(paths)
    controller = SessionController(hosts, default, entries, opener)

    async def go():
        app = TntApp(session_controller=controller, host_paths=paths, session_opener=opener)
        async with app.run_test(size=(160, 50)) as pilot:
            assert app.begin_session_work(object()) is None
            app.show(MenuItem("not-visible", "Not visible"))
            await pilot.pause(0.05)
            assert isinstance(app.query_one(HostPicker), HostPicker)
            app.close_host_picker()  # no selected session: leave the picker open

            transition = controller.begin_switch("lab")
            connected = controller.finish_switch(transition)
            app.current = "unknown-menu-item"
            app._restore_key = None
            app.close_host_picker()
            await pilot.pause(0.05)
            assert app.current is None and controller.session is connected

            def fail_default(_self, _name):
                raise ProfileError("synthetic default refusal")

            monkeypatch.setattr(HostStore, "set_default", fail_default)
            app.set_default_host("lab")

            transition = controller.begin_switch("lab")
            app.disconnect_to_picker()
            assert controller._switching
            controller.finish_switch(transition)
            await pilot.pause(0.05)
            app.session_controller.close()

    asyncio.run(go())
