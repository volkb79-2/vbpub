# assay Wave C (the Go wave, P27 re-carve) — adversarial review, round 1

**Reviewer:** fresh adversarial session, no inherited judgment.
**Subject:** `feature/assay-wave-c-go`, worktree `/workspaces/vbpub/.worktrees/assay-wave-c-go`, tip `d938ab8c` (26 commits, 71 files, +13147/−300 against `main`).
**Date:** 2026-09-02.
**Worktree state on return:** `git status --short` empty, tip `d938ab8c`. Nothing was committed to or edited in the review worktree. All probes ran in a detached scratch worktree under the scratchpad, in `git archive` copies, or inside `tester-unified-go:local`.

---

# PART A — BLIND PHASE (my own findings, recorded before opening LOG/REPORT/BRIEF)

I read, in order: the diff stat, then the diff file by file (`helpers/go/stmtpos/`, `go_stmtpos.py`, `statement_attribution.py`, `coverage_parsers/{model,go_cover}.py`, `adapters/{base,go,go_modfile,python,javascript,sql}.py`, `runner.py`, `evaluate.py`, `verdict.py`, `vocabulary.py`, `cli.py`, `pyproject.toml`, `conftest.py`, `tester-unified-go/Dockerfile`); the wave prompt's Wave C section; `4-backlog.md` B047/B055–B061 and main's B053; `decisions.md` A-390…A-404; `carve-assets/P27/README.md` + `BLOCKED-grammar.md` + all eight witnesses; `carve-assets/P27-recarve/*`; F008 and M6.

## A.1 The oracle — re-run by me, against all eight witnesses

`carve-assets/P27-recarve/probe-stmtpos.sh`, re-run with `ASSAY=<worktree>/assay`, inside `tester-unified-go:local`, `--network=none`, `--cgroup-parent=dev-background.slice`. Exit 0.

* **`collision-colA` → `{4,6}`, `collision-colB` → `{4,5}`**, from the byte-identical profile record `example.invalid/coll/f.go:3.22,7.2 2 1`. Derived from SOURCE, not from the profile — the impossibility proof is answered. ✔
* All eight witnesses (`collision-col{A,B}`, `seg`, `lit`, `shapes`, `edge`, `fixture/commit{1,2}/calc.go`): the oracle's extents and `num_stmts` reproduce the frozen profiles **exactly, as sets**, checked by me record by record. ✔
* My re-run reproduces the committed `stmtpos-witness-oracle.json` **content-identically** (its committed form is pretty-printed with `../witness/` stripped — a normalisation `PROVENANCE.md` line 41 discloses). `go_version` `go1.25.14` both sides. ✔
* `--network=none` held; `GOPROXY=off GOWORK=off GOTOOLCHAIN=local GOFLAGS=-mod=mod` were in force; the helper's `go.mod` has no `require` lines. Nothing could have been fetched. ✔ The **production** path (`go_stmtpos._FORCED_ENV`) is separately proven by my qualification run below, which ran `assay run` inside `--network=none`.
* **P27 assets are byte-unchanged.** I recomputed every sha256 in `P27/README.md`; all match, and `git diff main...d938ab8c -- assay/nyxloom-trove/carve-assets/P27/` is empty. ✔

### Transcription fidelity, checked against the real `cmd/cover`

I extracted `/usr/local/go/src/cmd/cover/cover.go` from the image and diffed each transcribed function against `stmtpos.go`:

| function | result |
|---|---|
| `isControl`, `hasFuncLiteral`, `findText`, `dedup`, `funcLitFinder.Visit` | **byte-identical** |
| `statementBoundary`, `endsBasicSourceBlock` | identical modulo comment re-wrapping |
| `addCounters` | identical except the one documented substitution (`f.edit.Insert(..., f.newCounter(pos,end,last))` → `f.record(pos, end, list[0:last])`; `len(list[0:last]) == last`) |
| `Visit` | identical except the documented removals: the edit-buffer inserts, `-pkgcfg` `preFunc`/`postFunc`, the `sync/atomic` anti-recursion guard |

Two deviations I checked rather than accepted:
* `case *ast.FuncLit:` — real cover returns `f` (generic walk into `Type`+`Body`); the transcription does `ast.Walk(f, n.Body); return nil`. Equivalent for block generation (`*ast.FuncType` contains no statements), and empirically confirmed by `lit.go`/`shapes.go`.
* `dedup` is applied in `newCounter`'s `-pkgcfg` branch in the real file — the branch `go test -cover` actually takes — so `record()` applying it is correct, not a guess. Verified at `cover.go:715-738`.

**Verdict on the oracle: sound, faithful, and independently reproduced.** See BLOCKER 1 for the one place its *consumers* get its own output wrong.

## A.2 `lit.go` — honestly filed, not claimed fixed

My oracle run shows `lit.go` block `3.14,4.18` (count 1, stmt_lines `[4]`) and `4.18,4.30` (count 0, stmt_lines `[4]`): both statements genuinely begin on line 4, and line 3 (`func H() int {`) appears in no `stmt_lines` at all. So the **fabrication is fixed** and the **laundering is not**. Every document I could find says exactly that: `statement_attribution.py:54-68`, `DESIGN-GUIDE.md:1450-1460`, `PROVENANCE.md:90-102`, `B055`, `A-393`, and `test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four` which asserts `4 in file_cov.executed` deliberately so a future fix goes red. `grep -rn 'lit\.go'` over `src/`, `docs/`, `README.md`, `CHANGES.md`, `2-product-definition.md`, `3-roadmap.md` turns up **no claim that it is fixed**. ✔

