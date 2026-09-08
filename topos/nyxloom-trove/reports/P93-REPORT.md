# P93 - Lifecycle owner-chain protocol and action migration - Implementation Report

## Summary

Froze D-016's owner-chain adapter contract (`docs/LIFECYCLE-ADAPTERS.md`) as
executable types plus a shared resolution/verification kernel, fixture-tested
it against all 11 handoff oracles, and migrated the existing Docker/systemd
action verbs onto it without adding any new mutating adapter. Compose, CIU
and Wings get real, tested discovery/capability contracts but no adapter here
invokes their CLI/API (`plan()` raises for all three) — that invocation
stays out of scope, per the handoff.

P87's `owner_safety.py` stopgap is now internally superseded (Docker verbs
resolve through the owner-chain kernel), while remaining byte-identical for
every existing caller: its own direct API (`owner_safety.evaluate`) is
untouched, and the new migration bridge reuses its identity/label/message
helpers so refusal reasons and messages are unchanged. The full existing
suite (P87/P46/P72/P78/actions, 340 tests) passes unmodified, and the full
zero-skip suite (1708 tests, including the 42 new P93 tests) is green.

## What changed

New file `src/topos/actions/lifecycle.py` — the protocol core, with no
adapter-specific logic:

- `OwnerFamily`, `Provenance`, `Confidence`, `Capability` — closed enums.
  `OwnerFamily` currently has `DOCKER`, `SYSTEMD`, `COMPOSE`, `CIU`, `WINGS`
  and a test-only `FAKE`; future families (Podman/Quadlet, Kubernetes, ...)
  stay out per contract 6 until they get their own adapter.
- `OwnerLink` — one chain link: family, identity, `incarnation` (the
  comparable instance id revalidation compares), provenance, confidence,
  capabilities, bounded `detail`, `state`.
- `DiscoveryResult` — `chain` (index 0 = most authoritative, last = the raw
  object) plus an optional `conflict` that always blocks resolution.
- `LifecyclePlan` — immutable; exactly one of `argv`/`api_intent` is set
  (enforced in `__post_init__`), plus reversibility/persistence/timeout.
- `LifecycleAdapter` — the base contract (`discover`, `capabilities`,
  `plan`); no method here may mutate anything.
- Kernel functions:
  - `resolve_authoritative_owner(discovery)` — chain[0] wins structurally; a
    `conflict` always refuses, preserving its original `reason` tag (oracle
    O5).
  - `check_capability(owner, action)` — absence of a capability is a typed
    `"unsupported"` refusal, never a fallback (oracle O8).
  - `revalidate(adapter, target, expected)` — re-discovers and refuses
    `"disappeared"` if a *different* (or no) owner is now authoritative, or
    `"stale"` if the same owner's incarnation changed (oracles O6/O7). A
    fallen-back-to owner is always treated as disappeared, never accepted.
  - `verify(owner_check, runtime_check)` — independent owner-level/observed-
    runtime booleans; a raising check counts as failed, never swallowed
    (oracle O9).

New file `src/topos/actions/lifecycle_adapters.py`:

- `DockerAdapter` — one `inspect` call, reusing `owner_safety.resolve_identity`
  / `detect_owner` (not re-derived) to build the chain. Compose/CIU/Wings
  labels insert the matching fixture link ahead of the docker link; an
  injectable `systemd_owner_lookup` (test-only, `None` in production) proves
  the systemd-owns-docker precedence case (oracle O2) without shipping a
  real detector for it.
- `SystemdAdapter` — self-owning by default (no real "who owns this unit"
  detector exists); an injectable `owner_lookup`/`invocation_reader` seam
  reserved for a future owner-above-systemd adapter (Podman/Quadlet).
- `ComposeAdapter` / `CiuAdapter` / `WingsAdapter` — fixture-only: `discover()`
  raises (use `describe_link()` with data `DockerAdapter` already obtained),
  `plan()` raises, capabilities are `{INSPECT}` only. No CLI/API call
  anywhere in this module for these three (oracle O11-adjacent; the direct
  O11 proof uses `FakeAdapter`, a family with zero production ties).
- `FakeAdapter` — test-only, no ties to any real family; used to prove
  discovery/plan are fully side-effect-free (oracle O11).
- `evaluate_docker_owner_chain(kind, target, *, inspect, protected_services)`
  — the P93 migration bridge. Performs the single `inspect` call itself,
  builds the chain via `DockerAdapter`, resolves it, and on an owner-managed
  result reuses `owner_safety._owner_message` for the exact message text
  P87 always produced. Returns `owner_safety.OwnerSafetyRefusal` so
  `execute.py`'s existing gate wiring needs no shape change.

