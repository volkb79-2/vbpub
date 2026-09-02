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
