# P93 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/topos-P93-lifecycle-owner-protocol
- Worktree: /workspaces/vbpub/.worktrees/feat/topos-P93-lifecycle-owner-protocol
- Base commit: a874bc4 (branch tip at session start; carries P64/P66/P81/P86/
  P87/P88 already merged)
- Package: P93 - freeze and fixture-test D-016's lifecycle owner-chain
  protocol (`docs/LIFECYCLE-ADAPTERS.md`) and migrate existing Docker/systemd
  actions onto it, without adding any new mutating adapter (Compose/CIU/
  Wings/Podman/Quadlet stay fixture-only).

## Timeline

Append newest entries at the bottom.

```text
2026-07-15 (session start)
- Action: Read the handoff, docs/LIFECYCLE-ADAPTERS.md, docs/ROADMAP.md/
  STATUS.md P93 context, and the existing action kernel: catalog.py,
  execute.py, governance.py, preview.py, audit.py, owner_safety.py (P87),
  kill_ops.py, update_ops.py, cli.py's action wiring, and the P87/P78/action
  test suites (tests/test_p87_owner_safety.py, test_actions.py,
  test_p78_action_kernel.py).
- Result: P87's owner_safety.py is explicitly "not the owner-adapter system
  of P93" but must stay behaviorally identical (its tests assert exact
  refusal reasons/messages: "owner-managed"/"owner-ambiguous"/"protected"/
  "inspect-failed", marker substrings like "Docker Compose"/"CIU"/
  "Pterodactyl", and secret-absence). Confirmed the hard constraint from
  P87's own log: existing P46/P72/P78 tests call the executors WITHOUT any
  inspect seam and must keep succeeding with no Docker present. => the
  protocol must be introduced as a superset that *reuses* owner_safety's
  tested identity/label/message helpers rather than re-deriving them, so
  migration is behavior-preserving by construction and verifiable by
  running the untouched existing suite.
- Decision: design the protocol as (1) pure types + a resolution/
  verification kernel with no adapter-specific logic (lifecycle.py), (2)
  concrete Docker/systemd adapters plus fixture-only Compose/CIU/Wings/Fake
  adapters (lifecycle_adapters.py) that reuse owner_safety for Docker label
  parsing, (3) a protocol-level gated executor for the oracles that need
  revalidation/verification with no existing production analog
  (lifecycle_execute.py), and (4) a minimal, low-risk migration inside
  execute.py: the existing `_make_owner_safety_gate` (used by
  execute_plan/execute_kill/execute_update for Docker verbs) now calls
  `lifecycle_adapters.evaluate_docker_owner_chain` instead of
  `owner_safety.evaluate` directly; a new no-op-by-default
  `_make_systemd_owner_gate` is wired into execute_plan (systemd-*) and
  execute_set_property (memory.high) so systemd verbs are inside the
  protocol's resolution kernel even though no real "who owns this unit"
  detector exists yet.
- Follow-up: implement lifecycle.py, lifecycle_adapters.py,
  lifecycle_execute.py, wire execute.py, write
  tests/test_p93_lifecycle_protocol.py for all 11 oracles, run gates.
```

## Decisions

- Decision: `resolve_authoritative_owner` is structural (chain[0] wins),
  never a preference score, and a `DiscoveryResult.conflict` always blocks
  resolution regardless of chain contents.
  Reason: contract 1 says "precedence is explicit; labels alone never
  confer authorization" and oracle O5 requires conflicting signals to
  surface as a typed conflict, never an automatic pick. Every adapter here
  is responsible for ordering its own chain correctly at discovery time
  instead of the kernel guessing from unordered signals.
  Impact: adding a future owner family only requires that family's adapter
  to order its chain correctly; the kernel never special-cases a family.

- Decision: `evaluate_docker_owner_chain` performs exactly one
  `inspect(target)` call and threads the cached raw payload into
  `DockerAdapter` via a closure, rather than letting the adapter call
  `inspect` a second time for message rendering or the protected-id check.
  Reason: P87 contract 1 ("no TOCTOU") is load-bearing and directly tested
  (`TestContract1SingleInspect`); a second inspect between authorization and
  execution would reopen the exact race P87 closed.
  Impact: `evaluate_docker_owner_chain` is not a thin pass-through to
  `DockerAdapter(inspect=...).discover()`; it deliberately owns the single
  inspect call itself.

