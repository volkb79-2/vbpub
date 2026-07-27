"""DR8 -- bounded reviewer repair (routing-model-redesign.md D-R8, refined).

An already-engaged independent reviewer MAY fix a small test/fixture-side
gap it finds (missed branch, absent fixture, missing assertion) and commit
that on the task's own branch, instead of forcing a full carve/implement/
review round-trip -- stamped `VERDICT: APPROVED (repaired)`. The permission
is bounded to `reviewer_repair_paths` and -- the actual mechanism this file
tests -- MECHANICALLY re-checked AFTER the fact by daemon.py's
EmitAttemptExit REVIEW_INDEPENDENT consumption: an out-of-bounds repair is
reverted on the branch and the outcome is forced to REJECTED/fixable, even
though the committed REVIEW.md still literally reads APPROVED.

adapters.py's dispatch-side prompt shaping (`repair_allowed`) is covered in
test_adapters.py; this file covers config.py's two new Policy fields and the
daemon.py post-hoc enforcement only.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from nyxloom import adapters, daemon, lint, notify, paths, reconcile, render, storage, wrapper
from nyxloom.config import Policy, ProjectConfig
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt, ReceiptResult,
    Role, Route, TaskState, TaskStateFile, utc_now,
)


# ---------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py -- same convention
# test_daemon.py documents; local COPY of its `patch_siblings` fixture, not
# an import, so this file has no hidden coupling to test_daemon.py's own
# internals). Not every test here needs it (EmitAttemptExit never calls
# build_dispatch/build_resume/launch_detached), but notify_event/lint are
# real side effects run_pass touches regardless -- stub them out the same
# way every other daemon.py test file does.

@pytest.fixture()
def patch_siblings(monkeypatch):
    monkeypatch.setattr(adapters, "probe", lambda route: (True, "ok"))
    monkeypatch.setattr(wrapper, "launch_detached", lambda spec: 4242)
    monkeypatch.setattr(render, "render_after_event", lambda registry: paths.www_dir())
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})


def _scripted(monkeypatch, sequence):
    seq = list(sequence)

    def fake(inp):
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          check=True, capture_output=True, text=True)


def _make_feature_branch(root, task_id, filename, content):
    _git(root, "branch", f"feat/{task_id}")
    _git(root, "checkout", f"feat/{task_id}")
    (root / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "add", "-A"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "commit",
                    "-qm", f"feat {task_id}"], check=True, capture_output=True)
    _git(root, "checkout", "main")


def _branch_head(root, task_id):
    return _git(root, "rev-parse", f"feat/{task_id}").stdout.strip()


def _commit_files_on_branch(root, task_id, files: dict, message: str):
    """Commit `files` (relpath -> content, relative to `root`) onto the
    ALREADY-EXISTING feat/<task_id> -- simulates the reviewer's own repair
    commit landing after the pre-review baseline."""
    branch = f"feat/{task_id}"
    _git(root, "checkout", branch)
    for relpath, content in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "add", "-A"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "commit",
                    "-qm", message], check=True, capture_output=True)
    _git(root, "checkout", "main")


def _commit_review_report(root, task_id, reports_dir, content):
    branch = f"feat/{task_id}"
    _git(root, "checkout", branch)
    report_dir = root / reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{task_id}-REVIEW.md").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "add", "-A"],
                    check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root), "commit",
                    "-qm", f"review {task_id}"], check=True, capture_output=True)
    _git(root, "checkout", "main")


_NO_BASELINE = object()  # sentinel: do not record pre_review_sha at all


def _seed_review_attempt(project, task_id, attempt_id, pre_review_sha, wave_id="wave-1"):
    """Mirrors test_daemon.py's own local `_seed_review_attempt`, PLUS the
    DR8 ATTEMPT_CREATED event LaunchReview now records per member with
    `pre_review_sha` in its payload (`_pre_review_sha` reads it back). Pass
    `pre_review_sha=_NO_BASELINE` to simulate an UNRESOLVED baseline (the
    real LaunchReview path when `_task_branch_head_sha` returns None)."""
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.AWAITING_REVIEW, since=utc_now(), handoff_path=None, wave_id=wave_id,
    )
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.REVIEW_INDEPENDENT, state=AttemptState.RUNNING,
                       route=route, started=utc_now(), wave_id=wave_id)
    tsf.attempts.append(attempt)
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    payload = {"attempt": attempt.to_dict()}
    if pre_review_sha is not _NO_BASELINE:
        payload["pre_review_sha"] = pre_review_sha
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.ATTEMPT_CREATED, payload=payload,
        task_id=task_id, attempt_id=attempt_id, wave_id=wave_id,
    )
    return storage.load_state(project, task_id)


def _write_receipt(project, attempt_id, result, exit_code=0):
    d = paths.attempt_dir(project, attempt_id)
    d.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(result=result, exit_code=exit_code)
    (d / "receipt.json").write_text(json.dumps(receipt.to_dict()), encoding="utf-8")


def _reload_policy_off(cfg: ProjectConfig, key: str, value: str) -> ProjectConfig:
    """Mirrors test_daemon.py's own `[policy]` TOML-override pattern
    (test_gate_diagnosis_state_threshold_two_absorbs_first_and_survives_review)
    -- append a `[policy]` key to the project's own project.toml and reload."""
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    ptoml.write_text(
        ptoml.read_text(encoding="utf-8").replace(
            "[policy]\n", f"[policy]\n{key} = {value}\n", 1),
        encoding="utf-8")
    return ProjectConfig.load(cfg.root)