New file `src/topos/actions/lifecycle_execute.py`:

- `execute_via_owner_chain(adapter, target, action, ...)` — the oracle-facing
  (O5-O9) gated executor: discover → resolve → capability-check → plan →
  [root/admin/confirm/timeout gates] → durable pre-audit → revalidate →
  runner → verify → durable post-audit, with typed outcomes (`conflict`/
  `owner-ambiguous`/`inspect-failed`, `unsupported`, `disappeared`, `stale`,
  `partial`, `success`, ...) at every refusal point. Reuses `execute.py`'s
  `AuditIdentity`, `_production_identity`, `_coerce_identity`,
  `_validate_timeout`, `_write_execution_audit_pre/post`, `_default_runner`
  and `_bound_output` rather than re-implementing audit-file safety or
  output bounding. Not wired into the CLI (see Key design decisions).

`src/topos/actions/execute.py`:

- `_make_owner_safety_gate` now calls `lifecycle_adapters.
  evaluate_docker_owner_chain` instead of `owner_safety.evaluate` directly.
  No change to its signature, its no-op-when-`owner_inspect=None` contract,
  or its post-audit-gate position.
- New `_make_systemd_owner_gate(kind, target, owner_lookup)` — a post-audit
  gate that is a no-op whenever `owner_lookup is None` (always true in
  production today). Wired into `execute_plan`'s post-audit gates (new
  `systemd_owner_lookup` keyword, default `None`) and into
  `execute_set_property`'s post-audit gates (same keyword) so `memory.high`
  governance is inside the protocol too, per contract 5.

New tests `tests/test_p93_lifecycle_protocol.py` (42 tests; see oracle
mapping below).

No changes to `owner_safety.py`, `catalog.py`, `governance.py`, `preview.py`,
`audit.py`, `kill_ops.py`, `update_ops.py` or `cli.py`.

## Key design decisions

**Reuse, don't re-derive, P87's tested helpers.** `DockerAdapter.discover`
and `evaluate_docker_owner_chain` call `owner_safety.resolve_identity`,
`owner_safety.detect_owner`, `owner_safety._extract_labels`,
`owner_safety._owner_message` and `owner_safety._is_protected` rather than
re-implementing label parsing or message text. This is what makes the
migration behavior-preserving *by construction*: a
`TestOracle10MigrationParity` test in the new suite additionally asserts
`evaluate_docker_owner_chain` produces identical `(reason, message)` to
`owner_safety.evaluate` across every label combination the P87 suite covers,
and the full unmodified P87/P46/P72/P78 suite (340 tests) stays green.

**Single inspect, still.** `evaluate_docker_owner_chain` performs exactly one
`inspect(target)` call and threads the cached payload through a closure into
`DockerAdapter`, rather than letting the adapter (or the protected-id check)
call `inspect` again. P87's contract 1 ("no TOCTOU") is directly tested by
`TestContract1SingleInspect` in the existing suite and stays true through the
migration.

**`execute_via_owner_chain` is new and unwired, not a CLI replacement.**
Every existing Topos action (Docker/systemd start/stop/restart/kill/update,
memory.high set-property) has always been a single-inspect-and-go operation
with no revalidation or post-execution verification step, because nothing
above it has ever been an intermediate owner whose state could change
between planning and execution. Oracles O6/O7/O9 require that machinery to
exist and be tested; building it as a new, fully protocol-generic executor
(rather than extending `_execute_gated`'s `ActionKind`/`ActionPlan`-typed
contract, which ~300 existing tests pin) proves the full contract without
risking the existing kernel. A future mutating adapter (e.g. a real
Podman/Quadlet action) is the natural next caller of this executor.

