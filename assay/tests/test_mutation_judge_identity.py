"""B088 -- a resume record's identity now includes what JUDGED the mutant.

`candidate_id` folds the mutation: path, source bytes, byte span,
replacement, operator. Nothing in it depended on the test command or the
content of the test files that command collects, so a mutant's source bytes
were "the same mutant" whether or not the suite judging them had just gained
the assertion that kills them. `--resume` therefore replayed the prior
`survived` verdict for a candidate a real, test-only fix had just killed --
measured twice in one week, in two repositories (dstdns
`worker-execution-admission-r2-flips`, 2026-09-09; vbpub nyxloom
`session-extract`, 2026-09-10).

This file measures the fix on three levels:

* `isolation._manifest_sha256` -- the content digest of the judged tree;
* `mutation.judge_sha256` -- that digest combined with the resolved command;
* two REAL repositories running the REAL CLI, where a strengthened test file
  genuinely flips a candidate from `survived` to `killed` across a
  `--resume`, and where an unchanged suite still resumes exactly as B066
  documents.

Boundary values are named rather than assumed (nyxloom
`reference/TESTING-METHODOLOGY.md`, "Definition of done"): zero entries, one
entry, identical content at a different path, content-identical-but-touched,
an empty argv, an empty environment, and a lane whose argv names no path at
all.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest
from conftest import GitRepo, make_lane, make_plan

from assay import isolation, mutation
from assay.cli import main
from assay.mutation import MutationStateError, judge_sha256


# --- the tree digest ----------------------------------------------------------


def _entry(path: str, oid: str, *, mode: str | None = None, target: str | None = None):
    return isolation._Entry(
        path=PurePosixPath(path),
        mode=isolation._MODE_REGULAR if mode is None else mode,
        oid=oid,
        size=1,
        target=target,
    )


def _manifest(*entries, omitted: tuple[str, ...] = ()):
    return isolation._Manifest(
        entries=tuple(entries),
        directories=(),
        omitted=tuple(PurePosixPath(path) for path in omitted),
    )


def test_an_empty_tree_digests_to_a_stable_value():
    """Boundary: ZERO entries. A degenerate manifest must still produce one
    stable digest rather than an empty string, a crash, or a value that
    happens to equal some other manifest's."""
    first = isolation._manifest_sha256(_manifest())
    assert first == isolation._manifest_sha256(_manifest())
    assert len(first) == 64
    assert first != isolation._manifest_sha256(_manifest(_entry("a.py", "a" * 40)))


def test_a_single_entry_tree_digests_to_a_stable_value():
    """Boundary: ONE entry -- the smallest non-degenerate suite, and the
    shape every "did anything change" comparison below is built on."""
    digest = isolation._manifest_sha256(_manifest(_entry("tests/one.py", "a" * 40)))
    assert digest == isolation._manifest_sha256(
        _manifest(_entry("tests/one.py", "a" * 40))
    )


def test_changed_file_content_changes_the_tree_digest():
    """The B088 case in miniature: same path, different bytes (a different
    Git object id IS different bytes) -- a different judge."""
    assert isolation._manifest_sha256(
        _manifest(_entry("tests/t.py", "a" * 40))
    ) != isolation._manifest_sha256(_manifest(_entry("tests/t.py", "b" * 40)))


def test_identical_content_at_a_different_path_is_a_different_tree():
    """DECIDED AND DOCUMENTED, not left implicit: moving a file with its
    bytes intact is a DIFFERENT judging suite.

    The test runner collects, imports and names tests BY PATH -- a
    `conftest.py` applies to its own directory and nowhere else, a pytest
    node id leads with the file path, and `-k`/`--deselect` match spellings.
    A file whose bytes are unchanged but whose path moved can therefore judge
    differently, so it must not be treated as the same judge. The opposite
    choice (content-only, path-blind) would be unsound in exactly the
    direction this whole item exists to close: it would keep trusting a
    verdict after a `git mv` that changed which tests run.
    """
    assert isolation._manifest_sha256(
        _manifest(_entry("tests/a.py", "a" * 40))
    ) != isolation._manifest_sha256(_manifest(_entry("tests/b.py", "a" * 40)))


def test_a_changed_file_mode_changes_the_tree_digest():
    """An executable bit is behaviour, not decoration: a judge script that
    stops being executable judges differently."""
    assert isolation._manifest_sha256(
        _manifest(_entry("tests/run.sh", "a" * 40))
    ) != isolation._manifest_sha256(
        _manifest(_entry("tests/run.sh", "a" * 40, mode=isolation._MODE_EXECUTABLE))
    )


