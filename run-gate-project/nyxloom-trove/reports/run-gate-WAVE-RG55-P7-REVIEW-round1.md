# run-gate-WAVE-RG55-P7 — adversarial review, round 1

**Reviewer:** fresh Opus session (never a fork of any implementer or the
controller). **Branch** `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`, project `assay/`.
**Range reviewed:** `11ac5d67...8f972def` (55 files, +11164/-848), every
file and every type, plus the eight session BRIEFs, the LOG and the REPORT.
Blind phase first (design of record §1/§2 D-17/D-22/D-23, RW-28/29/33/36/
39/41/42, the implementer handoff, backlog B088/B090/B091/B092, CONSUMERS
and DESIGN-GUIDE on `main` and as changed, then the diff), reconcile
second.

## VERDICT

**REJECT** — five blockers. Three of them are measurement defects that the
mechanism itself introduces, each reproduced end to end against a real
`assay run` on a scratch project, not argued from shape:

- **B1** the liveness plugin turns a genuine KILL into a false SURVIVOR.
- **B2** the derived idle bound kills healthy candidates as `hung`,
  including ones the suite was about to kill honestly.
- **B3** `candidate.tests_completed` is read from the wrong path on any
  lane declaring `cwd`, and reports exactly the value the code and the
  docs say must mean something else.
- **B4** every native-R2 verdict this build produces is refused by the
  released assay 6.1.1 `assay verify`, under an unchanged `schema_version`.
- **B5** `CHANGES.md [Unreleased]` contradicts itself and carries no
  BREAKING/Changed note for three consumer-visible changes.

Everything else I attacked held up, including several things I expected to
break: the `os._exit` hygiene (terminal summary, pytest-cov data file and
XML report all survive), the RW-36 consent scoping, the `hung` bucket's
threading through the verdict/schema/`verify` re-derivation in both
directions, `--rejudge`/`--rejudge-outcome`, the auto-budget derivation,
and the A/B proof that `liveness = false` restores pre-B091 behaviour
exactly. The registered gate is GREEN on this tip on my own independent
re-run. The work is close and the design is right; the blockers are small,
local fixes — B1 is three lines, B3 is one call, B5 is editing.

**The shape of the problem, for the controller.** B1 and B2 are the same
failure in two places: a mechanism whose whole purpose is to stop assay
reporting an outcome it did not measure currently *manufactures* two
outcomes it did not measure — a survivor from a suite that killed the
mutant, and a `hung` from a suite that was about to. Both are reachable
with default settings on ordinary project shapes, both were reproduced end
to end on scratch projects, and neither is visible to the existing tests
because every liveness test uses a fast, fixture-free suite. Until they are
fixed, turning this on by default (`liveness = "auto"`) makes native R2
mutation scores less trustworthy than they were before B091, which is the
one outcome this package must not have.

---

## Blockers

### B1 — the plugin's `os._exit` default converts a real kill into a false survivor

`assay/src/assay/liveness.py:153` (`_EXIT_STATUS = 0`) and
`assay/src/assay/liveness.py:198` (`pytest_unconfigure`), both inside
`_PLUGIN_SOURCE`.

`_EXIT_STATUS` is only ever assigned in `pytest_sessionfinish`. pytest does
not call `pytest_sessionfinish` when the session never started
(`_pytest/main.py::wrap_session` calls it only at `initstate >= 2`), but it
*does* still run `pytest_unconfigure` from `Config._ensure_unconfigure()`
once `_do_configure()` has set `_configured`. On that path the plugin
`os._exit(0)`s with a status pytest never assigned — and
`mutation._classify_mutant_result` (`assay/src/assay/mutation.py:1590`)
maps exit 0 to **`survived`**.

Measured directly (pytest 8.4.2, same interpreter, same project, only the
plugin differing):

| failure shape | plain pytest | with the plugin |
| --- | --- | --- |
| pass / fail / collection error / conftest import error / usage error / no tests / `pytest.exit(returncode=7)` | 0 / 1 / 2 / 4 / 4 / 5 / 7 | identical |
| `conftest.pytest_configure` raises | **3** | **0** |
| `conftest.pytest_sessionstart` raises | **3** | **0** |

End-to-end proof through the real CLI. Scratch project
(`/tmp/.../scratchpad/p7exit`) whose `ctests/conftest.py` bootstraps at
configure time from product code:

```python
def pytest_configure(config):
    if not guard(0):
        raise RuntimeError("configure-time bootstrap broken")
```

Two lanes, identical in every respect except `judge.mutation.liveness`:

```
lane coff  (liveness = false)  -> outcome PASS  buckets {killed: 1, survived: 0}
lane cauto (liveness = "auto") -> outcome FAIL  buckets {killed: 0, survived: 1}
                                  MUTANTS_SURVIVED
```

The same mutant, the same suite: **killed without liveness, survived with
it.** The suite *does* catch the mutant; the plugin erases the evidence.
A project whose `pytest_configure`/`pytest_sessionstart` touches product
code (fixture registries, settings loaders, logging/DB bootstrap) is an
ordinary shape, and the default policy is `auto`.

This is also a documentation defect in the same place: CHANGES, CONSUMERS
and `liveness.py`'s own docstring all describe this as "`os._exit(rc)`
after `pytest.main` returns" / "right after the terminal summary prints".
On the diverging path `pytest.main` has not returned and no summary was
printed.

