# Pre-dispatch adversarial review: W5 (self-contained SQL) and the Wave A plan

**Reviewer:** fresh session, read-only. **Worktree:** `.worktrees/assay-b110-landing`, HEAD `be803c3a`. Paths are relative to `assay/` unless absolute.
**Scope:** `W5-sql-self-contained.md`; `assay-WAVE-A-PLAN-2026-09-29.md`; `wave-a/CARVER-DECISIONS.md`; decisions A-475..A-480 (plus A-279/A-280/A-289); backlog B122–B133; the headers of every `wave-a/` brief.

## 0. What I measured (no container started, nothing edited)

| Claim | Result |
|---|---|
| Schema sha256 `b7b07e97…` for 76 lines | **Holds**, with every line LF-terminated including the last. Without the final LF the hash is `f1cd0335…`. |
| "24 sites, one per tag, none on trap lines" | **Holds.** The shipped `SqlAdapter` over the extracted schema gives exactly 24 sites on exactly the tagged lines and operators. `collect_mutation_sites` accepts all 24. |
| Old corpus "171 sites" | **Holds**: 111 + 8 + 52. |
| Image pin | `docker image inspect postgres:18-alpine@sha256:d3e1620b…` resolves locally to Id `b07129cc…`. `PG_VERSION=18.6`, created 6 weeks ago, so it passes the 14-day freshness rule. `Config.Volumes = {"/var/lib/postgresql":{}}`, which the tmpfs mount covers, so no anonymous volume is created. |
| Outer phase can reach Docker | Yes. `tester-unified` is `environment = "bare-host"` (`run-gate.toml:22-31`), and the outer mode already runs `docker run`/`inspect`. `DOCKER_HOST=unix:///run/docker-api/docker.sock` is set. `/var/run/docker.sock` reaches the same daemon (same `docker info` ID). |
| Tester has no socket and no network | Confirmed at `tools/tester-unified-gate.sh:756-765` (bind mount of the repo only, `--network=none`). |
| B107 known-red test | `35adca38` is an ancestor of HEAD. `test_run_liveness_classifies_a_thread_join_hang_as_hung` exists nowhere under `tests/` (only in docs). `612843ef` is on neither main nor HEAD; it was superseded by the deletion. |
| REBASE-P0-P2 R0 steps 1–2 | Already done at HEAD: the exclusions fixture says `liveness.py` `[94, 95]`, and main has been merged. |
| Live gate containers right now | `run-gate-rg55-p1-r2-…` and `run-gate-vbpub-mutation-…` are running. This is the concurrency that finding W5-6 is about. |

**Desk check of all 24 rows against PostgreSQL semantics** (reasoned, not run):
- Every killed row's probe gets its SQLSTATE unmutated and loses it under its own mutant: domain `CAST` raises 23502/23514; NO ACTION and RESTRICT both raise 23503 at statement end, inside the inner block; RAISE is P0001.
- K02 and K25 survive: the mutant changes the dump and no probe observes it. K09 is equivalent: the `DO` guard is inert because line 28 already created `child_qty_positive`, and `DO` bodies are not dumped.
- K23 is killed by "cannot insert into view" (DISTINCT makes the view non-updatable), not by its `EXISTS` check. That is fine, but the check only serves as the unmutated positive control.
- Cross-kills (K05→K24, K15→K19, K16→K18, K17→K20) always include the row's own id. **No row's claimed bucket is wrong.** The defects below are in the harness contract around the matrix, not in the matrix itself.

---

## 1. Findings: W5 (`wave-a/W5-sql-self-contained.md`)

### W5-1. BLOCKER: "Old O4 on K01" cannot pass on this schema
- **Where:** Harness, Flow (4), line 134.
- **Evidence:** O4 re-applies the mutant on a database that already carries the unmutated schema and *requires the re-apply to exit 0* (`qualify_dstdns_sql.py` `_require_o4_gate_applied(residue_gate, …)`, line 846). That only worked because the dstdns corpus is idempotent (`20-create-corpora.sql:7 CREATE TABLE IF NOT EXISTS`). The W5 schema is deliberately non-idempotent (`CREATE DOMAIN posint` at line 4, plain `CREATE TABLE`). The residue apply therefore stops at line 4 ("type posint already exists"), `schema-gate.sh` exits under `set -e`, and a faithful port raises `QualificationError` on every run. The implementer hits BLOCKED, or improvises.
- **Fix text** (replace "Old O4 on K01"): "(4a) **O4-residue:** on database `qual`, run `schema-gate.sh` with the unmutated corpus (must exit 0), then again on the same database with the K01-mutated corpus. Require exit ≠ 0 and no dump, so `derive_bucket` gives `crashed`: a residue apply is loud and never passes as `equivalent` or `survived`. The dstdns-era O4 premise (an idempotent corpus) does not hold for an assay-owned, non-idempotent schema (A-480). T3 gets this case." If the operator prefers, drop O4 instead and record why in DESIGN-GUIDE §11.

