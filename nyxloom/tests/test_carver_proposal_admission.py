"""F018 P3b — carver proposal validation + admission tests.

Covers docs/plan-long-running-carver.md §12 Package 3's proposal half
(work-items 4-5): daemon._validated_carve_proposals (the §4.1 per-artifact
validation pipeline the daemon input-builder now DERIVES instead of
hardcoding to ()) and daemon._execute_admit_carve_proposal (the §4.2
atomic-admission executor for reconcile.AdmitCarveProposal).

No turn produces a CARVER_PROPOSAL_RECORDED event yet (P3c, out of scope
for this package) -- every test here SYNTHESIZES that event directly via
storage.append_and_apply, exactly like test_carver_session_executor.py
synthesizes CARVER_SESSION_STARTED/DEGRADED for its own unit tests.

Test technique: most tests call _validated_carve_proposals /
_execute_admit_carve_proposal DIRECTLY with a locally-constructed `states`
dict (mirrors test_carver_session_executor.py's own convention for
_execute_start_carver_session et al) rather than going through a full
run_pass. This is deliberate, not just convenience: a proposal artifact
must physically exist on disk under cfg.handoff_globs for validation to
read/hash it (see _resolve_carve_proposal_artifact), which means the
ordinary plan_project 'new handoffs' scan (item 1, ALSO keyed off
frontmatter.discover_handoffs) would independently discover and CreateTask
the SAME file on the SAME real run_pass -- a genuine, harmless (idempotent
either way) race between the two paths given the current package boundary
(P3c has not yet built a staging area that keeps un-admitted artifacts out
of handoff_globs). Testing _execute_admit_carve_proposal directly isolates
admission's OWN task-creation logic from that race. The one true end-to-end
test below (_execute dispatch-chain wiring) sidesteps it the SAME way
test_e2e_resume_dispatch_branch_via_run_pass does: _scripted stubs
reconcile.plan_project wholesale, so the ordinary scan's own CreateTask
action is never planned that pass.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from nyxloom import daemon, frontmatter, lint, paths, reconcile, snapshot, storage
from nyxloom.config import ProjectConfig, register_project
from nyxloom.types import (
    Actor, ActorKind, EventType, TaskState, TaskStateFile, utc_now,
)

# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py -- every test_*.py in
# this suite keeps local helpers local; mirrors test_carver_session_executor.py)

CARVER_PROJECT_TOML = """\
[project]
id = "demo"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]
infra_globs = ["infra/**"]

[gates.pytest-q]
argv = ["true"]
phase = "implementation"
timeout_seconds = 60
environment = "local"

[mutexes.stack]
scope = "project"
capacity = 1

[policy]
max_active_tasks = 2
ready_queue_target = 3
carve_authority = "files"

[stage.carve]
session = "project-persistent"
retain_merge_digests = 5

[notify]
"""

CARVER_ROUTES_TOML = """\
revision = "test-p3b"

[tiers.flash-high]
routes = ["fake-cli"]

