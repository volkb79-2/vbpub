# W5 report -- self-contained SQL qualification (B126, A-480)

Base: `c8e6b783` (W4 reviewed head `21a7b1f0` merged with the landing branch: W3, W1, W2, W6). Package files:
`gate/python/qualify_sql.py`, `gate/python/fixtures/sql/`, `tests/fixtures/mutation/sql/qualification/01-schema.sql`,
`gate/tests/test_qualify_sql.py`, `tools/tester-unified-gate.sh` (outer SQL phase).

## Step 0 -- PostgreSQL 18.6 measurements (Work step 0)

Measured 2026-09-30 on `postgres:18-alpine@sha256:d3e1620b...65b2` (`select version()`: PostgreSQL 18.6, musl), one container at a
time, `--cgroup-parent=dev-gates.slice`, alongside another project's mutation gate (CD50 shared-host rules). Driver: a throwaway
script in the session scratchpad using the committed harness (`ThrowawayPostgres`, `check_sites`, `derive_bucket`, `capture_witness`).

**First run: BLOCKED (a probe fails unmutated); resolved by CD53, see "Re-run with the CD53 probes" below.** With the committed, hash-pinned probes the unmutated baseline exits 1 with
`ASSAY_SQL_FAILED=K18,K20` (the brief requires exit 0 and 21 passing probes).

**Cause (measured).** PostgreSQL 18.6 reports a violated `ON DELETE RESTRICT` with SQLSTATE `23001` (`restrict_violation`,
`ri_triggers.c:2785`, "violates RESTRICT setting of foreign key constraint"), not `23503` (`foreign_key_violation`); `NO ACTION`
still reports `23503` (`ri_triggers.c:2799`; K19 passes). The probes `K18.sql` and `K20.sql` (the two rows whose constraint is
`RESTRICT`) catch only `foreign_key_violation`, so the error escapes the `DO` block and psql exits 3. Every mutated row also carries
`K18,K20` in its signal, so with the committed probes K02/K25 (expected `survived`) derive no bucket (`test failed without naming
K02`), and the witness capture ends `FAIL COMMAND_FAILED` instead of `MUTANTS_SURVIVED`.

**Diagnostic (scratch copy only; no repo file changed).** In a scratch copy of the fixtures, `K18.sql` and `K20.sql` changed to
`EXCEPTION WHEN foreign_key_violation OR restrict_violation THEN RETURN;` (one token each), and the full driver rerun:

| Measurement | Committed probes | Scratch probes (`OR restrict_violation` in K18, K20) |
|---|---|---|
| Baseline apply / probes | exit 1, `ASSAY_SQL_FAILED=K18,K20` | **exit 0**, dump present, no signal |
| O5 (dump without the restrict key) | dumps differ (control holds) | dumps differ (control holds) |
| 24 rows vs matrix | 21 killed rows derive `killed` (signal also names K18,K20); **K02, K25 raise** (no bucket); K09 `equivalent` (equal dump) | **all 24 equal the matrix**: 21 `killed` (each signal names its own id; K05 also K24, K15 also K19, K16 also K18, K17 also K20), K02 and K25 `survived` (exit 0, dump different), K09 `equivalent` (exit 0, dump equal) |
| O4 residue (re-apply on a database that has the schema) | first 1 (baseline), second exit 3, no dump | first 0, second exit 3, no dump (crashed, as expected) |
| M11 (naive string widen of the integer `IN`) | exit 3, no dump (crashed) | exit 3, no dump (crashed) |
| Witness (real `assay run`) | `FAIL COMMAND_FAILED`; `check_witness_verdict`: "the witness R0 claim is not a single PASS" | `FAIL MUTANTS_SURVIVED`; `check_witness_verdict` **ok** |
| Databases left | `postgres,template0,template1` | `postgres,template0,template1` |
| tmpfs `/var/lib/postgresql` (262144 KiB), `df -Pk` after each apply, after the residue, M11 and the witness | 16% after the baseline; max **22%** (58672 KiB); after the witness 21% | 16%; max **22%** (58672 KiB); after the witness 21% |

