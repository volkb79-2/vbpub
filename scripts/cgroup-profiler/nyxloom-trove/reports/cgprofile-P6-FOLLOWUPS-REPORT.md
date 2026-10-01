# cgprofile-P6-FOLLOWUPS — REPORT (sessions 1-3, partial)

Package P6, RG-55 wave. Session 1 shipped C1 (CP-4) and C2 (CP-5). Session
2 shipped C3 (CP-7) and C4 (CP-6). Session 3 (this update) shipped C5
(`cgprofile.slice` + `ctl host` §8.5); C6-C9 are NOT started. Claim only
what was run.

## Deliverables done this session

### C1 — CP-4 (run-id flake), commit `376bb9cb`

**Change:** `lib.store.new_run_id`'s random suffix widened `os.urandom(2)`
(4 hex chars) -> `os.urandom(4)` (8 hex chars).

**Oracle (per the handoff):** uniqueness over 50 draws at collision
probability < 1e-6, stated in the test's docstring.
- Before: `1 - exp(-50*49/(2*65536)) ≈ 1.8%` (the measured real flake, CP-4
  backlog row).
- After: `50*49/(2*16**8) ≈ 2.85e-7` — under 1e-6. Stated in both
  `lib/store.py`'s `new_run_id` docstring and
  `tests/test_store.py::test_new_run_id_is_unique_even_for_the_same_instant`'s
  docstring.

**Format consumers updated** (grep `\[0-9a-f\]{4}` project-wide — 2 hits,
1 relevant): `tests/test_store.py`'s `RUN_ID_RE` (4->8 hex);
`lib/serve.py`'s `_SESSION_ID_RE` is session ids, a separate format,
confirmed untouched. Doc placeholders updated: `DESIGN.md:283`,
`ATTACH-GUIDE.md:129` (`xxxx` -> `xxxxxxxx`). `cgprofile.py:519` calls
`new_run_id()` with no hardcoded width — nothing to change there.

**Test evidence:** `tests/test_store.py` — 30 passed.
`tests/test_serve.py` — 92 passed (session-id path sanity check,
unaffected). Both run serially, `nice -n 19 ionice -c 3`.

**Mutation check:** NOT run this session (deferred — see Gates below).

### C2 — CP-5 (events.jsonl real rows), commit `16b01c1c`

