from __future__ import annotations

import json
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import ApplyFailed, Change, Plan, PlanRefused, PostAction, Problem, Request, StalePlan
from augeaswrap.check import Check
from augeaswrap.engine import apply, journal, load_request, plan, revert
from augeaswrap.file import File
from augeaswrap import engine
from augeaswrap import transactions
from cli_extended.identity import CliIdentity
from cli_extended.parser import CliFailure


def fake_root(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    return root


def request(path: str = "/etc/service.conf", text: str = "managed") -> Request:
    return Request("service install 1", (File(path, "present", text, "0640"),))


def txn_root(root: Path) -> Path:
    return root / "var/lib/augeaswrap/txn"


def txn_dirs(root: Path) -> list[Path]:
    directory = txn_root(root)
    return sorted(path for path in directory.iterdir() if path.is_dir()) if directory.exists() else []


def test_apply_is_journaled_idempotent_and_explicit_revert_restores_preimage(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/service.conf"
    target.parent.mkdir()
    target.write_text("before\n", encoding="utf-8")
    target.chmod(0o600)

    planned = plan(request(text="after\n"), root=root)
    assert planned.has_changes
    result = apply(planned)
    assert result.txn
    assert target.read_text(encoding="utf-8") == "after\n"
    txn = txn_root(root) / result.txn
    assert (txn / "stage").is_dir()
    assert (txn / "pre").is_dir()
    pre_metadata = next((txn / "pre").glob("*.json"))
    metadata = json.loads(pre_metadata.read_text(encoding="utf-8"))
    assert metadata["path"] == "/etc/service.conf"
    assert (txn / "pre" / (pre_metadata.stem + ".data")).read_bytes() == b"before\n"
    applied_journal = json.loads((txn / "journal.json").read_text(encoding="utf-8"))
    assert applied_journal["writer"] == "service install 1"
    assert applied_journal["status"] == "done"
    assert applied_journal["pre_images"]["file:/etc/service.conf"]["mode"] == 0o600

    before_noop = target.stat().st_mtime_ns
    noop = plan(request(text="after\n"), root=root)
    assert not noop.has_changes
    assert all(change.action == "noop" for change in noop.changes)
    assert apply(noop).txn is None
    assert target.stat().st_mtime_ns == before_noop
    assert len(txn_dirs(root)) == 1

    restored = revert(result.txn, root=root)
    assert restored.txn
    assert target.read_text(encoding="utf-8") == "before\n"
    assert target.stat().st_mode & 0o7777 == 0o600
    assert json.loads((txn / "journal.json").read_text(encoding="utf-8"))["reverted_by"] == restored.txn
    assert journal(restored.txn, root=root)["type"] == "revert"


def test_absent_file_reverts_to_exact_original_metadata_and_bytes(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/old.conf"
    target.parent.mkdir()
    target.write_bytes(b"\xffold\x00\n")
    target.chmod(0o640)
    absent = Request("remove old config", (File("/etc/old.conf", "absent"),))
    result = apply(plan(absent, root=root))
    assert result.txn and not target.exists()
    revert(result.txn, root=root)
    assert target.read_bytes() == b"\xffold\x00\n"
    assert target.stat().st_mode & 0o7777 == 0o640


def test_plan_preconditions_refuse_content_or_absence_changes(tmp_path):
    root = fake_root(tmp_path)
    planned = plan(request(), root=root)
    target = root / "etc/service.conf"
    target.parent.mkdir()
    target.write_text("external", encoding="utf-8")
    with pytest.raises(StalePlan, match="changed after plan"):
        apply(planned)

    target.unlink()
    present_plan = plan(request(), root=root)
    target.write_text("appeared", encoding="utf-8")
    with pytest.raises(StalePlan, match="changed after plan"):
        apply(present_plan)


def test_plan_refuses_duplicate_resource_addresses(tmp_path):
    root = fake_root(tmp_path)
    req = Request(
        "duplicate",
        (
            File("/etc/same.conf", "present", "a", "0644"),
            File("/etc/same.conf", "present", "b", "0644"),
        ),
    )
    with pytest.raises(PlanRefused, match="duplicate address"):
        plan(req, root=root)


def test_multi_address_resource_is_reobserved_for_preconditions_and_postimages_and_requires_json_safe_observations(tmp_path):
    root = fake_root(tmp_path)

    class MultiAddressResource:
        kind = "memory"

        def __init__(self):
            self.observe_calls = 0

        def addresses(self):
            return ("memory:first", "memory:second")

        def observe(self, _ctx):
            self.observe_calls += 1
            return {address: {"value": "before"} for address in self.addresses()}

        def diff(self, _ctx, observed):
            return [
                Change(self.kind, address, "change", observed[address], {"value": "after"}, self)
                for address in self.addresses()
            ]

        def stage(self, _ctx, change):
            return change.after

        def commit(self, _ctx, staged):
            return staged

        def post_actions(self, _changes):
            return []

        def verify(self, _ctx):
            return []

    resource = MultiAddressResource()
    result = apply(plan(Request("multi address", (resource,)), root=root))
    assert result.txn
    assert resource.observe_calls == 3

    class NonJsonResource(MultiAddressResource):
        def addresses(self):
            return ("memory:nan",)

        def observe(self, _ctx):
            return {"memory:nan": {"value": float("nan")}}

    with pytest.raises(PlanRefused, match="planning memory failed"):
        plan(Request("non-json", (NonJsonResource(),)), root=root)


def test_unknown_post_image_marker_is_idempotent():
    value = {
        "unknown_post_images": ["fake:a"],
        "post_images": {"fake:a": "optimistic"},
    }
    transactions._record_unknown_post_image(value, "fake:a")
    assert value == {"unknown_post_images": ["fake:a"], "post_images": {}}


def test_diff_changes_must_belong_to_resource_and_be_json_serializable(tmp_path):
    root = fake_root(tmp_path)

    class DiffResource:
        kind = "memory"

        def __init__(self, *, foreign=False, invalid=False, empty_resource=False):
            self.foreign = foreign
            self.invalid = invalid
            self.empty_resource = empty_resource

        def addresses(self):
            return ("memory:value",)

        def observe(self, _ctx):
            return {"memory:value": {"value": "before"}}

        def diff(self, _ctx, observed):
            return [Change(
                self.kind,
                "memory:value",
                "change",
                b"invalid" if self.invalid else observed["memory:value"],
                {"value": "after"},
                object() if self.foreign else None if self.empty_resource else self,
            )]

        def stage(self, _ctx, change):
            return change.after

        def commit(self, _ctx, staged):
            return staged

        def post_actions(self, _changes):
            return []

        def verify(self, _ctx):
            return []

    empty_resource = DiffResource(empty_resource=True)
    planned = plan(Request("missing change resource", (empty_resource,)), root=root)
    assert planned.changes[0].resource is empty_resource
    with pytest.raises(PlanRefused, match="another resource"):
        plan(Request("foreign change resource", (DiffResource(foreign=True),)), root=root)
    with pytest.raises(PlanRefused, match="not JSON serializable"):
        plan(Request("invalid change data", (DiffResource(invalid=True),)), root=root)


def test_stage_failure_discards_transaction_and_writes_no_target(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    monkeypatch.setattr(File, "stage", lambda *_args: (_ for _ in ()).throw(OSError("invalid staged image")))
    with pytest.raises(PlanRefused, match="staging failed"):
        apply(plan(request(), root=root))
    assert not (root / "etc/service.conf").exists()
    assert txn_dirs(root) == []


def test_commit_failure_keeps_journal_and_manual_revert_restores_partial_apply(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    req = Request(
        "two files",
        (
            File("/etc/first.conf", "present", "first", "0644"),
            File("/etc/second.conf", "present", "second", "0644"),
        ),
    )
    original_commit = File.commit

    def fail_second(self, ctx, staged):
        if self.path == "/etc/second.conf":
            raise OSError("injected second commit failure")
        return original_commit(self, ctx, staged)

    monkeypatch.setattr(File, "commit", fail_second)
    with pytest.raises(ApplyFailed, match="applied 1/") as caught:
        apply(plan(req, root=root))
    monkeypatch.setattr(File, "commit", original_commit)
    assert caught.value.exit_code == 4
    assert caught.value.remedy == f"augeaswrap revert {caught.value.txn}; or fix and re-run apply"
    failed = journal(caught.value.txn, root=root)
    assert failed["status"] == "failed"
    assert failed["steps"][0]["status"] == "done"
    assert failed["steps"][1]["status"] == "failed"
    assert all(step["status"] == "not-run" for step in failed["steps"][2:])
    assert (root / "etc/first.conf").read_text(encoding="utf-8") == "first"

    result = revert(caught.value.txn, root=root)
    assert result.txn
    assert not (root / "etc/first.conf").exists()
    assert not (root / "etc/second.conf").exists()


def test_revert_refuses_when_a_later_transaction_touched_same_address(tmp_path):
    root = fake_root(tmp_path)
    first = apply(plan(request(text="first"), root=root)).txn
    second = apply(plan(request(text="second"), root=root)).txn
    assert first and second
    with pytest.raises(PlanRefused, match=second):
        revert(first, root=root)
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "second"


def test_revert_allows_a_later_transaction_that_failed_before_touching_address(tmp_path):
    root = fake_root(tmp_path)
    first = apply(plan(request(text="first"), root=root)).txn
    assert first
    with pytest.raises(ApplyFailed) as failed:
        apply(plan(request(text="second"), root=root), fail_step=1)
    assert journal(failed.value.txn, root=root)["post_images"] == {}
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "first"
    reverted = revert(first, root=root)
    assert reverted.txn
    assert not (root / "etc/service.conf").exists()


def test_revert_refuses_when_a_later_journal_cannot_prove_order(tmp_path):
    root = fake_root(tmp_path)
    first = apply(plan(request(text="first"), root=root)).txn
    assert first
    later_id = "99991231T235959Z-ffff"
    later = txn_root(root) / later_id
    later.mkdir()
    journal_path = later / "journal.json"
    journal_path.write_text(json.dumps({"schema": 1, "id": later_id}), encoding="utf-8")
    with pytest.raises(PlanRefused, match="unreadable journal"):
        revert(first, root=root)
    journal_path.write_text(json.dumps({"schema": 1, "id": later_id, "root": str(root)}), encoding="utf-8")
    with pytest.raises(PlanRefused, match="invalid journal"):
        revert(first, root=root)


def test_revert_refuses_external_drift_without_later_transaction(tmp_path):
    root = fake_root(tmp_path)
    txn = apply(plan(request(text="first"), root=root)).txn
    assert txn
    (root / "etc/service.conf").write_text("outside", encoding="utf-8")
    with pytest.raises(PlanRefused, match="changed outside Augeaswrap"):
        revert(txn, root=root)


def test_revert_refuses_when_target_becomes_a_symlink(tmp_path):
    root = fake_root(tmp_path)
    txn = apply(plan(request(text="managed"), root=root)).txn
    assert txn
    target = root / "etc/service.conf"
    target.unlink()
    outside = tmp_path / "outside.conf"
    outside.write_text("outside", encoding="utf-8")
    target.symlink_to(outside)
    with pytest.raises(PlanRefused, match="cannot observe current state"):
        revert(txn, root=root)


def test_failure_after_atomic_replace_records_the_changed_postimage(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    target = root / "etc/service.conf"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    planned = plan(request(text="after"), root=root)
    original_commit = File.commit

    def write_then_fail(self, ctx, staged):
        original_commit(self, ctx, staged)
        raise OSError("failed after replacing file")

    monkeypatch.setattr(File, "commit", write_then_fail)
    with pytest.raises(ApplyFailed) as caught:
        apply(planned)
    failed = journal(caught.value.txn, root=root)
    assert "file:/etc/service.conf" in failed["post_images"]
    assert target.read_text(encoding="utf-8") == "after"


def test_unobservable_commit_failure_is_journaled_and_cannot_be_reverted(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    target = root / "etc/service.conf"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    planned = plan(request(text="after"), root=root)
    original_commit = File.commit
    original_observe = File.observe
    unobservable = False

    def commit_then_hide(self, ctx, staged):
        nonlocal unobservable
        original_commit(self, ctx, staged)
        unobservable = True
        raise OSError("commit completed but post-image is inaccessible")

    def observe(self, ctx):
        if unobservable:
            raise OSError("post-image cannot be observed")
        return original_observe(self, ctx)

    monkeypatch.setattr(File, "commit", commit_then_hide)
    monkeypatch.setattr(File, "observe", observe)
    with pytest.raises(ApplyFailed) as caught:
        apply(planned)
    monkeypatch.setattr(File, "commit", original_commit)
    monkeypatch.setattr(File, "observe", original_observe)
    failed = journal(caught.value.txn, root=root)
    assert failed["unknown_post_images"] == ["file:/etc/service.conf"]
    with pytest.raises(PlanRefused, match="post-image could not be observed"):
        revert(caught.value.txn, root=root)


def test_fail_step_hook_stops_at_requested_commit_and_keeps_manual_revert(tmp_path):
    root = fake_root(tmp_path)
    req = Request(
        "fail step",
        (
            File("/etc/first.conf", "present", "first", "0644"),
            File("/etc/second.conf", "present", "second", "0644"),
        ),
    )
    with pytest.raises(ApplyFailed, match="injected failure") as caught:
        apply(plan(req, root=root), fail_step=2)
    assert (root / "etc/first.conf").exists()
    assert not (root / "etc/second.conf").exists()
    assert journal(caught.value.txn, root=root)["status"] == "failed"
    revert(caught.value.txn, root=root)
    assert not (root / "etc/first.conf").exists()


class ActionResource:
    kind = "memory"

    def addresses(self):
        return ("memory:action",)

    def observe(self, _ctx):
        return {"memory:action": {"value": "before"}}

    def diff(self, _ctx, observed):
        return [Change(self.kind, "memory:action", "change", observed["memory:action"], {"value": "after"}, self)]

    def stage(self, _ctx, change):
        return change.after

    def commit(self, _ctx, staged):
        return staged

    def post_actions(self, changes):
        return [PostAction("reload", ("reload-command",), 1.0, phase="runtime")] if changes else []

    def verify(self, _ctx):
        return []


def test_post_action_is_skipped_for_fake_root_and_failure_is_journaled(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    plan_value = plan(Request("memory writer", (ActionResource(),)), root=root)
    assert plan_value.as_dict()["post_actions"][0]["status"] == "skipped(root)"
    result = apply(plan_value)
    assert result.actions == ("reload: skipped(root)",)
    assert journal(result.txn, root=root)["actions"][0]["status"] == "skipped(root)"

    class DuplicateActions(ActionResource):
        def post_actions(self, changes):
            action = PostAction("reload", ("reload-command",), 1.0, phase="runtime")
            return [action, action] if changes else []

    duplicate = plan(Request("duplicate actions", (DuplicateActions(),)), root=root)
    assert len(duplicate.as_dict()["post_actions"]) == 1

    class ConflictingActions(ActionResource):
        def post_actions(self, changes):
            return [
                PostAction("reload", ("reload-command",), 1.0, phase="runtime"),
                PostAction("reload", ("reload-command",), 2.0, phase="runtime"),
            ] if changes else []

    with pytest.raises(PlanRefused, match="conflicting timeouts"):
        plan(Request("conflicting actions", (ConflictingActions(),)), root=root)

    root2 = fake_root(tmp_path / "other")
    planned = plan(Request("memory writer", (ActionResource(),)), root=root2)
    monkeypatch.setattr(transactions, "_run_post_action", lambda *_args: (_ for _ in ()).throw(RuntimeError("reload failed")))
    with pytest.raises(ApplyFailed, match="reload failed") as caught:
        apply(planned)
    failed = journal(caught.value.txn, root=root2)
    assert failed["failed"]["step"] == "post-action reload"
    assert failed["status"] == "failed"
    assert failed["actions"][0]["status"] == "failed"
    assert failed["actions"][0]["addresses"] == ["memory:action"]


def test_live_post_action_success_nonzero_timeout_and_oserror(monkeypatch):
    import subprocess

    action = PostAction("reload", ("reload-tool",), 2.0, phase="runtime")
    monkeypatch.setattr(engine.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert engine._run_post_action(Path("/"), action) == "reload: done"
    for result, message in (
        (SimpleNamespace(returncode=1, stdout="", stderr="bad config\n"), "exit 1: bad config"),
        (SimpleNamespace(returncode=1, stdout="", stderr=""), "no diagnostic"),
    ):
        monkeypatch.setattr(engine.subprocess, "run", lambda *_args, _result=result, **_kwargs: _result)
        with pytest.raises(RuntimeError, match=message):
            engine._run_post_action(Path("/"), action)
    for failure in (OSError("missing action"), subprocess.TimeoutExpired("reload-tool", 2)):
        monkeypatch.setattr(engine.subprocess, "run", lambda *_args, _failure=failure, **_kwargs: (_ for _ in ()).throw(_failure))
        with pytest.raises(RuntimeError):
            engine._run_post_action(Path("/"), action)


def test_verify_failure_stops_and_records_failed_step(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    resource = ActionResource()
    planned = plan(Request("verify writer", (resource,)), root=root)
    monkeypatch.setattr(ActionResource, "verify", lambda *_args: [Problem("verify.failed", "mismatch")])
    with pytest.raises(ApplyFailed, match="mismatch") as caught:
        apply(planned)
    assert journal(caught.value.txn, root=root)["failed"]["step"] == "verify memory #1"


def test_transaction_allocator_retries_collision_and_journal_retains_last_fifty(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    directory = txn_root(root)
    directory.mkdir(parents=True)
    collision = "20261108T000000Z-0000"
    (directory / collision).mkdir()
    generated = iter([collision, "20261108T000000Z-0001"])
    monkeypatch.setattr(transactions, "_new_txn_id", lambda: next(generated))
    monkeypatch.setattr(transactions, "_ordered_txn_id", lambda _root, candidate: candidate)
    txn_id, txn_dir_value = transactions._allocate_txn(root)
    assert txn_id == "20261108T000000Z-0001"
    assert txn_dir_value.is_dir()

    txn_root(root).mkdir(parents=True, exist_ok=True)
    for index in range(51):
        txn = txn_root(root) / f"20261108T000001Z-{index:04x}"
        txn.mkdir()
        (txn / "journal.json").write_text(
            json.dumps({"schema": 1, "id": txn.name, "writer": "w", "root": str(root)}),
            encoding="utf-8",
        )
    transactions._prune_transactions(root)
    assert len(txn_dirs(root)) == 50
    listed = journal(root=root)
    assert len(listed) == 50
    assert listed[0]["id"] > listed[-1]["id"]


def test_transaction_allocator_orders_same_second_and_clock_rollback(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    monkeypatch.setattr(transactions, "_new_txn_id", lambda: "20261108T000001Z-0000")
    first, _first_dir = transactions._allocate_txn(root)
    second, _second_dir = transactions._allocate_txn(root)
    assert second > first

    monkeypatch.setattr(transactions, "_new_txn_id", lambda: "20261107T235959Z-ffff")
    after_rollback, _rollback_dir = transactions._allocate_txn(root)
    assert after_rollback > second

    advanced_root = fake_root(tmp_path / "advanced")
    latest = txn_root(advanced_root) / "20261108T000000Z-ffff"
    latest.mkdir(parents=True)
    monkeypatch.setattr(transactions, "_new_txn_id", lambda: "20261108T000001Z-0000")
    advanced, _advanced_dir = transactions._allocate_txn(advanced_root)
    assert advanced == "20261108T000001Z-0000"


def test_transaction_allocator_advances_after_sequence_exhaustion_and_rejects_bad_ids(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    latest = txn_root(root) / "20261108T000001Z-ffff"
    latest.mkdir(parents=True)
    monkeypatch.setattr(transactions, "_new_txn_id", lambda: latest.name)
    txn_id, _txn_dir_value = transactions._allocate_txn(root)
    assert txn_id == "20261108T000002Z-0000"

    bad_root = fake_root(tmp_path / "bad")
    monkeypatch.setattr(transactions, "_new_txn_id", lambda: "invalid")
    with pytest.raises(ValueError, match="invalid id"):
        transactions._allocate_txn(bad_root)

    collision_root = fake_root(tmp_path / "exhausted")
    collision = txn_root(collision_root) / "20261108T000000Z-0000"
    collision.mkdir(parents=True)
    monkeypatch.setattr(transactions, "_new_txn_id", lambda: collision.name)
    monkeypatch.setattr(transactions, "_ordered_txn_id", lambda _root, candidate: candidate)
    with pytest.raises(OSError, match="after 100 attempts"):
        transactions._allocate_txn(collision_root)


def test_revert_replays_recorded_post_actions_for_restored_addresses(tmp_path):
    class FileWithReload(File):
        def post_actions(self, changes):
            return [PostAction("reload", ("reload-command",), 1.0, phase="runtime")] if changes else []

    root = fake_root(tmp_path)
    resource = FileWithReload("/etc/service.conf", "present", "managed", "0644")
    applied = apply(plan(Request("reload writer", (resource,)), root=root))
    assert applied.txn
    assert journal(applied.txn, root=root)["actions"][0]["addresses"] == ["file:/etc/service.conf"]

    restored = revert(applied.txn, root=root)
    assert restored.txn
    assert journal(restored.txn, root=root)["actions"][0]["status"] == "skipped(root)"


def test_journal_refuses_a_transaction_from_another_root(tmp_path):
    original_root = fake_root(tmp_path / "original")
    applied = apply(plan(request(), root=original_root))
    assert applied.txn
    other_root = fake_root(tmp_path / "other")
    copied = txn_root(other_root) / applied.txn
    copied.mkdir(parents=True)
    source_journal = txn_root(original_root) / applied.txn / "journal.json"
    (copied / "journal.json").write_bytes(source_journal.read_bytes())
    with pytest.raises(PlanRefused, match="different root"):
        journal(applied.txn, root=other_root)


def test_journal_rejects_invalid_or_malformed_transaction(tmp_path):
    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match="invalid format"):
        journal("bad", root=root)
    bad_id = "20261108T000000Z-0000"
    bad_dir = txn_root(root) / bad_id
    bad_dir.mkdir(parents=True)
    (bad_dir / "journal.json").write_text("not json", encoding="utf-8")
    with pytest.raises(PlanRefused, match="cannot read transaction"):
        journal(bad_id, root=root)


def test_request_loader_is_strict_roundtrips_and_has_no_owner_or_prune(tmp_path):
    req = Request(
        "svc install 2",
        (
            File("/etc/service.conf", "present", "line\n", "0640", create_only=True),
            Check("present", exec=("/bin/true",), expect_exit=0, timeout=1.5),
        ),
    )
    encoded = req.to_toml()
    assert encoded.startswith("augeaswrap = 1\n")
    assert "owner =" not in encoded and "prune =" not in encoded
    path = tmp_path / "request.toml"
    path.write_text(encoded, encoding="utf-8")
    decoded = load_request(path)
    assert decoded == req

    for mapping, expected in (
        ({"augeaswrap": True, "writer": "w"}, "schema must be integer"),
        ({"augeaswrap": 1, "writer": "w", "owner": "legacy"}, "unknown request key"),
        ({"augeaswrap": 1}, "missing request field"),
        ({"augeaswrap": 1, "writer": "w", "file": [{"path": "/etc/a", "state": "present", "text": "x"}]}, "missing file field"),
        ({"augeaswrap": 1, "writer": "w", "extra": 1}, "unknown request key"),
    ):
        with pytest.raises(ValueError, match=expected):
            load_request(mapping)


def test_load_request_rejects_toml_parse_and_missing_path(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("augeaswrap = [", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot load Augeaswrap request"):
        load_request(bad)
    missing = tmp_path / "missing.toml"
    with pytest.raises(ValueError, match="cannot load Augeaswrap request"):
        load_request(missing)


@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/argument-shape/argument:route:entrypoint:augeaswrap/apply/request")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/minimum")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/option-spelling/option:route:entrypoint:augeaswrap/apply/--root/--root")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/option-spelling/option:route:entrypoint:augeaswrap/apply/--yes/--yes")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/option-spelling/option:route:entrypoint:augeaswrap/apply/--dry-run/--dry-run")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/argument-choice/argument:route:entrypoint:augeaswrap/journal/action/8f06acb022")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/argument-choice/argument:route:entrypoint:augeaswrap/journal/action/dcb452a982")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/argument-shape/argument:route:entrypoint:augeaswrap/journal/action")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/argument-shape/argument:route:entrypoint:augeaswrap/journal/txn")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/minimum")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/option-spelling/option:route:entrypoint:augeaswrap/journal/--root/--root")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/plan/argument-shape/argument:route:entrypoint:augeaswrap/plan/request")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/plan/minimum")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/plan/option-spelling/option:route:entrypoint:augeaswrap/plan/--root/--root")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/argument-shape/argument:route:entrypoint:augeaswrap/revert/txn")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/minimum")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/option-spelling/option:route:entrypoint:augeaswrap/revert/--root/--root")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/option-spelling/option:route:entrypoint:augeaswrap/revert/--yes/--yes")
def test_cli_every_verb_uses_fake_root_and_confirmation_contract(tmp_path):
    from augeaswrap.cli import build_cli

    root = fake_root(tmp_path)
    request_path = tmp_path / "request.toml"
    request_path.write_text(request().to_toml(), encoding="utf-8")
    identity = CliIdentity(name="AUGEASWRAP", version="test", long_name="Augeaswrap tests", command="augeaswrap")
    app = build_cli(identity)

    plan_out = app.run(argv=["plan", str(request_path), "--root", str(root)], stdout=(plan_stdout := io.StringIO()), stderr=io.StringIO())
    assert plan_out == 10 and "changes are pending" in plan_stdout.getvalue()
    assert not (root / "etc/service.conf").exists()

    apply_out = app.run(argv=["apply", str(request_path), "--root", str(root)], stdout=(apply_stdout := io.StringIO()), stderr=(apply_stderr := io.StringIO()), stdin=io.StringIO(""))
    assert apply_out == 2 and "--yes" in apply_stderr.getvalue()
    assert not (root / "etc/service.conf").exists()

    dry_status = app.run(argv=["apply", str(request_path), "--root", str(root), "--dry-run"], stdout=io.StringIO(), stderr=io.StringIO(), stdin=io.StringIO(""))
    assert dry_status == 0
    assert not (root / "etc/service.conf").exists()

    yes_status = app.run(argv=["apply", str(request_path), "--root", str(root), "--yes"], stdout=(yes_stdout := io.StringIO()), stderr=io.StringIO(), stdin=io.StringIO(""))
    assert yes_status == 0 and "Applied" in yes_stdout.getvalue()
    txn_id = journal(root=root)[0]["id"]

    list_status = app.run(argv=["journal", "list", "--root", str(root), "--json"], stdout=(list_stdout := io.StringIO()), stderr=io.StringIO())
    assert list_status == 0 and txn_id in list_stdout.getvalue()
    default_list_status = app.run(argv=["journal", "--root", str(root)], stdout=(default_list_stdout := io.StringIO()), stderr=io.StringIO())
    assert default_list_status == 0 and txn_id in default_list_stdout.getvalue()
    show_status = app.run(argv=["journal", "show", txn_id, "--root", str(root), "--json"], stdout=(show_stdout := io.StringIO()), stderr=io.StringIO())
    assert show_status == 0 and json.loads(show_stdout.getvalue())["id"] == txn_id

    revert_status = app.run(argv=["revert", txn_id, "--root", str(root), "--yes"], stdout=(revert_stdout := io.StringIO()), stderr=io.StringIO(), stdin=io.StringIO(""))
    assert revert_status == 0 and "Reverted" in revert_stdout.getvalue()
    assert not (root / "etc/service.conf").exists()

    no_change = app.run(argv=["plan", str(request_path), "--root", str(root), "--json"], stdout=(json_stdout := io.StringIO()), stderr=io.StringIO())
    assert no_change == 10 and json.loads(json_stdout.getvalue())["has_changes"]

    class TTY(io.StringIO):
        def isatty(self):
            return True

    tty_root = fake_root(tmp_path / "tty")
    tty_apply = app.run(
        argv=["apply", str(request_path), "--root", str(tty_root)],
        stdout=io.StringIO(),
        stderr=(tty_stderr := io.StringIO()),
        stdin=TTY("yes\n"),
    )
    assert tty_apply == 0 and "[y/N]" in tty_stderr.getvalue()
    assert (tty_root / "etc/service.conf").read_text(encoding="utf-8") == "managed"


@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/option-spelling/option:route:entrypoint:augeaswrap/journal/--json/--json")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/plan/option-spelling/option:route:entrypoint:augeaswrap/plan/--json/--json")
def test_cli_plan_apply_and_revert_report_json_refusals_and_root_mismatch(tmp_path):
    from augeaswrap.cli import build_cli

    root = fake_root(tmp_path)
    planned = plan(request(), root=root)
    with pytest.raises(StalePlan, match="root differs"):
        apply(planned, root=Path("/"))

    identity = CliIdentity(name="AUGEASWRAP", version="test", long_name="Augeaswrap tests", command="augeaswrap")
    app = build_cli(identity)
    bad = tmp_path / "bad.toml"
    bad.write_text("augeaswrap = 1\nwriter = 'bad'\nowner = 'old'\n", encoding="utf-8")
    stdout = io.StringIO()
    status = app.run(argv=["plan", str(bad), "--root", str(root), "--json"], stdout=stdout, stderr=io.StringIO())
    assert status == 2
    assert json.loads(stdout.getvalue())["status"] == "refused"


@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/option-spelling/option:route:entrypoint:augeaswrap/apply/--json/--json")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/option-spelling/option:route:entrypoint:augeaswrap/revert/--json/--json")
def test_cli_apply_and_revert_json_emit_one_document(tmp_path):
    from augeaswrap.cli import build_cli

    root = fake_root(tmp_path)
    request_path = tmp_path / "request.toml"
    request_path.write_text(request().to_toml(), encoding="utf-8")
    identity = CliIdentity(name="AUGEASWRAP", version="test", long_name="Augeaswrap tests", command="augeaswrap")
    app = build_cli(identity)

    apply_stdout = io.StringIO()
    apply_status = app.run(
        argv=["apply", str(request_path), "--root", str(root), "--yes", "--json"],
        stdout=apply_stdout,
        stderr=io.StringIO(),
    )
    assert apply_status == 0
    applied = json.loads(apply_stdout.getvalue())
    assert applied["status"] == "applied"
    txn_id = applied["result"]["txn"]
    assert txn_id

    revert_stdout = io.StringIO()
    revert_status = app.run(
        argv=["revert", txn_id, "--root", str(root), "--yes", "--json"],
        stdout=revert_stdout,
        stderr=io.StringIO(),
    )
    assert revert_status == 0
    reverted = json.loads(revert_stdout.getvalue())
    assert reverted["status"] == "reverted"
    assert reverted["result"]["txn"]
    assert not (root / "etc/service.conf").exists()


@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/apply/option-spelling/option:route:entrypoint:augeaswrap/apply/--debug-raw/--debug-raw")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/journal/option-spelling/option:route:entrypoint:augeaswrap/journal/--debug-raw/--debug-raw")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/plan/option-spelling/option:route:entrypoint:augeaswrap/plan/--debug-raw/--debug-raw")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/option-spelling/option:route:entrypoint:augeaswrap/revert/--debug-raw/--debug-raw")
@pytest.mark.cli_case("case:route:entrypoint:augeaswrap/revert/option-spelling/option:route:entrypoint:augeaswrap/revert/--dry-run/--dry-run")
def test_cli_debug_raw_is_shared_by_all_verbs(tmp_path):
    from augeaswrap.cli import build_cli

    root = fake_root(tmp_path)
    request_path = tmp_path / "request.toml"
    request_path.write_text(request().to_toml(), encoding="utf-8")
    identity = CliIdentity(name="AUGEASWRAP", version="test", long_name="Augeaswrap tests", command="augeaswrap")
    app = build_cli(identity)

    assert app.run(argv=["plan", str(request_path), "--root", str(root), "--debug-raw"], stdout=io.StringIO(), stderr=io.StringIO()) == 10
    assert app.run(argv=["apply", str(request_path), "--root", str(root), "--debug-raw", "--dry-run"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    result = apply(plan(request(), root=root))
    assert result.txn
    assert app.run(argv=["journal", "list", "--root", str(root), "--debug-raw"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert app.run(argv=["revert", result.txn, "--root", str(root), "--debug-raw", "--dry-run"], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    assert (root / "etc/service.conf").exists()


def test_root_path_must_exist_and_not_be_symlinked(tmp_path):
    with pytest.raises(CliFailure, match="does not exist"):
        plan(request(), root=tmp_path / "missing")
    real = fake_root(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(CliFailure, match="symlinked root"):
        plan(request(), root=link)
