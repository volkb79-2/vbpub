# assay wave B074 + B077 — adversarial code review, round 1

Reviewer: fresh session, no prior context on this wave.
Branch: `feat/assay-b074-b077-quickwins-2026-09-08` @ `b3a33415`
Merge-base: `9c2c435f` · Worktree: `/workspaces/vbpub/.worktrees/assay-b074-b077-quickwins`

## Verdict: **ACCEPT-conditional** — 3 blockers, 1 decision ask for the controller

The implementation is **correct**. I drove both items end-to-end myself and
they do what the backlog asked. What holds the merge is a **test-coverage
hole on B074's two headline acceptance lines** (Blocker 1) — the feature
could ship dead-on-arrival with a green gate — plus a diagnostic-message
regression this wave itself creates at R2 (Blocker 2).

Everything the controller flagged for independent verification came back
clean; details in "Verified good" below, with the evidence.

---

## Method

Blind phase first: read the wave prompt, then B074/B077 in `4-backlog.md`,
then the full diff, and formed a view **before** opening the implementer's
LOG/REPORT. Then: 4 hand-built end-to-end probes through the real `main()`,
a merge-base-vs-branch verdict byte-comparison, an empirical `git
check-ignore` traversal probe, a 10-mutant targeted battery, and two full
4359-test suite runs (one clean baseline, one mutated). All probe files
removed; the worktree is pristine (`git status --short` empty).

Host discipline: no gate container launched (a sibling B078 gate was
running throughout, correctly left alone); everything under
`nice -n 19 ionice -c 3`, load 2.25 at start.

---

## Blockers

### Blocker 1 (HARD) — the flag's entire runner plumbing is untested; both of B074's headline acceptance lines are unprotected

**Evidence.** I replaced BOTH forwardings in `runner.py` with a literal
`False`, in place, and ran the whole suite:

* `src/assay/runner.py:1620-1628` (`evaluate_r1` → `evaluate_targets`)
  → `allow_test_path_targets=False,`
* `src/assay/runner.py:3466-3475` (`_run_prepared_lane` → `JudgmentR1`)
  → `allow_test_path_targets=False,`

Result: **`4359 passed, 20 skipped` — fully green.**

With those two mutations live, `judge.allow_test_path_targets = true` in a
real `assay.toml` has **no effect whatsoever**: the lane still refuses
`ERROR`/`BAD_LANE_CONFIG`, and `judgment.r1.allow_test_path_targets` is
never emitted. Both mutants are the whole feature deleted, and the
registered gate would still be GREEN.

That is exactly acceptance lines **1** and **5** of B074's own box:

> a `whole_target` lane naming `tests/<...>/lib.py` WITH the flag set is
> JUDGED …
> the flag appears in the verdict's resolved judgment …

Both are currently proven only at the *function* level —
`_resolve_whole_target(..., allow_test_path_targets=True)`,
`evaluate_targets(..., allow_test_path_targets=True)`,
`JudgmentR1(..., allow_test_path_targets=True)`. **No test anywhere drives a
LANE.** The two sentences say "a lane", and nothing in the suite connects
`JudgeConfig.allow_test_path_targets` to either consumer. `grep -rl
allow_test_path_targets tests/` returns exactly three files, none of which
calls `runner.evaluate_r1`, `runner._run_prepared_lane` or `cli.main`.

This is the project's own recurring defect species — REPORT row 1 and row 5
cite unit-level tests as evidence for a lane-level claim, and the gate
cannot tell the difference.

**The implementation itself is right.** My own probe, a real `assay.toml`
with `mode = "whole_target"`, `targets = ["tests/_harness/lib.py"]`,
`allow_test_path_targets = true`, driven through `cli.main(["run", ...])`:

```
unit: PASS (exit 0)
judgment.r1 = {
  "allow_excluded": false,
  "allow_test_path_targets": true,
  "coverage_artifact": "cov.json",
  "coverage_format": "coverage-py-json",
  "fail_under": 100.0,
  "mode": "whole_target",
  "require_branch": false,
  "targets": ["tests/_harness/lib.py"]
}
```

