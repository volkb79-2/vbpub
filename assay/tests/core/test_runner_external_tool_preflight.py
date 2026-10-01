"""A-253/A-284, P34/W3 — the external-tool PATH preflight at the TOP of
:func:`assay.runner.run_lane`: every name the resolved adapter declares in
``external_tools`` must resolve on the effective ``PATH`` before this
function does ANY snapshot, command or Git work.

**The trap A-253's own wording walks into (carve §3.5).** SQL declares
``external_tools = ()``, so a green test over an adapter with an empty
tuple is A-278's empty-list gate verbatim: a check with nothing to check is
not a passing check. So this module ships THREE tests, not one:

* :func:`test_a_missing_external_tool_refuses_before_any_command_runs` — the
  negative: an adapter declaring a tool absent from ``PATH`` is refused
  ``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` before the lane's own
  command is ever launched.
* :func:`test_a_present_external_tool_lets_the_command_run` — the paired
  MUST-SUCCEED control: the identical adapter shape, declaring a tool that
  DOES resolve, reaches the command.
* :func:`test_the_empty_external_tools_tuple_is_the_real_subject_under_test`
  — the empty-tuple control: proves the loop body runs ZERO iterations
  (never merely that the run happens to pass) AND that the tuple hand to
  it really is empty, so "it passed" cannot mean "it iterated nothing
  unnoticed" (the sibling to the naive read A-278 already rejects).

Every test is driven through :func:`assay.runner.run_lane` directly —
the real production entry point :mod:`assay.cli` itself calls, the same
established unit-level surface ``test_runner_run_lane.py``/
``test_runner_run_lane_r2.py`` already use — with the adapter resolved
through a REAL :class:`~assay.registry.Registry` round-trip
(:func:`assay.registry.new_registry`/:func:`assay.registry.get_adapter`),
never handed to ``run_lane`` by construction alone.

The negative needs no real Git repository at all: the preflight is placed
before :func:`assay.runner._require_evidence_bound_to_lane` even runs its
own Git-free comparison, so ``refuse_lane`` -- the function this refusal
actually calls -- never touches the filesystem or Git either (A-284). The
two positive controls DO need one: any lane carrying a resolved adapter
takes ``run_lane``'s higher-rigor dispatch unconditionally (``adapter`` is
``None`` exactly when neither R1 nor R2 is declared), which is P22/P23's
real committed-snapshot machinery.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from conftest import FakeAdapter, GitRepo, make_lane, make_r1_judge

from assay import registry, runner
from assay.errors import Outcome, ReasonCode

#: A name deliberately never installed anywhere real (A-278's own naming
#: convention for a fixture that must NOT resolve) -- the fixture's own
#: premise is asserted directly in the test below, never merely assumed.
_ABSENT_TOOL = "assay-nonexistent-tool-9f3c2"


def _seed_two_commits(repo: GitRepo) -> tuple[str, str]:
    """A real two-commit diff, so ``base != head`` and R1 has something to
    measure (mirrors ``test_runner_run_lane.py``'s own helper)."""
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = repo.commit_all("add pkg head")
    return base_rev, head_rev


def _write_cov_argv() -> tuple[str, ...]:
    """A REAL shell command whose own execution writes ``cov.json`` covering
    the one changed line -- proves the lane's command genuinely ran, not
    merely that a stub was reached."""
    payload = (
        '{"files": {"pkg/mod.zzz": '
        '{"executed_lines": [2], "missing_lines": [], "excluded_lines": []}}}'
    )
    script = f"cat > cov.json <<'EOF'\n{payload}\nEOF"
    return ("/bin/sh", "-c", script)


def _registered_adapter(*, external_tools: tuple[str, ...]) -> object:
    """A :class:`~conftest.FakeAdapter` round-tripped through a REAL
    :class:`~assay.registry.Registry` (never handed to ``run_lane`` merely
    by construction), matching this module's own claim that the preflight
    is proven against a genuinely resolved adapter."""
    reg = registry.new_registry(
        registry.RegistryEntry(
            adapter=FakeAdapter(name="zzz", external_tools=external_tools),
            rigor=frozenset({"R1"}),
        )
    )
    return registry.get_adapter(reg, "zzz", "R1")


# ---------------------------------------------------------------------------
# The negative: a declared tool absent from PATH
# ---------------------------------------------------------------------------


def test_a_missing_external_tool_refuses_before_any_command_runs(tmp_path: Path):
    assert shutil.which(_ABSENT_TOOL) is None, (
        "fixture premise: this name must not resolve on the REAL PATH, or "
        "the negative below proves nothing"
    )
    adapter = _registered_adapter(external_tools=(_ABSENT_TOOL,))

    calls: list[tuple] = []

    def spy_process_runner(argv, *, env, cwd, timeout):
        calls.append(argv)
        raise AssertionError("the lane's command must never be launched")

    judge = make_r1_judge(source_root_paths=(tmp_path,))
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    # No real Git repository needed: the preflight fires before ANY
    # snapshot, command or Git work (A-284), so `repo`/`project_root` are
    # never touched on this path.
    verdict = runner.run_lane(
        lane,
        commit="0" * 40,
        repo=tmp_path,
        project_root=tmp_path,
        adapter=adapter,
        assay_version="0.1.0",
        process_runner=spy_process_runner,
    )

    assert calls == [], "the lane's command must never have been launched"
    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert {c.status for c in verdict.claims} == {Outcome.NO_MEASUREMENT}, (
        "refuse_lane's own rule: every declared level renders the SAME pair"
    )
    assert {c.reason_code for c in verdict.claims} == {
        ReasonCode.MISSING_EXTERNAL_TOOL
    }
    assert verdict.judgment is None


# ---------------------------------------------------------------------------
# The paired must-succeed control: the same shape, a tool that DOES resolve
# ---------------------------------------------------------------------------


def test_a_present_external_tool_lets_the_command_run(git_repo: GitRepo):
    assert shutil.which("sh") is not None, (
        "fixture premise: 'sh' must resolve on the real PATH, or this "
        "control is not actually distinguishing 'present' from 'absent'"
    )
    adapter = _registered_adapter(external_tools=("sh",))

    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=_write_cov_argv())

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=adapter,
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].status is Outcome.PASS
    assert verdict.claims[1].status is Outcome.PASS
    assert verdict.claims[1].coverage is not None, (
        "the command actually ran and wrote the declared coverage artifact"
    )