### W5-2. BLOCKER (Sonnet-executability): the probe template is unparseable for 11 of 21 rows, and the probes are not verbatim
- **Where:** "Probe `tests/Knn.sql`" (line 127) and the matrix notation (lines 100-125).
- **Evidence:**
  - Read literally, `DO $$ BEGIN <setup>; BEGIN <probe>; …` with an empty setup (K01, K03, K04, K05, K06, K07, K08, K15, K16, K17, K24) produces `BEGIN ; BEGIN …`. PL/pgSQL has no empty statement.
  - Which statements are setup and which is the probe ("P; c(…); c(…)", "then", "parents (…)") is left to inference.
  - The probes are the oracle. A Sonnet implementer writing them is the carver's design choice delegated.
- **Fix text:** ship the 21 files verbatim in the brief, or at minimum the exact rule:

  "Form A (condition), no setup:
  `BEGIN;\nDO $$\nBEGIN\n  BEGIN\n    <probe>;\n  EXCEPTION WHEN <cond> THEN RETURN;\n  END;\n  RAISE EXCEPTION '<id>';\nEND $$;\nROLLBACK;\n`

  Form A with setup: the same, with one line `  <stmt>;` per setup statement before the inner `BEGIN`.

  Form C (K22, K23): `BEGIN;\nDO $$\nBEGIN\n  <stmt>; …\n  IF NOT (<check>) THEN RAISE EXCEPTION '<id>'; END IF;\nEND $$;\nROLLBACK;\n`.

  In the matrix notation, the **last** statement is the probe and all earlier ones are setup.
  - `P` = `INSERT INTO parent (id, label) VALUES (1, 'p')`.
  - `c(a,b,c,d,e)` = `INSERT INTO child (id, parent_id, owner_id, slot, qty) VALUES (a,b,c,d,e)`.
  - `pa(x) VALUES (…)` = `INSERT INTO parent (id, label, x) VALUES (…)`.
  - `then (…)` repeats the previous INSERT head with the new VALUES.
  - `parents (…)` = `INSERT INTO parent (id, label) VALUES (…)`.
  - `shipment (…)` = `INSERT INTO shipment VALUES (…)`.
  - Files end with LF. `run-assertions.sh` identifies a failure by file name, never by message."

### W5-3. MAJOR (dependency contradiction)
- **Where:** header rows "Branch" and "Order" (lines 6-7) vs BLOCKED (line 165).
- **Evidence:**
  - The header says "branch after W3, W1" and "parallel with W4". BLOCKED says "W3/W1/W4 unmerged → BLOCKED". W5 would therefore be BLOCKED at its first step.
  - W5 needs W4's `gate/tests/__init__.py`, `gate/tests/support.py` (CD14), `finish_registered_gate` (W4 P4) and `run_bash`'s new home (`gate/tests/test_distribution_gate.py`) for Work 2, Work 5 and T7.
  - W4 and W5 both rewrite the gate's entry section.
- **Fix text** (header): "**Branch** `wave-a-w5-sql` from `assay-b110-landing` **after W3, W1, W2, W6 and W4 have merged**. **Order:** stage 2, last. Delete 'Parallel with W4'. T7 imports `run_bash` from `gate.tests.test_distribution_gate`."

