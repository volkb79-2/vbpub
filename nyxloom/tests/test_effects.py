"""CR-05a: the effect boundary — ports, registry, and the rules over both.

Two kinds of test, for the same reason ``test_exception_census.py`` has two:

* ``TestThisRepo`` runs the structural rules against the REAL registry the
  daemon builds, so they bind the code that ships;
* the rest drive the registry and the ports over synthetic inputs, so every
  rule is seen to FAIL as well as pass. A registry oracle that has never
  rejected a duplicate handler is not known to reject one.
"""

from __future__ import annotations

import ast
import dataclasses
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from nyxloom import (
    daemon, effects, effects_gates, effects_lifecycle, paths, reconcile, storage,
)
from nyxloom.config import GateDef
from nyxloom.types import (
    Actor, ActorKind, Event, EventType, TaskState, TaskStateFile, utc_now,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "nyxloom"
EFFECTOR_MODULES = ("effects.py", "effects_gates.py", "effects_lifecycle.py")


# ---------------------------------------------------------------------------
# helpers


def _spec(**overrides):
    base = dict(
        action_type=reconcile.CreateTask, kind="probe",
        handler=lambda ctx, action: [], emits=frozenset(),
        idempotency_key=lambda a: "k",
    )
    base.update(overrides)
    return effects.HandlerSpec(**base)


class _FakeClock:
    def __init__(self) -> None:
        self.mono = 100.0

    def now(self):
        return utc_now()

    def monotonic(self) -> float:
        return self.mono


class _RecordingProcesses:
    """A duck-typed process port. Substitution by shape is the point: the
    ports are concrete classes, not Protocols, precisely so no unexecuted
    stub bodies end up on the surface this package exists to make visible."""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result or subprocess.CompletedProcess([], 0, "", "")

    def run(self, argv, *, cwd=None, timeout=None):
        self.calls.append(("run", tuple(argv), cwd, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def terminate(self, pid):
        self.calls.append(("terminate", pid))

    def terminate_group(self, pid):
        self.calls.append(("terminate_group", pid))


# ---------------------------------------------------------------------------
# the rules, against the registry this repo actually builds


class TestThisRepo:

    @pytest.fixture()
    def registry(self, tmp_state, sample_project):
        return daemon.Daemon({"demo": sample_project.root})._registry

    def test_every_planned_action_type_has_exactly_one_owner(self, registry):
        """The parent plan's CR-05 acceptance, stated mechanically."""
        owned = [s.action_type for s in registry.specs]
        assert len(owned) == len(set(owned)), "an action type has two owners"
        registry.require_covers(reconcile.Action.__subclasses__())

    def test_the_legacy_handler_count_matches_its_budget_exactly(self, registry):
        """Both directions, like the exception census. Over budget is new
        debt; UNDER budget is debt repaid without recording it, which leaves
        a number that quietly re-authorizes the next handler to stay put."""
        actual = len(registry.legacy_specs())
        assert actual == effects.LEGACY_HANDLER_BUDGET, (
            f"{actual} legacy handlers against a budget of "
            f"{effects.LEGACY_HANDLER_BUDGET}. Moving one LOWERS the budget in "
            "the same commit; nothing raises it.")

    def test_every_legacy_handler_names_the_package_that_owns_moving_it(
            self, registry):
        """CR-05e emptied the legacy set: every action type is owned by an
        effector module. The rule inverts rather than retires -- a legacy
        registration reappearing is a REGRESSION now, not debt, and this is
        what says so."""
        assert registry.legacy_specs() == (), (
            "a legacy handler reappeared: every action type has an effector "
            f"module owner. {[s.kind for s in registry.legacy_specs()]}")

    def test_a_handler_that_starts_background_work_declares_an_idempotency_key(
            self, registry):
        """The parent's "never a duplicate launch" acceptance needs an
        identity to be stated, not inferred from whichever dict the effector
        happens to key on."""
        for spec in registry.specs:
            if spec.starts_background_work:
                assert spec.idempotency_key is not None, spec.kind

    def test_every_declared_key_builder_produces_a_distinct_stable_identity(
            self, registry):
        """The builders are EXERCISED, not merely declared.

        A key must (a) be produced without raising for an action of its own
        type, (b) be stable for equal actions, and (c) name its own family --
        two different handlers whose keys collide would let one family's
        in-flight work suppress another's. CR-05a CONSUMES these keys only in
        the two background families; the rest declare them so CR-05b has an
        identity to enforce when it moves the launch and merge effects, and
        this test is what stops those declarations being decoration until
        then.
        """
        samples = {
            "create-task": reconcile.CreateTask(task_id="t1"),
            "transition": reconcile.Transition(task_id="t1",
                                               to=TaskState.QUEUED),
            "interrupt-attempt": reconcile.InterruptAttempt(task_id="t1",
                                                            attempt_id="a1"),
            "mark-interrupted": reconcile.MarkInterrupted(task_id="t1",
                                                          attempt_id="a1"),
            "mark-stalled": reconcile.MarkStalled(task_id="t1", attempt_id="a1"),
            "spec-attention": reconcile.SpecAttention(reason="ratchet"),
            "provider-pause": reconcile.ProviderPause(route_id="r1"),
            "post-merge-gate": reconcile.RunPostMergeGate(task_id="t1"),
            "launch-review": reconcile.LaunchReview(wave_id="w1",
                                                    task_ids=["t1"]),
            "launch-gate-diagnosis": reconcile.LaunchGateDiagnosis(task_id="t1"),
            "auto-merge": reconcile.AutoMergeTask(task_id="t1"),
            "dispatch-implementer": reconcile.DispatchImplementer(
                task_id="t1", route_id="r1"),
            "resume-attempt": reconcile.ResumeAttempt(task_id="t1",
                                                      attempt_id="a1"),
            "launch-self-review": reconcile.LaunchSelfReview(
                task_id="t1", source_attempt_id="a1"),
            "start-carver-session": reconcile.StartCarverSession(
                project="demo", mode="headroom"),
            "resume-carver-session": reconcile.ResumeCarverSession(
                project="demo", mode="merge-feed", source_ids=("d1",),
                generation=1),
            "compact-carver-session": reconcile.CompactCarverSession(
                project="demo", generation=1, trigger="turns"),
            "carve-dispatch": reconcile.CarveDispatch(project="demo",
                                                      kind="headroom"),
            "admit-carve-proposal": reconcile.AdmitCarveProposal(
                project="demo", proposal_id="p1"),
            "emit-attempt-exit": reconcile.EmitAttemptExit(task_id="t1",
                                                           attempt_id="a1"),
        }
        keys = {}
        for spec in registry.specs:
            if spec.legacy_owner or spec.idempotency_key is None:
                continue
            action = samples[spec.kind]
            key = spec.idempotency_key(action)
            assert isinstance(key, str) and key, spec.kind
            assert key == spec.idempotency_key(samples[spec.kind]), (
                f"{spec.kind}: key is not stable for an equal action")
            assert key.startswith(spec.kind), (
                f"{spec.kind}: key {key!r} does not name its own family, so it "
                "could collide with another handler's")
            keys[spec.kind] = key
        assert len(set(keys.values())) == len(keys), (
            f"two handlers produce the same identity: {keys}")

    def test_a_transition_key_distinguishes_the_target_state(self, registry):
        """Negative control for the rule above: a key that ignored `to` would
        make two different edges on one task look like the same effect."""
        spec = next(s for s in registry.specs if s.kind == "transition")
        queued = spec.idempotency_key(
            reconcile.Transition(task_id="t1", to=TaskState.QUEUED))
        blocked = spec.idempotency_key(
            reconcile.Transition(task_id="t1", to=TaskState.BLOCKED))
        assert queued != blocked

    def test_the_legacy_ladder_matches_the_legacy_registrations(self, registry):
        """The ladder and the registry must name the SAME action types.

        A branch for a type that is no longer registered legacy is DEAD --
        the registry routes that action to its effector and the branch can
        never run -- and dead code that calls a method the same package
        deleted is a landmine rather than clutter. CR-05d shipped exactly
        that: three branches survived the move because deleting the METHODS
        and the REGISTRATIONS looked like the whole job, and nothing tied the
        two together. This is what ties them.
        """
        source = (SRC / "daemon.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        legacy_fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_execute_legacy"),
            None)
        if legacy_fn is None:
            # CR-05e deleted the ladder with the last family. The rule still
            # binds: no ladder means the registry must declare no legacy.
            assert not registry.legacy_specs(), (
                "the ladder is gone but the registry still declares legacy "
                f"handlers: {[s.kind for s in registry.legacy_specs()]}")
            return
        in_ladder = set()
        for node in ast.walk(legacy_fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "isinstance"
                    and len(node.args) == 2
                    and isinstance(node.args[1], ast.Attribute)):
                in_ladder.add(node.args[1].attr)
        registered = {s.action_type.__name__ for s in registry.legacy_specs()}
        assert in_ladder == registered, (
            f"the legacy ladder branches on {sorted(in_ladder)} but the "
            f"registry declares {sorted(registered)} legacy. A branch with no "
            "registration is unreachable dead code; a registration with no "
            "branch reaches a ladder that cannot handle it.")

    def test_no_effector_module_can_reach_the_daemon(self):
        """The amendment's §3.3 rule made structural. Six modules that all
        reach back into one god object would pass every behavioural test and
        deliver nothing, so the check is on the import graph, not on
        behaviour."""
        offenders = []
        for name in EFFECTOR_MODULES:
            tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("daemon"):
                    offenders.append(f"{name}: imports {node.module}")
                if isinstance(node, ast.Import):
                    offenders.extend(f"{name}: imports {a.name}" for a in node.names
                                     if a.name.endswith("daemon"))
                if isinstance(node, ast.Name) and node.id == "Daemon":
                    offenders.append(f"{name}: names Daemon")
                if isinstance(node, ast.Attribute) and node.attr == "Daemon":
                    offenders.append(f"{name}: names .Daemon")
        assert not offenders, (
            "an effector module reaches the shell: " + "; ".join(offenders))

    def test_declared_emissions_match_what_each_handler_can_actually_emit(
            self, registry):
        """`emits` is derived from the code, not trusted.

        Walk each non-legacy handler's own module for the ``EventType``
        members its call graph can reach, and require the declaration to be
        exactly that set. A handler that starts appending a new event type
        fails until it says so, and a declaration listing an event the code
        cannot emit fails too -- an over-broad permission is as much a drift
        as a missing one.
        """
        mismatches = []
        for spec in registry.specs:
            if spec.legacy_owner:
                continue
            actual = _derive_emissions(spec)
            if actual != spec.emits:
                mismatches.append(
                    f"{spec.kind}: declared {sorted(e.name for e in spec.emits)}, "
                    f"code can emit {sorted(e.name for e in actual)}")
        assert not mismatches, "\n".join(mismatches)


def _function_nodes(module_file: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)}


def _derive_emissions(spec) -> frozenset:
    """EventType members the spec's entry points can APPEND.

    The call graph is WALKED, not listed: starting from the handler (and the
    drain, when the family has one), every ``self.<name>`` the source mentions
    is followed, whether it is called there or handed to the background port
    to be called later. A hand-maintained root list would let a new helper
    emit a new event type without the declaration noticing.

    Only APPEND sites count -- an ``EventType`` member reached inside an
    append call, plus ``ctx.transition(...)`` for TASK_TRANSITIONED. CR-05d is
    why: the carver's proposal validators READ CARVER_PROPOSAL_RECORDED to
    decide whether a proposal was already admitted, and counting a comparison
    as an emission would have forced the launch verbs to declare an event
    they cannot produce. A declaration that names events the handler only
    reads is not a permission -- it is noise that makes the real permission
    unreadable.
    """
    module = sys.modules[spec.handler.__module__]
    functions = _function_nodes(Path(module.__file__))
    pending = [spec.handler.__name__]
    if spec.drain is not None:
        pending.append(spec.drain.__name__)
    seen: set[str] = set()
    found: set[EventType] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Attribute):
                if (isinstance(node.value, ast.Name)
                        and node.value.id == "self"):
                    pending.append(node.attr)
                continue
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr == "transition" and not (
                    isinstance(func.value, ast.Name) and func.value.id == "self"):
                found.add(EventType.TASK_TRANSITIONED)
            if func.attr not in ("append", "_append_ev"):
                continue
            for arg in ast.walk(node):
                if (isinstance(arg, ast.Attribute)
                        and isinstance(arg.value, ast.Name)
                        and arg.value.id == "EventType"):
                    found.add(getattr(EventType, arg.attr))
    return frozenset(found)


