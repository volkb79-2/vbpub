# P80 - Daemon install execution (render, then apply, the same plan)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P22 (merged), P25 (merged), P46 (merged), P32 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** P78   <!-- P78 refactors the execution chain this package must reuse; do not run them concurrently. If P78 has not merged, build on the chain as it stands and say so in the REPORT. -->
> **Escalate-if:** applying the install plan cannot be expressed as the P46 kernel's argv-only, audit-first execution (e.g. it needs a shell, a heredoc, or an unbounded file write) - that is a BLOCKED condition: write what the plan actually requires to the LOG and stop. Do NOT hand-roll a second privileged writer.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **roadmap-driven**.
docs/ROADMAP.md's v2 bucket names "install execution/service hardening" among the
remaining privileged-daemon items, and it has never been carved. P22 renders the
preflight, P25 renders the install *plan* - and there the trail stops: an operator
still hand-copies the rendered steps into a root shell. This is the roadmap item, not
a child of anything reviewed this wave (P72/P74 were actions and GPU). Carving it keeps
the queue off the report/daemon-client orbit that §8 exists to break.
-->

## Goal

P25 renders a correct, safe, operator-readable install plan for the groop daemon
(systemd unit + tmpfiles + a group-readable socket). Today the operator's next move is
to retype it into a root shell - which means groop's most safety-critical output is
applied by copy-paste, unaudited, with no proof that what ran is what was shown.

Add `groop daemon install --execute`: apply the **already-rendered** P25 plan through
the P46 execution kernel, with the same posture as every other mutating verb (root,
`--admin`, typed confirmation, argv-only, bounded timeout, fail-closed audit).

## The contract that defines this package

**What executes is exactly what `groop daemon install` printed.** Not a re-derivation,
not a template rendered a second time with the same inputs - the *same plan object*,
built once, displayed, then applied. This is the standing operator-facing contract
("commands/templates are parameterized and render exactly what any preview/plan mode
shows - no ad-hoc shell substitutions") applied to the one command where a divergence
between preview and execution would be worst.

If you find yourself writing a second code path that reconstructs the steps at execution
time, stop - that is the BLOCKED trigger, and it is the same architectural failure P58's
flash-max attempt made (hand-rolling a parallel path instead of extending the typed one).

## Context To Read First (bounded)

- `src/groop/daemon/` install-plan module + `handoff/P25-daemon-install-plan.md` and its
  REPORT - what the plan object is, and what the rendered steps are.
- `handoff/P22-daemon-deployment-preflight.md` + report - the preflight that must gate
  the apply.
- `src/groop/actions/execute.py` - the P46 kernel you must reuse (and, if P78 has
  merged, the single extracted gate chain).
- `groop/CONTRACTS.md` standing contracts.
- Do **not** read UI, report, MCP, BPF, or collector code.

## Required Contracts

1. **Reuse the P46 kernel.** Same gate chain, same audit record shape, same argv-only
   execution, same fail-closed audit. No new execution path, no shell, no
   `subprocess.run(..., shell=True)`, ever. P78 preserves that audit shape and exposes
   the shared chain privately: if this package adds install-specific validation, make
   it an ordered pre-audit verb gate in that chain. It needs no P49 stale-plan
   post-audit hook; that exception is specific to retaining set-property's existing
   `pre`/`post` stale-refusal trail.
2. **Plan-once.** The plan is built once and carried through display and execution.
   Assert this structurally, not by eye (oracle 1).
3. **Preflight is a gate, not advice.** P22's preflight runs at execute time and a
   failing preflight refuses the install - it does not warn and proceed.
4. **Idempotence is explicit.** Re-running `--execute` against an already-installed
   daemon must be a typed no-op-or-refusal, not a partial re-application. State which
   you chose and why in the REPORT. A half-applied install (unit written, tmpfiles not)
   after a mid-plan failure is the worst outcome: define and test what happens.
5. **Every step is audited individually.** The audit answers "what did groop write to
   this host, and when" for each step - one aggregate "install ran" record is useless in
   an incident (P72 contract 4, generalized).
6. **Nothing is written outside the plan's declared paths.** The set of paths the plan
   may touch is closed and stated; a step that would write elsewhere is refused.
7. **`--execute` is root + `--admin` + typed confirmation** (`INSTALL`), per-verb, per
   P72 contract 3 - not the generic `EXECUTE` token.

## Acceptance Oracles (numbered, adversarial)

All tests use the P46 injectable runner/clock/identity seams and a tmp filesystem root.
**No real systemd, no real root, no host mutation.** Seams stay Python-API-only.

1. **Rendered == executed.** Capture the rendered plan, execute with an injected runner,
   and assert the executed argv sequence is *derived from the same plan object* the
   renderer used - e.g. by asserting equality against the rendered plan's steps, and by a
   test that mutates the plan between render and execute and proves the execution changes
   with it. (A test that merely compares two independently-rendered strings passes against
   the double-render bug this contract forbids.)
2. **Preflight failure refuses, runner never invoked** - assert the runner call list is
   empty, not just that an error was returned.
3. **Mid-plan failure** (step 2 of 4 fails): assert the defined behavior of contract 4 -
   the remaining steps do NOT run, the audit records exactly which steps ran, and the
   result names the partial state.
4. **Re-run against an installed daemon** behaves per contract 4, and the test asserts
   the observable filesystem/argv outcome, not a return flag.
5. **Audit fail-closed:** with the audit writer forced to fail, no step reaches the
   runner.
6. **Non-root / non-`--admin` / wrong token** each refuse before any plan is applied.
7. **A step whose target path is outside the plan's declared set is refused** - engineer
   the fixture so the step looks plausible (a path one directory up), because a validator
   that only rejects obvious junk passes against a traversal.

## Out Of Scope

- Uninstall / rollback (a successor, and it needs its own safety design).
- Starting or enabling the daemon (`systemctl start/enable` is P46's existing verb set;
  install writes the unit, it does not run it).
- Changing the P25 plan's *content*, the unit template, or the socket permissions - this
  package applies the existing plan, it does not redesign it.
- Any remote/multi-host install.

## Docs

`groop/README.md` (quickstart line + work-package row), `docs/OPERATIONS.md` (the install
runbook: what `--execute` does, what it refuses, and how to read the audit afterwards),
`docs/STATUS.md`, `docs/ROADMAP.md` (mark install-execution landed in the v2 bucket).

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/<daemon install test file> -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
python3 -m py_compile <changed files>
git diff --check
```

Run the suite **from the repo root** - four tests shell out via the repo-root-relative
path `groop/src` and fail spuriously otherwise. State in the REPORT which environment
each result came from. Live install on a real host is controller-side evidence on a
deliberate test host, **not** an agent claim - do not run `--execute` against anything
real from the agent sandbox.
