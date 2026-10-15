# cgprofile CP-20 release-candidate correction — implementer LOG

Worktree `.worktrees/run-gate-rg93-git-metadata-20261013`, base HEAD
`5f0985db1a551964756a000621b0deb8e22ebfa4`. The eight-file controller draft
(uncommitted at start) was inspected and refined, not reset.

## Steps

1. Inspected the draft diff. `lib/resource_observer.py` already refused
   `pids_localevents` before any cgroup read and always reported
   `pids_events_scope = "hierarchical"`. Verified no remaining path treats
   `pids_localevents` as equivalent evidence: `_read_cgroup_entry` no longer takes
   a pids flag, `_effective_local_events` is called with `regular_is_local=False`
   for pids, and the child-cgroup scan runs only for `memory_localevents`.
   Only change to the module: the `_require_no_candidate_subgroups` docstring now
   says it applies to `memory_localevents`.
2. Tests: the draft's `tests/test_resource_observer.py` covers the refusal
   (`rw,pids_localevents` and `rw,pids_localevents,memory_localevents`), ordinary
   hierarchical success, and the preserved `memory_localevents` behaviours
   (scope report, child-cgroup refusal, inactive-controller allowance, regular
   file supplying local counters, scan-failure refusal). Fixed a stub in
   `tests/test_serve_resource_observer.py` that still used
   `pids_events_scope: "local"` as a valid response.
3. Release qualification: CHANGES, README, DESIGN-GUIDE (new section
   "Release qualification and the full R2 gate"), CONSUMERS, `cmru.toml` and
   `run-gate.toml` comments now say CMRU's release gate is `r0-r1` + `r3`, the
   complete `gate` lane adds the 24h `r2`, and the initial 1.0.0 release has no
   completed full-source R2 verdict. No R2 pass is claimed anywhere.
4. `docs/CONSUMERS.md` native-R2 example now declares
   `[lanes.assay-r2.resources] memory = "2g"` and `pids = 8192`, with a note
   that Run-Gate refuses the lane without both.
5. New tests in `tests/test_docs.py`: the published example loads through the
   installed Run-Gate `run_gate.load_config` (a plain import, so a missing wheel
   fails loudly, not skips); removing either ceiling makes the loader raise
   `GateError` ("requires explicit resources.memory and resources.pids");
   `cmru.toml` release commands are exactly `r0-r1` and `r3` while `gate`
   includes `r2`; every release surface states "no completed full-source R2
   verdict". The Run-Gate wheel is in the tester-unified image
   (`tester-unified/Dockerfile` installs it into `/opt/tester-venv`).
6. CP-20 entry: acceptance text rewritten for the pids refusal; update
   2026-10-15 records the independent-review finding; exact-tip gates and live
   observer acceptance remain pending.

## Evidence actually run (cockpit; no project tests, gates, docker)

- `python3 -m py_compile` on the three edited Python files.
- A throwaway probe script in the job tmp dir calling the cockpit venv's
  `run_gate.load_config` on generated configs: no resources, memory only, pids
  only → refused with the expected message; both → loads. Same probe on the
  extracted CONSUMERS example: loads; with either line removed → refused. This is
  a loader probe, not a pytest run.
- A plain-Python check that the "no completed full-source R2 verdict" phrase is
  present on all six release surfaces (same normalisation as the new test).

## Known open item (outside scope)

`tests/test_docs.py::test_package_contract_mirrors_canonical_contract_byte_for_byte`
requires `docs/RG55-INTERFACE-CONTRACT.md` to equal
`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`. The draft edited only
the cgroup-profiler copy; the canonical copy is another project and was not
touched, so this test will fail until the controller syncs it
(`cp scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
and commits it under run-gate-project's own review).
