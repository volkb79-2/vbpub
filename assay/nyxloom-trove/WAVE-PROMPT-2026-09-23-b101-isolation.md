# Wave prompt — B101 isolation wave: shallow snapshot seed, dirty-tree override, liveness side files (2026-09-23)

**Audience:** a third-party controller agent taking this wave end to end —
carve → implement → adversarial review → authoritative gate → merge →
release → deploy → notify. Written by the controller session that held the
operator design interview on 2026-09-23. Everything decided is stated as
decided; everything left open is named as open, with who decides.

**Scope, in order:** P0 (B104 triage) → P2 (B101 shallow seed + limits,
with its own user docs) → P3 (B102 dirty-tree override + B093 liveness side
files, with their own user docs) → P4 (B082/B083/B084 docs only) → release.
P1 is already DONE. Do not fold in any other backlog entry without asking
the operator.

**Glossary** (assay-internal terms used below):
- **R0–R3** — rigor levels of a lane: R0 = the command's exit code;
  R1 = coverage judged; R2 = mutation testing; R3 = canary (a deliberate
  defect must be caught).
- **P22 snapshot** — the private per-run git repository assay materializes
  for R1+ lanes (named after the package that introduced it).
- **`whole_target` / `changed_lines`** — `judge.mode`: judge declared
  target files whole, or only lines added since a base commit.
- **Frozen generations** — the committed expected-verdict fixtures under
  `assay/nyxloom-trove/carve-assets/W*/` that the gate replays per verdict
  schema version.
- **RG-55** — a separate, long-running run-gate/cgroup-profiler program run
  by another agent on this host.
- **Release receipts** — the attestation/verification records consumers
  bind to a verdict.

---

## 0. Why this wave exists and why it is urgent

Assay runs every R1+ lane inside a private P22 snapshot. Today the snapshot
seed carries — and the limits budget — the **full reachable git history** of
the judged commit, counted as UNCOMPRESSED bytes. That quantity only ever
grows. Measured 2026-09-23:

| repo | commits | objects (limit 100,000) | uncompressed (limit 1024 MiB) | full pack | judged-tree pack |
|---|---|---|---|---|---|
| dstdns `f1b179be` | 5,105 | 44,007 | **1084 MiB — already over** | 38 MiB | 16 MiB |
| vbpub `main` | 6,109 | 52,852 | **873 MiB** (+15–20 MiB/day) | 39 MiB | 21 MiB |

- dstdns (`assay.toml` has 116 lanes, 115 with `snapshot_selection =
  "repository"`; 16 `changed_lines`, the rest `whole_target`): they refuse
  `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED`; it has been working around
  assay with raw `docker exec` test runs, which throws away assay's
  coverage/mutation verdict. Two append-heavy tracked ledgers
  (`decisions.md`, `CONTROLLER-BRIEF.md`) are 60% of its total.
- vbpub: expected to cross within about a week of 2026-09-23. After that,
  every vbpub R1+ lane (assay's own gate, run-gate, cgroup-profiler
  mutation campaigns) refuses. **If P2 cannot land in time, ship P2's
  `[isolation.limits]` part alone first as an escape hatch** (section 3.4).

Separately, R1+ lanes refuse `NO_MEASUREMENT/DIRTY_TREE` if ANY file in the
repository is uncommitted (`runner.py`, pre-snapshot `git.dirty_paths(repo)`,
~line 5132 on `7a3c0571`), although the snapshot is the committed tree
and no uncommitted byte can reach it. A controller editing its own ledger
blocks every lane. That is B102.

## 1. Read first (in this order)

1. `/workspaces/vbpub/AGENTS.md` — estate policies, worktree protocol,
   shared-main committing (`git commit --only -- <paths>`), defaults-are-
   hazards. Binding.
2. `/workspaces/vbpub/CLAUDE.md` and `~/.claude/CLAUDE.md` — model routing,
   checkpoint/successor-brief rule, commit trailers.
3. `assay/nyxloom-trove/4-backlog.md`: `## B101` (whole section, including
   "Operator design interview held", "vbpub itself is close to the wall",
   "Wave plan"), `## B102`, `## B093`, `## B104`, `## B082`, `## B083`,
   `## B084`. **The backlog sections are the requirements; this prompt
   sequences them and rules the forks.**
4. `assay/nyxloom-trove/reports/assay-B101-SHALLOW-SEED-AND-B096-TRIAGE-2026-09-23.md`
   — what a snapshot is, why it must be a git repo, full vs shallow, the
   measurements, and why P1 was needed.
