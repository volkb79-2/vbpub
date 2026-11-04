"""The git procedure of section 5.4 against a local bare remote: worktree, lock, retry, idempotency, convergence."""

import hashlib
import itertools
import threading

import pytest

import world
from rigor_record import RepoGit, check_project
from rigor_record import store
from rigor_record.assemble import Refusal
from rigor_record.render import RenderError
from rigor_record.store import StoreError, check_commits, default_lock, write_through
from rigor_record.sync import plan_sync
from rigor_record.update import fragment_key, plan_qualification, plan_r2, sha12
from world import LANE_DIR, TAG, git, make_world

FAST = dict(sleep=lambda _s: None, jitter=lambda attempt: 0.0)


@pytest.fixture
def w(tmp_path):
    return make_world(tmp_path)


def digest(w):
    return hashlib.sha256(w.record_text.encode()).hexdigest()


def sync_fn(w):
    return lambda by_commit, _project: plan_sync(record=w.record, asset_sha256=digest(w), records=by_commit)


def do_sync(w, clone=None, project_dir=LANE_DIR, **kw):
    kw.setdefault("lock", w.root / "lock-a")
    kw.setdefault("push", True)
    return write_through(repo=clone or w.clone, project_dir=project_dir, plan_fn=sync_fn(w), idem=sha12(digest(w)), **FAST, **kw)


def record_path(w, project_dir=LANE_DIR):
    prefix = "" if project_dir == "." else f"{project_dir}/"
    return f"{prefix}rigor/commits/{w.commit[:2]}/{w.commit}.json"


def r2_fn(clone, w, fragments=None):
    fragments = fragments or world.gate_pair(w.commit, w.tree)

    def plan(by_commit, project):
        return plan_r2(
            records=by_commit, fragments=fragments,
            attempts=1, closeout_exit=1, base=None, project_dir=LANE_DIR, git=RepoGit(clone),
        )
    key = fragment_key(hashlib.sha256(d).hexdigest() for _n, d in fragments)
    return plan, key


def verified(path, clone):
    return check_project(path, git=RepoGit(clone))


def advance(w, name):
    """Another writer lands a commit on the remote's main."""
    other = w.clone_again(name)
    (other / f"{name}.txt").write_text(name)
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", name)
    git(other, "push", "-q", "origin", "main")


def test_pushes_a_commit_with_prefix_trailer_and_views_and_cleans_up(w):
    before = git(w.clone, "status", "--porcelain")
    outcome = do_sync(w)
    assert outcome.status == "pushed" and outcome.attempts == 1
    assert w.remote_log()[0] == f"chore(rigor): {TAG} release synced"
    message = git(w.bare, "log", "-1", "--format=%B", "main")
    assert message.startswith("chore(rigor): ")
    assert f"Rigor-Record: cgroup-profiler@{w.commit} rev=1 fragments={sha12(digest(w))}" in message
    assert w.remote_files(record_path(w)) == w.record_text
    assert "cgroup-profiler" in w.remote_files(f"{LANE_DIR}/rigor/index.json")
    assert TAG in w.remote_files(f"{LANE_DIR}/RIGOR.md")
    assert git(w.clone, "worktree", "list").count("\n") == 0
    assert git(w.clone, "status", "--porcelain") == before
    assert check_commits(w.bare, LANE_DIR, "main") == []
    verify = w.clone_again("verify")
    assert verified(verify / LANE_DIR, verify) == []


def test_dry_run_commits_only_in_the_throwaway_worktree(w):
    outcome = do_sync(w, push=False)
    assert outcome.status == "dry-run" and outcome.commit
    assert w.remote_log() == ["one"]
    assert git(w.bare, "cat-file", "-t", outcome.commit, check=False) == ""
    assert git(w.clone, "worktree", "list").count("\n") == 0


def test_idempotent_by_trailer_and_noop_plans(w):
    do_sync(w)
    assert do_sync(w).status == "noop"
    plan, idem = r2_fn(w.clone, w)
    first = write_through(repo=w.clone, project_dir=LANE_DIR, plan_fn=plan, idem=idem, push=True, lock=w.root / "lock-a", **FAST)
    assert first.status == "pushed"
    count = len(w.remote_log())
    again = write_through(repo=w.clone, project_dir=LANE_DIR, plan_fn=plan, idem=idem, push=True, lock=w.root / "lock-a", **FAST)
    assert again.status == "noop" and len(w.remote_log()) == count
    nothing = write_through(repo=w.clone, project_dir=LANE_DIR, plan_fn=lambda *_a: None, push=True, lock=w.root / "lock-a", **FAST)
    assert nothing.status == "noop"


def test_rejected_push_retries_from_a_fresh_tip_and_regenerates_views(w):
    seen, sleeps = [], []

    def hook(attempt):
        seen.append(attempt)
        if attempt == 1:
            advance(w, "other")

    outcome = write_through(
        repo=w.clone, project_dir=LANE_DIR, plan_fn=sync_fn(w), push=True, lock=w.root / "lock-a", before_push=hook,
        sleep=sleeps.append, jitter=lambda attempt: attempt * 0.5,
    )
    assert outcome.status == "pushed" and outcome.attempts == 2 and seen == [1, 2] and sleeps == [0.5]
    assert w.remote_log()[:2] == [f"chore(rigor): {TAG} release synced", "other"]
    verify = w.clone_again("verify")
    assert verified(verify / LANE_DIR, verify) == []


