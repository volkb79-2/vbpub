# cgprofile CP-20 release-candidate correction — REPORT

Status: implemented and committed; **not gated**. Exact-tip registered gates
(`r0-r1`, `r3`) and live observer acceptance remain pending, and no full-source
R2 has been run or is claimed. The initial 1.0.0 release has no completed
full-source R2 verdict.

## What changed (all under `scripts/cgroup-profiler/`)

- `lib/resource_observer.py` — refuses a `pids_localevents` mount with a
  diagnostic; `pids_events_scope` is always `hierarchical`; `memory_localevents`
  unchanged (candidate-child refusal kept); dead pids-localevents plumbing
  removed.
- `tests/test_resource_observer.py`, `tests/test_serve_resource_observer.py` —
  refusal (alone and combined), ordinary success, preserved memory behaviour.
- `tests/test_docs.py` — shipped-Run-Gate-loader test of the native-R2 example,
  missing-ceiling rejection (memory, pids), release-gate shape, and "no R2
  verdict" statement presence.
- `docs/CONSUMERS.md` (example fixed: `resources.memory = "2g"`,
  `resources.pids = 8192`), `docs/DESIGN-GUIDE.md` (new release-qualification
  section), `README.md`, `docs/PROTOCOL.md`, `docs/RG55-INTERFACE-CONTRACT.md`,
  `CHANGES.md`, `cmru.toml` and `run-gate.toml` comments.
- CP-20 backlog entry — acceptance text and 2026-10-15 review update.
- This LOG and REPORT.

## Evidence run

Only `py_compile` plus a throwaway Run-Gate loader probe and phrase check in the
cockpit (details in the LOG). No pytest, no gates, no docker.

## For the controller

1. Sync `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` with the
   edited `docs/RG55-INTERFACE-CONTRACT.md` or the byte-for-byte mirror test
   fails (see LOG).
2. Run `run-gate r0-r1` and `run-gate r3` at the exact tip; the new
   `test_docs.py` tests import `run_gate` and need the Run-Gate wheel in the
   tester image.
3. Run live observer acceptance before closing CP-20.
