# P34 carve — the source-oriented SQL/DDL adapter

**Carver:** CR-opus-0, 2026-08-18. **Branch:** `assay-P34-sql-adapter` at
`bdc3dc78`. **assay:** v2.0.0 published, verdict schema v6, lane schema v2.

> **Every quantity in this document was measured on this host, at this
> revision, against real PostgreSQL 18.4, real dstdns DDL, and the real
> shipped assay code.** §9 carries every command and its real output. Nothing
> below is inherited from `assay-P34-CARVE-SCOPE.md`'s prose without being
> re-taken; where the scope or B001 turned out to be wrong, §4 and §8 say so.

**Three premises handed to this carve are corrected here, loudly:**

1. **B001's central operator has no site in real DDL under the obvious rule.**
   Both real `ON DELETE RESTRICT` foreign keys in dstdns's entire init-script
   corpus live inside a `DO $$ ... $$` block. A lexer that treats a
   dollar-quoted body as the string literal it lexically is finds **zero**
   `sql:weaken-delete-action` sites — for the operator that motivated the
   whole feature (§9 M3, M4).
2. **`carve-assets/W1/expected/sql-r2-v6-template.json` presumes an external
   helper** (`"tool": "assay-sql-sites"`, `/opt/assay-helpers/bin/...`). It is
   a *contract-grammar* fixture, never a witness of assay's output, so this
   carve's stdlib route does not falsify it — but it must not be read as the
   intended design, and §6/W9 says what an actual witness looks like (§9 M18).
3. **A-243's ruled widening is not implemented at `bdc3dc78`** — the model
   still refuses a `helpers` entry beside `MUTATION_DISCOVERY_FAILED` (§9 M13).
   And under this carve's route, SQL invokes no helper at all, so P34 has no
   real run in which to witness the widening. §4.6 rules it and reassigns it.

---

## 1. The problem, in consumer terms

A project owns a directory of `.sql` files that build its database. Someone
changes one. What can the project's test suite prove about that change today?

At dstdns `151cda0d6fca018c31e781673c19b4bad41179a8` (pinned, §9 M17) the
answer is: almost nothing, and the shape of the "almost nothing" is the point.
`libs/common/tests/test_backend_foundations.py:117-129` asserts schema
properties by string containment over the file's own text:

```python
content = (MIGRATION_DIR / "20-create-corpora.sql").read_text()
assert "CREATE TABLE IF NOT EXISTS corpora" in content
```

Injecting `THIS IS NOT SQL AT ALL;` as line 2 of that exact file leaves the
assertion `True`, while real PostgreSQL refuses the file outright
(`ERROR: syntax error at or near "THIS"`, exit 3). Measured, not quoted —
§9 M16. B001 asserted this in prose; it is now a fact at a pinned revision.

The consequence is that a schema change — among the most irreversible changes
a project makes — is the *least* verified one it makes. Three real defects
from one week at dstdns, all in reviewed and frozen contracts:

* A `BEFORE DELETE` trigger added to protect one invariant also fired on FK
  cascade, making an entire class of row permanently undeletable, and silently
  turned the table's own `ON DELETE CASCADE` into dead DDL. Two document
  reviews and three hand-probes missed it; an independent auditor found it only
  by running a delete.
* A test asserting `pytest.raises(RestrictViolationError)` **passed against a
  schema with the `ON DELETE RESTRICT` removed entirely**, because a trigger on
  the same table raises the same SQLSTATE. "It raised" is not evidence of
  *which* mechanism refused.
* Numbered init scripts applied with a bare glob and no pinned collation, so
  under glibc `03a-…` sorts before `03-…` and fresh init dies. Production
  survived only because the base image is Alpine, whose BusyBox shell sorts
  bytewise — an ambient property nothing stated or tested.

The first two are the same defect: **nothing removes a constraint and checks
whether the suite notices.** The third is not (see §8.1).

What a consumer wants is therefore small and specific: *take the constraints I
just changed, remove them one at a time, run my own schema tests, and tell me
which removals my suite failed to notice.* Nothing in that sentence asks assay
to connect to a database, know a DSN, provision a container, or understand a
query plan — and nothing in this package does any of those.

---

## 2. The property this claims, and what it does not give

**The property, in one sentence that is exactly true:**

> For each mutant it reports, assay proves that exactly one byte span of one
> tracked, changed DDL file — a span located outside every comment, string
> literal and quoted identifier at both the outer and the dollar-quoted lexical
> level — was replaced by a recorded replacement, and it classifies that mutant
> using only the project-declared command's exit status and the bytes of the
> two files the lane declared that command would write.

That is the whole claim. Every word of it is mechanical: assay knows the span
because it located it, the replacement because it constructed it, the exit
status because it ran the command, and the artifact bytes because it read them
through `safeio`'s single-use reservation. Nothing in it requires assay to
understand SQL semantics, and nothing in it is true of a database.

**What it does NOT give, enumerated:**

1. **It does not prove a mutant is valid DDL.** Measured: widening an integer
   `IN`-list with a string literal produces DDL PostgreSQL refuses
   (`invalid input syntax for type integer`, exit 3 — §9 M11). Nothing in assay
   can tell that from a mutant the tests killed. §3.6's classification rule is
   what keeps such a mutant out of `killed`, and it depends on a **consumer
   obligation** (§3.4) that assay cannot itself verify. This is the largest
   soft spot in the design and §8.2 states it as such.
2. **It does not prove the operator name matches what changed in the
   database.** Measured: `sql:drop-check` and `sql:widen-check-in` produce an
   *empty* delta over `pg_constraint`'s constraint names — the row survives
   with the same `conname` and `contype`, only `conbin` changes (§9 M6). A
   consumer diffing catalog names sees nothing; only a schema dump sees it.
3. **It does not prove each mutant was judged against an isolated database.**
   Measured: applying a mutant to a database that already carries the
   un-mutated schema leaves `is_nullable = 'NO'` — the mutation never happened,
   exit 0, no warning (§9 M9). The equivalence artifact converts that case into
   `equivalent` rather than `survived` (§9 M10), which is a **refusal to
   claim**, not a proof of isolation.
4. **It does not verify a kill's cause.** With `kill_attribution = "declared"`
   assay records, verbatim, whatever string the command wrote into
   `kill_signal_artifact`. It does not check that string against the mutation.
   That is B001's own use case 2 made *visible*, not *closed*.
5. **It is not a whole-schema audit.** Sites come only from changed lines in
   tracked files under the declared `source_roots`. `max_mutants` bounds it
   further, and `MUTANT_LIMIT_EXCEEDED` is reachable on a real corpus (§8.4).
6. **It gives no R1 and no R3 for SQL.** DDL has no coverage tool; A-192
   forbids R3 without R1. SQL lanes are `R0,R2`. This is settled, not reopened.
7. **It never reads a database catalog, never opens a connection, never sees a
   DSN, and never provisions anything.** A-215 and DESIGN-GUIDE §7.

**The A-242 sentence, owed by that ruling.** `GoAdapter.statement_spans`
returning `None` is one of the protocol's two documented legal return shapes —
Go declares `requires_span_attribution = False` because Go's classification is
already line-granular, so `None` is *the answer to a question Go was asked and
correctly has nothing to add to*. `SqlAdapter.statement_spans` **raises**
because SQL is never evaluated at R1 at all — its `RegistryEntry.rigor` is
`{"R2"}`, so the call is unreachable through the CLI, and returning `None`
would be an answer to a question SQL was never asked. One is the contract; the
other is the absence of one.

---

## 3. The design

### 3.1 Route: a stdlib-only two-level bounded DDL lexer inside assay

**Chosen: route (i).** §4.1 argues it against route (ii); this section
specifies it and §9 M1–M8 is its span-fidelity evidence.

The adapter needs a *lexer*, not a parser. The seven operators are keyword
patterns plus parenthesis matching; what makes them hard is not grammar, it is
knowing which bytes are code. Real DDL is dense with the traps: dstdns's 37
init scripts, 316,077 bytes, carry 4,007 `--` sequences, 4,588 single quotes,
422 double quotes and 64 dollar-quote delimiters (§9 M2).

**Phase 1 — the mask.** Walk the source bytes once, classifying every byte as
code or not-code, and emit a mask of identical length in which every non-code
byte becomes `0x20` (newlines preserved, so line arithmetic still works and
mask offsets *are* source offsets). The lexer recognises exactly six
non-code constructs:

| construct | rule |
|---|---|
| `-- …` line comment | to the next `\n` or EOF |
| `/* … */` block comment | **nests** (PostgreSQL, unlike SQL-92) |
| `'…'` string literal | `''` is an embedded quote; a backslash is **not** an escape (measured: `standard_conforming_strings = on`, §9 M22) |
| `"…"` quoted identifier | `""` is an embedded quote |
| `$tag$…$tag$` dollar quote | tag is `$`, or `$` + ident + `$`; body ends at the identical tag |
| `E'…'` prefixed string | same terminator scan **plus** backslash escapes: `\'` does not terminate (measured, §9 M22) |

`U&'…'` is lexed as a plain `'…'` (its `UESCAPE` form affects decoding, not
termination). Neither `E'` nor `U&'` occurs in the real corpus (§9 M22), so the
`E'` rule is carried because PostgreSQL has it, not because a consumer hit it —
and its test is a constructed fixture, stated as such.

**Phase 2 — one level of recursion.** A dollar-quoted body is lexically a
string literal, but its content is a program PostgreSQL executes, and it is
where real projects put their idempotent DDL. Measured: 18.2% of dstdns's
init-script bytes sit inside a dollar-quoted body, and treating those bodies as
opaque loses 22 real operator sites — **including both of the corpus's only two
`ON DELETE RESTRICT` foreign keys** (§9 M3, M4). So after the outer mask is
built, each dollar-quoted body is lexed *by the same phase-1 routine* and its
result spliced back into the mask. Exactly one level: a body inside a body is
not recursed into, and §8.3 records that limit.

