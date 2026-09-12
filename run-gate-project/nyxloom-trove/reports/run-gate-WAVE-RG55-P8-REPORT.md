# run-gate-WAVE-RG55-P8 — REPORT

**Tip:** `62927f1f` (branch `mdt-dev-slices`, worktree `.worktrees/mdt-dev-slices`,
base `main`@`11ac5d67`). **Gate:** `modern-debian-tools-python-debug`'s
registered `smoke` lane (`run-gate.py smoke`) — **exit 0**, verdict read in a
separate step from a clean (committed) tree. Full LOG:
`run-gate-WAVE-RG55-P8-LOG.md`.

## What shipped

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
| `b9e628f5` | **Revert** — withdraws `dev-infra.slice` and every `DEV_INFRA_*`/`CGROUP_PARENT_DEV_INFRA` reference from the three commits above, byte-verified against baseline |
| `4ec6dcfa` | M4 — docs: README "dev-gates: why", CGROUP-NOTES facts, operator upgrade sequence |
| `62927f1f` | Gates — renderer test, wired into the registered gate |

**Net effect (what actually exists at the tip):** exactly one new slice,
`units/dev-gates.slice.in`, its env keys, its render/install/check wiring,
its devcontainer export, its docs, and a new renderer test. `git diff
11ac5d67..HEAD --name-status` shows no residue from the withdrawn
`dev-infra.slice` — it was added and removed within this same branch, so it
does not appear in the final diff at all.

## dev-gates.slice — what it is

Sizing per design doc §3/D-19 (16 GiB-host defaults, expected to be re-tuned
from real admission-usage data): `MemoryHigh=4G`, `MemoryMax=6G`,
`MemorySwapMax=32G`, `CPUWeight=20`, `IOWeight=10`,
`ManagedOOMMemoryPressure=kill` + `ManagedOOMMemoryPressureLimit=75%` +
`ManagedOOMSwap=kill` (gates are disposable, same treatment as
`dev-background.slice`). Fixes the 2026-08-04 finding (memory
`soulmask-memory-pressure-findings.md`): gates used to share
`dev-background.slice` with the ~4 GiB `dstdns` stack and started with only
~2 GiB of headroom before throttling.

`CGROUP_PARENT_DEV_GATES=dev-gates.slice` travels the same export path as
the existing two vars (`templates/devcontainer.json`'s `containerEnv`), with
D-24's fallback rule (unset → `CGROUP_PARENT_DEV_BACKGROUND`, today's
placement) documented inline and in `host-setup/README.md`. No downstream
consumer (`run-gate`, `cmru`'s `tester_gate.py`, `srdm`'s `gate.sh`, ciu
governance) was modified to actually read the new var — that is each
consumer's own future work (P5 for run-gate), out of this package's scope;
`run-gate.py smoke`'s own gate run (see Gates below) confirms it still
correctly falls back to `dev-background.slice` today, exactly as D-24
predicts.

## Operator install/upgrade command sequence (exact, verified against the real script)

**Fresh host, never ran mdt host-setup before:**
```bash
sudo ./host-setup/install.sh                  # seeds /etc/mdt/host-setup.env from
                                                # the example (dev-gates.slice section
                                                # included), REVIEW IT
sudo vi /etc/mdt/host-setup.env                # size DEV_GATES_MEMORY_HIGH/_MAX/
                                                # _SWAP_MAX/_CPU_WEIGHT/_IO_WEIGHT/
                                                # _OOM_PRESSURE_LIMIT for this host if the
                                                # 16 GiB-host defaults do not fit
sudo ./host-setup/install.sh                   # renders + installs every unit
                                                # (dev-gates.slice included),
                                                # daemon-reload, starts every slice,
                                                # enables + runs the sweep once
sudo mdt-host-check.sh                         # verify: dev-gates.slice exists and
                                                # is active, effective memory.high/
                                                # max/swap.max/cpu.weight/io.weight
                                                # match what you set
```

