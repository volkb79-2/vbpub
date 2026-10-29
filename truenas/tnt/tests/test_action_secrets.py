import threading

import pytest

from tnt.actions import Action, Kind, Plan, RpcCall, apply_plan, make_plan
from tnt.errors import PlanExecutionError, Refused
from tnt.profile import Secret
from tnt.transport import GuardedRpc


PASSWORD = "fake-password-material"


class Inner:
    def __init__(self, fail=False):
        self.fail = fail
        self.params = None

    def call(self, method, *params, job=False):
        self.params = params
        if self.fail and method == "user.set_password":
            secret = params[0].get("password", params[0].get("new_password"))
            raise RuntimeError(f"middleware echoed {secret}")
        if method == "user.query":
            return [{"id": 1, "username": "alice"}]
        if method == "auth.me":
            return {"pw_name": "operator"}
        return {"ok": True}

    def close(self):
        pass


def test_plan_repr_and_render_mask_sensitive_rpc_fields():
    secret = Secret(PASSWORD)
    action = Action("U-1", Kind.MUTATE, "Create user", (RpcCall("user.create", ({"password": secret},)),))
    plan = Plan(action, "fingerprint")

    assert PASSWORD not in repr(action)
    assert PASSWORD not in repr(plan)
    assert PASSWORD not in plan.render()
    assert "***" in plan.render()


def test_system_plan_discloses_whether_job_visibility_is_verified():
    action = Action("TNT-PREFLIGHT", Kind.READ, "Inspect host",
                    (RpcCall("system.info"),),
                    system_preflight=("lab", [], [], [], 0, True))
    plan = Plan(action, "fingerprint")
    assert "FULL_ADMIN verified; the job list covers all sessions." in plan.render()

    limited = Action("TNT-PREFLIGHT", Kind.READ, "Inspect host",
                     (RpcCall("system.info"),),
                     system_preflight=("lab", [], [], [], 0, False))
    assert "FULL_ADMIN verified" not in Plan(limited, "fingerprint").render()


def test_guarded_rpc_reveals_secret_only_to_transport_and_scrubs_failures():
    inner = Inner(fail=True)
    rpc = GuardedRpc(inner, read_only=False)
    secret = Secret(PASSWORD)
    params = ({"username": "alice", "new_password": secret},)
    action = Action("U-1", Kind.MUTATE, "Set password", (RpcCall("user.set_password", params),),
                    (RpcCall("user.query", ([['username', '=', 'alice']], {})), RpcCall("auth.me")))
    plan = make_plan(action, rpc)
    with pytest.raises(PlanExecutionError) as caught:
        apply_plan(plan, rpc, allow_write=True)

    assert inner.params == ({"username": "alice", "new_password": PASSWORD},)
    assert caught.value.partial.calls[0].status == "unknown"
    assert "user.set_password" in caught.value.partial.calls[0].method
    assert PASSWORD not in str(caught.value)
    assert PASSWORD not in repr(rpc.calls)


def test_cancelled_session_aborts_before_the_first_mutating_rpc():
    inner = Inner()
    rpc = GuardedRpc(inner, read_only=False)
    action = Action("U-2", Kind.MUTATE, "Set password",
                    (RpcCall("user.set_password", ({"username": "alice", "new_password": Secret(PASSWORD)},)),),
                    (RpcCall("user.query", ([['username', '=', 'alice']], {})), RpcCall("auth.me")))
    plan = make_plan(action, rpc)
    with pytest.raises(Refused, match="cancelled"):
        apply_plan(plan, rpc, allow_write=True, cancelled=lambda: True)
    assert not any(method == "user.set_password" for method, _params in rpc.calls)


def test_single_use_plan_forwards_sensitive_rpc_fields_without_displaying_them():
    inner = Inner()
    rpc = GuardedRpc(inner, read_only=False)
    secret = Secret(PASSWORD)
    action = Action("U-2", Kind.MUTATE, "Set password",
                    (RpcCall("user.set_password", ({"username": "alice", "new_password": secret},)),),
                    (RpcCall("user.query", ([['username', '=', 'alice']], {})), RpcCall("auth.me")))
    plan = make_plan(action, rpc)

    assert apply_plan(plan, rpc, allow_write=True) == [{"ok": True}]
    assert inner.params == ({"username": "alice", "new_password": PASSWORD},)
    assert PASSWORD not in plan.render()


def test_replacing_a_secret_after_planning_invalidates_the_plan():
    class Host:
        def __init__(self):
            self.writes = 0

        def call(self, method, *params, job=False):
            if method == "user.set_password":
                self.writes += 1
            if method == "user.query":
                return [{"id": 1, "username": "alice"}]
            if method == "auth.me":
                return {"pw_name": "operator"}
            return None

        def close(self):
            pass

    host = Host()
    rpc = GuardedRpc(host, read_only=False)
    original = Action("TNT-41", Kind.MUTATE, "Set password",
                      (RpcCall("user.set_password", ({"username": "alice", "new_password": Secret("first-secret")},)),),
                      (RpcCall("user.query", ([['username', '=', 'alice']], {})), RpcCall("auth.me")))
    plan = make_plan(original, rpc)
    changed = Action("TNT-41", Kind.MUTATE, "Set password",
                     (RpcCall("user.set_password", ({"username": "alice", "new_password": Secret("second-secret")},)),),
                     (RpcCall("user.query", ([['username', '=', 'alice']], {})), RpcCall("auth.me")))

    with pytest.raises(Refused, match="changed since"):
        apply_plan(Plan(changed, plan.fingerprint, plan.session_id, plan.nonce), rpc,
                   allow_write=True)
    assert host.writes == 0


