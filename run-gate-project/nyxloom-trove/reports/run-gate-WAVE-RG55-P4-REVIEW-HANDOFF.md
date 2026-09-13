# run-gate-WAVE-RG55-P4 — adversarial review handoff (RG-57..RG-61 follow-ups)

**Reviewer:** FRESH session (Opus, xhigh), never a fork. **Break this before
merge.** 3-round cap; fix-verification resumes YOUR session. Records:
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-round<n>.md`.
Branch `rg55-followups-run-gate`, worktree `.worktrees/rg55-followups-run-gate`,
project `run-gate-project/`. Base: the P2 tip the branch merged last (the
dispatch message names base and tip). Review the FULL diff `base...tip`.

## Phase 1 — BLIND: controller log RW-26/RW-27, backlog RG-57..RG-61 (the
spec), contract §4.1/§4.3, the P4 implementer handoff, SPEC R-36/R-39/
R-43/R-44 before and after, then the diff. Own sweeps first.
## Phase 2 — RECONCILE against P4 LOG/REPORT/BRIEFs.

## Attack surface (minimum)
1. **RG-57 R-36h:** plant exceptions in the self-id resolution, `ctl start`,
   `stop`, and `getrusage`; the bare-host lane's exit code and verdict never
   move. `ru_maxrss` units (KiB on Linux) → bytes; `rusage-maxrss` is the
   largest child, never a sum — is that stated everywhere the number shows
   (SPEC, `history`, `footprint`, `doctor`)? `null` discipline for what
   getrusage cannot give. Token exported to the child env and to nothing
   else; `RUN_GATE_PROFILE=off`/`profile = false` still opt out; `--dry-run`
   starts nothing. Self container id: `/etc/hostname` vs `docker inspect`
   disagreement, run-gate outside a container → daemon path skipped with a
   disclosed reason, never a crash.
2. **RG-60:** inflight record written before the exec, cleared on every
   path; kill the client mid-run and find the record; R-39 reconciliation
   on the next invocation.
3. **RG-59:** the two branches ("not running" vs malformed stdout) share
   one constant with `doctor`; no regex over-match (a running daemon whose
   stderr merely mentions "container" must not be mis-reported).
4. **RG-58:** exactly one warning per load, doctor WARN, container/exec
   lanes untouched, exit code unchanged; a refusal would violate RW-27a.
5. **RG-61:** each of the eight items verified against the code; the
   `footprint --write` transcript is REAL (re-run it yourself on the
   project store); `usage()`, `__revision__ = 42`, CHANGES entries.
6. **Hollow tests / coverage:** mutate each subject, watch the test; 100%
   line AND branch on changed lines via the selftest judge (run it);
   in-process `run_gate.main()` where coverage is claimed.
7. **Gates yourself:** `./run-gate.py selftest`, `--base <base> assay-r1`,
   `assay-r3`; read the r2 verdict and every survivor justification.

## Live probes
- `./run-gate.py selftest` on the tip with the daemon DOWN → the history
  record shows `method: rusage`, `source: rusage-maxrss`, cpu > 0, peak
  plausible; `footprint --write` succeeds for this project; `doctor` prints
  the source caveat.
- If `cgprofile-host-daemon` is UP (do not start/stop it yourself): one
  selftest with the daemon → `method: daemon`, `scope: container-shared`,
  `targets_seen ≥ 1`. Otherwise say so.

## Verdict: `ACCEPT` / `ACCEPT-conditional` / `REJECT` with B1.. (file:line +
prescription), S1.. non-blocking, decision asks named. Round file first,
then the verdict line first in your message.

## HOST LOAD (binding): as the P4 implementer handoff — PSI is the signal,
serial `nice -n 19 ionice -c 3` pytest, whole selftest at most once, ≤ 2
gate containers, never `--cgroupns=host`/`--pid=host`, never touch
`scripts/cgroup-profiler/`, `ciu/`, `/workspaces/dstdns`; no commits to the
branch — repairs are the implementer's.
