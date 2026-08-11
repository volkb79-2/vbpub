# assay-P33 — mandatory pre-dispatch adversarial specification review, ROUND 6

**Verdict: NOT READY.**

- Reviewer: fresh Opus xhigh child forked from `CR-opus-0` (package-neutral
  orientation base, built at `016863a4792e2cc6c3ef1eb472cb91314f109cb2`).
- Repository state reviewed: `git rev-parse HEAD` =
  `6a7f9764aabc4660a726e50166b0ec0c8ef91013`, working tree clean.
- Handoff anchor under review: `input_revision: cb5ceeab6306b2dc17613762b2530e67224a2f7a`.
- Method: every numeric or behavioural claim below that is marked VERIFIED was
  re-run by me in this session and its real output is pasted. Runs are from the
  interactive cockpit and are therefore **diagnostic, not a ship signal**
  (AGENTS.md, AUTHORING §4); they are used here only to check carve claims that
  the carver itself made from the same environment.
- The AUTHORING prompt applied is quoted verbatim in §7, read directly from
  `nyxloom/reference/AUTHORING.md` (blob `24460b57d015e44f0f1463e2a0393b09bdafaf40`).
  I diffed it against the copy supplied in my instructions: identical.

---

## 0. Headline

The procedural remedy (A-232, pasted output rather than asserted counts) **worked
where it was applied and did not hold where it was not.** Six of the seven claimed
repairs verify exactly as stated. The failure is concentrated in the one artifact
the carver itself named as least-trusted — and it is worse than the carver
feared:

> *"What I would attack first: `test_gate_script_wiring_is_exactly_what_the_handoff_claims`
> now spawns a real pytest collection... if collection errors for an unrelated
> reason, the test reports a wiring defect that isn't there. It is the newest
> thing here and the one I trust least."* — JIT-CARVE, Disposition

That concern is correct, and it is not hypothetical: **the oracle cannot pass
against any correctly-wired gate script**, because it harvests `-m pytest` out of
the gate's own `python -m pytest` command lines and feeds it back to pytest as a
marker filter. Simultaneously it **admits both of the attacks it was rewritten to
close.** The reason none of this was caught is structural and worth naming: an
earlier assertion in the same test short-circuits before the collection code is
ever reached, so the carver's own run never executed the new code at all. The red
was classified as "gate not yet wired" — true, but it concealed that the new
oracle is unexercised.

That is the same shape as round 5 (a fix claimed but never re-run), one layer in:
a *new* proof mechanism whose first execution is deferred to the implementer.

---

## 1. What I ran, with real output

### 1.1 Locked acceptance suite — claim 31 failed / 13 passed: **VERIFIED**

```
$ cd /workspaces/vbpub/assay
$ PYTHONPATH=src python3 -m pytest nyxloom-trove/carve-assets/P33/test_acceptance_v5.py -q -p no:randomly
...
31 failed, 13 passed, 1 warning in 11.50s
```

Zero `AttributeError`, zero `TypeError`, zero fixture errors, zero collection
errors — **VERIFIED** by parsing the full output:

```
AttributeError: 0
fixture '...' not found: 0
ERROR blocks: 0
```

### 1.2 Independent classification of all 31 reds — carver's table is INCOMPLETE

I parsed every failure block and assigned a cause from the failing assertion
text, then compared with the JIT-CARVE table at line 649.

```
blocks: 31
   24  A. v4 verifier rejects v5 doc
    2  C. refusal message lacks P34
    1  B. schema $id still v4
    1  B. schema bytes still v4
    1  D. gate not wired
    1  *** E. v4 doc ACCEPTED by v4 verifier   (not in carver's table)
    1  *** F. vocabulary not renamed           (not in carver's table)
UNCLASSIFIED: []
total classified: 31
```

The carver's table names four causes summing to **29**. Two reds fall outside all
four:

| test | cause | in carver's table? |
|---|---|---|
| `test_a_v4_artifact_is_refused_for_its_version` | today's verifier *is* v4, so a v4 doc validates clean — the **inverse** direction of cause A | no |
| `test_config_accepts_a_matching_language_operator` | `python:compare-swap` rejected by the v4 vocabulary (work item 4) | no |

