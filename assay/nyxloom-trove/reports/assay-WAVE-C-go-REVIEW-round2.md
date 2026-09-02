# assay Wave C (Go / P27 re-carve) — adversarial review, **round 2** (fix verification)

Reviewer: fresh adversarial reviewer, same session lineage as round 1.
Subject: `feature/assay-wave-c-go` @ **`1d464fc4`** (worktree
`/workspaces/vbpub/.worktrees/assay-wave-c-go`).
Round-1 review committed verbatim on the branch as
`assay/nyxloom-trove/reports/assay-WAVE-C-go-REVIEW-round1.md` (`210812f6`) —
I diffed it against my scratch original: identical.

---

## 0. Verdict

**NOT ACCEPT. One blocker remains (BLOCKER R2-1).**

Everything the fix round was dispatched to do, it did. Both round-1 blockers,
all seven should-fixes and both decision asks verify as implemented by my own
probes, re-run verbatim. The gate is green in my own hands on the tip. The
srdm residue reproduces exactly.

The blocker is not a defect the fix round introduced — I proved that with a
counterfactual against the pre-round-1 build. It is a **pre-existing wave
defect my own round-1 probes missed**, and A-405 has now given it a second,
*ruled* path: in the only realistic shape of the case DA-R2 was ruled for, the
consumer does not get the ruled refusal. It also swallows the flagship refusal
CONSUMERS.md has documented since generation 5, and in both cases writes **no
verdict artifact at all**.

It is 12 lines to fix, at a site the code's own comment already names as the
correct one. I built that fix in a scratch worktree, rebuilt the zipapp from
it, and re-ran all five scenarios: all five then behave as ruled.

---

## 1. Method

* Nothing was edited or committed in `/workspaces/vbpub/.worktrees/assay-wave-c-go`.
  Confirmed clean at `1d464fc4` on entry and on return (§6).
* Probes ran in detached scratch worktrees under
  `…/scratchpad/{probe-wt,fixprobe}` and in disposable container mounts.
* Every Go execution: `tester-unified-go:local`, `--network=none`,
  `--cgroup-parent=dev-background.slice`, `GOPROXY=off GOWORK=off
  GOTOOLCHAIN=local GOFLAGS=-mod=mod` (A-042/A-043).
* Every zipapp was built OUTSIDE the worktree with
  `assay/gate/distribution/build_release.py --repo <wt> --outdir <scratch>`.
  Because `build_release` builds from **HEAD's committed OID**, every mutation
  probe below is a *commit* in a scratch worktree, never a working-tree edit —
  the invalid-probe lesson from round 1.
* Verdicts read in a separate step from the runs that produced them (L4).
* Two detached scratch worktrees are left in place for round 3:
  `…/scratchpad/probe-wt` (the tip, unmodified — harness home) and
  `…/scratchpad/fixprobe` (`254e23ed`, the proven prescription). Neither is on
  a branch. The five-scenario lane battery is `…/scratchpad/ldlane/work/`
  (`setup.sh`, `laneA..E.sh`, `all.sh`); the srdm F008-A5 harness is
  `…/scratchpad/a5/` (`in-image.sh`, `round4.sh`).

Builds used, by sha256 of the zipapp:

| build | commit | pyz sha256 |
|---|---|---|
| round-1-era | `875382d2` (ancestor of `d938ab8c`) | `…dev38+g875382d2` |
| **the tip** | `1d464fc4` | `37f019da1f00137ab419d8bb872ec159ecaaa079de6b7e895a89ecaa78345ac0` |
| prescription probe | `254e23ed` (tip + 12 lines) | `…dev48+g254e23ed` |

---

## 2. Round-1 findings — re-run verbatim

