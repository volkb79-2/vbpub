# CMRU review at `1ddc25f1f`: KI-78 candidate folding, A–G contract, and prior findings

**Not releasable yet.** The new KI-78 design (notes folded into the candidate before the gate and tag) is the right one, and most of my earlier findings are fixed. But two new defects block: one stops the registered gate from starting, and one refuses every later release that has notes. C-2 and the Run-Gate floor are still open. No aggregate gate has passed on any commit.

**State.** `git rev-parse HEAD` returned `1ddc25f1ff0d98934d239e73e912ec6b9c32e4fc` at the start and the end. `git status --short` was empty both times. I made no edits, commits, test or gate runs, or messages.

**Sources.** The original Sonnet assignment (`jobs/cd05ec35/state.json` intent), the implementation report, the Opus readiness report, and my preserved 10-14 review at `72e05e3f3`. I also read Run-Gate lane logs under `/tmp/run-gate`. This pass covers the eight new commits `710f3b89f..1ddc25f1f`.

## Blockers

**B-1 · Critical · `cmru/tools/run_release_gate.py:73-85` together with `cmru/run-gate.toml:225`: the registered gate cannot get past component loading.**
- **Trigger:** the `gate` lane now starts `python3 -I -B …/run_release_gate.py`. `-I` keeps the script's own folder, `cmru/tools`, off the import path. `_load_components` only adds `cmru/src`, `assay/src` and `libraries/worktree/src`, then runs `import prepare_assay_baseline`. That module lives only in `cmru/tools/`, and it in turn imports `assay_baseline_tags` from the same folder.
- **Impact:** every gate run fails with ModuleNotFoundError after the preflight and before any lane. No test catches it:
  - every `test_release_gate.py` fixture replaces `_load_components` (`:126`, `:156`);
  - the new subprocess test (`:821`) runs in a fake repo and only asserts that the exit code is non-zero.
- **Minimum fix:**
  - Add `PROJECT_ROOT / "tools"` to the inserted import paths. It is already covered by the C-1 check.
  - Add a test that runs the real entrypoint under `-I` against a git fixture up to the point where the first lane would launch.

**B-2 · High · `cmru/src/cmru/changelog.py:106`: after one release folds notes, every later release with notes refuses.**
- **Trigger:**
  1. Release 1.0.0 folds notes. The promoted changelog keeps `<!-- cmru: pending release notes begin/end -->` inside the 1.0.0 section.
  2. An author then writes `[Unreleased]` notes for 1.1.0.
  3. `fold_unreleased_notes` calls `_folded_notes_block(markdown)` on the **whole file**, finds 1.0.0's block, and raises "both pending [Unreleased] notes and an existing folded-note block". `_fold_pending_release_notes` (`cli.py:4285`) turns that into RefusedBeforeChange.
- **Impact:** nothing is lost, but the feature works exactly once per project. Releases are then blocked until someone deletes the markers from shipped history by hand. The test at `test_unreleased_notes.py:267` folds into the *same* version that holds the block, so it accepts this behaviour.
- **Minimum fix:**
  - Scope the guard to the target version's own section.
  - Optionally convert the markers to a neutral "released" marker on promotion, or accept them as permanent.
  - Add a test with two consecutive releases that both carry notes.

**B-3 · High · C-2 is unchanged: `gate_snapshot.py:291-306` and `docs/DESIGN-GUIDE.md:697-700`.**
- **Trigger:** the protected refs are still only the snapshot branch, the source branch, and `refs/cmru-gate/<id>`. The installed Run-Gate (`site-packages/run_gate.py:10413-10425`, rev 68) says outright that "the common Git directory itself stays read-write", and only `hooks/` and `info/` are read-only.
- **Impact:** any lane can move `refs/heads/main` or any release tag without the gate noticing, including mutated code during the 7h mutation lane. The docs still assign confinement to "Run-Gate's mount hardening", which does not exist. The contract required detecting reachable ref changes, or a recorded `BLOCKED:` dependency.
- **Minimum fix:** record a `BLOCKED: Run-Gate common-dir write confinement` dependency and file it in Run-Gate's backlog. In CMRU meanwhile, refuse if any pre-existing tag is moved or deleted, or `main` moves non-fast-forward, during a lane. Concurrent agents only add refs, so this does not cry wolf.