**Host already running mdt host-setup, from before `dev-gates.slice`
existed** (a plain re-run of `install.sh` alone will NOT pick up the new
`DEV_GATES_*` keys — verified by reading `install.sh`: it never touches an
already-existing `/etc/mdt/host-setup.env` except to render units *from* it,
so `render()`'s own "unset → directive dropped" rule would render
`dev-gates.slice` with no `MemoryHigh`/`MemoryMax`/`MemorySwapMax`/
`ManagedOOMMemoryPressureLimit` at all):
```bash
sudo ./host-setup/install.sh --force          # backs up /etc/mdt/host-setup.env,
                                                # re-seeds it wholesale from the
                                                # current example (has dev-gates.slice
                                                # now) -- DISCARDS prior tuning on
                                                # purpose; diff the backup next
sudo vi /etc/mdt/host-setup.env                # reapply your prior customizations
                                                # from the backup; size the new
                                                # DEV_GATES_* keys for this host
sudo ./host-setup/install.sh                   # renders + installs every unit
                                                # (dev-gates.slice included),
                                                # daemon-reload, starts every slice,
                                                # runs the sweep once
sudo mdt-host-check.sh                         # verify
```
(`sudo ./host-setup/install.sh --wizard` is the interactive alternative to
the `--force` + hand-edit steps above — same backup, but walks every section
it knows with existing values pre-filled as defaults; still review the new
"dev-gates.slice" section afterwards since the wizard does not walk it.)