**Prescription** (three lines, in `_PLUGIN_SOURCE`):

```python
_EXIT_STATUS = None                       # sentinel: pytest never decided
...
@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    try:
        if os.environ.get("ASSAY_LIVENESS_EXIT") != "1":
            return
        if _EXIT_STATUS is None:
            return      # no sessionfinish: let pytest exit normally
        ...flush...; os._exit(_EXIT_STATUS)
```

Falling through when `sessionfinish` never ran restores the plain
behaviour byte for byte, and costs nothing: the hang B091 exists to cure
can only happen *after* tests have run, which implies `sessionfinish` ran.
Add the two diverging shapes above as tests (they are three-line
conftests), and fix the "after `pytest.main` returns" wording.

### B2 — `expect_next_event_within_s` is derived from `call` durations only, so a quiet setup/teardown phase is classified `hung`

`assay/src/assay/liveness.py:160` (`pytest_runtest_logreport` returns
unless `report.when == "call"`), `assay/src/assay/liveness.py:587`
(`baseline_slowest_test_s`), `assay/src/assay/liveness.py:635`
(`compute_expect_next_event_within_s`), and the `hung` predicate at
`assay/src/assay/liveness.py:946`.

The only progress signals the monitor has are (a) a new line in the
plugin's events file and (b) growth of the candidate's stdout/stderr
files. Neither exists during collection, session/module fixture setup, or
teardown — and the plugin only records the **call** phase, so
`slowest_test_s` never sees a slow fixture at all. `expect_next_event_
within_s` therefore collapses to its 15 s floor for essentially every
real suite (measured: 15.0 on all three scratch lanes I ran, and
`slowest_test_s` = 0.0003 for a module fixture that slept 3 s).

Reproduced against the real `LivenessRunner`, real clock, real `/proc`,
real pytest, with the bound the code itself derived for that project:

```
module-scoped fixture: time.sleep(50)        # I/O wait: no CPU, no output
expect_next_event_within_s = 15.0            # derived from this project's own baseline
result: *** FALSE HUNG *** after 31.0s       # killed; LivenessHungExpired
```

The documented fallback does not save it. Measured: during the whole 31 s
the candidate's `.stdout` and `.stderr` side files were **0 bytes** and the
events file did not yet exist — pytest under its default global capture
emits nothing to the real fds until the run ends, so the "plugin inactive
→ watch stdout growth" progress signal RW-33 names is inert for exactly
the one runner liveness is restricted to. The events file is therefore the
*only* live signal, and it is silent through collection, setup and
teardown.

**End-to-end through the real CLI, and it is worse than a mis-bucketing.**
Scratch project (`/tmp/.../scratchpad/p7slow`) with an ordinary
integration-suite shape — a module-scoped `service` fixture that sleeps
40 s — and a test that *asserts on the mutated function*
(`assert guard(0) is True`, which the `LtE→Lt` mutant fails). Default
`budget_per_candidate` (omitted → `"auto"`), `liveness` omitted → `auto`:

```
R0  PASS                                    # baseline suite green in 42.5s
plan      baseline_s 42.522  budget_per_candidate_s 127.566  derived True
          slowest_test_s 0.00037   expect_next_event_within_s 15.0
candidate outcome_bucket 'hung'   elapsed_seconds 31.196
R2  BUDGET_EXCEEDED / CANDIDATE_HUNG        {hung: 1, killed: 0, survived: 0}
```

The suite **would have killed** that mutant — the assertion is right there
— but liveness kills the candidate at 31 s, a quarter of the 127.6 s bound
assay itself derived, before the test body ever runs. So B2 is not only a
false `hung`; like B1 it destroys a real kill, by a different route.

