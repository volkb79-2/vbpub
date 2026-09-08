# ciu-P50 — CIU-94 (per-container `memory.min` injection + admission
# control) + CIU-95 (`memory_recursiveprot` check + downward slice
# enumeration)

**Input revision:** ciu `main` @ `c7c04a0fee64d57b236da1f4e1ba3a07c497f0ee`
(the commit that filed CIU-95). Post ciu-P49/ciu-v7.11.0. Neither entry has
been touched since filing.

**Product decision this package builds under:** `nyxloom-trove/decisions.md`
**D-012** — build in v7 now, not deferred to ciu8/v8. Read it; it also names
where the consuming host-setup design lives.

**Why one package, not two:** CIU-95's downward-enumeration primitive is the
exact mechanism CIU-94's admission-control sum also needs — building it
twice would duplicate the one genuinely new capability this package
introduces (a live cgroupfs walk of a slice's current occupants). They also
share the devcontainer-unreachability framing in Part 0. Implement in the
order below — each part depends on the previous one's primitive.

---

## Context to read first (in this order)

1. `KNOWN_ISSUES_TODO_BACKLOG.md` — search `## CIU-94` and `## CIU-95` (both
   have full sections, not just table rows). Read both completely before
   writing any code — they carry the live incident (dstdns P165/D-373) and
   the design reasoning this handoff builds concrete shapes on top of.
2. `modern-debian-tools-python-debug/host-setup/CGROUP-NOTES.md` §"Per-
   container `memory.min` guarantees" — the host-setup half (already
   shipped, do not touch it) this package's ciu-side half consumes. Read
   especially "The one invariant that actually matters" (why the ceiling
   must never be generous) and "Chose (2)" (why static-ceiling +
   admission-control, not a dynamic reconciler — this package builds the
   admission-control half of that chosen design).
3. `docs/DESIGN-NOTES.md` **D2** (`Options for CIU to write cgroup values
   Docker doesn't expose`) — CIU-94(a)'s injection is the first write this
   decision's "verify-only, forever, until/unless that changes" framing
   didn't plan for. Read D2 in full; you are about to write the D9 entry
   that reconciles it (Part D below tells you what D9 must say).
   Also read **D6** (`Survey: which existing warn/error sites are
   candidates for S10.6`) — the severity mechanism (Part E) goes through
   the same `warn_policy` unification D6 already established as the
   pattern, not a bespoke raise.
4. `src/ciu/governance.py` — read in full once (1324 lines; you will touch
   several regions). Specifically: `GOVERNANCE_DEFAULTS["mem_min"]`
   (~line 135-151, its comment block is stale after this package — see
   Part F), `check_slice_memory_min` (line 827), `slice_ancestor_chain`
   (line 887), `check_memory_min_ancestor_chain` (line 908),
   `_systemd_is_pid1` (line 693) — every new probe in this package gates
   on this the same way these do.
