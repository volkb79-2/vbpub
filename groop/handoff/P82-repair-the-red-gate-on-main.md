# P82 - Repair the red gate on main

> **STATUS: SUPERSEDED by P79 (merged). Do not implement. See "Superseded" below.**

## Superseded

P79's review pass #2 repaired `test_zst_without_zstandard_exits_2` as part of fixing
the defect it was hiding, and it meets this handoff's contracts:

- The degradation path **is** still tested with `zstandard` installed. P79 forces the
  extra's absence in a subprocess by shadowing the import with a stub `zstandard.py`
  on `PYTHONPATH`, so the branch is exercised in the environment we actually gate in
  (this handoff's oracle 2).
- The test **can** fail: deleting the missing-`zstandard` branch turns it red -
  verified by mutation (this handoff's oracle 3). Note it did **not** satisfy that
  oracle as first written: it asserted the bare token `"zstandard"` in stderr, and
  pytest names `tmp_path` after the test (`test_zst_without_zstandard_exi...`), so the
  token arrived via the echoed file path rather than the message. It passed with the
  branch deleted. P79 pass #2 found and fixed this; it now asserts the full typed
  phrase.
- The CLI's user-visible behavior is unchanged (exit 2, message naming `zstandard`).

The in-progress P82 branch took a different route: a `_ZSTD_FORCE_UNAVAILABLE` module
global in `record/reader.py` that production code branches on. That is a test seam in
shipping source, and P79 achieves the same coverage without one, so adopting it now
would be a regression. **Abandon the P82 branch rather than rebasing it.**

The *motivation* behind P82 - a gate that cannot be trusted is worse than no gate -
was not fully discharged, and is carried forward by two carves:

- **P84** (`handoff/P84-pin-the-gate-environment.md`): the extra is unpinned, so the
  zstd tests skip in some venvs. P79 shipped "green" that way while actually being red.
- **P85** (`handoff/P85-flaky-ui-timing-gate.md`): two UI/record tests are flaky on
  unmodified `main`, which is the same tax this package was carved to remove.

---

## Original handoff (for the record)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** none
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** the correct behavior under a missing optional dependency is genuinely ambiguous (then file a DECISIONS-INBOX entry rather than guessing). Do NOT make the test pass by deleting it or by loosening its assertion to match current behavior.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P67/P75/P76 pass #2).
Not a feature. The standing contract is "gates re-run from main in the package
venv -- never trust agent-env greens", and main's gate is red, so every reviewer
in this wave had to hand-verify that the same failure predated their package
before they could trust anything. That is exactly the erosion that makes a
false-green invisible later.
-->

## Goal

`groop/tests/test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2`
fails on unmodified `main` in the package venv. Make the gate honest again.

## Why this is worth a package

The whole review protocol rests on "re-run the gates from `main`; a red gate means
the package broke something." A test that is *permanently* red on `main` inverts
that: reviewers learn to skim past failures, and the next genuine regression hides
behind the familiar one. In this wave, three packages each independently had to
prove the failure was pre-existing before their own results could be read. The
cost is small and recurring, which is the worst shape.

## The actual defect

The test asserts that `groop report <file>.zst` exits 2 when the optional
`zstandard` dependency is **absent**. But `zstandard` *is* installed in the package
venv, so the "without zstandard" branch cannot be reached and the assertion fails.
The test is trying to exercise a degradation path while running in an environment
where that path does not exist.

This is an environment-coupled test, not a broken feature: the degradation behavior
itself is very likely correct. The test is what is wrong.

## Required Contracts

- **The degradation path is still tested.** The fix is not "skip when zstandard is
  installed" -- that would leave the behavior untested in every environment we
  actually run, which is a hollow-test outcome and will be rejected. Simulate the
  absence (e.g. make the import resolution injectable, or patch the module lookup
  the CLI performs) so the branch is exercised **with** the dependency installed.
- **The test must be able to fail.** Prove it: with the degradation branch removed
  from the CLI, the test goes red. State this explicitly in the REPORT -- a test
  that passes against a broken implementation is the thing this package is
  repairing, so shipping another one would be self-defeating.
- Do not change the CLI's user-visible behavior (exit 2 with a clear message on a
  `.zst` recording when `zstandard` is unavailable) unless it is itself wrong; if
  it is, say so in the REPORT before changing it.
- If any *other* test on `main` is environment-coupled the same way, name it in the
  REPORT. Do not fix them silently; one repair, audited, beats a sweep.

## Acceptance Oracles (numbered, adversarial)

1. `timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis`
   is **fully green from `main`** with the package merged. This is the whole point;
   a partial pass is a failure.
2. The repaired test exercises the missing-dependency branch **with `zstandard`
   installed** (the environment we actually gate in).
3. Removing the CLI's degradation branch turns the repaired test red. Include the
   evidence in the REPORT.
4. `groop report <file>.zst` with `zstandard` genuinely available still works
   (no regression in the happy path).

## Out Of Scope

- Any other report/CLI behavior change.
- Adding or removing dependencies from the package.
- The `--window auto` / assertion machinery (P62/P70 own those).

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result. Write P82-LOG.md / P82-REPORT.md.