### W5-4. MAJOR: externally visible serialized forms are not fixed (AUTHORING 2b: "serialized forms … fixed")
- **Where:** Harness bullets, lines 131-135; T3/T4.
- **Evidence:** the witness freezes each killed mutant's `kill_signal` verbatim. The old witness has `"kill_signal": "schema test command failed (exit 3) against database witness: …"`. So these become frozen contract, yet each is left to the implementer:
  - the grammar and order of `ASSAY_SQL_FAILED=<ids>`;
  - how `schema-gate.sh` captures "the last ASSAY_SQL_FAILED= line";
  - `matrix.json`'s operator spelling (abbreviated or `sql:*`);
  - `derive_bucket`'s signature, precedence and "five outcomes" ("no dump → crashed (error)" can be read two ways);
  - how verdict mutants map to matrix rows.
- **Fix text:**
  - "`run-assertions.sh`: `LC_ALL=C`. Run **every** `$SCHEMA_GATE_ASSERT_DIR/K*.sql` in glob order. If any fails, print exactly one stdout line `ASSAY_SQL_FAILED=<id>[,<id>…]` (ascending, comma-separated, no spaces) and `exit 1`; otherwise print nothing and `exit 0`."
  - "`schema-gate.sh` step 3: `sh -c \"$TEST_CMD\" >\"$out\" 2>&1; rc=$?; cat \"$out\"`. On rc≠0 write `schema test command failed (exit $rc): $(grep '^ASSAY_SQL_FAILED=' \"$out\" | tail -n 1)`, or `…: ASSAY_SQL_FAILED=none` if absent. Here `$out` is `mktemp`."
  - "`matrix.json`: `[{\"id\":\"K01\",\"line\":9,\"operator\":\"sql:drop-not-null\",\"expected\":\"killed\"}, …]`, sorted by id, full vocabulary names."
  - "`derive_bucket(row_id, *, exit_code, dump, baseline_dump, failed_ids) -> str`, in this order:
    1. `dump is None` → `crashed`
    2. `dump == baseline_dump` → `equivalent` (even when exit≠0, mirroring `mutation._classify_mutant_result_with_equivalence`)
    3. `exit_code == 0` → `survived`
    4. `row_id in failed_ids` → `killed`
    5. otherwise raise `QualificationError('<id>: test failed without naming <id>')`.

    The caller raises unless bucket == expected, so `crashed` is always refused.
    T3 covers exactly these five inputs plus the precedence case 'dump equal and exit 1 → equivalent'."
  - "The verdict→row key is `(mutant.lineno, mutant.operator)` from the R2 claim's `mutation.<bucket>` lists. It is unique in this matrix; T2 asserts that."

### W5-5. MAJOR: PostgreSQL storage lifecycle and container count are unspecified; ENOSPC/OOM risk on a 256 MiB tmpfs
- **Where:** `docker run` bullet (line 133); Flow (3)–(5).
- **Evidence:**
  - "Per row, fresh DB" does not say which database name is used or whether it is dropped. The old harness (the model the brief says to follow) created one **distinct** database per scenario and **three sequential containers** (O3, O4, witness each had their own `ThrowawayPostgres`).
  - That pattern would give about 24 × 7.5 MB of databases, plus un-checkpointed CREATE DATABASE WAL (WAL_LOG strategy). Without drops, `max_wal_size` defaults to 1 GB. That exceeds a 256 MiB tmpfs, which also counts against the 512 MiB memory cgroup together with the 128 MB `shared_buffers`.
  - The failure mode is PANIC / crash, which makes rows derive `crashed`. A Sonnet implementer would then see "red, BLOCKED" or improvise sizes. This is unmeasured (reasoned).
- **Fix text:**
  - "Exactly **one** `docker run` per CLI invocation. Append server args after IMAGE: `postgres -c max_wal_size=64MB -c min_wal_size=32MB`.
  - Every direct row, O4-residue, O5 and M11 uses database `qual`, created with `psql -c 'DROP DATABASE IF EXISTS qual;'` then `psql -c 'CREATE DATABASE qual;'` (two invocations, A-289).
  - The witness wrapper keeps database `witness`, created the same way.
  - Before removal, log `docker exec NAME df -Pk /var/lib/postgresql` to stderr. BLOCKED if use exceeds 50%."