Both are **LEGITIMATE** (green after work items 1–3 and 4 respectively), so the
carver's *conclusion* survives my check. The *classification* does not. The
arithmetic is the tell — and the mechanism is that the table counts `grep -E "^E "`
**output lines**, not failing tests, while A-232 requires classifying "each
pre-implementation red". Cause F is visible in the carver's own R5-B1 section
(`python:compare-swap (control; green only under v5)`) and then absent from the
classification table eight lines later.

A-232's own text carries the same overclaim: *"this round's 31 reds trace to
exactly four causes"*.

### 1.3 Sweep — claim 27 = 24 direct + 1 argv + 2 environ: **VERIFIED**

```
$ python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py --json
total consumers: 27
Counter({'direct': 24, 'indirect-path-from-environ': 2, 'indirect-path-from-argv': 1})
```

`src/assay/verify.py` correctly **absent**: `True`.

### 1.4 `P20/test_acceptance.py` found by the location rule, not text: **VERIFIED**

```
P20 reads_frozen_tree(text) -> []            <- names no frozen tree: text matching cannot find it
P20 lives_in_frozen_tree(f)  -> nyxloom-trove/carve-assets
P20 matches __file__ ?       -> True
=> found ONLY via location rule? True

P20 actual expectation-loading lines:
  26: ASSET_ROOT = Path(__file__).resolve().parent
  350: (ASSET_ROOT / "expected" / "post-dirty-v3.json").read_text(encoding="utf-8")
```

A-233's location rule is real and does the work claimed of it.

### 1.5 Migration transform — claim `--check` exits 0: **VERIFIED**

```
$ python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check
OK: committed v5 asset matches the transform exactly
EXIT=0
```

### 1.6 Manifest arithmetic: **VERIFIED**

```
counts block: {"locked_carver_owned": 23, "implementer_owned": 104, "carver_owned_prose_excluded": 19}
  bucket locked_carver_owned:        len=23   CANONICAL=23   OK
  bucket implementer_owned:          len=104  CANONICAL=104  OK
  bucket carver_owned_prose_excluded: len=19  CANONICAL=19   OK
```

---

## 2. Findings on each claimed fix

### R5-B1 (config fixture) — **VERIFIED FIXED**

```
$ PYTHONPATH=src python3 -m pytest ...test_acceptance_v5.py -p no:randomly \
    -k "config_fixture_itself_loads or config_refuses_a_cross_language" -v
...::test_config_fixture_itself_loads_today PASSED [ 50%]
...::test_config_refuses_a_cross_language_operator PASSED [100%]
================= 2 passed, 42 deselected, 1 warning in 0.20s ==================
```

The "control for the controls" genuinely loads under the **current v4-shipped**
`load_lane_file`, in the v4 spelling, today. This is the single most important
repair of round 5 and it holds.

### R5-B2 (dead constant) — **VERIFIED PURGED**

`grep -rn "indirect-path-from-caller" assay/` returns hits **only** in
append-only history: the five prior review reports, the JIT-CARVE's own narrative
of rounds 2–5, and A-229's decision text. Zero hits in any live asset —
`sweep_v4_consumers.py`, `test_acceptance_v5.py`, `migration-manifest.json`,
`README.md`, or the handoff. All of those now spell it `indirect-path-from-argv`.
The contradiction is genuinely purged, not moved.

One residue worth a line: **A-229 is cited by the handoff's "Context to read
first"** and still says *"a second `indirect-path-from-caller` category"*.
`decisions.md` is append-only and A-233 supersedes it, so this is correct
practice, not a defect — but the handoff cites A-229 without noting the
supersession. Cheap to fix by adding A-233 to the citation list.

### R5-B3 (counts reference CANONICAL_COUNTS) — **PARTIALLY MET**

The manifest is internally consistent (§1.6). Two bare hand-typed counts survive
in the handoff:

- **`:365` — "Ninety files change"**, which **contradicts** `CANONICAL_COUNTS.implementer_owned = 104`.
- **`:256` — "20 of P26's 24 tests keep running"** — restated independently,
  though consistent with `p26_retained: 20` / `p26_deselected: 4`.

Work item 9 asserts *"no document restates a number independently any more."*
That claim is false as written, and one of the two restatements is wrong.

