# assay B033/B034 — remediation — REPORT

**Branch:** `fix/assay-b033-b034-sql-mutation-operators`
**Base:** `main` at `8de40fa2` (assay-v2.4.1)
**Filings:** B033, B034 in `nyxloom-trove/4-backlog.md`, from
`reports/assay-review-gap-audit-2026-08-25.md` §5 (`ba8908d6`) and §1
(`126ef577`/`6324548d`), both shipped in assay-v2.3.0.
**Decisions recorded:** A-325 (B033), A-326 (B034). New filing: B035.

Every "before" transcript below was produced by the **released** CLI
(`assay 2.4.1`, `/home/vscode/.venv/bin/assay`), which is byte-for-byte the
code on `main` at the branch point — not by reading the diff. Every "after"
transcript was produced by the worktree installed into a throwaway venv
(`assay 2.4.2.dev0+g8de40fa2`). Both fixtures are real Git repositories with
real commits; no mocks.

---

## B033 — SQL whole-target R2

### Fixture

A real repo with a whole-target SQL lane:

```toml
[lanes.sqllane.judge]
language = "sql"
source_roots = ["db"]
mode = "whole_target"
base = "origin/main"
targets = ["db/schema.sql", "db/tests/fixtures.sql"]
```

`db/schema.sql` carries a `NOT NULL` and a `CHECK (n > 0)`;
`db/tests/fixtures.sql` is a real, existing `.sql` file that the SQL adapter's
own `is_test_path` rejects (it sits under `db/tests/`). The lane's command
copies the schema to the declared equivalence artifact and greps for both
constraints, so `sql:drop-not-null` and `sql:drop-check` mutants are killed.

### (a) + (c) BEFORE — `assay 2.4.1`

```
$ assay run sqllane --verdict-json before.json
sqllane: PASS (exit 0)

resolved: {"base": "02655f84cf54cad0cd4fd30ff0cce748d31ee94f",
           "base_resolution": "merge-base",
           "language": "sql", "source_roots": ["db"]}
R2 status PASS total 2 candidates 2
   ('killed', 'db/schema.sql', 'sql:drop-check')
   ('killed', 'db/schema.sql', 'sql:drop-not-null')
any mention of fixtures.sql in verdict? False
```

Both defects in one artifact: a resolved `base`/`base_resolution` on a run
that never compared against a base (whole-target R2 skips `check_base_is_head`
and the `git diff` outright), and a clean `PASS` over a target set silently
narrowed from two declared entries to one — with nothing anywhere in the
verdict naming the dropped entry.

### (b) BEFORE — the SQL carve-out, `assay 2.4.1`

Deleting the single line `mode = "whole_target"` from the same lane:

```
$ assay run sqllane --verdict-json before-nomode.json
sqllane: NO_MEASUREMENT/BASE_IS_HEAD (exit 3)
```

The lane **loads clean**. `judge.targets` is still declared, still reaches the
artifact through `JudgeConfig.as_declared()`, and enforces nothing — the run
silently routes to the diff path. For every language except the one that
actually uses this feature, the same edit is refused at load with the message
"a target list under changed-line mode does nothing and silently declaring one
is how a consumer comes to believe a floor is enforced when it is not."

### AFTER — the fixed CLI

Same lane, unmodified:

```
$ assay run sqllane
assay: ERROR/BAD_LANE_CONFIG: assay.toml: lane 'sqllane': declares rigor
['R0', 'R2'], which reads none of judge.{base} -- so that configuration is
inert and cannot fail loudly if it is wrong. Either declare the rigor level
that consumes it, or delete it.
exit=2
```

`judge.base` is now refused as inert config on a whole-target lane (A-325(c)).
Deleting that one line and re-running, with the out-of-scope target still
declared:

```
$ assay run sqllane
assay: lane sqllane: R2 whole-target resolution refused: mutation target
'db/tests/fixtures.sql' is a test path per the adapter's own convention
sqllane: ERROR/BAD_LANE_CONFIG (exit 2)
exit=2
```

