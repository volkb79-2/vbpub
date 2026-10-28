"""Helpers for host-bound Textual workers and generation-checked callbacks."""
from __future__ import annotations

from typing import Any, Callable

from ..session import Session
from ..session_controller import WorkerLease


def begin(view: Any) -> WorkerLease | None:
    """Register one worker against the exact session captured by its view."""
    return view.app.begin_session_work(view.session)


def post(view: Any, lease: WorkerLease, callback: str, *args: Any) -> None:
    """Queue a callback which the app rechecks on the UI thread."""
    if lease.cancelled.is_set():
        return
    view.app.call_from_thread(view.app.deliver_session_callback, lease, view, callback, args)


def error_message(session: Session, error: BaseException) -> str:
    message = str(error)
    if session.credential is not None:
        message = session.credential.scrub(message)
    return message


def run(view: Any, operation: Callable[[Session], Any], on_success: str, on_failure: str) -> None:
    """Run a view RPC and send only a still-current result back to the UI."""
    run_cancellable(view, lambda session, lease: operation(session), on_success, on_failure)


def run_cancellable(view: Any, operation: Callable[[Session, WorkerLease], Any], on_success: str,
                    on_failure: str) -> None:
    """Run an operation that can check its lease between network calls."""
    lease = begin(view)
    if lease is None or lease.session is None:
        return
    try:
        try:
            result = operation(lease.session, lease)
        except Exception as exc:
            post(view, lease, on_failure, error_message(lease.session, exc))
        else:
            post(view, lease, on_success, result)
    finally:
        view.app.finish_session_work(lease)
