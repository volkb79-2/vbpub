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
| 12 | *(this report)* | `docs(assay): the Wave B report` |

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
| `cwd_declared` | root property, **not** in the `dependentRequired` matrix (A-363) | `Verdict.cwd_declared` + `_check_cwd_declared` | `verify.py:1393` reads it explicitly |
| `snapshot_policy.link_paths` | `snapshot_policy.properties` | `SnapshotPolicy.link_paths` + `_check_link_paths` | `verify.py:1322` |
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
77 KB of JSON would be strictly worse and is precisely what the drift guard
exists to catch. W4 was demoted to the collect-only + hard-cut-probe treatment
W1/W2 already get, and W5 wired into `tools/tester-unified-gate.sh` in its
place (`1577fa45`).

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
`d0aab6fd`, the tip of the *implementation*: every source, test, doc, backlog
and decisions change in this wave. This report's own commit follows it, is
docs-only, and was gate-verified in a second run afterwards — see §14.

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
v9 successor-template check, and 44 nodes pass over the schema cut B045 made.
Phase 5 proves the same cut did not disturb the 18 frozen v6/v7/v8 templates —
including W5, the drift-guard asset §9 shows is byte-identical.

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

It was run **twice**, because the gate refuses a dirty worktree and judges an
exact-OID clone of `HEAD`:

| run | judged OID | what that commit is | result |
|-----|-----------|---------------------|--------|
| 1 | `1a783f3e` | this report, before §11 carried a transcript | PASS — 11 phases + receipt, full transcript in §11 |
| 2 | the tip | §11 and §14 filled in; **docs-only**, one file | PASS — see below |

Run 2's own transcript cannot live in the file it judges. What can be checked
and is worth checking: run 2 differs from run 1 by exactly one Markdown file
under `nyxloom-trove/reports/`, no source, test, schema, or `docs/` path — a
reviewer can confirm that with `git diff --stat 1a783f3e..HEAD` and see a
single `.md` changed. Both runs' receipts are reported in the completion
message; if the two disagree with each other or with this table, believe the
log, not this paragraph.

**Not verified by me, deliberately:** the release. `cmru release` is the
controller's, and gate-green is not release-green (A-335) — the release
mutation gate runs the whole since-last-release diff, which is a wider surface
than any gate run here. The `!` in `af14021f` is what makes this 4.0.0; it is
the only one in the range, and I did not run the release that consumes it.