### W5-6. MAJOR: host-load, "one gate container at a time" is only instruction, not mechanism
- **Where:** "Host load" (line 163); gate wiring (line 137); CD21.
- **Evidence:**
  - The registered gate is run by every later package and by the release. Its outer phase starts PostgreSQL 30–60 minutes after the launching agent's `docker ps` check, without re-checking.
  - The name `assay-sqlq-…` does not carry the estate's gate prefix `run-gate-…` (compare `run-gate-assay-selfhosted-…` at `:745`, and the live `run-gate-*` containers above). Between the tester's exit and PostgreSQL starting, another session's `docker ps` sees no gate at all.
  - The wiring also runs PostgreSQL **after a red tester**: `|| tester_status=$?` → phase → `exit "$tester_status"`.
- **Fix text** (brief and CD21):
  - "The PostgreSQL container belongs to the same registered gate run and uses its slot. It starts only after the tester container has exited, so one gate never has two running. It runs in `$cgroup_parent` (`dev-gates.slice`) with `--cpus 1 --memory 512m`.
  - Name: `run-gate-assay-sqlq-${BASHPID}-${RANDOM}-$(date +%s)`, so other sessions' `docker ps` checks see this gate as still running.
  - The harness does not poll for foreign gates: that would make the verdict contention-dependent.
  - If the tester exits non-zero, `exit "$tester_status"` **before** the phase: no PostgreSQL after a red tester."

### W5-7. MAJOR: the known-red clause is obsolete and now hides real failures
- **Where:** line 163 ("Known red (plan §4) … may be the only failure; log it") and BLOCKED line 165.
- **Evidence:** measurement row "B107 known-red test" in §0.
- **Fix text:** delete the sentence. BLOCKED becomes "gate red (any failure)". The same fix is needed in W4 (`W4-test-split.md:173,180`) and plan §4.

### W5-8. MAJOR: the backlog instruction contradicts CD22, and part of the out-of-scope list has no home
- **Where:** "Backlog: B126 done; next free B-id for the list" (line 160) and "Out of scope" (line 129).
- **Evidence:** CD22 already filed B132 (PostgreSQL-refused constructs) and B133 (mislabels). A literal implementer files a third id (B134) that duplicates them. The "unknown" items (NOT NULL on IDENTITY/serial/PK; string widen on an enum) and the "unreached" items (generated expressions, EVENT triggers, quoted FK targets, triggers in `DO`) are in neither B132 nor B133.
- **Fix text:** "Backlog: B126 DONE. Append the 'unknown' and 'unreached' bullets to **B132** (measure on this harness). The mislabels are **B133**. File no new id."

### W5-9. MAJOR: the outer scratch (a full vbpub clone) leaks on every gate run; the clone commit is not pinned to the gated commit
- **Where:** gate wiring (line 137): `mktemp -d; make_exact_oid_clone`.
- **Evidence:**
  - `make_exact_oid_clone` does `git clone --no-local` of the whole repo: 128 MB of objects plus about 40 MB of `assay/` checkout.
  - The inner mode's scratch dies with its container. The outer one lives in the devcontainer's `/tmp`, and nothing removes it.
  - `make_exact_oid_clone` re-reads `HEAD`, so the SQL phase can qualify a commit other than W4's captured `C`. `finish_registered_gate` then refuses, but only after the work is wasted.
- **Fix text:**
  - "Global `_assay_sql_scratch=\"\"`. The cleanup trap runs `rm -rf -- \"$_assay_sql_scratch\"` when it is non-empty (exact mktemp path), after removing the container.
  - After the clone, die unless `git -C \"$scratch/clone\" rev-parse HEAD` equals W4's captured `C`.
  - Keep W4's capture-C/T and clear steps before `run_registered_tester_container`."

### W5-10. MAJOR: A-480's "covers more situations than dstdns" becomes unprovable once the corpus is deleted
- **Where:** "Why" (line 10: "171 sites are 13 classes … all rows here") and Work 1 (`git rm F/dstdns-sql/corpus/`, the fixture).
- **Evidence:**
  - The 171 → 13 classification is asserted but recorded nowhere. `grep "13 classes"` finds only this line.
  - B126 says "Record which situations are covered".
  - After the `git rm`, nobody can re-derive it without history (A-475).
- **Fix text:** "The carver attaches `wave-a/W5-dstdns-site-classes.md` before dispatch. It has one row per class: the dedupe-rule key, the count out of 171, example `file:line`s, and the representing `Knn`. The 11 new rows are marked 'new'. DESIGN-GUIDE §11 cites it."

