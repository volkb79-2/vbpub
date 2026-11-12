<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-10-30; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: TRUENAS-MANIFEST-OPUS-2026-10-30.md -->
<!-- relation: TrueNAS patch manifest -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# TrueNAS patch manifest: second opinion (Opus, 2026-10-30)

Read-only review of `<worktree>/scripts/truenas` (paths below are relative to it).
"GUESS" marks claims I did not verify.

## Verdict

The direction is right: hand-written metadata plus machine-derived hunks, with generated docs and a test that fails on drift.
I would change five parts of the proposal.
The operator has said the proposal is not final, so each change is marked **DEVIATION** below, with the alternative it replaces.

1. **DEVIATION: key the metadata on a logical *change*, not on a hunk.**
   - A hunk is too unstable to carry purpose, risk or identity across versions. The same intent is built in very different ways:
     - Boot mount: v27 is 1 file / 3 hunks / +181 inline; v28 is 2 hunks / +8 plus a 185-line helper (docs/UPDATE-AND-DRIFT.md:135-137).
     - Container code moves from `container/container.py` (v27) to `container/crud.py` (v28) (:141-144).
   - Per-hunk records would copy one purpose line across 3-6 hunks.
   - The fix is two levels:
     - **change** (hand-written, stable ID across versions).
     - **hunk** (derived per build, carries only a pointer to its change).
   - Rejected: one record per hunk.
2. **DEVIATION: no per-hunk "Path A / Path B / UI" flags.**
   - Profiles are declared as sets of capabilities.
   - Changes declare `requires` edges, and the tool computes the closure.
   - Rejected: per-hunk flags. They are subjective, they repeat, and they cannot express dependencies such as an import hunk or a guard.
3. **DEVIATION: anchor status is not a column in the committed doc.**
   - For a registered build it is always "pass", because `verify` refuses anything else (acceptance/patch_drift.py:239-259).
   - Anchor status means something only for a candidate build. It belongs in `diagnose`/`upstream` output, mapped to change IDs, not in a committed doc that is stale the next day.
   - Rejected: anchor columns per version in the main doc.
4. **DEVIATION: generate the inventory sections only, not "the docs".**
   - Generated blocks go between markers and replace the hand tables at UPDATE-AND-DRIFT.md:133-184.
   - The rationale prose stays hand-written. The purpose lines are hand-written too, but they live once, in the manifest.
   - Rejected: a fully generated document. Q1/Q2/Q4 prose cannot be derived.
5. **DEVIATION: helpers are first-class rows.**
   - Helpers add 1,007 lines (v27) and 1,163 lines (v28 Oct27), more than the diffs themselves (:154-158).
   - A view of "how much and how deep we patch" that leaves them out under-reports by about 2x.

Kept from the proposal:
- The capability → change → file tree.
- One column per supported build. Under the policy that is at most 4: v27 RC.1, RC.2 and GA, plus one rolling v28.
- A separate cross-major delta view.
- "Hunk = one contiguous `@@` region."

## Q1 Failure modes and how to make it impossible to lie

| Failure mode | Mechanism that catches it |
|---|---|
| Metadata drifts from the real hunks | A unit test re-parses every patch of every build in `load_supported_builds()` (patch_drift.py:89). Every hunk must map to exactly one change, and every map row must resolve to an existing hunk. |
| A patch is edited without re-mapping | Hunk identity is a body hash, so any edit leaves the hunk unmapped and the test fails. Side benefit: v28 patch bytes are not pinned in the registry today. The bundle digest is computed at run time (acceptance/live_v28_patch.py:112-166), and only v27 pins `install_bundle_sha256`. This test becomes the de-facto pin. |
| A hunk moves (line offsets) between builds | The identity hash excludes the `@@` line numbers and the function-context suffix. That suffix exists in the v28 git-style patches and not in the v27 plain ones (compare `v28-nightly-20261027/ui-pools.patch:5` with `v27/ui-pools.patch:104`). |
| Same intent, different anchor or file in v28 | The change ID stays the same and the per-build hunk rows differ. `sync` suggests carry-forward by matching our own added text (`added_sha` over normalized `+` lines), since upstream context drifts but our code rarely does. A human confirms; the suggestion is never identity. |
| One logical change spans several hunks or files | This is normal. The change → hunks relation is 1:N, and an import hunk belongs to the change that uses it. |
| One hunk serves two changes | The rule is that a hunk maps to exactly one change. If it serves two (for example the combined import hunk `v27/storage.patch:3`), it maps to the primary change, and the other change gets `requires`. Fix it by splitting the hunk when authoring. These hunks are the main blocker for subsets; see Q3. |
| Capability tags are subjective | Use a closed vocabulary of about 12 capabilities with one-line definitions in the same file. The test rejects unknown tags. The machine guarantees *completeness*; reviewers own *correctness* of the labels. Accept that split openly. |
| A guard silently missing in one build | Add a guard-parity test: for each profile a build enables, every `role = "guard"` change in its closure must be present, or be listed in `known_gaps` with a reason. |

