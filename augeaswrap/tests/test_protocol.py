from __future__ import annotations

import math

import pytest

import augeaswrap as aw


class ExampleResource:
    kind = "example"

    def addresses(self):
        return ("example:value",)

    def observe(self, _ctx):
        return {"example:value": {"value": 1}}

    def diff(self, _ctx, observed):
        return [aw.Change(self.kind, "example:value", "noop", observed["example:value"], observed["example:value"], self)]

    def stage(self, _ctx, change):
        return change.after

    def commit(self, _ctx, staged):
        return staged

    def post_actions(self, _changes):
        return []

    def verify(self, _ctx):
        return []


def test_problem_string_structured_equality_and_hash_contract():
    problem = aw.Problem("test.code", "message", path="/p", owner="fragment", key="K")
    same = aw.Problem("test.code", "message", path="/p", owner="fragment", key="K")
    assert str(problem) == "message"
    assert problem == "message"
    assert problem == same
    assert problem != aw.Problem("other", "message")
    assert hash(problem) == hash(same)
    assert "sage" in problem
    assert "missing" not in problem
    assert problem.as_dict() == {
        "code": "test.code",
        "message": "message",
        "path": "/p",
        "owner": "fragment",
        "key": "K",
    }


def test_change_constructor_validates_contract_fields():
    with pytest.raises(ValueError, match="kind is required"):
        aw.Change("", "a", "noop", None, None)
    with pytest.raises(ValueError, match="kind is required"):
        aw.Change(None, "a", "noop", None, None)
    with pytest.raises(ValueError, match="address is required"):
        aw.Change("x", "", "noop", None, None)
    with pytest.raises(ValueError, match="address is required"):
        aw.Change("x", None, "noop", None, None)
    with pytest.raises(ValueError, match="action"):
        aw.Change("x", "a", "remove", None, None)
    with pytest.raises(ValueError, match="details"):
        aw.Change("x", "a", "noop", None, None, details=["bad"])
    with pytest.raises(ValueError, match="details"):
        aw.Change("x", "a", "noop", None, None, details=(1,))
    change = aw.Change("x", "a", "change", {"old": 1}, {"new": 2})
    assert change.action == "change"


def test_post_action_constructor_rejects_shell_and_invalid_timeout_values():
    for name, argv, timeout, message in (
        ("", ("command",), 1, "name"),
        ("reload", [], 1, "argv"),
        ("reload", ("",), 1, "non-empty strings"),
        ("reload", ("command",), True, "timeout"),
        ("reload", ("command",), "1", "timeout"),
        ("reload", ("command",), 0, "timeout"),
        ("reload", ("command",), math.inf, "timeout"),
    ):
        with pytest.raises(ValueError, match=message):
            aw.PostAction(name, argv, timeout)
    assert aw.PostAction("reload", ("systemctl", "daemon-reload"), 30).timeout == 30


def test_request_rejects_invalid_writer_resource_collection_and_protocol():
    resource = ExampleResource()
    assert isinstance(resource, aw.Resource)
    with pytest.raises(ValueError, match="writer"):
        aw.Request("", ())
    with pytest.raises(ValueError, match="writer"):
        aw.Request("  ", ())
    with pytest.raises(ValueError, match="writer"):
        aw.Request("two\nlines", ())
    with pytest.raises(ValueError, match="writer"):
        aw.Request("two\rlines", ())
    with pytest.raises(ValueError, match="NUL"):
        aw.Request("writer\x00suffix", ())
    with pytest.raises(ValueError, match="resources must be a tuple"):
        aw.Request("writer", [])
    with pytest.raises(ValueError, match="implement the Augeaswrap resource protocol"):
        aw.Request("writer", (object(),))
    assert aw.Request("writer", (resource,)).resources == (resource,)


def test_plan_result_and_expected_errors_keep_stable_fields():
    request = aw.Request("writer")
    empty = aw.Plan(request, "/", ())
    changed = aw.Plan(
        request,
        "/",
        (aw.Change("example", "example:value", "change", 1, 2),),
    )
    assert not empty.has_changes
    assert changed.has_changes
    assert not aw.Result(None).has_changes
    assert aw.Result("txn", changed=("example:value",)).has_changes

    refused = aw.PlanRefused([aw.Problem("bad", "refused")])
    assert refused.exit_code == 2 and refused.problems[0].code == "bad"
    stale = aw.StalePlan("stale")
    assert stale.exit_code == 2 and str(stale) == "stale"
    failed = aw.ApplyFailed("txn", ["commit a"], "commit b: failed", 2)
    assert failed.txn == "txn"
    assert failed.done == ["commit a"]
    assert failed.exit_code == 4
    assert failed.remedy == "augeaswrap revert txn; or fix and re-run apply"
