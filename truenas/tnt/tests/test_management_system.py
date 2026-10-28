import json
import stat
import tarfile
import io
import ssl
import urllib.error

import pytest
from textual.widgets import Checkbox, Input, Static, Tree

from dataclasses import replace

from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import Refused, TntError, TransportError
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp
from tnt.tui.workflows import PlanReview
from tnt.workflows import (_absolute_tar_path, _system_preflight_snapshot, _update_status_summary,
                           validate_plan_preconditions)


class _DownloadResponse:
    def __init__(self, body=b"", *, status=200, error=None):
        self.body = body
        self.status = status
        self.error = error
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _size):
        if self.error is not None:
            raise self.error
        self.reads += 1
        return self.body if self.reads == 1 else b""


class _DownloadOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


def test_update_check_uses_v27_status_and_reports_train_versions_and_release_notes():
    with FakeServer() as server:
        server.update_target_train = "TrueNAS-SCALE-27-NEXT"
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            result = session.workflows.update_check()
            assert result["train"] == "TrueNAS-SCALE-27-STABLE"
            assert result["target_train"] == "TrueNAS-SCALE-27-NEXT"
            assert result["current_version"] == "TrueNAS-27.0.0-RC.1"
            assert result["new_version"] == "TrueNAS-27.0.0-RC.2"
            assert "maintenance release" in result["changelog_summary"]
        finally:
            session.close()


def test_reboot_plan_shows_preflight_and_requires_force_and_typed_host():
    with FakeServer() as server:
        server.active_jobs = [{"id": 41, "method": "pool.scrub.run", "state": "RUNNING", "description": "Scrub"}]
        server.containers[1] = {"id": 1, "name": "web", "status": {"state": "RUNNING"}}
        server.vms[1] = {"id": 1, "name": "guest", "status": {"state": "RUNNING", "pid": 99}}
        server.active_smb_sessions = 3
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            with pytest.raises(Refused, match="running job"):
                make_plan(session.workflows.system_power_action("reboot", delay=15), session.rpc)
            action = session.workflows.system_power_action("reboot", delay=15, force_with_jobs=True)
            plan = make_plan(action, session.rpc)
            rendered = plan.render()
            assert "1 running job" in rendered and "pool.scrub.run" in rendered
            assert "1 running VM" in rendered and "guest" in rendered
            assert "1 running container" in rendered and "web" in rendered
            assert "3 active SMB sessions" in rendered
            assert plan.action.confirm_target == "test"
            with pytest.raises(Refused, match="type 'test'"):
                apply_plan(plan, session.rpc, allow_write=True, typed_confirmation="wrong")
            assert "system.reboot" not in server.called()
            apply_plan(plan, session.rpc, allow_write=True, typed_confirmation="test")
            request = next(row for row in server.requests if row["method"] == "system.reboot")
            assert request["params"] == ["tnt requested reboot", {"delay": 15}]
        finally:
            session.close()


def test_system_preflight_refuses_partial_job_visibility_without_full_admin():
    with FakeServer() as server:
        server.system_roles = ["SYSTEM_UPDATE_WRITE", "SYSTEM_GENERAL_READ"]
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            with pytest.raises(Refused, match="FULL_ADMIN"):
                session.workflows.system_power_action("shutdown")
            assert "system.shutdown" not in server.called()
        finally:
            session.close()


def test_update_apply_binds_target_and_emits_job_progress():
    with FakeServer() as server:
        server.update_target_train = "TrueNAS-SCALE-27-NEXT"
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            action = session.workflows.update_apply_action("TrueNAS-27.0.0-RC.2")
            plan = make_plan(action, session.rpc)
            assert plan.action.kind.value == "destructive"
            assert plan.action.confirm_target == "TrueNAS-27.0.0-RC.2"
            progress = []
            apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=plan.action.confirm_target,
                       job_progress=lambda job: progress.append(job.get("progress", {}).get("percent")))
            assert progress == [25, 75, 100]
            request = next(row for row in server.requests if row["method"] == "update.run")
            assert request["params"] == [{"dataset_name": None, "resume": False,
                                         "train": "TrueNAS-SCALE-27-NEXT",
                                         "version": "TrueNAS-27.0.0-RC.2", "reboot": False}]
        finally:
            session.close()


def test_config_export_downloads_tar_to_new_private_local_file(config, opener, capsys, tmp_path, server):
    target = tmp_path / "truenas-config.tar"
    cli = build_cli(opener=opener)
    code = cli.run(argv=["system", "config-export", str(target), "--config", str(config), "--allow-write"])
    output = capsys.readouterr().out
    assert code == 0 and "0600" in output and str(target) in output
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with tarfile.open(target, "r:") as archive:
        assert archive.getnames() == ["config.db"]
    assert "auth_token" not in output
    call = next(row for row in server.requests if row["method"] == "core.download")
    assert call["params"][0:3] == ["config.save", [{"secretseed": True}], "truenas-config.tar"]
    assert call["params"][3] is False
    code = cli.run(argv=["system", "config-export", str(target), "--config", str(config), "--allow-write"])
    captured = capsys.readouterr()
    assert code != 0 and "overwrite" in (captured.out + captured.err)


