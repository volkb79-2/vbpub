# ciu-P47 — LOG

Package: `nyxloom-trove/handoffs/ciu-P47-instance-generated-overlay-split.md`
— C1 (`ciu.instance.generated.toml`, the dedicated CIU-owned facts file), C2
(`ciu.global.worktree.toml.j2` → `ciu.global.instance.toml.j2`, hard
cutover), C3 (activate ciu-P46's dormant `retired-overlay-file` rule), C4
(the exhaustive docs/fixture/consumer sweep). Harness-provided worktree
`/workspaces/vbpub/.claude/worktrees/agent-a5db4950c58078004`, branch
`worktree-agent-a5db4950c58078004`, based on vbpub main `ce08d077`
(`docs(assay): Wave C controller log -- session checkpoint`, the tip when the
worktree was created, carrying ciu 7.9.0 / ciu-P46).

Two commits: `82d2154b` (code + tests + `.gitignore`/`.gitignored.ciu`) and
`d9e2d26a` (the docs/consumer sweep, `CHANGES.md`, backlog, decisions
record). The split departs from ciu-P46's "docs land in the same commit as
the code" precedent for one deliberate reason: this package's C4 IS a
distinct oracle — the one ciu-P46's review found gaps in — and keeping it a
separate, reviewable diff is what lets a reviewer read the sweep as a sweep
rather than as noise inside a 46-file rename. Both commits are on the branch,
and the gate ran at the SECOND one, so the shipped state is the reviewed
state either way.

**The real gate** (`./run-gate.py ciu --worktree
/workspaces/vbpub/.claude/worktrees/agent-a5db4950c58078004`, run from
`<worktree>/ciu`) was run ONCE, at the final HEAD `d9e2d26a`, and **PASSED**.
`--worktree` is required here for the same reason ciu-P46's implementer
needed it: this is an isolated checkout and the gate's Docker invocation must
be pointed at it rather than at the top-level tree. Verdict read in a
separate step from the run — output redirected to a file, then read, then the
verdict artifact parsed — never a piped tail. Verbatim in
`ciu-P47-REPORT.md`.

---

## Orientation, before any edit

Read the handoff in full, then `ciu-P46-{LOG,REPORT,REVIEW}.md`. The load-
bearing thing taken from them is not design: it is that **all 8 of P46's
review blockers were documentation/consumer text that still described old
behaviour after the code changed, and zero were mechanism defects.** That set
the shape of this package's work: the code change here is large but
mechanical, and the risk is concentrated in prose.

Then a grep census, before deciding anything:

- `GLOBAL_CONFIG_WORKTREE_OVERRIDES` — 1 definition, ~45 uses across 8 `src/`
  modules, 13 uses in 3 test files.
- `upsert_generated_facts` / `read_generated_facts` / `has_generated_facts` —
  the whole reader/writer API, used from `deploy.py`, `engine.py`,
  `worktree.py`, `migration_check.py`, `config_model.py` and 22 test files.
- The literal old filename in 20 test files and 9 docs, plus `.gitignore`,
  `.gitignored.ciu` and `scaffold._GITIGNORE_ENTRIES`.
- `test-repo/` has **no** committed overlay — it is gitignored and generated
  by the integration suite into the committed fixture, which is why ciu's own
  `.gitignore` carries the pattern at all (CIU-60's comment says so).

## C1 — the new file

`config_constants.INSTANCE_GENERATED_FACTS = 'ciu.instance.generated.toml'`,
beside the renamed overlay constant, with the module docstring's filename
convention table extended to explain why this one is deliberately NOT a
`.j2`.

`workspace_env` gained `generated_facts_path(ciu_root)` — one place the
location is composed, so every reader, writer and diagnostic agrees — and
`upsert_generated_facts` became `write_generated_facts`:

```python
path = generated_facts_path(ciu_root)
body = "\n".join(render_generated_facts_block(facts)) + "\n"
_atomic_write_text(path, body)
```

That is the whole writer. Deleted with it: the read-back of the existing
file, the header-line scan, the next-table scan, the walk-back over the
trailing blank/comment run, the manufactured separator, the
`_OVERLAY_FRESH_HEADER` constant, and the docstring's documented-and-accepted
limit (a line reading exactly `[ciu.instance.generated]` inside a multi-line
string elsewhere in the file would have been mistaken for the header).

**The rename of the function is not cosmetic.** "Upsert" names the deleted
mechanism. Leaving it would have been exactly P46's defect class in an
identifier instead of a sentence, so it was renamed at all 23 call sites.

The banner moved from inside the table to the top of the file. Inside was
required before (a comment above the header would have survived the surgical
replace and been re-emitted every run, growing a duplicate banner per
`env generate`); with a wholesale rewrite that constraint is gone, and the
banner now also points the reader at `ciu.global.instance.toml.j2` as the
file that IS theirs.

`read_generated_facts` became a whole-file `tomllib` parse. Its three-outcome
contract is unchanged (absent → `{}`; table absent → `{}`; present-but-
unreadable → `WorkspaceEnvError`), but the "table present as something other
than a table" case is now reachable and is a refusal, not a degradation —
two new guards, both tested.

### The one place C1 was not a straight repoint

`render_global_chain` merges the new file last, at exactly the position the
embedded table occupied. The first implementation called `read_generated_facts`
there. `tests/tests/test_ciu_render_selection_context.py::test_identity_
unreadable_agrees_between_check_preflight_and_real_run` caught that
immediately, and it is a **real regression, not a test inconvenience**: that
reader refuses a non-string fact, so a corrupt identity record would have
aborted `render_global_chain` before `ciu up` ever reached STEP 12 —
converting CIU-80's `identity_unreadable` degradation into a traceback out of
the render, which is the opposite of what that flag exists for. Before the
split, the chain merged these bytes through an ordinary TOML parse of the
overlay with no type checking at all.

Fixed by splitting the read in two, with the reason stated at both ends:
`generated_facts_document` (plain parse — the MERGE view, as tolerant as the
layer it replaced) and `read_generated_facts` (which layers the identity
strictness on top of it). One file location, one parse, two questions.

## C2 — the rename

Constant renamed `GLOBAL_CONFIG_WORKTREE_OVERRIDES` →
`GLOBAL_CONFIG_INSTANCE_OVERRIDES`, and every site that used it to name where
the FACTS live was repointed at `generated_facts_path` instead — those are
different files now, and about half the uses were the facts, not the overlay.
Each one was classified individually rather than swept: `deploy.py`'s two
identity readers and `--vanilla` set, `engine.py`'s compose-project namer and
ownership labeller and STEP-12 hook identity, `worktree.py`'s eight sites
(shared-infra reference, clean-target identity, budget survey, reap
readiness, adopt refusal, the overlay writer itself).

`_write_worktree_overlay`'s refuse-if-exists is unchanged and still needed —
it is the ONE writer of the operator's file, at worktree-creation time. What
changed is that it is now the ONLY one, which is what makes S3.1b's new
"CIU MUST NOT write it after that initial creation" clause true of the code
rather than of a careful implementation.

`_GITIGNORE_ENTRIES`, `.gitignored.ciu` and ciu's own `.gitignore` all gained
the two new names and dropped the retired one. The retired pattern was NOT
kept in ciu's own `.gitignore`: a leftover copy in a working tree is exactly
what the retired-overlay rule wants visible, and ciu's own tree has none.

## C3 — the rule goes live

One constant. `RETIRED_OVERLAY_NAMES` still literally lists
`ciu.global.worktree.toml.j2`; `detect_retired_overlay` still filters it
against `GLOBAL_CONFIG_INSTANCE_OVERRIDES`; the name is no longer current, so
the rule fires. No detector body edited, no registry edit — exactly as P46
designed it, which is worth recording because it is rare for that prediction
to hold exactly.

Its two tests were rewritten to prove the REAL flip rather than a
monkeypatched one, as the handoff asked:
`test_retired_overlay_rule_fires_for_real_after_the_p47_rename` asserts on
the unpatched module. The dormancy mechanism is still covered, but now by
monkeypatching the HISTORY list rather than the live-name constant — proving
the property (a name that is still live produces nothing) survives for the
next rename that wants this rule.

Rule 2's message was repointed at the new filename, and its docstring now
names the pre-P47 checkout (table still in the retired overlay) as a state it
legitimately fires on. Rule 1's docstring records why dropping the retired
name from `_GITIGNORE_ENTRIES` does not blind it — it asks the filesystem,
never the `.gitignore` — which is the interaction the handoff flagged.

## Tests

79 failures after the code change, all fixture/expectation, worked through
file by file rather than swept. Two were interesting:

- `test_ciu_workspace_env.py`'s whole O2 section existed to prove the deleted
  mechanism (hand-authored content survives; the blank separator is not
  doubled; a block butted against the next table gains one). Those tests are
  not "updated" — the property they pinned no longer has a mechanism. They
  were replaced with the stronger property the split makes available: the
  operator's file is byte-identical across a real generate, and **no writer
  anywhere opens it in a write mode** (proven by a `Path.open` guard that
  raises on `w`/`a`/`x`/`+` for that filename, with read access deliberately
  still allowed because the S3.3 chain renders it).
- `test_ciu_worktree_shared_infra.py`'s byte-identity oracle got SIMPLER: the
  expected overlay text is once again exactly the four-line pre-CIU-52 shape,
  because nothing appends the identity table to it any more.

`test_ciu_deploy_clean_vanilla.py` now derives its file list from
`deploy.VANILLA_RESET_FILES` with an assertion that every entry has a fixture
body, and pins the literal set in exactly one test. A hand-copied tuple would
have kept passing while silently covering only three of four files — the same
count-drift the `--vanilla` help text had.

## C4 — the sweep

Done as its own pass, twice, with the grep patterns written down rather than
recalled: the literal old filename; the concept without the filename
("worktree overlay", "overlay table", "overlay facts", "the overlay's");
and the deleted mechanism by name ("upsert", "surgical", "text-region",
"block replace"). Across `src/`, `tests/`, `docs/`, `test-repo/`,
`README.md`, `.gitignore`, `.gitignored.ciu` and `nyxloom-trove/`.

Every surviving hit was then classified as intentional-historical or a
defect. Two defects found that way, both of the P46 class and neither in a
file the handoff named:

- `tests/tests/test_ciu_identity_cutover_ciu75.py`'s module docstring still
  said the old file was the sole identity source;
- `tests/tests/test_ciu_worktree.py`'s `fake_generate_env` docstring still
  said the fake "upserts into the overlay".

The intentional-historical set (left alone, deliberately): `CHANGES.md`'s
released sections, `KNOWN_ISSUES_TODO_BACKLOG.md`'s FIXED rows, everything
under `nyxloom-trove/reports/` and `handoffs/`, and the v8 design documents —
these are records of what was true then, and rewriting them would destroy the
history the migration-check rule's own design depends on.

`docs/DESIGN-GUIDE.md` was the judgment call: its CIU-60 section is a WHY
narrative about the surgical replace. Rewriting it would have erased the
reasoning; leaving it would have left a guide recommending a deleted
mechanism. It is now past-tense, followed by a new section on why the
mechanism was deleted rather than hardened, ending in the general lesson
(when machinery exists to make two owners safe in one place, ask whether they
have to be in one place).

## Gate

`./run-gate.py ciu --worktree …` at `d9e2d26a`: **PASS**, R0 PASS, R1 PASS,
100.0% line + branch over 140 changed lines / 12 branches in 10 files,
`excluded_lines={}`. No `# pragma: no cover` was added anywhere in this
package — checked explicitly, because that is precisely what failed ciu-P46's
first gate run while `pytest` was green.

---

# Addendum — review fix pass (2026-09-02)

A fresh adversarial reviewer returned **ACCEPT-conditional** on the three
commits above. The mechanism (C1/C2/C3) was accepted without change: the
reviewer specifically re-tried-to-break the `generated_facts_document` /
`read_generated_facts` split against CIU-80's `identity_unreadable`
degradation and it held, verified template-binding identity by differential
execution against pre-P47 `main`, and verified C3 end-to-end with the real
production constant. The fix list was five blockers and three nits, and its
shape is the finding worth recording: **seven of the eight were stale prose,
one was a missing test. Zero were mechanism defects.** That is ciu-P46's
result reproduced exactly, on a package whose author had read P46's review
and run a deliberate three-pass sweep specifically to avoid it.

The reviewer's diagnosis of why the sweep missed them is the durable lesson:

> P47's implementer did a real, documented 3-pass sweep specifically to
> avoid P46's gap, and still shipped P46's exact defect class — because the
> sweep's pattern list … does not match the prose that actually went stale.

A grep sweep finds the *terms* you thought of. It does not find a paragraph
that describes the old mechanism accurately in words that never name it —
DESIGN-GUIDE's reader paragraph (B2) contained four false statements and not
one of them used a filename or a renamed identifier. The sweep's own oracle
was the blind spot, not the diligence applied to it.

## What was fixed

**B4 — the only functional gap, and the one worth the most.** The merge
order between the operator's overlay and the generated-facts file was
asserted in two places (`config_model.py`, SPEC S3.1b clause 5) and pinned by
nothing: the reviewer moved the merge line above the overlay block, ran the
full suite, and got 3526 passed. The regression is real — an operator
hand-writing `[ciu.instance.generated]` into their overlay would silently
shadow CIU's derived facts. Pre-P47 this class of bug was *structurally*
impossible (one file, one table, no ordering choice); C1's split created the
choice in code and left it unguarded. This is the sharper form of the split's
cost, and it is exactly what the original implementation pass did not think
to test, because before the split there was nothing there to test.

Fixed by `test_the_derived_fact_outranks_the_same_key_hand_written_in_the_overlay`
in `test_ciu_workspace_env.py`'s O3 section: generate real facts, then have
the operator's overlay write `"OPERATOR-WINS"` over *every* derived key, and
assert `render_global_chain` returns the derived table unchanged — plus that
an unrelated overlay key still merges, so the test cannot pass by the overlay
being ignored wholesale. Proven planted-and-fired rather than taken on faith:
with the merge order flipped it fails on concrete diverging values
(`{'instance_id': 'OPERATOR-WINS'} != {'instance_id': '8ffce1'}`); mutation
reverted. `config_model.py` now carries a comment naming that test as the
thing that pins the line's position, so the next person to tidy the merge
sequence learns what they are moving.

**B1/B2/B3 — stale prose, the P46 class.** B1: SPEC S3.1c clauses 2/4/5 still
described slicing a region out of a Jinja overlay; clause 5 is now a
whole-file plain-TOML parse, keeping the still-valid reasons a chain render
must not be required to read identity. B2: DESIGN-GUIDE's reader paragraph
past-tensed and pointed at the "why it was deleted" section above it (I first
wrote "the section below" — the pointer was wrong in direction as well as the
prose being wrong in tense). B3: eight "overlay" references naming the wrong
file across `deploy.py`, `worktree.py`, SPEC S3.12, `tests/conftest.py` and
`test_ciu_worktree.py`. The surviving "overlay" hits in those files are the
unrelated env-var sense — overlaying keys onto a subprocess env — and were
classified individually rather than swept.

**B5 — the cross-repo gitignore gap, and my own error inside it.** vbpub's
ROOT `.gitignore` carried an un-globbed `ciu.global.worktree.toml.j2`, added
for assay's "B017 class, third occurrence"; the rename silently retired that
mitigation for the two names that replaced it. `ciu/.gitignore` covers them
*inside* `ciu/`, but the root list is what protects vbpub's own worktrees.
Both new names added, retired name kept. My first edit wrote a comment
claiming "the retired name above is KEPT" while the edit had actually removed
that line — caught only because I ran `git check-ignore -v` against all three
filenames instead of rereading my own diff, and got two matches for three
names. The comment and the code disagreed, and the comment was the confident
one. Re-added and re-verified: all three now resolve to lines 201-203.

**N1/N2/N3.** N1: `ciu/.gitignore`'s comment asserted a causal link to
`ciu migration-check` that the reviewer disproved by direct testing — the
detector is a bare filesystem existence check that never consults a
`.gitignore`. Restated as plain hygiene. N2: the *published* consumer helper
in CONSUMERS §11b leaked a bare `AttributeError` on two shapes this package
had just hardened CIU's own reader against. Rewritten with `isinstance`
guards — and verified by extracting the helper from the markdown and
**executing** it against nine shapes, rather than eyeballing it: absent → {},
happy → facts, empty → {}, non-UTF-8 → ValueError, malformed TOML →
ValueError, scalar ancestor → ValueError, scalar leaf → ValueError,
non-string fact → ValueError, directory → ValueError. Published code is code;
reading it is not testing it. The indeterminacy-case count was corrected from
FOUR to FIVE in both the docstring and §21 — the whole-file parse makes a
non-table at the table's path reachable where the old block-slice did not.
N3: ARCHITECTURE.md's `workspace_env.py` inventory now lists the three new
functions and states why the two readers are distinct.

## Gate

`./run-gate.py ciu --worktree …` at `80ef0a18`: **PASS**, R0 PASS, R1 PASS,
`changed_lines` 100.0% — 141/141 executable lines and 12/12 branches over 10
files, `excluded_lines={}`, `unclassified_lines={}`,
`files_missing_coverage=[]`, base `945c7a16` by merge-base. Suite 3527 passed
(3526 + the new B4 test). Still no `# pragma: no cover` anywhere in this
package.
