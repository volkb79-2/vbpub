# ciu-P50 — REPORT

CIU-94 (per-container `memory.min` injection + admission control) and CIU-95
(`memory_recursiveprot` check + downward slice enumeration), built in v7 per
`nyxloom-trove/decisions.md` D-012.

Gate: `GATE_VERDICT_1`

---

## Part A — cgroupfs slice enumeration (the shared primitive)

**Shipped.** `src/ciu/governance.py`:

| Symbol | Line | Notes |
|---|---|---|
| `CGROUP_ROOT` | 307 | `Path("/sys/fs/cgroup")`, module-level, overridable per call |
| `slice_cgroup_path` | 1004 | reverses `slice_ancestor_chain` into nested dirs; no I/O |
| `_read_memory_min_bytes` | 1026 | `0` for unset/`max`/unreadable; does NOT gate (callers do) |
| `enumerate_slice_children` | 1051 | `(None, note)` abstain / `([], note)` definitive-zero / list of `(name, bytes)` |

Implemented as a `pathlib` walk, not `systemctl`/`docker` per child, per
CGROUP-NOTES.md's own text and `mdt-slice-audit.py`'s prior art. Only Part D's
write uses `systemctl`.

The `None`-vs-`[]` split is the load-bearing part and is tested as such:
`None` means "cannot see the host's real tree" (a devcontainer's own
`/sys/fs/cgroup` is its OWN namespace-local root — walking it would enumerate a
*different* tree, not a smaller view of the right one), while `[]` means "the
slice is inactive or genuinely empty," which is an answer. Conflating them
would make an empty guaranteed slice permanently un-admittable.

**Oracles (`tests/tests/test_ciu_governance.py`, all `tmp_path`-built fake
cgroup trees — no subprocess, no real filesystem outside `tmp_path`):**

- `TestSliceCgroupPath` — 5 tests. Includes
  `test_default_root_is_the_real_cgroup2_mount_point`, which asserts the
  PRODUCTION default is `/sys/fs/cgroup`: every other test parameterises
  `cgroup_root`, so a wrong default would otherwise be invisible to the whole
  suite while silently walking the wrong tree on a real host.
- `TestEnumerateSliceChildren` — 5 tests: the two-child tree from the handoff
  (`[("docker-a.scope", 83886080), ("docker-b.scope", 0)]`, `"max"` → 0);
  interface FILES in the slice dir are not counted as occupants; a child with
  no `memory.min` at all reads 0; an absent slice dir → `([], "not active")`;
  and not-host-rooted → `(None, ...)` with `Path.iterdir` monkeypatched to
  raise `AssertionError` if touched (this file's own
  `test_no_systemctl_never_calls_subprocess` idiom).

## Part B — CIU-95(a) `memory_recursiveprot`

**Shipped.** `governance.check_memory_recursiveprot` (`governance.py:1105`);
call site `deploy.py:1568`, inside `governance_slice_preflight`, guarded by
`if mem_min_required:`, `severity="ERROR"`, tagged `[S15.22]`.

Built exactly as the handoff specified: a separate function called once, NOT
folded into `check_slice_memory_min`/`check_memory_min_ancestor_chain`. Reads
`/proc/mounts` directly rather than shelling to `findmnt`.

One implementation detail worth flagging because it is a real bug class the
handoff did not have to name: mount options are compared as whole
comma-separated **tokens**, not with a substring test. A naive
`"memory_recursiveprot" in line` would report a host mounted
`nomemory_recursiveprot` as protected — the exact false-green this check
exists to prevent. `test_substring_lookalike_option_is_not_a_match` pins it.

**Oracles:**

- `TestCheckMemoryRecursiveprot` (governance) — 5 tests: flag present → True;
  absent → False with the observed options in the note; the `no…` lookalike →
  False; no `cgroup2` line at all → `(None, "not a cgroup-v2 host")` (a
  cgroup-v1 host has no such flag to be missing, so False there would be a
  false accusation); not host-rooted → `(None, ...)` with `Path.read_text`
  monkeypatched to raise if called.
- `test_governance_slice_preflight_raises_when_recursiveprot_missing_and_mem_min_declared`
  (deploy) — `pytest.raises(ValueError, match=r"\[S15\.22\]")` under the
  **default** config, no `ciu.exit_on` override. It additionally monkeypatches
  `check_memory_min_ancestor_chain` to raise `AssertionError` if reached,
  proving the host-wide finding is fatal on its own rather than incidentally
  co-occurring with a chain failure.