def test_the_emissions_oracle_rejects_a_declaration_the_code_contradicts(
        tmp_state, sample_project):
    """Negative control for the rule above.

    Narrow one handler's declaration to a strict subset of what its code can
    emit and require the derivation to disagree -- otherwise a walk that
    silently found nothing would let every declaration pass.
    """
    ports = effects.EffectPorts.system()
    effector = effects_lifecycle.LifecycleEffector(
        ports, effects.ProviderPauseRegistry(ports.clock))
    spec = next(s for s in effects_lifecycle.specs(effector)
                if s.kind == "transition")
    assert _derive_emissions(spec) == spec.emits
    narrowed = dataclasses.replace(
        spec, emits=frozenset({EventType.TASK_TRANSITIONED}))
    assert _derive_emissions(narrowed) != narrowed.emits
    widened = dataclasses.replace(
        spec, emits=spec.emits | {EventType.MERGE_RECORDED})
    assert _derive_emissions(widened) != widened.emits


# ---------------------------------------------------------------------------
# the registry: every rule seen to fail


class TestRegistry:

    def test_a_second_handler_for_one_action_type_is_refused(self):
        registry = effects.EffectRegistry()
        registry.register(_spec(kind="first"))
        with pytest.raises(effects.DuplicateHandler) as exc:
            registry.register(_spec(kind="second"))
        assert "first" in str(exc.value) and "second" in str(exc.value)

    def test_an_action_with_no_handler_is_refused_at_lookup(self):
        registry = effects.EffectRegistry()
        with pytest.raises(effects.UnownedAction):
            registry.spec_for(reconcile.OpenWave(task_ids=[]))

    def test_require_covers_names_every_unowned_type(self):
        registry = effects.EffectRegistry()
        registry.register(_spec())
        with pytest.raises(effects.UnownedAction) as exc:
            registry.require_covers([reconcile.CreateTask, reconcile.OpenWave,
                                     reconcile.StallCheck])
        assert "OpenWave" in str(exc.value) and "StallCheck" in str(exc.value)
        assert "CreateTask" not in str(exc.value)

    def test_require_covers_passes_when_every_type_is_owned(self):
        registry = effects.EffectRegistry()
        registry.register(_spec())
        registry.require_covers([reconcile.CreateTask])   # does not raise

    def test_background_work_without_an_idempotency_key_is_rejected(self):
        with pytest.raises(ValueError, match="idempotency key"):
            _spec(starts_background_work=True, idempotency_key=None)

    def test_a_non_legacy_handler_must_declare_its_emissions(self):
        with pytest.raises(ValueError, match="event types it emits"):
            _spec(emits=None)

    def test_a_legacy_handler_may_leave_its_emissions_undeclared(self):
        """The honest answer while the handler is still a branch of the
        shell's ladder: its call graph is the shell's, so any set written
        here would be a claim nothing checks."""
        spec = _spec(emits=None, legacy_owner="CR-05b")
        assert spec.emits is None

    def test_drains_run_in_registration_order(self):
        order: list[str] = []
        registry = effects.EffectRegistry()
        registry.register(_spec(action_type=reconcile.CreateTask, kind="a",
                                drain=lambda ctx: order.append("a") or []))
        registry.register(_spec(action_type=reconcile.OpenWave, kind="b",
                                drain=lambda ctx: order.append("b") or []))
        registry.register(_spec(action_type=reconcile.StallCheck, kind="c"))
        assert registry.drain(None) == []
        assert order == ["a", "b"]

    def test_execute_routes_to_the_registered_handler_with_the_key_resolved(self):
        """The registry resolves the declared identity BEFORE invoking, so an
        effector reads `ctx.idempotency_key` rather than choosing a key of its
        own -- which is what stops the declared identity and the enforced one
        from drifting apart."""
        seen = []
        registry = effects.EffectRegistry()
        registry.register(_spec(
            idempotency_key=lambda a: f"probe:{a.task_id}",
            handler=lambda ctx, action: seen.append(
                (action, ctx.idempotency_key)) or []))
        ctx = effects.EffectContext(project="demo", cfg=None, states={},
                                    ports=None)
        action = reconcile.CreateTask(task_id="t1")
        assert registry.execute(ctx, action) == []
        assert seen == [(action, "probe:t1")]

    def test_a_handler_with_no_declared_key_sees_none(self):
        seen = []
        registry = effects.EffectRegistry()
        registry.register(_spec(idempotency_key=None,
                                handler=lambda ctx, action: seen.append(
                                    ctx.idempotency_key) or []))
        ctx = effects.EffectContext(project="demo", cfg=None, states={},
                                    ports=None)
        registry.execute(ctx, reconcile.CreateTask(task_id="t1"))
        assert seen == [None]