There is **no daemon-restart step** in either sequence (the re-scoped
handoff explicitly dropped it — the daemon is a separate deployment,
`scripts/cgroup-profiler/`'s own concern).

## Gates

Registered gate: `modern-debian-tools-python-debug/run-gate.toml`
`[lanes.smoke]` (a host lane; previously `py_compile` over every tracked
`.py` file only). Extended `argv` to also run the new
`host-setup/tests/test-render.sh` between the `py_compile` step and the
final `echo`.

**`host-setup/tests/test-render.sh`** (new, bash): extracts `install.sh`'s
own `render()` function and `RENDER_VARS` list verbatim (a `sed` byte-range
on two stable anchor patterns — never a hand-duplicated copy that could
drift), sources that plus `host-setup.env.example` itself (no root, no
`/etc/mdt/`, no host mutation), renders every `units/*.in` template, and
asserts:
1. no rendered unit keeps an unresolved `@VAR@` placeholder token (checked
   across every unit, not just the new one — this is the exact bug class
   CGROUP-NOTES.md's own "Status corrected 2026-09-08" note describes for a
   real, past `DEV_MEMORY_MIN_GUARANTEED_CEILING` `RENDER_VARS` omission);
2. `dev-gates.slice` renders every `DEV_GATES_*` key to its shipped example
   value;
3. `dev.slice` and `dev-memory_min_guaranteed.slice` render with **no**
   `MemoryMin=` line at all — confirms the RW-30/A1 revert restored the
   original opt-in-only ancestor-chain behavior byte-for-byte, not just by
   eyeball diff (`DEV_MEMORY_MIN_GUARANTEED_CEILING` ships unset in the
   example);
4. no `units/*.in` file mentions `dev-infra`/`DEV_INFRA_` anywhere
   (confirms the withdrawal is complete, not just from the units I remembered
   to touch);
5. `bash -n` clean on every shell script this package touches, itself
   included;
6. `shellcheck -S warning` clean on the same set (not default severity — the
   three pre-existing files carry SC2015/SC2181 info/style findings on lines
   this package's diff never touches, verified by line number against `git
   diff 11ac5d67`; fixing pre-existing style debt across `install.sh`/
   `check.sh`/`mdt-apply-dev-caps.sh` is out of this package's scope).

**Ran directly** (`bash host-setup/tests/test-render.sh`): all 6 assertions
pass, `test-render: ALL OK`, exit 0.

**Ran the registered gate** (`nice -n 19 ionice -c 3 python3 run-gate.py
smoke`, from a clean committed tree, verdict read in a separate step
afterward — never a pipe tail): `run-gate: lane 'smoke' exit 0`. Notable in
the transcript: run-gate's own container launched with `--cgroup-parent
dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`
— i.e. it is not yet reading `$CGROUP_PARENT_DEV_GATES` (expected, P5's
future work) and correctly falls back to today's placement per D-24. Checked
for a leftover gate container afterward (`docker ps -a --filter
name=run-gate-vbpub-smoke`): none — run-gate cleaned it up itself.

**Python coverage:** this package touches zero `.py` files (confirmed:
`git diff 11ac5d67..HEAD --name-only | grep '\.py$'` is empty) — the
renderer test was written in bash specifically to avoid needing to introduce
throwaway Python just to satisfy the coverage bar, matching this
subdirectory's existing shape (bash + a handful of specific, already-tested
Python scripts, none of which this package touched). "100% line AND branch
on any python you change" is vacuously satisfied.

**Not added:** `systemd-analyze verify` — grepped the whole `host-setup/`
tree first; it is not used anywhere in this project today, so the handoff's
"if it is there already" condition does not apply.

## Decisions taken without stopping (RW-9)

1. **dev.slice ancestor-chain arithmetic for dev-infra's MemoryMin (M1,
   later moot).** CGROUP-NOTES.md's own documented invariant ("keep the
   parent's claim exactly matched") meant `dev-infra.slice`'s `MemoryMin=256M`
   would have been silently inert without also updating `dev.slice`'s own
   `MemoryMin` to a computed sum — neither the design doc nor the handoff
   called this out explicitly. Took the design's literal values, wired the
   arithmetic correctly (a new `_bytes_of()` helper + `DEV_SLICE_MEMORY_MIN`
   in `install.sh`), logged the ask, continued. Fully reverted along with
   `dev-infra.slice` itself once RW-30 withdrew it — moot at the tip, but
   the reasoning is preserved in the LOG in case a similar ancestor-chain gap
   resurfaces for a future `dev-gates.slice` leaf `memory.min` (see
   CGROUP-NOTES.md's new "dev-gates.slice and placed lane leaves" section,
   M4 — this is now forward-looking documentation instead of shipped code).
2. **Blacklisted `TODO.md` / no CHANGES.md / no mdt backlog structure (M4).**
   `modern-debian-tools-python-debug/TODO.md` is on the handoff's explicit
   "never touch, someone else's uncommitted edits" list and has no existing
   dev-gates/cgprofile line item (checked, read-only). No separate
   `CHANGES.md` exists for this project. mdt has no `nyxloom-trove/backlog/`
   structure. Result: no FIXED-evidence backlog entry was filed anywhere in
   mdt itself — this REPORT carries the evidence (commit hashes above) for
   whoever next touches `TODO.md` once the other session's edits land, and
   for whoever next updates the memory file
   `soulmask-memory-pressure-findings.md` (not repo-tracked, out of a
   package implementer's scope to edit directly), which is where the
   original "dev-gates.slice ... remains unbuilt" finding lives.

## Controller re-scope (RW-30, design amendment A1)

Received mid-package, after M3 (`1de4c93c`), before M4. Verified against the
real repository before acting — not taken on faith: `main` had genuinely
moved (`11ac5d67` → `5edec58c`) with a real new `## A1` section in the
design doc, content matching the message's claims exactly (checked via
`git log`/`git show main:...`). Full mechanism, ruling, and file-by-file
revert accounting in the LOG's "Controller re-scope" entry; summarized above
under "What shipped."

## Scope discipline

Touched only: `modern-debian-tools-python-debug/{DEVCONTAINER-LIFECYCLE.md,
run-gate.toml, host-setup/**, templates/devcontainer.json}` and
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P8-{LOG,REPORT}.md`.
Never touched: the SHARED checkout (`/workspaces/vbpub/
modern-debian-tools-python-debug/` outside this worktree — confirmed
untouched, still shows only the same pre-existing uncommitted files named in
the handoff), `Dockerfile`/`README.md` (top-level)/`TODO.md`/
`ai-cli-tools.list`/the three `ai-cli-tools` scripts (the blacklisted set),
`run-gate-project/` source, `scripts/cgroup-profiler/`, `ciu/`,
`/workspaces/dstdns`, any other worktree. No host mutation — every check in
this REPORT ran against a rendered-to-tmpdir copy or inside run-gate's own
gate container, never against `/etc/mdt`, `/etc/systemd/system`, or any real
cgroup.

## Checkpoint clause

Not triggered — package stayed well under ~120k context / ~60 tool calls.
