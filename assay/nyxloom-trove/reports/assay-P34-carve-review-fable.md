# Adversarial review — W3-CARVE-P34-sql-adapter.md

**Verdict: READY WITH CORRECTIONS.**

The design core survived every attack I could mount: the stdlib two-level
lexer route, the seven-operator table and its three measured context rules,
the required `equivalence_artifact` and its asymmetry argument, the zero
schema-surface claim, the registry/rigor refusal set, and the safeio
reservation story are all real against the shipped code and against a live
PostgreSQL 18. But the document reproduces, at its own centre, the exact
defect class it was written to prevent: under §3.4's own canonical consumer
command, **the `killed` bucket is unreachable and every genuine kill turns
the lane into `ERROR`/`EXEC_FAILED`** (finding A, confirmed and extended —
O1's expected observable can never render). Around that sit four more
blocking corrections: the consumer command that satisfies the obligations
exists nowhere and no work item writes it; two conformance-audit exclusions
P34 falsifies are in no work item; §3.1 phase 4's bounded-retention wording
contradicts the shipped identity contract and the Python precedent; and the
converse helper-direction obligations shipped code assigns to P34 are
orphaned by route (i) with no reassignment. All five are correctable in the
carve document itself — none reopens the route, the operator set, the
classification table, or the config surface. Reviewer: Fable (claude-fable-5),
2026-08-18, at `bdc3dc78`, cwd `/workspaces/vbpub/.worktrees/assay-P34-sql-adapter`.
None of A–D was refuted; all four confirmed, all four extended.

---

## 1. Findings A–D

### A — CONFIRMED and EXTENDED. `killed` is unreachable under the carve's own canonical command, and O1 can never render its expected output.

Carve line 352–354 (obligation 1) gives the canonical shape as
`apply && test && dump`. Carve line 414/416 (§3.6) classifies
`FAIL` + present + ≠ as `killed` and `FAIL` + absent as `crashed`. A kill
*is* `test` exiting non-zero; `&&` short-circuits; `dump` never runs. And a
stale artifact cannot substitute for the missing dump:
`safeio.OutputReservation.arm()` unlinks the pre-run regular file
(`src/assay/safeio.py:193` "``arm`` is the only operation allowed to unlink
the pre-run regular file", `:225`/`:249`), so under the canonical command
**present ⟺ test exited 0**, i.e. `FAIL` ⟹ absent ⟹ `crashed`. §3.7
line 470 then maps any `crashed` mutant to `ERROR`/`EXEC_FAILED`
(`mutation.judge_mutation`, verified at `src/assay/mutation.py:966-967`,
where `crashed` outranks `budget_exceeded`, `survived` and `equivalent`).
The first mutant a consumer's suite actually catches turns the whole lane
into an error, and twenty kills plus nothing else render `ERROR`, never
`PASS`.

**Extension 1 — the carve's own acceptance oracle O1 is unreachable.** O1
(carve lines 844–853) expects `"outcome": "PASS"` with
`claims[R2].mutation.total >= 1` from §3.4's lane. `judge_mutation` renders
`PASS` only for ≥1 killed, 0 survived, 0 crashed, 0 budget_exceeded
(`mutation.py:964-978`). Under the canonical command a kill is impossible,
so O1's expected observable cannot occur. The oracle contradicts the
document's own §3.4 + §3.6 as written.

**Extension 2 — the fix is the one token, plus prose, and the table itself
is untouched.** With `apply && dump && test`, every row of §3.6's table is
enumerable and reachable by a real consumer, and no row becomes unreachable:

| row | reachable under `apply && dump && test`? | by what |
|---|---|---|
| `PASS` + absent → crashed | yes | consumer error only: command exits 0 without writing the declared path (wrong path in the script). Honest refusal; this was already its only role under the old order too. |
| `PASS` + present + ≠ → survived | yes | mutant applied, suite blind — the headline `FAIL`/`MUTANTS_SURVIVED` path (M5-class mutants) |
| `PASS` + present + = → equivalent | yes | residue / never-firing-guard mutant, suite passes (§9 M9/M10) |
| `FAIL` + present + ≠ → killed | **yes — newly reachable** | mutant applied, dump written, suite refused it |
| `FAIL` + present + = → equivalent | yes | schema byte-equal to baseline yet tests failed. Since `run_mutation` only runs after the baseline R0 PASSed (`mutation.py:774`, `runner.py:1639`), this means a nondeterministic/flaky suite failure, and `equivalent` is the honest refusal-to-claim. It remains distinguishable from a kill: byte equality of the dump is the mechanical discriminator, exactly as designed. |
| `FAIL` + absent → crashed | yes | apply failed = invalid mutant (M11-class, and my §6 probes) |
| `ERROR` / `BUDGET_EXCEEDED` | unchanged | unchanged |

The fix therefore reaches **prose and its propagation, not the table**:
obligation 1 must say the dump is written *after a fully successful apply
and independent of the test result* (`apply && dump && test`; `dump || true`
stays forbidden), and the same ordering must appear in W5's
CONSUMERS.md/DESIGN-GUIDE items and in whatever command W9's harness runs
(see blocking finding 2 — that command does not currently exist). One
subtlety worth a sentence in CONSUMERS.md: under declared attribution the
kill-signal write stays in the *test* stage, so a genuine kill whose harness
fails to write the signal still reclassifies `crashed` per §3.6 and reddens
the lane as `ERROR` — correct per the declared contract, but a consumer
should be told.

### B — CONFIRMED and EXTENDED. Route (i) orphans the converse-direction obligations, and the carve never mentions them.

`grep -n "converse" nyxloom-trove/W3-CARVE-P34-sql-adapter.md` → **zero
hits**. §4.6 reassigns only A-243's widening and its open sub-case. But at
`bdc3dc78`:

* `src/assay/verdict.py:2396-2401` — "**The converse is deliberately NOT
  implemented here.** 'A claim produced with a helper requires an entry'
  … **P34 owns that direction, with the adapter that makes the state
  reachable**".
* `src/assay/verify.py:761-764` — the twin: "P34 owns it, with the adapter
  that makes the state reachable."
* `tests/test_verdict_judgment.py:983-986` — the test docstring: "The
  converse … has no readable antecedent in the artifact bytes, so P34 owns
  it."

This is a **distinct obligation from A-243's widening** (A-243 is about a
payload-free `helpers` entry beside `MUTATION_DISCOVERY_FAILED`; the
converse is "claim-produced-with-helper requires an entry", A-223c-era) —
there is no reason the two sites differ from the widening's disposition:
under `external_tools = ()` P34 invokes no helper, so it can make neither
state reachable. The carve owes the same reassignment-to-P29 ruling for the
converse direction, plus docstring touch-ups in all three files, and none of
verdict.py / verify.py / test_verdict_judgment.py appears in any work item's
file list. Further P34-named text route (i) leaves half-true:
`tests/test_standalone.py:634` and `tests/test_cli_run.py:328` say config
"refuses that field until P34" — after W4 the refusal *survives* for their
Python lanes, so "until P34" becomes wrong in the other direction (test_cli_run.py
is at least in W6's list; test_standalone.py is in no list).

### C — CONFIRMED and EXTENDED. The stale-reservation sweep is much larger than errors.py, and two entries are mechanical audits, not comments.

Confirmed: `src/assay/errors.py:131` "(P21/A-163, RESERVED for P27)" and
`:147` "`MISSING_EXTERNAL_TOOL` above is still reserved" — both false the
moment W3 lands, and `errors.py` is in no work item's file list. Extended —
the full set of in-code text P34 falsifies, with its work-item coverage:

| location | text | falsified by | in a file list? |
|---|---|---|---|
| `errors.py:131,147` | reserved for P27 / still reserved | W3 | **no** |
| `tests/test_verdict_conformance.py:180-193` | `EXCLUDED_ENTIRELY` carries `("NO_MEASUREMENT","MISSING_EXTERNAL_TOOL")` ("reserved by A-163 for P27's first real external-tool preflight" — doubly stale post-A-253) and `("INCONCLUSIVE","ALL_MUTANTS_EQUIVALENT")` ("**P34 removes this line when it makes the state reachable**") | W3, W5 | **no — see blocking finding 3** |
| `src/assay/registry.py:34-46` | "external-tool preflighting … deferred … the closed reason-code vocabulary has no member for it yet … belong[s] to whichever future package first registers an adapter that actually needs one" | W3 (and already stale: the member has existed since P21) | **no** |
| `src/assay/verdict.py:1547,1554` | "NOT declarable in lane config until P34" (both artifact fields) | W4 | **no** |
| `src/assay/vocabulary.py:76` | "declarable but unproducible here; P34 lands the adapter" | W2/W6 | **no** |
| `src/assay/runner.py:1873-1879` | "In THIS build the derivation has exactly one reachable answer … config refuses `kill_signal_artifact` until P34" | W5 | file yes, docstring unmentioned |
| `src/assay/config.py:265-269,1454-1469` | `_MUTATION_FIELDS_RESERVED_FOR_P34` and the refusal message | W4 | yes (covered) |
| `tests/test_verdict_conformance.py:188-190` comment | "config refuses that declaration until P34 ships the producer" | W4/W5 | no |

The conformance rows are not comment hygiene — see blocking finding 3.

### D — CONFIRMED and EXTENDED. Not greppable, one-line fix covers all three entries, the verify.py twin is clean — but a shipped test pins the broken literal and is in no file list.

The rendered "carries no an R2 claim carrying a mutation payload" is
composed at `src/assay/verdict.py:2432` (`f"but this verdict carries no
{what} …"`) from the `requirement` table at `:2419-2426`, whose three `what`
strings all begin with an article ("an R1 claim carrying a coverage
payload", "an R2 claim carrying a mutation payload", "an R1 claim carrying
coverage or an R2 claim carrying mutation"). Fixing the f-string wrapper
once (e.g. "but this verdict does not carry {what}") repairs **all three**
roles' messages; alternatively strip the article from all three `what`
strings. `verify.py`'s twin does **not** share the defect — its message at
`verify.py:794-797` is differently worded ("but no claim in this verdict
carries the payload such a helper would have produced") and grammatical, so
the fix does not reach it. Extension: **a shipped test pins the broken
literal** — `tests/test_verdict_judgment.py:1011`:

```python
with pytest.raises(ValueError, match="carries no an R1 claim|statement-positions"):
```

After the grammar fix the first alternative can never match and the assert
silently degrades to matching the bare role name — a weakened negative. The
fix must update that `match` too, and neither verdict.py nor
test_verdict_judgment.py is in W5's file list (the carve's own M13 note,
lines 1220-1224, says "W5 touches that function [`verdict._check_helpers`]
and should fix it in passing" — but W5's Files line names only `runner.py`
and `mutation.py`).

---

## 2. Blocking findings

### BLOCK-1. `killed` unreachable / O1 unreachable under the canonical command (finding A).

* **Wrong:** §3.4 obligation 1's canonical `apply && test && dump` (line
  353-354) makes §3.6's `killed` row and O1's expected `PASS` unreachable;
  every real kill renders `ERROR`/`EXEC_FAILED` for the lane.
* **Measurement:** §1-A above — `safeio.arm()` unlink semantics + §3.6 rows
  414/416 + `mutation.judge_mutation` precedence (`mutation.py:966-967`) +
  O1's `PASS` condition (`mutation.py:964-978`).
* **Consequence if shipped:** the feature's headline outcome never occurs;
  the first consumer whose suite catches a mutant sees the lane go red as an
  ERROR; wave-1 lesson 2 (a check that cannot fire on the success path)
  inside the document written to prevent it.
* **Smallest fix:** reorder the canonical shape to `apply && dump && test`,
  extend obligation 1's prose with "written after a fully successful apply
  and regardless of the test outcome", and propagate the same ordering to
  W5's docs items and W9's command. §3.6's table needs **no** row change
  (the enumeration in §1-A shows every row stays reachable and honest).

### BLOCK-2. The consumer command that satisfies the obligations does not exist, and no work item writes it.

* **Wrong:** §3.4's "complete, pasteable" lane declares
  `argv = ["scripts/schema-gate.sh"]` (line 297) with
  `allow_argv_append = false`, and W9 pins that script's blob `88de912d` as
  a verified input (line 824). The real pinned script (a) **writes no dump
  and no kill signal anywhere** — it applies via `schema-apply.sh` then runs
  pytest, nothing else; (b) **exits 2 immediately under that argv** — its
  first positional parameter is mandatory
  (`WORKTREE_ARG="${1:?usage: schema-gate.sh <worktree-path> …}"`); and (c)
  depends on the main checkout, `config_helper.py env`, `testing-exec.sh`,
  a docker daemon and the app network — none of which exists inside an assay
  snapshot.
* **Measurement:** §6 M-R7 below (the pinned blob's own text).
* **Consequence if shipped:** every mutant under the pasted lane is
  artifact-absent → `crashed` → the whole W9 qualification and O1/O3/O4
  render `ERROR`; the "worked dstdns lane a consumer can paste" cannot work
  as written; the implementer discovers mid-W9 that the central deliverable
  (a schema-gate command implementing obligations 1–3) was never assigned.
* **Smallest fix:** add to W9 (and to W5's CONSUMERS.md item) an explicit
  new file: the qualification's own gate script — apply to a throwaway
  database, `pg_dump --schema-only --no-owner --restrict-key=<pinned>` to
  the declared path, then run the schema tests, writing the kill signal on
  failure — and state that §3.4's `argv` line is the *shape* of a consumer
  lane, with W9 substituting its own self-contained command. Strongly
  recommended while writing it: make obligation 2 self-enforcing by dumping
  twice and `cmp`-ing inside the command (see NB-6).

### BLOCK-3. Two conformance-audit exclusions P34 falsifies are in no work item, and nothing mechanical forces their removal.

* **Wrong:** `tests/test_verdict_conformance.py:180-193` excludes
  `("NO_MEASUREMENT","MISSING_EXTERNAL_TOOL")` and
  `("INCONCLUSIVE","ALL_MUTANTS_EQUIVALENT")` from the
  every-pair-has-a-covering-fixture audit
  (`test_every_required_vocabulary_pair_has_a_covering_fixture`,
  `:267-279`), each with an explicit removal obligation ("P34 removes this
  line when it makes the state reachable, and this audit turns red until it
  does"). W3 makes the first pair producible, W5 the second. Neither W3 nor
  W5 lists `test_verdict_conformance.py` or new fixtures under
  `tests/fixtures/verdicts/`.
* **Measurement:** file text quoted above; audit mechanics read at
  `:267-279` — removal without a covering complete-artifact fixture turns
  the suite red; *not* removing keeps it green forever with a false comment.
* **Consequence if shipped:** P34 lands, both exclusions silently persist,
  the fixture corpus under-covers the closed vocabulary for the two pairs
  P34 itself made reachable — A-141's exact shape, in the audit that exists
  to prevent it.
* **Smallest fix:** add to W3: remove the `MISSING_EXTERNAL_TOOL` exclusion
  line + commit a complete witnessed fixture for the pair; add the twin to
  W5 for `ALL_MUTANTS_EQUIVALENT`; both audits then close themselves.

### BLOCK-4. §3.1 phase 4's bounded-retention wording cannot be implemented as stated and contradicts the shipped precedent.

* **Wrong:** carve line 195-197: "walks the mask once per operator pattern
  in `MutationSite.identity` order and stops appending at limit."
  `MutationSite.identity` is `(start_byte, end_byte, replacement_sha256,
  operator)` (`src/assay/mutation.py:255-259`) — **position-major, operator
  last** — so a per-operator walk cannot be "in identity order", and
  `_validate_sites` refuses any batch that is not identity-sorted
  (`mutation.py:516-521`).
* **Measurement:** the three possible readings, against the shipped code:
  (i) append per operator, stop at limit, return as-is → on any file where a
  later-listed operator has an earlier `start_byte` (ordinary — e.g. a
  `NOT NULL` above a `CHECK`), the batch is unordered →
  `MutationDiscoveryError` → spurious `ERROR`/`MUTATION_DISCOVERY_FAILED`;
  (ii) append per operator, stop at limit, sort before returning → retains
  the first `limit` in *operator-major* order, a different subset from the
  identity bound; (iii) collect-then-sort → violates A-180's retention
  bound, which the carve itself forbids for the prototype. The shipped
  precedent is none of these: `_generate_python_sites`
  (`src/assay/adapters/python.py:742-763`) keeps a **bounded worst-eviction
  heap keyed by identity** — the retained set is the `limit`
  identity-smallest candidates over the whole walk, sorted once at return.
* **Consequence if shipped:** a competent implementer follows the sentence,
  ships (i) or (ii); (i) errors real multi-operator files; (ii) passes W2's
  bounded-retention test as specified ("limit=1 … exactly one descriptor" —
  which cannot distinguish the subsets) while diverging from the Python
  precedent's semantics. Mitigating fact, measured: under `run_mutation`'s
  max+1 sentinel (`mutation.py:780-795`) any truncation ⟹ the sentinel
  refusal with `total=0`, so the *retained subset's identity* never reaches
  the artifact — the observable damage is (i)'s spurious ERROR and the
  contradiction itself.
* **Smallest fix:** replace the sentence with "mirror
  `_generate_python_sites`' bounded worst-eviction heap
  (`python.py:742-763`): each operator pattern's scan feeds one heap bounded
  at `limit`, keyed by `MutationSite.identity`, sorted by identity at
  return", and strengthen W2's test: a fixture in which operator-listing
  order and byte order disagree, asserting the returned batch is
  identity-ordered and (at `limit=1`) that the identity-smallest site is the
  one retained.

### BLOCK-5. The converse-direction ownership must be reassigned in W0, or shipped code lies about its owner forever (finding B).

* **Wrong / measurement / consequence:** §1-B above. Route (i) makes the
  P34-owned converse direction unwitnessable by P34; the carve is silent;
  `verdict.py:2399`, `verify.py:763` and `test_verdict_judgment.py:986`
  would permanently name an owner that has shipped and cannot discharge it.
* **Smallest fix:** one more W0 ruling row: the converse helper-direction
  check is reassigned to P29 with A-243's widening (same producer, same
  reasoning as §4.6), flagged for controller acceptance alongside it; add
  the three docstring touch-ups to a work item's file list (W5 or a small
  W0-adjacent sweep — see NB-1).

---

## 3. Non-blocking findings

**NB-1 — stale-reservation sweep (finding C's list).** Add a small work item
(or extend W3/W4/W5's file lists) covering: `errors.py:131,147`,
`registry.py:34-46`, `verdict.py:1547,1554`, `vocabulary.py:76`,
`runner.py:1873-1879`, `test_standalone.py:634`, `test_cli_run.py:328`, and
the conformance comments (the mechanical half is BLOCK-3). Cheap; prevents a
shipped tree whose comments contradict its behavior.

**NB-2 — the grammar fix's file list and pinned literal (finding D).** The
one-line wrapper fix at `verdict.py:2432` covers all three roles;
`verify.py`'s twin needs nothing; `tests/test_verdict_judgment.py:1011`'s
`match="carries no an R1 claim|statement-positions"` must be updated or it
silently weakens. Add `verdict.py` + `test_verdict_judgment.py` to W5's
Files line (the carve's M13 note already promises the fix; the file list
does not deliver it).

**NB-3 — W5 names a test file that does not exist.**
`tests/test_mutation_run_execution.py` (carve line 776) is absent from the
tree (`ls tests/ | grep mutation` → §6 M-R6). The real execution/claim
tests are `test_mutation_isolation.py`, `test_mutation_baseline_gate.py`,
`test_mutation_judge.py`, `test_mutation_collect.py`,
`test_mutation_executor_bound.py`. Name the real files; an implementer told
to modify a named file will otherwise create a duplicate suite.

**NB-4 — a fourth (and fifth) lexical context rule, measured.** The carve's
three §3.2 rules survived attack, but two adjacent shapes are legal
PostgreSQL whose naive mutants fail, both absent from the pinned corpus
(same evidential class as the `E'…'` rule the carve carries "because
PostgreSQL has it"):

* **`DROP NOT NULL` — the mirror of the carve's own `SET` rule, and the
  very text the carve's replacement emits.** Measured (§6 M-R3):
  `ALTER TABLE tb ALTER COLUMN c DROP NULL;` → `ERROR: syntax error at or
  near "NULL"`, exit 1; the paired control `… DROP NOT NULL;` exits 0. Any
  consumer with ALTER-based idempotent migrations has this token sequence;
  the naive rule turns it into a crash-mutant → whole-lane ERROR. Rule
  costs one clause: *`NOT NULL` immediately preceded by `DROP` ⇒ no site.*
* **plpgsql `DECLARE … NOT NULL` — imported into scope by the carve's own
  phase-2 recursion.** Measured (§6 M-R4): `DO $$ DECLARE v INT NOT NULL :=
  0; …` exits 0 (control); the mutant `DECLARE v INT NULL := 0` → `ERROR:
  syntax error at or near "NULL"`. Dollar bodies are code after phase 2, and
  a declaration's `NOT NULL` is not a column constraint. The carve handled
  the analogous body-grammar leak for `drop-trigger` (§3.2's exclusion) but
  not for `drop-not-null`. Absent from the pinned corpus (§6 M-R5), but
  `DO $$` guards are dstdns's house idiom — one future guard variable
  crashes the lane. Cheapest honest rule: *no `drop-not-null` site inside a
  dollar body between that body's leading `DECLARE` and its first `BEGIN`* —
  or record it in §8 as a known crash-mutant ceiling, explicitly.
* Related spec gap, same class: the operator table gives no rule for
  *recognising* a "bare column-level `REFERENCES` clause".
  `GRANT REFERENCES ON te TO r1` is legal (measured, exit 0, §6 M-R4) and
  the naive replacement `GRANT CHECK (true) ON …` is a syntax error
  (measured, exit 1). Zero GRANT/REVOKE-REFERENCES in the pinned corpus
  (§6 M-R2), but the discrimination rule (e.g. `REFERENCES` must be
  followed by an identifier that is not `ON`) belongs in §3.2. Also
  unspecified and worth one line each: `UNIQUE USING INDEX` /
  `UNIQUE … INCLUDE (…)` / `NULLS NOT DISTINCT` (all zero in the corpus,
  §6 M-R2; naive `drop-unique` spans produce syntax errors for all three),
  and `WITH CHECK OPTION` / `CREATE POLICY … WITH CHECK` for `drop-check`
  (zero in corpus).

**NB-5 — O8's "byte-identical" is testable, but only with pins the carve
does not name.** A verdict's required fields include `started`, `ended`,
`commit`, `assay_version` (`src/assay/schemas/verdict.schema.json`
`required`). The harness has everything needed — `conftest.fixed_clock`
(used throughout `test_runner_run_lane.py`), pinned
`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` precedent (`tests/test_isolation.py:
77-78`) making fixture-repo OIDs deterministic, and `assay_version` threaded
as a parameter (`runner.py:748`) — but W5/O8 must *say* so, and must say the
frozen pre-P34 capture is generated at `bdc3dc78` with the identical pinned
harness before W5 lands (a carve-asset step no work item lists). Without the
pins the test cannot be written; with silent normalization it stops being
byte-identical. Alternative that avoids the frozen capture entirely: assert
`_classify_mutant_result`'s successor is the identity function of exit
outcome when no `equivalence_artifact` is declared, plus one pinned
end-to-end equality run.

**NB-6 — obligation 2's failure mode silently resurrects the false
`survived` the requirement exists to kill.** If the consumer omits
`--restrict-key` (or dumps non-reproducibly), every artifact ≠ baseline, the
`equivalent` bucket is permanently empty, and the residue case (§9 M9) is
recorded `survived` again — now *certified* by the machinery that claims to
prevent it — and nothing goes red (the carve admits this at obligation 2;
O5 catches it only inside W9's harness, not in a consumer's lane). assay
cannot check it (it sees one baseline run), but the *command* can: the
worked example in CONSUMERS.md should dump twice and `cmp` before running
tests, turning obligation 2 into a red-on-violation property of the
consumer's own gate. One sentence in W5's docs item.

**NB-7 — §3.6's baseline-artifact location is misattributed.** "exactly
where `evaluate_r1` already consumes the coverage artifact" (line 439):
consumption actually happens in `_execute_snapshot_unit`
(`runner.py:1294-1341`, reserve → tracked-check → arm → execute → consume);
`evaluate_r1` receives the already-parsed profile. On an R0,R2 SQL lane
`evaluate_r1` never runs at all — but `_execute_snapshot_unit` runs
unconditionally inside the baseline snapshot (`runner.py:1506-1517`), so the
mechanism is sound; only the pointer is wrong. Name
`_execute_snapshot_unit` (add a `wants_equivalence` parameter beside
`wants_coverage`) so the implementer lands in the right function.

**NB-8 — W9's witness discipline names the wrong precedent.**
`qualify_cmru_b006a.py` performs **no complete-artifact comparison** — it
asserts field-level properties (outcome, one killed identity, canary
reason, snapshot_policy; `gate/python/qualify_cmru_b006a.py:529-570`) and
its committed suite stubs exactly its named subprocess seams
(`tests/test_gate_qualify_cmru_b006a.py:13-14` — so W9's stubbing phrase
itself is fine). The complete-artifact witness discipline W9 actually needs
lives in `qualify_topos.py:784-837`: `normalize_artifact` **validates each
volatile field against an independently-known value, then substitutes a
placeholder** (`@STARTED@`, `@HEAD_OID@`, `@ASSAY_VERSION@`, …) before
comparing the complete document — which is how a from-a-real-run witness
(A-274) coexists with `started`/`ended`/`commit` the real CLI cannot pin.
Following cmru "exactly" produces either a decorative witness JSON
(A-278's empty gate) or an unreproducible byte comparison. One sentence:
"witness comparison follows `qualify_topos.compare_complete_artifact`'s
validate-then-placeholder discipline."

**NB-9 — `FAIL`+present+`=` absorbs flaky-suite signal.** Under the
corrected order this row (reachable only when the suite fails against a
byte-identical schema after having passed baseline) is classified
`equivalent`. All-equivalent renders loud `INCONCLUSIVE`; a *mixed* run
silently converts a flaky failure into an inert-mutant datum. Acceptable —
it is a refusal to claim — but CONSUMERS.md's classification-table entry
should name flakiness as this row's real-world cause.

---

## 4. What I tried to break and could not

* **§5.2 / M18 (zero schema surface).** Controller-confirmed; I additionally
  verified `$defs/mutation_operator` carries the per-language closed
  `oneOf` with all seven `sql:*` names and the cross-language rule's
  documented placement (§6 M-R8). Holds.
* **The A-242 sentence.** `GoAdapter.statement_spans` really returns `None`
  (`adapters/go.py:521-522`); SQL raising instead is coherent with
  `verify.py:1285`'s `_BASELINE_NEVER_READ` precedent, which exists as
  claimed. Holds.
* **§3.7's refusal table rows 1–2.** `registry.get_adapter` refuses unknown
  language and unwired rigor with `ERROR`/`BAD_LANE_CONFIG`
  (`registry.py:140-156`), and M20's quoted message matches the shipped
  f-string exactly. `RegistryEntry(rigor=frozenset({"R2"}))` is expressible
  and validated (`registry.py:78-91`). Holds.
* **W4 feasibility.** `_load_mutation(value, where, language)` already
  receives the lane's language (`config.py:1449`, caller `:1234`), and the
  per-language operator cross-check already lives there (`:1523-1542`);
  the coverage-artifact path validator with `is_relative_to` containment
  exists to reuse (`config.py:1429`). The language-scoped unreservation is
  as small as the carve claims. Holds.
* **§3.6's "why stale bytes cannot leak".** `arm()` unlinks the pre-run
  regular file; `consume()` returns `None` for a missing output
  (`safeio.py:193,225,249,252`). Holds — and is what makes finding A
  watertight rather than merely likely.
* **M19 / the kill-signal model rule.** `verify.py:733-754` enforces both
  directions (declared ⇒ every kill signalled; unattributed ⇒ no signal
  anywhere). §3.6's "signal missing ⇒ crashed" is exactly what keeps the
  producer inside the model. Holds.
* **W7's premise.** Exactly five derived vocabularies exist today
  (`test_docs_examples_and_vocabulary.py:288-345`), `MUTATION_OPERATORS`
  is not among them, and the anti-placeholder test named by the carve is
  real (`:336`). M21 holds.
* **The named-NOT-NULL attack.** `CREATE TABLE td(c INT CONSTRAINT c_nn2
  NULL)` **applies cleanly** on PostgreSQL 18 (§6 M-R4) — so PG-17+ named
  not-null constraints do *not* produce invalid mutants and need no rule.
  Attack fizzled; recorded so the implementer does not add a needless rule.
* **`judge_mutation`'s widened shape.** The `equivalent` bucket, sentinel,
  and `ALL_MUTANTS_EQUIVALENT` branch already exist in the core
  (`mutation.py:957-978`, `verdict.py:1173-1226,2770-2782`); §3.6 genuinely
  is "call sites, not mechanism".
* **The A-243/§4.6 reassignment reasoning.** A-243's own text (decisions.md
  row) confirms the sub-case was assigned to P34's carve conditionally on
  real SQL runs; route (i) removes those runs; the flag-for-controller
  treatment is the honest shape. The *widening* half of §4.6 survives review
  — only the converse direction is missing from it (BLOCK-5).
* **M17's pin.** Controller-confirmed; my corpus measurements below were
  taken against `151cda0d` explicitly and agree with M3's line-99/327/332
  facts.

---

## 5. Measurements

All commands run 2026-08-18 in the foreground, cwd
`/workspaces/vbpub/.worktrees/assay-P34-sql-adapter/assay` unless noted.
Outputs redirected to files under `/tmp/p34-review/` and exit codes captured
directly (never through a pager pipe).

**M-R1 — P34-named obligations in the shipped tree.**
```
$ grep -rn "P34" src/
src/assay/verify.py:763:    bytes, so implementing it here would be vacuous; P34 owns it, with the
src/assay/runner.py:1875:    ``kill_signal_artifact`` until P34 ships a producer for the values it
src/assay/config.py:265/269/1454/1463/1465/1469: (reserved-fields block)
src/assay/verdict.py:1547/1554: NOT declarable in lane config until P34 ...
src/assay/verdict.py:2399:        implementation of it would be vacuous. P34 owns that direction, with
src/assay/vocabulary.py:76:#:   but unproducible here; P34 lands the adapter.
$ grep -n "converse" nyxloom-trove/W3-CARVE-P34-sql-adapter.md
(no output — zero hits)
$ grep -rn "P34" tests/ docs/ README.md   # (files:) test_verdict_judgment.py:451,986;
  test_verdict_mutation_artifacts.py:79; test_verdict_conformance.py:188-190;
  test_cli_run.py:328; test_standalone.py:634; test_lane_schema_v2_locked_successors.py:350-377
```

**M-R2 — pinned-corpus shape census (read-only git, `/workspaces/dstdns`,
rev `151cda0d…`).**
```
$ git grep -c -iE "<pat>" 151cda0d -- 'infra/db-init/init-scripts/*.sql'
GRANT: 21 hits in 7 files      REVOKE: 0        WITH CHECK OPTION: 0
CREATE POLICY: 0               NULLS NOT DISTINCT: 0     CREATE DOMAIN: 0
USING INDEX: 0                 INCLUDE(: 0      CONSTRAINT x NOT NULL: 0
EXCLUDE USING: 0               DROP NOT NULL: 0          CREATE RULE: 0
ALTER TABLE...NOT NULL / ADD COLUMN...NOT NULL: 0
```
Every GRANT is `GRANT EXECUTE`/`GRANT SELECT` (lines shown in transcript);
**zero `GRANT REFERENCES`** in the corpus.

**M-R3 — the `DROP NOT NULL` mirror rule, on real PostgreSQL 18
(`postgres:18-alpine`, container `p34review`, `--network none`, removed
after use).**
```
exit=0 :: CREATE TABLE ta(c INT NOT NULL);
exit=0 :: ALTER TABLE ta ALTER COLUMN c DROP NOT NULL;        <- control
exit=0 :: CREATE TABLE tb(c INT NOT NULL);
exit=1 :: ALTER TABLE tb ALTER COLUMN c DROP NULL;            <- naive mutant
ERROR:  syntax error at or near "NULL"
```

**M-R4 — plpgsql DECLARE, named NOT NULL, GRANT REFERENCES.**
```
exit=0 :: DO $$ DECLARE v INT NOT NULL := 0; BEGIN PERFORM 1; END $$;   <- control
exit=1 :: DO $$ DECLARE v INT NULL := 0; BEGIN PERFORM 1; END $$;
ERROR:  syntax error at or near "NULL"
exit=0 :: CREATE TABLE tc(c INT CONSTRAINT c_nn NOT NULL);              <- control
exit=0 :: CREATE TABLE td(c INT CONSTRAINT c_nn2 NULL);                 <- attack FIZZLED: legal
exit=0 :: CREATE TABLE te(a INT); / CREATE ROLE r1;
exit=0 :: GRANT REFERENCES ON te TO r1;                                 <- real PG shape
exit=1 :: GRANT CHECK (true) ON te TO r1;
ERROR:  syntax error at or near "CHECK"
```
Container removed: `docker rm -f p34review` → `rm_exit=0`,
`docker ps -a | grep -c p34review` → `0`.

**M-R5 — corpus absence of the M-R3/M-R4 shapes** — included in M-R2's
census (all zero). So both new rules carry the same evidential status as the
carve's own `E'…'` rule: PostgreSQL's contract, not a consumer's measured
need.

**M-R6 — W5's nonexistent test file.**
```
$ ls tests/ | grep -E "mutation"
test_config_mutation.py  test_mutation_argv_fidelity.py  test_mutation_baseline_gate.py
test_mutation_collect.py test_mutation_executor_bound.py test_mutation_isolation.py
test_mutation_judge.py   test_mutation_operator_filter.py test_mutation_python_pipeline.py
test_mutation_resolve_targets.py test_mutation_target.py test_mutation_type.py
test_runner_assemble_verdict_mutation.py test_verdict_mutation_artifacts.py
test_verdict_mutation_payload.py
```
`test_mutation_run_execution.py` (carve line 776) is not in the tree.

**M-R7 — the pinned consumer script writes no artifact and refuses §3.4's
argv.**
```
$ git -C /workspaces/dstdns cat-file blob 88de912d52d5552a23630f68871d4f12e2a9eb83
...
WORKTREE_ARG="${1:?usage: schema-gate.sh <worktree-path> [pytest-target]}"
...
if ! docker exec ... "$CID" bash /tmp/schema-apply.sh /tmp/init-scripts; then ... exit 2
...
"$MOUNT_ROOT/scripts/testing-exec.sh" \
    "cd $WORKTREE_REL && SCHEMA_GATE_DSN='$DSN' ... pytest $PYTEST_TARGET -q"
```
Full text grepped for `dump`: zero occurrences of `pg_dump` or any artifact
write; mandatory `$1`; depends on `MOUNT_ROOT` (main checkout),
`config_helper.py env`, docker, and the app network.

**M-R8 — shipped-code verifications backing §4.**
```
verdict.py:2419-2432   requirement table + f"carries no {what}"        (finding D)
verify.py:794-797      twin message, differently worded, grammatical    (finding D)
tests/test_verdict_judgment.py:1011  match="carries no an R1 claim|statement-positions"
mutation.py:255-259    identity = (start_byte, end_byte, sha, operator) (BLOCK-4)
mutation.py:516-521    identity-order refusal in _validate_sites        (BLOCK-4)
adapters/python.py:742-763  bounded worst-eviction heap precedent       (BLOCK-4)
mutation.py:780-795    max+1 sentinel; truncation => total=0            (BLOCK-4 mitigation)
mutation.py:964-978    judge_mutation precedence: crashed > ... > PASS  (finding A)
mutation.py:774        baseline non-PASS => no mutants                  (row-5 honesty)
runner.py:1506-1517    _execute_snapshot_unit runs unconditionally      (NB-7)
runner.py:1294-1341    reserve -> tracked-check -> arm -> execute -> consume
safeio.py:193,225,249,252  arm() unlinks; consume() -> None if missing  (finding A)
registry.py:140-156    two BAD_LANE_CONFIG refusals, M20's exact message
config.py:1449,1234,1523-1542  _load_mutation already receives language (W4)
config.py:1429         resolved_artifact.is_relative_to(project_root)   (§3.4 validator reuse)
verify.py:1285         _BASELINE_NEVER_READ exists                      (§3.3 precedent)
adapters/go.py:521-522 statement_spans returns None                     (A-242 sentence)
cli.py:100             "This build evaluates R0, Python R1, ..."        (W6)
tests/test_docs_examples_and_vocabulary.py:288-345  five derived sets   (W7/M21)
tests/test_verdict_conformance.py:180-193,267-279   exclusions + audit  (BLOCK-3)
gate/python/qualify_topos.py:784-837  normalize_artifact placeholders   (NB-8)
gate/python/qualify_cmru_b006a.py:529-570  field-level checks only      (NB-8)
tests/conftest.py fixed_clock; tests/test_isolation.py:77-78 pinned GIT dates;
runner.py:748 assay_version threaded                                    (NB-5)
$ python3 -c "...verdict.schema.json...['$defs']['mutation_operator']"
  -> per-language closed oneOf, description as quoted in M18
```
