# P87 - Close Docker action owner and protected-ID bypasses - Implementation Report

## Summary

Added a narrow, fail-closed owner / protected-ID safety gate on Groop's raw
Docker mutation verbs. A single `docker inspect` now resolves an accepted
container identifier to its canonical full id, short id and inspected name, and:

- a container listed in `[tiers] protected_services` by any of those forms is
  refused no matter which form the operator addressed it with (closing the
  64-hex protected-ID bypass); and
- a container owned by Docker Compose, CIU or Pterodactyl/Wings is refused with
  a typed, audited message that names the owner and the safe next step (closing
  the owner bypass).

This is the D-016 stopgap, not the owner-adapter system; P93 owns the full
owner-chain protocol.

## What changed

New file `src/groop/actions/owner_safety.py`:

- `resolve_identity(raw)` derives `ResolvedContainer(full_id, short_id, name)`
  from one inspect payload; returns `None` when identity cannot be established
  (malformed/absent `Id`) so the caller fails closed (contract 7).
- `detect_owner(labels)` classifies provenance from the **existing** label
  names (invents none):
  - Compose `com.docker.compose.project`,
  - CIU `ciu.managed="true"` / `ciu.stack`,
  - Wings `Service="Pterodactyl"` / `ContainerType="server_process"`
    (`TUI-SPEC.md`: "the Docker labels wings sets are exactly `Service=
    Pterodactyl` + `ContainerType=server_process`").
  It collapses the coherent CIU-over-Compose chain (CIU is the top owner, so
  both present is CIU, not a conflict), and fails closed as `owner-ambiguous`
  on two incompatible families (Wings with Compose/CIU) or a partial/
  uninterpretable `ciu.managed` value (contract 6). Unknown labels are not
  ownership (contract 2).
- `evaluate(kind, target, *, inspect, protected_services)` runs the whole gate
  from one inspect: inspect-failure/identity refusal -> ambiguous refusal ->
  owner-managed refusal -> canonicalized protected refusal -> allow. Messages
  are bounded and secret-free (only the compose project / ciu stack identifier
  is surfaced, via a conservative character-class sanitizer; arbitrary label
  values never reach the message).
- `default_owner_inspect` / `default_protected_services` are the production
  seams (one inspect implementation, reused from `collect/dockerjoin`).

`src/groop/actions/execute.py`:

- Added a shared `_make_owner_safety_gate(kind, target, owner_inspect,
  owner_protected_services)` **post-audit** gate builder and appended it to the
  post-audit gate tuple of `execute_plan`, `execute_kill` and `execute_update`.
- Added the API-only `owner_inspect` and `owner_protected_services` keyword
  seams to those three executors.

`src/groop/cli.py`: the `action execute` path wires `owner_inspect=
owner_safety.default_owner_inspect` for the three Docker executors, so the gate
is engaged in production (protected list comes from config).

New tests `tests/test_p87_owner_safety.py` (see oracle mapping below).

## Key design decisions

**Post-audit gate, so refusals are audited as one pre/post pair.** A pre-audit
gate writes zero audit records; oracle 2 and contract 4 require the refusal to
be audited. The gate therefore runs after the durable pre-record and before the
runner, producing exactly one pre/post pair on refusal (verified). The existing
P72 kill pre-audit `protected_gate` is left untouched so its tests pass verbatim;
the new gate is purely additive.

**The `owner_inspect` seam defaults to `None` (no owner layer).** The existing
P46/P72 start/stop/restart/kill/update tests call the executors without any
inspect seam and expect success with no Docker present. An always-on
fail-closed inspect default would refuse those standalone runs. So the inspect
is an opt-in Python-API seam: `None` preserves the legacy path, and once
engaged the gate is fail-closed (a returned `None`/raise/malformed identity is a
refusal, never a name-only fallback). Production enforcement is via the CLI
wiring above. This is the only way to satisfy "existing P46/P72 tests pass
unmodified" together with the fail-closed contract, and it mirrors how the P72
`protected_check` and P49 `current_value_reader` seams already work.

**Single inspect / no TOCTOU.** One `evaluate` call performs exactly one
inspect, deriving identity and labels together, then checks the rules; execution
uses the original (validated) target string and never inspects again. Tests
assert the injected inspect is called exactly once across the whole
authorize->execute path, on both the success and refusal branches.

## Oracle mapping (all in `tests/test_p87_owner_safety.py`)

1. **Protected by canonical id** - `TestOracle1ProtectedCanonicalId`: a
   name-listed protected container is refused when addressed by name, short id
   and full 64-hex id (parametrized), plus the reverse (id-listed, name-
   addressed). `test_full_id_case_is_the_canonicalization_mutation_test`
   documents the mutation: replacing the canonical `{full_id, short_id, name}`
   comparison with a raw `target in protected_services` turns the full-id case
   red. Refusal is audited pre/post.
2. **Owner-managed refusals** - `TestOracle2OwnerManagedRefusals`: Compose, CIU
   and Wings fixtures each refuse `start`/`stop`/`restart` (execute_plan),
   `kill`, a durable `--memory` update, and a `--cpus`-only update, before the
   runner is called, with exactly one pre/post audit pair, and with the secret
   in an unrelated label (`SECRET`) absent from the message while the safe
   compose project / ciu stack identifier is present.
3. **Standalone executes** - `TestOracle3StandaloneExecutes`: a standalone
   fixture runs each verb (success, runner called once, pre/post audit);
   unknown labels do not refuse; and the no-seam legacy path is a no-op. The
   "existing P46/P72 tests pass unmodified" half is proved by the full suite
   (those files are untouched and green).
4. **Owner-ambiguous** - `TestOracle4OwnerAmbiguous`: Wings+Compose,
   Wings+CIU and a partial `ciu.managed` value each fail closed with
   `owner-ambiguous` (runner not called), audited pre/post.
5. **Inspect failure** - `TestOracle5InspectFailure`: inspect returning `None`,
   raising, returning a malformed identity, or an empty list each produce a
   typed refusal with the runner not called and no raw exception text leaked;
   `test_no_name_only_fallback_when_unprotected_and_unlabelled` proves an
   otherwise-safe target is still refused on inspect failure (contract 7).

Plus `TestContract1SingleInspect` (exactly one inspect on both branches),
`TestSystemdKindsUnaffected` (systemd-kill / systemd-restart never inspect and
still succeed), and `TestDetectOwner` / `TestResolveIdentity` unit coverage.

## Test evidence

Environment: fresh worktree venv, `pip install -e './groop[dev]'` (exit 0);
`PYTHONPATH=groop/src` from the worktree root. This is agent-env evidence; the
controller's clean rerun decides.

```
$ pytest groop/tests/test_p87_owner_safety.py groop/tests/test_actions.py \
    groop/tests/test_p72_kill_update.py groop/tests/test_p78_action_kernel.py \
    -q -W error -p no:schemathesis
381 passed in 2.63s

$ timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests \
    -q -W error -p no:schemathesis
1509 passed in 176.57s (0:02:56)
```

Zero skips (P84 gate did not fire). `py_compile` clean on the four changed/new
Python files; `git diff --check` clean. CLI smoke: `action execute` refuses at
the root gate (exit 2, no crash, new wiring exercised); `action preview`
unaffected (exit 0).

## Deviations from the handoff

None in contract. Two implementation notes:

- The `owner_inspect` seam defaults to `None` (opt-in) rather than to a
  production inspector, because a fail-closed default would break the "existing
  P46/P72 tests pass unmodified" requirement. Production is wired through the
  CLI; the gate itself is fail-closed once engaged. (Rationale above.)
- For `docker-update`, the P72 pre-audit current-usage guard still runs before
  the post-audit owner gate. When a container is *both* owner-managed and asked
  for a below-current memory limit, the operator sees the below-current refusal
  rather than the owner refusal; both are refusals and the runner is never
  reached. Tests isolate the owner refusal by passing a safe usage. This
  ordering is a consequence of keeping the P72 guard unmodified.

## Proposed contract changes

None. Additive, package-private code plus API-only seams.

## Known gaps / follow-ups

- This is the D-016 stopgap. The full side-effect-free discovery/plan,
  centralized authorization and migration of existing actions are P93's scope
  (already carved). No backlog entry needed.
- `default_owner_inspect` runs `docker inspect` in production; on a host without
  Docker every Docker mutation is refused as `inspect-failed`. That is the
  intended fail-closed posture for the stopgap.
