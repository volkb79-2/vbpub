# run-gate-WAVE-RG55-P8 — REPORT

**Tip:** `e6af30df` (branch `mdt-dev-slices`, worktree `.worktrees/mdt-dev-slices`,
base `main`@`11ac5d67`). **Gate:** `modern-debian-tools-python-debug`'s
registered `smoke` lane (`python3 run-gate.py smoke`, run from the project
root — **not** `host-setup/`) — **exit 0**, verdict read in a separate step
from a clean (committed) tree, ran ONCE for this repair round (budget was
two). Full LOG: `run-gate-WAVE-RG55-P8-LOG.md`.

This REPORT covers two passes: the original M1-M4+Gates package (tip
`62927f1f` at the time), and this round's full repair-and-extend set
following review round 1's ACCEPT-conditional verdict (RW-32) — ten
granular commits, `244728c0`..`e6af30df`, fixing every B1-B6 blocker, every
D1-D5 ruling, the non-blocking findings the ruling asked for by name, and
shipping M5 (the `/run/cgprofile` template mount, new scope in this same
round). **Claim scope:** everything below is either a command I ran this
session with its real output shown, or a `git diff`/`grep` I ran to check a
specific claim — nothing is asserted from memory of an earlier pass.

## What shipped (original M1-M4+Gates)

