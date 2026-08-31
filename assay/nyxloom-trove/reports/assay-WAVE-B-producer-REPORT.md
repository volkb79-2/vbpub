# assay Wave B ("producer wave", target release 4.0.0, verdict schema v9) — REPORT

Four scope items, one MAJOR schema cut, four implementer generations. This
report is the per-item acceptance evidence, the decisions, the two things
flagged for a reviewer rather than resolved by me, and what I deliberately did
not do.

**Branch** `feature/assay-wave-b-producer`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-b-producer`, from `main` at
`a78a0046`.

**Scope:** B045 (declare the coverage PRODUCER), B046 (R2 by evidence
ingestion), B043 (a lane-level `cwd`), B041(b) (`isolation.link_paths`).
Resolves **B037**, **B038**(a)(b), **B040**(b). Files **B050**.

---

## 1. Commits

| # | hash | subject |
|---|---|---|
| 1 | `384f3c0f` | `test(assay): a REAL StrykerJS mutation-testing-report-schema artifact (B046 evidence)` |
| 2 | `fac1b73b` | `feat(assay): B045 (1/2) -- the coverage PRODUCER as a declared, per-format, closed fact` |
| 3 | `b85d3a6e` | `docs(assay): Wave B checkpoint 1` |
| 4 | `cc4e955f` | `feat(assay): B045 (2/2 non-schema) -- real branch arcs and the type-only lexer` |
| 5 | `b1a2f0e9` | `docs(assay): Wave B checkpoint 2` |
| 6 | `af14021f` | **`feat(assay)!: verdict schema v8 -> v9 -- the producer cut (B045/B046/B043/B041(b))`** |
| 7 | `1577fa45` | `test(assay): W5 -- the v9 frozen drift-guard generation, and the gate wiring that demotes W4` |
| 8 | `f620c97b` | `docs(assay): Wave B checkpoint 3` |
| 9 | `143e927e` | `feat(assay): B043 -- a lane-level cwd, honoured at every execution site` |
| 10 | `9bb52280` | `feat(assay): B041(b) -- isolation.link_paths, with the teardown canary` |
| 11 | `d0aab6fd` | `feat(assay): B046 -- R2 by evidence ingestion, and javascript at R2` |
| 12 | `1a783f3e` | `docs(assay): the Wave B report` |
| 13 | `a4bf1bc3` | `docs(assay): the Wave B gate transcript` |

*(Rows 12–13 corrected in fix round 1: this table listed 12 of the wave's 13
commits, and named row 12 "(this report)" rather than its hash. Fix round 1's
own commits are listed in §15.)*

**Exactly one `feat(assay)!:` commit exists — `af14021f`.** cmru takes a `!`
anywhere in the release range literally, and one is what 4.0.0 needs.

**Ordering note.** Brief 3 §5 listed B046 before B043. I inverted them, and
the dependency is real rather than aesthetic: an ingested Stryker report's
`projectRoot` is the directory the TOOL ran in and its `files` keys are
relative to that, so resolving them to repository-relative wire paths is
impossible without the lane's declared `cwd`. Implementing B046 first would
have meant hardcoding "projectRoot == snapshot root" and rewriting it, or
building the path resolution twice.

---

## 2. B045 — declare the coverage PRODUCER

**Every box ticked in `4-backlog.md`.** Evidence:

- [x] **vocabulary + loader refusals, each with a test.**
  `src/assay/vocabulary.py`: `COVERAGE_PRODUCERS_BY_FORMAT` (closed PER
  FORMAT), `COVERAGE_PRODUCER_REQUIRED_FORMATS`,
  `REFUSED_COVERAGE_PRODUCERS`, `ARC_BEARING_COVERAGE_PRODUCERS`.
  `config._load_coverage_producer` carries the four refusals in a deliberate
  ORDER (A-352): refusal-by-name is tested BEFORE catalogue membership, so a
  consumer who declared the unsound Vitest provider is told what is wrong with
  it and how to fix it rather than that the string is not in a list.
  `tests/test_config_coverage_producer.py`.
- [x] **`judgment.r1.coverage_producer` in schema/dataclass/`verify.py`;
  drift-guard v9; CHANGES.md migration notes.** Landed with the cut
  (`af14021f`), frozen in W5 (`1577fa45`), and WIRED in the same commit at
  `runner.py`'s `JudgmentR1(...)` construction — so the field is never
  declared-but-never-populated.
- [x] **Real branch arcs under `istanbul` on both committed real artifacts;
  `unavailable` for every other producer.**
  `src/assay/coverage_parsers/coverage_istanbul_json.py` (`cc4e955f`). A-355
  widened every parser's `parse` to take the producer keyword-only with **no
  default**, so a caller that forgets it raises `TypeError` at the call site
  instead of silently producing an arc-less profile.
- [x] **The type-only lexer with its fail-closed controls.**
  `src/assay/adapters/javascript.py`; scoped exactly to B045's list, and
  answering "has code" for anything it does not recognise (A-358).
- [x] **Docs updated; B038 and B040(b) marked resolved.** Both marked in
  `4-backlog.md`: B038(a) = A-356/A-357, B038(b) = A-358, B040(b) = A-353.

---

## 3. B043 — a lane-level `cwd`

- [x] **Loader accepts/refuses per the contract, with a test per refusal.**
  `config._load_lane_cwd`; `tests/test_config_lane_cwd.py` (19 nodes).
  Three checks, three distinct messages: the shared `_validate_omission_path`
  grammar (so `..`, a leading `/` and `.`/`.git` components are refused by the
  same code that refuses them in `unsafe_symlink_omissions`, not a second
  transcription); containment after symlink collapse (which the grammar
  cannot do — a symlink has no `..` in its spelling, and
  `test_a_symlink_escaping_the_project_is_refused` is the case proving the two
  checks are not redundant); and "is a directory in the invoking checkout".

  **One documented deviation from the contract's wording, recorded as
  A-368.** The contract asks for "must resolve to a tracked DIRECTORY at the
  resolved commit … at load". At load there IS no resolved commit — base
  resolution happens per run, well after `assay.toml` is read — so a loader
  claiming to check tracked-ness would be checking it against whatever `HEAD`
  happened to be, a different fact wrongly named. The check is split, and the
  split is better than the original wording: a typo is caught while a human is
  editing the file, and the realistic failure (an ignored `build/` present in
  the checkout and absent from the object store) is caught by
  `runner._execute_snapshot_unit`, the only layer that can name the commit.

- [x] **The command, every R2 candidate execution and every R3 canary run use
  the same resolved cwd, proven by a command that writes `$PWD`.**
  `tests/test_runner_lane_cwd.py` (8 nodes, real runner, real subprocesses —
  A-334). The oracle is a `/bin/sh` command appending its own `$PWD` to a log
  **outside** the snapshot; every recorded line is asserted to end in `/app`.
  Stronger than the box asks: there is **ONE join**, on the resolved
  `CommandPlan` inside `execute_plan` (A-367), so the four sites agree
  structurally rather than because four tests happen to pass. The R2 node
  carries a second, mechanical proof — its command greps `src/mod.py`, spelled
  relative to the declared cwd, so a mutant executed at the snapshot root
  could not find the file; the single generated mutant is killed by the real
  command or the test fails.

  Two negative controls, because "every line ends in `/app`" proves nothing
  without them: a lane declaring no `cwd` runs at the project root, and the
  `environment_command` probe's own log is asserted NOT to end in `/app` while
  the same lane's command log does.

- [x] **schema/dataclass/`verify.py` carry `cwd_declared`; drift-guard
  updated.** Landed with the cut; populated here at `assemble_verdict`, the
  single verdict-construction site every producer path funnels through
  (normal completion, `refuse_lane`, every CLI refusal branch).
- [x] **CONSUMERS' JS worked lane uses `cwd`; `lanes --json` exposes it.** The
  monorepo lane now declares `cwd` instead of its `bash -c "cd … && …"`
  wrapper. A new CONSUMERS section documents the key with a TABLE of what does
  **not** re-root (A-369/A-271). **The SQL example had no wrapper to retire** —
  its argv is `["scripts/schema-gate.sh"]`, already project-root-relative — so
  nothing changed there; that half of the box is vacuous rather than skipped,
  and is called out here so a reviewer does not go looking for the diff.

---

## 4. B041(b) — `isolation.link_paths`

- [x] **Rules 1–6, with a gate-runnable test per rule.**
  `config.IsolationConfig.link_paths` + `_check_link_paths` (grammar, bound,
  strict order); `isolation.SnapshotRepository._plant_link_paths`;
  `tests/test_isolation_link_paths.py` (32 nodes). Snapshot cleanliness is
  checked with a **real `git` subprocess** driven independently of assay, not
  with assay's own `_verify`.

  Planted from `_build` and **after** `_verify` (A-370): `_verify` proves the
  materialised tree is exactly the commit's content, and a link planted before
  it would make that proof false about a tree it is supposed to be true about.
  Placing it in `_build` rather than at call sites is what makes rule 3's
  "every snapshot the lane creates (R3 canary snapshots included)" structural —
  `materialize` **and** `materialize_replacement` (an R2 mutant's own entry
  point, the one a per-call-site implementation would most easily miss) both
  reach it, and both are tested.

- [x] **Rule 6, the review's #1 flagged risk, proven EMPIRICALLY.** Not
  "stdlib `rmtree` unlinks symlinks rather than recursing" — that sentence is
  true and is not evidence (A-373): it is a claim about code assay does not
  own, it would silently stop being true if `_remove_owned_tree` were
  rewritten as a manual walker, and it says nothing about the SECOND teardown
  beside it. Three nodes: a real symlink to a real directory outside the
  snapshot with a canary file inside it, and the canary's **bytes** asserted
  after teardown on the success path, on `_materialize`'s exception path (the
  best-effort `rmtree(root, ignore_errors=True)` — the teardown a consumer
  reaches when something has already gone wrong, i.e. exactly when destroying
  their `node_modules` would be least explicable), and against
  `_remove_owned_tree` directly on a hand-built tree carrying a nested link.

- [x] **`snapshot_policy.link_paths` in schema/dataclass/`verify.py`;
  drift-guard.** Landed with the cut; `_verdict_snapshot_policy` emits
  `link_paths or None` here — omitted, never empty (A-051) — with two
  end-to-end runner nodes proving both shapes.
- [x] **`assay lanes --json` exposes `link_paths`.** Real declared list;
  `inventory_schema` stays `1` (it changes when an EXISTING key's meaning
  changes, never because a key gained real values).

**Two things the contract's own rule list does not have, both added:**

- **A FIFTH refusal (A-371): a link must be covered by a COMMITTED
  `.gitignore`.** `git.dirty_paths` deliberately unions `status` with
  `ls-files --others --exclude-per-directory=.gitignore` so that
  `.git/info/exclude` cannot hide anything (A-177/A-290). An un-ignored link
  therefore surfaces as `DIRTY_TREE` **after the lane's command** — assay
  blaming the command for something the lane's own isolation table declared.
  Writing the path into the snapshot's `info/exclude` was considered and
  rejected: the `ls-files` half defeats it by design, and hiding a declared
  link from the dirt check is the wrong shape of fix for a fact the verdict
  exists to state out loud.
- **A MEASURED finding (A-372): a trailing slash breaks it.** The realistic
  consumer rule is `node_modules/`. Git treats a trailing-slash pattern as
  directory-only and does not count a SYMLINK as a directory, so the rule
  every JS project already carries leaves the link untracked. Measured with a
  real git in a scratch repo: under `app/node_modules/` both
  `git status --porcelain` and
  `git ls-files --others --exclude-per-directory=.gitignore` report
  `app/node_modules`; under `app/node_modules` both report nothing. B041(b)'s
  contract text does not anticipate this — it says only "the link is not a
  tracked path so the diff never sees it". It is now a named refusal, its own
  test, and its own paragraph in CONSUMERS.md.

---

## 5. B046 — R2 by evidence ingestion

- [x] **The REAL Stryker report is the primary fixture (A-334); synthetic
  cases cover the rest.**
  `tests/fixtures/mutation/mutation-report-json.probe-js-stryker.json` —
  StrykerJS 10.0.0 over `tests/fixtures/coverage/probe-js`, 109 mutants across
  6 files, 21 `Killed` / 19 `Survived` / 69 `NoCoverage`; recipe and pinned
  versions in `tests/fixtures/mutation/PROVENANCE.md`. Every happy-path node
  in `tests/test_runner_ingested_r2.py` is driven by it, and **each refusal
  node mutates ONE field of that same real document** rather than
  hand-building a synthetic one — including `test_a_report_from_another_
  project_root_is_refused`, which presents the real report literally
  unmodified (its own absolute `projectRoot` intact) to a lane that ran
  somewhere else.
- [x] **Parser + registry + loader keys, with the native-policy keys refused
  and reasons given.** `src/assay/mutation_parsers/{__init__,model,
  mutation_report_json}.py`; `config._load_ingested_mutation`;
  `tests/test_config_ingested_mutation.py` (20 nodes). The registry lives in
  the package rather than in `assay.mutation` because `config.py` must close
  `judge.mutation.format` against the registry's own keys (A-068) and
  `assay.mutation` imports `Lane` from `config` — a registry there would close
  a real `config -> mutation -> config` cycle (A-376). The refusal tests assert
  the message says **why** ("assay decided none of it", "mutation.*[].operator"),
  not merely that the key is disallowed: **two layers, two messages** — the
  verdict model independently forbids the same fields (A-360), and a
  `ValueError` out of a dataclass is not a diagnostic for someone editing
  `assay.toml`.
- [x] **The five `judgment.r2` fields in schema/dataclass/`verify.py`, with
  re-derivation from the payload; drift-guard v9.** Schema, dataclass and
  drift-guard landed with the cut. The `verify.py` half landed here as
  `_check_ingested_r2_agrees_with_its_payload`, and it closes a **real hole
  brief 3 flagged**: every pre-existing raw R2 check is guarded on
  `judgment.r2.operators`, which is absent by contract on an ingested
  document — so those checks SKIPPED rather than passed, and a skipped check
  is indistinguishable from a satisfied one at a green bar. Four
  re-derivations were added, plus the raw operator-language check now forks on
  `producer` in **both** directions. The mutation SCORE is re-derived by the
  existing `_check_r2_rederivation` through `judge_mutation`, which an
  ingested claim goes through unchanged (A-379) — one owner, not two that
  could disagree.
- [x] **`cli.py` registers `javascript` at `{"R1","R2"}` through the ingested
  path only.** `generate_mutation_sites` is still unconditionally
  `"UNSUPPORTED"` — which is what MAKES it the ingested path, not an
  oversight left standing. The runner selects native vs ingested by
  `judge.mutation.format`'s presence and by nothing else. The 340-374
  docstring is rewritten to say which guard now carries the
  no-native-JS-R2 guarantee, and
  `test_the_registry_does_not_open_the_NATIVE_r2_path` tests it rather than
  leaving it a claim in prose.
- [x] **CONSUMERS + DESIGN-GUIDE + B037 resolved + decisions recorded.**
  CONSUMERS §"R2 for JavaScript, by ingesting Stryker's report (B046)" —
  worked lane (a **loadable** example, verified by the docs guard), the
  `thresholds.break: null` mandate, the status-map table, what the verdict
  records, and the refusals. DESIGN-GUIDE §11 gains the ingested paragraphs
  and the explicit tier statement. B037 marked RESOLVED. Decisions A-375–A-383.

**One deviation, filed rather than fudged: `fail_under` is honoured at
`100.0` only.** See §7.

---

## 6. The three-place field registration table (the 2.4.0 lesson)

Every v9 field, in all three places it must be registered. Schema, dataclass
and `verify.py` reconstruction — a field missing from any one of them is a
refusal or a silent drop.

| field | schema (`verdict.schema.json`) | dataclass (`verdict.py`) | `verify.py` |
|---|---|---|---|
| `judgment.r1.coverage_producer` | `$defs.judgment_r1.properties` | `JudgmentR1.coverage_producer` | `_reconstruct_judgment_r1` reads it; `to_dict` emits it → registered via `_reject_unknown_keys` |
| `judgment.r2.producer` | `judgment_r2.properties`, in `required` | `JudgmentR2.producer` | `_reconstruct_judgment_r2` |
| `judgment.r2.producer_tool` | `$defs.mutation_producer_tool` (new) | `MutationProducerTool` (new) | `_reconstruct_producer_tool` (new) |
| `judgment.r2.survived_uncovered` | array of `source_position` | `JudgmentR2.survived_uncovered` | `_reconstruct_source_positions` (new) |
| `judgment.r2.discarded` | integer ≥ 0 | `JudgmentR2.discarded` | `_reconstruct_judgment_r2` |
| `judgment.r2.lines_without_candidates` | array of `source_position` | `JudgmentR2.lines_without_candidates` | `_reconstruct_source_positions` |
| `cwd_declared` | root property, **not** in the `dependentRequired` matrix (A-363) | `Verdict.cwd_declared` + `_check_cwd_declared` | `_reconstruct_verdict` reads it explicitly — `verify.py:1727-1728` at the fix-round tip |
| `snapshot_policy.link_paths` | `snapshot_policy.properties` | `SnapshotPolicy.link_paths` + `_check_link_paths` | `_reconstruct_snapshot_policy` — `verify.py:1656` at the fix-round tip; its ORDER additionally at `verify.py:397-415` |

*(Both `verify.py` citations were WRONG as first written — `:1393` and
`:1322`, which are neither of these registrations. The registrations
themselves were real and were verified independently by the reviewer; only the
line numbers pointed elsewhere. Corrected in fix round 1, and now given by
FUNCTION name as well as line, because a line number in a report goes stale
the moment anything above it moves — as these did.)*
| `mutation_operator` `stryker:` branch | 4th `oneOf` branch, a `pattern` not an `enum` | `MutantOutcome.__post_init__` consults `is_ingested_operator` first | `_check_resolved_language_owns_every_operator`, forked on `producer` |
| `$defs.source_position` | new `$def` | `SourcePosition` (new) | `_reconstruct_source_positions` |

A mechanism worth stating, because it is what makes the conditionally-emitted
producer-fork fields safe: `_reject_unknown_keys(raw, obj.to_dict(), …)`
compares against the RECONSTRUCTED OBJECT's `to_dict()` output, not a static
field list. A field is registered iff the reconstruct function READS it AND
`to_dict()` EMITS it — so a field emitted only on one branch of the fork is
automatically registered on that branch and automatically refused on the
other.

---

## 7. The W5 freeze, and the `cmp` evidence

`carve-assets/W5/verdict.schema.v9.json` is byte-identical to the shipped
`src/assay/schemas/verdict.schema.json`, and
`W5/test_acceptance_v9.py::test_shipped_schema_is_byte_identical_to_the_locked_v9_asset`
asserts exactly that on every run. The asset was created by `cp` and then
verified with `cmp` — the one operation `Write` cannot do honestly, since its
entire contract is that it is a byte-for-byte duplicate; hand-transcribing
**89,596 bytes (~87 KB)** of JSON would be strictly worse and is precisely
what the drift guard exists to catch. *(This paragraph said "77 KB"; corrected
in fix round 1 — `wc -c` reports 89,596.)* W4 was demoted to the collect-only
+ hard-cut-probe treatment W1/W2 already get, and W5 wired into
`tools/tester-unified-gate.sh` in its place (`1577fa45`).

**Fix round 1 added a seventh template and re-verified the freeze.** No
schema byte changed in the fix round — the three false "checked by the raw
verifier" descriptions were made TRUE by building the missing checks rather
than by editing the sentences (A-387) — so `cmp` still reports the shipped
schema and the locked asset byte-identical, re-run and confirmed. The corpus
gained `expected/ingested-r2-v9-template.json`, a REAL ingested verdict
generated by running the producer over the committed StrykerJS artifact:
B046's entire new branch had no frozen drift-guard coverage anywhere before
it (A-389).

`W5/MANIFEST.md` follows W4's 72-line model and records one seam no earlier
brief had: `W3/expected/dstdns-sql-r2-v6-witness.json` is **not** a frozen
generation despite its filename — it is a LIVE witness that
`gate/python/qualify_dstdns_sql.py` regenerates and compares end to end, so it
tracks the CURRENT schema and must migrate with every cut.

---

## 8. Decisions recorded

`nyxloom-trove/decisions.md`, A-351 … A-383. Summary:

| range | item | what |
|---|---|---|
| A-351–A-354 | B045 | back-recorded the first generation's four calls (per-format vocabulary; required-for-istanbul; refusal-by-name first, three distinct grounds; `go-cover` producers NOT shipped) |
| A-355–A-358 | B045/B038 | parser protocol widened uniformly; arcs key per-ARM with entry-line fallback; unrecognised `branchMap` type REFUSES; the lexer's scope and all-or-nothing rule |
| A-359–A-366 | the cut | one bump for four items, cut BEFORE the features; the `producer` fork both directions; `producer_tool` its own object; the shared `stryker:` pattern source; `cwd_declared` not lane-resolved; `coverage_producer` a bare string; `source_position` objects; `link_paths` independent of `selection` |
| A-367–A-369 | B043 | ONE join on the plan, not four; the two-layer refusal; nothing else re-roots |
| A-370–A-374 | B041(b) | plant after `_verify`, inside `_build`; the committed-`.gitignore` requirement; the measured trailing-slash trap; rule 6 proven empirically; `link_paths` defaulted and read before the fork |
| A-375–A-383 | B046 | `projectRoot`/`framework` required though upstream-optional; registry placement and the `parse` asymmetry; `Ignored` refuses; the `lines_without_candidates` approximation; **no `fail_under` fork in `judge_mutation`**; `fail_under` honoured at 100.0 only; `survived_uncovered` deduplicated to positions; the reservation; `Timeout` → `budget_exceeded` |

---

## 9. Findings filed

**B050 — an ingested R2 lane cannot declare a mutation-score floor below 100.**
Filed with evidence, unimplemented, and the reason is a WIRE gap rather than
an implementation shortcut.

`JudgmentR2`'s own docstring states the property that makes R2 auditable:
*"an independent consumer can already re-derive the R2 claim's status from
`Mutation`'s own bucket fields alone"*. `judgment.r1` carries `fail_under`;
`judgment.r2` does not, and v9 is frozen. A lane judging at 90 would emit
`PASS` beside recorded survivors with **nothing in the document explaining
it**, and `verify._check_r2_rederivation` — which reuses `judge_mutation` —
would correctly report that verdict as inconsistent. The verdict would be
un-auditable by exactly the tool that exists to audit it.

I wrote the `fail_under` fork, had it working, then removed it. Three options
were weighed (A-380): dropping the key leaves B046's acceptance unmet and the
documented worked lane unloadable; accepting-and-ignoring is inert config that
cannot fail loudly when wrong (AGENTS.md 4.2a's own test); shipping the key,
honouring its one currently-expressible value, and refusing the rest with the
reason is what landed. `mutation.mutation_pct` is implemented and tested and
is what a future `judgment.r2.fail_under` will consult, so B050's fix is a
re-wiring rather than new arithmetic. B050 names the field, the three code
changes, and its own acceptance boxes.

---

## 10. Docs disposition

| file | what changed |
|---|---|
| `docs/CONSUMERS.md` | NEW §"Run the lane somewhere other than the project root: `cwd` (B043)" — grammar, both refusals, and a TABLE of what does not re-root. NEW §"Linking a dependency closure into the snapshot: `link_paths` (B041(b))" — the trade-off, the five refusals, the trailing-slash trap, the teardown guarantee. NEW §"R2 for JavaScript, by ingesting Stryker's report (B046)" — worked (loadable) lane, `thresholds.break: null` mandate, status-map table, what the verdict records, refusals. The JS monorepo lane rewritten to use `cwd`. The JS section heading is no longer "(R1 only)". `lanes --json`'s stale "always `null`/`[]`" paragraph replaced with real descriptions of `cwd` and `link_paths`, and its `argv0` note corrected. |
| `docs/DESIGN-GUIDE.md` | §11 "Mutation is source-oriented" gains the ingested-R2 paragraphs: the R1 parallel, scope stays assay's computation, and the explicit tier statement (native vs ingested, the forbidden policy fields, `producer_tool` as declared-not-verified). |
| `CHANGES.md` | the v8→v9 migration block EXTENDED (never restarted) with B043, B041(b) incl. the trailing-slash finding, and B046 incl. the `fail_under`/B050 limitation. |
| `nyxloom-trove/4-backlog.md` | B043/B041(b)/B045/B046 acceptance boxes ticked with evidence; B037/B038/B040(b) marked RESOLVED with the closing A-ids; B050 filed. |
| `nyxloom-trove/decisions.md` | A-351 … A-383 (append-only). |
| `carve-assets/W5/MANIFEST.md` | new, on W4's model, incl. the live-witness seam. |
| `tools/tester-unified-gate.sh` | W5 wired in; W4 demoted. |

---

## 11. The registered gate

**A note on why this section names a commit that is not the tip.** The gate
refuses to run against a worktree with uncommitted changes, and it judges an
exact-OID clone of `HEAD` — so a report cannot contain the transcript of the
run that judges the report. The transcript below is from the run over
**`1a783f3e`** — this report's own commit, whose parent `d0aab6fd` is the tip
of the *implementation* (every source, test, doc, backlog and decisions change
in this wave). *(This paragraph originally said the run was over `d0aab6fd`,
contradicting the judged OID, the wheel name and the §14 table three
paragraphs below, all of which say `1a783f3e`. Corrected in fix round 1: the
wheel is the evidence, and it is named for `1a783f3e`.)*

**This section is superseded as the gate record.** It documents run 1 of the
implementation round and is kept because its phase table is still the useful
inventory of what the gate proves. The authoritative gate evidence for this
wave is now **§14**, rewritten in fix round 1 after §14 was found to record a
PASS nobody could have observed.

**Command**, run from `/workspaces/vbpub` with an absolute worktree path,
backgrounded to a log, verdict read in a separate step:

```
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-b-producer
```

**Judged OID: `1a783f3e121cb4948f8f66d4b5ef46f984caea11`.** This is not a claim
about the worktree — the gate builds its wheel from a private, no-local,
exact-OID sparse clone, and the wheel it produced is named for that OID:

```
Created wheel for assay: filename=assay-3.2.1.dev16+g1a783f3e-py3-none-any.whl
  size=452100 sha256=42deb316cdc22421c8f7e7b86bd3148864fd9bc8c5231f295266bc7e81899ae9