# ---------------------------------------------------------------------------
# the ports


class TestPorts:

    def test_the_clock_reports_wall_and_monotonic_time(self):
        clock = effects.SystemClock()
        assert clock.now().tzinfo is not None
        assert clock.monotonic() <= clock.monotonic()

    def test_processes_run_captures_output_and_decodes_it(self):
        res = effects.SystemProcesses().run(["printf", "hi"])
        assert res.returncode == 0 and res.stdout == "hi"

    def test_processes_run_lets_a_timeout_propagate(self):
        """The port must not decide what a timeout MEANS: a gate timeout is
        exit 124 and a missing binary is exit 127, and only the caller knows
        which convention applies."""
        with pytest.raises(subprocess.TimeoutExpired):
            effects.SystemProcesses().run(["sleep", "5"], timeout=0.05)

    def test_processes_run_lets_a_missing_executable_propagate(self):
        with pytest.raises(OSError):
            effects.SystemProcesses().run(["definitely-not-a-real-binary-xyz"])

    def test_terminate_and_terminate_group_signal_the_right_target(self,
                                                                   monkeypatch):
        calls = []
        monkeypatch.setattr(effects.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
        monkeypatch.setattr(effects.os, "getpgid", lambda pid: pid + 1000)
        monkeypatch.setattr(effects.os, "killpg", lambda pgid, sig: calls.append(("killpg", pgid, sig)))
        ports = effects.SystemProcesses()
        ports.terminate(4242)
        ports.terminate_group(4242)
        assert calls == [("kill", 4242, effects.signal.SIGTERM),
                         ("killpg", 5242, effects.signal.SIGTERM)]

    def test_filesystem_reads_are_the_only_thing_the_port_offers(self, tmp_path):
        """Read-only on purpose: a port that cannot write is one fewer way
        for an effector to acquire durable authority outside the journal."""
        target = tmp_path / "f.txt"
        fs = effects.SystemFilesystem()
        assert fs.exists(target) is False
        target.write_text("body", encoding="utf-8")
        assert fs.exists(target) is True and fs.read_text(target) == "body"
        assert not hasattr(fs, "write_text")

    def test_background_spawn_runs_the_target_and_reports_liveness(self):
        done = threading.Event()
        bg = effects.ThreadBackground()
        handle = bg.spawn("probe", lambda flag: flag.set(), (done,))
        assert done.wait(timeout=5)
        handle.join(timeout=5)
        assert bg.is_running(handle) is False
        assert bg.is_running(None) is False

    def test_background_reports_a_live_worker_as_running(self):
        release = threading.Event()
        bg = effects.ThreadBackground()
        handle = bg.spawn("probe", lambda flag: flag.wait(timeout=5), (release,))
        try:
            assert bg.is_running(handle) is True
        finally:
            release.set()
            handle.join(timeout=5)


class TestGitPort:

    def _git(self, result=None):
        processes = _RecordingProcesses(result)
        return effects.SystemGit(processes), processes

    def test_every_call_is_scoped_to_the_repo_it_was_given(self):
        git, processes = self._git()
        git.run("/repo", "status")
        assert processes.calls[0][1] == ("git", "-C", "/repo", "status")

    def test_top_level_propagates_a_failure_to_exec_git(self):
        """Deliberately NOT total. The post-merge probe runs on a background
        thread whose death is self-healing, whereas substituting an empty
        root would silently gate the operator's live checkout instead of the
        merge commit."""
        git, _ = self._git(OSError("no git"))
        with pytest.raises(OSError):
            git.top_level("/repo")

    def test_top_level_or_falls_back_on_a_timeout(self):
        git, _ = self._git(subprocess.TimeoutExpired(["git"], 15))
        assert git.top_level_or("/repo", "/fallback", timeout=15) == "/fallback"

    def test_top_level_or_falls_back_on_a_non_zero_exit(self):
        git, _ = self._git(subprocess.CompletedProcess([], 128, "", "not a repo"))
        assert git.top_level_or("/repo", "/fallback", timeout=15) == "/fallback"

    def test_top_level_or_returns_the_resolved_root_when_git_answers(self):
        git, _ = self._git(subprocess.CompletedProcess([], 0, "/real/root\n", ""))
        assert git.top_level_or("/repo", "/fallback", timeout=15) == "/real/root"

    def test_head_commit_is_empty_when_git_refuses(self):
        git, _ = self._git(subprocess.CompletedProcess([], 128, "", "err"))
        assert git.head_commit("/repo") == ""

    def test_head_commit_returns_the_sha(self):
        git, _ = self._git(subprocess.CompletedProcess([], 0, "abc123\n", ""))
        assert git.head_commit("/repo") == "abc123"

    def test_rev_parse_is_quiet_about_an_unresolvable_rev(self):
        git, processes = self._git(subprocess.CompletedProcess([], 1, "", ""))
        assert git.rev_parse("/repo", "deadbeef^1") == ""
        assert "--quiet" in processes.calls[0][1]

    def test_worktree_add_returns_the_whole_result_including_stderr(self):
        """One caller only needs to know it failed; another puts git's own
        stderr into the operator escalation, and "scratch worktree setup
        failed" with no reason is a page nobody can act on."""
        git, _ = self._git(subprocess.CompletedProcess([], 0, "", ""))
        assert git.worktree_add_detached("/repo", "/scratch", "abc").returncode == 0
        git, _ = self._git(subprocess.CompletedProcess([], 128, "", "already exists"))
        refused = git.worktree_add_detached("/repo", "/scratch", "abc")
        assert refused.returncode == 128 and refused.stderr == "already exists"

    def test_worktree_remove_is_forced_and_best_effort(self):
        git, processes = self._git()
        git.worktree_remove("/repo", "/scratch")
        assert processes.calls[0][1][-3:] == ("remove", "--force", "/scratch")

    def test_changed_paths_reports_nothing_when_git_refuses(self):
        """An empty list is REAL zero-progress to the caller, so the refusal
        case must not be distinguishable by accident -- it degrades to the
        same answer deliberately, and the caller's own guard is what keeps a
        merge from being undone by a progress signal that could not run."""
        git, _ = self._git(subprocess.CompletedProcess([], 128, "", "bad object"))
        assert git.changed_paths("/repo", "deadbeef") == []

    def test_changed_paths_lists_the_files_a_commit_touched(self):
        git, processes = self._git(
            subprocess.CompletedProcess([], 0, "a.py\n\n b.py \n", ""))
        assert git.changed_paths("/repo", "c1") == ["a.py", "b.py"]
        assert "diff-tree" in processes.calls[0][1]

    def test_update_ref_is_a_compare_and_swap_and_returns_the_refusal(self):
        """The CAS operand is what makes an auto-revert safe, and the WHOLE
        result comes back because the caller reports a refusal rather than
        only detecting it."""
        refusal = subprocess.CompletedProcess([], 1, "", "ref changed")
        git, processes = self._git(refusal)
        res = git.update_ref("/repo", "refs/heads/main", "parent", "merge")
        assert processes.calls[0][1][-4:] == ("update-ref", "refs/heads/main",
                                              "parent", "merge")
        assert res.returncode == 1 and res.stderr == "ref changed"


class TestJournal:

    def test_a_dead_notification_transport_cannot_unrecord_an_event(
            self, tmp_state, sample_project, monkeypatch):
        """The event is already committed by the time notification runs, so
        containment here cannot turn a recorded effect into an unrecorded
        one -- and a broken ntfy must not stop a reconcile pass."""
        def boom(cfg, states, ev):
            raise RuntimeError("transport down")

        monkeypatch.setattr(effects.notify, "notify_event", boom)
        journal = effects.StoreJournal()
        ev = journal.append("demo", sample_project, {},
                            EventType.SPEC_ATTENTION, {"reason": "ratchet"})
        assert ev.type is EventType.SPEC_ATTENTION
        assert [e.type for e in storage.iter_events("demo")] == [
            EventType.SPEC_ATTENTION]

    def test_transition_logs_once_and_appends_the_typed_edge(
            self, tmp_state, sample_project):
        journal = effects.StoreJournal()
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                            task_id="t1", project="demo",
                            state=TaskState.CARVED, since=utc_now())
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.TASK_CREATED,
                                 payload={"statefile": tsf.to_dict()},
                                 task_id="t1")
        states = storage.list_states("demo")
        ev = journal.transition("demo", sample_project, states, "t1",
                                TaskState.QUEUED, "go")
        assert ev.type is EventType.TASK_TRANSITIONED
        assert ev.payload == {"from": "CARVED", "to": "QUEUED", "notes": "go"}


