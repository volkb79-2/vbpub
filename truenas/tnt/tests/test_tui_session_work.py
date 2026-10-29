from __future__ import annotations

from types import SimpleNamespace

from tnt.profile import Secret
from tnt.session_controller import WorkerLease
from tnt.tui.session_work import error_message, post, run, run_cancellable


class FakeApp:
    def __init__(self, lease):
        self.lease = lease
        self.callbacks = []
        self.finished = []

    def begin_session_work(self, session):
        assert session is self.lease.session
        return self.lease

    def call_from_thread(self, callback, *args):
        self.callbacks.append((callback, args))

    def finish_session_work(self, lease):
        self.finished.append(lease)

    def deliver_session_callback(self, *args):
        pass


def test_session_work_posts_results_and_scrubs_errors():
    session = SimpleNamespace(credential=Secret("1-placeholder-secret"), rpc=SimpleNamespace())
    lease = WorkerLease(7, session)
    app = FakeApp(lease)
    view = SimpleNamespace(app=app, session=session)
    run(view, lambda current: (current is session) and "loaded", "loaded", "failed")
    assert app.callbacks[0][1][3] == ("loaded",)
    assert app.finished == [lease]

    run_cancellable(view,
                    lambda current, worker: (_ for _ in ()).throw(RuntimeError(
                        f"transport echoed {current.credential.reveal()}")), "loaded", "failed")
    callback, args = app.callbacks[1]
    assert args[3][0] == "transport echoed <redacted>"
    assert app.finished == [lease, lease]
    assert error_message(session, RuntimeError("1-placeholder-secret")) == "<redacted>"
    assert callback.__self__ is app and callback.__func__ is FakeApp.deliver_session_callback


def test_session_work_drops_cancelled_callbacks_and_missing_session_leases():
    session = SimpleNamespace(credential=None)
    lease = WorkerLease(3, session)
    lease.cancelled.set()
    app = FakeApp(lease)
    view = SimpleNamespace(app=app, session=session)
    post(view, lease, "loaded", "late")
    assert app.callbacks == []

    empty = WorkerLease(3, None)
    empty_app = FakeApp(empty)
    empty_view = SimpleNamespace(app=empty_app, session=None)
    called = []
    run(empty_view, lambda current: called.append(current), "loaded", "failed")
    assert called == [] and empty_app.callbacks == [] and empty_app.finished == []

    absent_app = FakeApp(lease)
    absent_app.begin_session_work = lambda _session: None
    absent = SimpleNamespace(app=absent_app, session=session)
    run(absent, lambda current: called.append(current), "loaded", "failed")
    assert called == [] and absent_app.callbacks == []