Successfully installed assay-3.2.1.dev16+g1a783f3e
```

**Head of transcript** (the five-wheel hash-checked offline build closure into
its own `build-venv`, then the wheel built from the committed clone):

```
Looking in links: .../assay/gate/distribution/build-wheelhouse
Processing .../setuptools-84.0.0-py3-none-any.whl   (build-requirements.txt line 1)
Processing .../wheel-0.47.0-py3-none-any.whl        (line 2)
Processing .../setuptools_scm-10.0.5-py3-none-any.whl (line 3)
Processing .../packaging-26.3-py3-none-any.whl      (line 4)
Processing .../vcs_versioning-2.2.4-py3-none-any.whl (line 5)
Installing collected packages: setuptools, packaging, wheel, vcs-versioning, setuptools-scm
Successfully installed packaging-26.3 setuptools-84.0.0 setuptools-scm-10.0.5
                      vcs-versioning-2.2.4 wheel-0.47.0
Processing /tmp/tmp.JJDjAANJvP/clone/assay
  Building wheel for assay (pyproject.toml): finished with status 'done'
```

**Every phase marker emitted, in order** — this is the part worth reading,
because it is the list of things that would have had to stay silent for a
false green:

| # | line | `ASSAY_GATE_PHASE=` | result |
|---|------|---------------------|--------|
| 1 | 22 | `wheel-installed` | 25 passed, 16 deselected in 1.69s |
| 2 | 25 | `attestation-hardened` | 13 passed, 31 deselected in 20.91s |
| 3 | 28 | `verdict-v5-accepted` | 17 passed in 1.83s |
| 4 | 31 | `lane-schema-v2-successors-verified` | — |
| 5 | 33 | `verdict-v6-v7-v8-hard-cut-verified` | hard-cut guard passed for 18 frozen templates |
| 6 | 36 | `verdict-v9-successors-verified` | **44 passed in 0.87s** |
| 7 | 40 | `judge-provenance-bound-to-the-installed-wheel` | — |
| 8 | 41 | `self-hosted-lane-passed` | `tester-unified: PASS (exit 0)` |
| 9 | 42 | `topos-qualified` | — |
| 10 | 57 | `cmru-b006a-qualified` | `ASSAY_B006A_CMRU_QUALIFIED=1` (line 56) |
| 11 | 60 | `independent-self-hosting-passed` | 7 passed in 10.86s |
| — | 61 | `ASSAY_REGISTERED_GATE_COMPLETE=1` | **literal last line of the log** |

Phase 6 is the one this wave moves: `verdict-v9-successors-verified` is the
v9 successor-template check (44 nodes at this run; 47 after fix round 1 added
the ingested template and its two guards), and it is where W5's byte-identity
assertion lives — `test_shipped_schema_is_byte_identical_to_the_locked_v9_asset`
is a node of `test_acceptance_v9.py`, which this phase runs.

Phase 5 proves the same cut did not disturb the **18 frozen v6/v7/v8**
templates: `tools/tester-unified-gate.sh:491` loops `("W1", 6), ("W2", 7),
("W4", 8)` and asserts each of their six `expected/` documents is REFUSED
under v9. *(Corrected in fix round 1: this paragraph said phase 5 covered 18
templates "including W5" and cross-referenced §9. Both were wrong — W5 is not
in that loop and could not be, since its documents are v9 and the loop asserts
refusal; and the `cmp` evidence is §7, not §9. W5 is proven by phase 6, as
stated above.)*

**Tail of transcript**, the self-hosted lane and the B006(a) WI-5 receipt:

```
tester-unified: PASS (exit 0)
  commit: 1a783f3e121cb4948f8f66d4b5ef46f984caea11
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
--- B006(a) WI-5 qualification receipt ---
input_oid=d2ad506a66d8f2a43170bce8ebf6c034d724fae3
qualification_baseline_oid=1bea2767444c4839da1b7c5d9f03e0e5869a7e59
head_oid=5e007b1d427194a80a308aabc9280e158de3f52a
outcome=PASS exit_code=0
claim[R0]=status=PASS
claim[R1]=status=PASS
claim[R2]=status=PASS
claim[R3]=status=PASS
r2_killed_identity={"description": "Eq->NotEq", "operator": "python:compare-swap", ...}
r3_canary={"control_outcome": "PASS", "transformed_outcome": "FAIL",
           "expected_reason_code": "UNCOVERED_LINES",
           "observed_reason_code": "UNCOVERED_LINES", ...}
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
.......                                                     [100%]
7 passed in 10.86s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
```

The `r2_killed_identity` line is worth one sentence for the reviewer: the
qualification receipt still carries a **native** R2 kill (`python:compare-swap`
over `cmru/src/cmru/_b006a_probe.py`), which is the evidence that adding the
ingested producer did not disturb the native one. B046 is a second path, not a
replacement — §12 lists the negative test that holds that line in `cli.py`.

**One honesty note about this run.** I launched it with `nohup … & disown` and
did not capture the shell exit code, so for run 1 I have the receipt marker as
the literal last line and the in-band `exit 0`, but not the process status. The
second run (§14) captures the exit code explicitly. A reviewer who wants a
single self-contained artifact should prefer the second run's evidence.

---

## 12. What a reviewer should push on

Ordered by how much I would want a second opinion.

1. **A-380 / B050 — `fail_under` at 100.0 only.** This is the one place the
   shipped behaviour is narrower than B046's contract text. I believe the
   re-derivation property is worth more than a partially-honoured threshold,
   and that a loud refusal naming the gap beats inert config. But this is a
   judgment call about a documented contract, and the alternative reading —
   that B046 authorised the threshold and the schema cut simply should have
   included the field — is defensible. If a reviewer takes that reading, the
   fix is a v10 field, not a patch.

2. **A-377 — an in-scope `Ignored` mutant REFUSES the lane.** B046's status
   map says `Ignored → excluded` "(counted, never laundered as a kill)", and
   v9 has nothing to count it in. I chose refusal over both alternatives, but
   the practical consequence is real: a consumer with a single
   `// Stryker disable` comment on a changed line cannot run the lane. A
   reviewer should decide whether that is correctly strict or whether B046
   intended out-of-band tolerance.

