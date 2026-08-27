# Adversarial audit — the six unreviewed 2.1.0→2.3.0 commits

**Verdict: 0 CLEAN, 2 MINOR, 4 BLOCKING. Thirteen blocking findings, nine of
them still live on `main`.**

An audit of `assay` 2.1.0→2.3.0 found six commits that shipped with no
independent-review commit and no matching report file (the last report added to
`nyxloom-trove/reports/` is `d7a78a60`, 2026-08-18; all six commits land
2026-08-22/24). This report is that missing review, run after the fact against
`main` at `4415894e`, audit-only — no code was modified and no backlog entry
filed. Reviewer: Opus 5, 2026-08-25, cwd `/workspaces/vbpub`, with `assay`
installed editable into a scratch venv and driven through its real CLI.

The headline results:

- **`8a2a4731`** shipped two of its four features non-functional. `assay plan`
  reports zero candidates for **every** lane (its own test asserts the bug), and
  an R2 lane writes a progress file into the consumer's live worktree, so the
  lane **passes once and then refuses forever with `DIRTY_TREE`**. Both live.
- **`ba8908d6`** shipped with zero tests, zero docs and zero decision records;
  R2's whole-target resolver silently drops declared targets where R1's refuses
  them, and its run-gate half silently disarmed dstdns's live-test lane into a
  false green.
- **`126ef577`/`6324548d`**: B015's headline claim is false. Its two new
  "semantic" operators produce a strict, byte-identical subset of the sites
  `python:compare-swap` already produces — 87 measured across
  `src/assay/**.py`, **zero** of them a mutation `compare-swap` does not already
  make — while mislabelling ordinary attribute comparisons (`cfg.debug == True`)
  as enum comparisons and emitting every co-selected site twice.
- **`f3ce3d0a`** merged a `NameError` into the registered gate driver 87 seconds
  before its own fix.

Every finding below was reproduced by running code, not by reading the diff.
Commands and real output are shown inline.

---

## Scope and method

For each commit I read the diff (`git show <hash>` from `/workspaces/vbpub`),
then checked the current shipped behaviour on `main`, exercising the code rather
than reading it. For anything touching the verdict schema, mutation operators,
gate-facing config, or the CLI surface I checked all four registration points
named in the project's own recent near-miss:

1. the dataclasses in `src/assay/verdict.py`;
2. `src/assay/schemas/verdict.schema.json`;
3. `src/assay/verify.py`'s hand-written raw-document reconstruction layer
   (`_reconstruct_verdict`, `_reconstruct_judgment_resolved`,
   `_reject_unknown_keys`) — deliberately independent of 1 and 2 by A-182;
4. the byte-frozen witnesses under `nyxloom-trove/carve-assets/`, which live
   outside `tests/` and which `pytest tests/` never touches.

### Registration-point drift check (clean)

The exact drift class behind the recent near-miss is **not** present today:

```
$ sha256sum src/assay/schemas/verdict.schema.json \
            nyxloom-trove/carve-assets/W2/verdict.schema.v7.json
97b8e77a921674803521a22296bd0465a3ff4b8a188c2ff9af9899714b3b3807  src/assay/schemas/verdict.schema.json
97b8e77a921674803521a22296bd0465a3ff4b8a188c2ff9af9899714b3b3807  nyxloom-trove/carve-assets/W2/verdict.schema.v7.json
```

Byte-identical. The W2 `expected/*.json` templates are current with respect to
the recent wave's additive fields (`base_resolution` is present in the four
templates whose lanes resolve a base; absent, correctly, from
`ca1-r3-no-base-v7-template.json` and `missing-tool-v7-template.json`), and all
23 W2 acceptance tests pass against the installed wheel. The W3 witness was
migrated to v7 by `b6d9615c` and the two frozen A-279 evidence documents remain
v6 with the sha256s their MANIFEST pins, which is correct — they are historical
evidence, verified by nothing.

---

## 1. `126ef577` / `6324548d` — B015 semantic Python mutation operators — **BLOCKING**

Two operators were added to the closed vocabulary,
`python:uuid-equality-swap` and `python:enum-comparison-swap`, wired through
`src/assay/vocabulary.py`, the packaged schema's `$defs/mutation_operator`
`oneOf`, and `src/assay/adapters/python.py`.

**The vocabulary plumbing itself is correct.** I verified registration points
1–4 for the two new names by round-tripping a real v7 document with both
operators in `judgment.r2.operators`, `judgment.resolved.language = "python"`,
and every mutant bucket entry relabelled, through
`assay.verify.verify_document`: zero failures. The schema enum and the
`MUTATION_OPERATORS_BY_LANGUAGE["python"]` tuple agree in membership and order.

The **behaviour** is where this fails.

### B015-A (BLOCKING) — the two new operators add zero new mutations

`_semantic_comparison_sites` (`src/assay/adapters/python.py:712-761`) flips
`ast.Eq`→`ast.NotEq` and `ast.NotEq`→`ast.Eq`, splicing `_COMPARE_TOKEN` for the
target class. That is *exactly and only* what `_compare_swap_sites`
(`src/assay/adapters/python.py:~490`) already does for the same two operators —
`_COMPARE_SWAP` (`src/assay/adapters/python.py:453-462`) contains
`ast.Eq: ast.NotEq` and `ast.NotEq: ast.Eq`, and `_compare_swap_sites` applies no
operand-type restriction whatsoever. Every site B015 can produce is therefore a
site `compare-swap` already produces, at the same byte span, with the same
replacement bytes.

Measured over assay's own source tree:

```
SUBSET CHECK over src/assay/**.py: 87 B015 sites total; 0 that compare-swap
does NOT already produce identically
```

**What the user observes.** A consumer who adopts B015 — following the
DESIGN-GUIDE's own updated example, which `126ef577` changed to declare all six
Python operators and which still stands at `docs/DESIGN-GUIDE.md:1477` — gets no
additional mutation coverage of any kind. The
backlog's B015 ask (filed from the post-2.2.0 release review, sourced from CIU
V7 §10.1 / P127) was for operators that catch UUID- and enum-specific defects,
i.e. mutations `compare-swap` cannot make. What shipped is a relabelling filter
over `compare-swap`'s existing output.

**The filing anticipated this exact failure and forbade it in writing.**
`nyxloom-trove/4-backlog.md` §B015 "Required before dispatch" includes:

> Decide explicitly whether any proposed site overlaps `compare-swap` enough to
> be indistinguishable evidence; reject or split accordingly.

Its "Candidate operator families" section states, of a general-equality family:

> Only add a third family if its sites are not already covered by
> `compare-swap` and it produces kills that generic swapping cannot attribute.

And it warns:

> A broad "UUID/enum mode" hidden inside existing operators would blur
> attribution and violate the catalogue's one-site/one-operator discipline.

The overlap is not partial — it is total (87 of 87 sites), and the evidence is
not merely "indistinguishable" but byte-identical down to the
`replacement_sha256`. No such explicit decision exists: no A-number, no carve,
no review report. The mandated qualification —

> Qualify against real consumer code where UUID/equality/enum semantics decide
> test outcomes, not only synthetic AST fixtures

— was likewise skipped: B015's own acceptance list still shows
`- [ ] a real R2 lane demonstrates kills attributable to each admitted family`
**unchecked**, while the four boxes above it were checked and the item marked
`IMPLEMENTED 2026-08-24`. The single criterion that would have caught this is
the one left undone.

### B015-B (BLOCKING) — every co-selected site is emitted twice, as a byte-identical duplicate

