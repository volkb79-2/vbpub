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
from pathlib import Path

import pytest

from nyxloom import daemon, lint, paths, reconcile, storage
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


def _record(project: str, payload: dict) -> None:
    storage.append_and_apply(
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
    proposals = d._validated_carve_proposals("demo", cfg, snap, {})

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
    assert d._validated_carve_proposals("demo", cfg, None, {}) == ()


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
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()


def test_stale_base_revision_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901", input_revision="abc1234")
    payload = _proposal_payload(artifacts=[_artifact(relpath, sha)], base_revision="ffffff0")
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()


def test_hash_mismatch_not_validated(tmp_state, carver_project):
    cfg = carver_project
    _bootstrap_warm()
    relpath, _sha = _write_handoff(cfg.root, "demo-P901")
    payload = _proposal_payload(artifacts=[_artifact(relpath, "0" * 64)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()


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
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()


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
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()


@pytest.mark.parametrize("bad_path", ["../outside.md", "/etc/passwd", "handoff/../../escape.md"])
def test_path_traversal_not_validated(tmp_state, carver_project, bad_path):
    cfg = carver_project
    _bootstrap_warm()
    payload = _proposal_payload(artifacts=[_artifact(bad_path, "0" * 64)])
    _record("demo", payload)

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()


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
    assert d._validated_carve_proposals("demo", cfg, snap, {}) == ()

    # The SAME artifact, correctly attributed to generation 1, DOES validate.
    fresh_payload = _proposal_payload(generation=1, turn_id="att-1",
                                       artifacts=[_artifact(relpath, sha)])
    _record("demo", fresh_payload)
    proposals = d._validated_carve_proposals("demo", cfg, snap, {})
    assert len(proposals) == 1
    assert proposals[0].proposal_id == "demo:carve:1:att-1"


def test_already_admitted_proposal_excluded(tmp_state, carver_project):
    """Once every artifact_id of a proposal already has a task in `states`,
    _validated_carve_proposals excludes it -- the structural 'proposal
    cursor consumed' signal this package uses in place of P4's
    CARVER_CONTEXT_CONSUMED ack-cursor (see its own docstring)."""
    cfg = carver_project
    _bootstrap_warm()
    relpath, sha = _write_handoff(cfg.root, "demo-P901")
    _record("demo", _proposal_payload(artifacts=[_artifact(relpath, sha)]))

    d = daemon.Daemon({"demo": cfg.root})
    snap = d._carver_session("demo", cfg)
    already_admitted_states = {
        "demo-P901": TaskStateFile(schema_version=1, task_id="demo-P901", project="demo",
                                    state=TaskState.CARVED, since=utc_now()),
    }
    assert d._validated_carve_proposals("demo", cfg, snap, already_admitted_states) == ()
    # Not yet admitted -> still validated.
    assert len(d._validated_carve_proposals("demo", cfg, snap, {})) == 1


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
    marker = [e for e in events if e.type is EventType.ARTIFACT_REGISTERED]
    assert len(marker) == 1
    assert marker[0].payload["kind"] == "carver-proposal-admitted"
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
    assert len(d._validated_carve_proposals("demo", cfg, snap, {})) == 1  # sanity: valid now

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
    states = {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    }
    snap = d._carver_session("demo", cfg)
    # Merely being validated does NOT touch the origin.
    assert len(d._validated_carve_proposals("demo", cfg, snap, states)) == 1
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
    states = {
        "demo-origin": TaskStateFile(schema_version=1, task_id="demo-origin", project="demo",
                                      state=TaskState.READY_TO_CARVE, since=utc_now()),
    }
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
    assert any(e.type is EventType.ARTIFACT_REGISTERED
               and e.payload.get("kind") == "carver-proposal-admitted" for e in events)
    assert storage.load_state("demo", "demo-P901").state is TaskState.CARVED


# ==========================================================================
# Byte-identical feature-off (structural, but asserted): with
# cfg.carve.session == "fresh" (default), _validated_carve_proposals is
# always (), so AdmitCarveProposal is never planned/executed -- a
# representative run_pass produces no ARTIFACT_REGISTERED carver-proposal-
# admitted marker and no TICK_ERROR, exactly matching
# test_carver_session_executor.py's own equivalent oracle for Start/Resume.
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
    assert not any(
        e.type is EventType.ARTIFACT_REGISTERED
        and e.payload.get("kind") == "carver-proposal-admitted"
        for e in events
    )