[routes.fake-cli]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
role_default = "review-independent"
resume = ["fake", "--resume", "{session}", "{prompt}"]
"""


@pytest.fixture()
def carver_project(tmp_state, tmp_path) -> ProjectConfig:
    root = tmp_path / "demo-repo"
    (root / ".nyxloom").mkdir(parents=True)
    (root / "handoff").mkdir()
    (root / "docs").mkdir()
    (root / ".nyxloom" / "project.toml").write_text(CARVER_PROJECT_TOML)
    (root / "docs" / "DECISIONS-INBOX.md").write_text("# Decisions inbox\n\n---\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=root, check=True)
    paths.routes_path().write_text(CARVER_ROUTES_TOML)
    register_project("demo", root)
    paths.ensure_layout("demo")
    return ProjectConfig.load(root)



def _persist(project: str, states: dict) -> dict:
    """Persist hand-made states through the canonical mutation.

    CR-04b: `append_and_apply` derives the projection from COMMITTED state, so
    a task that exists only in a caller's dict is now correctly invisible to
    it -- a phantom task can no longer receive a transition. These fixtures
    used to rely on that, which meant they were exercising a projection the
    store had never heard of.
    """
    for task_id, tsf in list(states.items()):
        storage.append_and_apply(
            project, states, actor=Actor(ActorKind.OPERATOR, "test"),
            type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
            task_id=task_id)
    return states



def _bootstrap_warm(project: str = "demo", generation: int = 1, session_id: str = "S1") -> None:
    """Synthesize a WARM session snapshot at `generation` directly (no real
    launch needed for these tests -- proposal validation/admission reads
    ONLY snap.generation/status/last_consumed_event_sequence). Mirrors
    test_carver_session_executor.py's own
    test_start_carver_session_refuses_on_stale_warm_snapshot technique."""
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_SESSION_STARTED,
        payload={"generation": generation, "session_id": session_id,
                 "route": {"route_id": "fake-cli"}, "spine_revisions": {}},
    )


_HANDOFF_TEMPLATE = """---
schema_version: 1
id: {id}
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "{input_revision}"
source: {{kind: roadmap, ref: docs/DECISIONS-INBOX.md}}
scope:
  touch: ["src/demo/thing.py", "tests/test_thing.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Worktree: none (carve_authority=files). Branch: feat/{id}. Context to read first: docs/DECISIONS-INBOX.md.
Out of scope: everything else; do not touch forbidden files.

Contract body. If a named contract cannot be met as specified, STOP, write
`BLOCKED: <reason>` to the LOG, commit, exit.
"""

# A body that omits the required L12 'BLOCKED:' marker -- deliberately
# lint-red (has_blocking True) for the negative oracle.
_LINT_RED_HANDOFF_TEMPLATE = """---
schema_version: 1
id: {id}
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "{input_revision}"
source: {{kind: roadmap, ref: docs/DECISIONS-INBOX.md}}
scope:
  touch: ["src/demo/thing.py", "tests/test_thing.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Worktree: none. Branch: feat/{id}. Context to read first: docs/DECISIONS-INBOX.md.
Out of scope: everything else.

Contract body with no completion marker at all.
"""


def _write_handoff(root: Path, id_: str, *, input_revision: str = "abc1234",
                    template: str = _HANDOFF_TEMPLATE) -> tuple[str, str]:
    """Write a real handoff file under root/handoff/. Returns (relpath, sha256)."""
    text = template.format(id=id_, input_revision=input_revision)
    p = root / "handoff" / f"{id_}.md"
    p.write_text(text)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    return f"handoff/{id_}.md", sha


def _artifact(relpath: str, sha: str) -> dict:
    return {"kind": "handoff", "path": relpath, "sha256": sha, "source_ref": None}


def _proposal_payload(*, project: str = "demo", generation: int = 1, turn_id: str = "att-1",
                       artifacts: list[dict], dispositions: list[dict] | None = None,
                       base_revision: str = "abc1234", mode: str = "headroom",
                       outcome: str = "CANDIDATES_READY") -> dict:
    return {
        "kind": "carve-proposal", "schema_version": 1,
        "proposal_id": f"{project}:carve:{generation}:{turn_id}",
        "turn_id": turn_id,
        "source": {"mode": mode, "refs": [], "base_revision": base_revision,
                   "merge_digest_cursor": 0},
        "artifacts": artifacts,
        "dispositions": dispositions or [],
        "outcome": outcome, "headroom_estimate": 0,
    }


def _record(project: str, payload: dict):
    return storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_PROPOSAL_RECORDED, payload=payload,
    )


def _scripted(monkeypatch, sequence):
    """monkeypatch reconcile.plan_project to pop one actions-list per call;
    local twin of test_carver_session_executor.py's own identical helper."""
    seq = list(sequence)

    def fake(inp):
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)


# ==========================================================================
# _validated_carve_proposals -- the §4.1 validation pipeline
# ==========================================================================

def test_valid_proposal_produces_one_validated_proposal(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    proposals = d._validated_carve_proposals("demo", cfg, snap)

    assert len(proposals) == 1
    vp = proposals[0]
    assert vp.proposal_id == "demo:carve:1:att-1"
    assert vp.generation == 1
    assert vp.artifact_ids == ["demo-P901"]
    assert vp.artifact_paths == [relpath]
    assert vp.artifact_hashes == [sha]


def test_feature_off_validated_proposals_is_empty(tmp_state, carver_project):
    """MASTER GATE: snap is None (feature off) -> () regardless of any
    recorded proposal event."""
    cfg = carver_project
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    assert d._validated_carve_proposals("demo", cfg, None) == ()


@pytest.mark.parametrize("mutate", [
    "empty_payload",
    "not_a_dict_artifacts",
])
def test_malformed_payload_not_validated(tmp_state, carver_project, mutate):
    cfg = carver_project
    _bootstrap_warm()
    if mutate == "empty_payload":
        payload = {}
    else:
        payload = _proposal_payload(artifacts=[])
        payload["artifacts"] = "not-a-list"
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_stale_base_revision_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901", input_revision="abc1234")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)], base_revision="ffffff0")
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_hash_mismatch_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, "0" * 64)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_lint_red_handoff_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901", template=_LINT_RED_HANDOFF_TEMPLATE)
    # Sanity: this fixture really is lint-red (L12, missing BLOCKED marker).
    findings = lint.lint_file(cfg.root / relpath, cfg)
    assert lint.has_blocking(findings)

    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


@pytest.mark.parametrize("turn_mutate", ["absent", "wrong"])
def test_wrong_or_absent_turn_id_not_validated(tmp_state, carver_project, turn_mutate):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    if turn_mutate == "absent":
        del payload["turn_id"]
    else:
        payload["turn_id"] = "att-not-the-one-in-proposal-id"
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


@pytest.mark.parametrize("bad_path", ["../outside.md", "/etc/passwd", "handoff/../../escape.md"])
def test_path_traversal_not_validated(tmp_state, carver_project, bad_path):
    cfg = carver_project
    _bootstrap_warm()
    payload = _proposal_payload(artifacts=[_artifact(bad_path, "0" * 64)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_concern1_stale_generation_excluded(tmp_state, carver_project):
    """CONCERN-1 (single-authority): a proposal recorded for a generation
    OTHER than the session's current generation is excluded here even
    though it is otherwise perfectly valid -- the pure planner does not
    re-check this."""
    cfg = carver_project
    _bootstrap_warm(generation=1)
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    stale_payload = _proposal_payload(generation=2, turn_id="att-2",
                                       artifacts=[_artifact(relpath, sha)])
    _record("demo", stale_payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert snap.generation == 1
    assert d._validated_carve_proposals("demo", cfg, snap) == ()

    # The SAME artifact, correctly attributed to generation 1, DOES validate.
    fresh_payload = _proposal_payload(generation=1, turn_id="att-1",
                                       artifacts=[_artifact(relpath, sha)])
    _record("demo", fresh_payload)
    proposals = d._validated_carve_proposals("demo", cfg, snap)
    assert len(proposals) == 1
    assert proposals[0].proposal_id == "demo:carve:1:att-1"


def test_already_admitted_proposal_excluded_via_marker(tmp_state, carver_project):
    """AD1 fix: exclusion is keyed on a durable CARVER_PROPOSAL_ADMITTED
    marker event, NOT on task presence in `states` -- see
    _proposal_already_admitted. Before the marker exists, the proposal
    validates; once the marker is recorded (by a real admission), it is
    excluded."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert len(d._validated_carve_proposals("demo", cfg, snap)) == 1

    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=EventType.CARVER_PROPOSAL_ADMITTED,
        payload={"proposal_id": payload["proposal_id"], "generation": 1,
                 "artifact_ids": ["demo-P901"]},
    )
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_race_with_ordinary_scan_does_not_suppress_validation(tmp_state, carver_project):
    """AD1 REGRESSION (the actual bug): a proposal's artifact_id already
    present in `states` -- e.g. because the ordinary plan_project 'new
    handoffs' scan (CreateTask, which runs BEFORE carver_actions in one
    pass) discovered the same on-disk file first -- must NOT be excluded
    from validation just because a task with that id already exists. Only
    a genuine CARVER_PROPOSAL_ADMITTED marker excludes it. The OLD
    structural check (`all(aid in states for aid in vp.artifact_ids)`)
    would have wrongly excluded this proposal here, permanently skipping
    step-4 re-scope supersession -- see the full run_pass regression test
    below (test_ad1_real_run_pass_admits_and_supersedes_origin) for the
    end-to-end proof."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    # Simulate the ordinary scan having ALREADY created the task -- no
    # CARVER_PROPOSAL_ADMITTED marker exists yet.
    raced_states = {
        "demo-P901": TaskStateFile(schema_version=1, task_id="demo-P901", project="demo",
                                    state=TaskState.CARVED, since=utc_now()),
    }
    proposals = d._validated_carve_proposals("demo", cfg, snap)
    assert len(proposals) == 1
    assert proposals[0].artifact_ids == ["demo-P901"]
    # Confirm _execute_admit_carve_proposal correctly proceeds despite the
    # race (idempotent task-creation skip, but admission itself -- and
    # therefore step-4 re-scope -- still runs).
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=proposals[0].proposal_id, artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, raced_states, action)
    assert any(e.type is EventType.CARVER_PROPOSAL_ADMITTED for e in events)
    assert not any(e.type is EventType.TASK_CREATED for e in events)  # already existed


# ==========================================================================
# _execute_admit_carve_proposal -- the §4.2 atomic admission executor
# ==========================================================================

def test_admission_creates_task_once_and_emits_admitted_marker(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))

    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    types = [e.type for e in events]
    assert EventType.TASK_CREATED in types
    marker = [e for e in events if e.type is EventType.CARVER_PROPOSAL_ADMITTED]
    assert len(marker) == 1
    assert marker[0].payload["proposal_id"] == payload["proposal_id"]
    assert marker[0].payload["artifact_ids"] == ["demo-P901"]
    assert "demo-P901" in states
    assert states["demo-P901"].state is TaskState.CARVED
    assert states["demo-P901"].handoff_path == relpath

    # Idempotent: admitting the SAME proposal_id again is now a clean no-op
    # (the proposal is already fully admitted -- _validated_carve_proposals
    # excludes it, so _execute_admit_carve_proposal finds no `chosen` match
    # and creates ZERO further events, never a duplicate task).
    events2 = d._execute_admit_carve_proposal("demo", cfg, states, action)
    assert events2 == []


def test_multi_artifact_proposal_creates_each_task_once(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath1, sha1 = _write_handoff(cfg.root, "demo-P901")
    relpath2, sha2 = _write_handoff(cfg.root, "demo-P902")
    payload = _proposal_payload(artifacts=[_artifact(relpath1, sha1), _artifact(relpath2, sha2)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"],
        artifact_ids=("demo-P901", "demo-P902"))

    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    created_ids = {e.task_id for e in events if e.type is EventType.TASK_CREATED}
    assert created_ids == {"demo-P901", "demo-P902"}
    assert set(states) == {"demo-P901", "demo-P902"}


def test_admission_no_longer_validated_proposal_id_refuses_cleanly(tmp_state, carver_project):
    """Unknown/never-recorded proposal_id -- refuses cleanly, no side effect."""
    cfg = carver_project
    _bootstrap_warm()
    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id="demo:carve:1:never-recorded", artifact_ids=("x",))

    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert events == []
    assert states == {}


def test_effect_boundary_recheck_refuses_on_changed_artifact(tmp_state, carver_project):
    """'A proposal whose file changed since validation is refused' (§4.2
    item 1). Fresh re-validation inside _execute_admit_carve_proposal
    re-reads and re-hashes the artifact every call -- a file mutated after
    the proposal was recorded/planned no longer matches its declared hash,
    so admission refuses cleanly instead of creating a task from stale
    content."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert len(d._validated_carve_proposals("demo", cfg, snap)) == 1  # sanity: valid now

    # Mutate the artifact's content after validation but before admission.
    (cfg.root / relpath).write_text(
        (cfg.root / relpath).read_text() + "\nmutated after validation\n")

    states: dict = {}
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert events == []
    assert states == {}


def test_rescope_supersedes_origin_only_on_successful_admission(tmp_state, carver_project):
    """§4.2 tightens B7: the origin task named by a 'handoff' disposition
    is superseded (outcome RESCOPED) ONLY once admission actually
    succeeds -- never merely on a proposal existing/being valid."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff",
                       "artifact_ref": relpath}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = _persist("demo", {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    })
    snap = d._carver_session("demo", cfg)
    # Merely being validated does NOT touch the origin.
    assert len(d._validated_carve_proposals("demo", cfg, snap)) == 1
    assert states["demo-origin"].state is TaskState.READY_TO_CARVE

    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    superseded = [e for e in events if e.type is EventType.TASK_SUPERSEDED]
    assert len(superseded) == 1
    assert superseded[0].task_id == "demo-origin"
    assert superseded[0].payload["outcome"] == "RESCOPED"
    assert states["demo-origin"].state is TaskState.SUPERSEDED


@pytest.mark.parametrize("result", ["decision", "drop", "backlog", "redundant"])
def test_non_handoff_disposition_leaves_origin_untouched(tmp_state, carver_project, result):
    """Negative oracle: 'origin remains READY_TO_CARVE ... until an
    explicit typed decision/drop disposition' -- any disposition result
    other than 'handoff' leaves the named origin exactly where it was."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": result, "artifact_ref": None}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = _persist("demo", {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    })
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)
    assert states["demo-origin"].state is TaskState.READY_TO_CARVE


# ==========================================================================
# Bounded repair-count escalation (§4.1: 'after the configured repair
# count, emit NEEDS_OPERATOR{reason: carver-proposal-invalid}')
# ==========================================================================

def test_repair_count_exceeded_emits_needs_operator_once(tmp_state, carver_project):
    cfg = carver_project
    assert cfg.carve.max_proposal_repairs == 2
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    # Two DISTINCT invalid proposals (different turn ids -> different
    # proposal_ids) for the current generation -- each fails validation on
    # a hash mismatch.
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, "0" * 64)]))
    _record("demo", _proposal_payload(turn_id="att-2", artifacts=[_artifact(relpath, "1" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    events = d._carve_proposal_repair_escalations("demo", cfg, states)

    escalations = [e for e in events if e.type is EventType.NEEDS_OPERATOR
                   and e.payload.get("reason") == "carver-proposal-invalid"]
    assert len(escalations) == 1
    assert escalations[0].payload["generation"] == 1
    assert escalations[0].payload["invalid_count"] == 2

    # Debounced: a second call does not re-emit.
    events2 = d._carve_proposal_repair_escalations("demo", cfg, states)
    assert events2 == []


def test_repair_count_below_threshold_emits_nothing(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, "0" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    events = d._carve_proposal_repair_escalations("demo", cfg, {})
    assert events == []


def test_repair_escalation_feature_off_is_noop(tmp_state, sample_project):
    d = daemon.Daemon({"demo": sample_project.root})
    assert d._carve_proposal_repair_escalations("demo", sample_project, {}) == []


# ==========================================================================
# F018 AD3 (docs/handoff/f018-ad3-carve-repair-proposal.md): the daemon-side
# repair-signal builder _pending_carve_repairs -- reuses P3b's EXACT counting
# but returns the invalid proposal ids for a repair turn, GATED to
# `1 <= invalid < max_proposal_repairs` so it COMPOSES with the P3b escalation
# above and never double-fires (O1, O2, O6).
# ==========================================================================

def test_ad3_pending_repairs_below_ceiling_and_p3b_does_not_escalate(
        tmp_state, carver_project):
    """O1 (daemon build_input) + the below-ceiling half of O2: ONE invalid
    proposal, ceiling=2 -> _pending_carve_repairs yields exactly one
    CarveRepairRequest (right proposal_id + generation) AND
    _carve_proposal_repair_escalations emits NOTHING (invalid=1 < 2). Repair
    fires, escalation does not -- the complement of the at-ceiling case."""
    cfg = carver_project
    assert cfg.carve.max_proposal_repairs == 2
    _bootstrap_warm()
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, "0" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    repairs = d._pending_carve_repairs("demo", cfg, snap)
    assert len(repairs) == 1
    assert repairs[0].proposal_id == "demo:carve:1:att-1"
    assert repairs[0].generation == 1
    # Complement: P3b does NOT escalate below the ceiling.
    assert d._carve_proposal_repair_escalations("demo", cfg, {}) == []


def test_ad3_pending_repairs_empty_at_ceiling_where_p3b_escalates(
        tmp_state, carver_project):
    """O2 (compose, no double-fire): TWO invalid proposals, ceiling=2 ->
    _pending_carve_repairs is EMPTY (no repair) AND
    _carve_proposal_repair_escalations emits exactly one NEEDS_OPERATOR. Repair
    and escalation are mutually exclusive by the single `<` vs `>=` boundary --
    they never both fire for the same generation. Uses the SAME two-invalid-
    proposal seeding as test_repair_count_exceeded_emits_needs_operator_once so
    the boundary is provably shared."""
    cfg = carver_project
    assert cfg.carve.max_proposal_repairs == 2
    _bootstrap_warm()
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, "0" * 64)]))
    _record("demo", _proposal_payload(turn_id="att-2", artifacts=[_artifact(relpath, "1" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._pending_carve_repairs("demo", cfg, snap) == ()
    escalations = [e for e in d._carve_proposal_repair_escalations("demo", cfg, {})
                   if e.type is EventType.NEEDS_OPERATOR
                   and e.payload.get("reason") == "carver-proposal-invalid"]
    assert len(escalations) == 1


def test_ad3_pending_repairs_feature_off_and_zero_invalid_are_empty(
        tmp_state, carver_project):
    """O6 (daemon off-path): feature off (snap None) -> (); and with the
    persistent carver ON but only a VALID proposal recorded (zero invalid) ->
    () as well (the invalid==0 gate). Both are byte-identical 'nothing to
    repair'."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    # A fully-VALID proposal -> zero invalid.
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._pending_carve_repairs("demo", cfg, None) == ()   # feature off
    assert d._pending_carve_repairs("demo", cfg, snap) == ()   # on, zero invalid


def test_ad3_pending_repairs_sorted_by_proposal_id(tmp_state, carver_project):
    """Determinism: below a raised ceiling, several invalid proposals are
    returned deterministically sorted by proposal_id (a pass with multiple
    invalids always yields the same order). Ceiling raised to 3 so invalid=2
    stays below it; the two proposals are RECORDED in reverse proposal_id order
    to prove the sort is real."""
    cfg = replace(carver_project,
                  carve=replace(carver_project.carve, max_proposal_repairs=3))
    _bootstrap_warm()
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(turn_id="att-2", artifacts=[_artifact(relpath, "1" * 64)]))
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, "0" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    repairs = d._pending_carve_repairs("demo", cfg, snap)
    assert [r.proposal_id for r in repairs] == ["demo:carve:1:att-1", "demo:carve:1:att-2"]


