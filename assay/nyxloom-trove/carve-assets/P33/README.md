# P33 carve assets — carver-owned, implementer must not edit

Frozen by C-sol-1 against main `b03555d79227ef7eb76eaf7f851c2896968fa455`.
Specification: `../../SCHEMA-V5-DESIGN.md`. Decisions: A-220 (shape), A-221
(`go:*` vocabulary), A-222 (locked v4 evidence not rewritten).
Report: `../../reports/assay-P33-JIT-CARVE.md`.

| asset | what it fixes |
|---|---|
| `verdict.schema.v5.json` | **the** new `src/assay/schemas/verdict.schema.json`, installed byte-for-byte |
| `migrate_v4_to_v5.py` | the auditable v4→v5 delta. `--check` must exit 0 against the committed schema before and after implementation, which is what proves the schema was not hand-edited |
| `probe_v5_controlled_red.py` | 13 witnessed pre-implementation expectations. Expectations 3, 4 and 5 must **invert** once implemented |
| `migration-manifest.json` | the ownership boundary: 17 locked carver-owned paths the implementer may not touch, 90 implementer-owned, 7 build artifacts excluded |
| `expected/sql-r2-v5-template.json` | the keystone — an `R0,R2` SQL lane, structurally unrepresentable in v4 |
| `expected/missing-tool-v5-template.json` | P27's missing-tool document at v5. Only `schema_version` changes, which is itself the evidence that V5-1 touches nothing an R0/R1 refusal needs |

Both templates carry `@PLACEHOLDER@` tokens and are **not** directly valid — the
same convention as every P25/P26/P27 `expected/*-template.json`.
`probe_v5_controlled_red.py`'s `SUBS` map is the authoritative substitution list.
The P27 carve shipped an asset whose placeholders were undocumented and whose
recorded validation had silently been run on a substituted copy; that is why this
is stated here and encoded in a runnable probe rather than in prose.

## Witnessed pre-implementation state

```text
PASS  v5 is a legal 2020-12 JSON Schema
PASS  missing-tool-v5-template.json validates against v5
PASS  missing-tool-v5-template.json is REJECTED by the current v4 schema      [1 error]
PASS  missing-tool-v5-template.json is REJECTED by the current raw verifier   [1 failure]
PASS  sql-r2-v5-template.json validates against v5
PASS  sql-r2-v5-template.json is REJECTED by the current v4 schema            [13 errors]
PASS  sql-r2-v5-template.json is REJECTED by the current raw verifier         [1 failure]
PASS  an existing v4 artifact is REJECTED by v5 (breaking, as intended)
PASS  keystone: the SQL lane declares R0,R2 with no r1
PASS  keystone: language/source_roots/base live in judgment.resolved
PASS  keystone: v4's judgment has NO place for these facts without r1
PASS  keystone: no sql: operator is spellable in v4
```

Reproduce with `PYTHONPATH=src python3 nyxloom-trove/carve-assets/P33/probe_v5_controlled_red.py`
from the assay project root. The 13 v4 errors against the SQL template are the
measure of how much of v5 is load-bearing: it is not a version-number bump.

## Asset hashes

```text
47a17e5184ab175938ea431ee467c91bf9631747c389bf73d4de949a75ed69c4  expected/missing-tool-v5-template.json
895c2bd0156a7fec4ee014dfa791c179e59d9d5444f98d9bfb404582d9f880bf  expected/sql-r2-v5-template.json
1844cf8192c844f6567a06e417bc369ae641f22afa7d1c8a313e31b96632ac5d  migrate_v4_to_v5.py
62e3aa40bb973879064ed7137bfbbb421590cf1bf8b8254e771aac762ed66fe4  migration-manifest.json
41e57d3208575fae8dc8c7b2e0794ac805ec62d44861df240f36e01207a70d3f  probe_v5_controlled_red.py
577e576fe2d2642f174ad804fda4a62d09b9447fcc82ba8d077859220d442759  verdict.schema.v5.json
```
