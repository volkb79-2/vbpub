"""Coverage-fill wave B: runtime-network verification in ``ciu.worktree``.

Targets ``_docker_network_details``, ``_verify_unrecorded_nested_root_runtime``
and ``verify_own_runtime_network``.  Docker is replaced at the ``procutil``
boundary (or at the network-details seam); Git, the shared-workspace records,
the generated-facts files and the allocation lock are real.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402

from test_ciu_worktree import (  # noqa: E402,F401
    _git,
    fake_generate_env,
    tmp_repo,
)

NET_ID = "e" * 64


# --------------------------------------------------------------------------
# _docker_network_details
# --------------------------------------------------------------------------

def _docker_returning(monkeypatch, inspect_result=None, inspect_exc=None):
    """Fake docker: `network ls` lists `wanted`; `network inspect` is scripted."""
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["network", "ls"]:
            return subprocess.CompletedProcess(argv, 0, "wanted\n", "")
        if inspect_exc is not None:
            raise inspect_exc
        return inspect_result(argv)

    monkeypatch.setattr(worktree.procutil, "docker", docker)
    return calls


def _inspect_payload(payload):
    return lambda argv: subprocess.CompletedProcess(
        argv, 0, json.dumps(payload), ""
    )


def test_network_details_none_when_network_absent(monkeypatch):
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "other\n", "")

    monkeypatch.setattr(worktree.procutil, "docker", docker)

    assert worktree._docker_network_details("wanted") is None
    assert all(call[:2] == ["network", "ls"] for call in calls)


@pytest.mark.parametrize("exc", [FileNotFoundError("no docker"), OSError("boom")])
def test_network_details_inspect_launch_failure_is_a_probe_error(monkeypatch, exc):
    _docker_returning(monkeypatch, inspect_exc=exc)

    with pytest.raises(
        worktree.RuntimeNetworkProbeError,
        match=r"could not inspect existing runtime network 'wanted'.*" + str(exc),
    ):
        worktree._docker_network_details("wanted")


@pytest.mark.parametrize(
    "stdout,stderr,expected",
    [("", "daemon said no\n", "daemon said no"), ("out only\n", "", "out only")],
)
def test_network_details_inspect_nonzero_reports_output(
    monkeypatch, stdout, stderr, expected
):
    _docker_returning(
        monkeypatch,
        inspect_result=lambda argv: subprocess.CompletedProcess(
            argv, 1, stdout, stderr
        ),
    )

    with pytest.raises(
        worktree.RuntimeNetworkProbeError,
        match=r"could not inspect existing runtime network 'wanted': " + expected,
    ):
        worktree._docker_network_details("wanted")


def test_network_details_invalid_json_is_a_probe_error(monkeypatch):
    _docker_returning(
        monkeypatch,
        inspect_result=lambda argv: subprocess.CompletedProcess(
            argv, 0, "not json", ""
        ),
    )

    with pytest.raises(worktree.RuntimeNetworkProbeError, match="invalid JSON"):
        worktree._docker_network_details("wanted")


def test_network_details_empty_stdout_is_invalid_json(monkeypatch):
    _docker_returning(
        monkeypatch,
        inspect_result=lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )

    with pytest.raises(worktree.RuntimeNetworkProbeError, match="invalid JSON"):
        worktree._docker_network_details("wanted")


@pytest.mark.parametrize(
    "payload",
    [
        {"Name": "wanted"},
        [],
        [{"Name": "wanted", "Id": NET_ID}, {"Name": "wanted", "Id": NET_ID}],
        ["wanted"],
        [{"Name": "wanted-other", "Id": NET_ID}],
    ],
)
def test_network_details_requires_exactly_the_named_network(monkeypatch, payload):
    _docker_returning(monkeypatch, inspect_result=_inspect_payload(payload))

    with pytest.raises(
        worktree.RuntimeNetworkProbeError, match="did not return exactly that network"
    ):
        worktree._docker_network_details("wanted")


@pytest.mark.parametrize("network_id", [None, 7, "short", "E" * 64, "g" * 64])
def test_network_details_requires_a_full_object_id(monkeypatch, network_id):
    entry = {"Name": "wanted", "Labels": {}}
    if network_id is not None:
        entry["Id"] = network_id
    _docker_returning(monkeypatch, inspect_result=_inspect_payload([entry]))

    with pytest.raises(
        worktree.RuntimeNetworkProbeError, match="returned no full object id"
    ):
        worktree._docker_network_details("wanted")


def test_network_details_null_labels_become_empty_mapping(monkeypatch):
    _docker_returning(
        monkeypatch,
        inspect_result=_inspect_payload(
            [{"Name": "wanted", "Id": NET_ID, "Labels": None}]
        ),
    )

    assert worktree._docker_network_details("wanted") == {
        "id": NET_ID, "labels": {},
    }


def test_network_details_missing_labels_key_also_becomes_empty(monkeypatch):
    _docker_returning(
        monkeypatch,
        inspect_result=_inspect_payload([{"Name": "wanted", "Id": NET_ID}]),
    )

    assert worktree._docker_network_details("wanted") == {
        "id": NET_ID, "labels": {},
    }


@pytest.mark.parametrize(
    "labels",
    [["a"], "a=b", {"k": 1}, {"k": None}, {"k": "v", "j": ["x"]}],
)
def test_network_details_rejects_malformed_labels(monkeypatch, labels):
    _docker_returning(
        monkeypatch,
        inspect_result=_inspect_payload(
            [{"Name": "wanted", "Id": NET_ID, "Labels": labels}]
        ),
    )

    with pytest.raises(worktree.RuntimeNetworkProbeError, match="malformed labels"):
        worktree._docker_network_details("wanted")


# --------------------------------------------------------------------------
# verify_own_runtime_network
# --------------------------------------------------------------------------

def _owned_details(record):
    return lambda network: (
        {"id": NET_ID, "labels": {worktree._NETWORK_OWNER_LABEL: record.instance_id}}
        if network == record.network else None
    )


def test_verify_rejects_lock_reuse_without_allocating_check(tmp_path):
    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="allocation-lock reuse requires an allocating-record check",
    ):
        worktree.verify_own_runtime_network(
            tmp_path,
            expected_instance_id="i",
            expected_network="n",
            allocation_lock_held=True,
        )


def test_verify_primary_checkout_without_shared_record_returns_none(
    tmp_repo, fake_generate_env
):
    assert worktree.verify_own_runtime_network(
        tmp_repo, expected_instance_id="i", expected_network="n",
    ) is None


def test_verify_wraps_shared_record_lookup_failure(tmp_repo, monkeypatch):
    def boom(_path):
        raise worktree.WorktreeError("lookup exploded")

    monkeypatch.setattr(worktree, "_shared_record_for_checkout", boom)

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="could not determine whether .* is a managed checkout without its "
        "CIU record: lookup exploded",
    ):
        worktree.verify_own_runtime_network(
            tmp_repo, expected_instance_id="i", expected_network="n",
        )


def test_verify_requires_logical_name_label_on_shared_ciu_record(
    tmp_repo, monkeypatch
):
    shared = type("S", (), {})()
    shared.purpose = "ciu"
    shared.labels = {}
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _p: shared)

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="no valid logical-name label"
    ):
        worktree.verify_own_runtime_network(
            tmp_repo, expected_instance_id="i", expected_network="n",
        )


def test_verify_non_ciu_shared_record_is_not_a_managed_ciu_checkout(
    tmp_repo, monkeypatch
):
    shared = type("S", (), {})()
    shared.purpose = "generic"
    shared.labels = {}
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _p: shared)

    assert worktree.verify_own_runtime_network(
        tmp_repo, expected_instance_id="i", expected_network="n",
    ) is None


def test_verify_nested_root_without_record_delegates_to_nested_verifier(
    tmp_repo, monkeypatch
):
    shared = type("S", (), {})()
    shared.purpose = "ciu"
    shared.labels = {"ciu.logical_name": "lg"}
    seen = {}
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _p: shared)

    def nested(ciu_root, shared_record, *, expected_instance_id, expected_network):
        seen.update(
            root=ciu_root, shared=shared_record,
            iid=expected_instance_id, net=expected_network,
        )

    monkeypatch.setattr(worktree, "_verify_unrecorded_nested_root_runtime", nested)

    assert worktree.verify_own_runtime_network(
        tmp_repo, expected_instance_id="i", expected_network="n",
    ) is None
    assert seen == {
        "root": tmp_repo.resolve(), "shared": shared, "iid": "i", "net": "n",
    }


def test_verify_refuses_record_that_is_not_ready(tmp_repo, fake_generate_env):
    record = worktree.create(tmp_repo, "verify-not-ready")
    worktree._write_instance_record(replace(record, state="recovery-required"))

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match=r"'verify-not-ready' is 'recovery-required'; run `ciu worktree "
        r"ensure verify-not-ready`",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )


def test_verify_allocating_record_accepted_only_when_allowed(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-allocating")
    allocating = replace(record, state="allocating")
    worktree._write_instance_record(allocating)
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _n: None)

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="is 'allocating'"
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )
    result = worktree.verify_own_runtime_network(
        record.ciu_root,
        expected_instance_id=record.instance_id,
        expected_network=record.network,
        allow_allocating_instance=True,
    )
    assert result == allocating


def test_verify_lock_held_reuses_the_callers_lock(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-lock-held")
    worktree._write_instance_record(replace(record, state="allocating"))
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _n: None)

    def no_lock(_root):
        raise AssertionError("must not take the allocation lock again")

    monkeypatch.setattr(worktree, "_allocation_lock", no_lock)

    result = worktree.verify_own_runtime_network(
        record.ciu_root,
        expected_instance_id=record.instance_id,
        expected_network=record.network,
        allow_allocating_instance=True,
        allocation_lock_held=True,
    )
    assert result.state == "allocating"


def test_verify_identityless_record_returns_as_is_without_root_marker(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-identityless")
    bare = replace(record, instance_id=None, network=None)
    worktree._write_instance_record(bare)
    monkeypatch.setattr(worktree, "_ciu_root_marker_present", lambda _r: False)

    assert worktree.verify_own_runtime_network(
        record.ciu_root, expected_instance_id=None, expected_network="x",
    ) == bare


def test_verify_identityless_record_with_root_marker_requires_ensure(
    tmp_repo, fake_generate_env
):
    record = worktree.create(tmp_repo, "verify-identityless-marker")
    # An allocating record may legitimately lack identity; the committed CIU
    # root marker makes that absence a defect rather than a generic worktree.
    worktree._write_instance_record(
        replace(record, state="allocating", instance_id=None, network=None)
    )
    assert (record.ciu_root / worktree.GLOBAL_CONFIG_DEFAULTS).is_file()

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="has no saved runtime identity; run `ciu worktree ensure "
        "verify-identityless-marker`",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root, expected_instance_id=None, expected_network="x",
            allow_allocating_instance=True,
        )


@pytest.mark.parametrize("which", ["instance", "network"])
def test_verify_identity_mismatch_refuses(tmp_repo, fake_generate_env, which):
    record = worktree.create(tmp_repo, f"verify-mismatch-{which}")
    kwargs = {
        "expected_instance_id": record.instance_id,
        "expected_network": record.network,
    }
    kwargs["expected_instance_id" if which == "instance" else "expected_network"] = "other"

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="generated runtime identity .* does not match its managed worktree record",
    ):
        worktree.verify_own_runtime_network(record.ciu_root, **kwargs)


def test_verify_sibling_collision_marks_recovery(tmp_repo, fake_generate_env):
    first = worktree.create(tmp_repo, "verify-collide-a")
    second = worktree.create(tmp_repo, "verify-collide-b")
    # Give the second checkout the first one's runtime identity.
    clone = replace(second, instance_id=first.instance_id, network=first.network)
    worktree._write_instance_record(clone)

    with pytest.raises(worktree.RuntimeNetworkOwnershipError, match="already belongs"):
        worktree.verify_own_runtime_network(
            second.ciu_root,
            expected_instance_id=first.instance_id,
            expected_network=first.network,
        )

    saved = worktree.read_instance_record(second.record_path)
    assert saved.state == "recovery-required"
    assert saved.recovery_status == "runtime-collision"


def test_verify_foreign_owner_label_marks_runtime_collision(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-foreign-label")
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda network: {
            "id": NET_ID, "labels": {worktree._NETWORK_OWNER_LABEL: "someone-else"},
        },
    )

    with pytest.raises(worktree.RuntimeNetworkOwnershipError, match="someone-else"):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )

    saved = worktree.read_instance_record(record.record_path)
    assert saved.state == "recovery-required"
    assert saved.recovery_status == "runtime-collision"


def test_verify_unlabeled_foreign_network_marks_verification_failed(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-unlabeled")
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda network: {"id": NET_ID, "labels": {}},
    )

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipUnprovenError, match="cannot prove ownership"
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )

    saved = worktree.read_instance_record(record.record_path)
    assert saved.state == "recovery-required"
    assert saved.recovery_status == "runtime-verification-failed"


def test_verify_unchanged_record_is_returned_without_rewrite(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-unchanged")
    pinned = worktree._record_with_verified_network_id(record, NET_ID)
    worktree._write_instance_record(pinned)
    before = pinned.record_path.read_bytes()
    monkeypatch.setattr(worktree, "_docker_network_details", _owned_details(pinned))
    monkeypatch.setattr(
        worktree, "_write_instance_record",
        lambda _r: (_ for _ in ()).throw(AssertionError("must not rewrite")),
    )

    verified = worktree.verify_own_runtime_network(
        pinned.ciu_root,
        expected_instance_id=pinned.instance_id,
        expected_network=pinned.network,
    )

    assert verified == worktree.read_instance_record(pinned.record_path)
    assert verified.network_id == NET_ID
    assert pinned.record_path.read_bytes() == before


def test_verify_checkpoints_new_object_id_into_shared_snapshot_too(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-checkpoint")
    monkeypatch.setattr(worktree, "_docker_network_details", _owned_details(record))
    snapshots = []
    real_snapshot = worktree._write_shared_ciu_record_snapshot

    def spy(context, verified, **kw):
        snapshots.append((context, verified))
        return real_snapshot(context, verified, **kw)

    monkeypatch.setattr(worktree, "_write_shared_ciu_record_snapshot", spy)

    verified = worktree.verify_own_runtime_network(
        record.ciu_root,
        expected_instance_id=record.instance_id,
        expected_network=record.network,
    )

    assert verified.network_id == NET_ID
    assert len(snapshots) == 1
    assert snapshots[0][0] is not None and snapshots[0][1] == verified
    shared = worktree._shared_record_for_checkout(record.ciu_root)
    snap = shared.metadata[worktree._CIU_WORKTREE_RECORD_METADATA]
    assert snap["network_id"] == NET_ID
    assert worktree.read_instance_record(record.record_path).network_id == NET_ID


def test_verify_checkpoint_without_shared_context_still_writes_record(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "verify-no-shared-context")
    monkeypatch.setattr(worktree, "_docker_network_details", _owned_details(record))
    real_lookup = worktree._shared_record_for_checkout
    state = {"calls": 0}

    def lookup(path):
        # The first lookup (record-less path) is never reached; the checkpoint
        # lookup is the only one here and reports no shared workspace.
        state["calls"] += 1
        return None

    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lookup)
    monkeypatch.setattr(
        worktree, "_write_shared_ciu_record_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no shared context")),
    )

    verified = worktree.verify_own_runtime_network(
        record.ciu_root,
        expected_instance_id=record.instance_id,
        expected_network=record.network,
    )

    assert state["calls"] == 1
    assert verified.network_id == NET_ID
    assert worktree.read_instance_record(record.record_path).network_id == NET_ID
    assert real_lookup is not lookup


# --------------------------------------------------------------------------
# _verify_unrecorded_nested_root_runtime
# --------------------------------------------------------------------------

@pytest.fixture
def nested_env(tmp_path, monkeypatch, write_instance_facts):
    """A monorepo with CIU root `component`, one managed worktree, and a sibling
    CIU root `other` inside that worktree that has no instance record."""
    git_root = tmp_path / "mono"
    comp = git_root / "component"
    comp.mkdir(parents=True)
    assert _git(["init", "-b", "main"], git_root).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], git_root).returncode == 0
    assert _git(["config", "user.name", "Test"], git_root).returncode == 0
    (comp / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    (git_root / ".gitignore").write_text(
        "ciu.env\nciu.global.instance.toml.j2\nciu.instance.generated.toml\n"
    )
    assert _git(["add", "."], git_root).returncode == 0
    assert _git(["commit", "-m", "init"], git_root).returncode == 0

    def gen(path: Path, **_kw) -> int:
        write_instance_facts(
            path, instance_id="nest1", network="mono-nest1-network",
        )
        return 0

    monkeypatch.setattr(worktree, "_generate_env_in", gen)
    monkeypatch.setattr(worktree, "_docker_network_exists", lambda _n: False)
    record = worktree.create(comp, "nested-parent", profile="core")
    other = record.git_worktree_path / "other"
    other.mkdir()
    write_instance_facts(
        other, instance_id="oth1", network="mono-oth1-network",
    )
    shared = worktree._shared_record_for_checkout(record.ciu_root)
    assert shared is not None
    return type("Env", (), {
        "record": record, "other": other, "shared": shared,
        "metadata": dict(shared.metadata),
    })


def _with_metadata(env, **changes):
    meta = dict(env.metadata)
    for key, value in changes.items():
        if value is ...:
            meta.pop(key, None)
        else:
            meta[key] = value
    return replace(env.shared, metadata=meta)


def _ready_entry(offset="other", iid="oth1", net="mono-oth1-network", state="ready"):
    return {
        "offset": offset, "root_instance_id": iid, "network": net, "state": state,
    }


def _verify_nested(env, shared, **overrides):
    kwargs = {"expected_instance_id": "oth1", "expected_network": "mono-oth1-network"}
    kwargs.update(overrides)
    return worktree._verify_unrecorded_nested_root_runtime(
        env.other, shared, **kwargs
    )


def test_nested_missing_or_non_string_selected_offset(nested_env):
    for bad in (..., 5):
        shared = _with_metadata(nested_env, ciu_root_offset=bad)
        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="no selected root offset",
        ):
            _verify_nested(nested_env, shared)


@pytest.mark.parametrize("offset", ["/abs/root", "../escape", "a/../b"])
def test_nested_unsafe_selected_offset(nested_env, offset):
    shared = _with_metadata(nested_env, ciu_root_offset=offset)
    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="unsafe selected root offset"
    ):
        _verify_nested(nested_env, shared)


def test_nested_selected_root_without_record_must_be_ensured(nested_env):
    shared = _with_metadata(nested_env, ciu_root_offset="other")

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match=r"managed checkout 'nested-parent' at .* has no CIU instance "
        r"record; run `ciu worktree ensure nested-parent` to restore it",
    ):
        _verify_nested(nested_env, shared)


def test_nested_selected_root_with_unlabeled_shared_uses_unknown_name(nested_env):
    shared = _with_metadata(nested_env, ciu_root_offset="other")
    shared = replace(shared, labels={})

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match=r"checkout 'unknown' .*ciu worktree ensure unknown",
    ):
        _verify_nested(nested_env, shared)


def test_nested_requires_committed_root_entries_list(nested_env):
    for bad in (..., "nope", {"offset": "other"}):
        shared = _with_metadata(nested_env, root_entries=bad)
        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="no valid committed root entries",
        ):
            _verify_nested(nested_env, shared)


@pytest.mark.parametrize(
    "entries,count",
    [
        ([], 0),
        (["other", 3, _ready_entry("component")], 0),
        ([_ready_entry(), _ready_entry()], 2),
    ],
)
def test_nested_requires_exactly_one_matching_entry(nested_env, entries, count):
    shared = _with_metadata(nested_env, root_entries=entries)

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match=rf"has {count} committed runtime entries; expected exactly one",
    ):
        _verify_nested(nested_env, shared)


def test_nested_unreadable_generated_identity_is_verification_error(nested_env):
    (nested_env.other / "ciu.instance.generated.toml").write_text(
        "[ciu.instance.generated]\ninstance_id = not-a-toml-value\n",
        encoding="utf-8",
    )
    shared = _with_metadata(nested_env, root_entries=[_ready_entry()])

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="could not read generated runtime identity",
    ):
        _verify_nested(nested_env, shared)


def test_nested_facts_without_identity_are_verification_error(nested_env):
    (nested_env.other / "ciu.instance.generated.toml").unlink()
    shared = _with_metadata(nested_env, root_entries=[_ready_entry()])

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="lacks instance_id or network"
    ):
        _verify_nested(nested_env, shared)


@pytest.mark.parametrize(
    "overrides,entry",
    [
        ({"expected_instance_id": "wrong"}, _ready_entry()),
        ({"expected_instance_id": None}, _ready_entry()),
        ({"expected_network": "wrong-network"}, _ready_entry()),
        ({}, _ready_entry(iid="different")),
        ({}, _ready_entry(net="different-network")),
        ({}, _ready_entry(state="allocating")),
    ],
)
def test_nested_identity_must_match_committed_entry(nested_env, overrides, entry):
    shared = _with_metadata(nested_env, root_entries=[entry])

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="runtime identity does not match its committed shared-workspace entry",
    ):
        _verify_nested(nested_env, shared, **overrides)


def test_nested_requires_parent_instance_snapshot(nested_env):
    for bad in (..., "text", None):
        shared = _with_metadata(
            nested_env, root_entries=[_ready_entry()],
            **{worktree._CIU_WORKTREE_RECORD_METADATA: bad},
        )
        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="no parent instance snapshot for nested-root verification",
        ):
            _verify_nested(nested_env, shared)


def test_nested_unparseable_snapshot_is_wrapped(nested_env):
    shared = _with_metadata(
        nested_env, root_entries=[_ready_entry()],
        **{worktree._CIU_WORKTREE_RECORD_METADATA: {"schema_version": "bogus"}},
    )

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="cannot verify nested-root runtime from the shared snapshot",
    ):
        _verify_nested(nested_env, shared)


def _good_nested_shared(env):
    return _with_metadata(env, root_entries=[_ready_entry()])


def test_nested_missing_network_object_is_accepted(nested_env, monkeypatch):
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _n: None)

    assert _verify_nested(nested_env, _good_nested_shared(nested_env)) is None


def test_nested_runtime_collision_with_sibling_record_is_refused(
    nested_env, monkeypatch
):
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _n: None)
    parent = nested_env.record
    sib = parent.git_worktree_path.parent / "sibling-wt"
    repo = parent.git_worktree_path.parent.parent
    assert _git(
        ["worktree", "add", "-b", "sibling-branch", str(sib), "main"], repo
    ).returncode == 0
    (sib / "other").mkdir()
    # A sibling managed checkout whose `other` root already owns this identity.
    worktree._write_instance_record(replace(
        parent,
        logical_name="sibling",
        display_name="sibling",
        branch="sibling-branch",
        git_worktree_path=sib.resolve(),
        ciu_root_offset=Path("other"),
        instance_id="oth1",
        network="mono-oth1-network",
    ))

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipError,
        match="'oth1' already belongs to logical instance 'sibling'",
    ):
        _verify_nested(nested_env, _good_nested_shared(nested_env))


def test_nested_accepts_network_labeled_for_its_instance_without_recording(
    nested_env, monkeypatch
):
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda network: {
            "id": NET_ID,
            "labels": {worktree._NETWORK_OWNER_LABEL: "oth1"},
        } if network == "mono-oth1-network" else None,
    )
    before = nested_env.record.record_path.read_bytes()

    assert _verify_nested(nested_env, _good_nested_shared(nested_env)) is None
    assert nested_env.record.record_path.read_bytes() == before
    assert not (nested_env.other / worktree.WORKTREE_INSTANCE_RECORD).exists()


def test_nested_refuses_unlabeled_same_name_network(nested_env, monkeypatch):
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda network: {"id": NET_ID, "labels": {}},
    )

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipUnprovenError, match="cannot prove ownership"
    ):
        _verify_nested(nested_env, _good_nested_shared(nested_env))


def test_nested_refuses_network_labeled_for_another_instance(nested_env, monkeypatch):
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda network: {
            "id": NET_ID, "labels": {worktree._NETWORK_OWNER_LABEL: "intruder"},
        },
    )

    with pytest.raises(worktree.RuntimeNetworkOwnershipError, match="intruder"):
        _verify_nested(nested_env, _good_nested_shared(nested_env))


def test_verify_own_network_routes_recordless_nested_root_through_shared_record(
    nested_env, monkeypatch
):
    """End to end through the public entry: an `other` root with no record of
    its own is verified against the shared snapshot and entry."""
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _n: None)
    shared = _good_nested_shared(nested_env)
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _p: shared)

    assert worktree.verify_own_runtime_network(
        nested_env.other,
        expected_instance_id="oth1",
        expected_network="mono-oth1-network",
    ) is None

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError, match="does not match its committed"
    ):
        worktree.verify_own_runtime_network(
            nested_env.other,
            expected_instance_id="nope",
            expected_network="mono-oth1-network",
        )