class TestEventLogPort:

    def test_an_unreadable_log_raises_rather_than_reading_as_empty(
            self, tmp_state, sample_project, monkeypatch):
        """"Nothing escalated yet" from an unreadable log is precisely the
        answer that reproduces the notification storm the debounce exists to
        stop, so the acquisition is AUTHORITATIVE."""
        monkeypatch.setattr(storage, "iter_events",
                            lambda project: (_ for _ in ()).throw(OSError("gone")))
        with pytest.raises(effects.snapshot.SnapshotUnavailable):
            effects.StoreEventLog().require("demo")


class TestProviderPauseRegistry:

    def test_a_pause_expires_from_the_clock_it_was_given(self):
        clock = _FakeClock()
        registry = effects.ProviderPauseRegistry(clock)
        assert registry.paused_until("r1") is None
        registry.pause("r1", 60)
        assert registry.paused_until("r1") == 160.0

    def test_the_duration_is_read_at_pause_time_not_at_construction(self):
        """So the tunable stays a module constant a test can shrink, rather
        than a value frozen into the object when the daemon was built."""
        clock = _FakeClock()
        registry = effects.ProviderPauseRegistry(clock)
        registry.pause("r1", 10)
        registry.pause("r2", 3600)
        assert registry.paused_until("r1") == 110.0
        assert registry.paused_until("r2") == 3700.0