3. **A-378 — "executable" in `lines_without_candidates` is approximated** as
   "in-scope, non-blank, no mutant starts here". Assay has no per-line
   executability oracle for an ingested language and structurally cannot have
   one here. The approximation over-reports (import lines, closing braces),
   which I argue is the safe direction, but it does make the field noisier
   than its name suggests.

4. **A-360's two unendorsed extensions** (carried forward from brief 3, and
   flagged by the controller for reviewer attention rather than for me to
   re-decide). The controller endorsed option (i) — fork `judgment.r2` on
   `producer` — but did not explicitly rule on two things the implementation
   added: the native→ingested forbidding is mirrored in BOTH directions (a
   native document carrying `discarded`/`survived_uncovered` is refused, not
   only an ingested one carrying `operators`), and `equivalence_artifact`
   joined the wire's forbidden set. Both follow from the endorsed reasoning;
   neither was explicitly ruled.

5. **A-354 — `go-cover`'s `go-test`/`covdata` producers were NOT shipped.**
   Unchanged since brief 1 and still the B045 call most open to challenge. The
   alternative reading is that B045's contract text authorised shipping them
   now; the shipped reading is DESIGN-GUIDE §5's no-speculative-names rule
   applied literally — a vocabulary opens when a consumer needs it and can say
   what each name MEANS, and the Go wave (B047) is what can measure the
   difference between those two.

