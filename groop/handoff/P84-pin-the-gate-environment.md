# P84 - Pin the gate environment so optional extras stop hiding defects

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P79 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** pinning the extra turns out to require making `zstandard` a hard runtime dependency (it must not - the runtime extra stays optional; only the *test* environment is pinned). Say so in the REPORT and BLOCK rather than promoting it.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P79 pass #2). Not a
feature. P79 shipped green and was red: it was validated in a venv without the
`zstandard` extra, so every zstd oracle skipped and the defect the package exists
to fix was never executed. Rebuilding the venv the handoff actually specifies
turned the package red immediately. The skips were honest and named and it still
hid a shipped bug -- so the skip is not the fix, the unpinned environment is the
bug. Evidence: handoff/reports/P79-REPORT.md "Review Pass #2".
-->

## Goal

`groop report` on a corrupt `.jsonl.zst` is only *reachable* when the optional
`zstandard` extra is installed. The package venv is supposed to have it; this
devcontainer's venv did not. Nothing in the repo pins it, so "the suite is green"
means "the suite is green **in whatever environment happened to run it**" - and for
every optional-extra code path, that is not a gate.

This is the same class of defect as P82 (an environment-conditional test is not a
gate), one level down: an environment-conditional *environment*.

## The actual defect

`pyproject.toml` declares `[project.optional-dependencies] zstandard` and `mcp`,
and **no test/dev extra at all**. `pytest`, `textual`, and friends are simply
whatever the ambient venv happens to contain. So:

- With the extra absent, P79's oracles 1/2/2b/2d/5 `pytest.skip`. The suite is
  green and the zstd reader is completely untested.
- With the extra present, they run. That difference shipped a real bug (a truncated
  recording silently reported on the surviving half of the file).

An honest, named skip did not save us. Nothing surfaces "you just skipped the tests
that matter"; a skip reads as a pass at a glance.

## Required Contracts

1. **A declared, installable test environment.** Add a test/dev extra (or the
   project's equivalent) that pins what the gate needs, `zstandard` included.
   `zstandard` stays an **optional runtime** extra - this pins the *test* env only,
   and the missing-extra degradation path must remain tested (P79 already forces its
   absence with a stub module; do not regress that).
2. **The zstd oracles no longer skip in the gate env.** Running the documented gate
   command in the documented environment executes them. Prove it by quoting the skip
   count: it must not include the zstd oracles.
3. **A skipped oracle is loud.** If the extra *is* absent, the run must say so in a
   way a reviewer cannot skim past (e.g. fail the gate, or a session-level summary
   naming the skipped capability). "Green with 5 skips" must stop being
   indistinguishable from "green".
4. **Document how to build the gate env** in one place, so the next reviewer does
   not have to reconstruct it from a handoff footnote (this one had to).
5. **No behavior change to `groop` itself.** This package touches packaging, test
   config, and docs. If it needs a source change, say why in the REPORT first.

## Acceptance Oracles (numbered, adversarial)

1. From a venv built by the documented procedure, `pytest groop/tests -q` runs the
   zstd oracles - assert on the *test IDs executed*, not just the exit code. A pass
   with the oracles skipped is the exact failure this package exists to remove.
2. From a venv **without** the extra, the run does not silently read as clean:
   contract 3's mechanism fires. State which mechanism you chose and why.
3. The missing-`zstandard` degradation path is still tested with the extra
   installed (P79's stub-module test still passes and still goes red when the
   degradation branch is deleted).
4. `zstandard` is still not a hard runtime dependency: `pip install groop` without
   extras still imports and runs `groop report` on a plain `.jsonl`.

## Out Of Scope

- Any change to reader/report/CLI behavior (P79 owns that; it is merged).
- CI provisioning beyond declaring the environment, unless the repo already has CI
  config to update.
- The pre-existing UI timing flakes (P85 owns those).

## Docs

`groop/README.md` (how to build the gate env), `docs/STATUS.md`.

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q
python3 -m py_compile <changed files>
git diff --check
```

State for each result **whether `zstandard` was installed** - that is the whole
subject of this package, and a REPORT that omits it has not reported anything.