5. `assay/nyxloom-trove/decisions.md` rows **A-184, A-185** (seed design,
   full-closure rationale — A-185 is being relaxed), **A-177** (why
   `dirty_paths` must not honor `.git/info/exclude`), **A-366/A-370/A-384**
   (`link_paths`, planted symlinks, manifest-not-filesystem rule).
6. Code: `assay/src/assay/isolation.py` (whole file — `prepare_snapshot`,
   `_closure_oids`, `_object_metadata`, `_enforce_object_limits`,
   `SnapshotRepository._build/_verify/_enforce_child_closure`,
   `_copy_objects`, `_build_manifest`), `runner.py` (the pre-snapshot
   block that builds `SnapshotSpec`, `_resolve_declared_base`,
   `_run_prepared_lane`), `git.py` (`dirty_paths`, `resolve_base`,
   `_p22_*`), `measurability.py`, `canary.py`, `config.py`
   (`IsolationConfig`, `_load_isolation_for_lane`), `verdict.py`
   (`VERDICT_SCHEMA_VERSION = 11`), `verify.py`, `attestation.py`,
   `liveness.py`.
   Also `cli.py`'s `plan` command (~1605), which builds the SECOND
   `SnapshotSpec` in the codebase, and `cli.py:474` → `config.load_lane_file`
   (the lane file is read from the WORKING TREE).
7. Process references: `/workspaces/vbpub/AGENTS.md` "docs sync is
   MANDATORY" (~lines 191-235, A-270) and "Consuming assay from inside
   vbpub" (~155-167); `assay/nyxloom-trove/nyxloom.toml` (`[gates]`,
   `[refs]`); `nyxloom/reference/AUTHORING.md` (carve doc shape);
   `assay/docs/INTERNAL-CONSUMERS.md`.
8. Precedent prompts: `WAVE-PROMPT-2026-09-08-b070-discarded-mutants.md`
   (how a verdict-schema bump — v10→v11 — was carried through verify,
   frozen generations and the gate), `WAVE-PROMPT-2026-09-08-b074-b077-quickwins.md`.

## 2. Already done — do not redo

- **P1 (merged `e785b955`, 2026-09-23):** nothing inside a snapshot
  re-resolves the base any more. `measurability.check_resolved_base_is_head`
  consumes the OID resolved against the consumer repository BEFORE the
  snapshot; threaded through `evaluate_r1`, the R2 target diff, both R3
  canary halves, and `assay plan`. History-cut regressions live in
  `tests/conftest.py::cut_snapshot_history` and
  `tests/test_runner_run_lane{,_r2,_r3}.py`. Gate PASS on `260055ee`.
- Backlog audited and operator-triaged (`e0a8e6c2`, `96ac8197`).

## 3. P2 — B101 shallow seed (the core package)

### 3.1 Decided (operator, 2026-09-23 — do not re-litigate)

1. **Default seed = the judged commit, plus the pre-snapshot resolved base
   commit iff `runner._resolve_declared_base` returned an OID** (~`runner.py:5179`;
   the base is always HEAD's merge-base or first parent, i.e. an ancestor).
   Nothing else.
   Both are recorded as shallow boundaries in the seed's `shallow` file, so
   git treats absent parents as an intentional cut, not corruption.
2. **Full history becomes a per-lane opt-in**, spelled
   `snapshot_history = "full"` inside the lane's `[isolation]` table
   (values `"shallow"` default, `"full"`). Unknown value → `BAD_LANE_CONFIG`
   at load.
3. **Limits count what the seed actually contains and what materialization
   writes**, not history: object count and total bytes over the seed's own
   object set; keep `max_pack_bytes` as the transfer bound; add an explicit
   ceiling on the judged tree's total blob bytes (today bounded only
   incidentally because history was always larger).
4. **`SnapshotLimits` is configurable** via a project-level
   `[isolation.limits]` table in `assay.toml`, per field,
   `DEFAULT_SNAPSHOT_LIMITS` as fallback, validated by the dataclass's own
   `__post_init__` (which raises `ValueError` — the loader must map it to
   `BAD_LANE_CONFIG`); an unknown key or non-positive value →
   `BAD_LANE_CONFIG`. The carve defines key spelling, units (bytes), and
   how the new judged-tree-bytes field relates to
   `max_total_object_bytes`. Whether it may also appear per lane is the
   carver's call (default: project level only — say why if otherwise).
   Note `load_lane_file` refuses unknown top-level keys
   (`config.py` ~1320): **rule at carve whether `LANE_SCHEMA_VERSION` stays
   2 (additive keys; an older assay fails closed on them) or bumps** — a
   consumer pinned to an older zipapp (dstdns) must re-vendor before using
   any new key.
