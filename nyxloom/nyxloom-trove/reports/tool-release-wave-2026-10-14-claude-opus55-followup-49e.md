# Tool release wave: Claude Opus follow-up review

- Review route: Claude Opus 5.5, high effort (operator-selected resumable session)
- Reviewed branch: `tool-release-wave-20261014`
- Reviewed HEAD: `49e4be0927a629bb1b039af7bbdc6df39a195cb5`
- Base: `0244fe4e7428d5eb6ce1c37ecc02f2cba0b7719d`
- Review scope: full release-wave diff and earlier review findings; read-only; no tests or gates
- Checkout was clean at the reviewed HEAD before and after the review.
- This report predates subsequent candidate fixes and is retained as historical review evidence.

I reviewed `49e4be092` against base `0244fe4e7` read-only: no edits, commits, tests or gates. Five of the earlier findings are still partly open (#1, #3, #7, #8, #15), and there are two new problems with real consequences: the release order only works through an unreleased CMRU, and the tester-image build instructions can't be run as written. I couldn't save the report to a file because Write is disabled in this session, so it's all here.

Three facts about the devcontainer that several findings depend on:
- `cmru` reports 6.1.0 but is installed editable from the shared main checkout (`/workspaces/vbpub/cmru`).
- `run-gate --version` reports `23.10.1.dev458+g2c9f6be37`, a dev build.
- `CMRU_WHEEL_BUILDER_IMAGE` is not set.

## Status of the earlier findings

| # | Status | Evidence |
|---|---|---|
| 1 Run-Gate version 23.11 vs 24.0.0 | **PARTIAL** | Release notes now come from `CHANGES.md` (`--notes-env` was removed). But 24.0.0 still depends on someone passing `--set-version` (`run-gate-project/cmru.toml:24-28`). No commit under `run-gate-project` since `run-gate-v23.10.0` is marked breaking, and changelog-only commits don't count toward the bump. → N4 |
| 2 `validate-pointers` path forms | FIXED | `run_gate.py:7096`, plus a parametrized test at `test_run_gate.py:27809`. |
| 3 / Codex P2 tester build argument | **PARTIAL** | The scripts now pass the argument, but the procedure they call can't run. → N2 |
| 4 cleanup leads to reinstall | FIXED | The marker is written before cleanup (`installer.py:4371`). `_refuse_completed_install` runs before any host change, from both `bootstrap._install` and `Installer.install`. |
| 5 `userdata-only` leaves secrets | FIXED | All four cloud-init copies are removed (`installer.py:76-81`). The payload itself is written under `/run`. |
| 6 Assay tail leaks part of a secret | FIXED | `_read_tail` reads extra bytes before the cut, and redaction keeps the byte width (`redaction.py:28-31`), so the cut point doesn't move. Both runners pass the secret values (`runner.py:4025`, `mutation.py:3488`). |
| 7 sharded analyses fail the schema | **PARTIAL** | The usual sharded case now validates; a complete sharded run still doesn't. → N5 |
| 8 commit-scope filtering | **PARTIAL** | The real release now sees every project (`cli.py:3018`, `:5568`). The changelog plan, `status` and `--dry-run` still see one, so the mismatch has moved rather than gone. → N3 |
| 9 diverged tag | FIXED | `version.py:533-545`. Every project's latest tag is an ancestor of HEAD, and promotion merges rather than rebases, so ordinary releases won't hit the refusal. |
| 10 gated commit read too late | FIXED | `_run_release_gates_on_exact_head` (`cli.py:2874`), and tagging is refused if HEAD moved during the gate. |
| 11 Run-Gate version checked once | FIXED | Checked before and after each lane, run by resolved path, with a hash of the installed files (`run_release_gate.py:509-535`). |
| 12 misleading tag error | FIXED | `gate_snapshot.py:460-466` |
| 13 hollow CMRU notes tests | FIXED | A real-git test now checks the tagged `CHANGES.md` and that the published commit is the tagged one (`test_release_end_to_end_real_git.py:245-250`). |
| 14 image tests check text only | PARTIAL (low) | The new test runs a copy of the Dockerfile's shell expansion, kept in sync only by a string assertion. Nothing builds the image. |
| 15 tester version check proves nothing | **PARTIAL** | The `+tester.unified` suffix is back (`Dockerfile:165`, `:231`), but CMRU accepts it as meeting its minimum, and the version is still just what the caller passes in. → N6 |
| 16 ignored files overwritten | FIXED | `--ignored=matching` (`worktree.py:4925`), with only the CIU record file exempted. |
| 17 internal flag exposed | FIXED | Replaced by a private entry point (`worktree.py:1961-1971`). → N7 for a small side effect |
| 18 docstring indentation | FIXED | `coverage_gate.py:421` |
| Codex P1 assume-unchanged | FIXED | Index-flag scan plus a byte comparison of every tracked file against the commit (`gate_snapshot.py:288-374`, `:965-972`). The repo has no submodules or `.gitattributes`, so this won't refuse a healthy snapshot. |
| Codex P2 `--check-env` skips directories | FIXED | `os.walk` with an error callback; directory and lane-resolution failures now mark the scan INCOMPLETE (`run_gate.py:6896-6978`). |
| Codex P2 indented `## ` headings | FIXED | `release_notes.py:167`; the fold step uses the same parser (`changelog.py:101`). |

## New and remaining findings, most severe first

**N1 (High). The release order only works through the editable, unreleased CMRU.**
- **Where:** `run-gate-project/cmru.toml:59-61,94`; the hand-written `[Unreleased]` notes in the CGProfile and Run-Gate `CHANGES.md`; `cmru/tools/run_release_gate.py:31,137-146`.
- **Problem:** Released CMRU 6.1.0 refuses a non-empty `[Unreleased]` (KI-30; `git show cmru-v6.1.0:cmru/src/cmru/changelog.py`, line 343). Without `--notes-env` it would also publish only "run-gate 24.0.0" as the release body. So only the new, unreleased CMRU code can release CGProfile and Run-Gate. Meanwhile CMRU's own gate needs Run-Gate 24.0.0 or later; the installed `23.10.1.dev458` fails it with the misleading message "unrecognized SemVer identity".
- **Impact:** Today it works only because `cmru` runs editable from the shared checkout while reporting "6.1.0". The release evidence names the wrong tool version, and that checkout's uncommitted edits run too. A clean host with released tools can't follow the order. The `cmru.toml` comment "The installed CMRU 6.1.0 executes this argv" is wrong.
- **Fix:** Write the bootstrap down and enforce it:
  1. After the provisional merge, install CMRU as a non-editable wheel built from the merge commit, with a local version suffix.
  2. Record that commit with the release evidence.
  3. Make the CGProfile and Run-Gate releases refuse early if the releasing CMRU lacks the new release-notes handling (KI-78).
  4. Reword the message at `:137-146` to say a dev build was found and Run-Gate 24.0.0 or later must be installed.
- **Test:** A real-git release with notes in `[Unreleased]`, run with a CMRU that lacks KI-78, must refuse before tagging.

**N2 (High). The tester-image build instructions can't be run as written (rest of #3).**
- **Where:** `tester-unified/README.md:13-30` and `nyxloom/tools/remote-mutation-audit-host.sh:93-99`. Several other files print these same steps as the fix for a stale image: `ciu/scripts/check_run_gate_wheel.py:29`, `scripts/cgroup-profiler/tools/gate.sh:81`, `tester-unified/docs/CONSUMERS.md`, and `run-gate-project/REMOTE-LANES-BUILDKITE.md:403`.
- **Problem:** `cmru handler wheel-build` reads only the process environment (`handlers.py:250-296`). It needs `CMRU_WHEEL_BUILDER_IMAGE`, which only the release path loads from `cmru.orchestration.toml`. It also needs the `wheel-builder:local` image and a cgroup parent. The remote audit script also assumes `cmru` is installed on the remote host and sets neither variable.
- **Impact:** Every local image rebuild, and every audit run without `--tester-image`, stops with exit 3 before `docker build` runs.
- **Fix:** Give the full command with its environment in the README, and say where the builder image comes from. In the audit script, export the builder image and `CMRU_DOCKER_CGROUP_PARENT="$cgroup_parent"`, and refuse early if any prerequisite is missing. Alternatively, derive the version without CMRU, from `git describe --match 'run-gate-v*'`.
- **Test:** Run the audit script with fake `cmru`/`docker` and assert the environment they receive. Add a docs test that the README command includes the required variables.

**N3 (Medium). The changelog version and the tag can now disagree (introduced by the #8 fix).**
- **Where:** `cmru/src/cmru/changelog.py:155` still checks one project at a time. That plan drives the changelog section, the release notes and the tag named in the commit trailer. `release_cmd` now sees every project. `status` (`version.py:767`) and `--dry-run` (`cli.py:5624`) also still see one.
- **When it breaks:** A commit inside one project's folder that is scoped to another project and carries a bigger bump, for example `feat(assay)!:` that also touches `run-gate-project/`.
- **Impact:** The changelog section says one version and the tag is a patch. The publisher looks up the section for the tag after the tag has been pushed, and fails. Nothing in this wave triggers it (the only breaking commit touches only `assay/`), but nothing prevents it either.
- **Fix:** Compute the release plan once and pass that version to the changelog, the notes and the tag.
- **Test:** A real-git test with a cross-scoped breaking commit, asserting that the changelog heading matches the created tag.

**N4 (Medium). Run-Gate's major version still depends on remembering a flag (rest of #1).**
- **Problem:** Forgetting `--set-version 24.0.0`, or a `--resume` that drops it, produces 23.11.0 with "Breaking" notes.
- **Impact:** The install URLs at `CONSUMERS.md:19` and `:1842` return 404, and CMRU's 24.0.0 minimum refuses that release.
- **Fix:** Land a `feat(run-gate)!:` commit with a `BREAKING CHANGE:` footer that touches a source file under `run-gate-project/`. Alternatively, have CMRU enforce a declared minimum next version when it plans the release.

**N5 (Medium). A complete sharded analysis still fails the schema (rest of #7).**
- **Where:** `assay/analysis/src/assay_analysis/campaign.py:2012`; the schema rule is at `analysis-campaign.schema.json:53-58`.
- **Problem:** `--shard-count 1` is allowed (`mutation.py:2742`). With that, with an empty plan, or with a small plan whose candidates all land in the selected shard, nothing blocks completion. The result is `status: complete` with `qualifying: false`, and the schema rejects that combination.
- **Fix:** Add a `sharded_selection` blocker whenever a shard is set, or let the schema accept that combination for sharded runs.
- **Test:** Validate a shard-count-1 analysis and an empty-plan analysis against the shipped schema.

**N6 (Low–Medium). A pre-release Run-Gate build can pass as the real release (rest of #15).**
- **Problem:** The README's release-candidate step builds a plain `run_gate-24.0.0` wheel before 24.0.0 exists. Installing it is the only way to get CMRU's gate to pass before the release (see N1). The gate records a file hash but never compares it to the published release.
- **Fix:** For versions without a local suffix, compare that hash against the published release asset. Have the README use a local suffix for candidates, and explain why the image needs the planned version at all.

**N7 (Low). CIU's private env-generation entry point lost the CLI's error message.**
- **Where:** `ciu/src/ciu/worktree.py:1961-1971`.
- **Problem:** A `WorkspaceEnvError` now shows up as a traceback instead of the `[ERROR] …` line the CLI printed (`cli.py:1107-1109`).
- **Fix:** Wrap the call in a private module function that catches the error the same way, and run it with `-m`.

## Is the release order workable?
Yes, but only with N1's bootstrap written down and N2 fixed, so the tester image can be rebuilt between steps:
1. Provisional merge.
2. Install CMRU, non-editable, from the merge commit.
3. Rebuild the tester image.
4. Release CGProfile with `--set-version 1.0.0`.
5. Release Run-Gate with `--set-version 24.0.0`, or rely on the commit from N4.
6. Install Run-Gate 24.0.0 in the devcontainer and rebuild the tester image.
7. Release CMRU. The over-20-minute mutation run (R2) can be postponed with `--postpone-mutation`, which records the postponement as evidence.
8. Release CIU, then Assay. Assay reaches 10.0.0 through its existing `feat(assay)!:` commit.

`--set-version` accepts only one project per run (`cli.py:5183`), so the two flagged releases must be separate `cmru release` runs.

Reviewed HEAD `49e4be0927a629bb1b039af7bbdc6df39a195cb5`; `git status --short` was empty before and after.