# ---------------------------------------------------------------------------
# The empty-tuple control: the loop truly ran zero iterations
# ---------------------------------------------------------------------------


def test_the_empty_external_tools_tuple_is_the_real_subject_under_test(
    git_repo: GitRepo, monkeypatch
):
    adapter = FakeAdapter(name="zzz")
    assert adapter.external_tools == (), (
        "the audit states its own subject: the tuple under test really is "
        "empty, never merely assumed to be"
    )

    # `shutil` is a process-wide singleton: `assay.git` resolves the `git`
    # binary through the SAME `shutil.which`, always with its own `path=`
    # keyword (`git.py`'s one other call site), so a bare spy would also
    # count git's own real, unrelated calls -- which is exactly what a
    # first attempt at this test measured (P22/P23's snapshot machinery
    # calls `git` over twenty times for one lane). The preflight's own call
    # shape, `shutil.which(tool)`, is the ONLY call site anywhere in
    # `src/assay/` with no keyword argument at all, so that shape is what
    # this spy isolates: `git.py`'s calls pass straight through unrecorded.
    calls: list[str] = []
    real_which = shutil.which

    def spy_which(name, *args, **kwargs):
        if not kwargs:
            calls.append(name)
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(runner.shutil, "which", spy_which)

    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=_write_cov_argv())

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=adapter,
        assay_version="0.1.0",
    )

    assert calls == [], (
        "the preflight loop must run ZERO iterations over an empty tuple -- "
        "'it passed' must not be able to mean 'it iterated nothing unnoticed'"
    )
    assert verdict.outcome is Outcome.PASS