### R5-B4 (items 6/6b red today for the message) — **VERIFIED FIXED**

Both tests are red **today**, for exactly the specified reason, and the pasted
failure proves the vacuity argument was real:

```
E  assert 'P34' in "...: lane 'demo': unknown judge.mutation key(s): kill_signal_artifact;
                    expected only: jobs, max_mutants, operators"
E  assert 'P34' in "...: lane 'demo': unknown judge.mutation key(s): equivalence_artifact;
                    expected only: jobs, max_mutants, operators"
E  AssertionError: the refusal must name the field as reserved for P34, not report it as
   an unknown key -- otherwise work item 6b has no observable
```

The refusal already happens today; only the message distinguishes a correct
implementation from doing nothing. Correctly specified and correctly witnessed.

### A-230a (`helpers` minItems) — **VERIFIED FIXED, differentially**

```
ca4-all-equivalent-v5-template.json: control errors=0  helpers=[] errors=1
    HELPERS ERROR: $.helpers | [] should be non-empty
sql-r2-v5-template.json:            control errors=0  helpers=[] errors=1
    HELPERS ERROR: $.helpers | [] should be non-empty
```

Control validates clean, the injected defect produces exactly one error naming
the rule. This is the `refuses_only_the_defect` shape done properly.

### The sweep's environ supplier guard — **PARTIALLY CORRECT (one phantom consumer)**

The **real** case traces correctly. `tests/test_self_hosting.py` resolves through
a module constant to a setter that is the gate shell script, spelled bare:

```
$ grep -n "ASSAY_SELF_HOSTING_VERDICT" tools/tester-unified-gate.sh
197:  PYTHONPATH="$run_venv_site" ASSAY_SELF_HOSTING_VERDICT="$scratch/verdict.json" \
```

Sweep reports `['tools/tester-unified-gate.sh::ASSAY_SELF_HOSTING_VERDICT']`.
Correct, and genuinely hard to find. A-233's three-iteration account is accurate.

**But the guard does not implement what A-233 says it implements.** A-233:

> *"An environ-sourced consumer counts only when some **other** member of the
> gate's execution path ... actually sets that variable **to a frozen path**."*

`_env_suppliers` (`:303-325`) checks neither half. It does not exclude the file
itself (unlike `_subprocess_callers`, which does `if f == target: continue`), and
it never inspects the assigned **value**. It only requires that some file which
happens to mention a frozen tree or the word `verdict` assigns *a variable of that
name*. Consequence, verified:

```
tests/test_cgroup_parent.py
  env var names extracted: ['PATH']
  reads_frozen_tree:  []        <- names no frozen tree
  lives_in_frozen_tree: None    <- lives in none
  Is the file its OWN supplier? True

  Its only environ use, line 29:
      "PATH": f"{fake_bin}:{os.environ['PATH']}",
```

It is reported as a consumer of a frozen expectation because it reads `$PATH` and
other files assign `PATH=`. It compares no frozen expectation at all. This is a
**phantom consumer**, and `CANONICAL_COUNTS.sweep_consumers = 27` bakes it in.

Severity: this is not a false PASS on the product, but work item 8b instructs the
implementer to *"re-run it and confirm it reports no consumer you have not
addressed."* A phantom entry that needs no addressing trains the implementer to
dismiss sweep rows — which is precisely the habit that would let a real one
through. It also means the guard's own docstring overclaims, the same
documentation-ahead-of-code pattern A-230a was raised for.

---

## 3. The gate-wiring oracle — four defects, two of them false PASSes

`test_gate_script_wiring_is_exactly_what_the_handoff_claims` (`:704-764`).

I could not exercise it against a modified real gate script without editing a
repo file, so I built a **non-destructive replica** of its logic (lines 728–759
copied verbatim, only the gate *text* parameterised) and drove it with synthetic
gate scripts. Probe: `/tmp/oracle_probe.py`.

### D1 — the oracle is UNSATISFIABLE against the real gate script (blocking)

`flags = re.findall(r'(--deselect[= ]\S+|-k\s+"[^"]+"|-m\s+\S+)', gate)` — the
`-m\s+\S+` alternative matches **`-m pytest`** in every `python -m pytest` line.
The real gate spells all three of its invocations that way (`:187`, `:198`,
`:230`), and work item 8 adds two more. The harvested `-m pytest` is handed back
to pytest as a **marker expression**, which matches nothing:

