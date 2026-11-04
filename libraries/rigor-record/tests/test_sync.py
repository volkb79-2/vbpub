"""``sync``: the release asset download behind its seam, the sha256 check and the repository bindings."""

import copy
import hashlib
import json

import pytest

from conftest import COMMIT, GOLDEN, FakeGit, run_assemble
from rigor_record import canonical_json, validate_record
from rigor_record.assemble import Refusal
from rigor_record.gitfacts import GitError
from rigor_record.sync import GhReleaseSource, SyncError, asset_names, fetch_record, plan_sync
from rigor_record.update import sha12
from world import TAG, make_world

PROJECT = "cgroup-profiler"
DIR = "cgroup-profiler"


class Memory:
    """A fake release: asset name -> bytes."""

    def __init__(self, files):
        self.files = files

    def fetch(self, tag, name):
        if name not in self.files:
            raise SyncError(f"release {tag} has no asset {name}")
        return self.files[name]


def release_files(record=None, digest=None, data=None, tag=TAG):
    data = data if data is not None else canonical_json(record or run_assemble().record).encode()
    name, digest_name = asset_names(tag)
    token = digest if digest is not None else hashlib.sha256(data).hexdigest()
    return {name: data, digest_name: f"{token}  {name}\n".encode()}


def fetch(files, **kw):
    args = dict(tag=TAG, project=PROJECT, project_dir=DIR, source=Memory(files), git=FakeGit())
    args.update(kw)
    return fetch_record(**args)


def refused(fragment, files, **kw):
    with pytest.raises(Refusal) as info:
        fetch(files, **kw)
    assert any(fragment in p for p in info.value.problems), info.value.problems


def test_fetch_checks_digest_and_returns_record():
    files = release_files()
    record, digest = fetch(files)
    assert record["labels"][0]["tag"] == TAG and digest == hashlib.sha256(files[asset_names(TAG)[0]]).hexdigest()
    assert asset_names(TAG) == (f"{TAG}-rigor.json", f"{TAG}-rigor.json.sha256")


def test_fetch_refusals():
    refused("does not start with a sha256", release_files(digest="zz"))
    files = release_files()
    files[asset_names(TAG)[1]] = b""
    refused("does not start with a sha256", files)
    refused("says " + "0" * 64, release_files(digest="0" * 64))
    big = b"x" * 70000
    refused("limit", release_files(data=big))
    refused("not valid JSON", release_files(data=b"{not json"))
    refused(f"{TAG}-rigor.json: ", release_files(data=b"{}"))
    refused("not exactly cgprofile-v9.0.0", release_files(tag="cgprofile-v9.0.0"), tag="cgprofile-v9.0.0")
    refused("not other", release_files(), project="other")
    revised = copy.deepcopy(run_assemble().record)
    revised["revision"] = 2
    refused("revision 2", release_files(record=revised))
    refused("moved tag", release_files(), git=FakeGit(tag="c" * 40))
    refused("the commit has " + "d" * 40, release_files(), git=FakeGit(default="d" * 40))
    relabelled = copy.deepcopy(run_assemble().record)
    relabelled["labels"][0].update(tag="cgprofile-v9.9.9", version="9.9.9")
    refused("is the record of ['cgprofile-v9.9.9'], not exactly", release_files(record=relabelled))
    refused("no such commit", release_files(), git=FakeGit(missing=[COMMIT]))


def test_transport_failure_is_a_sync_error():
    with pytest.raises(SyncError, match="no asset"):
        fetch({})


def test_gh_release_source(tmp_path, monkeypatch):
    w = make_world(tmp_path)
    monkeypatch.setenv("FAKE_GH_ASSETS", str(w.assets))
    source = GhReleaseSource(None, str(w.gh))
    assert source.fetch(TAG, f"{TAG}-rigor.json") == w.record_text.encode()
    assert GhReleaseSource("owner/name", str(w.gh)).fetch(TAG, f"{TAG}-rigor.json.sha256").endswith(b"\n")
    with pytest.raises(SyncError, match="no such asset"):
        source.fetch(TAG, "absent.json")
    monkeypatch.setenv("FAKE_GH_FAIL", "")
    monkeypatch.setenv("FAKE_GH_FAIL", "HTTP 401")
    with pytest.raises(SyncError, match="HTTP 401"):
        source.fetch(TAG, f"{TAG}-rigor.json")
    monkeypatch.delenv("FAKE_GH_FAIL")
    monkeypatch.setenv("FAKE_GH_NOFILE", f"{TAG}-rigor.json")
    with pytest.raises(SyncError, match="has no asset"):
        source.fetch(TAG, f"{TAG}-rigor.json")
    monkeypatch.delenv("FAKE_GH_NOFILE")
    silent = tmp_path / "silent-gh"
    silent.write_text("#!/bin/sh\nexit 3\n")
    silent.chmod(0o755)
    with pytest.raises(SyncError, match="no message"):
        GhReleaseSource(None, str(silent)).fetch(TAG, "x")


