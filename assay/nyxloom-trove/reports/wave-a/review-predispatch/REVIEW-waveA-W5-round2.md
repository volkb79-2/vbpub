# Round-2 adversarial review: W5 (self-contained SQL qualification)

**Reviewer:** fresh session, read-only on the repo. **Worktree:** `.worktrees/assay-b110-landing`, HEAD `ec427945`. Paths are relative to `assay/` unless absolute.
**Under review:** `nyxloom-trove/reports/wave-a/W5-sql-self-contained.md` (26 514 bytes), read against `CARVER-DECISIONS.md` (CD8, CD21 amended, CD22, CD28, CD29, CD31, CD32, CD33), `W4-test-split.md` and round-1 `review-predispatch/REVIEW-waveA-sql-plan.md`.
**Scratch evidence:** `scratchpad/w5r2/`: `01-schema.sql`, `matrix.json`, `tests/K*.sql`, `sites.py`/`sites.out`, `classes.py`/`classes.out`, `lexchk.py`, `probes.py`/`probes.out`. I started no container, gate or PostgreSQL. The only Docker call was a read-only `docker image inspect` of the pinned digest.

## 0. What I recomputed

| Claim | Result |
|---|---|
| Schema: 76 lines, sha256 `b7b07e97…3af1` | **Holds.** Lines 22–97 of the brief, every line LF-terminated, give exactly `b7b07e973e988202dbc34cb3ff69415d6b79adc801c3b3566595cb3a63703af1`. |
| 24 sites, one per tag, none on trap lines {2,3,15,29,32,50,52,74,75,76} | **Holds.** The shipped `SqlAdapter` (all lines, 7 operators, limit 500) gives 24 sites; `collect_mutation_sites` accepts 24 jobs. |
| Tag rule (ascending `start_byte`; lines 11, 13, 19, 38 carry two) | **Holds.** Line 11: K06 @432, K25 @463. Line 13: K05 @549, K24 @576. Line 19: K15 @740, K19 @773. Line 38: K17 @1658, K20 @1713. K16's span runs from line 24 into line 25, which carries K18. |
| Matrix `(line, operator)` for all 24 rows | **Agrees** with the tag map. The pairs are unique. K12 is deliberately absent (K01–K25 minus K12 = 24). `-` rows are exactly K02, K09 and K25. |
| 21 probes, 4673 bytes, sha256 `12f564d9…0ce0` | **Holds on the first attempt.** I built the files from the brief's own text: the K11 Form A example, the Form C line list, the macros, a split at `; ` with the last statement as the probe, and LF endings. |
| Class map: 13 classes, 171 sites, the brief's exact key rule | **Holds.** Over the three files in `gate/python/fixtures/dstdns-sql/corpus/` (111 + 8 + 52), every count matches: nn 38, nn-default 36, ck 28, ck-named 11, wi-string 20, wi-int 2, uq-UNIQUE 12, uq-CREATE UNIQUE INDEX 1 (03c:372, a partial index), fk-REFERENCES 11, fk-FOREIGN KEY top 1 / body 3, wd body 2, tg 6. |
| Work 3 lexer claims | **Hold.** Line 2's `ON DELETE RESTRICT` is masked. Line 38's is kept. Of the 5 `dollar_bodies`, exactly one (1500–1752) contains `FOREIGN KEY`. The weaken sites sit on lines [19, 25, 38]. |
| Pinned image | Still present: Id `b07129cc…`, `PG_VERSION=18.6`, created 2026-08-13, `VOLUME /var/lib/postgresql`, `PGDATA=/var/lib/postgresql/18/docker`. The tmpfs covers the volume. |
| Buckets (desk check against PG 18 semantics) | I agree with all 24 rows, and with O4-residue (line 4, `CREATE DOMAIN posint`, fails on re-apply), M11 (`'__assay_widened__'` in an integer IN-list fails at CREATE TABLE) and O5. Every killed mutant changes the dump, so derivation rule (2) never misfires. The K09 dump is byte-equal, because `DO` bodies are not dumped. |