**Compose/CIU/Wings capabilities are `{INSPECT}` only.** Rather than special-
casing "these three families always refuse mutation," the protocol expresses
it as an ordinary capability gap: their adapters simply never advertise a
mutate capability (because their `plan()` — which would build the real
argv/API call — is out of scope), so `check_capability` refuses any mutate
action through the same generic mechanism a Docker/systemd capability gap
would use. `evaluate_docker_owner_chain` still renders the P87-style
owner-specific message ("container is managed by Docker Compose; use
...") rather than the generic `check_capability` message, to keep byte-
identical existing behavior; a caller of the general protocol (e.g.
`execute_via_owner_chain`) gets the generic `"unsupported"` message instead.

**systemd verbs get a real (currently no-op) protocol gate, not silence.**
`_make_systemd_owner_gate` is unconditionally `None`-returning in production
today (no `owner_lookup` is wired anywhere), so this is a zero-risk addition
verified by the full suite. It exists so contract 5's "migrate ... systemd
actions ... to the protocol" is true in the shipped code path, not only in
fixture tests, and so a future owner-above-systemd adapter has exactly one
place to plug in.

## Oracle mapping (all in `tests/test_p93_lifecycle_protocol.py`)

1. **Standalone Docker** — `TestOracle1Standalone`: a labelless container's
   discovery chain has exactly one link (itself); unrelated labels don't
   fabricate an owner.
2. **Systemd-owned service** — `TestOracle2SystemdOwnsContainer`: an injected
   `systemd_owner_lookup` produces `[SYSTEMD, DOCKER]`; systemd resolves
   authoritative; the raw container link is never chosen.
3. **Compose-owned** — `TestOracle3Compose`: a compose-labelled container
   resolves Compose authoritative with the sanitized project as `detail`;
   no compose label means standalone Docker resolves instead.
4. **CIU/Wings chains** — `TestOracle4CiuWings`: CIU and Wings each resolve
   authoritative through their fixture adapters; CIU supersedes a
   simultaneous Compose signal (the coherent CIU-over-Compose chain, reused
   from `owner_safety.detect_owner`); `ComposeAdapter`/`CiuAdapter`/
   `WingsAdapter.discover()`/`.plan()` all raise `NotImplementedError`
   (no CLI/API path exists to invoke).
5. **Conflicting labels** — `TestOracle5Conflict`: Wings+Compose together is
   a typed `owner-ambiguous` `ChainConflict`/`ChainRefusal`, never an
   automatic pick; a malformed (but successfully read) inspect payload stays
   `inspect-failed`, not `owner-ambiguous` (regression-pinned after the
   self-review fix in P93-LOG.md).
6. **Disappeared owner** — `TestOracle6Disappeared`: `revalidate()` refuses
   `"disappeared"` when the previously authoritative owner is no longer
   resolvable; `execute_via_owner_chain` refuses before the runner is ever
   called when discovery "falls back" to a different, lower-precedence
   owner on revalidation.
7. **Stale incarnation** — `TestOracle7Stale`: `revalidate()` refuses
   `"stale"` when the same owner's incarnation changed since planning;
   `execute_via_owner_chain` refuses a stale plan without running; an
   unchanged incarnation proceeds.
8. **Unsupported action** — `TestOracle8Unsupported`: `check_capability`
   refuses a Compose owner's `RESTART` (`{INSPECT}` only) while allowing
   `INSPECT`; `execute_via_owner_chain` against a Wings-owned target refuses
   with `outcome == "unsupported"` and never calls the runner.
9. **Verification failure / partial audit** — `TestOracle9PartialVerification`:
   a failing `owner_check`/`runtime_check` (either or both) after a
   successful runner call produces `outcome == "partial"` with a durable
   two-line pre/post audit record; a raising check counts as failed via the
   `verify()` kernel unit tests; no verification configured stays `success`.
10. **Existing tests pass unchanged** — `TestOracle10MigrationParity`:
    `evaluate_docker_owner_chain` is asserted identical to
    `owner_safety.evaluate` across every P87 label fixture, plus direct
    no-op/no-inspect-seam checks and a systemd no-op-gate execution check.
    The stronger proof is the full untouched `test_p87_owner_safety.py` /
    `test_actions.py` / `test_p78_action_kernel.py` suite staying green.
11. **Side-effect-free fake adapter** — `TestOracle11SideEffectFree`:
    `subprocess.run`/`Popen`/`check_output`/`check_call` are monkeypatched to
    raise `AssertionError` if called, then `FakeAdapter.discover()`/`.plan()`
    run without triggering them; repeated calls are proven idempotent.

Plus `TestLifecyclePlanInvariant` (the argv-xor-api_intent invariant),
`TestSystemdAdapterSelfOwning` (bare-unit self-ownership and argv shape).

## Test evidence

Environment: fresh venv in the worktree,
`pip install -q -e 'topos[dev]'` (exit 0).

```
$ pytest tests/test_p87_owner_safety.py tests/test_actions.py \
    tests/test_p78_action_kernel.py -q
340 passed in 1.87s

$ pytest tests/test_p93_lifecycle_protocol.py -q
42 passed in 0.63s

$ cd .. && pytest topos/tests -q          # exact handoff gate command
1708 passed in 189.01s (0:03:09)
```

Zero skips. `py_compile` clean on `lifecycle.py`, `lifecycle_adapters.py`,
`lifecycle_execute.py`, `execute.py`. `git diff --check`: clean.

## Deviations from the handoff

None in contract. One scoping note:

- B-040 ("Docker verbs still execute by the raw accepted name/ID string...
  P93 should decide the argv contract") is decided but not closed: execution
  still runs against the raw accepted target, because switching to the
  resolved canonical incarnation id would require preview to also resolve
  identity (currently deliberately inspect-free) to keep preview/execute
  argv parity — a larger change than "migrate existing planning ... without
  adding pull/recreate yet." The decision and its bounded-not-closed
  rationale (via `OwnerLink.incarnation` + `revalidate()`) is recorded in
  `P93-LOG.md`'s Decisions section. B-040 stays open in `docs/BACKLOG.md`.

## Proposed contract changes

None. Additive modules plus two small, no-op-by-default keyword seams on
`execute_plan`/`execute_set_property` (`systemd_owner_lookup`); one existing
gate's internals rerouted through a behavior-preserving bridge function.

## Known gaps / follow-ups

- No real "which systemd unit owns this Docker container" detector exists
  in production (oracle O2 is fixture/protocol-level only, via
  `DockerAdapter`'s injectable `systemd_owner_lookup`). Building one is
  future, separately-scoped work.
- No real "which higher owner manages this systemd unit" detector exists
  either (`SystemdAdapter.owner_lookup` is the reserved seam for a future
  Podman/Quadlet adapter, per `docs/LIFECYCLE-ADAPTERS.md`'s implementation
  order).
- `execute_via_owner_chain` is not reachable from the CLI. It is exercised
  entirely by `tests/test_p93_lifecycle_protocol.py` today; the first real
  mutating adapter built on this protocol (Podman/Quadlet per the
  recommended implementation order) is expected to be its first production
  caller.
- B-040 (canonical-id argv/preview parity) stays open, decision recorded
  above.

## Port-forward addendum (2026-09-08)

Everything above this line is the ORIGINAL 2026-07-15 report from branch
`feat/groop-P93-lifecycle-owner-protocol` (tip `b5e44773`), mechanically
`groop`→`topos`-substituted verbatim with no other edits. This section
records the 2026-09-08 port-forward that recreated this implementation at
its intended `topos/` location after it fell through the groop→topos rename
(`2860d3f5`, 2026-07-16) unmerged. Full narrative, including the exact
`execute.py` reconciliation, is in `P93-LOG.md`'s matching addendum.

**Port-forward summary:** the branch's 4 new modules and this
LOG/REPORT pair were recreated at their `topos/` paths with only
`groop.actions`→`topos.actions` import substitution (no other code changes;
confirmed byte-identical to the branch's blobs otherwise).
`actions/execute.py` — the only file with real drift since the branch's
parent commit — was reconciled by hand: applied the branch's P93 gate-wiring
diff verbatim against current `main`'s file, which had independently
acquired one unrelated hunk (a stricter, explicit refusal instead of a
silent `None` when `execute_set_property`'s `current_value_reader` raises).
The two changes touch the same function on non-overlapping lines; both are
preserved intact.

**Fresh validation (this session, worktree
`.worktrees/feat/topos-P93-lifecycle-owner-protocol`, branch
`feat/topos-P93-lifecycle-owner-protocol`):**

```
$ pytest topos/tests/test_p87_owner_safety.py topos/tests/test_actions.py \
    topos/tests/test_p78_action_kernel.py \
    topos/tests/test_actions_execute_remaining_boundaries.py -q
355 passed in 2.18s

$ pytest topos/tests/test_p93_lifecycle_protocol.py -q
42 passed in 0.61s

$ PYTHONPATH=topos/src:topos pytest topos/tests -q     # gate-lane PYTHONPATH
4 failed, 2960 passed in 220.93s (0:03:40)
```

The 4 failures are a single root cause (hardcoded `"0.1.0"` version assertion
vs. this ad hoc venv's `setuptools_scm`-derived dev version) reproduced
identically against a clean `main` checkout at the same commit in an
isolated venv with no P93 changes present — pre-existing and unrelated to
this port-forward, not a regression.

`py_compile` clean on `lifecycle.py`, `lifecycle_adapters.py`,
`lifecycle_execute.py`, `execute.py`, `test_p93_lifecycle_protocol.py`.
`git diff --check`: clean.

**Official gate (`topos/run-gate.py`, `tester-unified` image):**
<results recorded after commit, per the lanes' `clean_tree=true`
requirement — see below>

**Review status:** no independent reviewer APPROVE commit exists for the
original branch or this port-forward. First-time adversarial review is the
next step, unchanged from what the handoff always required.