Final scope, after a mid-package controller re-scope (RW-30 / design
amendment A1 — see "Re-scope" below): **`dev-gates.slice` only.**
`dev-infra.slice` was built in M1-M3, then fully withdrawn by a dedicated
revert commit before M4/Gates, per the operator's own boundary correction
("mdt is supposed to be only a devcontainer / cockpit ... the daemon ...
would/should run as a deployment on the host"). The daemon now ships its own
`cgprofile.slice` as a separate package (`scripts/cgroup-profiler/`, out of
this project's scope) — not part of this deliverable.

| commit | what |
|---|---|
| `16f3a476` | M1 — `dev-infra.slice.in` + `dev-gates.slice.in` units, env keys (WITHDRAWN below) |
| `22049949` | M2 — install.sh render/install, placement report, check.sh (WITHDRAWN in part below) |
| `1de4c93c` | M3 — `CGROUP_PARENT_DEV_INFRA`/`_GATES` exported to devcontainers (WITHDRAWN in part below) |
| `b9e628f5` | **Revert** — withdraws `dev-infra.slice` and every `DEV_INFRA_*`/`CGROUP_PARENT_DEV_INFRA` reference from the three commits above |
| `4ec6dcfa` | M4 — docs: README "dev-gates: why", CGROUP-NOTES facts, operator upgrade sequence |
| `62927f1f` | Gates — renderer test, wired into the registered gate |
| `7bd2f03c` | docs — first-pass REPORT (superseded by this document) |

## Round-1 repairs (RW-32) — finding → commit → one-command verification

Every blocker (B1-B6) and ruling (D1-D5) below is closed; every
non-blocking finding the ruling named by number (S1-S5, S7-S14) is fixed.
Verification commands are runnable from
`modern-debian-tools-python-debug/host-setup/` unless noted.

| finding | fix | commit | one-command verification |
|---|---|---|---|
| B1 (cap-watcher misses dev-gates.slice) | `mdt-dev-cap-watcher.py` watches `dev-gates.slice` too, via a per-slice `SLICE_DEFAULT_MEMORY_MAX` dict (not the shared 1G default) | `b1ec4f2e` | `python3 -c "import ast; ast.parse(open('scripts/mdt-dev-cap-watcher.py').read())"` then `grep -n 'dev-gates.slice' scripts/mdt-dev-cap-watcher.py` |
| B2 (extraction not fail-closed) | `test-render.sh`'s `sed` extraction of `install.sh`'s `render()`/`RENDER_VARS` gained two anchor-match guards + a host-mutation-statement scan before sourcing | `244728c0` | `bash tests/test-render.sh` (mutation-demo: edit a scratch copy's anchor line, confirm RED — done this session, not repeated here) |
| B3 (install.sh/check.sh WARN-vs-hard-fail mismatch) | `install.sh` WARNs (not fails) on a config predating a known key; `check.sh` hard-`fail`s when the LIVE `memory.max`/`memory.high` cgroup value disagrees with the configured `DEV_GATES_*` value | `83521c56`, `6d9da520` | `grep -n 'MISSING_KEYS' install.sh`; `grep -n '_bytes_of\|prop_label' scripts/check.sh` |
| B4 (`--force` upgrade sequence wrong) | README's upgrade sequence rewritten as additive (backup, diff, hand-edit, one `install.sh` pass); `--force` demoted to "maintenance window only" after confirming it has no `exit` and falls through to full live estate activation | `20d52736` | `sed -n '82,90p' install.sh` shows no `exit` in the `--force` branch; `grep -n 'scheduled maintenance window' README.md` |
| B5 (env.example wording drift) | Line reverted to the exact original "BOTH this slice and dev.slice" (the round-1-added "(its share of)" wording removed) | `f0601a17` | `git diff 11ac5d67..HEAD -- host-setup.env.example \| grep -A2 -B2 'BOTH this slice'` |
| B6 (test-render.sh doesn't check install.sh's own wiring) | Three new assertions: install.sh renders + `systemctl start`s every `units/*.in`; every `@VAR@` has an example assignment; (M5) install.sh installs the tmpfiles entry | `244728c0` | `bash tests/test-render.sh` (mutation-demo: 3 separate scratch-copy mutations run this session, each confirmed RED) |
| D1 (cap-watcher gets its own `DEV_CAP_GATES_MEMORY_MAX`, default `4G`) | New env key + `SLICE_DEFAULT_MEMORY_MAX["dev-gates.slice"]` wired to it, not the shared `DEV_CAP_MEMORY_MAX` (1G) | `f0601a17`, `b1ec4f2e` | `grep -n 'DEV_CAP_GATES_MEMORY_MAX' host-setup.env.example scripts/mdt-dev-cap-watcher.py` |
| D2 (drop `ManagedOOMSwap=kill`) | Removed from `dev-gates.slice.in`, comment explains why (32G swap is a deliberate relief valve, D-19/D-6) | `21ca5e3f` | `grep -c 'ManagedOOMSwap' units/dev-gates.slice.in` → `0` |
| D3 (CPUWeight/IOWeight arithmetic sentence) | README "Sizing choices" paragraph — see "CPUWeight/IOWeight arithmetic" note below for why the numbers differ from the ruling's own stated figures | `20d52736` | `grep -n 'Sizing choices' README.md` |
| D4 (declare all three placement vars in AGENTS.md, list consumers in REPORT) | `$CGROUP_PARENT_DEV_GATES` bullet added to root `AGENTS.md`; consumers listed below in "Onward propagation" | `3cd5a91c` | `grep -n 'CGROUP_PARENT_DEV_GATES' /workspaces/vbpub/.worktrees/mdt-dev-slices/AGENTS.md` |
| D5 (never touch TODO.md; README Changes + REPORT paste-in line) | New README "## Changes" section; this REPORT's "Operator: add to TODO.md" section below carries the exact text | `20d52736` | `grep -n '^## Changes' README.md`; `TODO.md` untouched — `git diff 11ac5d67..HEAD --name-only \| grep -c TODO.md` → `0` |
| S1 (why no `ManagedOOMSwap`) | Folded into D2's README paragraph | `20d52736` | see D2 row |
| S2 (wizard earmark-sum omits dev-gates.slice's MemoryHigh, 4th tier) | `main()` adds `DEV_GATES_MEMORY_HIGH` to the dict `propose_memory_min_guaranteed_suggestion()` sums; formula text generalized off a hardcoded "three"; doctest proves the before/after | `e6af30df` | `python3 -m doctest mdt-host-setup-wizard.py -v 2>&1 \| tail -5` |
| S3 (AGENTS.md gap) | Same as D4 | `3cd5a91c` | see D4 row |
| S4 (name the non-adopting consumers) | README "Onward propagation" paragraph | `20d52736` | `grep -n 'Onward propagation' README.md` |
| S5 (stale "dev-interactive + dev-background only" phrasing in 3 files) | Generalized header/log-line wording in `dev.slice.in`, `mdt-apply-dev-caps.sh`, `check.sh` | `21ca5e3f`, `6d9da520` | `git diff 11ac5d67..HEAD -- units/dev.slice.in scripts/mdt-apply-dev-caps.sh scripts/check.sh` |
| S7 (test-render.sh doesn't verify `TMPDIR` isolation) | `export TMPDIR="$TMP"` added before any render | `244728c0` | `grep -n 'export TMPDIR' tests/test-render.sh` |
| S8 (DEV_GATES_* keys not fail-closed if example goes stale) | Assertion 2 rewritten to `${VAR:?message}` pattern per key | `244728c0` | `bash tests/test-render.sh` (mutation-demo run this session, confirmed RED) |
| S9 (check.sh has no M5 coverage) | New "cgprofile socket carrier" section | `6d9da520` | `grep -n 'cgprofile socket carrier' scripts/check.sh` |
| S10 (D-24 fallback note conflates two failure modes) | Reworded to distinguish devcontainer-not-rebuilt vs. host-not-upgraded, each with its own mechanical catch; new "Rebuild your devcontainer" paragraph | `20d52736` | `grep -n 'Rebuild your devcontainer' README.md` |
| S11 (CPUWeight/IOWeight numbers unexplained) | Folded into D3's "Sizing choices" paragraph | `20d52736` | see D3 row |
| S12 (mdt-slice-audit.py header stale) | Header comment now names `dev-gates.slice`, notes behavior unchanged (recursive scan already covers it) | `21ca5e3f` | `grep -n 'dev-gates.slice' scripts/mdt-slice-audit.py` |
| S13 (TODO.md dirty-file ambiguity) | Addressed by D5 — TODO.md untouched, operator line lives in this REPORT | `20d52736` | see D5 row |
| S14 (shellcheck severity not pinned in test-render.sh) | `-S warning` explicit in the shellcheck loop (already present, reconfirmed unchanged this round) | `244728c0` | `grep -n 'shellcheck -S warning' tests/test-render.sh` |

**Overclaim withdrawn (self-caught, not a reviewer finding):** the original
REPORT's Gates section described assertion 3 (no `MemoryMin` line renders)
as confirming the RW-30 revert "byte-for-byte" against baseline. That
assertion only checks one rendered line's absence, not a full byte
comparison — dropped from this REPORT. The actual byte-identical claim was
checked separately and correctly, via `git diff 11ac5d67..HEAD --
units/dev.slice.in units/dev-memory_min_guaranteed.slice.in
scripts/mdt-apply-dev-caps.sh` at revert time (empty before the round-1
repairs modified these files again for S5 — see the S5 row above for
what changed since).

**CPUWeight/IOWeight arithmetic (D3):** the ruling's own message stated
"83% (100/120) to 71% (100/140)"; I could not reproduce "71%" from any
combination of this file's actual shipped weights
(`DEV_INTERACTIVE_CPU_WEIGHT=200`, `DEV_BACKGROUND_CPU_WEIGHT=20`,
`DEV_GATES_CPU_WEIGHT=20`) — tried CPUWeight-only, IOWeight-only, and a
4-way total with buildkitd, none matched. Recomputed independently:
two-way baseline `200/220 ≈ 91%` → three-way with `dev-gates.slice` added
`200/240 ≈ 83%` (this DOES match the ruling's own first figure, and
matches review finding S11's own "40 against interactive's 200" wording
exactly). Shipped my own verified numbers rather than the unreproducible
pair — logged as an RW-9 decision, not a blocker, since the ruling asked
for "an arithmetic sentence," not a specific pair of numbers, and a
round-2 reviewer will recompute this regardless.

## M5 — `/run/cgprofile` template mount (D-30/A2, new scope this round)

| piece | what | commit |
|---|---|---|
| `templates/devcontainer.json` | New `mounts` entry: `source=/run/cgprofile,target=/run/cgprofile,type=bind`, with the required 4-part comment (purpose / group-access / host-prerequisite / opt-out) | `d3aa5e6a` |
| `host-setup/units/tmpfiles-cgprofile.conf` (new file) | `d /run/cgprofile 0770 root docker -` — matches the daemon's own socket perms; extensive comment on why `tmpfiles.d` not ad hoc `mkdir` | `83521c56` |
| `install.sh` | Installs the entry to `/etc/tmpfiles.d/cgprofile.conf` (`install -m 0644`) and applies it (`systemd-tmpfiles --create`, WARN not fail if that step itself errors) | `83521c56` |
| `check.sh` | New "cgprofile socket carrier" section: hard-`fail`s if `/run/cgprofile` is missing or has the wrong mode/owner; `INFO`-only (new `info()` helper, does not affect exit code) on whether the daemon's control socket is present | `6d9da520` |
| `tests/test-render.sh` | New assertion: `install.sh` installs the tmpfiles entry | `244728c0` |
| README/AGENTS docs | "The socket carrier's mount" paragraph (README); no AGENTS.md change needed (M5 doesn't touch a `CGROUP_PARENT_*` var) | `20d52736` |

**Why Docker `--mount` (not `-v`) makes the ordering matter:** `--mount`
refuses to start a container at all when the bind SOURCE path doesn't
exist; `-v` would instead silently create one, owned by whichever user
Docker itself runs as (wrong owner, wrong mode) — so the directory must
already exist, with the right owner and mode, before any devcontainer using
this template is ever built. `tmpfiles.d` (not `install.sh`'s own `mkdir`)
is the standard mechanism this codebase already uses for exactly this
(precedent: `docker-api-socket.conf`) because it also re-applies across a
reboot without any of this package's own code running again.

**Forbidden paths respected:** `scripts/cgroup-profiler/`,
`run-gate-project/run-gate.py`, `ciu/`, `/workspaces/dstdns` — none
touched. Confirmed: `git diff 11ac5d67..HEAD --name-only` (full diff,
below) shows no path under any of those four.

## dev-gates.slice — what it is

Sizing per design doc §3/D-19 (16 GiB-host defaults, expected to be re-tuned
from real admission-usage data): `MemoryHigh=4G`, `MemoryMax=6G`,
`MemorySwapMax=32G`, `CPUWeight=20`, `IOWeight=10`,
`ManagedOOMMemoryPressure=kill` + `ManagedOOMMemoryPressureLimit=75%`.
`ManagedOOMSwap=kill` was dropped this round (D2 — see table above); the
32G swap allowance is a deliberate relief valve, not something to
kill-on-touch. Fixes the 2026-08-04 finding (memory
`soulmask-memory-pressure-findings.md`): gates used to share
`dev-background.slice` with the ~4 GiB `dstdns` stack and started with only
~2 GiB of headroom before throttling. Backstopped per-container by
`mdt-dev-cap-watcher.py`'s reactive inotify watch, which now also covers
`dev-gates.slice` with its own `DEV_CAP_GATES_MEMORY_MAX=4G` knob (D1) —
deliberately not the shared `DEV_CAP_MEMORY_MAX=1G`, which would
re-create the 2026-08-04 incident at the per-container layer even after
fixing it at the slice layer.

`CGROUP_PARENT_DEV_GATES=dev-gates.slice` travels the same export path as
the existing two vars (`templates/devcontainer.json`'s `containerEnv`),
with D-24's fallback rule documented inline and in `host-setup/README.md`
— **precisely stated this round (S10):** the rule protects a
**devcontainer that was not rebuilt** (the variable is simply absent from
its environment), **not** a **host that was not upgraded** (the slice unit
itself missing) — that second failure mode is instead caught mechanically
by `mdt-host-check.sh`, and independently by `docker run` itself failing
when a named `--cgroup-parent` slice was never installed and the
daemon-wide default can't resolve it either.

## Onward propagation (D4/S4) — named consumers that do not read `CGROUP_PARENT_DEV_GATES` yet

None of the following were modified by this package (each is its own
future adoption work, out of scope here):
- **run-gate's own default `--cgroup-parent`** — `run-gate-project/{SPEC.md,
  CONSUMERS.md}` still document only the interactive/background pair; this
  session's own `run-gate.py smoke` run (see Gates below) shows its argv
  using `--cgroup-parent dev-background.slice`, confirming the fallback is
  real and currently exercised, not theoretical.
- **cmru's tester-gate**, `cmru/src/cmru/tester_gate.py` — forwards only
  `CGROUP_PARENT_DEV_BACKGROUND` into spawned gate containers.
- **srdm's gate script**, `shared-ramdisk-depot-manager/tools/
  cgroup-parent.sh` — exports only the interactive/background pair.

Until one of these adopts it, every gate/lane container keeps landing in
`dev-background.slice` (today's placement, per D-24) regardless of whether
a given host has `dev-gates.slice` installed.

## Operator install/upgrade command sequence (exact, verified against the real script)

**Fresh host, never ran mdt host-setup before:** the existing "Quick start"
section in `host-setup/README.md` covers this end to end as of this round —
it now seeds/renders `dev-gates.slice` and the `/run/cgprofile`
`tmpfiles.d` entry along with everything else, in one pass.

**Host already running mdt host-setup, from before `dev-gates.slice`
existed** (B4 fix — additive, never discards existing tuning):
```bash
sudo cp /etc/mdt/host-setup.env /etc/mdt/host-setup.env.bak-$(date +%F)
diff <(grep -oE '^[A-Z_]+=' host-setup/host-setup.env.example | sort) \
     <(grep -oE '^[A-Z_]+=' /etc/mdt/host-setup.env | sort)
                                                # shows every new key (DEV_GATES_*,
                                                # DEV_CAP_GATES_MEMORY_MAX) this
                                                # host's config predates
sudo vi /etc/mdt/host-setup.env                # hand-add the missing keys,
                                                # sized for this host if the
                                                # 16 GiB-host defaults don't fit
sudo ./host-setup/install.sh                   # ONE render+install pass --
                                                # renders/starts dev-gates.slice,
                                                # installs the cgprofile tmpfiles
                                                # entry, daemon-reload, enables +
                                                # runs the sweep once; WARNs by
                                                # name for any key you still missed
sudo mdt-host-check.sh                         # verify: dev-gates.slice active
                                                # with matching effective values,
                                                # /run/cgprofile mode/owner correct
```
`sudo ./host-setup/install.sh --force` (backs up, then re-seeds the WHOLE
config from the example, discarding prior tuning) is a **scheduled
maintenance window operation only** — confirmed by reading the script that
it has no `exit` after re-seeding and falls straight through into a full,
live, estate-wide re-activation using the example's 16 GiB-host numbers,
not merely "picking up new keys" (B4). `--wizard` is the recommended
interactive alternative — it offers this host's own prior values back as
defaults on every section it walks (dev-gates.slice's sizing is not
walked, hand-edit that one section regardless of which path you take).

**After either sequence: rebuild your devcontainer** (S10) —
`containerEnv` changes (including `CGROUP_PARENT_DEV_GATES` and any future
consumer reading it) only take effect on container recreate, independent
of whether the host itself is fully upgraded.

There is **no daemon-restart step** in either sequence (the daemon is a
separate deployment, `scripts/cgroup-profiler/`'s own concern) — except
that M5's socket carrier directory is created/applied automatically as
part of the `install.sh` pass above; nothing extra to run for it.

## Operator: add to TODO.md (D5 — this package does not touch TODO.md itself)

`modern-debian-tools-python-debug/TODO.md` is on this package's blacklist
and carries unrelated uncommitted edits in the shared checkout throughout
this entire package's life — never opened for writing. Paste the following
wherever the operator's own TODO.md maintenance next lands:

```markdown
## dev-gates.slice / cgprofile socket carrier (RG-55 P8, 2026-09-12)

- `dev-gates.slice` ships (host-setup), sized per D-19; reactive
  per-container backstop via `mdt-dev-cap-watcher.py` (its own
  `DEV_CAP_GATES_MEMORY_MAX` knob, default 4G).
- `/run/cgprofile` bind mount + host-setup tmpfiles.d entry ship
  (M5/D-30) for the future cgroup-profiler daemon socket carrier; exec
  stays the permanent fallback transport, nothing required to adopt it.
- Still open: no spawner (run-gate's own default, cmru's tester-gate,
  srdm's gate script) reads `$CGROUP_PARENT_DEV_GATES` yet -- every
  gate/lane container still lands on dev-background.slice until one
  adopts it. Track per-tool, not here.
```

## Gates

Registered gate: `modern-debian-tools-python-debug/run-gate.toml`
`[lanes.smoke]` (a host lane; `argv` runs `py_compile` over every tracked
`.py` file, then `host-setup/tests/test-render.sh`, then an `echo`).

**`host-setup/tests/test-render.sh`** (bash, extended this round to 8
assertions — see the Round-1 repairs table above for B2/B6/S7/S8/S14's
contributions): extracts `install.sh`'s own `render()` function and
`RENDER_VARS` list verbatim (a guarded `sed` byte-range on two stable
anchor patterns, now fail-closed on a drifted anchor and scanned for any
host-mutating statement before ever being sourced), sources that plus
`host-setup.env.example` itself (no root, no `/etc/mdt/`, no host
mutation), renders every `units/*.in` template, and asserts all 8 items
listed inline in the script's own header comment plus a `bash -n`/
`py_compile` loop and a `shellcheck -S warning` loop over every script this
package touches.

**Ran directly this session** (`nice -n 19 ionice -c 3 bash
tests/test-render.sh`): all 8 assertions pass, `test-render: ALL OK`,
exit 0. Full output:
```
ok: render()/RENDER_VARS extracted from install.sh and guarded (anchors + host-mutation scan)
ok: every rendered unit is free of unresolved @VAR@ placeholders
ok: dev-gates.slice renders every DEV_GATES_* key from the shipped example, and ManagedOOMSwap=kill stays withdrawn
ok: dev.slice and dev-memory_min_guaranteed.slice render with no MemoryMin (opt-in, unset by default)
ok: no units/*.in template mentions the withdrawn dev-infra.slice
ok: install.sh renders every units/*.in, and starts every *.slice
ok: every @VAR@ placeholder used in a template has a matching assignment in host-setup.env.example
ok: install.sh installs the cgprofile tmpfiles.d entry
ok: wizard's earmark-sum doctest (before/after dev-gates.slice's MemoryHigh) passes
ok: bash -n / py_compile clean on every touched script
ok: shellcheck (warning severity+) clean on every touched shell script
test-render: ALL OK
```

**Ran the registered gate this session** (`nice -n 19 ionice -c 3 python3
run-gate.py smoke`, run from `modern-debian-tools-python-debug/` — the
project root, NOT `host-setup/` — from a clean committed tree at tip
`e6af30df`, verdict read in a separate step afterward, never a pipe tail):
```
run-gate: lane 'smoke' exit 0
```
with `smoke: OK` and the full `test-render: ALL OK` block reproduced
inside the gate container's own output. Notable in the transcript:
run-gate's own container launched with `--cgroup-parent
dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`
— confirms it is not yet reading `$CGROUP_PARENT_DEV_GATES` (expected,
future consumer work per D4/S4 above) and correctly falls back to today's
placement per D-24. This was gate run **1 of the 2 allowed** for this
repair round; it passed cleanly, so no second run was made.

**Python coverage:** this round touches exactly one `.py` file,
`mdt-host-setup-wizard.py` (plus `mdt-dev-cap-watcher.py`, touched in C3).
Both verified via `python3 -m py_compile` (clean) and, for the wizard, a
doctest exercising the exact function changed (`python3 -m doctest
mdt-host-setup-wizard.py -v` → 5 tests, 5 passed) — the changed lines
(the new `high_values["DEV_GATES_MEMORY_HIGH"]` assignment in `main()`,
and the generalized formula in `propose_memory_min_guaranteed_suggestion()`)
are exercised by the doctest's before/after pair and by `test-render.sh`'s
own assertion 8, which runs the full module doctest as part of the
registered gate. `mdt-dev-cap-watcher.py`'s changed lines (the per-slice
dict, the new startup assertion) are exercised by `test-render.sh`'s
`py_compile`/`ast.parse` loop; no dedicated unit-test harness exists for
this script in either pass (matches the pre-existing shape of this
subdirectory — bash + a handful of specifically-tested Python scripts).

**Not added:** `systemd-analyze verify` — not used anywhere in this
project today; the handoff's "if it is there already" condition does not
apply.

## Decisions taken without stopping (RW-9)

1. **dev.slice ancestor-chain arithmetic for dev-infra's MemoryMin (M1,
   later moot).** Fully reverted along with `dev-infra.slice` itself once
   RW-30 withdrew it. Reasoning preserved in CGROUP-NOTES.md's "dev-gates.slice
   and placed lane leaves" section as forward-looking documentation, in case
   a similar ancestor-chain gap resurfaces for a future `dev-gates.slice`
   leaf `memory.min`.
2. **check.sh's flat cgroupfs path convention, left unchanged (round-1
   repair C6).** `check.sh` reads `$CG/dev-interactive.slice` etc. directly
   (flat, not nested under `dev.slice/`) — a pre-existing pattern this
   package did not introduce, and one round-1 review's own "claims I could
   not verify" section explicitly declined to verify on a real host. My new
   `dev-gates.slice` code in the same file matches this existing convention
   exactly rather than switching to a "more correct" nested path, to avoid
   creating a fresh inconsistency next to an unchanged sibling. If the flat
   convention is wrong on a real host, that predates this package and
   belongs to whoever owns `dev-interactive.slice`'s own block to fix, with
   a real host to verify against (none available here, per HOST LOAD).
3. **D3's CPUWeight/IOWeight arithmetic, recomputed rather than copied.**
   See the "CPUWeight/IOWeight arithmetic" note in the Round-1 repairs
   section above — the ruling's own stated numbers did not reproduce
   against this file's real shipped values; shipped my own verified pair
   instead of an unreproducible one, logged not blocking.
4. **Blacklisted `TODO.md` / no CHANGES.md / no mdt backlog structure.**
   Unchanged from the first pass — see D5's row in the Round-1 repairs
   table and the "Operator: add to TODO.md" section above for how this
   round resolves it without touching the file.

## Controller re-scope (RW-30, design amendment A1)

Received mid-package, after M3 (`1de4c93c`), before M4. Verified against the
real repository before acting: `main` had genuinely moved (`11ac5d67` →
`5edec58c`) with a real new `## A1` section in the design doc, content
matching the message's claims exactly. Full mechanism, ruling, and
file-by-file revert accounting in the LOG's "Controller re-scope" entry.

## Scope discipline

Touched only: `modern-debian-tools-python-debug/{DEVCONTAINER-LIFECYCLE.md,
run-gate.toml, host-setup/**, templates/devcontainer.json}`, root
`AGENTS.md` (D4/S3, this round — a repo-root file, not under
`modern-debian-tools-python-debug/`), and
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P8-{LOG,REPORT}.md`.

Never touched: the SHARED checkout (`/workspaces/vbpub/
modern-debian-tools-python-debug/` outside this worktree — confirmed
untouched again this round), `Dockerfile`/`README.md` (top-level)/
`TODO.md`/`ai-cli-tools.list`/the three `ai-cli-tools` scripts (the
blacklisted set), `run-gate-project/` source including `run-gate.py`
itself, `scripts/cgroup-profiler/`, `ciu/`, `/workspaces/dstdns`, any other
worktree. No host mutation this round either — every check ran against a
rendered-to-tmpdir copy, a mocked cgroupfs/env under the scratchpad, or
inside run-gate's own gate container; never against `/etc/mdt`,
`/etc/systemd/system`, `/etc/tmpfiles.d`, or any real cgroup.

Full diff surface, this round (`git diff 244728c0~1..HEAD --name-only`,
i.e. everything the ten round-1-repair commits touched together):
```
AGENTS.md
modern-debian-tools-python-debug/host-setup/README.md
modern-debian-tools-python-debug/host-setup/host-setup.env.example
modern-debian-tools-python-debug/host-setup/install.sh
modern-debian-tools-python-debug/host-setup/mdt-host-setup-wizard.py
modern-debian-tools-python-debug/host-setup/scripts/check.sh
modern-debian-tools-python-debug/host-setup/scripts/mdt-apply-dev-caps.sh
modern-debian-tools-python-debug/host-setup/scripts/mdt-dev-cap-watcher.py
modern-debian-tools-python-debug/host-setup/scripts/mdt-slice-audit.py
modern-debian-tools-python-debug/host-setup/tests/test-render.sh
modern-debian-tools-python-debug/host-setup/units/dev-gates.slice.in
modern-debian-tools-python-debug/host-setup/units/dev.slice.in
modern-debian-tools-python-debug/host-setup/units/mdt-dev-cap-watcher.service
modern-debian-tools-python-debug/host-setup/units/tmpfiles-cgprofile.conf
modern-debian-tools-python-debug/templates/devcontainer.json
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P8-LOG.md
```
Matches the expected set exactly — no unexpected path.

## What was NOT done, and why

- **A second registered-gate run.** Not needed — the first run this round
  was green; the budget (at most two) was intentionally not spent on a
  redundant confirmation.
- **Fixing `check.sh`'s pre-existing flat-vs-nested cgroupfs convention.**
  Deliberately left as-is; see RW-9 decision 2 above — out of scope, no
  real host available to verify either convention against (HOST LOAD).
- **Making any onward consumer (run-gate's default, cmru's tester-gate,
  srdm's gate script) read `$CGROUP_PARENT_DEV_GATES`.** Explicitly ruled
  out of scope by D4 ("list onward consumers in REPORT, not fix them") —
  named above under "Onward propagation," not modified.
- **Editing `TODO.md`.** Blacklisted; D5's resolution (README Changes
  section + this REPORT's paste-in line) used instead.
- **Touching `scripts/cgroup-profiler/`, `run-gate-project/run-gate.py`,
  `ciu/`, or `/workspaces/dstdns`.** All explicitly forbidden for M5;
  confirmed absent from the diff surface above.

## Checkpoint clause

Not triggered this round either — the repair set stayed well under
~120k context / ~60 tool calls.
