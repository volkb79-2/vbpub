# P72 - Admin action kill/update verbs

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-max
> **Depends-on:** P46 (merged), P49 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none   <!-- but it is the only package allowed in src/topos/actions/ at a time; do not run concurrently with any other actions-area package -->
> **Escalate-if:** a named contract cannot be met as specified; the P46 kernel would need a signature change to carry the new verbs (propose it in the REPORT and BLOCK — do not fork a second execution path)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **roadmap-driven**.
docs/ROADMAP.md "Later" and the P46 report both name kill/update as the
remaining verbs of the v2 action surface: "Real Docker/systemd action
execution" landed in P46 (start/stop/restart), `systemctl set-property`
governance landed in P49, and P46's own report scopes out "Kill, update, TUI
actions, and daemon RPCs" as later packages. This closes the verb set. It is
NOT a review child of anything in the current wave.
-->

## Goal

Add exactly two verbs to the P46 admin action kernel: `kill` (send a signal to a
Docker container or systemd unit) and `update` (apply a bounded set of Docker
resource limits to a running container). Everything else about the P46 posture —
root, `--admin`, typed confirmation, strict argv, timeouts, fail-closed audit —
is inherited unchanged. **This package adds verbs to an existing kernel; it does
not build a second one.**

## Why these two, and why carefully

`kill` and `update` are the first action verbs that can destroy state a user cares
about. `restart` (P46) is recoverable; `kill -9` on a game server is not, and
`docker update --memory` on a running container can OOM it instantly. The P46
kernel already has the right posture. The entire risk here is in the *validation*
of the two new argument surfaces (signal name, resource limits), which are the
first action arguments that are neither a fixed verb nor a validated target.

## Context To Read First (bounded)

- `src/topos/actions/` — the whole P46 kernel: plan construction, target
  validation, typed confirmation, argv builder, timeout, audit writer. Read the
  P46 handoff (`handoff/P46-admin-action-execution-kernel.md`) and
  `handoff/reports/P46-REPORT.md` for the invariants it committed to.
- `handoff/P49-systemd-memory-governance.md` + its report — the most recent
  package to extend this kernel with a *structured, validated* argument
  (`memory.high` value parsing, max/overflow/range checks, stale re-read). It is
  the exemplar to imitate for `update`'s limit parsing.
- `topos/CONTRACTS.md` standing contracts (input trust; bounds enforced then
  proven; test seams Python-API-only).
- Do **not** read UI, daemon, report, MCP, DAMON, or BPF code.

## Required Contracts

### Shared (both verbs)

1. **Reuse the P46 kernel.** Same plan/preview/confirm/execute path, same audit
   record shape, same argv-only execution, same timeout, same fail-closed audit
   (if the audit write fails, the action does not run). No new execution path, no
   new subprocess call site, no new confirmation mechanism. If you find yourself
   copying an argv builder, stop — that is the BLOCKED trigger.
2. **Preview renders exactly what executes** (standing contract: operator-facing
   commands are parameterized and render exactly what preview shows). The
   preview must display the fully-resolved argv including the signal or the limit
   values — not a template.
3. **Typed confirmation is per-verb, not generic.** P46 confirms with a typed
   token; `kill` and `update` must not be confirmable by a token that a user
   memorized for `restart`. State the token scheme in the REPORT.
4. **Audit records name the new arguments.** The signal, or the exact limits
   applied, land in the audit record. An audit line that says only "update" is
   useless in an incident.

### `kill`

5. **Closed signal allowlist.** Accept only `TERM`, `INT`, `HUP`, `KILL`, `QUIT`,
   `USR1`, `USR2` (spelled without the `SIG` prefix; reject `9`, `SIGKILL`, and
   arbitrary strings — a closed enum, not a passthrough). Unknown signal is a
   typed rejection with exit code 2, never a silent default to `TERM`.
6. **`KILL` requires an extra explicit opt-in** beyond the normal typed
   confirmation (e.g. `--force`), because it is the one verb in the whole surface
   that guarantees data loss. Default to graceful.
