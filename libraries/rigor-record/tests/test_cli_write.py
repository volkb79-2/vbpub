"""The ``sync`` and ``update`` verbs end to end against a local bare remote and a fake ``gh`` (commit-keyed store)."""

import copy
import io
import json

import pytest
from cli_extended import CliIdentity

import world
from conftest import FakeGit, run_assemble
from rigor_record import canonical_json
from rigor_record.assemble import Refusal
from rigor_record.cli import _base, _build_cli, _guard_stable
from rigor_record.update import Plan
from world import LANE_DIR, TAG, git, make_world

IDENTITY = CliIdentity(name="RIGOR-RECORD", version="0.0.0", long_name="test", command="rigor-record")
REPLACEMENT = "cgprofile-v2.1.0"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = _build_cli(IDENTITY).run(argv=list(argv), stdout=out, stderr=err, stdin=io.StringIO())
    return code, out.getvalue() + err.getvalue()


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_ASSETS", str(tmp_path / "assets"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return make_world(tmp_path)


def common(w, *extra):
    return ["--project-dir", LANE_DIR, "--repo", str(w.clone), "--lock-file", str(w.root / "lock"), *extra]


def sync(w, *extra):
    return run("sync", "--project", "cgroup-profiler", "--tag", TAG, "--gh", str(w.gh), *common(w), *extra)


def fragments(w, tmp_path, fn=None):
    directory = tmp_path / f"frag{len(list(tmp_path.glob('frag*')))}"
    directory.mkdir()
    for name, data in world.gate_pair(w.commit, w.tree, edit=fn):
        (directory / name).write_bytes(data)
    return directory


def synced(w):
    assert sync(w, "--push")[0] == 0


def record_path(commit):
    return f"{LANE_DIR}/rigor/commits/{commit[:2]}/{commit}.json"


def remote_record(w, commit=None):
    return json.loads(w.remote_files(record_path(commit or w.commit)))


def r2_args(frag, *extra):
    return ["update", "--fragments", str(frag), "--attempts", "2", "--closeout-exit", "1", *extra]


def test_sync_dry_run_then_push_then_noop(w):
    code, out = sync(w)
    assert code == 0 and "dry run" in out and w.remote_log() == ["one"]
    code, out = sync(w, "--push", "--github-repo", "owner/name")
    assert code == 0 and out.startswith("pushed: chore(rigor): ") and remote_record(w)["revision"] == 1
    code, out = sync(w, "--push")
    assert code == 0 and "nothing to do" in out


def test_sync_failures_exit_1(w, monkeypatch):
    sha = w.assets / TAG / f"{TAG}-rigor.json.sha256"
    good = sha.read_text()
    sha.write_text("0" * 64 + "\n")
    code, out = sync(w, "--push")
    assert code == 1 and "sync refused" in out
    sha.write_text(good)
    monkeypatch.setenv("FAKE_GH_FAIL", "HTTP 404")
    code, out = sync(w, "--push")
    assert code == 1 and "HTTP 404" in out
    monkeypatch.delenv("FAKE_GH_FAIL")
    code, out = run("sync", "--project", "cgroup-profiler", "--tag", TAG, "--gh", str(w.gh), *common(w), "--push", "--remote", "nowhere")
    assert code == 1 and "git fetch failed" in out


def test_update_r2_needs_no_tag_and_is_idempotent(w, tmp_path):
    synced(w)
    frag = fragments(w, tmp_path)
    code, out = run(*r2_args(frag, *common(w), "--expect-revision", "7", "--push"))
    assert code == 1 and "expected 7" in out
    code, out = run(*r2_args(frag, *common(w), "--expect-revision", "1", "--push"))
    assert code == 0 and "R2 survivors" in out
    record = remote_record(w)
    assert record["revision"] == 2
    result = next(r for r in record["results"] if r["lane"] == "r2")
    assert result["levels"]["R2"] == "survivors"
    code, out = run(*r2_args(frag, *common(w), "--push"))
    assert code == 0 and "nothing to do" in out
    assert run("check", "--project-dir", str(w.clone_again("v") / LANE_DIR), "--repo", str(w.clone))[0] == 0