```
=== A. correct wiring (baseline: should PASS) ===
  flags reconstructed: ['-m pytest', '--deselect ...', ..., '-m pytest']
  pytest returncode  : 5 (NEVER CHECKED by the oracle)
  collected count    : 0
  assert must_run in collected -> FAIL
  ORACLE VERDICT: FAIL
```

Removing only the `-m pytest` text from the same gate proves it is the cause:

```
=== A2. correct wiring, no `-m pytest` in text ===
  pytest returncode  : 0
  collected count    : 20
  ORACLE VERDICT: PASS
```

So an implementer can wire the gate **exactly** as work item 8 specifies and this
test stays red, with the message *"A-210's aggregate-bounds oracle is NOT
collected under the gate's own flags — however it was suppressed, it stopped
running (A-229)."* By A-232's own definition that is an **ILLEGITIMATE red:
unsatisfiable by any implementation.** The implementer's only correct move is
`BLOCKED` (editing a locked asset is refused by `escalate_if` and A-197/A-222).

This is also the carver's feared scenario, realised by default rather than by
accident: collection fails for a reason unrelated to wiring, and the oracle
reports a wiring defect.

### D2 — `-k` bypass is NOT closed; it is a false PASS (blocking)

Round 5's attack, re-run:

```
=== C2. -k bypass, -m poison removed ===
  flags reconstructed: [4 x --deselect ..., '-k "not test_all_structural_and_aggregate_bounds_precede_every_git_call"']
  pytest returncode  : 4 (NEVER CHECKED by the oracle)
  collected count    : 20
  assert must_run in collected -> PASS
  assert not still_collected   -> PASS
  ORACLE VERDICT: PASS
  stderr tail: ERROR: Wrong expression passed to '-k': "not test_all_structural..."
```

The gate genuinely suppresses A-210's security oracle and **the oracle says PASS.**

Mechanism: `f.split(None, 1)` on `-k "not X"` yields `['-k', '"not X"']` — the
double quotes are retained, pytest rejects the expression as malformed, and exits
4 **after** `--collect-only` has already printed the unfiltered list. Proof that
the quoting is the defect:

```
$ ... --collect-only -q <P26> -k '"not test_all_structural_..."'
41 tests collected in 0.17s                      <- quotes retained: filter not applied
$ ... --collect-only -q <P26> -k 'not test_all_structural_...'
40/41 tests collected (1 deselected) in 0.18s    <- quotes stripped: filter applied
```

The JIT-CARVE claim *"The `-k "not ..."` bypass that would have silently stopped
A-210's oracle is closed"* is false.

### D3 — flags are harvested file-wide, not scoped to the P26 invocation (blocking)

`flags` is computed over the **whole gate script**. The `line = next(...)`
expression immediately above it (`:729-733`) computes a candidate invocation line
and is then **never used** — dead code that documents the intent the code does not
carry out. Attack: put the four `--deselect` flags on the *wrong* pytest
invocation, so P26 really runs unfiltered:

```
=== B2. ATTACK: deselects on WRONG invocation ===
  pytest returncode  : 0
  collected count    : 20
  assert must_run in collected -> PASS
  assert not still_collected   -> PASS
  ORACLE VERDICT: PASS
```

The gate would run all 24 P26 tests — including the four v4-coupled ones that
redden under v5 — and O5's oracle passes. O5's whole observable is *which*
invocation carries the deselects; the oracle structurally cannot see it.

### D4 — `proc.returncode` is never inspected

Any collection failure yields empty stdout, hence an empty `collected` set. Note
the asymmetry: `assert must_run in collected` fails loudly, but
`assert not (must_not_run & collected)` **passes vacuously** on an empty set. The
carver's feared unrelated-error case, demonstrated generically:

```
$ python3 -m pytest --collect-only -q /tmp/collerr/test_sibling.py   # bad import
returncode=2
stdout has :: matches -> []
=> oracle collected set would be: set()
```

A sibling import error, a syntax error, a missing plugin, or a conftest failure
all land here.

