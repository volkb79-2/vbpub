# ciu-P47 — REPORT

Package: `nyxloom-trove/handoffs/ciu-P47-instance-generated-overlay-split.md`
(C1-C4). Branch `worktree-agent-a5db4950c58078004`, based on vbpub main
`945c7a16`. Final HEAD at gate time: **`d9e2d26a`**. **Not merged to main** —
a fresh adversarial reviewer verifies first, per this repo's pipeline.

## 1. The real gate — verbatim verdict

Command (run from `<worktree>/ciu`, the only place `./run-gate.py` exists;
`--worktree` because this is an isolated checkout, the same reason ciu-P46's
implementer needed it):

```
./run-gate.py ciu --worktree /workspaces/vbpub/.claude/worktrees/agent-a5db4950c58078004
```

Run ONCE. Verdict read in a separate step from the run (output redirected to
a file, then read; then the verdict artifact parsed separately), never off a
piped tail.

### Run 1 — at `d9e2d26a` — **PASSED** (the final HEAD)

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 32 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.claude/worktrees/agent-a5db4950c58078004/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: d9e2d26a3dd5e8ff3532084d1ffbb129968b7db9
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.claude/worktrees/agent-a5db4950c58078004/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact (`.assay/verdict-ciu.json`, schema_version 8), per claim:

```
outcome  PASS   reason_code  (none)   scope S1   declared_rigor ['R0', 'R1']
R0  PASS  (computed, verified_by_assay)
R1  PASS  pct=100.0  covered=140/140  branches=12/12  considered=10 files
          excluded_lines={}  files_missing_coverage=[]
          missing_lines={}   missing_branch_lines={}   unclassified_lines={}
judgment.r1: mode=changed_lines  fail_under=100.0  require_branch=true
             allow_excluded=false
judge: assay 3.2.0 zipapp, sha256 bbbed3ef35cb8ac3e62075c62fcdb801b7a668b6fc72aa0180419ac4996b84d6
base: 945c7a16bf8b1bbee851f1ad6b5d06743639d915 (merge-base), source_roots ['src']
```

**No `# pragma: no cover` was introduced anywhere in this package.** Checked
explicitly rather than assumed: that pragma is what made ciu-P46's first gate
run FAIL with `EXCLUDED_LINES` while `run-ciu-tests.py` reported 100.00%, and
it is the one failure mode a green local run cannot see.

## 2. Test counts

`run-ciu-tests.py` (the real `--cov=ciu -n auto --dist loadfile --cov-branch
--cov-fail-under=100` invocation) at final HEAD:

**3526 passed**, **100.00% line + branch coverage across every module**
(TOTAL 10150 statements / 4084 branches, 0 missing), zero warnings beyond the
pre-existing third-party `schemathesis` `DeprecationWarning`.

Before this package: 3526. Net **0** — but that number hides real churn: the
`test_ciu_workspace_env.py` O2 section lost six tests of the deleted
text-surgery mechanism and gained six of the new ownership property, and
`test_ciu_identity_cutover_ciu75.py` and `test_ciu_migration_check.py` each
gained one and lost one. Net-zero here means "the mechanism was replaced, not
merely deleted", which is what it should mean.

## 3. Per-oracle: C1-C4 as actually implemented