6. **Verdict schema in P2 (decide, then fold into the §4.2 question):** the
   verdict's `snapshot_policy` object is closed (`additionalProperties:
   false`, `schemas/verdict.schema.json` `$defs/snapshot_policy`; adding
   `link_paths` to it bumped the schema to v9). Should the verdict record
   `snapshot_history` (and the effective limits)? If yes, that is a schema
   bump in P2 — sequence it with P3's so the schema bumps ONCE, not twice,
   including across the §3.4 split.
5. A-185 is relaxed, not abandoned: the **source** repository must still be
   a complete, non-shallow, non-promisor, alternates-free SHA-1 repository
   (its refusals stay exactly as they are — B083 documents that). Only the
   **seed** becomes deliberately shallow, with boundaries assay chooses.

### 3.2 Why this is safe (the argument the carve must preserve)

The materialized snapshot is a detached HEAD with **no refs and no tags**
(`isolation.py` writes `HEAD` as a bare OID), so tag-based versioning
(`git describe`, setuptools-scm) already cannot work inside a snapshot;
full history bought only ancestor walks. After P1, assay itself no longer
walks ancestry inside a snapshot for base handling.

**What a shallow seed DOES break, so audit before release:** any lane
command that walks history (`git log`, `rev-list --count`), names an old
commit directly (`git show <old>:path`, `git archive <pinned-oid>`,
`merge-base --is-ancestor`), or runs a nested assay against the snapshot
(a shallow SOURCE is refused, `git.py` ~1628). Audit every vbpub and dstdns
R1+ lane argv for these patterns and give such lanes
`snapshot_history = "full"`. Oracle: every vbpub R1+ lane runs green once
under the shallow default.

### 3.3 Known seams the implementation must handle (from code reading + the P1 review)

- **Two `SnapshotSpec` construction sites:** `runner.py` (~5191) and
  `cli.py`'s `plan` (~1605). Both must honor `[isolation.limits]` and
  `snapshot_history`, or the §3.4 escape hatch makes `run` pass while
  `plan` still refuses. `plan`'s base diff runs on the consumer repo
  (`cli.py` ~1654-1665), so it does not need the base seeded.
- **Source inventory:** replace `rev-list --objects <commit>` with an exact
  set: `rev-list --objects --no-object-names --no-walk <commit> [<base>]`
  (commit objects + their full trees/blobs, no parents; the source has full
  history, so `--no-walk` is required there). Record the boundary commits.
- **Seed transfer:** `pack-objects --stdout` of exactly those OIDs (no
  `--revs`, as today) into the private seed, then write `shallow` in the
  seed git dir listing the boundary commits BEFORE any git command walks
  the seed.
- **Seed re-verification** (`prepare_snapshot` re-derives the closure from
  the seed and compares with the source inventory): on the SEED side use
  the WALKING form `rev-list --objects --no-object-names <commit> [<base>]`
  under the seed's `shallow` file and require set equality. Do NOT use
  `--no-walk` on the seed side: measured 2026-09-23, `--no-walk` returns
  the same set even when the `shallow` file is MISSING, so it would prove
  nothing; the walking form fails (`rc=128 "Failed to traverse parents"`)
  without the file and matches exactly with it — that is what proves the
  boundary file is exact.
- **`_copy_objects` copies only `objects/pack`.** Every materialization is
  its own `.git`; the `shallow` file must be written into EACH
  materialization's git dir too, before `_verify` and before
  `_enforce_child_closure`. **`_verify` today runs no ancestry walk**, so a
  baseline materialization missing `shallow` would pass every assay check
  and only break the consumer's own `git log`: `_verify` must additionally
  assert `<git_dir>/shallow` equals exactly the expected boundary set
  (absent under `"full"`) and run a walking `rev-list --count HEAD`, for
  every materialization.
- **`SnapshotRepository._enforce_child_closure` (`isolation.py` ~943) runs
  `rev-list --objects <child>` inside every replacement snapshot** (R2
  mutants, R3 transformed half). The replacement child is created with
  `commit-tree -p <spec.commit>`; with `spec.commit` a shallow boundary the
  walk stops there (verified 2026-09-23 in a scratch repo: the child
  closure is child + boundary objects only; `status` clean, `diff base
  HEAD` works, `fsck --connectivity-only` passes with the file present) —
  make the child-closure proof still prove what it proves today (no object
  outside the seed + the child's own new objects).
- Transfer detail already favorable: `pack-objects --stdout` (no `--revs`)
  into `index-pack --stdin` without `--strict`, so writing `shallow` after
  indexing is safe; commit-graph/MIDX are disabled and
  `GIT_NO_LAZY_FETCH=1` is set for this trust boundary (`git.py`
  ~1099-1106).
- **Merge HEAD:** HEAD with two parents becomes a shallow root, so its
  parents are invisible in the snapshot. P1 already moved the first-parent
  decision before the snapshot; confirm no other snapshot-side code asks
  about HEAD's parents (`grep -n "parents\|rev-list\|merge-base\|log"` over
  `src/assay/`, classify each call as consumer-repo vs snapshot).
- **`git diff <base> HEAD` inside the snapshot** needs both commits' trees —
  present by construction when the base is a boundary.
- **Lanes that need history** opt into `"full"`; the full path must stay
  exactly today's behavior (same inventory, same checks), now budgeted
  against the configurable limits.
- **Test helper:** P1's `cut_snapshot_history` patches `_verify`, which
  runs AFTER `_enforce_child_closure`, so it never exercised the closure
  walk. P2 needs a **real** shallow-seed oracle (the seed itself is shallow),
  not the patch. Keep `cut_snapshot_history`'s tests passing or replace them
  with the real thing and say so.

### 3.4 Split for the urgent escape hatch

If P2's full carve cannot merge before vbpub crosses the limit (re-measure:
`nice -n19 ionice -c3 git rev-list --objects --no-object-names main |
nice -n19 git cat-file --batch-check='%(objectsize)' | awk '{s+=$1} END
{print s/1048576}'` — 875.8 MiB / 52,911 objects at `7a3c0571`),
land `[isolation.limits]` (decision 4) first as its own reviewed, gated
merge — **with its README/DESIGN-GUIDE/CONSUMERS docs in the same merge**
(AGENTS.md docs-sync rule) and both `SnapshotSpec` sites threaded — then
add `[isolation.limits]` with a commented reason to **each vbpub project
`assay.toml` that has R1+ lanes** (there is no repo-root `assay.toml`;
list them with `git ls-files '*assay.toml'` and check each for R1+ rigor).
Do not raise `DEFAULT_SNAPSHOT_LIMITS` in code as a shortcut.

