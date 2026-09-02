# run-gate wave "resumable, observable gate" — implementer LOG

Append-only. One entry per commit / gate / decision, with measured numbers.
Wave prompt: `run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`.
Branch `feature/run-gate-wave-resumable`, forked from `main` at `f6d3a858`.
Target: run-gate 23.4.0, `__revision__ = 34`.

---

## E1 — orientation (2026-09-02)

Read: wave prompt (RW-1..RW-8), backlog RG-32/34/35/36/38, SPEC R-04/R-08/
R-14/R-15/R-16/R-26/R-30a/R-35/R-36(a-i)/R-38, `run-gate.py`
(`run_container_lane` 2469-2560, the R-36 history store 724-1040,
`cmd_doctor` 2081-2260, `_validate_lane` 151-256, `build_assay_inner`
2347-2429, `main` 2886-3149), `tests/test_run_gate.py` (fixtures 55-232,
`TestResumeAndProgressAlways` 5920-6043, `make_history_repo` 4777),
CHANGES `[Unreleased]`, README lane schema, AGENTS.md "Manual tester-unified
gate runs", WAVE-PLAN §1/§4 D3-D5/§4a.

Seams identified:
- `run_container_lane:2469` — the loop RG-35 and RG-36 rewrite.
- `_record_invocation:958` + `_write_history_store:949` — the store
  discipline (lock + atomic rename + `paths_are_git_ignored`) the inflight
  record reuses verbatim.
- `finish_run_record:852` — duration comes from `_started_monotonic`; a
  re-attached run's duration must come from the CONTAINER's start (RW-3).
- `_validate_lane:151` — the pin table is validated for `sha256`/`version`
  and NOTHING else, which is why `pins.assay.budget` was accepted (RG-32).
- `cmd_doctor:2081` — the report shape RG-34's `[WARN]` joins.

## E2 — RG-35 red proof (controlled wrong implementation), 2026-09-02

The pre-fix `run_container_lane` IS the wrong implementation. New test
`TestReattachAcrossADeadClient::test_a_killed_client_leaves_a_container_the_next_run_re_attaches_to`
drives it end to end against a STATEFUL fake docker (`fake_docker_stateful`:
`run -d` creates a container file, `inspect` answers from it or exits 1 like
real docker's `No such object`, `wait` returns the recorded code, `rm -f`
destroys it, `<state>/.hang` makes `logs -f` block).

Measured, pre-fix (`nice -n 19 ionice -c 3 python3 -m pytest -q -p no:randomly
-k TestReattachAcrossADeadClient`, 2.00 s, 1 failed):

```
E  AssertionError: a SECOND container was started for a lane that already had
   one running: [... '--name', 'run-gate-repo-suite-1764662-1788385130' ...],
                 [... '--name', 'run-gate-repo-suite-1764674-1788385130' ...]
E  assert 2 == 1
```

Two `docker run -d` for the SAME lane, SAME worktree, SAME commit, the first
container still running — the one-gate rule broken by the tool, exactly as
RG-35 describes.

## E3 — RG-35 implemented (rev 34, SPEC R-39), 2026-09-02

Code: `INFLIGHT_*` constants; `_write_json_atomic` (was `_write_history_store`)
and a new `_acquire_store_lock` extracted from `_record_invocation` so the
inflight writer reuses the history store's lock/atomic-rename discipline
verbatim rather than growing a second one; `inflight_dir/_path/
_written_paths`, `load_inflight_record`, `write_inflight_record`,
`clear_inflight_record`, `container_state`, `_fmt_age`,
`adopt_inflight_start`, `record_lost_run`; `assay_verdict_rel` /
`assay_progress_rel` / `assay_artifact_paths` (one construction of the two
`.assay/` paths, now shared by `build_assay_inner`, the inflight record and
— next commit — the progress watch); `await_container()` as the ONE finish
for all three arrival paths; `resolve_inflight()` taking the five-way
decision before anything is built; `--fresh` + its refusals; usage text.

Decisions taken where the rulings did not reach (all in the REPORT):
- **An un-ignored `.run-gate/` warns, it does not fail the lane.** RW-1 says
  "refuse to write it … with the same remedy history.json gives"; history's
  remedy is a warning, so refusing to WRITE is not refusing to RUN. The one
  BREAKING config change in this wave is RG-32's, declared as such.
- **A `docker inspect` answer that parses as neither a state nor a gone
  signal refuses (exit 3)** instead of being read as gone — guessing gone
  starts the duplicate this feature exists to prevent.
- **`record_lost_run`'s entry is superseded in `latest` by the fresh run's
  own record in the same invocation** (aborted entries never join the trend
  series). It is still written, per RW-3, and is directly tested.

Measured: `-k "Inflight or Reattach or FreshFlag"` → **32 passed** in 8.30 s.
Full suite: **440 passed, 2 skipped** after fixing one stale monkeypatch
target in `TestHistoryStoreSafety` (`_write_history_store` →
`_write_json_atomic`).

Gate (pre-commit, `./run-gate.py selftest --allow-dirty`, verdict read in a
separate step from `scratchpad/selftest-rg35.log`): **exit 0**, `449 passed,
2 skipped … in 83.64s`, `diff-coverage OK: 0/0 changed executable lines`.
NOTE: `tools/coverage_gate.py` diffs `base..HEAD` (committed only), so an
uncommitted change measures 0/0 — the meaningful diff-coverage number for
each item comes from the gate run taken AFTER its commit, and that is the
one recorded per item below.