**Phase 3 — fail closed.** An unterminated string, dollar quote, or block
comment raises `MutationDiscoveryError` (`ERROR`/`MUTATION_DISCOVERY_FAILED`),
the SQL analogue of `PythonAdapter`'s `SyntaxError` path. This is not
decoration: the prototype without it silently produced sites for the valid
prefix of all three malformed inputs while real PostgreSQL refused every one
(§9 M14). Swallowing an unterminated `'` silently masks out the entire rest of
the file, which is a measurement gap that looks like a clean run.

**Phase 4 — bounded retention.** `generate_mutation_sites` receives the
remaining capacity and must retain at most `limit` descriptors *while walking*
(A-180). The implementation walks the mask once per operator pattern in
`MutationSite.identity` order and stops appending at `limit`; it never
materialises a full candidate list and slices. The reference prototype in
`/tmp/p34-scratch/mutate.py` collects-then-sorts and is **not** the shipping
shape.

### 3.2 The seven operators: span, replacement, and its measured validity

Every replacement below was applied to a real PostgreSQL 18.4 database. 21
mutant applications, 21 clean applies, and every catalog delta matched the
operator's claim (§9 M5, M6, M15).

| operator | span located | replacement | measured catalog effect |
|---|---|---|---|
| `sql:drop-check` | `CHECK` through its matching `)` | `CHECK (true)` | row survives, `conbin` changes; **no name delta** |
| `sql:drop-unique` | `UNIQUE` + optional `( … )` | `CHECK (true)` | `contype` `u` → `c` |
| `sql:drop-unique` (index form) | `CREATE UNIQUE INDEX` | `CREATE INDEX` | index loses uniqueness |
| `sql:drop-not-null` | `NOT NULL` | `NULL` | `contype='n'` row disappears |
| `sql:drop-not-null` (alter form) | `SET NOT NULL` | `DROP NOT NULL` | as above |
| `sql:drop-foreign-key` | `FOREIGN KEY ( … ) REFERENCES … [ON DELETE/UPDATE …]`, or a bare column-level `REFERENCES …` clause | `CHECK (true)` | `contype` `f` → `c` |
| `sql:weaken-delete-action` | the action word after `ON DELETE` (`RESTRICT` / `NO ACTION`) | `CASCADE` | `confdeltype` `r` → `c`, `contype` stays `f` |
| `sql:drop-trigger` | `CREATE [OR REPLACE] [CONSTRAINT] TRIGGER` through its own terminating `;` | `SELECT 1;` | `pg_trigger` row disappears |
| `sql:widen-check-in` | the closing `)` of an `IN ( … )` inside a `CHECK` | `, <extra literal>)` | `conbin` gains a member |

Three of those rows are context rules that exist **because a measurement
refused the naive form**, and an implementer must not simplify them away:

* **`NOT NULL` is not always a constraint.** 35 of the 267 code-context
  `NOT NULL` occurrences in the real corpus (13.1%) are `IS NOT NULL`
  *predicates* inside `CHECK` expressions. Mutating one to `NULL` inverts a
  predicate — the mutant applies cleanly (§9 M12), so it silently becomes
  evidence in an artifact that labels it `sql:drop-not-null` at a span that is
  not a `NOT NULL` constraint. **Rule: no site where `NOT NULL` is immediately
  preceded by `IS`.**
* **`SET NOT NULL` → `SET NULL` is a syntax error** (measured, §9 M15A).
  **Rule: where `NOT NULL` is immediately preceded by `SET`, the span is
  `SET NOT NULL` and the replacement is `DROP NOT NULL`.**
* **`sql:widen-check-in` must be literal-shape-aware.** Measured: widening
  `CHECK (envelope_version IN (1))` with `'__assay_widened__'` produces DDL
  PostgreSQL refuses (§9 M11); widening it with a numeric literal applies
  cleanly (§9 M11). **Rule:** inspect the existing `IN`-list members. All
  single-quoted string literals ⇒ widen with a string literal not already in
  the list. All unsigned integer literals ⇒ widen with `max(members) + 1`.
  Anything else — mixed shapes, casts (`'a'::mystatus`), column references,
  function calls, a subquery — ⇒ **emit no site**. An honest gap beats a
  crashed mutant.

And one deliberate scope reduction, also measured: **`sql:drop-trigger` emits
no site inside a dollar-quoted body.** `SELECT 1;` is valid at the outer level
but invalid inside a plpgsql body (`query has no destination for result data`,
§9 M15B). `CREATE TRIGGER` inside a `DO $$` block is a real, legal shape (§9
M15C), but all 6 `CREATE TRIGGER` occurrences in the real corpus are at outer
level (§9 M4), so the exclusion costs nothing measured and removes the whole
class rather than teaching the lexer which dollar bodies are plpgsql.

**Replacement invariants**, checked in the adapter before a site is returned:
the replacement contains no `$`, no `'`, no `"`, no `--`, no `/*`, and no `;`
except the trigger form's own. A replacement carrying a delimiter could
terminate the construct it sits inside, which is how a byte splice turns into a
different file.

### 3.3 Adapter surface

New `src/assay/adapters/sql.py`, implementing the frozen seven-method
`LanguageAdapter` (A-242 — the flat protocol stays).

```python
name = "sql"
source_globs = ("*.sql",)
excluded_dir_names = frozenset({"node_modules", "vendor", ".venv"})
requires_span_attribution = False
external_tools = ()                      # route (i): nothing to shell out to
```

* `is_test_path(rel_path)` — **implemented.** `True` for a path with a
  `tests/` or `test/` segment, or a basename matching `test_*.sql` /
  `*_test.sql`. Consulted by `resolve_mutation_targets`.
* `generate_mutation_sites(...)` — **implemented**, §3.1/§3.2.
* `has_executable_code`, `normalize_coverage_key`, `statement_spans`,
  `inject_import_break`, `inject_uncovered_line` — **raise**
  `NotImplementedError` with a message naming SQL's `R0,R2`-only reachability,
  per A-242 and `verify.py`'s `_BASELINE_NEVER_READ` precedent. Each gets a
  direct test asserting the raise (see the `ciu` Protocol-stub trap: a bare
  `...` body is auto-excluded by coverage.py and reads as a pragma dodge).

**Registry.** `cli._built_in_registry` gains
`RegistryEntry(adapter=SqlAdapter(), rigor=frozenset({"R2"}))`. `R2` only —
that single fact is what makes the five raising methods provably unreachable
through the CLI before any adapter method is called.

### 3.4 Config surface — exact TOML keys

A complete, pasteable SQL lane. Everything here already exists except the two
`judge.mutation` keys this package unreserves.

```toml
schema_version = 2

[lanes.schema]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["scripts/schema-gate.sh"]
env = {}
env_passthrough = ["PATH"]
budget = "20m"
allow_argv_append = false

[lanes.schema.isolation]
snapshot_selection = "repository"

[lanes.schema.judge]
language = "sql"
source_roots = ["infra/db-init/init-scripts"]
base = "origin/main"

[lanes.schema.judge.mutation]
jobs = 1
max_mutants = 200
operators = [
  "sql:drop-check", "sql:drop-unique", "sql:drop-not-null",
  "sql:drop-foreign-key", "sql:weaken-delete-action",
  "sql:drop-trigger", "sql:widen-check-in",
]
equivalence_artifact = ".assay/schema-dump.sql"   # REQUIRED on a sql lane
kill_signal_artifact = ".assay/kill-signal.txt"   # optional
```

**The two unreserved keys, and the refusal that must survive.** A-227/A-230b
refuse both at load with a `LaneConfigError` naming P34. P34 lifts that refusal
**only** when the lane's resolved `judge.language` is `"sql"`. A Python or Go
lane declaring either still gets the identical refusal — A-227's "the refusal
for every other language must survive" is a test, not a note, and the message
must change from "RESERVED for P34" to a language-scoped one (it currently also
says "the v5 verdict contract", stale at v6 — §9 M20).

**`equivalence_artifact` is REQUIRED on a SQL R2 lane.** Absent ⇒
`ERROR`/`BAD_LANE_CONFIG` at load. This is the single most consequential
config decision in the carve and it is measured, not stylistic: without it,
§2's limitation 3 becomes an *inverted claim* rather than a refusal to claim.
A mutant that never actually mutated — because a previous run's residue is
still in the database — exits 0 and is recorded `survived`, i.e. assay states
"no test asserts this constraint" about a constraint that was never removed
(§9 M9). With the declaration, the identical run compares byte-identical
artifacts and records `equivalent` (§9 M10). §4.3 argues this against making it
optional.

**Both artifact paths** follow `judge.coverage.artifact`'s existing grammar
exactly, and reuse its validator: project-relative, never absolute, resolving
beneath the project root (no `..`, no symlink escape), existence not checked at
load. And, exactly like the coverage artifact, an artifact **tracked by git
inside the prepared snapshot** is refused `ERROR`/`BAD_LANE_CONFIG`:
measurement output must not be committed.

**Consumer obligations, which assay cannot verify and CONSUMERS.md must
state.** These are the trust boundary named in §2.1 and §8.2:

1. The command writes `equivalence_artifact` **only after the schema has been
   fully and successfully applied** — the canonical shape is
   `apply && test && dump`, never `dump || true`.
2. The dump must be **byte-reproducible across two invocations against an
   unchanged database.** `pg_dump` 18.4 is not, by default: it emits
   `\restrict <random>` / `\unrestrict <random>` lines that differ on every
   invocation, so two dumps of the *same* database have different SHA-256s
   (§9 M7). `--restrict-key=<fixed>` makes them identical (§9 M8). Without
   this, every mutant is unequal to baseline, the `equivalent` bucket is
   permanently empty, `ALL_MUTANTS_EQUIVALENT` is unreachable, and **nothing
   goes red** — the estate's own most expensive defect class, one tool over.