**Change:** `lib/serve.py`'s `_Session` gains `detector`
(`lib.events.Detector`, one per session, built in `_create_session_locked`
from `lib.limits.effective()` at start — same call shape
`cgprofile.py`'s `cmd_collect` already uses), `_prev_record` (previous
tick's full sample record), `last_effective_limits`. `_on_session_sample`
calls `detector.observe(prev_record, record, dt)` every tick (appends
`memory_high_breach`/`memory_max_breach`/`oom_kill`/`psi_spike`/
`swap_burst`/`zswap_refault`/`cpu_throttled` rows as detected) and
`detector.limits_changed(...)` on the existing pid-discovery cadence
(appends `limit_drift` rows when resolved effective limits move).

**Oracle (per the handoff and the CP-5 backlog row):** a session whose
target's `memory.high` changes mid-run must produce AT LEAST one
`events.jsonl` row naming that transition — not just the Summary's
`events.limit_drift` counter.

**Red-first proof:** `tests/test_serve.py::test_on_session_sample_appends_real_events_jsonl_rows`
failed on its first run (before a code fix, not before the test was
written correctly) — `memory_high_breach` was missing from the second
tick's events because event detection was wrongly gated on
`sess._prev_cpu_usage_usec is not None` (the CPU-rate calc's own,
narrower condition) instead of a general "there is a previous record"
condition. Fixed by splitting a `dt_since_prev` (gated only on
`sess._prev_mono is not None`, mirroring `cmd_collect`'s own
unconditional `dt = record["mono"] - prev_mono`) out from the CPU-rate
calc's gate. Test is green after the fix; both failure modes the CP-5
backlog row itself named ("wired in but never actually appends" /
"only increments the counter") are what it is written to catch.

**Summary counters stay byte-identical (handoff requirement):**
`lib/summary.py`'s `SummaryAccumulator`/`_events_block` was NOT touched —
confirmed by grep (no `events.jsonl` or `Detector` reference in
`lib/summary.py`) and by the EXISTING `TestFullLifecycleGoldenReproduction`
class in `tests/test_serve.py` (drives a real session through
`_on_session_sample` against the frozen contract fixtures, asserts
`summary-v1.json`/`summary-container-v1.json` byte-for-byte) still passing
unchanged as part of the 93-passed run below — no new golden fixture was
needed for this proof since the existing one already covers it and stayed
green.

**Test evidence:** `tests/test_serve.py` — 93 passed (92 pre-existing + 1
new). `tests/test_events.py` + `tests/test_summary.py` + `tests/test_limits.py`
— 87 passed, no regressions. All run serially, `nice -n 19 ionice -c 3`.

**Mutation check:** NOT run this session (deferred — see Gates below).

### C3 — CP-7 (manifest `limits` table), commit `907ddd50` (session 2)

**Change:** `_manifest_for` writes `"limits": {sess.cgroup:
cg_module._limits_snapshot(limits_mod, sess.last_effective_limits)}` (was
unconditionally `{}`) — reuses C2's own `sess.last_effective_limits`
(resolved once at session start), no second `limits_mod.effective()` call.
`cgprofile.py`'s `_limits_snapshot` is imported lazily inside
`_manifest_for`, mirroring `cgprofile.py`'s own lazy `lib.serve` import in
`cmd_serve` — confirmed no cycle (grep: no `lib/*.py` imports `cgprofile`
at module level; `cgprofile.py`'s top level never imports `lib.serve`).

**Structural finding, not a bug this fix could close:** a daemon session
profiles exactly one cgroup, so `manifest["limits"]` always has exactly
one entry. `lib/analyze.py`'s `_check_oversubscription` needs >= 2 sibling
entries under a shared parent to produce anything — it is a **permanent
no-op for every daemon session**, this fix included. The CP-7 backlog
row's own oracle sketch ("assert... includes the `oversubscribed:`
finding") did not account for this. `_check_recursiveprot_gap` needs only
one entry and is the real, honest oracle used instead (documented inline
in `_manifest_for` and in the test's own docstring).

**Test evidence** (`tests/test_serve.py::TestManifestLimitsTable`, 3
tests): (1) manifest limits match an independently recomputed
`_limits_snapshot` value byte-for-byte, `protection_mode == "strict"`
(this suite's fixtures have no `/proc/mounts`, matching this estate's real
default); (2) `sess.last_effective_limits = None` degrades to `{}`,
never raises; (3) end-to-end through `lib.analyze.build(sess.rundir)`
against a fake tree with an ancestor `memory.min` gap — asserts
`recursiveprot-gap:<cgroup>` fires and `oversubscribed:*` is structurally
absent.

**Mutation check (manual, 3 planted mutants, targeted files only):** M1
revert to unconditional `{}` — caught (tests 1, 3). M2 wrong cgroup key
(`sess.slice_cgroup` instead of `sess.cgroup`) — caught (tests 1, 3). M3
inverted None-guard — caught (all 3 tests). Applied/reverted via a file
backup + Python patch script, **not** `git checkout` — see the LOG's
"Self-caught mistake" entry: the first mutation round was accidentally
reverted with `git checkout -- lib/serve.py`, which discarded the whole
uncommitted C3 diff, not just the mutant; caught immediately by re-running
the tests, C3 was re-applied from scratch, and the remaining rounds used
`cp` backup/restore (confirmed byte-identical via `diff` after the last
restore).

**Test evidence:** `tests/test_serve.py` — 96 passed (93 + 3 new).
`tests/test_analyze.py` + `tests/test_limits.py` — 154 passed, no
regressions. Combined run: 250 passed.

### C4 — CP-6 (DAMON series in the report), commit `e053276b` (session 2)

**Two findings that narrowed the backlog's "not a small patch" framing:**
(1) `lib/model.py`'s `PANELS` and `lib/report_html.py`'s
`GROUP_LABELS`/`UNIT_LABELS` already carry a `"damon"` entry —
`build_figure` is fully generic over `analysis.groups()`/`series_in()`,
so **zero changes to `report_html.py`** were needed; the whole
subplot/legend/colour/resampling/hover pipeline was already wired, just
never fed. (2) `damon.jsonl` rows carry no timestamp
(`{"hot":…,"warm":…,"cold":…,"idle":…}`, confirmed against
`tests/fixtures/contract/frames/*/damon.json`), but `lib.serve`'s
`_on_session_sample` appends one row per tick in the SAME call as the
matching `samples.jsonl` row whenever DAMON is on (`DamonSession
.last_class_bytes` never returns `None`) — so positional pairing against
the raw sample index is honest, not a hack.

**Change:** `lib/analyze.py` gains `_damon_series(run, index, target)` —
reads `damon.jsonl` via the existing `_read_safe`, returns `[]` on an
absent file or no subject target (no error), otherwise one `Series` per
class (`key="damon.<class>_bytes"`, `group="damon"`, `unit="bytes"`)
paired against `df.index`. `build()` computes `damon_subject` (first
non-observer target's cgroup) and folds the result into `series` before
the existing by-cgroup relabel loop.

**Oracle (CP-6's own, literal — not just "data was read"):**
`TestDamonSeriesInReport` (3 tests): (1) `analyze.build()` against a real
session driven through all 5 golden fixture frames reproduces the EXACT
hot-byte sequence `[157286400, 188743680, 230686720, 199229440,
178257920]`; (2) a `damon=off` session yields no damon series and no
error; (3) `report_html.render()`'s actual HTML output contains
`"cgp-figure"`, `"Plotly.newPlot"`, `"DAMON hot bytes"`, and every one of
the 5 exact hot-byte integers.

**Mutation check (manual, 3 planted mutants):** M1 `_damon_series`
returns `[]` unconditionally — caught (tests 1, 3). M2 inverted
subject-target filter (`!= "observer"` → `== "observer"`) — caught
(tests 1, 3). M3 dropped the `_bytes` key suffix — caught (test 1, exact
key-set assertion). `cp` backup/restore discipline throughout (per the
C3 mistake); `diff` confirmed byte-identical restoration.

**Test evidence:** `tests/test_serve.py` + `tests/test_analyze.py` +
`tests/test_report_html.py` + `tests/test_model.py` — 306 passed, no
regressions. PSI (`full avg10`) ranged 0.12-7.21 across this deliverable
(briefly over the 5% back-off threshold once, with no test mid-flight at
that moment — noted, rechecked before the next run).

### C5 — `cgprofile.slice` (D-29) + `ctl host` §8.5, commit `39d43934`

**Change:**
- `infra/cgprofile.slice` (new): `MemoryMin=128M`, `MemoryHigh=768M`,
  `MemoryMax=1G`, `CPUWeight=100`, `IOWeight=50`, no `ManagedOOM` directive
  (deliberate — the daemon must never be the thing an oomd policy kills),
  `Before=slices.target`, a `Description` naming D-29. Unhyphenated single
  token — stays at cgroup root, same reasoning `srdm.slice`'s own header
  documents (a hyphenated name would nest under an auto-created parent
  carrying `MemoryMin=0`).
- `infra/README.md` (new): operator install steps
  (`cp` → `daemon-reload` → `systemd-analyze verify`, no `systemctl
  start`/`enable` needed for a `.slice`), what happens when NOT installed
  (systemd auto-vivifies it unbounded; `ctl host`/`doctor` read that off
  the resulting cgroup files, since nothing inside the container can see
  `/etc/systemd/system` to check "is the unit file present" any other
  way), and the distinction from mdt's `dev-gates.slice` (different owner,
  different install path, read-only from this side).
- `ciu.compose.yml.j2`: the daemon service's `cgroup_parent` changed from
  the templated `"{{ env.CGROUP_PARENT_DEV_INTERACTIVE }}"` to the
  authored literal `"cgprofile.slice"` (D-29: no environment variable, no
  fallback). Comment rewritten to explain why this is a deliberate
  exception to the general AGENTS.md "no hardcoded slice name" rule.
  `ciu.global.defaults.toml.j2`'s `[governance]` table mirrors the same
  literal (its OWN value is never injected into an author-set key per
  S15.3, but `governance.build_injections()` still resolves it
  unconditionally, so it must stay a valid, non-drifting expression — the
  file's own comment already called this "a single source of truth, not
  two tiers that could drift").
- `ciu.defaults.toml.j2`: new `[cgprofile.daemon]` field `gates_slice =
  "dev-gates.slice"`. `ciu.compose.yml.j2`'s `command:` list gains
  `--gates-slice {{ cgprofile.daemon.gates_slice }}` (unconditional,
  unlike `--observe-slices`, since it always has a value).
- `cgprofile.py`: `serve` subparser gains `--gates-slice` (default
  `dev-gates.slice`), plumbed through `cmd_serve` into
  `SessionServer(gates_slice_name=...)`.
- `lib/serve.py`: `SessionServer.__init__` gains `gates_slice_name`
  (stored as `self.gates_slice_name`); two new constants
  `DEFAULT_GATES_SLICE_NAME = "dev-gates.slice"` and `DAEMON_SLICE_NAME =
  "cgprofile.slice"` (the latter is NOT configurable — the daemon's own
  slice name is fixed by its shipped unit + the compose template's
  authored literal, so there is nothing to resolve). `_host_snapshot()`
  gains two new methods' output under `"gates_slice"`/`"daemon_slice"`:
  - `_gates_slice_snapshot()`: `present: false` shape (`{"name", "present"}`
    only) when `<cgroup_root>/dev.slice/dev-gates.slice` (or whatever
    `--gates-slice` resolves to via `targets_mod.slice_to_path`) does not
    exist on disk; otherwise the full §8.5 shape, `leaves` computed by
    listing the slice's real children and keeping only `rg-*`-prefixed
    ones (C8 has not landed yet, so this is always `[]` today — no further
    change needed once it does, since it counts whatever is really on
    disk), `sessions_live = len(leaves)`.
  - `_daemon_slice_snapshot()`: reads `<cgroup_root>/cgprofile.slice`'s
    `memory.min`/`memory.high` directly (`util.read_int`, which already
    treats `max`/absent as `None` — the same convention the pre-existing
    `_slice_snapshot` uses). No separate "installed" bit — there is
    nothing in this container that can see `/etc/systemd/system`, so `ctl
    host`/`doctor` infer "no unit" from the numbers reading back as
    defaults, exactly as `infra/README.md` documents.

**Oracle (contract §8.5, verified literally):** the two-shape union
(`present: false` alone, vs. the full populated shape with `leaves`/
`sessions_live` computed from real `rg-*` children) and the daemon slice's
nullable `memory_min_bytes`/`memory_high_bytes`.

**v1 golden compatibility (contract §8's own "existing goldens stay
byte-identical"):** `_host_snapshot()` is embedded in THREE verbs
(`host`, list-`status`, single-session `status`) — only the first two were
already golden-tested (`fixtures/contract/host-v1.json`,
`fixtures/contract/status-v1.json` via
`test_cli_golden_round_trip_version_start_status_stop`). Both existing
tests now assert the two new keys' exact "absent" shape explicitly, THEN
strip them before the byte-identical comparison against the untouched v1
golden FILE — the file's bytes are unchanged, only the live response
gained keys the v1 golden predates. `stop`/`version` never call
`_host_snapshot()` (confirmed by grep) — unaffected, no test changes.

**New goldens:** `tests/fixtures/rg55/host-v1.1.json` (new directory —
did not exist before this session; `tests/fixtures/rg55/` is THIS
package's own v1.1 fixture tree, distinct from
`run-gate-project/nyxloom-trove/fixtures/rg55/`, which is the pre-existing
FROZEN v1-only copy `test_contract_fixtures_are_byte_identical_to_the_
frozen_copy` diffs against `tests/fixtures/contract/` — same "rg55" word,
two unrelated directories; noted here so a future session is not confused
by the name collision). Generated by literally running
`SessionServer._host_snapshot()` against a constructed fixture tree
(`fixtures/contract/frames/4` copied, plus a populated `dev-gates.slice`
with two `rg-*` leaves + one non-`rg-*` sibling, plus a populated
`cgprofile.slice`) rather than hand-transcribed, so the golden and the
code cannot silently disagree at authoring time — the test
(`test_gates_and_daemon_slice_present_match_host_v1_1`) rebuilds the same
tree and diffs against the committed file, with explicit oracle
assertions on every field BEFORE the full-document diff.

**New tests** (`tests/test_serve.py`):
- `test_gates_and_daemon_slice_present_match_host_v1_1` — the golden
  above, plus explicit field-by-field oracle assertions (exact bytes,
  sorted+filtered leaves, `sessions_live` count).
- `test_gates_slice_absent_when_directory_does_not_exist` — the "present:
  false" branch on a tree with no `dev.slice` at all; also asserts
  `daemon_slice`'s all-`None` shape on the same bare tree.
- `test_gates_slice_name_is_configurable` — `--gates-slice`/
  `gates_slice_name` actually changes which cgroup path is read (picked a
  one-hyphen slice name deliberately — `slice_to_path`'s systemd-hierarchy
  encoding would nest a two-hyphen name a level deeper, which the test's
  own comment explains).
- `test_cli_serve_gates_slice_flag_reaches_session_server` /
  `test_cli_serve_gates_slice_defaults_to_dev_gates` — `cmd_serve`'s CLI
  plumbing specifically (a fake `SessionServer` captures constructor
  kwargs), since every `_host_snapshot`-level test above constructs
  `SessionServer` directly and would never catch a dropped `--gates-slice`
  → `gates_slice_name` passthrough in `cgprofile.py` itself.

**Mutation check (manual, 5 planted mutants across two clusters —
`cp`-backup/restore discipline, NOT `git checkout`, per the C3 mistake):**
- M1 (host-snapshot cluster) — inverted the `present` guard
  (`if not os.path.isdir(...)` → `if os.path.isdir(...)`): caught by 3
  tests (`test_gates_and_daemon_slice_present_match_host_v1_1`,
  `test_gates_slice_absent_when_directory_does_not_exist`,
  `test_gates_slice_name_is_configurable`).
- M2 (host-snapshot cluster) — dropped the `rg-*` prefix filter on
  `leaves`: caught by `test_gates_and_daemon_slice_present_match_host_v1_1`
  (`some-container.scope` leaked into the list).
- M3 (host-snapshot cluster) — hardcoded `sessions_live` to `0`: caught by
  `test_gates_and_daemon_slice_present_match_host_v1_1`.
- M4 (host-snapshot cluster) — swapped `memory.min`/`memory.high` reads in
  `_daemon_slice_snapshot`: caught by
  `test_gates_and_daemon_slice_present_match_host_v1_1`.
- M5 (CLI-plumbing cluster) — dropped `gates_slice_name=args.gates_slice`
  from `cmd_serve`'s `SessionServer(...)` call: caught by both
  `test_cli_serve_gates_slice_flag_reaches_session_server` and
  `test_cli_serve_gates_slice_defaults_to_dev_gates`.
`md5sum` of both mutated files checked against the pre-mutation value
after every restore; all five matched.

**Test evidence:** `tests/test_serve.py` + `tests/test_cgprofile.py` +
`tests/test_targets.py` together — 359 passed, no regressions. PSI
(`full avg10`) checked before/after, ranged 0.00-4.00 across this
deliverable, never crossed the 5% back-off threshold.

**Scope note — `docs/RG55-INTERFACE-CONTRACT.md` mirror:** confirmed
(via `git show main:...` diff) that this in-worktree copy is currently
STALE relative to `main`'s contract (missing all of §8). Left untouched
this session — the handoff explicitly places "contract mirror
byte-identical" under C9 close-out, not C5, and re-syncing it now would
mean re-syncing it again after C6-C8 add more sections; one sync at C9 is
the documented plan.

**Decision ask:** none genuinely blocking. One judgment call, resolved by
following the design doc's own wording rather than asking: §8.5's prose
says "the daemon reports its OWN slice as `daemon_slice`... null leaves
when the unit is not installed" — taken as loose wording for "the
numeric fields read back null/default", not a literal `"leaves"` key
(the given JSON shape for `daemon_slice` has no such key at all, and
`gates_slice` already owns the real `"leaves"` concept). Logged here per
the BLOCKED protocol's "write the ask into the LOG... continue" even
though this one never stopped the package.

## Live probes

NOT run this session (session 3) either. C5 has no daemon-behaviour
surface to probe live (a static unit file + a `ctl host` field addition,
verified by unit/golden tests against fake cgroup trees) — the handoff's
actual probe list (both carriers diffed, a placed exec-mode probe, a
watch probe) needs C6-C8. `docker build` of `cgprofile:local` also stayed
off-limits all session: the P1 `run-gate-vbpub-r2-…` container HAD exited
by session 3's HOST LOAD recheck (`docker ps -a` showed `Exited (1) 4
minutes ago`), but the OTHER named mutation run (`assay-r2`, a different
package's r2 lane — NOT P1's or P2's own, `pgrep` showed
`python3 ./run-gate.py --base main assay-r2` at a different worktree
path) was still alive throughout via `kill -0 2415767` — so the "while
EITHER is alive" rule stayed in force for the whole session, and the
`ctl host` `gates_slice.present` live-probe item from the handoff's own
list is likewise deferred (needs a real daemon build/run either way; C5's
tests instead prove the two branches — present and absent — directly
against constructed fixture trees, which is a stronger oracle than "one
real host happened to have it installed or not" would have been).

## Gates

NOT run this session (session 3) beyond the targeted pytest files listed
above under C5. Reason: HOST LOAD, same binding text as sessions 1-2 —
"While EITHER is alive: targeted pytest files ONLY, serial, nice -n 19
ionice -c 3; no whole-suite run, no run-gate lane, no image build" — and
`assay-r2` (pid 2415767) never exited during this session (rechecked
immediately before commit). PSI (`full avg10`) was checked before and
after the deliverable and ranged 0.00-4.00, never crossing the 5%
back-off threshold this session. r0/r1/r3 and the r2 mutation lane remain
for whichever session finds ALL named mutation runs gone (the P1 container
specifically has already exited — only `assay-r2` was still blocking as
of this session's close).

## Survivor table

Still empty — no mutation lane run yet, sessions 1-3.

## E-002 telemetry

**Session 1:** ~68 tool calls total (see above).

**Session 2** (fresh successor, counter reset to ~1): orientation before
the first edit (BRIEF-2, HANDOFF in full, design doc in FULL — session 1
only saw it second-hand, this session's own first direct read — contract
§8 re-confirmed against BRIEF-2's summary, HOST LOAD recheck, CP-7/CP-6/
CP-2 backlog rows, `lib/serve.py`'s `_manifest_for`/`_create_session_locked`,
`cgprofile.py`'s `_limits_snapshot`, `lib/analyze.py`'s registry/`build`,
`lib/model.py`'s `Series`/`Analysis`, `lib/report_html.py`'s
`build_figure`/`GROUP_LABELS`, `lib/damon.py`'s `last_class_bytes`,
contract fixtures): ~26 calls. C3 (implementation + the git-checkout
mistake and its recovery + 3-mutant check + LOG + commit): ~20 calls. C4
(implementation + test-writing + 3-mutant check + LOG + commit): ~26
calls. Session 2 cut at ~72 tool calls, past the ~60 ARM threshold and
approaching but not past the ~90 hard ceiling — at the C4 post-commit
boundary (checkpoint clause's "green gate > commit > LOG/REPORT write"
ordering, all three satisfied here).

**Session 3** (fresh successor, counter reset): orientation before the
first edit (BRIEF-3 in full, the HANDOFF on `main`, contract §8 in full,
design doc A1/A2 in full, RW-30/31/34/35/37 from the controller log,
`ciu.compose.yml.j2`, `lib/serve.py`'s `handle_host`/`_host_snapshot`,
`lib/targets.py`'s `slice_to_path`/`list_children`, `lib/util.py`'s
`read_int`/`read_pressure`, `cgprofile.py`'s `cmd_serve`/`build_parser`'s
serve subparser, `tests/conftest.py`'s `write_cgroup`/`cgroup_files`,
existing `fixtures/contract/host-v1.json` + its two consuming tests):
~28 calls. C5 (implementation across 6 files, golden generation via a
scratch script, two existing-test repairs for the v1.1 key additions, 6
new tests, 5-mutant check across 2 clusters, LOG/REPORT writing): ~35
calls. Session 3 cutting after this commit, well under the ~60 ARM
threshold — a genuine "smallest remaining deliverable, no reason to push
further before checking in" stop rather than a checkpoint-forced one;
BRIEF-4 follows for C6 onward.

## Left out and why (as of session 3)

C6 (CP-2 socket carrier), C7 (CP-8 watch role — backlog row not yet
filed), C8 (CP-9 placement — backlog row not yet filed), C9 (close-out:
docs, CHANGES.md, version bump, backlog FIXED rows, contract mirror sync,
INDEX.md) — none started, over any of the three sessions. Each is
independently substantial (a new wire transport with peer-cred auth and
per-verb goldens; a state machine with real subprocess-kill end-to-end
tests; cgroup-write plumbing with a whitelist-refusal test; a close-out
pass touching every doc in the project) — C6 alone is flagged in every
handoff/brief as "the largest remaining deliverable." See BRIEF-4 for the
exact successor continuation point.

# Session 4 — C6 (CP-2, the socket carrier), commit `bb575fd4`

C6 only. C7/C8/C9 NOT started (see "Left out and why (as of session 4)").

## What changed, file by file

| file | change |
|---|---|
| `lib/serve.py` | `_dispatch` now takes the §8.1 WIRE request and refuses anything else; every handler takes the `args` object. `_bind` calls the new `_assert_socket_permissions`. New `_log`, `_peer_uid`, `_peer_allowed`, module-level `parse_allow_uids`, `_WIRE_KEYS`, `SOCKET_DIR_MODE`, `SOCKET_MODE`, `ALLOW_UIDS_ENV`. `__init__` takes `allow_uids`. `_handle_connection` checks peer creds before reading a byte. `handle_version` gains §8.6's `transports`. |
| `cgprofile.py` | new `_ctl_wire()` + `_ctl_request()` rewritten to build §8.1 (the reference translator); `CONTRACT_VERSION` constant with the same drift-guard shape `DEFAULT_CTL_SOCKET` already had; `cmd_serve` reads `CGPROFILE_ALLOW_UIDS` and refuses to start on a malformed value. |
| `ciu.compose.yml.j2` | bind-mounts `{{ cgprofile.daemon.socket_dir }}` host→container; conditional `environment: CGPROFILE_ALLOW_UIDS`. |
| `ciu.defaults.toml.j2` | new `socket_dir` (default `/run/cgprofile`) and `allow_uids` (default empty), each with the operator note. |
| `docs/PROTOCOL.md` | NEW — carriers, the one request shape, the per-verb `args` table (incl. `watch`/placement/policy names for C7/C8), authorisation, `version.transports`, error codes, goldens. |
| `README.md` | "Running the daemon" gains the two-carrier section: the `socat` one-liner, the mdt `mdt-cgprofile.conf` host prerequisite, docker-group access, the root:root degradation, `CGPROFILE_ALLOW_UIDS`. |
| `tests/fixtures/rg55/socket/` | NEW — `<verb>-{request,response}.json` for all 7 verbs + `watch-request.json` + a README (provenance, regeneration command, the only normalization, why `gc` says `kept: 2`). |
| `tests/test_serve_socket_carrier.py` | NEW — 35 tests (below). |
| `tests/test_serve.py` | migrated to the wire shape via new `_wire()`/`_wire_bytes()` helpers; `version`'s v1 golden comparison asserts-then-strips `transports` (C5's pattern for `host`). |
| `tests/test_cgprofile.py` | `TestCtlRequest` expectations rewritten to §8.1; new `test_contract_version_matches_lib_serve` drift guard. |

## Exactly what is tested (35 tests in `tests/test_serve_socket_carrier.py`)

- **Wire shape (7):** the v1 flat request is `bad-argument` naming the key
  and §8.1; a foreign `contract` major is refused naming both numbers;
  non-object `args`; a non-object request; `args` may be omitted for an
  argument-less verb; `contract` may be omitted; an unknown verb still
  names the verb.
- **`parse_allow_uids` (4):** unset/empty/whitespace/`,,` → no allowlist;
  a list parses with duplicates dropped and order kept; a malformed entry
  raises naming it; a negative uid raises.
- **Peer credentials (8):** no allowlist serves any peer; a listed uid is
  served; an unlisted uid gets exactly
  `{"ok": false, "contract": 1, "error": {"code": "peer-refused", …}}` and
  the connection closes; **uid 0 is served even when not listed** (the
  exec carrier's own path); unreadable credentials are refused under an
  allowlist and served without one; a truncated `SO_PEERCRED` payload
  counts as unreadable; and ONE test does a **real** `SO_PEERCRED` round
  trip over a real socket — refused when the allowlist holds only
  `getuid()+1000`, served once it holds `getuid()`.
- **`serve` CLI (3):** the environment reaches `SessionServer.allow_uids`;
  unset → `None`; malformed → exit 2, message names the variable, and the
  server is never constructed (the double fails the test if it is).
- **Permissions (5):** the directory really becomes `0770` and the socket
  really `0660` on a real bind; the socket is chowned to `root:<the
  directory's own gid>` (asserted as the call, with a faked gid 4242,
  since chown needs root); a `root:root` directory logs the "socket
  carrier root-only until host-setup is installed" line naming
  `mdt-cgprofile.conf` **and the daemon is still listening**; `chmod`/
  `chown` raising `PermissionError` degrades to log lines and the listener
  still binds; an unstattable directory likewise.
- **`version.transports` (2):** the exact §8.6 shape before binding
  (`listening: false`, `allow_uids: []`), and a configured allowlist is
  disclosed.
- **Goldens (4):** every verb's request and response byte-compared against
  `fixtures/rg55/socket/`; a test that every dispatcher verb HAS a golden
  pair (so a new verb cannot be added without one); the `watch` request
  golden frozen for C7 with no response golden; and a test that the
  daemon still answers `bad-argument` for that exact frozen request.
- **Parity (2):** §8.1 rule 1's own diff — every verb over both carriers
  against ONE serve loop, requests and responses compared; plus a
  non-vacuity guard (the `start` really started, `status` really listed
  that session with `samples == 1`, `stop` really returned a schema-1
  summary, `report` really returned a `report.html` path) so the parity
  test cannot pass by both carriers agreeing on the same error.

The exec carrier is simulated the way the handoff prescribes and the way
`test_cli_host_v1` already did it — `cg.main(["ctl", "--socket", …])`
in-process, which is byte-for-byte what `docker exec … cgprofile ctl`
runs. The socket carrier deliberately uses a hand-written stdlib client
rather than `cg._ctl_roundtrip`, so the diff compares two independent
implementations.

## Goldens: how they were produced

`CGPROFILE_REGEN_SOCKET_GOLDENS=1 python3 -m pytest
tests/test_serve_socket_carrier.py -q` runs the real scenario and writes
the files; the same test without that variable byte-compares them. Every
request comes from `cgprofile.py`'s own `_ctl_request()` driven through
the real argument parser (the contract's reference translator), never a
hand-written dict. Determinism: fixed wall clock, fixed session ids, a
per-thread counting sampler clock, a fake cgroup/proc tree, DAMON forced
available with sessions started `--damon off`, and a sampler sleep that
gives each session exactly one sample. The ONLY normalization is the
test's tmp paths → the production defaults (`/var/lib/cgprofile/sessions`,
`/run/cgprofile/ctl.sock`). Re-ran three times consecutively: identical
bytes, 35 passed each time.

v1 goldens stayed byte-identical: `fixtures/contract/version-v1.json` is
untouched and still compared in full — the test asserts the new
`transports` block exactly, then strips it, exactly as C5 did for `host`'s
two new keys.

## Mutation check (5 planted, 5 caught)

`cp` backup + `md5sum` verification after every restore (never `git
checkout`; md5 returned to `1b49465e…` / `193455f7…` each time).

| # | mutant | caught by |
|---|---|---|
| M1 | `_peer_allowed`: unreadable creds fail OPEN (`return True` instead of `False`) | `test_unreadable_credentials_are_refused_when_an_allowlist_is_set`, `test_a_truncated_peercred_payload_is_treated_as_unreadable` |
| M2 | `_dispatch`: drop the unexpected-top-level-key check (v1 flat shape silently tolerated) | `test_refuses_the_v1_flat_request_shape` |
| M3 | `_assert_socket_permissions`: socket `0666` instead of `0660` (world-writable) | `test_directory_becomes_0770_and_the_socket_0660` |
| M4 | `handle_version`: `transports.socket.listening` hardcoded `true` | `test_shape_before_the_listener_is_bound` |
| M5 | `_ctl_request`: the translator renames the arg (`session` → `session_id`) | `test_socket_goldens_are_the_live_documents`, `test_the_scenario_actually_exercised_a_session`, `test_status_stop_report_carry_session` |

M5 is the interesting one: it is the shape of the bug that would break P5
silently (a consumer coded against the goldens would keep working, the
daemon would answer `unknown-session` forever), and three independent
tests catch it.

## Gates

- `tests/test_serve_socket_carrier.py tests/test_serve.py
  tests/test_cgprofile.py` — **323 passed**, serial, `nice -n 19 ionice
  -c 3`.
- Coverage (`--cov=lib.serve --cov=cgprofile --cov-branch`):
  `cgprofile.py` 618 statements / 160 branches, **0 missing, 100%**;
  `lib/serve.py` 716 statements / 194 branches, **0 missing lines**, one
  partial branch `812->818` — that is C2's `limits_changed` drift path in
  `_on_session_sample`, untouched by this session's diff (verified against
  `git diff -U0`'s hunk list). 100% line AND branch on every changed line.
- **No run-gate lane was run this session** (r0/r1/r3/r2 all still
  unexecuted for this package, across all four sessions): both estate
  mutation runs were alive the entire time — see HOST LOAD below.
- Compose template: rendered locally with jinja2 + `yaml.safe_load` (no
  container, no ciu) both with and without `allow_uids`; volumes render as
  `['cgprofile-sessions:/var/lib/cgprofile', '/run/cgprofile:/run/cgprofile']`
  and the `environment` block appears only when the allowlist is set. A
  full `ciu`-side render (governance overlay) was NOT run — no image build
  or stack operation was permitted this session.

## Live probe: DEFERRED, with the plan

Not run. HOST LOAD was binding all session: the P1 r2 container
(`run-gate-vbpub-r2-680904-1789228700`) was up from the start and
`assay-r2` (pid 2415767) never exited; PSI memory `full avg10` was 5.09 at
the start (already over the 5 back-off threshold) with loadavg 8.46. The
handoff forbids an image build or any lane while either run is alive, and
the probe needs `cgprofile:local` built.

The plan a successor (or the reviewer) runs once both are gone:

1. `docker ps -a | grep run-gate-vbpub` and `pgrep -af 'assay-r2|assay.cli
   run r2'` both empty; `cat /proc/pressure/memory` `full avg10` < 5.
2. `python3 build-push.py --build` → `cgprofile:local`, then
   `docker update --cpus=3 <probe>` right after launch.
3. `mkdir -p /tmp/cgprofile-p6` — the SCRATCH mount, never the real
   `/run/cgprofile`. Note its gid: as a `vscode`-owned directory the
   daemon will chown the socket to that gid and log nothing special; to
   exercise the root:root path instead, `sudo chown root:root
   /tmp/cgprofile-p6` and expect the INFO line in `docker logs`.
4. `docker run --rm -d --name cgprofile-p6-probe --privileged --pid=host
   --cgroupns=host --network none --cgroup-parent cgprofile.slice
   -v /tmp/cgprofile-p6:/run/cgprofile cgprofile:local` (remove in a
   `finally`; NEVER touch `cgprofile-host-daemon`).
5. Exec carrier: `docker exec cgprofile-p6-probe cgprofile ctl <verb>
   --json` for every verb. Socket carrier: from a throwaway
   `cmru-enroll-fixture:local` container mounting the same
   `/tmp/cgprofile-p6`, `printf '{"verb": "<v>", "args": {…}, "contract":
   1}\n' | socat - UNIX-CONNECT:/run/cgprofile/ctl.sock` — show `id` in
   that container (docker-group membership is the thing being
   demonstrated). Diff the two documents per verb; they should differ only
   in genuinely time-varying fields.
6. `ls -l /tmp/cgprofile-p6` → the socket at `0660`, group = the
   directory's gid; `docker exec cgprofile-p6-probe cgprofile ctl version
   --json | jq .transports` → §8.6's block with `listening: true`.
7. Peer-cred: re-run the probe with `-e CGPROFILE_ALLOW_UIDS=0` and
   confirm the throwaway container's non-root user gets `peer-refused`
   while `docker exec` (uid 0) still works.

## Left out and why (as of session 4)

- **C7 (CP-8 watch role)** — not started, deliberately. The dispatch's own
  instruction: "If you cannot finish C7, cut BEFORE starting it rather
  than leaving it half-done." C6 consumed the session (the wire-shape
  migration touched ~35 existing call sites on top of the new carrier
  code, goldens and parity harness); C7 is a state machine with a real
  kill path and three new golden families. Its backlog row CP-8 is still
  UNFILED — that is the successor's first action.
- **C8 (CP-9 placement), C9 (close-out)** — unchanged from BRIEF-4.
- **Backlog bookkeeping** — CP-2's row is now FIXED IN CODE but its file
  still says `status: open`, as do CP-4/CP-5/CP-6/CP-7's. C9 owns the
  pass; a successor with spare budget can do it earlier.
- **`CGPROFILE_VERSION` is still `"1.0.0"`** — the bump to 1.1.0 is C9's,
  per BRIEF-3/BRIEF-4.
- **The in-worktree contract mirror `docs/RG55-INTERFACE-CONTRACT.md` is
  still stale** (no §8). C9 syncs it; C6 deliberately did not hand-edit it
  (the dispatch forbids editing the mirror copies).

---

# C7 — CP-8, the watch role (`4fa725dc`)

Contract §8.2 (`ctl watch`, streaming), §8.4 (liveness + the policy
options + the state vocabulary), §8.7 (Summary additions), §8.8
(`bad-policy`, `not-streaming`). Design A1/D-27, D-17, D-22. Backlog row
CP-8 filed before any code (`nyxloom backlog index` regenerated).

## The oracle CP-8 sketched, and the test that implements it

| CP-8's proposed contract | what implements it | what proves it |
|---|---|---|
| four `start` policy options, `bad-policy` (exit 2) and NO session on anything unparsable | `liveness.parse_policy` + the parse in `handle_start` BEFORE the registry lock | `TestPolicyRefusal::test_an_unparsable_policy_is_bad_policy_and_starts_nothing` (4 cases) — the oracle is `server._sessions == {} and server._by_target == {}`, not the response |
| the §8.4 `liveness` block, per session, every field | `LivenessTracker.liveness_block()` fed by `_observe_liveness` | `TestBlocks::test_the_liveness_block_is_the_contract_shape` (exact key set) + `test_a_valid_policy_reaches_the_session_and_status` |
| idle clock PAUSES over memory `full avg10 > 5`, gates slice or host | `_pause()` (slice first, then host) + `_gates_slice_psi_full_avg10` | `test_the_idle_clock_pauses_under_slice_and_host_pressure` (100 s of pressure = 0 s idle, both reasons), `test_activity_resets_the_pause_accounting_too` |
| `auto` idle bound = `max(300, 3 x cadence hint)` | `Policy.idle_bound_seconds` | `test_idle_bound_auto_is_the_floor_until_a_hint_arrives`, and end to end in `test_a_progress_stream_is_read_through_proc_root` (a real `plan` event's hint of 45 s -> a 300 s bound) |
| `auto` ceiling = `3 x meta.expected.duration_s` when known, else none | `Policy.ceiling_seconds` + `_expected_duration_seconds` | `test_ceiling_auto_needs_a_declared_expected_duration`, `test_expected_duration_is_read_only_when_it_is_really_a_duration` (9 shapes) |
| stream read through `/proc/<pid>/root/<path>`, bounded, last COMPLETE line, terminal events | `resolve_stream_path` + `read_progress_stream` | `TestReadProgressStream` (11 tests: absent, directory, half-written line, >64 KiB with the hint in the head, garbage lines, growth with no complete object yet) |
| every §8.4 state | `LivenessTracker._evaluate` | `TestStateMachine` — one test per state and per transition, including recovery back to `ok` |
| `--on-stall kill` -> SIGKILL to the token subtree; verdict recorded | `_kill_targets` + `_enforce_stall_kill` + `record_kill` | `TestRealSubtreeEnforcement` (a REAL `sleep`, killed, `returncode == -9`) and `TestKillTargets` (5 tests) |
| `status` and `stop` return the verdict; Summary gains `liveness`/`watch` | `_status_entry`, `_finalize_session_locked` | `test_the_summary_carries_liveness_and_watch` (response AND the on-disk `summary.json`), `summary-v1.1.json`, `status-v1.1.json` |
| `ctl watch` streams §8.2's lines on BOTH carriers | `_watch_connection`/`_stream_watch` + `_ctl_stream` | `TestWatchStream` (7 tests), `TestWatchOverExec` (5 tests), `socket/watch-response.json` |

## What is actually tested (the claims a reviewer should check first)

1. **A real lane is really killed.** `TestRealSubtreeEnforcement` spawns a
   real `sleep 30` carrying a real `RUN_GATE_PROFILE_SESSION` in its
   environment, names its pid in the (faked) `cgroup.procs`, and lets the
   daemon resolve it through the REAL `/proc` with the same
   `SubtreeResolver` production uses. Under `--on-stall kill` the process
   ends with `returncode == -signal.SIGKILL`; under `--on-stall report` the
   same lane is untouched and the verdict says `reported`. Only the cgroup
   tree is faked (a test cannot create a cgroup).
2. **`bad-policy` starts nothing.** The oracle is the registry, not the
   response document.
3. **A refused kill never claims a kill.** A `container-shared` session
   with no token would mean killing the devcontainer; the daemon refuses,
   records `kill-refused:no-token-in-shared-scope`, and the verdict stays
   `reported`.
4. **The stream is a stream.** Over the socket: `reading` on attach,
   `reading` when `stop` wakes it, `verdict` on the state change (exactly
   the four §8.2 keys), exactly one `end` — frozen as
   `socket/watch-response.json`. Over exec: `ctl watch` forwards every line
   and exits 0. And the daemon answers `version` on another connection
   while a stream is open, which is the property the serial accept loop
   would otherwise have destroyed.
5. **v1 goldens are still byte-identical.** `summary-v1.json`,
   `summary-container-v1.json`, `status-v1.json`, `stop-v1.json` all pass
   after the new keys are stripped — the machine-checkable form of §8's
   "additive" promise.

## Goldens

| file | what it is |
|---|---|
| `fixtures/rg55/status-v1.1.json` | the whole `status` document with §8.5's two host keys and §8.4's two session keys (from the lockstep CLI test, all clocks injected) |
| `fixtures/rg55/summary-v1.1.json` | §8.7's Summary with `liveness` + `watch` (from the socket scenario, for determinism — see LOG decision 10) |
| `fixtures/rg55/watch-reading.json` / `-verdict.json` / `-end.json` | one file per §8.2 line SHAPE |
| `fixtures/rg55/socket/watch-response.json` | the whole stream one connection carried, answering the request golden C6 froze |
| `fixtures/rg55/socket/{start-request,status-response,stop-response}.json` | REGENERATED: `start` now carries the four policy keys (explicit nulls), `status`/`stop` the two new blocks. Diff read before committing |

## Mutation evidence (5 planted, 5 caught)

| # | mutant | caught by |
|---|---|---|
| M1 | `CPU_GROWTH_SECONDS = 1.0` -> `0.1` (the activity threshold) | `test_cpu_growth_under_the_threshold_is_not_activity` |
| M2 | `elif paused:` -> `elif False:` (the PSI pause never accrues) | `test_the_idle_clock_pauses_under_slice_and_host_pressure`, `test_activity_resets_the_pause_accounting_too` |
| M3 | `record_kill_refused` sets `VERDICT_KILLED` | `test_a_refused_kill_is_reported_never_claimed`, `TestKillTargets::test_the_refusal_is_recorded_on_the_verdict` |
| M4 | `_kill_targets` always returns the pids (the shared-scope refusal deleted) | `test_shared_scope_without_a_token_is_refused`, `test_the_refusal_is_recorded_on_the_verdict` |
| M5 | the stream emits a `verdict` on every reading | `test_the_watch_stream_golden_is_the_live_document`, `test_each_watch_line_shape_is_frozen_on_its_own`, `test_readings_a_verdict_on_change_and_exactly_one_end`, `test_ctl_watch_forwards_every_line_and_exits_0` |

Backup discipline: `cp` to the scratchpad + `md5sum -c` back to the
pre-mutation hashes after the last restore (never `git checkout`).

## Coverage

`lib/liveness.py` 345 statements / 118 branches: **100% + 100%**.
`cgprofile.py`: **100% + 100%**. `lib/serve.py`: 0 missing lines, one
partial branch `872->878` — C2's `limits_changed` drift path, untouched by
this session's diff (already recorded in C6's evidence).

## Gates

The `r0-r1` lane was run for the first time in this package's five
sessions, per RW-39 (memory `full avg10` 0.85 at launch, one lane, `nice
-n 19 ionice -c 3`, while the estate's two mutation runs were still live).
First invocation was refused by run-gate itself — `--base` is meaningless
for a command lane with no `{base}` token in its argv, and run-gate says so
rather than dropping the ref silently; re-run without it.

**Run 1 (`./run-gate.py r0-r1`): RED, `exit 2`.** 1260 tests passed; the
lane failed ONLY on its `fail-under=100` coverage gate, at 99% total, on
two gaps that predate C7 — `lib/analyze.py:703` (C4's `_damon_series`
`n == 0` early return: `damon.jsonl` has rows but the sample frame has no
time axis) and `lib/serve.py 872->878` (C2's `if sess.last_effective_limits
is not None` guard, never taken False). That is exactly what five sessions
without a lane run hides, and why RW-39 asked for one.

Both were covered honestly rather than excused:
`test_damon_rows_with_an_empty_time_index_produce_no_series` (a run whose
samples were all dropped must render NO DAMON figure, not one with an empty
x-axis) and `test_a_session_with_no_resolved_limits_yet_detects_no_drift`
(no baseline to diff against must emit no `limit_drift` row AND refresh the
baseline, rather than compare against `None` or invent an event).

**Run 2 (`./run-gate.py r0-r1`): GREEN, `exit 0`.** `1262 passed`,
`TOTAL 5007 statements / 1636 branches, 0 missing, 0 partial, 100%` across
the whole project — the first green package gate this package has had.

## Deferred out of C7 (and why)

- **C8 (CP-9 placement)** — not started. The dispatch: "cut BEFORE starting
  C8 rather than leaving it half-done". Its seam is marked in
  `_enforce_stall_kill` (the `cgroup.kill` branch) and its `placement` key
  is already `null` in every `reading` line, so C8 adds a value where a key
  already exists rather than changing a shape a consumer parses.
- **`throttled` in production.** The state is implemented and tested, but
  the leaf readings that feed it only exist once C8 places a lane; until
  then no caller supplies `leaf_psi_full_avg10`/`leaf_memory_high_applied`
  and the state is unreachable outside tests. Said so in the tracker's
  docstring rather than leaving a silent hole.
- **The live probes (C6's 7-step plan and C7's own watch probe)** — an
  image build/probe container is still forbidden while the estate's
  mutation runs are live (RW-39 relaxes only the bare-host pytest lanes).
- **The r2 mutation lane and the r3 canary** — same rule; r2 is LAST and
  once, per the handoff.

# C8 — CP-9, placement (§8.3, D-20/D-25, RW-35a)

Backlog row `CP-9` filed first (INDEX regenerated). New `lib/placement.py`;
wiring in `lib/serve.py`, `lib/liveness.py`, `cgprofile.py`; docs in
`docs/PROTOCOL.md`; 72 tests in `tests/test_serve_placement.py`.

## The oracle CP-9 sketched → the test that implements it

| CP-9 / §8.3 clause | test |
|---|---|
| `--place` requires `--token` | `TestApply::test_a_refusal_leaves_no_leaf_and_no_exception[no-token]`, `TestServerPlacement::test_a_refusal_never_fails_start[...no-token]` |
| gates slice absent on the host | the same two, `[no-gates-slice]` |
| `--memory-max` over the slice's `memory.max` | `test_a_memory_max_above_the_slice_ceiling_is_over_slice`; `memory.max: max` has no ceiling to exceed → `test_an_unlimited_slice_has_no_ceiling_to_exceed` |
| leaf created under the gates slice | `TestApply::test_the_leaf_is_created_capped_and_populated` (+ the real directory on disk) |
| `+memory +cpu +pids` only when the slice lacks them | `test_the_controllers_are_delegated_only_when_the_slice_lacks_them` (both arms) |
| caps applied and **read back**, never echoed | `test_applied_is_read_back_from_the_leaf_not_echoed` — a subclass whose writes land ROUNDED, so an echoing implementation reports a number the file does not hold |
| migrate every resolved pid, **also later ones** | `TestMigration::test_later_pids_are_moved_and_earlier_ones_are_not_rewritten`; through the real sampler: `TestServerPlacement::test_a_pid_discovered_later_is_migrated_by_the_sampler` |
| `pids_moved` counted | the block assertions in both of the above |
| `stop` moves survivors back, then `rmdir` | `TestRelease::test_survivors_go_back_to_the_origin_scope_and_the_leaf_goes` (incl. a pid forked after the last discovery tick) |
| `rmdir` retried 3× over 3 s, then `place-refused:write-failed:<file>` | `test_a_leaf_that_will_not_go_is_retried_three_times_then_reported` (attempt count AND the sleep vector asserted) |
| placement never fails `start` | `TestServerPlacement::test_a_refusal_never_fails_start` (3 codes; the session starts, samples and stops) |
| D-15 stays a whitelist | `TestWriteGuard` — 8 planted write targets refused, every leaf file admitted, origin `cgroup.procs` admitted for the move-back and its `memory.high` refused |
| a `-` value to `cgroup.subtree_control` is refused | `test_subtree_control_refuses_anything_but_plus_the_three` (`-memory`, `+memory -cpu`, `+io`, empty) |
| `cgroup.kill` on a placed session | `TestPlacedKill::test_a_placed_session_dies_by_cgroup_kill_not_by_pid`; fallback intact → `test_an_unplaced_session_still_dies_by_pid`, `test_a_leaf_that_will_not_take_the_write_falls_back_to_pids` |
| `throttled` becomes reachable | `TestServerPlacement::test_a_placed_leaf_makes_throttled_reachable` (fake leaf `memory.pressure` full avg10 = 44 with `memory.high` applied) and its negative `test_an_unplaced_session_cannot_be_throttled` |
| every document carries the block | `test_start_places_the_lane_and_every_document_carries_the_block` (start, `status`, a `watch` reading, the Summary) |
| D-25 "every cgroup write is an events.jsonl row" | the same test reads the session's real `events.jsonl` |

## Goldens

| file | what it freezes |
|---|---|
| `fixtures/rg55/start-placed-v1.1.json` | a full placed `start` response |
| `fixtures/rg55/start-refused-v1.1.json` | all five §8.8 refusal blocks, one per key |
| `fixtures/rg55/status-v1.1.json` (regen) | `+ "placement": null` on the session entry |
| `fixtures/rg55/socket/start-{request,response}.json` (regen) | the four new `args` keys; `"placement": null` in the response |
| `fixtures/rg55/socket/status-response.json` (regen) | same additive key |
| v1 goldens (`start-v1.json`, `status-v1.json`, `summary-v1.json`, …) | **byte-identical** — asserted by stripping `placement` in `test_serve.py`'s round-trip, the treatment C5/C6/C7 used for their own keys |

## Mutation evidence (6 planted, 6 caught)

Planted in a `cp -a` copy of the project (never the live tree), one at a
time, each reverted before the next:

| mutant | catching test |
|---|---|
| M1 `cgroup.subtree_control` accepts a non-`+` value | `test_subtree_control_refuses_anything_but_plus_the_three` |
| M2 `applied` echoes the request instead of reading back | `test_applied_is_read_back_from_the_leaf_not_echoed` |
| M3 `migrate` re-writes pids it already moved | `test_later_pids_are_moved_and_earlier_ones_are_not_rewritten` |
| M4 the guard admits any file under the gates slice | `test_a_write_planted_anywhere_else_is_refused` |
| M5 `release` skips the move-back | `test_survivors_go_back_to_the_origin_scope_and_the_leaf_goes` |
| M6 `_enforce_stall_kill` ignores the leaf (always the pid loop) | `test_a_placed_session_dies_by_cgroup_kill_not_by_pid` |

## Coverage

`lib/placement.py` 267 statements / 86 branches — **100% line, 100%
branch**. `lib/serve.py`, `lib/liveness.py` and `cgprofile.py` remain 100%
line + 100% branch over the same targeted set (502 tests). The two gaps the
first coverage pass found (`_abandon`'s failing `rmdir`, `kill`'s no-log
arm) were closed with two named tests, not with pragmas.

## Gates

`./run-gate.py r0-r1` (bare, no `--base` — the lane refuses one), run twice
under RW-39 (memory `full avg10` 0.35 then 2.67, `nice -n 19 ionice -c 3`,
one lane at a time, no image build, no r2, singleton untouched):

* run 1 — RED on `test_start_builds_the_full_request` (mine: the
  `_ctl_request` shape test hand-builds its Namespace and needed the four
  new options). Fixed.
* run 2 — RED on `TestRealSubtreeEnforcement`'s two real-subtree tests and
  `TestPeerCredentials::test_a_real_socket_peer_is_this_process_uid`.
  **Pre-existing and order-dependent, reproduced at `7c34dcc2` (pre-C8)** —
  the full bisection, with the exact commands, is in the LOG under
  "Finding: the r0/r1 lane is order-dependent". `pytest-randomly` reseeds
  per run, so the lane's colour currently depends on the draw. C8's own 72
  tests are green in every order tried, and the whole targeted set (502
  tests over the six serve/liveness/CLI files) is green in declaration
  order.

**No clean green lane verdict is claimed for this tip.** The honest
statement is: green except for two pre-existing order-dependent failures
that also fail at the pre-C8 tip under the same order.

## Live probes — still deferred, plan unchanged

Both estate mutation runs were live for this whole session (P1's
`run-gate-vbpub-r2-680904-…` container and P2's bare-host `assay-r2`, pid
1499375), so RW-39/RW-42 forbid an image build or a probe container. C6's
7-step probe plan, C7's watch probe and now C8's placement probe (`docker
run --rm -d --name cgprofile-p6-probe --privileged --pid=host
--cgroupns=host --network none --cgroup-parent cgprofile.slice -v
/tmp/cgprofile-p6:/run/cgprofile`, then `ctl start --place --memory-high
…` against an 80 MiB lane → leaf exists during, `applied` read back, gone
after `stop`; `ctl host` for `gates_slice.present`) are all still pending,
in a `finally`-removed instance, never the singleton.

## Deferred out of C8 (and why)

- **C9 (close-out)** — not started, per the dispatch's "cut before C9
  rather than half-doing it". `docs/PROTOCOL.md` is complete for C8 (the
  placement `args` table landed and the "lands with C8" hedges are gone),
  but README/ATTACH-GUIDE/DESIGN.md/CHANGES.md, the CP-* → FIXED rows, the
  `1.0.0` → `1.1.0` version bump and the contract mirror are all untouched.
- **A backlog row for the C7 test-isolation defect** — found at the very
  end of the session; the reproduction is in the LOG so the successor can
  file it as CP-10 with the evidence rather than re-derive it.
- **`gc` does not reclaim an orphaned leaf.** A daemon that dies with a
  placed session live leaves `<gates slice>/rg-<token>` behind:
  `_recover_orphans` finalizes the session's manifest but has no placement
  object to release. Worth a row (CP-1's retention work is the natural
  home); not in §8.3's text, so not invented here.
## Session 7 — CP-10 root cause + proof, C9 close-out, gates

### CP-10 — root cause (corrects session 6's hypothesis)

**Session 6's finding, restated:** `TestPeerCredentials` running before
`TestRealSubtreeEnforcement` correlated, across two trials, with the
latter's two `on_stall` tests failing (`state == "ok"` instead of
`"stalled"`).

**What session 7 found on re-investigation:** that correlation does not
hold across MORE than two trials. Repeated runs of the identical two-class
combo, in BOTH orders, at different real moments, show red/green tracking
the HOST's real memory PSI at run time, not which class ran first:

| order | run | host `full avg10` at start | result |
|---|---|---|---|
| forward (PeerCred, RealSubtree) | 1 | 8–16 (elevated) | 2 failed |
| forward | 2 (immediate retry) | <1 | 11 passed |
| reverse (RealSubtree, PeerCred) | 1 | elevated | 2 failed |
| reverse + forward, 3 `pytest-randomly` seeds each, post-fix | 6 runs | <1 to ~11 | 12/12 passed, every run |

**Mechanism:** `TestRealSubtreeEnforcement` passes `proc_root="/proc"`
(needed for real pid/subtree resolution via a real `sleep` subprocess).
`lib/serve.py`'s `SessionServer` had exactly one `proc_root` knob, and
`lib.metrics.sample_host(proc_root=self.proc_root)` (4 call sites) is what
feeds §8.4's pause condition (`host_psi_full_avg10 > 5.0` pauses the idle
clock). So the daemon's liveness pause check was reading THIS HOST's real,
ambient memory PSI — driven by the estate's own concurrent mutation runs
and gate lanes, not by anything the test controls. When that value sat
above 5.0 through the test's 30 s budget, the idle clock never advanced
and the `stalled` assertion failed with `state == "ok"`.

**Fix (`b50163e9`):** `SessionServer.__init__` gains `host_proc_root:
Optional[str] = None` (default `= proc_root`, so every real caller and
every other existing test — all of which already pass one root for both
concerns — is byte-identical); the 4 `metrics.sample_host()` call sites
read `self.host_proc_root`. `TestRealSubtreeEnforcement`'s three server
constructions pass a `_fake_proc(tmp_path)` (zero-pressure) as
`host_proc_root` alongside the real `proc_root="/proc"`, decoupling "which
process to watch" from "how loaded is this host right now" — the same
kind of test seam `cgroup_root`/`proc_root` already were for every OTHER
reader in the package (the class's own docstring said so before this fix;
it just wasn't true of the host-PSI read specifically).

**New regression test** proves the decoupling directly rather than by
absence-of-flake: `test_host_pressure_is_read_from_host_proc_root_not_the_
real_proc` claims `full avg10=99` on `host_proc_root` and asserts the
session stays `ok`/paused (`pause_reason == "host-psi"`, `paused_for_
seconds > 0`) — proven at real host PSI ranging from <1 to ~24 across the
runs while landing this fix, i.e. independent of what the real host was
doing. First draft of this test asserted after a flat 2.0 s sleep and
itself flaked (`paused_for_seconds == 0.0`) — traced to `_observe_
liveness` running on the DISCOVERY cadence (`DISCOVERY_INTERVAL_SECONDS =
2.0`), not the sample cadence; rewritten to poll `sess.watch.readings >=
3` with a 15 s deadline. Left as a comment in the test: this cadence
detail is itself part of why a fixed short sleep is the wrong pattern for
any test of this codepath.

### Docs disposition (C9)

| doc | disposition |
|---|---|
| `docs/PROTOCOL.md` | already complete as of C8 (verb table, placement args, streaming exception) — no change |
| `README.md` "Running the daemon" | added: `cgprofile.slice`/`infra/README.md` pointer, liveness/watch policy options + `ctl watch`, placement options + refusal semantics |
| `ATTACH-GUIDE.md` | added §9: consumer-facing view of watch (`verdict` → the caller's own stall-exit path) and placement (`applied` vs the request) |
| `DESIGN.md` | added §4.15a: D-27..D-30 summary, pointers to the design doc of record on `main` (§A1/§A2) and contract §8; corrected two stale P1-era claims ("limits always `{}}`"/"`damon.jsonl` unread") C3/C4 had already fixed |
| `CHANGES.md` | created (didn't exist for this project) — estate-standard shape, `[Unreleased]` with one line per CP id + landing commit |
| contract mirror (`docs/RG55-INTERFACE-CONTRACT.md`) | untouched, as directed — arrives with the `main` merge |

### Version sweep (1.0.0 -> 1.1.0)

| file | field | change |
|---|---|---|
| `lib/serve.py` | `CGPROFILE_VERSION` | `"1.0.0"` -> `"1.1.0"` |
| `Dockerfile` | `ARG CGPROFILE_VERSION` | `1.0.0` -> `1.1.0` (OCI label derives from it) |
| `tests/test_summary.py` | `_new_accumulator`'s `daemon_version=` | `"1.0.0"` -> `"1.1.0"` (this literal reproduces `summary-v1.json`/`summary-container-v1.json` byte-for-byte — not an independent test fixture) |
| `tests/fixtures/contract/{version,stop,summary,summary-container}-v1.json` | embedded `cgprofile`/`daemon.version` | `"1.0.0"` -> `"1.1.0"` |
| `tests/fixtures/rg55/summary-v1.1.json`, `tests/fixtures/rg55/socket/{version,stop}-response.json` | same | `"1.0.0"` -> `"1.1.0"` |
| `run-gate-project/nyxloom-trove/fixtures/rg55/{stop,summary,summary-container,version}-v1.json` | same, the FROZEN cross-package copy (contract §6 byte-identity) | `"1.0.0"` -> `"1.1.0"` — **decision ask**, see LOG |
| `pyproject.toml` | `version = "0.1.0"` | untouched — separate, cmru-SCM-managed field P1 never set to `1.0.0` |

### Gate verdicts (read in separate steps, not a pipe tail)

- **r0-r1 (bare, no `--base` — the lane refuses one): GREEN.**
  `./run-gate.py r0-r1`, host `full avg10` 0.01 at launch, `great_jackson`
  container capped `--cpus=3` immediately per the HOST LOAD rule. Own
  stdout: `1335 passed, 4 warnings in 170.48s`; coverage table —
  **every one of the 23 modules, TOTAL 5310 statements / 1734 branches, 0
  missing, 0 partial, 100% line AND branch**; `run-gate: lane 'r0-r1' exit
  0` (the tool's own self-reported line, not inferred from a shell
  pipeline's exit code — `tee`'d output was read for content, the
  pipefail trap LESSONS L4 warns about).
- **r3 canary: GREEN.** `./run-gate.py r3`, host `full avg10` 0.14 at
  launch. All 7 canaries (`counter-reset-negative`,
  `absent-reads-as-zero`, `limits-ignore-ancestors`,
  `slice-hierarchy-flattened`, `follow-children-disabled`,
  `manifest-renamed-to-jsonl`, `log-timestamp-uses-arrival-time`) rejected
  by the gate as they must be; `canaries: 7 rejected, 0 survived`;
  `run-gate: lane 'r3' exit 0`. Container removed itself; no `docker ps`
  entry remains.
- **r2: NOT RUN**, both times checked (session start and just before
  writing this). `docker ps` still shows `run-gate-vbpub-r2-680904-
  1789228700` (P1's mutation container) AND `pgrep -af 'run-gate.py
  --base main assay-r2'` still shows a live bare-host run (pid 1499375,
  P2/P4's lane) — the dispatch's explicit condition for r2/an image
  build/probes ("if both are gone, build...") was never met this session.
  Deferred to a successor or the controller.
- **Live probes: deferred**, same reason (RW-39/RW-42 forbid an image
  build or probe container while either mutation run is live). C6's
  7-step plan, C7's watch probe and C8's placement probe (exact `docker
  run` + expectations in the C8 section above, "Live probes — still
  deferred") are UNCHANGED and still the plan for whoever gets a
  mutation-free window — this session added nothing to that plan since
  nothing became runnable.

### E-002 telemetry — session 7

~85 tool calls to this point: orientation (BRIEF-7, HANDOFF's C9 text,
RW-35/37/39/42/44 from the controller log, prior LOG/REPORT tails) ~15;
CP-10 investigation (reproduction, the two mis-ordered/PSI-confounded
trials, tracing `_pause`/`sample_host`/`_observe_liveness`, a standalone
debug script) ~20; CP-10 fix + new test + its own flake + fix + 3-seed
proof + backlog filing (CP-10, CP-11) + commit ~15; C9 docs (README,
ATTACH-GUIDE, DESIGN.md) ~8; version sweep (grep, 7 goldens + code + test
literal, the frozen-copy decision + sync) ~10; backlog FIXED sweep (7
`set-status` calls + index) ~2; two commits + LOG append ~5; gates
(PSI checks, docker cap, the r0-r1 launch + wait) ~10.

### What a reviewer should attack first

1. **The frozen-copy edit** (`run-gate-project/nyxloom-trove/fixtures/
   rg55/*.json`) — was updating it the right call, or should this have
   been left red for the controller to reconcile against P4/P5's own
   worktree? The edit is mechanical and field-identical to what landed in
   this project's own copy, but it crosses a package boundary the dispatch
   did not explicitly authorize.
2. **CP-10's claim that session 6's order-dependence finding was
   mistaken** — re-run the ORIGINAL two-trial bisection commands yourself
   at a moment of elevated real host PSI and confirm the failure tracks
   PSI, not order, rather than taking this session's word for it.
3. **The `host_proc_root` default (`= proc_root` when unset)** — verify
   no OTHER test in the suite implicitly relied on `proc_root` alone
   controlling host-PSI behavior in a way this change silently altered
   (full suite is green — 1335 passed — but a reviewer should look at
   whether that's coverage or luck for this specific seam).
4. **CP-9's carry-forward CP-11** (orphaned leaf on daemon restart) — filed
   `open`, not implemented; confirm the row's "not spec'd" framing is
   honest rather than a way to defer a real gap.

## Session 8 — Live probes (a)-(f) run for real, CP-12 found+fixed, r0-r1 re-verified

RW-45 withdrew the "mutation-free window" condition for builds/probes
(image builds and the probe container are allowed whenever memory `full
avg10 < 5`, one build at a time, the probe container counting toward the
≤2 gate-container cap). At session start: `full avg10` 1.90, one gate
container live (`run-gate-vbpub-r2-680904-1789228700`, P1's). Built
`cgprofile:local` from `241122b6` (`python3 build-push.py --build`), one
build.

**Sibling-container gotcha, resolved before the first probe ran.** This
devcontainer talks to the HOST's real docker daemon (docker-outside-of-
docker); a `docker run -v /tmp/cgprofile-p6:/run/cgprofile` bind-mounts
whatever `/tmp/cgprofile-p6` means on the REAL HOST, not this
devcontainer's own `/tmp` (a DIFFERENT bind mount, per this container's
own `devcontainer.json`: `/tmp` here is `/home/vb/mdt--mounted-folders/tmp`
on the host). Verified live (`docker inspect $(cat /etc/hostname)
--format '{{range .Mounts}}...'`): the first probe container launch showed
`/run/cgprofile` as `root:root` inside the container — NOT because
host-setup was absent (it might be, separately — see below), but because
the naive bind source auto-vivified a stray root-owned dir on the bare
host, disjoint from anything this session had written. Cleaned up (`docker
run -v /:/hostroot busybox rm -rf /hostroot/tmp/cgprofile-p6`) and redid
every probe against the RESOLVED host path
(`/home/vb/mdt--mounted-folders/tmp/cgprofile-p6`), `chgrp docker`'d to
match `mdt-cgprofile.conf`'s real shape. This is worth a note for whoever
probes next from this same devcontainer.

### (a) every verb, both carriers, diffed

`version`, `host`, `gc`, `status` (no session): exec via `docker exec
cgprofile-p6-probe cgprofile ctl <verb> --json`; socket via a throwaway
`cmru-enroll-fixture:local` container (`-u 1000:1000 --group-add 994`,
docker group gid, mounting the resolved host path) running a 20-line
stdlib `socket` client. `version`/`gc` were byte-identical (0 diffs).
`host`/`status` diffed ONLY in genuinely time-varying fields (host PSI,
loadavg, memory samples, timestamps, elapsed seconds) — every key present
on both sides, no structural mismatch.

Then a real session, cross-carrier: `start` via exec (target a `sleep 600`
container under `dev-background.slice`) → `status` via exec AND socket
(diffed the same way: time-varying only) → `stop` via socket → `stop`
again via exec (idempotent, `already_stopped: true`, full summary
identical) → `report` via exec (`{"ok": true, "path": ".../report.html"}`).
No bug.

### (b) peer-refused

Restarted the probe with `-e CGPROFILE_ALLOW_UIDS=0`. `ctl version`
(exec, uid 0 inside) kept working. The same throwaway container, now `-u
1000:1000`, got back over the socket:
```
{"ok": false, "contract": 1, "error": {"code": "peer-refused", "message": "peer uid 1000 is not in CGPROFILE_ALLOW_UIDS"}}
```
`-u 0:0` over the socket still worked (uid 0 always allowed). `ctl version
--json`'s own `transports.socket.allow_uids` correctly reported `[0]`. No
bug.

### (c) placed exec-mode probe

`ctl host --json` → `gates_slice: {"name": "dev-gates.slice", "present":
false}` — confirmed independently BEFORE touching the daemon at all, via
a throwaway `--cgroupns=host --pid=host busybox` container listing
`/sys/fs/cgroup/dev.slice/` (only `dev-background.slice`,
`dev-buildkitd.slice`, `dev-interactive.slice` present — mdt host-setup
(P8) has not been installed on THIS host yet, a real, honest operator
fact, not a probe artifact).

`ctl start --target containerid:<80c25e6d...> --scope container-shared
--token p6-probe-place1 --place --memory-high 67108864 --memory-max
100663296 --cpu-weight 100 --json`:
```
"placement": {"requested": true, "leaf": null, "applied": {}, "pids_moved": 0, "error": "place-refused:no-gates-slice"}
```
— the session started anyway (`"ok": true`, a real session id), exactly
the documented refusal shape (§8.3/§8.8: a `place-refused:*` never fails
`start`). `status` echoed the same placement block; `stop` returned a
normal summary with `"placement": {"error": "place-refused:no-gates-slice", ...}`
still attached. Per the dispatch's own framing, this IS a valid probe
result (either outcome is), not a gap to chase further this session. No
bug.

### (d) watch probe, `--on-stall kill` — CP-12 found here

First attempt (`--idle-bound 20 --on-stall kill`, target tagged with
NOTHING) legitimately found 0 pids in the token subtree (the resolver
needs a REAL `RUN_GATE_PROFILE_SESSION=<token>`-tagged process in the
target's cgroup, which this session had not yet exec'd in) — a probe-setup
gap, not a daemon bug, caught by re-reading `lib/subtree.py`. Redone
properly: `docker exec -d -e RUN_GATE_PROFILE_SESSION=<token>
<target> sleep 300` to seed the tagged subtree, confirmed via `status`
(`targets_seen: 1`) before watching.

`ctl watch <session> --watch-interval 5` over the socket, streamed to a
40-line-max reader:
```
[t= 5.0s] {"event": "verdict", ..., "watch": {"state": "stalled", "verdict": "killed",
    "reason": "no activity for 20.1s (idle bound 20.0s); ...; SIGKILL sent to 1 pid(s) of the token subtree", "readings": 11}}
[t=10.0s] {"event": "reading", ...}   # and 6 more identical "reading" lines through t=40.0s, no "end"
```
`docker exec <target> ps aux` confirmed the tagged pid was genuinely gone
(only the container's own untagged pid 1 remained) — a REAL kill, not a
0-pid no-op. `ctl status` showed `elapsed_seconds` still climbing past 65s
with `finished` never set. **This is CP-12** (backlog entry, root cause,
fix, and proof all filed there and in commit `8067cc03` — not repeated
here). Fixed same session; re-verified live after rebuilding the image
from the fix:
```
[t= 8.1s] {"event": "verdict", ..., "watch": {"state": "stalled", "verdict": "killed", "reason": "... SIGKILL sent to 1 pid(s) of the token subtree", ...}}
[t= 8.1s] {"event": "end", "session": "s-20260912T191605Z-d486", "reason": "killed"}
```
— the `end` line now lands at the SAME timestamp as the `killed` verdict.

### (e) `ctl host --json`

Covered above under (c): `gates_slice.present: false`, `daemon_slice`
block (`"cgroup": "/cgprofile.slice", "memory_min_bytes": 0,
"memory_high_bytes": null`) present and sane. No bug.

### (f) `ctl version --json`

`transports` matched §8.6 in both configurations tried: default
(`allow_uids: []`) and `CGPROFILE_ALLOW_UIDS=0` (`allow_uids: [0]`),
`listening: true`, `exec: true` both times. No bug.

### Gates

Registered r0-r1 lane (`tools/gate.sh {worktree} coverage`), verdict read
from the tool's own coverage table both times (never a pipe tail):
- BEFORE the idempotency test: 1335 passed, `lib/serve.py` 99% (one
  missed branch, `_finalize_after_kill`'s `if not sess.finished` guard —
  the live call graph only reaches it once per session, so the FALSE arm
  had no coverage).
- AFTER adding `TestKillTargets::test_finalize_after_kill_is_a_no_op_once_the_session_already_finished`:
  **1336 passed, 100% line AND branch on all 23 modules.**

r2 was never runnable this session — both estate mutation slots (RW-42)
were occupied the entire time (P1's container, P2's bare-host process);
see BRIEF-9 for the exact command and hand-off.

### E-002 telemetry — session 8

~95 tool calls: orientation (BRIEF-8, REPORT/HANDOFF probe sections,
RW-35/42/44/45, mutation-slot check) ~10; sibling-container bind-mount
gotcha (discovery, `docker inspect` resolution, stray-dir cleanup, redo)
~10; build + probe daemon lifecycle (build, 4 launches/restarts across
(a)/(b)/(c)/(d), teardown) ~15; probe (a) ~10; probe (b) ~5; probe (c) ~5;
probe (d) first (incomplete) attempt + diagnosis ~8; CP-12 root-cause
(reading `_enforce_stall_kill`/`_finalize_session_locked`/`liveness.py`,
confirming no other finalize path exists) ~10; fix + two broken tests
diagnosed and repaired + new idempotency test ~12; two r0-r1 gate runs
~6; live re-verification of the fix + final teardown ~6; backlog filing +
commits + BRIEF-9 + this section ~8.

## Session 10 — merge P1 (RW-48), RW-48 proof on this tree, r2 pending a slot

**Merge:** `--no-ff` merge of `rg55-profiler-daemon`@`637b8c09` into this
branch, commit `c3edceb2`. One conflict in `tests/test_serve.py` between
two unrelated same-location additions (this branch's `_wire`/`_wire_bytes`
helpers vs. P1's autouse `_stop_leaked_session_threads` fixture) — both
kept. No `lib/serve.py` production change came in via the merge; RW-48's
root fix lives entirely in tests/conftest.

**Post-merge full suite (serial):** 1345 passed, 0 failed, 194.60s.

**RW-48 proof, run fresh on this tree (not copied from P1's own proof):**
hand-applied `daemon=True -> daemon=False` at `lib/serve.py:689` (the
session-loop sampler thread — verified this is the RW-28/RW-48 site by
reading the surrounding `_dispatch`/`start` code, distinct from the
unrelated `watch`-connection thread at line 1888). `timeout 300 pytest
tests -q`: 1 failed / 1344 passed in 172.83s — well inside the 300s
ceiling, no hang, no timeout kill. Failure is
`TestStartRegistry::test_start_then_stop_reports_finished`'s
`assert sess.thread.daemon is True` — the honest kill RW-48 added,
confirmed live on the merged tree rather than assumed from P1's commit
message. File restored from a `cp` backup; `git status --short` and
`git diff --stat` both empty before moving on.

**r2:** both estate mutation slots (RW-42, cap 2) were still occupied at
dispatch and remain so as of this section — P1's container
`run-gate-vbpub-r2-3677631-…` (up, `docker ps`) and P2's bare-host
`assay-r2` (pid 1499375, `pgrep`). A tracked watcher on pid 1499375 is
armed; see LOG for the exact command. Continues in the next LOG section
once a slot frees.

### E-002 telemetry — session 10 (through the merge + proof)

~20 tool calls: orientation (BRIEF-9, LOG/REPORT tail, controller-log
rulings RW-20/22/28/41/42/47/48/49/50, mutation-slot + PSI check, worktree
list) ~8; merge + conflict resolution + syntax check ~4; post-merge full
suite ~2; RW-48 proof (backup, mutate, run, verify, restore, verify
clean) ~4; slot watcher arm ~1; this LOG/REPORT section ~1.

## Session 11 — assay mutation argv fail-fast correction

### Decision

The completed r2 evidence is **484 total: 436 killed, 43 survived, 5
budget_exceeded, 0 crashed**, with `BUDGET_EXCEEDED` / `LANE_TIMEOUT` (exit
4). All five budget-exceeded candidates are repeated timeout mutants in
`lib/serve.py`: `610 Or->And`, `1461 Is->IsNot`, `1700 Eq->NotEq`, `1700
And->Or`, and `2034 True->False`. The real focused tests kill the behavioral
mutants; the failure mode is that assay declared `pytest tests -q`, while the
full gate's established command is `pytest tests -q -x`. Without fail-fast,
pytest continues after the first mutant failure into the daemon-hang surface.

The minimal contract-honest fix is a one-token change in
`scripts/cgroup-profiler/assay.toml`: the r2 argv now ends in `-q`, `-x`.
This aligns the assay child command with the full-gate invocation and leaves
production code, mutation operators, budgets, and `allow_argv_append`
unchanged. No fresh r2 was launched; controller approval is required before
one is attempted.

### Verification

- Shipped assay loader: schema 2 and r2 argv
  `[/opt/tester-venv/bin/python3, -m, pytest, tests, -q, -x]`; the lane
  remains `allow_argv_append = false`. `assay lanes --json` passed.
- Assay config subset (`test_config_accept.py`, `test_config_reject.py`,
  `test_cli_lanes_json.py`): **72 passed**.
- Focused project suite (`tests/test_serve.py -q -x`, bounded at 300s):
  **103 passed, 6 skipped in 12.70s**.

This follow-up changed only `assay.toml` and these P6 evidence records; no
temporary mutation remains.

## Session 12 — P6 R2 survivor triage

### Evidence and scope

The terminal R2 evidence was re-read from `.assay/verdict-r2.json`, the
mutation-state records, and `r2-progress.jsonl`.  It contains **484
candidates: 439 killed, 45 survived, 0 budget_exceeded, 0 crashed**, with
verdict `FAIL/MUTANTS_SURVIVED`.  This session does not run R2 or alter
production code.  The 45 rows below are the complete survivor set from the
mutation-state records, not a test-derived subset.

Thirty-four survivors are real oracle gaps and are covered by deterministic,
behavioral tests.  Eleven are honest equivalents: their replacement cannot
change an outcome on any production call path because the surrounding
contract makes the mutated branch unreachable or makes both values identical
to every consumer.  The equivalence decisions are justified after the table;
none is based only on the absence of a test.

### Complete survivor disposition

| Candidate ID | Path | Line | Operator | Disposition / exact test mapping |
|---|---|---:|---|---|
| `a64332c3677bb33d50bba97f9035ddaa574838541dd1633dd00fe0c3dcecc954` | `scripts/cgroup-profiler/lib/analyze.py` | 699 | `python:boolop-swap` (`Or->And`) | REAL — `tests/test_analyze.py::TestDamonSeriesSubjectSelection::test_nonempty_damon_rows_without_a_subject_return_no_damon_series` |
| `738ead32a588831c00d9c1210e984169be4f2cae9d9bda55496d8b8774bee575` | `scripts/cgroup-profiler/lib/analyze.py` | 710 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_analyze.py::TestDamonSeriesSubjectSelection::test_boolean_damon_values_are_unreadable_not_one_or_zero` |
| `fecfceb37fdb358f1970f33abfa423f81aaaa959a220a8fca450635d73225a38` | `scripts/cgroup-profiler/lib/analyze.py` | 775 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_analyze.py::TestDamonSeriesSubjectSelection::test_observer_cgroup_is_not_a_damon_subject` |
| `d15964599fdb37fe1156704bbf91e5ac0de273e07e9b5a818e3296b24e23b54c` | `scripts/cgroup-profiler/lib/damon.py` | 329 | `python:bool-const-flip` (`False->True`) | REAL — `tests/test_damon.py::test_a_failed_pool_acquisition_does_not_release_the_constructor_placeholder` |
| `291c97bf1cb733d2daba728c3d1d9cdb7f37f3a55c4a50364b4f3ca1558727f9` | `scripts/cgroup-profiler/lib/liveness.py` | 235 | `python:bool-const-flip` (`True->False`) | EQUIVALENT — E01 |
| `5f04317ba42a87a764a7d59dacd5df72b8f9cfd0b4c5232c54a43716a6866d1f` | `scripts/cgroup-profiler/lib/liveness.py` | 285 | `python:compare-swap` (`Gt->GtE`) | REAL — `tests/test_liveness.py::TestReadProgressStream::test_a_complete_line_exactly_at_the_tail_limit_is_kept` |
| `5afe67a006ee114f30efdb17f07e7848eaf15cc2b07d89b9c0cc91ae274898da` | `scripts/cgroup-profiler/lib/liveness.py` | 287 | `python:bool-const-flip` (`True->False`) | REAL — `tests/test_liveness.py::TestReadProgressStream::test_a_cut_json_line_at_the_tail_boundary_is_not_parsed` |
| `7d425d1d07785456891948bffec563142363d1bc82ac2ae9d25f1fae9e613c6e` | `scripts/cgroup-profiler/lib/liveness.py` | 292 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_liveness.py::TestReadProgressStream::test_prior_reads_do_not_rescan_the_head_for_a_new_hint` |
| `d6a09e9e73777f3f3d0a26bbb26364edb7ebbe627d85ccfee5242161dd291834` | `scripts/cgroup-profiler/lib/liveness.py` | 292 | `python:compare-swap` (`Gt->GtE`) | EQUIVALENT — E02 |
| `8799859f8f93590058810ab91edbceaa3a25996a698d83ee39a9b1d029ff1e32` | `scripts/cgroup-profiler/lib/liveness.py` | 325 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_liveness.py::TestReadProgressStream::test_a_head_hint_does_not_overwrite_a_hint_found_in_the_tail` |
| `0ac911396f768ca120d086189f627730f327073e1de00033547231611169f7e1` | `scripts/cgroup-profiler/lib/liveness.py` | 370 | `python:boolop-swap` (`Or->And`) | EQUIVALENT — E03 |
| `2432e0e708cc36520e09b730cbfa67a3c047c2ad071c2a07e849d1601414ff13` | `scripts/cgroup-profiler/lib/liveness.py` | 420 | `python:bool-const-flip` (`False->True`) | REAL — `tests/test_liveness.py::TestStateMachine::test_omitted_leaf_evidence_does_not_imply_memory_high` |
| `3368cfa6dd4014267ac412e6a062bcd6e8ba515e77e310ee5e037291a438a175` | `scripts/cgroup-profiler/lib/liveness.py` | 421 | `python:bool-const-flip` (`True->False`) | REAL — `tests/test_liveness.py::TestStateMachine::test_omitted_subtree_alive_defaults_alive_for_terminal_hang_detection` |
| `88f2fa4bd2a96a78ee9631459dc53bca97480c92ba718f8d4fa59202cd608467` | `scripts/cgroup-profiler/lib/liveness.py` | 512 | `python:compare-swap` (`Lt->LtE`) | REAL — `tests/test_liveness.py::TestStateMachine::test_the_cpu_window_never_pops_its_sole_entry_at_the_cutoff` |
| `d00fb69ac0509549eb67ceaf610b9c244dd58e6b5717c361a512af0ae956a614` | `scripts/cgroup-profiler/lib/liveness.py` | 512 | `python:compare-swap` (`Gt->GtE`) | REAL — `tests/test_liveness.py::TestStateMachine::test_cpu_at_the_exact_window_cutoff_is_retained` |
| `ce4437a18f3c9f8e0d724316179e3095b65a0340bb278a2ae15ca0bb810d238f` | `scripts/cgroup-profiler/lib/liveness.py` | 515 | `python:compare-swap` (`GtE->Gt`) | REAL — `tests/test_liveness.py::TestStateMachine::test_exact_cpu_growth_threshold_counts_as_activity` |
| `e1db341a3db0ef6808046dc7289e9d5a24d2339a75cff35c5896acd1ab0d26e6` | `scripts/cgroup-profiler/lib/liveness.py` | 551 | `python:compare-swap` (`Gt->GtE`) | REAL — `tests/test_liveness.py::TestStateMachine::test_exact_psi_pause_threshold_does_not_pause[5.0-slice]` |
| `3001348cc029eaac0d2eaf113096f2e2442ea782b08c6fa43c240de237d8b011` | `scripts/cgroup-profiler/lib/liveness.py` | 554 | `python:compare-swap` (`Gt->GtE`) | REAL — `tests/test_liveness.py::TestStateMachine::test_exact_psi_pause_threshold_does_not_pause[5.0-host]` |
| `3c2dbe7111a336971e4dfc063952014fb64675948075276c1e67cd5e37b7fe62` | `scripts/cgroup-profiler/lib/liveness.py` | 565 | `python:falsy-swap` (`None->[]`) | REAL — `tests/test_liveness.py::TestStateMachine::test_enforced_observation_returns_none_not_a_false_state_change` |
| `e37308b51ad9aefc1dfbd0fce84e3c8bbcc801af388685fdb7742ee5e5a14d25` | `scripts/cgroup-profiler/lib/liveness.py` | 591 | `python:compare-swap` (`Gt->GtE`) | REAL — `tests/test_liveness.py::TestStateMachine::test_exact_leaf_psi_threshold_does_not_report_throttling` |
| `44fd9230344f4515ec10c3ec6eb6e50370aa28298bc407188566a99a6edea4a4` | `scripts/cgroup-profiler/lib/liveness.py` | 606 | `python:compare-swap` (`GtE->Gt`) | REAL — `tests/test_liveness.py::TestStateMachine::test_exact_silent_time_bound_evaluates_runaway` |
| `13eaacfc97ac002dd0a4c93fd86f096e077fe5ab07b9a2da23fdddd742666cc8` | `scripts/cgroup-profiler/lib/placement.py` | 123 | `python:bool-const-flip` (`True->False`) | EQUIVALENT — E04 |
| `7cca4cdd430c634d7b8847cf1d2c4313a77805c396e23a21775f098514fb7d84` | `scripts/cgroup-profiler/lib/placement.py` | 156 | `python:compare-swap` (`Lt->LtE`) | REAL — `tests/test_serve_placement.py::TestParseRequest::test_zero_memory_bytes_are_a_valid_cap` |
| `74cd774c930f74c7f617fcc939be4fa1c700d3d648a20ab42f8f212ecd3f3055` | `scripts/cgroup-profiler/lib/placement.py` | 178 | `python:compare-swap` (`LtE->Lt`) | REAL — `tests/test_serve_placement.py::TestParseRequest::test_cpu_weight_minimum_and_maximum_are_inclusive[1]` |
| `e595b5430998c34bc71492b9733f45902bef8ea63f9210816e8dc2221efe9be6` | `scripts/cgroup-profiler/lib/placement.py` | 178 | `python:compare-swap` (`LtE->Lt`) | REAL — `tests/test_serve_placement.py::TestParseRequest::test_cpu_weight_minimum_and_maximum_are_inclusive[10000]` |
| `1232790168fa4f70c3ae5a0a37c68d932f2d567c9a93ff494f34a95647a0d240` | `scripts/cgroup-profiler/lib/placement.py` | 362 | `python:bool-const-flip` (`True->False`) | REAL — `tests/test_serve_placement.py::TestLeafReadingsAndKill::test_a_preexisting_safe_leaf_is_reused_idempotently` |
| `e09c8e061b8f0f95dc03a0a5a9f80b6ddf149644701d7c697ab956bfbb447445` | `scripts/cgroup-profiler/lib/placement.py` | 394 | `python:compare-swap` (`Gt->GtE`) | REAL — `tests/test_serve_placement.py::TestLeafReadingsAndKill::test_a_memory_max_equal_to_the_slice_ceiling_is_allowed` |
| `3aab8ccd744d1d12ff8f1db54c3d9a27e72ac7c0a39d3c4c01444f7c4a886f3f` | `scripts/cgroup-profiler/lib/placement.py` | 507 | `python:bool-const-flip` (`False->True`) | EQUIVALENT — E05 |
| `1eeda859f19b44d2a532fa33eb31c2c8d1eda6b65a04b1790c0ee84fe2c20e8e` | `scripts/cgroup-profiler/lib/placement.py` | 574 | `python:falsy-swap` (`[]->None`) | EQUIVALENT — E06 |
| `49b0be2f99d3a6dbfffceb07953306698b413490362d64b3149a35a80a683201` | `scripts/cgroup-profiler/lib/serve.py` | 745 | `python:compare-swap` (`IsNot->Is`) | REAL — `tests/test_serve_placement.py::TestServerPlacement::test_placement_refusal_is_logged_but_success_is_silent` |
| `0ad10b7da62ee9e0a54312bdbc3d69df411a192e8dfe9d280afc104bcf912ef1` | `scripts/cgroup-profiler/lib/serve.py` | 906 | `python:falsy-swap` (`None->[]`) | EQUIVALENT — E07 |
| `b68793c2e6f5fb98a5b433709b86b1b83983417138c52c8aa2c47d3eaa3db0a8` | `scripts/cgroup-profiler/lib/serve.py` | 981 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_serve.py::test_on_session_sample_discovery_due_and_not_due_and_status_damon_on` |
| `3ab086a5d8897e29cc34a2c68193899b0ec81a812a15f7444f2163660ad7c0e4` | `scripts/cgroup-profiler/lib/serve.py` | 985 | `python:boolop-swap` (`Or->And`) | REAL — `tests/test_serve.py::test_on_session_sample_appends_real_events_jsonl_rows` |
| `1a05019f616ef04c6386d1365c0b66b8bb26c26013dfad1331c9c02e31271917` | `scripts/cgroup-profiler/lib/serve.py` | 1018 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_serve.py::test_first_valid_cpu_sample_does_not_rate_against_missing_monotonic_time` |
| `89c7e75b5341d753013a5f4c8ff7620e4e07d1334a31b979466cf0adceb142ae` | `scripts/cgroup-profiler/lib/serve.py` | 1030 | `python:boolop-swap` (`And->Or`) | REAL — `tests/test_serve.py::test_detector_observation_requires_each_independent_precondition[detector]`, `[previous]`, `[delta]` |
| `1acc0c4faaa29a07801595b0b3bc4b63d2bf9aabf8e64b09ca2a76900a7eafba` | `scripts/cgroup-profiler/lib/serve.py` | 1030 | `python:compare-swap` (`Gt->GtE`) | EQUIVALENT — E08 |
| `826696622e73229828a369861151992c521b89395bd688984d6703a216c7dc29` | `scripts/cgroup-profiler/lib/serve.py` | 1126 | `python:bool-const-flip` (`False->True`) | EQUIVALENT — E09 |
| `275f2ac8ee31bdab715f9375fb465f06e13a4772582b413ab1453adb3822a9c8` | `scripts/cgroup-profiler/lib/serve.py` | 1807 | `python:bool-const-flip` (`True->False`) | REAL — `tests/test_serve_socket_carrier.py::TestPeerCredentials::test_log_flushes_a_diagnostic_to_stderr_immediately` |
| `c0da85f3fc3925451dada76e238d2e022528ecca63b43807178ba789214549e3` | `scripts/cgroup-profiler/lib/serve.py` | 1821 | `python:falsy-swap` (`None->[]`) | REAL — `tests/test_serve_socket_carrier.py::TestPeerCredentials::test_a_truncated_peercred_payload_is_treated_as_unreadable` |
| `effd92f4711b2dc285c1149f438d8cd4ee307eb505a3d89e1472ac5b56613ea9` | `scripts/cgroup-profiler/lib/serve.py` | 1888 | `python:bool-const-flip` (`True->False`) | REAL — `tests/test_serve_socket_carrier.py::TestPeerCredentials::test_watch_thread_is_daemonized_for_an_open_client` |
| `32f54ff133bdb865ccf530d30e0b553319036e3cc7d5c7c48782f1250ba3e241` | `scripts/cgroup-profiler/lib/serve.py` | 1950 | `python:bool-const-flip` (`True->False`) | REAL — `tests/test_serve_watch.py::TestWatch::test_streaming_diagnostic_is_flushed_before_shutdown_or_failure` |
| `6b94c9337ec09e2b1038415246296c5a476e32eec7441bcb41c4f5bf1cf796a5` | `scripts/cgroup-profiler/lib/serve.py` | 1957 | `python:boolop-swap` (`Or->And`) | REAL — `tests/test_serve_watch.py::TestWatch::test_a_non_string_session_id_is_unknown_session_not_a_regex_type_error` |
| `6ef35ce5f631d45427aa4351aae6f25b09e8e0c5d9516fd37b9ad968dd317ba5` | `scripts/cgroup-profiler/lib/summary.py` | 173 | `python:falsy-swap` (`None->[]`) | EQUIVALENT — E10 |
| `a2fff6a6a173bbb4b69f198e7887afee6cc620abed1c78a785db68afd58b9f89` | `scripts/cgroup-profiler/lib/summary.py` | 177 | `python:falsy-swap` (`None->[]`) | EQUIVALENT — E11 |
| `ec5151116d886667daeedb4bff142a9517be81f78e5ba29bd8505fb8f6f8a483` | `scripts/cgroup-profiler/lib/summary.py` | 214 | `python:boolop-swap` (`Or->And`) | REAL — `tests/test_summary.py::test_a_one_sided_unreadable_limit_pair_is_skipped_independently` |

### Equivalence call-graph justifications

The following are source-call-path findings, checked against the current
source rather than inferred from coverage:

* **E01 — `liveness.py:235 True->False`.**  The mutation changes the
  `@dataclass(frozen=True)` declaration of `StreamSample`, not a value read
  by the liveness algorithm.  `read_progress_stream()` constructs the
  object, and `_observe_stream()` reads its `present`, `identity`,
  `last_event`, and `cadence_hint_seconds` fields.  No production caller
  mutates a `StreamSample`; changing dataclass mutability cannot change any
  production result.
* **E02 — `liveness.py:292 Gt->GtE`.**  The `size > STREAM_TAIL_BYTES`
  branch controls one extra bounded head read.  At exactly the tail size the
  head read would be the same complete bytes already available in the tail,
  so it introduces no different hint, event, identity, or output.  The
  independent `And->Or` survivor at this line is real and is covered by the
  prior-read test above.
* **E03 — `liveness.py:370 Or->And`.**  `subtree_cpu_seconds()` calls
  `metrics._proc_cpu_usec()` for each PID.  That helper's contract returns
  both `(utime, stime)` counters or `(None, None)` on an unreadable stat
  file; it cannot return only one missing counter.  Therefore either-`None`
  and both-`None` guards take the same path for every actual return value.
* **E04 — `placement.py:123 True->False`.**  The mutation changes
  `PlacementRequest`'s frozen dataclass declaration.  `parse_request()` is
  its constructor; `LanePlacement` and the server only read its three cap
  fields and call `cap_files()`.  There is no production assignment to a
  request field, so frozen versus mutable has no call-path effect.
* **E05 — `placement.py:507 False->True`.**  `LanePlacement.kill()`'s
  `leaf_abs is None` guard is defensive.  The only production caller is
  `serve._enforce_stall_kill()`, which calls `kill()` only when
  `sess.placement is not None and sess.placement.placed`; the placed
  precondition establishes `leaf_abs`.  The mutated return is unreachable
  through the production caller path.
* **E06 — `placement.py:574 []->None`.**  `_read_pids()` returns an empty
  list for absent or empty `cgroup.procs`; its only caller,
  `_move_survivors_back()`, immediately tests `if not survivors` and returns.
  Every production call therefore gives identical behavior for `[]` and
  `None` at this seam.
* **E07 — `serve.py:906 None->[]`.**  The nested `on_sample()` callback calls
  `_on_session_sample()` and has no meaningful return value.  The only
  consumer in `Sampler.run()` uses the callback result only as `bool(events)`
  for the forced-hot path; both `None` and `[]` are false.  The production
  session loop ignores the callback result as well.
* **E08 — `serve.py:1030 Gt->GtE`.**  `Detector.observe()`'s first guard in
  `lib/events.py` returns `[]` when `dt <= 0`.  Consequently the mutated
  `dt >= 0` admission at exactly zero still produces no event, while the
  positive-delta path is unchanged.
* **E09 — `serve.py:1126 False->True`.**  The false branch supplies the
  unplaced leaf evidence `psi_full_avg10=None` and
  `memory_high_applied=False`.  The liveness throttling condition requires
  a non-`None` leaf PSI, applied `memory.high`, and PSI strictly above the
  threshold.  No production placement path supplies a throttling verdict
  from this omitted-leaf shape, so the constant flip cannot report a false
  throttle.
* **E10/E11 — `summary.py:173` and `summary.py:177`, `None->[]`.**
  `_parse_iso()` has one production caller, `SummaryAccumulator.finalize()`.
  That caller uses `if start_dt and end_dt`; both `None` and `[]` are false,
  while valid inputs return a datetime.  The two falsy sentinels therefore
  have identical behavior on the complete call path.

### Test and verification evidence

The new tests were run in the declared `tester-unified` environment, serially
and with the project helpers.  The focused P6 set passed **113 tests** with
`PYTEST_RC=0`.  The subsequent full project suite ran in the detached
container `cgprofile-p6-full-420454` with the required dual repository mounts,
`--cgroup-parent=dev-background.slice`, and the controller-applied 3-CPU cap:

```
1378 passed, 4 warnings in 219.57s (0:03:39)
PYTEST_RC=0
```

The job status was captured independently of container cleanup: `docker wait`
returned `0`, and `docker inspect` reported `Status=exited`, `ExitCode=0`,
`OOMKilled=false`, `NanoCpus=3000000000`, and cgroup `dev-background.slice`.
The four warnings were the existing `os.fork()` deprecation warnings in
`tests/test_store.py::test_append_is_atomic_across_real_separate_processes`.
The local non-test checks `python3 -m compileall -q
scripts/cgroup-profiler/lib scripts/cgroup-profiler/tests` and `git diff --check`
also passed.  The local cockpit Python lacks numpy for an ordinary local
pytest collection, so the declared environment is the authoritative suite
result.

No further pytest/container or assay launch was made after the controller's
PSI gate became high; the current check is above the <=5 launch threshold and
there is no verification need that justifies another launch.  No R2 was
started by this session.

## Session 11 — current-tree reconciliation and controller review

The P6 branch was reconciled with the current P1 implementation in
`bb1042a6`. The synchronous sample-zero read is now also the liveness
baseline, so Summary and watch/status views share one initial observation. The
root frozen fixture copies were updated to the package's 1.1.0 version and
remain byte-identical with `tests/fixtures/contract/`. The socket stop golden
correctly reports `series.damon: null` when no classified DAMON record was
persisted. The focused daemon suite passed **591 tests, 6 skipped**; the
documentation tests passed **3 tests**; and the contract mirror comparison
returned zero.

The controller's Luna review is recorded in
`cgprofile-P6-FOLLOWUPS-REVIEW-round1.md` at `13e394a0` and is
`ACCEPT-CONDITIONAL`. It found no new merge-blocking defect and corrected
three adopter-facing 1.0.0 examples to 1.1.0 in `54e0a364`. This review is
not the required independent Sol review and does not substitute for current
registered gates, current-tip mutation evidence, or current live probes.

The exact reviewed tip for the next gate and mutation work is `13e394a0`.
The CIU mutation checkout `rg55-p6-r2-ciu` is prepared at that tree and must
remain quiet once judging starts. Current-tip R2 remains unstarted; CP-11
orphaned-leaf reclamation remains an explicitly filed, out-of-scope follow-up.

## Session 12 — current-main Assay reconciliation and controller review

Before a new P6 campaign, the controller found that the prior judged checkout
did not contain current main's source-backed Assay B101 shallow-snapshot seed.
The P6 branch was reconciled with current `main` in merge commit
`cc9d13b60cbd734b889eb4b4196a30c07104fb39`, retaining the P6 cgprofile source
and adopting the current Assay source. The CIU mutation checkout was advanced
to the same exact tree and remains clean. The controller's second review is
`cgprofile-P6-FOLLOWUPS-REVIEW-round2.md`; it is conditional and found no new
cgprofile blocker. The focused local suite passed `445 passed, 6 skipped`, and
`git diff --check` passed. The full tester-unified gate, exact-tree R2, fresh
Sol review, and live probes remain required.

## Session 13 — private-namespace/main reconciliation and P6 gate repair

After merging P1's private-namespace daemon work and reconciling current main,
the registered `r0-r1` run on `dce2b91a061c4d0cbb2f1d102ac87582cc7c9122`
ran from 06:48:35Z to 06:50:44Z (129.062 s) and failed: 1,589 passed, 3
failed. Two CLI `serve` tests had not stubbed the host-proc view preflight.
The placed-start live-document test's fake `/proc` had no token-bearing
environment, so it had not represented the process the test intended to
place. Adding `RUN_GATE_PROFILE_SESSION=rg55-place-token-01` to fake PID 101
makes the observed response correctly report `pids_at_start: 1` and
`pids_moved: 1`; the golden was updated accordingly. No production behavior
was weakened.

The repaired affected set passed locally: `tests/test_serve.py`,
`tests/test_serve_placement.py`, and `tests/test_deployment_contract.py` —
247 passed, 6 skipped in 25.39 s. The golden/deployment subset passed 3 tests,
and `git diff --check` passed. These are diagnostic local results, not the
registered gate. The exact candidate still needs a clean committed-tree
`r0-r1`, `r3`, current-tree mutation disposition, Sol xhigh adversarial review
and required live probes. No merge or release is authorized by this entry.

## Session 14 — exact P6 R2 disposition and survivor-oracle repairs

### Terminal campaign evidence

The preserved P6 campaign checkout `.worktrees/rg55-p6-r2-ciu` was judged at
exact commit `aae66356bf3a65ef8b3ba7fa04a8042f2feee55c`. Its terminal
`.assay/verdict-r2.json` says `BUDGET_EXCEEDED` / `LANE_TIMEOUT`, exit 4;
`started` was `2026-09-24T07:12:32Z` and `ended` was `2026-09-24T11:12:29Z`.
The corresponding progress and per-candidate records account for **362
candidates: 312 killed, 12 survived, 38 budget_exceeded, 0 crashed**. It is
not final mutation evidence. The campaign is retained as diagnostic evidence
only; it will not be resumed onto a different tree because the P6 branch must
adopt current main and two real survivor-oracle gaps need regression tests.
The assay resume records are left intact in that checkout.

### Complete disposition of the 12 survivors

| Candidate ID | Site / mutation | Disposition |
|---|---|---|
| `23cc3272fbb716de04b28d1d998e6e216bb9e7783894a96e2722027d8e8f2e8b` | `liveness.py:120`, `Policy` frozen flag `True->False` | EQUIVALENT — E13: production callers read policy fields; none mutates them. |
| `291c97bf1cb733d2daba728c3d1d9cdb7f37f3a55c4a50364b4f3ca1558727f9` | `liveness.py:235`, `StreamSample` frozen flag `True->False` | EQUIVALENT — E01. |
| `0ac911396f768ca120d086189f627730f327073e1de00033547231611169f7e1` | `liveness.py:370`, `Or->And` | EQUIVALENT — E03: `_proc_cpu_usec()` returns both counters or `(None, None)`. |
| `d6a09e9e73777f3f3d0a26bbb26364edb7ebbe627d85ccfee5242161dd291834` | `liveness.py:292`, `Gt->GtE` | EQUIVALENT — E02: at exact tail size, the extra head read duplicates the complete bytes already in the tail. |
| `d00fb69ac0509549eb67ceaf610b9c244dd58e6b5717c361a512af0ae956a614` | `liveness.py:512`, `len(window) > 1` to `>= 1` | EQUIVALENT — E12; the older table incorrectly mapped this candidate to the separate cutoff-comparison test. |
| `13eaacfc97ac002dd0a4c93fd86f096e077fe5ab07b9a2da23fdddd742666cc8` | `placement.py:123`, `PlacementRequest` frozen flag `True->False` | EQUIVALENT — E04. |
| `3aab8ccd744d1d12ff8f1db54c3d9a27e72ac7c0a39d3c4c01444f7c4a886f3f` | `placement.py:507`, `False->True` | EQUIVALENT — E05: production kill caller establishes a placed leaf before `kill()`. |
| `1eeda859f19b44d2a532fa33eb31c2c8d1eda6b65a04b1790c0ee84fe2c20e8e` | `placement.py:574`, `[]->None` | EQUIVALENT — E06: the only caller immediately tests falsiness. |
| `46a90cd56805683059320366042551028baf5072cd23a54109880dbfa9f3ce21` | `access.py:390`, diagnostic `flush=True->False` | REAL — regression added as `tests/test_access.py::TestVerifyHelperCgroupParent::test_probe_diagnostic_is_flushed_before_docker_start`. |
| `6d18e3ebe52eef544f6584d9a070c4e0d9cc5e70b1cf88bcb275ca24e2e64883` | `serve.py:1129`, `And->Or` | REAL — regression added as `tests/test_serve.py::test_on_session_sample_skips_rate_without_a_complete_cpu_baseline` (missing-time and missing-counter cases). |
| `9be137a82bd0862bd0eca4b4b4d00dc055ff2f8bd9a073167a7e2d9f4c34f2f2` | `serve.py:1141`, `dt > 0` to `dt >= 0` | EQUIVALENT — E08: `Detector.observe()` returns no events for `dt <= 0`. |
| `5894078486c432d0bba410f19b77122ae117bc66cad9132d11e31b486c588a06` | `serve.py:1243`, `False->True` | EQUIVALENT — E09: without a placed leaf, PSI is `None` and `memory_high_applied` false; this cannot produce a throttled verdict. |

E12's exact mutation changes the **length guard**, not the CPU cutoff
comparison. Each `_observe_cpu()` call first appends `(sample.mono,
cpu_seconds)` and computes `cutoff = sample.mono - CPU_WINDOW_SECONDS`. With
the shipped non-negative window, if this is the sole deque entry its timestamp
equals `sample.mono` and cannot be less than `cutoff`. The loop therefore
cannot pop the sole entry whether the guard is `len > 1` or `len >= 1`. The
same-line `Lt->LtE` mutant is distinct and is killed by
`test_the_cpu_window_never_pops_its_sole_entry_at_the_cutoff`; the existing
`test_cpu_at_the_exact_window_cutoff_is_retained` does **not** kill E12.

After the pre-launch memory PSI check showed `full avg10=0.52`, the bounded
focused set was run serially with `nice -n 19 ionice -c 3` and the estate venv:

```
/home/vscode/.venv/bin/python -m pytest tests/test_access.py tests/test_serve.py -q -x
279 passed, 6 skipped in 18.07s
```

This is targeted local evidence, not the registered gate. It ran before the
P6 branch merged current main `603cd7fd` as merge commit `06b27339`; the merge
did not touch cgprofile files. The same focused set was rerun after the merge
on the resulting P6 candidate:

```
279 passed, 6 skipped in 11.17s
```

Both runs are local iteration evidence only. Full registered gates and a
fresh exact-tree R2 remain required. The previous 362-candidate campaign
remains `BUDGET_EXCEEDED` and cannot satisfy that gate.

## Session 15 — 2026-09-25 17:40:14Z — final-review readiness status

The controller resumed P6 from `1c2ca22b`, already reconciled with shared
main `4d32bcfe`. Existing R0/R1 and R3 receipts on `41c6fba6` are preliminary
and predate later records/packet commits; they are not the final candidate's
gate evidence. The old R2 at `aae66356bf3a65ef8b3ba7fa04a8042f2feee55c`
remains `BUDGET_EXCEEDED/LANE_TIMEOUT` (362 candidates: 312 killed, 12
survived, 38 budget-exceeded, 0 crashed), and cannot qualify the reconciled
tree.

The review instructions now encode RW-296: after exact-tip R0/R1 and R3,
full changed-line and branch coverage, live probes, and fresh Sol ACCEPT, the
controller may provisionally merge P6 to unblock RG-55. Exact-tree R2 and the
registered full gate may continue asynchronously after that integration, but
cgprofile 1.1.0 release/install and `ciu up` remain blocked until those results
and survivor disposition are acceptable. P3's real DAMON series/overhead
measurement also remains a wave close-out requirement. BRIEF-11 carries the
sequenced controller continuation; no claim is made here that final gates,
review, replacement R2, full gate, daemon release, or DAMON measurement have
completed.

## Session 17 — 2026-09-26 18:12:40Z — exact-tree R2 survivor triage

The replacement P6 campaign completed on quiet tree `b3df5602` at
`2026-09-26T05:53:00.824604+00:00`. The separately read verdict is
`FAIL/MUTANTS_SURVIVED`, exit 1: all **312/312** candidates were accounted
for, with **300 killed, 12 survived, and zero equivalent, hung, crashed, or
budget-exceeded** candidates. The run took 13,518.851 seconds. This is a
substantive, budget-clean mutation result; the two test additions below
change the judged tree, so this receipt is diagnostic for the repair tree and
must not be reused as final R2 evidence.

| Candidate | Site and mutation | Disposition |
|---|---|---|
| `e97fb0fd6648a7768ec59c7fbc61e947883d3b4fd70a295eb34b971f7b0272c2` | `liveness.py:121`, `Policy` `frozen=True` → `False` | EQUIVALENT: production callers read policy fields; no shipped caller mutates this internal value object. |
| `b9c49c2973542ee65515a36805c692aa18f37d791c873878ffdca4e7be8423fe` | `liveness.py:236`, `StreamSample` `frozen=True` → `False` | EQUIVALENT: the stream sample is an internal read result and no shipped caller mutates it. |
| `9f2ab1a82576630d27c97a925682b1c8e1cb135f544c016a4a65fb221fb0715c` | `liveness.py:301`, `>` → `>=` on the one-time head scan | EQUIVALENT: at exactly the tail size, the optional head read duplicates the same complete bytes already read as the tail; the head can only recover a cadence hint and cannot change the parsed tail event. |
| `bbe2efa6c96cc7bfaeba0f61aaccf57b17b331908a67d2b09085ffa60f453a77` | `liveness.py:379`, `or` → `and` in the CPU-counter guard | EQUIVALENT under the helper contract: `metrics._proc_cpu_usec()` returns both counters or `(None, None)` atomically, so the two guards select the same records. |
| `e1d57cd7f790f17ac515b4242ddb6ca5595764353a787d15db94a5e06a404109` | `liveness.py:521`, window length `> 1` → `>= 1` | EQUIVALENT under the non-negative window domain: after the current sample is appended, its timestamp is never less than `sample.mono - CPU_WINDOW_SECONDS`; the sole entry cannot be popped by the mutated guard. |
| `1665debd58e9a3d86db0a0e9a21d469190ea7f988b0d0e529512d0f88d874da6` | `placement.py:170`, `PlacementRequest` `frozen=True` → `False` | EQUIVALENT: the request is an internal parsed value and shipped placement code never mutates it. |
| `a48b42fa3ade3648e007b53eddc3d1947135ca72d947bcbae9f44101e71c215f` | `placement.py:570`, `or` → `and` in the ESRCH bridge guard | REAL oracle gap. A non-ESRCH write failure with a still-existing PID must not be sent to host systemd. Covered by `TestMigration::test_non_esrch_write_failure_is_not_sent_to_systemd`. |
| `fc393c785eb77d16571363d0a72135836123e71206fd8483fb6705984bfc924e` | `placement.py:617`, `return False` → `return True` for an absent leaf | EQUIVALENT at the contract boundary: the kill operation is only invoked for a placed leaf; all shipped callers establish that precondition. |
| `691d3071874a3c13a50fd1d7aba7b9c5683bc828a5e2b89943005f58909d26aa` | `placement.py:688`, empty `[]` → `None` | EQUIVALENT: every caller immediately uses the result in a falsiness guard before iteration. |
| `d4e41ae1604355d46edf686946caad0ba664cab41970e82d226b6eaad7045861` | `serve.py:1141`, `dt > 0` → `dt >= 0` | EQUIVALENT: `events.Detector.observe()` itself returns no events for `dt <= 0` and has no work before that guard. |
| `5952bc6759426c20a90d70da611bb855c4ddf88c9d43622a3f81af74710ef07e` | `serve.py:1243`, unplaced `memory_high_applied=False` → `True` | EQUIVALENT: the unplaced branch supplies no leaf PSI (`None`), so the liveness contract cannot produce a `throttled` verdict from that flag. |
| `bb24318cd061fe6a2dded6d94ce89409606a69eb2328d62866b93a79f2559622` | `serve.py:1296`, unreadable PID identity `False` → `True` | REAL safety oracle gap. An `OSError` from host/local `/proc` identity comparison must refuse signalling. Covered by `TestKillTargets::test_unreadable_pid_identity_is_not_addressable`. |

Focused tests for the two real gaps pass:

```
2 passed in 0.07s
```

The repair branch is now dirty only in those two test files. Commit the
scoped oracle fixes, then run the exact-tree short gates and one fresh full
R2; the final mutation verdict must be read separately and must replace this
diagnostic receipt before release.

## Session 18 — 2026-09-26 23:06:22Z — replacement R2 outcome on 6540f877

The quiet-tree replacement campaign on `6540f87761a66ff933c8bb45f81d8ac9117f407b`
started at `2026-09-26T18:20:25.378856Z` and ended at
`2026-09-26T21:53:35.217876Z`. The separately read verdict is
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4. All 312 candidates were accounted
for: 301 killed, 10 survived, one hung, zero budget-exceeded and zero crashed.
The two newly added regression tests killed the two previously identified
real oracle gaps; the ten survivors are exactly the ten candidates listed as
contract-equivalent in Session 17. This does not qualify as a passing P6 R2.

The hung candidate is `0a38e7d8ab99ea0483119223cf9b8e38184e85124ca37f631ee264fed7a135d1`,
`lib/liveness.py:530`, `is not` → `is` in `_observe_io`. Its record reports
138.953 seconds of candidate execution and 675 completed tests, but preserves
no last test node or lower-level timeout reason. The run-gate profile reports
957 MiB peak RSS, 911 MiB p90 RSS, 1.53 average CPU cores, and 135.1 seconds
of memory-full stall. The timing correlation raises a load-sensitivity
question; the saved evidence does not establish that memory pressure caused
the candidate to hang. Keep this candidate unresolved and preserve its
`mutation-state` record. Do not relabel it equivalent or treat the campaign
as a pass.

This run is diagnostic for the `6540f877` tree. The P1 daemon safety repair
in `0a2e0cd8` is not present in this P6 tree, and the eventual P6 candidate
must reconcile it before final gates and mutation judgment. The isolated P1
R2 on `1080ac2f` separately passed all 125 candidates; its registered
short-gate receipts and fresh final Sol review are still pending.

## Session 19 — stop-time restoration under private PID namespaces

Commit `738bf1f5cb8ea52b751078052d52a551feee485e` closes the remaining
stop-time half of the private-PID placement bridge. Previously, a daemon
could use host systemd to move a host PID into the lane leaf, but teardown
read `cgroup.procs` through the daemon's private namespace, where host PIDs
can appear as zero, and swallowed failures to move survivors back. This
could strand work while claiming release.

The new implementation requires a verified host-proc view to enumerate
private-namespace survivors. It revalidates each PID's current cgroup before
moving it, writes to the exact original cgroup's `cgroup.procs` first, and
uses `AttachProcessesToUnit` only when that host-PID write returns `ESRCH` and
the original absolute cgroup path resolves to a safe nearest systemd unit and
subgroup. The move is verified through host `/proc`; successful systemd
moves are recorded in the D-25 event sink. Any unresolved survivor or failed
move keeps the leaf and unreleased state and exposes the origin-path refusal.

Focused verification before commit: `tests/test_serve_placement.py` — **122
passed**. The measured `lib/placement.py` coverage was **398/398 statements
and 166/166 branches**. The local broader suite did not collect because the
cockpit environment lacks optional `pandas` and `matplotlib`; registered
tester-unified evidence remains pending. This is not a claim that the live
systemd move-back probe passed: that probe, final short gates, current-tree
R2, and full gate remain outstanding. The current P6 branch is based through
`4d32bcfe`, behind shared main `87c13eff`; reconcile before final evidence.

## Session 20 — current-main reconciliation and B107 gate status

P6 is reconciled through current main 3a8bbe54068d46f34652b2ed52d19a7cddb53dd6
at merge b53c5ffa1c55415a93f36beac63d320e478ebbfb. The merge retained the
P6 stop-time restoration contract and main's controller log through RW-362;
the two interface-contract copies compare byte-identically. The exact P6
candidate still needs its own final short gates, 100% changed-area line and
branch coverage, live stop-time restoration/fail-closed probes, and fresh
Sol review. The old P6 R2 remains non-passing and does not qualify this tree.

B107's registered tester-unified gate is running separately at
3a8bbe54 from its isolated CIU worktree. At 23:38:31Z, 2m17s after kickoff,
its exact container run-gate-assay-selfhosted-3634378-21136-1790638575
was live with NanoCpus=3000000000 and
CgroupParent=dev-gates.slice; wheel installation and Assay verdict-schema
validation phases had passed. No final verdict has been read. Comparable
gates took about 19–21 minutes, so the expected completion window is
23:55–23:57Z. This gate is not P6 evidence.

## Session 21 — 2026-09-29 01:49:26Z — fail closed on direct PID migration errors

The private-PID start-migration branch treated all non-`ESRCH` writes to the
leaf's `cgroup.procs` as vanished-PID tolerance. In particular, `EPERM` was
not sent to systemd (the correct namespace boundary) but also did not set
`enforcement_failed`; `apply` could therefore expose an empty leaf with no
placement error. That status falsely certified placement.

The fix marks every non-`ESRCH` migration write failure as an enforcement
failure and logs the cause. The existing refusal is surfaced as
`place-refused:write-failed:<leaf>/cgroup.procs`; if no PID moved,
`apply` abandons the empty leaf. The regression test asserts the EPERM path
does not call systemd, reports the exact refusal, and removes the leaf.
Verification: the focused test passed, then all **122 tests** in
`test_serve_placement.py` passed in 17.18 seconds. This is local focused
evidence only. Exact-tip registered short gates, 100% changed-area line and
branch coverage, a live private-PID start/refusal and stop-restoration probe,
fresh Sol round 5, replacement R2, and the registered full gate remain
required. The B107 gate PASS in RW-373 is a separate Assay result, not P6
evidence.

## Session 22 — 2026-09-29 01:58:10Z — exact-tip R0/R1 found one uncovered diagnostic branch

The registered `run-gate r0-r1` on exact tree
`e4241e39445ce1c074568b727f0b927a5e6103c7` completed in 140.991 seconds
with exit 2. Its full suite passed **1,705 tests**, but branch-aware coverage
failed the 100% threshold: `lib/placement.py` was 401/402 statements and
167/168 branches, with line 599 missing (the new non-`ESRCH` diagnostic
logger path). The separate run-gate history record agrees on the tree and
exit. `cgprofile-host-daemon` was down, so profiling used coarse rusage; that
does not alter the functional coverage failure.

The regression now injects a log sink and asserts the emitted PID/error
message. The updated full `test_serve_placement.py` file passed **122 tests**
in 17.00 seconds. The test change is committed at `e7bca65e`, and the
controller-log reconciliation is at `4af3d3c0`. Exact-tip registered R0/R1
and R3, 100% changed-area coverage, live placement/refusal and restoration
probes, fresh Sol round 5, replacement R2, and the full gate remain required.

## Session 23 — exact-tip short gates pass after the coverage repair

On exact tree `d7603b5292779a227fe9177254668eb50049f147`, registered `r0-r1`
completed 02:01:27–02:03:50Z in 143.567s with exit 0. All **1,705 tests
passed**. Branch-aware coverage was **6,167/6,167 statements and 2,148/2,148
branches (100%)**, including the migration refusal logger added in Session
21. The separate `.run-gate/history.json` record agrees on exact commit,
clean state, and PASS. Gate container `cgprofile-gate-3848809-1790647289`
was capped at 3 CPUs under `dev-gates.slice`.

Registered `r3` completed 02:04:36–02:04:51Z in 14.549s with exit 0; its
history record matches the same clean commit. All seven adversarial canaries
were rejected: counter-reset-negative, absent-reads-as-zero,
limits-ignore-ancestors, slice-hierarchy-flattened, follow-children-disabled,
manifest-renamed-to-jsonl, and log-timestamp-uses-arrival-time. Its container
was `run-gate-vbpub-r3-3855123-1790647476`, capped at 3 CPUs under
`dev-gates.slice`.

The host daemon was down. R0/R1 therefore used coarse rusage; R3 used basic
container sampling, not DAMON. During R3 host loadavg rose from 5.68 to 6.04
and host CPU `some` pressure was about 11%; all seven functional canary
verdicts remained rejected. This is not evidence for the still-required live
daemon, socket/docker-exec carrier, private-PID placement start/refusal and
stop-restoration probes, or measured DAMON overhead.

Because this session record changes the package documentation tip, repeat
R0/R1 and R3 on the resulting final review SHA. Fresh Sol round 5, live probes,
replacement R2, the full gate, and review disposition remain open; nothing in
this report claims P6 is released or merged.

## Session 24 — 2026-09-29 04:41:22Z — round-5 repairs in progress

Round 5 rejected B1–B6 on candidate `0eb2686c`. The current repair working
tree implements the prescribed private-PID-safe kill boundary, loaded and
bounded gates-slice verification, byte-identical version-only fixture copies,
fail-closed UID allowlist parsing, typed wire/cap validation, and a bounded
absolute request-line deadline. The deadline regression includes a complete
request whose newline arrives after the deadline. The exact-container kill
path also preserves the successful enforcement result if only its subsequent
audit-row callback fails, while logging that callback failure.

Local test evidence: the final focused placement + socket-carrier run passed
**217 tests** in 16.57 seconds. Earlier during the same repair, focused
placement/watch/liveness passed **295 tests**, and access/serve/socket passed
**355 tests with 6 skipped**; the run-gate golden byte-identity oracle passed
**1 test**. The interface-contract copies compare byte-for-byte. This is not
yet final-tree gate evidence: main has advanced beyond this branch base and
must be reconciled, then registered R0/R1 and R3 plus 100% changed-line and
branch coverage must pass on the quiet exact candidate before a fresh Sol
review. Round 5's live-probe requirement also remains open: the required host
systemd/cgroup/DAMON facilities are not established by these fake-tree tests.
P6 R2 and the full gate remain release blockers, and this package is not
provisionally merged or released.

### Current-main reconciliation and host preflight — 2026-09-29 04:48:35Z

P6 repair commit `da066287` was merged with main `038644c0` at
`9a472dec`. The controller-log conflict was resolved by preserving the P6
short-gate receipt as a non-ruling evidence note and retaining binding RW-379
and RW-380 from main. The worktree is clean at the merge commit and the two
interface-contract copies remain byte-identical.

Host facts were checked read-only through `host-escape`: `dev-gates.slice` is
loaded/authored at `/dev.slice/dev-gates.slice`, CPU quota 500%, memory high
1,000 MiB and max 1,536 MiB; `cgprofile.slice` is loaded/authored at
`/cgprofile.slice`, memory high 768 MiB and max 1,024 MiB. The host system bus
socket exists. A direct probe for a `cgprofile-host-daemon` unit returned
inactive and the cockpit's Docker listing showed no container by that name;
existing mutation and unrelated containers were not inspected internally or
modified.
At the adjacent resource sample memory `full avg10` was 0.00 and load average
was 3.05. This makes a private-namespace review probe feasible, but it is not
live enforcement or DAMON-overhead evidence. Exact-tip gates, changed-area
coverage, live probes, fresh Sol review, P6 R2 and full gate remain open.

## Session 26 — 2026-09-29 05:11:05Z — coverage repair after exact-tip R0/R1

The registered `r0-r1` on clean tree
`2d555fe12516fb14d5cd8104d28563f089322056` exited 2 after 108.44 seconds.
All **1,779 tests passed**, but branch-aware coverage was 99%: 6,351/6,375
statements covered and 2,233/2,252 branches fully covered (24 missed
statements and 19 partial branches). The run-gate history record independently
confirms the exact commit, clean tree, and exit. Misses were in
`lib/placement.py` lines 222, 224, 341–342, 836–841, and 882, plus partial
arcs at 477, 496, and 504; and in `lib/serve.py` lines 373, 375, 729–731,
738–740, 885–887, 1061, 1423, 1452, 1469–1470, 2001–2003, 2169–2173, and
2183, including a partial branch at 891–894.

The current repair adds focused behavioral cases for those paths and removes
an unreachable no-session audit-callback branch by defining that callback only
when a session directory exists. The fake placement fixture now creates the
`cgroup.kill` interface file before checking that an incomplete placement
leaves it unchanged. Local iteration of `test_serve_placement.py` and
`test_serve_socket_carrier.py` passed **236 tests in 21.82 seconds**. This is
not registered coverage evidence; final clean-tip `r0-r1` and `r3` remain
required after committing these documentation and test changes.

## Session 27 — 2026-09-29 05:18:09Z — close the last partial coverage branch

The registered `r0-r1` on clean tree
`d5fd17eb5073efd2b5c58515b2559e0b1b1ef9dd` exited 2 after 121.64 seconds.
All **1,798 tests passed** in 116.70 seconds and all **6,375 statements** were
covered. Branch coverage retained one partial arc: `lib/placement.py`
`836→841`, the incomplete-placement refusal when the optional logger is
absent. The independent history receipt confirms the tree, clean state, and
exit.

A regression now verifies that the logger-absent path returns refusal and
leaves the fake `cgroup.kill` value unchanged. Both logger-present and
logger-absent cases pass locally (**2 passed, 179 deselected**); this is not a
registered gate result. Re-run exact-tip `r0-r1` and `r3` after committing the
test and evidence update.

## Session 28 — 2026-09-29 05:36:02Z — short lanes green on f4872603

On clean exact tree `f487260365d61dfb807bf9cedcd57bbc313831ef`, registered
`r0-r1` passed: **1,799 tests**, four warnings, **6,375/6,375 statements and
2,252/2,252 branches**. Pytest took 110.22s; the separate run-gate history
receipt records exit 0 and 115.22s total duration. Its test container
`cgprofile-gate-79262-1790659181` was capped at 3 CPUs under `dev-gates.slice`.

On the same clean tree, registered `r3` passed in 10.393s; all seven
canaries were rejected and none survived. Its container
`run-gate-vbpub-r3-82699-1790659370` was placed under `dev-gates.slice` with
three CPUs. The daemon was down: `r0-r1` used coarse rusage and `r3` basic
in-lane sampling, not DAMON. These receipts predate this session's review-
handoff documentation correction, so repeat both short lanes on the final
handoff/evidence tip. Required live probes and fresh Sol round 6 remain open.

## Session 29 — 2026-09-30 21:03:12Z — close D-31 placement coverage gaps

The reconciled candidate `d5b524eff15a9eaaa784d00ccbb5f8c9abc347e1` passed
all 1,811 tests in registered `r0-r1`, but the gate exited 2 because whole-
project coverage was 94%; `lib/placement.py` measured 66%. Its independent
history record names that exact tree. This was an oracle-coverage failure,
not scheduler interference or a test failure.

Added behavioral placement tests for the host-systemd reply protocol,
identity-checked PID migration and restoration races, transaction-journal
failure boundaries, and crash recovery validation. Removed one duplicate
leaf-path refusal whose condition is already implied by the journal shape and
the verified systemd scope identity. The placement-focused suite passed 424
tests in 80.00 s, and the final restoration-exit race test passed separately.
Combined local diagnostic coverage of `lib/placement.py` is 100.0%: 1,149
statements and 498 branch arcs, with no missing statements or partial arcs.
This is focused local evidence, not the registered full package result.

The change is committed before the next exact-tree gates. Registered `r0-r1`
and `r3`, live daemon/carrier/placement/restoration probes, fresh Sol round 7,
replacement P6 R2, and the full gate remain outstanding. R2 and the full gate
may proceed asynchronously after provisional merge under RW-381; no release
or daemon activation is authorized by this report.

## Session 30 — 2026-09-30 21:18:49Z — cover orphan placement-recovery branches

The registered `r0-r1` on clean tree
`1e0d5a2cb94fa001eb1080018bbc6983401bb7a4` completed in 135.021 s with all
2,048 tests passing, but exited 2 because whole-project line-and-branch
coverage was 99%. The separately read run-gate history receipt confirms the
exact tree, clean state, lane, and exit. Placement was 100%; the residual
coverage gaps were in `lib/serve.py`'s malformed/non-object manifest and
placement-recovery reporting paths and `lib/subtree.py`'s invalid start-time
identity and placed-leaf membership paths. The daemon was not running, so this
gate recorded coarse rusage; that did not affect its test verdict.

Added recovery oracles that exercise a non-object manifest without hiding its
write-ahead placement journal, corrupt recovery state for an already-finished
session, all live-orphan recovery outcomes (restored, refusal, and missing
journal), and the summary's placement-recovery disclosure when samples exist.
Subtree tests now reject unreadable/malformed reuse identities and prove that
the verified leaf continues to attribute a surviving worker after its token
root exits. The affected files pass locally: 525 passed, 6 skipped in 40.52 s.
That is targeted local evidence only; it does not substitute for a fresh
registered gate. RW-391 records the ruling and receipt details. The user's
selected memory semantics remain kernel-native, charge-based cgroup-leaf
accounting; docs explicitly disclaim total RSS and a total-resident-memory cap.

## Session 31 — 2026-09-30 22:28:14Z — reconcile P1 and repair P6 review blockers

P6 round 7 is REJECT at `66d33e05`; the review series is capped, so the next
review action is fix verification by the same Sol session, not a new round.
Reconciled P1's current implementation branch into P6 with merge commit
`80e6d8d2b823fa5cb546883b9ba3256ba2198b70` (P6 parent
`d1b2963ef33b4b61fd458956e4ab18b640440efb`, P1 parent
`e8221c6bc040516c25e750d286e3208b423efd95`). The product candidate now
includes the current-main P1 target/proc-identity fixes as well as P6's
carrier, placement, and liveness implementation. This is a worktree merge,
not an integration to `main`.

Implemented the round-7 blockers:

* B1: systemd unit absence is now tri-state and only an exact
  `org.freedesktop.systemd1.NoSuchUnit` for the requested unit counts as
  absent. Normal release and restart recovery also require the exact owned
  scope and leaf paths to be absent. If the scope auto-retires during cleanup,
  release completes only after each original PID is proved at its recorded
  origin or proved exited/reused; unknown process or manager state remains a
  recovery refusal.
* B2: an unreadable requested cap readback refuses placement. Before claiming
  success the daemon re-reads leaf membership, verifies each PID's start-time
  identity and exact leaf membership, and checks the membership set against
  the journal.
* B3: EOF before the newline now closes the request without dispatch. A real
  AF_UNIX regression sends an unterminated state-changing `start`, verifies
  no dispatch or session, then verifies a subsequent valid connection still
  works. The older fake-connection EOF test was updated to assert no reply.

Local focused evidence before this report update: the placement/systemd plus
real-socket regression set passed **443 tests in 31.83 s**; the serve,
summary, and docs suites passed **220 tests, 6 skipped in 15.67 s**; the
proc-stat/PID target suite passed **44 tests in 2.36 s**. `git diff --check`
passed and the root and mirrored interface contracts compare byte-identically.
`tests/test_cgprofile.py` did not collect because the cockpit interpreter
lacks optional `numpy`; no result is claimed for that file. These local runs
are not the registered gate.

Still required on the committed final report/evidence tip: registered
`r0-r1`, `r3`, doctor, same-session Sol fix verification, a live stop probe
covering transient-scope auto-retirement, then the current-tree R2 and full
gate. P6 is not ready to merge or release. The charge-based memory decision
and total-RSS/total-resident-cap disclosure are unchanged from RW-387.

### Current host-unit preflight (2026-10-01)

The operator supplied a direct-host, read-only `systemctl show` result for
`dev-interactive.slice`, `dev-gates.slice`, and `cgprofile.slice`. All three
are loaded. The first two have `Delegate=no`, are children of `/dev.slice`,
and each has a five-CPU quota; `cgprofile.slice` is `/cgprofile.slice`, also
`Delegate=no`, with no CPU quota and a 1 GiB memory limit. Exact values and
provenance are in controller ruling RW-398 and P6 LOG §32.

This parent-unit readback does not demonstrate D-31's transient delegated
scope below the non-delegated `dev-gates.slice`: the live probe must create
the scope with `Delegate=yes`, read back its actual `Slice` and `ControlGroup`,
exercise owned leaf placement, restore each process, and verify safe scope
retirement. No host mutation or namespace join was used for this preflight.

### Focused post-repair regressions (2026-10-01)

On exact worktree tip `298e4157e9a5cb96fefea6d976dbf50691fb008d`,
`tests/test_serve_placement.py` and
`tests/test_serve_socket_carrier.py` passed serially: **406 passed in
33.16 s**. This is local targeted evidence for the B1–B3 repairs, not the
registered package gate. Exact-tip R0/R1, R3, doctor, live delegated-scope
restoration probe, and round-7 fix verification remain outstanding.

### Focused rerun on current P6 tip (2026-10-01)

On exact committed tip `28345cecb440fa8e6dc566ec98f0f44ad8e091bc`,
`nice -n 19 ionice -c 3 python3 -m pytest tests/test_serve_placement.py
tests/test_serve_socket_carrier.py -q` passed serially: **406 passed in
32.29 s**. This is targeted local regression evidence only. It does not
replace exact-tip registered R0/R1, R3, doctor, the delegated-scope
start/stop restoration probes, or R2/full gate. The current checkout must
first reconcile the accepted P1/main tree and then receive fresh final
evidence.

### Release-output correction: CP-14 (2026-10-01)

The original Bake `all` target exposed both `cgprofile:local` and the
versioned GHCR tag to `--push`. Since an unqualified Docker image name
defaults to Docker Hub, so the CMRU publish step would also have attempted an
unintended `docker.io/library/cgprofile:local` publication. The wrapper now
uses a local-only target for `--build` and selects `cgprofile-release` for
`--push`; the latter's resolved output is exactly the versioned GHCR image.
The independent `docker buildx bake --print` checks showed the two targets
emit only their intended single tags, and the focused wrapper tests passed
3/3. CP-14 is fixed and indexed. This packaging repair is local focused
evidence only; all registered gates and release checks remain outstanding on
the resulting exact tree.

### Existing nyxloom config-lint limitation (2026-10-01)

`nyxloom lint` currently exits 1 on this package's pre-existing
`coverage-floor` gate assertion because the accepted vocabulary does not yet
include a truthful whole-project coverage assertion. The upstream nyxloom
backlog already records this design gap as NL-25. I did not relabel this
whole-project 100% line-and-branch check as `changed-line-coverage`, which
would claim a different comparison; no nyxloom files were changed as part of
RG-55.

### Mutation-oracle regression restoration (2026-10-01)

The current P6 tree was missing two P6 regression tests that had been added
after the 312-candidate survivor triage: the non-ESRCH placement-write failure
must not invoke systemd's PID-migration bridge, and an unreadable process
identity must fail closed. Both tests are restored in the current checkout.
This checkpoint has not yet run those tests; it is not gate or mutation
evidence. The replacement campaign recorded for `6540f8776` ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, so it does not qualify P6. After focused
verification, reconcile the latest accepted P1 tree, freeze the final P6
candidate, and collect fresh exact-tree R0/R1, R3, R2, full-gate, and live
delegated-scope restoration evidence before release.

### Controller correction — focused oracle semantics (2026-10-01)

The first focused run after that checkpoint reported 522 passed and one
failure in the restored non-ESRCH test. The test had been copied from an older
direct-`cgroup.procs` movement design; current D-31 placement instead uses the
verified systemd scope API for PID migration, so the test's expected refusal
was not a valid oracle for the current contract. It was replaced with a
behavioral test that rejects any direct membership-file write and asserts
successful systemd-mediated placement. The separate unreadable-process-
identity refusal oracle was retained.

At exact P6 commit `67f45c28b614923f3a4e4a883c3284308c5ef410`, the serial,
load-niced placement/systemd/socket regression set passed **523 tests in
31.02 s**. This is targeted local evidence only. The old
`6540f8776` mutation result remains `BUDGET_EXCEEDED/CANDIDATE_HUNG`; it is
not current-tree evidence. Current-main reconciliation, exact registered
gates, a live delegated-scope start/stop restoration probe, fix-verification
review, and a fresh R2/full gate remain open.