### W5-11. MAJOR (process): the highest-risk seam is unproved before dispatch
- **Where:** the matrix and "All pass unmutated".
- **Evidence:** only the adapter run is cited ("Carver-run: 24 sites"). The real-PostgreSQL buckets, the probe baseline, readiness and the tmpfs sizing have no receipt. AUTHORING §6 says "the carver must … witness each acceptance negative fail before dispatch", and 2b says "a proved construction path for the highest-risk seam". My desk check agrees with every bucket (§0), but W5-1, W5-2 and W5-5 are exactly what a single measured run would have surfaced.
- **Fix text:** "Before dispatch, the carver runs one throwaway measurement. Rules: one container, `docker ps` first, nice/ionice, `--cgroup-parent=dev-gates.slice`, removal by exact name. Record baseline pass, the 24 derived buckets, and the R1 break, and cite the log in the brief." The alternative is to state explicitly that the BLOCKED rule is the expected first outcome.

### W5-12. MINOR: `--cgroup-parent` is optional
- **Where:** CLI spec, line 131.
- **Evidence:** the manual CLI runs (Work 5, R1, the review rerun) omit it, so those containers escape `dev-gates.slice`.
- **Fix text:** "`--cgroup-parent` is required. The gate passes `$cgroup_parent`. Manual runs pass `\"$CGROUP_PARENT_DEV_GATES\"`, and an empty value is a `parser.error`."

### W5-13. MINOR: T6 scope and rationale
- **Where:** T6, line 152.
- **Fix text:**
  - "Scan `git ls-files -- src tests gate tools`: tracked files only, which excludes `__pycache__`.
  - The negative plants the token in a **non-`.py`** file (a `.sh` in a tmp tree), so a `*.py`-only scanner is red.
  - `tests/fixtures/verdicts/` is excluded because its `gate/qualify_dstdns_sql.sh` string is frozen conformance data, not a checkout or revision (A-480).
  - The bare word 'dstdns' legitimately remains in about 25 `src` provenance comments (for example `adapters/sql.py:236` and `adapters/python.py:65-275`). A-480 forbids checkout and revision references only, and `src/**` is out of scope."

### W5-14. MINOR: CD8 conflict and stale MANIFESTs
- **Where:** Work 1 `git rm carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json`; Scope "Forbid … other carve-assets".
- **Evidence:** CD8 says retired harnesses' carve assets "stay in place as history". The W5, W6, W7 and W8 `MANIFEST.md` files each call this file "a LIVE witness that `gate/python/qualify_dstdns_sql.py` regenerates", and W5 forbids editing them.
- **Fix text:** keep the file, and add to W3 `MANIFEST.md`: "retired by A-480; frozen at v13; no longer migrated". Allow one-line notes in the W5–W8 MANIFESTs. The alternative is to amend CD8 to allow removing a LIVE (non-frozen) witness and allow those four MANIFEST edits.

### W5-15. MINOR: decision notes are missing
- **Fix text:** add notes to A-280 ("the evidence pin `88de912d` is retired with A-480"), A-286 (the dstdns fixture is removed) and A-289 (the TimescaleDB exclusion is obsolete; the named/unnamed divergence is now K05/K06).

### W5-16. MINOR: "port the old pure tests" is undefined
- **Where:** Work 2.
- **Fix text:** "**Port:** `normalize_verdict` ×6, `compare_with_witness` ×2, witness-is-current-schema, `_require_witness_commit_matches` ×2, `_assay_argv` ×2, wrapper-script ×4 (adapted), `_run` ×3, `main` scratch refusal, `_wait_ready` ×2, `_remove` no-op, `__main__` dispatch. **Drop:** `verify_pinned_inputs` ×8, list/export/write corpus ×4, fixture blob pins, the scenario-site tests, `create_role` ×2, and every `docker`/`dstdns_checkout` fixture test. Those tests always skipped inside the socketless tester (a silent skip), and their removal is intended."

### W5-17. MINOR: signal and clock seams
- **Evidence:** Python's default SIGTERM skips `finally`, so a killed harness leaks the container on a manual run. The readiness loop's 300 × 1 s needs an injectable sleep for T5 (rule E).
- **Fix text:** "`signal.signal(SIGTERM, lambda *_: sys.exit(3))` in `main`. `_sleep = time.sleep` is a module seam that T5 stubs."