Consequence: on any project with an I/O-bound session/module fixture
longer than ~15 s (a container or DB coming up, a migration, a fixture
that shells out and waits) **every** candidate is killed as `hung` — the
baseline is unaffected because it runs under `default_process_runner`, so
the lane looks healthy right up to the point where it reports an all-`hung`
sweep. That is the same class of failure D-17 was written to remove ("a
fixed bound fails on slow hardware"), reintroduced as a fixed 15 s bound
on idleness. It is default-on (`auto`) for every native R2 python lane
whose argv invokes pytest — in vbpub alone that is `cgroup-profiler`,
`damon-analysis`, `debian-install-v2`, `gstammtisch-guide`, `cmru`,
`nyxloom` and `ciu`.

**Prescription.** Calibrate against what the baseline actually measured
rather than against call durations:

1. Record every phase, not just `call`: in `pytest_runtest_logreport`,
   drop the `report.when != "call"` early return and carry `when` on the
   record (keep forwarding only `call` events to the progress stream if
   the A4 contract requires it). `report.duration` for a `setup` report
   *does* include the fixture — measured 3.001 s for the 3 s fixture
   above. `baseline_slowest_test_s` then becomes the max over all phases.
2. Better still, and cheap because the plugin already writes `t` on every
   event: take the baseline's own worst observed inter-event gap
   (including the leading gap from process start — add a `session_start`
   event from `pytest_configure` — and the trailing gap to
   `session_finish`) and use
   `max(3 × gap_max, 3 × slowest_phase_s, 15)`.
3. Whichever is chosen, document the residual limitation in CONSUMERS'
   liveness section: it currently documents the formula but not a single
   word about what makes it wrong.

### B3 — `candidate.tests_completed` is keyed on the wrong directory for any lane declaring `cwd`

Writer: `assay/src/assay/liveness.py:804` —
`LivenessRunner.__call__` keys the side file (via
`candidate_events_path`, `assay/src/assay/liveness.py:705`) on the `cwd`
it is handed, which `runner.execute_plan` sets to
`run_cwd = resolve_run_cwd(cwd, plan)`
(`assay/src/assay/runner.py:1188`, `:915`), i.e. `project_root /
lane.cwd`.

Reader: `assay/src/assay/mutation.py:2492` —
`liveness.candidate_events_path(liveness_events_dir, snapshot.project_root)`,
i.e. `project_root` **without** the `cwd` join.

They are two different paths whenever `lane.cwd` is declared, so the
reader misses the file. `count_test_events` returns `0` for an absent
path, and `0` is precisely the value the code comment
(`assay/src/assay/mutation.py:1569`: "`None` for every non-liveness
lane, never `0` (a `0` would claim the plugin ran and genuinely saw no
test…)") and CONSUMERS' `candidate` row both define as the *opposite*
fact.

**This is assay's own A-367, named in this very file.**
`runner.py:4712` performs the identical re-rooting with a comment that
reads: "which is why it comes from the one join, `resolve_run_cwd`, over
the very `CommandPlan` that ran, and not from a fifth hand-rederivation of
`project_root / lane.cwd`. The two agreed when this was written; A-367
exists precisely because 'agrees today' is not a property an edit to
either side preserves." `runner.py:2805` does the same. `mutation._run_one`
is the sixth site and is the one that hand-rederives.

Reproduced end to end (`/tmp/.../scratchpad/p7cwd`, lane with
`cwd = "sub"`):

```
candidate {'outcome_bucket': 'killed', 'tests_completed': 0}
.assay/liveness/candidates/8e67bc9b5af2959e.ndjson   <- written, 2 test events + session_finish
                           e461b29c…                 <- what the reader looked for (not written)
```

`candidate_events_path`'s own docstring claims it exists so writer and
reader "compute the SAME path from the SAME one implementation — never two
independent hashes of the same *cwd*". They are the same implementation
fed two different `cwd`s, which is the same defect one level up.

The test that claims this ground —
`test_candidate_progress_event_gains_tests_completed_from_its_own_events_file`
(REPORT line 471, "keyed by `cwd`") — uses a fake `process_runner` on a
lane with no `cwd`, where `run_cwd == project_root`, so it is structurally
incapable of seeing the divergence. Hollow for this property.

**Prescription.** Give `_run_one` the same resolved directory the runner
receives — `runner.resolve_run_cwd(snapshot.project_root, plan)`, the one
join, exactly as `runner.py:2805`/`:4712` already do — or (better) hand
`LivenessRunner` an explicit key rather than deriving it from `cwd` at
all, which removes the coupling entirely. Add a test with a lane that
declares `cwd` and asserts a non-zero `tests_completed`.

### B4 — every native-R2 verdict this build writes is refused by released assay 6.1.1, under an unchanged `schema_version: 11`

`Mutation.to_dict()` now emits `hung` unconditionally (`MUTATION_BUCKETS`
gained a sixth entry, `assay/src/assay/verdict.py:682`), and
`judgment.r2` gains `liveness` on every native R2 lane
(`assay/src/assay/runner.py:4848`). `verify._reject_unknown_keys`
compares a raw document's keys against the *running build's* `to_dict()`,
so an older verifier at the same schema version refuses the newer
document.

Measured, with the devcontainer's installed release:

```
$ python3 -c "import assay; print(assay.__version__)"   # 6.1.1
$ assay verify <verdict produced by this branch>
assay verify: schema: unknown mutation field(s): ['hung']
$ jq .schema_version <that verdict>
11
```

The reverse direction (new verifier, old document) is handled and tested
(`_mutation_of` / `_reconstruct_mutation` default `hung`, and
`tests/test_verify_hung_bucket.py::test_pre_hung_document_with_no_hung_key_still_verifies_clean`
passes) — that is the direction the handoff named, and it is fine. The
untested direction is the one that breaks, and it breaks for *every*
native-R2 document, not an edge case: `hung` is always emitted.

`schema_version` is the field a consumer uses to decide whether it can
read a document. Two mutually unreadable wire shapes must not both claim
11 with no note anywhere saying so. CONSUMERS' "Migration notes (v10 →
v11)" still says "This cut carries exactly ONE change, and it touches
exactly one field."

**Decision ask for the controller (RW-33 chose "additive under v11, no v12
cut"):** either (a) cut `schema_version` 12, or (b) keep 11 and state, in
CHANGES as BREAKING and in CONSUMERS' migration notes, that an assay
< 6.2.0 `assay verify` refuses any native-R2 verdict produced by 6.2.0+,
naming the exact diagnostic above so an operator who hits it recognises
it. Option (b) is defensible under assay's "every consumer pins its own
release" model, but it cannot ship undocumented — the `.assay-inbox`
release-notify flow and every cross-build `assay verify` in this estate
land on it. Blocking either way because no disclosure exists today.

### B5 — `CHANGES.md [Unreleased]` contradicts itself and has no Added/Changed/BREAKING section

`assay/CHANGES.md`, `[Unreleased]`, second bullet, closing sentence:

> **Not yet shipped in this entry:** the active liveness-monitoring loop
> and the new `hung` outcome bucket that distinguishes an idle stall from
> a genuine CPU-bound runaway (`budget_exceeded`) — tracked as open work
> on B091.

The fourth bullet **in the same section** ships exactly those two things.
Session 2 wrote the disclaimer; session 4 added the bullet that falsifies
it; no later session removed it. Release notes are the consumer-facing
artifact; this one tells a reader the mechanism is not shipped inside the
section that ships it.

Second half: every entry sits under `### Fixed (detail)`. Three of these
are not fixes:

- a **new lane key** (`judge.mutation.liveness`) and a **new CLI surface**
  (`--rejudge` / `--rejudge-outcome`) → *Added*;
- an omitted `judge.mutation.budget_per_candidate` now means **bounded**
  where it used to mean unbounded, so every existing native R2 lane that
  omitted the key changes behaviour and can now produce `budget_exceeded`
  candidates it never produced before → *Changed*, with a BREAKING note;
- `_refuse_unbounded_without_unit_bounds` now **admits** a lane
  (`budget = "unbounded"` + omitted `budget_per_candidate`) it used to
  refuse at load → *Changed*;
- `-p assay_liveness_plugin` is appended to the lane's argv by default, so
  `argv_effective` moves in every native-R2 pytest lane's verdict →
  *Changed*, worth naming for anyone diffing verdicts.

Plus B4's disclosure belongs here.

**Prescription.** Delete the stale "Not yet shipped" sentence; split the
section into `### Added` / `### Changed` / `### Fixed`; add the BREAKING
notes above.

---

## Decision asks for the controller

Named, not improvised — each is a product/design call the review should not
make on its own.

- **D1 (from B4) — cut verdict schema v12, or keep 11 and disclose?**
  RW-33 chose "additive under v11, no v12 cut", which is defensible under
  assay's pin-per-consumer model but is currently undocumented and
  measurably one-way-incompatible. Either is acceptable to me; shipping
  neither is not.

- **D2 — should `judge.mutation.liveness` default to `"auto"` or to
  `false` for the 6.2.0 release?** If B1 and B2 are both repaired in this
  package, `"auto"` is right and I have no objection. If the controller
  wants to land the package sooner and repair B2's calibration in a
  follow-up row, the honest shape is to ship `false` as the default for one
  release (the A2 `os._exit` cure and the `hung` bucket still work for
  anyone who opts in) rather than to ship a default-on mechanism with a
  known false-`hung` class. I am not asking for that — repairing B2 looks
  cheap — but it is the controller's call, not mine.

- **D3 (from B2) — which calibration replaces
  `max(3 × slowest_call, 15)`?** RW-33 fixed that formula, so changing it
  is a ruling, not an implementer's choice. My two candidates, in
  preference order, are (a) the baseline's own worst observed inter-event
  gap (the plugin already stamps `t` on every event; needs a
  `session_start` event for the leading gap), and (b) the max `duration`
  across *all* report phases rather than `call` only. (b) is a two-line
  change and fixes the measured case; (a) is strictly more faithful to
  D-17's "the PRODUCER publishes its own calibrated expectation" and also
  covers slow collection, which (b) does not.

---

## Non-blocking (S)

- **S1 — liveness writes into the judged tree with no ignore-guard, and
  never cleans up.** `runner._run_prepared_lane` materializes into
  `project_root / ".assay" / "liveness"` (`assay/src/assay/runner.py:3539`)
  — the consumer's live checkout, not a snapshot. On a project whose
  `.gitignore` does not already cover `.assay/`, run 1 succeeds and run 2
  of the same lane is refused `NO_MEASUREMENT/DIRTY_TREE`; reproduced
  (`/tmp/.../scratchpad/p7probe`, `EXIT1=1` then `EXIT2=3`, `git status`
  showing `?? .assay/`). assay guards the *identical* hazard for
  `--progress` with a load-time refusal that names the fix; liveness has
  no guard, no WARN, and is on by default. Every vbpub subproject happens
  to be covered by the root `.gitignore`'s `.assay/` entry (verified with
  `git check-ignore` for assay, cgroup-profiler, debian-install-v2, cmru,
  nyxloom, run-gate-project), which is why this is S and not B — but the
  R0+R2 lane shape has no doc path that tells a consumer to add it.
  Separately, the side files accumulate: each candidate gets a distinct
  `sha256(snapshot cwd)[:16]` triple (`.ndjson`/`.stdout`/`.stderr`) and
  nothing deletes them — measured 4 candidates → 12 files, second run →
  24. A 283-candidate lane leaves 849 files per run, each `.stdout`
  holding that candidate's full pytest output. *Prescription:* refuse (or
  WARN) at injection time when `<project_root>/.assay/` is not git-ignored,
  mirroring the `--progress` diagnostic; and delete each candidate's
  triple once `tests_completed` has been read (or key the whole directory
  per-run and remove it at sweep end).