def test_a_changed_symlink_target_changes_the_tree_digest():
    assert isolation._manifest_sha256(
        _manifest(_entry("tests/link", "a" * 40, mode=isolation._MODE_SYMLINK, target="x"))
    ) != isolation._manifest_sha256(
        _manifest(_entry("tests/link", "a" * 40, mode=isolation._MODE_SYMLINK, target="y"))
    )


def test_declared_omissions_participate_in_the_tree_digest():
    """An omitted leaf is absent from the materialized worktree, so the set
    of omissions is part of what the suite actually sees."""
    entries = (_entry("tests/t.py", "a" * 40),)
    assert isolation._manifest_sha256(
        _manifest(*entries)
    ) != isolation._manifest_sha256(_manifest(*entries, omitted=("danger/link",)))


def test_entry_order_does_not_reach_the_tree_digest():
    """A property, stated over a real permutation rather than asserted: the
    digest answers "what is in this tree", never "in what order did the walk
    happen to yield it"."""
    entries = [
        _entry("z.py", "c" * 40),
        _entry("a.py", "a" * 40),
        _entry("m/n.py", "b" * 40),
    ]
    forward = isolation._manifest_sha256(_manifest(*entries))
    assert forward == isolation._manifest_sha256(_manifest(*reversed(entries)))
    assert forward == isolation._manifest_sha256(_manifest(entries[1], entries[2], entries[0]))


def test_the_tree_serialization_cannot_be_forged_by_field_injection():
    """Injectivity, attacked rather than assumed.

    The serialization joins fields with `\\0`. Git itself forbids `\\0` in a
    path, so this is safe in practice -- but a hand-built manifest can still
    put one there, and if the joiner were the only thing separating fields, a
    crafted path could impersonate a different manifest entirely. Fixed
    four-field arity plus length-prefixed sections is what makes that
    impossible; this is the test that would fail if either were dropped.
    """
    crafted = _manifest(
        _entry("a.py\x00100644\x00" + "b" * 40 + "\x00", "a" * 40),
    )
    honest = _manifest(
        _entry("a.py", "b" * 40),
        _entry("a.py", "a" * 40),
    )
    assert isolation._manifest_sha256(crafted) != isolation._manifest_sha256(honest)

    # The same attack across the section boundary: an "omitted" path that
    # spells out what an extra entry would have contributed.
    assert isolation._manifest_sha256(
        _manifest(_entry("a.py", "a" * 40), omitted=("b.py",))
    ) != isolation._manifest_sha256(
        _manifest(_entry("a.py", "a" * 40), _entry("b.py", "0" * 40))
    )


# --- the judge identity -------------------------------------------------------


def _plan(*, argv=("pytest", "tests"), env=None, passthrough=(), prefix="."):
    lane = make_lane(
        argv=tuple(argv),
        env={} if env is None else dict(env),
        env_passthrough=tuple(passthrough),
    )
    return make_plan(
        lane,
        project_prefix=prefix,
        passthrough_source={name: f"value-of-{name}" for name in passthrough},
    )


def test_the_judge_identity_is_stable_for_identical_inputs():
    plan = _plan()
    assert judge_sha256(tree_sha256="a" * 64, plan=plan) == judge_sha256(
        tree_sha256="a" * 64, plan=plan
    )


def test_a_different_tree_is_a_different_judge():
    plan = _plan()
    assert judge_sha256(tree_sha256="a" * 64, plan=plan) != judge_sha256(
        tree_sha256="b" * 64, plan=plan
    )


def test_a_different_argv_is_a_different_judge():
    """Same tree, narrowed selection: `-k` or a shorter test-file list means
    a mutant is judged by fewer tests than the record was produced under."""
    base = judge_sha256(tree_sha256="a" * 64, plan=_plan())
    assert base != judge_sha256(
        tree_sha256="a" * 64, plan=_plan(argv=("pytest", "tests", "-k", "frozen"))
    )
    assert base != judge_sha256(tree_sha256="a" * 64, plan=_plan(argv=("pytest",)))


def test_argv_order_is_part_of_the_judge_identity():
    assert judge_sha256(
        tree_sha256="a" * 64, plan=_plan(argv=("pytest", "a", "b"))
    ) != judge_sha256(tree_sha256="a" * 64, plan=_plan(argv=("pytest", "b", "a")))