- `test_governance_slice_preflight_recursiveprot_present_does_not_raise` — the
  control: proves the raise above is caused by the FINDING, not by the check
  merely running.
- `test_governance_slice_preflight_skips_recursiveprot_check_when_no_mem_min_declared`
  — the mocked function raises `AssertionError` if called (the `fail_mem_min`
  idiom); asserts it is never called, not merely that nothing raised.

### Controlled wrong implementation #1 — the ERROR-vs-WARN choice

Reverted the call site's `severity="ERROR"` to `severity="WARN"` and re-ran:

```
FAILED tests/tests/test_ciu_deploy_actions.py::test_governance_slice_preflight_raises_when_recursiveprot_missing_and_mem_min_declared
1 failed, 2 passed, 161 deselected
```

The severity choice is load-bearing and pinned, not incidental. Source
restored immediately.

The ERROR-vs-WARN blast-radius reasoning is stated in three places, not only
the code: SPEC S15.22 ("Why ERROR here, when the `[S15.16]` ancestor-chain
finding immediately below it in the same function stays WARN"), the call site's
own comment, and the test's docstring.

**One consequence of the specified insertion point, reported rather than
silently changed:** the handoff places this check "right before the existing
`mem_min_required` loop," which is *before* the `if missing: raise
[S15.G9-1]` block further down. So on a host that both lacks the mount flag
and is missing a slice, the `[S15.22]` ERROR raises first and the missing-slice
abort is not reached that run. Both conditions are fatal and both messages are
actionable, so this is a message-ordering nuance rather than a defect — but it
is a real behavioral consequence of the specified position, so it is named here
rather than quietly relocated.

## Part C — CIU-94(b) admission control

**Shipped.** `governance.check_mem_min_admission` (`governance.py:1170`);
`deploy.mem_min_admission_check` (`deploy.py:1236`); call site
`deploy.py:2079`, in `action_deploy`'s per-entry loop immediately before
`_run_stack`, guarded `rendered is not None and not no_preflight and not
dry_run` (the same guard the per-phase `provisioning_preflight` call uses).
Kept strictly separate from `governance_slice_preflight`, which remains a
one-time up-front check of a different question.

The gating order the handoff singled out is implemented as specified:
`_systemd_is_pid1()` is checked FIRST, before the ceiling read and before
`enumerate_slice_children` — `_read_memory_min_bytes` deliberately does not
gate on its own, so a later check would have already read the devcontainer's
namespace-local tree.

**Shared helper (recommended, not required):** implemented as
`deploy._resolve_entry_governance` (`deploy.py:1186`), and
`governance_slice_preflight`'s own loop body was rewritten to call it. Behavior
is unchanged — every pre-existing preflight test (shipped-stack skip,
unrendered skip, invalid-shape skip, governance-disabled skip, non-`.slice`
skip, and the `resolve_cgroup_parent` propagating raise) still passes
untouched.

**Optional-call-site behavior:** a `[S15.23]` raise from this check fails the
phase through the same path a `provisioning_preflight` failure does (entry to
`failed`, `phase_failed = True`, `stop_remaining` unless `--ignore-errors`) —
no new exit-code plumbing.

**Oracles:**

- `TestCheckMemMinAdmission` (governance) — 7 tests over `tmp_path` cgroup
  trees: ceiling unset/`max` → `(None, "no live memory.min of its own")`;
  under-ceiling → True; exactly-full → True; one byte over → False with
  ceiling, live sum, candidate AND the per-occupant breakdown all asserted
  present in the note; an inactive slice's full ceiling available to the first
  claimant; occupants with no declared claim consuming 0; and the gating-order
  test below.
- `test_..._not_host_rooted_abstains_before_reading_ceiling_or_enumerating` —
  monkeypatches BOTH `Path.read_text` and `enumerate_slice_children` to raise
  `AssertionError` if called. This is the test that actually pins the docstring's
  gating-order requirement rather than just describing it.
- deploy side — `test_mem_min_admission_check_raises_when_over_ceiling`
  (`[S15.23]` ValueError under default config),
  `..._passes_when_under_ceiling`, `..._skips_when_no_ceiling_configured`,
  `..._skips_entirely_when_mem_min_not_declared` and
  `..._skips_entirely_when_governance_disabled` (both with the mocked
  governance function raising `AssertionError` if reached), plus
  `test_mem_min_admission_check_passes_the_declared_size_in_bytes`, which
  asserts the candidate handed across is `128 * 1024**2`, not the raw string
  `"128m"` — comparing a size string against a byte ceiling would silently
  admit anything.

### Controlled wrong implementation #2 — the `<=` boundary

Flipped `if total <= ceiling:` to `if total < ceiling:` in
`check_mem_min_admission` and re-ran that class:

```
FAILED tests/tests/test_ciu_governance.py::TestCheckMemMinAdmission::test_exactly_full_slice_still_admits
FAILED tests/tests/test_ciu_governance.py::TestCheckMemMinAdmission::test_empty_slice_admits_a_candidate_that_fits_the_whole_ceiling
FAILED tests/tests/test_ciu_governance.py::TestCheckMemMinAdmission::test_occupants_with_no_declared_claim_do_not_consume_the_ceiling
3 failed, 4 passed, 188 deselected
```

The dedicated exactly-full test flips from admit to reject exactly as the
handoff required (two sibling cases that also land precisely on the ceiling
fail with it). Source restored immediately.

### The granularity simplification, named explicitly

**One candidate claim = one STACK's declared `mem_min`, not
`mem_min × count-of-non-exempt-services`.** Forced by architecture: at this
call site the stack's compose YAML has not been rendered yet, and
`governance_slice_preflight` plus this check both live in `deploy.py`
specifically so live host probing stays out of the render pipeline
(`composefile.py` does no host probing by design).

The consequence is stated in SPEC S15.23 with the required framing — a stack
that clears admission at 1× its declared claim actually claims N× once running,
which **"dilutes the kernel's proportional protection for every other occupant
of the slice"**, not merely "under-counts". It degrades gracefully (the same
proportional-overcommit tolerance CGROUP-NOTES.md already accepts for the
concurrent-admission race) rather than failing, and CGROUP-NOTES.md's own
motivating cases are single-container stacks — so it does not block the design.
It is documented in SPEC S15.23, CHANGES.md's "Known limitations" section,
CONFIG.md, the CIU-94 backlog row, and here. It was neither silently narrowed
nor silently widened.

## Part D — CIU-94(a) injection (the write)

**Shipped.** `governance.container_transient_scope` (`governance.py:1253`),
`governance.set_scope_memory_min` (`governance.py:1296`);
`deploy.apply_mem_min_injections` (`deploy.py:1315`); call site
`deploy.py:2108`, right after a successful `_run_stack` where
`deployed.append(entry["path"])` sits.

Guarded by `rendered is not None and not dry_run` only — deliberately NOT by
`no_preflight`. The consequence the handoff asked to be explicit about is
implemented and tested: under `--no-preflight` this call site performs the
FIRST `parse_size_to_bytes` of the run, so a malformed `mem_min` string raises
unconditionally here, post-`_run_stack`, with the container already running.
`parse_size_to_bytes` is reused as-is; its exception is neither swallowed nor
downgraded (`test_apply_mem_min_injections_raises_on_malformed_mem_min`).

Enumeration reuses `resolve_selection_health_containers`' pattern
(`yaml.safe_load(compose_path.read_text())` → `services[...]["container_name"]`),
honors `exempt_services`, and applies the identical declared value to every
non-exempt service, matching `build_injections`' `mem_limit`/`mem_reservation`
precedent. `_inspect_state` (the existing `docker inspect --format '{{json
.State}}'` helper) supplies `.Pid` — reuse rather than a second docker shell-out.