# ---------------------------------------------------------------------------
# the shared debounce predicates


def _ev(kind: EventType, **payload):
    return Event(schema_version=storage.SCHEMA_VERSION, sequence=0,
                 timestamp=utc_now(), project="demo",
                 actor=Actor(ActorKind.TICK, "t"), type=kind, payload=payload)


class TestDebouncePredicates:

    def test_spec_attention_is_debounced_only_within_the_recent_window(self):
        matching = _ev(EventType.SPEC_ATTENTION, reason="ratchet")
        filler = [_ev(EventType.TICK_ERROR, error="x") for _ in range(500)]
        assert effects.spec_attention_recently_emitted([matching], "ratchet")
        assert not effects.spec_attention_recently_emitted([matching] + filler,
                                                           "ratchet")

    def test_spec_attention_distinguishes_reasons(self):
        events = [_ev(EventType.SPEC_ATTENTION, reason="ratchet")]
        assert not effects.spec_attention_recently_emitted(events, "rejections")

    def test_needs_operator_is_open_until_a_session_marker_clears_it(self):
        opened = [_ev(EventType.NEEDS_OPERATOR, reason="gate-verify-broken")]
        assert effects.needs_operator_recently_emitted(opened, "gate-verify-broken")
        cleared = opened + [_ev(EventType.CARVER_SESSION_ROTATED)]
        assert not effects.needs_operator_recently_emitted(
            cleared, "gate-verify-broken")
        reopened = cleared + [_ev(EventType.NEEDS_OPERATOR,
                                  reason="gate-verify-broken")]
        assert effects.needs_operator_recently_emitted(
            reopened, "gate-verify-broken")

    def test_a_session_start_also_clears_the_episode(self):
        events = [_ev(EventType.NEEDS_OPERATOR, reason="r"),
                  _ev(EventType.CARVER_SESSION_STARTED)]
        assert not effects.needs_operator_recently_emitted(events, "r")


