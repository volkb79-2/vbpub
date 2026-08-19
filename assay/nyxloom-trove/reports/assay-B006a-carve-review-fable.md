# Independent adversarial review — B006(a) carve (`W1-CARVE-B006a-project-scope.md` @ `d3173e61`)

Reviewer: independent (Fable), 2026-08-17. Everything below was verified against the
real tree at this revision; every command shown was actually run. Scratch work in
`/tmp/fable-b006a/`; no repo file was modified except this report.

## 1. Verdict

**READY WITH CORRECTIONS.**

- **Blocking findings: 5** (F1–F5). None invalidates the chosen shape, the P22
  mechanism, the config grammar's intent, the verdict record, or the oracle set —
  all five are scoping/coordination/spec-completeness defects with local fixes.
- **Non-blocking findings: 6** (N1–N6).

This round *converged*: the recurring defect classes of the three failed
project-boundary rounds (over-claimed property, unreachable refusals, underivable
verdict values, vacuity reopening, naming collisions, inexpressible schema claims,
cannot-fail oracles) are each addressed, and I could reproduce the design's
load-bearing measurements independently (§5 below). The blockers are the kind an
author fixes in a day, not a fourth diverging round.

## 2. Is the shape right?

**Yes.** "Full repository snapshot minus exact, commit-validated unsafe-symlink
leaves" is the right solution to the measured consumer problem, and it does not
fail any need the project-boundary design actually met — because the boundary
design never met the needs it *claimed*. Concretely:

- **It fixes both real incidents exactly.** CMRU: the three Topos `/etc/passwd`
  leaves (reproduced at `c3b00729`: `_danger/passwd_link`,
  `cgroup_escape/.../passwd_escape`, `cgroup_nonreg/.../memory.current` — the five
  `procfs/network/*/ns/net` links are relative and repo-contained, hence P22-safe)
  are omitted while `cmru.project.sample.toml` and `cmru.release.sh` stay
  materialised — the two root dependencies M8 proves are load-bearing, and which
  killed every "restrict to the project subtree" variant. dstdns: the deleted
  `etc-nginx/modules -> /usr/lib/nginx/modules` (reproduced at dstdns `c359a6b1`)
  is a mode-`120000` **leaf** regardless of its target being a directory — Git
  cannot track paths *through* a symlink, so a leaf-only rule has no
  directory-target complication, and O5 pins the exact path.
- **The vacuity door stays shut structurally, not by policy.** An omission must be
  mode `120000` at the resolved commit with a P22-unsafe target; coverage
  artifacts, sources, mutation candidates, canary and B005 targets must all be
  regular blobs; and a leaf cannot be another path's ancestor in one Git tree. So
  the exclusion mechanism *cannot* be used to hide judged content — the exact hole
  the arbitrary-`exclude` and directory-target variants had.