def test_ad3_pending_repairs_excludes_wrong_generation(tmp_state, carver_project):
    """Generation filter (mirrors test_concern1_stale_generation_excluded): an
    invalid proposal recorded for a DIFFERENT generation than the session's
    current one is skipped -- so a lone stale-generation invalid proposal
    yields () (it does not count toward this generation's repair signal)."""
    cfg = carver_project
    _bootstrap_warm(generation=1)
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    # Invalid (hash mismatch) AND for generation 2, not the current gen 1.
    _record("demo", _proposal_payload(generation=2, turn_id="att-2",
                                      artifacts=[_artifact(relpath, "0" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert snap.generation == 1
    assert d._pending_carve_repairs("demo", cfg, snap) == ()


def test_ad3_pending_repairs_storage_error_is_typed_unavailable(
        tmp_state, carver_project, monkeypatch):
    """CR-02a: () was described as the conservative direction and is not.

    An empty repair set means "nothing needs repairing", so an unreadable log
    silently retires the repair signal for a generation that may have several
    invalid proposals outstanding -- and P3b's ceiling escalation, which
    counts from the same log, goes quiet with it. The read is authoritative.

    Negative control: the feature-off path (`snap is None`) still returns ()
    without touching the log."""
    cfg = carver_project
    _bootstrap_warm()
    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)  # captured BEFORE the monkeypatch

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._pending_carve_repairs("demo", cfg, snap)
    assert d._pending_carve_repairs("demo", cfg, None) == ()


# ==========================================================================
# _execute isinstance dispatch-chain wiring (mirrors
# test_e2e_resume_dispatch_branch_via_run_pass's own _scripted technique)
# ==========================================================================

def test_execute_dispatches_admit_carve_proposal_branch(
        tmp_state, carver_project, monkeypatch):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    _scripted(monkeypatch, [[action]])

    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    assert any(e.type is EventType.CARVER_PROPOSAL_ADMITTED for e in events)
    assert storage.load_state("demo", "demo-P901").state is TaskState.CARVED


# ==========================================================================
# AD1 REGRESSION -- the real, UNSTUBBED run_pass flow (reconcile.plan_project
# is NOT monkeypatched here, unlike every _scripted test above). This is the
# scenario the structural 'all artifact_ids in states' cursor got wrong: the
# ordinary 'new handoffs' CreateTask action (lifecycle actions, combined
# BEFORE carver_actions in reconcile.plan_project's own 'Combine results in
# order' section) discovers and creates the SAME on-disk artifact task in
# THIS SAME pass, before AdmitCarveProposal executes. Proves: (1) the task
# still ends up created (exactly once, whichever path got there first), (2)
# CARVER_PROPOSAL_ADMITTED still fires, and (3) critically, step-4 re-scope
# supersession STILL fires -- the origin does not get stranded in
# READY_TO_CARVE forever. A second run_pass must not re-admit.
# ==========================================================================

def test_ad1_real_run_pass_admits_and_supersedes_origin(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff", "artifact_ref": relpath}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = storage.list_states("demo")
    states["demo-origin"] = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-origin", project="demo",
        state=TaskState.READY_TO_CARVE, since=utc_now())
    storage.save_state(states["demo-origin"])

    # reconcile.plan_project runs FOR REAL -- no _scripted stub.
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)

    created = [e for e in events if e.type is EventType.TASK_CREATED
               and e.task_id == "demo-P901"]
    assert len(created) == 1  # created exactly once, whichever path won
    assert storage.load_state("demo", "demo-P901").state is TaskState.CARVED

    admitted = [e for e in events if e.type is EventType.CARVER_PROPOSAL_ADMITTED]
    assert len(admitted) == 1
    assert admitted[0].payload["proposal_id"] == payload["proposal_id"]

    origin = storage.load_state("demo", "demo-origin")
    assert origin.state is TaskState.SUPERSEDED
    superseded_events = [e for e in events if e.type is EventType.TASK_SUPERSEDED
                          and e.task_id == "demo-origin"]
    assert len(superseded_events) == 1
    assert superseded_events[0].payload["outcome"] == "RESCOPED"

    # A second pass must NOT re-admit (marker already recorded) and must
    # not error.
    d.run_pass("demo")
    events2 = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events2)
    assert len([e for e in events2 if e.type is EventType.CARVER_PROPOSAL_ADMITTED]) == 1
    assert len([e for e in events2 if e.type is EventType.TASK_CREATED
                and e.task_id == "demo-P901"]) == 1


# ==========================================================================
# Byte-identical feature-off (structural, but asserted): with
# cfg.carve.session == "fresh" (default), _validated_carve_proposals is
# always (), so AdmitCarveProposal is never planned/executed -- a
# representative run_pass produces no CARVER_PROPOSAL_ADMITTED marker and
# no TICK_ERROR, exactly matching test_carver_session_executor.py's own
# equivalent oracle for Start/Resume.
# ==========================================================================

def test_feature_off_run_pass_never_admits_proposals(tmp_state, sample_project):
    cfg = sample_project
    assert cfg.carve.session == "fresh"
    # A CARVER_PROPOSAL_RECORDED event present in the log changes nothing --
    # the MASTER GATE excludes it before it is ever read for validation.
    _record("demo", _proposal_payload(artifacts=[_artifact("handoff/does-not-exist.md", "0" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.TICK_ERROR for e in events)
    assert not any(e.type is EventType.CARVER_PROPOSAL_ADMITTED for e in events)


# ==========================================================================
# Diff-coverage closers: every remaining structural branch in the new
# validation/admission pipeline (storage-read fail-safes, malformed-
# structure edges, and the admission-side idempotency/disposition edges
# not already exercised above).
# ==========================================================================

def test_validated_proposals_storage_error_is_typed_unavailable(
        tmp_state, carver_project, monkeypatch):
    """CR-02a: an unreadable log is not "no admittable proposals".

    Degrading to () hides a proposal that IS admittable, and (worse) the
    same read backs `_proposal_already_admitted`, whose degraded answer is
    "not yet admitted" -- so the pair could both hide a real proposal and
    re-admit a consumed one depending on which call the fault hit."""
    cfg = carver_project
    _bootstrap_warm()
    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)  # captured BEFORE the monkeypatch below

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._validated_carve_proposals("demo", cfg, snap)
    assert d._validated_carve_proposals("demo", cfg, None) == ()


def test_event_sequence_at_or_before_cursor_excluded(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    ev = _record("demo", _proposal_payload(artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert len(d._validated_carve_proposals("demo", cfg, snap)) == 1  # sanity

    snap.last_consumed_event_sequence = ev.sequence
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


@pytest.mark.parametrize("proposal_id", [
    "other-project:carve:1:att-1",   # wrong project prefix
    "demo:notcarve:1:att-1",         # wrong literal (not "carve")
    "demo:carve:1:",                 # empty turn-id segment
    "demo:carve:notanumber:att-1",   # non-numeric generation
])
def test_malformed_proposal_id_structure_not_validated(
        tmp_state, carver_project, proposal_id):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    payload["proposal_id"] = proposal_id
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


@pytest.mark.parametrize("bad_path", [None, 123, ""])
def test_artifact_path_not_a_string_not_validated(tmp_state, carver_project, bad_path):
    cfg = carver_project
    _bootstrap_warm()
    payload = _proposal_payload(artifacts=[
        {"kind": "handoff", "path": bad_path, "sha256": "0" * 64, "source_ref": None}])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_artifact_path_null_byte_resolution_error_not_validated(tmp_state, carver_project):
    """resolve()/relative_to() raising (a syntactically-odd but non-'..'/
    non-absolute path) is caught, not propagated."""
    cfg = carver_project
    _bootstrap_warm()
    payload = _proposal_payload(artifacts=[
        {"kind": "handoff", "path": "handoff/\x00bad.md", "sha256": "0" * 64,
         "source_ref": None}])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_discover_handoffs_error_not_validated(tmp_state, carver_project, monkeypatch):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    def _boom(cfg):
        raise RuntimeError("simulated glob failure")

    monkeypatch.setattr(frontmatter, "discover_handoffs", _boom)
    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_read_bytes_oserror_not_validated(tmp_state, carver_project, monkeypatch):
    """A path that resolves and 'discovers' clean but no longer exists on
    disk at read time (e.g. removed between recording and validation)."""
    cfg = carver_project
    _bootstrap_warm()
    ghost_relpath = "handoff/ghost.md"
    ghost_path = cfg.root / ghost_relpath
    monkeypatch.setattr(frontmatter, "discover_handoffs", lambda cfg: [ghost_path])
    payload = _proposal_payload(artifacts=[_artifact(ghost_relpath, "0" * 64)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_source_not_a_dict_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    payload["source"] = "not-a-dict"
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_artifact_entry_not_a_dict_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    payload = _proposal_payload(artifacts=["not-a-dict"])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


@pytest.mark.parametrize("bad_hash", [None, 123, ""])
def test_artifact_hash_not_a_string_not_validated(tmp_state, carver_project, bad_hash):
    cfg = carver_project
    _bootstrap_warm()
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[
        {"kind": "handoff", "path": relpath, "sha256": bad_hash, "source_ref": None}])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_frontmatter_parse_error_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath = "handoff/demo-P901.md"
    p = cfg.root / relpath
    p.write_text("not frontmatter at all\n")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_lint_file_error_not_validated(tmp_state, carver_project, monkeypatch):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)])
    _record("demo", payload)

    def _boom(path, cfg):
        raise RuntimeError("simulated lint failure")

    monkeypatch.setattr(lint, "lint_file", _boom)
    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap) == ()


def test_carve_proposal_recorded_payload_storage_error_is_typed_unavailable(
        tmp_state, carver_project, monkeypatch):
    """CR-02a: None means "never recorded", which skips the §4.2 step-4
    re-scope supersession entirely. An unreadable log must not be able to
    produce that answer. The genuine never-recorded case still returns None
    -- see test_carve_proposal_recorded_payload_not_found_returns_none."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._carve_proposal_recorded_payload("demo", "demo:carve:1:att-1")


def test_carve_proposal_recorded_payload_not_found_returns_none(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    assert d._carve_proposal_recorded_payload("demo", "demo:carve:1:does-not-exist") is None


def test_repair_escalations_storage_error_is_typed_unavailable(
        tmp_state, carver_project, monkeypatch):
    """CR-02a: run_pass calls this BEFORE the fan-in's own audit check, so
    the typed fault propagates to the pass-level containment and the pass
    emits SNAPSHOT_UNAVAILABLE -- rather than the old [] which read as "no
    escalation needed"."""
    cfg = carver_project
    _bootstrap_warm()
    d = daemon.Daemon({"demo": cfg.root})

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._carve_proposal_repair_escalations("demo", cfg, {})


def test_repair_escalated_storage_error_is_typed_unavailable(
        tmp_state, carver_project, monkeypatch):
    """CR-02a: False means "not yet escalated", so an unreadable log re-pages
    the operator on every pass -- the notification-storm shape this debounce
    exists to prevent."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._carve_proposal_repair_escalated("demo", 1)


def test_repair_count_ignores_other_generation_records(tmp_state, carver_project):
    cfg = carver_project
    assert cfg.carve.max_proposal_repairs == 2
    _bootstrap_warm(generation=1)
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    # A well-formed but WRONG-generation record -- must be ignored, not
    # counted toward generation 1's repair budget.
    _record("demo", _proposal_payload(generation=2, turn_id="att-9",
                                       artifacts=[_artifact(relpath, "0" * 64)]))
    _record("demo", _proposal_payload(turn_id="att-1", artifacts=[_artifact(relpath, "0" * 64)]))
    _record("demo", _proposal_payload(turn_id="att-2", artifacts=[_artifact(relpath, "1" * 64)]))

    d = daemon.Daemon({"demo": cfg.root})
    events = d._carve_proposal_repair_escalations("demo", cfg, {})
    escalations = [e for e in events if e.type is EventType.NEEDS_OPERATOR]
    assert len(escalations) == 1
    assert escalations[0].payload["invalid_count"] == 2  # the gen-2 record excluded


def test_execute_admit_feature_off_returns_no_events(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id="demo:carve:1:att-1", artifact_ids=("x",))
    events = d._execute_admit_carve_proposal("demo", cfg, {}, action)
    assert events == []


def test_partial_admission_creates_only_missing_task(tmp_state, carver_project):
    """Self-healing partial admission (e.g. a prior pass crashed mid-loop):
    an artifact_id already present in `states` is skipped, the remaining
    one is created."""
    cfg = carver_project
    _bootstrap_warm()
    relpath1, sha1 = _write_handoff(cfg.root, "demo-P901")
    relpath2, sha2 = _write_handoff(cfg.root, "demo-P902")
    payload = _proposal_payload(artifacts=[_artifact(relpath1, sha1), _artifact(relpath2, sha2)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = {
        "demo-P901": TaskStateFile(schema_version=1, task_id="demo-P901", project="demo",
                                    state=TaskState.CARVED, since=utc_now(),
                                    handoff_path=relpath1),
    }
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"],
        artifact_ids=("demo-P901", "demo-P902"))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    created_ids = {e.task_id for e in events if e.type is EventType.TASK_CREATED}
    assert created_ids == {"demo-P902"}
    assert set(states) == {"demo-P901", "demo-P902"}


def test_disposition_missing_or_unknown_origin_ref_skipped(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[
            {"source_ref": None, "result": "handoff", "artifact_ref": relpath},
            {"source_ref": "unknown-task", "result": "handoff", "artifact_ref": relpath},
        ],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states: dict = {}
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)


def test_disposition_origin_not_ready_to_carve_skipped(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff", "artifact_ref": relpath}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.ACTIVE, since=utc_now()),
    }
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)
    assert states["demo-origin"].state is TaskState.ACTIVE


def test_disposition_artifact_ref_mismatch_skipped(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff",
                       "artifact_ref": "handoff/some-other-file.md"}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = _persist("demo", {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    })
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)
    assert states["demo-origin"].state is TaskState.READY_TO_CARVE


def test_disposition_artifact_ref_normalized_match_supersedes(tmp_state, carver_project):
    """AD2 fix: a disposition's artifact_ref that is a differently-
    normalized but EQUIVALENT form of an admitted artifact path (a leading
    './' here) still matches -- normalization must not create a false
    mismatch that silently skips a legitimate supersession."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff",
                       "artifact_ref": f"./{relpath}"}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = _persist("demo", {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    })
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    superseded = [e for e in events if e.type is EventType.TASK_SUPERSEDED]
    assert len(superseded) == 1
    assert states["demo-origin"].state is TaskState.SUPERSEDED


def test_disposition_missing_artifact_ref_skipped(tmp_state, carver_project):
    """A 'handoff' disposition with a valid, READY_TO_CARVE origin but no
    (or a non-string) artifact_ref never matches -- _normalize_repo_relpath
    returns None for it, distinct from the 'wrong path' mismatch case."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(
        artifacts=[_artifact(relpath, sha)],
        dispositions=[{"source_ref": "demo-origin", "result": "handoff", "artifact_ref": None}],
        mode="rescope",
    )
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    states = _persist("demo", {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    })
    action = reconcile.AdmitCarveProposal(
        project="demo", proposal_id=payload["proposal_id"], artifact_ids=("demo-P901",))
    events = d._execute_admit_carve_proposal("demo", cfg, states, action)

    assert not any(e.type is EventType.TASK_SUPERSEDED for e in events)
    assert states["demo-origin"].state is TaskState.READY_TO_CARVE


def test_proposal_already_admitted_storage_error_is_typed_unavailable(
        tmp_state, carver_project, monkeypatch):
    """CR-02a REVERSES this one: False was labelled 'fail-safe' and is the
    opposite. False means NOT yet admitted, so an unreadable log RE-ADMITS an
    already-admitted proposal -- recreating its tasks and re-running its
    supersession. This is the AD1 idempotency cursor; it must be authoritative."""
    cfg = carver_project
    d = daemon.Daemon({"demo": cfg.root})

    def _boom(project, since=0):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(storage, "iter_events", _boom)
    with pytest.raises(snapshot.SnapshotUnavailable):
        d._proposal_already_admitted("demo", "demo:carve:1:att-1")
