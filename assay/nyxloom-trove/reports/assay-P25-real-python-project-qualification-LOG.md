# P25 — real Python-project qualification — implementation LOG

Fresh Sonnet xhigh implementer. Worktree
`/workspaces/vbpub/.worktrees/assay-P25-real-python-project-qualification`,
branch `feat/assay-P25-real-python-project-qualification`, created from the
Sol freeze commit `f311dc3d3c826f5f78119205244694168a9e11c5` (sole parent
`9f522a72d37b9cb5beb1939ceca1978c9fc4ef23`).

## Locked packet verification (before any edit)

Mechanically verified all 19 `carve-assets/P25/fixture-manifest.json` entries
byte-for-byte via SHA-256, plus the manifest's own self-hash:

```
19/19 file hashes match
fixture-manifest.json self-hash:
eedb73711d8ad56b03ea11230b2f0f3d9e929683e15195453e89a0035a9a6ffd
```

Matches the frozen value recorded in `reports/assay-P25-JIT-CARVE.md` and the
carver's `READY_FROM_SOL` section exactly. Re-verified again after
implementation (see "Post-implementation locked-asset re-check" below) —
still 19/19, self-hash unchanged, confirming no carve-asset was edited.

## Controlled red (verified via `git stash`, not merely assumed)

The `9 passed, 4 failed` controlled red the freeze recorded was reconfirmed
directly against the real skeleton-plus-copies state by stashing this
package's implementation (`git stash push -u`) and re-running the locked
acceptance command from `assay/`:

```
python -m pytest nyxloom-trove/carve-assets/P25/test_acceptance.py -q -p no:randomly
4 failed, 9 passed in 0.35s
```

The four reds were exactly the frozen classes named by the freeze (missing
production-harness promotion, absent byte-exact fixture/release promotion,
absent registered-gate wiring/phase, and the consequent
no-alternate-production-route check), by test id:

- `test_production_harness_promotes_the_skeleton_without_todos`
- `test_production_fixture_and_release_promotions_are_byte_exact`
- `test_registered_gate_uses_current_wheel_and_emits_the_p25_marker`
- `test_no_production_route_selects_an_alternate_wheel_or_topos_outer_docker`

None were collection or setup failures. The stash was popped immediately
after (`git stash pop`), restoring the implementation; `git status` was
checked clean-modulo-the-implementation before resuming.

## Implementation

1. **`gate/python/qualify_topos.py`** — copied byte-identical from
   `nyxloom-trove/carve-assets/P25/skeleton/qualify_topos.py`, then completed
   the six TODO bodies without changing any frozen signature, constant,
   scenario object, or the already-frozen `normalize_artifact`/
   `compare_complete_artifact` pair:
   - `install_locked_release` — P24-verifier → requirements file → offline
     `pip --require-hashes` install into a fresh release venv, `.pth`
     tester-site injection, and the same installed/runtime-version and
     import-location purity assertions the carver's tracer proved
     (`install_locked_release` in `probe_topos_qualification.py`).
   - `materialize_scenario` — delegates to a new private
     `_materialize_negative` shared by both the five named `SCENARIOS` and
     the seven integrity-negative checks: `git archive --format=tar` export
     of `.gitignore` + pinned `topos/`, exact 966-entry/three-absolute-
     symlink verification and deletion, force-`add` to a fixed-identity,
     fixed-date baseline commit (965 entries), then the scenario's own
     probe/test/wrapper/`assay.toml` on a second commit.
   - `run_scenario` — invokes the declared Assay executable, asserts
     commit/version/exit-code/outcome/reason-code against the frozen
     `ScenarioSpec`, computes the copied-Topos comparator when a witness
     exists (asserting the frozen parity/asymmetry per
     `spec.compare_with_topos`), and asserts the disposable repository is
     clean afterward.
   - `qualify` — orchestrates: `install_locked_release`, all five scenarios
     (current owner for four, the release owner for `release-targeted-pass`),
     the literal hand-manifest cross-check (`_check_line_manifests`), the two
     whole-document `compare_complete_artifact` comparisons (current-full-pass
     against `pass-v4-template.json`, missing-line against
     `missing-v4-template.json`), the seven-item integrity matrix, and a
     before/after `git status` snapshot of `source_repo` (raises if it ever
     changed).
   - Private helpers added (degrees of freedom, no public shape changed):
     `_run`'s `env=` parameter, `_git`, `_consumer_status`,
     `_fixed_identity`, `_copy_fixture`, `_extract_pin`, `_seed_baseline`,
     `_write_lane`, `_materialize_negative`, `_topos_comparator`, `_invoke`,
     `_check_missing_profile`, `_check_dirty_consumer`, `_check_base_is_head`,
     `_check_command_dirt`, `_check_command_head_move`,
     `_check_wrong_source_root`, `_check_universal_pass_mutation`, and two
     private module constants (`_TESTER_VENV_PYTHON`, `_TESTER_VENV_PATH`)
     naming the exact verified tester-unified image facts the handoff froze
     — production code never branches on their value; they exist so a local
     test can monkeypatch them without ever touching the tracked file (see
     "Manual real-pipeline verification" below).