### W5-18. MINOR: gate acceptance must check exit status and exact output
- **Fix text:** "The gate requires exit 0 **and** stdout == `ASSAY_SQL_QUALIFIED=1`. T7 negatives:
  - a fake that prints the marker and exits 1 → red;
  - a fake that prints the marker plus one extra line → red;
  - exit 3 → the gate prints `ASSAY_GATE_DIAGNOSTIC=sql-qualification-inconclusive` (retry, contention-agnostic rule) before dying."

### W5-19. MINOR: smaller gaps
- **T1's negative has no mechanism.** The CLI has no `--schema` option. Fix: "T1 calls `check_sites(schema_text, matrix)`."
- **Add two negatives to T1:** remove `UNIQUE` from line 14 → red naming K10; swap the operators of K06 and K25 → red.
- **Add a T0:** pin the schema sha256 in `gate/tests`.
- **`schema-gate.sh` rewrite is unspecified.** Fix: drop the 95-/99- exclusion and the dstdns header, and keep A-279/NB-6.
- **Imports.** Also import `LANE_SCHEMA_VERSION` for the lane template.
- **Witness lane template.** Also give `jobs = 1` explicitly.
- **File name.** `test_path.py:29` is `tests/adapters/sql/test_adapters_sql_test_path.py` (W3 keeps basenames).
- **Lane environment.** The witness lane strips `DOCKER_HOST`. It works only because `/var/run/docker.sock` reaches the same daemon. Document that in the harness docstring.
- **The dedupe rule's "bucket" axis is circular** (omitting a probe manufactures a distinct case). Fix: "bucket distinguishes only the single deliberate survivor per replacement branch."

### Per-oracle attack: a plausible wrong implementation that still passes as written

| Oracle | Negative given? | Wrong implementation that passes today | Required added negative |
|---|---|---|---|
| T1 | yes (extra `NOT NULL`) | a one-way check (site ⇒ row), or row↔site matched by line only (K06/K25 swap passes) | remove line 14's `UNIQUE` → red K10; swap the K06/K25 operators → red |
| T2 | yes (drop a probe) | "probe iff killed" checked one way only (a stray `tests/K02.sql` passes) | add `K02.sql` → red |
| T3 | implicit | checks exit before dump equality ("equal dump + exit 1" → killed); "exit≠0 → killed" ignoring the id | the precedence case; exit 1 with `{K24}` for row K05 → raise |
| T4 | yes (K05 as survived) | bucket compared by counts per bucket (swapping K01 and K02 passes) | a verdict copy with K01 and K02 swapped → red |
| T5 | yes (missing image) | `docker image inspect postgres:18-alpine` by tag; `rm` only on the success path | assert the inspect argv carries `@sha256:d3e1…`; assert `rm -f -v NAME` last on readiness-failsafe, `QualificationError` and SIGTERM paths |
| T6 | yes | a `*.py`-only scanner | plant the token in a `.sh` file |
| T7 | yes | checks stdout only, or `grep`s for the marker | marker + exit 1 → red; marker + extra line → red |
| R1 | yes (K05 `SELECT 1`) | the review regenerates the witness with `--witness-out` first (self-fulfilling) | the review runs **without** `--witness-out` first and diffs against the committed file |
| R2 | none (evidence) | phase echoed with the harness skipped | covered by T7; state that R2 has no break of its own |

Also missing: AUTHORING §7's traceability table (work item → owner → oracle → fixture → break). Add it.

**Positive notes:**
- The digest pin with `--pull=never` and the exit-3 refusal is right.
- "No test starts a container" is right: it retires the old socket-gated tests, which always skipped silently inside the tester.
- Readiness over TCP (`-h 127.0.0.1`), because the init server is socket-only, is a correct reading of the image.
- Removal by exact name, on both the harness side and the trap side, is right.

---

## 2. Findings: plan, carver decisions, decisions, backlog

### X-1. MAJOR: plan §4 known-red is obsolete, and so are the "after B107 lands" conditions
- **Where:** plan §4 line 58; §1 line 12; §5 step 1; W4 lines 173 and 180; W5 lines 163 and 165; REBASE-P0-P2 R0 steps 1–2.
- **Evidence:** measurement rows "B107 known-red test" and "REBASE-P0-P2 R0 steps 1–2" in §0.
- **Fix text:**
  - §4: "No known-red test: `35adca38` (merged into landing) deleted the B107 real-clock tests. Any gate failure is a failure."
  - §1 and §5: drop "after the B107 fix lands".
  - Mark REBASE R0.1 and R0.2 done.