3. Both artifacts must be gitignored (or otherwise untracked), or the mutant
   leaves the snapshot dirty and the claim becomes
   `NO_MEASUREMENT`/`DIRTY_TREE`.

### 3.5 The external-tool preflight (A-253), and its non-vacuous witness

A-253 assigns the effective-`PATH` preflight to P34 unconditionally. P34 builds
it, and the honest form is:

```
for tool in adapter.external_tools:
    if shutil.which(tool) is None:
        raise AssayError(..., NO_MEASUREMENT, MISSING_EXTERNAL_TOOL)
```

placed in `runner.run_lane` immediately after `get_adapter` resolves the
adapter and **before** any snapshot, command or Git work — the same position
`_check_coverage_artifact_containment` occupies.

**The trap A-253's own wording walks into.** SQL declares
`external_tools = ()`, so that loop body never executes. A green test over an
adapter with an empty tuple is A-278's empty-list gate verbatim: a check with
nothing to check is not a passing check. So the preflight ships with **three**
tests, not one:

* a test adapter declaring `external_tools = ("assay-nonexistent-tool-9f3c2",)`
  registered into a real `Registry` and driven through the real `assay run`,
  asserting `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` **and** that the lane's
  command never ran;
* the paired must-succeed control: the same adapter with
  `external_tools = ("sh",)` reaches the command;
* the empty-tuple control, asserting the loop is entered zero times **and**
  asserting the tuple it was handed was empty — the audit states its own
  subject, so "it passed" cannot mean "it iterated nothing unnoticed".

### 3.6 Mutant classification — the one core widening this package makes

`mutation._classify_mutant_result` today reads exit status alone:
`PASS` ⇒ survived, `FAIL` ⇒ killed, `BUDGET_EXCEEDED` ⇒ its own bucket,
`ERROR` ⇒ crashed. That mapping cannot distinguish "the tests caught the
mutation" from "the DDL never applied", and §9 M11 shows the latter is
reachable and exits non-zero.

When — and only when — the lane declares `equivalence_artifact`, the
classification becomes a function of `(outcome, artifact-present, artifact-bytes)`:

| exit outcome | equivalence artifact | bucket | why |
|---|---|---|---|
| `PASS` | absent | `crashed` | the lane declared an artifact its command did not write; nothing was measured |
| `PASS` | present, **≠** baseline | `survived` | the mutated schema was built and the suite did not notice |
| `PASS` | present, **=** baseline | `equivalent` | the mutant provably changed nothing |
| `FAIL` | present, ≠ baseline | `killed` | the mutated schema was built, and something refused it |
| `FAIL` | present, = baseline | `equivalent` | it never mutated (residue / a never-firing guard); the failure is about something else |
| `FAIL` | absent | `crashed` | the schema never got built — an invalid mutant, **not a kill** |
| `ERROR` | any | `crashed` | unchanged |
| `BUDGET_EXCEEDED` | any | `budget_exceeded` | unchanged |

Two properties of this table matter more than its rows. **It contains zero SQL
knowledge** — exit status, file presence, byte equality, nothing else — so it
stays in the language-free core where `_classify_mutant_result` already lives.
And **it is inert for every existing lane**: with no `equivalence_artifact`
declared, the current mapping applies unchanged, so no Python lane's verdict
moves. That inertness needs its own test, not an assurance.

**Kill signals.** When `kill_signal_artifact` is declared,
`kill_attribution` derives to `"declared"` (A-223b), and the model then
requires a `kill_signal` on **every** killed entry (measured, §9 M19). So a
mutant that lands in `killed` with no signal file is reclassified `crashed`:
the lane's own declared contract was not met for that mutant, and calling it a
kill would record an attribution that does not exist. The signal string is read
through the same single-use reservation, bounded, decoded as UTF-8, stripped,
and recorded **verbatim** — assay never parses or interprets it.

**Where the baseline's bytes come from.** `runner.run_lane` holds
`baseline_snapshot` open across R1 evaluation and closes it before R2 begins.
The baseline's `equivalence_artifact` must therefore be reserved and consumed
*inside* that block — exactly where `evaluate_r1` already consumes the coverage
artifact — and threaded into `run_mutation` as a new
`baseline_equivalence: bytes | None` parameter. A baseline that declared an
artifact and did not write it is `NO_MEASUREMENT`/`EMPTY_COVERAGE`… no: it is
`ERROR`/`BAD_LANE_CONFIG`'s sibling case and gets `ERROR`/`EXEC_FAILED`, with
no mutant attempted. Never a comparison against `None`, which would make every
mutant unequal and hide the fault.

**Why stale bytes cannot leak.** `safeio.reserve_output(...).arm()` unlinks any
pre-existing regular file before the command runs, and `consume()` returns
`None` for a missing output. Each mutant runs in its own fresh replacement
snapshot materialised from the committed seed, and the artifacts are untracked,
so they are absent at the start of every mutant. This is existing, proven
machinery; P34 adds call sites, not mechanism.

### 3.7 The refusal set, with the exact reason code for each

| condition | outcome / reason code | layer |
|---|---|---|
| `judge.language = "sql"` on a build without the adapter registered | `ERROR` / `BAD_LANE_CONFIG` | `registry.get_adapter` |
| SQL lane declaring `R1` or `R3` | `ERROR` / `BAD_LANE_CONFIG` | `registry.get_adapter` (entry rigor is `{"R2"}`) |
| SQL R2 lane with no `judge.mutation.equivalence_artifact` | `ERROR` / `BAD_LANE_CONFIG` | `config._load_mutation` |
| non-SQL lane declaring `equivalence_artifact` or `kill_signal_artifact` | `ERROR` / `BAD_LANE_CONFIG` (`LaneConfigError`) | `config._load_mutation` |
| either artifact path absolute, escaping the project root, or empty | `ERROR` / `BAD_LANE_CONFIG` | `config._load_mutation` |
| either artifact tracked by git inside the prepared snapshot | `ERROR` / `BAD_LANE_CONFIG` | `runner._execute_snapshot_unit` |
| a declared `external_tools` entry absent from the effective PATH | `NO_MEASUREMENT` / `MISSING_EXTERNAL_TOOL` | `runner.run_lane` preflight (§3.5) |
| unterminated string / dollar quote / block comment in a target file | `ERROR` / `MUTATION_DISCOVERY_FAILED` | `SqlAdapter` → `MutationDiscoveryError` |
| adapter returns more sites than the remaining capacity, an out-of-policy operator, a mis-recorded `lineno`, an unordered or duplicate batch, a span splitting a UTF-8 character, or a no-op splice | `ERROR` / `MUTATION_DISCOVERY_FAILED` | `mutation._validate_sites` (existing) |
| baseline declared `equivalence_artifact` and did not write it | `ERROR` / `EXEC_FAILED` | `runner.run_lane`, before any mutant |
| candidate discovery reaches `max_mutants + 1` | `BUDGET_EXCEEDED` / `MUTANT_LIMIT_EXCEEDED` | `mutation.run_mutation` (existing) |
| a mutant left the snapshot dirty or moved HEAD | `NO_MEASUREMENT` / `DIRTY_TREE` or `HEAD_CHANGED` | `mutation._snapshot_left_dirt` (existing) |
| any mutant classified `crashed` | `ERROR` / `EXEC_FAILED` | `mutation.judge_mutation` (existing) |
| any mutant `survived` | `FAIL` / `MUTANTS_SURVIVED` | `mutation.judge_mutation` (existing) |
| every attempted mutant `equivalent` | `INCONCLUSIVE` / `ALL_MUTANTS_EQUIVALENT` | `mutation.judge_mutation` (existing) |
| a supported analysis found no site | `INCONCLUSIVE` / `NO_MUTANTS` | `mutation.judge_mutation` (existing) |

`MUTATION_UNSUPPORTED` is deliberately **unreachable** for SQL: the adapter has
an engine, so — like Python — its failures are failures, not absence. It never
returns the `"UNSUPPORTED"` marker.

---

## 4. Why this shape, and not the alternatives

### 4.1 Why the stdlib lexer and not an external helper

Four measurements decide it, none of them aesthetic.

**(a) There is no tool to declare.** No SQL parser or linter exists on this
host — `sqlfluff`, `sqlglot`, `pgsanity`, `pg_format`, `sqlparse` all absent,
and neither `sqlparse` nor `sqlglot` imports (§9 M1). More decisively, none
exists inside `tester-unified:local`, the image assay's own gate runs in (§9
M1). Route (ii) therefore begins with "add a dependency to the shared test
image", which A-O02 already records as re-risking ciu/cmru/topos/nyxloom's
gates. That is a large cost paid before a single span is located.

**(b) The engineering question the scope asked — which route produces honest
byte spans — has a measured answer, and it is "either, if it lexes".** The
naive form of route (i), a bare keyword regex over the file bytes, produces
**68 phantom matches out of 512** across the real corpus (13.3%) — matches
inside comments, string literals and dollar-quoted bodies. Worst of all, all 3
of its `ON DELETE RESTRICT` matches are phantoms (§9 M3). But the fix is not a
parser; it is a mask. With the two-level mask, tier C reports 466 sites, of
which every one is in code (§9 M4). A grammar would buy nothing further for
these seven operators, because none of them needs to know what *kind* of
statement it is in.

**(c) The spans it produces are honest, measured against the only oracle that
counts.** All 12 mutants the prototype generated over a trap-laden fixture, and
9 further single-shape mutants, applied cleanly to real PostgreSQL 18.4, and
every catalog delta was exactly what the operator claims — `contype` `u`→`c`,
`f`→`c`, `confdeltype` `r`→`c`, the `n` row gone, the `pg_trigger` row gone
(§9 M5, M6, M15). Zero of the 21 produced invalid DDL. A helper would have to
beat that, and could not: the failure modes measured in §3.2 (`IS NOT NULL`,
`SET NOT NULL`, integer `IN`-lists) are *semantic* traps a general SQL parser
would hand back just as faithfully.