## 1. Round-1 closure

| R1 id | Status | Reason |
|---|---|---|
| W5-1 O4 cannot pass | **CLOSED** | Replaced by O4-residue: unmutated apply, then the K01 mutant on the same `qual`, which must exit ≠ 0 with no dump, giving `crashed`. The premise is documented. |
| W5-2 probes not verbatim / unparseable | **CLOSED** | The K11 text, the Form C line list, the macros and the split rule determine all 21 files, and the hash pin reproduces (§0). |
| W5-3 dependency contradiction | **CLOSED** | The header, "Builds on" and BLOCKED all say after W3/W1/W2/W6/W4. `run_bash` comes from `gate.tests.test_distribution_gate`. |
| W5-4 serialized forms | **CLOSED** | `run-assertions.sh` and schema-gate step 3 are exact. The signal grammar, `matrix.json` form, `derive_bucket` signature and precedence, and the `(lineno, operator)` key are all fixed. |
| W5-5 storage lifecycle | **CLOSED** | One `docker run`, WAL args, `qual`/`witness` created and dropped, `df` logged. The measurement point is still weak (W5R2-8). |
| W5-6 host-load mechanism | **CLOSED** | `run-gate-assay-sql-<pid>-<epoch>`, the slot, `--cpus 1 --memory 512m`, the one-shot check exits 3, no PostgreSQL after a red tester. The exit-3 value is lost at the gate (W5R2-2). |
| W5-7 known-red | **CLOSED** | "Every failure is real (CD29)". |
| W5-8 backlog ids | **CLOSED** | B132 gets the append, B133 the mislabels, and new ids start at B134 (CD28). |
| W5-9 scratch leak / commit pin | **CLOSED** | The global, the trap `rm -rf` and the HEAD = `$commit` die are all present. The trap wording has a `$?` hazard (W5R2-4). |
| W5-10 class map unrecorded | **CLOSED** | The exact rule and counts are in the brief, recorded by Work 0b before `git rm`. They reproduce, with one corpus-scope ambiguity (W5R2-9). |
| W5-11 unproved PostgreSQL seam | **CLOSED** | Resolved by CD28's alternative: Work 0 measures, and BLOCKED applies on contradiction. |
| W5-12 `--cgroup-parent` optional | **CLOSED** | Required and non-empty, else `parser.error`. Manual runs pass `$CGROUP_PARENT_DEV_GATES`. |
| W5-13 T6 scope | **CLOSED** | `git ls-files`, a `.sh` negative, the verdicts exclusion and the bare-`dstdns` rationale are all present. |
| W5-14 CD8 / MANIFESTs | **CLOSED** | The file is kept. One line each in W3 and W5–W8 (CD33). These are exactly the five MANIFESTs that mention it (verified). |
| W5-15 decision notes | **CLOSED** | A-280, A-286 and A-289 are covered. |
| W5-16 port list | **PARTIAL** | Port/Drop covers about half of the old file. 30 tests are in neither list (W5R2-7). |
| W5-17 signal/clock seams | **CLOSED** | The SIGTERM handler and the `_sleep` seam are specified. `finally` ordering has gaps (W5R2-3). |
| W5-18 exact gate acceptance | **CLOSED** | Exit 0 and exact stdout are both required. T7 has the three negatives. |
| W5-19 smaller gaps | **CLOSED** | Every bullet is folded in: T1 via `check_sites`, the two T1 negatives, T0, the schema-gate rewrite, the `LANE_SCHEMA_VERSION` import, `jobs = 1`, the `test_path.py:29` name, the DOCKER_HOST docstring, the dedupe "bucket" axis. |
| Per-oracle table + traceability | **CLOSED** | Every added negative is present, and the traceability row exists. |

**Tally: 18 CLOSED, 1 PARTIAL, 0 OPEN.**

## 2. Internal consistency

