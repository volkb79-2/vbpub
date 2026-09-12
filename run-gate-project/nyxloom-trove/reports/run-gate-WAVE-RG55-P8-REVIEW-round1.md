# run-gate-WAVE-RG55-P8 — adversarial review, round 1

**Verdict: ACCEPT-conditional.** Merge is BLOCKED until B1–B6 land and are
re-verified in round 2. The unit itself, its sizing, its render/install
wiring and its docs are correct and match D-19/D-24/A1; what is missing is
the safety net around them — one silent capability regression the package
itself causes (B1), a test that can mutate the host after a plausible future
edit (B2), a fail-open upgrade path with no automated signal (B3), an
operator sequence that misstates what `install.sh --force` does (B4), and
two smaller correctness gaps (B5, B6).

Reviewer: fresh Opus xhigh session, never a fork. Base `main@11ac5d67`, tip
`7bd2f03c` (8 commits; withdrawal `b9e628f5`). Full diff `11ac5d67...7bd2f03c`
reviewed, every file type. Phase 1 was blind to the LOG/REPORT; Phase 2
reconciled against them. No host installs, no `systemctl`, nothing written
under `/etc`, no commits to the branch. One container ran: the registered
gate itself (`./run-gate.py smoke` — run-gate's own `environment = "host"`
lane launches an ephemeral container; explicitly instructed by the handoff,
verified cleaned up afterwards).

---

## What I verified green (so the blockers below are read in proportion)