- Decision: `ChainConflict.reason` is preserved through
  `resolve_authoritative_owner` (not collapsed to a generic `"conflict"`
  tag).
  Reason: caught during self-review — an early version hardcoded
  `reason="conflict"` in `resolve_authoritative_owner`, which would have
  turned every P87 `"inspect-failed"` refusal into `"owner-ambiguous"` once
  routed through the new kernel, silently changing observable behavior for
  a malformed (but successfully read) inspect payload.
  Impact: `evaluate_docker_owner_chain` passes `resolution.reason` straight
  through to `owner_safety.OwnerSafetyRefusal.reason` unchanged. A
  regression test (`test_evaluate_docker_owner_chain_preserves_inspect_failed_reason`)
  pins this.

- Decision: Compose/CIU/Wings adapters advertise `{Capability.INSPECT}`
  only, and their `plan()` raises `NotImplementedError`.
  Reason: contract 6 / "out of scope" both say their CLI/API invocation is
  out of scope for P93. Modeling this as "zero mutate capability" lets
  oracle O8's generic `check_capability` refusal produce the same effective
  behavior P87's owner-managed refusal already had, through the general
  mechanism rather than a family-specific special case.

- Decision: `execute_via_owner_chain` (lifecycle_execute.py) is new,
  additive code, not wired into the CLI or into any existing execute_*
  function.
  Reason: it is the only place oracles O6/O7/O9 (revalidation, disappeared-
  owner, partial verification) have a concrete implementation to fixture-
  test, but none of Topos's existing actions need revalidation/verification
  today (systemd/Docker standalone execution has always been a single
  inspect-and-go). Building it as a parallel, fully protocol-generic
  executor avoids touching `_execute_gated`'s `ActionKind`/`ActionPlan`-
  typed contract (which is tightly coupled to the fixed catalog and is
  exercised by ~300 existing tests) while still proving the full protocol
  contract end-to-end for any future adapter that plugs in.