The guard-parity test would fire today.
The `plugins/boot.py` reserved-partition attach refusal is listed as part of the minimal Path B set (UPDATE-AND-DRIFT.md:139, :176).
It exists only in `v27-nightly-20261027/middleware.patch:44`, not in v27 RC.1 and not in v28 Oct27, while v28 Oct27 does ship the Path B UI chooser.
This may be intentional, for example if upstream boot.py already refuses (GUESS). The manifest would force someone to write that reason down.

## Q2 Schema, location, formats, hooks

> **Superseded in part by "Patcher project?" below.** For new adapters, the change identity moves into per-change patch headers. `hunk-map.json` stays only as an optional bridge for the frozen, already-gated builds.

Location: `boot-pool-patch/manifest/`.
- Hand-written files are TOML, read with stdlib `tomllib`, which the tree already uses (`truenas_api_client.py:28`).
- Machine-written files are JSON, written with the stdlib so there is no `tomli_w` dependency.

`manifest/changes.toml` (hand-written, the only place purpose and risk live):
```toml
[capabilities.path-b-pool-create]
title   = "Create a pool on a reserved boot-disk partition"
purpose = "pool.create accepts a validated PARTUUID instead of a whole disk"
[changes.pb-create-schema]          # stable ID across v27/v28
capability = "path-b-pool-create"
role       = "enable"               # enable | guard | plumbing | helper
purpose    = "pool.create accepts a reserved-partition vdev"
risk       = "high:data-boundary"   # level:area, closed set
requires   = ["pb-helper-boot-disk-partition", "pb-export-wipe-guard"]
retired    = false                  # true = in no registered build; kept for history
[helpers."middlewared/plugins/boot_disk_partition.py"]
change = "pb-helper-boot-disk-partition"
[profiles.path-b-api]
capabilities = ["path-b-pool-create", "boot-pool-safety", "dataset-guards"]
[known_gaps."v28-nightly-20261027"]
pb-boot-attach-guard = "reason …"
```

`manifest/hunk-map.json` is generated by `sync`. A human edits only the `change` values.
```json
{"schema_version":1,"builds":{"v28-nightly-20261027":[
  {"patch":"ui-pools.patch","file":"middlewared/plugins/pool_/pool.py",
   "body_sha":"…","added_sha":"…","change":"pb-create-schema"}]}}
```

How it stays DRY with `supported-builds.json`:
- The map is keyed by `adapter_id`, the registry's own key. It holds no versions, target hashes, trains or counts; those stay in the registry.
- The set of builds comes from `load_supported_builds()`. A map key with no registry entry fails, and so does a registry build with no map key.
- Line numbers, `+/-` counts and `patch:line` links are always recomputed, never stored.
- `body_sha` is a lock on hunk identity, not a copy of a target hash.

Fingerprint definition:
- `body_sha` = sha256 of the target path plus the exact hunk body lines (` `/`-`/`+`), excluding the `@@` header.
- The test asserts uniqueness within a build; a tie would be broken by ordinal.

New module and CLI: `acceptance/patch_manifest.py`
- `parse`: a parser for both patch flavours (v27 has bare `---/+++` lines; v28 has `diff --git` plus `index` lines).
- `sync --adapter ID`: writes or updates map rows, carries forward from the newest earlier build of the same major, and leaves unknown hunks as `"UNASSIGNED"`. The test fails until a human assigns them.
- `check`: the validation that the unit test calls.
- `render`: writes the generated blocks.

