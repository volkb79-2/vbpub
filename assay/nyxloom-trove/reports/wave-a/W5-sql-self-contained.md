# W5 — Self-contained SQL qualification (B126)

| Field | Value |
|---|---|
| Backlog / decision | **B126** / **A-480**; A-279…A-289 (`decisions.md:632-642`) stay binding |
| Branch | `wave-a-w5-sql` from `assay-b110-landing` after **W3, W1**; own worktree; `--no-ff` |
| Order | Parallel with W4, **merges after W4** (`gate.tests.support`, `finish_registered_gate`); SQL tests in `tests/adapters/sql/` (W3) |
| Class / implementer | **2b** / **Sonnet**, fresh (operator rule) |

**Why.** The gate never ran real PostgreSQL (tester container has no Docker socket, `tools/tester-unified-gate.sh:756-765`). The tag now means 18.6. The corpus's 171 sites are 13 classes (rule below), all rows here, plus 11 new. Read: the old harness (`ThrowawayPostgres` 504-630, O4 811-857, witness 880-1133), its `schema-gate.sh` and tests, `src/assay/adapters/sql.py:1-90`.

**Carver questions (assumed "yes").** Q1 real-DB evidence is an outer gate phase after the tester exits. Q2 pin the local 18.6 digest. Q3 that phase uses host `python3` ≥3.11 and the clone's `src/`. Q4 out-of-scope: follow-up.

## Implementation packet (normative)
**Files** (`F` = `gate/python/fixtures`). `git mv`: `gate/python/qualify_dstdns_sql.py` → `gate/python/qualify_sql.py`; `F/dstdns-sql/schema-gate.sh` → `F/sql/schema-gate.sh`; `tests/test_gate_qualify_dstdns_sql.py` → `gate/tests/test_qualify_sql.py`. `git rm`: `F/dstdns-sql/corpus/`, `tests/fixtures/mutation/sql/dstdns-21-create-workflow-corpus.sql`, `carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json`. New in `F/sql/`: `run-assertions.sh`, `matrix.json` (`[{"id","line","operator","expected"}]`), `tests/K*.sql` (21), `expected/sql-r2-witness.json`.

**Dedupe rule.** Keep a case only if it differs from every kept one in replacement form, span shape (recogniser branch; text after it), enclosing object (table, domain, index, partial index, view, constraint trigger), lexical context (top, executed/inert `DO`), catalog effect (A-289) or bucket; names, types, trigger timing do not count. So no K12 (`ALTER … ADD … UNIQUE (a)` = K11), no plain `AFTER` trigger (= K21).

**`tests/fixtures/mutation/sql/qualification/01-schema.sql`** (verbatim, 76 lines; sha256 `b7b07e973e988202dbc34cb3ff69415d6b79adc801c3b3566595cb3a63703af1`):
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
Carver-run: 24 sites, one per tag, none on trap lines {2,3,15,29,32,50,52,74,75,76}; all pass `collect_mutation_sites`.

**Matrix.** All `killed` except **K02 survived** (DEFAULT hides it), **K25 survived** (no realistic test inserts `__assay_widened__`), **K09 equivalent** (inert guard). `id line op | probe | condition`; ops: nn/ck/uq/fk = drop-not-null/-check/-unique/-foreign-key, wd weaken-delete-action, tg drop-trigger, wi widen-check-in. `P` = `INSERT INTO parent (id,label) VALUES (1,'p')`; `c(…)` = `INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (…)`; `pa(x)` = `INSERT INTO parent (id,label,x)`.
```
K01  9 nn | INSERT INTO parent (id,label) VALUES (1,NULL) | not_null_violation
K02 10 nn | -
K03 27 nn | c(1,NULL,NULL,NULL,NULL) | not_null_violation
K04  5 nn | PERFORM CAST(NULL AS posint) | not_null_violation
K05 13 ck | pa(priority) VALUES (1,'a',9) | check_violation
K06 11 ck | pa(kind) VALUES (1,'a','gamma') | check_violation
K07 28 ck | c(1,NULL,NULL,NULL,0) | check_violation
K08  6 ck | PERFORM CAST(0 AS posint) | check_violation
K09 44 ck | -
K10 14 uq | pa(code) VALUES (1,'a','c') then (2,'b','c') | unique_violation
K11 23 uq | P; c(1,1,NULL,5,1); c(2,1,NULL,5,1) | unique_violation
K13 30 uq | parents (1,'d') then (2,'d') | unique_violation
K14 31 uq | P; c(1,NULL,1,NULL,200); c(2,NULL,1,NULL,300) | unique_violation
K15 19 fk | c(1,999,NULL,NULL,1) | foreign_key_violation
K16 24 fk | c(1,NULL,999,NULL,1) | foreign_key_violation
K17 38 fk | INSERT INTO shipment VALUES (1,999) | foreign_key_violation
K18 25 wd | P; c(1,NULL,1,NULL,1); DELETE FROM parent WHERE id=1 | foreign_key_violation
K19 19 wd | P; c(1,1,NULL,NULL,1); DELETE FROM parent WHERE id=1 | foreign_key_violation
K20 38 wd | c(1,NULL,NULL,NULL,1); shipment (1,1); DELETE FROM child WHERE id=1 | foreign_key_violation
K21 57 tg | P; UPDATE parent SET label='q' WHERE id=1 | raise_exception
K22 64 tg | P; c(1,1,NULL,NULL,1); require (SELECT count(*) FROM audit)=1
K23 72 tg | INSERT INTO parent_labels VALUES (1,'v'); require EXISTS (SELECT 1 FROM parent WHERE id=1)
K24 13 wi | pa(priority) VALUES (1,'a',4) | check_violation
K25 11 wi | -
```
Probe `tests/Knn.sql`: `BEGIN; DO $$ BEGIN <setup>; BEGIN <probe>; EXCEPTION WHEN <condition> THEN RETURN; END; RAISE EXCEPTION 'Knn'; END $$; ROLLBACK;` (K22/K23: `IF NOT (<check>) THEN RAISE EXCEPTION 'Knn'; END IF;`). All pass unmutated.

