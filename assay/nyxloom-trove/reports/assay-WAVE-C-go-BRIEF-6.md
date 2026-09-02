# Wave C (Go) — continuation brief 6

**Cumulative delta since BRIEF-5 only.** BRIEF-1 is the seam map; BRIEF-2, -3,
-4 and -5 are the earlier deltas; read all five, then this. Nothing here
re-copies any of them.

Written by **generation 5**, a fresh session seeded with BRIEF-1..5 plus the
controller's 2026-09-01 DA-8 entry (`vbpub@3a95459e`). You are generation 6 on
the same terms.

---

## 1. BRIEF-5 §5's task list, as amended by the controller, item by item

| task | state |
|---|---|
| 1. DA-8 per the ruling → gate | **DONE**, `4b5e7707` + `1885d64e`, gate run 8 |
| 6. file B060 | **DONE**, `1885d64e` |
| 4. F008-A3's tick with the CLI-form transcript | **DONE** — A3 is `proven`, four cited tests |
| 2. F008-A4 fixture regeneration | **NOT DONE** — unblocked, never started |
| 3. F008-A5 per DA-6 | **NOT DONE** — unblocked, and §3 is a lane-shape correction it needs |
| 5. the other acceptance boxes | F008-A4/A5 stay `absent` — §4 |