Hooks into existing tooling:
- `diagnose`/`upstream` (patch_drift.py:343-355, :584-593) gain per-hunk dry-runs: split each patch into one-hunk temp patches.
- They then print `ANCHOR-DIFF change=<id> capability=<id> file=…`, so "Oct 29 nightly breaks path-b-pool-create (2 hunks)" is the review starting point.

## Q3 Surgical subsets

How subsets are expressed:
- Profiles are declared in TOML (`path-a-api`, `path-a-ui`, `path-b-api`, `path-b-ui`, `vm-tcg`, `container`, `zvol`).
- `closure(profile)` takes the profile's changes, follows `requires`, and adds every guard tied to the enabled capabilities.
- The tool's first output is a **report only**: per build, which changes, files, hunks, helpers and line counts a profile needs.
- That report replaces the hand table at UPDATE-AND-DRIFT.md:173-179.

Building a reduced bundle is feasible but should be deferred. Done correctly it needs four steps:
1. **Select hunks by change, never by single hunk.**
2. **Regenerate a clean patch.**
   - Apply the selected hunks to the exact preimage file in memory, then re-diff.
   - Dropping a hunk shifts later line numbers, and GNU `patch --fuzz=0` still accepts *offsets*. `patch_dry_run_argv` (patch_drift.py:225-229) does not reject offset output.
   - The full bundles are safe only because the preimage hashes are pinned. A subset should apply with offset 0, and its dry-run should fail on any `offset` message.
   - This needs a pinned preimage source per build. Today `upstream` fetches `master`, which is unpinned (:568). That means a new `source_ref` field in **supported-builds.json**, not in the manifest.
3. **Run static checks:**
   - `py_compile`.
   - An undefined-name check (pyflakes or an `ast` walk) to catch a dropped import hunk. For example, `v28 ui-pools.patch:39-56` adds imports that are used by the hunks at :60 and :111.
   - A test that a helper is in the bundle iff its change is selected.
4. **Register and gate.**
   - A subset is a **new `adapter_id`** in `supported-builds.json`, with its own bundle digest and target set, and its own live gate for that profile.
   - Until it passes, it is "buildable, unsupported". This matches the existing caveat at :181-184.

Risks of partial subsets:
- Semantic dependencies the text cannot show: validators that assume the helper's namespace, or a UI hunk without its safety guard.
- Shared hunks that serve two changes.
- Profile-gated code inside one hunk.
- Tests that assert on whole-patch content: test_v27/v28_patch_artifacts.py read full patch text and would need per-profile variants.
- `requires` edges catch only the dependencies someone declared. Only the live gate proves behaviour.

## Q4 Operator view (generated block)

1. **Summary line per build:** files, hunks, `+/-` lines, helper lines, number of capabilities.
2. **Capability table:** about 12 rows sorted by risk. Each row has the capability, its 1-line purpose and its highest risk, plus one column per build showing `files/hunks +a/-d` or `—`. Guards are visibly marked, and missing guards show `—(gap: reason)`.
3. **Profile table:** profile → capabilities, with per-build counts. This answers "minimal for Path A only".
4. **Per-capability drill-down:** a heading, the purpose, then each change (purpose, risk, requires) and, per build, its files with `patch:line` links.
5. **Separate `PATCH-DELTA-v27-v28.md`:**
   - One row per change: present in v27 latest / v28, files moved, Δhunks, Δlines.
   - New and retired changes listed first.

Leave out of the operator view:
- Hashes and line coordinates at the top level.
- Diff text.
- Anchor status for registered builds.
- Evidence log names (they belong in the prose).
- Per-hunk rows above the drill-down.

## Q5 Smallest first increment

> **Revised: see "Revised work packages" at the end, which supersede WP2-WP5 below.** WP1 is unchanged.

Every package is tests-first and runs under the existing `unit-boot-pool-patch` lane.