def test_an_empty_argv_is_accepted_and_distinct():
    """Boundary: ZERO argv entries. A hand-built plan can carry one; the
    digest must neither crash nor collide with a one-element argv."""
    empty = judge_sha256(tree_sha256="a" * 64, plan=_plan(argv=()))
    assert len(empty) == 64
    assert empty != judge_sha256(tree_sha256="a" * 64, plan=_plan(argv=("",)))


def test_a_different_declared_environment_is_a_different_judge():
    """A verdict produced under a different DECLARED `PYTHONPATH` is not
    evidence about this run -- it may not even have imported the same
    package. Declared values are committed lane configuration, so folding
    them by value is both sound and reproducible."""
    assert judge_sha256(
        tree_sha256="a" * 64, plan=_plan(env={"PYTHONPATH": "src"})
    ) != judge_sha256(tree_sha256="a" * 64, plan=_plan(env={"PYTHONPATH": "lib"}))


def test_a_passthrough_value_that_differs_between_invocations_still_resumes():
    """Round-1 review finding 1, the confirmed regression, pinned.

    The first version of this function folded `env_effective` by VALUE, which
    includes every `env_passthrough` name that was present and every B013
    infrastructure fact. Those values are per-invocation BY DESIGN -- dstdns'
    `P165_PHYSICAL_REPO_ROOT` is literally the worktree's host path,
    `SCHEMA_GATE_DSN` is a per-instance DSN -- so the identity changed
    between two runs that judged byte-identically, and resume became
    impossible across exactly the ephemeral-checkout case B066/RG-38 built
    `--state-dir` for. Silently, and forever.
    """
    first = _plan(passthrough=("P165_PHYSICAL_REPO_ROOT",))
    second = make_plan(
        make_lane(argv=("pytest", "tests"), env={}, env_passthrough=("P165_PHYSICAL_REPO_ROOT",)),
        passthrough_source={"P165_PHYSICAL_REPO_ROOT": "/a/completely/different/worktree"},
    )
    assert first.env_effective != second.env_effective, "the fixture must differ by value"
    assert judge_sha256(tree_sha256="a" * 64, plan=first) == judge_sha256(
        tree_sha256="a" * 64, plan=second
    )


def test_a_passthrough_name_appearing_or_vanishing_is_a_different_judge():
    """The name set is still part of the identity, and must be: a lane that
    starts (or stops) passing something through has changed what it declares,
    and `resolve_command_plan` drops a passthrough name that is absent from
    the source -- so presence alone is a real difference."""
    present = make_plan(
        make_lane(argv=("pytest",), env={}, env_passthrough=("TERM",)),
        passthrough_source={"TERM": "xterm"},
    )
    absent = make_plan(
        make_lane(argv=("pytest",), env={}, env_passthrough=("TERM",)),
        passthrough_source={},
    )
    assert judge_sha256(tree_sha256="a" * 64, plan=present) != judge_sha256(
        tree_sha256="a" * 64, plan=absent
    )


def test_a_declared_name_and_a_passthrough_name_are_not_interchangeable():
    """The two env sections are folded differently, so the serialization must
    keep them apart -- a declared `A=` must not digest like a passed-through
    `A`."""
    declared = _plan(env={"A": ""})
    passed = make_plan(
        make_lane(argv=("pytest", "tests"), env={}, env_passthrough=("A",)),
        passthrough_source={"A": ""},
    )
    assert judge_sha256(tree_sha256="a" * 64, plan=declared) != judge_sha256(
        tree_sha256="a" * 64, plan=passed
    )


def test_assays_own_version_is_part_of_the_judge_identity():
    """Round-1 review finding 6. The code that CLASSIFIES a result is as much
    the judge as the suite is, and because B088 deliberately does not bump
    `MUTATION_STATE_SCHEMA_VERSION` there would otherwise be NO lever that
    invalidates records across an assay upgrade changing bucket semantics."""
    plan = _plan()
    assert judge_sha256(
        tree_sha256="a" * 64, plan=plan, tool_version="6.1.0"
    ) != judge_sha256(tree_sha256="a" * 64, plan=plan, tool_version="6.2.0")


def test_the_real_sweep_folds_the_real_assay_version():
    """The seam, not just the parameter: whatever `run_mutation` passes must
    be assay's actual version, or the dimension above is decorative."""
    from assay import __version__

    assert mutation._tool_version() == __version__
    assert isinstance(mutation._tool_version(), str)


def test_an_empty_environment_is_accepted_and_distinct():
    """Boundary: ZERO environment entries, the common case for a lane that
    declares `env = {}`."""
    empty = judge_sha256(tree_sha256="a" * 64, plan=_plan(env={}))
    assert len(empty) == 64
    assert empty != judge_sha256(tree_sha256="a" * 64, plan=_plan(env={"A": ""}))


