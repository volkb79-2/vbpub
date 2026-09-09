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

Gate-verified commit: **`427157c1b5b04807a55c1544b05014a6add1b1a7`**.

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
adapters:

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
* the key can appear ONLY on a verdict that was impossible to produce before
  B074 — such a lane refused `ERROR`/`BAD_LANE_CONFIG` and emitted no
  `judgment.r1` at all — so no artifact any existing consumer already reads
  gains a key;
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
* **`runner.py`** — resolves the effective `False` at the two places
  `effective_mode`/`effective_require_branch` are already resolved.
* **`verdict.py` / schema / `verify.py`** — registered in one commit across all
  three, per `_reconstruct_judgment_r1`'s own standing rule. `to_dict` emits
  iff true; `__post_init__` refuses `True` outside `whole_target`; the schema
  constrains it to `const: true` and to `whole_target` via a second `allOf`
  branch; the reconstructor reads it with an absent-means-`False` default.
* **`docs/CONSUMERS.md`** — a worked, paste-able lane (validated by the
  project's own `test_docs_examples_and_vocabulary.py`, which loads every live
  example through the real loader) plus a four-row table of what the flag
  deliberately does not do.

### Deliberately NOT touched

`evaluate.py:428`'s sweep-side `is_test_path`, `mutation.py:478`'s, and
`runner._mutation_targets_whole`'s R2 gate — per the ruling.

**The R2 interaction, and why it is not refused at load.** An `R1+R2`
whole-target lane naming a test-path target with the flag set will grade at R1
and then be refused **loudly by name** at R2 (`_mutation_targets_whole` raises
`BAD_LANE_CONFIG`; it does not silently narrow). That is an honest failure, not
a vacuity. A blanket load-time refusal was considered and rejected: the flag is
lane-level, not target-level, so a lane that declares R2 while none of its
targets is actually a test path would be falsely refused. It is documented in
`CONSUMERS.md` instead ("declare such a lane R1-only, or move the file").

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