6. **A-371 — the committed-`.gitignore` requirement is a fifth rule B041(b)
   does not list.** I added a refusal the contract did not ask for. It
   prevents a misnamed `DIRTY_TREE`, but it is scope I took rather than scope
   I was given.

7. **The `_ingest_r2_report` call site's placement.** It runs INSIDE the
   baseline snapshot's `with` block, unlike the native `run_mutation` call
   below it, because `baseline_snapshot` is torn down the moment that block
   ends and ingestion is entirely about that snapshot's paths. The asymmetry
   is deliberate and commented, but it is the kind of thing a later edit could
   "tidy" into a bug.

---

## 13. What I did NOT do, and why

- **No `judgment.r2.fail_under`.** §9/B050. Adding it would have meant
  reopening the frozen v9 schema, which brief 3 correctly ruled out.
- **No R3 for `javascript`.** B041's own acceptance box makes the
  qualification harness a precondition, and that harness proves R1 only — no
  real canary PAIR has ever run. Registering R3 would be a capability claim
  with no producer behind it (DESIGN-GUIDE §7).
- **No `go-cover` producer vocabulary.** A-354; B047's, with the Go wave.
- **No `assay verify` re-read of the ingested report's `source` against the
  snapshot's own committed bytes.** The report embeds each file's source and
  assay derives byte offsets from it; comparing that against the snapshot's
  blobs would be a stronger non-repudiation check than B046 asks for. It is
  not implemented, and I have not filed it, because it needs a design call
  about what a MISMATCH means (a stale report? a tool that rewrote sources
  mid-run?) that I did not want to make unilaterally at the end of a wave.
  Worth a reviewer's opinion on whether to file it.
