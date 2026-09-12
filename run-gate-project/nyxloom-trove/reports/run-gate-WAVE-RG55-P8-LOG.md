# run-gate-WAVE-RG55-P8 — LOG

One entry per commit (self-hash rule: a commit's own hash lands in the NEXT
entry). Package: mdt host-setup `dev-infra.slice` / `dev-gates.slice`.
Worktree `.worktrees/mdt-dev-slices`, branch `mdt-dev-slices`, base
`11ac5d67` (main).

## Orientation

Read in full: DESIGN-2026-09-12-liveness-placement-admission.md (all of §1-4,
§6-7 for context); host-setup/README.md, CGROUP-NOTES.md (full read —
"Per-container memory.min guarantees" section is load-bearing for M1, see
Decision ask below), host-setup.env.example, install.sh, units/dev.slice.in,
dev-interactive.slice.in, dev-background.slice.in,
dev-memory_min_guaranteed.slice.in, scripts/mdt-apply-dev-caps.sh,
scripts/check.sh, scripts/mdt-slice-audit.py (header only — generic
dev.slice-subtree walker, needs no change), mdt-host-setup-wizard.py (main()
orchestration + write flow only — template-surgery over full example text,
needs no change since new keys are simply untouched/preserved).
`modern-debian-tools-python-debug/templates/devcontainer.json`
(`containerEnv` block — the actual CGROUP_PARENT_DEV_* export path),
`DEVCONTAINER-LIFECYCLE.md` "Host resource governance" section.
mdt project gate: `modern-debian-tools-python-debug/run-gate.toml` (host-lane
`py_compile` smoke only, no pytest wired in yet).
Memory: `mdt-host-setup-companion.md`, `soulmask-memory-pressure-findings.md`
(confirms the D-19 motivation: gates currently share `dev-background.slice`
with the ~4 GiB dstdns stack; "the user's own instinct was a separate
dev-gates.slice; that remains unbuilt").

## Decision ask #1 (RW-9 — logged, not blocking)

CGROUP-NOTES.md's own documented ancestor-chain rule ("Per-container
memory.min guarantees" section) applies to `dev-infra.slice`'s
`MemoryMin=256M` (M1) exactly as it already applies to
`dev-memory_min_guaranteed.slice`'s ceiling: `dev.slice`'s own `MemoryMin`
must hand down at least as much as the SUM of every child that declares its
own `MemoryMin`, or the surplus/shortfall either leaks to siblings or leaves
a child's floor silently inert (the exact two rejected-alternative rows in
that section's table). The design doc's §3 layout table lists
`dev-infra.slice`'s `MemoryMin=256M` as a fixed, always-on default (not
opt-in like the guaranteed tier), so `dev.slice.in`'s own `MemoryMin` cannot
stay pinned to `DEV_MEMORY_MIN_GUARANTEED_CEILING` alone once `dev-infra`
exists — it would leave the daemon's floor silently inert, defeating D-18's
explicit point ("the only way a floor is real on this host"). Neither the
design doc nor the handoff's M1 text calls this out explicitly.

Taking the design's own established invariant (never re-deriving the VALUES
256M/768M/1G/100/50, only correctly wiring the ancestor arithmetic that makes
256M real): `dev.slice.in`'s `MemoryMin` becomes a render-time SUM of
`DEV_MEMORY_MIN_GUARANTEED_CEILING` (opt-in, default empty=0) and
`DEV_INFRA_MEMORY_MIN` (always-on default 256M), computed in `install.sh`
from the two source vars (never a third independently-set value — same
anti-drift discipline the existing doc already insists on for the two-var
case). `dev-memory_min_guaranteed.slice.in` itself is untouched (still pinned
to `DEV_MEMORY_MIN_GUARANTEED_CEILING` alone). `check.sh`'s existing
ancestor-mismatch check is extended to cover the new three-way arithmetic.
Continuing without stopping (RW-9).

## Commits

### M1 — dev-infra.slice / dev-gates.slice units + env keys (hash: see next entry)

