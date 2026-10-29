"""Reviewed System > Update, Reboot, Shutdown, and General Settings workflows."""
from __future__ import annotations

import json
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input

from ..actions import Plan, make_plan
from ..errors import TntError, TransportError
from ..session import Session
from .workflows import PlanReview
from .session_work import (begin as begin_session_work, error_message, post as post_session_callback, report_unavailable,
                           run as run_session_work, run_cancellable as run_cancellable_session_work)
from .widgets import Static


def _call_outcomes(result: Any) -> list[dict[str, Any]]:
    return [call.as_dict() for call in getattr(result, "calls", ())]


class SystemPowerView(Vertical):
    DEFAULT_CSS = """
    SystemPowerView { height: 1fr; }
    SystemPowerView #system-power-note { height: auto; color: $text-muted; padding: 0 1; }
    SystemPowerView #system-power-job { height: auto; max-height: 8; overflow-y: auto; padding: 0 1; }
    SystemPowerView #system-power-form { height: auto; padding: 1; }
    SystemPowerView Input { width: 1fr; }
    """

    def __init__(self, session: Session, operation: str):
        super().__init__(id=f"view-system-{operation}")
        self.session = session
        self.operation = operation

    def compose(self) -> ComposeResult:
        yield Static(f"System {self.operation}: every action is destructive and requires typing the host name.",
                     id="system-power-note")
        yield Static("", id="system-power-job")
        with Vertical(id="system-power-form"):
            yield Input(placeholder="Delay in seconds (blank for immediate)", id="system-power-delay")
            yield Checkbox("Force only when running jobs are listed; typed host confirmation is still required",
                           id="system-power-force")
            yield Button(f"Plan {self.operation}", id="system-power-plan", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "system-power-plan":
            return
        raw_delay = self.query_one("#system-power-delay", Input).value.strip()
        try:
            delay = None if not raw_delay else int(raw_delay)
        except ValueError as exc:
            self.query_one("#system-power-note", Static).update(f"Plan refused: {exc}")
            return
        force = self.query_one("#system-power-force", Checkbox).value
        self.query_one("#system-power-note", Static).update("Checking current host state and preparing the plan…")
        self.query_one("#system-power-plan", Button).disabled = True
        self._make_plan(delay, force)

    @work(thread=True, exclusive=True)
    def _make_plan(self, delay: int | None, force: bool) -> None:
        def build_and_plan(session, lease):
            action = session.workflows.system_power_action(
                self.operation, delay=delay, force_with_jobs=force)
            return make_plan(action, session.rpc, cancelled=lease.cancelled.is_set)

        run_cancellable_session_work(
            self, build_and_plan, "_plan_ready", "_plan_failed")

    def _plan_ready(self, plan: Plan) -> None:
        self.query_one("#system-power-plan", Button).disabled = False
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_failed(self, message: str) -> None:
        self.query_one("#system-power-plan", Button).disabled = False
        self.query_one("#system-power-note", Static).update(f"Plan refused: {message}")

    def _plan_applied(self, plan: Plan, result: Any) -> None:
        outcomes = _call_outcomes(result)
        if outcomes:
            self.query_one("#system-power-job", Static).update(
                "Applied call outcomes:\n" + json.dumps(outcomes, indent=2, sort_keys=True))
        if self.operation == "shutdown":
            self.query_one("#system-power-note", Static).update("Host shutting down. The session will end.")
            return
        self.query_one("#system-power-note", Static).update("Host rebooting. Waiting before reconnecting…")
        self._reconnect_after_reboot()

    def _plan_apply_uncertain(self, plan: Plan, calls: list[dict[str, Any]]) -> None:
        if calls:
            self.query_one("#system-power-job", Static).update(
                "Uncertain applied call outcomes:\n" + json.dumps(calls, indent=2, sort_keys=True))
        if self.operation == "shutdown":
            self.query_one("#system-power-note", Static).update(
                "Shutdown request outcome is uncertain after the connection closed. Verify host state before retrying.")
            return
        self.query_one("#system-power-note", Static).update(
            "Reboot request outcome is uncertain; checking the host without resubmitting the action…")
        self._reconnect_after_reboot(uncertain=True)

    def _plan_cancelled(self, plan: Plan) -> None:
        self.query_one("#system-power-note", Static).update("System action cancelled.")

    @work(thread=True, exclusive=True)
    def _reconnect_after_reboot(self, *, uncertain: bool = False) -> None:
        app = self.app
        lease = begin_session_work(self)
        if lease is None or lease.session is None:
            report_unavailable(
                self, "_reconnect_not_started",
                "Could not check whether the reboot finished because the active host session changed. "
                "Verify host state before retrying.")
            return
        delays = app.reconnect_delays
        last_error = "the host did not return to READY"
        saw_power_transition = False
        try:
            for attempt, delay in enumerate(delays, 1):
                if lease.cancelled.is_set():
                    return
                post_session_callback(self, lease, "_reconnect_progress", attempt, len(delays), delay)
                if lease.cancelled.wait(delay):
                    return
                try:
                    app.session_controller.reconnect_current(lease.generation, lease.session)
                except TransportError as exc:
                    last_error = error_message(lease.session, exc)
                    if exc.kind != "connect":
                        break
                    continue
                except TntError as exc:
                    last_error = error_message(lease.session, exc)
                    break
                except Exception as exc:
                    last_error = f"reconnect failed: {type(exc).__name__}"
                    break
                try:
                    state = lease.session.rpc.call("system.state")
                except TransportError as exc:
                    last_error = error_message(lease.session, exc)
                    # State probes are read-only and safe to retry within this bounded loop.
                    if exc.kind in {"connect", "call"}:
                        continue
                    break
                except TntError as exc:
                    last_error = error_message(lease.session, exc)
                    break
                if state == "READY":
                    post_session_callback(self, lease, "_reconnected", uncertain and not saw_power_transition)
                    return
                if state not in {"BOOTING", "SHUTTING_DOWN"}:
                    last_error = f"system.state returned unexpected state {state!r}"
                    break
                saw_power_transition = True
                last_error = f"host is still {state.lower()}"
            post_session_callback(self, lease, "_reconnect_failed", last_error, uncertain)
        finally:
            app.finish_session_work(lease)

    def _reconnect_not_started(self, message: str) -> None:
        self.query_one("#system-power-note", Static).update(message)

    def _reconnect_progress(self, attempt: int, total: int, delay: int) -> None:
        self.query_one("#system-power-note", Static).update(
            f"Host rebooting. Reconnect attempt {attempt}/{total} after {delay} seconds; login retries are bounded.")

    def _reconnected(self, uncertain: bool = False) -> None:
        self.app.query_one("#banner", Static).update(self.session.banner())
        message = ("Host is reachable and READY. The reboot request outcome remains uncertain; inspect host state "
                   "before retrying." if uncertain else "Host rebooted and returned to READY.")
        self.query_one("#system-power-note", Static).update(message)

    def _reconnect_failed(self, reason: str, uncertain: bool = False) -> None:
        if uncertain:
            message = (f"Reboot request outcome is uncertain; automatic reconnect stopped after "
                       f"{len(self.app.reconnect_delays)} bounded attempts: {reason}. "
                       "Verify host state before retrying the power action.")
        else:
            message = (f"Host rebooting; automatic reconnect stopped after {len(self.app.reconnect_delays)} bounded "
                       f"attempts: {reason}. Exit tnt and reconnect later.")
        self.query_one("#system-power-note", Static).update(message)


class SystemUpdateView(Vertical):
    DEFAULT_CSS = """
    SystemUpdateView { height: 1fr; }
    SystemUpdateView #system-update-note { height: auto; color: $text-muted; padding: 0 1; }
    SystemUpdateView #system-update-details { height: 1fr; overflow-y: auto; padding: 1; }
    SystemUpdateView #system-update-form { height: auto; padding: 1; }
    SystemUpdateView Input { width: 1fr; }
    """

    def __init__(self, session: Session):
        super().__init__(id="view-system-update")
        self.session = session
        self.status: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield Static("Update status has not been checked.", id="system-update-note")
        yield Static("", id="system-update-details", markup=False)
        with Vertical(id="system-update-form"):
            yield Input(placeholder="Exact target version to apply", id="system-update-target")
            yield Checkbox("Force only when running jobs are listed", id="system-update-force")
            with Horizontal():
                yield Button("Check for updates", id="system-update-check")
                yield Button("Plan update", id="system-update-plan", variant="error")

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#system-update-note", Static).update("Checking update status…")
        self._check()

    @work(thread=True, exclusive=True)
    def _check(self) -> None:
        run_session_work(self, lambda session: session.workflows.update_check(), "_checked", "_check_failed")

    def _check_failed(self, message: str) -> None:
        self.status = None
        self.query_one("#system-update-note", Static).update(f"Update check failed: {message}")
        self.query_one("#system-update-details", Static).update("")

    def _checked(self, status: dict[str, Any]) -> None:
        self.status = status
        self.query_one("#system-update-note", Static).update(
            "Update available." if status.get("new_version") else "No update is currently available.")
        details = {
            "current_train": status.get("train"), "target_train": status.get("target_train"),
            "profile": status.get("profile"),
            "current_version": status.get("current_version"), "new_version": status.get("new_version"),
            "changelog_summary": status.get("changelog_summary"),
            "release_notes_url": status.get("release_notes_url"),
        }
        self.query_one("#system-update-details", Static).update(json.dumps(details, indent=2, sort_keys=True))
        if status.get("new_version"):
            self.query_one("#system-update-target", Input).value = str(status["new_version"])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "system-update-check":
            self.action_refresh()
            return
        if event.button.id != "system-update-plan":
            return
        target = self.query_one("#system-update-target", Input).value.strip()
        force = self.query_one("#system-update-force", Checkbox).value
        self.query_one("#system-update-note", Static).update("Checking current host state and preparing the plan…")
        self.query_one("#system-update-plan", Button).disabled = True
        self._make_plan(target, force)

    @work(thread=True, exclusive=True)
    def _make_plan(self, target: str, force: bool) -> None:
        def build_and_plan(session, lease):
            action = session.workflows.update_apply_action(target, force_with_jobs=force)
            return make_plan(action, session.rpc, cancelled=lease.cancelled.is_set)

        run_cancellable_session_work(
            self, build_and_plan, "_plan_ready", "_plan_failed")

    def _plan_ready(self, plan: Plan) -> None:
        self.query_one("#system-update-plan", Button).disabled = False
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_failed(self, message: str) -> None:
        self.query_one("#system-update-plan", Button).disabled = False
        self.query_one("#system-update-note", Static).update(f"Plan refused: {message}")

    def _plan_applied(self, plan: Plan, result: Any) -> None:
        outcomes = _call_outcomes(result)
        self.query_one("#system-update-note", Static).update(
            f"Update job completed for {plan.action.confirm_target}. Reboot is separate; check for updates again after installation.\n"
            f"Applied call outcomes:\n{json.dumps(outcomes, indent=2, sort_keys=True)}")

    def _plan_cancelled(self, plan: Plan) -> None:
        self.query_one("#system-update-note", Static).update("Update apply cancelled.")


class SystemGeneralView(Vertical):
    DEFAULT_CSS = """
    SystemGeneralView { height: 1fr; }
    SystemGeneralView #system-general-note { height: auto; color: $text-muted; padding: 0 1; }
    SystemGeneralView #system-general-form { height: auto; padding: 1; }
    SystemGeneralView Input { width: 1fr; }
    """

    def __init__(self, session: Session):
        super().__init__(id="view-system-general")
        self.session = session
        self.pending_path: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Configuration export includes the secret seed and is written as a new mode-0600 tar file.",
                     id="system-general-note")
        with Vertical(id="system-general-form"):
            yield Input(placeholder="New absolute local path ending in .tar", id="system-general-path")
            yield Checkbox("Include root authorized keys", id="system-general-root-keys")
            yield Button("Plan configuration export", id="system-general-plan", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "system-general-plan":
            return
        path = self.query_one("#system-general-path", Input).value.strip()
        root_authorized_keys = self.query_one("#system-general-root-keys", Checkbox).value
        self.pending_path = path
        self.query_one("#system-general-note", Static).update("Checking current host state and preparing the plan…")
        self.query_one("#system-general-plan", Button).disabled = True
        self._make_plan(path, root_authorized_keys)

    @work(thread=True, exclusive=True)
    def _make_plan(self, path: str, root_authorized_keys: bool) -> None:
        def build_and_plan(session, lease):
            action = session.workflows.config_export_action(path, root_authorized_keys=root_authorized_keys)
            return make_plan(action, session.rpc, cancelled=lease.cancelled.is_set)

        run_cancellable_session_work(
            self, build_and_plan, "_plan_ready", "_plan_failed")

    def _plan_ready(self, plan: Plan) -> None:
        self.query_one("#system-general-plan", Button).disabled = False
        self.app.push_screen(PlanReview(plan, self.session, parent_view=self))

    def _plan_failed(self, message: str) -> None:
        self.pending_path = None
        self.query_one("#system-general-plan", Button).disabled = False
        self.query_one("#system-general-note", Static).update(f"Plan refused: {message}")

    def _plan_applied(self, plan: Plan, result: Any) -> None:
        if not isinstance(result, list) or not result or not isinstance(result[0], (list, tuple)) or len(result[0]) != 2:
            self.pending_path = None
            self.query_one("#system-general-note", Static).update(
                "Configuration export job returned an invalid download result; no URL was displayed.")
            return
        job_id, download_url = result[0]
        path = self.pending_path
        self.pending_path = None
        if path is None:
            self.query_one("#system-general-note", Static).update("Configuration export path was lost; download was not started.")
            return
        self.query_one("#system-general-note", Static).update("Downloading configuration tar over the selected host connection…")
        self._download(job_id, download_url, path)

    def _plan_cancelled(self, plan: Plan) -> None:
        self.pending_path = None
        self.query_one("#system-general-note", Static).update("Configuration export cancelled.")

    @work(thread=True, exclusive=True)
    def _download(self, job_id: Any, download_url: Any, path: str) -> None:
        from ..config_export import download_config_export
        run_session_work(
            self,
            lambda session: download_config_export(session.profile, session.insecure_tls, job_id, download_url, path),
            "_downloaded", "_download_failed")

    def _download_failed(self, message: str) -> None:
        self.query_one("#system-general-note", Static).update(f"Configuration download failed: {message}")

    def _downloaded(self, path: str) -> None:
        self.query_one("#system-general-note", Static).update(
            f"Configuration tar saved to {path} with mode 0600. It contains the TrueNAS secret seed; store it securely.")