def test_system_cli_covers_review_refusals_and_job_flows(config, capsys):
    from tnt.session import open_session

    def cli_for(server):
        def open_fake(profile, **kwargs):
            credential = kwargs.pop("secret", server.secret())
            return open_session(server.profile(profile.name), secret=credential, allow_loopback_ws=True, **kwargs)
        return build_cli(opener=open_fake)

    with FakeServer() as server:
        cli = cli_for(server)
        code = cli.run(argv=["system", "reboot", "--config", str(config), "--dry-run"])
        captured = capsys.readouterr()
        output = captured.out
        assert code == 0 and "system.reboot" in output, captured
        assert "system.reboot" not in server.called()

        code = cli.run(argv=["system", "shutdown", "--config", str(config), "--confirm", "test"])
        captured = capsys.readouterr()
        assert code != 0 and "--allow-write" in (captured.out + captured.err)
        assert "system.shutdown" not in server.called()

        code = cli.run(argv=["system", "reboot", "--config", str(config), "--allow-write", "--confirm", "wrong"])
        captured = capsys.readouterr()
        assert code != 0 and "test" in (captured.out + captured.err)
        assert "system.reboot" not in server.called()

        code = cli.run(argv=["system", "reboot", "--config", str(config), "--allow-write", "--confirm", "test",
                             "--delay", "12"])
        output = capsys.readouterr().out
        assert code == 0 and "host is rebooting" in output
        reboot = next(row for row in server.requests if row["method"] == "system.reboot")
        assert reboot["params"] == ["tnt requested reboot", {"delay": 12}]

    with FakeServer() as server:
        cli = cli_for(server)
        code = cli.run(argv=["system", "shutdown", "--config", str(config), "--allow-write", "--confirm", "test"])
        output = capsys.readouterr().out
        assert code == 0 and "host is shutting down" in output
        shutdown = next(row for row in server.requests if row["method"] == "system.shutdown")
        assert shutdown["params"] == ["tnt requested shutdown", {"delay": None}]

    with FakeServer() as server:
        cli = cli_for(server)
        code = cli.run(argv=["system", "update", "check", "--config", str(config), "--json"])
        update = json.loads(capsys.readouterr().out)
        assert code == 0 and update["new_version"] == "TrueNAS-27.0.0-RC.2"
        target = update["new_version"]
        code = cli.run(argv=["system", "update", "apply", target, "--config", str(config), "--allow-write",
                             "--confirm", target])
        captured = capsys.readouterr()
        assert code == 0 and "System action completed" in captured.out
        assert "100%" in captured.err
        request = next(row for row in server.requests if row["method"] == "update.run")
        assert request["params"][0]["version"] == target and request["params"][0]["reboot"] is False


def test_system_config_export_cli_dry_run_and_invalid_download_result(config, opener, capsys, tmp_path, monkeypatch):
    import tnt.cli as cli_module

    target = tmp_path / "preview.tar"
    cli = build_cli(opener=opener)
    code = cli.run(argv=["system", "config-export", str(target), "--config", str(config), "--dry-run"])
    assert code == 0 and "core.download" in capsys.readouterr().out
    code = cli.run(argv=["system", "config-export", str(target), "--config", str(config)])
    captured = capsys.readouterr()
    assert code != 0 and "--allow-write" in (captured.out + captured.err)

    monkeypatch.setattr(cli_module, "apply_plan", lambda *args, **kwargs: ["invalid"])
    code = cli.run(argv=["system", "config-export", str(target), "--config", str(config), "--allow-write"])
    captured = capsys.readouterr()
    assert code != 0 and "invalid config export result" in (captured.out + captured.err)
    assert "_download" not in (captured.out + captured.err)


def _select_key(app, key):
    tree = app.query_one("#nav", Tree)
    found = None

    def search(node):
        nonlocal found
        for child in node.children:
            if child.data and child.data.key == key:
                found = child
                return
            search(child)

    search(tree.root)
    assert found is not None
    tree.select_node(found)


async def _wait_for(pilot, predicate, *, count=100):
    for _ in range(count):
        await pilot.pause(0.05)
        if predicate():
            return
    assert predicate(), "condition did not become true before the bounded UI wait ended"