**Minimum repair:** scope the flag scan to the P26 invocation line (revive the
dead `line`); drop the `-m` alternative or anchor it so it cannot match `-m pytest`;
`shlex.split` the reconstructed flags instead of `str.split`; and assert
`proc.returncode == 0` with the stderr in the message before interpreting
`collected`. Each of the three attacks above should then be a fixture in the
locked suite — the oracle that exists to prove non-suppression needs its own
controlled break, which is A-067 applied to the tool rather than to the product.

---

## 4. One more evidence defect: CA8 is specified but unwitnessed

Work item 8c takes CA8 — make declared and resolved `base` genuinely differ — and
it is the correct call (A-143). But the scenario is added to
`gate/python/qualify_topos.py`, which is `implementer_owned`, and **no locked
asset asserts that it exists or that the two values actually differ.** The locked
suite's only reference to that harness is a substring check for the two P25 v5
sibling filenames (`:761-764`).

So the single oracle that closes A-143's trap for `judgment.resolved.base` is
authored by the implementer, from the same handoff, with no independent
counterpart — precisely the "tests written by the implementer from the same
handoff are not independent evidence" case AUTHORING names, on the field where
this package collapses two independently-resolved values into one. Every locked
template substitutes a full 40-hex, and `resolve_base` returns a full SHA
unchanged, so an implementation that records the **declared** string passes the
entire locked suite.

Fix: add a locked assertion that `qualify_topos.py` contains a scenario whose
declared base is not 40-hex, and that the expected `judgment.resolved.base` in
its comparison is a 40-hex differing from it.

---

## 5. Non-blocking findings

| # | Finding |
|---|---|
| N1 | **Stale round count and stale pointer.** Dispatch contract `:57` says "re-carved three times, after three NOT READY pre-dispatch reviews"; `:63-65` sends the implementer to "Answering the pre-dispatch review", "Answering round 2" and "Answering round 3". The JIT-CARVE has **five** such sections (`:176`, `:274`, `:397`, `:484`, `:586`). Rounds 4 and 5 hold A-230a–d, A-232, A-233, the R5-B1 fixture rebuild and the carver's own least-trusted-artifact warning. A-103's exact class. |
| N2 | **Anchor inconsistency.** Handoff `input_revision: cb5ceeab`; `migration-manifest.json`'s `frozen_at_main: 27e3a998` (one commit earlier). I checked the impact: the only bucket-listed files changed between them are `handoffs/assay-P33-*.md` and `decisions.md`, both in `carver_owned_prose_excluded`, so the inventory is not materially stale — but two authoritative-looking anchors disagree. |
| N3 | **The freeze convention is not met literally.** A-176/A-187/A-206/A-214 freeze the packet "in the commit containing `reports/...JIT-CARVE.md`". Here the JIT-CARVE, README, handoff and STATE.md were all modified *after* the declared anchor (`081d945b`, `6a7f9764`). The **locked assets themselves are unchanged** since `cb5ceeab` — I verified `git diff --name-only cb5ceeab HEAD` touches none of them — so this is a bookkeeping deviation, not a proof-integrity one. |
| N4 | `README.md:16` carries "13 logged changes" as a bare count outside `CANONICAL_COUNTS`. |
| N5 | A-229 is cited in "Context to read first" without noting that A-233 supersedes its `indirect-path-from-caller` half. |

---

## 6. What is genuinely strong

Recording this because five NOT READY rounds should not obscure it, and because
the next round should not re-litigate it:

- The **differential-negative discipline** (`refuses_only_the_defect`) is the
  right shape and is applied consistently; I confirmed it defeats the
  version-short-circuit false pass on the two templates I drove by hand.
- **A-232 itself is a good rule** and it demonstrably worked: it caught R5-B1's
  survival, and the R5-B1/R5-B4 repairs are the best-evidenced work in the packet.
- The **location rule (A-233)** is a genuine improvement over text matching and I
  proved it is load-bearing for `P20/test_acceptance.py`.
- The **P34 message oracles** correctly identify that "it is refused" is vacuous
  and pin the only non-vacuous observable.
- **6d is honest**: declining to build a construction seam whose only purpose is
  to make an unwitnessable claim testable is the right call, and saying so
  explicitly is better than a decorative test.