**B-4 · High · the gate now trusts whatever `run-gate` is on PATH, with no floor or identity check (KI-77 still open).**
- **Trigger:**
  - `_invoke_lane` runs `run-gate …` from PATH (`run_release_gate.py:340`).
  - The release step in `cmru.toml:49` does the same.
  - Here that binary is `/home/vscode/.venv/bin/run-gate`, version `23.10.1.dev441+g341abd66a`. It is a local dev wheel installed today from another worktree (`run-gate-rg93-git-metadata-20261013`), and commit `341abd66a` is not an ancestor of HEAD.
- **Impact:** every lane verdict is decided by unreleased code from another branch. The candidate's binding to its own commit no longer covers the component that judges it.
- **KI-77 is still wrong:** its reason ("needs a Run-Gate-side revision API") was already false. Run-Gate has `run-gate --version` (prints `rev 68`) and records `run_gate_revision` in lane metadata.
- **Minimum fix:**
  - In the gate preflight, read `run-gate --version` and the installed version, and refuse a release gate below the floor or on a `.dev`/`+local` build.
  - Record that identity in the snapshot manifest.

**B-5 · Release evidence is still open (contract F).**
- No aggregate `gate` has ever passed. The mutation and canary lanes have never run on any commit.
- The latest coverage log `/tmp/run-gate/lanes/coverage/7d06a…log` (06:54, this worktree) shows 100.00% with 4602 passed. It started after the HEAD commit (06:49:53), but the log records no commit, so it is not evidence bound to `1ddc25f1f`.
- KI-78's own text cites coverage at `187366468`, which is before the fold rewrite.
- D-4 (host capacity record) is still missing.
- B-1 means even a fresh gate run fails right now.

## Follow-ups (not blocking, medium and low)

**F-1 · Medium · `--no-build` and image-only releases leave notes misattributed or pending for ever.** `cli.py:2968` and `:4412-4420`.
- **`--no-build`:** the tag is created with the notes still under `[Unreleased]`. A later `cmru publish --from-checkout` of that tag publishes a body without them, and the next version publishes them. They are recoverable and not duplicated, but they land under the wrong version.
- **Tagged image pushes** (e.g. `scripts/cgroup-profiler/cmru.toml:40,65-68`): notes are never folded on any release. `[Unreleased]` slowly describes work that has already shipped, which is the orphaning KI-30 used to prevent.
- **Design fix:** fold whenever a tag is created. Folding is about history; whether a GitHub body exists is the publisher's concern. A later publish of a `--no-build` tag would then carry the notes for free. If you keep the current rule, have dry-run and prepare warn when notes are left pending on an image-only project.

**F-2 · Medium · release-source selection works, but the local-tag fallback trusts local state.** `handlers.py:72-91`.
- Inside a transaction the notes are read from `CMRU_RELEASE_COMMIT`, the tag commit (correct). A retained build uses its manifest commit (correct).
- `--from-checkout` resolves the **local** tag `<prefix>-v<version>` without checking that it matches origin. That is the "name for object" pattern from the cmru KI-12 incident.
- **Fix:** compare against `git ls-remote origin refs/tags/<tag>`, or refuse if they differ.

**F-3 · Low · publisher detection.** `_has_cmru_github_release_body` (`cli.py:4276`) only recognises an argv starting `["cmru", "handler", "wheel-publish" | "tarball-publish"]`. A wrapper script or `python -m cmru handler …` is silently classed as having no body, so its notes stay pending. This is safe but silent. Log the classification, or declare it in config.

**F-4 · Low · wrong error type.** A malformed marker block found during regeneration (`changelog.py:589`, inside `generate_release_changelog`) raises a bare ReleaseNotesError, not RefusedBeforeChange. The exit-code category is then "failure" rather than "refused".

