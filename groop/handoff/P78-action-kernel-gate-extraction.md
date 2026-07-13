# P78 - Action kernel gate-chain extraction (one executor, four verbs)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P46 (merged), P49 (merged), P72 (merged)
> **Base:** main after P72 merge
> **Session-hint:** fresh
> **Serialize-with:** none   <!-- but it OWNS src/groop/actions/ for its duration: do not run any other actions-area package concurrently -->
> **Escalate-if:** the extraction cannot preserve a single existing refusal message, audit record field, or exit code byte-for-byte; or it requires changing any public signature in `actions/__init__.py`'s `__all__`. Both are BLOCKED conditions — write the reason to the LOG and stop. Do NOT "improve" behavior while refactoring it.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P72 pass #2).
P72's review found three safety gates inert in production. Two of them were inert
*because* the gate chain is copied per verb rather than shared: execute_kill and
execute_update are ~200-line transcriptions of execute_plan's body, and each
transcription is a chance to drop a gate. execute_set_property (P49) is a third
copy. The next verb will be a fourth. This is the structural debt behind P72's
findings, flagged in P72-REVIEW.md "Accepted as-is" and carved rather than churned
mid-wave.
-->

## Goal

`src/groop/actions/execute.py` now contains **four** near-identical executors —
`execute_plan`, `execute_set_property`, `execute_kill`, `execute_update` — each
re-implementing the same P46 chain: admin gate, typed-confirmation gate, root gate,
timeout validation, audit-path validation, identity coercion, pre-audit write, argv
build, target revalidation, bounded runner invocation, result normalisation, duration
clamping, post-audit write, audit-failure result.

Collapse them into **one** gated executor that the four verbs parameterize. Behavior
must not change in any observable way.

## Why this is worth a package (and why it is sonnet5-high)

This is not tidiness. P72's review found that `kill` shipped with a protected-entity
check that always returned `False` and an OOM guard whose reader could never resolve a
target — in a file where the correct pattern was sitting 200 lines above, in a copy the
author had transcribed by hand. A verb-specific gate that has to be *remembered* in
each copy is a gate that will eventually be forgotten in one; the P46 posture is only
as strong as its most recently copy-pasted executor.

The tier is high because a refactor of the privileged execution path is the one place
where "no behavior change" must be literally true, including in failure and audit
paths, and where a plausible-looking rewrite that passes the suite can still have
dropped a gate on an unexercised branch.

## Context To Read First (bounded)

- `src/groop/actions/execute.py` — all four executors, end to end. The differences
  between them are the specification of this package; enumerate them before writing
  anything.
- `src/groop/actions/kill_ops.py`, `update_ops.py`, `governance.py` — the per-verb
  validation and argv builders. These stay where they are; only the *chain* moves.
- `handoff/reports/P72-REVIEW.md` — what the copies cost, concretely.
- `groop/CONTRACTS.md` standing contracts.
- Do **not** read UI, daemon, report, MCP, DAMON, BPF, or collector code.

## Required Contracts

1. **One chain.** After this package there is exactly one implementation of the
   gate/audit/runner sequence. The verbs supply, by parameter: the confirmation token
   (`EXECUTE`/`KILL`/`UPDATE`), the argv builder, the kind string, and an optional
   ordered list of **pre-argv verb gates** (closures returning a refusal reason or
   `None`) — which is what the signal allowlist, the `--force` gate, the protected
   check, the systemd-target refusal, the below-current guard, and P49's stale re-read
   all are.
2. **Verb gates run before the pre-audit write and before the runner**, in the order
   the verb declares. P72's plan-time refusals must remain plan-time refusals: no verb
   gate may move to after the audit-first write.
3. **Byte-identical observable behavior.** Every refusal string, every `outcome` /
   `audit_outcome` value, every audit record field and its ordering, and every CLI exit
   code is unchanged. This is the whole risk surface of the package: if a message must
   change, it is a BLOCKED condition, not a judgement call.
4. **Public API unchanged.** `execute_plan`, `execute_set_property`, `execute_kill`,
   `execute_update` keep their exact signatures and keyword names — they are exported
   in `actions/__init__.py.__all__` and used by `cli.py`. Callers must not notice. The
   shared executor is package-private.
5. **The existing test seams keep working unchanged** (runner / clock / identity /
   root_check / protected_check / current_memory_reader), and remain Python-API-only.
6. **No new gate, no removed gate, no reordered gate** — except as forced by contract
   2, which is a restatement of current behavior, not a change to it.

## Acceptance Oracles (numbered, adversarial)

The whole suite is the primary oracle: `test_actions.py` (200 tests) and
`test_p72_kill_update.py` (51) must pass **unmodified**. A diff that edits an existing
action test to make the refactor pass is an automatic review reject (standing contract:
never weaken existing tests to make new code pass). Beyond that:

1. **Differential refusal taxonomy.** For every (verb x gate-failure) pair — non-admin,
   wrong token, non-root, bad timeout, relative audit path, invalid identity, pre-audit
   failure, invalid target, bad signal, KILL-without-force, protected target, systemd
   target to update, below-current, unverifiable usage, runner OSError, runner timeout,
   post-audit failure — assert the exact `outcome`, `audit_outcome` and `stderr` string.
   Build this table against `main` **before** refactoring (record it in the LOG), then
   assert the refactored code reproduces it exactly. This is the oracle that fails
   against a plausible-but-wrong extraction.
2. **Audit-record equality.** For one success and one refusal per verb, capture the
   written JSONL audit records before and after the refactor and assert they are equal
   field-for-field (modulo the injected clock/identity).
3. **Gate-ordering proof.** For each verb, a test that makes *two* gates fail at once
   and asserts which refusal wins — proving the order is preserved, not just the set.
   (E.g. `kill` with both a bad signal and a protected target; `update` with both a
   systemd target and an unverifiable usage — the latter is a real ordering bug this
   wave already had to fix once, in the preview path.)
4. **Fail-closed preservation.** The audit-first contract still holds for all four
   verbs: with the pre-audit writer forced to fail, no verb reaches its runner.
5. **Line-count evidence.** The REPORT states `execute.py`'s line count before and
   after. This is not the goal, but a refactor that does not substantially shrink it
   has not done the job (expect ~600+ lines of duplication to go).

## Out Of Scope

- Any behavior change, message change, or new verb.
- Moving verb validation out of `kill_ops`/`update_ops`/`governance` — only the chain
  moves.
- TUI action integration, daemon RPC exposure of actions (still out of scope, still no).
- Touching `squeeze.py`'s session posture (it audits per-session by design, not per-step).

## Docs

`docs/ARCHITECTURE.md` (module map line for `actions/`), `handoff/reports/P78-REPORT.md`.
`CONTRACTS.md` only if a public signature changes — which contract 4 forbids, so it
should not.

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_actions.py groop/tests/test_p72_kill_update.py -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
python3 -m py_compile <changed files>
git diff --check
```

Run the suite **from the repo root** — four tests shell out via the repo-root-relative
path `groop/src` and fail spuriously otherwise. State in the REPORT which environment
each result came from. There is one known-failing test on `main`
(`test_zst_without_zstandard_exits_2`, unrelated, carved as P79); your run should show
that one and no other.