- **No `INGESTED_OPERATOR_NAMESPACES` beyond `stryker`.** The tuple and the
  regex are built to take a second namespace as a one-line edit (A-362), and
  no second producer exists to name.
- **B049 not fixed.** Out of this wave's scope; filed in Wave A with evidence.
- **The `coverage_parsers/__init__.py` DAG sentence is still stale** (it omits
  the `assay.vocabulary` edge that `coverage_istanbul_json` now has). Carried
  from brief 2 §8 through brief 3 §8 to here: low priority, real, and no
  commit in this wave touched that file.

---

## 14. Verification

**Full suite, on the implementation tip.** `pytest tests/` over the whole
worktree: **3779 passed, 13 skipped, 0 failed** in 357.84 s. This is the run
that caught the four JS-registry nodes encoding the pre-B046 "javascript is
R1-only" contract; all four were migrated and one new negative added (§5)
before this result.

**Registered gate.** `bash assay/tools/tester-unified-gate.sh
/workspaces/vbpub/.worktrees/assay-wave-b-producer`, run from
`/workspaces/vbpub`, backgrounded to a log and read in a separate step —
never a pipe tail (LESSONS L4). The verdict is the process exit code together
with `ASSAY_REGISTERED_GATE_COMPLETE=1` as the literal last line.

