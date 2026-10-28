"""Shared test doubles: an in-memory host and plan helpers."""
import pytest  # noqa: F401
import threading

from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan
from tnt.transport import GuardedRpc


SIZE = 10 ** 9


class ImmediateJob:
    def __init__(self, job_id, value, callback, method):
        self.job_id = job_id
        self.event = threading.Event()
        self.event.set()
        self.value = value
        if callback is not None:
            callback({"id": job_id, "method": method, "state": "SUCCESS",
                      "progress": {"percent": 100, "description": "Complete"}})

    def result(self):
        return self.value


class Stub:
    """An in-memory host: results by method name; records everything it was asked."""

    def __init__(self, **overrides):
        self.results = {
            "pool.query": [{"id": 1, "name": "tank", "topology": {"data": [{"disk": "sdb", "path": "/dev/sdb1"}]}},
                           {"id": 2, "name": "boot-pool", "topology": {"data": [{"disk": "sda3", "path": "/dev/sda3"}]}}],
            "boot.get_state": {"name": "boot-pool", "topology": {"data": [{"disk": "sda3"}]}},
            "disk.query": [{"name": "sda", "serial": "BOOT0001", "pool": "boot-pool", "size": SIZE}, {"name": "sdb", "serial": "DATA0002", "pool": "tank", "size": SIZE},
                           {"name": "sdc", "serial": "SPARE003", "size": SIZE}, {"name": "sdd", "serial": "SPARE004", "size": SIZE}],
        }
        self.results.update(overrides)
        self.log = []
        self._next_job_id = 1

    def call(self, method, *params, job=False, callback=None):
        self.log.append((method, params))
        value = self.results.get(method, "done")
        if isinstance(value, Exception):
            raise value
        if job == "RETURN":
            job_id = str(self._next_job_id)
            self._next_job_id += 1
            return ImmediateJob(job_id, value, callback, method)
        return value

    def close(self):
        pass

    def executed(self):
        return [m for m, _ in self.log]


def guarded(stub, read_only=False):
    return GuardedRpc(stub, read_only=read_only)


def run_plan(stub, action, *, typed=None, allow_write=True, read_only=False):
    rpc = guarded(stub, read_only)
    plan = make_plan(action, rpc)
    return apply_plan(plan, rpc, allow_write=allow_write, typed_confirmation=typed)


def destructive(method, *params, job=False, probe=(RpcCall("disk.query", ([], {})),)):
    return Action("T-1", Kind.DESTRUCTIVE, "test", (RpcCall(method, params, job),), probe=probe, confirm_target="x")