---

## 7. AUTHORING's pre-dispatch adversarial specification review — applied

Prompt as read from `nyxloom/reference/AUTHORING.md` § "Pre-dispatch adversarial
handoff review", verbatim:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.

### Requirement → oracle traceability

| Work item | Oracle | Locked witness | Status |
|---|---|---|---|
| 1 schema installed verbatim | O1 | `test_shipped_schema_is_byte_identical_to_the_locked_asset`, `test_schema_identity_is_internally_consistent`, `--check` | sound |
| 2 model migration | O1 | `test_locked_v5_template_is_accepted[4]`, `test_a_v5_artifact_missing_judgment_resolved_is_refused` | sound |
| 3 verifier + 5 invariants | O1–O4 | per-clause differential negatives | sound |
| 4 operator rename | O2 | `test_config_{refuses_cross_language,accepts_a_matching}`, `test_a_cross_language_operator_is_refused` | sound |
| 5 `go:*`/`sql:*` declarable-unreachable | O2 | refusal-pinning test **specified in prose, not locked** | weak |
| 6 / 6b P34 message | O4 | two message tests, red today | sound |
| 6c `helpers` omitted | O1 | `minItems:1` + `test_helpers_is_omitted_when_no_helper_ran` | sound |
| 6d no witness claimed | O4 | — (deliberately none) | honest |
| 7 `ALL_MUTANTS_EQUIVALENT` | O3 | `test_ca4_*`, `test_ca9_*` | sound |
| 8 gate wiring | **O5** | `test_gate_script_wiring_...` | **broken (§3)** |
| 8b repoint 6 sites | O5 | substring check for 2 sibling filenames only | weak |
| 8c CA8 declared≠resolved | O1 | **none** | **unwitnessed (§4)** |
| 9 migrate manifest paths | — | `CANONICAL_COUNTS` + scope-vs-manifest test | sound |

### (1) Blocking ambiguities

- **B1.** O5's oracle is unsatisfiable against a correctly wired gate (§3 D1).
  The implementer cannot reach green without editing a locked asset, which
  `escalate_if` and A-197 forbid. Its only lawful outcome is BLOCKED.
- **B2.** Work item 8 does not state **which** invocation must carry the four
  `--deselect` flags in a machine-checkable way, and its oracle cannot tell
  (§3 D3). "Append `--deselect` for each of" is satisfiable by appending them to
  any pytest line in the file.
- **B3.** Work item 5 requires "a test pinning exactly that, for both `go` and
  `sql`" but supplies no locked fixture; the shape of the refusal assertion
  (`ERROR/BAD_LANE_CONFIG` at which layer, with what message) is left to invent.

### (2) False-PASS attacks

- **F1.** Suppress A-210's aggregate-bounds oracle with `-k "not ..."` → O5
  passes (§3 D2, reproduced).
- **F2.** Attach the deselects to the P33 invocation instead of the P26 one → O5
  passes while P26 runs unfiltered and reddens under v5 (§3 D3, reproduced).
- **F3.** Record the **declared** `judge.base` string in `judgment.resolved.base`
  → passes the entire locked suite, because every template substitutes a 40-hex
  and `resolve_base` returns a full SHA unchanged (§4). Only CA8 catches it, and
  CA8 has no locked witness.
- **F4.** Any collection failure makes O5's `must_not_run` assertion vacuous
  (§3 D4); combined with F2 it is a silent pass.
- **F5.** Implement `ALL_MUTANTS_EQUIVALENT` as "`equivalent` non-empty →
  INCONCLUSIVE", ignoring the `killed + survived == 0` guard. A run with 1 killed,
  0 survived and 1 equivalent should be a judged result, not the all-inert
  terminal. I found no locked fixture with a **mixed** bucket set exercising the
  guard — `ca4` is all-equivalent, and the arithmetic tests check bucket sums, not
  the terminal's guard.

### (3) Missing implementation-packet content

- The three O5 attacks above are not fixtures anywhere; the anti-suppression
  oracle has no controlled break of its own.