Refused, naming the target and the gate — the refusal cause goes to the
caller's diagnostics stream (A-322's channel), because `judgment.r2` carries no
`targets` field and gains none here. A target absent at the judged commit is
named too, where before it produced an unnamed `GIT_FAILED` from one layer
down:

```
$ assay run sqllane        # targets = ["db/schema.sql", "db/missing.sql"]
assay: lane sqllane: R2 whole-target resolution refused: mutation target
'db/missing.sql' does not exist as a regular file at the judged commit (looked
for 'db/missing.sql' inside the snapshot); a whole-target entry is always a
regular file, never a directory and never absent
sqllane: ERROR/BAD_LANE_CONFIG (exit 2)
```

With only in-scope targets declared and no `base`:

```
$ assay run sqllane --verdict-json after3.json
sqllane: PASS (exit 0)
resolved: {"language": "sql", "source_roots": ["db"]}
R2 PASS total 2 killed [('db/schema.sql', 'sql:drop-not-null'),
                        ('db/schema.sql', 'sql:drop-check')]
$ assay verify after3.json ; echo $?
0
```

`base`/`base_resolution` are gone; the artifact records exactly what was
judged. `assay verify` accepts it — which it did **not** at the first attempt:
the model (`Judgment.__post_init__`), the raw verifier
(`verify._check_base_matches_the_tiers_present`) and the packaged JSON Schema
each carried an independent copy of the same false rule ("r2 always compares a
base"), and all three had to be corrected in step. That is the fourth
independent registration point for one wire rule, and it is worth recording as
evidence about this codebase's shape, not just about this fix.

### Schema-version question, answered rather than assumed

The `base` conditional had to be relaxed in `src/assay/schemas/verdict.schema.json`
(and its frozen twin `nyxloom-trove/carve-assets/W2/verdict.schema.v7.json`,
kept byte-identical). Applying A-324's own test: this is a pure **widening** —
every document that validated under the old v7 still validates, and no released
`assay verify` rejects anything it previously accepted. Documents become valid
that were not before — including the whole-target R2 artifacts that could not
be honestly produced at all under the old rule, and, unavoidably, some
malformed ones (measured below, "The cost of that, stated plainly").
**No version bump on that basis.**

The narrowing half is now asserted only where the artifact can witness it.
`judgment.r1.mode` is on the wire; `judgment.r2` records neither `mode` nor
`targets`, so an `R0,R2` document (every SQL lane) cannot say whether its R2
diffed or mutated whole files. Rather than assert a rule the document cannot
witness — which is exactly how the old rule came to reject honest artifacts —
the gap is left open and **filed as B035**, whose fix is a v8 field.

### The cost of that, stated plainly (round-2 review, finding 4)

"A pure widening" is true, and it is only half the story. Widening a verifier
means it **stops rejecting things**, and some of what it stops rejecting is
genuinely malformed. Both directions, measured on the same two builds:

```
# the honest whole-target R2 artifact this wave exists to make producible
$ assay verify after3.json          # released 2.4.1
assay verify: judgment.resolved omits base while judgment records r2, or r1
in changed-line mode, ...
exit=1
$ assay verify after3.json          # fixed build
exit=0                                                    <- the intended gain

# a DIFF-based R0,R2 artifact with its base deleted -- genuinely malformed
$ assay verify diffr2-nobase.json   # released 2.4.1
exit=1
$ assay verify diffr2-nobase.json   # fixed build
exit=0                                                    <- the cost
```

So: **for `R0,R2` documents the verifier is measurably weaker than 2.4.1 was.**
A diff-based R2 that omits the base it was scoped against used to be caught and
now is not. That is not a side effect to be discovered later — it is the direct,
unavoidable price of B033(a), because one rule cannot both require and forbid
the same field for two shapes the artifact does not distinguish. The old rule
bought that strictness by making honest whole-target R2 artifacts impossible to
emit, which is the worse failure: a verifier that rejects the truth teaches its
consumers to stop believing it.

Lanes declaring R1 are unaffected — `judgment.r1.mode` witnesses the lane's
scope, so both halves are still enforced there, and the whole-target/base
contradiction is now caught for `R1,R2` lanes where the old rule wrongly
demanded a base.

**This raises B035's priority rather than merely deferring it**, and the reason
is concrete: dstdns's `cw2b_schema` — the only R2 lane it has, and the only
known consumer of the whole-target R2 path — is exactly the `R0,R2` shape that
lost enforcement. The window lasts until `judgment.r2` can record its own
scope. B035 should ride the next schema-version bump, and this paragraph is the
argument for not letting that bump drift.

---

## B034 — the two "semantic" Python mutation operators

### BEFORE — `assay 2.4.1`, real R2 lane, real repo

A Python `R0,R2` lane declaring
`["python:compare-swap", "python:uuid-equality-swap", "python:enum-comparison-swap"]`
against a one-line change `if cfg.flag == True:` (no UUID, no enum anywhere in
the source):

```
$ assay run pylane --verdict-json before.json
pylane: PASS (exit 0)

total: 2   candidate_count: 2
  killed  line 2 span=(28,30) op=python:compare-swap
          sha=c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2
  killed  line 2 span=(28,30) op=python:enum-comparison-swap
          sha=c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2
$ assay verify before.json ; echo $?
0
```

**One** distinct mutation — identical byte span, identical replacement digest —
reported as `total: 2`, executed twice, attributed to two operator families,
and accepted by the shipped verifier. The comparison contains no enum: the
predicate `_is_enum_member_expression` was `Attribute(value=Name)` and nothing
more, so it matched `cfg.flag`, `self.count`, `path.suffix` — any dotted
attribute access.

That last `verify` exit code is also the **load-bearing fact for the
version-bump question** (A-324's test): a released `assay verify` **does**
accept a v7 document naming a withdrawn operator, and a released `assay run`
**did** emit one. This is the opposite of A-320's `progress_artifact` finding,
and it is why the two names keep their spelling.

### AFTER — the fixed CLI

The same lane is refused at load:

```
$ assay run pylane
assay: ERROR/BAD_LANE_CONFIG: assay.toml: lane 'pylane':
'judge.mutation.operators' names withdrawn operator(s):
python:enum-comparison-swap, python:uuid-equality-swap. Every site they ever
produced was already produced by python:compare-swap at the same byte span
with the same replacement, so declaring both emitted each shared site twice
and added no coverage. Delete them; python:compare-swap already covers ==/!=
swapping
exit=2
```

With the two names deleted from the lane, the same change yields one mutation,
not two:

```
$ assay run pylane --verdict-json after.json
pylane: PASS (exit 0)
total: 1  candidate_count: 1
  killed  line 2 span=(28,30) op=python:compare-swap  sha=c10987bd7cf8…
```

And the pre-fix artifact still verifies against the fixed build, which is the
whole point of keeping the spelling:

```
$ assay verify before.json ; echo $?     # the 2.4.1-produced document above
0
```

### Redesign vs. withdraw — the call, and why

Withdrawn. The reasoning is recorded in full at A-326; the short form:

* The overlap is **total**, not partial (87 of 87 sites over `src/assay/**.py`,
  byte-identical down to `replacement_sha256`), so neither de-duplication nor
  predicate-tightening can produce new coverage. That is the backlog's own bar.
* A genuinely distinct UUID rule **does** exist in the abstract — splice
  `==`→`is` instead of `==`→`!=`. `compare-swap` never maps `Eq` to `Is`, and
  an in-place UUID construction makes the identity comparison provably `False`,
  so the mutant is strictly harder to kill and carries information `Eq→NotEq`
  does not. It was rejected anyway, on this project's own governance: it is new
  mutation-testing design, which A-112 forbids in as many words ("this package
  reuses ONLY the catalogue, never extends it") and which A-221 already invoked
  once to exclude a Go `falsy-swap` analogue as "new, unmeasured
  mutation-testing design". It would also make an operator named
  `uuid-equality-swap` stop swapping equality — a rename, which the vocabulary
  module's own docstring says is a version bump, not a widening.
* The **enum** half has no redesign at all. `is` on an enum member is an
  equivalent mutant (members are singletons), a `.value` splice turns every
  false positive into a *crashed* mutant rather than a survived one, and no
  single-file AST can tell an enum member from `cfg.debug` without cross-module
  resolution.
* Withdrawal is therefore the honest outcome, and it was not forced: the
  redesign was worked through to a concrete candidate rule before being
  rejected on a named, pre-existing rule.

**Behaviour withdrawn now; spelling withdrawn at the next schema bump.** The
vocabulary is the schema's source (`test_verdict_schema_is_packaged.py` asserts
membership AND order), so deleting the names deletes them from the packaged
`oneOf`, and real artifacts already on disk would stop verifying. Bumping
v7→v8 to withdraw two dead operators would invalidate **every** v7 artifact — a
far larger break than the one it prevents — and would drag the frozen
`carve-assets/W2` acceptance assets into a restructuring that belongs to a
carve, not a bugfix wave.

---

## Test suite

Pre-existing environment failures in the scratch venv used for iteration (they
fail identically on the unmodified tree, verified by `git stash`): the
`test_canary_python_pipeline*`, `test_runner_run_lane_r3`, `test_standalone`
and `test_distribution_build_release` groups, which need toolchain state the
throwaway venv does not have. The **registered gate** builds its own two-venv
wheel install and is the authority; its transcript is below.

## Registered gate

`bash tools/tester-unified-gate.sh ..` from the assay project root, exit code
read in a separate step.

Run at `40127f76`, the branch tip. The gate builds its own wheel from an
exact-OID, no-local sparse clone of that commit and self-hosts through it, so
the throwaway-venv failures above are not in scope for it.

```
$ bash tools/tester-unified-gate.sh ..
...
Successfully built assay
Processing ./tmp/tmp.mCqw9jRNTc/dist/assay-2.4.2.dev4+g40127f76-py3-none-any.whl
Installing collected packages: assay
Successfully installed assay-2.4.2.dev4+g40127f76
ASSAY_GATE_PHASE=wheel-installed
25 passed, 16 deselected in 1.46s
ASSAY_GATE_PHASE=attestation-hardened
13 passed, 31 deselected in 18.07s
ASSAY_GATE_PHASE=verdict-v5-accepted
17 passed in 0.81s
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
v6 hard-cut guard passed for 6 frozen templates
ASSAY_GATE_PHASE=verdict-v6-successors-verified
23 passed in 0.73s
ASSAY_GATE_PHASE=verdict-v7-successors-verified
tester-unified: PASS (exit 0)
  commit: 40127f76bec681d268c1e68508817564c195efdc
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py \
        --override-ini=pythonpath=
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
--- B006(a) WI-5 qualification receipt ---
ASSAY_GATE_PHASE=cmru-b006a-qualified
7 passed in 10.41s
ASSAY_GATE_PHASE=independent-self-hosting-passed
```

Exit code read in a separate step, never from a pipe tail:

```
$ grep "GATE_EXIT=" gate3.txt
GATE_EXIT=0
```

Two earlier gate attempts were aborted before they could produce a verdict,
both for the same self-inflicted reason and neither for a code failure: an
uncommitted file (first the LOG, then a `docs/CONSUMERS.md` addition) left the
worktree dirty, and the self-hosted lane correctly refused
`NO_MEASUREMENT`/`DIRTY_TREE`. The transcript above is the run on a clean tree.

## Consumer notification owed

`dstdns`'s `cw2b_schema` lane (`/workspaces/dstdns/assay.toml`) declares
`mode = "whole_target"` **and** `base = "origin/main"`, and will be refused at
load by this build until that one line is deleted. It is the only known
consumer of the whole-target R2 path. This belongs in the next
assay→dstdns release-notify alongside A-324's own outstanding cross-reference.

