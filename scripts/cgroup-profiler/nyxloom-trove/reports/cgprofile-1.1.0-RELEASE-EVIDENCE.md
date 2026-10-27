# cgprofile 1.1.0 release evidence (wave 2026-10-27)

Branch `wave-cgprofile-20261027`. Contents of 1.1.0: CGP-110 (Sol blockers B1-B6 of 1.0.0, ten commits merged from
`cgp-110-20261021`, tip `6ea5ef2fb`) plus the CP-28/CP-29 backlog entries (`efded4f97`).

## Gate policy for this release

- R2 (assay mutation) is WAIVED by the operator for every release of the 2026-10-27 wave. No R2 verdict exists for 1.1.0 and none may
  be inferred from the r3 canaries. Tracked as CP-30. (1.0.0 also had no completed full-source R2 verdict.)
- `r0-r1` (unit + coverage, 100% line and branch required by `tools/gate.sh coverage`) and `r3` (7 canaries) are mandatory and run
  from the exact tree. `--skip-gates` is not used.

## Acceptance evidence

Operator decision 2026-10-27: the live acceptance evidence is the read-only observation made that day, not a separate acceptance run.
A short-lived root observation container bind-mounted the real host cgroup tree read-only and the repository read-only, and called
the collectors directly (`lib.metrics.sample_cgroup`, `lib.limits.read_limits`) with `host_cgroup_view` true. Recorded in the
CP-28 entry, section "Evidence": all of `mem memstat memev psi cpu io pids cgstat` returned for a container scope, a slice,
`system.slice` and the root (full per-device `io.stat` incl. `cost.usage`, three PSI files, `memory.stat`, `memory.events.local`,
swap, `cpu.stat` throttling, `pids`), 0.36 ms per call for all groups; `read_limits` returned `io.max`, `io.weight`,
`memory.high/max/swap.max`, `pids.max` with binding-level attribution; gaps found (`memory.swap.events`, hierarchical
`memory.events`, `io.cost.*`, `/proc/diskstats`, io PSI in `ctl host`) were filed as CP-29/CP-28.

What this evidence covers: collectors and limit readers against the real tree. What it does not cover: the contract-2 observer
acceptance deltas (`tools/observer-live-acceptance.py`, Sol B5), the end-to-end `serve` refusal path of B4 on a host without the
slice unit, and any CGP-110 stop/finalize race (unit-tested with fakes only). CP-20 therefore stays `carved`.

## Deploy-side facts measured 2026-10-27

- The host slice unit that B4 requires is installed and bounded (`cgprofile.slice` loaded, `MemoryMax=1073741824`), so the 1.1.0
  daemon passes its own start guard on this host. The running host daemon is still the pre-1.1.0 image.
- Run-gate client compatibility (installed run-gate 24.0.1): `stop-incomplete` is surfaced as a generic profile warning
  ("refused (stop-incomplete)"), the stop is not retried, and the unknown additive Summary keys `damon_cleanup`/`stop_forced` are
  ignored. Nothing breaks; a wedged-worker session stays reserved until the third incomplete stop or daemon shutdown. cgprofile 1.1.0
  may therefore ship before run-gate 24.x; a run-gate follow-up (retry `stop-incomplete`, surface `damon_cleanup.confirmed`) is
  recommended, not required.

## Gate results

Run through the supported verb `run-gate --worktree <wave worktree> <lane>` from `scripts/cgroup-profiler`, installed run-gate 24.0.1.

| Lane | Tree | Result |
|---|---|---|
| r0-r1 (run 1) | merge `68762c9ca` + docs `d354b5e78` | FAIL exit 2: 2625 passed, coverage 99% (two CGP-110 lines uncovered: `serve.py` 1819 and 3160; the shipped `_observe_liveness` closed-entry test used a null tracker and returned on the wrong branch) |
| r0-r1 (run 2) | `420ac1554` | PASS exit 0: 2627 passed, 8667 statements / 3236 branches, 100% line and branch (fail_under = 100) |
| r3 | `420ac1554` | PASS exit 0: 7 canaries rejected, 0 survived |
| r0-r1 (run 3) | `a8e3e9a90` (test-only nits after round-2 review) | PASS exit 0: 2627 passed, 100% line and branch |
| r2 | - | WAIVED (operator, 2026-10-27), CP-30 |

## Post-gate changes: pre-release deep review (branch `fable-cgprofile-20261027`, 2026-10-27)

A one-time deep review of the tool before the cut landed `3e50df9cc` on top of `677a56d0c`. It CHANGES `lib/`
(`metrics.py`, `summary.py`, `events.py`, `util.py`, `targets.py`, `serve.py`) and `cgprofile.py`, so the gate
table above does NOT cover the release tree any more: **`r0-r1` and `r3` must be re-run on the final tree** (the
cmru release gate does both) before 1.1.0 is cut. Evidence for the review commit itself, measured in a scratch venv
built from `requirements.txt` (+ `coverage`, see CP-32) on the devcontainer, not through the registered lane:

| Check | Result |
|---|---|
| full suite, pre-change tree (`677a56d0c`) | 2624 passed, 3 failed: the three `tests/test_docs.py` cases that import the installed `run_gate` wheel, absent from the scratch venv (environment, not product; the registered lane runs in tester-unified where the wheel exists) |
| the 32 new tests against the pre-change tree | 30 failed, 2 passed (the two are regression pins: default-group selection and root-path spellings) |
| full suite, review tree (`3e50df9cc`) | 2656 passed, same 3 environment failures |
| coverage, review tree | 100% line and branch on every module (`coverage report` lists no file below 100%) |

What the review changed (details in `CHANGES.md` [Unreleased] and the commit): additive sample groups `swapev` and
`memev_h` (CP-29 items 1-2) and the `swap_fail` event; `cgroup:` path canonicalisation with `..` refused; slice-name
validation (`a--b`, `-a`, `a/b` refused, also at `serve` start for `--observe-slices`/`--gates-slice`);
`docker inspect --type container` with a 64-hex `Id` check and a resolve-failure instead of a `TimeoutExpired`
traceback; non-finite PSI fields dropped. None of it touches the contract-1 wire shapes, the Summary document, the
goldens in `tests/fixtures/rg55/`, or the slice guard. Backlog items filed by the review: CP-32 to CP-37; CP-27,
CP-28 and CP-29 carry dated review notes.

Review: round 1 (fresh Sonnet reviewer) found a real regression (`stop` joined the sampler under the daemon-wide lock, so a
worker that was itself finishing produced a spurious `stop-incomplete`); fixed in `420ac1554` with a test that fails without the fix,
and MINOR findings N9-N12 filed under CP-27. Round 2 (fresh Sonnet reviewer) on the fix: READY, two test nits applied. The r3 run is on `420ac1554`; the later commits change tests and docs only (`git diff 420ac1554 HEAD -- lib` is empty at the time of writing, re-run r3 on the final release tree via the cmru release gate).