def test_system_update_tui_uses_reviewed_plan_and_shows_job_progress():
    import asyncio

    from tnt.session import open_session

    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        def reconnect(profile, **kwargs):
            credential = kwargs.pop("secret", server.secret())
            return open_session(server.profile(profile.name), secret=credential, allow_loopback_ws=True, **kwargs)

        async def go():
            app = TntApp(session, session_opener=reconnect, reconnect_delays=(0, 0))
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, "update")
                for _ in range(80):
                    await pilot.pause(0.05)
                    if "Update available" in str(app.query_one("#system-update-note", Static).render()):
                        break
                details = str(app.query_one("#system-update-details", Static).render())
                assert "TrueNAS-27.0.0-RC.2" in details and "maintenance release" in details
                await pilot.click("#system-update-plan")
                for _ in range(40):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, PlanReview):
                        break
                assert isinstance(app.screen, PlanReview)
                app.screen.query_one("#plan-confirm", Input).value = "TrueNAS-27.0.0-RC.2"
                await pilot.click("#plan-apply")
                for _ in range(80):
                    await pilot.pause(0.05)
                    if not isinstance(app.screen, PlanReview):
                        break
                assert "Update job completed" in str(app.query_one("#system-update-note", Static).render())
                assert "update.run" in server.called()

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_reboot_tui_reconnects_after_fake_disconnect_with_bounded_attempts():
    import asyncio

    from tnt.session import open_session

    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        def reconnect(profile, **kwargs):
            credential = kwargs.pop("secret", server.secret())
            return open_session(server.profile(profile.name), secret=credential, allow_loopback_ws=True, **kwargs)

        async def go():
            app = TntApp(session, session_opener=reconnect, reconnect_delays=(0, 0))
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, "reboot")
                await pilot.pause(0.1)
                app.screen.query_one("#system-power-delay", Input).value = "0"
                await pilot.click("#system-power-plan")
                for _ in range(40):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, PlanReview):
                        break
                assert isinstance(app.screen, PlanReview)
                app.screen.query_one("#plan-confirm", Input).value = "test"
                await pilot.click("#plan-apply")
                for _ in range(120):
                    await pilot.pause(0.05)
                    note = str(app.screen.query_one("#system-power-note", Static).render())
                    if "returned to READY" in note or "reconnect stopped" in note:
                        break
                assert "returned to READY" in note, note
                assert [request["method"] for request in server.requests].count("system.state") == 2
                assert server.logins == 3

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_system_power_tui_rejects_bad_delay_cancels_plan_and_confirms_shutdown():
    import asyncio
    from types import SimpleNamespace

    from tnt.tui.system import SystemPowerView

    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session, reconnect_delays=(0, 0))
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, "reboot")
                await pilot.pause(0.1)
                view = app.query_one(SystemPowerView)
                view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="unrelated")))
                app.screen.query_one("#system-power-delay", Input).value = "not-a-number"
                await pilot.click("#system-power-plan")
                assert "Plan refused" in str(app.screen.query_one("#system-power-note", Static).render())
                app.screen.query_one("#system-power-delay", Input).value = ""
                view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="system-power-plan")))
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview), count=300)
                assert isinstance(app.screen, PlanReview), str(app.screen.query_one("#system-power-note", Static).render())
                await pilot.pause(0.1)
                await pilot.click("#plan-cancel")
                assert "cancelled" in str(app.screen.query_one("#system-power-note", Static).render())

                _select_key(app, "shutdown")
                await _wait_for(pilot, lambda: app.query("#view-system-shutdown").first() is not None, count=300)
                view = app.query_one(SystemPowerView)
                view.query_one("#system-power-delay", Input).value = "0"
                view.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="system-power-plan")))
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview), count=300)
                assert isinstance(app.screen, PlanReview)
                await pilot.pause(0.1)
                app.screen.query_one("#plan-confirm", Input).value = "test"
                app.screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="plan-apply")))
                await _wait_for(pilot, lambda: "Host shutting down" in str(
                    app.query_one("#system-power-note", Static).render()), count=300)
                assert "system.shutdown" in server.called()

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_system_update_tui_handles_check_failure_no_update_bad_target_and_cancel():
    import asyncio
    from types import SimpleNamespace

    from tnt.tui.system import SystemUpdateView

    with FakeServer() as server:
        server.update_new_version = None
        server.fail["update.status"] = "synthetic update service unavailable"
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, "update")
                await pilot.pause(0.1)
                app.query_one(SystemUpdateView).on_button_pressed(
                    SimpleNamespace(button=SimpleNamespace(id="unrelated")))
                await _wait_for(pilot, lambda: "Update check failed" in str(
                    app.query_one("#system-update-note", Static).render()))
                server.fail.pop("update.status")
                await pilot.click("#system-update-check")
                await _wait_for(pilot, lambda: "No update is currently available" in str(
                    app.query_one("#system-update-note", Static).render()))
                app.query_one("#system-update-target", Input).value = ""
                await pilot.click("#system-update-plan")
                assert "Plan refused" in str(app.query_one("#system-update-note", Static).render())

                server.update_new_version = "TrueNAS-27.0.0-RC.2"
                await pilot.click("#system-update-check")
                await _wait_for(pilot, lambda: app.query_one("#system-update-target", Input).value != "")
                await pilot.click("#system-update-plan")
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview))
                await pilot.click("#plan-cancel")
                assert "cancelled" in str(app.query_one("#system-update-note", Static).render())

        try:
            asyncio.run(go())
        finally:
            session.close()


def test_system_general_tui_exports_config_and_handles_invalid_result_and_lost_path(tmp_path):
    import asyncio

    from tnt.tui.system import SystemGeneralView

    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)

        async def go():
            app = TntApp(session)
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, "general")
                await pilot.pause(0.1)
                view = app.query_one(SystemGeneralView)
                view._plan_cancelled(None)
                assert "cancelled" in str(app.query_one("#system-general-note", Static).render())
                view._plan_applied(None, [])
                assert "invalid download result" in str(app.query_one("#system-general-note", Static).render())
                view._plan_applied(None, [[7, "/_download/7?auth_token=synthetic"]])
                assert "path was lost" in str(app.query_one("#system-general-note", Static).render())

                target = tmp_path / "tui-config.tar"
                app.query_one("#system-general-path", Input).value = str(target)
                app.query_one("#system-general-root-keys", Checkbox).value = True
                await pilot.click("#system-general-plan")
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview))
                await pilot.click("#plan-apply")
                await _wait_for(pilot, lambda: "saved to" in str(app.query_one("#system-general-note", Static).render()))
                assert stat.S_IMODE(target.stat().st_mode) == 0o600
                assert "auth_token" not in str(app.query_one("#system-general-note", Static).render())
                download = next(row for row in server.requests if row["method"] == "core.download")
                assert download["params"][1] == [{"secretseed": True, "root_authorized_keys": True}]
                view._download(0, None, str(tmp_path / "never-created.tar"))
                await _wait_for(pilot, lambda: "Configuration download failed" in str(
                    app.query_one("#system-general-note", Static).render()))

        try:
            asyncio.run(go())
        finally:
            session.close()


