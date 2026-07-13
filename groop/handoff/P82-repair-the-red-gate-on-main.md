# P82 - Repair the red gate on main

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