> ### This section was REWRITTEN in fix round 1, because it recorded a PASS
> ### that could not have been observed.
>
> As shipped, the table below had a second row reading "run 2 | the tip |
> PASS — see below". **That row was written before the run it describes could
> exist**, and there was no "below" to see: the table lives inside `a4bf1bc3`,
> which is the very commit run 2 was supposed to judge, so the text was
> committed first and the run could only have followed it. No gate log for
> either run was ever written to disk, so nothing could be re-read to check
> the claim either.
>
> That is a fabricated observation, not a bookkeeping slip, and it is the
> worst possible defect for this particular table to have — the whole purpose
> of a gate record is that it reports what was seen. The reviewer caught it.
> What follows is what was actually observed, and nothing else.

**Run 1 — judged `1a783f3e`.** Real, and the transcript is §11. What I have
for it is the receipt marker `ASSAY_REGISTERED_GATE_COMPLETE=1` as the literal
last line of the log, the in-band `tester-unified: PASS (exit 0)` from the
self-hosted lane, and the wheel name `assay-3.2.1.dev16+g1a783f3e` binding the
run to that OID. What I do **not** have is the process exit status: it was
launched with `nohup … & disown` and `$?` was never captured. That gap is
real, was disclosed at the time in §11, and is one of the things fix round 1
was sent to fix.

**Run 2 — did not happen as recorded.** Struck. See the box above.

**The wave tip `a4bf1bc3` — the reviewer's own independent run is the evidence
of record.** I did not produce a citable gate log for that commit, so rather
than assert one, this report defers to the run a fresh adversarial reviewer
performed on it directly: **exit 0**, `ASSAY_REGISTERED_GATE_COMPLETE=1` as
the literal last line, wheel `assay-3.2.1.dev17+ga4bf1bc3`. That is somebody
else's observation, reported here as somebody else's, which is the honest
form for a fact I did not witness.

**Fix round 1's own gate run is the current record** — a real run, with the
process exit code captured this time, over a tip that contains every fix.
Transcript committed at
`nyxloom-trove/reports/assay-WAVE-B-FIXROUND-gate-transcript.txt`; see §15.

**Not verified by me, deliberately:** the release. `cmru release` is the
controller's, and gate-green is not release-green (A-335) — the release
mutation gate runs the whole since-last-release diff, which is a wider surface
than any gate run here. The `!` in `af14021f` is what makes this 4.0.0; it is
the only one in the range, and I did not run the release that consumes it.

---

## 15. Fix round 1

A fresh adversarial reviewer returned **ACCEPT-conditional** on `a4bf1bc3`:
one code blocker and five must-fix items, plus three the controller folded in.
All nine are below with the commit that fixed each and its evidence.

### Commits

| # | hash | subject |
|---|---|---|
| 14 | `52b1f86b` | `fix(assay): BLOCKER -- cwd x link_paths composed into a snapshot escape` |
| 15 | `9848d5ca` | `fix(assay): the report-schema major pin admits an unmeasured major; B037 docstring is stale` |
| 16 | `4780c4ba` | `fix(assay): the raw verifier's three missing ORDER checks and its unclosed producer vocabulary; file B051` |
| 17 | *(this section + the transcript)* | `docs(assay): fix round 1 -- the rewritten gate record` |

**No new `!` commit.** The blocker fix is a `fix(assay):`, deliberately: cmru
takes a `!` anywhere in the release range literally, and `af14021f` is still
the only one. A second would not change the computed MAJOR, but it would make
§1's "exactly one" false and put a second breaking marker in front of the
controller's release for no gain.

### BLOCKER — `cwd` + `link_paths` composed into a snapshot escape (`52b1f86b`)

The two keys were individually correct. `_plant_link_paths` plants its
symlinks from `_build`, **after** `_verify` (A-370), so by the time the
commit-bound `cwd` check ran there was a live symlink at `<snapshot>/deps`
pointing at `<checkout>/deps`. That check was `run_cwd.is_dir()` — a
**filesystem** test, which follows the link and answers `True` — so an
untracked, gitignored directory was accepted as commit-bound and the lane's
command executed in the consumer's real working tree, writing to it for real.