Note for the record: the wave prompt's reviewer emphasis said "confirm `lit.go`'s missing-set failure is **actually fixed**". It is not, and cannot be at line granularity. A-393 + B055 are the honest answer and they were written before the tick, not after the question.

## A.3 The `dedup` witness the REPORT says is unproven — I built it, and it broke the parser

REPORT §5 item 4 says the `seenPos2`/`dedup` replication is **unproven**: "I have not constructed a witness that triggers it." I constructed one, from the Go project's own canonical fixture (`cover_test.go`'s `lineDupContents`, the `TestLineDup` corpus), inside `tester-unified-go:local`:

```text
mode: count
linedup/linedup.go:5.21,6.25 1 1
linedup/linedup.go:6.25,100.0 1 100
linedup/linedup.go:100.0,100.0 1 100      <- dedup bumped this end column
linedup/linedup.go:100.0,102.0 1 50
linedup/linedup.go:100.0,102.1 3 25       <- ...and this one
linedup/linedup.go:103.0,103.0 1 100
linedup/linedup.go:103.0,103.1 1 100
linedup/linedup.go:103.0,105.0 2 34
linedup/linedup.go:103.0,105.1 4 20
```

**Good news:** the oracle reproduces all nine extents and all nine `num_stmts` **exactly**. The `dedup` replication is now PROVEN, by me. Add it to the corpus.

**Bad news:** those are the columns `//line` directives produce, and they are **zero**. See BLOCKER 1.

## A.4 F008-A4 — regeneration re-run by me, byte for byte

`regenerate-fixtures.sh` re-run with `ASSAY=<worktree>/assay`, in-image, `--network=none`. Exit 0.

| artifact | my re-run vs committed |
|---|---|
| `tests/fixtures/go/hello/hello.out` | **byte-identical** |
| `tests/fixtures/canary/go/greet/greet_control.out` | **byte-identical** |
| `tests/fixtures/canary/go/greet/greet_transformed.out` | **byte-identical** |
| `carve-assets/P27-recarve/fixture-oracle.json` | content-identical (committed form pretty-printed, `../oracle-src/` stripped — disclosed at `PROVENANCE.md:175`) |

Every sha256 listed in `PROVENANCE.md`'s F008-A4 block recomputes correctly against the tree. ✔

**The naive-expansion control is not vacuous.** Mutating `statement_attribution.attribute_statements`' extent-mismatch refusal to a no-op turns `test_an_extent_the_source_does_not_have_refuses_and_names_it` red; the F008-A4 control test `test_the_naive_block_expansion_of_this_very_fixture_reports_three_times_the_lines` asserts `{32,33,34}`/`{38,39,40}` against the same real profile the passing test reads as `{33}`/`{39}`, so the two cannot both be satisfied by one rule. ✔

## A.5 F008-A5 — the srdm qualification, rebuilt and re-run by me, with my own classifier

Per DA-9's harness: synthetic two-commit repository built **inside** `tester-unified-go:local` from `git archive 10b174a5 shared-ramdisk-depot-manager` then `83c2ff79` over it; lane file at the **module root**, `source_roots = ["internal"]`, **no `cwd`**, srdm's `tools/gate.sh:105` argv verbatim, base = commit 1; `covergate` run in the same checkout on the same profile.

```text
covergate: diff-coverage OK: 639/684 changed executable lines covered (93.4% >= 75.0% floor)
assay:     PASS  considered=12 executable=418 covered=394 pct=94.2584
           helpers=[{role: statement-positions, tool: go,
                     resolved_path: /usr/local/go/bin/go, identity: "go version go1.25.14"}]
```

Profile: 68 761 lines, ~20 records per block, exactly as B061 describes.

**My own classifier** — an independent re-implementation of each tool's published rule over the same three inputs (my own `--unified=0` diff parse, my own profile parse, my own oracle run over the 12 changed non-test `internal/*.go` files):

```
file                                          cg_ex cg_cov as_ex as_cov onlyCG onlyAS
internal/assign/assign.go                        49     49    24     24     25      0
internal/config/config.go                        31     27    16     14     15      0
internal/doctor/doctor.go                         6      5     3      2      3      0
internal/doctor/headroom.go                      49     45    39     36     10      0
internal/opctl/headroom.go                       21     21    11     11     10      0
internal/opctl/opctl.go                          11     11     6      6      5      0
internal/opctl/update.go                        180    160   112    102     68      0
internal/power/power.go                          11     11     4      4      7      0
internal/power/readiness.go                      65     63    42     41     23      0
internal/power/wings.go                         201    191   120    115     81      0
internal/profile/profile.go                      36     36    22     22     14      0
internal/publish/sizing.go                       24     20    19     17      5      0
TOTAL                                           684    639   418    394    266      0
```

* My independent totals **reproduce both tools' own printed output exactly** (684/639 and 418/394). ✔
* **266-line difference, all in one direction.** Zero lines assay calls executable that covergate does not. ✔
* **Every one of the 266 is extent-expansion.** My classifier computed them as `covergate-executable − oracle-statement-lines`; sampling with source text shows comments (`assign.go:141-143`), signatures (`config.go:151`, `power.go:75`), closing braces (`doctor.go:111`), and a **blank line** (`update.go:62`). ✔
* **The file-absence axis is EMPTY, checked POSITIVELY, not inferred:** all 12 changed non-test `.go` files under `internal/` appear in the profile (`changed non-test files ABSENT from the profile: NONE`), and assay's `files_missing_coverage` is `[]`. ✔
* **assay's 24 uncovered lines are a strict subset of covergate's 45**, leaving exactly 21 extras. ✔
* My classifier also asserts, per file, that the profile's folded extent SET equals the oracle's extent set and that every `num_stmts` agrees — an independent confirmation of the extent join over 68 761 real records. ✔
* `git diff main...d938ab8c -- shared-ramdisk-depot-manager` is **empty**. ✔