The tmpfs bound (max 50%) holds with a wide margin. Containers used: see `W5-LOG.md` (three starts, each removed by exact name by the harness's context manager).

### Re-run with the CD53 probes (2026-09-30, committed fixtures, no scratch copy)

The carver decided QUESTION 1 as CD53: `K18.sql` and `K20.sql` now read `EXCEPTION WHEN foreign_key_violation OR restrict_violation THEN RETURN;`
(nothing else changed); the 21 probes concatenated are 4717 bytes, sha256 `b5c4eb283dc01ec3d8b15f2441363d5bea9312a49e025bd9eaf318086fd41e10`
(computed by the harness's own `_sha256` over the harness's own probe join, from a scratch script). Same driver, same image
(`postgres:18-alpine@sha256:d3e1620b...65b2`, PostgreSQL 18.6), container `run-gate-assay-sql-1569099-1790733241`, one container,
`--cgroup-parent=dev-gates.slice`, alongside another project's mutation gate.

**Result: every bucket equals the matrix; not BLOCKED.**

| Measurement | Expected (matrix / brief) | Measured |
|---|---|---|
| Baseline apply + probes | exit 0, dump present | **exit 0**, dump present, no signal |
| O5 (dump without the restrict key) | dumps differ | dumps differ (control holds) |
| 24 rows vs matrix | 21 `killed`, K02/K25 `survived`, K09 `equivalent` | **all 24 equal the matrix**: 21 `killed` (each signal names its own id; K05 also K24, K15 also K19, K16 also K18, K17 also K20), K02 and K25 `survived` (exit 0, dump different), K09 `equivalent` (exit 0, dump equal) |
| O4 residue (re-apply on a database that has the schema) | first 0 (baseline), second exit non-zero (3), no dump | first 0, second exit 3, no dump |
| M11 (naive string widen of the integer `IN`) | crashed (exit 3, no dump) | exit 3, no dump |
| Witness (real `assay run`) | `FAIL MUTANTS_SURVIVED`; `check_witness_verdict` ok | `FAIL MUTANTS_SURVIVED`; `check_witness_verdict` ok |
| Databases left | `postgres,template0,template1` | `postgres,template0,template1` |
| tmpfs `/var/lib/postgresql` (262144 KiB), peak `df -Pk` (max allowed 50%) | <= 50% | 16% after the baseline; peak **22%** (58672 KiB); after the witness 21% |

## Work 5 -- CLI runs and the committed witness (2026-09-30)

All with `--allow-shared-host`, `--cgroup-parent dev-gates.slice`, `env -u FORCE_COLOR nice -n 19 ionice -c3 python3 -I`.

1. **Witness capture** (`--witness-out gate/python/fixtures/sql/expected/sql-r2-witness.json`): exit 0, stdout `ASSAY_SQL_QUALIFIED=1`
   (the 24 `ASSAY_SQL_ROW=` lines, controls and df go to stderr). The witness has schema_version 13, lane `sql_qualification`, `FAIL
   MUTANTS_SURVIVED`, `judgment.r2.mode = changed_lines`, `source_roots = ["db/schema"]`, no host path or container name inside
   (grep for `run-gate`, `/tmp/`, `/workspaces`, `vscode` = 0).
2. **Row review (all 24, from the witness JSON via a scratch reader; line and operator matched to `matrix.json`):**

| Row | Line | Operator | Bucket | Matrix | Kill signal |
|---|---|---|---|---|---|
| K01 | 9 | drop-not-null | killed | killed | `ASSAY_SQL_FAILED=K01` |
| K02 | 10 | drop-not-null | survived | survived | none |
| K03 | 27 | drop-not-null | killed | killed | `ASSAY_SQL_FAILED=K03` |
| K04 | 5 | drop-not-null | killed | killed | `ASSAY_SQL_FAILED=K04` |
| K05 | 13 | drop-check | killed | killed | `ASSAY_SQL_FAILED=K05,K24` |
| K06 | 11 | drop-check | killed | killed | `ASSAY_SQL_FAILED=K06` |
| K07 | 28 | drop-check | killed | killed | `ASSAY_SQL_FAILED=K07` |
| K08 | 6 | drop-check | killed | killed | `ASSAY_SQL_FAILED=K08` |
| K09 | 44 | drop-check | equivalent | equivalent | none (equal dump) |
| K10 | 14 | drop-unique | killed | killed | `ASSAY_SQL_FAILED=K10` |
| K11 | 23 | drop-unique | killed | killed | `ASSAY_SQL_FAILED=K11` |
| K13 | 30 | drop-unique | killed | killed | `ASSAY_SQL_FAILED=K13` |
| K14 | 31 | drop-unique | killed | killed | `ASSAY_SQL_FAILED=K14` |
| K15 | 19 | drop-foreign-key | killed | killed | `ASSAY_SQL_FAILED=K15,K19` |
| K16 | 24 | drop-foreign-key | killed | killed | `ASSAY_SQL_FAILED=K16,K18` |
| K17 | 38 | drop-foreign-key | killed | killed | `ASSAY_SQL_FAILED=K17,K20` |
| K18 | 25 | weaken-delete-action | killed | killed | `ASSAY_SQL_FAILED=K18` |
| K19 | 19 | weaken-delete-action | killed | killed | `ASSAY_SQL_FAILED=K19` |
| K20 | 38 | weaken-delete-action | killed | killed | `ASSAY_SQL_FAILED=K20` |
| K21 | 57 | drop-trigger | killed | killed | `ASSAY_SQL_FAILED=K21` |
| K22 | 64 | drop-trigger | killed | killed | `ASSAY_SQL_FAILED=K22` |
| K23 | 72 | drop-trigger | killed | killed | `ASSAY_SQL_FAILED=K23` |
| K24 | 13 | widen-check-in | killed | killed | `ASSAY_SQL_FAILED=K24` |
| K25 | 11 | widen-check-in | survived | survived | none |

   21 killed, 2 survived (K02, K25), 1 equivalent (K09), 0 crashed, 0 hung, 0 budget-exceeded, total 24; `candidate_count` 24.
3. **R1 rerun** (same command without `--witness-out`, container `run-gate-assay-sql-1601018-1790733473`): stdout exactly
   `ASSAY_SQL_QUALIFIED=1`, exit 0 (the rerun compares against the committed witness).
4. **R1 break:** a copy of `gate/python/fixtures/sql` in the scratchpad, `tests/K05.sql` overwritten with `SELECT 1;` (Write tool in the copy),
   `--fixture-root <copy>` (container `run-gate-assay-sql-1612472-1790733568`): exit **1**, stdout empty, stderr last line
   `ASSAY_SQL_FAILED: K05: test failed without naming K05`; the run stopped after K04's row (no rows K06 onward, no marker).
5. The three witness tests are re-added to `gate/tests/test_qualify_sql.py`; `gate/tests/test_qualify_sql.py` is 121 passed.

## dstdns classes (Work step 0b, recorded before the corpus was removed)

Corpus = exactly the three files that were in `gate/python/fixtures/dstdns-sql/corpus/` (111 + 8 + 52 = 171 sites; the
`tests/fixtures` copy of the `21-` file was byte-identical, verified with `cmp`, and is not counted). Method per the brief:
`lex_sql(data)`; sites over all lines, seven operators, `limit=500`; key = (operator, description with `widened with <n>` folded
to `widened with <int>`, `body` if the site starts inside a top-level dollar body else `top`, `x`); `x` = `default` (a
`NOT NULL` followed by `DEFAULT`), `named` (a `CHECK` preceded by `CONSTRAINT <name>`), else empty. Exactly 13 classes and
exactly the counts the brief predicts. File abbreviations: `03c` = `03c-create-workflow-core.sql`, `20` =
`20-create-corpora.sql`, `21` = `21-create-workflow-corpus.sql`.

| # | Class key (operator, description, context, x) | Count | Three sites (`file:line`) | Matrix row |
|---|---|---|---|---|
| 1 | `sql:drop-not-null`, `NOT NULL -> NULL`, top, plain | 38 | `03c:28`, `03c:38`, `03c:86` | K01 |
| 2 | `sql:drop-not-null`, `NOT NULL -> NULL`, top, `default` | 36 | `03c:32`, `03c:34`, `03c:39` | K02 |
| 3 | `sql:drop-check`, `CHECK (...) -> CHECK (true)`, top, plain | 28 | `03c:28`, `03c:33`, `03c:35` | K06 |
| 4 | `sql:drop-check`, `CHECK (...) -> CHECK (true)`, top, `named` | 11 | `03c:56`, `03c:60`, `03c:61` | K05 |
| 5 | `sql:widen-check-in`, `IN (...) widened with '__assay_widened__'` (string), top | 20 | `03c:28`, `03c:33`, `03c:35` | K25 |
| 6 | `sql:widen-check-in`, `IN (...) widened with <int>`, top | 2 | `03c:266`, `03c:331` | K24 |
| 7 | `sql:drop-unique`, `UNIQUE -> CHECK (true)`, top | 12 | `03c:120`, `03c:123`, `03c:217` | K11 |
| 8 | `sql:drop-unique`, `CREATE UNIQUE INDEX -> CREATE INDEX` (partial), top | 1 | `03c:372` | K14 |
| 9 | `sql:drop-foreign-key`, `REFERENCES ... -> CHECK (true)`, top | 11 | `03c:86`, `03c:138`, `03c:194` | K15 |
| 10 | `sql:drop-foreign-key`, `FOREIGN KEY (...) REFERENCES ... -> CHECK (true)`, top | 1 | `21:201` | K16 |
| 11 | `sql:drop-foreign-key`, `FOREIGN KEY (...) REFERENCES ... -> CHECK (true)`, body | 3 | `20:41`, `21:327`, `21:332` | K17 |
| 12 | `sql:weaken-delete-action`, `ON DELETE RESTRICT -> ON DELETE CASCADE`, body | 2 | `21:327`, `21:332` | K20 |
| 13 | `sql:drop-trigger`, `CREATE TRIGGER ...; -> SELECT 1;`, top | 6 | `21:84`, `21:109`, `21:173` | K21 |

Total 171 in 13 classes. Rows new to assay's own schema (no dstdns class of their own): **K03** (`SET NOT NULL` via `ALTER`),
**K04** (domain `NOT NULL`), **K07** (`NOT VALID` check), **K08** (domain `CHECK`), **K09** (an inert guard: equivalent),
**K10** (column `UNIQUE` with a bare tag), **K13** (plain `CREATE UNIQUE INDEX`), **K18** (`ON DELETE RESTRICT` at top level),
**K19** (explicit `NO ACTION`), **K22** (constraint trigger), **K23** (`INSTEAD OF` trigger on a view).

## Dedupe rule

A case is kept only if it differs from every kept one in replacement form, span shape (recogniser branch; text after it),
enclosing object, lexical context (top, executed/inert `DO`), catalog effect (A-289) or bucket (only one deliberate survivor
per replacement branch); names, types and trigger timing do not count.