| # | claim | how verified | result |
|---|---|---|---|
| 1 | unit matches D-19 exactly | rendered it | `MemoryHigh=4G` `MemoryMax=6G` `MemorySwapMax=32G` `CPUWeight=20` `IOWeight=10` `ManagedOOMMemoryPressure=kill` `ManagedOOMMemoryPressureLimit=75%` (== background's), `Before=slices.target`, dash-name nesting, **no `MemoryLow`** | ✅ |
| 2 | `dev.slice` MemoryMin logic unchanged | rendered every `units/*.in` from `11ac5d67` and from `7bd2f03c` with each tree's own example; `diff -r` | byte-identical for **every** unit; only `dev-gates.slice` is new | ✅ |
| 3 | withdrawal complete | `git diff 11ac5d67 7bd2f03c` per file touched by `16f3a476`/`22049949`/`1de4c93c` | `units/dev-infra.slice.in` gone, `units/dev.slice.in`, `units/dev-memory_min_guaranteed.slice.in`, `scripts/mdt-apply-dev-caps.sh` byte-identical to main; `git grep dev-infra\|DEV_INFRA` at the tip hits only P8 records, the design doc's deliberately preserved pre-A1 text, the test's own guard, and `ciu/docs/SPEC-V8.md:828` (controller's, see S15) | ✅ |
| 4 | every `@KEY@` has an example key | extracted all `@[A-Z_]*@` from `units/*.in`, matched `^KEY=` in the example | 34/34 present, zero misses | ✅ |
| 5 | render is idempotent | rendered twice into fresh dirs and twice over the same dir | identical every time | ✅ |
| 6 | `systemd-analyze verify` on the rendered unit | run read-only on `render-new/dev-gates.slice` | rc 0, no complaint about the new unit (the `dev.slice` IO*Max notes are pre-existing, caused by the example's empty `IO_DEV_PATH`) | ✅ |
| 7 | the test extracts `render()` rather than duplicating it | read + mutation-tested | genuinely a `sed` byte-range on `install.sh` (see B2 for the sharp edge) | ✅ |
| 8 | mutation: drop a key from the unit template | removed `MemoryHigh=@DEV_GATES_MEMORY_HIGH@` | **RED** — `FAIL: dev-gates.slice missing expected line: MemoryHigh=4G` | ✅ |
| 9 | mutation: typo a key in the env example | `DEV_GATES_MEMORY_HIGH` → `..._HIG` | **RED** (via `set -u` crash, see S8) | ✅ |
| 10 | mutation: drop a key from `RENDER_VARS` | removed `DEV_GATES_MEMORY_MAX` | **RED** — `unresolved placeholder: @DEV_GATES_MEMORY_MAX@` | ✅ |
| 11 | mutation: resurrect `units/dev-infra.slice.in` / mention `dev-infra` in a template | both | **RED** both | ✅ |
| 12 | registered gate | `nice -n 19 ionice -c 3 ./run-gate.py smoke` from `.worktrees/mdt-dev-slices/modern-debian-tools-python-debug`, verdict read in a **separate step** | `run-gate: lane 'smoke' exit 0`; all 6 test assertions pass inside the gate container; shellcheck is present in the gate image; no leftover container; worktree still clean | ✅ |
| 13 | shellcheck claim ("no finding on a touched line") | `shellcheck -f gcc` at **default** severity + `git diff -U0` hunk ranges | true: all 8 findings are `note` (SC2015/SC2181), none in a changed hunk | ✅ |
| 14 | shared checkout untouched | `git status --porcelain -- modern-debian-tools-python-debug/` | exactly the same 7 pre-existing dirty files; `host-setup/` clean | ✅ |
| 15 | devcontainer template still valid | JSONC comment-strip + `json.loads` | parses; 3 `CGROUP_PARENT_*` keys; `runArgs --cgroup-parent=dev-interactive.slice` unchanged | ✅ |
| 16 | the wizard carries the new keys | read `mdt-host-setup-wizard.py` (`text = example_text` then key-surgery, line 1285) | **README's claim is TRUE** — `--wizard` output is the example text, so the `DEV_GATES_*` block survives verbatim (but see S2) | ✅ |

---

## Blockers

### B1 — Gate containers silently lose their per-container `MemoryMax` backstop

`modern-debian-tools-python-debug/host-setup/scripts/mdt-dev-cap-watcher.py:68`

```python
WATCHED_SLICES = ["dev-interactive.slice", "dev-background.slice"]
```

The reactive watcher applies `MemoryMax=$DEV_CAP_MEMORY_MAX` (default `1G`,
`host-setup.env.example:264`) to every new `docker-*.scope` under a watched
slice that did **not** pass its own `--memory`. Its own docstring states the
reason: *"without it, one container ballooning under memory pressure can
force reclaim on every OTHER cgroup sharing the tier … before the tier's own
MemoryHigh/Max ever triggers."*

Today run-gate's lane containers land in `dev-background.slice` **and pass no
`--memory`** — I read the real argv in this session's own gate run:

```
docker run -d --name run-gate-vbpub-smoke-… --cgroup-parent dev-background.slice
  -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v … --user 1003:1003 <image>
```

so they are exactly the containers that watcher exists for. The moment any
consumer honours `CGROUP_PARENT_DEV_GATES`, those containers move to a slice
nobody watches, and their only remaining per-container bound is the
deliberately generous `docker-.scope.d/50-default-limits.conf` backstop
(whose own header says "THIS IS NOT A TIER CEILING … MUST be sized generously
… never tight"). One unlabelled runaway lane can then consume the whole 6 G
tier — the very capacity object RG-56 admission is going to key on, so the
readings admission trusts become meaningless exactly when they matter.

`mdt-apply-dev-caps.sh` does **not** compensate: I read it end to end; it
applies IO caps only, never `MemoryMax`, and matches by image/name, not slice.
`mdt-slice-audit.py` is fine (it scans `CG/dev.slice` recursively, so the new
tier is covered automatically).

**Prescription — pick one, deliberately, and say which in the README:**
(a) add `"dev-gates.slice"` to `WATCHED_SLICES` *and* give the tier its own
cap knob (`DEV_GATES_CAP_MEMORY_MAX`, defaulting to something a lane can
actually live in — `1G` is below the ~2 G headroom the 2026-08-04 incident
says a gate already needed, so reusing `DEV_CAP_MEMORY_MAX` would re-create
the incident in a new place); update the three places the slice list is
written down — `scripts/mdt-dev-cap-watcher.py:6-7,68`,
`units/mdt-dev-cap-watcher.service:2`, `host-setup.env.example:254-255`; or
(b) declare the tier watcher-exempt on purpose, with the reason (P5 will pass
per-lane `--memory` at create, D-20) written into the "dev-gates: why"
section AND into the watcher's docstring, so the next reader does not read
the omission as an oversight.
This is also a **decision ask** (see D1) — the value matters more than the
wiring.

---

### B2 — `test-render.sh`'s verbatim extraction can over-run and turn the gate test into a host-mutating script

`modern-debian-tools-python-debug/host-setup/tests/test-render.sh:40-45`

```bash
sed -n '/^RENDER_VARS="/,/SWEEP_INTERVAL"$/p' "$INSTALL_SH" > "$RENDER_SNIPPET"
sed -n '/^render() {/,/^}$/p'                 "$INSTALL_SH" >> "$RENDER_SNIPPET"
grep -q '^RENDER_VARS="' "$RENDER_SNIPPET" || fail "…anchor patterns are stale…"
grep -q '^render() {'    "$RENDER_SNIPPET" || fail "…anchor patterns are stale…"
…
. "$RENDER_SNIPPET"
```

The guards check only that the **start** anchors were found. If the **end**
anchor `/SWEEP_INTERVAL"$/` ever stops matching, `sed` ranges to EOF and the
snippet becomes the entire remainder of `install.sh` — which is then
`source`d. Demonstrated (extraction only; I did **not** source it): appending
one variable to the last `RENDER_VARS` line, the exact shape of the next
routine edit,

```
-IO_DEV_PATH SWEEP_INTERVAL"
+IO_DEV_PATH SWEEP_INTERVAL DEV_NEWKEY"
```

grows the extracted snippet from **12 lines to 156**, and the sourced text
then contains `render "$HERE/units/dev.slice.in" /etc/systemd/system/dev.slice`
(and the other seven renders), `mkdir -p /etc/systemd/system/docker-.scope.d`,
the `/etc/docker/daemon.json` merge, `install -m 0755 … /usr/local/sbin/…`,
`modprobe bfq`, `udevadm`, `systemctl daemon-reload`, `systemctl start …`,
`systemctl enable --now …`. Note both guards still pass in that state — the
start anchors are present.

As the non-root gate user the first `render` fails on permission and the test
dies with a confusing error; run once as root (an operator sanity-checking
host-setup with `sudo`, or any root shell) it rewrites the host's units and
reloads/starts systemd. This estate already has the durable rule for exactly
this class ("ask whether a test does a real host-impacting op and whether
cleanup reverses it" — the 2026-09-09 host-hang incident); a gate test must
be fail-closed here, not fail-open.

**Prescription** (three lines, no redesign):

```bash
RENDER_SNIPPET="$TMP/render-snippet.sh"
sed -n '/^RENDER_VARS="/,/SWEEP_INTERVAL"$/p' "$INSTALL_SH" > "$RENDER_SNIPPET"
tail -n1 "$RENDER_SNIPPET" | grep -q 'SWEEP_INTERVAL"$' \
  || fail "RENDER_VARS end anchor no longer matches install.sh -- extraction over-ran to EOF; update this test"
grep -nE '^(systemctl|apt-get|install |mkdir |modprobe|udevadm|python3 )|/etc/systemd/system|/etc/docker' "$RENDER_SNIPPET" \
  && fail "extracted snippet contains host-mutating statements -- refusing to source it"
```

(keep the two existing start-anchor guards). Consider also setting
`TMPDIR="$TMP"` for the whole test so nothing it does can escape its own
scratch dir.

---

### B3 — The upgrade path renders an **unbounded** `dev-gates.slice`, and nothing mechanical says so

`modern-debian-tools-python-debug/host-setup/install.sh:139-140` (RENDER_VARS)
+ `:143-154` (`render()`) + `scripts/check.sh:63-70`

Verified by rendering with an `/etc/mdt/host-setup.env` that predates the new
keys (shipped example minus the six `DEV_GATES_*` lines):

```
[Slice]
ManagedOOMMemoryPressure=kill
ManagedOOMSwap=kill
```

That is the whole `[Slice]` section. `render()`'s "empty → drop the
directive" rule removes `MemoryHigh`, `MemoryMax`, `MemorySwapMax`,
`CPUWeight`, `IOWeight` **and** `ManagedOOMMemoryPressureLimit`. So the
admission capacity object ships with:

* no memory ceiling of any kind (`memory.max` = `max`) — admission reads a
  meaningless number, and the 6 G containment the design promises is absent;
* oomd killing **enabled** with the limit directive dropped, i.e. oomd falls
  back to its own `DefaultMemoryPressureLimit` (60 %), *stricter* than the
  75 % this package configures — unbounded memory and a more trigger-happy
  killer at the same time.

`systemd-analyze verify` is clean on that render too (I checked), so it would
not catch it either. And `check.sh` then **actively reassures**: the unit
exists and `systemctl start` made it active, so the slice-units loop prints
`OK dev-gates.slice active (…)`, and the new effective-values block prints
`memory.max max` without comment — even though `check.sh` already sources
`$CONF` (line 14) and therefore has `DEV_GATES_MEMORY_MAX` in hand.

The README documents this trap honestly and prominently (`README.md:132-146`)
— that is real credit, and it is why this is B3 and not B1. But documentation
is not the mechanism this directory uses for its other fail-open invariant:
the `MemoryMin` pin gets a hard `fail` in `check.sh`. The estate's rule here
is "defaults are hazards"; a silently unbounded capacity object is the same
hazard shape.

**Prescription:**
1. `install.sh`, right after `. /etc/mdt/host-setup.env`: compare the `^[A-Z_]+=`
   key set of `$HERE/host-setup.env.example` against the installed config and
   print a loud, named WARN for every key present in the example and absent
   from the config ("your /etc/mdt/host-setup.env predates these keys: … —
   re-run with `--force` or `--wizard`, or add them by hand; directives for
   missing keys are DROPPED, not defaulted"). Generic, so it also covers the
   next key that gets added.
2. `scripts/check.sh:63-70`: compare each printed value against the
   corresponding `DEV_GATES_*` from `$CONF` and `fail` on "config says 6G,
   effective says max" (and on any other mismatch), the way the `MemoryMin`
   check already does. A `warn` is acceptable for a genuinely unset config;
   "config sets it, kernel does not have it" must be a `fail`.

---

### B4 — The operator upgrade sequence misstates what step 1 does; the "verified against the real script" claim does not hold

`modern-debian-tools-python-debug/host-setup/README.md:148-163` and
`run-gate-WAVE-RG55-P8-REPORT.md:88-101` (which calls the sequence "exact,
verified against the real script")

`install.sh`'s `--force` branch (`install.sh:81-85`) backs up, re-seeds from
the example, prints "REVIEW IT and re-run to apply edits" — and then **falls
straight through**: there is no `exit`. Line 91 sources the freshly-seeded
config and the script continues into `apt-get`, the full render of every unit
into `/etc/systemd/system`, the `daemon.json` merge, `systemctl daemon-reload`,
`systemctl start` of every slice, `systemctl enable --now` of the timer/
buildkitd/watcher, and `systemctl start mdt-host-slices.service`.

So the documented step 1 does not merely "back up and re-seed, discarding
prior tuning". It **installs and activates the example's 16 GiB-host numbers
across the whole dev estate, live**, and leaves them in force until the
operator finishes step 2 and runs step 3. On a host that is not 16 GiB (the
wizard exists precisely because that is the normal case) that window is a
real misconfiguration of `dev-interactive`/`dev-background`/`dev-buildkitd`,
not just of the new tier — on a shared host that also carries production.

**Prescription — fix the sequence, not just the prose.** Preferred: drop
`--force` from the recommended path entirely and document the additive
upgrade, which is one render pass and never discards tuning:

```bash
sudo cp /etc/mdt/host-setup.env /etc/mdt/host-setup.env.bak-$(date +%F)
diff <(grep -oE '^[A-Z_]+=' host-setup/host-setup.env.example | sort) \
     <(grep -oE '^[A-Z_]+=' /etc/mdt/host-setup.env | sort)   # shows the new keys
sudo $EDITOR /etc/mdt/host-setup.env      # paste the dev-gates.slice block, size it
sudo ./host-setup/install.sh              # ONE render/install/activate pass
sudo mdt-host-check.sh
```

If `--force` is kept as an alternative, it must carry an explicit warning
that it activates example defaults estate-wide immediately and therefore
belongs in a maintenance window. The `--wizard` note should be promoted from
a parenthetical to the recommended interactive path, since it is the only
variant that renders the operator's own values on the first pass. Mirror the
correction into the REPORT (and drop the "exact, verified" wording, or make
it true).

---

### B5 — Residue of the withdrawn design in the one invariant that matters

`modern-debian-tools-python-debug/host-setup/host-setup.env.example:127`

```diff
-# BOTH this slice and dev.slice below, per install.sh's render rule
+# BOTH this slice and (its share of) dev.slice below, per install.sh's
```

"its share of" is left over from M1's withdrawn 3-way `MemoryMin` sum (LOG
"decision 1"). With `dev-infra.slice` gone, `dev.slice`'s `MemoryMin` is a
strict 1:1 pin again — which the very next paragraph of the same comment
asserts ("THE ONE RULE THAT MATTERS: this value is read by BOTH … the SAME
variable, never two"), which `CGROUP-NOTES.md` calls "the one invariant that
actually matters", and which `check.sh:107/116` hard-`fail`s on. The
withdrawal commit `b9e628f5` restored `units/dev.slice.in` and
`units/dev-memory_min_guaranteed.slice.in` byte-for-byte but missed this
line.

**Prescription:** revert to `# BOTH this slice and dev.slice below, per
install.sh's render rule — the`, restoring main's wording exactly.

---

### B6 — The renderer test does not cover the wiring it was written to protect

`modern-debian-tools-python-debug/host-setup/tests/test-render.sh:54-66`

The test renders every `units/*.in` **itself**; it never asserts that
`install.sh` renders and installs them. Mutation-verified on a scratch copy:

| mutation | test result |
|---|---|
| delete `render "$HERE/units/dev-gates.slice.in" /etc/systemd/system/dev-gates.slice` from `install.sh` | **GREEN — `test-render: ALL OK`, exit 0** |
| drop `dev-gates.slice` from `install.sh`'s `systemctl start …` list | **GREEN — exit 0** |
| add a `@VAR@` to a unit + its key to `RENDER_VARS` but **not** to the example (the upgrade shape, B3) | **GREEN — exit 0**, directive silently dropped |

So the M2 deliverable — "install.sh renders/installs it in the same pass as
the others" — has no test at all, and the B3 failure mode has none either.

**Prescription** (three cheap assertions, all pure text):

```bash
# (5) every units/*.in is actually rendered+installed by install.sh
for tmpl in "$HERE"/units/*.in; do
  base="$(basename "${tmpl%.in}")"
  grep -qF "units/$(basename "$tmpl")" "$INSTALL_SH" \
    || fail "install.sh never renders units/$(basename "$tmpl")"
  case "$base" in *.slice)
    grep -qE "systemctl start( |.*\\\\\n *)[^#]*\b$base\b" "$INSTALL_SH" \
      || fail "install.sh never starts $base" ;;
  esac
done
# (6) every @VAR@ in any template has an assignment line in the example
for v in $(grep -ohE '@[A-Z_][A-Z0-9_]*@' "$HERE"/units/*.in | tr -d '@' | sort -u); do
  grep -qE "^${v}=" "$EXAMPLE" || fail "@$v@ has no ${v}= line in host-setup.env.example"
done
```

I confirmed (6) passes cleanly today: all 34 placeholders have an example
assignment, zero false positives (including the deliberately-empty
`DEV_MEMORY_MIN_GUARANTEED_CEILING=` and `DEV_BUILDKITD_CPU_QUOTA=""`).

---

## Non-blocking findings

**S1 — `ManagedOOMSwap=kill` was added beyond the design.** D-19 and the
handoff name `ManagedOOMMemoryPressure=kill` + a background-consistent limit;
`units/dev-gates.slice.in:44` also copies `ManagedOOMSwap=kill` from
`dev-background.slice.in`. oomd's swap path triggers on **host** swap usage
(`SwapUsedLimit`, 90 % by default) and then kills the descendant using the
most swap — i.e. precisely the lane that is using the `MemorySwapMax=32G`
this unit deliberately grants it, and on a signal D-6 explicitly rejects
("swap usage is never the gate; PSI is"). Defensible by the "a gate is
disposable" argument, but it is an un-asked-for addition to a tier whose
swap allowance is deliberately generous. See D2.

**S2 — the wizard does not host-scale the new tier, and its guaranteed-ceiling
suggestion is now systematically too generous.**
`host-setup/mdt-host-setup-wizard.py:374-407` (`propose_memory_tiers`) scales
interactive/background/buildkitd off this host's live `MemAvailable`/
`SwapTotal` and walks them; there is no `DEV_GATES_*` entry, so on any host
that is not the 16 GiB reference the operator ends up with three host-sized
tiers and one hardcoded 4G/6G/32G tier. The README discloses that half.
The undisclosed half is `propose_memory_min_guaranteed_suggestion`
(`:411-436`): it sums *"step d's three MemoryHigh figures"* as the earmarked
load, subtracts from `MemAvailable`, and proposes 5 % of the remainder as
`DEV_MEMORY_MIN_GUARANTEED_CEILING`. With a fourth tier now claiming a
`MemoryHigh`, the leftover is overstated by that tier and the suggested
ceiling is too high — and "too high" is the failure direction
`CGROUP-NOTES.md` warns about (surplus leaks to interactive/background). The
function's own comment ("30+30+25 = 85 %, deliberately below 100 %") is now
stale arithmetic. Prescription: add the gates keys to `propose_memory_tiers`
and walk them in the wizard, or at minimum add the gates `MemoryHigh` to the
earmark sum and fix the comment. No wizard test suite exists, so nothing
caught this.

**S3 — `AGENTS.md` (the estate's root guide) still declares only two
placement variables.** `/workspaces/vbpub/AGENTS.md:78-92` is where every
agent and tool learns which slice to use and states `$CGROUP_PARENT_DEV_BACKGROUND`
is "the shared tier for a test/gate/build container you spawn". A third
variable now exists and this file does not know it. `AGENTS.md` was not on
the handoff's forbidden list. One bullet (or an explicit "adoption deferred
to P5" line) would do it.

**S4 — the "same export path" is complete for the declaration, incomplete for
propagation, and the reason lives only in the REPORT.** The single tracked
declaration site is `templates/devcontainer.json` and it was updated
correctly (`.devcontainer/devcontainer.json` is untracked and per-instance,
so the template is the right and only place). But the variable's onward
journey stops there: `cmru/src/cmru/tester_gate.py:531` forwards only
`-e CGROUP_PARENT_DEV_BACKGROUND=` into spawned gate containers,
`shared-ramdisk-depot-manager/tools/cgroup-parent.sh:47` exports only the old
pair, and `run-gate-project/{SPEC.md:305,CONSUMERS.md:626}` document only the
old pair. The REPORT names this as each consumer's own future work — correct
and honest — but nothing repo-tracked says it, so a cmru or srdm maintainer
reading only the code sees an inconsistency with no explanation. Put the
consumer list + fallback rule in `host-setup/README.md`'s "dev-gates: why"
(it already has the right section for it).

**S5 — stale "dev-interactive + dev-background" phrasing left in the scripts.**
The README was correctly generalised ("all three children" → "every child"),
but the same claim survives in `scripts/mdt-apply-dev-caps.sh:4-9` and its
runtime log line `:148` ("covers dev-interactive.slice + dev-background.slice
combined"), `scripts/check.sh:53` ("dev.slice IO caps (shared by
dev-interactive + dev-background)"), and `units/dev.slice.in`'s own header
("Root parent for dev-interactive.slice + dev-background.slice +
dev-memory_min_guaranteed.slice"). The last one is a pre-existing gap
(`dev-buildkitd` was already missing) that this package widens.

**S6 — the REPORT's own `Tip:` is stale.** `run-gate-WAVE-RG55-P8-REPORT.md:3`
says `62927f1f`; the branch tip is `7bd2f03c`, and the commit table
(`:20-27`) omits `7bd2f03c` too. Harmless to the code (the tip commit changes
only the REPORT, so the gate verdict still covers everything shippable), but
the tip hash is the one fact a controller copies.

**S7 — overclaim on test assertion 3.** REPORT `:131-135` and LOG say the
assertion "confirms the RW-30/A1 revert restored the original opt-in-only
ancestor-chain behaviour **byte-for-byte**, not just by eyeball diff". It does
not: it asserts only that no `MemoryMin=` line renders when
`DEV_MEMORY_MIN_GUARANTEED_CEILING` is unset, and would pass just as happily
if the directive had been re-pointed at a different (also unset) variable.
The underlying fact is true — I established it independently by rendering
both trees and by `git diff` — but the test is not what establishes it.

**S8 — a typo'd env key fails with `unbound variable`, not a named
assertion.** `tests/test-render.sh:72-78` interpolates `${DEV_GATES_*}`
directly under `set -u`, so MUT-B died with
`line 72: DEV_GATES_MEMORY_HIGH: unbound variable`. Red is red, but the
message points at the test, not at the example. Use `${DEV_GATES_MEMORY_HIGH:?…}`
with a message, or assert the key's presence in the example first.

**S9 — `check.sh` reports the new tier but never checks it.** Folded into B3's
prescription 2; listed separately because "reports both slices' … effective
values" (handoff M2) *is* satisfied as written — the gap is that this
directory's convention for a fail-open invariant is a `fail`, not a printout.

**S10 — the operator sequences omit the devcontainer side.** `containerEnv`
changes only take effect on container **recreate**; until the devcontainer is
rebuilt, `CGROUP_PARENT_DEV_GATES` is absent from every tool's environment
(confirmed: this session's own devcontainer exports only the old two). Both
sequences end at `mdt-host-check.sh`. One line — "then rebuild/recreate your
devcontainer so `CGROUP_PARENT_DEV_GATES` reaches the tools inside it" —
closes the loop. Related: D-24's fallback is keyed on the variable being
*unset*, but the template sets it unconditionally, so the fallback actually
protects "devcontainer not yet rebuilt", **not** "host not yet upgraded";
`README.md:126-131`'s "nothing breaks on a host that has not re-run
install.sh yet" is therefore true only by accident (the slice gets implicitly
auto-created, unbounded, by docker). Worth one honest sentence.

**S11 — a fourth same-weight sibling changes the existing ratios.**
`CPUWeight=20`/`IOWeight=10` are identical to `dev-background`'s, so whenever
a gate and a long-running stack are both runnable the non-interactive pair
now holds `40` against interactive's `200` instead of `20` — and on the IO
side `20` against `100` under `dev.slice`'s single absolute ceiling. That is
per the design's own table, so it is not a defect; but this directory's rule
is "express IO ratios by lowering the loser, never raising the winner"
(`README.md`, `CGROUP-NOTES.md §BFQ`), and adding a same-weight sibling is
the "raise the winner" move in disguise. Neither the unit comment nor the
README mentions it. See D3.

**S12 — `mdt-slice-audit.py:10-11`'s header** ("dev.slice sets no
MemoryMin/MemoryLow at all, and dev-background.slice sets neither either") now
also applies to `dev-gates.slice`. Behaviour is fine — the audit scans
`CG/dev.slice` recursively, so the new tier is covered automatically — only
the comment is incomplete.

**S13 — no mdt-side record of the change.** No `TODO.md` line, no `CHANGES.md`
(none exists), no backlog entry (mdt has no `nyxloom-trove/backlog/`). The
REPORT discloses all three and the reasoning (TODO.md is blacklisted by the
handoff because another session holds uncommitted edits to it) is sound, and
the worktree's own `TODO.md` could have been edited without touching the
shared checkout — but it would then conflict at merge. Controller call, see D5.

**S14 — the test writes into `$TMPDIR` (`/tmp`), not a scoped directory.**
Harmless today (`mktemp -d` + `trap … EXIT`), but combined with B2 it means
an over-run has the whole filesystem in front of it. Setting `TMPDIR="$TMP"`
costs one line.

**S15 — `ciu/docs/SPEC-V8.md:828` item (5) still reads "The daemon itself
moves to a new `dev-infra.slice`".** RW-30 ruled that item corrected; it is
not. Out of P8's scope (`ciu/` is on its forbidden list and the branch
correctly does not touch it) — flagged for the controller, who owns that file.

---

## Decision asks

**D1 (attached to B1) — should `dev-gates.slice` get a reactive per-container
`MemoryMax`, and at what value?** Adding it at the shared `DEV_CAP_MEMORY_MAX=1G`
would cap every unlabelled lane at 1 G — tighter than the ~2 G of headroom
the 2026-08-04 incident says a gate already needed, so it would re-create the
motivating problem inside the slice built to fix it. Options: a separate
`DEV_GATES_CAP_MEMORY_MAX` (suggest the tier's own `MemoryHigh`, 4G, as the
default); or a deliberate exemption because P5 will pass per-lane `--memory`
at create (D-20) — in which case the window between this merge and P5 is
unprotected, and that should be stated.

**D2 (S1) — keep `ManagedOOMSwap=kill` on the gates slice?** It is not in
D-19's list and it kills on a signal D-6 rejects, in a tier whose swap
ceiling is deliberately 32 G.

**D3 (S11) — are `CPUWeight=20`/`IOWeight=10` right for a *fourth* sibling?**
The design set them by analogy with `dev-background`; the estate's own BFQ
rule argues for splitting background's share rather than duplicating it.
"Measure first" (design §7) applies, but the choice should be conscious.

**D4 (S3/S4) — is `AGENTS.md` (and `run-gate-project/CONSUMERS.md`, `SPEC.md`)
updated now or at P5?** Either is fine; silence is not.

**D5 (S13) — who records this in mdt,** given `TODO.md` is held by another
session's uncommitted edits?

---

## Claims I could not verify

1. **Anything about real-host behaviour.** Per the binding HOST LOAD rule I
   never ran `install.sh`, never called `systemctl`, never wrote under `/etc`,
   and never inspected a real `dev-gates.slice` cgroup. So: that
   `systemctl start dev-gates.slice` creates the cgroup and that `check.sh`'s
   effective-value reads resolve on a live host are **unverified** (they are
   structurally identical to the existing siblings, which is why I did not
   treat it as a finding).
2. **Whether `systemd-oomd` is installed and running on the target host.**
   Every `ManagedOOM*` directive in this unit is a silent no-op without it
   (the unit comment says so). `install.sh` apt-installs it, but I could not
   confirm the end state.
3. **The 2026-08-04 finding itself.** `soulmask-memory-pressure-findings.md`
   is a session-memory file, not repo-tracked; I took the "~4 GiB dstdns
   stack / ~2 GiB of headroom" numbers from the design doc's §1 row, which is
   what the README and env-example comments cite. The *reasoning chain* is
   consistent; the underlying measurement I did not re-derive.
4. **REPORT "Checkpoint clause — not triggered".** No external evidence
   available to a reviewer.
5. **That no OTHER estate consumer hardcodes a gate slice name** outside the
   `CGROUP_PARENT_DEV_*` grep I ran (`git grep` at `11ac5d67` and `7bd2f03c`,
   excluding `.worktrees/`). A consumer that hardcodes `dev-background.slice`
   as a literal without the variable would not have shown up in S4's list.

---

## Evidence trail

Everything above was produced from `/workspaces/vbpub/.worktrees/mdt-dev-slices`
(read-only) and a scratch copy under this session's scratchpad; mutations were
applied to the scratch copy only and reverted (`diff -r` against the worktree
confirmed identical after every mutation batch). The shared checkout's dirty
mdt files were never touched (`git status --porcelain` before and after: the
same 7 files). No commits were made to `mdt-dev-slices`. Host memory PSI was
checked before starting (`full avg10 = 1.47`, under the 5 back-off threshold)
and everything ran under `nice -n 19`.