# ---------------------------------------------------------------------------
# config.py: the two new Policy fields.

def test_policy_reviewer_repair_defaults_on():
    """DR8 (operator decision 2026-07-27): unlike its `*_interval_days`/
    `mutation_gate` neighbours (default OFF -- enabling them spends budget
    the operator never asked for), reviewer_repair spends nothing extra (a
    permission inside an already-dispatched review session) so it defaults
    ON, same precedent as `pre_merge_gate: bool = True`."""
    assert Policy().reviewer_repair is True


def test_policy_reviewer_repair_paths_default_globs():
    assert Policy().reviewer_repair_paths == [
        "*/tests/*", "tests/*", "*/conftest.py", "*/test_*.py",
    ]


def test_project_config_loads_reviewer_repair_from_toml(tmp_state, sample_project):
    """Round-trips through the SAME `[policy]` TOML-override + reload path
    the daemon-level tests below use to flip it off."""
    cfg2 = _reload_policy_off(sample_project, "reviewer_repair", "false")
    assert cfg2.policy.reviewer_repair is False


# ---------------------------------------------------------------------------
# Oracle 9: glob matching -- pure, no git needed.

def test_reviewer_repair_glob_matching_accepts_tests_rejects_src(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    offending = d._reviewer_repair_offending_paths(
        sample_project,
        ["nyxloom/tests/test_x.py", "nyxloom/tests/conftest.py", "nyxloom/src/nyxloom/daemon.py"],
    )
    assert offending == ["nyxloom/src/nyxloom/daemon.py"]


def test_reviewer_repair_glob_matching_all_in_bounds_is_empty(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    offending = d._reviewer_repair_offending_paths(
        sample_project, ["tests/test_a.py", "nyxloom/tests/conftest.py"])
    assert offending == []


# ---------------------------------------------------------------------------
# Oracle 4: in-bounds repair passes -- exactly like an ordinary APPROVED, no
# revert, no invalidation record.

def test_in_bounds_repair_routes_as_approved_no_revert(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    cfg = sample_project
    task_id, attempt_id = "t-repair-ok", "att-repair-ok"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    baseline = _branch_head(cfg.root, task_id)
    _commit_files_on_branch(
        cfg.root, task_id, {"tests/test_thing.py": "def test_x():\n    assert True\n"},
        "reviewer repair: add missing assertion")
    repaired_head = _branch_head(cfg.root, task_id)
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nFindings: missing test coverage, fixed in-session.\n\n"
        "VERDICT: APPROVED (repaired)\nPatched: added tests/test_thing.py::test_x\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id, pre_review_sha=baseline)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.MERGE_READY
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "approved"
    assert "repair_invalidated" not in recorded.payload
    assert "reject_class" not in recorded.payload
    # no revert: the repair commit is still the branch tip (the review-report
    # commit lands on top of it, so the tip has moved on, but nothing was
    # reverted -- the repair commit itself is still an ancestor).
    merge_base = _git(cfg.root, "merge-base", "--is-ancestor", repaired_head,
                      f"feat/{task_id}")
    assert merge_base.returncode == 0
    assert subprocess.run(["git", "-C", str(cfg.root), "log", "--oneline",
                           f"feat/{task_id}"], capture_output=True, text=True
                          ).stdout.count("Revert") == 0


# ---------------------------------------------------------------------------
# Oracle 5 (the core negative): out-of-bounds repair is invalidated.

def test_out_of_bounds_repair_is_reverted_and_forced_rejected_fixable(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """The reviewer's diff also touches a production src/ file -- even
    though the committed REVIEW.md literally reads `VERDICT: APPROVED
    (repaired)`, the daemon must: (a) revert the reviewer's commits on the
    branch, (b) force the outcome to REJECTED, (c) reject class 'fixable',
    (d) record a durable invalidation naming the offending path. Uses a
    REAL git repo (sample_project) so the revert is genuinely exercised."""
    cfg = sample_project
    task_id, attempt_id = "t-repair-oob", "att-repair-oob"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    baseline = _branch_head(cfg.root, task_id)
    _commit_files_on_branch(
        cfg.root, task_id, {
            "tests/test_thing.py": "def test_x():\n    assert True\n",
            "src/thing.py": "# a production 'fix' the reviewer had no business making\n",
        },
        "reviewer repair: touched production source too")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED (repaired)\nPatched: tests/test_thing.py "
        "and src/thing.py\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id, pre_review_sha=baseline)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    # (b) outcome REJECTED, not APPROVED -- even though the committed
    # REVIEW.md still literally says APPROVED.
    assert storage.load_state("demo", task_id).state is TaskState.REVIEW_REJECTED

    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    # (c) reject class fixable.
    assert recorded.payload["reject_class"] == "fixable"
    # (d) durable invalidation record naming the offending path.
    assert recorded.payload["repair_invalidated"] is True
    assert recorded.payload["reason"] == "out_of_scope"
    assert recorded.payload["offending_paths"] == ["src/thing.py"]
    assert recorded.payload["reverted"] is True

    # (a) the reviewer's commits are reverted on the branch -- the
    # production file must no longer be present at the branch tip.
    show = subprocess.run(
        ["git", "-C", str(cfg.root), "show", f"feat/{task_id}:src/thing.py"],
        capture_output=True, text=True)
    assert show.returncode != 0, "production file must have been reverted away"


def test_out_of_bounds_repair_revert_failure_still_forces_rejected(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """A failed revert (git error) must NEVER silently downgrade back to
    trusting the unverified repair -- the outcome is still forced to
    REJECTED/fixable, with `reverted: False` recorded."""
    cfg = sample_project
    task_id, attempt_id = "t-repair-oob-revert-fail", "att-repair-oob-revert-fail"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    baseline = _branch_head(cfg.root, task_id)
    _commit_files_on_branch(
        cfg.root, task_id, {"src/thing.py": "# out of bounds\n"}, "reviewer repair")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED (repaired)\nPatched: src/thing.py\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id, pre_review_sha=baseline)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    monkeypatch.setattr(d, "_revert_reviewer_repair", lambda *a, **k: False)
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.REVIEW_REJECTED
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["reject_class"] == "fixable"
    assert recorded.payload["reverted"] is False


# ---------------------------------------------------------------------------
# Oracle 6: missing baseline invalidates -- never silently trusted.

def test_missing_baseline_invalidates_without_reverting(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    cfg = sample_project
    task_id, attempt_id = "t-repair-no-baseline", "att-repair-no-baseline"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    _commit_files_on_branch(
        cfg.root, task_id, {"tests/test_thing.py": "def test_x():\n    assert True\n"},
        "reviewer repair: in-bounds, but baseline unresolved")
    head_before = _branch_head(cfg.root, task_id)
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED (repaired)\nPatched: tests/test_thing.py\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id, pre_review_sha=_NO_BASELINE)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.REVIEW_REJECTED
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "rejected"
    assert recorded.payload["reject_class"] == "fixable"
    assert recorded.payload["repair_invalidated"] is True
    assert recorded.payload["reason"] == "missing_baseline"
    assert "offending_paths" not in recorded.payload  # no diff was ever computed

    # no revert attempted: the repair commit made before the review-report
    # commit is still on the branch, untouched.
    head_after = subprocess.run(
        ["git", "-C", str(cfg.root), "log", "--oneline", f"feat/{task_id}"],
        capture_output=True, text=True).stdout
    assert "Revert" not in head_after
    # sanity: head_before really was an ancestor commit (repair landed)
    assert _git(cfg.root, "merge-base", "--is-ancestor", head_before,
               f"feat/{task_id}").returncode == 0


# ---------------------------------------------------------------------------
# Oracle 7: empty diff (mis-stamped verdict) is not a violation.

def test_repaired_marker_with_no_reviewer_commits_is_ordinary_approved(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    """The reviewer stamped `(repaired)` but committed NOTHING beyond its
    own required REVIEW.md (which is excluded from the touched-paths check
    -- see _reviewer_repair_touched_paths) -- a mis-stamped verdict, not a
    violation: ordinary APPROVED, no revert."""
    cfg = sample_project
    task_id, attempt_id = "t-repair-empty-diff", "att-repair-empty-diff"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    baseline = _branch_head(cfg.root, task_id)
    # deliberately NO repair commit -- only the review report itself.
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED (repaired)\nPatched: nothing, actually.\n",
    )
    _seed_review_attempt("demo", task_id, attempt_id, pre_review_sha=baseline)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    assert storage.load_state("demo", task_id).state is TaskState.MERGE_READY
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "approved"
    assert "repair_invalidated" not in recorded.payload


# ---------------------------------------------------------------------------
# Oracle 8: policy off -- no permission dispatched (see test_adapters.py),
# AND the post-hoc path is inert: a `(repaired)` verdict is handled by the
# ordinary pre-existing logic (the plain VERDICT: APPROVED regex match).

def test_policy_off_repaired_verdict_handled_by_ordinary_logic_no_enforcement(
    tmp_state, sample_project, patch_siblings, monkeypatch
):
    cfg = sample_project
    task_id, attempt_id = "t-repair-policy-off", "att-repair-policy-off"
    _make_feature_branch(cfg.root, task_id, f"{task_id}.py", f"# {task_id}\n")
    baseline = _branch_head(cfg.root, task_id)
    # Even an OUT-OF-BOUNDS repair (production src/ touched) must be
    # ignored entirely when the policy is off -- the whole enforcement path
    # is inert, not merely permissive.
    _commit_files_on_branch(
        cfg.root, task_id, {"src/thing.py": "# would be out of bounds if enforced\n"},
        "would-be out-of-bounds repair, but policy is off")
    _commit_review_report(
        cfg.root, task_id, cfg.reports_dir,
        "# Review\n\nVERDICT: APPROVED (repaired)\nPatched: src/thing.py\n",
    )
    # DR8's `[policy]` override MUST land LAST, after every branch/checkout/
    # `git add -A` above -- those helpers stage-and-commit the WHOLE working
    # tree onto feat/<task_id> (including any uncommitted project.toml edit
    # made earlier), and the trailing `git checkout main` then restores
    # main's own (still-unmodified) committed project.toml, silently
    # swallowing an earlier override. Mutating here, with no further git
    # checkout after it, is what makes it actually reach `run_pass`.
    cfg_off = _reload_policy_off(cfg, "reviewer_repair", "false")
    _seed_review_attempt("demo", task_id, attempt_id, pre_review_sha=baseline)
    _write_receipt("demo", attempt_id, ReceiptResult.DONE, exit_code=0)
    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])

    d = daemon.Daemon({"demo": cfg_off.root})
    d.run_pass("demo")

    # Ordinary pre-existing logic: `VERDICT: APPROVED (repaired)` still
    # regex-matches plain APPROVED -> MERGE_READY, no invalidation, no revert.
    assert storage.load_state("demo", task_id).state is TaskState.MERGE_READY
    recorded = next(e for e in storage.iter_events("demo") if e.type is EventType.REVIEW_RECORDED)
    assert recorded.payload["result"] == "approved"
    assert "repair_invalidated" not in recorded.payload
    show = subprocess.run(
        ["git", "-C", str(cfg_off.root), "show", f"feat/{task_id}:src/thing.py"],
        capture_output=True, text=True)
    assert show.returncode == 0, "policy off -> no revert, the file must still be present"
