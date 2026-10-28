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