Because B015's sites are a subset of `compare-swap`'s, selecting both families
(the configuration the DESIGN-GUIDE now recommends) emits each eligible site
twice. `_candidate_sites` (`src/assay/adapters/python.py:779-784`) concatenates
`_compare_swap_sites(node, text_bytes) + _semantic_comparison_sites(node, text_bytes)`
with no de-duplication.

`MutationSite.identity` (`src/assay/mutation.py:317-322`) is
`(start_byte, end_byte, replacement_sha256, operator)` — it **includes** the
operator — so the duplicate-identity guard at `src/assay/mutation.py:585-589`
does not fire, and `collect_mutation_sites` accepts the pair:

```
$ python -c "... collect_mutation_sites([target], adapter=PythonAdapter(),
             operators=<all six python operators>, limit=100)"
collect_mutation_sites ACCEPTED, 6 mutant jobs to execute:
```

against a four-line function containing exactly **four** distinct mutable sites.
The source used:

```python
def f(cfg, other, flag):
    if cfg.debug == True:      # -> compare-swap AND enum-comparison-swap
        return 1
    if flag and other:         # -> boolop-swap
        return 2
    if cfg.mode != "x":        # -> compare-swap AND enum-comparison-swap
        return 3
    return 4
```

both duplicated pairs sharing an identical `replacement_sha256`
(`c10987bd…`, `f16474a9…`).

**Confirmed in a real shipped verdict.** A genuine `assay run` on a real R2
lane (`rigor = ["R0","R2"]`, `language = "python"`, snapshot isolation, real
git base, `operators = ["python:compare-swap", "python:uuid-equality-swap",
"python:enum-comparison-swap"]`) against a one-line change
`if cfg.debug == True:` produces:

```
package: PASS (exit 0)
total: 2 candidate_count: 2
  killed     line 2 span=(29,31) op=python:compare-swap          sha=c10987bd7cf8
  killed     line 2 span=(29,31) op=python:enum-comparison-swap  sha=c10987bd7cf8
$ assay verify v.json ; echo $?
0
```

**One** distinct mutation — identical span, identical replacement digest —
reported as `total: 2`, executed twice, and accepted by `assay verify`. The
comparison contains no enum. This is a false statement in a shipped artifact
that the project's own verifier cannot catch, because
`MutationSite.identity` includes the operator name.

**What the user observes.** On a Python R2 lane declaring both families:
`mutation.total` and `candidate_count` are inflated; the `--max-mutants` /
`max_mutants` budget is consumed by duplicates, so a budget-capped run covers
roughly *half* the distinct mutations the consumer believes it covers; each
duplicate re-runs the full test command, roughly doubling lane wall-clock on
eligible sites; and the verdict records two mutants with identical spans and
identical replacement digests attributed to two different operators, which
misreports which operator family actually killed or survived.

### B015-C (BLOCKING) — `_is_enum_member_expression` matches any attribute access, not enum members

`src/assay/adapters/python.py:705-710`:

```python
def _is_enum_member_expression(node: ast.expr) -> bool:
    """True for a dotted identifier such as ``Color.RED`` or
    ``enums.Color.RED``; false for bare names, calls and computed attributes.
    """
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
```

The predicate is `Name.attr` and nothing more. It matches `self.count`,
`cfg.debug`, `path.suffix`, `os.sep` — any attribute access on a bare name.
Nothing about it is enum-specific. (The docstring's second example,
`enums.Color.RED`, is `Attribute(value=Attribute)` and is in fact *rejected* by
this predicate, so the docstring is wrong in both directions.)

Reproduced against source containing no enum at all:

```
  line  8 op=python:enum-comparison-swap   # cfg.debug == True
  line 10 op=python:enum-comparison-swap   # self_obj.count == 0
  line 12 op=python:enum-comparison-swap   # path.suffix == ".py"
  line 14 op=python:enum-comparison-swap   # other == Color.RED   (the only real one)
```

All 87 B015 sites found across `src/assay/**.py` are of this false-positive
class — assay's own source compares no enum members and constructs no UUIDs.

**What the user observes.** A verdict claiming a mutant was produced by
`python:enum-comparison-swap` when the comparison had no enum in it — a false
statement on the wire, the class of defect A-262 took a major version bump to
avoid. It also means B015-B's duplication fires on essentially *every*
`obj.attr == x` in a codebase, not on a rare enum comparison.

The shipped test suite cannot see this: `tests/test_adapters_python_semantic_operators.py`
tests only `selected == Color.RED` (a true positive) and
`value == helper.build()` (a `Call`, correctly rejected). It never tests an
ordinary attribute comparison — the entire false-positive class.

### B015-D (MINOR) — stale `left` mis-attributes the site in a mixed comparison chain

In `_semantic_comparison_sites`, `left = right`
(`src/assay/adapters/python.py:761`) is the last statement of the loop body and
is therefore skipped by the `continue` at `src/assay/adapters/python.py:729-730`.
A chain whose first comparison is ineligible and whose second is eligible
searches for its token starting from the wrong left operand:

```
source: 'if f() == g() == cfg.x:'
  compare-swap           span=(7,9)   token='==' -> 'if f() != g() == cfg.x:'
  compare-swap           span=(14,16) token='==' -> 'if f() == g() != cfg.x:'
  enum-comparison-swap   span=(7,9)   token='==' -> 'if f() != g() == cfg.x:'
```

