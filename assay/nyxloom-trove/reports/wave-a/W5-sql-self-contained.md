# W5 — Self-contained SQL qualification (B126)

| Field | Value |
|---|---|
| Backlog / decision | **B126** / **A-480**; A-279…A-289 (`decisions.md:632-642`) stay binding |
| Branch | `wave-a-w5-sql` from `assay-b110-landing` **after W3, W1, W2, W6 and W4 have merged** (CD28; stage 2 merges W6→W4→W5); own worktree; `--no-ff` |
| Builds on | W4: `gate/tests/` package, `gate.tests.support`, `finish_registered_gate` and its captured commit, `run_bash` in `gate.tests.test_distribution_gate`. W3: `tests/adapters/sql/`. Re-resolve paths by file name |
| Class / implementer | **2b** / **Sonnet**, fresh (operator rule) |

**Why.** The gate never ran real PostgreSQL (the tester has no Docker socket, `tools/tester-unified-gate.sh:756-765`); the tag now means 18.6. The dstdns corpus's 171 sites form 13 classes, each represented below; 11 rows are new. **Carver questions, Decided (CD21, CD22):** an outer gate phase after a green tester; the local 18.6 digest, never pulled; host `python3` ≥3.11 on the clone's `src/`; out of scope → B132/B133.

## Context to read first
**`wave-a/CARVER-DECISIONS.md`** (CD8, CD21 (amended), CD22, CD28, CD29, CD31 old test's post-W4 path, CD32 entry check W5's phase sits behind, CD33 MANIFEST lines); `review-predispatch/REVIEW-waveA-sql-plan.md` §1 (folded in; this brief wins); `W4-test-split.md` P2, P4; old `gate/python/qualify_dstdns_sql.py` (`_run` 176-201, `ThrowawayPostgres` 501-627, O3/O4 705-858, witness 880-1133), its `schema-gate.sh` and tests; `mutation.py` `_classify_mutant_result_with_equivalence`.

## Implementation packet (normative)
**Files** (`F` = `gate/python/fixtures`). `git mv`: `gate/python/qualify_dstdns_sql.py` → `gate/python/qualify_sql.py`; `F/dstdns-sql/schema-gate.sh` → `F/sql/schema-gate.sh`; `test_gate_qualify_dstdns_sql.py` → `gate/tests/test_qualify_sql.py`. `git rm` after Work 0b: `F/dstdns-sql/corpus/`, `tests/fixtures/mutation/sql/dstdns-21-create-workflow-corpus.sql`. **Keep** `carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json` (Decided CD8: history, frozen at v13). New in `F/sql/`: `run-assertions.sh`, `matrix.json`, `tests/K*.sql` (21), `expected/sql-r2-witness.json`.

**Dedupe rule** (for the docs). A case is kept only if it differs from every kept one in replacement form, span shape (recogniser branch; text after it), enclosing object, lexical context (top, executed/inert `DO`), catalog effect (A-289) or bucket (only one deliberate survivor per replacement branch); names, types, trigger timing do not count.