def test_environment_iteration_order_does_not_reach_the_judge_identity():
    """The plan carries a mapping, and a mapping's insertion order is an
    accident of how it was built -- it must never decide whether a verdict
    is trusted."""
    forward = _plan(env={"A": "1", "B": "2"})
    backward = _plan(env={"B": "2", "A": "1"})
    assert judge_sha256(tree_sha256="a" * 64, plan=forward) == judge_sha256(
        tree_sha256="a" * 64, plan=backward
    )


def test_a_different_project_prefix_is_a_different_judge():
    assert judge_sha256(
        tree_sha256="a" * 64, plan=_plan(prefix=".")
    ) != judge_sha256(tree_sha256="a" * 64, plan=_plan(prefix="assay"))


def test_a_different_declared_cwd_is_a_different_judge():
    with_cwd = make_plan(make_lane(argv=("pytest",), cwd="sub"))
    without = make_plan(make_lane(argv=("pytest",)))
    assert judge_sha256(tree_sha256="a" * 64, plan=with_cwd) != judge_sha256(
        tree_sha256="a" * 64, plan=without
    )


def test_declared_link_paths_are_part_of_the_judge_identity():
    """`[isolation] link_paths` (B041(b)) links directories into the snapshot
    from the invoking checkout, so declaring, dropping or re-pointing one
    changes what judges the mutant. Their CONTENT is a documented residual --
    untracked by construction, and CONSUMERS.md already says a lane declaring
    them is only as reproducible as the linked directory -- but the
    DECLARATION is knowable and is folded in."""
    plan = _plan()
    bare = judge_sha256(tree_sha256="a" * 64, plan=plan)
    assert bare != judge_sha256(
        tree_sha256="a" * 64, plan=plan, link_paths=("app/node_modules",)
    )
    assert judge_sha256(
        tree_sha256="a" * 64, plan=plan, link_paths=("a", "b")
    ) != judge_sha256(tree_sha256="a" * 64, plan=plan, link_paths=("a",))


def test_link_path_declaration_order_does_not_reach_the_judge_identity():
    """The verdict already records `link_paths` sorted; the identity must
    agree, or the same lane digests two ways depending on how the list was
    spelled."""
    assert judge_sha256(
        tree_sha256="a" * 64, plan=_plan(), link_paths=("b", "a")
    ) == judge_sha256(tree_sha256="a" * 64, plan=_plan(), link_paths=("a", "b"))


def test_the_judge_serialization_cannot_be_forged_by_field_injection():
    """Injectivity again, at the other level: an argv element or environment
    value containing the joiner must not be able to spell out a different
    judge. Length-prefixed sections are what prevent it."""
    assert judge_sha256(
        tree_sha256="a" * 64, plan=_plan(argv=("pytest\x00tests",))
    ) != judge_sha256(tree_sha256="a" * 64, plan=_plan(argv=("pytest", "tests")))
    assert judge_sha256(
        tree_sha256="a" * 64, plan=_plan(env={"A": "1\x00B\x002"})
    ) != judge_sha256(tree_sha256="a" * 64, plan=_plan(env={"A": "1", "B": "2"}))


# (The same invariants are stated over GENERATED input in
# `test_mutation_judge_identity_properties.py`, which is a separate module so
# that a missing Hypothesis skips the property tests alone and never this
# file's behavioural ones.)


# --- the record reader's dispositions -----------------------------------------


_TEXT_PY = "a = True\n"


def _job():
    from assay.adapters.python import PythonAdapter

    sites = mutation.collect_mutation_sites(
        (mutation.MutationTarget(path="pkg/flags.py", text=_TEXT_PY, lines=frozenset({1})),),
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=10,
    )
    assert sites != mutation.UNSUPPORTED and sites, sites
    return sites[0]


def _record(job, *, judge: str, **overrides) -> dict:
    import hashlib

    payload = {
        "schema_version": mutation.MUTATION_STATE_SCHEMA_VERSION,
        "candidate_id": mutation.candidate_id(job),
        "path": job.path,
        "operator": job.site.operator,
        "replacement_sha256": hashlib.sha256(job.site.replacement).hexdigest(),
        "source_sha256": hashlib.sha256(job.original_text.encode("utf-8")).hexdigest(),
        "judge_sha256": judge,
        "outcome_bucket": "survived",
    }
    payload.update(overrides)
    return payload