@pytest.mark.parametrize(("mode", "expected", "calls"), [
    ("auth-error", "automatic reconnect stopped", 1),
    ("other-error", "automatic reconnect stopped", 1),
    ("unknown-state", "unexpected state", 1),
    ("connect-then-ready", "returned to READY", 3),
    ("state-error-then-ready", "returned to READY", 3),
])
def test_reboot_tui_bounds_reconnect_and_stops_on_non_connection_failures(mode, expected, calls):
    import asyncio
    from types import SimpleNamespace
    from tnt.tui.system import SystemPowerView

    with FakeServer() as server:
        if mode == "unknown-state":
            server._system_reboot = lambda _params: setattr(server, "system_state", "MIGRATING") or True
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        reconnect_calls = 0

        def reconnect(profile, **kwargs):
            nonlocal reconnect_calls
            reconnect_calls += 1
            if mode == "auth-error":
                raise TransportError("auth", "synthetic login rejected")
            if mode == "other-error":
                raise TntError("synthetic reconnect refusal")
            if mode == "connect-then-ready" and reconnect_calls == 1:
                raise TransportError("connect", "synthetic network unavailable")
            if mode == "state-error-then-ready":
                if reconnect_calls == 1:
                    server.fail["system.state"] = "synthetic state endpoint failure"
                else:
                    server.fail.pop("system.state", None)
            credential = kwargs.pop("secret", server.secret())
            return open_session(server.profile(profile.name), secret=credential, allow_loopback_ws=True, **kwargs)

        async def go():
            app = TntApp(session, session_opener=reconnect, reconnect_delays=(0, 0, 0, 0))
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, "reboot")
                def power_ready():
                    try:
                        return "destructive" in str(app.screen.query_one("#system-power-note", Static).render())
                    except Exception:
                        return False

                await _wait_for(pilot, power_ready, count=300)
                app.screen.query_one("#system-power-delay", Input).value = "0"
                app.screen.query_one(SystemPowerView).on_button_pressed(
                    SimpleNamespace(button=SimpleNamespace(id="system-power-plan")))
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview), count=300)
                app.screen.query_one("#plan-confirm", Input).value = "test"
                await pilot.pause(0.1)
                app.screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="plan-apply")))

                def power_result_visible():
                    if isinstance(app.screen, PlanReview):
                        return False
                    try:
                        return expected in str(app.screen.query_one("#system-power-note", Static).render())
                    except Exception:
                        return False

                for _ in range(300):
                    await pilot.pause(0.05)
                    if power_result_visible():
                        break
                try:
                    current_note = str(app.screen.query_one("#system-power-note", Static).render())
                except Exception:
                    current_note = f"screen={type(app.screen).__name__}"
                assert expected in current_note, f"{current_note}; reconnect_calls={reconnect_calls}; " \
                    f"server_calls={server.called()}"
                assert reconnect_calls == calls
                assert server.called().count("system.reboot") == 1

        try:
            asyncio.run(go())
        finally:
            session.close()


@pytest.mark.parametrize(("operation", "reconnect_calls"), [("reboot", 1), ("shutdown", 0)])
def test_power_action_call_error_is_uncertain_and_never_resubmitted(operation, reconnect_calls, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from tnt.tui.system import SystemPowerView

    with FakeServer() as server:
        if operation == "reboot":
            server._system_reboot = lambda _params: True
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        original_call = session.rpc._inner.call

        def lose_power_reply(method, *params, **kwargs):
            if method in {"system.reboot", "system.shutdown"}:
                try:
                    original_call(method, *params, **kwargs)
                finally:
                    server.system_state = "READY"
                raise RuntimeError("synthetic lost reply after the power request")
            return original_call(method, *params, **kwargs)

        monkeypatch.setattr(session.rpc._inner, "call", lose_power_reply)
        reconnect_count = 0

        def reconnect(profile, **kwargs):
            nonlocal reconnect_count
            reconnect_count += 1
            credential = kwargs.pop("secret", server.secret())
            return open_session(server.profile(profile.name), secret=credential, allow_loopback_ws=True, **kwargs)

        async def go():
            app = TntApp(session, session_opener=reconnect, reconnect_delays=(0,))
            async with app.run_test(size=(160, 50)) as pilot:
                _select_key(app, operation)
                await _wait_for(pilot, lambda: "destructive" in str(
                    app.screen.query_one("#system-power-note", Static).render()), count=300)
                app.screen.query_one("#system-power-delay", Input).value = "0"
                app.screen.query_one(SystemPowerView).on_button_pressed(
                    SimpleNamespace(button=SimpleNamespace(id="system-power-plan")))
                await _wait_for(pilot, lambda: isinstance(app.screen, PlanReview), count=300)
                app.screen.query_one("#plan-confirm", Input).value = "test"
                await pilot.pause(0.1)
                app.screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="plan-apply")))
                def final_note_visible():
                    if isinstance(app.screen, PlanReview):
                        return False
                    note = str(app.screen.query_one("#system-power-note", Static).render())
                    if operation == "reboot":
                        return "Host is reachable and READY" in note or "automatic reconnect stopped" in note
                    return "outcome is uncertain" in note

                for _ in range(300):
                    await pilot.pause(0.05)
                    if final_note_visible():
                        break
                if isinstance(app.screen, PlanReview):
                    current_note = str(app.screen.query_one("#plan-progress", Static).render())
                else:
                    try:
                        current_note = str(app.screen.query_one("#system-power-note", Static).render())
                    except Exception:
                        current_note = f"screen={type(app.screen).__name__}"
                expected_note = "outcome is uncertain" if operation == "shutdown" else "automatic reconnect stopped"
                assert expected_note in current_note or (operation == "reboot" and "Host is reachable and READY" in current_note), current_note
                assert reconnect_count == reconnect_calls
                assert server.called().count(f"system.{operation}") == 1

        try:
            asyncio.run(go())
        finally:
            session.close()