- Decision (B-040, "P93 should decide the argv contract"): execution still
  runs against the raw accepted target string, not the resolved canonical
  incarnation id.
  Reason: preview is deliberately inspect-free (side-effect-free by
  design), so switching execute's argv to a canonical id would break
  preview/execute argv parity (`_validate_plan` requires the execute argv to
  match the catalog builder's output for the accepted target). Closing
  B-040 fully requires a preview-time canonical-id resolution decision,
  which is a larger change than "migrate existing planning onto the
  protocol without adding pull/recreate yet."
  Impact: the TOCTOU window B-040 describes is bounded, not closed --
  `OwnerLink.incarnation` pins the canonical id at discovery time and
  `revalidate()` re-checks it immediately before execution (oracle O7), so
  a race during that window now produces a typed `stale` refusal instead of
  a silent misexecution against a reassigned name. B-040 stays open in
  `docs/BACKLOG.md` with this decision recorded in P93-REPORT.md.

## Blockers

- None.

## Validation

Environment: fresh venv in the worktree
(`/tmp/handoffctl-gate-<hash>/bin/pip install -q -e 'topos[dev]'`, exit 0).

- Existing P87/P46/P72/P78 suites, unmodified, to prove O10 (migrated
  protocol, byte-identical behavior):
  `pytest tests/test_p87_owner_safety.py tests/test_actions.py
  tests/test_p78_action_kernel.py -q` -> `340 passed in 1.87s`.
- New protocol suite (all 11 oracles):
  `pytest tests/test_p93_lifecycle_protocol.py -q` -> `42 passed in 0.63s`.
- Full zero-skip suite from the worktree root (matches the handoff's gate
  command exactly): `pytest topos/tests -q` -> `1708 passed in 189.01s
  (0:03:09)`.
- `py_compile` on every new/changed action module: clean.
- `git diff --check`: clean.

## Timeline (continued)

```text
2026-07-15 (implementation)
- Action: Added topos/src/topos/actions/lifecycle.py (OwnerFamily,
  Provenance, Confidence, Capability, OwnerLink, ChainConflict,
  DiscoveryResult, ChainRefusal, LifecyclePlan, VerificationOutcome,
  LifecycleAdapter, resolve_authoritative_owner, check_capability,
  revalidate, verify). Added lifecycle_adapters.py (DockerAdapter,
  SystemdAdapter, ComposeAdapter, CiuAdapter, WingsAdapter, FakeAdapter,
  evaluate_docker_owner_chain). Added lifecycle_execute.py
  (execute_via_owner_chain, reusing execute.py's audit/timeout/identity
  primitives). Wired execute.py's _make_owner_safety_gate to
  evaluate_docker_owner_chain and added _make_systemd_owner_gate
  (no-op-by-default) to execute_plan and execute_set_property. Added
  tests/test_p93_lifecycle_protocol.py (11 oracle classes + protocol-level
  unit tests, 42 tests).
- Result: self-review caught the ChainConflict.reason collapse bug (see
  Decisions) before it reached a test; fixed and re-verified O10 parity.
  All gates green as recorded above.
- Follow-up: none for P93's own scope. B-040 (canonical-id argv/preview
  parity) stays open per the decision above. A real "systemd unit owns this
  Docker container" detector and a real Podman/Quadlet adapter are future,
  separately-versioned work per docs/LIFECYCLE-ADAPTERS.md's implementation
  order.
```

## Port-forward addendum (2026-09-08)

Everything above this line is the ORIGINAL 2026-07-15 implementation record
from branch `feat/groop-P93-lifecycle-owner-protocol` (tip `b5e44773`,
worktree `.worktrees/feat/groop-P93-lifecycle-owner-protocol`), mechanically
`groop`→`topos`-substituted verbatim (module paths, imports, branch/worktree
names, `pytest groop/tests`→`pytest topos/tests`, `groop[dev]`→`topos[dev]`)
with no other edits — the branch's own account is left intact per
DOCTRINE.md §6 ("append the outcome rather than editing the agent's original
entries"). This section is the append-only record of the 2026-09-08
port-forward session.

**Why this port-forward was needed:** the branch above was a complete,
gate-green P93 implementation dated 2026-07-15. It never merged because the
groop→topos rename (`2860d3f5`, 2026-07-16) landed the next day and left the
branch on the old `groop/` path layout. No independent reviewer APPROVE
commit exists for this branch — first-time review is still pending after
this port-forward, which is expected.

**Premise check before porting:** confirmed the current handoff
(`topos/nyxloom-trove/handoffs/topos-P93-lifecycle-owner-protocol.md`,
`input_revision: f77727e`) is byte-identical in substance to the branch's
original spec (`groop/handoff/groop-P93-lifecycle-owner-protocol.md`) — only
the mechanical rename differs (id/project/touch-paths/gate name
`groop-suite`→`topos-suite`/worktree-branch naming). This is a pure
port-forward, not a redesign.

**What this session did:**
- Created worktree `.worktrees/feat/topos-P93-lifecycle-owner-protocol` off
  current `main` (`3dd08b12`), branch
  `feat/topos-P93-lifecycle-owner-protocol`.
- Recreated the branch's 4 new modules at their `topos/` path
  (`actions/lifecycle.py`, `actions/lifecycle_adapters.py`,
  `actions/lifecycle_execute.py`, `tests/test_p93_lifecycle_protocol.py`)
  with `groop.actions`→`topos.actions` substituted in imports — the only
  `groop` references anywhere in those 4 files. Bodies are otherwise
  byte-identical to the branch's originals (confirmed by diffing the
  substituted output against the branch blobs).
- Recreated `P93-LOG.md`/`P93-REPORT.md` here with the same mechanical
  substitution; this addendum and the matching one in `P93-REPORT.md` are
  the only new prose.
- Verified `topos/src/topos/actions/owner_safety.py`,
  `topos/src/topos/actions/catalog.py`'s `DOCKER_EXECUTABLE`/
  `SYSTEMCTL_EXECUTABLE`, and `topos/docs/LIFECYCLE-ADAPTERS.md` — everything
  the ported modules import or reference by name — are unchanged (catalog.py
  had one unrelated comment/dead-check cleanup, not touching the constants
  used here) since the branch's parent commit (`a874bc4`), so no further
  reconciliation was needed beyond `execute.py`.
- Reconciled `actions/execute.py` by hand — the one file `git merge-tree
  --write-tree main feat/groop-P93-lifecycle-owner-protocol` flagged as a
  real content conflict rather than a pure path rename. Detail below.
- Ran the gates fresh in the new worktree. Results below.

**execute.py reconciliation — exact nature of the conflict:**

Diffing `topos/src/topos/actions/execute.py` at the rename commit
(`2860d3f5`) against the branch's parent (`a874bc4`, textually
groop→topos-substituted) produced a 0-line diff — the rename itself changed
nothing but paths in this file. So the only real drift to reconcile was
between the rename-commit snapshot and current `main` (`3dd08b12`), and that
drift is exactly one hunk, entirely unrelated to P93: in
`execute_set_property`'s `stale_revalidation_gate`, `main` now refuses
explicitly when `current_value_reader` raises, instead of the old
behavior of silently treating the value as unreadable —