The `cfg.x` operand is what supplies the (already-bogus) "semantic evidence",
but the site produced splices the **first** `==`. `_compare_swap_sites` handles
this correctly (`left = node.left if index == 0 else node.comparators[index - 1]`,
recomputed per index rather than carried) — an asymmetry between two structurally
identical loops one function apart. No crash results (the identity still differs
from `compare-swap`'s by operator name), so this is MINOR relative to A–C.

### B015-E (MINOR) — no decision record

`grep -c "B015" nyxloom-trove/decisions.md` returns **0**. A change that extends
the closed per-language operator vocabulary — the thing `vocabulary.py`'s own
module docstring says exists to be closed by construction, governed by
A-112/A-114/A-220/A-221 — carries no A-numbered ruling.

---

## 2. `37462618` — B014 bounded command output tails — **MINOR**

This commit bumped `VERDICT_SCHEMA_VERSION` 6→7 and added four optional
top-level fields. **The core feature works end to end**, which I verified by
driving a real lane through the installed CLI:

```
$ assay run package --verdict-json v.json     # argv exits 3 after writing both streams
package: FAIL/COMMAND_FAILED (exit 1)
$ python -c "print({k:v for k,v in json.load(open('v.json')).items() if k.startswith('result_')})"
{'result_stderr_dropped_bytes': 0, 'result_stderr_tail': 'BOOM-STDERR\n',
 'result_stdout_dropped_bytes': 0, 'result_stdout_tail': 'HELLO-STDOUT\n'}
$ assay verify v.json ; echo $?
0
```

All four registration points are wired: dataclass fields with bounds validation
(`src/assay/verdict.py:2330-2346`, `:2368-2384`), schema properties with
`maxLength: 65536` and `minimum: 0`, reconstruction in
`_reconstruct_verdict` (`src/assay/verify.py:1247-1256`), and the W2 frozen
assets. A PASS correctly omits all four fields. The 64 KiB bound is honoured
exactly (measured: `len(tail.encode()) == 65536` on a 200 KB stream), and the
`_bounded_tail` cutoff correctly advances off UTF-8 continuation bytes.

Three real issues, none of which corrupts a verdict's outcome.

### B014-A (MINOR) — the schema does not encode the pairing the verifier enforces

The verifier requires a tail's dropped-byte counterpart
(`src/assay/verify.py:1247-1256` indexes `document["result_stdout_dropped_bytes"]`
with `[]`, and W2's `test_a_tail_requires_its_dropped_byte_count` asserts the
refusal). The shipped schema does **not**: `dependentRequired` at the top level
covers the ten `declared_rigor` lane-resolved fields and
`env_effective_incomplete`, and names none of the four B014 fields.

```
$ jsonschema validate unpaired.json   # result_stdout_tail present, dropped_bytes deleted
unpaired.json SCHEMA-VALID
$ assay verify unpaired.json ; echo $?
assay verify: schema: 'result_stdout_dropped_bytes'
1
```

A document the shipped JSON Schema accepts, `assay verify` rejects. Per A-029
the schema is shipped as data precisely so consumers can validate *without*
importing assay; those consumers get a different answer than assay does. assay
itself never produces an unpaired document (`Verdict.to_dict` writes both or
neither), so no shipped artifact is affected — this is a latent contract
divergence between registration points 2 and 3, not a live wrong verdict. The
diagnostic is also a bare `KeyError` repr rather than a sentence.

### B014-B (MINOR) — a pre-command refusal claims output was "captured and empty"

The schema and the `Verdict` docstring both define the distinction: absent means
"no command-output contract applies"; `""` means "captured and known to be
empty". `CommandResult`'s own docstring (`src/assay/runner.py:330-332`) states
"A process that never started has no tail." The commit message says "PASS and
pre-command refusals omit the contract."

Both pre-command refusal paths set `stdout_tail=""` / `stderr_tail=""`
(`src/assay/runner.py:499-500`, the `argv_appended and not allow_argv_append`
refusal, and `src/assay/runner.py:540-541`, the `OSError` exec-failure):

```
$ assay run package --verdict-json v4.json     # argv = ["/nonexistent/tool"]
outcome ERROR EXEC_FAILED | result_*: {'result_stderr_dropped_bytes': 0,
  'result_stderr_tail': '', 'result_stdout_dropped_bytes': 0, 'result_stdout_tail': ''}
```

Nothing was captured — nothing ran. The artifact asserts otherwise, and a
consumer distinguishing "the command ran silently" from "the command never
started" reads it wrong. The code contradicts its own docstring, its commit
message, and DESIGN-GUIDE's stated semantics simultaneously. The field is
already `str | None`, so the fix is `None` on both paths.

The same conflation is baked into `_bounded_tail` itself
(`src/assay/runner.py:265-266`):

```python
if raw is None or raw == "":
    return "", 0
```

`None` (no stream at all) and `""` (an empty stream) return the identical
`("", 0)`. The function's return type `tuple[str, int]` cannot express absence,
so the distinction the schema and docstrings define is unrepresentable at the
one place it would have to be made.

**This was noticed during the wave and resolved the wrong way.** `ebdd8f6c`
("distinguish captured timeout tails from no-process timeouts") states the
problem exactly — "the synthetic pre-command timeout fixture omits B014 fields,
while a real `TimeoutExpired` with empty captured streams emits explicit empty
tails" — and then repairs the *test* to accept the behaviour, additionally
mutating the fixture's `raise_timeout` helper to set `exc.stdout = ""` /
`exc.stderr = ""` so the tails would be produced. The semantics were never
revisited. That is the "weaken the oracle to reach green" shape, and it is the
second instance of it in this wave (see W2-D).

### B014-C (MINOR) — `dropped_bytes` is not the child's byte count when output is not UTF-8

`_bounded_tail`'s docstring (`src/assay/runner.py:258-264`) claims the count is
"measured on its UTF-8 encoding: the same currency as the process's output".
That holds only for decodable output. `subprocess` under `text=True` has already
turned each undecodable byte into U+FFFD, which re-encodes to **three** bytes, so
the reported count inflates:

```
$ # argv = ["/bin/sh","-c","head -c 200000 /dev/urandom; exit 3"]
tail utf8 bytes: 65536
dropped_bytes reported: 296575
child ACTUALLY emitted 200000 raw bytes; reported dropped + retained = 362111
```

An 81% over-count on a field whose entire purpose is being trustworthy about how
much was lost. Diagnosis-only, so the verdict outcome is unaffected — but the
docstring's "same currency" claim is false, and the schema's
"head-side UTF-8 byte count removed" reads to a consumer as "how much output I
lost".

### B014-D (MINOR) — the 64 KiB bound exists in three places with no drift guard

`COMMAND_TAIL_BYTES = 64 * 1024` is defined at `src/assay/runner.py:240` and
again at `src/assay/verdict.py:289` (deliberately, with a documented
circular-import rationale), and a *third* time as the schema's
`"maxLength": 65536` on all four fields. `grep -rn COMMAND_TAIL_BYTES tests/`
finds only two uses, neither of which asserts that the three copies agree.

This is the exact shape A-310 ruled on for `MAX_SHARD_COUNT` weeks later — and
A-310's own text cites `COMMAND_TAIL_BYTES` as precedent for the tradeoff. But
`MAX_SHARD_COUNT` got a deliberate split *and* a recorded ruling; the schema's
third copy of this bound is undocumented and unguarded. Lowering the model
constant without editing the schema (or vice versa) reproduces exactly the
model-valid/schema-invalid split `vocabulary.py`'s docstring says the project
exists to make impossible.

### B014-E (MINOR) — no decision record

`grep -c "B014" nyxloom-trove/decisions.md` returns **0**. A
`VERDICT_SCHEMA_VERSION` hard cut 6→7 — the exact thing A-170 governs, and which
every prior bump (A-261 for 5→6, A-220/P33 for 4→5) recorded — has no
A-numbered ruling.

---

## 3. `f3ce3d0a` — W2 verdict schema v7 successors — **BLOCKING**

### W2-A (BLOCKING) — a `NameError` was merged into the registered gate driver

`f3ce3d0a` replaced the deselect-based v6 handling in
`tools/tester-unified-gate.sh` with an inline Python heredoc probe. At
`f3ce3d0a`, that probe reads:

```python
import json
from pathlib import Path

from assay.verify import verify_document

root = Path(sys.argv[1])
```

`sys` is never imported. Under `set -euo pipefail` this aborts the
`verdict-v6-successors-verified` phase with
`NameError: name 'sys' is not defined` on every invocation — the registered gate
could not reach the W2 phase, the self-hosted lane, or P25 qualification.

This is not merely a transient. The timeline:

```
f3ce3d0a 11:33:04 feat(assay): W2 verdict schema v7 successors   <- introduces the NameError
b7184395 11:33:25 Merge branch 'feature/w2-assay-v7-successors'  <- merged to main anyway
56d6c2c5 11:34:52 fix(assay): import sys for W1 hard-cut gate probe
```

The feature branch was merged to `main` **21 seconds after the broken commit and
87 seconds before the fix**, with its own gate driver unable to execute — so the
registered gate cannot have passed on the merged state. Not live on `main` today.

This is squarely the *same shape* as the recent wave's near-miss: a gate-facing
surface outside `tests/` that `pytest tests/` cannot execute, so nothing local
was red.

### W2-B (MINOR) — the W2 test that ties B015 to the verifier is vacuous

`nyxloom-trove/carve-assets/W2/test_acceptance_v7.py::test_b015_operators_are_schema_and_verifier_compatible`
is the only asset-level proof that B015's new operator names are acceptable in a
real verdict. It rewrites `killed` and `survived` but not `equivalent`, so
`sql:drop-check` survives in the document, and it asserts only
`not any("unknown field" in failure for failure in failures)` — which passes
while the document is in fact rejected:

```
B015-compat failures count: 3
  - the R2 mutation payload names operator(s) ['sql:drop-check'] that
    judgment.r2.operators [...] never declared
  - ... judgment.resolved.language is 'python'; a run cannot apply a catalogue
    belonging to another language
  - schema: claim[R2].mutation records outcome(s) for ['sql:drop-check'] ...
```

The test proves nothing. (Repairing the document properly — rewriting all five
buckets — the verifier *does* accept both new operators with zero failures, so
the underlying wiring is sound; only the proof is empty.) Its sibling
`test_dropped_bytes_cannot_be_negative_or_miscounted` likewise tests only the
negative case, never a miscount, despite its name.

### W2-C (MINOR) — the gate stopped running 26 differential proofs and replaced them with 6

`f3ce3d0a` changed the W1 suite invocation to `--co -q >/dev/null`
(collect-only) and substituted a probe that asserts the v6 hard-cut rejection
over the 6 documents in `W1/expected/`. W1's suite is 26 tests, 6 of which are
template acceptance; of the remaining 20, roughly fourteen are differential
*defect-refusal* controls — e.g.
`test_kill_signal_is_rejected_outside_the_killed_bucket_under_v6`,
`test_ca10_unattributed_forbids_a_kill_signal_on_a_killed_entry_under_v6`,
`test_ca9_payload_free_all_mutants_equivalent_is_refused_under_v6`,
`test_helpers_entry_requires_a_correspondingly_judged_claim_under_v6`,
`test_a_cross_language_operator_is_refused_under_v6`,
`test_base_is_forbidden_unless_r1_or_r2_under_v6`.

W2's 23 tests account for: schema identity (2), the hard-cut guard (6),
template acceptance (4 + 2 P25 + 2 P25-omit-tails), P26 shapes (4), and three
B014/B015 tests. **Not one of W1's ~14 defect-refusal controls has a v7
successor.**

This is a defensible consequence of the hard cut (v6 documents genuinely cannot
verify under v7) and the commit message describes what it did accurately. But
the net effect — the gate no longer proves the verifier refuses those ten
specific defect shapes — is not called out anywhere, and W2 does not replace
them. Worth a deliberate ruling rather than an implicit one.

---

## 4. `6b777274` — W2 gate and v7 test migrations — **MINOR**

The commit does what it says: it adds the missing `as exc` binding for
`subprocess.TimeoutExpired` (`src/assay/runner.py:519` today), migrates
embedded v6 fixtures to v7, and adds two `shellcheck disable=SC1007`
directives. The `as exc` fix is real — at `37462618` the handler referenced
`exc.stdout` with no binding, so *any* lane timeout raised `NameError` instead
of `BUDGET_EXCEEDED`/`LANE_TIMEOUT`. That is a genuine BLOCKING defect in
`37462618`, but it was fixed inside the same wave before release, so it is
recorded here against `6b777274` as repaired rather than against `37462618` as
shipped.

### W2-D (MINOR) — the real end-to-end oracle was weakened to absorb the new fields

`tests/test_standalone.py`'s `_assert_complete` previously compared every key of
a real artifact except three genuinely un-injectable ones
(`assay_version`, `started`, `ended`). This commit added all four B014 fields to
that `volatile` set, with the rationale "their presence is proven by the
dedicated runner tests and schema tests, not duplicated here."

The consequence is that the project's only **real-run, byte-compared** oracle no
longer asserts anything about the output tails: not their presence, not their
pairing, not their absence on PASS. The exact-bytes argument is legitimate (child
buffering is genuinely nondeterministic); dropping the four keys wholesale is
more than that argument buys. Asserting *presence and pairing* while ignoring
*content* would have preserved the oracle. Notably, the B027 crash the recent
stabilization wave had to fix lived in precisely this code path and was invisible
to the suite.

---

## 5. `ba8908d6` — whole-target SQL mutation targets and declared env forwarding — **BLOCKING**

This commit spans two projects (`assay/src/assay/{config,runner}.py` and
`run-gate-project/`). The assay half shipped with **zero tests**
(`git show ba8908d6 --stat -- 'assay/tests/*'` is empty;
`grep -rn "_mutation_targets_whole\|whole_file_r2" assay/tests/` returns
nothing), **zero doc updates**, and **zero A-numbers** — `decisions.md`'s
sessions jump 2026-08-16 → 2026-08-25 across a 2026-08-22 commit. Four blocking
findings, all reproduced by running code.

### ba-A (BLOCKING) — a whole-target R2 verdict records a `base` nothing compared against

`whole_file_r2` (`src/assay/runner.py:2047-2050`) makes `runner.py:2051` skip
both `check_base_is_head` and the `git diff`. But `compares_a_base` remains
`judgment_r1 is not None or judgment_r2 is not None` (`runner.py:2295`), so
`_build_judgment_resolved` (`runner.py:2328-2332`) still writes the resolved
base into the artifact.

That function's own docstring forbids exactly this: "`base` … is present exactly
when a tier that reads one is … recording one would be an invented fact rather
than a missing one." `docs/DESIGN-GUIDE.md:903-905` states the same rule as the
reason an `R0,R1,R2` whole-target lane keeps `base` — "for R2's own sake" —
which is now false, because R2 no longer reads it.

Reproduced against a repo where `HEAD == origin/main` (so a diff-based R2 would
find nothing):

```
$ assay run sqllane --verdict-json verdict.json
sqllane: FAIL/MUTANTS_SURVIVED (exit 1)
judgment.resolved: { "base": "ee4925be…", "base_resolution": "merge-base", … }
R2 mutation: candidate_count 2, survived paths ["db/schema.sql", "db/schema.sql"]
```

Two mutants from `judge.targets`, and a `base` + `base_resolution` on the wire
for a comparison that never ran. dstdns's `cw2b_schema` lane
(`/workspaces/dstdns/assay.toml:83-116`) emits this on every run today.

### ba-B (BLOCKING) — the SQL carve-out reopens the vacuity hole its own error message names

`src/assay/config.py:1299`:

```python
if targets_declared and effective_mode != "whole_target" and declared_language != "sql":
```

The commit appended `and declared_language != "sql"` to the guard whose message
reads "a target list under changed-line mode does nothing and silently declaring
one is how a consumer comes to believe a floor is enforced when it is not."

A SQL lane declaring `judge.targets` **without** `judge.mode = "whole_target"`
now loads clean, and `runner.py:2047` routes it to the diff path. Same lane, one
line of TOML different:

```
mode ABSENT    -> R2 INCONCLUSIVE/NO_MUTANTS  candidates=0 total=0
mode PRESENT   -> R2 FAIL/MUTANTS_SURVIVED    candidates=2 total=2
```

`targets` was also added to the unconditional surplus exemption
(`config.py:1362`), so nothing downstream catches it. The inert list still
reaches the artifact — `JudgeConfig.as_declared()` (`config.py:516-517`) emits
`"targets": ["db/schema.sql"]` with no `mode`, so the verdict advertises a floor
that was never applied. Deleting or typo'ing one line of dstdns's `cw2b_schema`
lane is refused for every language except the one that uses it.

### ba-C (BLOCKING) — R2's whole-target resolver silently drops declared targets; R1's refuses them

`_mutation_targets_whole` (`src/assay/runner.py:1770-1806`) applies six gates and
`continue`s silently on three: excluded dir (`:1796`), non-matching
`source_globs` (`:1798`), test path (`:1800`).

Its R1 counterpart `_resolve_whole_target`
(`src/assay/evaluate.py:715-780`) **refuses** every one of those with
`ERROR`/`BAD_LANE_CONFIG` naming the target and the gate, and its docstring says
why: "a directory target expanding to N files of which only one is measured
would PASS while leaving the rest unjudged, which is precisely the vacuity hole
this whole mode exists to close." The commit reopened that hole one tier down —
a textbook asymmetry between two structurally identical resolvers.

```
$ assay run sqllane   # targets = ["db/schema.sql", "db/tests/fixtures.sql"]
sqllane: FAIL/MUTANTS_SURVIVED (exit 1)
R2: candidate_count = 2
files actually mutated: ['db/schema.sql']
anything in the verdict naming a DROPPED target? -> False
```

Direct resolver probes confirm the shape: `db/tests/fixtures.sql` → `[]`,
`db/legacy.SQL` (uppercase extension) → `[]`, `db/notes.md` → `[]`, all silent.

This is unrecoverable from the artifact: `judgment.r2` carries no `targets`
field (only `judgment.r1` does), so "judged and clean" is indistinguishable from
"silently skipped." The **all**-skipped case is at least honest —
`judge_mutation` (`mutation.py:1746`) maps `total == 0` to
`INCONCLUSIVE`/`NO_MUTANTS`, not PASS. **Partial** skip is the dangerous case,
and it renders a real `FAIL`/`PASS` over a silently narrowed target set.

Related, same function: a declared target absent at the judged commit — a state
`_load_targets`' docstring (`config.py:1163-1168`) explicitly designs for ("a
whole-target lane must be judgeable from ANY commit") — yields
`ERROR`/`GIT_FAILED` with **no message naming the target**, where R1 gives a
named `BAD_LANE_CONFIG`.

### ba-D (BLOCKING, cross-repo) — dropping run-gate's hardcoded exec allowlist disarmed dstdns's live-test lane

Pre-commit, `run_exec_lane` hardcoded
`for key in ("MOCK_MODE", "RUN_LIVE_TESTS", CGROUP_ENV_VAR)`. The commit
replaced it with `(CGROUP_ENV_VAR, *env.get("forward_env", []))`
(`/workspaces/vbpub/run-gate-project/run-gate.py:1394`). No consumer `.toml` was
migrated, and neither `SPEC.md` nor `CONSUMERS.md` records the removal.

`/workspaces/dstdns/run-gate.toml:12-16` lists 13 `forward_env` names;
`RUN_LIVE_TESTS` is not among them. `/workspaces/dstdns/run-gate.toml:189` still
says "Consumer must set RUN_LIVE_TESTS=1 (forwarded by run-gate exec-mode)" —
true before this commit, false since.

**Failure scenario.** An operator exports `RUN_LIVE_TESTS=1` as instructed and
runs the `release` lane (`pytest -m 'integration or observability or e2e or
infra'`). The variable never enters the container.
`/workspaces/dstdns/tests/conftest.py:588,611-613` then skips every selected
test. An all-skipped pytest exits 0, so **the lane reports GREEN having executed
no live test** — a silent false-green on the exact class of test the lane exists
to run.

```
$ python repro_env.py     # replays run-gate.py:1394 against the real dstdns run-gate.toml
-e flags the container actually receives:
    CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice
RUN_LIVE_TESTS forwarded? False
MOCK_MODE forwarded?      False
$ # same input against ba8908d6^
PRE-COMMIT exec-lane forwards: ['RUN_LIVE_TESTS=1', 'CGROUP_PARENT_DEV_BACKGROUND=...']
```

Neither safety net catches it: the `release` lane declares no `required_env` (so
`preflight_required_env`, `run-gate.py:703`, never fires), and `--check-env`'s
`ENV_REF_RE` (`run-gate.py:773`) needs a string literal inside `getenv(...)`,
while dstdns reads it as `os.getenv(name, "")` through `_env_flag_enabled(name)`
(`conftest.py:39-41`). The fix is in dstdns, not assay: add `RUN_LIVE_TESTS` to
`forward_env`, and preferably to that lane's `required_env`.

### ba-E (MINOR) — the `base` SQL exemption is the inverse of its own comment

`config.py:1316`:

```python
if "base" in required and "R2" not in rigor and declared_language != "sql":
```

carries the comment "A SQL **R2** lane has no diff to measure, but its baseline
command still needs the declared base … keep `base` required there." The branch
is gated on `"R2" not in rigor`, so it can never affect an R2 lane. Its only
real effect is on an R1-only lane:

```
R1-only  language=sql     base=DECLARED -> ACCEPTED (judge.base='origin/main')
R1-only  language=sql     base=ABSENT   -> REFUSED: missing required field 'judge.base'
R1-only  language=python  base=DECLARED -> REFUSED: … inert and cannot fail
R1-only  language=python  base=ABSENT   -> ACCEPTED (judge.base=None)
```

A SQL R1-only whole-target lane is *forced* to declare an inert `base`,
contradicting `docs/DESIGN-GUIDE.md:1490` and `docs/CONSUMERS.md:119`
("`judge.base` is FORBIDDEN here") and inverting A-062/A-260's inert-config rule
for one language.

### ba-F (MINOR) — `whole_file_r2` is not language-gated, and is undocumented

`runner.py:2047-2050` checks only `mode`/`targets`. A **Python** `R0,R1,R2`
whole-target lane — a shape `DESIGN-GUIDE.md:904-905` explicitly blesses — has
silently switched its R2 from diff-based to whole-file mutation. Every shipped
doc still describes `mode`/`targets` as R1-only (`README.md:30-34`,
`DESIGN-GUIDE.md:878` "Two **R1** modes", A-260 at `decisions.md:613` "a MODE of
R1"). Nothing in the repo documents that `mode` now also selects R2's target set.

Also at `runner.py:1803`, `lines = frozenset(range(1, text.count("\n") + 2))`
over-counts by one for newline-terminated files. Harmless in practice (sites are
byte-offset based); reasoned, not reproduced.

### ba-G (MINOR, fixed since) — forwarded secrets were printed, and the two runners disagreed

At `ba8908d6`, `run_container_lane` forwarded on `if value is not None` (empty
string forwarded) while `run_exec_lane` used `if value` (empty string dropped) —
an asymmetry inside the same diff. And `run-gate.py:399` at that revision
printed `docker argv: {shlex.join(argv)}` **unredacted**, so the commit's own
flagship example `forward_env = ["SCHEMA_GATE_DSN"]` printed a full database DSN
to the gate log. Both are fixed today (rev 23: matching `if value:` at
`run-gate.py:1253-1256`, plus `redact_forwarded_values`). Recorded for
completeness, not action.

### Checked and clean on this commit

Subprocess crash/timeout handling (`runner.py:504-541`) maps `TimeoutExpired` →
`BUDGET_EXCEEDED`/`LANE_TIMEOUT` and `OSError` → `ERROR`/`EXEC_FAILED` with the
B027 decode fix in place; nothing in `ba8908d6` touches it. Zero-target vacuity
is honest (`INCONCLUSIVE`/`NO_MUTANTS`, not PASS). Symlink targets are refused by
`SnapshotRepository.read_regular_file` (`isolation.py:435`) even though
`_mutation_targets_whole` does not check them. `forward_env` name validation
(`run-gate.py:113-121`) is correct. The run-gate suite is `212 passed, 1 failed`,
the failure being pre-existing pointer-linkage drift unrelated to this commit.

## 6. `8a2a4731` — B010/B012 preflight and mutation observability — **BLOCKING**

This is the worst of the six. It shipped in **assay-v2.2.0**. Two of its four
headline features are non-functional as shipped, five blocking defects are
**still live on `main` today**, and the commit message's "with review fixes"
does not describe an independent review — `decisions.md` is untouched by the
commit, and the first A-numbers for this work (A-292/A-296) appear only in the
*later* remediation commits, where A-296 records that the defects were "found
entirely by an independent adversarial review driving the shipped CLI (never by
reading the diff)."

### 8a-A (BLOCKING, LIVE ON MAIN) — `assay plan` is 100% non-functional, and its own test freezes the bug

`src/assay/cli.py:572` (at the commit, `cli.py:520`):

```python
relocated = runner._relocate_source_roots(
    lane, project_root=lane_file.project_root,
    scratch_project_root=(prepared.spec.scratch_root / "unused"),
)
```

`_relocate_source_roots` respells `judge.source_root_paths` against the
snapshot's *materialized* project root. The real run passes
`baseline_snapshot.project_root` (`runner.py:2077`/`:2101`). Plan passes a
directory literally named `"unused"` that is never created.
`resolve_mutation_targets`'s containment gate at `src/assay/mutation.py:459` is
an unconditional
`any(abs_path.is_relative_to(root) for root in source_root_paths)`, so it can
never be satisfied. **No lane configuration escapes this.**

Reproduced on `main` (2.4.0), same repo, same commit, same lane:

```
$ assay run unit --verdict-json v1.json
unit: PASS (exit 0)      -> mutation: candidate_count 1, killed 1

$ assay plan unit
{ "candidate_count": 0, "by_file": {}, "by_operator": {}, "candidates": [], ... }
```

Root cause proved by suppressing only the relocation call
(`runner._relocate_source_roots = lambda lane, **kw: lane`), which makes plan
return the correct answer, matching the run's own candidate id byte-for-byte
(`"candidate_count": 1, "by_file": {"pkg/flags.py": 1}`, id `56f00e68…`).

For `mode = "whole_target"` lanes it does not merely under-report — it fails
outright, naming a temp dir that never existed:

```
$ assay plan whole --file assay-whole.toml
assay: ERROR/BAD_LANE_CONFIG: mutation target 'pkg/flags.py' is outside
  judge.source_roots ['/tmp/assay-plan-seed-j7x4gzvu/unused/pkg']
exit=2
```

Two aggravating facts. First, **the commit's own test asserts the bug**:
`test_plan_reports_candidates_without_executing`'s fixture genuinely yields one
`bool-const-flip` candidate, and the test asserts `payload["candidate_count"] == 0`
— still asserting `== 0` today at
`tests/test_mutation_progress_budget_plan.py:550`. It was written to match
observed output, not the requirement. Second, `docs/CONSUMERS.md:517` (added
*after* the bug, in the remediation wave) states plan "reports deterministic
candidate IDs, total/per-file/per-operator counts, declared worker concurrency,
and runtime estimates." That is false on main today, and B012's acceptance
criterion "`assay plan` reports deterministic totals/IDs/runtime estimate" is
unmet.

Three later review rounds (`7941fdcb`, `45ea7d0b`, `b97f3aaf`, `21205b78`)
touched `_cmd_plan`'s argument parsing and never once ran it against a real lane
with source roots.

### 8a-B (BLOCKING) — `mutation.progress_artifact` fails all four registration points, differently

This commit added exactly one verdict field. Every registration point is wrong
in its own way — the clearest illustration in the whole audit of why the project
keeps four.

**(a) dataclass — OK.** `verdict.py:1251`, serialized at `:1373`.

**(b) schema — WRONG `$defs`.** The two 10-line insertions landed on
`$defs/coverage/properties` and `$defs/claim/properties`, not `$defs/mutation`:

```
== AT 8a2a4731   urn:assay:schema:verdict:6
  progress_artifact at: ['/$defs/coverage/properties', '/$defs/claim/properties']
  $defs/mutation properties: [budget_exceeded, candidate_count, crashed,
                              equivalent, killed, survived, total]
  $defs/mutation additionalProperties: False
```

Because `$defs/mutation` sets `additionalProperties: false`, any verdict that
*did* carry the field would have violated assay's own published schema. Fixed
later by `7941fdcb`, but it survived B014 (`37462618`), B015 (`126ef577`) and
the resume/sharding wave (`7a4f6333`) first.

**(c) `verify.py` reconstruction — MISSING, LIVE ON MAIN.**
`src/assay/verify.py:1137-1147`'s `_reconstruct_mutation` never reads
`progress_artifact`, so `_reject_unknown_keys(raw, mutation.to_dict(), "mutation")`
rejects it. **This is the exact near-miss shape the audit was commissioned to
look for**, and it reproduces today:

```
$ # real verdict + claims[1].mutation.progress_artifact = ".assay/unit.progress.jsonl"
SCHEMA VALIDATION: PASS
$ assay verify with_progress.json
assay verify: schema: unknown mutation field(s): ['progress_artifact']
exit=1
```

The identical hole exists for `candidate_ids` (added later by `7a4f6333`):
`assay verify: schema: unknown mutation field(s): ['candidate_ids']`. Both live.

**(d) frozen carve asset — updated byte-identically wrong.**
`nyxloom-trove/carve-assets/W1/verdict.schema.v6.json` received the same
misplaced hunk, so `test_shipped_schema_is_byte_identical_to_the_locked_v6_asset`
stayed green. A byte-equality drift guard structurally cannot catch a
correct-shape/wrong-place addition.

**And the field is dead regardless.** No code path anywhere constructs
`Mutation(progress_artifact=...)` — at the commit or on main. A real run emits

```json
{"budget_exceeded":[],"candidate_count":1,"crashed":[],"equivalent":[],
 "killed":[{...}],"survived":[],"total":1}
```

with no `progress_artifact`, while `.assay/unit.progress.jsonl` sits on disk
unreferenced. B012 requirement 1's explicit "**Summarize the artifact path in
the verdict**" and its acceptance box "progress events emitted **and referenced
from verdict**" are unmet.

### 8a-C (BLOCKING, LIVE ON MAIN) — the progress artifact poisons assay's own clean-tree precondition

`src/assay/runner.py:1892-1894` (at the commit, `runner.py:1649`):

```python
progress_path = (Path(".assay") / f"{lane.name}.progress.jsonl" if r2_declared else None)
```

Unconditional for every R2 lane, written into the consumer's **live worktree**.
Reproduced on main with a fresh repo whose `.gitignore` has no `.assay/` entry
(the lane declares no coverage artifact, so there is no reason for one):

```
=== RUN 1 ===  unit: PASS (exit 0)
=== tree state after run 1 ===  ?? .assay/   unit.progress.jsonl
=== RUN 2 (user changed nothing) ===  unit: NO_MEASUREMENT/DIRTY_TREE (exit 3)
```

`git.dirty_paths()` returns `('.assay/', '.assay/unit.progress.jsonl')`. **An R2
lane passes once, then refuses forever.** This contradicts the project's own
B006(b) rule ("never the consumer's real worktree", `runner.py:1895`,
`cli.py:29`) and the *later* A-292 ruling that "persisting in the caller
repository would violate the dirty-tree contract" — A-292 was written for resume
state while this commit's progress artifact was already doing exactly that,
unreviewed.

### 8a-D (BLOCKING, LIVE ON MAIN) — the preflight probe discards its real outcome and misreports a budget overrun

`src/assay/runner.py:2786-2807`. `execute_plan` correctly classifies the probe,
then `probe_result` is thrown away and the refusal is hardcoded to
`status=Outcome.ERROR, reason_code=ReasonCode.BAD_LANE_CONFIG`. Four
structurally different failures collapse into one indistinguishable verdict,
with `argv_effective` recording **the lane command that never ran**:

| `environment_command` | `execute_plan` returns | verdict emitted | exit |
|---|---|---|---|
| `/nonexistent/probe-binary` | ERROR/EXEC_FAILED | ERROR/BAD_LANE_CONFIG | 2 |
| `sh -c "exit 7"` | FAIL/COMMAND_FAILED | ERROR/BAD_LANE_CONFIG | 2 |
| `sh -c "kill -SEGV $$"` | FAIL/COMMAND_FAILED | ERROR/BAD_LANE_CONFIG | 2 |
| `sh -c "sleep 45"`, `budget = "30s"` | **BUDGET_EXCEEDED/LANE_TIMEOUT** | **ERROR/BAD_LANE_CONFIG** | **2** (should be 4) |

The last row is the false-evidence case this audit was told to hunt: a lane that
genuinely exhausted its budget is recorded as an operator config error. A gate
that retries on `BUDGET_EXCEEDED` but hard-fails on `BAD_LANE_CONFIG` — the
estate's own run-gate shape — does the wrong thing.

**The intended 30 s probe cap is also dead code.** `runner.py:2783` sets
`budget_seconds=min(30.0, deadline.remaining())` on the plan, but `execute_plan`
(`runner.py:320-335`) ignores `plan.budget_seconds` entirely and uses its
`timeout=` argument, which `:2789` passes as the **full** `deadline.remaining()`.
Reproduced: `environment_command = ["/bin/sh","-c","sleep 35"]` with
`budget = "5m"` → `elapsed=35s`. A hung probe consumes the entire lane budget.

### 8a-E (BLOCKING, LIVE ON MAIN) — B010's entire stated deliverable is missing

B010's ask, verbatim from `nyxloom-trove/4-backlog.md:1088`: "refusing with
'this lane's declared environment does not match the invoking one; run via
`<declared wrapper>`' instead of surfacing the suite's raw traceback" — summary:
"refuses with **a clear message**."

Reproduced: stderr is **0 bytes**.

```
$ assay run nonzero --verdict-json vv.json 2>err.txt
--stdout--
nonzero: ERROR/BAD_LANE_CONFIG (exit 2)
  argv: /bin/sh -c 'exit 0'
--stderr(bytes: 0)--
```

The consumer gets neither the raw ImportError nor a clear message — they get a
generic `BAD_LANE_CONFIG` pointing at the lane's own argv, which is *actively
misleading* since that argv never executed. The silent-refusal pattern is
pre-existing (`refuse_lane` carries no free-text diagnostic, cf. B026); what is
new is overloading that terminal with a **third** indistinguishable cause and
shipping it as the fix for a filing whose entire point was diagnosability.

### 8a-F (MINOR, mostly live) — the "constrain progress paths" review fix constrains nothing

The commit message claims a review repair "constrain progress paths". What
shipped is a `_check_wire_path` guard on `Mutation.progress_artifact`
(`verdict.py:1271`) — a field no production code ever populates (8a-B) — plus a
test that only exercises the dataclass. The **actual write path**
(`runner.py:1892`) interpolates the raw lane name with no validation, and
`progress_writer` does `path.parent.mkdir(parents=True, exist_ok=True)`
(`mutation.py:688`). A lane named `"../../../pwned/esc"` (a legal quoted TOML
key; `config.py` defines no lane-name grammar):

```
$ assay run '../../../pwned/esc' --file assay-esc.toml
../../../pwned/esc: PASS (exit 0)
$ find scratchpad -name '*.progress.jsonl'
/tmp/.../scratchpad/pwned/esc.progress.jsonl      <- 3 levels above the project root
```

assay created a directory and wrote NDJSON outside the repository, driven by
repo-controlled `assay.toml` content, and still reported PASS. Two related
sub-issues, both live: the path is **CWD-relative, not project-relative**
(`cd /empty/dir && assay run unit --file <proj>/assay.toml` lands the file in
`/empty/dir/.assay/`, though the schema types this field as `repo_tree_path`);
and it is opened `"a"` and never truncated, so three runs leave one 6-line file
with no run id, commit, or timestamp — a tailing monitor cannot tell which run a
`candidate_index: 0` belongs to.

### 8a-G (MINOR, live) — the two `replacement_sha256` fields mean different things

`mutation.py:713`'s `_progress_event` computes `sha256(whole mutated file)`;
`MutantOutcome.replacement_sha256` in the verdict is `sha256(replacement text)`.
Same field name, same candidate, different values — which defeats the one thing
a progress artifact is for. Measured on one run: verdict `60a33e6c…`
(= `sha256(b"False")`) vs progress `d5435996…` (= whole-file digest).
`_plan_candidate_id` uses the whole-file digest too, so `plan`↔`progress` agree
and both disagree with the verdict.

### 8a-H (MINOR) — remaining items

Zero documentation shipped for three new user-facing config keys and a new CLI
verb: at the commit, `grep -c` for `environment_command`, `budget_per_candidate`
and `assay plan` across `docs/CONSUMERS.md`, `README.md` and
`docs/DESIGN-GUIDE.md` returns **0, 0, 0**. Backfilled by the later remediation
wave; `environment_command` is *still* absent from `docs/CONSUMERS.md` on main.

`mutation.py:1164` declared `per_candidate_timeout_positions: set[int] = set()`
and never read or wrote it — the vestige of B012 requirement 6's actual point,
that a per-candidate timeout and a lane-budget exhaustion be distinguishable. As
shipped both land in the same `budget_exceeded` bucket with no distinguishing
field. `e2169d46`'s pyflakes sweep removed the dead local; the underlying
indistinguishability is unchanged. The same commit shipped
`def progress_writer(path: Path) -> Iterator[ProgressWriter]` with `Iterator`
never imported (fixed later by `e2169d46`).

`config.py:1782-1788` (at the commit `:1665`) has `if "kill_signal_artifact" in value:`
dedented out of the `if language == "sql":` block while its body kept the inner
12-space indent. Behaviourally a no-op (the `_MUTATION_SQL_ONLY_FIELDS`
reserved-key check above already refuses it on a non-sql lane), but the comment
20 lines above still asserts "neither is even INSPECTED for a non-sql lane",
which the code now contradicts. Still misindented on main.

Beyond 8a-A, `_cmd_plan` never runs `lane.environment_command` (so plan happily
plans a lane `run` would refuse) and plans against HEAD without `run`'s
clean-tree precondition. Its runtime estimate falls back to a fabricated
`60.0` s/candidate when `budget_per_candidate` is absent, reported to three
decimals as `estimated_serial_seconds`; when the key *is* declared it uses the
timeout (an upper bound) as an "estimate". B012 requirement 2's
"measured/estimated baseline runtime" is not implemented at all.

### On the commit message's "with review fixes"

The commit names five review repairs. "Constrain progress paths" constrains a
field nothing populates (8a-F). "Refresh the locked v6 schema snapshot"
refreshed it to byte-identically-wrong content (8a-B(d)). The other three (probe
lookup validation, packaged-schema resolution, uncommitted-source refusal) are
real — though the gate check
`[[ -n "$(git -C "$worktree" status --porcelain=v1 -- assay)" ]]` in
`tools/tester-unified-gate.sh` fails open on any git error, the bare-returncode
pattern already recorded in nyxloom LESSONS L25 (reasoned, not reproduced).
Given that no independent review report exists and that a genuinely independent
reviewer later found four *more* CLI-reachable defects in the same feature
(A-296), "review fixes" here should be read as **self-review only**.

---

## Appendix — adjacent finding, not attributable to the six

`resolve_command_plan` (`src/assay/runner.py:445-459`, introduced by
`e2169d46` / A-303) refuses an `env_passthrough` name colliding with a plain
`lane.env` key using the message "collides with a declared **infrastructure
fact** of the same name" — even when the lane declares no `[infrastructure]`
table at all. Reproduced by constructing a `Lane` with `env={'FOO': …}`,
`env_passthrough=('FOO',)`, `infrastructure={}`. Reachable only by direct
construction (the loader refuses the overlap first), so this is a misleading
diagnostic rather than a correctness defect. Belongs against B022, not against
any of the six commits.

## Summary

**Zero of the six are clean. Four of the six are BLOCKING; two are MINOR.**

| commit | subject | verdict | blocking findings |
|---|---|---|---|
| `ba8908d6` | whole-target SQL targets, declared env forwarding | **BLOCKING** | 4 (ba-A…ba-D) |
| `8a2a4731` | B010/B012 preflight + mutation observability | **BLOCKING** | 5 (8a-A…8a-E) |
| `37462618` | B014 bounded command output tails | MINOR | 0 (5 minor) |
| `126ef577` / `6324548d` | B015 semantic Python mutation operators | **BLOCKING** | 3 (B015-A…C) |
| `f3ce3d0a` | W2 verdict schema v7 successors | **BLOCKING** | 1 (W2-A) |
| `6b777274` | v7 gate/test migrations | MINOR | 0 (1 minor) |

Thirteen blocking findings in total. **Nine are still live on `main` today**:
ba-A, ba-B, ba-C, ba-D (cross-repo, fix belongs in dstdns), 8a-A, 8a-C, 8a-D,
8a-E, and all three B015 findings — plus 8a-B's `verify.py` half. Four were
repaired later inside the same waves (W2-A by `56d6c2c5`; `37462618`'s
unbound-`exc` timeout crash by `6b777274`; 8a-B's schema misplacement by
`7941fdcb`).

### Same bug shape as the recent wave, or new?

**Mostly the same shapes — which is the point.** Mapping the thirteen onto the
four shapes this audit was told to hunt:

- **A field never wired into `verify.py`'s reconstruction layer** — the exact
  near-miss shape. **8a-B(c)** is a direct hit and is live: a real verdict
  carrying `mutation.progress_artifact` (or `candidate_ids`) passes JSON Schema
  validation and is rejected by `assay verify` as an unknown field. **B014-A** is
  the mirror image at the top level: the verifier enforces a pairing the shipped
  schema does not encode.
- **Stale / structurally-blind frozen artifacts** — **8a-B(d)**: the W1 v6 lock
  was refreshed to byte-identically-wrong content, so the byte-equality drift
  guard stayed green through a correct-shape/wrong-place schema addition. The
  guard cannot catch that class by construction. (The *specific* drift from the
  recent near-miss — `verdict.schema.json` vs `W2/verdict.schema.v7.json` — is
  clean today.)
- **A gate-facing surface outside `tests/`** — **W2-A**: a `NameError` merged
  into `tools/tester-unified-gate.sh` 87 seconds before its fix. `pytest tests/`
  cannot execute that file, so nothing local was red.
- **Asymmetric handling of two structurally similar paths** — **ba-C** (R2's
  whole-target resolver silently drops targets where R1's refuses them, with R1's
  docstring naming the vacuity hole R2 reopened), **B015-D** (the semantic-site
  loop mis-tracks `left` where the compare-swap loop one function away does not),
  and **ba-G** (the two run-gate runners disagreed on empty-string forwarding
  inside the same diff).
- **Silent swallowing of a crash/timeout into a wrong verdict** — **8a-D**: a
  preflight probe that genuinely exhausts its budget is recorded
  `ERROR`/`BAD_LANE_CONFIG` instead of `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, exit 2
  instead of 4, with `argv_effective` naming a command that never ran. This is
  the same family as B027 and is still live.
- **Documented behaviour the code does not implement** — the largest group:
  **8a-A** (`assay plan` always reports zero candidates while CONSUMERS.md
  describes working output), **8a-E** (B010's "refuse with a clear message" ships
  0 bytes of stderr), **8a-B** (`progress_artifact` never populated by any code
  path), **B015-A** (operators that add no mutations), **ba-A** (a `base`
  recorded for a comparison that never ran, forbidden by the emitting function's
  own docstring), **ba-F** (`whole_file_r2` silently changes Python R2 behaviour,
  undocumented anywhere), **B014-B/C**.

**One genuinely new shape**, worth adding to the project's lessons: **a test or
oracle written to match observed output rather than the requirement.** Three
instances, none of which any wave's `pytest tests/` could ever fail —
`test_plan_reports_candidates_without_executing` asserting `candidate_count == 0`
on a fixture that genuinely has one candidate (8a-A);
`test_b015_operators_are_schema_and_verifier_compatible` asserting only the
absence of one substring while the document is in fact rejected three ways
(W2-B); and `_assert_complete` adding all four B014 fields to its `volatile`
ignore-set (W2-D), alongside `ebdd8f6c` repairing a fixture to accept the
"captured and empty" misstatement rather than fixing it (B014-B). A green suite
is not evidence when the assertions were derived from the behaviour.

### On the review gap itself

The correlation is close to perfect. The two commits that got no review and no
decision record (`ba8908d6`, `8a2a4731`) carry nine of the thirteen blocking
findings between them. `ba8908d6` shipped with **zero tests, zero docs, zero
A-numbers**. `8a2a4731` shipped three config keys and a CLI verb with zero
documentation, and its "with review fixes" describes self-review. B014 and B015
— a `VERDICT_SCHEMA_VERSION` hard cut and an extension of the closed operator
vocabulary, the two changes in this range most clearly governed by existing
rulings (A-170, A-112/A-114/A-220) — have **no A-numbered decision at all**
(`grep -c` for both returns 0).

B015 is the sharpest case: its own backlog filing contained an explicit
pre-dispatch requirement to "decide explicitly whether any proposed site overlaps
`compare-swap` enough to be indistinguishable evidence; reject or split
accordingly," and the shipped implementation overlaps it totally. The one
acceptance criterion that would have caught it — "a real R2 lane demonstrates
kills attributable to each admitted family" — is the one box left unchecked while
the item was marked `IMPLEMENTED`.

### Recommended triage order (human decision, not filed here)

1. **8a-C** — an R2 lane passes once then refuses forever with `DIRTY_TREE`. The
   most likely to be biting a consumer right now.
2. **8a-A** — `assay plan` is entirely non-functional, and its test must be
   corrected before any fix, or the fix will look like a regression.
3. **B015-A/B/C** — decide whether the two operators earn their place at all;
   removing them is a vocabulary change and therefore a schema question.
4. **ba-C / ba-B** — silent target-dropping and the SQL vacuity carve-out; both
   can render a real `PASS`/`FAIL` over a silently narrowed scope.
5. **8a-D**, **8a-B(c)**, **ba-A**, **ba-D** (dstdns-side), then the minors.