- **S2 — `argv_invokes_pytest` matches a `pytest` token anywhere in argv.**
  `assay/src/assay/liveness.py:247`. `["make", "pytest"]` and
  `["tox", "-e", "pytest"]` both match; the second even passes the
  `liveness = true` load-time refusal (verified). assay then appends
  `-p assay_liveness_plugin` to `make`/`tox`, which is not pytest's
  argument to take. It fails loudly (the baseline breaks, and the argv is
  disclosed as `(appended: …)`), so this is S, not B. *Prescription:*
  make the rule positional as the docstring already claims ("matching how
  a shell would actually invoke `python -m pytest`"): `argv[0] == "pytest"`
  or `argv[0].endswith("/pytest")`, or an adjacent `-m pytest` pair — not
  any token at any index.

- **S3 — an unknown `--rejudge` id refuses with the wrong reason code.**
  Observed: `assay: ERROR/UNREADABLE_ARTIFACT: --rejudge named a candidate
  id not present in this lane's current candidate set …`. The message is
  good; the code is not. Every other rejudge operator error
  (`runner._refuse_bad_rejudge_config`, `assay/src/assay/runner.py:5775`)
  uses `BAD_LANE_CONFIG`, which is what an operator typo is. A consumer
  matching on `UNREADABLE_ARTIFACT` will conclude a state file is corrupt.
  *Prescription:* raise the unknown-id refusal with `BAD_LANE_CONFIG`.

- **S4 — the `liveness` refusal advertises spellings it refuses.**
  `assay/src/assay/config.py:3018` ends with "known spellings: auto,
  true, false", but `liveness = "true"` and `liveness = "false"` (quoted
  strings) are both refused — only the bare TOML booleans and the string
  `"auto"` load (verified). *Prescription:* say `true`/`false` (bare
  booleans) or `"auto"` (quoted), and drop the `LIVENESS_POLICIES` echo,
  which is the internal normalized vocabulary rather than the accepted
  TOML spellings.