# ---------------------------------------------------------------------------
# effector behaviour the moved tests do not already cover


class TestGateEffectorQueues:

    def test_a_result_for_another_project_is_returned_to_the_queue(self):
        """One queue serves every registered project, so a foreign result
        must go back rather than be discarded -- the tick loop reaches that
        project in the same pass or the next one."""
        q: queue.Queue = queue.Queue()
        q.put({"project": "other", "verdict": "TRUSTWORTHY"})
        q.put({"project": "demo", "verdict": "BROKEN"})
        mine = effects_gates.GateEffector._take(q, "demo")
        assert mine == [{"project": "demo", "verdict": "BROKEN"}]
        assert q.get_nowait() == {"project": "other", "verdict": "TRUSTWORTHY"}
        assert q.empty()

    def test_the_post_merge_worktree_value_falls_back_to_the_project_root(
            self, tmp_state, sample_project):
        """A non-git fixture is an expected case here, not a fault: the value
        is a {worktree} substitution whose only reasonable default is the
        project root."""
        ports = effects.EffectPorts.system()
        effector = effects_gates.GateEffector(ports)
        cfg = dataclasses.replace(sample_project, root=Path("/nonexistent/xyz"))
        assert effector.post_merge_worktree_value(cfg) == "/nonexistent/xyz"

    def test_a_leftover_scratch_worktree_is_removed_before_the_gate_runs(
            self, tmp_state, sample_project, monkeypatch):
        """A previous run that died mid-gate leaves its scratch worktree
        behind. Adding over it fails, which would silently fall back to
        gating the operator's LIVE checkout -- the exact wrong-tree verdict
        the scratch worktree exists to prevent. Remove first, then add."""
        calls: list[tuple] = []

        class _Git:
            def top_level(self, root):
                return "/repo"

            def worktree_remove(self, root, path):
                calls.append(("remove", path))

            def worktree_add_detached(self, root, path, commit):
                calls.append(("add", path, commit))
                return subprocess.CompletedProcess([], 0, "", "")

        class _Files:
            def exists(self, path):
                return True      # the leftover is there

        ports = dataclasses.replace(effects.EffectPorts.system(),
                                    git=_Git(), files=_Files(),
                                    processes=_RecordingProcesses())
        effector = effects_gates.GateEffector(ports)
        gate = GateDef(gate_id="ok", argv=["true"], phase="post-merge",
                       timeout_seconds=30, environment="local")
        cfg = dataclasses.replace(sample_project, gates={"ok": gate})
        effector._run_post_merge_probe("demo", cfg, "t1", "abc123",
                                       "post-merge-gate:t1")
        scratch = "/repo/.worktrees/postmerge-t1"
        assert calls[0] == ("remove", scratch), "the leftover was not cleared first"
        assert calls[1] == ("add", scratch, "abc123")
        assert calls[-1] == ("remove", scratch), "the scratch was not cleaned up"
        assert effector.post_merge_results.get_nowait()["outcome"] == "completed"

    def test_the_drain_clears_exactly_the_entry_the_dispatcher_registered(
            self, tmp_state, sample_project):
        """The dispatcher's key travels with the result, so the drain does
        not derive the identity a second time -- two derivations that could
        disagree is how a family ends up unable to start its next run."""
        effector = effects_gates.GateEffector(effects.EffectPorts.system())
        effector.post_merge_running["post-merge-gate:t1"] = None
        effector.post_merge_results.put({
            "project": "demo", "task_id": "t1", "key": "post-merge-gate:t1",
            "gate_id": None, "gate_result": None, "outcome": "no_gate"})
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t1",
                            project="demo", state=TaskState.VALIDATING,
                            since=utc_now())
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.TASK_CREATED,
                                 payload={"statefile": tsf.to_dict()},
                                 task_id="t1")
        states = storage.list_states("demo")
        states["t1"].state = TaskState.VALIDATING
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states=states,
                                    ports=effects.EffectPorts.system())
        effector.drain_post_merge(ctx)
        assert effector.post_merge_running == {}

    def test_a_gate_binary_that_cannot_be_executed_records_exit_127(
            self, tmp_state, sample_project, monkeypatch):
        """127 is the shell's command-not-found convention, and it has to be
        distinguishable from a genuine test failure -- an operator repairs
        them differently."""
        gate = GateDef(gate_id="missing", argv=["definitely-not-a-real-binary-xyz"],
                       phase="post-merge", timeout_seconds=30, environment="local")
        cfg = dataclasses.replace(sample_project, gates={"missing": gate})
        effector = effects_gates.GateEffector(effects.EffectPorts.system())
        effector._run_post_merge_probe("demo", cfg, "t1", "", "post-merge-gate:t1")
        result = effector.post_merge_results.get_nowait()
        assert result["outcome"] == "blocked"
        assert result["gate_result"]["exit_code"] == 127


