# assay wave B074 + B077 — implementer LOG (2026-09-08)

Branch: `feat/assay-b074-b077-quickwins-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b074-b077-quickwins`
Base: `9c2c435f` (main, after B070's v11 merge)
Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b074-b077-quickwins.md`

## Commits

| hash | item | what |
|---|---|---|
| `991ede05` | B074 | `judge.allow_test_path_targets` end to end: loader, evaluator, runner, verdict model, packaged schema, `verify` reconstructor, `docs/CONSUMERS.md`, `CHANGES.md`, tests |
| `dc944932` | B077 | `_refuse_a_destination_reached_through_a_symlink` in `cli.py`, regression tests for both flags, `docs/CONSUMERS.md`, `CHANGES.md` |
| `427157c1` | B074 | gate-caught follow-up: move the locked W7 v11 schema asset with the shipped one |
| `15258dfc` | B074 | **fix round** — round-1 blockers 1/2/3, the controller's R2 ruling, and notes N1/N2/N3 |

Gate-verified commit: **`15258dfc85688f03efe23cfdeed1a265b1f5dfa2`**
(`scratchpad/b074-b077-gate3.log`). The pre-review green at `427157c1` is
superseded but recorded below.

---

## Item 1 — B074

### The two open forks, decided

**(a) A target that is a genuine test file even WITH the flag set.** The wave
prompt left this to be ruled and stated. **Ruled: it still refuses.** The flag
overrides only the DIRECTORY half of an adapter's test-path convention; the
FILENAME half stays in force in every adapter.

The reasoning: `judge.allow_test_path_targets` is a claim about a repository's
LAYOUT — "this directory holds library code, whatever it is named". It is not a
claim about a file that names itself a test. Grading a test file's own coverage
is exactly the vacuity `whole_target` exists to close, so the guard-rail the
backlog's Shape 1 was preferred *for* would be gone if the filename half went
with it. `tests/_harness/lib.py` is admitted; `tests/_harness/test_lib.py`,
`tests/_harness/conftest.py` and `src/conftest.py` are all still refused, with
a message that says *why the flag did not help* rather than repeating the
generic refusal the flag was meant to answer.

The split is taken by `evaluate._is_test_filename`, which asks the adapter
about the path's bare BASENAME. A basename has no directory segments, so a
`True` means some filename branch of the adapter's own rule fired — for every
adapter shipped and any future one, without this module re-deriving any
language's convention and **without changing the `LanguageAdapter` surface**
(no Protocol-stub blast radius). Verified directly against all four real
adapters — and, after the fix round, over the set DERIVED from
`cli._built_in_registry().entries` with a completeness guard, so this table
cannot fall behind a fifth adapter (review Blocker 3):

| adapter | directory case (admitted) | filename case (still refused) |
|---|---|---|
| Python | `tests/_harness/lib.py` | `tests/_harness/test_lib.py`, `conftest.py` |
| JavaScript | `__tests__/helper.ts` | `__tests__/helper.test.ts` |
| SQL | `tests/ddl/schema.sql` | `tests/ddl/schema_test.sql` |
| Go | — (no directory branch exists) | `*_test.go`, so the flag is inert for Go |

**(b) Verdict visibility vs. "no verdict-schema change".** The wave prompt says
both "no verdict-schema change in this wave" and (in the acceptance box) "the
flag appears in the verdict's resolved judgment". Those are in tension: there
is no free-form section in the verdict, `JudgeConfig.as_declared()` feeds
`assay lanes` and never the artifact, and `judgment_r1` is
`additionalProperties: false`.

**Ruled: implement the acceptance line as an ADDITIVE optional field, with no
`VERDICT_SCHEMA_VERSION` bump.** `judgment.r1.allow_test_path_targets` is
emitted **only when true**, so:

* a lane that did not opt in writes a byte-identical verdict (asserted, not
  asserted-in-prose — `test_allow_test_path_targets_is_absent_from_a_lane_that_did_not_opt_in`);
* a pre-B074 **loader** refuses `allow_test_path_targets` as a surplus judge
  key, so no older assay can emit it at all, whatever a lane's targets — the
  absolute form of the argument (review N3, folded in during the fix round;
  the narrower "such a lane used to refuse `BAD_LANE_CONFIG` and emitted no
  `judgment.r1`" is also true but weaker);
* a consumer that DOES opt in is by construction on the assay that grew it.

In-version schema edits are ordinary practice on this project, and the
precedent is exact: `5b2730b6` (B051/A-437) edited the shipped schema **and**
the locked W6 v10 asset in one commit, inside v10's life, with no bump. Four
earlier commits (`86ceb527`, `6e0dca84`, `ae09425d`, `e2169d46`) did the same
between cuts. This is that pattern.

### What was built

* **`config.py`** — `judge.allow_test_path_targets`, `bool | None` (`None` =
  absent from the file, never "assay chose false"), in `_KNOWN_JUDGE_FIELDS`,
  in `as_declared()`, and exempt from the `surplus` sweep alongside `mode` /
  `require_branch` / `base_source`. Refused by name in two placements, both
  inert per A-062: not under `whole_target` mode, and not on a lane without R1.
* **`evaluate.py`** — `_resolve_whole_target(..., allow_test_path_targets=False)`
  relaxes ONLY the sixth gate, and only its directory half. The other five
  (symlink, source-root containment, regular-file, excluded-directory,
  adapter-recognised-source) are unchanged and each has a test that they still
  refuse WITH the flag set. `evaluate_targets` forwards the flag and reads it
  nowhere else.
* **`runner.py`** — resolves the effective `False` (absent means `False`) at
  the places `effective_mode`/`effective_require_branch` are already resolved:
  two initially, three after the fix round added R2's
  `_mutation_targets_whole`. All three forwardings are mutation-tested.
* **`verdict.py` / schema / `verify.py`** — registered in one commit across all
  three, per `_reconstruct_judgment_r1`'s own standing rule. `to_dict` emits
  iff true; `__post_init__` refuses `True` outside `whole_target`; the schema
  constrains it to `const: true` and to `whole_target` via a second `allOf`
  branch; the reconstructor reads it with an absent-means-`False` default.
* **`docs/CONSUMERS.md`** — a worked, paste-able lane (validated by the
  project's own `test_docs_examples_and_vocabulary.py`, which loads every live
  example through the real loader) plus a four-row table of what the flag
  deliberately does not do. Row 4 was rewritten in the fix round when R2 came
  into scope. **`docs/DESIGN-GUIDE.md`** gained the flag's own section there
  too (review N1).

### Deliberately NOT touched

`evaluate.py:428`'s sweep-side `is_test_path` and `mutation.py:478`'s — per the
ruling. Both resolve paths swept out of a **diff**, which nobody has vouched
for; neither takes the flag as a parameter at all.

**`runner._mutation_targets_whole` was a third site, and I got its disposition
wrong.** I originally left R2's DECLARED-target gate out, reasoning that
"mutating a file to see whether a suite notices is a different claim from
measuring that file's coverage", and documented that in `CONSUMERS.md` and
`config.py`. Round-1 review surfaced it as a decision ask and the controller
**ruled the other way: extend the flag to it.** The ruling is right and my call
was wrong — that gate's own docstring says it is "R1's own rule, one tier
down", it resolves the *same* declared `judge.targets` list, so B074's central
argument (an explicit declaration is a reviewed assertion, not a swept path)
applies to it verbatim; there is no safety asymmetry, because mutation runs in
an ephemeral snapshot and never touches a deployed artifact; and my version
left B074's own motivating consumer coverage-gradeable but never
mutation-gradeable, an incomplete fix for the one case the entry exists for.
See § "Fix round" below.

---

## Item 2 — B077

### Reproduced first, at the git level

```
$ git check-ignore -q -- statelink/0000.json
fatal: pathspec 'statelink/0000.json' is beyond a symbolic link
rc=128
```

…and end to end, through the real CLI, with the guard removed (the controlled
wrong implementation, run deliberately):

```
assay: ERROR/GIT_FAILED: git check-ignore store-link/records/<64 zeros>.json
  failed (128): fatal: pathspec 'store-link/...json' is beyond a symbolic link
assay: ERROR/GIT_FAILED: git check-ignore store-link/progress.jsonl
  failed (128): fatal: pathspec 'store-link/progress.jsonl' is beyond a symbolic link
```

Both flags, the reviewer's exact repro (a **committed** symlink inside the tree
pointing at a **gitignored** directory — a consumer who configured this
correctly).

### The fix

`cli._refuse_a_destination_reached_through_a_symlink`, called from the shared
`_refuse_a_visible_store_inside_the_tree` immediately after round-1's N2
pathspec-magic guard and **before** `git.path_is_ignored`. It walks the probe's
DIRECTORY components under the root and, on the first symlink, refuses naming
the link, its target, and the real path to pass instead.

Two calls the reviewer's fix shape left open, and how they went:

* **Reason code.** `ERROR`/`BAD_LANE_CONFIG` (via `LaneConfigError`), not
  `GIT_FAILED`. The entry's own "or a more specific reason code, if one already
  exists" clause is answered by the two sibling refusals in the very same
  function, which already use it for this class of before-any-work destination
  mistake. Git did not fail; the destination cannot be asked about.
* **Which components.** `probe.parts[:-1]` only. Measured, not assumed:
  `check-ignore` answers **normally** (rc=1) about a path whose LAST component
  is a symlink — there is nothing "beyond" it — so probing every component
  would refuse a question git can answer. Asserted directly against the helper,
  because both CLI flags refuse a final-position symlink even earlier for their
  own separate, pre-existing reasons (`--state-dir` requires a directory,
  `--progress` an ordinary regular file), so the scoping is not observable end
  to end through either flag.

The message quotes git's phrasing (`'fatal: pathspec ... is beyond a symbolic
link'`) inside an explanatory sentence, deliberately: an operator who already
met the raw error recognises it. The tests assert the raw *passthrough* form
(`fatal: pathspec '<path>'`, with the path interpolated) is gone, and that
`GIT_FAILED` is gone.

### Both already-correct outcomes proven unchanged

* a destination genuinely outside the repository — including one whose own
  parents are symlinks — still runs to completion;
* a destination with no symlink involved is untouched; the two shipped SF-1
  probes (C and D, symlinks *outside* the tree pointing *at* it) still refuse
  `DIRTY_TREE` and are unaffected, because the guard fires only on a symlink in
  a directory position *relative to the root*.

---

## Gate

Two runs of the registered `tester-unified` lane, both launched detached under
`nice -n 10 ionice -c2 -n7`, container identified by the worktree path in its
own launch argv and `docker update --cpus=3` applied immediately:

| run | commit | verdict | log |
|---|---|---|---|
| 1 | `dc944932` | **RED** | `scratchpad/b074-b077-gate1.log` |
| 2 | `427157c1` | **GREEN** | `scratchpad/b074-b077-gate2.log` |

**Run 1's red was real and useful**, and it is the B070-surface interaction the
wave prompt asked me to watch for:
`nyxloom-trove/carve-assets/W7/test_acceptance_v11.py::test_shipped_schema_is_byte_identical_to_the_locked_v11_asset`
— "the guard this project has been bitten by TWICE… whatever moves in the
shipped schema must move in the frozen copy in the same commit". I did not
guess at it: the guard's own docstring states the required response, the
locked asset is documented in W7's `MANIFEST.md` as "a byte copy of
`src/assay/schemas/verdict.schema.json`", and `5b2730b6` is the working
precedent for moving it in-version. Fixed in `427157c1`; the *separate*
hard-cut guard (every v6..v10 frozen TEMPLATE is still rejected under v11 — 34
templates) was never touched and passes.

Run 2, read from the log in a separate step, never from a piped exit code:

```
110 passed in 1.10s
ASSAY_GATE_PHASE=verdict-v11-successors-verified
tester-unified: PASS (exit 0)
  commit: 427157c1b5b04807a55c1544b05014a6add1b1a7
...
ASSAY_GATE_PHASE=pyflakes-clean
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

## Host discipline (parallel-wave exception)

`docker ps --no-trunc` + `pgrep -af tester-unified-gate.sh` before every launch.
At the first check three foreign gate runs were live (a `nyxloom-p107` gate, a
`cmru-release-…-assay-…` gate, a dstdns `run-gate` chain) at load 12.9 — I did
implementation work and waited rather than racing them. Both of my own gate
containers were identified by `--inner
/workspaces/vbpub/.worktrees/assay-b074-b077-quickwins` in their own argv before
being capped. No container I could not positively confirm as mine was touched.
Local pytest ran serially under `nice -n 19 ionice -c 3` throughout.

## Two things the controller should know

1. **A transient full-suite failure that was NOT mine.** One local full-suite
   run failed `test_gate_qualify_dstdns_sql.py` with `artifact assay_version
   '5.2.0' != installed '6.0.0'`. That is the concurrent `cmru release`
   swapping the shared venv's installed `assay` dist mid-run. Confirmed both
   ways: the same file passes on the clean tree AND with my changes, run in
   isolation. The registered gate builds its own venv in a container and is
   immune. **assay 6.0.0 landed on main while this wave was in flight**
   (`ec0bc47f`), which also means this branch's `CHANGES.md [Unreleased]` block
   will conflict at merge with main's post-release one — my two entries are
   additive and belong in the *next* Unreleased block.

2. **A live backlog-ID collision, pre-existing, not created here.** Eight
   shipped comments and test docstrings in `verify.py`,
   `coverage_parsers/coverage_py_json.py`,
   `coverage_parsers/coverage_istanbul_json.py`,
   `adapters/go_stmtpos.py`, `tests/test_untrusted_json_parse_sweep.py` and two
   other test files say **"B074"** meaning the untrusted-JSON `RecursionError`
   sweep — which the backlog **renumbered to B075 at merge time**, after those
   comments had already shipped. My new comments say B074 meaning
   `allow_test_path_targets`. Both names are now in the tree for different
   things. I did NOT touch the older ones: the wave prompt says do these two
   items and nothing else, and rewriting another item's shipped narrative is
   the controller's call. A note is left at the head of
   `tests/test_evaluate_whole_target_allow_test_path.py` so the next reader is
   not misled.

---

## Fix round (`15258dfc`) — round-1 review

Review: `assay-WAVE-B074-B077-REVIEW-round1.md` (ACCEPT-conditional, 3
blockers + 1 decision ask). The reviewer verified both items' behaviour
end-to-end and found it correct; nothing about either design was rethought.

### Blocker 1 — the flag's plumbing had no lane-level oracle

The reviewer pinned BOTH `runner.py` forwardings to a literal `False` and ran
the suite: **4359 passed, fully green.** The feature was deleted at the
plumbing level and nothing noticed. Every test proved it one layer down —
`_resolve_whole_target(..., allow_test_path_targets=True)` — while B074's
acceptance lines say "a **lane** … is JUDGED" and "the flag appears in **the
verdict**". This is the project's own recurring defect species, and my REPORT
had cited unit-level tests as evidence for lane-level sentences.

`tests/test_lane_allow_test_path_targets.py` closes it: a real `assay.toml`
through `cli.main(["run", …, "--verdict-json", …])`, reading back the verdict
the run wrote. The positive and its controlled negative differ by exactly one
line of TOML.

The root cause was addressed too, not just the symptom:
`tests/conftest.py::make_r1_judge` — the project's standard R1-judge helper —
could not construct a judge carrying the flag at all, so no runner-level test
could reach either consumer even if someone had tried to write one.

**Mutants re-applied in place and confirmed killed**, which the fix brief
required me to verify rather than assert:

| mutant | site | result |
|---|---|---|
| M1 | `evaluate_r1` → `evaluate_targets` | **3 failed** |
| M2 | `_run_prepared_lane` → `JudgmentR1` | **2 failed** |
| M3 | → `_mutation_targets_whole` (new this round) | **1 failed** |

`runner.py` was restored byte-exactly afterwards (`git diff --stat` verified
against the intended change).

### Blocker 2 + the decision ask — R2 now honours the flag

The controller ruled: extend `allow_test_path_targets` to
`runner._mutation_targets_whole`. Implemented on exactly R1's terms —
`_is_test_filename` is **imported** from `evaluate`, not reproduced, so the
directory/filename split cannot drift between the two tiers. Both of R2's
remaining refusal messages now name the flag and the remedy, in R1's own
words; the old bare sentence was the diagnostic gap Blocker 2 identified.

Four consequences carried through so nothing states the withdrawn position:

* `CONSUMERS.md` table row 4 rewritten (it previously told consumers to
  "declare such a lane R1-only, or move the file" — now actively wrong);
* `config.py`'s R1-required refusal message re-argued: R1 is required because
  it is the tier that **records** the policy into the verdict, so an R2-only
  lane would relax its gate with nothing in the artifact admitting it —
  the auditability half of B074's own acceptance, and a better reason than
  the "only reader" claim it replaced, which is no longer true;
* `evaluate.py`'s and `config.py`'s field docs now say the flag governs both
  whole-target tiers;
* `CHANGES.md` rewritten to match.

### Blocker 3 — the per-adapter claim is now derived, not hand-copied

`_SPLIT_CASES` is keyed by registry name and checked for completeness against
`cli._built_in_registry().entries`, so a fifth adapter with no case turns the
suite red instead of silently narrowing "in every adapter, and for any future
one" to the four that happened to be listed (A-270, the discipline
`test_docs_examples_and_vocabulary.py` already applies to closed vocabularies).
The per-case adapters come from the registry too, so the tests exercise the
objects a real lane resolves. Go's absence of a directory branch is recorded
as an explicit `None` and **asserted** (`tests/helper.go` is not a test path
for Go), rather than skipped — "this adapter has no directory branch" stays a
checked claim.

### N1 / N2 / N3

* **N1** — `docs/DESIGN-GUIDE.md` gained the flag's own section, in the shape
  `require_branch` and `base_source` already have: what the fused convention
  conflates, why a declared target is not a swept path, and the three
  properties that keep it a narrow override.
* **N2** — the schema `description` posed the field as *"did this lane grade a
  target that would otherwise have been refused?"* It records the
  DECLARED/effective **policy**: a lane that sets the flag and names no test
  path still emits `true`. Reworded in the shipped schema and the locked W7
  twin **together** (the lockstep guard that caught run 1 would otherwise have
  caught this one).
* **N3** — folded in although marked controller-owned, because it strengthens
  an argument I make in three places: the load-bearing reason the field is
  additive is not "such a lane used to refuse" but the absolute one — a
  pre-B074 **loader** rejects `allow_test_path_targets` as a surplus judge
  key, so no older assay can emit it whatever the lane's targets. Now the
  primary clause in `verdict.py`, the schema description and `CHANGES.md`.

**N4** (the B074/B075 comment collision) and **N5** (backlog ticks) are
controller-owned per the fix brief and untouched.

### Verification

* Full local suite: **4367 passed, 20 skipped** (up 8 from the reviewer's
  4359 baseline), serial under `nice -n 19 ionice -c 3`.
* Registered gate run 3, on `15258dfc`: **PASS (exit 0)**, read from
  `scratchpad/b074-b077-gate3.log` in a separate step —
  `tester-unified: PASS (exit 0)` / `commit:
  15258dfc85688f03efe23cfdeed1a265b1f5dfa2` / `claim[R0..R3]=status=PASS` /
  `ASSAY_REGISTERED_GATE_COMPLETE=1` / `run-gate: lane 'tester-unified' exit 0`.
* Host discipline: the sibling **B078 gate was running** when the fix round was
  ready. I waited for it to finish rather than racing it (identified by
  `assay-b078-r0-structured-report` in its own argv, never touched), then
  launched mine and capped it at 3 CPUs after confirming
  `--inner /workspaces/vbpub/.worktrees/assay-b074-b077-quickwins` in its own
  `Config.Cmd`.
