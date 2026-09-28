# B110-P11 — Isolation-unit execution model (decision-gated fallback design)

*Revised 2026-09-28 after round-1 and round-2 reviews (see REVIEW-2026-09-28-round1.md, REVIEW-2026-09-28-round2.md). Round-1: findings P11-1..P11-3. Round-2: findings P11R2-1..P11R2-4 and carver decision C26 (the size classes P8's `project()` uses).*

| Field | Value |
|---|---|
| Backlog | B121, plan package P11 |
| Branch | `assay-b110-p11-units`, cut from the integration line (`assay-b110-integration`, plan §11.1 / C18) |
| Dispatch condition | **Only** when the plan §7 pilot returns **NO-GO** on the claim-preserving path (plan §8, pilot GO) **and** the operator decides to pursue this model. Both conditions must hold. Until then, the brief is dormant. |
| Depends on | P0–P8 merged, including P8's exported pure `project(samples, rows, jobs, fixed)` (C14); the pilot report `reports/assay-B110-PILOT-REPORT.md` exists |
| Contract class | **2a**: design and probe. The implementation brief (P11b) is carved only after the operator decision. |
| Implementer | Opus |
| Decisions | A-467 (plan D3: the units model is a pilot-gated fallback needing its own decision); A-464 (time never classifies; shards and units are scheduling, not proof); A-462 (full-source claim) |
| Size | L (design and probe) |

## Why this package exists

The plan preserves today's claim: every candidate is judged by **one process running the declared suite in the declared order**. Kills stop at the first verified call failure (P3). Survivors run everything.

If the pilot shows that fixed per-candidate cost (snapshot, interpreter start, collection of 5,831 tests) plus the declared-order prefix cannot fit 6 h on the available CPU, the only structural lever left is to change **how the suite is executed per candidate**:
- run each test *file* as its own fresh-process **unit**;
- order the units per candidate so that the tests likely to kill it run first.

The analysis report ranked this row 8: per kill ≈ snapshot + unit start (~0.5–1.5 s, assumed) + K ≈ 1–6.5 s, which gives **0.35–2.3 h on 3 workers plus fixed overhead**. That figure is **a scenario, not a forecast**; this package measures it.

**This changes the declared execution semantics, in both directions.**
- A test whose result depends on what an earlier file did in the same process would behave differently.
- Some kills can disappear: a mutant is caught only because an earlier file left state that the killing test depends on.
- Kills can also be **added**: a mutant's effect that single-process order masks, for example a value cached by an earlier file before the mutated code path ran, can surface in a fresh unit.

That is why A-467 requires a separate operator decision, and why this package only designs and measures.

## Context to read first

Paths are relative to `assay/` at HEAD `db85f747`; the lines move with P0–P8.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §2, §3 (D3, D4 and D6), §5 (the manifest and hook-fingerprint definitions), §7 and §8 (GO/NO-GO).
2. `nyxloom-trove/reports/assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`: the cost model, the ranked strategies (row 8), the external comparison (PIT per-test ordering with bail; Stryker static mutants "run all tests … sorted last"; mutmut function→tests maps), and the coverage mechanics.
3. `reports/assay-B110-PILOT-REPORT.md`: measured snapshot, collection and prefix distributions, and the kill rate. These are the inputs to the projection.
4. `src/assay/mutation.py`:
   - `_execute_mutation_jobs` (at HEAD 2559-3035);
   - `_run_attempt` (2646-2855);
   - `_run_one` (2857-2888), the attempt chain where a unit attempt would sit;
   - `_classify_mutant_result` 1668-1700 (any non-zero exit means killed);
   - `_snapshot_left_dirt` 1843-1874 (A-195).
5. `src/assay/mutation_witness.py`: `make_attempt_plan` 195-204, the plugin source 324-493 (collection manifest, started prefix, `shouldfail`), and the P3b additions for manifests and hook fingerprints.
6. `src/assay/runner.py:3561-4760` `_run_prepared_lane`: baseline, liveness calibration and the R2 dispatch.
7. `tests/conftest.py`: session fixtures `schema` (:1356), `validator` (:1362) and `standalone` (:1431-1499, which builds a wheel and venv). Also `tests/test_distribution_gate.py:51` `gate_functions` and `:690` `lint_venv` (session), and `tests/test_distribution_build_release.py:362` `built` (module).
   - Under per-file units, every session fixture is rebuilt once per unit that uses it.
   - `standalone` users are: `test_analysis.py`, `test_dependency_purity.py`, `test_go_helper_is_packaged.py`, `test_verdict_schema_is_packaged.py` and `test_standalone.py` (P1 moves the last into `tests/zz_slow/`).
8. pytest 9.1.1 collection order: `_pytest/main.py:569` and `_pytest/pathlib.py:941-943` (directories and files interleave by name; this is why P1 uses `tests/zz_slow/`).
9. Coverage mechanics:
   - coverage 7.16.1 uses the `sysmon` core by default. Its docs say that core "does not yet support plugins, dynamic contexts", so a per-test map needs `COVERAGE_CORE=ctrace` ([config](https://coverage.readthedocs.io/en/latest/config.html)).
   - pytest-cov `--cov-context=test` records `setup`/`run`/`teardown` per test ([pytest-cov contexts](https://pytest-cov.readthedocs.io/en/latest/contexts.html)).
   - "Any code measured before a dynamic context is set will be recorded in this empty context" ([contexts](https://coverage.readthedocs.io/en/latest/contexts.html)).
   - No subprocess coverage is configured in this repository.
10. Test inventory: 243 top-level `tests/test_*.py` files and 247 in all (including `tests/qualification/`). After P1, add the `tests/zz_slow/` files. `tests/test_python_qualification.py` and `tests/test_self_hosting.py` are ignored by the B105 lanes (D4).
11. `tools/tester-unified-gate.sh:742-826`. It shows how a tester-unified container is launched from this devcontainer:
    - the host daemon is used, so the bind source is the **host** path, derived with `docker inspect "$HOSTNAME" --format '{{range .Mounts}}{{if eq .Destination "/workspaces/vbpub"}}{{println .Source}}{{end}}{{end}}'`;
    - `--cgroup-parent="$(tools/cgroup-parent.sh)"`;
    - `--init`;
    - `--network=none`;
    - image `tester-unified:local`;
    - an exact container name.

    The probe copies this pattern (see *Tracer-bullet probe*).
12. P8's exported `campaign.project(samples, rows, jobs, fixed)` (C14) is the projection helper. Its strata are `operator × size_class` (C26: small ≤ 10, medium 11–100, large > 100 candidates per file), falling back to operator, then all. Also plan §3 D7/P9's closed `judge_identity_inputs` key set: any new identity input, such as a coverage-map digest, is a coordinated change to P9's derivation.
13. `tools/self-qualification-gate.sh` ~49-154: the exact-OID private clone, `build-venv` and `run-venv` (`python3 -m venv`), the pinned build closure from `$distribution/build-wheelhouse`, `pip wheel` into `$scratch/dist`, and the run-venv install. The probe replicates these steps (P11R2-1).
14. `tools/cgroup-parent.sh`. It verifies the gates slice by starting its **own** transient, read-only `docker run --rm --cgroupns=host --network=none` probe container, and it exits non-zero, printing nothing on stdout, on any failure.
15. Root `.gitignore:343` ignores `.assay/`, so `assay/.assay/b121-probe/` is not tracked, and files there do not make the clone dirty (P11R2-2).

## Design packet (2a)

### The model, stated precisely (carver proposal)

- **Unit:** one test file under the lane's declared argv. It keeps the same `--ignore` and `--deselect` filters, the same `cwd` and the same R2 transform (P3), with the file path substituted for the lane's `tests` positional.
- **Unit-mode baseline:**
  - every unit passes alone;
  - the **concatenation** of the per-unit collection manifests, in declared file order, equals the declared single-process manifest **as an ordered sequence** (plan §5 canonical netstring digest);
  - the per-unit hook fingerprints are recorded.
- **Per candidate:**
  1. Use one fresh P22 snapshot (never one per unit).
  2. Run units sequentially in that snapshot, in the candidate's **unit order**, each in a fresh interpreter.
  3. Run the A-195 dirt check after **every** unit. A unit that leaves dirt, i.e. changes the snapshot's named Git state, gets **exactly today's A-195 treatment**: a `DIRTY_TREE`/`HEAD_CHANGED` fatal that stops later work. It is never a kill and never a reason to continue with the next unit. The only difference from today is that the check runs more often.
  4. Stop at the first unit whose cold receipt is a verified call-phase failure. That candidate is `killed`, and its evidence names the unit and the receipt.
- **Survivor:** every unit ran and passed, and each unit's manifest equals its baseline manifest. Then the ordered union equals the declared manifest by construction.
- **Uncertainty** (a unit collection error, an untrusted hook, a manifest mismatch, or a failure during setup or teardown): fall back to **one declared-command full attempt**, exactly the P3/D6 A2 fallback. That attempt is authoritative.
- **Unit order for a candidate:**
  1. Units containing ≥ 1 test whose coverage context covers the mutated line, sorted by baseline unit duration ascending.
  2. Then all other units, sorted by duration ascending.

  Candidates whose line is only in the **empty context** (import-time code, including all 190 import-time candidates and the 148 dataclass flags) use pure duration order. This is Stryker's "static mutants run all tests" rule, applied as ordering only.
- **Nothing is ever skipped.** The coverage map changes **order only**, so a wrong or incomplete map costs time, never correctness. Coverage-derived *selection* stays rejected (A-464, Sol review).
- **R0 and R1 are unchanged.** They stay single-process. R1's 100% floor is still measured by the declared command.

### Open choices

| # | Choice | Admissible options | Recommended | Invariant | Deciding evidence |
|---|---|---|---|---|---|
| OC1 | Unit granularity | per file; per directory; per P1 tier; per test | **per file** | Every declared test is in exactly one unit | T1/T2: unit start cost × file count against the gain |
| OC2 | Unit order | coverage-covering first then duration; duration only; previous killer first (PIT rule 4) as an extra key for B106 reruns | **covering-first, duration ascending**, with the prior killer's unit first when `--reuse-from` applies | Order never changes the inventory | T5 offline replay on the pilot kills |
| OC3 | Coverage map | one ctrace plus `--cov-context=test` run per campaign, bound by digest into the judge identity; per-candidate recomputation; none | **once per campaign**, digest in the identity | A map is an ordering hint and never outcome evidence | T4 overhead and the empty-context fraction. **Note (P11R2-4):** a new identity input is a coordinated change in **three** places:<br>• P3b's `judge_sha256` signature gains a keyword, and the identity label is bumped from `assay-judge-identity/3` to `/4`, so every older record is rejected rather than mis-resumed;<br>• A-470's canonical identity list gains the input (a decision-row amendment);<br>• P9's closed `judge_identity_inputs` key set, since `judge_sha256_from_inputs` refuses unknown keys, together with its O1 vectors.<br>It is never a local addition. |
| OC4 | Snapshot scope | per candidate; per unit | **per candidate** | A-120/A-184: fresh per mutant; A-195: dirt checked per unit | T2 plus P5's measured snapshot cost |
| OC5 | Kill evidence | per-unit cold receipt plus the unit manifest equal to its baseline; any non-zero unit exit | **cold receipt plus manifest** (same strength as P3) | A collection error is not a cold kill | P3 contract |
| OC6 | Survivor proof | all units plus per-unit manifest equality; also rerun the declared command | **all units plus manifest equality** | Complete declared inventory | T3 concatenation equality |
| OC7 | R1 under units | unchanged single-process; combined per-unit coverage | **unchanged** | The R1 floor stays on the declared command | No need to change it |
| OC8 | Session-fixture rebuilds | accept; move heavy session-fixture users into late units; refactor fixtures to file-local caches | **measure first** (T1). Prefer ordering, because heavy units come last and rarely run for kills. | Fixtures stay per-unit fresh, so there is no cross-unit sharing | T1 per-file wall compared with the call-time sum |
| OC9 | Contract surface | CLI opt-in `--unit-execution` (requires `--cold-witness`); a lane key | **CLI opt-in**, following the B110 pattern. The B105 gate script passes it only after the operator decision. | Default behavior unchanged | Luna's precedent for CLI-only transforms |
| OC10 | Verdict wire | v15 `judgment.r2.unit_execution = {model: "pytest-file-units/1", order_policy: "coverage-first/1", coverage_map_sha256, units: [{path, collection_count, collection_sha256, hook_fingerprint_sha256}]}` plus per-outcome `evidence.unit_index`/`units_run` | as proposed | One active schema (A-170) | Review |

### Invariants

1. The **complete declared test inventory** is judged for every survivor; the unit manifests concatenate to the declared manifest.
2. Every candidate in the plan is judged. Units change execution, never the candidate set.
3. **The coverage map is ordering only.** It never skips, and never contributes positive evidence.
4. Time, host load and ETA never decide an outcome (A-464). A unit that is still running at a deadline leaves the candidate incomplete.
5. Any uncertainty takes the authoritative declared-command attempt.
6. **Semantic change to disclose:** cross-file order dependence (a test's result depending on earlier files in the same process) no longer counts.
   - The unit-mode baseline must pass, so a dependence that makes a test *pass only after* earlier files is caught at baseline.
   - A dependence that makes a test *fail only after* earlier files becomes invisible under units.
   - **Per candidate, units can both remove and add kills** relative to the single-process claim:
     - **removed:** a kill that needed earlier-file state (fixture H1);
     - **added:** a mutant effect that single-process order masked, now exposed in a fresh unit.

   The decision row must disclose both directions.

### Required operator decision (after the probe)

A new `decisions.md` row, the next free A-number at that time. It records that the B105 claim becomes: "every candidate is judged by the declared test inventory executed as per-file fresh-process units in coverage-guided order; survivors run every unit; any uncertainty runs the declared single-process command". It also records invariant 6's disclosure.

**Without this row, no P11b implementation brief is dispatched.**

### Tracer-bullet probe

Run it once. It needs **controller approval**, because it runs the suite about three times.

**P11 is not a run-gate lane (P11R2-4).** The probe uses only the exact `docker run` line below. No `b121-probe` lane is added to `run-gate.toml`, which P11 forbids editing.

**Environment** (exact; P11-1, P11R2-1..3). All steps run in one `bash` script, `<clone>/assay/.assay/b121-probe/run-probe.sh`, with `set -euo pipefail` at the top.
1. **Scratch clone.**
   - Make a git worktree of the integration-line tip at `/workspaces/vbpub/.worktrees/b121-probe-<yyyymmdd>`. It must be under the bind root, so the container sees it, and it is never a gate worktree.
   - **Probe files live only under the gitignored `<clone>/assay/.assay/b121-probe/`** (P11R2-2): the probe script `b121_units_probe.py`, `run-probe.sh`, and all outputs. Root `.gitignore:343` ignores `.assay/`, so the clone stays clean and `assay plan`/`cli.plan_jobs()` do not refuse with `DIRTY_TREE` (`runner.py:2571-2580`).
   - Never write the script to `tools/`.
   - Check that `git -C <clone> status --porcelain` prints nothing before launching.
2. **Preconditions** (concrete and read-only; P11R2-3).
   - Run `docker ps --format '{{.Names}}' | grep -E '^(run-gate-|b121-probe-)'`. It must print nothing; otherwise stop (BLOCKED trigger).
   - `run-gate-` covers the registered gate containers, e.g. `run-gate-assay-selfhosted-*` (`tools/tester-unified-gate.sh:745`), and run-gate's own lane containers. `b121-probe-` covers an earlier probe.
   - Record that (empty) output and the host load (`cat /proc/loadavg`) in the probe JSON. The load is diagnostic only.
3. **Host path.**

   ```bash
   host_repo_root="$(docker inspect "$HOSTNAME" --format '{{range .Mounts}}{{if eq .Destination "/workspaces/vbpub"}}{{println .Source}}{{end}}{{end}}')"
   [ "$(printf '%s\n' "$host_repo_root" | grep -c .)" -eq 1 ] || { echo "host path not exactly one line" >&2; exit 2; }
   ```

   This is the same derivation as `tester-unified-gate.sh:808-816`.
4. **Cgroup parent** (P11R2-3). Resolve it into a variable **before** `docker run`, never inline:

   ```bash
   cgroup_parent="$(<clone>/assay/tools/cgroup-parent.sh)"   # set -e aborts the script if it fails
   [ -n "$cgroup_parent" ] || { echo "empty cgroup parent; refusing a fail-open launch" >&2; exit 2; }
   ```

   `cgroup-parent.sh` itself starts one short-lived, read-only `docker run --rm --cgroupns=host --network=none` verification container, then exits. That container is **not** the probe container, and "exactly one probe container" refers to the probe container below. The verification container is the existing gate tool's own behavior and runs before the probe starts, never at the same time. Never pass `--cgroupns=host` to the probe container itself.
5. **Launch.** Run exactly one probe container:

   ```bash
   name="b121-probe-$(date -u +%Y%m%dT%H%M%SZ)"
   docker run --rm --name "$name" --init --network=none \
     --cgroup-parent="$cgroup_parent" \
     --cpus=1 --memory=2g --memory-swap=8g \
     --mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub" \
     -w "<clone>/assay" tester-unified:local \
     timeout --verbose --signal=TERM --kill-after=60s 3h \
     bash .assay/b121-probe/probe-in-container.sh
   ```

   - The image's default user is the same identity the registered gate uses; do not pass `--user`.
   - **`nice`/`ionice` on `docker run` affect only the client process, not the container** (P11R2-3). They are omitted here. The container is bounded by `--cpus=1`, `--memory=2g`, the verified gates cgroup parent and the 3 h cap. Inside the container, `probe-in-container.sh` runs every step under `nice -n 19`.
   - The session cap is **3 h**, enforced by `timeout` inside the container. A timed-out session gives partial, labelled output. Never extend it.
   - After exit, run `docker ps -a --filter name=^/$name$`, which must be empty (`--rm`). If it is not, remove the container by **exact name**.
6. **The interpreter and venv inside the container (P11R2-1).** `probe-in-container.sh` replicates the gate's steps from `tools/self-qualification-gate.sh` ~49-154 exactly, into a scratch directory `$scratch` under `.assay/b121-probe/`:
   1. make an exact-OID private clone of the clone's HEAD, and verify its HEAD and tree;
   2. `python3 -m venv $scratch/build-venv` and `$scratch/run-venv`;
   3. install the pinned build closure from `$distribution/build-wheelhouse` into build-venv;
   4. `pip wheel` the exact-OID source into `$scratch/dist` and check there is exactly one `assay-*.whl`;
   5. install that wheel into run-venv.

   **Every unit command, every `--collect-only`, and T4 use `$scratch/run-venv/bin/python`.** The plugin, hook fingerprints and runtime fingerprint then match the gate environment. The probe JSON records `sys.version`, the wheel filename and its sha256, and the run-venv path.
7. **Per-unit failsafe.** The probe script runs each unit command under `subprocess.run(..., timeout=1800)`.
   - A unit that exceeds this is recorded as `probe_timeout: true` with its partial output. It is **never** classified as pass or fail, and it is excluded from T5 with a count.
   - This is a failsafe against a hang, not a measurement bound (§3b.A).
8. **Inside the container**, all runs are serial. `-p no:cacheprovider` applies everywhere.

**Tests:**
- **T0, the reference full run (P11R2-4).** Before T1, run the lane's declared argv **once as a single process** with the R2 no-cov transform: `$scratch/run-venv/bin/python -m pytest <lane argv, transformed> -p no:cacheprovider`, with P3b's plugin in manifest-only mode and `--junitxml=.assay/b121-probe/reference.xml`. This is **the** "full run" that T1 and T3 compare against. Record its pass/fail per node ID, its collection manifest and its wall time. If T0 itself fails, stop: `BLOCKED: reference full run fails`.
- **T1, per-file isolation baseline.**
  - For each unit, run `$scratch/run-venv/bin/python -m pytest <file> -q -p no:cacheprovider` with the lane's `--ignore`/`--deselect` filters and the R2 no-cov transform.
  - Record pass/fail, wall time, and the per-unit hook fingerprint (P3b's plugin, manifest-only) for each file.
  - **Every file that fails alone but passes in T0 is a cross-file order dependence.** List each one with its failing node IDs.
  - **A unit whose hook fingerprint differs from the others' common set** means every candidate would fall back to the declared command in that unit (fixture H3). Examples: a conftest-level `pytest_collection_modifyitems` in a subdirectory, or a file-local plugin. List each such unit.
  - Expected cost: about one suite run plus per-file overhead.
- **T2, unit start cost.**
  - **The file choice is fixed by fixture *parameter* names, not substrings (P11R2-4).** For each top-level `tests/test_*.py` file, parse it with `ast` and collect the parameter names of every function whose name starts with `test` (including methods of `Test*` classes).
    - Exclude a file if any such parameter name is in `{standalone, schema, validator, built, gate_functions, lint_venv, installed_assay}`, or if it contains a `pytest.mark.usefixtures(...)` naming one of them.
    - From the remaining files, take the **10 with the most collected tests in T0**, breaking ties by sorted name. This avoids the old substring filter's bias toward tiny files.
    - The probe script computes and records this list and each file's T0 test count.
  - Run `$scratch/run-venv/bin/python -m pytest <file> --collect-only -q` five times each and report the median.
  - Also report the median T1 wall minus call time, per file.
- **T3, manifest concatenation.** Compare, as a sequence, the concatenated per-file collection node IDs (declared file order) with **T0's** collection manifest. Any difference is listed. Compare sequences, not sets: a reordering is a difference.
- **T4, coverage context map.** Run one full-suite pass with `$scratch/run-venv/bin/python`, `COVERAGE_CORE=ctrace` and `--cov=src/assay --cov-context=test`. Report:
  - its overhead against the no-cov baseline;
  - the `.coverage` size;
  - the fraction of the current plan's candidate lines with ≥ 1 covering test context. Take the candidate count from `assay plan` at the probe commit; do not use a hard-coded "3,760";
  - the fraction recorded only in the empty context.

  There is no child-process estimate. R1 already measures 100% of `src/assay` in-process, so no candidate line is reachable only through a child process. Any such claim would be reasoning about a report (§3b.F).
- **T5, offline replay.**
  - Use the pilot's killed candidates, whose witness node IDs are in the pilot evidence. For each, compute:
    - the projected time-to-kill under OC2 unit order: the sum of the preceding units' T1 walls, plus the killing unit's start and prefix;
    - the declared-order cold prefix actually measured.
  - Then project the full inventory with **P8's exported `campaign.project(samples, rows, jobs, fixed)`** (C14). Pass unit-mode per-candidate costs as `samples`, the current `cli.plan_jobs()` rows as `rows`, `jobs` equal to the lane's measured worker count, and the measured fixed components.
  - The projection reports **nearest-rank p50/p90** per stratum, exactly as `project()` computes it, with its C26 strata (`operator × size_class`, then operator, then all) and fallback counts. Never report sample means, and never re-stratify by path.

**Outputs:**
- `nyxloom-trove/reports/assay-B121-P11-PROBE.json`;
- the design doc `nyxloom-trove/reports/assay-B121-P11-DESIGN.md`, containing:
  - the resolved open choices;
  - the probe tables;
  - the projection, labelled a scenario with its sample limits;
  - the list of cross-file order dependences, each marked to fix before adoption or accepted;
  - the proposed decision row for the operator.

## Work

1. Confirm the dispatch condition: the pilot report says NO-GO **and** the operator has asked for this path. If not, BLOCKED.
2. Write `run-probe.sh`, `probe-in-container.sh` and `b121_units_probe.py` under `<clone>/assay/.assay/b121-probe/`, and confirm the clone is clean. Then run the environment steps and T0–T5 in the approved container (one session).
3. Write the design doc and the probe JSON. Resolve OC1–OC10 with evidence.
4. Obtain a fresh-session design review verdict of READY, following the plan §10 routing.
5. Hand the controller the proposed decision row and a P11b carve outline (interfaces, v15 wire, oracles). **Do not implement.**

## Oracles (design package)

| # | Observable | Negative |
|---|---|---|
| O1 | T3 **sequence** equality against T0's manifest holds, or every difference (including reorderings) is listed with its cause. The probe JSON records the run-venv interpreter, wheel sha256 and a clean-clone check. | Units silently drop, duplicate or reorder tests; comparing sets instead of sequences; running units under the devcontainer interpreter |
| O2 | T1 lists every alone-fail/full-pass file and every hook-fingerprint outlier unit. The design states for each whether it is fixed before adoption. | Adopting units over an unknown order dependence or a unit that always falls back |
| O3 | The projection comes from P8's `project()`, states its sample size, strata, fallback counts and nearest-rank p50/p90, and is labelled a scenario | Presenting a forecast; using sample means under a "scenario" label |
| O4 | The design keeps invariants 1–6 (including both kill-direction disclosures and the dirt-in-unit rule) and shows where each is enforced in the P11b outline | A design that uses the coverage map for selection |
| O5 | The review verdict and the operator's ruling (an accepted decision row, not "proposed") are both present before any P11b brief exists | Implementation without the decision; a "proposed" row treated as the ruling |

**Design fixtures the P11b outline must carry forward:**
- **H1:** a kill that depends on a mutant's module global being set by an earlier file.
- **H2:** the `standalone` session fixture rebuilt per unit, combined with the kill-ordering choice.
- **H3:** a subdirectory conftest `pytest_collection_modifyitems` hook, which gives a per-unit hook fingerprint mismatch, so the declared-command fallback applies for every candidate.

Any tests later specified for P11b must obey this list:

### Oracle anti-patterns (AUTHORING.md §3b, pasted verbatim)

### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger — the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- ✗ A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- ✗ Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list — it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- ✓ Assert the POLICY requirement instead — the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision — as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- ✓ If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code — never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Docs sync

P11 is design only, so there are no user-facing docs.
- The design doc must include the README, DESIGN-GUIDE and CONSUMERS text that P11b would ship. At minimum, that text must state the semantic change (invariant 6) plainly and give a pasteable opt-in.
- Update the B121 backlog status.

## Scope / forbid

**Touch only:**
- `nyxloom-trove/reports/assay-B121-P11-*.{md,json}`;
- the probe files under the gitignored `<clone>/assay/.assay/b121-probe/` (`run-probe.sh`, `probe-in-container.sh`, `b121_units_probe.py`). They are never under `tools/` (P11R2-2). If the design review asks for the script to be kept, copy it into `nyxloom-trove/reports/assay-B121-P11-probe.py.txt` as a document;
- a proposed decision row, marked "proposed, pending operator".

**Forbid:**
- any change under `src/` or `tests/`;
- changes to the gate script, `assay.toml` or `run-gate.toml`. There is no `b121-probe` run-gate lane;
- any file under `tools/` in the probe clone;
- `--cgroupns=host`, `--pid=host` or `--net=host` on the probe container;
- running the `self-qualification` lane;
- more than one probe container session;
- implementing P11b.

## Gate

There is no registered gate for a design package. The probe is the evidence, and it runs only as the approved single-container session above, with the exact `docker run` line, the 3 h session cap and the per-unit failsafe. If a later P11b exists, it follows plan §10:
1. `cd <worktree>/assay && python ./run-gate.py tester-unified`, then read `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE` in a separate step (L4).
2. `self-qualification-preflight`.

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file: STOP. Write `BLOCKED: <reason>` to the LOG (`nyxloom-trove/reports/assay-B110-P11-REPORT.md`), commit, and exit. Do NOT improvise a workaround.

**Specific triggers:**
- The dispatch condition is not met.
- The probe container cannot be granted or would overlap another gate (step 2 of the environment).
- The host bind source cannot be derived as exactly one line.
- `tools/cgroup-parent.sh` fails or prints an empty value.
- The probe clone is not clean before launch.
- The gate's clone → build-venv → wheel → run-venv steps cannot be replicated inside the container (for example, the build wheelhouse is missing).
- The T0 reference full run fails.
- T3 shows the unit manifests cannot reproduce the declared manifest for a structural reason, such as cross-file parametrization.
- P8's `project()` is not exported with the stated signature.

## Report

Write `nyxloom-trove/reports/assay-B110-P11-REPORT.md` with:
- the dispatch condition evidence;
- the probe summary;
- the review verdict;
- the proposed decision row;
- the P11b carve outline;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`