def _valid_system_preflight():
    return [
        {"hostname": " test "},
        {"privilege": {"roles": ["FULL_ADMIN"]}},
        [{"id": 1, "method": "pool.scrub.run", "state": "RUNNING"},
         {"id": 2, "method": "vm.start", "state": "SUCCESS"}],
        [{"name": "guest", "status": {"state": "RUNNING"}},
         {"name": "old", "status": {"state": "STOPPED"}}],
        [{"name": "web", "status": {"state": "RUNNING"}}],
        4,
    ]


def test_system_preflight_snapshot_normalizes_and_sorts_visible_runtime_state():
    snapshot = _system_preflight_snapshot(_valid_system_preflight())
    assert snapshot == ("test", ((1, "pool.scrub.run"),), ("guest",), ("web",), 4, True)
    empty = _valid_system_preflight()
    empty[2] = []
    empty[3] = [{"name": "stopped", "status": {"state": "STOPPED"}}]
    empty[4] = [{"name": "suspended", "status": {"state": "SUSPENDED"}}]
    assert _system_preflight_snapshot(empty)[1:5] == ((), (), (), 4)


@pytest.mark.parametrize(("index", "value", "message"), [
    (None, [], "complete host"),
    (0, {}, "host name"),
    (0, {"hostname": "  "}, "host name"),
    (1, {}, "FULL_ADMIN"),
    (1, {"privilege": {"roles": "FULL_ADMIN"}}, "FULL_ADMIN"),
    (1, {"privilege": {"roles": ["FULL_ADMIN", 7]}}, "FULL_ADMIN"),
    (1, {"privilege": {"roles": ["SYSTEM_UPDATE_WRITE"]}}, "FULL_ADMIN"),
    (2, {"id": 1}, "core.get_jobs"),
    (2, [{"id": True, "method": "pool.scrub.run", "state": "RUNNING"}], "entry without an ID"),
    (2, [{"id": 1, "method": "", "state": "RUNNING"}], "entry without an ID"),
    (2, [{"id": 1, "method": "pool.scrub.run", "state": None}], "entry without an ID"),
    (2, [{"id": 1, "method": "pool.scrub.run", "state": "CANCELLED"}], "unknown state"),
    (2, [{"id": i, "method": "pool.scrub.run", "state": "SUCCESS"} for i in range(10000)], "10000-row"),
    (3, [{"name": "guest", "status": {}}], "valid name or state"),
    (3, [{"name": "guest", "status": {"state": "MIGRATING"}}], "unknown state"),
    (4, [{"name": "web", "status": {"state": "BROKEN"}}], "unknown state"),
    (5, True, "numeric session count"),
    (5, -1, "numeric session count"),
    (5, 2.5, "numeric session count"),
])
def test_system_preflight_snapshot_refuses_incomplete_or_ambiguous_state(index, value, message):
    results = _valid_system_preflight()
    if index is None:
        results = value
    else:
        results[index] = value
    with pytest.raises(Refused, match=message):
        _system_preflight_snapshot(results)


def _valid_update_raw(*, release_notes="First paragraph.\n\nSecond paragraph.", changelog="Fallback notes.", new=True):
    candidate = {
        "version": "TrueNAS-27.0.0-RC.2",
        "manifest": {"changelog": changelog},
        "release_notes": release_notes,
        "release_notes_url": "https://updates.example.invalid/release",
    }
    raw = {"code": "NORMAL", "status": {"current_version": {
        "train": "TrueNAS-SCALE-27-STABLE", "profile": "SCALE", "matches_profile": True,
    }, "new_version": candidate if new else None}}
    return raw, candidate


def test_update_summary_covers_release_notes_fallback_truncation_and_no_update():
    raw, candidate = _valid_update_raw()
    versions = [{"train": "TrueNAS-SCALE-27-NEXT", "version": candidate}]
    result = _update_status_summary("  TrueNAS-27.0.0-RC.1  ", raw, versions)
    assert result["target_train"] == "TrueNAS-SCALE-27-NEXT"
    assert result["changelog_summary"] == "First paragraph."
    raw, candidate = _valid_update_raw(release_notes="", changelog="Manifest fallback.")
    result = _update_status_summary("v27", raw, [{"train": "train", "version": candidate}])
    assert result["changelog_summary"] == "Manifest fallback."
    raw, candidate = _valid_update_raw(release_notes="x" * 900)
    result = _update_status_summary("v27", raw, [{"train": "train", "version": candidate}])
    assert len(result["changelog_summary"]) == 800 and result["changelog_summary"].endswith("...")
    raw, _ = _valid_update_raw(new=False)
    result = _update_status_summary("v27", raw, [])
    assert result["new_version"] is None and result["target_train"] is None
    assert result["changelog_summary"] == "No release notes were supplied by update.status."