### 3.5 P2 oracles (acceptance)

1. **Fixture** (note: B101's own sketch — appending ~5 MB per commit — does
   NOT work: after ~20 commits the single blob exceeds `max_blob_bytes`
   = 64 MiB and still refuses under a shallow seed): ~210 commits, each
   **rewriting** one tracked file of ~5 MiB distinct but highly
   compressible content (unique header + repeated body), built with
   `git fast-import` under `nice -n19`. History exceeds 1 GiB uncompressed
   while the final tree stays under every per-blob and tree ceiling. It
   **PASSES under default limits with no override** (shallow default).
2. The same fixture with `snapshot_history = "full"` **FAILS** under
   defaults (`SNAPSHOT_LIMIT_EXCEEDED`, regression guard for the old
   accounting) and **PASSES** with a raised `[isolation.limits]`.
3. A many-small-commits fixture exceeding 100,000 history objects (~33k
   commits; build with `git fast-import` under `nice -n19`) PASSES by
   default (`max_objects` wall gone).
4. A `changed_lines` lane whose symbolic base is several commits behind
   HEAD yields the **identical added-line set, `judgment.resolved.base`, and
   `base_resolution`** as full history (compare against a `"full"` run of
   the same lane in the same test).
5. A merge-HEAD lane records the first parent and `base_resolution =
   "first-parent"` under the shallow seed.
6. R2 (mutants killed/survived identical to a `"full"` run on the same
   fixture) and R3 (canary control PASS, transform FAIL with the expected
   reason) under the shallow seed — this exercises `_enforce_child_closure`.
7. The seed on disk really is shallow: `shallow` lists exactly {commit} or
   {commit, base}; `git rev-list --count HEAD` = 1 in every baseline
   materialization and = 2 (child + boundary) in every R2/R3 replacement.
8. A source that is itself shallow/promisor/alternates-backed is still
   refused exactly as today.