**`tests/fixtures/mutation/sql/qualification/01-schema.sql`** (verbatim, 76 lines, every line LF-terminated including the last; sha256 `b7b07e973e988202dbc34cb3ff69415d6b79adc801c3b3566595cb3a63703af1`):
```sql
-- assay SQL qualification schema (A-480). [Knn] tags name matrix rows.
-- Trap: ON DELETE RESTRICT, NOT NULL, CHECK (x), UNIQUE in a comment.
/* Trap: CREATE TRIGGER t BEFORE INSERT ON parent; REFERENCES parent (id) */
CREATE DOMAIN posint AS integer
  NOT NULL -- [K04]
  CHECK (VALUE > 0); -- [K08]
CREATE TABLE parent (
  id integer PRIMARY KEY,
  label text NOT NULL, -- [K01]
  kind text NOT NULL DEFAULT 'alpha' -- [K02]
    CHECK (kind IN ('alpha', 'beta')), -- [K06] [K25]
  priority integer DEFAULT 1
    CONSTRAINT parent_priority_domain CHECK (priority IN (1, 2, 3)), -- [K05] [K24]
  code text UNIQUE, -- [K10]
  note text DEFAULT 'IS NOT NULL in a string'
);
CREATE TABLE child (
  id integer PRIMARY KEY,
  parent_id integer REFERENCES parent (id) ON DELETE NO ACTION, -- [K15] [K19]
  owner_id integer,
  slot integer,
  qty integer,
  CONSTRAINT child_slot_unique UNIQUE (parent_id, slot), -- [K11]
  CONSTRAINT fk_child_owner FOREIGN KEY (owner_id) REFERENCES parent (id) -- [K16]
    ON DELETE RESTRICT ON UPDATE CASCADE -- [K18]
);
ALTER TABLE child ALTER COLUMN qty SET NOT NULL; -- [K03]
ALTER TABLE child ADD CONSTRAINT child_qty_positive CHECK (qty > 0) NOT VALID; -- [K07]
ALTER TABLE child ALTER COLUMN slot DROP NOT NULL;
CREATE UNIQUE INDEX parent_label_uidx ON parent (label); -- [K13]
CREATE UNIQUE INDEX child_owner_big_uidx ON child (owner_id) -- [K14]
  WHERE qty > 100 AND owner_id IS NOT NULL;
CREATE TABLE shipment (id integer PRIMARY KEY, child_id integer);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shipment_child') THEN
    ALTER TABLE shipment ADD CONSTRAINT fk_shipment_child
      FOREIGN KEY (child_id) REFERENCES child (id) ON DELETE RESTRICT; -- [K17] [K20]
  END IF;
END $$;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'child_qty_positive') THEN
    ALTER TABLE child ADD CONSTRAINT child_qty_positive CHECK (qty > 0); -- [K09]
  END IF;
END $$;
CREATE TABLE audit (id bigserial PRIMARY KEY, tbl text);
CREATE FUNCTION parent_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  guard integer NOT NULL := 0;
BEGIN
  IF OLD.label IS NOT NULL AND NEW.label IS DISTINCT FROM OLD.label THEN
    RAISE EXCEPTION 'label is frozen';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER parent_freeze BEFORE UPDATE ON parent -- [K21]
  FOR EACH ROW EXECUTE FUNCTION parent_freeze();
CREATE FUNCTION child_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO audit (tbl) VALUES (TG_TABLE_NAME);
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER child_audit AFTER INSERT ON child -- [K22]
  DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION child_audit();
CREATE VIEW parent_labels AS SELECT DISTINCT id, label FROM parent;
CREATE FUNCTION parent_labels_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO parent (id, label) VALUES (NEW.id, NEW.label);
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER parent_labels_insert INSTEAD OF INSERT ON parent_labels -- [K23]
  FOR EACH ROW EXECUTE FUNCTION parent_labels_insert();
GRANT REFERENCES ON parent TO PUBLIC;
COMMENT ON TABLE parent IS 'NOT NULL CHECK (x) UNIQUE REFERENCES parent (id) ON DELETE RESTRICT';
CREATE TABLE "Trap Table" ("NOT NULL" text, "UNIQUE" text);
```
Carver-run, re-measured by the review: 24 sites, one per tag, none on trap lines {2,3,15,29,32,50,52,74,75,76}; all pass `collect_mutation_sites`. **Tag rule:** a line's tags name its sites left to right by ascending `start_byte` (lines 11, 13, 19, 38 carry two).