- No locked fixture pins CA8's declared≠resolved property (§4).
- No locked fixture for work item 5's `go`/`sql` refusal.
- No mixed-bucket fixture for work item 7's terminal guard (F5).
- The sweep's supplier guard has no negative: nothing asserts that a file which
  merely reads an unrelated environment variable is **excluded**, which is why the
  `test_cgroup_parent.py` phantom went unnoticed.

### (4) Scope / dependency defects

None blocking. Scope is coherent: `qualify_topos.py`, `cli.py`,
`tools/tester-unified-gate.sh` and `mutation.py` are all correctly in `touch`
(A-224 closed the earlier omissions), and the forbid list matches the assets the
work items actually need. Two soft issues: `frozen_at_main` ≠ `input_revision`
(N2), and A-229 cited without its A-233 supersession (N5).

### (5) Corrected oracle / fixture matrix

Pairwise axes that matter here: {gate invocation carrying the flags} ×
{suppression mechanism} × {collection exit status}; and {declared base spelling} ×
{rigor set} × {bucket population}.

Three combined-axis fixtures a convenient implementation would fail:

1. **`gate-flags-on-the-wrong-invocation` + `-k` + clean exit.** A gate whose P26
   line is bare and whose P33 line carries all four deselects *and* a
   `-k "not test_all_structural..."`. Correct oracle: FAIL, naming both the
   misplacement and the suppression. Current oracle: PASS.
2. **`symbolic-base` + `R0,R2` + `equivalent`-only buckets.** A lane declaring
   `judge.base` as an annotated tag, rigor `R0,R2`, whose mutants are all
   equivalent. Exercises F3 (resolved≠declared), V5-1 (`R0,R2` legality, no
   `judgment.r1`), and F5's terminal guard in one document.
3. **`mixed-buckets` + `unattributed` + `helpers` present.** 1 killed, 0 survived,
   1 equivalent, `kill_attribution: unattributed`, one `mutation-sites` helper
   entry with a corresponding R2 payload. Must render a judged R2 status (not
   `ALL_MUTANTS_EQUIVALENT`), must reject any `kill_signal`, and must satisfy
   helper correspondence — three invariants that no current template combines.

Plus: the sweep needs a **planted non-consumer** (a file reading an unrelated
env var) that the guard must exclude, as the mirror of its existing planted decoy.

### (6) Verdict

**NOT READY.**

Reasons, shortest form:

1. O5's oracle cannot pass against a correctly wired gate — an illegitimate red
   by A-232's own definition, whose only lawful implementer response is BLOCKED.
2. The same oracle admits both attacks it was rewritten to close, one of them the
   exact `-k` bypass round 5 raised; the claim that it is closed is false.
3. The A-232 classification that this round's discipline rests on is itself
   incomplete — 4 named causes over 29 of 31 reds, counting grep lines rather
   than tests, with one omitted cause visible in the carver's own adjacent
   section.
4. CA8, the one oracle closing A-143's declared-vs-resolved trap, is delegated to
   the implementer with no locked witness.
5. The sweep's supplier guard does not implement A-233 as written and reports a
   phantom consumer that `CANONICAL_COUNTS` has frozen at 27.

Items 1–2 are the dispatch blockers. Items 3–5 are the reason this should not be
waved through on "the substance has been sound since round 2": the substance
largely *is* sound, and the recurring failure is that the newest proof mechanism
is never the one that gets run. The pattern across rounds 4, 5 and 6 is identical
— a repair is made, and its *own* verification is deferred. A-232 fixed that for
tests the carver already had. It did not fix it for tests the carver had just
written, because a short-circuiting assertion above them means "the suite ran"
does not imply "this code ran".

**Suggested standing addition (for the controller, not the carver):** any new
oracle introduced in a re-carve must be exercised against at least one synthetic
input that makes it PASS, and one that makes it FAIL, before the packet is
declared ready — because a red that never reaches the new code is indistinguishable
from a red that does.

---

*Reviewer note on independence: I am the same provider and model family as the
carver (A-216's recorded loss of cross-provider independence). Every finding above
was reached by running the artifact rather than by reading the carver's account of
it, which is the only independence axis still available. The probe used for §3 is
at `/tmp/oracle_probe.py`; it is a literal copy of the oracle's own lines 728–759
with the gate text parameterised, so its fidelity can be checked by diff.*