The schema text, the tag rule, `matrix.json`, the 21 probe files, the expected buckets and the class map are mutually consistent and reproduce byte-for-byte (§0). The inconsistencies I found are in the **harness contract**, not the fixtures:
- The CLI's exit-3 list and the Witness bullet's requirement order contradict each other (W5R2-1).
- The harness exit 3 and the gate's `die` disagree with CD32's exit 3 (W5R2-2).
- "Host load (Decided CD28)" cites the wrong decision: the one-shot check is **CD21 (amended)**. CD28 says nothing about it (W5R2-10).

## 3. Harness contract vs CD21 / CD28 / CD32

| Requirement | Brief | Verdict |
|---|---|---|
| Name `run-gate-assay-sql-<pid>-<epoch>` | CLI fullmatch `run-gate-assay-sql-[0-9]+-[0-9]+`; gate `${BASHPID}-$(date +%s)` | OK |
| `--cpus 1 --memory 512m` in `$cgroup_parent` | exact argv, plus `--memory-swap 512m --pids-limit 256` | OK |
| One-shot host check, exit 3, no polling | `docker ps --no-trunc`, right before `docker run` | OK in the harness. **The gate turns it into exit 1** (W5R2-2) |
| No PostgreSQL after a red tester | relies on `set -e` in W4's `run_registered_gate` | OK (W4 O7 guards it) |
| Removal by exact name on every exit path incl. SIGTERM | harness `finally` plus gate EXIT trap | **Gaps** (W5R2-3, W5R2-4) |
| tmpfs cap | 256 MiB, `max_wal_size=64MB` | OK. Peak not measured (W5R2-8) |
| Database residue | step (7) `postgres,template0,template1` (`name` sorts with C collation, so the order is stable) | OK |

**Leak paths found:**
1. A `TimeoutExpired`/`OSError` from the `df` call in `finally` skips the `docker rm`. `check=False` does not suppress a timeout. The container leaks on manual runs, since the gate trap only backstops gate runs.
2. A second SIGTERM during `finally` raises `SystemExit` in the middle of `docker rm`.
3. SIGKILL of the harness gives no self-removal. The gate trap covers it only if bash survives.
4. `docker run` refused with a name `Conflict` still reaches the `finally` removal, which deletes **another process's** container of that name. CD28 dropped `${RANDOM}` from the name, so the only defence is the name being unique.

A leaked `run-gate-assay-sql-*` then makes every gate exit 3 through CD32. The brief's Gate section says "wait until it ends, recheck", which is a wait that never ends (W5R2-3).

**Database leaks:** none that outlive the container. Every early exit removes the container, and the tmpfs goes with it.

## 4. Oracles T0–T7, R1, R2

Each has a concrete break. Hazards:
- **T0:** the negative must run the same hash check over a tmp fixture root and see it raise. "`sha256(modified) != pin`" on its own is hollow (W5R2-5).
- **T4** tests `cross_check` alone, so it cannot catch the ordering defect in W5R2-1.
- **T5 SIGTERM:** if the harness installs no handler (the natural negative), `signal.raise_signal(SIGTERM)` takes the default action and **kills the pytest process**, which is the registered gate, instead of showing red. The test needs a sentinel handler (W5R2-5).
- **T7's exit-3 case** says "red" but does not pin the gate's exit code (W5R2-2).
- **Clock:** no assert depends on time. T7 must read the container name from the docker stub's log, never recompute `date +%s`.
- **Globals:** the SIGTERM handler is covered by T5's restore assert (with the sentinel). The harness's module-level `sys.path.insert` duplicates `src`, which is harmless.
- **R1** (K05 = `SELECT 1;`): under K05's mutant only K24 fails, so rule 5 raises "K05: test failed without naming K05", giving exit 1 naming K05. Correct.
- **R2** has no break of its own, as stated.

## 5. Anchors (all at HEAD `ec427945`)