```python
# before (rename-commit snapshot, = branch's parent behavior)
except BaseException:
    fresh_current_value = None
# current main
except BaseException as exc:
    return _GateRefusal(
        f"current memory.high value could not be read ({type(exc).__name__}); "
        "preview again before execution"
    )
```

P93's own diff never touches that `try/except` — it only appends a new gate
to the same function's `post_audit_gates` tuple, several lines below. The
two changes sit in the same function but on non-overlapping lines with no
semantic interaction: `_make_systemd_owner_gate`'s no-op-by-default gate
runs independently of whether `stale_revalidation_gate`'s read path
succeeds, fails silently (old), or refuses explicitly (current).

**How it was resolved:** applied the branch's `execute.py` edit verbatim
against current `main`'s file, at the (mechanically shifted) line positions,
leaving main's read-failure refusal untouched:
- `_make_owner_safety_gate`: import `lifecycle_adapters` alongside
  `owner_safety`; call `lifecycle_adapters.evaluate_docker_owner_chain(...)`
  instead of `owner_safety.evaluate(...)` (identical args/signature).
- New `_make_systemd_owner_gate(kind, target, owner_lookup)` — no-op when
  `owner_lookup is None` (always true in production) — added verbatim.
- `execute_plan`: new `systemd_owner_lookup` keyword (default `None`) plus
  docstring addition; `_make_systemd_owner_gate(kind, target,
  systemd_owner_lookup)` appended to `post_audit_gates`.
- `execute_set_property`: new `systemd_owner_lookup` keyword (default
  `None`) plus docstring addition; `_make_systemd_owner_gate(
  "systemd-set-property", unit, systemd_owner_lookup)` appended to
  `post_audit_gates` ALONGSIDE (not replacing) `stale_revalidation_gate` —
  main's newer, stricter read-failure refusal is preserved verbatim.

Nothing from either side was dropped or corrupted: main's post-rename
read-failure refusal in `stale_revalidation_gate` is byte-identical to
before this port; P93's gate wiring (both new functions, both new keyword
seams, both `post_audit_gates` insertions) is byte-identical to the
branch's. `execute_kill` and `execute_update` (both also call
`_make_owner_safety_gate`, at unrelated line ranges) needed no per-call-site
edit — they inherit the Docker-verb routing change for free because it
lives inside the gate-factory function itself, exactly as in the original
branch diff. Full diff of the reconciled file against `main`:
`git diff main -- topos/src/topos/actions/execute.py` in this commit
reproduces precisely the branch's original 65-line `execute.py` diff, just
at shifted line numbers.

**Validation (2026-09-08, port-forward session):**

Environment: two fresh venvs (`python3 -m venv ...; pip install -q -e
'topos[dev]'`, exit 0 both times) — one against this worktree, one against
a clean `main` checkout, to separate P93 effects from environment effects.