**(d) A helper would have to be written anyway.** No off-the-shelf tool emits
`(start_byte, end_byte, replacement, operator)` for these seven operators. The
helper route is "write the same lexer, then also ship it, version it, resolve
it on PATH, pin its identity, and add a subprocess boundary and its failure
modes". A-005's zero-runtime-dependency claim is what makes assay's standalone
story near-unfalsifiable, and this is not the feature worth spending it on.

**The reachability half is closed either way** (A-253), so §3.5's preflight
ships regardless — with a real witness, not an empty tuple.

### 4.2 Why the fallback (an external PostgreSQL mutation auditor) is not taken

B001 names it as the fallback if source mutation cannot truthfully represent
the deployed schema. §9 M5/M6 is the evidence that it can: for every one of the
seven operators, the source mutation produced exactly the deployed-schema
change the operator names. The fallback stays available and un-argued-against
for a consumer whose DDL is generated rather than tracked (§8.5).

### 4.3 Why `equivalence_artifact` is required rather than optional

The tempting shape is "optional, like everything else; a consumer who wants
equivalence detection opts in". Rejected, because the two failure modes are not
symmetric.

Without it, the residue case (§9 M9) — the most likely way a real consumer gets
isolation wrong — surfaces as `survived`, and `survived` is an *assertion about
the consumer's test suite* that is false. assay would tell dstdns "no test
asserts `runs.corpus_version_id` is required" about a run in which the column
was never made nullable. That is worse than a missing feature; it is the class
of false statement this whole project exists to remove.

With it, the identical run is `equivalent`, and if every mutant lands there the
claim is `INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT` — a loud, non-green terminal
that says "this run proves nothing about your tests". A consumer whose lane is
misconfigured gets told so. That asymmetry is what makes it a requirement.

The cost is real and stated: a SQL consumer must produce a byte-reproducible
schema dump, and §3.4's obligation 2 is a genuine burden with a measured
gotcha. §8.2 records that this moves trust into a document.

### 4.4 What is dropped from B001's sketch, and why

1. **Catalog introspection as the mutation-target source** (`pg_constraint` /
   `pg_trigger` / `pg_index` rows as targets). Already excluded by A-215, and
   now with a measurement behind it: a live catalog row does not identify a
   byte span, and the catalog cannot even *see* two of the seven operators —
   `sql:drop-check` and `sql:widen-check-in` produce an empty delta over
   constraint names and types (§9 M6).
2. **"Every kill signal is a `constraint_name` PostgreSQL hands back on
   violation."** Dropped as an assay claim. assay never observes a violation.
   Reduced to: the command writes a string, assay records it verbatim, and
   §2.4 says so.
3. **The canary mapping** — `inject_import_break` → "make the script fail to
   apply", `inject_uncovered_line` → "drop one constraint". Dropped: SQL lanes
   are `R0,R2`, and A-242 makes both methods raise.
4. **`statement_spans` → "statement boundaries in a `.sql` file".** Dropped for
   the same reason; there is no R1 to attribute lines for.
5. **"The mutation set can be *complete* rather than sampled."** Dropped as
   stated, kept as scoped. Completeness is over *the selected operators on the
   changed tracked DDL assay was asked to judge* — B001's own owner disposition
   already narrows it this way, and the corpus measurement makes the narrowing
   concrete: 466 code-context operator matches across 37 files, so a
   whole-corpus run would blow past any sane `max_mutants` (§8.4).
6. **Literal deletion as the `drop-*` mechanism.** Replaced by neutralisation
   (`CHECK (true)`, `NULL`, `CREATE INDEX`, `SELECT 1;`). `MutationSite`
   requires a non-empty replacement, and literal deletion would additionally
   have to reason about trailing commas in a constraint list — grammar work for
   no gain.