Four changes, in the order the fix brief asked for:

1. **`runner.py` decides it from the COMMIT'S OWN TREE.**
   `isolation.Snapshot` gains `tracked_directories` (`isolation.py:206-231`),
   populated from `self._manifest.directories` at the single `Snapshot`
   construction site (`isolation.py:520`). `_execute_snapshot_unit` re-anchors
   the project-relative `cwd_declared` through `plan.project_prefix` and tests
   membership (`runner.py:1792-1832`). This is not a new mechanism: it is the
   same manifest oracle `_plant_link_paths`' own tracked-ness rule already
   consults at `isolation.py:721`, which is exactly what the review asked
   for. A manifest is derived from the tree the commit names and knows nothing
   about what is on disk, so no symlink can enter the answer.
2. **A symlink `cwd` is refused explicitly** in the same block — enforcement
   of an already-STATED contract clause that nothing was checking. Unreachable
   by construction today (a tracked directory materialises as a real
   directory, and rule 2 refuses linking a tracked path), which is precisely
   why it is cheap to keep.
3. **`config.py` refuses the declaration PAIR at load**
   (`_check_cwd_is_not_under_a_link_path`), naming both keys. Exact rather
   than merely cautious: a linked path must be UNTRACKED and a `cwd` must be
   TRACKED, and a tracked directory's ancestors are tracked, so no commit can
   satisfy both — TRACKED+LINKED was never a legitimate combination being
   given up. Component-wise, not string-prefix.
4. **The 5th `cwd` join is gone.** `_ingest_r2_report` hand-rederived
   `snapshot.project_root / lane.cwd`; it now takes the `CommandPlan` and
   calls `resolve_run_cwd`, the one join (A-367).

**The test reproduces the escape, and I verified that it does.** With the old
`is_dir()` check restored *and* the new symlink check disabled,
`test_an_untracked_cwd_reached_through_a_link_path_is_refused_not_followed`
FAILS — the command runs and `escaped.txt` lands in the checkout. With either
guard in place it passes. Three witnesses in that one node: the lane is
refused, the command's `$PWD` log does not exist, and the file the command
would have written is absent from the checkout. Two controls beside it: the
same lane without `link_paths` (still refused, same terminal, so the test is
about the PAIR) and `test_a_TRACKED_cwd_still_composes_with_a_link_path_beside_it`
— `cwd = "app"` with `link_paths = ["app/node_modules"]`, which runs, reads
the linked marker from inside the cwd, and asserts no process ran under the
consumer's checkout path. A fix that closed the escape by refusing the feature
would have been a worse bug than the one it fixed.

**`docs/CONSUMERS.md` stated a guarantee that was FALSE** — "a directory that
exists in your checkout but is not tracked at the resolved commit is genuinely
absent from the snapshot, because a snapshot holds committed objects only".
A snapshot is *not* only committed content; that is what `link_paths` is for.
Replaced with the guarantee assay actually makes, plus a sixth row in the
`link_paths` refusal table. Design recorded as **A-384** (manifest over
filesystem; why tracked+linked is not a loss) and **A-385** (the 5th join).

### MUST-FIX 2 — three schema descriptions claimed a raw check that did not exist (`4780c4ba`)

`survived_uncovered`, `lines_without_candidates` and `link_paths` each say
their array ORDER "is checked by the model and the raw verifier". The model
half was real; the raw half was not — this module's only ordering check was
`unsafe_symlink_omissions`'.

**I built the checks rather than correcting the prose** (A-387), which the
brief preferred and which I agree with: draft 2020-12 cannot express order at
all, so for these three fields the raw layer is one of only TWO witnesses, not
a redundant third. Editing the sentences would have left three v9 fields on a
single witness — the exact state three-place registration exists to prevent —
and would have done it by deleting the sentence that was telling the truth
about the design. `_positions_are_ascending` (`verify.py:309-355`) and the
`link_paths` byte-order loop inside `_check_snapshot_policy`
(`verify.py:389-416`, placed BEFORE the `selection` fork or it would be dead
under `selection = "repository"`). Each is worded differently from the model's
so a test can tell which layer fired, and each test asserts that wording —
the model refuses these documents too, so "it went red" would prove nothing.

**Consequence: no schema byte changed, so W5 needed no regeneration.** `cmp`
re-run and confirmed byte-identical, and
`test_shipped_schema_is_byte_identical_to_the_locked_v9_asset` passes inside
the gate.

**Bundled — the W5 corpus gained a real ingested document** (A-389).
`expected/ingested-r2-v9-template.json`: a real run over the committed
StrykerJS artifact, `FAIL`/`MUTANTS_SURVIVED` (the honest verdict for 19
Survived + 69 NoCoverage), carrying all five B046 fields, `producer_tool`,
`stryker:`-namespaced operators throughout, and `cwd_declared = "app"`.
Generated by running the producer, for the same reason the schema asset is
`cp`'d. Before it, B046's entire new branch had **zero** frozen drift-guard
coverage — the corpus guarded the producer fork's native half only. W5's
original "no ingested document, and that is deliberate" reasoning was sound
when written and expired the moment B046 landed; MANIFEST.md now records both
halves of that. Suite is 47 nodes, up from 44, and the gate runs all 47.

### MUST-FIX 3 — `judgment.r2.producer` had no closed raw vocabulary (`4780c4ba`)

Both raw readers did a bare `== "ingested"`. `"Ingested"`, `"INGESTED"` or
`"ingsted"` fell through to the NATIVE branch: four ingested re-derivations
skipped silently, while the native language-catalogue rule fired on the
document's `stryker:` operators — a real-looking failure about the wrong
thing. `_R2_PRODUCERS` + `_validated_r2_producer` +
`_check_r2_producer_vocabulary` (`verify.py:249-307`), enforced the way
`_SNAPSHOT_SELECTIONS` already is, and both call sites branch on the validated
value. An unrecognised producer now takes **neither** branch (A-388) — falling
back to native would be assay guessing which contract a document it does not
understand was written against. Eight new nodes, including one that asserts
the *old* wrong behaviour is gone (the native message must NOT appear).

### MUST-FIX 4 — an unmeasured schema major (`9848d5ca`)

`SUPPORTED_REPORT_SCHEMA_MAJORS` was `{"1", "2"}` under a docstring saying it
is "pinned to the major the committed real fixture carries" — and the fixture
carries `schemaVersion: "1.0"`. Now `{"1"}`. Major 2 is refused as **unproven,
not proven-defective**, the way `jest-v8` is one format over (A-386). Two
tests: major 2 refuses, and the fixture's own major is asserted to BE the
admitted set, so the pin cannot drift from its witness silently. CONSUMERS
says which major assay reads and why a later one refuses.