- **WP1, hunk parser** (no metadata). Expected effort: Codex, about 150 LOC.
  - Tests first:
    - Parse all 4 registered builds and assert the documented totals. Hunks: 56 / 54 / 43 / 45. Lines: +597/-59, +596/-58, +316/-53, +388/-48 (UPDATE-AND-DRIFT.md:151-153).
    - `body_sha` stays stable when the `@@` line numbers and context suffix change.
    - Both patch flavours parse.
  - Reuse the hunk-count logic in tests/test_v28_patch_artifacts.py:33-60.
- **WP2, manifest and `check`.**
  - Codex scaffolds `changes.toml`, `sync` and the coverage, orphan, vocabulary, acyclic-`requires` and registry-key tests.
  - **Labelling of the roughly 40 changes is controller or reviewer work**, using :133-184 as the source. Codex must not invent purposes.
- **WP3, `render` plus a freshness test.** Regenerating must equal the committed marker blocks. Delete the hand tables :133-171.
- **WP4, profiles, closure and the guard-parity test.** It records the boot.py gap explicitly in `known_gaps` or fixes it. Add the delta view here; it is cheap once WP2's data exists.
- **WP5, per-hunk change IDs in `diagnose`/`upstream` output.**
- **Deferred:**
  - The subset bundle builder.
  - The `source_ref` preimage pinning.
  - Per-profile live gates.
  - Splitting shared hunks.

  Start them only when an operator actually asks to install a reduced profile.

## Patcher project?

Short answer: **no new project or product. Adopt git patch-series practice, plus a small shared convention and library.**
Writing this up surfaced a better design for the TrueNAS side. It is stated below and supersedes the hunk-map core of Q2.

### The two use cases side by side (verified from the tree unless marked)

**Wings (game-server daemon).** It lives in `<checkout>/wings-cgroups/v1-legacy/patchstack/`; this is the deployed v1 `cgroup` series. A v2 layout is at `<checkout>/wings-patchstack/`: one patch (F1) plus `series.yaml`.

| Aspect | Wings |
|---|---|
| What is patched | Go source in a git clone. The clones under `build/wings-{pterodactyl,pelican}` are disposable; the committed truth is `patches/pterodactyl-v1.13.3/0001-0011*.patch`. |
| Unit of change | **One commit = one logical feature**, made with `git format-patch`. The README table gives each patch's purpose (README.md:7-19). |
| "Supported build" | One upstream tag per target (`stack.conf`: `PTERODACTYL_REF="v1.13.3"`), tag only with no commit pin. v2 `series.yaml` does pin the `commit`. The deployable image is `wings-local:<ref>-cgroup.<n>`. The suffix is chosen by hand (`build-image.sh:9-11`), and nothing in the repo records which series digest built which image. `cgroup.6` appears nowhere in-repo (grep). |
| Anchors / verification | `git am` context match (`apply.sh:20`). A conflict means drift; it is resolved by hand, then `export-patches.sh`. |
| Drift to new upstream | `rebase.sh <ref>` runs `git rebase --onto` and stops on conflict. There is no upstream-release watcher. |
| Build / deploy | `build-image.sh` runs `docker build` from the patched tree; deployment follows `../SETUP.md` by hand. |
| Tests | `go build/vet/test` in golang containers, `dockerintegration`, and the systemd e2e. Assay R1 coverage is ≥50% (memory note). |
| Dependencies / subsets | `series.yaml` has `depends` and `combined_order` (series.yaml:14-34). Upstream PRs are contiguous ranges (README.md:21-26). |

**TrueNAS (this review).**

| Aspect | TrueNAS |
|---|---|
| What is patched | Installed Python files inside a read-only per-BE `/usr` of an OS image. There is no git on the target side. |
| Unit of change | **One patch file per area** (storage/ui-pools/…, 6 files). A logical change is spread across them, which is the root cause of the hunk-map problem. |
| "Supported build" | Exact build string, `targets.sha256` preimage hashes, and on-box install records (`supported-builds.json`). Fails loud on anything else. |
| Anchors / verification | `patch --dry-run --fuzz=0` after preimage hashes match, plus reverse dry-run after apply (patch_drift.py:225-320). |
| Drift to new upstream | `diagnose` / `upstream` (feed + `master` source), and PWMCP for the Storj listing. |
| Build / deploy | A deterministic tar bundle with a digest; transactional on-box apply with readonly toggling and rollback. |
| Tests | Unit artifact tests plus about 15 live lanes on <host>, including update survival. |
| Dependencies / subsets | Not expressed (`UPDATE-AND-DRIFT.md:181-184`). |