and the same lane with the flag line deleted:

```
assay: ERROR/BAD_LANE_CONFIG: judge.targets entry 'tests/_harness/lib.py'
is a test path per the adapter's own convention. If this file is library
code that merely LIVES under a test directory -- deployed helper modules
under tests/ are the named case -- declare judge.allow_test_path_targets =
true on this lane to assert that, which the verdict then records
```

So this is a **test blocker, not a behaviour blocker**. Nothing about the
shipped behaviour needs to change.

**Prescription.**

1. Add an end-to-end test — a real `assay.toml` through `cli.main(["run",
   ..., "--verdict-json", ...])` — asserting (a) exit 0 / `outcome ==
   "PASS"`, and (b) `judgment.r1["allow_test_path_targets"] is True`; plus
   the controlled negative, the identical lane with the flag line removed,
   asserting `ERROR`/`BAD_LANE_CONFIG`. Both must fail under either
   mutation above. (My probe is reproducible from the two blocks quoted
   here; the lane needs the coverage artifact gitignored and written by the
   lane's own argv, not committed.)
2. Add `allow_test_path_targets: bool | None = None` to
   `tests/conftest.py::make_r1_judge` (line 730-742) — the project's
   standard R1-judge helper cannot currently build a judge carrying the
   flag at all, which is why the gap exists. A `runner.evaluate_r1`-level
   test through that helper kills mutation 1 on its own.

---

### Blocker 2 (MEDIUM) — R2 refuses with a message that contradicts a flag the operator just set, and the documented R2 behaviour has no oracle

`CONSUMERS.md` (new table row 4) makes a factual claim:

> It does not reach **R2**. Whole-target mutation … applies its own
> test-path gate and still refuses a test-path target by name. … Declare
> such a lane R1-only, or move the file.

Correct as stated, and a defensible scope call. Two problems with it.

**2a. The message.** `src/assay/runner.py:2964-2970` still reads, verbatim
and unchanged by this wave:

```
mutation target 'tests/_harness/lib.py' is a test path per the adapter's
own convention
```

Measured end-to-end on an `R0+R1+R2` whole-target lane with the flag set
and a `tests/` target:

```
=== EXIT 2
assay: ERROR/BAD_LANE_CONFIG: mutation target 'tests/_harness/lib.py' is a
test path per the adapter's own convention
  in lane 'unit', resolving R2 whole-target mutation targets
=== claims
  R0 PASS
  R1 PASS      (judgment.r1.allow_test_path_targets = true)
  R2 ERROR/BAD_LANE_CONFIG
```

The verdict therefore records `allow_test_path_targets: true` — assay
accepting the lane's claim — while the same run refuses the same target
with a sentence that names neither the flag nor the remedy. **This wave
created that contradiction**: it upgraded the R1 twin of this exact message
to name the flag and the fix (`evaluate.py:1104-1116`), and left its R2
twin as the old bare sentence. That is precisely the "a real, fail-closed
refusal with a message that does not tell a correctly-configured consumer
what to do about it" shape that `_linked_worktree_gap()` (B068), the
round-1 N2 guard, and B077 *in this same wave* all exist to close.

*Prescription:* extend the message at `runner.py:2964-2970` with the clause
CONSUMERS.md already words — that `judge.allow_test_path_targets`
deliberately does not reach R2 because mutating a file is a different claim
from measuring it, and that the remedy is an R1-only lane or moving the
file. One sentence; no behaviour change.

**2b. No test pins it.** The CONSUMERS.md row asserts a behaviour with no
oracle. If a later wave relaxes the R2 gate, the docs go stale silently and
the gate stays green. *Prescription:* a test running an `R1+R2`
whole-target lane with the flag over a `tests/` target, asserting R1 `PASS`
with the flag recorded and R2 `ERROR`/`BAD_LANE_CONFIG` naming the target.

---

### Blocker 3 (LOW — controller may reasonably downgrade to a note) — the per-adapter split is a hand-copied table, not derived from the registry

`tests/test_evaluate_whole_target_allow_test_path.py:266-291` proves the
directory/filename split for `PythonAdapter`, `JavaScriptAdapter` and
`SqlAdapter` by hand, plus `GoAdapter` in a separate test. All four are
correct — I re-derived each regex myself:

| adapter | rule | basename can match the directory branch? |
|---|---|---|
| Python | `(^\|/)(tests/\|test_[^/]*\.py$\|conftest\.py$)` | no — `tests/` needs the slash |
| JavaScript | `(^\|/)(__tests__/\|[^/]+\.(test\|spec)\.(c\|m)?[jt]sx?$)` | no |
| SQL | `(^\|/)(tests?/\|test_[^/]*\.sql$\|[^/]*_test\.sql$)` | no |
| Go | `rel_path.endswith("_test.go")` | n/a — no directory branch at all, so the flag is correctly inert |

But `evaluate.py:968-995`'s docstring claims the split holds "for every
adapter shipped today, **and for any future one**", and CHANGES.md +
CONSUMERS.md both say "in every adapter". Nothing enforces that: a fifth
registered adapter is not forced into the table, and the claim silently
becomes false. This project's own A-270 discipline is that such sets are
"DERIVED from the shipped module, never hand-copied"
(`test_docs_examples_and_vocabulary.py` does exactly that for every closed
vocabulary).

*Prescription:* parametrize over `assay.cli._built_in_registry().entries`
(it holds `python`, `sql`, `javascript`, `go` — verified) with a
per-language directory/filename case table, and assert every registered
language has a case, so adding an adapter without a case turns the suite
red rather than quietly weakening the claim.

---

## Decision ask for the controller (not mine to make)

**Should `judge.allow_test_path_targets` reach `runner._mutation_targets_whole`
(R2's DECLARED-target test-path gate)?**

The backlog's ruling names only the two **sweep-side** sites as untouched —
`evaluate.py:428` and `mutation.py:470`, where "paths arrive from a diff and
nobody has vouched for them". `runner._mutation_targets_whole:2964` is a
**third** site the backlog never mentions, and it is *not* sweep-side: it
resolves the same explicit `judge.targets` list that `_resolve_whole_target`
does, one tier down. Its own docstring says so — "This is R1's own rule, one
tier down: `assay.evaluate._resolve_whole_target` refuses the identical
shapes."

So B074's central argument — "a `judge.targets` entry is not a swept path; it
is an explicit, reviewed, per-lane declaration" — applies to it verbatim, and
the flag not reaching it means B074's own repro consumer (dstdns
`tests/_harness/`, `COPY`-ed into an image and run as a real service) can put
that code under R1 but **never** under R2. The implementer considered this,
made a deliberate call, documented it in `config.py:2013-2025` and CONSUMERS.md,
and reasoned it as "mutating a file to see whether a suite notices is a
different claim from measuring that file's coverage" — which is a coherent
position, not an oversight.

I am not deciding it. Blocker 2 stands either way (the message must
acknowledge the flag, whichever way this goes); this is the scope question
underneath it.

---

## Verified good — the controller's three items, and the standard checklist

**Controller item 2 — the `427157c1` schema fix is correct and complete, and
the other hard-cut guard was not weakened.** Both halves confirmed
independently:

* `diff src/assay/schemas/verdict.schema.json
  nyxloom-trove/carve-assets/W7/verdict.schema.v11.json` → **IDENTICAL**;
  `test_shipped_schema_is_byte_identical_to_the_locked_v11_asset` passes.
* The hard cut is intact and untouched:
  `test_every_earlier_frozen_template_is_rejected_under_v11` → **34 passed**
  (W1 6 + W2 6 + W4 6 + W5 7 + W6 9 = 34, matching the LOG's claim). The
  branch's only `carve-assets/` change is `W7/verdict.schema.v11.json`;
  `git diff --name-only 9c2c435f..b3a33415` shows W1–W6 completely untouched.
  The guard was not loosened to make the fix pass.

**Controller item 1 — the field is genuinely additive, wired in every place,
and a non-opting lane's verdict really is byte-identical.** Proven, not
taken on faith. I ran the *same* lane over the *same* repo at the *same*
commit twice — once with `PYTHONPATH` at the merge-base `9c2c435f` source
(`git archive`d to scratch), once at the branch — and diffed the two
verdicts modulo timestamps/env:

```
IDENTICAL
r1 keys base  : ['allow_excluded','coverage_artifact','coverage_format',
                 'fail_under','mode','require_branch','targets']
r1 keys branch: ['allow_excluded','coverage_artifact','coverage_format',
                 'fail_under','mode','require_branch','targets']
```

Three-place wiring is complete: schema (`verdict.schema.json:1526-1529` +
the `allOf` `whole_target` guard at 1556-1575, `const: true` so `false` is
unrepresentable), dataclass (`verdict.py:2052-2064`, `__post_init__` type +
mode guards at 2103-2119, `to_dict` present-iff-true at 2163-2168), and
`verify.py:1659-1664`. `require_branch`'s own registration sites were used
as the reference; `allow_test_path_targets` correctly stays OUT of the
schema's `required` list because absence *is* false. It does not belong in
`assay lanes --json` — that inventory carries neither `mode`, `targets` nor
`require_branch` either, so omitting it is consistent.

**The ambiguous edge case is really resolved and really enforced.** With the
flag set, `tests/_harness/test_lib.py` and `tests/_harness/conftest.py` both
still refuse, naming FILENAME and "does not override"; so does
`src/conftest.py` (a filename positive with no `tests/` segment involved).
`_is_test_filename` (`evaluate.py:996`) is a genuine basename-only predicate
(`adapter.is_test_path(PurePosixPath(rel_path).name)`), correct for all four
shipped adapters per the table above. Mutating it to `return False` and to
`return adapter.is_test_path(rel_path)` were both KILLED.

**Evasion probes, both items — all planted violations caught.**

* B074 controlled negative (no flag, same target) → `ERROR`/`BAD_LANE_CONFIG`
  naming the target, the gate AND the remedy. Verified end-to-end through
  `main()`, not just at the unit level.
* `changed_lines` sweep untouched — the strongest available form:
  `evaluate_coverage` takes no such parameter at all, asserted directly
  against `__code__.co_varnames`. `mutation.py` is not in the diff.
* The other four `_resolve_whole_target` gates (symlink, source-root
  containment, regular-file, excluded-directory, adapter-recognised-source)
  are byte-unchanged in the diff and each has a test that runs it **with the
  flag set**. None loosened.
* B077's two already-correct outcomes: proven, not asserted. A destination
  genuinely outside the repo never reaches the guard (`_containments`
  returns no pair); a destination inside the tree with no symlink is
  unaffected (the guard is a no-op and `path_is_ignored` answers).
* **B077 over-refusal probe (mine, not in the suite):** I checked
  empirically whether the new guard now refuses a question git *could*
  answer. It does not — `git check-ignore` returns
  `fatal: pathspec 'link/rec.json' is beyond a symbolic link` (rc=128) for a
  directory symlink whether that symlink is **tracked or untracked**. So the
  guard strictly replaces an opaque `GIT_FAILED` with a named refusal and
  narrows nothing. The final-position exclusion (`probe.parts[:-1]`) is
  correct and pinned by `test_the_guard_probes_directory_components_only`;
  mutating it to `probe.parts` was KILLED.

**Hollow-test battery — 10 targeted mutants, 10 KILLED.**
`_is_test_filename` → `False`; `_is_test_filename` → full path; the flag
branch → always-allow; the cli guard call removed; the guard probing all
components; `verify.py` dropping the field; the schema's `const: true`
relaxed to `"type": "boolean"`; the dataclass mode-guard removed; `to_dict`
always emitting; the config mode-placement check removed. The only survivors
in the whole battery are the two in Blocker 1.

**Forbid-list integrity — clean.** 16 files, every one inside B074+B077's
remit; nothing unrelated touched. The 8 disclosed pre-existing "B074"
comments are genuinely pre-existing: `git grep -n B074 9c2c435f --
'assay/src/assay/*'` finds 5 in `src/` at the merge-base
(`adapters/go_stmtpos.py`, both coverage parsers, `verify.py` ×2), all
meaning the RecursionError sweep the backlog itself renumbered to B075.
Correctly out of scope. Also worth the controller's note: no source file in
this diff has moved on `origin/main` since the merge-base — `git diff
9c2c435f origin/main` over all eight touched source paths is empty — so
CHANGES.md really is the only merge conflict.

**Consumer dimension — met.** `docs/CONSUMERS.md:313-380` carries a full,
paste-able lane file plus the four-row "what it does not do" table, and it
is a *live* example: `test_docs_examples_and_vocabulary.py` extracts every
unmarked ` ```toml ` fence and loads it through the real `load_lane_file`,
and the new block carries no skip marker. B077 also got its own paragraph
in the resume/shard section and a correction to the `--progress` reference
table.

**LOG/REPORT accuracy.** Read after forming the above. Both are accurate and
unusually honest — the red-then-green gate history, the reason code call,
the directory-components-only measurement, the CHANGES.md conflict, the
pre-existing ID collision, and the transient venv-swap failure are all
disclosed and all check out against what I independently measured. The one
gap is that REPORT rows 1 and 5 present unit-level tests as satisfying
lane-level acceptance sentences (Blocker 1), and the binding-constraints
table does not mention the R2 decision at all — it is in the LOG
(§ "Deliberately NOT touched") but not in the acceptance box, where a
reader checking the boxes would meet it.

---

## Non-blocking notes

* **N1.** `docs/DESIGN-GUIDE.md` gained no entry, while both comparable
  optional judge flags have one (`require_branch` at §"require_branch governs
  absence, never presence (A-259)", `base_source` at 1262-1273). The wave
  prompt bound only CONSUMERS.md, so this is not a blocker — but the pattern
  says a flag like this gets a DESIGN-GUIDE paragraph explaining the ruling.
* **N2.** `verdict.schema.json:1527`'s `description` poses the field as "did
  this lane grade a whole target that assay's own adapter test-path
  convention would otherwise have refused?" The field does not record that.
  It records the DECLARED/effective policy: a lane that sets the flag and
  names no test path still emits `true`. The dataclass comment
  (`verdict.py:2052`) gets this right ("the EFFECTIVE … policy"); reword the
  schema description to match.
* **N3.** `verdict.py:2056-2060` gives the narrower of two reasons the field
  is additive ("such a lane refused `BAD_LANE_CONFIG` and emitted no
  `judgment.r1` at all"). The load-bearing reason is simpler and absolute: a
  pre-B074 loader rejects `allow_test_path_targets` as a surplus judge key,
  so **no** pre-B074 lane could declare it, whatever its targets. Both are
  true; the second is the one that makes the argument airtight.
* **N4.** The `B074`-means-B075 comment collision is now worse in absolute
  terms — one token, two meanings, 14 new instances of the new meaning. The
  disambiguating note at the head of
  `tests/test_evaluate_whole_target_allow_test_path.py:378-386` is the right
  minimum. Recommend a backlog note (not a fix in this wave) to retitle the
  5 stale `src/` comments to B075.
* **N5.** B077's `- [ ]` acceptance boxes in `4-backlog.md` are unticked. The
  implementer's stated reason (shared file, concurrent B078 wave) is sound;
  tick at merge.

---

## Summary

| item | correctness | acceptance box | tests |
|---|---|---|---|
| B074 | correct — verified end-to-end | lines 2/3/4 fully met; lines 1/5 met in behaviour, **not defended by any test** | Blocker 1 |
| B077 | correct — verified end-to-end, incl. an over-refusal probe the suite does not do | all 3 lines met | solid; 3 of 10 mutants targeted it, all killed |

Nothing here requires rethinking either design. Blocker 1 is one end-to-end
test plus a two-line conftest helper change; Blocker 2 is one sentence in an
existing message plus one test; Blocker 3 is a parametrization swap. The
decision ask is genuinely open and belongs to the controller.