7. **Ordering / idempotency conformance** ("applies from scratch, in a
   deterministic order, twice"). B001 itself calls this
   distribution-shaped R0 artifact conformance rather than mutation, and it is
   correct. Out of scope, §8.1.

### 4.5 The A-252 question, asked explicitly rather than assumed

A-252 says in-place tightening of a shipped schema version is legal only with a
differential sweep, that A-236b was the first such tightening of v5 and A-251
the second, and that **a third needs an explicit ruling, not that precedent.**
v6 has since shipped for unrelated reasons.

**Does the spent-precedent rule survive a version bump?** This carve does not
need the answer and deliberately does not assume one, because **P34 requires no
tightening of v6 at all** (§5.2). The question is recorded here so that
whichever package first wants to tighten v6 in place must obtain the ruling
rather than reading "it's a new version, the counter reset" into A-252's
silence — and equally must not read "the precedent is spent forever" into it.
It is an open question, and it is not P34's to close.

### 4.6 A-243's still-open sub-case, ruled — and reassigned

A-243 left open: *a helper that ran successfully on a run that then failed for
an unrelated reason (a deadline, say)*, and assigned it to P34's carve
"looking at real SQL runs, not a blind ruling now".

Two measurements settle it, and they point away from P34:

* **A-243's already-ruled widening is not implemented.** At `bdc3dc78` the
  model refuses a `mutation-sites` helper beside `MUTATION_DISCOVERY_FAILED`,
  and beside `LANE_TIMEOUT`, `MISSING_EXTERNAL_TOOL` and
  `SNAPSHOT_LIMIT_EXCEEDED` too (§9 M13). The ruling exists; the code does not.
* **Under route (i), SQL invokes no helper, ever.** `external_tools = ()`, no
  subprocess boundary, no `resolved_path`, no self-reported identity. There is
  no real SQL run in which a helper both ran and a terminal was payload-free.

**Ruling: P34 does not implement the widening, and does not rule the
sub-case.** Implementing either would ship a permission no producer can
exercise, whose only test would have to hand-build the artifact it then
asserts about — wave 1 lesson 5, and A-087's own "adding its reason would be
decorative" one code over. **The widening and its open sub-case are reassigned
to P29**, the first package with a real helper invocation, a real
`resolved_path`, and a real deadline to hit.

**This reverses an assignment a decision made, so it is flagged for the
controller rather than taken silently.** If the controller prefers A-243
landed on schedule, the honest way is to land it *with* P29's producer, not
with P34's absence of one. Contrast A-253, which is **not** reversed: it rules
its own mechanism unconditional and says so explicitly, and §3.5 gives it a
non-vacuous witness rather than the empty-tuple control alone.

---

## 5. What it records in the verdict

### 5.1 Field by field, with the exact producing call site

| verdict field | value | producing call site |
|---|---|---|
| `judgment.resolved.language` | `"sql"` | `runner._build_judgment_resolved`, from the lane |
| `judgment.resolved.source_roots` | declared roots | unchanged |
| `judgment.resolved.base` | resolved base OID | unchanged |
| `judgment.r2.jobs` / `.max_mutants` / `.operators` | declared policy | `runner._build_judgment_r2` |
| `judgment.r2.kill_attribution` | `"declared"` iff `kill_signal_artifact` present, else `"unattributed"` | `runner._build_judgment_r2` (already derives this at `runner.py:1888`) |
| `judgment.r2.kill_signal_artifact` | declared path | `runner._build_judgment_r2` (already reads it at `runner.py:1882`) |
| `judgment.r2.equivalence_artifact` | declared path | `runner._build_judgment_r2` (already reads it at `runner.py:1891`) |
| `claims[R2].mutation.candidate_count` / `.total` | bounded observation / attempted | `mutation.run_mutation` |
| `…mutation.killed[]` | `MutantOutcome` per killed site | `mutation._outcome_of`, bucket from §3.6 |
| `…mutation.killed[].kill_signal` | the command's own string, verbatim | **new** `mutation._read_kill_signal` via `safeio` reservation |
| `…mutation.survived[]` / `.crashed[]` / `.budget_exceeded[]` | as today | `mutation._outcome_of` |
| `…mutation.equivalent[]` | mutants whose artifact bytes equalled baseline's | **new**, in `mutation.run_mutation` |
| `…[].path` / `.lineno` / `.start_byte` / `.end_byte` / `.replacement_sha256` / `.operator` / `.description` | the validated site, directly | `mutation._outcome_of` (unchanged) |
| `helpers` | **omitted entirely** | route (i) invokes no helper (A-230a: omitted, never `[]`) |
| `snapshot_policy.selection` | `"repository"` | `runner._verdict_snapshot_policy` (unchanged) |
| top-level `outcome` / `reason_code` | rolled up | `verdict.rollup` (unchanged) |

**Three notes an implementer will otherwise get wrong.** `runner.py`'s three
`getattr(mutation_config, …)` lines at 1882–1891 already read all three
fields — nothing there changes; only `config` stops refusing them. The
`equivalent` bucket counts toward `total` and `candidate_count` like any other
attempted mutant, and is excluded from the mutation score's denominator. And
`kill_signal` is legal **only** on a `killed` entry (A-223e, enforced in the
model and per-bucket in the schema) — so §3.6's "signal missing ⇒ crashed" rule
is what keeps the producer inside a shape the model will accept.

### 5.2 Schema surface needed: **NO — measured, not asserted**

This is the question wave 2 got wrong at B004, so it is answered by building
the artifact rather than by reading the field list.

`/tmp/p34-scratch/target_artifact.py` constructs the exact verdict P34 intends
to emit at its most demanding: `language = "sql"`, all seven operators
declared, both artifacts declared, `kill_attribution = "declared"`, and a
`mutation` payload populating **`killed` with a `kill_signal`, `survived`,
`crashed`, and `equivalent` simultaneously**. Result (§9 M18):

```
MODEL: constructed OK
SCHEMA v6: valid
RAW VERIFIER on the target artifact -> ACCEPTED (0 failures)
```

Three independent layers accept it with **zero changes** to
`verdict.schema.json`, `errors.py`, `verdict.py`'s vocabularies, or
`verify.py`. And the layers are not vacuous on it — three targeted negatives
each produce failures from both the raw verifier and the model:

* removing the `kill_signal` from the killed entry under
  `kill_attribution = "declared"`;
* removing `equivalence_artifact` while keeping the `equivalent` bucket;
* relabelling a mutant `python:compare-swap` on a `sql` lane.

The belief the scope handed this carve — that
`_MUTATION_FIELDS_RESERVED_FOR_P34` reserves exactly what P34 needs — is
therefore **confirmed by construction**, not inherited. `PROVENANCE_UNVERIFIED`
(A-276) stays reserved for whichever bump another item pays for; P34 pays for
none.

**Lane schema (v2): also no change.** The two keys land inside the existing
`[lanes.X.judge.mutation]` table, which is already versioned at v2 and already
refuses unknown keys. No new table, no new top-level key, no `LANE_SCHEMA_VERSION`
bump.

---

## 6. Work items, in dependency order

Each is independently committable. `Docs (A-270)` names the user-facing
documents that must be in sync **before that item merges**, not after.

**W0 — record the rulings.** No code.
Files: `nyxloom-trove/decisions.md`, `nyxloom-trove/4-backlog.md`.
Rows for: the route (§4.1); `equivalence_artifact` required on a SQL lane
(§4.3); the §3.6 classification widening and its inertness for existing lanes;
A-243's sub-case ruled and reassigned to P29 (§4.6) — **this reverses an
assignment and needs the controller's acceptance before W5 lands**; the A-252
question left explicitly open (§4.5); and the correction that
`carve-assets/W1/expected/sql-r2-v6-template.json` is a contract-grammar
fixture presuming a helper, not a witness of intended output.

**W1 — the DDL lexer.** Files: **new** `src/assay/adapters/sql_lex.py` (pure,
imports only stdlib `re`).
Tests: **new** `tests/test_adapters_sql_lexer.py`.
Must cover: each of the six non-code constructs; `''`/`""` embedded quotes;
nested `/* /* */ */`; `$$` and `$tag$` including a body containing the *other*
tag; one level of recursion into a dollar body; mask length equals source
length and newlines survive; and all three unterminated constructs raising
`MutationDiscoveryError`. Include the real dstdns-shaped fixture from §9 M3 as
a committed asset so the `DO $$`-guarded FK is exercised by name.
**Docs (A-270): none** — no user-facing surface.

**W2 — the SQL adapter.** Files: **new** `src/assay/adapters/sql.py`.
Tests: **new** `tests/test_adapters_sql_generate_mutants.py`,
`tests/test_adapters_sql_test_path.py`,
`tests/test_adapters_sql_unreachable_methods.py`.
Must cover: all seven operators' spans and replacements; the three context
rules of §3.2 (`IS NOT NULL` produces no site, `SET NOT NULL` produces
`DROP NOT NULL`, `widen-check-in` is literal-shape-aware and emits nothing for
a non-literal list); no `drop-trigger` site inside a dollar body; the
replacement-invariant checks; bounded retention proven by handing `limit=1` to
a file with many candidates and asserting exactly one descriptor; and each of
the five unreachable methods raising `NotImplementedError` **by direct call**
(never a bare `...` body — coverage.py auto-excludes those and the gate reads
it as a pragma dodge).
**Docs (A-270): none yet** — W7 carries the operator vocabulary.

**W3 — the external-tool preflight (A-253).** Files: `src/assay/runner.py`.
Tests: **new** `tests/test_runner_external_tool_preflight.py`.
Exactly §3.5's three tests, including the assertion that the empty-tuple
control's subject really was empty.
**Docs (A-270): `docs/DESIGN-GUIDE.md`** — `MISSING_EXTERNAL_TOOL` stops being
reserved and gains a producer; its row must say so.

**W4 — the config surface.** Files: `src/assay/config.py` (language-scoped
unreservation; require `equivalence_artifact` on SQL; reuse the coverage-artifact
path validator; correct the stale "v5 verdict contract" message).
Tests: `tests/test_config_mutation.py`, `tests/test_config_accept.py`,
`tests/test_config_reject.py`, `tests/test_lane_schema_v2_locked_successors.py`.
Must cover: a SQL lane accepting both keys; a **Python** lane still refused for
each key with a language-scoped message; a SQL lane without
`equivalence_artifact` refused; every path-grammar refusal
(`judge.coverage.artifact`'s suite, re-asserted for both new keys).
**Docs (A-270): `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`** —
two new public config keys and a new consumer-typed language value.

**W5 — artifact plumbing and classification.** Files:
`src/assay/runner.py` (baseline artifact reservation inside the
`baseline_snapshot` block; thread `baseline_equivalence` into `run_mutation`;
extend the tracked-artifact refusal to both new paths), `src/assay/mutation.py`
(per-mutant reservations, `_read_kill_signal`, the §3.6 table,
`Mutation.equivalent` population).
Tests: `tests/test_mutation_classification.py` (**new**),
`tests/test_mutation_run_execution.py`, `tests/test_runner_run_lane*.py`.
Must cover: every row of §3.6's table; the **inertness** test — an existing
Python R2 lane with no `equivalence_artifact` produces a byte-identical verdict
before and after this item; a baseline that declared an artifact and did not
write it stopping before any mutant; a killed mutant with no kill signal under
declared attribution becoming `crashed`; and that `arm()` unlinked a
pre-planted stale artifact so the comparison cannot read it.
**Blocked on W0's controller acceptance** (§4.6 note).
**Docs (A-270): `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`** — the
classification table and the three consumer obligations of §3.4, including the
`pg_dump --restrict-key` gotcha with its measurement.

**W6 — CLI registry wiring.** Files: `src/assay/cli.py`.
Tests: `tests/test_cli_run.py`, `tests/test_adapters_sql_registration.py`
(**new**, mirroring `test_adapters_python_registration.py`).
Must cover: `judge.language = "sql"` at R2 resolving; at R1 and R3 refused
`BAD_LANE_CONFIG`; `assay run --help`'s capability sentence updated from
"This build evaluates R0, Python R1, Python R2 and Python R3."
**Docs (A-270): `README.md`** — the supported-language surface changes.

**W7 — the documentation gate, and its sixth derived vocabulary.**
Files: `tests/test_docs_examples_and_vocabulary.py`, plus the three documents.
A-270 check 2 derives five vocabularies today. `judge.mutation.operators` is a
closed public vocabulary a consumer types and is **not** among them — and it is
a live A-277-class gap, measured: **10 of the 14 `MUTATION_OPERATORS` appear in
no user-facing document at all** (all seven `sql:*` and all three `go:*` —
§9 M21). Add `MUTATION_OPERATORS` as the sixth derived set, extend
`test_derived_vocabularies_are_not_accidentally_identical_placeholders`, and
document the seven `sql:*` names.
The three `go:*` names and the three `HELPER_ROLES` (also undocumented, §9 M21)
are **P29's** to document; note them in the backlog rather than silently
documenting operators no producer exists for.
**Docs (A-270): `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`** —
the test fails until the seven names appear.

**W8 — acceptance oracles.** Files: tests only, per §7.

**W9 — real-PostgreSQL qualification at a pinned dstdns revision (3c).**
Files: **new** `gate/python/qualify_dstdns_sql.py`, **new**
`tests/test_gate_qualify_dstdns_sql.py`, **new**
`nyxloom-trove/carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json`.
Follows `qualify_cmru_b006a.py`'s shape exactly: pin the inputs, export a
disposable copy, and stub **only** the environment-bound subprocess seams while
everything else runs for real.
Pinned inputs (§9 M17), all four verified before anything runs:
`dstdns` `151cda0d6fca018c31e781673c19b4bad41179a8`, tree
`113154e6f66440b8e193c502f7b4c213be28ee86`; `infra/db-init/init-scripts` tree
`820d4c3cdfd38f0b3e29bfb9918febd2f2e1ada2`;
`scripts/schema-gate.sh` blob `88de912d52d5552a23630f68871d4f12e2a9eb83`;
`docs/proposals/cw2-p85-wave/REVIEW-CW2A.md` blob
`fc1a694d47650b1ae04e73e71ba20c8a39bcdc11`.
Operator-run, not in-gate: `tester-unified:local` does carry a docker CLI (§9
M1), but mounting the socket into the gate is a gate-invocation change this
package does not make.
The expected artifact is **witnessed from a real run and re-witnessed whenever
a ruling changes what assay produces** (A-274) — never structurally transformed
and never hand-edited toward green. `carve-assets/W1/expected/sql-r2-v6-template.json`
is *not* that witness and must not be edited (§9 M18).
**Docs (A-270): `docs/CONSUMERS.md`** — the worked dstdns lane a consumer can
paste.

---

## 7. Acceptance oracles

Each states the exact command, the exact expected output, and the exact
observable that differs if the feature is absent or broken.

**O1 — the lane runs at all.** *The absent-feature observable, measured today.*
Command: with §3.4's lane in a repository with one changed `.sql` file,
`assay run schema --verdict-json v.json`.
Expected: exit 0 and `v.json` carrying `"outcome": "PASS"` with
`claims[R2].mutation.total >= 1`, `judgment.resolved.language == "sql"`.
**Differs if absent:** measured at `bdc3dc78` — exit 2, stderr
`assay: ERROR/BAD_LANE_CONFIG: 'sql' is not a language this registry knows;
declared adapters: ['python']`, and the verdict is
`"outcome": "ERROR"`, `"reason_code": "BAD_LANE_CONFIG"` (§9 M20).
**Differs if broken:** an adapter registered at R1 or R3 as well resolves a
lane that must be refused.

**O2 — real DDL, real spans, and the traps.** *The oracle that consumes real
producer-emitted input (wave-1 lesson 5).*
Command: `python -m pytest tests/test_adapters_sql_generate_mutants.py -q`, over
the committed real dstdns file
`infra/db-init/init-scripts/21-create-workflow-corpus.sql` at the pinned blob.
Expected: exactly 2 `sql:weaken-delete-action` sites, at the two
`ON DELETE RESTRICT` foreign keys **inside** the `DO $$` block (source lines 327
and 332); and 0 sites for the `-- ON DELETE RESTRICT` comment on line 99.
**Differs if absent:** a bare-regex scanner reports **3** sites, one of them the
comment on line 99 (§9 M3). A single-level lexer reports **0** — it loses both
real sites (§9 M4).
**Differs if broken:** any off-by-one in the mask moves a span, and O3 catches
it as an invalid or wrong-effect mutant.

**O3 — span fidelity against a real database.** *Operator-run, W9.*
Command: `python gate/python/qualify_dstdns_sql.py --json report.json`, which
provisions a throwaway PostgreSQL 18.4, applies the pinned baseline DDL, then
applies each generated mutant to its own fresh database and reads
`pg_constraint` / `pg_trigger`.
Expected: every mutant applies with exit 0, and each catalog delta matches its
operator's row in §3.2's table — `contype` `u`→`c` for `drop-unique`, `f`→`c`
for `drop-foreign-key`, `confdeltype` `r`→`c` for `weaken-delete-action`, the
`contype='n'` row absent for `drop-not-null`, the `pg_trigger` row absent for
`drop-trigger`, and an **empty** name delta for `drop-check` / `widen-check-in`
with a differing `pg_dump`.
**Differs if absent:** nothing checks that a generated mutant is even valid DDL.
**Differs if broken:** a naive `widen-check-in` over
`CHECK (envelope_version IN (1))` fails to apply with
`ERROR: invalid input syntax for type integer: "__assay_widened__"` (§9 M11),
and under §3.6 lands in `crashed` rather than silently in `killed`.

**O4 — the residue false-survival, converted to a refusal.** *The real
false-kill attempt B001 item 4 demands, which a one-mutant fixture cannot show.*
Command: `python gate/python/qualify_dstdns_sql.py --residue-probe`, which runs
the identical mutant twice — once against a fresh database, once against a
database that already carries the un-mutated schema.
Expected: the fresh run reports `is_nullable = YES` and the mutant lands in
`survived` or `killed`; the residue run reports `is_nullable = NO`, its
`equivalence_artifact` is byte-identical to the baseline's, and the mutant
lands in **`equivalent`**. With every mutant there, the claim is
`INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT`.
**Differs if absent:** measured (§9 M9, M10) — the residue run exits 0 with the
constraint still in place, so without the equivalence comparison assay records
`survived` and renders `FAIL`/`MUTANTS_SURVIVED`, a false statement about the
consumer's tests.

**O5 — the `pg_dump` reproducibility trap.**
Command: `python -m pytest tests/test_gate_qualify_dstdns_sql.py -k restrict_key -q`.
Expected: two dumps of an unchanged database taken **with** a pinned
`--restrict-key` are byte-identical; taken **without** one they differ, and the
harness refuses the qualification with a message naming `\restrict`.
**Differs if absent:** measured (§9 M7) — two dumps of the same database have
different SHA-256s, so `equivalent` is permanently empty and
`ALL_MUTANTS_EQUIVALENT` is permanently unreachable, silently and greenly.

**O6 — the preflight can fire, and its control is not empty.**
Command: `python -m pytest tests/test_runner_external_tool_preflight.py -q`.
Expected: 3 passed. The absent-tool case renders
`NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` and the lane's command records zero
invocations; the present-tool case reaches the command; the empty-tuple case
asserts `adapter.external_tools == ()` **and** that the loop body ran zero times.
**Differs if absent:** `MISSING_EXTERNAL_TOOL` has no producer at all (its own
`errors.py` comment still says "RESERVED for P27").
**Differs if broken:** deleting the `raise` leaves the empty-tuple test green —
which is exactly why it is not the only test.

**O7 — malformed DDL fails closed.**
Command: `python -m pytest tests/test_adapters_sql_lexer.py -k unterminated -q`.
Expected: 3 passed — unterminated string, unterminated dollar quote,
unterminated block comment each raise `MutationDiscoveryError`, rendering
`ERROR`/`MUTATION_DISCOVERY_FAILED`.
**Differs if broken:** measured on the prototype (§9 M14) — all three inputs
silently produce sites for the file's valid prefix while real PostgreSQL
refuses every one with `ERROR: unterminated …` at exit 3.

**O8 — existing lanes do not move.**
Command: `python -m pytest tests/test_mutation_classification.py::test_a_lane_without_an_equivalence_artifact_is_byte_identical_before_and_after -q`,
plus the full suite.
Expected: a Python R2 lane's verdict JSON is byte-identical to the frozen
pre-P34 capture.
**Differs if broken:** §3.6's table applied unconditionally reclassifies every
Python mutant that exits 0 without an artifact as `crashed`.

**O9 — the three-layer artifact acceptance, with live negatives.**
Command: `python -m pytest tests/test_verdict_mutation_artifacts.py -k sql -q`
and `assay verify <the W9 witness>`.
Expected: the full SQL artifact of §5.2 is model-valid, schema-valid and
raw-verifier-clean; and each of the three negatives of §5.2 produces failures
from **both** the raw verifier and the model.
**Differs if broken:** the negatives are the proof the acceptance is not
vacuous; a missing one leaves an accept-everything oracle.

**O10 — the documentation gate can fail.**
Command: `python -m pytest tests/test_docs_examples_and_vocabulary.py -q`.
Expected: all pass, including the new sixth derived vocabulary, and the
fabricated-value control still reports its value missing.
**Differs if absent:** measured (§9 M21) — 10 of 14 operator names, all three
helper roles, and `unattributed` appear in no user-facing document today.

---

## 8. Limitations and deferrals

**8.1 Ordering and idempotency conformance is out of scope.** B001's third
defect — a bare glob with no pinned collation, surviving only because the base
image's shell sorts bytewise — is not a mutation finding. It is
distribution-shaped conformance of an ordered artifact set ("applies from
scratch, in a deterministic order, twice"), closer to `gate/distribution`'s
wheel contract than to R2, and it belongs to the project's own R0 command.
Recorded so it is not re-proposed as an adapter feature.

**8.2 The false-kill class is narrowed, not closed, and the residue is a
document.** §3.6 keeps an invalid mutant out of `killed` *provided* the
consumer's command writes `equivalence_artifact` only after a successful apply.
assay cannot verify that ordering — it sees a file, not a pipeline. A consumer
who writes the dump unconditionally converts every invalid mutant back into a
false kill. This is the design's largest soft spot and the thing an adversarial
reviewer should attack first. Two things bound it rather than excuse it: the
21 measured applications show the operator set does not *routinely* produce
invalid DDL, and `kill_signal_artifact` makes each kill's claimed mechanism
visible to a human reading the artifact.

**8.3 One level of dollar-quote recursion.** A dollar-quoted body inside a
dollar-quoted body is masked out and yields no sites. Not measured in the real
corpus because it does not occur there; recorded as a known ceiling rather than
handled speculatively.

**8.4 `max_mutants` will bite on a large schema change.** The real corpus
carries 466 code-context operator matches across 37 files, 267 of them
`NOT NULL` alone (§9 M4, M12). A change touching several files can exceed a
default cap and render `BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED` — which is the
correct, non-green refusal, and CONSUMERS.md must say so rather than let a
consumer read it as a failure.

**8.5 The external-auditor fallback stays available.** For a consumer whose
schema is generated (an ORM's migrations, a declarative-diff tool) rather than
tracked as DDL text, source mutation has nothing to locate. B001's fallback — a
reusable external PostgreSQL mutation auditor whose structured result assay
consumes as Tier-2 adjudicated evidence — is untouched by this carve and is the
right shape for that consumer.

**8.6 No SQL R1, no SQL R3.** Settled by `SCHEMA-V5-DESIGN.md` and A-192; B001
probe item 5 is answered and is not reopened.

**8.7 Deferred to P29, not to nobody:** A-243's `helpers` widening and its
open successful-helper-on-unrelated-failure sub-case (§4.6), and documenting
the three `go:*` operators and the three `HELPER_ROLES` (§9 M21).

**8.8 The A-252 spent-precedent question stays open** (§4.5). P34 needs no
tightening; the next package that does must obtain a ruling rather than read
one into A-252's silence.

---

## 9. Measurements

Every command was run in the foreground on this host on 2026-08-18. Real
output, trimmed only where marked.

**M1 — no SQL parser exists here, or in the gate image.**
```
$ for t in sqlfluff sqlglot pgsanity pg_query pg_format sqlparse; do
      printf '%s -> ' "$t"; command -v "$t" || echo MISSING; done
sqlfluff -> MISSING      sqlglot -> MISSING     pgsanity -> MISSING
pg_query -> MISSING      pg_format -> MISSING   sqlparse -> MISSING
$ python3 -c "import sqlparse" ; python3 -c "import sqlglot"
ModuleNotFoundError: No module named 'sqlparse'
ModuleNotFoundError: No module named 'sqlglot'

$ docker run --rm --network none tester-unified:local sh -c '...'
sqlfluff -> MISSING   sqlglot -> MISSING   pgsanity -> MISSING
psql -> /usr/bin/psql   pg_dump -> /usr/bin/pg_dump
ModuleNotFoundError: No module named 'sqlparse'
ModuleNotFoundError: No module named 'sqlglot'
Python 3.14.6
$ docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
      tester-unified:local sh -c 'command -v docker'
/usr/bin/docker
```

**M2 — trap density in the real corpus** (`/workspaces/dstdns` at
`151cda0d`, `git ls-files 'infra/db-init/init-scripts/*.sql'`):
```
files=37 bytes=316077
  -- comment         4007
  /* comment            0
  $$ dollar-quote      64
  'string'           4588
  "ident"             422
```

**M3 — a bare keyword regex over the bytes vs the same regex over a
code-only mask** (`/tmp/p34-scratch/scan.py`):
```
operator                      raw-regex  code-only  phantom
sql:drop-check                       71         49       22
sql:drop-unique                      53         39       14
sql:drop-not-null                   274        251       23
sql:drop-foreign-key                 56         50        6
sql:weaken-delete-action              3          0        3
sql:drop-trigger                      6          6        0
sql:widen-check-in                   49         49        0
TOTAL                               512        444       68
```
68/512 = **13.3% phantom**. And `sql:weaken-delete-action` goes to **zero**,
which sent me looking:
```
$ grep -rniE 'ON[[:space:]]+DELETE[[:space:]]+RESTRICT' infra/db-init/init-scripts/
21-create-workflow-corpus.sql:99:-- ON DELETE RESTRICT, which is the invariant that protects history.
21-create-workflow-corpus.sql:327:  FOREIGN KEY (corpus_version_id) REFERENCES corpus_versions(id) ON DELETE RESTRICT;
21-create-workflow-corpus.sql:332:  FOREIGN KEY (current_version_id) REFERENCES corpus_versions(id) ON DELETE RESTRICT;
```
Line 99 is a comment. **Lines 327 and 332 are real foreign keys inside a
`DO $$ … $$` idempotency guard** — the only two in the corpus.

**M4 — three tiers, and what recursing into dollar bodies recovers**
(`/tmp/p34-scratch/scan2.py`):
```
operator                       A raw  B outer  C +$body
sql:drop-check                    71       49        49
sql:drop-unique                   53       39        39
sql:drop-not-null                274      251       267
sql:drop-foreign-key              56       50        54
sql:weaken-delete-action           3        0         2
sql:drop-trigger                   6        6         6
sql:widen-check-in                49       49        49
TOTAL                            512      444       466

dollar-quoted body bytes: 57453 / 316077 (18.2%)
```

**M5 — every generated mutant applied to real PostgreSQL 18.4, and its catalog
delta.** Baseline fixture `/tmp/p34-scratch/fixture.sql` carries four traps
(a comment naming all three keywords, a string literal naming them, a
plpgsql body naming them, and a real FK inside a `DO $$` block).
`PostgreSQL 18.4 on x86_64-pc-linux-musl`, throwaway container, no network.
```
baseline catalog rows: 13

operator                    line apply        catalog delta
sql:drop-not-null              7 applied      -['c:parent_label_not_null:n:'] +[]
sql:drop-not-null              8 applied      -['c:parent_kind_not_null:n:'] +[]
sql:drop-check                 9 applied      -[] +[]
sql:widen-check-in             9 applied      -[] +[]
sql:drop-unique               10 applied      -['c:parent_label_unique:u:'] +['c:parent_label_unique:c:']
sql:drop-not-null             15 applied      -['c:child_parent_id_not_null:n:'] +[]
sql:drop-check                17 applied      -[] +[]
sql:drop-foreign-key          18 applied      -['c:fk_child_parent:f:r'] +['c:fk_child_parent:c:']
sql:weaken-delete-action      19 applied      -['c:fk_child_parent:f:r'] +['c:fk_child_parent:f:c']
sql:drop-trigger              37 applied      -['t:trg_child_reject_zero'] +[]
sql:drop-foreign-key          48 applied      -['c:fk_child_parent_deferred:f:r'] +['c:fk_child_parent_deferred:c:']
sql:weaken-delete-action      48 applied      -['c:fk_child_parent_deferred:f:r'] +['c:fk_child_parent_deferred:f:c']
```
12/12 applied. Zero sites in any of the four traps. Lines 48 are the sites
inside the `DO $$` block.

**M6 — two operators are invisible to a catalog-name diff.**
`sql:drop-check` and `sql:widen-check-in` show an empty delta above. They are
visible in a schema dump:
```
$ diff dump_m_base.sql dump_m_2.sql
63c63
<     CONSTRAINT parent_kind_domain CHECK ((kind = ANY (ARRAY['alpha'::text, 'beta'::text])))
---
>     CONSTRAINT parent_kind_domain CHECK (true)
$ diff dump_m_base.sql dump_m_3.sql
63c63
<     CONSTRAINT parent_kind_domain CHECK ((kind = ANY (ARRAY['alpha'::text, 'beta'::text])))
---
>     CONSTRAINT parent_kind_domain CHECK ((kind = ANY (ARRAY['alpha'::text, 'beta'::text, '__assay_widened__'::text])))
```

**M7 — `pg_dump --schema-only` is NOT byte-reproducible by default.**
Two dumps of the *same, unchanged* database:
```
$ diff dump_a.sql dump_b.sql
5c5
< \restrict HcvdJaImf8mvxSIPA6alkcTpg6hitLi1IFne1jqkOxzJ4LafDWbdjQEwIXvtk0p
---
> \restrict 7pfJB5b4Gs7JkJxgaPOntfxN4fqx3OS5tYWZJI2bakCE53cmLkAdXXICQ5m1qgB
118c118  (\unrestrict, same random key)
$ sha256sum dump_a.sql dump_b.sql
fbe6f7b30f8640a57b861144c28d131d0ec9383445084faae38e7bb5b8c9b9de  dump_a.sql
753997084fc06ad50f8ce9c0d5cee9bf9a70254f189b6185c809eec1fc7f84ba  dump_b.sql
```

**M8 — and it IS reproducible with `--restrict-key` pinned.**
```
$ K=assayfixedkey0000000000000000000000000000000000000000000000000000
$ pg_dump --schema-only --no-owner --restrict-key=$K -d m_base > dump_k1.sql   # x2
$ diff -q dump_k1.sql dump_k2.sql && echo "IDENTICAL with pinned restrict-key"
IDENTICAL with pinned restrict-key
6296e15ac473139cd478dd651da8d25a0fb4612f1da7c66263ea531daf9a41f4  dump_k1.sql
6296e15ac473139cd478dd651da8d25a0fb4612f1da7c66263ea531daf9a41f4  dump_k2.sql
```

**M9 — the residue false-survival, on real PostgreSQL.** Fixture in the
dstdns idiom (`CREATE TABLE IF NOT EXISTS` + a `DO $$` guard). Mutant:
`label TEXT NOT NULL` → `label TEXT NULL`.
```
apply base to resid: 0
apply real-mutant to the SAME db: 0
label column is_nullable on RESIDUE db: NO
label column is_nullable on FRESH   db: YES
```
Both applications exit 0. On the residue database the mutation **never
happened**, and nothing says so.

**M10 — the equivalence artifact catches both the residue case and a genuinely
inert mutant.**
```
sha256 (fresh, base)        66c1dd2f759a0bc733ee731909aaf7fadfe6d6d970576517706980220eafaec5
sha256 (guarded-clause mut) 66c1dd2f759a0bc733ee731909aaf7fadfe6d6d970576517706980220eafaec5  <- equivalent
sha256 (real mutant)        4c05c2d249ac5229e88d7eceaf12af3ea33826d22f4d8f6ff484b594ba95af16  <- differs
sha256 (residue db)         66c1dd2f759a0bc733ee731909aaf7fadfe6d6d970576517706980220eafaec5  <- equivalent
```
The mutation inside the never-firing `DO $$` guard is byte-identical to
baseline. So is the residue run. Both become `equivalent`, not `survived`.

**M11 — the type-mismatch widening hazard, and its fix.**
```
$ # CHECK (envelope_version IN (1)) widened with a string literal
ERROR:  invalid input syntax for type integer: "__assay_widened__"
LINE 3: ...NOT NULL DEFAULT 1 CHECK (envelope_version IN (1, '__assay_w...
wide_exit=3
$ # CHECK (envelope_version IN (1, 2)) widened with a numeric literal
numeric_widen_exit=0
```
And this shape is present in the real corpus:
```
$ grep -rniE "CHECK[[:space:]]*\([^)]*IN[[:space:]]*\([[:space:]]*[0-9]" .
03c-create-workflow-core.sql:266:  envelope_version INT NOT NULL DEFAULT 1 CHECK (envelope_version IN (1)),
03c-create-workflow-core.sql:331:  envelope_version INT NOT NULL DEFAULT 1 CHECK (envelope_version IN (1)),
```
A non-zero exit maps to `FAIL` in `execute_command`, which
`_classify_mutant_result` currently reads as **killed** — a false kill.

**M12 — 13.1% of code-context `NOT NULL` occurrences are `IS NOT NULL`
predicates, and mutating one applies cleanly.**
```
code-context NOT NULL total: 267
  ... of which 'IS NOT NULL' predicates (WRONG target): 35
  ... genuine column-constraint candidates:            232
  mis-location rate of the naive rule: 13.1%
```
```
$ # (status IN ('succeeded','failed') AND finished_at IS NOT NULL)  ->  IS NULL
mutated line:  (status IN ('succeeded','failed') AND finished_at IS NULL)
base_exit=0
mutant_exit=0
```
The mutant applies. It is a legal mutation **mis-labelled `sql:drop-not-null`**
at a span that is not a `NOT NULL` constraint.

**M13 — A-243's ruled widening is not implemented at `bdc3dc78`.** A
`mutation-sites` helper beside each payload-free R2 terminal, through the real
`assay.verdict.Verdict`:
```
LANE_TIMEOUT (A-243 open sub-case):    REFUSED -- helpers records a 'mutation-sites' helper ... but this verdict carries no an R2 claim carrying a mutation payload
MUTATION_UNSUPPORTED (A-243 guard 3):  REFUSED -- (same rule)
MUTATION_DISCOVERY_FAILED (A-243 ruled): REFUSED -- (same rule)
MISSING_EXTERNAL_TOOL (A-253):         REFUSED -- (same rule)
SNAPSHOT_LIMIT_EXCEEDED:               REFUSED -- (same rule)
```
Guard (3) already holds. The ruled permission for `MUTATION_DISCOVERY_FAILED`
does not exist yet. (The message also has a grammar defect — "carries no an R2
claim" — in `verdict._check_helpers`; W5 touches that function and should fix
it in passing.)

**M14 — the prototype lexer without fail-closed silently succeeds on malformed
DDL that PostgreSQL refuses.**
```
=== unterm_dollar ===   scanner: 1 site, exit 0
  psql: ERROR:  unterminated dollar-quoted string at or near "$$   psql_exit=3
=== unterm_string ===   scanner: 1 site, exit 0
  psql: ERROR:  unterminated quoted string at or near "'oops);     psql_exit=3
=== unterm_comment ===  scanner: 1 site, exit 0
  psql: ERROR:  unterminated /* comment at or near "/* nested /*   psql_exit=3
```

**M15 — replacement-shape validity on real PostgreSQL (9 further applications).**
```
A. ALTER ... SET NOT NULL
   base (SET NOT NULL)   exit=0
   SET NULL              ERROR: syntax error at or near "NULL"   exit=3
   DROP NOT NULL         exit=0
B. trigger elision
   SELECT 1; at top level                exit=0
   SELECT 1; inside a DO $$ body         ERROR: query has no destination for result data   exit=3
   PERFORM 1; inside a DO $$ body        exit=0
C. CREATE TRIGGER inside a DO $$ guard   exit=0   (a real, legal shape)
D-G. column UNIQUE -> CHECK (true)                exit=0
     column CHECK -> CHECK (true)                 exit=0
     ADD CONSTRAINT n UNIQUE (a) -> CHECK (true)  exit=0
     CREATE UNIQUE INDEX -> CREATE INDEX          exit=0
     column REFERENCES ... ON DELETE RESTRICT -> CHECK (true)  exit=0
```

**M16 — B001's mock-lane claim, confirmed at the pinned revision.** The real
`20-create-corpora.sql` with `THIS IS NOT SQL AT ALL;` inserted as line 2:
```
dstdns test_corpora_table_in_migration assertion on the BROKEN file: True
psql: ERROR:  syntax error at or near "THIS"
LINE 1: THIS IS NOT SQL AT ALL;
psql_exit=3
```
Also measured at that revision: two of the three sibling tests
(`test_corpus_runs_table_in_migration`, `test_job_domain_scope_table_in_migration`)
assert strings the file no longer contains — the tables were removed in D-030.
So that mock lane is either red or not run; either way it is not evidence.

**M17 — the pinned dstdns evidence (3c).**
```
$ git -C /workspaces/dstdns rev-parse HEAD
151cda0d6fca018c31e781673c19b4bad41179a8
$ git rev-parse 'HEAD^{tree}'
113154e6f66440b8e193c502f7b4c213be28ee86
$ git rev-parse 'HEAD:infra/db-init/init-scripts'
820d4c3cdfd38f0b3e29bfb9918febd2f2e1ada2
$ git rev-parse 'HEAD:scripts/schema-gate.sh'
88de912d52d5552a23630f68871d4f12e2a9eb83
$ git rev-parse 'HEAD:docs/proposals/cw2-p85-wave/REVIEW-CW2A.md'
fc1a694d47650b1ae04e73e71ba20c8a39bcdc11
$ git status --porcelain    # clean
```

**M18 — the schema question, answered by construction.**
```
$ python3 -c "import json,pathlib; print(json.loads(pathlib.Path(
      'src/assay/schemas/verdict.schema.json').read_text())['$id'])"
urn:assay:schema:verdict:6
   /$defs/mutation_operator/oneOf/2  ['sql:drop-check', 'sql:drop-unique',
     'sql:drop-not-null', 'sql:drop-foreign-key', 'sql:weaken-delete-action',
     'sql:drop-trigger', 'sql:widen-check-in']

$ PYTHONPATH=src python3 /tmp/p34-scratch/target_artifact.py
MODEL: constructed OK
SCHEMA v6: valid

$ # and through the raw verifier, plus three negatives
RAW VERIFIER on the target artifact -> ACCEPTED (0 failures)
neg 1 (declared attribution, kill without signal) -> [
  "an attributed R2 run leaves killed mutant(s) in [...] with no kill_signal; attribution that covers only some kills is not attribution",
  "schema: judgment.r2 records kill_attribution 'declared' but killed mutant(s) [...] carry no kill_signal ..."]
neg 2 (equivalent bucket, no equivalence_artifact) -> [
  "the R2 payload claims 1 provably-inert mutant(s) while judgment.r2 declares no equivalence_artifact to have compared them against",
  "schema: claim[R2].mutation records 1 equivalent mutant(s) but judgment.r2 declares no equivalence_artifact ..."]
neg 3 (python operator on a sql lane) -> [
  "the R2 mutation payload names operator(s) ['python:compare-swap'] that judgment.r2.operators [...] never declared",
  "the R2 mutation payload records mutants produced by ['python:compare-swap'] while judgment.resolved.language is 'sql' ...",
  "schema: claim[R2].mutation records outcome(s) for ['python:compare-swap'] on a lane whose judgment.resolved.language is 'sql' ..."]
```
Zero schema changes. Both layers fire on all three negatives.
*(Note on method: `verify.verify_document` RETURNS a failure list; it does not
raise. My first attempt wrapped it in `try/except` and reported both rules as
missing. That was a mis-measurement of another tool's contract — the exact
class A-272 exists for — caught by reading the function rather than trusting
the run.)*

The frozen `carve-assets/W1/expected/sql-r2-v6-template.json` presumes an
external helper:
```json
"helpers": [{"role": "mutation-sites", "tool": "assay-sql-sites",
             "resolved_path": "/opt/assay-helpers/bin/assay-sql-sites", ...}]
```
Its consumers in `carve-assets/W1/test_acceptance_v6.py` use it as a *valid
base document* for the helper-correspondence tests (`load(... sql-r2-v6-template.json)`
then `broken["helpers"] = [...]`), never as an expectation about assay's
output. So route (i) does not falsify it, and it must not be edited.

**M19 — the model requires a signal on every kill under declared attribution.**
`verdict.Verdict._check_kill_attribution`: `declared` ⇒ every `killed` entry
carries `kill_signal`; `unattributed` ⇒ no entry in any bucket carries one.
Confirmed by neg 1 above. This is what forces §3.6's "signal missing ⇒ crashed".

**M20 — the absent-feature observable, measured end to end at `bdc3dc78`.**
A real repository, a real `assay.toml` with §3.4's lane, run through
`assay.cli.main(['run','schema','--verdict-json','-'])`:
```
"outcome": "ERROR", "reason_code": "BAD_LANE_CONFIG", "exit_code": 2,
"schema_version": 6, "snapshot_policy": {"selection": "repository"}
assay: ERROR/BAD_LANE_CONFIG: 'sql' is not a language this registry knows;
  declared adapters: ['python']
```
And with `equivalence_artifact` declared:
```
assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: lane 'schema': judge.mutation
  key(s) equivalence_artifact are RESERVED for P34 and cannot be declared yet;
  the v5 verdict contract carries them, ...
```
(That message says "v5"; the shipped contract is v6. W4 fixes it.)

**M21 — an A-277-class documentation gap, one vocabulary over.** Derived from
the shipped modules and checked against `README.md`, `docs/CONSUMERS.md`,
`docs/DESIGN-GUIDE.md`:
```
MUTATION_OPERATORS: 14 values, MISSING from all three docs:
  ['go:compare-swap', 'go:boolop-swap', 'go:bool-const-flip',
   'sql:drop-check', 'sql:drop-unique', 'sql:drop-not-null',
   'sql:drop-foreign-key', 'sql:weaken-delete-action', 'sql:drop-trigger',
   'sql:widen-check-in']
languages:          3 values, MISSING: []
HELPER_ROLES:       3 values, MISSING: ['statement-positions', 'mutation-sites', 'executable-code']
KILL_ATTRIBUTIONS:  2 values, MISSING: ['unattributed']
```
`judge.mutation.operators` is a closed public vocabulary a consumer types, and
it is not among `test_docs_examples_and_vocabulary.py`'s five derived sets.

**M22 — string-escape lexing, measured rather than remembered.**
```
$ psql -tAc "SHOW standard_conforming_strings"
on
$ psql -tAc "SELECT length('a\')"        # plain string, backslash then quote
2                                        # -> the quote TERMINATED; '\' is literal
$ psql -tAc "SELECT length(E'a\'b')"     # E-string
3                                        # -> \' did NOT terminate; the value is a'b
$ grep -rhoE "\bE'" infra/db-init/init-scripts/ | wc -l
0
$ grep -rhoE "U&'" infra/db-init/init-scripts/ | wc -l
0
```
So a plain `'…'` must be scanned with `''`-only escaping and an `E'…'` must
additionally honour `\`. Neither prefix appears in the real corpus, so the
`E'` rule is carried on PostgreSQL's contract, not on a consumer's need, and
its test fixture is constructed rather than harvested.

---

**Scratch artifacts** (not committed; reproduce from §9):
`/tmp/p34-scratch/{scan.py, scan2.py, mutate.py, verify_spans.py,
target_artifact.py, fixture.sql, fixture2.sql}`. Throwaway PostgreSQL:
`docker run -d --name p34probe --network none -e POSTGRES_PASSWORD=probe
postgres:18-alpine`.