@pytest.mark.parametrize(("current", "raw", "versions", "message"), [
    (None, {}, [], "current version"),
    ("v27", None, [], "current version"),
    ("v27", {}, None, "current version"),
    ("v27", {"code": "ERROR", "error": {"reason": "offline"}}, [], "offline"),
    ("v27", {"code": "ERROR", "error": {}}, [], "unspecified error"),
    ("v27", {"code": "BUSY"}, [], "unknown status code"),
    ("v27", {"code": "NORMAL", "status": {}}, [], "current train/profile"),
    ("v27", {"code": "NORMAL", "status": {"current_version": {"train": "", "profile": "SCALE"}}}, [], "invalid train"),
    ("v27", {"code": "NORMAL", "status": {"current_version": {"train": "stable", "profile": ""}}}, [], "invalid train"),
    ("v27", {"code": "NORMAL", "status": {"current_version": {"train": "stable", "profile": "SCALE"}, "new_version": "bad"}}, [], "invalid new-version"),
])
def test_update_summary_refuses_invalid_status_roots(current, raw, versions, message):
    with pytest.raises(Refused, match=message):
        _update_status_summary(current, raw, versions)


@pytest.mark.parametrize("field,value", [
    ("manifest", None), ("release_notes", []), ("release_notes_url", 12),
])
def test_update_summary_refuses_incomplete_candidate_schema(field, value):
    raw, candidate = _valid_update_raw()
    candidate[field] = value
    with pytest.raises(Refused, match="incomplete v27 available-version"):
        _update_status_summary("v27", raw, [{"train": "stable", "version": candidate}])


@pytest.mark.parametrize("row", [
    {"train": "", "version": {}},
    {"train": "stable", "version": None},
    {"train": "stable", "version": {"version": "v"}},
    {"train": "stable", "version": {"version": "v", "manifest": {}, "release_notes": [], "release_notes_url": None}},
    {"train": "stable", "version": {"version": "v", "manifest": {}, "release_notes": None, "release_notes_url": 7}},
])
def test_update_summary_refuses_malformed_available_version_rows(row):
    raw, _ = _valid_update_raw()
    with pytest.raises(Refused, match="incomplete v27 train/version"):
        _update_status_summary("v27", raw, [row])


def test_update_summary_refuses_zero_exact_train_matches():
    raw, candidate = _valid_update_raw()
    other = dict(candidate)
    other["version"] = "TrueNAS-27.0.0-RC.3"
    versions = [{"train": "stable", "version": other}]
    with pytest.raises(Refused, match="one update train"):
        _update_status_summary("v27", raw, versions)


def test_update_summary_refuses_multiple_exact_train_matches():
    raw, candidate = _valid_update_raw()
    with pytest.raises(Refused, match="one update train"):
        _update_status_summary("v27", raw, [{"train": "stable-a", "version": candidate},
                                             {"train": "stable-b", "version": candidate}])


@pytest.mark.parametrize("path", [None, "", "relative.tar", "/tmp/not-a-tar.zip"])
def test_config_export_path_requires_absolute_tar(path):
    with pytest.raises(Refused, match="absolute.*tar"):
        _absolute_tar_path(path)


def test_config_export_path_refuses_existing_path_and_non_boolean_options(tmp_path):
    target = tmp_path / "exists.tar"
    target.touch()
    with pytest.raises(Refused, match="overwrite"):
        _absolute_tar_path(str(target))
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        try:
            with pytest.raises(Refused, match="root_authorized_keys"):
                session.workflows.config_export_action(str(tmp_path / "new.tar"), root_authorized_keys=1)
        finally:
            session.close()


def test_system_actions_refuse_invalid_operation_delay_flags_and_targets():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            for operation in ("restart", "update", None):
                with pytest.raises(Refused, match="must be reboot or shutdown"):
                    session.workflows.system_power_action(operation)
            for delay in (True, -1, 86401, 1.5, "3"):
                with pytest.raises(Refused, match="delay must"):
                    session.workflows.system_power_action("reboot", delay=delay)
            with pytest.raises(Refused, match="force_with_jobs"):
                session.workflows.system_power_action("reboot", force_with_jobs=1)
            with pytest.raises(Refused, match="exact available target"):
                session.workflows.update_apply_action(" ")
            with pytest.raises(Refused, match="force_with_jobs"):
                session.workflows.update_apply_action("TrueNAS-27.0.0-RC.2", force_with_jobs=1)
            with pytest.raises(Refused, match="not the currently available"):
                session.workflows.update_apply_action("TrueNAS-27.0.0-RC.3")
        finally:
            session.close()