**Out of scope (Q4; one backlog item).** Emitted, reasoned PostgreSQL-refused (→ `crashed`): `UNIQUE … DEFERRABLE`/`NULLS NOT DISTINCT`/`INCLUDE`/`USING INDEX`, FK `MATCH FULL`/`DEFERRABLE`/`SET NULL (col)`; unknown: `NOT NULL` on IDENTITY/serial/PK, string widen on an enum. Mislabels: widened `NOT IN`; drop-check on `POLICY … WITH CHECK`. Unreached: generated expressions, `EVENT` triggers, quoted FK targets, triggers in `DO`.

**Harness (`qualify_sql.py`).** CLI `--scratch DIR` (absent) `--container-name NAME [--cgroup-parent SLICE] [--witness-out PATH] [--fixture-root DIR]`; stdout exactly `ASSAY_SQL_QUALIFIED=1`. Exit 0; 1 `QualificationError`; 3 `InconclusiveError` (docker/image missing, readiness failsafe, `budget_exceeded`, `hung`, `LANE_TIMEOUT`); Python <3.11 → `parser.error`. Import `VERDICT_SCHEMA_VERSION`.
- Image `postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2`; absent from `docker image inspect` → exit 3 naming `docker pull`; never pull.
- `docker run -d --pull=never --network none --name NAME --cpus 1 --memory 512m --memory-swap 512m --pids-limit 256 --mount type=tmpfs,destination=/var/lib/postgresql,tmpfs-size=268435456 -e POSTGRES_HOST_AUTH_METHOD=trust [--cgroup-parent=SLICE] IMAGE`, no `--rm`. Ready when `docker exec NAME psql -h 127.0.0.1 -U postgres -tAc 'SELECT 1'` prints `1` (init server is socket-only); failsafe 300 × 1 s. Always `docker rm -f -v NAME`.
- Flow: (1) `SqlAdapter` sites == matrix, each on its tagged line. (2) Baseline, `SCHEMA_GATE_TEST_CMD='sh /run-assertions.sh'`, exits 0. (3) Per row, fresh DB, `derive_bucket`: no dump → crashed (error); dump equal → equivalent; exit 0 → survived; id in `ASSAY_SQL_FAILED` → killed; else error; == `expected`. (4) Old O4 on K01, O5, M11 (`, '__assay_widened__')` at K24, refused). (5) `assay run` as old `capture_witness`: lane `sql_qualification`, `source_roots = ["db/schema"]`, seven operators, `max_mutants` = rows, `budget = "60m"`; base commit `.gitignore` + `db/tests/`; head adds schema, wrapper (copies `db/schema`→`/corpus`, `db/tests`→`/tests`), `assay.toml`. Buckets == `expected`; killed signals hold their id; `FAIL`/`MUTANTS_SURVIVED`; normalized verdict == witness.
- `schema-gate.sh`: apply `[0-9][0-9]-*.sql`; kill signal `schema test command failed (exit <rc>): <last ASSAY_SQL_FAILED= line>`; keep A-279/NB-6. `run-assertions.sh`: `psql -X -q -v ON_ERROR_STOP=1 -U postgres -d "$SCHEMA_GATE_DBNAME" -f` each `$SCHEMA_GATE_ASSERT_DIR/K*.sql`; failures → print `ASSAY_SQL_FAILED=<ids>`, exit 1.

