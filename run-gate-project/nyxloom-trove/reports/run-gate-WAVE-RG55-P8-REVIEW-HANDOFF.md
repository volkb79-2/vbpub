# run-gate-WAVE-RG55-P8 — adversarial review handoff (mdt host-setup `dev-gates.slice`)

**Reviewer:** FRESH session (Opus, xhigh), never a fork. **Break this before
merge.** 3-round cap; fix-verification resumes YOUR session. Records:
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P8-REVIEW-round<n>.md`.
Branch `mdt-dev-slices`, worktree `.worktrees/mdt-dev-slices`, project
`modern-debian-tools-python-debug/host-setup/` (+ wherever
`CGROUP_PARENT_DEV_*` is exported). Base `main@11ac5d67`; tip in the dispatch
message. Review the FULL diff `11ac5d67...<tip>`, every file type (units,
env example, shell, docs, run-gate.toml, tests).

## Phase 1 — BLIND
Read: design doc `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-
placement-admission.md` §1 (findings row), §2 D-19/D-24/D-25, §3, **§A1
(RW-30: dev-infra WITHDRAWN, dev-gates stays)**; controller log RW-29/RW-30;
the P8 implementer handoff; `host-setup/README.md`, `CGROUP-NOTES.md`,
`host-setup.env.example`, `install.sh` (its `render()`), the sweep and check
scripts as they were on `main` and as changed; then the diff. Own sweeps
first.

## Phase 2 — RECONCILE against P8 LOG/REPORT.

## Attack surface (minimum)
1. **The unit is what the design says**: `MemoryHigh=4G`, `MemoryMax=6G`,
   `MemorySwapMax=32G`, `CPUWeight=20`, `IOWeight=10`, `ManagedOOMMemoryPressure=kill`
   + a limit consistent with `dev-background.slice.in`; `Before=slices.target`;
   naming makes it a child of `dev.slice` (systemd dash hierarchy); no
   `MemoryLow` reliance (inert on this host); does `dev.slice`'s
   `MemoryMin` ceiling logic (`DEV_MEMORY_MIN_GUARANTEED_CEILING`) still
   render byte-identically (the REPORT claims reversion)?
2. **Rendering**: every `@KEY@` in the new unit has a key in
   `host-setup.env.example`; `install.sh` renders/installs it in the same
   pass as the others; re-running `install.sh` is idempotent; a host WITHOUT
   the new keys in `/etc/mdt/host-setup.env` (upgrade case) — does render
   fail loudly or silently emit `@DEV_GATES_MEMORY_HIGH@` into a unit? Render
   it yourself both ways.
3. **Sweep/check**: `mdt-apply-dev-caps.sh` and `mdt-host-check.sh` report
   the new slice correctly and never touch it when absent (pre-install
   hosts); `systemd-analyze verify` if present in the check path.
4. **The env export path (M3)**: `CGROUP_PARENT_DEV_GATES` travels the SAME
   path as `CGROUP_PARENT_DEV_BACKGROUND` (devcontainer template `remoteEnv`/
   `runArgs`, env files, docs) — grep the whole repo (outside `.worktrees/`)
   for the existing variable and check every site got the new one, or a
   documented reason why not. Fallback rule (D-24) stated where consumers
   will read it.
5. **The withdrawal (`b9e628f5`)**: zero `dev-infra`/`DEV_INFRA`/
   `CGROUP_PARENT_DEV_INFRA` references remain anywhere on the branch
   (`git grep` at the tip); files it touched are byte-identical to `main`.
6. **Docs**: README "dev-gates: why" is accurate to the design (containment +
   capacity object; gates leave dev-background; the daemon is NOT under
   `dev.slice` by design, pointing at design §A1); CGROUP-NOTES facts
   (memory.min-on-leaf, no nesting inside container scopes) are correct;
   the operator fresh-install and upgrade sequences in the REPORT actually
   match `install.sh`'s behaviour — walk them against the script.
7. **The test** (`host-setup/tests/test-render.sh`): it extracts
   `install.sh`'s `render()` rather than duplicating it — confirm; mutate the
   unit template (drop a key) and the env example (typo a key) and watch
   the test go red; it is wired into the project's registered gate
   (`run-gate.toml` `[lanes.smoke]`) — run `./run-gate.py smoke` yourself
   from the worktree's project dir and read the verdict separately.
8. **Hollow claims**: anything in the REPORT you cannot reproduce, list it.

## Verdict
`ACCEPT` / `ACCEPT-conditional` / `REJECT` with B1.. (file:line +
prescription), S1.. non-blocking, decision asks named. Round file first,
then the verdict line first in your message.

## HOST LOAD (binding)
No containers, no host installs (NEVER run `install.sh` against the real
host, never `systemctl`, never write under `/etc`); shell tests only,
`nice -n 19`; RAM PSI is the limit. Never touch `run-gate-project/` source,
`scripts/cgroup-profiler/`, `ciu/`, `/workspaces/dstdns`, the shared
checkout's dirty mdt files; no commits to the branch — repairs are the
implementer's.