**F-5 · Low · the implementation report is stale (contract G).** It was last changed in `72e05e3f3` and still describes the post-publish consume step, `./run-gate.py`, and the old evidence. KI-58's title and status still rest on the removed KI-30 premise.

**F-6 · Low · carried over unchanged:**
- After a release, `_unchanged_reason` (`version.py:549`) still says "no commits under…" even when there are changelog-only commits.
- "Git reports prunable" is still a local-namespace check (`gate_snapshot.py:993`, `worktree/core.py:1232`).
- The live-manifest proof is still a 30-minute freshness window (`installed_wheel_smoke.py:164`).
- A custom changelog that sits inside another project's `paths` still schedules that other project.

## Prior findings at HEAD

| Prior | Now |
|---|---|
| 1 · C-1 shadow modules | **Fixed in principle**: `-I` blocks `cmru/tools` shadows, and Run-Gate is no longer run from the caller's tree. **Regressed into B-1**, and trust moved to the ambient binary (B-4). |
| 2 · C-2 ref reach | **Open** (B-3). |
| 3 · post-publish consume can fail after the release is public | **Fixed**: there is no post-publish commit. |
| 4 · duplicated notes via `--no-build` or promotion failure | **Mostly fixed**. `--no-build` no longer duplicates but misattributes (F-1). After a publish with failed promotion, the tag-ahead refusal blocks the next release until recovery. |
| 5 · direct publish reads the working tree | **Fixed**, apart from F-2. |
| 6 · image-only notes marked as published | **Fixed**; the remaining design gap is F-1. |
| 7 · `## ` in comments or fences truncates | **Fixed** by the masking parser. An unclosed comment makes later headings disappear, which then refuses loudly. |
| 8 · near-miss or duplicate `[Unreleased]` | **Fixed**: now refused. |
| 9–11 | Unchanged (F-6). |
| 12 · KI-77 reason | Unchanged, and now more important (B-4). |
| 13 · test strength | **Better**: real-git tests for the fold, regeneration, custom paths and dry-run. Still missing: a two-release sequence (B-2), the real gate entrypoint (B-1), and an end-to-end check that dry-run output equals the publisher's body. |

## Contract A–G

| Item | Status |
|---|---|
| **A** | C-1 fixed but broken by B-1. C-2 open. C-3 likely passing (unbound log). C-4 done, with F-6. |
| **B** | C-5 to C-8 done. D-1 not done (decision recorded). D-2/D-3 in KI-77, with the wrong rationale. D-4 missing. |
| **C** | KI-75 done. KI-78 redesigned correctly, except B-2 and F-1. README, DESIGN-GUIDE, CONSUMERS and RELEASE-TRANSACTIONS now agree with the fold model. |
| **D** | KI-58 source notes untouched. |
| **E** | KI-59/KI-68 done. Regeneration keeps the folded block. |
| **F** | Open (B-5). |
| **G** | Committed. Report stale (F-5). |

## Design feedback

Folding the notes into the candidate before the tag is the right transaction boundary. The tag, the GitHub body and the promoted `main` now carry the same bytes, and any failure before promotion leaves `main` untouched. Three things would finish it off:
- **Make folding depend on tag creation, not on publisher kind** (F-1). That removes the `--no-build` and image-only special cases and their docs.
- **Treat the marker block as part of the version section only** (B-2). A one-time "pending" marker kept in shipped history either has to be scoped or neutralised after release.
- **Pin and attest the gate's judge** (B-4), since moving Run-Gate out of the tree made the installed CLI part of the trusted base without a check.

**Next safe steps, in order:**
1. Fix B-1 and B-2, with tests.
2. Record B-3 as BLOCKED with Run-Gate and add the CMRU-side tag/`main` check.
3. Add the Run-Gate floor and identity check (B-4).
4. Refresh the implementation report.
5. Then, with your authorisation, run the coverage lane and the aggregate `gate` on the new commit, plus the D-4 capacity record before the 7h mutation lane.