| round-1 item | ruling | my probe | result |
|---|---|---|---|
| **BLOCKER 1** — parser refused column 0, so a `//line`-remapped Go file killed the whole artifact | DA-R2 shape (ii) | rebuilt the `lineDupContents` witness in-image; re-ran the parser on a real column-0 profile; scenarios A/D below | **FIXED** — `_parse_pos` accepts `col >= 0` and refuses negative naming `//line`; lines still `>= 1`; `FileCoverage.line_directive_remapped` derived from `blocks`; A-405 |
| **BLOCKER 2** — `registry.py:30,43` stated two facts that Wave C falsified | — | read both paragraphs; ran `test_adapters_go_registration.py` | **FIXED** — both rewritten; a third stale copy in `cli.py`'s module docstring ("JavaScript at R1 only" → R1 and R2) found and fixed by the implementer, which I had missed |
| **DA-R1** — the vacuous `statement_attributed=True` branch | refuse-never-vacuous | built a Go lane declaring a block-less format; config load | **FIXED** — refused at load naming language, format and `'go-cover' (producers: go-test, covdata)`; the vacuous branch is deleted and replaced by an `AssayError`; empty profile terminates `NO_MEASUREMENT`/`EMPTY_COVERAGE` with no helper and no PASS. A-406 |
| **should-fix 1** — CLI unknown-language branch had lost its test | — | `test_cli_run.py` | **FIXED** |
| **should-fix 2** — three `go.mod` divergences from the real parser | — | `go list -m` on five variants in-image | **FIXED**, and the implementer found a fourth I did not probe (backquote **mid**-token: `` module ex`ample ``). Their in-image transcript reproduces on my run |
| **should-fix 3** — reachable hole in the vacuous branch | folded into DA-R1 | see DA-R1 | **FIXED** |
| **should-fixes 4/5/7** — docs/comment drift | — | read the diffs | **FIXED** |
| **should-fix 6** | ruled IMPLEMENTED, not filed | read the diff | **IMPLEMENTED** |
| **my citation error** — I cited `cover.go:1053-1058`; implementer measured `1055-1060` | — | re-read `cmd/cover/cover.go` inside the image | **the implementer is right and I was wrong**; `dedup` at `1073-1090` also confirmed |

### Gate, suite, qualification — in my own hands, on the tip

| check | implementer | me, on `1d464fc4` |
|---|---|---|
| registered gate | run 11 PASS on `4c3e83f4` | **PASS on the tip.** All 12 `ASSAY_GATE_PHASE=` markers, `topos-qualified` (`outcome=PASS exit_code=0`), `cmru-b006a-qualified`, `independent-self-hosting-passed`, `ASSAY_REGISTERED_GATE_COMPLETE=1`, exit 0 (`…/probes2/gate-tip-r2.log`) |
| gate run 11 log markers | — | read myself: same 12 markers, R0/R1/R2/R3 all PASS, R3 canary `UNCOVERED_LINES` expected == observed |
| full suite | 3939 passed, 11 skipped | **3939 passed**, 18 skipped, exit 0 (skip delta: environment-conditional; passed count identical — see §7) |
| Go qualification | 5 passed | **5 passed** (`ASSAY_GO_QUALIFICATION=1`) |

### srdm F008-A5 — the pre-adjudicated residue

REPORT §48 records not repeating the srdm rebuild after `statement_attribution.py`
and `go_cover.py` changed shape, as a judgement call. **My round-1 harness
survived; I re-ran it against a tip-built zipapp.** The judgement call was
correct:

```text
verdict-r2            PASS PASS None (12, 418, 394, 94.26)
verdict-samefile-r2   PASS PASS None (12, 418, 394, 94.26)
covergate, unchanged: 639/684 changed executable lines covered (93.4%)
```

**418/394/94.26% holds, bit for bit, on both the real-run and the
same-profile-bytes lanes.** Nothing in the fix round moved the join for a
profile with no zero columns and no contradictory `numStmts`.

---

## 3. BLOCKER R2-1 — a Go R1 lane that refuses *after* the oracle ran reports assay's own wiring instead of the refusal, and writes no verdict

### 3.1 What happens

`_attribute_statements_for_lane` records the `statement-positions` helper the
instant the oracle returns —

* `assay/src/assay/runner.py:997` — `on_helper_invoked(report.helper)`, fired
  immediately after `adapter.statement_blocks(...)` and **before**
  `attribute_statements`.

— and `assemble_verdict` then *refuses* any verdict whose helper roles are not
supported by a rendered claim:

* `assay/src/assay/runner.py:1493` — "lane {name} recorded helper role(s)
  {unsupported} but rendered no claim carrying the payload each requires …
  Refusing before constructing an incomplete verdict."
* the correspondence rule itself: `assay/src/assay/verdict.py:406`
  `supported_helper_roles` — `statement-positions` is supported only while some
  R1 claim carries `coverage is not None`.

So **every** judge refusal that happens after the oracle has run — i.e. every
refusal raised inside `attribute_statements` or `evaluate_*` — voids the R1
payload, orphans the helper entry, and is replaced by a message about assay's
internal `helpers[]` wiring. `run_lane` never returns, so `--verdict-json`
is never written.

The runner's own comment at `:1476-1483` already states the correct design:

> It is deliberately a refusal rather than a filter — the one legitimate way to
> hold a helper whose claim has been voided is
> `_replace_highest_higher_rigor_claim_with_git_failed`, which drops the entry
> itself, **at the site that took the payload away**.

There is exactly one such site for R1 (`runner.py:2769`, `claims += (r1_claim,)`)
and it does not drop anything.

### 3.2 Measured, in-image, against the tip zipapp (`37f019da…`)

Five lanes, real `go test`, real oracle, real `git diff`; fixture is a two-file
Go module where `internal/gen/gen.go` carries `//line` directives
(`tester-unified-go:local`, `--network=none`). Transcript:
`…/probes2/ldlane-r2.log`, `…/probes2/laneE.log`.

| # | lane | expected (DA-R2 / CONSUMERS.md) | **observed on `1d464fc4`** |
|---|---|---|---|
| A | flagged file present, **no** judged lines in it | ignored, lane proceeds | `unit: PASS (exit 0)` ✔ |
| B | flagged file **has** judged lines, ordinary Go file also present | `ERROR/BAD_LANE_CONFIG` naming `//line`, the file, the remedy | ✘ `assay: ERROR/BAD_LANE_CONFIG: lane 'unit' recorded helper role(s) ['statement-positions'] but rendered no claim carrying the payload each requires …` — **and `verdictB.json` was never created** |
| C | **stale** Go profile, flagged file also present | `ERROR/UNREADABLE_ARTIFACT` (CONSUMERS.md:1889-1892, verbatim) | ✘ identical helper-wiring message; **no `verdictC.json`** |
| D | the **only** block-bearing file is flagged and judged | `ERROR/BAD_LANE_CONFIG` | ✔ `unit: ERROR/BAD_LANE_CONFIG (exit 2)`, verdict written (here the oracle never runs, so no helper is recorded — the masking cannot fire) |
| E | **stale** Go profile, **no `//line` file anywhere** | `ERROR/UNREADABLE_ARTIFACT` | ✘ identical helper-wiring message; **no `verdictE.json`** |

Scenario D is what makes this decisive: A-405's own refusal is *correct*. It is
simply unreachable in the shape DA-R2 was ruled for, because a Go module with a
generated file always has other Go files too, and the moment one of them is
present the oracle runs and the mask goes up.

### 3.3 It is NOT a regression from the fix round — I checked

Scenario C at the round-1-era build `875382d2` gives
`stale: ERROR/UNREADABLE_ARTIFACT` with a verdict written — which looks like a
regression, and I nearly reported it as one. It is not: at that build the
profile died earlier, in the parser, on the *old* column-0 bug
(`go coverprofile: column number 0 in '100.0' is not positive`), so the oracle
never ran. I re-checked with scenario **E**, which contains no `//line` file at
all:

```text
######## OLD-875382d2 ########   assay: ERROR/BAD_LANE_CONFIG: … recorded helper role(s) ['statement-positions'] …   (no verdict)
######## TIP-1d464fc4 ########   assay: ERROR/BAD_LANE_CONFIG: … recorded helper role(s) ['statement-positions'] …   (no verdict)
######## PROBEFIX      ########   stale: ERROR/UNREADABLE_ARTIFACT (exit 2)   verdictE.json written
```

**Identical on both builds.** This is a wave defect that predates round 1 and
that my round-1 probes missed — I say so plainly. What the fix round changed is
that it is now also the answer to a *ruled* question.

### 3.4 Prescription — proven, 12 lines, at the site the comment names

In `assay/src/assay/runner.py`, immediately after `claims += (r1_claim,)`
(`:2769`):

```python
                claims += (r1_claim,)
                if r1_claim.coverage is None:
                    # The judge refused AFTER the oracle ran, so the payload
                    # the helper produced went with it. Drop the entry here,
                    # at the site that took the payload away -- exactly what
                    # `_replace_highest_higher_rigor_claim_with_git_failed`
                    # does -- so `assemble_verdict`'s guard stays an assertion
                    # about WIRING instead of becoming the message the
                    # consumer gets instead of the real refusal.
                    kept = supported_helper_roles((r1_claim,))
                    helpers_seen[:] = [
                        helper for helper in helpers_seen if helper.role in kept
                    ]
```

`supported_helper_roles` is already imported (`runner.py:138`) and is the
single definition of the correspondence rule, so this adds no second copy of
the table. The `assemble_verdict` guard is left untouched and stays a true
wiring assertion.

Built as `254e23ed`, zipapp rebuilt from it, all five scenarios re-run in-image
(`…/probes2/ldlane-fixprobe2.log`):

| # | with the prescription |
|---|---|
| A | `unit: PASS (exit 0)` |
| B | `unit: ERROR/BAD_LANE_CONFIG (exit 2)` — **verdictB.json written** |
| C | `stale: ERROR/UNREADABLE_ARTIFACT (exit 2)` — **verdictC.json written** |
| D | `unit: ERROR/BAD_LANE_CONFIG (exit 2)` — verdictD.json written |
| E | `stale: ERROR/UNREADABLE_ARTIFACT (exit 2)` — **verdictE.json written** |

Regression test to add with it: a Go R1 lane whose judge refuses after the
oracle ran must (a) carry the judge's own reason code, and (b) produce a
verdict artifact. Scenario E is the cheapest shape — it needs no `//line`
fixture at all.

Suite under the prescription (`254e23ed`, devcontainer, whole `assay/tests`):
**3939 passed, 18 skipped, exit 0** — byte-for-byte the same counts as the
unmodified tip. Nothing regresses, including `assemble_verdict`'s own B047
item-5 guard tests, which still see the wiring defect they were written for.

---

## 4. Should-fixes

**SF-R2-1 — `test_the_same_refusal_fires_in_whole_target_mode` is hollow; the
whole-target half of A-405 has no test at all.**
`assay/tests/test_go_line_directive_witness.py:322`. Its fixture judges
`judge.targets = ("linedup.go",)` against `repo_top=Path("/repo")`, which does
not exist, so `_resolve_whole_target` refuses first and the test's three
assertions (`Outcome.ERROR`, `ReasonCode.BAD_LANE_CONFIG`, `"judge.targets" in
str(...)`) are all satisfied by the *wrong* refusal:

```text
UNMUTATED raiser: ERROR BAD_LANE_CONFIG
  judge.targets entry 'linedup.go' does not exist as a regular file under the
  project root /repo (looked for /repo/linedup.go); a whole-target entry is
  always a regular file, never a directory
```

Mutation M-B2 (replace `evaluate.py:1071`'s
`if file_cov is not None and file_cov.line_directive_remapped:` with
`if False:`) leaves all 69 tests in the four A-405 modules green. The branch
itself is correct and correctly *ordered* (before `TARGET_NOT_MEASURED`, with a
comment saying why) — it is simply unexercised. Fix: materialise the target on
a `tmp_path` repo so resolution succeeds, and assert on the message text, not
just the reason code.

Full mutation battery on the A-405 code (four test modules, 69 tests):

| mutation | result |
|---|---|
| M-A: `line_directive_remapped` always `False` | 6 failed ✔ |
| M-B: changed-lines refusal removed | 1 failed ✔ |
| **M-B2: whole-target refusal removed** | **69 passed — SURVIVED** |
| M-C: attribution keeps remapped lines | 4 failed ✔ |
| M-E: column bound back to `> 0` | 8 failed ✔ |
| **M-F: runner still sends remapped files to the oracle** | **69 passed — SURVIVED** |

**SF-R2-2 — M-F: the runner's remapped filter is a second guard with no test.**
`runner.py`'s `to_attribute` filter is masked by `attribute_statements`' own
short-circuit, so removing it changes nothing observable. It is a legitimate
defence-in-depth (it stops the oracle being run on a file whose result is
discarded), but two guards with one test between them is exactly the shape that
rots. Either assert the oracle is *not* asked about a flagged path, or say in
the comment that it is an optimisation and the correctness guard is downstream.

**SF-R2-3 — CONSUMERS.md renders refusal text the consumer never sees.**
CONSUMERS.md:1953-1968 (new, A-405) and :1888-1897 (pre-existing) both print
`status: ERROR / reason_code: … / <full message>` as if that is the operator's
output. It is not, on any surface: a judge-phase `AssayError` is folded into an
R1 claim, and a `Verdict` carries no free-text cause (A-138/A-170). Control,
run on the tip with a Python lane and a deliberately malformed artifact:

```console
$ assay run unit --file …/assay.toml
unit: ERROR/FORMAT_MISMATCH (exit 2)
```

— no message. Scenario D above behaves the same: the operator sees
`unit: ERROR/BAD_LANE_CONFIG (exit 2)`, and `verdictD.json` carries
`reason_code` and nothing else. So DA-R2's "*naming `//line`, the file and the
remedy*" is delivered **into the exception**, and into a library caller's
hands, but not to an `assay run` consumer. This is structural and pre-existing,
not something the fix round broke; it is the doc that overstates. Either mark
those blocks as "the refusal assay raises" rather than "what you will see", or
raise the wire question as a product call (below).

---

## 5. Decision asks

**DA-R3 (product).** Should a judge-phase refusal's *text* reach the consumer?
Today the closed `ReasonCode` vocabulary is the whole channel, by A-138/A-170,
and `BAD_LANE_CONFIG` is shared by at least five distinct causes in the Go
path alone — so "which config is bad" is not answerable from the artifact.
Wave C did not create this and must not improvise a wire field for it (the
dispatch forbids both a new reason code and a schema change). Options, for the
controller, not for me: (a) leave it and fix the docs (SF-R2-3), (b) route
judge-phase refusal text to the existing `diagnostics` stream that
`environment_command` already uses (`runner.py:365`, no wire change), (c) a
v10 field. My recommendation is **(a) now, (b) considered in the post-Wave-C
patch wave** — it is a one-line call site and it is already the established
seam for exactly this problem.

---

## 6. Controller checklist

| check | result |
|---|---|
| no new reason code | ✔ `git diff d938ab8c 1d464fc4 -- vocabulary.py` adds `STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE` and its docstring only; no enum member added |
| verdict wire schema untouched | ✔ 0 files touched under `assay/schemas`, `assay/gate`, and `schema_version` is still `9` in every real verdict I produced. `verdict.py` is touched only by the behaviour-preserving extraction of `supported_helper_roles`; the v6/v7/v8 hard-cut and v9 successor drift guards both passed in my own gate run |
| no `!` commit | ✔ none of the six repair commits carries `!` |
| nothing under `shared-ramdisk-depot-manager/` | ✔ 0 files |
| worktree clean at `1d464fc4` | ✔ `git status --short` empty, tip unchanged, on entry and on return |
| round-1 review committed verbatim | ✔ byte-identical to my scratch original |

---

## 7. Implementer claims I could not verify, or verified differently

| claim | status |
|---|---|
| full suite "3939 passed, 11 skipped" | passed count **reproduced exactly**; I measure **18** skipped, twice, on two worktrees. All 18 are environment-conditional and self-explaining: 9 × `test_python_qualification.py` ("requires the tester-unified image's own `/opt/tester-venv`"), 5 × `qualification/test_go_r1_real.py` (needs `ASSAY_GO_QUALIFICATION=1`), 2 × `qualification/test_javascript_real_vitest.py` (needs `ASSAY_NODE_QUALIFICATION=1`), 1 × `test_standalone.py:141`, 1 × `test_self_hosting.py:140`. The 7-skip delta is the 9 python-qualification tests minus whatever ran for them; not a defect, but the LOG's "11" is not reproducible outside the environment that produced it and should say where it was measured |
| "gate run 11 PASS on `4c3e83f4`" | verified from the log's own markers, and independently re-run by me on the tip `1d464fc4` |
| REPORT §45's in-image `//line` witness (nine records, eight with column 0, both `dedup` ladders) | reproduced: my own witness build gives the same profile shape; the one-line offset they explain (leading blank line in `lineDupContents`) is real |
| REPORT §48's srdm judgement call | **verified correct by re-running the harness**: 418/394/94.26 unchanged |
| "no backlog entry filed; B062 still free" | not independently checkable beyond reading the file; consistent |

---

## 8. What must happen before merge

1. **BLOCKER R2-1** — apply §3.4 (or an equivalent that drops the orphaned
   helper at the site that voids the claim) and add the scenario-E regression
   test. Re-run the registered gate.
2. **SF-R2-1** — give the whole-target A-405 test a real target file so it
   exercises the branch it names.
3. SF-R2-2 and SF-R2-3 at the implementer's discretion; DA-R3 is the
   controller's.

Nothing else stands between this branch and merge. The wave's substance — the
oracle, the extent join, the B061 fold, A-403, A-404, A-405, A-406 — is sound,
and I re-derived every load-bearing number in it myself.

---

## SELF-COMPACTION PROMPT

**KEEP:** the verdict (NOT ACCEPT, one blocker); BLOCKER R2-1's mechanism
(`runner.py:997` records the helper → `runner.py:1493` refuses → no verdict) and
its 12-line prescription at `runner.py:2769`; the five-scenario table and that
scenario E reproduces on `875382d2` too (pre-existing, not a regression);
SF-R2-1 (`test_go_line_directive_witness.py:322` is hollow — the fixture's
`/repo` does not exist); SF-R2-3 + DA-R3 (judge-phase refusal text reaches no
consumer; `diagnostics` at `runner.py:365` is the existing seam); srdm holds at
418/394/94.26; gate PASS on the tip in my own hands; 3939 passed / 18 skipped;
5 qualification passed; worktree clean at `1d464fc4`.

**DROP:** every round-1 item that verified FIXED (§2's first table) except the
one-line summary that they did; the cover.go citation correction; the build
sha256 table; the invalid-first-mutation lesson (already learned and applied).