- **S5 — monitor cost and unbounded sample list.**
  `_read_events_progress` (`assay/src/assay/liveness.py:660`) re-reads and
  re-`json.loads` the whole events file **every second** for the whole
  candidate lifetime — O(tests) per tick, for the life of each of `jobs`
  concurrent candidates. `cpu_samples`
  (`assay/src/assay/liveness.py:886`) is trimmed by nothing; its docstring
  bounds it by `budget_seconds / poll_interval_s`, which is unbounded
  under `budget_per_candidate = "none"`. *Prescription:* track a file
  offset and parse only the appended tail; drop `cpu_samples` entries
  older than `_HUNG_CPU_WINDOW_S` after the baseline sample is chosen.

- **S6 — `--rejudge-outcome`'s help text hand-transcribes
  `MUTATION_BUCKETS`.** `assay/src/assay/cli.py:250` lists "killed,
  survived, crashed (or its alias 'error'), budget_exceeded, equivalent,
  hung" as literal text. That is exactly the A-228 pattern this session's
  own `verdict.py` comment names as the root cause it is guarding against,
  and it is the one place the `MUTATION_BUCKETS` import was removed
  (`95d02f50`). *Prescription:* build the help string from
  `mutation.MUTATION_BUCKETS` + the alias map.

- **S7 — `mutation_pct`'s docstring was not updated for the new bucket.**
  `assay/src/assay/mutation.py` `mutation_pct` still explains the
  denominator in terms of `budget_exceeded` and `equivalent` only. The
  arithmetic is correct (`hung` is excluded by construction) but the
  docstring is now an incomplete enumeration of the closed vocabulary.

- **S8 — B091's FIXED evidence is incomplete and one sentence of it was
  untrue when written.** `assay/nyxloom-trove/4-backlog.md` B091 lists six
  hashes and omits `95d02f50` and `ee24ced6` — the two commits that
  actually made the registered gate green — and states "The real
  registered gate (`./run-gate.py tester-unified`) re-run green after the
  docs/backlog pass". The gate was RED after the docs/backlog pass (5
  failed / 4681 passed, 18:30Z) and needed those two commits.
  *Prescription:* add both hashes and correct the sentence to what
  happened.

- **S9 — `materialize_liveness_plugin` has no write guard.**
  `assay/src/assay/liveness.py:224`: `target.write_text` on an
  unwritable project root raises a bare `OSError` out of
  `_run_prepared_lane`, which is not an `AssayError` and so surfaces as a
  traceback rather than a clean refusal. Cheap to wrap into a
  `BAD_LANE_CONFIG`/`OUTPUT_WRITE_FAILED` refusal naming the directory.

- **S10 — R3 canary runs also get the injected argv, undisclosed.**
  `run_isolated_canaries(…, plan=plan, …)`
  (`assay/src/assay/runner.py:4479`) receives the liveness-augmented
  plan, so an R0/R1/R2/R3 python pytest lane (e.g. `nyxloom/assay.toml`'s
  `rigor = ["R0","R1","R2","R3"]`) runs every canary probe with
  `-p assay_liveness_plugin` on the argv. It is inert there
  (`ASSAY_LIVENESS_EVENTS`/`_EXIT` unset), so this is disclosure only:
  CONSUMERS says the mechanism applies to "R2 candidates only", which is
  true of `os._exit` but not of the argv injection. Say so.

---

## What I verified and could NOT break

Recorded so a later round does not re-spend the time.

**Attack 1 — `os._exit` hygiene.** Verified with real pytest 8.4.2, real
pytest-cov 7.1.0, real `--cov`:

- terminal summary printed, coverage terminal table printed, `cov.xml`
  written, `.coverage` SQLite file written with both files' line data —
  all present with `ASSAY_LIVENESS_EXIT=1`. The `sys.stdout.flush()` /
  `sys.stderr.flush()` pair is load-bearing and correct.
- The RW-28 incident reproduced and cured: a test leaking a
  `daemon=False` thread sleeping 120 s hangs plain pytest (killed by a 20 s
  `timeout`, exit 124) and exits 0 in 0.18 s with the plugin.