| Anchor | Result |
|---|---|
| old harness `_run` 176-201 | ✓ |
| old harness `ThrowawayPostgres` 501-627 | ✓ |
| old harness O3/O4 705-858 | ✓ |
| old harness witness 880-1133 | ✓ |
| `mutation.py` `_classify_mutant_result_with_equivalence` | ✓ :1951 |
| `tools/tester-unified-gate.sh:756-765` (`docker run`, `--network=none`, repo bind only) | ✓ |
| `decisions.md:632-642` = A-279…A-289 | ✓ |
| README `:632` §SQL | ✓ |
| `docs/DESIGN-GUIDE.md:2809` | ✓ |
| `docs/CONSUMERS.md:1057` | ✓ |
| `src/assay/provenance.py:245` | ✓ |
| `test_cli_provenance_and_request_base.py:357` | ✓ |
| `test_runner_result_report.py:573` | ✓ |
| `test_adapters_sql_test_path.py:29` | ✓ |
| B126/B132/B133 exist; B134 free | ✓ |
| The five MANIFESTs naming the witness (W3, W5, W6, W7, W8) | ✓ |

The `tests/` line numbers will shift after W3/W4 moves. The header's "re-resolve by file name" covers that. The T6 token sweep at HEAD finds hits only in the files W5 edits or removes, plus the excluded `tests/fixtures/verdicts/`.

## 6. Combined-axis attack: contention during the witness gives a FAIL where the brief promises INCONCLUSIVE

**The features that combine:**
- (a) the CLI contract: "`hung`/`budget_exceeded` entries or `LANE_TIMEOUT` in the verdict → exit 3";
- (b) the Witness bullet: "Require R0 PASS, outcome `FAIL`, reason `MUTANTS_SURVIVED`, **then** `cross_check`";
- (c) the gate's rc mapping.

**How it fails:**
1. Under host load (the memory records a 1-min load of 85), one witness mutant's run exceeds its derived per-candidate budget, `max(3×baseline, baseline+60 s)` (`mutation.py:2141-2158`). This is schema-gate plus 21 psql probes on a 1-CPU container.
2. `judge_mutation` returns `BUDGET_EXCEEDED`/`LANE_TIMEOUT` because `budget_exceeded` outranks `survived` (`mutation.py:3985-4002`). The top-level outcome is `BUDGET_EXCEEDED`.
3. The harness follows (b) literally. "Require outcome FAIL" raises `QualificationError`, so the process exits 1 before `cross_check` ever sees the `budget_exceeded` entry.
4. The gate prints `SQL qualification failed (exit 1)` with **no** `ASSAY_GATE_DIAGNOSTIC`. The implementer, bound by CD29 ("every failure is real"), records BLOCKED as a product failure.

The correct verdict is inconclusive/rerun, under the operator's contention-agnostic rule. T4 cannot see this, because it tests `cross_check` in isolation.

Even with (b) fixed, (c) still reports the SQL-phase inconclusive as exit 1 under a different marker name. CD32 reports the identical "host busy" condition as exit 3 `ASSAY_GATE_INCONCLUSIVE=`. Fixes: W5R2-1 and W5R2-2.

---

## 7. Findings