5. `src/ciu/deploy.py` — `governance_slice_preflight` (line 1186, its
   `mem_min_required` loop at 1240-1283 is the aggregation you are adding
   alongside, not replacing), its call sites in `action_deploy` (lines
   ~4567, ~4598), the per-entry deploy loop around `_run_stack`/
   `deployed.append(entry["path"])` (~line 1790-1801), and
   `resolve_selection_health_containers` (~line 1521-1649 — the existing
   pattern for reading a rendered compose file's `container_name`s you
   will reuse for injection's per-service enumeration).
6. `modern-debian-tools-python-debug/host-setup/scripts/mdt-slice-audit.py`
   — `read_protection()`/`ancestors()`/`audit_tree()`. This is the
   reference implementation for the cgroupfs-walk technique Part A ports
   into `governance.py`. Port the *technique*, not the file — it is a
   host-setup script with different constraints (always exits 0, audits
   `memory.low` too); your version is a `governance.py` library function
   with a different return contract (see Part A).
7. `modern-debian-tools-python-debug/host-setup/scripts/mdt-apply-dev-caps.sh`
   — the `docker ps` → `/proc/<pid>/cgroup` → `.scope`-trim technique Part D
   ports for `container_transient_scope`.
8. `docs/SPEC.md` S15.16 (line 3416) and S15.21 (line 3536, the CIU-90/
   ciu-P49 precedent for a new governance sub-section's shape/length) —
   your new S15.22/S15.23 sections (Part F) mirror S15.21's shape, not
   S15.2's longer one.
9. `tests/tests/test_ciu_governance.py` — the `TestResolveConfig`/
   `TestBuildInjections` classes (house mutation-oracle style) and the
   existing `check_slice_unit`/`check_slice_memory_min` test fixtures
   (~lines 579-598, 953-957) for the `_systemd_is_pid1`/`subprocess.run`
   monkeypatch idiom every new probe's tests reuse.
10. `tests/tests/test_ciu_deploy_actions.py` — the existing
    `_governance_selection_rendered_mem_min`/`_plain_config` helpers
    (~line 1103 region) and the `fail_mem_min`-style "assert this was
    never even called" idiom (~lines 1185-1188) your new preflight/
    admission tests reuse.

---

## Part 0 — where this can and cannot run (read before writing any function)

`check_slice_memory_min` already draws a line: ciu runs either bare-host
(systemd PID 1, real `systemctl`/host cgroupfs reachable) or inside a
devcontainer (`systemctl` present but not PID 1 — `_systemd_is_pid1()` is
False; per D2, this project's own devcontainer has `CgroupnsMode=private`,
no D-Bus socket, a `systemctl` shim, and cgroup2 mounted `ro`, so neither a
write nor a host-real-tree read can reach anything regardless of caller
privilege). Every function this package adds — reads AND the one write —
gates on the exact same `_systemd_is_pid1()` check and degrades the exact
same way (`None`/informational skip, never a crash, never a false FAIL).
This is not optional hygiene: it is the only thing that makes CIU-94(a)'s
write (Part D) consistent with D2's existing reachability reasoning rather
than contradicting it.

---

## Part A — shared primitive: cgroupfs slice enumeration (`governance.py`)

Read directly from cgroupfs (`pathlib`, no `docker`/`systemctl` per child) —
CGROUP-NOTES.md's own text is explicit that this is the intended mechanism
("read straight from cgroupfs ... no systemd interaction needed"), and
`mdt-slice-audit.py` is working prior art for the exact walk. Only the WRITE
in Part D needs `systemctl` (raw cgroupfs writes get wiped by `daemon-
reload` — reads have no such hazard).

Add to `governance.py`:

```python
CGROUP_ROOT: Path = Path("/sys/fs/cgroup")  # module-level, overridable per-call for tests

def slice_cgroup_path(slice_name: str, cgroup_root: Path = CGROUP_ROOT) -> Path:
    """Pure derivation of slice_name's cgroupfs directory, built by reversing
    slice_ancestor_chain(slice_name) into nested dirs — e.g.
    "dev-background.slice" -> CGROUP_ROOT/"dev.slice"/"dev-background.slice".
    Reuses slice_ancestor_chain rather than re-deriving the dash-naming rule
    a second time. No I/O."""

def _read_memory_min_bytes(cgroup_dir: Path) -> int:
    """(cgroup_dir / "memory.min").read_text() -> int; 0 for unset/"max"/
    unreadable/non-cgroup dir. Same semantics as mdt-slice-audit.py's
    read_protection(), scoped to memory.min only."""

def enumerate_slice_children(
    slice_name: str, *, cgroup_root: Path = CGROUP_ROOT,
) -> tuple[list[tuple[str, int]] | None, str]:
    """Every immediate child cgroup currently under slice_name, each as
    (child_name, its own live memory.min in bytes).

    Returns (None, note) when this environment cannot see the host's real
    cgroup tree (_systemd_is_pid1() is False — an isolated devcontainer's own
    /sys/fs/cgroup is ITS OWN namespace-local root, not the host's; walking it
    would silently enumerate the wrong tree, not a smaller version of the
    right one). Genuinely zero occupants (slice inactive, or active but
    empty) returns ([], note) — a definitive answer, not an abstention,
    matching mdt-slice-audit.py's own "not a dir -> 0 found" treatment. Never
    raises."""
```

## Part B — CIU-95(a): `memory_recursiveprot` mount-flag check

```python
def check_memory_recursiveprot() -> tuple[bool | None, str]:
    """/proc/mounts (no external tool needed, unlike host-setup's check.sh
    which shells to findmnt), the cgroup2 line's mount options,
    "memory_recursiveprot" flag present? Host-wide, not per-slice -- gated
    by _systemd_is_pid1() like every other probe (a devcontainer's own
    /proc/mounts reflects ITS mount namespace, not the host's).
    (None, note) when not host-rooted or no cgroup2 line found at all.
    Never raises."""
```

**Design decision, already made — do not re-litigate:** a **separate
function, called once** from `governance_slice_preflight`, NOT folded into
`check_slice_memory_min`/`check_memory_min_ancestor_chain`. It is a single
host-wide mount flag, not a per-slice/per-ancestor property; embedding it in
the per-ancestor check would re-run an identical, slice-independent check
once per ancestor per slice for zero additional information.

**Call site** — `governance_slice_preflight` (deploy.py:1186), inserted
right before the existing `mem_min_required` loop (deploy.py:1303-1315),
evaluated only when there is something for it to matter to:

```python
if mem_min_required:
    recursiveprot, rp_note = governance_mod.check_memory_recursiveprot()
    if recursiveprot is False:
        warn_policy.warn_or_raise(
            "[S15.22] cgroup2 is mounted WITHOUT memory_recursiveprot — every "
            "declared mem_min on this host is currently a NO-OP even where "
            "MemoryMin= is correctly set and the ancestor chain is otherwise "
            f"adequate (CGROUP-NOTES.md §5). {rp_note}",
            severity="ERROR", config=config,
        )
```

**Severity:** `severity="ERROR"` — under the default `ciu.exit_on = "ERROR"`
policy this raises `ValueError` (S10.3 → exit 2) **by default**, matching
`mdt-host-check.sh`'s own FAIL-not-WARN treatment of this exact flag. Use
`warn_policy.warn_or_raise` (the D6-unified mechanism), not a bespoke
unconditional `raise ValueError(...)` — D6 frames the older S15.G9-1 pattern
as a not-yet-migrated holdout, not a template for new checks.

## Part C — CIU-94(b): admission control

```python
def check_mem_min_admission(
    slice_name: str, candidate_bytes: int, *, cgroup_root: Path = CGROUP_ROOT,
) -> tuple[bool | None, str]:
    """Admission decision for ONE candidate about to start under slice_name.

    Ceiling = slice_name's OWN live memory.min
    (_read_memory_min_bytes(slice_cgroup_path(slice_name))) -- reused
    directly, never duplicated as a separate ciu-side config value, so
    nothing can drift from what host-setup actually provisioned.
    Currently-claimed = sum(bytes for _, bytes in
    enumerate_slice_children(slice_name)[0]) (Part A).

    Returns:
    - (None, note) -- not host-rooted, OR the slice carries no live
      memory.min of its own (0/unset): there is no real ceiling to admit
      into, so admission control does not apply. (S15.16's existing
      ancestor-chain WARN already covers "this floor is a no-op" from the
      DECLARATION side -- don't invent a second reason to block a container
      over guarantee infrastructure that was never provisioned.)
    - (True, note)  -- current_sum + candidate_bytes <= ceiling.
    - (False, note) -- current_sum + candidate_bytes > ceiling; note carries
      ceiling, current_sum, candidate_bytes, and the per-child breakdown."""
```

**Granularity — a real, explicit simplification, name it in your REPORT, do
not silently narrow it further or silently widen it:** one candidate claim =
one **stack**'s declared `mem_min` (as `governance_slice_preflight`'s own
existing `mem_min_required` aggregation already treats it — max across
stacks sharing a slice, not per compose service), not
`mem_min_bytes × count-of-non-exempt-services`. This is forced by
architecture: at the admission-control call site (below) the stack's compose
YAML has not been rendered yet — `governance_slice_preflight` and this new
check both live in `deploy.py` precisely so host-state checks stay out of
the render pipeline (`composefile.py` does no live host probing by design).
A multi-service stack landing on a guaranteed slice will under-count at
admission time relative to what Part D actually injects onto each of its
non-exempt containers. CGROUP-NOTES.md's own motivating cases are
single-container stacks ("a disposable Postgres", "sql-mutation-gate") —
reasonable v1 scope, but must be named, not discovered later.

**Call site:** new function `mem_min_admission_check(repo_root, entry,
rendered, config)` in `deploy.py`, called from `action_deploy`'s existing
per-entry loop **immediately before** `_run_stack(...)` (~line 1790),
guarded the same way the per-phase `provisioning_preflight` call already is
(`rendered is not None and not no_preflight and not dry_run`). This must be
a **live, per-entry** check reflecting state as earlier entries in *this
same run* come up — unlike `governance_slice_preflight`, which stays a
one-time up-front check for slice existence + declared-floor adequacy (an
unrelated question; do not merge the two checks). Factor the ~15-line
governance-table re-resolution both this function and
`governance_slice_preflight`'s loop body need into a shared
`_resolve_entry_governance(entry, rendered, config)` helper if you can do so
without changing either's existing behavior — recommended, not required for
correctness.

No-op (skip silently) when governance is disabled, `mem_min` is not
declared for this entry, or `cgroup_parent` doesn't resolve to a slice.
Otherwise: `parse_size_to_bytes(mem_min_raw)` (reuse, existing function) →
`governance_mod.check_mem_min_admission(slice_name, bytes)` → `(False,
note)` → `warn_policy.warn_or_raise(..., severity="ERROR", config=config)`,
tagged `[S15.23]`.

## Part D — CIU-94(a): injection (the write — read Part 0 again first)

```python
def container_transient_scope(pid: int) -> tuple[str | None, str]:
    """Port of mdt-apply-dev-caps.sh's cgroup-derivation: read
    /proc/<pid>/cgroup, take the line starting "0::" (the unified v2
    hierarchy -- absent on a pure cgroup-v1 host -> (None, note)), trim to
    the FIRST ".scope" path component (buildkitd-style nested sub-cgroups
    included -- mirrors mdt-apply-dev-caps.sh's own trim). Returns the
    scope's systemd UNIT NAME (e.g. "docker-<hex>.scope"), not a path --
    that's what set-property takes. (None, note) if pid<=0, /proc/<pid>/cgroup
    is unreadable (process already exited), or no ".scope" component is
    found. Never raises."""

def set_scope_memory_min(scope_unit: str, required_bytes: int) -> tuple[bool, str]:
    """systemctl set-property --runtime <scope_unit> MemoryMin=<required_bytes>
    (--runtime: reload-safe, CGROUP-NOTES.md's own established doctrine,
    already load-bearing for mdt-apply-dev-caps.sh's IO*Max writes against
    the SAME kind of docker-*.scope unit -- never a raw cgroupfs write).
    Gated FIRST by _systemd_is_pid1() -- returns (False, note) without
    attempting the write when not host-rooted (also mechanically necessary:
    a PID from `docker inspect .State.Pid` is a host-PID-namespace number,
    meaningless from inside an isolated devcontainer's OWN /proc). A nonzero
    systemctl exit (e.g. insufficient privilege) is caught and reported,
    never raised."""
```

**Call site:** new function `apply_mem_min_injections(repo_root, entry,
rendered, config)` in `deploy.py`, called from `action_deploy`'s per-entry
loop **right after** a successful `_run_stack` (~line 1799-1801, where
`deployed.append(entry["path"])` currently sits) — by this point the
container is genuinely running AND the compose file has been rendered to
disk (`_run_stack` → `engine.main_execution` writes it before starting
anything), so injection can enumerate the stack's *actual* compose services
and their concrete `container_name`s the same way
`resolve_selection_health_containers` already does (same
`yaml.safe_load(compose_path.read_text())`, same
`services[...]["container_name"]` pattern). No-op if `mem_min` not
declared. Else, for each non-exempt service's resolved `container_name`:
`docker inspect -f '{{.State.Pid}}' <name>` → `container_transient_scope` →
`set_scope_memory_min` — applying `mem_min` identically to every non-exempt
service (matching `build_injections`'s existing "same value on every
non-exempt service" precedent for `mem_limit`/`mem_reservation`).

Guarded only by `if not dry_run:` (nothing to inject onto in dry-run) — NOT
by `no_preflight` (that flag skips static gates; it does not disable
governance injection itself).

**Severity:** `severity="WARN"`, tagged `[S15.23]` — deliberately the
opposite of Part C's ERROR. The deploy has already succeeded by this call
site; "the floor didn't apply" is "declared intent unfulfilled," the same
severity class S15.16 already treats as WARN, not a new deploy-blocking
condition.

## Part E — exceptions/severities (verify your implementation against this table)

| Function | Failure condition | Mechanism | Default outcome | exit |
|---|---|---|---|---|
| `check_memory_recursiveprot` → preflight | mount flag missing, some stack declares `mem_min` | `warn_policy.warn_or_raise(severity="ERROR")` | raises | 2 |
| `check_mem_min_admission` → `mem_min_admission_check` | sum(live siblings) + candidate > slice's live ceiling | `warn_policy.warn_or_raise(severity="ERROR")` | raises | 2 |
| `set_scope_memory_min` → `apply_mem_min_injections` | `systemctl set-property` fails | `warn_policy.warn_or_raise(severity="WARN")` | logs, no raise | n/a unless `ciu.exit_on=WARN` |
| `parse_size_to_bytes` (existing, reused) | malformed `mem_min` string | unconditional `raise ValueError` | always raises | 2 |

No new exit-code plumbing anywhere — every new `ValueError` propagates
through `deploy.main` → `engine._exit_code_for` unchanged.

## Part F — documentation (part of the contract, not a follow-up)

- New `docs/SPEC.md` **S15.22** (`memory_recursiveprot` check + downward
  slice enumeration, CIU-95) and **S15.23** (per-container `memory.min`
  injection and admission control, CIU-94), inserted before "Appendix A"
  (~line 3568), mirroring S15.21's shape/length. S15.23 must document the
  "one claim per stack, not per compose service" granularity caveat from
  Part C explicitly — do not let it become an undocumented gap.
- **New `docs/DESIGN-NOTES.md` D9** — the most important doc debt in this
  package, more than the SPEC numbering. It must narrow D2's "verify-only,
  forever" framing: CIU-94(a) is the first write, but a maximally narrow
  one — gated by the identical `_systemd_is_pid1()` check every existing
  D-G9 probe uses (silent no-op inside ciu's own devcontainer, live only
  when ciu runs genuinely host-rooted, matching `mdt-apply-dev-caps.sh`'s
  own execution model), and it only ever touches the ONE scope Docker just
  created for a container *this* `ciu deploy` invocation started — never a
  shared slice, no privilege escalation attempted, no daemon, no helper
  container (D2 Options A/B/C stay explicitly out of scope, unchanged).
- **Stale-text companion edits** (not just new sections):
  - `docs/SPEC.md` S15.16's "it is **never injected** into the overlay"
    paragraph (~line 3465-3474) — still true of the *compose overlay*
    specifically; add a clause that it is no longer true that CIU makes
    zero mem_min-related host writes (S15.23 now does, via a different
    mechanism — the container's own transient scope, not the overlay).
  - `governance.py:135-151` (`GOVERNANCE_DEFAULTS["mem_min"]`'s comment
    block) — same clarification, inline.
  - `composefile.py:~1195-1198` ("`mem_min` ... is never injected here; it
    is checked ... at deploy time") — add: "and, once the container
    starts, injected onto its own transient scope by
    `deploy.apply_mem_min_injections` (S15.23) — a separate, post-
    compose-up code path this function has no part in."
- `CHANGES.md` — check for an `- UNRELEASED` fold-in gap before adding
  (recurring estate gotcha, cmru KI-23 — verify the heading has a date, not
  a bare `- UNRELEASED`).
- Frame commits as `feat(ciu):` — both CIU-94 and CIU-95 are new, additive
  capability behind existing opt-in config (`governance.mem_min`), not a
  correction to previously-wrong default behavior for existing configs.

---

## Oracles (controlled wrong implementation, satisfy exactly)

**Before writing any test, read this list — copied verbatim from
`nyxloom/reference/AUTHORING.md` §3b, "what an oracle must NOT contain."
You have no access to the incident history behind it and will reproduce
these by default otherwise:**

- **A. Nothing may make the verdict depend on how fast the machine is.** No
  `time.sleep`+assert, no wall-clock deadlines, no asserting on elapsed
  time or iteration counts. A timeout is legal ONLY as a hang-failsafe
  (generous, e.g. 60s), never the thing deciding pass/fail.
- **B. Nothing may depend on test order, worker assignment, or a sibling
  test.** No unrestored process-global state (`os.environ`, module
  attributes). Fresh `tmp_path` per test.
- **C. No hollow tests.** No `pass`-body tests, no asserting only "nothing
  raised," no asserting implementation trivia (call counts, private
  attributes) instead of the behavioral contract. Where a check guards a
  real crash, prove the crash is real.
- **D. No coverage evasion.** No no-cover pragmas on changed lines
  (including inside a comment that merely describes the rule — the gate
  matches the literal token anywhere on the line).
- **E. Network, clock, and filesystem are inputs — control them.** No real
  subprocess/network calls in a unit test; mock the boundary
  (`subprocess.run`, `Path.read_text`, per the existing fixtures named
  below).

New test classes in `tests/tests/test_ciu_governance.py` (monkeypatch
`gov._systemd_is_pid1`, `gov.shutil.which`, `gov.subprocess.run` per the
existing fixtures at ~lines 579-598/953-957; the new file-based probes use
`tmp_path`-built fake cgroup trees instead of subprocess mocking — a real
testability win):

- `TestSliceCgroupPath` — `slice_cgroup_path("dev-background.slice") ==
  CGROUP_ROOT/"dev.slice"/"dev-background.slice"`; single-segment slice
  case; reuse `TestSliceAncestorChain`'s fixtures.
- `TestEnumerateSliceChildren` — build
  `tmp_path/dev.slice/dev-memory_min_guaranteed.slice/{docker-a.scope/
  memory.min="83886080", docker-b.scope/memory.min="max"}`; assert
  `[("docker-a.scope", 83886080), ("docker-b.scope", 0)]`. Separate case:
  slice dir absent → `([], note)`. Separate case: `_systemd_is_pid1` False
  → `(None, note)`, and assert **no filesystem access was attempted**
  (monkeypatch `Path.iterdir` to raise `AssertionError` if called — mirrors
  this file's existing `test_no_systemctl_never_calls_subprocess` idiom).
- `TestCheckMemMinAdmission` — ceiling 0/unset → `(None, note)`;
  sum+candidate ≤ ceiling → `(True, ...)`; sum+candidate > ceiling →
  `(False, ...)` with ceiling/sum/candidate all present in the note.
  **Controlled wrong implementation:** flip `<=` to `<` in the
  implementation, confirm a dedicated exactly-full-slice test (candidate
  brings the sum to EXACTLY the ceiling) flips from admit to reject — an
  exactly-full slice must admit, mirroring `check_slice_memory_min`'s
  existing `test_exactly_equal_is_adequate`.
- `TestCheckMemoryRecursiveprot` — fake `/proc/mounts` content with/without
  the flag on the `cgroup2` line; no cgroup2 line at all → `(None, ...)`;
  not host-rooted → `(None, ...)` without reading the file (monkeypatched
  `Path.read_text` raises if called).
- `TestContainerTransientScope` — fake `/proc/<pid>/cgroup` with a
  `0::/dev.slice/.../docker-<hex>.scope` line, a nested buildkitd-style
  line (`.../docker-<hex>.scope/buildkit/...`) verifying the trim, and a
  cgroup-v1-only host (no `0::` line) → `(None, ...)`.
- `TestSetScopeMemoryMin` — mirrors `TestCheckSliceUnit`'s
  `subprocess.run` mocking: not host-rooted → `(False, ...)`, never calls
  `subprocess.run`; success (`returncode=0`) → `(True, ...)`; nonzero
  returncode → `(False, ...)` with stderr surfaced in the note.

New tests in `tests/tests/test_ciu_deploy_actions.py` (reuse the existing
`_governance_selection_rendered_mem_min`/`_plain_config` helpers, ~line
1103 region):

- `test_governance_slice_preflight_raises_when_recursiveprot_missing_and_mem_min_declared`
  — monkeypatch `deploy.governance_mod.check_memory_recursiveprot` →
  `(False, ...)`, assert `pytest.raises(ValueError, match=r"\[S15\.22\]")`
  under the **default** config (no `ciu.exit_on` override). **Controlled
  wrong implementation:** revert the severity to `"WARN"`, confirm this
  exact test now fails — proves the ERROR-vs-WARN choice is load-bearing
  and tested, not incidental.
- `test_governance_slice_preflight_skips_recursiveprot_check_when_no_mem_min_declared`
  — `check_memory_recursiveprot` set to raise `AssertionError` if called
  (mirrors the `fail_mem_min` idiom at ~lines 1185-1188), selection with no
  `mem_min` declared — must not raise, must not call the mocked function.
- `test_mem_min_admission_check_raises_when_over_ceiling` /
  `..._passes_when_under_ceiling` / `..._skips_when_no_ceiling_configured`
  — monkeypatch `deploy.governance_mod.check_mem_min_admission` directly
  (same style as existing `check_slice_memory_min` monkeypatches), assert
  `[S15.23]`-tagged `ValueError` / no-raise respectively.
- `test_apply_mem_min_injections_warns_not_raises_on_set_property_failure`
  — monkeypatch `governance_mod.set_scope_memory_min` → `(False, ...)`,
  assert `[WARN]` in captured output and **no** exception under default
  config (deliberate asymmetry with the admission-control test above —
  WARN, not ERROR).

**Live verification, mirroring CIU-90/ciu-P49's own method (its whole
finding was a `docker inspect` value, not a config-shape assertion — close
the loop the same way):** after the fix, on a host that actually has
`dev-memory_min_guaranteed.slice` provisioned (or a scratch slice you
create for the test — do not require a specific operator's host), bring up
a real governed test-repo service with `governance.mem_min` configured and
confirm via `systemctl show <scope> --property=MemoryMin` that the value
was actually applied to the container's own scope, AND that starting a
second candidate whose claim would exceed the slice's ceiling is genuinely
refused (not just asserted in a mock).

---

## Scope / forbid

**Touch:** `src/ciu/governance.py`, `src/ciu/deploy.py`,
`tests/tests/test_ciu_governance.py`, `tests/tests/test_ciu_deploy_actions.py`,
`docs/SPEC.md`, `docs/DESIGN-NOTES.md`, `docs/CONFIG.md` (only if an existing
governance worked-example section needs a companion note — check before
adding a new one), `composefile.py` (comment-only, the one stale line in
Part F), `CHANGES.md`, `KNOWN_ISSUES_TODO_BACKLOG.md` (flip CIU-94/CIU-95 to
FIXED at the end, with your own file:line citations — not the
proposed-contract language they currently carry).

**Forbid:** `modern-debian-tools-python-debug/` (any path under it —
host-setup's half is already shipped and out of scope here; a separate
package handles its interactive wizard), `src/ciu/composefile.py` beyond the
one named comment line, `ciu8/` (this is a v7 package per D-012), any
`docs/SPEC.md` S15 section other than S15.22/S15.23 and the one named
S15.16 companion-edit paragraph, `nyxloom-trove/decisions.md` (D-012 is
already filed — do not add another decision here; if you hit a genuine new
product ambiguity, that's the BLOCKED trigger below, not a self-authored
decision).

If a contract item above requires touching a file not listed in Touch, or a
named design decision (cgroupfs-walk-not-systemctl, one-claim-per-stack,
ERROR-vs-WARN split, D9's narrow-write framing) turns out to be
unimplementable as specified against the real current source — **STOP**.

**BLOCKED rule:** if a named contract cannot be met as specified, or scope
requires a forbidden file, STOP — write `BLOCKED: <reason>` to the LOG,
commit, and exit. Do NOT improvise a workaround.

---

## Process requirements (same convention as P46-P49)

- Fresh implementer, zero prior context beyond this document, both backlog
  entries in full, D-012, D2, and the live repo.
- **Real gate required**: `./run-gate.py ciu` (`--worktree <path>` if in an
  isolated worktree). Read the verdict in a separate step, never off a
  piped tail (estate-wide MANDATORY rule — see vbpub `AGENTS.md` "Read the
  exit status from the job, never from the wrapper").
- Update `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-94 and CIU-95 sections to
  FIXED with the actual shipped mechanism and file:line citations from your
  own diff — not the proposed-contract language currently there.
- LOG/REPORT: `nyxloom-trove/reports/ciu-P50-{LOG,REPORT}.md`. LOG per
  commit (self-hash rule). REPORT with per-oracle evidence, Parts A-F
  reported separately (don't conflate CIU-94's evidence with CIU-95's).
- Checkpoint clause: ARM at ~120k context or ~60 tool calls (whichever
  first), CUT at the next coherent boundary (green gate > commit >
  LOG/REPORT write > edit-cluster end; never on a red gate), repeat every
  ~40-55 calls, stop when <~40 calls remain. At the cut: continuation brief
  to a durable file (`nyxloom-trove/reports/ciu-P50-BRIEF.md`) + a
  self-authored `/compact`-style retention prompt
  (`nyxloom-trove/reports/ciu-P50-COMPACT.md`), commit, stop.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K6ZFTkbjEnh4haBGDUGyuJ
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge.
- **Host is shared with a production game server** — 8 cores, load hit 85
  on 2026-09-02 from concurrent xdist pytest + an uncapped gate container:
  serial pytest under nice/ionice, ONE gate container at a time across all
  agents, `docker update --cpus=3` right after launch, no builds concurrent
  with suites.
- Closing discipline: claim only what you ran, with the real numbers/
  outputs — this package's own live-verification oracle (systemctl show
  against a real scope) is exactly the kind of claim CIU-90's backlog entry
  shows gets checked, not taken on your word.