**Matrix** → `matrix.json` = `json.dumps(rows, indent=2) + "\n"`, rows sorted by id like `{"id": "K01", "line": 9, "operator": "sql:drop-not-null", "expected": "killed"}`. Ops: nn/ck/uq/fk = `sql:drop-not-null`/`-check`/`-unique`/`-foreign-key`, wd `sql:weaken-delete-action`, tg `sql:drop-trigger`, wi `sql:widen-check-in`. All `killed` except K02, K25 `survived` (DEFAULT hides K02; no realistic test inserts `__assay_widened__`), K09 `equivalent` (inert guard). Statements split at `; `; the **last** is the probe, the rest setup. Macros: `P` = `INSERT INTO parent (id,label) VALUES (1,'p')`; `C(v)` = `INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (v)`; `A(col,v)` = `INSERT INTO parent (id,label,col) VALUES (v)` (col ends at the first comma).
```
K01  9 nn | INSERT INTO parent (id,label) VALUES (1,NULL) | not_null_violation
K02 10 nn | -
K03 27 nn | C(1,NULL,NULL,NULL,NULL) | not_null_violation
K04  5 nn | PERFORM CAST(NULL AS posint) | not_null_violation
K05 13 ck | A(priority,1,'a',9) | check_violation
K06 11 ck | A(kind,1,'a','gamma') | check_violation
K07 28 ck | C(1,NULL,NULL,NULL,0) | check_violation
K08  6 ck | PERFORM CAST(0 AS posint) | check_violation
K09 44 ck | -
K10 14 uq | A(code,1,'a','c'); A(code,2,'b','c') | unique_violation
K11 23 uq | P; C(1,1,NULL,5,1); C(2,1,NULL,5,1) | unique_violation
K13 30 uq | INSERT INTO parent (id,label) VALUES (1,'d'); INSERT INTO parent (id,label) VALUES (2,'d') | unique_violation
K14 31 uq | P; C(1,NULL,1,NULL,200); C(2,NULL,1,NULL,300) | unique_violation
K15 19 fk | C(1,999,NULL,NULL,1) | foreign_key_violation
K16 24 fk | C(1,NULL,999,NULL,1) | foreign_key_violation
K17 38 fk | INSERT INTO shipment VALUES (1,999) | foreign_key_violation
K18 25 wd | P; C(1,NULL,1,NULL,1); DELETE FROM parent WHERE id=1 | foreign_key_violation
K19 19 wd | P; C(1,1,NULL,NULL,1); DELETE FROM parent WHERE id=1 | foreign_key_violation
K20 38 wd | C(1,NULL,NULL,NULL,1); INSERT INTO shipment VALUES (1,1); DELETE FROM child WHERE id=1 | foreign_key_violation
K21 57 tg | P; UPDATE parent SET label='q' WHERE id=1 | raise_exception
K22 64 tg | P; C(1,1,NULL,NULL,1) | require (SELECT count(*) FROM audit)=1
K23 72 tg | INSERT INTO parent_labels VALUES (1,'v') | require EXISTS (SELECT 1 FROM parent WHERE id=1)
K24 13 wi | A(priority,1,'a',4) | check_violation
K25 11 wi | -
```
**Probes, verbatim (Decided CD28):** `F/sql/tests/<id>.sql` per killed row, LF endings; sha256 of all 21 concatenated in id order `12f564d919d9575c71c2cee44b7f762f47cc00a7153a7c06e33f31afe14d0ce0` (4673 bytes). Form A (a condition), shown for K11; single-statement rows have no setup lines:
```sql
BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,1,NULL,5,1);
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (2,1,NULL,5,1);
  EXCEPTION WHEN unique_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K11';
END $$;
ROLLBACK;
```
Form C (`require`: K22, K23), one per line: `BEGIN;`, `DO $$`, `BEGIN`, `  <stmt>;` per statement, `  IF NOT (<check>) THEN RAISE EXCEPTION '<id>'; END IF;`, `END $$;`, `ROLLBACK;`.

**dstdns class map (Decided CD28).** Corpus = exactly the three files in `gate/python/fixtures/dstdns-sql/corpus/` (111 + 8 + 52 = 171); the `tests/fixtures` copy of `21-…` is byte-identical and is not counted. Per corpus file: `lex = lex_sql(data)`; sites over all lines, seven operators, `limit=500`; key `(operator, re.sub(r"widened with \d+", "widened with <int>", description), "body" if any(a <= start_byte < b for a, b in lex.dollar_bodies) else "top", x)`, `x` = `"default"` for nn if `re.match(rb"\s*DEFAULT\b", lex.mask[end_byte:], re.I)`, `"named"` for ck if `re.search(rb"CONSTRAINT\s+\w+\s+\Z", lex.mask[max(0, start_byte - 200):start_byte], re.I)`, else `""`. Carver-measured class (count) → row: nn (38) K01; nn default (36) K02; ck (28) K06; ck named (11) K05; wi string (20) K25; wi `<int>` (2) K24; uq `UNIQUE` (12) K11; uq partial `CREATE UNIQUE INDEX` (1) K14; fk `REFERENCES` (11) K15; fk `FOREIGN KEY` top (1) K16, body (3) K17; wd body (2) K20; tg (6) K21. New: K03 K04 K07 K08 K09 K10 K13 K18 K19 K22 K23.

**Out of scope (Decided CD22, CD28).** B132 (PostgreSQL-refused constructs) gets appended: unknown — `NOT NULL` on IDENTITY/serial/PK, string widen on an enum; unreached — generated expressions, `EVENT` triggers, quoted FK targets, triggers in `DO`; "needs an ad-hoc-construct mode of this harness". B133 holds the mislabels. Other new findings take ids the **next free ids**: one above the highest `## B…` heading in `4-backlog.md` when you commit (B134 and B135 are already taken).