def _store(tmp_path: Path, job, payload: dict) -> Path:
    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    name = mutation.mutation_state_record_name(mutation.candidate_id(job))
    (root / name).write_text(json.dumps(payload), encoding="utf-8")
    return root


def test_a_matching_judge_resumes_the_record(tmp_path: Path):
    job = _job()
    root = _store(tmp_path, job, _record(job, judge="j" * 64))
    loaded = mutation._load_validated_state_record(root, job, judge="j" * 64)
    assert loaded is not None and loaded["outcome_bucket"] == "survived"


def test_a_different_judge_is_rejected_not_treated_as_tampering(tmp_path: Path):
    """B088's headline disposition, and the B021 precedent it follows: the
    ground moved, so re-execute -- without failing the lane. A
    `MutationStateError` here would make every strengthened test a lane
    outage.

    REJECTED, not `None`: a refused record is a different fact from an
    absent one, and the counter that difference feeds is the only way an
    operator can tell "the cache is cold" from "the cache is refused every
    run" (round-1 review finding 2)."""
    job = _job()
    root = _store(tmp_path, job, _record(job, judge="j" * 64))
    assert (
        mutation._load_validated_state_record(root, job, judge="k" * 64)
        is mutation._RECORD_REJECTED
    )


def test_a_pre_b088_record_with_no_judge_at_all_is_rejected(tmp_path: Path):
    """Upgrade path. Records written before this change recorded nothing
    about what judged them, so nothing can vouch for them -- and a consumer
    must not have to hand-delete a state directory to upgrade."""
    job = _job()
    payload = _record(job, judge="j" * 64)
    del payload["judge_sha256"]
    root = _store(tmp_path, job, payload)
    assert (
        mutation._load_validated_state_record(root, job, judge="j" * 64)
        is mutation._RECORD_REJECTED
    )


def test_a_non_string_judge_field_is_rejected_even_against_a_none_judge(tmp_path: Path):
    """Fail-CLOSED, pinned (round-1 review finding 8).

    `payload.get("judge_sha256") != judge` alone trusts a judge-less record
    whenever the caller's own judge is `None`, because `None != None` is
    False. That is unreachable through `run_mutation` and guarded by two
    asserts -- which vanish under `python -O`. For the one code path whose
    whole bug class is a cache that trusts too much, the type check is the
    guarantee and this is the test that keeps it.
    """
    job = _job()
    payload = _record(job, judge="j" * 64)
    payload["judge_sha256"] = None
    root = _store(tmp_path, job, payload)
    assert (
        mutation._load_validated_state_record(root, job, judge=None)  # type: ignore[arg-type]
        is mutation._RECORD_REJECTED
    )


def test_an_absent_record_is_absent_not_rejected(tmp_path: Path):
    """The other half of the three-way outcome: a cold cache really is
    `None`, so the rejection counter counts refusals and not first runs."""
    job = _job()
    root = tmp_path / "empty-state"
    root.mkdir()
    assert mutation._load_validated_state_record(root, job, judge="j" * 64) is None


def test_a_corrupt_record_still_raises_even_when_the_judge_also_moved(tmp_path: Path):
    """ORDERING, pinned. The judge check runs LAST, after every
    identity-vs-filename check. If it ran first, a routine test edit would
    become a way to launder a hand-edited state file into a silent rerun --
    B021's corruption evidence would be discarded exactly when it is least
    likely to be noticed.
    """
    job = _job()
    root = _store(tmp_path, job, _record(job, judge="j" * 64, source_sha256="0" * 64))
    with pytest.raises(MutationStateError, match="stale source_sha256"):
        mutation._load_validated_state_record(root, job, judge="k" * 64)


def test_a_stale_schema_version_is_still_rerun_and_not_a_lane_failure(tmp_path: Path):
    """B021's disposition is untouched by B088 -- still a silent rerun, never
    a `MutationStateError`. Pinned here because this change deliberately did
    NOT bump `MUTATION_STATE_SCHEMA_VERSION` (that constant is also the
    shard-summary document's version, which `merge_mutation_shards` refuses
    outright on any other value)."""
    job = _job()
    root = _store(tmp_path, job, _record(job, judge="j" * 64, schema_version=99))
    assert (
        mutation._load_validated_state_record(root, job, judge="j" * 64)
        is mutation._RECORD_REJECTED
    )


def test_the_shard_summary_schema_version_is_unchanged():
    """The reason the bump was declined, stated as a fact rather than a
    comment: a consumer's existing shard summary documents still merge."""
    assert mutation.MUTATION_STATE_SCHEMA_VERSION == 1