def test_system_preflight_rejects_state_or_role_changes_and_normalizes_method_names():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            action = session.workflows.system_power_action("reboot")
            # Method normalization must not let case or whitespace bypass the exact safety probes.
            normalized = replace(action, calls=(RpcCall(" System.Reboot ", action.calls[0].params, job=True),))
            plan = make_plan(normalized, session.rpc)
            assert plan.action.calls[0].method == " System.Reboot "
            with pytest.raises(Refused, match="exact v27"):
                make_plan(replace(action, probe=(RpcCall("system.info"),)), session.rpc)
            with pytest.raises(Refused, match="safety counts changed"):
                make_plan(replace(action, system_preflight=("other-host", *action.system_preflight[1:])), session.rpc)
            bad_call = replace(action, calls=(RpcCall("system.reboot", action.calls[0].params, job=False),))
            with pytest.raises(Refused, match="v27 job"):
                make_plan(bad_call, session.rpc)
            bad_delay = replace(action, calls=(RpcCall("system.reboot", ("reason", {"delay": True}), job=True),))
            with pytest.raises(Refused, match="v27 job"):
                make_plan(bad_delay, session.rpc)
            with pytest.raises(Refused, match="host-name-confirmed"):
                make_plan(replace(action, confirm_target="wrong-host"), session.rpc)
        finally:
            session.close()


def test_update_preflight_rejects_malformed_or_drifted_apply_calls():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            action = session.workflows.update_apply_action("TrueNAS-27.0.0-RC.2")
            attrs = dict(action.calls[0].params[0])
            attrs["reboot"] = True
            with pytest.raises(Refused, match="leave reboot separate"):
                make_plan(replace(action, calls=(RpcCall("update.run", (attrs,), job=True),)), session.rpc)
            attrs = dict(action.calls[0].params[0])
            attrs["train"] = "different-train"
            with pytest.raises(Refused, match="no longer the version available"):
                make_plan(replace(action, calls=(RpcCall("update.run", (attrs,), job=True),)), session.rpc)
            malformed = replace(action, calls=(RpcCall("update.run", ("bad",), job=True),))
            with pytest.raises(Refused, match="must be a destructive v27 job"):
                make_plan(malformed, session.rpc)
            with pytest.raises(Refused, match="exact v27"):
                make_plan(replace(action, probe=action.probe[:-1] + (RpcCall("system.info"),)), session.rpc)
        finally:
            session.close()


def _config_tar(*, member=True):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        if member:
            body = b"configuration"
            info = tarfile.TarInfo("config.db")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return data.getvalue()


def _install_download_opener(monkeypatch, response):
    from tnt import config_export

    opener = _DownloadOpener(response)
    handlers = []

    def build_opener(*items):
        handlers.extend(items)
        return opener

    monkeypatch.setattr(config_export.urllib.request, "build_opener", build_opener)
    return opener, handlers