def test_account_access_plan_binds_current_login_at_apply_time():
    from tnt.workflows import Workflows

    class ChangingIdentity:
        def __init__(self):
            self.auth_reads = 0
            self.writes = 0

        def call(self, method, *params, job=False):
            if method == "user.query":
                return [{"id": 8, "username": "alice", "local": True, "builtin": False}]
            if method == "auth.me":
                self.auth_reads += 1
                return {"pw_name": "operator" if self.auth_reads < 3 else "alice"}
            if method == "user.update":
                self.writes += 1
            return None

        def close(self):
            pass

    from tnt.transport import GuardedRpc

    host = ChangingIdentity()
    rpc = GuardedRpc(host, read_only=False)
    action = Workflows(rpc).user_access_action("alice", {"locked": True}, confirm_self=True)
    plan = make_plan(action, rpc)
    with pytest.raises(Refused, match="changed since"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="alice")
    assert host.writes == 0


def test_account_access_plan_refuses_if_login_becomes_target_before_planning():
    from tnt.workflows import Workflows
    from tnt.transport import GuardedRpc

    class ChangingIdentity:
        def __init__(self):
            self.auth_reads = 0
            self.writes = 0

        def call(self, method, *params, job=False):
            if method == "user.query":
                return [{"id": 8, "username": "alice", "local": True, "builtin": False}]
            if method == "auth.me":
                self.auth_reads += 1
                return {"pw_name": "operator" if self.auth_reads == 1 else "alice"}
            if method == "user.update":
                self.writes += 1
            return None

        def close(self):
            pass

    host = ChangingIdentity()
    rpc = GuardedRpc(host, read_only=False)
    action = Workflows(rpc).user_access_action("alice", {"locked": True})
    with pytest.raises(Refused, match="extra confirmation flag"):
        make_plan(action, rpc)
    assert host.writes == 0


@pytest.mark.parametrize("cancel_at,partial", [(4, False), (5, True)])
def test_mutation_cancellation_reports_whether_prior_calls_completed(cancel_at, partial):
    class Host:
        def __init__(self):
            self.jobs = 0

        def call(self, method, *params, job=False):
            if method == "app.query":
                return [{"id": "demo", "name": "Demo", "state": "RUNNING", "upgrade_available": False}]
            if job == "RETURN":
                self.jobs += 1

                class RemoteJob:
                    def __init__(self, job_id):
                        self.job_id = job_id
                        self.event = threading.Event()
                        self.event.set()

                    def result(self):
                        return "started"

                return RemoteJob(str(self.jobs))
            return {"ok": True}

        def close(self):
            pass

    rpc = GuardedRpc(Host(), read_only=False)
    action = Action("TNT-CANCEL", Kind.DESTRUCTIVE, "Restart app Demo",
                    (RpcCall("app.stop", ("demo",), job=True),
                     RpcCall("app.start", ("demo",), job=True)),
                    (RpcCall("app.query", ([['id', '=', 'demo']], {})),), "demo")
    plan = make_plan(action, rpc)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= cancel_at

    if partial:
        with pytest.raises(PlanExecutionError, match="1 completed call") as caught:
            apply_plan(plan, rpc, allow_write=True, typed_confirmation="demo", cancelled=cancelled)
        assert [call.status for call in caught.value.partial.calls] == ["completed", "not-started"]
        assert [method for method, _ in rpc.calls].count("app.stop") == 1
        assert not any(method == "app.start" for method, _ in rpc.calls)
    else:
        with pytest.raises(Refused, match="operation was cancelled"):
            apply_plan(plan, rpc, allow_write=True, typed_confirmation="demo", cancelled=cancelled)
        assert not any(method in {"app.stop", "app.start"} for method, _ in rpc.calls)


@pytest.mark.parametrize("incomplete,expected_status", [(True, "unknown"), (False, "submitted")])
def test_failed_or_cancelled_job_wait_records_uncertain_submission(incomplete, expected_status):
    class Remote:
        job_id = "73"

        def __init__(self):
            self.event = None if incomplete else threading.Event()

        def result(self):
            return "done"

    class Host:
        def call(self, method, *params, job=False, callback=None):
            if method == "app.query":
                return []
            if job == "RETURN":
                return Remote()
            raise AssertionError((method, params, job))

        def close(self):
            pass

    rpc = GuardedRpc(Host(), read_only=False)
    action = Action("TNT-JOB-UNCERTAIN", Kind.MUTATE, "Start app",
                    (RpcCall("app.start", ("demo",), job=True),),
                    (RpcCall("app.query", ([], {})),))
    plan = make_plan(action, rpc)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return not incomplete and checks >= 5

    with pytest.raises(PlanExecutionError) as caught:
        apply_plan(plan, rpc, allow_write=True, cancelled=cancelled)
    record = caught.value.partial.calls[0]
    assert record.status == expected_status and record.job_id == "73"
    assert "app.start" in str(caught.value)