2. **`gate/python/fixtures/P25/**` and `gate/python/release/P25/**`** — every
   file under the locked packet's `fixtures/` and `release/` promoted
   byte-for-byte (`diff -rq` empty both ways).
3. **`tools/tester-unified-gate.sh`** — inside `run_inner`, between
   `run_self_hosted_lane` and `run_independent_witness`, added the inline
   invocation of `gate/python/qualify_topos.py` against
   `$scratch/run-venv/bin/assay` (the wheel `run_self_hosted_lane` just
   proved) and the same `$version`. `ASSAY_GATE_PHASE=topos-qualified` is
   printed only after the harness's own `ASSAY_P25_TOPOS_QUALIFIED=1` success
   marker is checked exactly once (`set -euo pipefail` already aborts the
   whole script if the harness itself exits non-zero, since a failing
   command substitution assignment is a `-e` trigger — confirmed
   empirically). All prior P24 phase markers, the outer cgroup/host-bind
   derivation, `--network=none`, and the final
   `ASSAY_REGISTERED_GATE_COMPLETE=1` receipt are unchanged.
4. **`nyxloom-trove/nyxloom.toml`** — added a P25 comment paragraph
   documenting the new phase and widened `timeout_seconds` from `1800` to
   `3600`: the primary scenario alone runs the full 2,923-test Topos suite on
   top of everything the self-hosted lane and independent witness already
   do, and 1800s was sized before that addition existed.