### X-2. MAJOR: CD7 is orphaned and contradicts W1 and A-479
- **Where:** CD7; W1 lines 76-77 ("Change none … REPORT: … consumer-visible").
- **Evidence:**
  - No brief removes ciu provenance schema 1. W1 forbids `src/assay` changes and classifies the removal as consumer-visible. W10 promises "nothing observable changes".
  - CD7 also extends A-477 (assay's *own* verdict and lane schemas) to a *foreign* producer's input.
  - A-479 makes any consumer-visible change a major version, while it also reserves 8.0.0 for v14.
- **Fix text** (CD7): "Deferred to the v14 wave (8.0.0), exactly like CD6. Wave A changes no accepted input."

### X-3. MAJOR: §5 and A-479 have no consumer-visible surface list
- **Fix text:** add to plan §5, as a checklist the release step must fill before choosing the version:
  - **W1:** gate phases, the 1.2.5 smoke, and cross-project harnesses: tooling-only, not consumer-visible.
  - **W2:** a new top-level `assay_analysis` in wheel and zipapp. `import assay.analysis` is dropped (CD19: undocumented). The `assay analyze` CLI and the schema paths (CD16) are unchanged.
  - **W4:** new `b105_report_check.py` flags; internal.
  - **W5:** none; `src` changes by a comment only.
  - **W6 (P2):** any new `ReasonCode` or refusal text in verdicts. Check it.
  - **W8 (P0):** any verdict field or `assay plan` output change. Check it; A-479 says "needs no schema change".
  - **W9:** a new `analyze` subcommand, additive.
  - **W10:** none by contract.
  - **CD7:** deferred (X-2).

  Also amend A-479: "if any row is consumer-visible, the release is 8.0.0 and the v14 wave becomes 9.0.0". Otherwise defer that change.

### X-4. MAJOR: CD11 contradicts W4 P4 on the receipt
- **Where:** CD11 vs `W4-test-split.md:99,104,133`.
- **Evidence:** CD11 says the receipt carries "commit, tree, assay version and completion markers". W4 says exactly four keys `{schema_version, lane, commit, tree}` and O5 refuses an extra key. CARVER-DECISIONS says "this file wins", so the W4 implementer is told two incompatible things.
- **Fix text:** amend CD11 to "exactly W4 P4's four keys". Or amend W4 P4 and O5 to add `assay_version` and `markers`. Pick one before W4 is dispatched.

### X-5. MAJOR: CD15 and W2 contradict W4 Work 3's ini pins
- **Where:** `W4-test-split.md:120` pins "ini keys exactly `pythonpath=[\"src\"]`, `testpaths=[\"tests\"]`". W2 (merged earlier) sets `pythonpath = ["src","analysis/src"]`, `testpaths = ["tests","analysis/tests"]` (`W2-analysis-package.md:49`). CD15 says W4's drift test pins the W2 value.
- **Fix text** (W4 Work 3): "ini keys exactly `pythonpath=[\"src\",\"analysis/src\"]`, `testpaths=[\"tests\",\"analysis/tests\"]` (CD15)."

### X-6. MAJOR: the ordering rules omit W4→W5 and W4→W7
- **Where:** plan §2 "Ordering rules"; stage-2 row W5.
- **Fix text:**
  - Add: "W4 merges before W5 branches (W5 needs `gate/tests`, `support.py`, `finish_registered_gate`)."
  - Add: "W4 before W7 (CD12)."
  - Set W5's "Touches" to: gate script outer phase and trap, `gate/python/`, `gate/tests/test_qualify_sql.py`, SQL fixtures, `tests/adapters/sql/`, the `provenance.py` comment, README, DESIGN-GUIDE and CONSUMERS.
  - Set the stage-3 merge order: W7, then W8, then W9 (W7 and W8 both edit `assay.toml`).

### X-7. MAJOR: W7 (plan §3) is not a Sonnet-executable brief
- **Evidence:**
  - §3 still names `tests/test_self_lane.py`. CD12 moves it to `gate/tests/`.
  - The drift proof's baseline ("the last full-history preflight") is undefined. After W4 changes both B105 argvs, no earlier preflight is comparable, so only a same-commit full-history preflight is. That means two preflights (3 CPUs, 60 minutes each) on a shared host.
  - The negative says "add a tiny fixture test if none exists", which is a design choice. A judge test reading the real repository's `HEAD~1` would itself violate A-475.
  - There is no BLOCKED rule and no gate section.
- **Fix text** (§3): "Edit `gate/tests/test_self_lane.py:<pin>` (was `tests/test_self_lane.py:102`).
  - **Drift proof:** at W7's parent commit, run `self-qualification-preflight` once with `full` and once with `shallow`: serially, host permitting, `docker ps` first. Compare collected, passed and skipped counts. Any difference is BLOCKED.
  - **Negative:** use the existing fixture-repository tests `tests/**/test_isolation.py::test_shallow_seed_*` and `test_b105_git_process_boundaries.py::test_p22_rejects_grafted_or_shallow_history`. Add nothing.
  - **BLOCKED:** a newly skipped or failing judge test.
  - **Gate:** `tester-unified`."

### X-8. MINOR: CD13 needs `analysis/src` too
- **Evidence:** after W2, `python -m assay analyze` lazily imports `assay_analysis`.
- **Fix text:** "with `src` **and `analysis/src`** on `PYTHONPATH`."

### X-9. MINOR: plan hygiene
- §4 gate command lacks `nice -n 19 ionice -c3` (W4 and W5 have it).
- The header's backlog list omits B131–B133.
- §5 step 3 "then `docs/testability-cleanup-20260928`" does not say what that ref is.

### X-10. MINOR: backlog
- **B126** should require the class table from W5-10 as acceptance.
- **B132** absorbs W5's unknown/unreached bullets (W5-8), and should note that W5's harness has a fixed matrix, so B132 needs a harness mode for ad-hoc constructs.
- **B122** is untouched and consistent with plan §6 ("conditional").

**Checked and consistent:**
- CD1↔W8/W2; CD2/CD3↔REBASE-P0-P2; CD5↔W1 Q1; CD9/CD10↔W4 C1/C2; CD12↔W4 C4; CD14↔W4 P2; CD16–CD19↔W2 Q1–Q4; CD20↔REBASE-P8; CD21↔W5 (apart from W5-6); CD22↔B132/B133; CD23↔W3 C3.
- W1 depends on W3. W2 depends on W3, with a second-merger rebase. W4 depends on W1 and W2. W8 and W9 depend on W2. W9 depends on W8. W10 is last. All consistent with the stage table.
- A-475, A-476, A-478 and A-480 are internally consistent. A-477 is consistent once CD7 is deferred. A-479 needs the X-3 sentence.

---

## 3. Verdicts

| Document | Verdict | Why |
|---|---|---|
| `wave-a/W5-sql-self-contained.md` | **NOT READY** | Blockers: W5-1 (O4 cannot pass), W5-2 (probe template unparseable, probes not verbatim). Majors: W5-3 to W5-11 (dependency contradiction, unfixed serialized forms, storage lifecycle, host-load mechanism, obsolete known-red, duplicate backlog id, scratch leak, unrecorded class map, unproved PostgreSQL seam). All are text fixes; the matrix itself is sound. |
| `assay-WAVE-A-PLAN-2026-09-29.md` | **READY-WITH-FIXES** | X-1, X-3, X-6, X-7 (W7 needs the §3 rewrite before dispatch), X-9. |
| `wave-a/CARVER-DECISIONS.md` | **READY-WITH-FIXES** | X-2 (CD7 → defer), X-4 (CD11 vs W4), X-5 (CD15 vs W4), X-8 (CD13), W5-6 (CD21 host-load text), W5-14 (CD8 vs W5's `git rm`). |
| decisions A-475..A-480 | **READY-WITH-FIXES** | A-479 needs the version fallback (X-3). A-477's scope should be kept to assay's own schemas (X-2). Separately, A-280, A-286 and A-289 need notes (W5-15). |
| backlog B122–B133 | **READY-WITH-FIXES** | X-10, W5-8. |
| (collateral) `wave-a/W4-test-split.md` | must take X-1, X-4, X-5 before dispatch | not in scope for a verdict |