7. **Protected entities are refused.** The config's `protected_services`
   (CONTRACTS §2/§7, `Entity.is_protected`) must be honored: killing a protected
   entity is refused with a typed error, at plan time, before confirmation.
   P46 already has the target-validation seam for this — use it, and add a test
   that proves a protected target cannot be killed even with `--admin` and a
   correct confirmation token.

### `update`

8. **Closed limit allowlist:** `--memory` and `--cpus` only, on Docker containers
   only. No `--restart`, no `--pids-limit`, no arbitrary passthrough. systemd unit
   resource changes are P49's `set-property` surface — **do not duplicate it
   here**; if a user passes a systemd target to `update`, refuse with a typed
   error pointing at `topos action set-property`.
9. **Byte/range validation reusing P49's parser.** Memory values are parsed by the
   same validated code path P49 built (suffix handling, overflow, max, range) —
   not a second parser. `--cpus` is a bounded positive float with an explicit
   upper bound (host CPU count).
10. **Refuse a memory limit below current usage.** `docker update --memory` to a
    value under the container's current RSS is an immediate OOM-kill. Read the
    container's current `memory.current` at plan time and refuse (typed error,
    plan-time, before confirmation) if the requested limit is below it, unless an
    explicit override flag is passed. State the override's name and its
    confirmation requirement in the REPORT. **This is the contract most likely to
    be silently skipped — it is the reason this package is flash-max and not
    flash-high.**

## Acceptance Oracles (numbered, adversarial)

All tests use the existing P46 injectable runner/clock/identity seams and fixture
cgroup trees. **No live Docker, no live systemd, no real signals.** Seams are
Python-API-only (standing contract) — no new production CLI fixture flags.

1. `kill --signal TERM` on a Docker target: previewed argv == executed argv,
   exact string match, and the audit record contains the signal.
2. `kill --signal 9` and `kill --signal SIGKILL` and `kill --signal bogus` each
   exit 2 with the offending token named. (Proves the allowlist is closed, not a
   passthrough.)
3. `kill --signal KILL` **without** `--force` is refused even with a correct
   confirmation token; with `--force` it proceeds. (Proves the extra gate exists
   and is not decorative.)
4. `kill` against a `protected_services` target is refused at plan time, with
   `--admin` and a correct token supplied. Assert the runner was never invoked —
   not just that an error was returned.
5. `update --memory 512M` on a container whose `memory.current` is 800M is
   refused at plan time; the runner is never invoked. With the override flag it
   proceeds. (Oracle 5 is the one that fails against a plausible-but-wrong
   implementation that validates only syntax.)
6. `update` against a systemd unit target exits 2 and the message names
   `set-property`. (Proves no second governance path was built.)
7. `update --memory <overflow/negative/garbage>` exits 2 via the P49 parser —
   assert the same error taxonomy P49 produces, proving reuse rather than a
   reimplementation.
8. Audit fail-closed: with the audit writer forced to fail, no `kill` and no
   `update` reaches the runner. (Inherited P46 contract — prove it still holds for
   the new verbs, do not assume it.)
9. A non-root / non-`--admin` invocation of either verb is refused before any
   plan is built.

## Out Of Scope

- TUI action integration (a later package; the verbs land CLI-first).
- Daemon RPC exposure of actions (the daemon surface is read-only; P52's envelope
  explicitly rejects mutation-shaped ops).
- `docker update` fields beyond `--memory`/`--cpus`.
- systemd unit resource changes (P49 owns those).
- `kill` on arbitrary PIDs — the target is always a resolved container or unit,
  never a raw process.
- Any MCP/web exposure of these verbs. Actions do not cross that boundary.

## Docs

`topos/README.md` (quickstart line + work-package row), `docs/OPERATIONS.md`
(operator guidance, incl. the `--force`/override semantics and *when not to use
them*), `CONTRACTS.md` if the action-plan shape gains a field,
`docs/ROADMAP.md`/`docs/STATUS.md`.

## Gates

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/<action test files> -q -W error
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error
python3 -m py_compile <changed files>
git diff --check
```

State in the REPORT which environment each result came from. Live Docker/systemd
execution is controller-side evidence on a deliberate test host, **not** an agent
claim — do not run these verbs against anything real from the agent sandbox.