| Handoff item | Landed as | Evidence |
|---|---|---|
| C1 dedicated plain-TOML file, at the same root | `config_constants.INSTANCE_GENERATED_FACTS`; `workspace_env.generated_facts_path` is the one place the path is composed | `test_ciu_config_constants_deeper13.py` (recognized as a config filename; the retired name is NOT) |
| C1 same six keys, same values, same semantics | `GENERATED_FACTS_KEYS`/`render_generated_facts_block` unchanged | `test_generate_writes_the_six_generated_keys` — the table still agrees key-for-key with the `ciu.env` the same invocation wrote |
| C1 wholesale write, no preservation logic | `write_generated_facts` is 3 statements; the whole surgical path deleted | `test_a_rewrite_discards_anything_hand_added_to_the_ciu_owned_file`; `test_the_writer_never_reads_the_file_it_is_about_to_replace` (a `Path.read_text` that raises does not stop the write — provable only because the read is gone) |
| C1 idempotent | pure function of the facts, fixed key order | `test_second_generate_is_byte_identical` |
| C1 internal reads repointed by exact path | every `read_generated_facts`/`has_generated_facts` call site; all diagnostics now name the new file via `generated_facts_path` | `test_ciu_identity_cutover_ciu75.py`'s whole O3 sweep, unchanged in shape: every site still answers with `ciu.env` deleted AND corrupted |
| C1 three-outcome reader preserved | absent → `{}`; table absent → `{}`; OSError/non-UTF-8/malformed/non-string → `WorkspaceEnvError` | `TestGeneratedFactsReader`, plus two NEW cases the whole-file parse makes reachable (`test_a_non_table_at_the_facts_path_refuses`) |
| C1 template-visible reads resolve identically | merged last in `render_global_chain`, after the instance overlay | `test_a_jinja_template_can_read_the_facts_like_any_other_value` renders a real stack template against `{{ ciu.instance.generated.physical_repo_root }}` and `.network`, unchanged from before this package |
| C1 no bespoke Jinja injection | negative test: delete the facts FILE, the facts vanish from the merge | `test_the_facts_are_not_injected_as_a_bespoke_context_field` |
| C2 hard rename, every site | `GLOBAL_CONFIG_INSTANCE_OVERRIDES`; each of ~45 uses classified as overlay-vs-facts individually | `test_ciu_worktree*.py`, `test_ciu_config_model*.py` |
| C2 no fallback read | the old name appears in `src/` only inside `RETIRED_OVERLAY_NAMES` | `test_the_operators_own_overlay_is_never_read_for_identity` — a complete table in the OLD file reads as no identity at all |
| C2 role otherwise unchanged | same chain position, same clean/vanilla preservation, same lifecycle writer | `test_ciu_worktree_shared_infra.py`'s byte-identity oracle, now asserting the four-line pre-CIU-52 shape and nothing else |
| C2 surgical writer deleted | `upsert_generated_facts`, `_OVERLAY_FRESH_HEADER` and the region logic are gone; no remaining caller | `test_no_writer_anywhere_opens_the_operators_overlay_during_a_generate` — a `Path.open` guard that fails the test if ANY write mode touches that filename |
| C2 `_GITIGNORE_ENTRIES` + `.gitignored.ciu` | new names added, retired one removed, both sides | `test_gitignore_entries_match_gitignored_ciu_sample` (pre-existing both-directions drift test), `test_init_scaffolding.py`'s pre-seeded fixture |
| C3 flip one constant | `migration_check` imports `GLOBAL_CONFIG_INSTANCE_OVERRIDES`; detector body and registry untouched | `test_retired_overlay_rule_fires_for_real_after_the_p47_rename` — **no monkeypatch**, asserts on the unpatched module |
| C3 dormancy mechanism still works | proven by patching the HISTORY list, not the live constant | `test_retired_overlay_rule_is_silent_for_the_live_overlay_name` |
| C3 remediation names the real destination | names `ciu.global.instance.toml.j2` for hand-authored content AND `ciu.instance.generated.toml` as needing no hand-copying | asserted on both filenames in the finding |
| C3 gitignore hygiene must not blind detection | detector reads the filesystem, never `.gitignore`; stated in its docstring | rule-1 tests pass with the retired name absent from `_GITIGNORE_ENTRIES` |
| C4 fixtures | `test-repo/` carries no committed overlay (gitignored, generated by the integration suite); ciu's own `.gitignore` patterns updated so the suite's output stays ignored | the gate itself — a dirty tree reds it (S18.4) |
| C4 docs | SPEC S3.1/S3.1a/S3.1b/S3.1c/S6.4b/S13.7/S16/S19, CONFIG.md, CIU.md, CONSUMERS.md (§11/§11a/§11b + new §21), DESIGN-GUIDE.md, README.md, CHANGES.md, KNOWN_ISSUES_TODO_BACKLOG.md, `nyxloom-trove/decisions.md` | see §4 |