New: `units/dev-infra.slice.in` (MemoryMin=256M/MemoryHigh=768M/MemoryMax=1G/
CPUWeight=100/IOWeight=50, no ManagedOOM), `units/dev-gates.slice.in`
(MemoryHigh=4G/MemoryMax=6G/MemorySwapMax=32G/CPUWeight=20/IOWeight=10,
ManagedOOMMemoryPressure=kill like dev-background). Env keys +
reasoned comment blocks in `host-setup.env.example`
(DEV_INFRA_MEMORY_MIN/_HIGH/_MAX/_CPU_WEIGHT/_IO_WEIGHT,
DEV_GATES_MEMORY_HIGH/_MAX/_SWAP_MAX/_CPU_WEIGHT/_IO_WEIGHT/
_OOM_PRESSURE_LIMIT), each citing the design doc and the 16 GiB-host/
re-tune-from-data framing M1 asked for.

Also (Decision ask #1 above): `units/dev.slice.in`'s `MemoryMin` changed
from a direct `@DEV_MEMORY_MIN_GUARANTEED_CEILING@` reference to
`@DEV_SLICE_MEMORY_MIN@`, a render-time-computed sum (wired in M2's
install.sh commit, since it is render logic) of that var and the new
always-on `DEV_INFRA_MEMORY_MIN` — without this, dev-infra.slice's 256M
floor would be silently inert per CGROUP-NOTES.md's own documented
ancestor-chain rule. `units/dev-memory_min_guaranteed.slice.in`'s
cross-reference comment updated to describe the sum instead of a 1:1 pin
(its own MemoryMin directive is unchanged — still reads
DEV_MEMORY_MIN_GUARANTEED_CEILING directly).

### M2 — install.sh render/install + placement report + check.sh (hash: 16f3a476 = M1)

`install.sh`: `_bytes_of()` helper (K/M/G/T -> bytes, base 1024, matching
systemd's own unit-file suffix convention) + `DEV_SLICE_MEMORY_MIN` computed
before any rendering happens; both new slices added to `RENDER_VARS`,
rendered (`units/dev-infra.slice.in` -> `/etc/systemd/system/dev-infra.slice`,
same for `dev-gates`), and started alongside the existing tiers. Top-of-file
description comment updated to list every installed slice.

`scripts/mdt-apply-dev-caps.sh`: a new read-only report block (placement is
create-time only, CGROUP-NOTES.md #1 — this never moves anything) — if a
container named `cgprofile-host-daemon` is present, WARN naming its actual
`HostConfig.CgroupParent` when it is not `dev-infra.slice`.

`scripts/check.sh`: `dev-infra`/`dev-gates` added to the existence/
ActiveState loop; two new "effective values" sections printing
`memory.min`/`memory.high`/`memory.max`/`cpu.weight`/`io.weight`/
`io.bfq.weight` (dev-infra) and `memory.high`/`memory.max`/
`memory.swap.max`/... (dev-gates) straight from cgroupfs. The
`dev-memory_min_guaranteed.slice` ancestor-chain check is generalized from a
1:1 pin to a 3-way sum (`dev.slice` MemoryMin == `dev-infra.slice` +
`dev-memory_min_guaranteed.slice`, FAIL naming the actual mismatch direction
either way) — matches the M1 `DEV_SLICE_MEMORY_MIN` change. Container-
placement informational section's hint text updated to name the two new
tiers' expected occupants.

`systemd-analyze verify` was NOT added — grepped the whole host-setup/ tree
first (M2 text: "if it is there already"); it is not used anywhere in this
project today, so nothing to extend.

Verification before commit: `bash -n` on all three scripts (clean) and
`shellcheck` (default severity) on all three — zero NEW findings; every
finding shellcheck reports (SC2015 x7, SC2181 x1) is on a pre-existing line
outside my diff, confirmed by line number against `git diff`.

### M3 — export CGROUP_PARENT_DEV_INFRA/_GATES to devcontainers + doc (hash: 22049949 = M2)

Orientation step 3 (`grep -rn CGROUP_PARENT_DEV_BACKGROUND` outside
`.worktrees/`) confirmed the actual export path within this project's own
scope is exactly one file: `templates/devcontainer.json`'s `containerEnv`
block (the file's own comment already calls it "the ONE place both tier
names are declared"). `host-setup.env.example`/`units/dev-background.slice.in`
only mention the var in prose comments, never as a key. Other consumers
found by that grep (srdm's `tools/gate.sh`, cmru's `tester_gate.py`,
run-gate itself, ciu governance) are OUT of this package's scope (P5/other
packages' job to actually read the new vars) — D-24's "consumer fallback
rule" is what I document, not what I implement for them.

`templates/devcontainer.json`: added `CGROUP_PARENT_DEV_INFRA":
"dev-infra.slice"` and `CGROUP_PARENT_DEV_GATES": "dev-gates.slice"` to
`containerEnv`, same path/pattern as the two existing keys; expanded the
block's own comment to name D-24's fallback rule explicitly (daemon ->
CGROUP_PARENT_DEV_INTERACTIVE, run-gate -> CGROUP_PARENT_DEV_BACKGROUND when
unset) and to note the devcontainer's own `runArgs --cgroup-parent=` is
UNCHANGED (dev-interactive.slice) — this devcontainer never itself joins
dev-infra/dev-gates, only spawns containers that do. Verified the edited
file is still valid JSON after JSONC comment-stripping (python, ad hoc, both
new keys and the unchanged runArgs value present) — jsonc has no standard
CLI validator in this project, this was the fastest honest check available.

`DEVCONTAINER-LIFECYCLE.md` "Host resource governance": light-touch edit,
"containerEnv vars naming both tiers" -> "naming all four dev-tier slices"
with the two new var names and a pointer to host-setup/README.md's new
section (written next, M4). Did NOT touch
`modern-debian-tools-python-debug/README.md` (top-level) — that file is in
the handoff's explicit "never touch, someone else's uncommitted edits"
list.

## Controller re-scope (RW-30, design amendment A1) — not a decision ask

Received mid-package, after M3 committed (1de4c93c), before M4 was written.
Verified before acting: the message named a specific commit (`main` HEAD
moved from `11ac5d67` to `5edec58c`) and a specific new `## A1` section in
the design doc; both checked out for real against `main` (`git log`/
`git show main:...DESIGN-....md`) with content matching the message exactly
(D-27/D-28/D-29, `cgprofile.slice`, "dev-infra.slice and
CGROUP_PARENT_DEV_INFRA are withdrawn", P8 = dev-gates.slice only) before any
file was touched.

Ruling (D-29): `dev.slice` contains dev LOAD only. `dev-gates.slice` stays
(gates ARE dev load, the admission capacity object). `cgprofile-host-daemon`
is a HOST DEPLOYMENT, not dev load — it ships its own top-level
`cgprofile.slice` with the `cgroup-profiler` package (P6's job), authoring
`cgroup_parent: cgprofile.slice` directly with no environment-variable
indirection. `dev-infra.slice` and `CGROUP_PARENT_DEV_INFRA` are withdrawn
from mdt entirely.

### Revert commit — withdraw dev-infra.slice (hash: see next entry)

Removed `units/dev-infra.slice.in` entirely. `host-setup.env.example`:
removed the whole `DEV_INFRA_*` section; restored `dev-memory_min_guaranteed.
slice`'s "THE ONE RULE THAT MATTERS" comment to its original 1:1-pin wording.
`units/dev.slice.in`: `MemoryMin` reverted from `@DEV_SLICE_MEMORY_MIN@` back
to the original direct `@DEV_MEMORY_MIN_GUARANTEED_CEILING@` reference.
`units/dev-memory_min_guaranteed.slice.in`: cross-reference comment reverted
to its original wording. `install.sh`: removed the `_bytes_of()` helper and
`DEV_SLICE_MEMORY_MIN` computation entirely, removed every `DEV_INFRA_*`/
`DEV_SLICE_MEMORY_MIN` entry from `RENDER_VARS` and the `dev-infra.slice.in`
render call and its `systemctl start` entry (dev-gates.slice's own render
call/start entry, added in M2, is untouched). `scripts/mdt-apply-dev-caps.sh`:
removed the whole cgprofile-host-daemon placement-report block and its
header bullet (per the re-scope: "drop the cgprofile-host-daemon placement
check" — the daemon is no longer this project's concern at all).
`scripts/check.sh`: removed `dev-infra` from the slice-existence loop and
the whole "dev-infra.slice effective values" section; the `dev.slice`
MemoryMin ancestor-chain check reverted from the 3-way sum back to the
original 2-way exact-pin check (byte-for-byte the M1-baseline text);
container-placement hint text's "cgprofile-host-daemon should show
dev-infra.slice" line dropped (the "gate/lane containers should show
dev-gates.slice" line, added in M2, stays). `templates/devcontainer.json`:
removed `CGROUP_PARENT_DEV_INFRA`; `containerEnv` comment rewritten to
explain D-19/D-24 for `CGROUP_PARENT_DEV_GATES` only, with an explicit note
that the daemon is NOT a consumer of this block per A1/D-29.
`DEVCONTAINER-LIFECYCLE.md`: "all four dev-tier slices" reverted to "three".

Verified byte-for-byte against `main`@`11ac5d67` (`git diff 11ac5d67 --
<path>`) that `units/dev.slice.in` and `units/dev-memory_min_guaranteed.
slice.in` are now IDENTICAL to their pre-P8 baseline (empty diff both), and
that `scripts/mdt-apply-dev-caps.sh` is fully identical too (empty diff) —
confirming the revert left no residue in files that should have zero
dev-gates-unrelated change. `bash -n` + `shellcheck` re-run on all touched
shell scripts after the revert: clean, zero new findings (same pre-existing
SC2015/SC2181 set as before, confirmed by line number). `templates/
devcontainer.json` re-validated as parseable JSON after JSONC comment
stripping (same ad hoc python check as M3), `CGROUP_PARENT_DEV_INFRA`
confirmed absent from the parsed object, `CGROUP_PARENT_DEV_GATES` confirmed
present with the correct value, `runArgs`'s `--cgroup-parent=
dev-interactive.slice` confirmed unchanged.

### M4 — docs (hash: see next entry)

`host-setup/README.md`: Tiering model diagram/table regain `dev-gates.slice`
(dev-infra never added, written fresh after the re-scope) plus a one-sentence
note that `cgprofile-host-daemon` is NOT under `dev.slice` by design,
pointing at design doc §A1 (asked for explicitly by the re-scope message).
New "dev-gates: why" section: containment + the admission-capacity-object
role, the 2026-08-04 finding it fixes (memory
`soulmask-memory-pressure-findings.md`), the explicit "dev LOAD only, does
not hold the daemon" boundary with the A1/D-29 pointer, env keys, and the
upgrade sequence (below). "What gets installed" table's units row gains
`dev-gates.slice.in` (and, found while already editing that exact line,
the pre-existing `dev-memory_min_guaranteed.slice.in` gap — it shipped
2026-09-08 but was never added to this table; a one-word drive-by fix, not
a new M4 obligation). "Uninstall" block's `rm` brace list gains
`dev-gates`/`dev-memory_min_guaranteed` (same drive-by).

Operator install/upgrade sequence: verified against the ACTUAL install.sh
behavior rather than assumed — `install.sh` never touches an
already-installed `/etc/mdt/host-setup.env` except to render *from* it
(confirmed by reading the script, not guessing), so a plain re-run after
this package would render `dev-gates.slice` with every `@DEV_GATES_*@`
directive silently DROPPED (render()'s "unset -> not applied" rule) --
effectively unbounded except the literal `ManagedOOM*=kill` lines. Documented
the already-established remedy the script's own header names for exactly
this case (`--force` or `--wizard`, both back up + regenerate to pick up
new keys) as the real sequence: `--force` (+ hand-reapply prior tuning from
the backup) -> plain `install.sh` (renders/installs/daemon-reload/starts
every slice/runs the sweep once) -> `mdt-host-check.sh` to verify, with
`--wizard` noted as the interactive alternative to the first step. No
daemon-restart step (re-scope: "operator sequence without the
daemon-restart step").

`host-setup/CGROUP-NOTES.md`: new "dev-gates.slice and placed lane leaves"
section, two facts, both scoped to dev-gates only per the re-scope --
(1) the memory.min-on-leaf fact (a future `rg-<token>` leaf's own
`memory.min`, if the daemon package ever writes one per D-20, needs the
same ancestor-chain cooperation from `dev-gates.slice`/`dev.slice` already
documented above for the guaranteed tier -- currently dormant since
`dev-gates.slice` itself declares no `MemoryMin`); (2) the
no-nesting-inside-scopes fact from D-20 (why a lane leaf is created as a
SIBLING under `dev-gates.slice`, never nested inside a container's own
`docker-<id>.scope` -- cgroup v2's "no internal processes" rule would break
every later `docker exec` with `EBUSY`; moving a pid's cgroup accounting out
of a container's scope is safe, its pid/mount namespaces are unaffected).
Both are documentation of design facts this package's own units depend on
being true later -- no daemon/leaf-creation code is implemented here (that
stays scripts/cgroup-profiler's job, out of scope).

CHANGES/TODO: `modern-debian-tools-python-debug/TODO.md` is on the
handoff's explicit "never touch, someone else's uncommitted edits" list and
has no existing dev-gates/cgprofile line item (checked, read-only, before
deciding) -- skipped, not touched, not committed; no separate `CHANGES.md`
exists for this project (checked: only `TODO.md` at the project root).
Noting the gap in the REPORT for the operator to reconcile once the other
session's TODO.md edits land.

Project's own backlog: mdt has no `nyxloom-trove/backlog/` structure (only
run-gate-project/assay/ciu/nyxloom do) and its one candidate location
(TODO.md) is the same blacklisted file above -- no FIXED-evidence entry
filed anywhere in mdt itself; the REPORT carries the FIXED evidence (commit
hashes) for whoever reconciles TODO.md, and separately for the memory file
`soulmask-memory-pressure-findings.md` (not repo-tracked, a controller/
session-memory concern, out of a package implementer's scope to edit
directly) which is where the original "dev-gates.slice ... remains unbuilt"
finding lives.

Found while re-checking every dev-gates file for A1-superseded citations
(D-18/D-21/D-24 were the three A1 touched): `units/dev-gates.slice.in`'s own
`ManagedOOMMemoryPressure` comment (written in M1, before the re-scope
arrived) cited "D-21's detached-owner design" for why a killed gate client
still leaves a usable history record — D-21 was DROPPED by A1/D-28. Fixed
in this commit to cite D-28 instead (same underlying claim, still true, just
under the daemon-enforced mechanism rather than the detached-owner one).
Grepped every dev-gates-touching file for D-18/D-21/D-24/D-6/D-19/D-20/D-25
citations first — this was the only stale one found.

### Gates — renderer test, wired into the registered gate (hash: see next entry)

Found the registered gate: `modern-debian-tools-python-debug/run-gate.toml`
`[lanes.smoke]`, a host lane doing `py_compile` over every tracked `.py`
file only — no host-setup-specific test existed (confirmed: no
`test_*`/`*_test.py`/`conftest.py` anywhere under `host-setup/`, and the
outer project's own `scripts/test_*.py` files are for the ai-cli-tools
installer, unrelated).

`host-setup/tests/test-render.sh` (new, bash — matches this project's
existing "no python here beyond a few specific scripts" shape, and sidesteps
the python-coverage bar entirely rather than writing throwaway product-less
Python just to satisfy it): extracts `install.sh`'s own `render()` function
+ `RENDER_VARS` list VERBATIM (a sed byte-range on two stable anchor
patterns, not a hand-copied duplicate that could silently drift), sources
that plus `host-setup.env.example` itself (no root, no `/etc/mdt/`, no host
mutation — pure text substitution), renders every `units/*.in` template, and
asserts: (1) no rendered unit keeps an unresolved `@VAR@` token — the exact
bug class CGROUP-NOTES.md's "Status corrected 2026-09-08" note describes for
a past `DEV_MEMORY_MIN_GUARANTEED_CEILING` `RENDER_VARS` omission, checked
for every unit, not just the new one; (2) `dev-gates.slice` renders every
`DEV_GATES_*` key to its shipped example value; (3) `dev.slice` and
`dev-memory_min_guaranteed.slice` render with NO `MemoryMin=` line at all
(confirms the RW-30/A1 revert restored the original opt-in-only behaviour
byte-for-byte, not just by eyeball diff); (4) no `units/*.in` file mentions
`dev-infra`/`DEV_INFRA_` (confirms the withdrawal); then `bash -n` and
`shellcheck -S warning` (not default severity — see the script's own
comment: the three pre-existing files carry SC2015/SC2181 info/style
findings on lines this package's diff never touches, verified earlier by
line number against `git diff 11ac5d67`; fixing decades of pre-existing
style debt is out of this package's scope, `-S warning`+ is a real
correctness bar and passes clean) on every shell script this package
touches, itself included.

Ran the script directly first (`bash host-setup/tests/test-render.sh`):
all 6 assertions pass, `test-render: ALL OK`, exit 0.

Wired into `run-gate.toml`'s `[lanes.smoke]` `argv` (after the `py_compile`
step, before the final `echo`) so it is part of the actual registered gate,
not a standalone script nobody runs — updated the lane's header comment to
describe it.

### Gate verdict (hash: 62927f1f = the Gates commit above)

RAM PSI checked first (`/proc/pressure/memory`): `full avg10=2.44` — well
under the 5 back-off threshold. From a clean, committed tree (run-gate
refuses a dirty one): `nice -n 19 ionice -c 3 python3 run-gate.py smoke`.
Verdict read in a separate step (not a pipe tail): `run-gate: lane 'smoke'
exit 0`. `docker ps -a --filter name=run-gate-vbpub-smoke` afterward, in
its own separate step: no leftover container. REPORT written next with the
final tip and the exact operator command sequence.

## Round-1 repair set (RW-32, coordinator message after 7bd2f03c)

Verified the coordinator's re-scope before acting (same discipline as the
RW-30 re-scope): read `run-gate-WAVE-RG55-P8-REVIEW-round1.md` in full
(untracked in the shared checkout, matches the established per-wave
convention for review files at this stage — same pattern already seen for
P2's own round1/round2 reports); confirmed `RW-32` genuinely exists in the
controller log and `A2`/`D-30` genuinely exist in the design doc, both on
`main` (`git show main:...`), content matching the coordinator's message
exactly, including this package's own tip hash `7bd2f03c`.

### C1 — test-render.sh: B2, B6, S7, S8, S14 (hash: see next entry)

`tests/test-render.sh` rewritten: (B2) end-anchor guard
(`tail -n1 | grep -q 'SWEEP_INTERVAL"$'` for RENDER_VARS, an equivalent
`}`-line guard for `render()`) PLUS a fail-closed content scan
(`systemctl|apt-get|install |mkdir |modprobe|udevadm|python3 |/etc/systemd/
system|/etc/docker`) that refuses to `source` an over-run snippet even if
both anchors still match; `export TMPDIR="$TMP"` (S14). (B6) three new
assertions: install.sh actually renders + (for `*.slice`) starts every
`units/*.in` (continuation-lines joined via `sed ':a;N;$!ba;s/\\\n/ /g'`
first, so a name on the `systemctl start ... \` continuation is still
found), every `@VAR@` used in any template has a matching `host-setup.env.
example` line, install.sh installs the cgprofile tmpfiles.d entry (M5, adds
before M5 lands so the assertion exists and goes green together with it),
and the wizard's earmark-sum doctest actually runs (S2, added here since
it's the same "run something real, not just py_compile" principle). (S7)
the file header comment's claim 3 no longer says "byte-for-byte" — it now
says explicitly that this test asserts only the no-MemoryMin-line behavior,
and that the byte-for-byte claim rests on `git diff` established
independently. (S8) assertion 2's `${VAR}` references now use
`${VAR:?msg}` naming `host-setup.env.example` as the likely fault, not a
bare `${VAR}` that crashes with "line N: VAR: unbound variable" under
`set -u`.

Mutation-verified (scratch copies under this session's scratchpad, restored
after each, never the real worktree files):
- B2's own demonstration (append a var to the last `RENDER_VARS` line) →
  **RED**: `FAIL: RENDER_VARS end anchor no longer matches install.sh`.
- B6 mutation 1 (delete the `dev-gates.slice.in` render call) → **RED**:
  `FAIL: install.sh never renders units/dev-gates.slice.in`.
- B6 mutation 2 (drop `dev-gates.slice` from the `systemctl start` list,
  precisely — buildkitd's own entry left untouched) → **RED**: `FAIL:
  install.sh never starts dev-gates.slice`.
- B6 mutation 3 (add `@DEV_GATES_NEWKEY@` to the unit + `RENDER_VARS`, not
  the example — B3's own upgrade-shape failure) → **RED**: `FAIL:
  @DEV_GATES_NEWKEY@ has no DEV_GATES_NEWKEY= line in host-setup.env.
  example`.
- S8's own demonstration (typo `DEV_GATES_MEMORY_HIGH` → `..._HIG` in the
  example) → **RED**, message now names `host-setup.env.example` as the
  fault, not the test script.

`shellcheck -S warning` flagged a NEW issue introduced by the S8 fix itself
(SC1011: an apostrophe in "typo'd" inside a `${VAR:?msg}` string breaks
shellcheck's own quote parsing) — reworded every occurrence to "mistyped"
(no apostrophe); re-ran clean. `bash -n` clean. Full green run afterward:
`test-render: ALL OK`, exit 0 (this run is BEFORE B1/M5 land, so it does
not yet cover the cap-watcher/tmpfiles assertions for real — re-run at the
end of the whole repair set, see the final gate verdict entry).

### C2 — host-setup.env.example: B5, D1's env keys (hash: see next entry)

(B5) `:127`'s "BOTH this slice and (its share of) dev.slice below" reverted
to main's exact original "BOTH this slice and dev.slice below" — residue of
the withdrawn 3-way `MemoryMin` sum the `b9e628f5` revert missed. Verified
no other residue: `git grep -n "its share of"` at this tip finds nothing.
(D1) new `DEV_CAP_GATES_MEMORY_MAX=4G` key in the "Reactive per-container
cap watcher" section, with the reasoning the ruling asked for (one lane
alone drives the tier into throttle, never past MemoryMax; two lanes are
bounded by oomd; `1G` would re-create the 2026-08-04 incident) — verify:
`grep -n DEV_CAP_GATES_MEMORY_MAX host-setup.env.example`.

### C3 — mdt-dev-cap-watcher.py: B1/D1 (hash: see next entry)

`WATCHED_SLICES` gains `dev-gates.slice`. New `SLICE_DEFAULT_MEMORY_MAX`
dict maps each watched slice to its own default (`DEV_CAP_MEMORY_MAX` for
interactive/background, `DEV_CAP_GATES_MEMORY_MAX` for gates) — deliberately
NOT a single shared constant, so `apply_default_cap()` now takes
`slice_name` and looks up the right one per slice; `main()` asserts every
`WATCHED_SLICES` entry has a `SLICE_DEFAULT_MEMORY_MAX` entry at startup
(fails loudly rather than falling back silently if the two lists ever
drift). Module docstring extended with the full D1 reasoning (why 4G not
1G, and that this coarse backstop COMPOSES with, is not withdrawn by, a
future daemon's per-lane placement caps). `units/mdt-dev-cap-watcher.
service`'s own header comment updated to match (the third of the "three
places the slice list is written down" the review named,
`host-setup.env.example` being the first two, C2).

Verify: `python3 -m py_compile scripts/mdt-dev-cap-watcher.py` (clean);
`grep -n WATCHED_SLICES scripts/mdt-dev-cap-watcher.py` shows all three
slices; `python3 -c "import ast; ast.parse(open('scripts/mdt-dev-cap-
watcher.py').read())"` (clean, confirms no syntax regression from the
signature change touching every call site).

### C4 — D2 + S5 + S12: drop gates ManagedOOMSwap, generalize stale sibling phrasing (hash: see next entry)

(D2/S1) `units/dev-gates.slice.in`: `ManagedOOMSwap=kill` removed; the
`ManagedOOMMemoryPressure` comment block explains why (D-19 lists only the
pressure kill; `MemorySwapMax=32G` is this tier's deliberate relief valve;
an oomd swap kill would fire on a signal D-6 rejects and kill the lane
that's legitimately using the allowance the unit grants it) and notes
`dev-background.slice` keeps its own unchanged — a real difference, not an
oversight. Verify: `grep -c ManagedOOMSwap units/dev-gates.slice.in`
prints `0`; `tests/test-render.sh`'s own assertion (2) from C1 now also
fails RED if this line is ever re-added (mutation-verified: re-adding it
on a scratch copy → `FAIL: dev-gates.slice has ManagedOOMSwap=kill --
withdrawn by RW-32/D2`).

(S5) "dev-interactive + dev-background"-only phrasing generalized to name
every `dev.slice` child (or "every child combined"), matching the wording
the P8 README pass already fixed: `scripts/mdt-apply-dev-caps.sh`'s header
comment + its own runtime log line, `scripts/check.sh`'s "dev.slice IO
caps" section header, `units/dev.slice.in`'s own header comment (now also
names `dev-gates.slice`/`dev-buildkitd.slice`, closing a PRE-EXISTING gap
that predates P8 — `dev-buildkitd.slice` was already missing before this
package).

(S12) `scripts/mdt-slice-audit.py`'s header comment now also names
`dev-gates.slice` alongside `dev-background.slice` as a tier with no
declared `MemoryMin`/`MemoryLow` (behaviour is unchanged and needs no fix —
the script scans `CG/dev.slice` recursively, so the new tier was already
covered; only the comment was incomplete).

Verify: `bash -n scripts/mdt-apply-dev-caps.sh scripts/check.sh` clean;
`python3 -m py_compile scripts/mdt-slice-audit.py` clean;
`shellcheck -S warning` on both shell scripts clean (no new findings).