**What is genuinely common:**
- Pinned upstream bases.
- Patches as committed truth.
- A purpose for each change.
- Dependencies between changes.
- Forward-porting to a new upstream.
- Docs on "how much and why".
- Subsets: upstream PR ranges for Wings, profiles for TrueNAS.

**What is not common:**
- Hash-pinned preimages of an installed OS.
- On-box transactional apply, BE rollback and install records.
- Image build and tagging.
- Go vs Python test harnesses.
- Feed scraping.

These are most of each tool's code and stay per consumer.

### The key insight: Wings already has the answer to Q2's hardest problem

Wings has no change-identity problem because each **logical change is its own patch file**: commit subject, body and diffstat.
TrueNAS has one because its patches are grouped by area.

The better TrueNAS design (**DEVIATION from my Q2**) is a per-change series for each new adapter:
- **Files:** `<adapter>/series/NNNN-<change-id>.patch`.
- **Headers:** each carries DEP-3/Gerrit-style trailers: `Change-Id: pb-create-schema`, `Capability:`, `Role: enable|guard|plumbing`, `Risk:`, `Requires:`, `Forwarded: not-needed`.
- **Purpose line:** the subject.

Consequences:
- **Stable identity across versions** is the `Change-Id` trailer. No `body_sha` map and no carry-forward heuristic are needed.
- **Verify code needs no change.** `verify` already loops over `build["patches"]` (patch_drift.py:245, :305). A registry `patches` list of about 40 per-change files works as-is, with the same zero-fuzz checks. The registry stays the single source.
- **Forward-port uses Wings's proven workflow.**
  1. Commit the exact preimage target files (19 files, hash-checked against `targets.sha256`) as the base of a scratch repo.
  2. `git am` the previous adapter's series.
  3. Resolve the conflicts.
  4. `git range-diff` old..new for review.
  5. `git format-patch` exports the new series.

  The existing `diagnose` stays the gate.
- **Subsets become selection of whole patch files.** Take the `Requires`-closure, then check with `git am` onto the preimage base plus compile and undefined-name checks. GNU `patch` offsets disappear, because each per-change patch is regenerated against its own base by `format-patch`.

  Rejected alternatives: hunk-level selection with `filterdiff`, and my Q3 "re-diff in memory".
- **Docs are rendered from the trailers and diffstat.** Wings gets the same renderer for its README patch table.

**Cost:**
- Splitting today's area patches changes bundle bytes, so `install_bundle_sha256` and the v27 `reapply_from_bundle_sha256` pins break, and live gates must be re-run.
- Therefore adopt it **at the next registration only** (v27 RC.2/GA, the next v28 rolling base). Leave the 4 registered builds byte-frozen.
- For the frozen builds, the doc shows parser-derived counts. Add an optional `hunk-map.json` only if the operator wants capability rows for them too.

### (a) vs (b): a project or a library?

**Recommendation: (b), plus a written convention. No standalone "patcher" product.**

The shared part is small:
1. **Trailer convention.** About one page: field names and the closed vocabulary rule, with DEP-3 field names reused where they exist.
2. **`patchseries.py`**, about 300-400 LOC, stdlib only:
   - `parse(series_dir)` → changes with trailers and diffstat.
   - `check(vocab)`: unknown tags, a dangling `Requires`, cycles.
   - `closure(profile)`.
   - `render(columns=[...]) → markdown`.
   - `delta(a, b)`: by `Change-Id`, with git range-diff used for the human review.
3. **A test helper:** `assert_series_applies(base_dir, patches)` using `git am` in a temp repo.

Not shared:
- Registries: `supported-builds.json` vs `series.yaml`/`stack.conf`.
- Apply and deploy.
- Upstream watchers.
- Image and BE handling.

A product with its own CLI, release train and cmru pipeline would cost more than both consumers' shared code.