def test_config_export_download_accepts_same_origin_absolute_url_and_selects_tls_mode(monkeypatch, server, tmp_path):
    from types import SimpleNamespace
    from tnt import config_export

    target = tmp_path / "export.tar"
    response = _DownloadResponse(_config_tar())
    opener, handlers = _install_download_opener(monkeypatch, response)
    verified_context = object()
    monkeypatch.setattr(config_export.ssl, "create_default_context", lambda: verified_context)
    profile = SimpleNamespace(url="wss://nas.example.invalid/api/current")
    saved = config_export.download_config_export(
        profile, False, 9, "https://nas.example.invalid/_download/9?auth_token=single-use", str(target))
    assert saved == str(target) and target.read_bytes() == _config_tar()
    assert opener.timeout == 120 and opener.request.full_url == "https://nas.example.invalid/_download/9?auth_token=single-use"
    assert opener.request.get_header("Accept") == "application/octet-stream"
    assert any(isinstance(handler, config_export.urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers)
    redirects = next(handler for handler in handlers if isinstance(handler, config_export._NoRedirect))
    assert redirects.redirect_request(None, None, 302, "redirect", None, "https://elsewhere.invalid/") is None
    assert any(isinstance(handler, config_export.urllib.request.HTTPSHandler) and handler._context is verified_context
               for handler in handlers)


def test_config_export_download_uses_explicit_insecure_tls_only_when_acknowledged(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tnt import config_export

    target = tmp_path / "export.tar"
    response = _DownloadResponse(_config_tar())
    opener, handlers = _install_download_opener(monkeypatch, response)
    insecure_context = object()
    monkeypatch.setattr(config_export.ssl, "_create_unverified_context", lambda: insecure_context)
    monkeypatch.setattr(config_export.ssl, "create_default_context", lambda: pytest.fail("verification should be disabled only for explicit insecure mode"))
    profile = SimpleNamespace(url="wss://nas.example.invalid/api/current")
    config_export.download_config_export(
        profile, True, 10, "/_download/10?auth_token=token", str(target))
    assert opener.request.full_url == "https://nas.example.invalid/_download/10?auth_token=token"
    assert any(isinstance(handler, config_export.urllib.request.HTTPSHandler) and handler._context is insecure_context
               for handler in handlers)


@pytest.mark.parametrize(("job_id", "url", "path", "message"), [
    (True, "/_download/1?auth_token=x", "/tmp/out.tar", "invalid job ID"),
    (0, "/_download/1?auth_token=x", "/tmp/out.tar", "invalid job ID"),
    (1, None, "/tmp/out.tar", "invalid one-time URL"),
    (1, "", "/tmp/out.tar", "invalid one-time URL"),
    (1, "/_download/1?auth_token=x", "relative.tar", "absolute"),
    (1, "/_download/1?auth_token=x", "/tmp/out.zip", "absolute"),
    (1, "/_download/1?auth_token=x", "/tmp/no-such-parent/out.tar", "parent directory"),
])
def test_config_export_download_rejects_invalid_identity_url_and_path(job_id, url, path, message, server, monkeypatch):
    from tnt.config_export import download_config_export

    with pytest.raises(Refused, match=message):
        download_config_export(server.profile(), False, job_id, url, path)


@pytest.mark.parametrize("url", [
    "https://attacker.invalid/_download/7?auth_token=x",
    "http://127.0.0.1:1/_download/7?auth_token=x",
    "/_download/8?auth_token=x",
    "/other/7?auth_token=x",
    "/_download/7",
    "/_download/7?auth_token=",
    "/_download/7?auth_token=a&auth_token=b",
    "/_download/7?auth_token=x#fragment",
])
def test_config_export_download_refuses_off_origin_or_malformed_one_time_url(url, server, tmp_path):
    from tnt.config_export import download_config_export

    with pytest.raises(Refused, match="origin|malformed"):
        download_config_export(server.profile(), False, 7, url, str(tmp_path / "out.tar"))


def test_config_export_download_rejects_unsupported_profile_scheme(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tnt.config_export import download_config_export

    with pytest.raises(Refused, match="WebSocket origin"):
        download_config_export(SimpleNamespace(url="ftp://nas.invalid/api"), False, 1,
                               "/_download/1?auth_token=x", str(tmp_path / "out.tar"))


def test_config_export_download_cleans_partial_file_after_non_200_and_empty_body(monkeypatch, server, tmp_path):
    from tnt.config_export import download_config_export

    for status, body, expected in ((503, b"bad", "HTTP 503"), (200, b"", "empty")):
        target = tmp_path / f"bad-{status}.tar"
        _install_download_opener(monkeypatch, _DownloadResponse(body, status=status))
        with pytest.raises(Refused, match=expected):
            download_config_export(server.profile(), False, status, f"/_download/{status}?auth_token=x", str(target))
        assert not target.exists()


@pytest.mark.parametrize(("error", "expected"), [
    (urllib.error.URLError(ConnectionError("offline")), "ConnectionError"),
    (urllib.error.HTTPError("https://host.invalid/", 503, "offline", {}, None), "HTTP 503"),
    (TimeoutError("slow"), "TimeoutError"),
    (OSError("disk full"), "OSError"),
])
def test_config_export_download_cleans_partial_file_after_transport_failure(error, expected, monkeypatch, server, tmp_path):
    from tnt.config_export import download_config_export

    target = tmp_path / "failed.tar"
    _install_download_opener(monkeypatch, _DownloadResponse(error=error))
    with pytest.raises(Refused, match=expected):
        download_config_export(server.profile(), False, 44, "/_download/44?auth_token=x", str(target))
    assert not target.exists()


@pytest.mark.parametrize("body", [b"not a tar", _config_tar(member=False)])
def test_config_export_download_removes_invalid_or_empty_archive(body, monkeypatch, server, tmp_path):
    from tnt.config_export import download_config_export

    target = tmp_path / "invalid.tar"
    _install_download_opener(monkeypatch, _DownloadResponse(body))
    with pytest.raises(Refused, match="valid non-empty tar"):
        download_config_export(server.profile(), False, 45, "/_download/45?auth_token=x", str(target))
    assert not target.exists()


def test_config_export_download_closes_and_cleans_when_write_fails(monkeypatch, server, tmp_path):
    from tnt import config_export

    target = tmp_path / "short-write.tar"
    _install_download_opener(monkeypatch, _DownloadResponse(_config_tar()))
    monkeypatch.setattr(config_export.os, "write", lambda _fd, _view: 0)
    with pytest.raises(Refused, match="OSError"):
        config_export.download_config_export(server.profile(), False, 46,
                                             "/_download/46?auth_token=x", str(target))
    assert not target.exists()


def test_config_export_refuses_local_open_errors(monkeypatch, server, tmp_path):
    from tnt import config_export

    monkeypatch.delattr(config_export.os, "O_NOFOLLOW")
    target = tmp_path / "open-failure.tar"
    for failure, expected in ((FileExistsError(), "overwrite"), (OSError(13, "permission denied"), "cannot create")):
        monkeypatch.setattr(config_export.os, "open", lambda *_args, _failure=failure, **_kwargs: (_ for _ in ()).throw(_failure))
        with pytest.raises(Refused, match=expected):
            config_export.download_config_export(server.profile(), False, 47,
                                                 "/_download/47?auth_token=x", str(target))


@pytest.mark.parametrize(("mode", "response"), [
    ("refused", _DownloadResponse(b"bad", status=503)),
    ("transport", _DownloadResponse(error=urllib.error.URLError(ConnectionError("offline")))),
    ("archive", _DownloadResponse(b"not a tar")),
])
def test_config_export_cleanup_failures_do_not_mask_primary_error(mode, response, monkeypatch, server, tmp_path):
    from pathlib import Path
    from tnt import config_export

    target = tmp_path / f"cleanup-{mode}.tar"
    _install_download_opener(monkeypatch, response)
    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "unlink", lambda _path, **_kwargs: (_ for _ in ()).throw(OSError("busy")))
        expected = {"refused": "HTTP 503", "transport": "ConnectionError", "archive": "valid non-empty tar"}[mode]
        with pytest.raises(Refused, match=expected):
            config_export.download_config_export(server.profile(), False, 48,
                                                  "/_download/48?auth_token=x", str(target))
    if target.exists():
        target.unlink()