class TestLifecycleEffector:

    def test_a_provider_pause_action_pauses_the_route_and_escalates(
            self, tmp_state, sample_project):
        """The ProviderPause ACTION path, distinct from the LIMIT-receipt
        callers that reach the same helper: before CR-05a this branch of the
        ladder had no test at all."""
        ports = effects.EffectPorts.system()
        backoff = effects.ProviderPauseRegistry(ports.clock)
        effector = effects_lifecycle.LifecycleEffector(ports, backoff)
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states={}, ports=ports)
        events = effector.provider_pause_action(
            ctx, reconcile.ProviderPause(task_id=None, route_id="r1"))
        assert [e.type for e in events] == [EventType.PROVIDER_STATE_CHANGED,
                                            EventType.NEEDS_OPERATOR]
        assert backoff.paused_until("r1") is not None

    def test_a_pause_with_no_route_still_escalates_but_backs_nothing_off(
            self, tmp_state, sample_project):
        ports = effects.EffectPorts.system()
        backoff = effects.ProviderPauseRegistry(ports.clock)
        effector = effects_lifecycle.LifecycleEffector(ports, backoff)
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states={}, ports=ports)
        events = effector.pause_provider(ctx, None, None)
        assert len(events) == 2
        assert backoff.paused_until(None) is None

    def test_interrupt_signals_the_wrapper_and_not_the_child(
            self, tmp_state, sample_project, monkeypatch):
        """Signal the WRAPPER first: its handler forwards SIGTERM to the
        child's group AND classifies the exit as 'interrupted'. Signalling
        the child directly makes the wrapper report 'error' instead, and the
        confirmed-stall pipeline then retries instead of reaching
        INTERRUPTED."""
        attempt_dir = paths.attempt_dir("demo", "att-1")
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "wrapper.pid").write_text("321", encoding="utf-8")
        (attempt_dir / "child.pid").write_text("654", encoding="utf-8")
        processes = _RecordingProcesses()
        ports = dataclasses.replace(effects.EffectPorts.system(),
                                    processes=processes)
        effector = effects_lifecycle.LifecycleEffector(
            ports, effects.ProviderPauseRegistry(ports.clock))
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id="t1",
                            project="demo", state=TaskState.ACTIVE,
                            since=utc_now())
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states={"t1": tsf}, ports=ports)
        events = effector.interrupt_attempt(
            ctx, reconcile.InterruptAttempt(task_id="t1", attempt_id="att-1"))
        assert events == []      # the wrapper emits ATTEMPT_INTERRUPTED itself
        assert processes.calls == [("terminate", 321)]

    def test_an_interrupt_for_an_unknown_task_is_a_planning_defect(
            self, tmp_state, sample_project):
        """A premise check, not a read: raising surfaces it as a TICK_ERROR
        rather than silently signalling nothing."""
        ports = effects.EffectPorts.system()
        effector = effects_lifecycle.LifecycleEffector(
            ports, effects.ProviderPauseRegistry(ports.clock))
        ctx = effects.EffectContext(project="demo", cfg=sample_project,
                                    states={}, ports=ports)
        with pytest.raises(KeyError):
            effector.interrupt_attempt(
                ctx, reconcile.InterruptAttempt(task_id="ghost",
                                                attempt_id="att-x"))