**Gate wiring.** Above `# --- entry points`: `run_sql_qualification <scratch> <name> <cgroup>` runs `nice -n 19 python3 -I "$scratch/clone/assay/gate/python/qualify_sql.py" --scratch "$scratch/sql" --container-name "$name" --cgroup-parent "$cgroup"`, dies unless stdout is the marker, echoes `ASSAY_GATE_PHASE=sql-qualified`. Global `_assay_sql_container_name=""`, `rm -f -v`'d first by the cleanup trap. Entry: `run_registered_tester_container … || tester_status=$?`; `mktemp -d`; `make_exact_oid_clone`; name `assay-sqlq-${BASHPID}-${RANDOM}-$(date +%s)`; the phase; `exit "$tester_status"` if non-zero; `finish_registered_gate`.

## Work
1. Moves, removals, schema (checksum), 21 probes, `matrix.json`; `qualify_sql.py`, `schema-gate.sh`, `run-assertions.sh`.
2. `gate/tests/test_qualify_sql.py`: port the old pure tests, add T1–T6. **No test starts a container** (stub `_run`).
3. `tests/adapters/sql/`: `FIXTURE_PATH` → new schema; the three `test_real_dstdns_fixture_*` tests become: weaken lines [19, 25, 38]; no site on trap lines; 24 jobs, seven operators. Lexer: line 2's `ON DELETE RESTRICT` masked, line 38's kept in the one `dollar_bodies` span holding `FOREIGN KEY`. Reword dstdns docstrings (and `test_path.py:29`).
4. Stale refs: `src/assay/provenance.py:245` (comment; sole `src` edit), `test_cli_provenance_and_request_base.py:357`, `test_runner_result_report.py:573`.
5. Wiring, T7. `docker ps`; CLI with `--witness-out`, then without; review each witness row. Docs, backlog, gate.

## Oracles
- **T1** sites == matrix; negative: a schema copy plus `x integer NOT NULL,` → red naming it.
- **T2** unique ids; tags ↔ rows; every operator has a killed row; probe iff killed; negative: drop a probe → red.
- **T3** `derive_bucket`: all five outcomes, each error named.
- **T4** cross-check refuses a bucket mismatch and a signal without its id; compare refuses K05 as survived.
- **T5** stubbed `_run`: exact argv; `rm -f -v` on every exit path; missing image → exit 3 naming the pull.
- **T6** no `/workspaces/dstdns`, `dstdns-sql`, `qualify_dstdns`, `dstdns-21-create`, `151cda0d`, `113154e6`, `820d4c3c`, `88de912d`, `fc1a694d`, `e188053a`, `d4b394ad`, `84b043f6` in `src tests gate tools` (minus `tests/fixtures/verdicts/`; tokens concatenated); negative: a one-token string is found.
- **T7** `run_bash`: fake harness → marker once; failing/wrong-output fake → non-zero, no marker; cleanup `rm -f -v`; phase between tester and `finish_registered_gate`.
- **R1** CLI exit 0, witness equal; logged break: `--fixture-root` copy with `tests/K05.sql` probe `SELECT 1` → exit 1 naming K05.
- **R2** gate log: `ASSAY_GATE_PHASE=sql-qualified`, W4's completion markers.

**Forbidden in tests (AUTHORING §3b):** A. no time-based verdicts; timeouts are generous failsafes; a slow-host red is true. B. no unrestored global state; no `monkeypatch` of `__getattr__` proxies. C. no hollow tests (`pass`, "nothing raised", call counts, private attributes, log strings); never weaken an assertion. D. no no-cover pragma, even in comments. E. no real network, registry or clock. F. no predicted numbers.

## Docs, backlog, scope
README §SQL (`:632`), DESIGN-GUIDE §11 (`:2809`): the qualification (schema, digest, phase, dedupe rule, two derivations). CONSUMERS SQL (`:1057`): the out-of-scope list. W3 `MANIFEST.md`: witness moved (A-480). Backlog: B126 done; next free B-id for the list. **Forbid:** `src/**` except `provenance.py:245`; `assay.toml`; `run-gate.toml`; other carve-assets.

## Gate, host load, BLOCKED, review
`docker ps`; `cd <wt>/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W5-gate.log 2>&1; echo "exit=$?"`; then read markers. **Known red (plan §4):** `test_run_liveness_classifies_a_thread_join_hang_as_hung` (B107) may be the only failure; log it. **Host load:** production game server here; nice/ionice; one gate container, none during another session's gate; PostgreSQL only after the tester exits; never full `self-qualification`; remove containers by exact name. Editor tools; trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

**BLOCKED** (`W5-LOG.md`, commit, stop): W3/W1/W4 unmerged; checksum differs; unmutated schema or a probe fails; a row derives another bucket; readiness race; gate red beyond the known test. Never edit expectations toward green.

**Review:** fresh session; rerun the CLI, recompute T1, empty a probe and a kill signal, read the witness.