def test_plan_sync_is_idempotent():
    record = run_assemble().record
    digest = hashlib.sha256(b"asset").hexdigest()
    plan = plan_sync(record=record, asset_sha256=digest, records={})
    assert plan.commit == COMMIT and plan.changed == {COMMIT: record} and plan.key == sha12(digest)
    assert plan.subject == f"chore(rigor): {TAG} release synced"
    assert plan.trailers == (f"Rigor-Record: {PROJECT}@{COMMIT} rev=1 fragments={sha12(digest)}",)
    assert plan_sync(record=record, asset_sha256=digest, records={COMMIT: record}) is None


def golden_record(name):
    return json.loads((GOLDEN / "records" / f"{name}.json").read_text())


def test_plan_sync_folds_the_release_into_an_existing_commit_record():
    record = run_assemble().record
    existing = golden_record("r2-unlabelled")
    plan = plan_sync(record=record, asset_sha256="1" * 64, records={COMMIT: existing})
    folded = plan.changed[COMMIT]
    assert folded["revision"] == existing["revision"] + 1 and [lbl["tag"] for lbl in folded["labels"]] == [TAG]
    assert [r["lane"] for r in folded["results"]] == ["r0-r1", "assay-r1", "assay-r3", "assay-r2", "release"]
    assert folded["inventory"]["r2_lanes"] == ["assay-r2"]
    assert validate_record(folded) == []
    assert folded["toolsets"][folded["results"][3]["toolset"]] == existing["toolsets"][existing["results"][0]["toolset"]]


def test_fold_remaps_machine_classes_qualifications_and_perf():
    record = run_assemble().record
    existing = golden_record("r2-unlabelled")
    perf = dict(record["perf"][0], machine_classes={"m1": 2})
    existing["machine_classes"] = [dict(record["machine_classes"][0], cpu_model="Other CPU model")]
    existing["perf"] = [perf]
    existing["qualifications"] = {"stage": {"judged_commit": COMMIT, "results": [record["results"][0]]}}
    folded = plan_sync(record=record, asset_sha256="1" * 64, records={COMMIT: existing}).changed[COMMIT]
    assert [m["cpu_model"] for m in folded["machine_classes"]][-1] == "Other CPU model"
    assert folded["perf"][-1]["machine_classes"] == {folded["machine_classes"][-1]["id"]: 2}
    assert folded["qualifications"]["stage"]["results"][0]["toolset"] in folded["toolsets"]
    # a class already in the release keeps the release's id
    existing["machine_classes"][0]["cpu_model"] = record["machine_classes"][0]["cpu_model"]
    again = plan_sync(record=record, asset_sha256="1" * 64, records={COMMIT: existing}).changed[COMMIT]
    assert len(again["machine_classes"]) == len(record["machine_classes"])


def test_fold_refuses_a_lane_result_present_in_both():
    record = run_assemble().record
    existing = golden_record("r2-pass")
    existing["labels"] = []
    with pytest.raises(Refusal, match="has a result in the release and in the commit's earlier record"):
        plan_sync(record=record, asset_sha256="1" * 64, records={COMMIT: existing})


def test_fold_refuses_an_invalid_result(monkeypatch):
    from rigor_record import sync

    monkeypatch.setattr(sync, "validate_record", lambda _record, **_kw: ["bad"])
    existing = golden_record("r2-unlabelled")
    with pytest.raises(Refusal, match="folded record: bad"):
        plan_sync(record=run_assemble().record, asset_sha256="1" * 64, records={COMMIT: existing})


def test_fold_keeps_the_labels_the_commit_already_carries():
    record = run_assemble().record
    existing = golden_record("r2-unlabelled")
    older = dict(record["labels"][0], tag="cgprofile-v1.9.0", version="1.9.0")
    existing["labels"] = [older]
    folded = plan_sync(record=record, asset_sha256="1" * 64, records={COMMIT: existing}).changed[COMMIT]
    assert [lbl["tag"] for lbl in folded["labels"]] == [TAG, "cgprofile-v1.9.0"]
    assert validate_record(folded) == []


def test_the_folded_record_runs_the_privacy_lint_with_the_operators_denylist(monkeypatch):
    from rigor_record import sync

    seen = []
    monkeypatch.setattr(sync, "validate_record", lambda _record, **kw: seen.append(kw) or [])
    plan_sync(
        record=run_assemble().record, asset_sha256="1" * 64, records={COMMIT: golden_record("r2-unlabelled")},
        hosts=["h1"], users=["u1"],
    )
    assert seen == [{"hosts": ("h1",), "users": ("u1",)}]