**B059 is closed except its fourth box** (F008-A5's run). Three of its four
acceptance boxes are ticked against work that ran.

**An id renumbering arrived mid-flight and is DONE** (`e7eb5241`, its own
commit). Main's `a050a467` filed a different **B053** and **B054** while this
branch was in flight, so every id this wave filed shifted up by two:
B053→B055, B054→B056, B055→B057, B056→B058, B057→B059, B058→B060. 108
references in 25 tracked files, historical briefs and logs included. **Next
free backlog id is B061**, `decisions.md` is unaffected, and `git grep -n -E
'\bB05[34]\b'` on this branch hits only the note at the top of B055 and the
LOG entry. Every id in THIS brief is already the new one.

## 2. The load-bearing facts a successor must not re-derive

**A-404 is landed and gate-verified. Do not redesign it.** The member is
`LanguageAdapter.for_project(*, repo_top: Path, project_root: Path) ->
LanguageAdapter`, keyword-only, returning a NEW adapter, called ONCE from
`runner.evaluate_r1` immediately after `repo_top = git.repo_top(...)` and
before anything reads the profile. The call site **rebinds the local name** —
that is not stylistic: binding for the statement oracle while an unbound
adapter stayed in scope for the evaluator would rebuild B059's own drift
inside its fix. Every adapter but Go returns `self`; `SqlAdapter` raises its
`_UNREACHABLE`. REPORT §33 carries the whole argument and A-404 the rejected
alternatives.

**The `go.mod` grammar question is CLOSED and was answered from the real
lexer**, not the Modules Reference: `go1.25.14`'s own vendored
`/usr/local/go/src/cmd/vendor/golang.org/x/mod/modfile/{read.go,rule.go}`,
read inside `tester-unified-go:local`. The one rule a reasonable implementer
gets wrong from memory: **a backquoted module path is NOT valid input to
`cmd/go`** — the lexer scans the token, `rule.go`'s `parseString` then rejects
any non-`"`-prefixed token containing a quote character. Do not "fix" that
into acceptance. Do not re-read those files; `go_modfile.py`'s module
docstring transcribes all four load-bearing rules with their sources.

**For a Go lane, `project_root` MUST be the module root, and this was true
before A-404.** `evaluate._repo_path_by_raw_key` is `normalize_coverage_key`
then `_to_repo_relative_key(..., project_prefix)`, and `project_prefix` is
`project_root` relative to `repo_top`. So the module strip leaves a
*project*-relative path. A lane whose project root sits above its module
cannot resolve a single key, with or without the derivation. B043's `cwd`
does NOT help: it moves only the command's working directory, while
`judge.source_roots` and the coverage artifact both anchor at
`project_root`. This is the correction §3 is about.

**The qualification now drives `assay run`.** `tests/qualification/
test_go_r1_real.py` is five tests, `ASSAY_GO_QUALIFICATION=1`, `5 passed`
in 28.6s. Every assertion inherited from generation 4 survived the move from
the library driver unchanged — BRIEF-5 §4 predicted that and it held. The
`_DRIVER` heredoc is gone. Two tests are new and are the DA-8 proofs: a
two-module fixture where `go test` really runs in the nested module (refusal
(c), `UNREADABLE_ARTIFACT`), and the same tree with the root `go.mod` removed
(refusal (b), `BAD_LANE_CONFIG`, with **R0 asserted PASS** so it cannot be
mistaken for a `go test` failure).

**A stale-prose class worth carrying.** A-394 registered Go two generations
ago and updated `_built_in_registry`'s docstring; the CLI's own module
docstring, `run --help` and README's summary all still said Go was refused at
any level. Nothing failed. When you land a capability, grep the
**user-visible** text separately from the code — the tests read the registry,
not the help string.

## 3. F008-A5: DA-6's prescribed lane shape cannot run as written

Full detail in REPORT §37. **This is not a disagreement with DA-6** — the
commits (`10b174a5` → `83c2ff79`), the argv, the classification rule and the
"classify before naming a side" discipline all stand. It is the lane FILE.

DA-6 prescribes `cwd = "shared-ramdisk-depot-manager"` with `source_roots =
["shared-ramdisk-depot-manager/internal"]`. Together those imply
`project_root` = the repository top, and then (a) there is no `go.mod` there,
so A-404 (b) refuses, and (b) even given the module path, `srdm/internal/…`
would strip to `internal/…` and resolve to `<repo>/internal/…`, which does
not exist. The corrected shape, which changes nothing DA-6 rules:

* lane file at `shared-ramdisk-depot-manager/<name>.toml`, **untracked** —
  `measurability.check_dirty_tree` is scoped to `source_root_paths` (its own
  docstring) and this file is not under `internal/`, so it does not trip
  `DIRTY_TREE`, and srdm's tree stays exactly as committed;
* `project_root = shared-ramdisk-depot-manager`, **no `cwd` at all**,
  `source_roots = ["internal"]` (project-relative);
* srdm's own `tools/gate.sh:105` argv, verbatim, `-coverpkg=./...` included.

Checked while writing this: `shared-ramdisk-depot-manager/go.mod` declares
`module srdm` — a bare non-domain path, so profile keys are
`srdm/internal/...`.

**The harness problem DA-6 does not mention:** `assay run` judges the
repository's HEAD, and neither differential commit is `main`'s tip, and this
wave may not move the shared checkout's HEAD. So the run needs its own
checkout of `83c2ff79` — a `git worktree` outside the shared one, or a clone
built inside the container, which is what the in-image harness already does
for its fixtures.

## 4. F008-A3 is ticked; A4 and A5 are not

**F008-A3 is `proven`.** REPORT §35 is a statement-granular Go R1 verdict
from the shipped zipapp, through `python3 <pyz> run …`, against the real
toolchain, with `executable: 1` where the removed rule said 3 and with
nothing about the module path declared anywhere. Four tests are cited, and
**two of them need no toolchain** (A-217's frozen collision witnesses), so the
criterion is not proven only where the registered gate cannot re-run it.

Its old text — "BLOCKED on A-217's source-side statement-position oracle;
A-239 records the accepted seam, which is designed but not carved" — was
false in three ways after this wave, which is worse than an untick'd box.

**A method note worth stealing.** I first decided to defer the tick so A3, A4
and A5 could "land together against one gate", then checked the premise:
`grep -rl 2-product-definition tests/ tools/ gate/` returns nothing. **No
gate judges that file**, so the argument was about a constraint that does not
exist. Check what the gate actually reads before sequencing work around it.
Also: the "opt-in qualification" note went into the existing `text` field,
not a new `evidence_note:` key — 60 rows use exactly `text`/`status`/
`evidence`, and a key with one user in a file nothing parses is a convention
invented for one sentence.

**F008-A4 and F008-A5 are unblocked and simply were not started.** B059 was
the blocker and it is closed. This generation cut at DA-8's green gate rather
than starting the larger of the two with too few calls to finish honestly.
F008-A4's shape is unchanged and is stated in B057 and REPORT §31:
regenerate the fixture bytes from real toolchain output **and** re-derive
every asserted line set from the oracle in the same change — bytes alone is
A-234's own warning.

## 5. Your task list, in order

1. **F008-A4** — fixture regeneration through the in-image harness (BRIEF-5
   §3, and `test_go_r1_real.py` is now a working example of that harness end
   to end). Discharges B057's first box; B057's second still needs a decision
   this generation had no standing to make.
2. **F008-A5** per DA-6, using §3's corrected lane shape. Classify every
   difference as extent-expansion or file-absence BEFORE naming a side;
   `carve-assets/P27/fixture/manifest/calc-statements.json` is the neutral
   third party where one exists. Nothing is committed under
   `shared-ramdisk-depot-manager/`.
3. **The acceptance boxes** — F008-A3 (evidence already written, §4), then A4
   and A5, each citing something you ran, in F008-A1/A2's `evidence:` style.
4. **B057's remaining boxes**, to whatever extent F008-A4 closes them.

## 6. Ledger

Decisions this generation: **A-404** (DA-8 implemented — the member's name,
signature, both refusals' reason codes, and three sub-rulings the ruling left
open). **Next free: A-405.**
Backlog: **B060** (`build_release.py` leaves `zipapp-staging/`), filed as
B058 and renumbered. **Next free: B061** — the whole wave's ids shifted up by
two, see §1.
Backlog boxes ticked: **B059's first three of four.** Nothing else.
Acceptance boxes ticked: **F008-A3** (`proven`). A4 and A5 stay `absent`.
Decision asks open: **none.**

## 7. Gate

**Run 8: PASS on `1885d64e`** — `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`GATE_EXIT=0`, through `cmru-b006a-qualified` and
`independent-self-hosting-passed`, and a SEPARATE grep for
`FAILED|DIRTY_TREE|Traceback|ERROR` returned nothing. Devcontainer full
suite on the same tree: `3902 passed, 18 skipped`, `PYTEST_EXIT=0` (from
3860/16 — the two extra skips are the new in-image refusal tests, correct
with no Go here). Go qualification, run separately with
`ASSAY_GO_QUALIFICATION=1`: `5 passed`, `PYTEST_EXIT=0`.

**Run 9: PASS on `dd1e2c46`** — the renumbering (`e7eb5241`) and the
B053 fold-in + F008-A3 tick landed after run 8, and the renumbering touches
source comments and nine test modules, so it was re-gated rather than
assumed inert. Transcript in REPORT §39.

**The wrapper-vs-job trap did not fire this generation either** — every
background job's completion notification agreed with its own appended
marker. That is eight instances across four generations and four agreeing
ones; the markers were still read separately, in their own step, which is
the only reason this sentence can say which was true.

**Commit before you gate, and do not edit the worktree while it runs.** This
generation wrote BRIEF-6 into a scratchpad OUTSIDE the worktree while the gate
ran and copied it in afterwards, for exactly the reason generation 3 lost a
run: the self-hosted lane refuses `DIRTY_TREE` on an untracked file and is
right to.

---

## SELF-COMPACTION PROMPT

**KEEP:** BRIEF-1 in full (the seam map); BRIEF-2..5 in full; this brief in
full; the wave prompt's "Wave C" section; the controller's 2026-09-01 entries
(DA-4..DA-7 at `vbpub@237b9585`, DA-8 at `vbpub@3a95459e`); BRIEF-1's rules
block (A-334, A-335, A-042/A-043, A-097/A-101, decisions.md append-only from
**A-405**, backlog from **B061**, `git commit -F <file> --only -- <paths>`,
the trailer, no `!` marker); §2 (what A-404 landed) and §3 (the F008-A5 lane
shape) as the two things the remaining items stand on; §5 as the literal task
list; BRIEF-5 §3's in-image harness recipe, of which
`tests/qualification/test_go_r1_real.py` is now the working example; the gate
command and the separate-verdict-read discipline.

**DROP:** the DA-8 design fork (closed — A-404 carries all three shapes and
why two were rejected; do not reopen "should it be a declared key"); the
`go.mod` grammar investigation (closed — `go_modfile.py`'s docstring
transcribes the four rules WITH their source lines, and 24 tests assert them;
do not re-read the vendored `modfile`); the argument for why the qualification
drove the library entry point (void — it drives `assay run` now); B059's
measurement transcript (the defect is fixed and the control is now a passing
test).