- Through the CLI, A/B on one project: `liveness = "auto"` → the mutant
  **survives in ~4 s** (the honest outcome D-23 asks for);
  `liveness = false` → the same mutant burns the full 30 s
  `budget_per_candidate` and lands in `budget_exceeded`. Exactly the
  contract.
- R0/R1 never get `ASSAY_LIVENESS_EXIT`: only `ASSAY_LIVENESS_EVENTS` is
  added, on a `baseline_plan` copy (`assay/src/assay/runner.py:3598`),
  and the coverage probe above shows coverage survives even when EXIT *is*
  set.
- Every hook body is `try/except: pass`; the plugin imports only
  `json`/`os`/`sys`/`time`/`pytest`, never `assay`. Exit codes for pass,
  fail, collection error, conftest import error, usage error, empty
  collection and `pytest.exit(returncode=7)` are byte-identical with and
  without the plugin (table in B1) — the two diverging shapes are B1.

**Attack 2 — the RW-36 injection rule.** Verified end to end:

- non-pytest argv (`python -m unittest discover`) with
  `liveness = "auto"` → **exactly one** WARN, no `-p` token anywhere in
  the executed argv, `judgment.r2.liveness = {active: false, reason:
  "argv-does-not-invoke-pytest", plugin: null}`, `plan` event's
  `slowest_test_s`/`expect_next_event_within_s` both `null`;
- `liveness = true` on a non-pytest argv → refused at LOAD with a clear
  message; on a non-python language likewise;
- `liveness = false` → off, no WARN, `reason: "declared-false"`;
- the liveness token goes through `argv_appended` and is disclosed on the
  run line as `(appended: -p assay_liveness_plugin)`;
  `cli_argv_appended` keeps `execute_plan`'s `allow_argv_append` refusal
  scoped to CLI tokens (both directions covered by
  `test_liveness_injected_plan_runs_through_execute_plan_despite_no_consent`
  and `test_a_plan_with_real_unconsented_cli_appended_argv_is_still_refused`);
  my probe lanes all declare `allow_argv_append = false` and ran;
- `PYTHONPATH` is prepended, never replaced (`os.pathsep.join`), and I
  confirmed a lane's own `PYTHONPATH = "."` survived injection in a real
  run;
- `judge.mutation.liveness` on an ingested lane is refused by
  `_load_ingested_mutation`'s `orchestration_only` set.

**Attack 3 — `LivenessRunner` scope and loop.** `LivenessRunner` is
constructed at exactly one site, under `if liveness_injected`
(`assay/src/assay/runner.py:4275`); every other path passes
`process_runner` verbatim — grepped, and confirmed live (the
`liveness = false` lane took the plain `subprocess.run(timeout=…)` path
and produced `LANE_TIMEOUT`). `start_new_session=True` makes the child its
own process-group leader, and `_kill` reads the pgid from a child that is
never reaped before `proc.wait()`, so `killpg` cannot reach another
group. Any `/proc` failure yields `cpu_now = None` → `cpu_growing = True`
→ never hung (`test_proc_read_failure_never_declares_hung`, and the
`ValueError`/`IndexError` paths out of `_pid_cpu_ticks` land in the same
blanket `except Exception`). The two boundary tests pin `>=` on both
thresholds. The two real-subprocess e2e CLI tests (`hung` for an in-test
`t.join()`, `budget_exceeded` for a busy loop) do exercise a real
`-m pytest` child and a real `/proc` walk — they are not fakes. B2 is
about the *threshold*, not the loop.

**Attack 4 — `hung` threading.** `MUTATION_BUCKETS` is now the single
source (bucket dict and `Mutation(**…)` construction both derive from it);
`mutation_pct` never reads `hung`; `judge_mutation` returns
`BUDGET_EXCEEDED/CANDIDATE_HUNG` (the session-4 precedence gap is real and
fixed). I forged verdict documents against the shipped
`r2_budget_exceeded_candidate_hung.json` fixture and `assay verify`
refused both directions:

```
as shipped                                   rc=0
forged: hung entries but outcome PASS        rc=1  "…disagrees with the re-derived judgment…(BUDGET_EXCEEDED, CANDIDATE_HUNG)"
forged: hung entries relabelled killed       rc=1  "…disagrees…(PASS, None)"
```

The W7 locked schema copy is now byte-identical to
`src/assay/schemas/verdict.schema.json` (`diff` → identical). The drift
session 7 fixed *was* a symptom of a missing test — but the test that
caught it (`test_acceptance_v11.py`) already existed and was simply never
run until the first real gate; the REPORT says so honestly.

**Attack 5 — the A4 progress stream.** Confirmed on real runs: `test`
events appear for the baseline only and never per candidate (one baseline
test → exactly one `test` line, four candidates → zero more); the `plan`
event's `slowest_test_s`/`expect_next_event_within_s` come from one
computation; `"plan"`/`"test"` are in `PROGRESS_EVENTS` and
`ProgressStream.emit` refuses anything outside it. run-gate's
`ProgressWatch._newest` (`run-gate-project/run-gate.py:3756-3775`) selects
only records carrying an integer `candidate_index`, so `plan`/`test`
records are invisible to it and the `candidate` cadence is unchanged —
RG-36/RG-41 is safe. (`tests_completed` on that same record is B3.)

**Attack 6 — `--rejudge`.** Verified live on a 4-candidate state store:

```
resume     {'candidate_total': 4, 'rejected_total': 0, 'rejudged_total': 1, 'resumed_total': 3}
candidates {'candidate_total': 4, 'pending_total': 1, 'selected_total': 4}
candidate  {'candidate_id': '45d06373…', 'outcome_bucket': 'survived'}
```

Exactly the named candidate re-executed, `rejudged_total: 1`, nothing else
touched. `--rejudge`/`--rejudge-outcome` without `--resume` refuse at the
CLI layer; an unknown bucket refuses by name; `error` → `crashed` works and
never reaches `MUTATION_BUCKETS`. The unknown-id refusal fires before any
record loads (it costs one baseline run, which CONSUMERS discloses and
which is unavoidable — the candidate set does not exist before site
collection). The "ids are `candidate_id()` digests, not
`MutantOutcome.identity`" caveat is documented in CONSUMERS; the refusal
enforces it only as "not in the current set" (see S3 for the code).

**Attack 7 — `"auto"` budget.** `auto_budget_per_candidate_seconds` is
`max(3 × baseline, baseline + 60)`; `run_mutation` refuses both
`budget_per_candidate_auto=True` and an explicit seconds value together;
`judgment.r2.budget_per_candidate_derived_s` rides on one computation and
is read back, never recomputed; an explicit duration leaves it `null`
(verified live: `derived_s: None` with `budget_per_candidate = "30s"`);
`"none"` WARNs on declare and still trips the `budget = "unbounded"`
admission rule while an omitted key no longer does; `assay plan`'s 60 s
fallback covers all three non-duration spellings; the `diagnostics=None`
+ `"none"` regression is handled as its own branch.

**Attack 10 — the gate.** See the gate section below.

**Records discipline.** The LOG's self-hash rule holds: all thirteen
`assay/`-touching commits in `11ac5d67..8f972def` (`de32bb91`, `f649a249`,
`f4fa1788`, `e27b107b`, `44dd12ca`, `99463ae5`, `d1540eda`, `5baf2670`,
`c15f6040`, `afda58fd`, `b3f31506`, `95d02f50`, `ee24ced6`) have their own
LOG entry keyed by their own hash, none missing, none invented. The five
session-8 gate repairs are real fixes, not cosmetic: I read
`95d02f50` (five genuinely-unused imports removed; both untrusted
`json.loads` sites widened to `except (ValueError, RecursionError)` — the
side files really are written by the consumer's own interpreter, so the
B072/B074 bar really does apply) and `ee24ced6` (the `_assert_complete`
change *adds* a positive/finite sanity check on a value no fixture can
hand-inject, then strips it from a **copy** — it does not weaken the
exact-match assertion, and no test was skipped or de-asserted).

**Survivor table N/A:** I accept the reading. `assay/assay.toml` declares
exactly one lane, `[lanes.tester-unified]` with `rigor = ["R0"]`, and the
file's own header cites A-046/A-133 ("This file stays R0-only
PERMANENTLY"). There is no assay-on-itself R2 lane to produce survivors,
and the `assay-r1`/`assay-r2`/`assay-r3` names in sibling packages' logs
belong to `run-gate-project`'s lane file, not this one. The B091 mechanism
is instead covered by the ≥3-planted-mutant table against a fixture
project, which is the right substitute.

---

## Why the existing suite could not catch B1/B2/B3

All three are invisible to the tests as written, and the reason is the same
in each case — worth saying because the repair must close the hole, not
just the symptom:

- every liveness test's fixture project is a **fast, fixture-free,
  conftest-free** pytest suite (no `scope="module"`/`scope="session"`
  fixture, no `pytest_configure`, no real sleep anywhere in
  `test_liveness*.py` or the two e2e CLI tests), so neither the
  `_EXIT_STATUS` divergence (B1) nor the setup-phase blind spot (B2) has an
  object to appear on;
- `test_liveness_runner_monitor.py` drives the loop with a **virtual
  clock** (`monotonic`/`sleep` fakes), which is exactly right for the
  decision logic and structurally unable to notice that the *threshold fed
  into it* is derived from an incomplete measurement;
- the `tests_completed` test (B3) uses a lane with no `cwd`, where the two
  paths coincide by accident.

**Repair asks, therefore:** each fix lands with a test whose fixture
project has the property that was missing — a `conftest.pytest_configure`
that raises (B1), a module-scoped fixture that idles past the derived bound
(B2, and it can use a short bound by lowering the constants through the
existing constructor injection rather than sleeping 40 s in CI), and a lane
declaring `cwd` (B3).

---

## Seams between the eight sessions

Each blocker is a seam, which is what the handoff predicted:

1. **B1** — session 2 wrote `_EXIT_STATUS = 0`; session 5 rewrote the same
   plugin string (the `repr`→`json.dumps` fix) without questioning the
   default; sessions 3–8 then all described the mechanism as "`os._exit`
   after `pytest.main` returns" in code comments, CHANGES and CONSUMERS,
   which made the assumption look settled.
2. **B2** — session 5 derived `slowest_test_s` from the plugin's `call`-only
   events; session 6 (A4) surfaced it on the `plan` event as a disclosed
   calibration figure. Nobody checked what the plugin does *not* observe.
3. **B3** — session 4 built `LivenessRunner` keyed on the `cwd` it is
   handed; session 6 read the file back from `snapshot.project_root`. Both
   call it "the candidate's cwd"; `execute_plan`'s B043 re-rooting makes
   them different.