Every failure mode is `[WARN]`, `[S15.23]`, naming the container and the
declared value: unreadable/serviceless compose file, a service with no concrete
`container_name`, no resolvable scope (a container that died between
`_run_stack` and this call), and a nonzero `systemctl set-property`.

**Oracles:**

- `TestContainerTransientScope` (governance) — 7 tests: returns the UNIT NAME
  not a path; buildkitd-style nested path (`.../docker-<hex>.scope/buildkit/xyz`)
  trims to `docker-<hex>.scope`; a v1 controller line before the `0::` line is
  ignored; a cgroup-v1-only host (no `0::`) → `(None, "no '0::' line")`; a
  `0::` path with no `.scope` component → `(None, ...)`; `pid <= 0` never
  touches `/proc` (asserted with a raising `Path.read_text`); a vanished
  process (`OSError`) is reported, not raised.
- `TestSetScopeMemoryMin` (governance) — 4 tests: not host-rooted → `(False,
  ...)` with `subprocess.run` monkeypatched to raise if called (this is the
  test that pins D9's narrowness — inside CIU's own devcontainer this is a
  silent no-op, not an attempted write); success asserts the EXACT argv
  `["systemctl", "set-property", "--runtime", "docker-abc.scope",
  "MemoryMin=83886080"]` (`--runtime` is not decoration — a raw cgroupfs write
  would be wiped by any `daemon-reload`); nonzero exit surfaces stderr in the
  note without raising; an `OSError` from `subprocess.run` likewise.