### MUST-FIX 5 — the gate record (this section, and §14)

§14 rewritten; see the box at its head. The fabricated run-2 row is struck and
labelled, run 1's real-but-incomplete evidence is stated with its missing exit
status named, and the wave tip's evidence is explicitly attributed to the
reviewer's own run rather than claimed.

Bundled corrections, all verified against the real files rather than
re-asserted: §11 said run 1 judged `d0aab6fd` while the wheel, receipt and §14
all said `1a783f3e` (the wheel is the evidence; corrected). §11 said phase 5
covers 18 frozen templates "including W5" — `tools/tester-unified-gate.sh:491`
loops `("W1", 6), ("W2", 7), ("W4", 8)` only, and W5 *could not* be in it
since that loop asserts REFUSAL and W5's documents are v9; W5 is proven by
phase 6, and the cross-reference is §7, not §9. §6's two `verify.py` citations
(`:1393`, `:1322`) pointed at neither registration; corrected to
`:1727-1728` and `:1656` and now given by function name too, since a bare line
number in a report goes stale the moment anything above it moves — as these
did, in this very round. §7's "77 KB" is 89,596 bytes. §1 listed 12 of 13
commits. The LOG called `test_config_coverage_producer.py` "20 tests"; it
collects 18.

### MUST-FIX 6 — stale B037 docstring (`9848d5ca`)

`adapters/javascript.py` said the native-vs-ingest fork was "deliberately left
open as B037". It is ruled and B046 implements it; `generate_mutation_sites`
staying `"UNSUPPORTED"` is now what MAKES this the ingested path. Surrounding
reasoning untouched.

### B051 filed — NOT B052

The brief asked for **B052**, citing "the same file-don't-build pattern as
B049/B050/B051". **There is no B051.** `git show main:assay/nyxloom-trove/4-backlog.md`
carries entries through B049, and this wave filed B050, so B051 is the
genuinely next free identifier. Filing at B052 would have left a permanent
phantom gap that a later reader would go looking for. Filed as **B051**, with
the substitution called out at the top of the entry itself, in the commit
message, and here — the standing rule to verify a next-free identifier against
the real file before using it is why this was caught, and it applies to
backlog IDs for the same reason it applies to A-rows.

The finding: `judgment.r2.discarded` is checked for presence and
non-negativity (`verify.py:964-974`) and nothing else — the reviewer set it to
`9999` against a 109-mutant report and the document verified clean. Not
buildable this wave, and the entry says why at length: "invalid mutants this
report LISTED" is derivable and "invalid mutants the tool ENCOUNTERED" is not,
they are different fields with the same name, and a re-derivation that checked
the easy half while looking like an audit would be worse than the honest gap.

### Explicitly NOT fixed, per the review

- **The type-only lexer reads `type (x)` as type-only.** A call to a top-level
  identifier literally named `type`, with a space before the paren. Reviewer
  called it pathological, and the surrounding evidence agrees: 26 of 27 tested
  constructs fail closed. Not chased. It is recorded here rather than silently
  left, because "known and judged not worth fixing" and "not noticed" are
  different states and only one of them is a decision.
- **No `maxLength` on the ingested operator pattern.** A 300-character
  `mutatorName` is accepted. Bounded in practice by the 100k-mutant ceiling
  (`MAX_INGESTED_MUTANTS`) and by the report size the reservation admits.
  Cosmetic; not fixed.

### Verification

**Full suite**, on the fix-round tip before the gate: **3801 passed, 13
skipped, 0 failed** in 344.14 s (the wave's own run was 3779 — this round adds
22 nodes). Backgrounded to a log and read in a separate step.

**Registered gate — run over `4780c4ba`, with the process exit code CAPTURED**
(the gap §14 discloses for run 1, and the thing this round was told to stop
repeating). Run from `/workspaces/vbpub` with the absolute worktree path,
backgrounded to a log, `$?` written to its own file, and both read in separate
steps — never a pipe tail (LESSONS L4):

```
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-b-producer
```

- **exit code: `0`** — captured, not inferred;
- **`ASSAY_REGISTERED_GATE_COMPLETE=1` is the literal last line** (line 61 of
  61);
- the gate's own clone names the judged commit in-band:
  `commit: 4780c4ba6f7259740aacea9a98d9973fdd09ecf5`;
- the wheel it built is named for that OID:
  `Successfully installed assay-3.2.1.dev20+g4780c4ba`.

The full transcript is committed verbatim at
`nyxloom-trove/reports/assay-WAVE-B-FIXROUND-gate-transcript.txt`. All eleven
phase markers, in order:

| # | line | `ASSAY_GATE_PHASE=` | result |
|---|------|---------------------|--------|
| 1 | 22 | `wheel-installed` | 25 passed, 16 deselected in 1.26s |
| 2 | 25 | `attestation-hardened` | 13 passed, 31 deselected in 20.74s |
| 3 | 28 | `verdict-v5-accepted` | 17 passed in 0.75s |
| 4 | 31 | `lane-schema-v2-successors-verified` | — |
| 5 | 33 | `verdict-v6-v7-v8-hard-cut-verified` | hard-cut guard passed for 18 frozen templates |
| 6 | 36 | `verdict-v9-successors-verified` | **47 passed in 0.92s** (44 before this round) |
| 7 | 40 | `judge-provenance-bound-to-the-installed-wheel` | — |
| 8 | 41 | `self-hosted-lane-passed` | `tester-unified: PASS (exit 0)` |
| 9 | 42 | `topos-qualified` | — |
| 10 | 57 | `cmru-b006a-qualified` | `ASSAY_B006A_CMRU_QUALIFIED=1` (line 56) |
| 11 | 60 | `independent-self-hosting-passed` | 7 passed in 10.73s |
| — | 61 | `ASSAY_REGISTERED_GATE_COMPLETE=1` | **literal last line** |

Phase 6 is the one this round moves: 47 nodes where the wave's run had 44 —
the ingested template plus its two guards, running from the installed wheel.
Phase 5 is unchanged at 18, as it must be: this round froze no new v6/v7/v8
document. The B006(a) receipt still carries a **native** R2 kill
(`python:compare-swap`) and a real R3 canary pair, which is the evidence that
none of this round's changes to the ingested path disturbed the native one.

**The honest limit of this record.** Commit 17 — this section and the
transcript file — is docs-only and is NOT itself gate-judged, for the same
structural reason §11 gives: the gate refuses a dirty worktree and judges an
exact-OID clone of `HEAD`, so no commit can contain the transcript of the run
that judges it. That regress is unavoidable, and the response to it is
disclosure, not a table row asserting a run nobody made. A reviewer can bound
it exactly: `git diff --stat 4780c4ba..HEAD` shows only files under
`nyxloom-trove/reports/` — no source, test, schema, `docs/` or backlog path.
A second gate run over the final tip is reported in the completion message
with its own captured exit code; if it disagrees with anything here, believe
the log.
