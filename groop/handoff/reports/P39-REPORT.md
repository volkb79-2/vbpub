# P39 — Release Readiness Ledger — Report

## What Was Built

A canonical release-candidate readiness surface for v1/v1.5, tying
`TUI-SPEC.md` §9 acceptance gates to evidence sources, with exact commands
for rootless automated checks, live-host evidence templates, and explicit
non-claims.

### New Document: `groop/docs/RELEASE-READINESS.md`

The document includes:

- **Release-cut scope:** a table mapping each capability to its release cut
  (v0/v1/v1.5/v2) and its evidence source.
- **Spec §9 acceptance map:** a 14-row table mapping every §9 item to its
  evidence source (test file, acceptance command, `MEASUREMENTS.md` entry,
  or "remaining" marker).
- **Rootless automated check commands:** exact, copy-pasteable commands for
  full tests, py_compile, smoke (P33), steady (P35), tui-smoke (P38), replay
  UI smoke, and packaging smoke.
- **Live-host evidence template:** structured templates for the three missing
  manual measurements: 5-minute Textual TUI CPU/RSS (spec §9 items 1–2), live
  DAMON vaddr/paddr acceptance, and daemon status.
- **Release blocker checklist:** 9 items that must be recorded in
  `MEASUREMENTS.md` before a release tag, including gates for BPF and DAMON.
- **Explicit non-claims:** 8 items documented as explicitly outside the
  release claim (per-cgroup network loss, live BPF lifecycle, executable admin
  actions, web UI, GPU/ZFS plugins, production daemon install, persistent
  paddr DAMON).
- **Document history:** versioned changelog entry.

### Updated Framework Documents

- `groop/README.md` — added `docs/RELEASE-READINESS.md` to the canonical
  documents list; changed P39 status from "Planned" to "Done" with REPORT.md
  reference.
- `groop/docs/OPERATIONS.md` — "What To Check Before A Release Claim" now
  points to `docs/RELEASE-READINESS.md` as the canonical checklist.
- `groop/docs/STATUS.md` — v1 summary row updated to reflect that P39 closes
  the "final release documentation" gap; P39 added to the acceptance evidence
  bullet.
- `groop/docs/ROADMAP.md` — P39 status changed from "planned" to "done" with
  report reference; remaining estimate for v1/v1.5 release confidence changed
  from "1" to "0" packages.

### No Python Files Changed

P39 is documentation-only. No `.py` files were created or modified.

## Deviations from the Handoff

None. The handoff was followed exactly:

- `groop/docs/RELEASE-READINESS.md` created with all required sections.
- All five framework documents updated as specified.
- `MEASUREMENTS.md` kept as the evidence ledger, not duplicated.
- Live-host templates provided for missing measurements.
- Non-claims documented.
- Validation commands run and recorded.

## Proposed Contract Changes

None. P39 is documentation-only. No shared interfaces or contracts were
touched.

## Test Evidence

### Full test suite

```bash
$ python3 -m pytest groop/tests -q --tb=short
# 367 passed, 15 failed in 47.66s
```

The 15 failures are all in `test_ui_app.py` (Textual pilot tests), a
pre-existing Textual version incompatibility documented in P33/P35 reports.
They are not caused by P39, which changes no Python code.

### Focused acceptance tests

```bash
$ python3 -m pytest groop/tests/test_acceptance.py -q --tb=short
# 40 passed in 8.04s
```

### P33 acceptance smoke harness

```bash
$ PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
```

Result: exit 0, `"ok": true`

- Entities: 8
- Metric source labels: 572 (derived=200, exact=66, host=40, netns=28,
  unavail_kernel=142, unavail_perm=80, unlimited=16)
- Replay frames: 1
- Wall: 0.2426s
- User CPU: 0.0501s
- Sys CPU: 0.0113s
- Max RSS: 23628 KB

### P35 acceptance steady harness

```bash
$ PYTHONPATH=groop/src python3 -m groop.acceptance steady \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --samples 2 --interval-s 0 --json
```

Result: exit 0, `"ok": true`

- Samples completed: 2/2
- Entity counts: min=8, max=8, last=8
- Wall: 0.197s
- User CPU: 0.0489s
- Sys CPU: 0.0043s
- Max RSS: 23196 KB
- Avg sample wall: 0.0985s
- CPU: 26.99%

### P38 TUI smoke harness

```bash
$ PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
```

Result: exit 0, `"ok": true`

- Smoke line: `ui smoke ok frames=1 view=tree profile=auto`
- Wall: 0.5606s
- Child user CPU: 0.4328s
- Child sys CPU: 0.0458s
- Child max RSS: 48436 KB

### Python compile

```bash
$ python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# exit 0, clean
```

No Python files were changed by P39 (documentation-only), but the existing
acceptance module compiles cleanly.

### Import contract

```bash
$ python3 -c "import groop.acceptance; import sys; print('textual' in sys.modules)"
# False
```

### Version check

```bash
$ groop --version
# groop 0.1.0
```

## Known Gaps / Open Items

- **5-minute live Textual TUI CPU/RSS** (spec §9 items 1–2): the
  `RELEASE-READINESS.md` document provides a template for this measurement,
  but it has not been run in this session. This is deliberate — it requires
  a real host with live cgroups and a 5-minute dedicated run.
- **Live DAMON vaddr/paddr acceptance:** the document provides a checklist
  template, but live-root acceptance requires a deliberate test host (same gap
  as P14).
- **15 pre-existing Textual pilot test failures:** these are environment-local
  (Textual 8.2.8 vs the 0.58 API the pilot tests expect). They are not caused
  by P39.
- **The `tui-smoke` command requires textual to be installed** in the Python
  environment. In this session it was installed midway through the validation
  step. This is correct behavior — P38's known-gaps note documents this.
- **pipx-specific packaging evidence** remains optional and unmeasured.
- **Byte-for-byte rendered table acceptance** (spec §9 item 10) has not been
  run; only model-equality and fixture round-trips exist.

## Files Changed

```
A groop/docs/RELEASE-READINESS.md          # New canonical release-readiness document
M groop/README.md                          # Added RELEASE-READINESS.md to canonical docs; P39 status Done
M groop/docs/OPERATIONS.md                 # Release checklist points to RELEASE-READINESS.md
M groop/docs/STATUS.md                     # Updated v1 summary; P39 in acceptance evidence
M groop/docs/ROADMAP.md                    # P39 done; remaining packages 0
A groop/handoff/reports/P39-LOG.md          # Work log
A groop/handoff/reports/P39-REPORT.md       # This report
```