## 4. The C4 sweep, and what it found

Three grep passes, run twice, with the patterns written down rather than
recalled: the literal retired filename; the CONCEPT without the filename
("worktree overlay", "overlay table", "overlay facts", "the overlay's"); and
the deleted MECHANISM by name ("upsert", "surgical", "text-region", "block
replace"). Over `src/`, `tests/`, `docs/`, `test-repo/`, `README.md`,
`.gitignore`, `.gitignored.ciu`, `nyxloom-trove/`.

**Two defects found in files the handoff did not name** — both exactly
ciu-P46's blocker class:

- `tests/tests/test_ciu_identity_cutover_ciu75.py`'s module docstring still
  named the retired file as the sole identity source;
- `tests/tests/test_ciu_worktree.py`'s `fake_generate_env` docstring still
  said it "upserts into the overlay".

Plus concept-level prose in `docs/CONSUMERS.md` (4 places), `docs/CONFIG.md`
(2), `docs/SPEC.md` (4), `docs/DESIGN-GUIDE.md` (2) and `src/ciu/deploy.py`
(1) that described the mechanism correctly for the old file and wrongly for
the new one without ever naming a file.

**Deliberately NOT rewritten** (history of record, not current-behaviour
claims): `CHANGES.md`'s released sections, `KNOWN_ISSUES_TODO_BACKLOG.md`'s
FIXED rows and detail sections, everything under `nyxloom-trove/reports/` and
`nyxloom-trove/handoffs/`, and the v8 design documents. Rewriting those would
destroy the version history that `ciu migration-check`'s whole design depends
on. The backlog's *current-state header* is a different thing and WAS updated.

**Consumer-facing artifacts that needed real work, not a rename:**

- `docs/CONSUMERS.md` §11b's published `read_ciu_identity` helper did
  block-slicing because the old file was a Jinja template whose surrounding
  content might not be valid TOML. That reasoning is void; the helper is now a
  whole-file `tomllib` parse, and §21 tells a consumer carrying the old one to
  replace it.
- `deploy.py`'s `--vanilla` help text said "all three". It is now enumerated
  from `VANILLA_RESET_FILES` — a count word drifts twice per change.
- `docs/DESIGN-GUIDE.md`'s CIU-60 section is a WHY narrative about the
  surgical replace. Made past-tense and followed by a new section on why the
  mechanism was deleted rather than hardened.

## 5. Judgment calls the handoff left open

**(a) `render_global_chain` merges through a DIFFERENT reader than the
identity reader. A reviewer should sanity-check this specifically.** The
handoff says the observable contract is that template reads resolve
identically. Implementing that with `read_generated_facts` broke a shipped
CIU-80 guarantee, caught by `test_identity_unreadable_agrees_between_check_
preflight_and_real_run`: that reader refuses a non-string fact, so a corrupt
record aborted the render before `ciu up` reached STEP 12 — a traceback where
`identity_unreadable` should have been. Pre-split, the chain merged these
bytes through an ordinary TOML parse with no type checking. The fix is
`workspace_env.generated_facts_document` (plain parse, the merge view) with
`read_generated_facts` layering the identity strictness on top of it: one
file location, one parse implementation, two questions. Both functions carry
the reason in their docstrings, and SPEC S3.1b clause 5 states it normatively.

**(b) The rename of `upsert_generated_facts` → `write_generated_facts` (23
call sites) was not asked for.** "Upsert" is the name of the mechanism this
package deletes. Leaving it would have been P46's defect class in an
identifier. The same reasoning drove renaming the CONSTANT
(`GLOBAL_CONFIG_WORKTREE_OVERRIDES` → `GLOBAL_CONFIG_INSTANCE_OVERRIDES`)
rather than only changing its value — a constant whose name says "worktree"
holding `ciu.global.instance.toml.j2` is the same stale-text defect.

**(c) The old pattern was REMOVED from ciu's own `.gitignore`, not kept
alongside the new one.** Keeping it would have been harmless for rule 3
(which only reports missing entries) and would have hidden a leftover from
`git status`. Hiding it is backwards: a leftover copy is exactly what the
retired-overlay rule wants an operator to see. ciu's own tree has none, so
this costs nothing today and is the honest default for consumers.

**(d) `VANILLA_RESET_FILES` gained the new file.** The handoff does not say
so explicitly; it follows from S6.4b's own reasoning. Both files are
artifacts of one `ciu env generate`, so removing one and keeping the other
leaves the workspace in neither the freshly-cloned state `--vanilla` promises
nor a generated one. Stated in CHANGES.md and CONSUMERS.md §21 as a
consumer-visible change.

**(e) `is_config_file` now recognizes `ciu.instance.generated.toml`.** It has
no `src/` consumers (tests only), but it is documented as "every recognized
CIU configuration file name" and the new file is one. The same test now also
pins that the RETIRED name is not recognized — the hard cutover stated as a
test rather than left to a reader's trust.

**(f) `has_generated_facts` still scans for the table header rather than
calling `.exists()`.** The file is CIU's alone now, so `exists()` looks
sufficient — but a truncated or emptied file is genuinely "no facts here",
and `read_generated_facts` returns `{}` for it. A bare `exists()` would make
the predicate disagree with the reader. Recorded in its docstring.

**(g) The two commits are split code/docs, unlike ciu-P46.** C4 is this
package's distinct oracle — the one P46's review found gaps in — so it is a
separately readable diff. The gate ran at the second commit, so the shipped
state is the fully-swept state regardless.

## 6. What was NOT done (by scope)

- **CIU-50 (the `instance_id` KEY rename inside config) is untouched**, per
  the handoff, and the backlog header says so explicitly so a reader does not
  assume naming-adjacency meant it was folded in.
- v8's richer `instance.*` binding, `[ciu.host.generated]`,
  `[ciu.instance.build]` and realness records — out of scope, tied to a
  host/topology model that does not exist in v7.
- No release, no tag, no version bump beyond the `CHANGES.md` `[7.10.0] -
  UNRELEASED` section (CMRU generates the released header at release time).
- **No merge to `main`.** Three commits sit on the worktree branch:
  `82d2154b` (feat — code, tests, gitignore), `d9e2d26a` (docs sweep,
  CHANGES, backlog, decisions — **the gate-green commit**), and this
  LOG/REPORT pair.

---

# Addendum — review fix pass (2026-09-02)

## 7. Review outcome and what changed

A fresh adversarial reviewer returned **ACCEPT-conditional**. The mechanism —
C1 (the file split), C2 (the rename plus deletion of the text-surgery writer)
and C3 (the migration-check flip) — was **accepted unchanged and is not
touched by this pass.** Three things in it were independently re-verified by
the reviewer rather than taken from this report: the
`generated_facts_document` / `read_generated_facts` split was re-attacked
against CIU-80's `identity_unreadable` degradation and held; template-binding
identity (`{{ ciu.instance.generated.* }}` resolving exactly as before) was
confirmed by differential execution against pre-P47 `main`; and C3 was
exercised end-to-end with the real production constant rather than a
monkeypatched one.

Five blockers and three nits were fixed. **Seven of the eight were stale
prose; one was a missing test; none was a mechanism defect** — ciu-P46's
result reproduced on a package that had read P46's review and run a
deliberate three-pass sweep to avoid exactly this. The reviewer's diagnosis
is the part worth carrying forward: a grep sweep finds the terms you thought
to grep for, and the paragraph that went stale (DESIGN-GUIDE's reader
narrative, four false statements) named neither a filename nor a renamed
identifier. **The sweep's pattern list is the oracle, and it was the blind
spot.** A future package in this program should not answer "did you sweep?"
but "what would a stale paragraph look like if it never used any word you
searched for?" — which in practice means reading the surviving prose in the
few files that explain the mechanism, not only grepping them.

## 8. The one functional gap (B4) and why it existed

`render_global_chain` merges the CIU-owned generated facts **after** the
operator's instance overlay, so an operator who hand-writes
`[ciu.instance.generated]` into their own file cannot shadow a CIU-derived
fact. Both `config_model.py` and SPEC S3.1b clause 5 asserted this. **Nothing
tested it:** the reviewer moved the merge line above the overlay block and
the full suite still passed, 3526 green.

The reason this slipped is structural and worth stating plainly, because it
generalizes to any file split. Before P47 there was one file and one table —
the precedence question could not be answered wrongly because it could not be
asked. C1 created a genuine ordering choice in code, and a choice that is new
has, by definition, no inherited test. The implementation pass wrote tests
for everything the split *changed* and none for the thing the split
*introduced*. **When a split turns an invariant into a decision, the decision
needs its own guard on the same commit.**

Now pinned by
`test_the_derived_fact_outranks_the_same_key_hand_written_in_the_overlay`
(O3): both files are seeded with a colliding `[ciu.instance.generated]`, the
overlay claiming every derived key, and the derived table must survive intact
— plus an unrelated overlay key must still merge, so the test cannot pass by
the overlay being dropped wholesale. Verified planted-and-fired: under the
flipped order it fails on concrete diverging values, not a vague assertion.
`config_model.py` carries a comment naming the test, so the line's position
is documented as load-bearing at the place someone would move it.

## 9. Judgment calls in this pass

**(h) N2's count: FIVE indeterminacy cases, not four.** The review left this
to me ("or to the correct count if you decide not to guard all of them — your
call, but state which"). I guarded all of them. The published helper now
refuses a non-table at `ciu`, `ciu.instance`, or `ciu.instance.generated` as
well as the original four shapes, because the move to a whole-file
`tomllib.load` makes those reachable where the old block-slice could not
produce them — a consumer copying the old helper into the new world would
take a bare `AttributeError` on a config an operator can plausibly typo. The
count is stated as FIVE in both the §11b docstring and §21.

**(i) The published helper was executed, not reviewed.** It is shipped code
that lives in a markdown file, which is the easiest kind of code to convince
yourself is correct. Extracted and run against nine shapes; all nine behave
as documented. This is the same discipline the gate applies to `src/`, applied
by hand to the one piece of shipped code the gate cannot see.

**(j) The remaining "overlay" words in B3's files were left alone
deliberately.** `worktree.py` and `test_ciu_worktree.py` use "overlay" in an
unrelated sense — overlaying environment keys onto a subprocess env (CIU-85).
Each surviving hit was classified individually rather than swept, which is
the same discipline the original C4 pass used and, this time, the right
answer: a blanket replace would have introduced fresh false statements while
fixing old ones.

**(k) B5 was verified by `git check-ignore`, and that is how my own error was
caught.** My first edit to vbpub's root `.gitignore` wrote a comment claiming
the retired filename was kept while the edit had removed it. Rereading the
diff would not have caught it — the comment was confident and the diff looked
right. Asking git which of the three names were actually ignored returned two
of three. **Ask the tool, not the text**, especially for a change whose entire
purpose is to make a tool behave a certain way.

## 10. Gate and commits

`./run-gate.py ciu --worktree …` at **`80ef0a18`**: **PASS** — R0 PASS, R1
PASS, `changed_lines` mode at `fail_under=100.0` with `require_branch=true`
and `allow_excluded=false`, measuring **100.0%: 141/141 executable lines,
12/12 branches, across 10 files**, with `excluded_lines={}`,
`unclassified_lines={}` and `files_missing_coverage=[]`. Base `945c7a16`
resolved by merge-base. Suite: **3527 passed** (3526 plus the new B4 test).
No `# pragma: no cover` anywhere in the package.

**Still no merge to `main`.** The branch `worktree-agent-a5db4950c58078004`
now carries five commits: `82d2154b`, `d9e2d26a`, `8f174836` (the original
three), **`80ef0a18`** (this fix pass — the gate-green commit) and this
LOG/REPORT addendum.
