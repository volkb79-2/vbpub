# W5 report -- self-contained SQL qualification (B126, A-480)

Base: `c8e6b783` (W4 reviewed head `21a7b1f0` merged with the landing branch: W3, W1, W2, W6). Package files:
`gate/python/qualify_sql.py`, `gate/python/fixtures/sql/`, `tests/fixtures/mutation/sql/qualification/01-schema.sql`,
`gate/tests/test_qualify_sql.py`, `tools/tester-unified-gate.sh` (outer SQL phase).

## Step 0 -- PostgreSQL 18.6 measurements (Work step 0)

_Filled in by the Work-0 run below (the container is started only when no `run-gate-*` container is running)._

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