The CONSUMERS.md §7 / README / F008-A5 / M6 numbers are therefore all independently confirmed, including the 266 and the 21.

## A.6 B061 — the fold, mutated

Reverting the fold to `{block.extent: block for block in file_cov.blocks}` in a scratch copy turns **two** tests red, both by name:

```
FAILED test_statement_attribution_go_witnesses.py::test_repeated_records_for_one_block_fold_executed_wins_not_last_wins
FAILED test_statement_attribution_go_witnesses.py::test_the_correction_can_never_downgrade_a_line_the_parser_called_executed
```

Direct probes of the shipped fold:

| records for one extent | result |
|---|---|
| all zero `[0,0,0]` | line stays **missing** — no laundering ✔ |
| mixed `[0,1,0]`, `[1,0,0,0]`, `[0,0,0,1]` | line **executed**, order-independent ✔ |

**On the semantics question the prompt asks about** ("cite where `cmd/cover` or `go tool cover` says so, or say it is assumed"): the branch cites neither — `statement_attribution.py:186-190` (the fold's comment) justifies executed-wins only by internal precedent ("the same rule `go_cover.parse` already applies … one layer down"). **The citation exists and should be used.** `/usr/local/go/src/cmd/vendor/golang.org/x/tools/cover/profile.go`, `ParseProfilesFromReader`'s "Merge samples from the same location" loop:

```go
if mode == "set" { p.Blocks[j-1].Count |= b.Count } else { p.Blocks[j-1].Count += b.Count }
```

`|=` for `set`, `+=` for `count`/`atomic` — both agree with executed-wins on the zero/non-zero question. So the fold **is** the profile's own semantics, not an assumption; the branch just never says where that comes from. See SHOULD-FIX 6 for the one place the same loop is stricter than assay.

## A.7 A-404 / `go.mod` derivation — probed at both boundaries

Library boundary (against the branch's own source, read-only):

| probe | result |
|---|---|
| planted `go.mod` disagreeing with the profile's keys | `ERROR`/`UNREADABLE_ARTIFACT`, message names the **key**, the **module path** and the **file**, and contains no "revision" ✔ |
| `go.mod` deleted | `ERROR`/`BAD_LANE_CONFIG`, names the paths searched ✔ |
| backquoted module path | refused, citing `modfile`'s "unquoted string cannot contain quote" ✔ |
| `/* */` comment | refused ✔ |
| `module` token inside a `require ( … )` block | correctly ignored ✔ |
| factored `module ( … )`, `//` trailing comment, `"`-quoted | all parsed correctly ✔ |
| `"a\nb"` escape | refused ✔ |

CLI boundary, in-image (my own qualification run, below): `go.mod` removed → **R0 PASS, R1 `ERROR`/`BAD_LANE_CONFIG`**, `coverage` absent from the claim. ✔

**Derivation mutation.** Making `for_project` return `module_path=""` while keeping `module_file` turns **five** assertions red, the named one being `tests/test_adapters_go_for_project.py::test_the_module_path_is_read_from_the_projects_own_go_mod` (plus `…_a_monorepo_lane_picks_its_own_module_not_an_ancestors`, `…_a_declared_module_path_that_agrees_is_simply_re_derived`, `…_a_bound_adapter_strips_its_own_modules_prefix`, `…_a_key_outside_the_derived_module_refuses_and_names_all_three_facts`). Mutating `find_module_declaration`'s `top` to escape `repo_top` turns 7 red across two modules. **Not hollow.** ✔

Three narrow fidelity gaps found — see SHOULD-FIX 2.

## A.8 `helpers[]` (B047 item 5)

* Real in-image verdict at `d938ab8c`: exactly one entry, `role: "statement-positions"`, `tool: "go"`, `resolved_path: "/usr/local/go/bin/go"`, `identity: "go version go1.25.14"`. ✔
* `assay verify` (branch source) accepts **all four** real in-image verdicts (PASS, FAIL, and both A-404 refusals), exit 0 each. ✔
* `Verdict._check_helpers` twin (`verify.py::_check_helpers_have_a_judged_claim`): stripping the R1 coverage payload while keeping the helper yields `"a helper with role 'statement-positions' is recorded, but no claim in this verdict carries the payload such a helper would have produced"`. `helpers: []` is refused by **both** layers. ✔
* `tests/test_cli_run.py:406` — `assert "helpers" not in document` — **intact and unmodified**. ✔
* Mislabelling the role in `_record_statement_position_helper` turns `test_the_runner_supplies_the_ROLE_and_the_adapter_never_does` red. ✔

## A.9 A-403 — the zipapp

First mutation attempt was **invalid and I say so**: I broke `_staged_helper` in the working tree and the test passed, because `build_release.build` builds from HEAD's committed OID via a private `--no-local` clone. After **committing** the mutation in the scratch worktree:

```
FAILED test_distribution_build_release.py::test_the_zipapp_can_stage_the_go_oracle_into_a_real_directory
```

against the real built artifact. The test carries its own vacuity guard (`helper_dir_holds_a_real_file is False`). **Not hollow.** ✔
`python3 <pyz> run …` executed in-image by me, five scenarios, all passing (§A.11).

## A.10 Wire schema — untouched

```
git diff main...d938ab8c -- src/assay/verify.py src/assay/schemas/ nyxloom-trove/carve-assets/W5/   -> EMPTY
```

`verdict.py` **is** touched, but only by a behaviour-preserving extraction of `_check_helpers`' requirement table into the public `supported_helper_roles()` + `HELPER_ROLE_REQUIREMENT`, because the runner needs the same rule (B047 item 5). No wire field, no `schema_version` change (real verdicts still say `9`), no drift-guard change. Disclosed in the LOG and CHANGES.md; no decision row, which I think is fine for a same-semantics extraction. **No `!` commit** on the branch (0 hits). ✔ The drift guard passed in my own gate run (`verdict-v6-v7-v8-hard-cut-verified`, `verdict-v9-successors-verified`). ✔

## A.11 Environment-specific claims — all re-run, none read

* **Registered gate, my own run on the tip `d938ab8c`**, from `/workspaces/vbpub`, log outside the worktree, verdict read in a separate step:
  `ASSAY_REGISTERED_GATE_COMPLETE=1` (exactly once), `REVIEWER_GATE_EXIT=0`, eleven `ASSAY_GATE_PHASE` lines through `independent-self-hosting-passed`, `commit: d938ab8c…`, and my own `grep -nE 'FAILED|DIRTY_TREE|Traceback|ERROR|error:'` → **no hits**. **PASS.** ✔
* **`ASSAY_GO_QUALIFICATION=1 python3 -m pytest assay/tests/qualification/test_go_r1_real.py`**, run by me from the worktree with the devcontainer venv: **5 passed**. ✔
* `assay lanes --json` for a Go lane: `external_tools: ["go"]`, `rigor_reachable: ["R1"]`, `coverage.producer: "go-test"`. ✔
* `run --help`: "This build evaluates R0, Python R1, Python R2, Python R3, JavaScript R1, **Go R1**, and SQL R2." No "Go is refused" text remains anywhere in the CLI. ✔
* `tester-unified-go/Dockerfile`: every added line is a `#` comment or blank (checked mechanically); `docker build --check` → "Check complete, no warnings found." ✔
* `build_release.py --repo <worktree> --outdir <scratch>` leaves `zipapp-staging/` beside `--outdir` — **B060 reproduced**, correctly still open.

## A.12 The renumbering (`e7eb5241`) — read as a diff, mechanically

I applied the id map to `e7eb5241^`'s version of **all 25 touched files** and compared against `e7eb5241`'s. Result: **every file is exactly the substitution**, with precisely two intended additions (the 11-line renumbering note in `4-backlog.md`, the 35-line LOG entry). Nothing else rode along on the scripted rewrite. ✔

`git grep -n -E '\bB05[34]\b'` on the branch hits only: the map (`4-backlog.md:5045-5048`), and explicit citations of **main's** B053 in BRIEF-6/LOG/REPORT/`test_go_r1_real.py:579,588`. ✔

Note for the merge: the branch's `4-backlog.md` does **not** contain main's B053/B054 entries (`a050a467` is not an ancestor), so `--no-ff` will conflict in that file after B052. The renumbering means there is no id collision to resolve — only a textual splice. Expected, and the commit message says so.

## A.13 Registry fallout (A-399/A-400)

* Docs gate operator derivation is **R2-scoped** (`_R2_REGISTERED_LANGUAGES`), with a live vacuity guard asserting the excluded set is non-empty. ✔
* Two findings — see BLOCKER 2 and SHOULD-FIX 1.

## A.14 Hollow-test battery

Six subject mutations, each in a scratch copy, each expected to redden one module:

| mutation | red |
|---|---|
| A-392 guard short-circuits | `test_evaluate_coverage_refuses_an_unattributed_profile`, `test_evaluate_targets_refuses_the_same_profile_identically` |
| helper role → `executable-code` | `test_the_runner_supplies_the_ROLE_and_the_adapter_never_does` |
| runner skips attribution entirely | 5 red across wiring + envelope |
| oracle output-schema pin removed | `test_an_unrecognised_output_schema_is_refused_not_read` |
| extent-mismatch refusal removed | `test_an_extent_the_source_does_not_have_refuses_and_names_it` |
| `go.mod` search escapes `repo_top` | 7 red across `for_project` + `modfile` |
| B061 fold → last-wins | 2 red (above) |
| A-403 staging reverted (committed) | 1 red against the real artifact |

**No hollow module found.** Vacuity guards are present where sets can be empty (witnesses, packaging, docs gate).

## A.15 Docs

README, DESIGN-GUIDE §"Go statement positions", CONSUMERS §"Go lanes" — every convention is cited to a decision row, a witness file, or a measurement. `CHANGES.md [Unreleased]` covers the commit range (I walked the 26 commits against the bullets; the only uncovered ones are the doc/chore commits and the inert `package-data` stanza, whose non-mention is *correct* per A-396). The hand-written block contains only this wave's items — no 4.0.0 leftovers, so the recurring fold-in gap did **not** recur. **CONSUMERS' worked Go lane really runs:** I used its TOML verbatim (only `base` substituted) for my own F008-A5 run and it PASSed. ✔

---

# PART B — RECONCILIATION

Read after the above: `assay-WAVE-C-go-LOG.md`, `-REPORT.md` (43 sections), `-BRIEF-1..7.md`, and the controller log's DA-3…DA-9.

Everything material they claim, I independently confirmed:

| their claim | my confirmation |
|---|---|
| oracle derives `{4,6}`/`{4,5}` from identical profile bytes | re-ran the probe; reproduced |
| oracle reproduces all eight frozen profiles' extents + numStmts | re-checked record by record |
| `go1.25.12 → go1.25.14` delta measured inert | follows from the exact join on all eight |
| F008-A4 regeneration is reproducible | re-ran; three profiles byte-identical, oracle content-identical |
| F008-A5: 684/639/93.4% vs 418/394/94.3%, 266 all extent-expansion, file-absence empty, 24 ⊂ 45 | rebuilt the fixture, re-ran both tools, re-derived with my own classifier — **exact match on every number** |
| B061's fix is order-independent and does not launder an all-zero block | direct probes + mutation |
| A-403 is proven against the real artifact | mutation (committed) reddens it |
| A-404's two refusals hold through the CLI | my own 5/5 qualification run |
| gate run 10 PASS | my own run on the tip: PASS, 11 phases, exit 0 |
| the scripted renumber carried nothing else | mechanical substitution proof over 25 files |
| P27 assets unedited; nothing committed under `shared-ramdisk-depot-manager/` | hashes + empty diff |
| `helpers[]` present for Go, absent elsewhere, refused when unpaired | probed both layers + real verdicts |

**One claim they marked UNPROVEN, I proved — and it broke something.** REPORT §5 item 4: "I have **not** constructed a witness that triggers [`dedup`] … the case is unproven." I constructed it (§A.3). The oracle passes. The *parser* does not. That is BLOCKER 1.

**One claim of theirs is now wrong in a way they could not have known:** REPORT §5 item 4's "the extent join would report a mismatch rather than mis-attribute, so the failure direction is safe." The failure direction is indeed safe — but it is not a mismatch, it is a total artifact refusal one layer earlier, and it fires on a profile that has nothing wrong with it.

---

# VERDICT: **ACCEPT-conditional**

The wave is, on the whole, unusually strong work: the oracle is faithful to `cmd/cover` line by line, every headline number reproduces independently, the tests are not hollow under mutation, the honesty discipline (A-393, A-396, A-401, B055, B061 found by their own qualification) is genuine rather than decorative, and the registered gate passes on the tip in my own hands. It should merge — after two fixes, one of which is a real, toolchain-reachable regression.

## BLOCKERS

### BLOCKER 1 — a real `go test -coverprofile` artifact that this branch refuses and `main` accepts: column 0

**Files/lines**

* `assay/src/assay/coverage_parsers/go_cover.py:229-230`
  ```python
  if col <= 0:
      raise _malformed(f"column number {col} in {spec!r} is not positive")
  ```
* `assay/src/assay/coverage_parsers/model.py:217-222` — `CoverageBlock.__post_init__`, `for name in ("start_line","start_col","end_line","end_col"): if value < 1: raise ValueError("… a 1-based source position is never below 1")`
* `assay/src/assay/statement_attribution.py:103-109` — `StatementBlock.__post_init__`, the identical loop.

**The probe.** I built the Go project's own canonical `//line`-directive fixture (`/usr/local/go/src/cmd/cover/cover_test.go`'s `lineDupContents`, the `TestLineDup` corpus) in `tester-unified-go:local`, `--network=none`, and ran `go test -covermode=count -coverprofile`. The real toolchain emits:

```text
linedup/linedup.go:6.25,100.0 1 100
linedup/linedup.go:100.0,100.0 1 100
linedup/linedup.go:100.0,102.0 1 50
linedup/linedup.go:100.0,102.1 3 25
...
```

Feeding those exact bytes to `coverage.parse_coverage_artifact(..., declared_format="go-cover", producer="go-test")`:

```
BRANCH d938ab8c : AssayError ERROR UNREADABLE_ARTIFACT
                  "go coverprofile: column number 0 in '100.0' is not positive"
MAIN             : parsed OK. executed [5,6,7,8,9,10] …
```

And independently, `StatementBlock(start_col=0, …)` — which the **oracle's own output for the same file** contains — raises `ValueError: StatementBlock.start_col is 0; a 1-based source position is never below 1`, so `go_stmtpos._read_block` would refuse the oracle too.

**In fairness to the branch:** `main`'s acceptance was not right either — it expanded those records into lines 5-105 of a 22-line file. So the ask is not "restore main's behaviour"; it is "stop asserting a false fact about Go and name the real cause", plus a ruling on the disposition (DA-R2).

**Why it is wrong, not merely strict.** A `//line file:line` directive with no column sets `token.Position.Column` to 0 by design; that is exactly the case `cover.go:1053-1058`'s own comment names ("positions can repeat when there is a line directive that does not specify column information", issues #27530/#30746) and the reason `dedup` exists at all. The invariant "a 1-based source position is never below 1" is a **guessed fact about `cmd/cover`'s output that the toolchain disproves** — the same class of defect the whole wave exists to remove (A-334: measure the external system; A-217: adapt, do not invent).

**Blast radius.** Not cgo — I checked, a cgo package's profile carries ordinary columns. It is Go sources carrying `//line` directives without a column: goyacc output, `peg`/`ragel`-style generators, and anything hand-emitting `//line`. For such a project **one generated file poisons the whole lane**: `parse` refuses the entire artifact, so R1 is `ERROR`/`UNREADABLE_ARTIFACT` with a message telling the consumer their profile is malformed when it is exactly what `go test` wrote. The failure is loud and fail-closed, which is why this is a blocker rather than a catastrophe — but "Go is supported at R1" (README, CONSUMERS) is now a claim with an unstated exclusion.

**What must change, and what is a product call.** Two things are defects regardless of disposition:

* **(a) The invariant is stated as a fact about Go and is false.** `"a 1-based source position is never below 1"` (`model.py:221`, `statement_attribution.py:108`) and `"column number 0 … is not positive"` (`go_cover.py:230`) assert something about `cmd/cover`'s output that `cmd/cover` contradicts. Whatever the disposition, those three sites must say what is actually true and cite `go/token`'s `//line`-without-column behaviour and `cover.go:1053-1058`'s own comment.
* **(b) The refusal misattributes the cause and has the wrong blast radius.** A consumer is told their profile's column is "not positive"; the real cause is a `//line` directive in one file, and the refusal takes down the whole artifact. Compare `go_modfile._refuse`'s own docstring, which rejects exactly this kind of misdirection.

**The disposition itself I am NOT prescribing — it is DA-R2 below**, because simply relaxing the bound is not obviously right. I applied "columns `>= 0`, lines `>= 1`" in a scratch copy and ran the full chain on the real bytes: `parse` succeeds, the extent join matches **all nine** records against the oracle's nine blocks with agreeing `num_stmts`, and the result is `executed [6, 100, 101, 103, 104] / missing []`. But `linedup.go` is 22 lines long: those are `ld.go`'s line numbers, remapped by the directive, under `linedup.go`'s profile key. `git diff` names physical lines, so lines 100-104 would intersect no added line, and such a file would silently measure **0/0** — DESIGN-GUIDE §5's own laundering-gate hazard. So "allow column 0" trades a loud wrong-cause refusal for a quiet under-measurement, and that is a call for the operator, not for me or the implementer.

**Minimum fix I can prescribe unconditionally** (whichever disposition is ruled):

1. Correct all three invariant statements (a), and make the refusal name `//line` directives as the cause and the FILE it came from, not "column … is not positive" (b).
2. Add the `linedup` profile + its oracle document to `carve-assets/P27-recarve/` as a committed witness, with the recipe in §A.3, and a test pinning whichever behaviour is ruled.
3. State the limit in CONSUMERS' Go section beside the other four ("what a Go lane does not do"), and file a backlog entry for the disposition if it is deferred.
4. While there: route `CoverageBlock`'s `ValueError` through `_malformed` at the `_parse_block` construction site (see SHOULD-FIX 5).

This also discharges REPORT §5 item 4's own open question — the `dedup` replication is proven correct by this witness.

### BLOCKER 2 — `registry.py`'s stated justification for having no preflight machinery is now false, and this wave is what falsified it

**File/line:** `assay/src/assay/registry.py:41-44` (line 43 carries the sentence; file untouched by the branch):

> "…every adapter a built-in registry can advertise today (Python, Go, and P34's own SQL) declares ``external_tools = ()``, so **no real entry here ever exercises one**."

`GoAdapter.external_tools` is now `("go",)` (`adapters/go.py:559`), and my own `assay lanes --json` on a Go lane returns `external_tools: ["go"]`. The sentence is not decoration: it is the reason the module gives for adding no preflight machinery of its own, and it is quoted from A-087's reasoning, which A-394/B047-item-2 explicitly rescoped. `cli.py:328-347` rewrote the *identical* paragraph for exactly this reason ("The sentence above used to continue …") and this one was missed — the same class of finding the wave itself raised as A-399 and A-400, applied to a file nobody re-read.

**The probe:** `grep -rn 'external_tools = ()' src/` → `registry.py:39,43` (plus the accurate SQL-only references in `verify.py`/`verdict.py`); `PYTHONPATH=src python3 -m assay.cli lanes --json` on a Go lane → `["go"]`.

**Prescription:** rewrite `registry.py:37-49` the way `cli.py`'s paragraph was rewritten — keep A-087's history, state that Go now declares `("go",)` and that `run_lane`'s preflight is exercised by every real Go lane. Cite A-394/B047 item 2.

*Adjacent, and NOT a blocker because it predates this wave:* the same paragraph's `registry.py:29-32` ("the CLI … naming Python at ``R1`` only, and no entry at all for Go") was already stale before Wave C (SQL and JavaScript were registered and it still said Python-only). Fix it in the same edit or file it; do not leave it because it is old.

## SHOULD-FIX

1. **Three `test_cli_run.py` docstrings/names that A-399's sweep missed.** `test_run_refuses_an_r2_lane_for_an_unregistered_language` (line 705, docstring line 727: "Go remains here: it still has no producer path wired to any rigor level (P22)"), `test_run_refuses_an_unregistered_language_at_r3_with_a_real_artifact` (line 1096, docstring line 1100: "Go has no producer path wired to ANY rigor level yet (P22), R3 included"), and `test_run_refuses_sql_at_r1_…` (line 867: "exactly like Go's own **total non-registration** one test up"). All three sentences are false after A-394. Both `go` tests still assert a true property (R2/R3 refused before the command runs) but they now exercise the *registered-at-a-different-rigor* branch, not the *unknown-language* branch their names claim — A-399's own "quietly become a decoy" shape. Consequence: **no CLI-level test exercises an entirely-unknown language any more** (the branch is still covered at the unit level by `test_registry.py::test_an_unregistered_language_is_refused_not_defaulted`, which is why this is not a blocker). `cli.py:325-326` still advertises "a rigor level for a language this registry does not know at all, is refused … Both refusals are asserted as controls in `tests/test_cli_run.py`" — half of that is no longer true. Fix: rename/re-point one of them to a genuinely unregistered language (e.g. `"rust"`), keep the other as the registered-at-another-rigor control and say so.

2. **`go_modfile.py` is stricter in its docstring than in its code — three measured divergences from the real parser.** I ran each variant through `go list -m` inside the image:

   | `go.mod` | real toolchain | assay |
   |---|---|---|
   | `module /` | `malformed module path "/": trailing slash` | derives `module_path=""` — the "empty module path" guard at `go_modfile.py:269-274` runs **before** `rstrip("/")` at line 275, so it does not fire. Caught downstream by `normalize_coverage_key`, but as `ERROR`/`UNREADABLE_ARTIFACT`, i.e. blaming the artifact for a lane-config fault — precisely what `_refuse`'s own docstring (lines 113-122) says it exists to avoid. |
   | `module example.invalid/x/` | `malformed module path: trailing slash` | silently accepted as `example.invalid/x` |
   | `module ex"ample` | `invalid quoted string: unquoted string cannot contain quote` | accepted as a bare ident |

   None can produce a wrong verdict (a `go.mod` the toolchain rejects means no profile exists), so this is a fidelity/attribution issue, not a correctness one. But the module docstring states rule 4 as "``parseString`` … **REFUSES anything else containing a quote character**" and it is not implemented. Fix: move `rstrip("/")` before the emptiness check (or reject a trailing slash outright, as Go does), and reject an `ident` containing `"`/`` ` ``.

3. **The vacuous-attribution branch lets a Go lane past A-392's guard with no oracle and no helper.** `runner._attribute_statements_for_lane` (`runner.py:912-920`) returns `statement_attributed=True` whenever no file carries blocks. `test_a_profile_with_no_block_bearing_files_is_attributed_vacuously` documents the intent ("such a profile is already statement truth"), and for a genuinely line-based format that is right. It is **not** right for the reachable case: `judge.language` and `judge.coverage.format` are independent by design (`config.py:2169-2172`), so a Go lane may declare `format = "lcov"`, and lcov generated *from a Go coverprofile* (`gcov2lcov`-style converters do the naive block expansion) is exactly the over-approximation A-392 exists to refuse. Probed: with lcov keys carrying the module prefix, `_attribute_statements_for_lane` returns `statement_attributed=True`, `helpers` empty, and `_check_statement_attribution` passes. Fix or rule: either refuse a `requires_statement_attribution` adapter whose profile carries no blocks at all (there is no honest Go profile without them), or record why the vacuous pass is safe in the docstring rather than asserting it. This is a **decision ask** if you prefer (see below).

4. **Cite Go's own merge semantics for the B061 fold.** `statement_attribution.py:186-190` (the fold's comment) justifies executed-wins only by assay-internal precedent. The authoritative citation is `src/cmd/vendor/golang.org/x/tools/cover/profile.go`, `ParseProfilesFromReader`: `Count |= b.Count` for `set`, `Count += b.Count` otherwise. One sentence turns an internal convention into a transcribed fact, which is this wave's own standard.

5. **A malformed record escapes `go_cover.parse` as a bare `ValueError`.** `mode: set` + `m/x.go:5.10,5.2 1 1` → `ValueError: CoverageBlock ends at 5.2, before it starts at 5.10`, raised from `model.py:219` through `_parse_block`'s `CoverageBlock(...)` construction (`go_cover.py:203-210`), which has no `try`. `runner._run_prepared_lane`'s `except AssayError` (`runner.py:2173`) catches only that, and `cli.main:288` catches only `AssayError` — so this is an uncaught traceback, no verdict artifact, and the coverage reservation closed at `runner.py:2175-2176` is never closed. `main` accepted the same bytes. Fix: wrap the `CoverageBlock(...)` construction and re-raise via `_malformed`.

6. **`numStmts` inconsistency across repeated records is silently accepted.** `x/tools`' merge loop refuses it outright (`inconsistent NumStmt: changed from %d to %d`); assay's fold keeps one record and checks only that one against the oracle. Probed: `[count=1 numStmts=1]` + `[count=0 numStmts=7]` for one extent, oracle `num_stmts=1` → **accepted silently**. Corrupt input only, never toolchain output, so: a backlog entry, or one extra check in the fold.

7. **Add the `linedup` witness to `carve-assets/P27-recarve/`** with its profile, its oracle document and its provenance, and strike REPORT §5 item 4's "unproven" — the `dedup` replication is now proven by a real toolchain witness (mine, reproducible with the recipe in this file's §A.3).

## DECISION ASKS

* **DA-R1 — vacuous statement attribution (SHOULD-FIX 3).** Should a lane whose adapter declares `requires_statement_attribution` be *refused* when its profile carries no block extents at all, rather than marked attributed vacuously? The current behaviour is deliberate and tested; the question is whether "no blocks" should be read as "already statement truth" for an adapter that has no non-block format. Not mine to improvise.
* **DA-R2 — what a `//line`-bearing Go source should do (BLOCKER 1's disposition).** Three defensible shapes, and the choice is a product call: **(i)** relax the column bound to `>= 0` — the join then works, but the remapped line numbers intersect no physical diff line, so such a file silently measures 0/0; **(ii)** refuse, but per-file and with a message naming `//line` as the cause rather than the whole artifact for a "column not positive"; **(iii)** refuse the lane, documented as "assay cannot judge Go sources carrying `//line` directives", with a backlog entry for (i)/(ii). My measurement supports any of the three; my only firm position is that the current message and blast radius are wrong (BLOCKER 1 (a) and (b)).

I did **not** re-open DA-8; A-404 is implemented as ruled.

## CLAIMS I COULD NOT VERIFY

| claim | where | why not |
|---|---|---|
| ~~"3905 passed, 18 skipped" on the devcontainer full suite~~ | REPORT §43 | **VERIFIED after the fact** — see the addendum. |
| gate runs 1–9 (their commits, not the tip) | REPORT §6/§14/§22/§30/§39 | historical; I re-ran the gate on the tip `d938ab8c` myself and it passes |
| "the oracle was run over all 26 changed `internal/` files at HEAD" | REPORT §41 | 26 is the changed-file count including `_test.go`; only 12 are non-test files with added lines, which is what my classifier and their table actually cover. Cosmetic wording, not a measurement error. |
| the two srdm `go test` runs' profiles "are NOT byte-identical, but every difference is in `cmd/srdm`" | REPORT §41 | I did not re-run srdm's argv twice; their byte-identical control lane and my independent run both land on 418/394, so the claim is not load-bearing |
| A-396's "with the ENTIRE package-data stanza deleted the wheel still carries the helper" | A-396, `pyproject.toml` | I did not rebuild with the stanza deleted; `test_go_helper_is_packaged.py` asserts the outcome (the wheel contains it) rather than the mechanism, which is the honest shape either way |
| main's B053/B054 texts | LOG/BRIEF-6 | read on `main`, not on the branch; the branch deliberately does not copy them |

---

## ADDENDUM — full-suite count, my own run

`PYTHONPATH=<scratch>/assay/src python3 -m pytest tests -q -p no:cacheprovider` in the detached scratch worktree at `d938ab8c`, devcontainer venv (Python 3.14.6, pytest 8.4.2):

```
3908 passed, 18 skipped, 1 warning in 356.16s
```

REPORT §43 claims "3905 passed, 18 skipped … B061's three tests landed after that count". 3905 + 3 = **3908**. The claim is exact. ✔

---

## SELF-COMPACTION PROMPT

**KEEP**

* **Verdict:** ACCEPT-conditional on `feature/assay-wave-c-go` @ `d938ab8c`. Two blockers, seven should-fixes, one decision ask (DA-R1), all above.
* **Blocker 1 recipe (the important one).** Fixture: `/usr/local/go/src/cmd/cover/cover_test.go`'s `lineDupContents`. Scratch tree at `…/scratchpad/dedup/` (`linedup/{linedup.go,linedup_test.go,go.mod}`, `helper/{stmtpos.go,go.mod}`, `run.sh`). Run: `docker run --rm --network=none --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" --mount type=bind,src=/home/vb/mdt--mounted-folders/tmp/<rel>,dst=/w -w /w tester-unified-go:local bash /w/run.sh`. Expected profile lines include `linedup/linedup.go:100.0,102.0 1 50`. Branch refuses at `go_cover.py:230`; main parses. Fix = columns `>= 0`, lines `>= 1`, in `go_cover._parse_pos`, `CoverageBlock.__post_init__`, `StatementBlock.__post_init__`. Verified post-fix result: `executed [6,100,101,103,104] / missing []`, nine-for-nine extent join.
* **Blocker 2:** `src/assay/registry.py:41-44` ("every adapter … declares `external_tools = ()`") is false; rewrite as `cli.py:328-347` was. Adjacent stale line 29-31 predates the wave.
* **Should-fix anchors:** `tests/test_cli_run.py:705/727`, `:1096/1100`, `:867`; `go_modfile.py:269-275` + `_argument_at`; `runner.py:912-920` (vacuous attribution) + `evaluate.py::_check_statement_attribution`; `statement_attribution.py:186-190` (the fold's comment) (missing citation → `cmd/vendor/golang.org/x/tools/cover/profile.go` `Count |= / +=`); `go_cover.py:203-210` (bare `ValueError`) + `runner.py:2173` + `cli.py:288`; the `numStmts` fold gap; add the `linedup` witness.
* **What is PROVEN and must not be re-litigated:** oracle transcription faithful to `cover.go` (function-by-function diff); all eight witnesses reproduced; `collision-col{A,B}` → `{4,6}`/`{4,5}`; F008-A4 regeneration byte-identical; F008-A5 reproduced by my own classifier (684/639, 418/394, 266 all extent-expansion, file-absence positively empty, 24 ⊂ 45); B061/A-403/A-404/helpers/A-392 mutation-tested non-hollow; registered gate PASS on `d938ab8c` in my own hands; qualification 5/5; wire schema untouched; renumber is a pure substitution over 25 files; P27 assets and `shared-ramdisk-depot-manager/` untouched.
* **Rules:** never edit or commit in the review worktree; probes go in `…/scratchpad/probe-wt` (detached worktree at `d938ab8c`, restore with `git checkout -- assay/`) or in-container; all Go inside `tester-unified-go:local`, `--network=none`, `--cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND"`; zipapp built outside the worktree at `…/scratchpad/rel/dist/`; host `/tmp` maps to `/home/vb/mdt--mounted-folders/tmp`.
* **Fix-verification round:** re-run (a) the `linedup` probe, (b) the new witness test red-then-green, (c) `bash assay/tools/tester-unified-gate.sh <worktree>` with a separate verdict read, (d) `ASSAY_GO_QUALIFICATION=1 pytest assay/tests/qualification/test_go_r1_real.py`. The srdm F008-A5 rebuild need not be repeated unless `statement_attribution.py` or `go_cover.py` changes shape.

**DROP**

* The full text of the eight witnesses, the profiles, and the oracle JSON documents (regenerable in ~30s by the two committed scripts).
* The per-function `cover.go` diffs (settled: faithful).
* The 68 761-line srdm profile and the per-file classifier table (settled: reproduced exactly; the totals above are enough).
* The invalid first A-403 mutation attempt (working-tree edit vs committed-OID build) — the lesson is one line: `build_release.build` builds from HEAD's OID, so mutations must be committed in the scratch worktree.
* All of `assay-WAVE-C-go-{LOG,REPORT,BRIEF-*}.md` — read, reconciled, nothing outstanding except the "unverified" table above.
