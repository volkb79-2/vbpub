# run-gate-WAVE-RG55-P8 — mdt host-setup: `dev-infra.slice` and `dev-gates.slice`

**Implementer:** FRESH Sonnet session, checkpoint clause on. Package P8 of the
RG-55 wave (design D-19, D-24; controller ruling RW-29). Records:
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P8-{LOG,REPORT,BRIEF-n}.md`.

## Where you work
```
git -C /workspaces/vbpub worktree add .worktrees/mdt-dev-slices -b mdt-dev-slices main
```
Project dir `modern-debian-tools-python-debug/host-setup/` (+ whatever
exports `CGROUP_PARENT_DEV_*` to devcontainers — find it). The SHARED
checkout `/workspaces/vbpub/modern-debian-tools-python-debug/` has someone
else's UNCOMMITTED edits (Dockerfile, README, TODO.md, ai-cli-tools scripts):
never touch the shared checkout, never commit those files; your worktree is
clean. Never touch `run-gate-project/`, `scripts/cgroup-profiler/`, `ciu/`,
`/workspaces/dstdns`. **Do not install anything on the host** — the operator
installs host-setup themselves; you deliver units, renderer, checks, docs and
the exact operator command sequence.

## Orientation (in order)
1. `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`
   §1 (the 2026-08-04 findings row), §2 D-19/D-24/D-25, §3 (the layout and
   the sizing with reasons), §4 step 7.
2. `modern-debian-tools-python-debug/host-setup/README.md`, `CGROUP-NOTES.md`,
   `host-setup.env.example` (every existing `DEV_*` key and its comment
   style), `install.sh` (how `.in` units are rendered), `units/*.slice.in`
   (`dev-background.slice.in` and `dev-interactive.slice.in` are your
   templates), `scripts/mdt-apply-dev-caps.sh` (the sweep), `check.sh`/
   `mdt-host-check.sh`, the wizard (`mdt-host-setup-wizard.py`) and its
   tests if it validates env/units.
3. `grep -rn CGROUP_PARENT_DEV_BACKGROUND /workspaces/vbpub --include='*' -l`
   (outside `.worktrees/`): learn how the variables reach the devcontainer
   and ciu today (devcontainer template `remoteEnv`/`runArgs`, `ciu.env`,
   docs) — the two new variables must travel the same path.
4. Memory of the estate's findings (read-only): `~/.claude/projects/
   -workspaces-vbpub/memory/soulmask-memory-pressure-findings.md`,
   `mdt-host-setup-companion.md`.

## Deliverables (one commit each)
- **M1** `units/dev-infra.slice.in`: `MemoryMin=@DEV_INFRA_MEMORY_MIN@`
  (256M), `MemoryHigh` (768M), `MemoryMax` (1G), `CPUWeight` (100),
  `IOWeight` (50), NO `ManagedOOM*` kill; Description names it "host-level
  dev infrastructure daemons that observe and act (cgprofile-host-daemon)".
  `units/dev-gates.slice.in`: `MemoryHigh` (4G), `MemoryMax` (6G),
  `MemorySwapMax` (32G), `CPUWeight` (20), `IOWeight` (10),
  `ManagedOOMMemoryPressure=kill` + limit like background; Description "gate
  and lane containers + placed lane leaves rg-<token> — the admission
  capacity object". Keys + defaults + a reasoned comment block each in
  `host-setup.env.example` (cite the design doc; say these are defaults for
  a 16 GiB host and are expected to be re-tuned from footprint data).
- **M2** `install.sh` renders/installs both; `mdt-host-slices.service` /
  the sweep script verify at each run that a container named
  `cgprofile-host-daemon`, when present, sits under `dev-infra.slice` (WARN
  naming the actual slice if not — placement is create-time, the sweep only
  reports); `mdt-host-check.sh` reports both slices' existence and effective
  `memory.max`/`memory.min`/`memory.high`; `systemd-analyze verify` in the
  check path if it is there already.
- **M3** Export `CGROUP_PARENT_DEV_INFRA=dev-infra.slice` and
  `CGROUP_PARENT_DEV_GATES=dev-gates.slice` on the SAME path the existing
  `CGROUP_PARENT_DEV_INTERACTIVE`/`_BACKGROUND` use (template, env file, docs)
  — and document the consumer fallback rule (design D-24): unset → today's
  placement.
- **M4** Docs: README section "dev-infra and dev-gates: why", CGROUP-NOTES
  (the `memory.min`-on-leaf fact, the no-nesting-inside-scopes fact from
  D-20), the operator install/upgrade sequence (copy units, daemon-reload,
  start slices, re-run sweep, verify with `mdt-host-check.sh`, then restart
  `cgprofile-host-daemon` via `ciu` from `scripts/cgroup-profiler` so it lands
  in `dev-infra`), CHANGES/TODO of mdt as the project keeps them; the
  project's own backlog gets an entry with FIXED evidence.

## Gates
mdt's own tests/gate as registered in-repo (find them; if host-setup has
none beyond shell syntax, add a renderer test that renders every `.in` unit
from `host-setup.env.example` and asserts the new keys appear, plus
`bash -n`/`shellcheck` if available). 100% line AND branch on any python you
change. Verdict in a separate step.

## Records, checkpoint clause, BLOCKED protocol, HOST LOAD
As in `run-gate-WAVE-RG55-P4-HANDOFF.md` (same wave). This package runs no
containers and no mutation lanes; keep pytest serial under `nice -n 19
ionice -c 3`; RAM PSI is the limit (back off while `/proc/pressure/memory`
`full avg10 > 5`). Decision asks never stop you (RW-9): take the design's
value, log the ask, continue.

Commit trailers: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`,
`Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
Edit tool for file changes; `git commit --only -- <paths>`. Claim only what
you ran — a fresh adversarial reviewer verifies every claim.