- **The claimed property is exactly what the substrate delivers.** Initial
  materialisation, not confinement — the full closure remains, `git show`/
  `checkout` restore remains (I reproduced M7's restore-and-stay-clean), and §2's
  non-property list says so. The boundary design failed three rounds precisely on
  this line; this design does not cross it.
- **What is genuinely given up** is the "project-scoped evidence" label and the
  `inputs` inventory. The inventory was unprovable-complete by the design's own
  measured argument (M8), and the label described a boundary the command could
  cross at will. Losing an unenforceable attestation is a gain, not a cost. The
  one real cost — a verdict reviewer must now judge the declared omission list
  itself — is small (three paths for CMRU, one for dstdns) and visible in v6.
- The backlog's "deliberately not an ignore list" sentence attacks a mechanism
  that *suppressed validation and materialised the link*. This design validates
  that the link is one P22 would refuse and then does **not** write it — a
  materially different mechanism. The contradiction with the backlog's literal
  words is real and the design correctly refuses to paper over it: A-269 is a
  hard gate (WI-0 BLOCKED without it). That is the honest structure.

## 3. Blocking findings

### F1 — WI-1 migrating `cmru/assay.toml` to lane schema v2 breaks CMRU's release gate at the moment the commit lands

**What is wrong.** WI-1 orders "All editable live lane literals migrate to v2 in
the same commit" and names `cmru/assay.toml` in its file list. But CMRU's release
gate evaluates that file with a **pinned v1-only assay**, not the candidate build:

- `cmru/cmru.toml:39` — sha256-verifies `tools/assay/assay-1.0.0.pyz`;
- `cmru/cmru.toml:41` — `"…exec /opt/tester-venv/bin/python tools/assay/assay-1.0.0.pyz run cmru --file assay.toml…"`;
- the pinned zipapp itself: extracting it yields `assay/config.py:114:
  LANE_SCHEMA_VERSION = 1` (verified: `unzip -p cmru/tools/assay/assay-1.0.0.pyz`),
  and `_load_schema_version` refuses any other value ("declares schema_version =
  2; this assay understands schema_version = 1" → `LaneConfigError`).

So the WI-1 commit turns CMRU's gate step red with `ERROR/BAD_LANE_CONFIG`, in a
monorepo with a live concurrent committer running CMRU releases, and it stays red
until an assay release + repin — which WI-6 explicitly defers as "external
adoption steps". That contradicts the design's own bar ("Each item is
independently committable and lands in this order … leaving the tree green").

**Why it blocks.** It is a guaranteed sibling-project gate breakage hidden inside
a "mechanical migration" commit — exactly the "what is MISSING from this commit"
defect class.

**Correction.** Remove `cmru/assay.toml` from WI-1's file list and amend the
migration rule to: *"All editable live lane literals **owned by assay** migrate to
v2 in the same commit. A consumer-owned lane file that is evaluated by a pinned
older assay (`cmru/assay.toml`, per `cmru/cmru.toml:39–41`) stays at
`schema_version = 1` and migrates in its own adoption step, when that consumer
repins a v2-capable release."* This is safe on the assay side — nothing in
`assay/tests`, `assay/gate`, or `assay/tools/tester-unified-gate.sh` reads
`cmru/assay.toml` (verified by grep). Add to WI-6's handoff: the CMRU adoption
step is (1) pin the released v2-capable zipapp, (2) bump `cmru/assay.toml` to v2
in the same CMRU-owned commit, (3) drop `.assay-inbox/release.json` per the
existing release-notify protocol, naming this migration in `landed`.

### F2 — WI-0 leaves the sibling wave carve actively contradicting this design, and the cross-carve ordering WI-4/WI-5 depend on is unstated

**What is wrong.** WI-0 says only "mark the old §1 project-boundary contract
superseded". But `W1-CARVE-branch-coverage-and-whole-target.md` contradicts this
carve in **four** places, three of which WI-0 does not touch:

- §6 "### isolation" (lines 917–993): a **required** top-level v6 verdict object
  `{snapshot_scope, materialisation, boundary_prefix, inputs}` — the exact
  execution-phase enum and boundary attestation A-269 withdraws;
- §7 item 1 (1a/1b/1c, lines 1090–1109): a full work plan for building the
  withdrawn boundary, including `ResolvedSnapshotBoundary` and the five preflights;
- §8 oracles O9, O15, O17, O18, O19: acceptance oracles for the withdrawn design;
- §7 item 4 names O15/O18 as part of the v6 cut's acceptance bar.

An implementer executing the wave's item 4 as written would ship **both** the
withdrawn `isolation` object and the new `snapshot_policy`. Separately, WI-4
lists `carve-assets/W1/migrate_v5_to_v6.py`, `test_acceptance_v6.py`, and
`expected/*.json` as if they exist ("already owned by wave §6") — **they do not
exist at `d3173e61`** (`carve-assets/` ends at P33; `verdict.py:142:
VERDICT_SCHEMA_VERSION = 5`). And WI-5's generated lane uses `mode`,
`require_branch`, branch-aware coverage, and B006(b)'s parent creation
(`safeio.reserve_output` still refuses a missing parent — M11 shows it), i.e.
wave items 2–4, none of which have landed. "Each item is independently
committable and lands in this order" is only true *given* an unstated cross-carve
schedule.

**Why it blocks.** Two authoritative carves in the same wave give an implementer
contradictory instructions for the same schema object, and the file lists of
WI-4/WI-5 reference planned-but-absent assets with no stated dependency.

**Correction.** Expand WI-0 to enumerate exactly what is superseded in
`W1-CARVE-branch-coverage-and-whole-target.md`, by marker note at each site (do
not rewrite history): §1 in full; §6's `### isolation` subsection (replaced by
this carve's §5 `snapshot_policy`); §7 items 1a/1b/1c and item 4's sentences
demanding the `isolation` object and O15/O18; §8 oracles O9/O15/O17/O18/O19
(replaced by this carve's O1–O7). Then add one explicit ordering paragraph to §6
of this carve: *"WI-0 and WI-1 may land now. WI-2/WI-3 may land before the wave's
v6 cut. WI-4 lands as part of the same commit as the wave's §7 item 4 (the single
v6 hard cut) — the `carve-assets/W1/**` buckets it names are created by that
item, not found in the tree today. WI-5 additionally requires the wave's item 2
(B006(b) parent creation) and item 4 (v6 + `mode`/`require_branch`) to have
landed."*

### F3 — the v5→v6 migration has no rule for `snapshot_policy`, so its third producer cannot derive a required value

**What is wrong.** §5.1 makes `snapshot_policy` **required** on every
lane-resolved v6 verdict whose `declared_rigor` contains R1/R2/R3, and the schema
`if` enforces it. WI-4's file list includes `migrate_v5_to_v6.py` and the
`expected/*.json` buckets, and the fixture corpus contains R1/R2/R3 verdicts —
but neither §5 nor WI-4 states what the typed migration *inserts*. The migration
is a producer of v6 documents exactly like `assemble_verdict`; §5.2's
producer-proof table covers the runtime producers and skips this one. That is
defect class 3 (a required field a named producer cannot derive) in the one
producer the design forgot.

**Why it blocks.** Without the rule, the v6 cut either fails on every migrated
R1+ fixture or the implementer invents a value.

**Correction.** Add to §5.3 and WI-4: *"`migrate_v5_to_v6.py` inserts
`"snapshot_policy": {"selection": "repository"}` into every v5 document whose
`declared_rigor` contains R1, R2, or R3 — truthful because every v5 producer
materialised the complete repository; no v5 build had an omission capability —
and inserts nothing for an R0-only document. A v5 input that already contains a
`snapshot_policy` key is refused as not-a-v5-document rather than merged."* Add
one migration test for each of the three cases.

### F4 — the omission-path grammar in §3.2 double-specifies redundant checks; as written, one refusal branch is guaranteed dead code

**What is wrong.** §3.2 requires each spelling to "contain no empty, `.`, `..`,
`.git`, backslash, or NUL component, **and** equal `PurePosixPath(raw).as_posix()`
byte-for-byte", with `IsolationConfig.__post_init__` "enforcing the closed
selection/list/path grammar" and §3.5 promising "direct-constructor differential
tests cover each branch". These two clauses cannot both be live branches:

- If the component-split checks run first, the equality refusal can never fire: a
  `/`-separated raw whose components are all non-empty and not `.`/`..` is
  already `as_posix`-canonical (`PurePosixPath` collapses only `//`, `./`, and
  trailing `/`, all of which the component checks refuse first; it *preserves*
  `..`, `.git`, `\`, NUL).
- If the equality check runs first, the "no empty component" and "no `.`
  component" refusals can never fire (`a//b`, `a/./b`, `./a`, `a/` all fail
  equality already).

In a project that forbids `pragma: no cover` and gates 100% line+branch coverage
on new code, one of the listed refusals is unimplementable as a distinct branch —
the previous design's headline defect class, recurring.

**Why it blocks.** The config work item cannot be implemented as specified and
pass the project's own gate.

**Correction.** Specify one mechanism and demote the other to a proven theorem.
Proposed: *"`__post_init__` implements exactly: the value is a `str`; non-empty;
does not start with `/`; UTF-8-encodes to at most 4096 bytes; and splitting on
`/` yields components none of which is `''`, `.`, `..`, or `.git`, and none of
which contains `\` or NUL. The clause 'equals `PurePosixPath(raw).as_posix()`
byte-for-byte' is a **derived property, not a code branch**: the accept-side test
asserts it for every accepted path in the matrix, which is what makes 'assay
refuses rather than normalises' checkable without a second unreachable
refusal."* Update §3.5 row 1's "non-canonical path" wording to name the
component-split refusals.

### F5 — WI-5's frozen premise that the (repaired) CMRU suite is green under the registered gate's conditions is inferred, not measured

**What is wrong.** M14's own run shows **three more failures** beyond the
network-dependent one: `test_agent_controller.py::TestConsulBackend::*` erroring
with `PermissionError: [Errno 1] Operation not permitted` on local socket
creation. The design disposes of them in one clause — "cockpit-specific" — which
is an inference from the errno, not a measurement: no run of the CMRU suite
inside `tester-unified` (the environment WI-5 freezes `INPUT_REVISION` for, under
`--network=none`, which permits loopback but whose seccomp/capability profile for
socket creation was never probed) is recorded anywhere in §9. The design's own
standard elsewhere is "measured, not preferred". If those three nodes also fail
in `tester-unified`, WI-5's R0 unit fails `FAIL/COMMAND_FAILED`, the harness
never emits its marker, and the carve's final proof cannot land green — after the
one-line repair, the frozen OIDs, and the harness are already built around the
contrary assumption.

**Why it blocks.** The whole qualification (O6/O7) rests on it, and discovering
it late means rebuilding the frozen harness inputs, not tweaking a test.

**Correction.** Two parts. (1) Before WI-5's harness is frozen, run once inside
`tester-unified` (the same `docker run --network=none … tester-unified:local`
entry the gate uses): the repaired suite at `c3b00729` +
`client.update_release = lambda *args, **kwargs: {"id": 7}` via
`PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q` from `cmru/`;
record the pass in the WI-5 audit/log as M20. (2) If any environment-caused
failure appears, extend the qualification-baseline repair rule the same way as
the network stub — explicit, differential, byte-compared — rather than
deselecting; and either way, give the harness a diagnostic that names the failing
node when the R0 unit fails, so an environmental red is attributable in one read.

## 4. Non-blocking findings

### N1 — A-269's wording should name the key replacement and moot A-268(a)

A-269 withdraws "A-266's required `snapshot_scope = "project"` …" but never says
what happens to the `snapshot_scope` **key** (A-266 also ruled its
`"repository"` value) or to A-268's amendment (a), which ruled the TOML key
`boundary_prefix` — now moot with the prefix itself withdrawn. **Correction:**
append to A-269: *"The `snapshot_scope` key itself is replaced by
`snapshot_selection`, closed to `"repository" |
"repository-minus-unsafe-symlinks"`; A-268's amendment (a) (`boundary_prefix`
naming) is moot with the prefix's withdrawal."*

### N2 — M13's migration-surface measurement missed shipped v1 surfaces outside its search scope

M13 searched only `assay/gate assay/tests assay/nyxloom-trove/carve-assets`.
Also carrying `schema_version = 1`: `assay/templates/consumer-assay.toml:4` (the
copy-me consumer template — after the hard cut, copying it produces a file the
shipped assay refuses), `assay/README.md:118`, and `assay/docs/DESIGN-GUIDE.md:855`.
**Correction:** add `assay/templates/consumer-assay.toml` to WI-1's migration
list (it is functional guidance, untested but shipped), and `assay/README.md` +
the DESIGN-GUIDE example to WI-6's documentation sweep.

### N3 — §3.3.4 and §3.3.6 describe the omission filter twice; pick one representation

"The leaf is removed from the worktree manifest" (§3.3.4) and "writes every
manifest entry except those exact symlink leaves" (§3.3.6) — if entries are
removed from `_Manifest.entries`, the write-time "except" filter is a second,
unreachable filter (and vice versa). **Correction:** state one representation:
*"`_build_manifest` returns entries **without** the omitted leaves plus a new
frozen `_Manifest.omitted: tuple[PurePosixPath, ...]`; `_build`'s skip-worktree
step and `_verify`'s expected-`S`/absence sets read `omitted`; `_write_worktree`
receives already-filtered entries and applies no filter of its own."*

### N4 — the WI-2/WI-3 → WI-4 window produces v5 verdicts from omission-mode runs with no policy record

Between WI-3 and the v6 cut, an omission-mode lane runs but its verdict (still
v5) records nothing about omissions — indistinguishable from full-repository
evidence, the exact ambiguity B006.5 exists to remove. It is currently mitigated
only implicitly (no live lane declares omission mode; no release happens
mid-sequence). **Correction:** add one sentence to WI-3: *"Until WI-4 lands, no
live checked-in lane may declare `repository-minus-unsafe-symlinks`; omission
mode exists only in test fixtures, and no release is cut between WI-1 and WI-4"*
— making the mitigation a stated rule the wave controller can check.

### N5 — O6/O7 invocation and placement precision

O6 invokes `PYTHONPATH= /opt/tester-venv/bin/python gate/python/qualify_cmru_b006a.py
--source-repo ..` — relative paths and a different interpreter than the
established pattern (`"$scratch/run-venv/bin/python"
"$worktree/assay/gate/python/qualify_topos.py" --source-repo "$worktree"`,
gate script ~:291). O7 presents three lines "in this order" without saying they
are non-adjacent (`run_independent_witness` and the outer receipt sit between
lines 2 and 3), and never says where the new phase slots. **Correction:** in O6
use the qualify_topos spelling (`"$scratch/run-venv/bin/python"` + absolute
`"$worktree"` paths); in O7 state *"in this relative order, with the existing
phase lines between them"* and name the insertion point: after
`ASSAY_GATE_PHASE=topos-qualified`, before `run_independent_witness`.

### N6 — WI-1's audit rule can mislabel a legitimate version-coupled extra red

"Any extra red is a regression to fix" — if a tenth frozen node turns out to be
lane-v1-coupled through a path the AST inventory missed, it is not a regression,
and "fixing" it would mean editing frozen assets. **Correction:** reword to:
*"any extra red must be dispositioned in the audit log: either fixed (a real
regression) or shown to be lane-v1-coupled, added to the deselection list, and
given its own named v2 successor test — never silently deselected."*

## 5. What was verified and found SOUND

Do not churn any of this.

- **M1/M3 exact.** `c3b00729` resolves; `HEAD:cmru` = `6fbb3c2c…`, `HEAD:topos`
  = `31b88ee2…`; exactly three tracked symlinks target `/etc/passwd` at the three
  declared paths; the five `procfs/network/*/ns/net` links are relative and
  repo-contained (P22-safe by the classifier's own lexical rule).
- **M5/M6/M7 mechanics reproduced independently** in `/tmp/fable-b006a/repo`:
  `git update-index --skip-worktree -z --stdin` succeeds with the worktree file
  already absent; `git ls-files -v -z` emits `TAG SP PATH NUL` with uppercase
  `S`; `status --porcelain=v1` empty; `write-tree == HEAD^{tree}`; and after an
  `update-index --cacheinfo` replacement on a sibling regular file, `write-tree`
  preserves both omitted `120000` entries and the ordinary sibling while the
  skip bits survive. The uppercase-`S` spelling — the prior round's cannot-fail
  oracle — is correct this time.
- **M10 exact** against read-only `/workspaces/dstdns` (`c359a6b1`, one deletion,
  target `/usr/lib/nginx/modules`). The leaf-omission rule handles the
  directory-target case with no special rule (a `120000` leaf cannot have tracked
  descendants).
- **M16 reproduced**, plus extra adversarial rows (`.git`, `..`, `.`, `a/..`
  refused; `.gitmodules`, `a b/c d` accepted): `PATTERN_MATRIX=PASS`. The
  JSON-encoded pattern decodes and behaves as claimed.
- **M17/M18/M19 reproduced.** The P26/P33 AST inventory matches exactly (7 + 5
  nodes; the gate currently deselects 3 of the 7 P26 nodes plus the marker test,
  so the 4+5 = 9 addition is right); `Lane(` direct constructors are exactly
  `tests/conftest.py` + `tests/test_canary_python_pipeline.py`; `SnapshotSpec(`
  editable constructors are exactly `runner.py`/`conftest.py`/`test_isolation.py`
  with the two frozen P22/P23 assets not run by the registered gate.
- **§5.2's producer proof holds.** `Verdict` is constructed at exactly one
  producer site (`runner.py:817`, inside `assemble_verdict`; `verify.py:1090` is
  the independent reconstruction); `cli.py:355` and `:385` both hold a resolved
  `Lane` and route through `refuse_lane` → `_refuse_lane_with_plan` →
  `assemble_verdict`; every early-refusal path (dirty/HEAD, snapshot `AssayError`
  → `refuse_all` at `runner.py:1786/1792`, cleanup replacement) ends at
  `assemble_verdict` with the lane in hand. `snapshot_policy` is derivable at
  every producer with no phase inference and no module constant — the previous
  design's `materialisation` defect is genuinely gone, not renamed.
- **The refusal table's code split is derivable at each named site.**
  `LaneConfigError` is `ERROR/BAD_LANE_CONFIG` by construction (`errors.py:241`);
  isolation's existing refusals are `GIT_FAILED`/`SNAPSHOT_LIMIT_EXCEEDED`; a
  `BAD_LANE_CONFIG` `AssayError` raised from `_build_manifest` reaches the normal
  whole-lane refusal artifact through the existing `except AssayError`.
- **No leak-back path for R2/R3.** Replacement children `read-tree` the base
  commit, so the omitted symlink entries persist in every child tree (measured);
  R1/R2 diffs are tree-to-tree (worktree never consulted); mutation targets pass
  adapter source-globs/test-path gates and `read_regular_file` rejects non-regular
  modes (`isolation.py:358`); canary targets must be regular source. The post-run
  checks (`runner.py:1120/:1122`) are skip-worktree-clean, and a command-side
  restore is correctly *allowed* by the stated property, not redescribed.
- **The coverage-artifact story checks out end to end.** Repo-top `.gitignore`
  carries `.assay/` (and `coverage.json`), so the qualification artifact is
  ignored by `git.dirty_paths`'s porcelain+`--exclude-per-directory` union; the
  B006(b) collision check is honestly labelled public-API defence-in-depth with
  its reachability route (direct `Lane`) named, and M11's `reserve_output`
  refusal for a symlinked parent reproduces the design's division of labour.
- **Naming is clean.** `snapshot_selection`/`unsafe_symlink_omissions`/
  `IsolationConfig`/`SnapshotPolicy`/`snapshot_policy` collide with nothing in
  `assay/src` (grepped); `scope`, `judge.mode`, and both existing
  `project_prefix` meanings are left untouched, and every path in the design
  states its spelling (repo-top vs project-relative) per A-145, with
  `is_relative_to` on `PurePosixPath`, not string prefixes.
- **WI-5's arithmetic and trap-avoidance are right.** `python:compare-swap`
  yields exactly one site for one `==` (`adapters/python.py:535` — one site per
  chain operator; `Eq→NotEq`), so the probe gives `candidate_count == total == 1`
  and both asserts kill the mutant; the controlled head touches `cmru/src`, so
  `NO_MUTANTS`/exit-5 cannot occur; the live `cmru/assay.toml` stays R0, so no
  permanent gate rolls on non-source commits (M9's basis holds); omitting
  `--cov-fail-under` makes the R3 transformed half fail as `UNCOVERED_LINES`
  through the judge, not `COMMAND_FAILED` — consistent with
  `run_isolated_canary`'s seed→child diff scope.
- **The pinning pattern is not a maintenance trap.** `qualify_topos.py`'s
  `verify_pinned_inputs` requires the frozen OIDs to be *reachable*, not to be
  `HEAD` — later commits to `cmru/`/`topos/` cannot red the gate.
- **The "fourth unsafe symlink" behaviour is right**: undeclared → existing
  `GIT_FAILED`, omission lanes red until an owner reviews and declares the exact
  path; never auto-broadened.
- **Closure headroom for eventual real-repo adoption**: 29,193 reachable objects
  at `HEAD` versus `max_objects = 100_000`.
- **Every oracle states command + exact marker + the observable that differs when
  the feature is absent or broken.** The `&& printf` marker pattern preserves
  pytest's status and gives a byte-exact success observable.

## 6. What the requirement demands that the design does not cover

Judged against backlog §B006 points 1–6 and its oracle list:

- **Point 1 (project scope + inputs) — withdrawn, legitimately.** The reason is
  stated (§4 items 1–2, M8: the inventory cannot be proven complete; the boundary
  buys no reachability), and the withdrawal is gated on A-269 rather than
  smuggled. Not a silent drop — but it is a *requirement change*, and the backlog
  prose itself must say so: WI-6 lists `4-backlog.md`, which is late — the B006(a)
  status line should be updated in **WI-0** alongside A-269, so the requirement
  and the ledger never disagree mid-implementation. (Fold into F2's WI-0 edit.)
- **Point 3's "must prove … the command cannot read a sibling worktree" — never
  deliverable** (full closure + stock Git; already narrowed by A-268). The design
  disclaims it explicitly in §2 and in the schema `description`. Legitimate
  re-scope, stated.
- **Point 4 (five containment preflights) — dropped as impossible
  intersections**, with a per-item impossibility argument (§3.4) and the one
  *possible* intersection (coverage artifact vs omitted leaf) retained. This is
  the correct inversion of the unreachable-branch defect. Legitimate.
- **Point 5 (attest scope/prefix/inputs) — met in modified form**
  (`snapshot_policy` with exact omissions). A reviewer can still distinguish
  full-repository evidence from narrowed evidence; they can no longer read a
  "project" label that was never enforceable. Legitimate, and §8 says so.
- **The backlog oracle "external target is never materialised or readable"** —
  "readable" is not met (object closure), never was, and §2/§5.3 disclaim it in
  the artifact itself. Stated, not silent.
- **"Release as a versioned artifact and pin it in CMRU"** — correctly external
  (§8), but under-specified as a handoff: F1's correction makes the CMRU
  repin + `cmru/assay.toml` v2 migration + release-notify an explicit, ordered
  adoption list in WI-6.
- **Genuinely uncovered by any work item:** nothing else found. B006(b) is
  correctly left as specified elsewhere, with only the collision seam added here.

## 7. Shape reconsidered

Written on the operator's explicit license to propose a better solution. §2 above
was a reviewer's answer; this is the engineer's answer, after arguing the other
side as hard as I can. F1–F5 stand regardless of this section.

### 7.1 The case against "repository minus declared unsafe symlink leaves"

**A1 — It is a manually maintained, ownership-inverted allowlist that scales as
N consumers × M unsafe paths.** CMRU's lane file hard-codes three paths inside
`topos/`, a tree CMRU does not own. Every future higher-rigor lane in every
project must carry the same three lines. The failure modes are worse than the
maintenance: when Topos *adds* a fixture, every omission lane in the repo goes
`ERROR/GIT_FAILED`; when Topos *moves or renames* one, every omission lane goes
`ERROR/BAD_LANE_CONFIG` (declared path absent at the commit). In both cases the
people who must act (every consumer team) are not the person who caused the
change (the Topos author), and the author gets **no signal at all** — Topos's own
lanes are R0 and unaffected. That is a cross-team friction generator with two
distinct red shapes, and it grows with someone else's repo forever.

**A2 — It is arguably `allow_escaping_symlinks` re-labelled**, and the backlog
withdrew that by name: "an ignore list only hides a path from validation without
proving the executed command cannot reach it." The new design also cannot prove
the command cannot reach the content (`git show HEAD:<path>` still works, M7's
restore still works), so — the attack goes — it has the withdrawn design's
weakness with an extra layer of ceremony.

**A3 — It ships no project-scope attestation.** Backlog point 5 asked, in its own
numbered words, for "scope mode, full commit, project prefix, and canonical
expanded input set in the verdict … so reviewers can distinguish full-repository
from project-scoped evidence." A verdict saying "full repository minus three
named links" is not that; a reviewer wanting "CMRU's evidence is about CMRU"
still has to reason about 3,185 files themselves.

**A4 — It fixes the symptom, not the coupling.** The real disease is that P22's
*whole-tree structural walk* couples every higher-rigor lane to every byte of
monorepo structure. This design deliberately keeps that (§2: "It does not hide
the rest of the commit from P22's structural walk"), so the day someone vendors a
submodule (gitlink, `isolation.py:955`), commits a non-UTF-8 filename (`:966`),
or the repo outgrows `max_objects`, every higher-rigor lane in every project reds
again — and the omission grammar, closed to symlink leaves, can express no
answer. The withdrawn boundary design attacked the coupling itself (A-268's walk
does not descend into out-of-scope subtrees); this design treats one symptom
class and leaves the disease.

### 7.2 The alternative shapes, designed far enough to be judged fairly

**B1 — Derive the omission set automatically from the commit** (omit every
P22-unsafe leaf; record the exact set in the verdict). Zero maintenance; new
fixtures never red anyone. It fails on silent evidence broadening, and the
failure is concrete, not theoretical: replace a quietly-dead source module with a
tracked symlink to `/etc/passwd` in one commit. The leaf is auto-omitted; the
changed-lines diff shows only deletions (nothing to cover); the suite still
passes; the verdict is `PASS` with a fourth path in a list nobody pre-approved.
Under the carve's design that same commit is `ERROR/GIT_FAILED` until a human
declares the path. The pre-commitment is a real tripwire, not ceremony.

**B2 — Auto-derive plus a pre-declared count** (`expected_omission_count = 3`).
Fail-closed on growth, but a *move* keeps the count at 3: rename
`_danger/passwd_link` to any other path and the lane stays green while the
evidence changes without review. A guard that misses renames of the exact thing
it guards is not a guard.

**B3 — Auto-derive plus a pre-declared digest** (sha256 over the sorted path
list). This catches renames — because it *is* the path list, hashed. It changes
exactly when the explicit list would change, so maintenance is identical, while
the lane file becomes unreviewable (a reviewer sees a hash and must recompute to
know what evidence is being narrowed). Strictly worse than the explicit list.

**B4 — Omit by validated class** (`omit_unsafe_symlink_classes = ["absolute",
…]`). A class declaration is auto-derivation with a filter: B1's source-swap
scenario passes unchanged (the new link is in the declared class). Same rejection.

**B5 — A repository-level policy file, owned by the repo** — the serious
contender, so designed concretely: a tracked repo-top
`.assay-repository.toml` with `[unsafe_symlinks] omissions = [...]`, read **from
the resolved commit** by `isolation.py` (commit content, not ambient state);
validated in both directions (an entry that is not a P22-unsafe `120000` leaf →
`BAD_LANE_CONFIG`; an unsafe leaf with no entry → `GIT_FAILED` naming it); lanes
say only `snapshot_selection = "repository-minus-declared-unsafe-symlinks"`.
This genuinely fixes A1's ownership inversion: the policy entry rides the same
commit, same author, same review as the fixture that needs it, and consumers
carry one enum value instead of M paths. It still keeps the tripwire (an
undeclared link reds; the declaration is in the diff that adds the link).

Why it loses **today**: (a) **it reintroduces defect class 3.** The omission set
becomes commit content, unknowable until a snapshot reads the commit — but the
early producer paths (`env_required` refusal, dirty tree, HEAD drift, attestation
timeout at `cli.py:355`, adapter refusal at `:385`) all emit complete verdicts
*before any commit object is read*, and `config.py` imports no git, so load time
cannot supply it either. `snapshot_policy.unsafe_symlink_omissions` would be
derivable on some producer paths and not others — a phase-dependent attestation,
the exact shape (`materialisation`) that failed three consecutive reviews. The
per-lane list is derivable at every producer *because it is lane config*; that is
the quiet load-bearing virtue of the current shape. (b) It adds a second,
discovered configuration surface adjacent to what A-266/A-267 deliberately closed
("no ambient discovery"; no inference from file location). (c) It weakens the
two-party property: today every broadening of a consumer's evidence requires an
edit to that consumer's own lane file, reviewed by that consumer's owner. (d) The
maintenance it saves is, at this revision, three lines in one file (M = 3, N = 1,
and unsafe fixtures change at the rate of Topos security-test authorship — M3
shows all three are years-stable `/etc/passwd` fixtures).

**B6 — The project boundary, done differently** (e.g., materialise the project
subtree plus repo-top regular files automatically, solving CMRU without
`inputs`). Any fixed heuristic is ambient scoping — the thing A-266/A-267
forbade by ruling — and the first consumer whose test dependency lives one
directory deeper resurrects the `inputs` inventory, which resurrects
completeness-that-cannot-be-proven, the five containment preflights, and the
attestation machinery that diverged 8 → 9 → 11 blocking findings. I looked for a
fourth boundary variant that dodges those and did not find one; every variant
either declares dependencies (unprovable-complete) or discovers them (forbidden).

### 7.3 Ruling

**Keep the carve's shape.** Having argued the other side:

- **A2 loses on mechanism.** Suppress-and-materialise gives the command a live
  link to `/etc/passwd` and silently host-dependent measurements; validate-and-
  omit gives it a loud absence. The backlog's stated reason attacked the former.
  These are different failure modes, and the difference is the whole point.
- **A3 loses on honesty.** Point 5's substance — a versioned attestation a
  reviewer can distinguish — is met by `snapshot_policy`. Its letter attested a
  "project scope" whose enforceable content was nil (closure + restore). An
  attestation stronger than the mechanism is the defect this program has now
  paid for three times.
- **A4 lands as fact, not as an error.** The general coupling fix *is* the
  boundary, and it failed on its own weight, three times. The remaining classes
  have different evidence semantics (an omitted gitlink hides a whole subtree of
  real content — no leaf rule covers it), so extending eligibility per class
  under a new ruling (§8 already says this) is governance, not procrastination.
- **A1 is the one that partially lands** — the ownership inversion is real. But
  every zero-maintenance alternative (B1/B2/B4) reopens silent broadening, B3 is
  the list minus readability, and B5 trades three stable lines for a
  phase-dependent attestation. At N = 1, M = 3, the explicit per-lane list wins
  on evidence quality and loses only friction that can be bought back cheaply
  **inside** the current shape. Two recommendations (additive; not revisions to
  F1–F5):

  **R1 (WI-2, product):** in omission mode, the `GIT_FAILED` diagnostic for an
  *undeclared* unsafe link must name the feature and the exact declarable
  spelling — e.g. `symlink topos/.../new_link targets the absolute path
  '/etc/passwd'; if this is a deliberate fixture, an omission lane may declare
  exactly "topos/.../new_link" in unsafe_symlink_omissions` — so the cross-team
  fix is a one-line reviewed edit, not an investigation.

  **R2 (WI-6 handoff, non-normative, monorepo-side):** recommend an author-side
  inventory test in the monorepo itself (a Topos-owned test asserting the exact
  set of tracked unsafe symlink leaves), so the tripwire fires first at the gate
  of whoever adds the fourth link, with the consumer lane edits as visible
  follow-ups. This repairs the ownership inversion without adding any assay
  product surface.

  And one sentence for the ledger: record in A-269 (or §8) that **B5 is the
  known escalation path** if this monorepo ever grows enough higher-rigor
  consumers for N×M to hurt — added as a *new* `snapshot_selection` enum value
  under its own ruling — together with its known trap (the omission set becomes
  commit content and is underivable on early producer paths), so the future
  designer starts where this analysis ended instead of rediscovering defect
  class 3.