9. Each new `[isolation.limits]` key: accepted, applied (a limit set below a
   fixture's size refuses), and malformed values refused at load.
10. Prove the oracles can fail, meaningfully: oracles 1, 3 and 4 red on
    `main` before the change. Oracles 2 and 6 cannot be meaningfully red on
    `main` (the new keys are simply unknown there → `BAD_LANE_CONFIG`):
    for 6 and 7, prove red against the P2 build with the per-materialization
    `shallow` write removed (`_enforce_child_closure` / the new `_verify`
    assertion must then fail); for 2, prove the raised limit is what turns
    it green.
11. Both `SnapshotSpec` sites: `assay plan` on the same fixture honors the
    limits and history mode exactly like `assay run`.

### 3.6 Records

Append a decisions row superseding A-185's full-closure clause and amending
A-186 ("every **fixed** limit" — limits become configurable) (append-only;
never edit A-185/A-186). **ID hazard:** `main`'s `decisions.md` ends at A-448, but
the unmerged P35 branches (`assay-b099-p35-repair` et al., owned by the
RG-55 continuation) already use A-449/A-450. **Use A-451 onward**, and note
why in the row. New backlog entries, if any: B105 onward (B103 is reserved
for P35).

## 4. P3 — B102 dirty-tree override + B093 liveness side files

### 4.1 Decided (operator, 2026-09-23)

1. **Declared exclude list** — project-level
   `[isolation] dirty_ignore = ["<posix glob>", ...]` in `assay.toml`:
   uncommitted changes matching it do not refuse a snapshot lane. **Reuse
   B092's `judge.mutation.identity_exclude` glob grammar and normalization**
   (`config.py` ~3581-3625, repo-root-relative, `fnmatchcase`); no second
   glob dialect. **Rule at carve whether `dirty_ignore` globs are
   repo-top-relative or project-relative** — `git.dirty_paths` returns
   repo-top-relative paths; dstdns is identical either way, vbpub (one
   `assay.toml` per project directory) is not. Never read the list from
   local unversioned config (A-177's reasoning: `.git/info/exclude` is
   exactly what must NOT hide changes).
   **Hazard — the lane file is read from the WORKING TREE, not the commit**
   (`cli.py:474` → `config.load_lane_file`, `read_bytes()`); today it matches
   the commit only because `DIRTY_TREE` guarantees a clean tree. With
   `dirty_ignore`/`--allow-dirty`, an uncommitted `assay.toml` edit would
   silently set the rules (including `dirty_ignore` itself) for a verdict
   labelled with commit X. Therefore: **a dirty lane file (and any file it
   loads) must refuse `DIRTY_TREE` even when matched by `dirty_ignore` and
   even under `--allow-dirty`, unless the carve switches lane loading to
   the committed blob.** Decide which and record it.
2. **Escape hatch `--allow-dirty`** on `assay run` (assay's subcommands
   are `analyze`, `lanes`, `run`, `plan`, `verify`; decide whether `plan`
   needs it for parity): the run proceeds despite dirty paths outside the
   exclude list. **Name clash to resolve at carve:** `run-gate.py` already
   has its OWN `--allow-dirty` (its `clean_tree` refusal,
   `run-gate-project/run_gate.py` ~8471). Decide and document whether
   run-gate forwards its flag to assay's, keeps them independent, or
   renames one — an operator who types `run-gate … --allow-dirty` must not
   silently get a different override than they think. Status quo default:
   run-gate documents its flag as an INDEPENDENT layer
   (`run_gate.py` ~8305-8307); keep that unless there is a reason, and
   document it in both tools. A run-gate change is a run-gate-project change
   (its own backlog/CHANGES/gate), not assay's.
3. **Both are recorded in the verdict:** the ignored dirty paths (from
   `dirty_ignore`) and, separately, the overridden dirty paths (from
   `--allow-dirty`) with an explicit marker. **`assay verify` and attestation
   consumers can refuse an `--allow-dirty` verdict** — attestation/release
   receipts refuse it by default; `assay verify` reports it explicitly
   (decide at carve whether `verify` refuses by default or needs a flag to
   accept; record the ruling).
4. **Scope: snapshot (R1+) lanes.** In-place lanes also run a post-command
   dirty check to catch lane-written files; extending the override there
   needs a pre/post dirty-set comparison. Carve decides in-scope vs a
   follow-up entry (B105+) — say which and why.
5. **Out of scope:** judging the uncommitted working tree itself (synthetic
   commit). Verdict identity stays bound to a real commit.

### 4.2 Verdict schema — a decision point you must raise, not make

A new verdict field (P3's marker, and possibly P2's `snapshot_history`
per §3.1(6)) almost certainly means **verdict schema v11 → v12** — once for
the whole wave,
i.e. a MAJOR assay release with BREAKING migration notes, a new frozen
generation for the gate's `verdict-v*-successors-verified` phase, and
`assay verify` accepting v12 (and whatever policy for v11). Follow B070's v10→v11 precedent exactly.
**Before building, ask the operator one question:** the operator deferred
**B079** "until a verdict schema bump" — should B079 (split
`judgment.r2.discarded` into CompileError vs RuntimeError) ride the same v12
bump? Do not fold it in without an explicit yes. If a schema-free design for
the marker exists that still lets verify/attestation refuse reliably,
present it as an alternative in the same question.

### 4.3 B093 — liveness side files

Liveness writes its plugin and per-candidate event/stdout/stderr files into
the **consumer's LIVE checkout** at `project_root/.assay/liveness/`
(`runner.py` ~3559/3611/3617; `liveness.py` ~1070 calls it "PERSISTENT") —
not into the snapshot — with no ignore guard or cleanup (see `## B093`: a
load-time refusal/WARN consistent with `--progress`, plus cleanup after
`tests_completed` is read). So the check they trip is the **NEXT run's**
pre-snapshot `dirty_paths` refusal (`runner.py` ~5132) or the R0 direct
check (~5963) — not the in-snapshot post-command check (~3020). It joined
this wave because that is exactly the surface B102 governs. **Rule at
carve: WARN + cleanup/relocate vs load-time refuse** — note a refusal
contradicts the §4.4 oracle as written (an unignored consumer gets no
`DIRTY_TREE`); pick one and make the oracle match. Cleanup must not destroy
evidence the verdict or `--rejudge` still needs.

### 4.4 P3 oracles

- dirty `ledger.md` matched by `dirty_ignore` → lane runs, verdict lists the
  ignored path; dirty `src/x.py` unmatched, no flag → `DIRTY_TREE`
  (unchanged); same with `--allow-dirty` → lane runs, verdict carries the
  override marker; `assay verify` / attestation behave per the ruling in
  4.1(3).
- A snapshot-lane verdict's judged content is byte-identical with and
  without the dirty file present (proves the snapshot really ignores it).
- A `.git/info/exclude` entry never hides a dirty path from the refusal
  (A-177 regression).
- A `dirty_ignore` glob that matches the lane file itself, and the same
  under `--allow-dirty`: an uncommitted `assay.toml` edit still refuses
  `DIRTY_TREE` (per the §4.1(1) hazard), unless lane loading was switched to
  the committed blob — then prove the verdict uses the committed rules.
- B093 (per the carve's WARN-vs-refuse ruling): after an R2 run, a
  consumer without `.assay/` in `.gitignore` has no liveness residue that
  makes the NEXT run refuse, and the side files are cleaned up (or kept
  where evidence requires it — state which).

## 5. P0 — B104 triage (do this first, it is cheap)

`tests/test_gate_qualify_dstdns_sql.py::test_capture_witness_end_to_end_matches_the_frozen_witness`
fails on unmodified `main` (`QualificationError: normalized verdict differs
from the frozen witness … dstdns-sql-r2-v6-witness.json`, 28.55 s). It needs
Docker + a real `/workspaces/dstdns` checkout, so it only runs where both
exist — it silently joined an ordinary serial `tests/` run on this host.
Determine: stale v6 witness vs verdict schema v11 (B070), dstdns checkout not
at the pinned commit, or a real regression. Then fix or reclassify, and make
sure this Docker-reaching test cannot run by accident in a plain local
suite (skip unless explicitly enabled). Confirm whether the release gate
depends on it. Record the outcome in `## B104`.

## 6. P4 — docs

- **B082:** CONSUMERS.md — the lane's own `assay.toml` must be tracked (or
  gitignored), including for a disposable Go module root; relate to
  `dirty_ignore`.
- **B083:** CONSUMERS.md Go gotchas — a shallow/grafted SOURCE clone is
  refused (unchanged), while the seed assay builds is now deliberately
  shallow; `snapshot_history = "full"` for lanes whose commands walk history.
- **B084:** the `golang:1.25` wording (measured example, not a floor) and the
  stale consumer-pin table (CONSUMERS.md still says `assay-4.0.0.pyz`;
  dstdns pins `assay-6.4.0.pyz` as of the audit — re-check at write time).
- **Not P4:** README / DESIGN-GUIDE / CONSUMERS (and
  `docs/INTERNAL-CONSUMERS.md` where vbpub-internal use changes) for
  `[isolation.limits]` and `snapshot_history` land **inside P2** (including
  the §3.4 split merge); for `dirty_ignore`, `--allow-dirty` and the
  verdict marker **inside P3**. AGENTS.md makes this MANDATORY (a public
  config key/vocabulary change is incomplete until the docs are in sync,
  in the work itself, A-270); each package's file list names its docs.
- `CHANGES.md` `[Unreleased]` entries per package, in the file's style.

## 7. Process — how to run it

### 7.1 Worktrees and branches
- One worktree per package under `/workspaces/vbpub/.worktrees/<branch>`,
  branched from current `main`; merge serially `--no-ff`. Suggested
  branches: `assay-b104-witness-triage`, `assay-b101-p2-shallow-seed`,
  `assay-b102-p3-dirty-override`, `assay-b101-p4-docs`. P3 depends on P2 only
  where they share `isolation.py`/`config.py` — sequence P3 after P2 merges
  unless you carve a clean split.
- Direct commits in `/workspaces/vbpub` itself only with
  `git commit --only -- <paths>`. Merges onto `main` ARE done there with
  `git merge --no-ff -F <msgfile> <branch>` (check `git status` first —
  `git merge` aborts if the shared index has staged changes, which may be
  another agent's; never reset them). Never bare `git stash`.
- **Clean up after yourself:** remove each worktree and delete its branch
  once merged; if a branch is abandoned, first carry any evidence worth
  keeping into a merged commit (the `assay-b096` precedent: product commit
  ported, review records preserved, then the branch was deleted).
- Edit files with the Edit tool / apply_patch, never sed or write_text
  rewrite scripts.

### 7.2 Roles (vbpub doctrine)
- You (controller): strongest available model, long-lived; you write the
  carve for P2 and P3 (a short design doc in
  `assay/nyxloom-trove/handoffs/` or the backlog section, your choice) and
  keep a controller log at
  `assay/nyxloom-trove/reports/assay-WAVE-B101-CONTROLLER-LOG.md`.
- Implementers: fresh sessions; Opus for P2 and P3 (design judgment),
  Sonnet acceptable for P0/P4.
- Reviewers: **fresh Opus sessions, never a fork of the implementer or
  controller**, 3-round cap per package, adversarial; every merged change
  gets one — docs included (size changes how heavy, never whether).
- Checkpoint rule for every long-running agent: arm at ~120k context or ~60
  tool calls, cut at the next green boundary with a continuation brief
  committed to the worktree; successors are fresh agents seeded with that
  brief (never a resume/fork for this).
- A sub-agent that spawns its own sub-agents can leave a worktree's index
  in a reverted state (observed 2026-09-23): check `git status` of the
  worktree after every agent returns, before trusting its tip.

### 7.3 Host rules (this host also runs a production game server)
- pytest serially under `nice -n19 ionice -c3`, `PYTHONPATH=src`, from
  `assay/`; no `-n auto`. **Until P0 gates it, add
  `--deselect tests/test_gate_qualify_dstdns_sql.py`** — on this host that
  file starts uncapped `ThrowawayPostgres` containers (~lines 654-689)
  whenever Docker and a dstdns checkout exist; running it on purpose
  counts as a gate container.
- **Gate containers:** normally one at a time. **Operator ruling
  2026-09-23 (given in the design session; recorded in this prompt and in
  B101's "Wave plan"):** a short (~10 min) assay `tester-unified` gate MAY run
  concurrently with the RG-55 continuation's long mutation campaigns,
  capped with `docker update --cpus=3 <container>` right after launch. The
  release mutation campaign (7.5) is NOT short — schedule it with the RG-55
  controller.
- Read gate verdicts from a log file in a separate step (never a pipe
  tail); see 7.4 for the exact pass criteria.
- Docker cleanup: remove containers by exact name only (never
  `ancestor=` filters — lane/gate/probe containers share images).

### 7.4 Gate (per package, before merge)
The gate budget is 60 min — longer than an agent's foreground tool limit —
so run it detached:
```
cd /workspaces/vbpub/.worktrees/<branch>/assay
setsid nohup bash -c '{ nice -n10 python3 run-gate.py tester-unified --worktree /workspaces/vbpub/.worktrees/<branch>; echo GATE_EXIT=$?; } > <log> 2>&1' &
# then cap the container named on the log's ASSAY_GATE_CONTAINER= line:
docker update --cpus=3 <that exact name>
```
PASS requires, read from the log in a separate step: `GATE_EXIT=0`,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, and all 12 `ASSAY_GATE_PHASE=` markers:
`wheel-installed`, `attestation-hardened`, `verdict-v5-accepted`,
`lane-schema-v2-successors-verified`,
`verdict-v6-v7-v8-v9-v10-hard-cut-verified`,
`verdict-v11-successors-verified`,
`judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
`topos-qualified`, `cmru-b006a-qualified`,
`independent-self-hosting-passed`, `pyflakes-clean` (a schema or
lane-schema bump adds/renames phases per B070's precedent — update this
list in the controller log). A bare `pytest tests/` pass is NOT
gate-verified.

### 7.5 Release, deploy, notify ("shipped" = all of these)
- Standing authorization: merge, push and `cmru release` in vbpub without
  asking, once review + real gate are green.
- Release: `cmru release assay` from `/workspaces/vbpub` (project is
  POSITIONAL — there is no `--project` flag; check `cmru release --help`).
  cmru derives the version from conventional commits (`assay/cmru.toml`:
  `strategy = "scm"`, `bump = "conventional"`): run `--dry-run` first to
  read the planned version; pass `--major` for a v12 schema bump; never
  `--resume` without re-checking the version (the nyxloom v1.0.0
  incident). cmru is at `/home/vscode/.venv/bin/cmru`; if it cannot parse
  the current config, bootstrap it with `cmru/build-initial-standalone.sh`
  + `pip install --no-deps --force-reinstall` of the built wheel. Push main
  to origin FIRST — the release
  snapshots origin/main; pass `--allow-uncommitted` only for files you did
  not create; run it `setsid nohup bash -c '{ cmru release …; echo
  CMRU_RELEASE_EXIT=$?; } > log 2>&1' &` and read the exit marker, never a
  harness task status; after it, `git merge --ff-only origin/main` by hand).
  The release gate runs a mutation campaign over the whole diff since the
  last `assay-v*` tag — hours; coordinate the host window with RG-55.
- Version: MAJOR if the verdict schema bumps (v12), else MINOR. **There is
  no cmru assay pin to bump:** cmru and every in-repo consumer use assay
  from worktree source (AGENTS.md "Consuming assay from inside vbpub";
  `cmru/cmru.toml` ~19-21 says assay is intentionally not an S15 pin;
  `cmru/assay.toml`'s `schema_version = 2` is the LANE-file schema, not
  the verdict schema). Out-of-repo pins (dstdns `run-gate.toml`
  `assay_command = tools/assay/assay-6.4.0.pyz`) are updated by that
  consumer after the notify.
- Deploy into this devcontainer: `/home/vscode/.venv/bin/python -m pip
  install --upgrade <released assay wheel>`; verify `pip show assay` and
  `/home/vscode/.venv/bin/assay --version` report the released version.
  (At `7a3c0571`, `pip show assay` reported a DEV build
  `6.5.1.dev237+g7a3c0571` installed from `file:///workspaces/vbpub/assay`
  — not a released wheel. Coordinate with the RG-55 controller so exactly
  one of you installs the release.)
- Notify dstdns: write `/workspaces/dstdns/.assay-inbox/release.json` per
  that directory's tracked `CONTRACT.md` (read it at the time; `sha256` from
  the release's own `.sha256` sidecar). Notes must say: the snapshot limit
  wall is gone (shallow default), dstdns can drop its `docker exec`
  bypasses, and — only AFTER re-vendoring the new zipapp (an older pinned
  assay refuses the unknown key) — should add
  `dirty_ignore = ["nyxloom-trove/**"]` (or its own ledger paths) to its
  `assay.toml`; plus full migration notes if v12.
- Clear `CHANGES.md` `[Unreleased]` after the release if cmru's fold-in
  left duplicates (a recurring gap).

## 8. Coordination with the RG-55 continuation (another agent)

- vbpub lanes install assay **from the judged worktree's own source**
  (`run-gate-project/run_gate.py`, `assay_source_setup`), so merging to
  `main` never disturbs a running campaign; a campaign picks up new assay
  only when its tree is reconciled onto a newer `main`. Tell the RG-55
  controller when P2 merges (its P6 reconcile may then judge with the
  shallow seed).
- B103 (P35 interruption design, unmerged branches) belongs to RG-55; do not
  touch those branches.
- One installer for the devcontainer assay wheel (7.5).

## 9. Definition of done

- P0: B104 resolved or reclassified, recorded; the Docker test cannot run by
  accident.
- P2, P3, P4 each: carved, implemented, adversarially reviewed (findings
  fixed or explicitly ruled), gate PASS on the merged tip's tree, merged
  `--no-ff`, pushed.
- Backlog: B101, B102, B093, B104, B082, B083, B084 carry DONE status lines
  with commit/version evidence; decisions rows appended (A-451+).
- Released (`assay-vX.Y.Z` tag, wheel + zipapp + manifest), deployed into
  the devcontainer, dstdns notified.
- Re-measure and record: a dstdns-shaped lane and a vbpub lane run under
  default limits with no override; snapshot prep time before/after on one
  real lane (optional but valuable).
- All wave worktrees and branches removed; controller log closed with a
  final summary; RG-55 controller told the release version.

## 10. Stop and ask the operator when

- the v12 schema / B079 question (4.2) — always ask;
- any oracle in 3.5/4.4 cannot be met without weakening a refusal that
  exists today;
- a review round 3 still has a BLOCKING finding;
- the release campaign would collide with a running RG-55 campaign and the
  RG-55 controller has not agreed a window.