**Harness (`qualify_sql.py`).** `IMAGE` = `postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2`; `RESTRICT_KEY` kept; schema read from `tests/fixtures/mutation/sql/qualification/01-schema.sql`; imports `VERDICT_SCHEMA_VERSION`, `LANE_SCHEMA_VERSION`. Docstring: the witness lane strips `DOCKER_HOST` and relies on `/var/run/docker.sock` reaching the same daemon.
- **CLI:** `--scratch DIR` (absent) `--container-name NAME` (fullmatch `run-gate-assay-sql-[0-9]+-[0-9]+`) `--cgroup-parent SLICE` (non-empty) `[--witness-out PATH] [--fixture-root DIR]` (default `F/sql`); a violation or Python <3.11 → `parser.error`. Exit 0 with stdout exactly `ASSAY_SQL_QUALIFIED=1`; 1 `QualificationError`; 3 `InconclusiveError`, stderr `ASSAY_SQL_INCONCLUSIVE=<reason>`, for exactly (Decided W5-local; visible, never skipped or green): `docker unavailable` (`docker version` fails, or `shutil.which("docker") is None`, the one seam T5 stubs); `image absent: docker pull IMAGE` (`docker image inspect IMAGE` fails; never pull); `host busy — rerun: <names>`; the readiness failsafe; a `_run` `TimeoutExpired`; `container name in use: NAME`; witness incomplete (`check_witness_verdict` step 1).
- **Host load (Decided CD21 (amended)):** once, right before `docker run`: `docker ps --no-trunc --format '{{.Names}}'`; any `run-gate-*` name → `host busy — rerun: <names, comma-joined>`, no `docker run` (the gate's own tester has exited). Never poll or wait.
- **Container:** exactly one `docker run -d --pull=never --network none --name NAME --cgroup-parent=SLICE --cpus 1 --memory 512m --memory-swap 512m --pids-limit 256 --mount type=tmpfs,destination=/var/lib/postgresql,tmpfs-size=268435456 -e POSTGRES_HOST_AUTH_METHOD=trust IMAGE postgres -c max_wal_size=64MB -c min_wal_size=32MB` (no `--rm`). Ready when `docker exec NAME psql -h 127.0.0.1 -U postgres -tAc 'SELECT 1'` prints `1`; failsafe 300 × `_sleep(1)` (module seam `_sleep = time.sleep`). `docker cp` the fixture root's `schema-gate.sh`, `run-assertions.sh`, `tests/` to `/`. `finally` (only after an attempted `docker run`) does, in order: (1) `signal.signal(SIGTERM, signal.SIG_IGN)`; (2) `docker exec NAME df -Pk /var/lib/postgresql` (logged to stderr) via `_run` inside `try/except (subprocess.TimeoutExpired, OSError)`, printing the failure; (3) `docker rm -f -v NAME` (last `_run`) in its own identical `try/except`; (4) restore the saved handler last. If `docker run` failed and its stderr contains `Conflict`, raise `InconclusiveError('container name in use: NAME')` and do **not** remove: that name belongs to another process. `main` installs `signal.signal(SIGTERM, lambda *_: sys.exit(3))` (so SIGTERM → `SystemExit(3)`) and restores the old handler in `finally`.
- **Databases (Decided CD28):** each scenario: `psql -v ON_ERROR_STOP=1 -U postgres -c 'DROP DATABASE IF EXISTS qual;'`, separately `-c 'CREATE DATABASE qual;'`, afterwards `-c 'DROP DATABASE qual;'`; `rm -f /dump.sql /kill.txt` before each run. One ≈8 MB database plus WAL ≤ `max_wal_size` fits the 256 MiB tmpfs within 512 MiB.
- **A run:** old `replace_corpus`, then `docker exec NAME env SCHEMA_GATE_INIT_SCRIPTS_DIR=/corpus SCHEMA_GATE_DBNAME=qual SCHEMA_GATE_DUMP_PATH=/dump.sql SCHEMA_GATE_KILL_SIGNAL_PATH=/kill.txt SCHEMA_GATE_RESTRICT_KEY=<key> SCHEMA_GATE_TEST_CMD='sh /run-assertions.sh' SCHEMA_GATE_ASSERT_DIR=/tests sh /schema-gate.sh`; read dump and signal with `docker exec NAME cat` if `test -f`, else `None`.
- `parse_failed_ids(signal) -> frozenset[str]`: `None` → empty; else, minus one trailing LF, fullmatch `schema test command failed \(exit [1-9][0-9]*\): ASSAY_SQL_FAILED=(none|K[0-9]{2}(,K[0-9]{2})*)` or raise `QualificationError`; `none` → empty.
- `derive_bucket(row_id, *, exit_code, dump, baseline_dump, failed_ids) -> str`, first match: (1) `dump is None` → `crashed`; (2) `dump == baseline_dump` → `equivalent` (even with exit ≠ 0, as `_classify_mutant_result_with_equivalence`); (3) `exit_code == 0` → `survived`; (4) `row_id in failed_ids` → `killed`; (5) raise `QualificationError(f"{row_id}: test failed without naming {row_id}")`. A bucket ≠ `expected` raises `f"{row_id}: derived {bucket}, expected {expected}"`, so `crashed` never passes.
- **Flow.** (1) `check_sites(schema_text, matrix)`: sites (all lines, seven operators, limit 500) mapped by the tag rule equal each row's `(line, operator)`; errors name the ids or the untagged line. (2) Checks; start; copy. (3) Baseline: exit 0, dump present, no signal → `baseline_dump`; O5: two `pg_dump --schema-only --no-owner -U postgres -d qual` **without** `--restrict-key` differ. (4) Rows in id order (`MutationSite.apply`); bucket == `expected`. (5) Controls, each `crashed`: **O4-residue** (Decided CD28) — on one `qual`, the unmutated apply exits 0, then the K01 mutant exits ≠ 0 with no dump (`type "posint" already exists`: the non-idempotent assay schema turns the dstdns-era residue premise into a loud failure); **M11** — K24's site with replacement `b", '__assay_widened__')"`. (6) Witness. (7) `SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database` = `postgres,template0,template1`, else `QualificationError("database residue")`. Stderr receipt: `ASSAY_SQL_ROW=<id>:<bucket>`; `ASSAY_SQL_CONTROL=o4-residue:crashed`/`m11:crashed`/`o5:differs`.
- **Witness (6)**, as old `capture_witness`: lane `sql_qualification`, `schema_version = LANE_SCHEMA_VERSION`, `source_roots = ["db/schema"]`, seven operators, `jobs = 1`, `max_mutants = 24`, `budget = "60m"`, same artifacts. Base: `.gitignore` (`.assay/`), `db/tests/` (probes). Head adds `db/schema/01-schema.sql`, `assay.toml`, `tools/witness-gate.sh` (old wrapper: database `witness`; `db/schema`→`/corpus`, `db/tests`→`/tests` copied as `rm -rf /tests /tests_new; docker cp db/tests NAME:/tests_new; mv /tests_new /tests` (a plain `docker cp db/tests NAME:/tests` nests as `/tests/tests` and silently runs the fixture-root probes); `-e SCHEMA_GATE_ASSERT_DIR=/tests`; TEST_CMD `sh /run-assertions.sh`; `DROP DATABASE witness;` just before `exit $rc`). `assay run` failsafe 3900 s. `assay_version` source: `_assay_argv(sys.executable, "--version")` stdout minus `assay ` (as old :1122-1128; the host venv's installed dist, fine since normalized). `check_witness_verdict(verdict, matrix)` does, in this order: (1) **Completeness:** raise `InconclusiveError('witness incomplete: <outcome>/<reason_code>')` (exit 3) if the top-level `outcome` is `BUDGET_EXCEEDED`, or any claim's `reason_code` is `LANE_TIMEOUT` or `CANDIDATE_HUNG`, or the R2 claim's `mutation.hung` or `mutation.budget_exceeded` is non-empty; (2) only then require R0 PASS, outcome `FAIL`, reason `MUTANTS_SURVIVED` (else `QualificationError`); (3) then `cross_check(verdict, matrix)`: key `(lineno, operator)` over the R2 claim's `mutation.{killed,survived,equivalent,crashed,hung,budget_exceeded}`; each row in exactly one list, == `expected`; no unmatched entry; each killed `parse_failed_ids(kill_signal)` holds its id. Then `compare_with_witness` against `F/sql/expected/sql-r2-witness.json`, or with `--witness-out` write the normalized verdict (`json.dumps(v, indent=2, sort_keys=True) + "\n"`) instead of that comparison.

**`schema-gate.sh`**: the old file with an assay-owned header (A-480; keep the A-279 ordering and NB-6 text), the loop `for sql_file in "$SCRIPT_DIR"/[0-9][0-9]-*.sql` without the 95-/99- case, dumps unchanged, step 3 exactly:
```sh
out="$(mktemp)"
set +e
sh -c "$TEST_CMD" >"$out" 2>&1
rc=$?
set -e
cat "$out"
if [ "$rc" -ne 0 ]; then
    line="$(grep '^ASSAY_SQL_FAILED=' "$out" | tail -n 1)"
    printf 'schema test command failed (exit %s): %s\n' "$rc" "${line:-ASSAY_SQL_FAILED=none}" > "$KILL_SIGNAL_PATH"
fi
rm -f "$out"
exit "$rc"
```
**`run-assertions.sh`** exactly (so a signal reads `schema test command failed (exit 1): ASSAY_SQL_FAILED=K05,K24`):
```sh
#!/bin/sh
# assay SQL qualification probes (A-480): one file per killed matrix row.
set -u
LC_ALL=C
export LC_ALL
: "${SCHEMA_GATE_DBNAME:?}" "${SCHEMA_GATE_ASSERT_DIR:?}"
failed=""
for probe in "$SCHEMA_GATE_ASSERT_DIR"/K*.sql; do
    [ -f "$probe" ] || { echo "no probes in $SCHEMA_GATE_ASSERT_DIR" >&2; exit 2; }
    id="$(basename "$probe" .sql)"
    psql -X -q -v ON_ERROR_STOP=1 -U postgres -d "$SCHEMA_GATE_DBNAME" -f "$probe"
    rc=$?
    case "$rc" in
        0) ;;
        3) failed="${failed:+$failed,}$id" ;;
        *) echo "psql exit $rc on $id: infrastructure, not a probe verdict" >&2; exit 2 ;;
    esac
done
[ -z "$failed" ] || { echo "ASSAY_SQL_FAILED=$failed"; exit 1; }
```

**Gate wiring (Decided CD28, W5-local)**, outer mode, above `# --- entry points`. Globals `_assay_sql_container_name=""`, `_assay_sql_scratch=""`; in `cleanup_assay_gate_container`, right **after** `local result=$?` and `trap - EXIT` (never before: any command there resets `$?`), for each non-empty global run `docker rm -f -v "$_assay_sql_container_name" >/dev/null 2>&1 || true` and `rm -rf -- "$_assay_sql_scratch" || true`, then the existing tester-container logic. `run_sql_qualification <commit> <cgroup>` (`local out rc=0`; reads the global `$worktree`, so T7's snippet must set `worktree=`):
1. `_assay_sql_scratch="$(mktemp -d)"`; `make_exact_oid_clone "$worktree" "$_assay_sql_scratch"`; `die "SQL clone is not the gated commit $commit"` unless its `rev-parse HEAD` = `$commit`.
2. `_assay_sql_container_name="run-gate-assay-sql-${BASHPID}-$(date +%s)"`.
3. `out="$(nice -n 19 python3 -I "$_assay_sql_scratch/clone/assay/gate/python/qualify_sql.py" --scratch "$_assay_sql_scratch/sql" --container-name "$_assay_sql_container_name" --cgroup-parent "$cgroup")" || rc=$?`.
4. rc 3 → `echo 'ASSAY_GATE_DIAGNOSTIC=sql-qualification-inconclusive'`; `printf 'ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun\n' >&2`; `exit 3` (no receipt, no COMPLETE; the EXIT trap keeps 3); other rc ≠ 0 → `die "SQL qualification failed (exit $rc)"`; `$out` ≠ `ASSAY_SQL_QUALIFIED=1` → `die 'SQL qualification printed no exact marker'`.
5. `rm -rf -- "$_assay_sql_scratch"`; clear both globals; `echo 'ASSAY_GATE_PHASE=sql-qualified'`.

Entry: W4's tester call → `run_sql_qualification "<W4's captured commit>" "$cgroup_parent"` → W4's `finish_registered_gate`. A red tester already ends the script (`set -e`): never PostgreSQL after a red tester.

## Work
0. **Measure PostgreSQL first (Decided CD28).** `docker ps` (no `run-gate-*`). In the session scratchpad, the fixtures above plus a throwaway driver (never committed) using the harness's `docker run` argv and a `run-gate-assay-sql-$$-$(date +%s)` name. Record baseline (exit 0, 21 probes pass); per row the apply exit, dump absent/equal/different, `ASSAY_SQL_FAILED`, `derive_bucket`; O4-residue, M11, O5; record `df -Pk /var/lib/postgresql` after each apply while `qual` exists (before its DROP) and after the witness's last run. Remove the container by exact name. Table → `wave-a/W5-REPORT.md` §Step 0. BLOCKED if a bucket differs from the matrix, the residue re-apply exits 0, or the **maximum** tmpfs use exceeds 50%.
0b. **Class map (Decided CD28)**, before any `git rm`: `W5-REPORT.md` §dstdns classes, one row per class (key, count, three `file:line`, row) plus the new rows. BLOCKED unless exactly the 13 classes and counts above.
1. Moves, removals, fixtures, `qualify_sql.py`, both scripts.
2. `gate/tests/test_qualify_sql.py` (via `gate.tests.support`). **Port:** `normalize_verdict` ×6, `compare_with_witness` ×2, witness-is-current-schema, `_require_witness_commit_matches` ×2, `_assay_argv` ×2, wrapper-script ×4 (adapted), the four `schema-gate.sh` tests (`sh -n` and shellcheck-when-available also run over `run-assertions.sh` and the generated `witness-gate.sh`), the old "never invokes dstdns's script" test (becomes "the header names no dstdns revision"), `_run` ×3, `main` scratch refusal, `_wait_ready` ×2, `_remove` no-op, `__main__` dispatch. **Drop:** the owned-signature test (`test_harness_has_every_owned_signature`), both O3/O4 receipt tests, `test_throwaway_postgres_names_are_unique_and_prefixed` (the name now comes from `--container-name`, T5), all 17 `_require_*` (replaced by T3/T4), the five `main` mode tests (O3/O4/witness modes are gone), wrapper test `…carries_the_exact_assertion_sql_verbatim` (no assertion SQL exists any more), `verify_pinned_inputs` ×8, corpus list/export/write ×4, blob pins, scenario-site tests, `create_role` ×2, every `docker`/`dstdns_checkout` fixture test (they skipped silently in the socketless tester). Add T0–T6. No test starts a container (`_run`, `_sleep` stubbed) or skips on Docker absence.
3. `tests/adapters/sql/`: `FIXTURE_PATH` → the new schema; the three `test_real_dstdns_fixture_*` tests become: weaken lines [19, 25, 38] (the first test's span assertion becomes `{b"RESTRICT", b"NO ACTION"}`, since line 19 is `NO ACTION`); no site on trap lines; 24 jobs, seven operators. Lexer: line 2's `ON DELETE RESTRICT` masked, line 38's kept in the one `dollar_bodies` span holding `FOREIGN KEY`. Reword dstdns docstrings (and `test_adapters_sql_test_path.py:29`).
4. Stale refs: `src/assay/provenance.py:245` (comment; sole `src` edit), `test_cli_provenance_and_request_base.py:357`, `test_runner_result_report.py:573`.
5. Wiring, T7. `docker ps`; CLI with `--witness-out`, review each row, then without. Docs, decisions, backlog, gate.

## Oracles (each negative applied, seen red, reverted, logged)
- **T0** sha256: schema `b7b07e97…3af1`; 21 probes concatenated `12f564d9…0ce0`. The check is a function `verify_fixture_hashes(schema_path, fixture_root)`. Negative: call it on a tmp copy with one extra byte and require it to raise.
- **T1** `check_sites` passes. Negatives: `x integer NOT NULL,` added → red naming that line; line 14 without `UNIQUE` → red naming K10; K06/K25 operators swapped in a matrix copy → red naming both.
- **T2** unique ids and `(line, operator)`; tags ↔ rows; each operator has a killed row; probe file iff killed, both ways. Negatives: remove `K05.sql`; add `K02.sql`.
- **T3** `derive_bucket` five branches; equal dump with exit 1 → `equivalent`; exit 1 with `{K24}` for K05 → raises. `parse_failed_ids`: `none`, two ids, malformed → raises.
- **T4** `cross_check` accepts a built verdict; refuses a bucket mismatch, K05 as survived, a killed signal without its id, K01/K02 swapped (per row, not counts), a `hung` entry (exit 3). Through `check_witness_verdict` (not `cross_check` alone): a built verdict with K02's entry moved to `budget_exceeded` and the R2 claim and top level set to `BUDGET_EXCEEDED`/`LANE_TIMEOUT` exits 3. Break: swap steps 1 and 2 → exit 1, red.
- **T5** stubbed `_run`/`_sleep`: exact `docker run` argv; inspect argv has `@sha256:d3e1620b…`; `docker rm -f -v NAME` last on the readiness-failsafe, `QualificationError` and SIGTERM (`signal.raise_signal` in the stub) paths; before calling `main`, save `signal.getsignal(SIGTERM)` and install `_sentinel` (raises `AssertionError('main installed no SIGTERM handler')`), after `main` exits assert `signal.getsignal(SIGTERM) is _sentinel`, restore the original in the test's own `finally`; the df stub raises `TimeoutExpired` and rm is still the last call; the run stub fails with `Conflict`: no rm, exit 3; image absent, no `docker`, a `run-gate-x` name from `docker ps` → exit 3 with that reason and no `docker run`; bad `--container-name` or empty `--cgroup-parent` → `parser.error`.
- **T6** none of `/workspaces/dstdns`, `dstdns-sql`, `qualify_dstdns`, `dstdns-21-create`, `151cda0d`, `113154e6`, `820d4c3c`, `88de912d`, `fc1a694d`, `e188053a`, `d4b394ad`, `84b043f6` in `git ls-files -- src tests gate tools` minus `tests/fixtures/verdicts/` (frozen conformance data, A-480); tokens concatenated in source; bare `dstdns` stays legal (≈25 `src` comments, out of scope). Negative: a token planted in a `.sh` in a tmp tree → found.
- **T7** `run_bash` (PATH stubs as existing gate tests): fake harness → `ASSAY_GATE_PHASE=sql-qualified` once, between tester and `finish_registered_gate`. Red, no phase marker: exit 1; marker plus exit 1; marker plus an extra line; harness exit 3 (after `ASSAY_GATE_DIAGNOSTIC=sql-qualification-inconclusive`) makes the gate exit **3**, stderr `ASSAY_GATE_INCONCLUSIVE=sql-qualification — rerun`, no receipt, no COMPLETE; clone HEAD ≠ commit; red tester (harness never invoked). The trap issues `docker rm -f -v <name>` and removes the scratch. Read the container name from the docker stub's log, never recompute `date +%s`; set `worktree=` in the snippet.
- **R1** CLI exit 0, witness equal. Break: `--fixture-root` copy with `tests/K05.sql` = `SELECT 1;` → exit 1 naming K05.
- **R2** gate log order `ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_GATE_PHASE=sql-qualified`, W4's receipt and COMPLETE markers; no own break (T7).

**Traceability** (work | owner | oracle | fixture | break): fixtures | W1 | T0–T2 | schema, matrix, probes | byte, swap, `K02.sql` · derivation | W1 | T3, T4, W0 | built dumps/verdicts | precedence, K01/K02 · container | W1 | T5 | `_run` stub | SIGTERM · gate phase | W5 | T7, R2 | fake harness | marker+exit 1 · PostgreSQL | W0, W5 | R1 | pinned image | K05 `SELECT 1;` · dstdns refs | W1, W4 | T6 | tmp tree | `.sh` token.

**Forbidden in tests:** AUTHORING §3b A–F exactly as tabled in `W4-test-split.md`.

## Docs, decisions, backlog, scope
README §SQL (`:632`), DESIGN-GUIDE §11 (`:2809`): schema, digest, phase, dedupe rule, class map (`W5-REPORT.md`), two derivations, O4-residue premise. CONSUMERS SQL (`:1057`): out-of-scope list. Decision notes: A-280 (`88de912d` pin retired by A-480), A-286 (dstdns fixture removed), A-289 (TimescaleDB exclusion obsolete; named/unnamed divergence is now K05/K06). One line in the W3, W5, W6, W7, W8 `MANIFEST.md`: `dstdns-sql-r2-v6-witness.json: retired by A-480; frozen at v13; no longer migrated`. Backlog: B126 DONE (class map as evidence); B132/B133 as above; new ids = the next free ones after the highest in `4-backlog.md`. **Forbid:** `src/**` except `provenance.py:245`; `assay.toml`; `run-gate.toml`; carve-assets beyond those lines.

## Gate, host load, BLOCKED, review
`docker ps` (a `run-gate-*` running → wait until it ends, recheck). If a `run-gate-assay-sql-*` container you did not start in this session is running and is more than 90 minutes old, stop as BLOCKED and name it in `W5-LOG.md`. Never remove a container you did not create, and never wait on it indefinitely. The controller decides; `cd <wt>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W5-gate.log 2>&1; echo "exit=$?"`; read markers separately. Every failure is real (CD29); exit 3 with `ASSAY_GATE_INCONCLUSIVE=` (at entry or in the SQL phase) means rerun later; every other non-zero exit is real. **Host load:** production game server; nice/ionice; one gate at a time; PostgreSQL only after a green tester, in `dev-gates.slice`; never full `self-qualification`; remove containers by exact name. Manual CLI: `s="$(mktemp -d)"; nice -n 19 ionice -c3 python3 -I gate/python/qualify_sql.py --scratch "$s/sql" --container-name "run-gate-assay-sql-$$-$(date +%s)" --cgroup-parent "$CGROUP_PARENT_DEV_GATES" …; rm -rf -- "$s"`. Editor tools; trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

**BLOCKED** (`W5-LOG.md`, commit, stop): W3/W1/W2/W6/W4 unmerged; a Work 0/0b contradiction; a checksum differs; the baseline or a probe fails unmutated; a row derives another bucket; readiness race; any gate failure. Never edit expectations toward green.

**Review:** fresh session; rerun the CLI **without** `--witness-out` first and diff against the committed witness; recompute T1 and the class map; empty a probe and a kill signal; read the witness.