4. **B5** — session 2's "Not yet shipped in this entry" was falsified by
   session 4 and never removed.
5. **S8** — session 7 wrote B091's FIXED evidence before the gate had ever
   passed; session 8 fixed the gate and did not revisit the row.

---

## Claims I could not verify

- **Plugin import-safety under pytest 7.** Only pytest 8.4.2 is installed
  here and I did not add a network install under the host-load rule. By
  inspection every API used (`pytest.hookimpl(trylast=True)`,
  `report.when/.nodeid/.outcome/.duration`,
  `pytest_sessionfinish(session, exitstatus)`, `pytest_unconfigure(config)`)
  is stable across pytest 6/7/8, and pytest 8 is verified by execution.
  Unverified by execution on 7.
- **The exact wheel-phase counts on the green gate.** The green run is on
  record in `assay/.run-gate/history.json` (commit
  `8f972defdcf494e9912269f3f5d15bcee868473b`, `dirty: false`, `exit_code:
  0`, `outcome: "pass"`, started `19:23:18Z`, `1213.676 s` → finished
  ~19:43:32Z, which matches "19:44Z"). Its LOG, however, was overwritten:
  a second `tester-unified` run was launched into the same
  `p7-gate5.log` path at ~19:43 and was SIGTERMed (`exit -15`) at ~19:51.
  The "4686 passed, 20 skipped" figure quoted in my dispatch is from
  `p7-gate4.log`, which is the `NO_MEASUREMENT/HEAD_CHANGED` run that
  exited 1. So the verdict is corroborated; the counts for that specific
  run are not. My own independent re-run is recorded below.
- **E-002 tool-call telemetry** (370/~80/"well over 60"/252/275/…) — taken
  from the BRIEFs, not independently measurable from here.

---

## Gate (my own independent re-run)

**GREEN.** `cd assay && ./run-gate.py tester-unified`, launched by me from
the worktree at `8f972def`, `nice -n 19 ionice -c 3`, detached, verdict read
in a separate step:

```
ASSAY_GATE_PHASE=pyflakes-clean
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

`assay/.run-gate/history.json` `latest`:

```json
{"commit": "8f972defdcf494e9912269f3f5d15bcee868473b", "dirty": false,
 "duration_seconds": 1343.502, "exit_code": 0, "outcome": "pass",
 "lane": "tester-unified", "started_at": "2026-09-12T19:53:37Z",
 "worktree": "/workspaces/vbpub/.worktrees/assay-liveness"}
```

Every phase passed: `wheel-installed` (25), `attestation-hardened` (13),
`verdict-v5-accepted` (17), `lane-schema-v2-successors-verified` (34 frozen
templates), `verdict-v6…v10-hard-cut-verified`,
`verdict-v11-successors-verified` (110 — W7's suite, confirming the schema
re-sync), `tester-unified: PASS (exit 0)` for the self-hosted wheel lane,
`judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
`topos-qualified`, `cmru-b006a-qualified` (B006(a) WI-5 receipt
`outcome=PASS exit_code=0`), `independent-self-hosting-passed` (7),
`pyflakes-clean`. HEAD was `8f972def` before and after and the worktree was
clean throughout; I committed nothing.

This is the second independent green on this exact tip. The first (the
controller's, `19:23:18Z` + `1213.676 s`) is also in `history.json` and
corroborates the dispatch's "GREEN at 19:44Z" — see "Claims I could not
verify" for what about it is *not* corroborated.

Also worth the controller's attention as housekeeping, not a finding
against the branch: a **second** `./run-gate.py tester-unified` was
launched into `scratchpad/p7-gate5.log` at ~19:43Z (right as the green run
finished), overwriting that run's log, and was SIGTERMed at ~19:51Z
(`run-gate: lane 'tester-unified' exit -15`). It left no history row and no
container, but it is why the green run's own transcript no longer exists.

`cmru status --project assay` (from the worktree): clean, and it agrees
with RW-29's planned release —

```
Project   Last Tag        Bump     Next Version
assay     assay-v6.1.1    minor    assay-v6.2.0
```

Note for the release step: the `minor` bump is `cmru`'s own read of the
change range. If the controller resolves B4 by cutting verdict schema v12
that reading should be revisited; if B4 is resolved by disclosure alone,
`minor` still under-states a wire-format change that older verifiers
refuse, which is the substance of B4.

---

## Host-load compliance

PSI read before every launch (`/proc/pressure/memory`). My gate was
launched at `full avg10 = 0.15` with one other gate container live
(`run-gate-vbpub-r2-…`, P1's mutation run) — two estate-wide, at the
limit — under `nice -n 19 ionice -c 3`, and `docker update --cpus=3` was
applied to its container (`infallible_sutherland`, image
`tester-unified:local`) as soon as it appeared. Peak observed during the
container phase: `full avg10 = 9.60`, at which point I stopped launching
probes and did read-only work until it fell back. Every scratch probe ran
serially, `nice -n 19 ionice -c 3`, outside the repository, in
`/tmp/claude-1003/.../scratchpad/`. No container was created by me other
than the gate's own, and it is removed by run-gate itself. I committed
nothing and edited no tracked file in the worktree; HEAD stayed at
`8f972def` for the whole review (`git -C .worktrees/assay-liveness
rev-parse HEAD` checked before the launch and after).