- deploy side —
  `test_apply_mem_min_injections_warns_not_raises_on_set_property_failure`
  (asserts `[WARN]` in captured output AND no exception under the default
  config — the deliberate asymmetry with Part C's ERROR),
  `..._applies_the_parsed_bytes_to_every_non_exempt_service` (records the
  actual `(scope, bytes)` calls; asserts the exempt service is skipped),
  `..._warns_when_no_scope_resolves`, `..._is_a_noop_when_mem_min_not_declared`
  (a raising `_inspect_state` proves no container is inspected at all when no
  floor is declared — this matters: otherwise every governed stack on every
  deploy would pay a `docker inspect` per service), and the malformed-size test
  above.

## Part E — severity table verified against the implementation

| Function → site | Failure condition | Mechanism | Default outcome | Verified by |
|---|---|---|---|---|
| `check_memory_recursiveprot` → preflight | flag missing, some stack declares `mem_min` | `warn_or_raise(severity="ERROR")` | **raises**, exit 2 | `test_..._raises_when_recursiveprot_missing_and_mem_min_declared` + mutation #1 |
| `check_mem_min_admission` → `mem_min_admission_check` | live sum + candidate > ceiling | `warn_or_raise(severity="ERROR")` | **raises**, exit 2 | `test_mem_min_admission_check_raises_when_over_ceiling` |
| `set_scope_memory_min` → `apply_mem_min_injections` | `set-property` fails | `warn_or_raise(severity="WARN")` | logs, no raise | `test_..._warns_not_raises_on_set_property_failure` |
| `parse_size_to_bytes` (reused) | malformed `mem_min` | unconditional `raise ValueError` | always raises | pre-existing `test_governance_slice_preflight_invalid_mem_min_size_raises` |
| same, inside `apply_mem_min_injections` under `--no-preflight` | same string, first parse attempt | unconditional `raise ValueError` | always raises, post-`_run_stack` | `test_apply_mem_min_injections_raises_on_malformed_mem_min` |

No new exit-code plumbing anywhere; every `ValueError` propagates through
`deploy.main` → `engine._exit_code_for` unchanged.

## Part F — documentation

- **`docs/SPEC.md` S15.22** (line 3580) and **S15.23** (line 3655), inserted
  before Appendix A, mirroring S15.21's shape. S15.22 states the ERROR-vs-WARN
  blast-radius distinction explicitly. S15.23 documents the granularity caveat
  with the required "dilutes protection for every occupant of the slice"
  phrasing, and carries the live-verification recipe.
- **`docs/DESIGN-NOTES.md` D9** (line 532) — the package's most important doc
  debt. Written after re-reading D2 and D6 in full. Its argument is that D2's
  recommendation still STANDS rather than being overturned: D2's options A/B/C
  were all ways to obtain privilege for writing to a **slice** (a shared,
  host-level, multi-tenant arbitration object — D2's own worked example says
  so), none is built and none is in scope; what shipped writes to a **transient
  scope** Docker created for a container *this invocation* started, gated by
  the identical `_systemd_is_pid1()` check every existing D-G9 probe uses,
  needing no new privilege and escalating to nothing when denied. D9 also
  records the mechanical reason the gate must come first (the PID is a host
  PID-namespace number) and closes with an explicit line — writing to a slice,
  creating a unit, mounting D-Bus, spawning a privileged helper, or persisting
  a drop-in that outlives the container each re-open D2's options table on
  their own merits — so a later package cannot widen this by increment.
- **Stale-text companion edits**, all three as specified: SPEC S15.16 gains an
  "Amended by S15.23" block distinguishing what is still true (never injected
  into the *overlay*; CIU never configures a *slice*'s properties) from what is
  not (that CIU makes no `mem_min`-related host write at all);
  `GOVERNANCE_DEFAULTS["mem_min"]`'s comment block gains the same clarification
  inline; `composefile.py`'s docstring gains the "and, once the container
  starts, injected onto its own transient scope by
  `deploy.apply_mem_min_injections` (S15.23) — a separate, post-compose-up code
  path this function has no part in" clause. `composefile.py` was touched on
  that one comment only.
- **`docs/CONFIG.md`** — added a `mem_min` paragraph next to the `cpus` one
  (CIU-90's own precedent). `mem_min` had NO CONFIG.md coverage at all before
  this; the addition is a companion note to the existing governance
  worked-example section, not a new section.
- **`CHANGES.md`** — checked for the cmru KI-23 fold-in gap first: the section
  below `<!-- cmru: release history -->` is `## [7.11.0] - 2026-09-02`, a real
  dated heading, not a bare `- UNRELEASED`, so there was no stale hand-authored
  block to fold in. A new `## [Unreleased]` section was added, framed
  `feat(ciu):` as instructed (both entries are new, additive capability behind
  the existing opt-in `governance.mem_min`).
- **`KNOWN_ISSUES_TODO_BACKLOG.md`** — CIU-94 and CIU-95 flipped to **FIXED**
  with the shipped mechanism and real `file:line` citations from this diff (the
  proposed-contract language they carried was replaced, not appended to).
  Column count verified against neighbouring rows.

## Fixture choice (`test-repo/`)

Added `[db_core.governance]` to `test-repo/infra/db-core/ciu.defaults.toml.j2`:
`enabled = true`, `cgroup_parent = "dev-memory_min_guaranteed.slice"`,
`mem_min = "128m"`.

**Why db-core rather than redis-core.** redis-core is the smaller stack and
already declares governance, so it looks like the obvious pick — but its
governance table deliberately leaves `cgroup_parent` unset to demonstrate the
ambient-default path, which resolves to `dev-background.slice`. Declaring a
floor there would make the fixture demonstrate the exact anti-pattern
CGROUP-NOTES.md opens by warning against (a shared, heterogeneous tier can't
safely carry a `MemoryMin`; the kernel redistributes it to every unrelated
occupant), and setting `cgroup_parent` explicitly on redis-core to avoid that
would change what existing tests observe about the ambient-default path.
`infra/db-core` is single-container, declared no governance at all (so nothing
conflicts), and is literally CGROUP-NOTES.md's motivating case — "a disposable
Postgres". The fixture's comment block explains the guaranteed-tier
requirement, so the example teaches the constraint rather than modelling
around it.

## Pre-existing tests edited (disclosed)

Five pre-existing tests in `test_ciu_deploy_actions.py` gained an explicit
`monkeypatch.setattr(deploy.governance_mod, "check_memory_recursiveprot", ...)`
pinning it to "present":

- `test_governance_slice_preflight_mem_min_inadequate_warns_by_default`
- `test_governance_slice_preflight_raises_when_mem_min_inadequate_and_exit_on_warn`
- `test_governance_slice_preflight_passes_when_mem_min_adequate`
- `test_governance_slice_preflight_mem_min_skipped_when_slice_missing`
- `test_governance_slice_preflight_mem_min_skips_on_non_systemd_host`

These are the tests that declare `mem_min`, so they now reach the new S15.22
check, which would otherwise read the REAL `/proc/mounts` of whatever host the
suite runs on. That is AUTHORING §3b anti-pattern E (an uncontrolled
environment input deciding a verdict) and would make these tests
host-dependent — they pass here only because this devcontainer's
`_systemd_is_pid1()` is False, and would begin raising `[S15.22]` on a
host-rooted runner whose flag happens to be missing. Pinning the boundary is
the fix for that, not an evasion: each keeps every one of its original
assertions, and no assertion was weakened or removed.

## Oracle-hygiene self-audit (AUTHORING §3b)

- **A (machine speed)** — no `time.sleep`, no wall-clock deadline, no
  elapsed-time or iteration-count assertion anywhere in the new tests. The only
  timeouts added are inside `set_scope_memory_min`'s `subprocess.run`
  (production hang-failsafe, never mocked into a verdict).
- **B (order/worker independence)** — every new filesystem test builds its tree
  under its own `tmp_path`. Every process-global mutation goes through
  `monkeypatch`, which restores it. No new module-level state, no `os.environ`
  writes. Verified by running the suite with `-p no:randomly` and again under
  the project's default random ordering (both green).
- **C (no hollow tests)** — no `pass`-body tests. Where a check guards a real
  hazard the hazard is proven, not assumed: "must not touch the filesystem"
  tests monkeypatch the accessor to RAISE, so a regression fails loudly rather
  than passing silently; the exact-argv assertion in `TestSetScopeMemoryMin`
  pins `--runtime` behaviorally; two controlled wrong implementations were
  actually run and their failures recorded above.
- **D (no coverage evasion)** — no `no-cover` pragma was added anywhere, in
  code or comments.
- **E (network/clock/filesystem controlled)** — no real subprocess and no
  network in any new test. `subprocess.run` is mocked at the module boundary
  (`gov.subprocess.run`), `/proc/mounts` and `/proc/<pid>/cgroup` via a
  path-selective `Path.read_text` wrapper that delegates to the real one for
  every other path (so nothing else in the test silently breaks), and cgroupfs
  via real directories under `tmp_path`. The five pre-existing tests above were
  fixed for this same rule.

## Live verification — NOT DONE, deferred to the merge (per the handoff)

This is stated plainly rather than glossed. `_systemd_is_pid1()` is False in
this dispatch's environment (this project's devcontainer has no D-Bus socket,
`CgroupnsMode=private`, a `systemctl` shim, and cgroup2 mounted `ro` — DESIGN-
NOTES D2). Unlike CIU-90's live check (a plain `docker inspect` over the
shared, DooD-trusted `docker.sock`), this package's mechanism needs genuine
host-rooted systemd, which this environment does not have and was not expected
to obtain. Nothing was fabricated and nothing is marked skipped-as-if-run.

The mutation-tested unit suite above is this dispatch's complete oracle. The
recipe left for whoever merges with real host access:

1. Provision `dev-memory_min_guaranteed.slice` with a `MemoryMin=` ceiling (and
   `dev.slice`'s own `MemoryMin=` pinned EXACTLY equal to it — CGROUP-NOTES.md
   "the one invariant that actually matters"; generous headroom there leaks the
   surplus to `dev-interactive.slice`/`dev-background.slice`).
2. `ciu deploy` a stack declaring `governance.mem_min` with `cgroup_parent`
   pointing at that slice (`test-repo/infra/db-core` is now exactly that
   shape). Then, on the container's own scope:
   `systemctl show <docker-<id>.scope> --property=MemoryMin` must report the
   declared byte count. Find the scope with
   `awk -F: '/^0::/{print $3}' /proc/$(docker inspect -f '{{.State.Pid}}' <name>)/cgroup`.
3. Deploy a SECOND stack whose claim would exceed the remaining ceiling and
   confirm it is genuinely REFUSED before starting, with the `[S15.23]`
   breakdown naming the current occupants — not merely asserted in a mock.
4. Optionally, confirm S15.22's ERROR fires on a host with the mount flag
   genuinely absent (`findmnt -no OPTIONS /sys/fs/cgroup`).

## Scope compliance

Touched, all within the handoff's **Touch** list: `src/ciu/governance.py`,
`src/ciu/deploy.py`, `src/ciu/composefile.py` (the one named comment line
only), `tests/tests/test_ciu_governance.py`,
`tests/tests/test_ciu_deploy_actions.py`,
`test-repo/infra/db-core/ciu.defaults.toml.j2`, `docs/SPEC.md` (S15.22/S15.23
plus the one named S15.16 companion paragraph — no other S15 section),
`docs/DESIGN-NOTES.md`, `docs/CONFIG.md`, `CHANGES.md`,
`KNOWN_ISSUES_TODO_BACKLOG.md`.

Nothing under `modern-debian-tools-python-debug/` was modified. Nothing under
`ciu8/` was modified. `nyxloom-trove/decisions.md` was read but not modified.
No BLOCKED condition was hit.
