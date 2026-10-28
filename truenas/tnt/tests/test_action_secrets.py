import pytest

from tnt.actions import Action, Kind, Plan, RpcCall, apply_plan, make_plan
from tnt.errors import Refused, TransportError
from tnt.policy import SafetyState
from tnt.profile import Secret
from tnt.transport import GuardedRpc


PASSWORD = "fake-password-material"


class Inner:
    def __init__(self, fail=False):
        self.fail = fail
        self.params = None

    def call(self, method, *params, job=False):
        self.params = params
        if self.fail:
            raise RuntimeError(f"middleware echoed {params[0]['password']}")
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
    params = ({"password": secret},)
    with pytest.raises(TransportError) as caught:
        with rpc.authorized([("user.create", params, False)], SafetyState()):
            rpc.call("user.create", *params)

    assert inner.params == ({"password": PASSWORD},)
    assert PASSWORD not in str(caught.value)
    assert PASSWORD not in repr(rpc.calls)


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