def test_update_argument_errors_exit_2(w, tmp_path):
    frag = str(fragments(w, tmp_path))
    base = common(w)
    assert run("update", *base)[0] == 2
    assert run("update", "--fragments", frag, "--reconcile", "--tag", TAG, *base)[0] == 2
    assert run("update", "--reconcile", *base)[0] == 2
    assert run("update", "--fragments", frag, "--attempts", "1", "--closeout-exit", "1", "--tag", TAG, "--commit", w.commit, *base)[0] == 2
    assert run("update", "--fragments", frag, "--qualification", "promotion", *base)[0] == 2
    assert run("update", "--fragments", frag, "--tag", TAG, "--stable", "--attempts", "1", "--closeout-exit", "1", *base)[0] == 2
    assert run("update", "--fragments", frag, *base)[0] == 2
    assert run("update", "--tag", TAG, "--reconcile", "--attribute-to", w.commit, *base)[0] == 2
    assert run("update", "--withdraw", TAG, "--replacement", REPLACEMENT, *base)[0] == 2
    assert run("update", "--withdraw", TAG, "--reason", "unsafe", *base)[0] == 2
    assert run("update", "--waive-r2", "op-1", "--tag", TAG, *base)[0] == 2


def test_update_without_a_record_refuses_and_lane_pins(w, tmp_path):
    frag = fragments(w, tmp_path)
    code, out = run(*r2_args(frag, *common(w), "--expect-revision", "3", "--push"))
    assert code == 1 and "has no record, expected revision 3" in out
    synced(w)
    code, out = run(*r2_args(frag, *common(w), "--lane", "nope", "--push"))
    assert code == 1 and "nope" in out
    code, out = run(*r2_args(frag, *common(w), "--lane", "r2", "--push"))
    assert code == 0, out


def test_update_attribute_to_a_published_commit(w, tmp_path):
    synced(w)
    frag = fragments(w, tmp_path)
    code, out = run(*r2_args(frag, *common(w), "--attribute-to", w.commit, "--push"))
    assert code in (0, 1), out
    code, out = run(*r2_args(frag, *common(w), "--attribute-to", "f" * 40, "--push"))
    assert code == 1 and "not published" in out


def test_update_qualification_with_stable(w, tmp_path):
    synced(w)
    directory = tmp_path / "qual"
    directory.mkdir()
    (directory / "g.json").write_bytes(world.retarget((world.ASSEMBLE / "fragments" / "r0-r1.run-gate.json").read_bytes(), w.commit, w.tree))
    code, out = run("update", "--tag", TAG, "--fragments", str(directory), "--qualification", "promotion", "--stable", *common(w), "--push")
    assert code == 0, out
    promotion = remote_record(w)["qualifications"]["promotion"]
    assert promotion["judged_commit"] == w.commit and promotion["results"][0]["levels"]["R0"] == "pass"
    assert json.loads(w.remote_files(f"{LANE_DIR}/rigor/index.json"))["stable"] == TAG
    code, out = run("update", "--commit", w.commit, "--fragments", str(directory), "--qualification", "other", *common(w))
    assert code == 0 and "dry run" in out


def test_guard_stable_refuses_when_a_newer_release_is_stable():
    older = run_assemble().record
    newer = copy.deepcopy(older)
    newer["commit"] = "9" * 40
    newer["labels"] = [dict(older["labels"][0], tag=REPLACEMENT, version="2.1.0")]
    promotion = {"results": [{"lane": "g", "levels": {"R0": "pass"}}]}
    newer["qualifications"]["promotion"] = promotion
    promoted = copy.deepcopy(older)
    promoted["qualifications"]["promotion"] = promotion
    plan = Plan(older["commit"], {older["commit"]: promoted}, "s", (), "k")
    git_facts = FakeGit(tag=older["commit"])
    guarded = _guard_stable(lambda by_commit, path: plan, TAG, git_facts)
    with pytest.raises(Refusal, match="would not be the stable release"):
        guarded({older["commit"]: older, newer["commit"]: newer}, None)


def waivers_text(ids):
    lines = ['schema = "urn:vbpub:rigor-waivers:1"', ""]
    for candidate in ids:
        lines += ["[[survivor]]", f'candidate_id = "{candidate}"', 'disposition = "equivalent-reviewed"',
                  'reason = "unobservable difference"', 'reviewed_by = "independent-review"', ""]
    return "\n".join(lines)