def test_gives_up_after_the_bound_and_publishes_nothing(w):
    counter = itertools.count()
    sleeps = []
    with pytest.raises(StoreError, match="rejected 3 times"):
        write_through(
            repo=w.clone, project_dir=LANE_DIR, plan_fn=sync_fn(w), push=True, lock=w.root / "lock-a", max_attempts=3,
            before_push=lambda _a: advance(w, f"churn{next(counter)}"), sleep=sleeps.append, jitter=lambda a: 0.0,
        )
    assert len(sleeps) == 3
    assert not any(line.startswith("chore(rigor)") for line in w.remote_log())


def test_a_push_failure_that_is_not_a_race_is_reported(w):
    hook = w.bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho no >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(StoreError, match="did not move"):
        do_sync(w)
    assert w.remote_log() == ["one"]


def test_git_failures_surface_as_store_errors(w):
    with pytest.raises(StoreError, match="git fetch failed"):
        do_sync(w, remote="nowhere")
    with pytest.raises(StoreError, match="git fetch failed"):
        write_through(repo=w.root / "missing", project_dir=LANE_DIR, plan_fn=sync_fn(w), push=True, lock=w.root / "l2", **FAST)


def test_an_invalid_committed_tree_refuses(w):
    other = w.clone_again("bad")
    commits = other / LANE_DIR / "rigor" / "commits"
    commits.mkdir(parents=True)
    (commits / "junk.txt").write_text("x")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "junk")
    git(other, "push", "-q", "origin", "main")
    with pytest.raises(Refusal) as info:
        do_sync(w)
    assert "invalid" in info.value.problems[0]


def test_a_plan_that_refuses_leaves_the_remote_alone(w):
    def refuse(_by_commit, _project):
        raise Refusal(["no"])
    with pytest.raises(Refusal):
        write_through(repo=w.clone, project_dir=LANE_DIR, plan_fn=refuse, push=True, lock=w.root / "lock-a", **FAST)
    assert w.remote_log() == ["one"]
    assert git(w.clone, "worktree", "list").count("\n") == 0


def test_view_rendering_failure_is_a_refusal(w, monkeypatch):
    def boom(*_a, **_k):
        raise RenderError(["too big"])
    monkeypatch.setattr(store, "render_views", boom)
    with pytest.raises(Refusal, match="too big"):
        do_sync(w)


def test_project_at_the_repository_root(tmp_path):
    root = make_world(tmp_path, ".")
    outcome = do_sync(root, project_dir=".")
    assert outcome.status == "pushed"
    assert root.remote_files(record_path(root, ".")) == root.record_text
    assert TAG in root.remote_files("RIGOR.md")
    assert do_sync(root, project_dir=".").status == "noop"
    assert check_commits(root.bare, ".", "main") == []


def test_default_lock_location(w, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_lock(w.clone) == tmp_path / "state" / "rigor-record" / "cockpit.lock"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_lock(w.clone) == tmp_path / "home" / ".local" / "state" / "rigor-record" / "cockpit.lock"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_through(repo=w.clone, project_dir=LANE_DIR, plan_fn=sync_fn(w), push=True, **FAST)
    assert (tmp_path / "state" / "rigor-record" / "cockpit.lock").exists()


def test_two_writers_with_distinct_locks_converge(w):
    do_sync(w)
    second = w.clone_again("second")
    barrier = threading.Barrier(2)

    def gate(attempt):
        if attempt == 1:
            barrier.wait(timeout=30)

    gate_fragment = (world.ASSEMBLE / "fragments" / "r0-r1.run-gate.json")
    promotion = [("g.json", world.retarget(gate_fragment.read_bytes(), w.commit, w.tree))]
    results, errors = {}, []

    def writer_a():
        try:
            plan, idem = r2_fn(w.clone, w)
            results["r2"] = write_through(repo=w.clone, project_dir=LANE_DIR, plan_fn=plan, idem=idem, push=True, lock=w.root / "la", before_push=gate, **FAST)
        except Exception as exc:  # pragma: no cover - reported through the assertion below
            errors.append(exc)

    def writer_b():
        try:
            def plan(by_commit, _project):
                return plan_qualification(ref=w.commit, name="promotion", records=by_commit, fragments=promotion, project_dir=LANE_DIR, git=RepoGit(second))
            results["qual"] = write_through(repo=second, project_dir=LANE_DIR, plan_fn=plan, push=True, lock=w.root / "lb", before_push=gate, **FAST)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer_a), threading.Thread(target=writer_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == []
    assert sorted(o.attempts for o in results.values()) == [1, 2]
    verify = w.clone_again("verify")
    assert verified(verify / LANE_DIR, verify) == []
    final = json_record(verify, w)
    assert [r["levels"]["R2"] for r in final["results"] if "R2" in r.get("levels", {})] == ["survivors"]
    assert "promotion" in final["qualifications"]
    assert final["revision"] == 3
    assert check_commits(w.bare, LANE_DIR, "main") == []


def json_record(clone, w):
    import json
    return json.loads((clone / record_path(w)).read_text())


def test_check_commits_flags_hand_made_commits(w):
    other = w.clone_again("hand")
    (other / LANE_DIR / "rigor" / "commits" / "xx").mkdir(parents=True)
    (other / LANE_DIR / "rigor" / "commits" / "xx" / "x.json").write_text("{}")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "hand edit")
    problems = check_commits(other, LANE_DIR, "origin/main..HEAD")
    assert len(problems) == 2 and "prefix" in problems[0] and "trailer" in problems[1]
    (other / LANE_DIR / "rigor" / "commits" / "xx" / "x.json").write_text("{ }")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "chore(rigor): x pass\n\nRigor-Record: p@t rev=1 fragments=abcdefabcdef")
    assert len(check_commits(other, LANE_DIR, "HEAD~1..HEAD")) == 0
    with pytest.raises(StoreError):
        check_commits(other, LANE_DIR, "nonsense..range")