### W5R2-1. MAJOR: the witness requirement order turns every contention-inconclusive verdict into a qualification failure
- **Where:** Harness, "Witness (6)": "Require R0 PASS, outcome `FAIL`, reason `MUTANTS_SURVIVED`, then `cross_check`…". This contradicts the CLI bullet's exit-3 list. T4.
- **Evidence:** §6. `mutation.py:3985-4002`: any `budget_exceeded` gives `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, and any `hung` gives `BUDGET_EXCEEDED`/`CANDIDATE_HUNG`. In both cases the outcome is never `FAIL`/`MUTANTS_SURVIVED`, so the first requirement always raises exit 1 first. The same applies when R0 exceeds the 60 m lane budget.
- **Fix text** (replace the sentence in Witness (6)): "`check_witness_verdict(verdict, matrix)` does, in this order:
  1. **Completeness:** raise `InconclusiveError('witness incomplete: <outcome>/<reason_code>')` (exit 3) if the top-level `outcome` is `BUDGET_EXCEEDED`, or any claim's `reason_code` is `LANE_TIMEOUT` or `CANDIDATE_HUNG`, or the R2 claim's `mutation.hung` or `mutation.budget_exceeded` is non-empty.
  2. Only then require R0 PASS, outcome `FAIL`, reason `MUTANTS_SURVIVED` (else `QualificationError`).
  3. Then `cross_check`.

  T4 adds, through `check_witness_verdict` (not `cross_check` alone): a built verdict with K02's entry moved to `budget_exceeded` and the R2 claim and top level set to `BUDGET_EXCEEDED`/`LANE_TIMEOUT` exits 3. Break: swap steps 1 and 2, which gives exit 1 and turns the test red."

### W5R2-2. MAJOR: SQL-phase inconclusive leaves the gate as exit 1 with a different marker, while CD32 gives the same host condition exit 3
- **Where:** Gate wiring step 4 ("rc 3 → echo DIAGNOSTIC, `die`"); T7's exit-3 negative; the Gate section.
- **Evidence:**
  - `die` exits 1 (`tester-unified-gate.sh:21`), and `cleanup_assay_gate_container` re-exits with the captured status.
  - CD32 (W4 O7a) makes "host busy" at gate entry exit 3 with `ASSAY_GATE_INCONCLUSIVE=…`.
  - CD21 (amended) makes the same condition in the SQL phase an exit 3 of the **harness**, which the gate converts to exit 1 with `ASSAY_GATE_DIAGNOSTIC=…`.
  - Automation keyed on exit 3 or on `ASSAY_GATE_INCONCLUSIVE=` (the mechanism CD32 introduced) treats the SQL variant as a real failure. T7's "exit 3 → red" freezes the exit-1 behaviour.
- **Fix text** (step 4): "rc 3 → `echo 'ASSAY_GATE_DIAGNOSTIC=sql-qualification-inconclusive'`; `printf 'ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun\n' >&2`; `exit 3`. The EXIT trap keeps 3: see W5R2-4." T7: "harness exit 3 makes the gate exit **3**, with stderr `ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun`, no receipt and no COMPLETE." Gate section: "Exit 3 with `ASSAY_GATE_INCONCLUSIVE=` (entry or SQL phase) means rerun later. Every other non-zero exit is real (CD29)." Record this in CD21 (amended). If the carver prefers exit 1 here, CD32 must say so explicitly.

### W5R2-3. MINOR: the harness `finally` is not exception-safe, a name conflict removes a foreign container, and a leaked container deadlocks the Gate instruction
- **Where:** Harness "Container" bullet (`finally`: df, then rm, "both `check=False`"); T5; the Gate section ("a `run-gate-*` running → wait until it ends").
- **Evidence:** the four leak paths in §3.
  - `_run` raises `TimeoutExpired` regardless of `check` (old `_run` 185-195).
  - The SIGTERM handler is still live inside `finally`.
  - A `docker run` refused with `Conflict` still reaches `docker rm -f -v NAME`.
  - A leaked `run-gate-assay-sql-*` makes every later gate exit 3 through CD32, and the brief tells the implementer to wait for it indefinitely.
- **Fix text** (Container bullet):
  - "`finally` (only after an attempted `docker run`) does, in order:
    1. `signal.signal(SIGTERM, signal.SIG_IGN)`;
    2. the df `_run` inside `try/except (subprocess.TimeoutExpired, OSError)`, printing the failure;
    3. `docker rm -f -v NAME` in its own identical `try/except`;
    4. restore the saved handler last.
  - If `docker run` failed and its stderr contains `Conflict`, raise `InconclusiveError('container name in use: NAME')` and do **not** remove: that name belongs to another process."
  - T5 adds: "the df stub raises `TimeoutExpired`, and rm is still the last call"; "the run stub fails with `Conflict`: no rm, exit 3".
  - Gate section adds: "A running `run-gate-assay-sql-<pid>-<epoch>` whose `<pid>` is not alive here (`kill -0`) and whose epoch is more than 65 minutes old is a leak from this package. Remove it by that exact name, log it in `W5-LOG.md`, then recheck. Never wait on it."

### W5R2-4. MINOR: "`cleanup_assay_gate_container` first runs …" can clobber `$?` and turn a red SQL phase green
- **Where:** Gate wiring, first paragraph.
- **Evidence:** the existing trap starts with `local result=$?; trap - EXIT` and ends with `exit "$result"` (`tester-unified-gate.sh:718-740`). Inserting the SQL cleanup literally **first** makes `result` the status of `rm -rf`/`|| true`, which is 0. A `die` in `run_sql_qualification` would then exit 0 with no COMPLETE marker. T7's "exit 1 → red" would catch it, but the brief text should not invite it.
- **Fix text:** "Right **after** `local result=$?` and `trap - EXIT` (never before: any command there resets `$?`), for each non-empty global, run `docker rm -f -v "$_assay_sql_container_name" >/dev/null 2>&1 || true` and `rm -rf -- "$_assay_sql_scratch" || true`, then the existing tester-container logic."

### W5R2-5. MINOR: oracle hygiene (the T5 SIGTERM negative kills the gate; T0's negative can be hollow)
- **Where:** T5, T0.
- **Evidence:**
  - With no harness handler (the obvious negative), `signal.raise_signal(SIGTERM)` in the pytest process takes the default action. The registered gate's pytest dies instead of reporting one red test.
  - T0's "one extra byte in a tmp copy → red" is satisfied by asserting `sha256(bytes+1) != pin`, which proves nothing about the check.
- **Fix text:**
  - T5: "Before calling `main`, save `signal.getsignal(SIGTERM)` and install `_sentinel` (it raises `AssertionError('main installed no SIGTERM handler')`). After `main` exits, assert `signal.getsignal(SIGTERM) is _sentinel`. Restore the original in the test's own `finally`."
  - T0: "The hash check is a function `verify_fixture_hashes(schema_path, fixture_root)`. The negative calls it on a tmp copy with one extra byte and requires it to raise."

### W5R2-6. MINOR: `run-assertions.sh` counts any psql failure as a kill, including a connection failure
- **Where:** `run-assertions.sh`, the `psql … || failed=…` line.
- **Evidence:** psql exits 3 only for a script error under `ON_ERROR_STOP`. It exits 2 for a lost or refused connection and 1 for its own fatal errors. Under the 512 MiB cap, a backend OOM makes the postmaster restart, and every later probe exits 2. Each is then recorded as a failed id, so an expected-`killed` row derives `killed` whether or not its mutant would kill. That is a false green for that row. K23's real kill ("cannot insert into view") is exit 3, so the fix does not disturb it.
- **Fix text** (replace the psql line):
  ```sh
      psql -X -q -v ON_ERROR_STOP=1 -U postgres -d "$SCHEMA_GATE_DBNAME" -f "$probe"
      rc=$?
      case "$rc" in
          0) ;;
          3) failed="${failed:+$failed,}$id" ;;
          *) echo "psql exit $rc on $id: infrastructure, not a probe verdict" >&2; exit 2 ;;
      esac
  ```
  Exit 2 then yields `ASSAY_SQL_FAILED=none`, and `derive_bucket` rule 5 raises, which is loud. Re-pin nothing else: the probe hash is unaffected.

### W5R2-7. MINOR (closes W5-16): 30 old tests are in neither Port nor Drop
- **Where:** Work 2.
- **Evidence:** the old file has 88 top-level `def test_` functions. Unassigned:
  - `test_harness_has_every_owned_signature` (:131);
  - the four `schema-gate.sh` tests (:153, :159, :167, :180);
  - the O3/O4 receipt tests ×2 (:630, :652);
  - `test_throwaway_postgres_names_are_unique_and_prefixed` (:666);
  - the 17 `_require_*` tests (:752–:845);
  - the five `main` mode tests (:879, :893, :901, :910, :928).

  "Wrapper-script ×4" leaves one of the five (:592–:616) unnamed.
- **Fix text:** "**Port (adapted):** the four `schema-gate.sh` tests. `sh -n` and shellcheck-when-available also run over `run-assertions.sh` and the generated `witness-gate.sh`. The old 'never invokes dstdns's script' test becomes 'the header names no dstdns revision'. **Drop:** the owned-signature test; both receipt tests; names-unique (the name now comes from `--container-name`, which T5 covers); all 17 `_require_*` (replaced by T3/T4); the five `main` mode tests (the O3/O4/witness modes are gone); and the wrapper test `…carries_the_exact_assertion_sql_verbatim` (:606), since no assertion SQL exists any more."

### W5R2-8. MINOR: Work 0's "tmpfs use > 50%" is measured at a point that hides the peak
- **Where:** Work 0, and the `finally` df.
- **Evidence:** the only `df` runs in `finally`, after every database is dropped. By then WAL has been recycled down toward `min_wal_size`. The peak is higher: base plus `qual` plus up to about `max_wal_size` (roughly 23 + 8 + 64 MB). My estimate puts it near the 50 % line, while the end-of-run reading is around 35 %.
- **Fix text:** "Work 0 records `df -Pk /var/lib/postgresql` after each apply while `qual` exists (before its DROP) and after the witness's last run. BLOCKED if the **maximum** exceeds 50 %."

### W5R2-9. MINOR: the class-map corpus scope is implicit
- **Where:** "dstdns class map" ("Per corpus file").
- **Evidence:** `tests/fixtures/mutation/sql/dstdns-21-create-workflow-corpus.sql` is byte-identical to `corpus/21-create-workflow-corpus.sql` (`cmp`) and is also slated for `git rm`. Counting it gives 223 sites, and Work 0b's "exactly the counts above" then fires a false BLOCKED.
- **Fix text:** "Corpus = exactly the three files in `gate/python/fixtures/dstdns-sql/corpus/` (111 + 8 + 52 = 171). The `tests/fixtures` copy of `21-…` is byte-identical and is not counted."

### W5R2-10. MINOR: small gaps
- "Context to read first" omits **CD31** (the old test's post-W4 path), **CD32** (the entry check that W5's phase sits behind) and **CD33**. "Host load (Decided CD28)" should cite **CD21 (amended)**.
- `run_sql_qualification` reads the global `$worktree`. Say so: T7's snippet must set `worktree=`. Alternatively pass it as `$1`.
- The witness `assay_version` source is unstated. Use `_assay_argv(sys.executable, "--version")` stdout minus `assay `, as the old test did (:1122-1128). It resolves the **host venv's installed** dist (`__init__.py:43`; the host has 7.1.1.dev188), which is fine because the value is normalized.
- The witness wrapper must copy `db/tests` the way the corpus is copied: `rm -rf /tests /tests_new; docker cp db/tests NAME:/tests_new; mv /tests_new /tests`. `docker cp db/tests NAME:/tests` into the existing `/tests` nests it as `/tests/tests` and silently runs the fixture-root probes instead.
- "No `docker`": pin the mechanism (`shutil.which("docker") is None`) so T5 stubs one seam.
- Work 3's first test also changes its span assertion to `{b"RESTRICT", b"NO ACTION"}`, because line 19 is `NO ACTION`.

## 8. Verdict

**READY-WITH-FIXES.** No blockers: the fixtures, hashes, matrix, class map and anchors all reproduce exactly. Before dispatch, fold in two MAJOR text fixes: W5R2-1 (the witness completeness check before the FAIL requirement, and a whole-function T4 case) and W5R2-2 (SQL-phase inconclusive exits 3 like CD32). Eight MINOR fixes are pasteable as written.

Counts: **0 BLOCKER, 2 MAJOR, 8 MINOR.** Round-1 closure: **18 CLOSED, 1 PARTIAL (W5-16), 0 OPEN.**