Where it lives:
- Start inside `scripts/truenas` with no TrueNAS imports.
- Move it to `<checkout>/libraries/patchseries/` (next to `cli-extended`) when Wings adopts the trailers. That is the "second real consumer" rule.

### (c) Existing tools: do not reinvent

V = verified via web search this session. M = from memory, unverified. Licences are M unless marked.

| Tool | Gives | Lacks vs our needs | Status / licence / weight | Verdict |
|---|---|---|---|---|
| **git am / format-patch / rebase** | The series-as-commits workflow; Wings already uses it | Exact-build registry, docs | Core git | **Adopt** for TrueNAS forward-porting |
| **git range-diff** (V) | Pairs commits across two series versions and shows the diff of the diffs. The output is for humans, not scripts (V). | Not machine-readable | Core git | **Adopt** as the review aid. The machine delta comes from the `Change-Id` trailers. |
| **git rerere** (M) | Replays conflict resolutions | Little reuse across builds | Core git | Optional |
| **quilt** (V: active, Savannah) | Series file, push/pop/refresh | IDs, registry, docs; anchors drift silently with `refresh` | GPL-2 (M), tiny | Ignore. git covers it, and quilt adds a second workflow beside Wings's. |
| **stgit** (V: Rust 2.x, 2024 releases) | Stack ops on git commits; makes editing a middle patch safe, which Wings memory says is error-prone | Same gaps | GPL-2 (M), one binary | **Consider for Wings only**; it addresses the amend/rebase care notes. |
| **git-series** (V: 0.9.1, 2016 origin) | Version history of a series | Maintenance unclear (V: unconfirmed) | MIT (M) | Ignore; range-diff is enough |
| **TopGit** (M) | Branch-per-patch dependency DAG | Heavy, niche | GPL-2 (M) | Ignore |
| **gbp pq + DEP-3** (V) | Patch queue ↔ git branch; standard header fields (Forwarded, Last-Update) | gbp pq handles DEP-3 only partially (V); Debian-packaging-centric | GPL-2+ (M), Python | **Borrow the DEP-3 header vocabulary**; do not adopt gbp |
| **Gerrit `Change-Id`** (M) | Stable identity across rebases | Server not needed | n/a | **Borrow the trailer idea** |
| **RPM `%autopatch` + fuzz 0** (V) | Precedent for strict fuzz; silent-skip pitfalls exist (V: rpm#3093) | OS-package format | n/a | Borrow: confirms the zero-fuzz policy |
| **Yocto patch-fuzz QA + `devtool modify/finish --force-patch-refresh`** (V) | Fuzz is a QA failure; refresh, then review the moved patches | Bitbake-bound | MIT/GPL (M), huge | **Borrow the workflow** (refresh → side-by-side review); our `diagnose` already matches it |
| **patchutils** (`lsdiff`/`filterdiff -#`/`grepdiff`/`interdiff`) (V) | Hunk listing and selection by number | Hunk numbers are not stable IDs | GPL-2+ (M), small C package | Ignore under the per-change design; `interdiff` is a handy manual aid |
| **Nix `patches`/`overrideAttrs`, Gentoo user patches, Buildroot patches** (M) | Ordered patch lists per package version | No capability docs or subsets; distro-bound | n/a | Ignore (same model as ours) |
| **coccinelle / spatch** (M) | Semantic patches for C | C only | GPL-2 (M) | Ignore |
| **ast-grep** (V: YAML rules with `fix`) / **semgrep** (M) / **comby** (V: appears inactive) | Anchor-robust structural rewrites | **Contradict the fail-loud exact-build policy**: they keep applying when upstream moves, which is what zero fuzz forbids | MIT / LGPL / Apache (M) | Ignore as the patch mechanism. ast-grep is OK as a *porting search aid* ("where did this function move"). |
| **Renovate custom datasource** (V) / **Dependabot** (M) | Upstream version watching and PRs | Needs a bot runtime and a forge; cannot gate on hashes | AGPL-3 (M), heavy | Ignore. `patch_drift.py upstream` plus a ~20-line GitHub-releases check for Wings is enough. |
| **patch-package / pnpm patch / cargo `[patch]`** (M) | Ecosystem-local patching | Wrong ecosystems | MIT (M) | Ignore |
| **diffoscope** (M) | Deep diff of two images or ISOs | Not a patch tool | GPL-3+ (M), heavy | Optional for investigating update deltas |
| **OSTree / bootc derivation** (M) | Layered image-based OS modification | TrueNAS ships its own BE/updater; a derived image would bypass the vendor update path | n/a | Ignore (GUESS: not viable on TrueNAS) |

### (d) Recommendation and next steps

1. **Adopt the per-change series format for the next TrueNAS registration**, and render docs from its trailers. Keep `supported-builds.json` as the only registry; its `patches` list simply gets longer.
2. **Write the trailer convention** (DEP-3 fields plus `Change-Id`/`Capability`/`Role`/`Risk`/`Requires`) and `patchseries.py` with tests, inside `scripts/truenas`.
3. **Wings, separately and small:**
   - Add the trailers to the existing 11 commits at the next rebase.
   - Generate the README patch table.
   - Record the series digest and upstream commit as image labels in `build-image.sh`. This closes the "which series is `cgroup.6`?" gap.
   - Pin the commit in `stack.conf` the way `series.yaml` already does.
   - Evaluate stgit for safe editing of middle patches.
4. **Extract to `libraries/patchseries/`** once Wings consumes it.

**Do NOT build:**
- A patch-stack manager or rebase engine (git am/rebase/rerere, stgit).
- A series-version differ (git range-diff).
- Hunk filtering utilities (patchutils).
- A structural-rewrite engine (ast-grep).
- An upstream-watch bot (Renovate-class).
- A standalone "patcher" product with its own release train.

### Revised work packages (supersede WP2-WP5)

- **WP1 (unchanged), hunk parser.** Needed for doc counts on the 4 frozen builds and as diffstat input.
- **WP2', trailer convention plus `patchseries.py`.**
  - Tests first: parse trailers; reject unknown capabilities and dangling or cyclic `Requires`; closure; render golden-file markdown; `delta` by `Change-Id`.
  - Fixture series are synthetic; no TrueNAS data.
- **WP3', forward-port helper.**
  - A script that builds a scratch git base from a preimage tree, checks it against `targets.sha256`, then `git am`s a series and prints `git range-diff`.
  - Test with a 2-file synthetic base: a clean apply, and a conflict that must exit non-zero.
- **WP4', first real per-change series.** The next registered TrueNAS build (v27 RC.2 or the next v28 base), labelled by controller or reviewer, not Codex. Live gates are re-run as for any new adapter.
- **WP5', render plus freshness test** into UPDATE-AND-DRIFT.md. Frozen builds show WP1 counts only.
- **WP6', `diagnose`/`upstream` print `Change-Id`** per failing patch. This is trivial once patches are per change, since a failure names its file.
- **Deferred:**
  - `hunk-map.json` for frozen builds (only if asked).
  - Subset bundle registration and per-profile live gates.
  - Wings adoption (item 3 above).
  - The move to `libraries/`.

Sources (web, this session):
- [git-range-diff](https://man.archlinux.org/man/git-range-diff.1.en)
- [b4 diff](https://b4.docs.kernel.org/en/latest/maintainer/diff.html)
- [stgit](https://github.com/stacked-git/stgit)
- [git-series](https://github.com/git-series/git-series)
- [DEP-3](https://documentation.ubuntu.com/project/how-ubuntu-is-made/concepts/deb-3-patch-file-headers)
- [gbp patches](https://www.eos-numerique.com/doc/git-buildpackage/manual-html/gbp.patches.html)
- [Yocto fuzz](https://docs.yoctoproject.org/pipermail/yocto/2018-March/040273.html)
- [rpm#3093](https://github.com/rpm-software-management/rpm/issues/3093)
- [patchutils](https://github.com/twaugh/patchutils)
- [filterdiff](https://www.mankier.com/1/filterdiff)
- [quilt](https://build.opensuse.org/package/meta/devel:tools:scm/quilt)
- [comby](https://gittrend.io/repo/comby-tools/comby)
- [Renovate custom datasource](https://docs.renovatebot.com/modules/datasource/custom/)