5. **`docs/DESIGN-GUIDE.md`** — added §15 ("Real Python-project qualification
   harness (P25)") documenting the disposable-baseline construction, the
   two-owner wheel split, the three-witness comparison, and the integrity
   matrix. §13 (already present from the Sol freeze commit) already states
   the qualification-not-adoption product claim and the exact three-symlink
   adoption precondition — §15 is the implementation-level companion, mirroring
   how §14 documents P24.
6. **`tests/test_python_qualification.py`** (new, focused, ordinary-suite
   collected) — see "Focused and ordinary results" below.

## Focused and ordinary results

Locked acceptance, from `assay/`:

```
python -m pytest nyxloom-trove/carve-assets/P25/test_acceptance.py -q -p no:randomly
13 passed in 0.30s
```

New focused suite:

```
python -m pytest tests/test_python_qualification.py -q -p no:randomly
16 passed, 6 skipped in 3.26s
```

The 6 skips are the full-pipeline tests gated on `/opt/tester-venv` existing
(the tester-unified image's own ambient interpreter) plus a real installed
`assay` reachable on `PATH` — both real only inside the self-hosted lane's
own `env_passthrough = ["PATH"]` (which puts the just wheel-installed
`run-venv/bin` first). Neither exists in this cockpit devcontainer, so they
skip cleanly rather than approximate the tester-unified image with local
substitutes — the same split this project's own `test_distribution_gate.py`
already uses for `run_self_hosted_lane`'s real-pytest-in-child behavior.
Because `assay.toml`'s own `tester-unified` lane declares
`argv = ["python", "-m", "pytest", "tests", ...]`, this new file IS collected
by the real self-hosted lane inside the registered gate, so these six tests
run for real there — not merely in a cockpit approximation.

Ordinary suite (full `tests/`, excluding the self-hosting conformance module
per the project's own convention):

```
python -m pytest tests -q -p no:randomly --ignore=tests/test_self_hosting.py
2299 passed, 6 skipped in 142.90s
```

Directly reconfirmed the pre-implementation baseline via the same
`git stash push -u` / `stash pop` technique used for the controlled red,
using `--collect-only` (a full un-stashed run of this size exceeds a single
tool-call timeout): **2283 tests collected**, matching this package's own
addition exactly (2283 + 22 new in `tests/test_python_qualification.py` = 2305
collected = the 2299 passed + 6 skipped above). No regression. `shellcheck`
and `bash -n` both pass on the modified `tools/tester-unified-gate.sh`.

## Manual real-pipeline verification (not part of the committed suite)

Before trusting the six gated tests' own internal assertions, every
constituent function was exercised directly against the REAL pinned Topos
commit in this exact worktree, using a disposable local venv (never
committed, entirely outside the repo, deleted afterward) with
`qualify_topos._TESTER_VENV_PYTHON` monkeypatched at the Python object level
(never the tracked file) to a local interpreter carrying real
pytest/pytest-cov/coverage/pytest-xdist/zstandard:

- `materialize_scenario(..., spec=MISSING)` produced baseline OID
  `12ac3a4abba87522e95cae3233d06d10f39650c5` — BYTE-IDENTICAL to the OID the
  carver's own tracer recorded in `carve-assets/P25/probe-results.json`
  (`scenarios[0].base`), proving the construction is the exact same one, not
  merely a plausible one.
- `run_scenario` for `missing-line`: `FAIL/UNCOVERED_LINES`, coverage
  `covered=4/changed_executable=5/pct=80.0/missing_lines={.../7}`, Topos
  comparator `passed=false/uncovered={.../[7]}` — byte-for-byte identical to
  `qualification-manifest.json`'s `missing_probe` and the carver's own
  `probe-results.json` `missing` scenario.
- `run_scenario` for `excluded-forbidden`: `FAIL/EXCLUDED_LINES` while the
  Topos comparator gives `passed=true` — the documented capability asymmetry,
  reproduced for real.
- `run_scenario` for `comment-only`: `PASS`, `changed_executable=0`,
  `covered=0`, Topos comparator `passed=true` — matches
  `qualification-manifest.json`'s `comment_only_probe` exactly.
- `install_locked_release` + `run_scenario` for `release-targeted-pass`:
  installed version `1.2.5`, outcome `PASS`, Topos comparator `passed=true`.
- All seven integrity-matrix checks (`_check_missing_profile`,
  `_check_dirty_consumer`, `_check_base_is_head`, `_check_command_dirt`,
  `_check_command_head_move`, `_check_wrong_source_root`,
  `_check_universal_pass_mutation`) ran to completion without raising —
  i.e. each real terminal matched its frozen expectation exactly.
- `compare_complete_artifact` against the real `missing-line` result and the
  locked `missing-v4-template.json` differed in EXACTLY the two fields the
  local monkeypatch changes (`argv_declared`/`argv_effective`'s interpreter
  path) and nothing else — direct evidence that with the real, unmodified
  `_TESTER_VENV_PYTHON` value the artifact would match the locked template
  exactly.
- The real Topos/vbpub checkout's `git status --porcelain=v1` was captured
  before and after every one of the above and was unchanged throughout.

The full 2,923-test `current-full-pass` scenario was deliberately NOT run
locally — this cockpit lacks the tester-unified image's full `topos[dev]`
extras closure, and per the handoff/AUTHORING doctrine the implementer runs
only the quick locked suite and focused tests, never an approximation of the
registered gate. That full run is the controller-owned registered gate's own
job.

Discovered and fixed during this manual verification: the initial
`_check_base_is_head` asserted no coverage witness was ever produced (i.e.
"no command ran"), matching the handoff's terminal-table prose literally.
Reading `src/assay/runner.py` (`_run_higher_rigor_lane` →
`_execute_snapshot_unit` → `evaluate_r1`) shows the lane's own command
DOES run first inside the P22 snapshot for a higher-rigor lane; `BASE_IS_HEAD`
is only detected later, once R1 evaluation resolves the declared base — unlike
the PRE-run `DIRTY_TREE`/`HEAD_CHANGED` guards at the very top of
`_run_higher_rigor_lane`, which genuinely never let the command run. The
real-checkout run confirmed a genuine coverage witness IS produced for
`base-is-head` while the artifact still correctly renders
`NO_MEASUREMENT/BASE_IS_HEAD`. The check was corrected to assert the frozen
terminal and clean-consumer-repository invariant instead of command
suppression — the terminal is the O4 oracle; command suppression was this
implementer's own over-literal reading, not a frozen requirement.

## Scope

Touched only:
`gate/python/**`, `tools/tester-unified-gate.sh`, `nyxloom-trove/nyxloom.toml`,
`tests/test_python_qualification.py`, `docs/DESIGN-GUIDE.md`, and this LOG.
`assay/README.md` does not exist; nothing to touch there.

Confirmed untouched (byte-identical `git status --porcelain` before/after,
zero diff): `src/assay/**`, `pyproject.toml`,
`nyxloom-trove/carve-assets/P20/**` through `P25/**`, every real Topos file,
and every unrelated project/path. The 19-entry `fixture-manifest.json`
re-verification below is the mechanical proof for `carve-assets/P25/`
specifically.

### Post-implementation locked-asset re-check

```
19/19 file hashes match
fixture-manifest.json self-hash:
eedb73711d8ad56b03ea11230b2f0f3d9e929683e15195453e89a0035a9a6ffd
```

Identical to the pre-implementation value — no carve-asset was edited or
regenerated.

## Reachability sweep (SB-P23-03)

`gate/python/qualify_topos.py` was written fresh for this package — it has
no early-return dispatch layered onto pre-existing code, so the specific
SB-P23-03 shape (a call site left dead beneath an intercepting return, still
carrying a since-changed signature) cannot occur here by construction. As a
direct check anyway: every one of the file's 28 top-level functions (12
private helpers + 7 integrity-check helpers + the 6 owned public functions +
`main`) was confirmed to have at least one real call site by static
cross-reference, and the "Manual real-pipeline verification" section above
exercised every function except `qualify`/`main` themselves directly (both
are thin, linear compositions of the already-exercised pieces — `qualify`
calls `install_locked_release`, `run_scenario` five times, the two
`compare_complete_artifact` calls, and the seven `_check_*` functions in a
fixed, non-branching sequence; `main` parses argv and calls
`verify_pinned_inputs` then `qualify`). No dead code, no branch whose
signature could have silently drifted underneath it.

## Registered gate

**Not run by this implementer**, per the handoff's `## Environment setup`
and the controller's own instruction. The controller owns the exact
foreground `bash tools/tester-unified-gate.sh <worktree>` receipt: outer
exit zero, the validated background cgroup, `--network=none`, the raw log
plus its SHA-256, and all five markers in order
(`ASSAY_GATE_PHASE=wheel-installed`,
`ASSAY_GATE_PHASE=self-hosted-lane-passed`,
`ASSAY_GATE_PHASE=topos-qualified`,
`ASSAY_GATE_PHASE=independent-self-hosting-passed`,
`ASSAY_REGISTERED_GATE_COMPLETE=1`).

## BLOCKED

Not triggered. Every named contract was met from the locked packet as
frozen; no hash, topology, or scope contradiction was found.