def test_reconcile_then_nothing_left(w, tmp_path):
    synced(w)
    frag = fragments(w, tmp_path)
    assert run(*r2_args(frag, *common(w), "--push"))[0] == 0
    code, out = run("update", "--tag", TAG, "--reconcile", *common(w), "--push")
    assert code == 0 and "nothing to do" in out
    other = w.clone_again("waivers")
    target = other / LANE_DIR / "rigor" / "waivers.toml"
    target.write_text(waivers_text(f"cand-{i:04d}" for i in range(31)))
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "waive survivors")
    git(other, "push", "-q", "origin", "main")
    code, out = run("update", "--commit", w.commit, "--reconcile", *common(w), "--push")
    assert code == 0 and "reconciled" in out
    result = next(r for r in remote_record(w)["results"] if r["lane"] == "r2")
    assert result["levels"]["R2"] == "pass-with-waivers"
    assert run("check", "--project-dir", str(w.clone_again("v") / LANE_DIR), "--repo", str(w.clone))[0] == 0


def publish_replacement(w):
    """A second, published commit with the same project tree and a newer label, as a hand-made record."""
    git(w.clone, "fetch", "-q", "origin")
    git(w.clone, "merge", "-q", "--ff-only", "origin/main")
    git(w.clone, "commit", "-q", "--allow-empty", "-m", "two")
    git(w.clone, "tag", "-a", "-m", "release", REPLACEMENT)
    git(w.clone, "push", "-q", "origin", "main", REPLACEMENT)
    commit = git(w.clone, "rev-parse", "HEAD")
    replacement = json.loads(w.record_text.replace(w.commit, commit))
    replacement["labels"][0]["tag"], replacement["labels"][0]["version"] = REPLACEMENT, "2.1.0"
    other = w.clone_again("replacement")
    target = other / record_path(commit)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(replacement))
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", f"chore(rigor): {REPLACEMENT} synced\n\nRigor-Record: cgroup-profiler@{commit} rev=1 fragments=aaaaaaaaaaaa")
    git(other, "push", "-q", "origin", "main")
    return commit


def test_withdraw_needs_the_replacement_first(w):
    synced(w)
    code, out = run("update", "--withdraw", TAG, "--reason", "unsafe default", "--replacement", REPLACEMENT, *common(w), "--push")
    assert code == 1 and "publish the fix release first" in out
    commit = publish_replacement(w)
    code, out = run("update", "--withdraw", TAG, "--reason", "unsafe default", "--replacement", REPLACEMENT, *common(w), "--push")
    assert code == 0 and "withdrawn" in out, out
    label = remote_record(w)["labels"][0]
    assert label["state"] == "withdrawn" and label["withdrawn"] == {"reason": "unsafe default", "replacement": REPLACEMENT}
    assert remote_record(w, commit)["labels"][0]["state"] == "current"


def test_base_accepts_a_commit_or_a_label():
    records = {"a" * 40: {"labels": [{"tag": "t-v1.0.0"}]}}
    assert _base(records, None) == (None, None)
    assert _base(records, "b" * 40) == ("b" * 40, None)
    assert _base(records, "t-v1.0.0") == ("a" * 40, "t-v1.0.0")
    with pytest.raises(Refusal, match="neither a commit nor a release tag"):
        _base(records, "nope")


def test_check_commits_option(w):
    synced(w)
    code, out = run("check", "--project-dir", LANE_DIR, "--repo", str(w.bare), "--commits", "main")
    assert code == 0 and "ok" in out
    assert run("check", "--project-dir", LANE_DIR, "--commits", "main", "--waivers", "--release-version", "1.0.0")[0] == 2
    code, out = run("check", "--project-dir", LANE_DIR, "--repo", str(w.bare), "--commits", "nonsense..range")
    assert code == 1 and "git log failed" in out
    other = w.clone_again("hand")
    (other / LANE_DIR / "rigor" / "index.json").write_text("{}")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "hand edit")
    code, out = run("check", "--project-dir", LANE_DIR, "--repo", str(other), "--commits", "origin/main..HEAD")
    assert code == 1 and "prefix" in out