# --- two real repositories, the real CLI --------------------------------------


_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "tests/judge.sh"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.unit.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
"""

#: Every mutant survives: the judge looks at nothing.
_BLIND_JUDGE = "exit 0\n"

#: The strengthened suite. `pkg/flags.py` is `a = True` in the judged tree,
#: so the BASELINE still passes; the bool-const-flip mutant rewrites it to
#: `a = False`, which this judge now detects -- the exact shape of the real
#: incident (a NEW assertion, zero bytes of the mutated source touched).
_STRICT_JUDGE = "grep -q '^a = False$' pkg/flags.py && exit 1\nexit 0\n"


def _seed(repo: GitRepo, judge: str) -> None:
    repo.write("assay.toml", _LANE)
    repo.write("pkg/flags.py", "a = True\n")
    repo.write("tests/judge.sh", judge)
    repo.commit_all("lane")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", "a = False\n")
    repo.commit_all("base flag")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", "a = True\n")
    repo.commit_all("restore flag")


def _events(destination: Path) -> list[dict]:
    return [
        json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()
    ]


def _run(repo: GitRepo, state_dir: Path, progress: Path) -> list[dict]:
    main(
        [
            "run",
            "unit",
            "--file",
            str(repo.path / "assay.toml"),
            "--state-dir",
            str(state_dir),
            "--progress",
            str(progress),
            "--resume",
        ]
    )
    return _events(progress)


def _candidates(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("event") == "candidate"]


def _resumed(events: list[dict]) -> int:
    for event in events:
        if event.get("event") == "resume":
            return event["resumed_total"]
    return 0


def _records(state_dir: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(state_dir.glob("*.json"))
    ]


def test_a_strengthened_test_file_re_executes_instead_of_replaying_survived(
    git_repo: GitRepo, tmp_path: Path
):
    """B088's acceptance criterion 1, as the incident actually happened.

    Run one: the judge looks at nothing, the mutant SURVIVES, the verdict is
    persisted. Then a real, test-only fix lands -- `tests/judge.sh` gains the
    check that detects the mutation, and NOT ONE BYTE of `pkg/flags.py`
    moves, so the candidate id is bit-identical. Run two, with `--resume`.

    Before this fix, run two resumed the stale record and reported the same
    `survived` for a mutant the new suite kills. The assertions below are
    each individually fatal to that behaviour: nothing is resumed, the
    candidate is re-executed, and the persisted verdict flips to `killed`.
    """
    _seed(git_repo, _BLIND_JUDGE)
    state_dir = tmp_path / "state"

    first = _run(git_repo, state_dir, tmp_path / "first.jsonl")
    assert [event["outcome_bucket"] for event in _candidates(first)] == ["survived"]
    assert [record["outcome_bucket"] for record in _records(state_dir)] == ["survived"]
    before = git_repo.git("rev-parse", "HEAD:pkg/flags.py").strip()

    git_repo.write("tests/judge.sh", _STRICT_JUDGE)
    git_repo.commit_all("strengthen the suite")
    assert git_repo.git("rev-parse", "HEAD:pkg/flags.py").strip() == before, (
        "the fixture must change ONLY the test file -- a changed source file "
        "would produce a new candidate id and prove nothing about B088"
    )

    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 0, "a strengthened suite must not be resumed past"
    assert [event["outcome_bucket"] for event in _candidates(second)] == ["killed"]
    assert [record["outcome_bucket"] for record in _records(state_dir)] == ["killed"]


def test_an_unchanged_suite_resumes_exactly_as_before(git_repo: GitRepo, tmp_path: Path):
    """B088's acceptance criterion 2: no regression to B066/RG-38's
    documented resume. Same tree, same commit, same command -- the second run
    resumes and re-executes nothing."""
    _seed(git_repo, _BLIND_JUDGE)
    state_dir = tmp_path / "state"

    first = _run(git_repo, state_dir, tmp_path / "first.jsonl")
    assert len(_candidates(first)) == 1

    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 1
    assert _candidates(second) == [], "an unchanged suite must re-execute nothing"


def test_a_touched_but_unchanged_test_file_still_resumes(
    git_repo: GitRepo, tmp_path: Path
):
    """Boundary, and the reason this is content-hashed rather than
    mtime-stamped: rewriting a file with IDENTICAL bytes and moving its mtime
    forward changes nothing about what judges the mutant, and must not cost a
    re-execution."""
    _seed(git_repo, _BLIND_JUDGE)
    state_dir = tmp_path / "state"
    _run(git_repo, state_dir, tmp_path / "first.jsonl")

    judge = git_repo.path / "tests" / "judge.sh"
    judge.write_text(_BLIND_JUDGE, encoding="utf-8")  # identical bytes, new mtime
    judge.touch()
    assert git_repo.git("status", "--porcelain") == "", "the tree must be unchanged"

    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 1
    assert _candidates(second) == []


def test_the_same_tree_at_a_different_commit_still_resumes(
    git_repo: GitRepo, tmp_path: Path
):
    """The discriminator that proves this hashes the TREE, not the commit.

    A commit id would have been a far cheaper identity and would have closed
    B088 too -- by invalidating every record on every commit, including an
    amended message or a rebase that touched nothing. This pins the narrower,
    honest behaviour: identical content resumes, whatever its commit id.
    """
    _seed(git_repo, _BLIND_JUDGE)
    state_dir = tmp_path / "state"
    _run(git_repo, state_dir, tmp_path / "first.jsonl")
    before = git_repo.head()

    git_repo.git("commit", "-q", "--amend", "-m", "same tree, new commit")
    assert git_repo.head() != before
    assert git_repo.git("rev-parse", f"{before}^{{tree}}") == git_repo.git(
        "rev-parse", "HEAD^{tree}"
    )

    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 1
    assert _candidates(second) == []


def test_a_relocated_test_file_re_executes(git_repo: GitRepo, tmp_path: Path):
    """The documented path-is-identity decision, measured end to end: a
    helper whose bytes never changed but whose path moved re-executes."""
    _seed(git_repo, _BLIND_JUDGE)
    git_repo.write("tests/helper.py", "SHARED = 1\n")
    git_repo.commit_all("add a helper")
    state_dir = tmp_path / "state"
    _run(git_repo, state_dir, tmp_path / "first.jsonl")

    git_repo.git("mv", "tests/helper.py", "tests/renamed_helper.py")
    git_repo.commit_all("relocate the helper")

    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 0
    assert len(_candidates(second)) == 1


def test_a_lane_whose_argv_names_no_path_still_notices_a_changed_suite(
    git_repo: GitRepo, tmp_path: Path
):
    """Boundary: EMPTY test-path selection.

    `/bin/sh -c '...'` names no test file, no test directory, nothing a
    reader could resolve to a path -- the shape of several real lanes in this
    estate (`bash -c`, `bash scripts/....sh`). An identity derived from the
    paths named in argv would have had nothing to hash here and would have
    silently kept the B088 defect for exactly these lanes. Digesting the
    judged tree has no such blind spot.
    """
    inline = _LANE.replace(
        'argv = ["/bin/sh", "tests/judge.sh"]',
        'argv = ["/bin/sh", "-c", ". ./tests/judge.sh"]',
    )
    _seed(git_repo, _BLIND_JUDGE)
    git_repo.write("assay.toml", inline)
    git_repo.commit_all("inline judge")
    state_dir = tmp_path / "state"

    first = _run(git_repo, state_dir, tmp_path / "first.jsonl")
    assert [event["outcome_bucket"] for event in _candidates(first)] == ["survived"]

    git_repo.write("tests/judge.sh", _STRICT_JUDGE)
    git_repo.commit_all("strengthen the suite")

    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 0
    assert [event["outcome_bucket"] for event in _candidates(second)] == ["killed"]


def test_two_worktrees_of_one_commit_still_share_one_state_dir(
    git_repo: GitRepo, tmp_path: Path
):
    """B066's headline acceptance, re-measured under B088's stricter
    identity: the judge identity is derived from the COMMIT's tree and the
    lane's own resolved command, neither of which knows which worktree it was
    computed in, so a second worktree of the same commit still resumes."""
    _seed(git_repo, _BLIND_JUDGE)
    state_dir = tmp_path / "state"
    _run(git_repo, state_dir, tmp_path / "first.jsonl")

    second_tree = tmp_path / "second-worktree"
    subprocess.run(
        [
            "git", "-C", str(git_repo.path), "worktree", "add", "-q",
            "--detach", str(second_tree), "feature",
        ],
        check=True,
        capture_output=True,
    )
    events = _run(GitRepo(path=second_tree), state_dir, tmp_path / "second.jsonl")
    assert _resumed(events) == 1
    assert _candidates(events) == []


_INSTANCE_LANE = _LANE.replace(
    'env_passthrough = ["PATH"]',
    'env_passthrough = ["PATH", "INSTANCE_ROOT"]',
)


def test_two_instances_with_different_passthrough_values_still_resume(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """Round-1 review finding 1 and 11, end to end.

    `test_two_worktrees_of_one_commit_still_share_one_state_dir` cannot see
    this: both its runs share one `os.environ`. This one varies a real
    passed-through value BETWEEN the two runs, which is precisely what two
    ciu worktrees or two Mode-B instances do -- dstdns'
    `P165_PHYSICAL_REPO_ROOT` is the worktree's own host path. Under the
    by-value folding this test fails with nothing resumed, and the feature
    `--state-dir` exists for is dead with no diagnostic.
    """
    _seed(git_repo, _BLIND_JUDGE)
    git_repo.write("assay.toml", _INSTANCE_LANE)
    git_repo.commit_all("pass an instance-scoped fact through")
    state_dir = tmp_path / "state"

    monkeypatch.setenv("INSTANCE_ROOT", "/workspaces/instance-a")
    first = _run(git_repo, state_dir, tmp_path / "first.jsonl")
    assert len(_candidates(first)) == 1

    monkeypatch.setenv("INSTANCE_ROOT", "/workspaces/instance-b")
    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 1, "a per-instance passthrough VALUE must not break resume"
    assert _candidates(second) == []


def test_dropping_a_passthrough_declaration_re_executes(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    """The other side of the same coin: the lane's declared name SET is part
    of the identity, so a lane that stops passing something through is a
    different judge and does not resume."""
    _seed(git_repo, _BLIND_JUDGE)
    git_repo.write("assay.toml", _INSTANCE_LANE)
    git_repo.commit_all("pass an instance-scoped fact through")
    state_dir = tmp_path / "state"

    monkeypatch.setenv("INSTANCE_ROOT", "/workspaces/instance-a")
    _run(git_repo, state_dir, tmp_path / "first.jsonl")

    monkeypatch.delenv("INSTANCE_ROOT")
    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")
    assert _resumed(second) == 0
    assert len(_candidates(second)) == 1


def test_a_rejected_store_says_so_in_the_progress_stream(
    git_repo: GitRepo, tmp_path: Path
):
    """Round-1 review finding 2. A store whose every record is refused used
    to emit NOTHING -- the `resume` event fired only on a successful resume,
    so "the cache is cold" and "the cache is refused every single run" had
    one identical symptom: silence. `--resume` is a performance feature whose
    failure is invisible by construction unless it is counted."""
    _seed(git_repo, _BLIND_JUDGE)
    state_dir = tmp_path / "state"
    first = _run(git_repo, state_dir, tmp_path / "first.jsonl")
    assert [event for event in first if event.get("event") == "resume"] == [], (
        "a genuinely cold cache must stay silent -- the counter reports "
        "refusals, not first runs"
    )

    git_repo.write("tests/judge.sh", _STRICT_JUDGE)
    git_repo.commit_all("strengthen the suite")
    second = _run(git_repo, state_dir, tmp_path / "second.jsonl")

    resume_events = [event for event in second if event.get("event") == "resume"]
    assert len(resume_events) == 1
    assert resume_events[0]["resumed_total"] == 0
    assert resume_events[0]["rejected_total"] == 1


def test_the_worst_case_record_still_fits_the_readers_own_limit():
    """B071's size argument, re-pinned with `judge_sha256` present: the new
    field is 64 hex characters plus its key, and the worst case a maximal
    crash-tail pair can serialize to must still fit
    `MUTATION_STATE_RECORD_LIMIT`."""
    from assay.runner import COMMAND_TAIL_BYTES, CommandResult

    worst = "\x7f" * COMMAND_TAIL_BYTES
    result = CommandResult.__new__(CommandResult)
    object.__setattr__(result, "stdout_tail", worst)
    object.__setattr__(result, "stderr_tail", worst)
    payload = {
        "schema_version": mutation.MUTATION_STATE_SCHEMA_VERSION,
        "candidate_id": "0" * 64,
        "judge_sha256": "0" * 64,
        "path": "infra/db-init/init-scripts/03c-create-workflow-core.sql",
        "operator": "sql:drop-unique",
        "replacement_sha256": "0" * 64,
        "source_sha256": "0" * 64,
        "lineno": 189,
        "description": "UNIQUE -> CHECK (true)",
        "outcome_bucket": "crashed",
        **mutation._crash_diagnostic_tails("crashed", result),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) < mutation.MUTATION_STATE_RECORD_LIMIT, len(encoded)