## E4 — RG-35 LIVE acceptance probe, 2026-09-02 21:54-21:55 UTC

Host rule observed: waited (background poll, 30 s interval, no foreground
sleep) for assay Wave D's `tester-unified:local` gate container
(`amazing_northcutt`, 21 min in, running cmru_b006a qualification) to
finish; `docker ps` showed no `tester-unified:local` and no
`run-gate-vbpub-*` before starting; the probe container was capped
`docker update --cpus=3` immediately after start and removed in a trap.

Real `tester-unified:local`, real repo at `/tmp/rg35-live-probe`, lane
`probe` = 14 x (echo; sleep 5). Client 1 SIGKILLed 8 s in. Result:

- container survived the client (`Up 8 seconds` after the kill);
- invocation 2 printed `run-gate: re-attached to
  run-gate-rg35-live-probe-probe-1840747-1788386064 (started
  2026-09-02T21:54:24Z, running for 0m 08s)` and **started no container**;
- `docker logs -f --since <started_at>` replayed the run from `tick 1`, not
  from the attach point;
- exit 0 at 21:55:35, container removed, inflight record cleared;
- history holds ONE entry, `duration_seconds: 70.858` — measured from the
  CONTAINER's start (21:54:24), not from invocation 2's attach at 21:54:33
  (which would have read ~62 s). RW-3 proven live.

Full transcript in the REPORT.

## E5 — RG-35 coverage round + commit `cee805ce` (amended)

First post-commit gate run was RED on the diff-coverage half only
(`449 passed`, `diff-coverage FAIL: 109/149 (73.2%)`): most of the new
tests drove the SUBPROCESS entrypoint, which coverage cannot see.
`tools/coverage_gate.py` diffs `base..HEAD`, so this only becomes visible
after the commit — recorded here as a standing note for the rest of the
wave. Converted the branch tests to in-process `main()` calls (the
`TestHistoryInProcess` pattern), kept the killed-client test as a
subprocess (it needs a real process to kill), and added
`TestContainerFinishPathsInProcess` for the pre-existing finish/refusal
behaviours that RG-35 MOVED into `await_container` (a moved line counts as
changed). Second round: `146/156 (93.6%)`. Third: **`diff-coverage OK:
156/156 changed executable lines covered (100.0%)`, `455 passed, 2 skipped
in 57.60s`, lane exit 0** — log
`scratchpad/selftest-rg35-cov2.log`.

Also added in this round: a `run-gate: rev 34 | lane <n> | re-attach — no
new container was started` line after the re-attach/collect disclosure. The
usual `rev | lane | env | slice` header belongs to a run this client
STARTED; printing it on a re-attach would claim mounts and a slice this
invocation never chose.

## E6 — RG-32 (rev 34, SPEC R-08a), BREAKING, 2026-09-02

Ruling RW-7: refuse, do not rename. `_validate_lane`'s pin loop gains (a) a
`budget`-specific refusal naming the value's real owner
(`assay.toml [lanes.<assay_lane>]`) and (b) `_check_keys(pin, {"sha256",
"version"})` — the durable half: a pin table that accepted anything is HOW
`budget` came to live there. Message is RW-7's verbatim, with
`<assay_lane>` substituted from the lane.

No red-first proof possible in the usual sense: the pre-fix implementation
is "accept silently", so the controlled wrong implementation IS the
absence of a check, and the four new tests in `TestPinKeysAreValidated` fail
against rev 33 by construction (the `pytest.raises` never fires).

Estate sweep (`grep -rn budget --include=run-gate.toml`): every `budget` in
the vbpub estate is a LANE-level one; no consumer here declares
`pins.*.budget`. dstdns does (`sql-mutation`,
`assay-p129-enumeration-cursor`) — controller notifies dstdns-23; the key
must be deleted BEFORE upgrading, since it now refuses at load.

Docs: SPEC `R-08a` + the `R-08` pins clause; CHANGES `### BREAKING` with the
one-deletion migration; CONSUMERS lane-schema pin block; backlog index row
(RG-32 had none) + section acceptance/status.

Measured: `-k PinKeys` → 4 passed in 1.69 s; full file 451 passed, 2 skipped
in 66.47 s.

## E7 — RG-34 (rev 34, SPEC R-30b), 2026-09-02

Ruling RW-8: doctor warns, run-gate does not rewrite argv, and does not
refuse (the same argv is correct under a full-repo mount). New doctor check
"2b", reading the DECLARATION only so it still answers for a lane whose
environment failed to resolve; one `[OK]` when there is at least one
container command lane and nothing to flag (R-30a's "so a reader can tell
it ran"). Six tests, including the three non-warning shapes and the two lane
kinds outside the check.

Estate sweep with `tomllib` (not grep) over every `*/run-gate.toml` in
vbpub: **no vbpub lane trips it**. dstdns's `schema` lane does; that edit is
dstdns's own.

Measured: `-k DoctorNamesUnprefixed` → 6 passed in 1.74 s.

Wave note recorded here because it cost a round: **`./run-gate.py selftest`
on a DIRTY tree reports a misleading diff-coverage number.**
`tools/coverage_gate.py` takes its changed-line numbers from `git diff
base..HEAD` (committed) but coverage.json from the file on disk, so any
uncommitted edit shifts the two apart and lines that ARE covered are
reported uncovered (RG-32's round: `175/177 (98.9%)` dirty →
`153/153 (100.0%)` on the same code once committed). The verdict that counts
is the one taken with a clean tree, after the commit.