- `python3 -m py_compile` on all 5 touched/new modules (`lifecycle.py`,
  `lifecycle_adapters.py`, `lifecycle_execute.py`, `execute.py`,
  `test_p93_lifecycle_protocol.py`): clean.
- Existing P87/P46/P72/P78 + boundary suite (`test_p87_owner_safety.py
  test_actions.py test_p78_action_kernel.py
  test_actions_execute_remaining_boundaries.py` — the last file is new on
  `main` since the original 2026-07-15 session, not part of its own O10
  evidence, and passes too) -> `355 passed in 2.18s`. O10 parity survives
  reconciliation.
- New protocol suite: `pytest topos/tests/test_p93_lifecycle_protocol.py -q`
  -> `42 passed in 0.61s` (matches the branch's original 42/42, all 11
  oracles).
- Full zero-skip suite, gate-lane PYTHONPATH
  (`PYTHONPATH=topos/src:topos`): `pytest topos/tests -q` ->
  `4 failed, 2960 passed in 220.93s`. The 4 failures
  (`test_record.py::test_cli_version_reports_package_version`,
  `test_acceptance.py::test_run_smoke_json_fixture_root`,
  `test_acceptance.py::test_subprocess_smoke_json`,
  `test_acceptance.py::test_run_steady_json_small_samples`) are ALL the same
  root cause: each asserts a hardcoded `"0.1.0"` package version, but this
  ad hoc venv's `pip install -e` resolves a `setuptools_scm` dev version
  (`0.2.2.dev992+g3dd08b12.d20260908`) from git describe. Confirmed
  pre-existing and unrelated to P93: the identical 4 tests fail the same way
  installed the same way from a clean `main` checkout at the same commit
  (`3dd08b12`), in a separate venv, with no P93 changes present. Not a
  regression; an artifact of installing outside the gate's controlled
  `tester-unified` image.
- `git diff --check` on the staged changes: clean.
- Official gate lanes (`topos/run-gate.py`, `tester-unified` image, exact
  `topos-suite`/`py-compile` argv from `topos/run-gate.toml`, run after
  committing per the lanes' `clean_tree=true`):
  - `python3 topos/run-gate.py py-compile` -> exit 0, `py-compile: OK`.
  - `python3 topos/run-gate.py topos-suite` -> exit 1: `4 failed, 2960
    passed in 69.84s`, THE SAME 4 tests as the local run above, for the
    same reason.
  - Control proof that these 4 failures are pre-existing and unrelated to
    P93, not a symptom of the port-forward or the reconciliation: ran the
    identical 4 tests directly inside the `tester-unified:local` image
    against the BARE `main` checkout (`git rev-parse HEAD` inside the
    container printed `3dd08b12...`, i.e. zero worktree/branch/P93 content
    present at all) -- same 4 failures, same root cause, same version
    string. Root cause identified: `pyproject.toml`'s `setuptools_scm`
    (`tag_regex = "^topos-v(?P<version>[0-9].*)$"`) now resolves off the
    newest reachable `topos-v*` tag (`topos-v0.2.1`; `git tag` lists
    `topos-v0.1.0`/`topos-v0.2.0`/`topos-v0.2.1`), producing a live dev
    version (`0.2.2.dev992+g<hash>.<date>`); the 4 failing tests hardcode
    the literal `"0.1.0"` and were never updated after the first real
    `topos-v` tag was cut -- pure test staleness, orthogonal to P93 (P93
    touches no version/packaging code). Filed as
    `docs/BACKLOG.md`/`nyxloom-trove/4-backlog.md` B-046/B-047 (numbered
    per each file's own existing max, since the two backlogs were already
    out of sync before this session) so it does not block or confuse
    review of this port-forward, and so it is visible to whoever gates
    the sibling P91 port-forward (same `topos-suite` lane, same failure
    expected there too).
  - Every P93-relevant test (all 42 new oracle tests, all 355 existing
    P87/P46/P72/P78 + boundary tests, and every other test in the 2960
    that DID pass) is green; the lane's overall exit 1 is attributable
    entirely to the 4 pre-existing, unrelated failures above.
