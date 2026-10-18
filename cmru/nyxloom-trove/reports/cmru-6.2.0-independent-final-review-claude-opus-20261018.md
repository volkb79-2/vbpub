# CMRU 6.2.0 candidate — independent final review (Claude Opus, 2026-10-18)

- Checkout: `.worktrees/cmru-candidate-integrated-20261017`, branch `cmru-candidate-integrated-20261017`
- Starting HEAD: `cf92f575267975d9ef241933cb96dd04e9df5d77`; compared with public `cmru-v6.1.0` (`f13879cc1f29`)
- Final commit: the commit that adds this report (its hash is in the controller hand-back)
- Method: a fresh read of the candidate's code, tests and docs. Four read-only sub-reviews
  covered snapshot/gate identity, transactions/resume, Run-Gate 24.0.1 integration and the
  docs triad. The controller verified every finding listed here against the code before
  acting on it. No earlier review was used as proof.

## Gate evidence

**I ran no tests and no gates.** The only gate evidence is what the controller supplied: the
registered Run-Gate 24.0.1 `canary` lane passed on the starting HEAD `cf92f575`
(`cmru/.assay/coverage-canary-cmru.json`; control exit 0, transformed run exit 1, reason
`Required test coverage of 100% not reached`). That evidence does not cover this report's
commit. Coverage, `gate-provisional`, and every new or changed test below are unverified
until the controller runs the registered gates through `cmru release`. Every edited Python file
was checked only with `ast.parse`.

## Findings fixed in this commit

| # | Severity | Where | Triggering state → consequence | Oracle added |
|---|---|---|---|---|
| F1 | major (dates from 6.1.0; the candidate adds new triggers) | `src/cmru/cli.py` `_release_projects_sequentially`, new `_discard_unpushed_release_tags` | `release_cmd` creates the annotated local tag, then the backup push, the re-gate of a `file:` bump commit, the receipt check, or the `before tag publication` check fails before `_push_tags`. The local-only tag stayed behind, and resume, a fresh release and `abandon` all then refused ("latest local tag … not present on origin"). This violated "a pre-tag failure leaves no release tag". | `test_release_end_to_end_real_git.py::test_a_failure_between_local_tagging_and_tag_push_leaves_no_tag_and_resumes` (real git, `file:` strategy, failing re-gate: no local or origin tag, nothing published, resume tags the same commit). `test_release_coverage_gaps.py`: three tests pin the hand-off, the exact-object deletion filters (another prefix, `-latest`, a tag in the start-of-transaction origin snapshot, a recorded push attempt, an already-absent tag), and a cleanup failure that never masks the original error. |
| F2 | major (dates from 6.1.0) | `src/cmru/transaction.py` `write_release_scope` | Project A completes and B fails. Resume #1 rewrites the scope to `[B]`, and B fails again. Resume #2 then refuses A's result as "outside the recorded scope", and default-mode `abandon` refuses because a result exists, so the transaction is stuck. For a feature source, `abandon` also read a complete publication as partial. The scope now keeps every project with a recorded result. | `test_core_orchestration_adversarial.py::test_resumed_scope_keeps_projects_whose_results_are_already_recorded` |
| F3 | major | `src/cmru/gate_snapshot.py` `open_gate_snapshot` | The host manifest's Run-Gate identity and anchors were validated, and refused on, before the code knew whether a snapshot record still existed. After `cmru gate-snapshots --remove --include-current` (the remedy that refusal prints), the next gate at the same HEAD refused again, or was bound to the removed snapshot's judge identity. Manifest problems are now raised only for a retained snapshot; a newly created one starts with no manifest judge or anchors. | `test_gate_snapshots_cli.py::test_a_fresh_gate_after_removal_is_not_bound_by_the_removed_snapshots_manifest`. The existing retained-snapshot refusal tests are unchanged. |
| F4 | minor | `src/cmru/changelog.py` `fold_unreleased_notes`, `src/cmru/release_notes.py` | An unclosed `<!--` inside `[Unreleased]` before the history marker: the fold silently deleted the text after it from the changelog and the release body. It now refuses before any change. | `test_unreleased_notes.py::test_an_unclosed_comment_in_pending_notes_refuses_the_fold_instead_of_dropping_notes` |
| F5 | minor | `src/cmru/gate_snapshot.py` `gate_lease_state` | A gate holder that exited but was not reaped (a zombie; this host has had zombie floods) still has its `/proc` start time, so it counted as a live lease and blocked removal and reruns for the 18 h TTL. A `Z` state is now dead. | `test_release_gate.py::test_lease_state_needs_positive_evidence_before_calling_a_holder_dead` (a real unreaped child via `waitid(WNOWAIT)`) |
| F6 | major (docs) | `docs/BOOTSTRAP-FROM-ZERO.md` | The five `CMRU_TESTER_*` resource inputs were inline prefixes on step 2's `build-image.sh` only. Steps 3 and 4 run `build-image.sh` again, and its `cmru tester-gate` preflight reads them only from the environment, so on a clean shell those steps refuse with missing configuration. They are now exported in the prerequisites block. | `test_bootstrap_from_zero_sequence.py::test_the_tester_resource_inputs_reach_every_tester_image_build` |
| F7 | minor (test gap) | `tests/test_release_end_to_end_real_git.py` | The feature-worktree e2e test did not prove the tag descends from the exact source commit, or that origin gained no ref except the generated candidate. | Assertions added to `test_feature_worktree_release_keeps_both_main_tips_unchanged_and_retains_a_pr_candidate` |
| F8 | minor (docs) | README, DESIGN-GUIDE, CONSUMERS, SPEC, SKILL, CHANGES | Feature-source `--resume` (source mode taken from the record, its refusals) was in no user guide. `--secret-file` was called "release-only", but `publish` accepts it. README lacked `gate-snapshots --include-current`. CHANGES lacked the new `gate-snapshots` verb and called the candidate branch "same-named". Each fixed in all three guides where it is a capability. The F1, F2, F4, F3 and F5 behavior is in CHANGES; F1 and F4 are also in CONSUMERS. | none new |

## Contract 1 (`--from-worktree`) — reviewed, no defect found

- `_preflight_feature_worktree_source` and `create_workspace(base=source_commit)` bind the
  candidate to the exact source commit. `write_release_source` and `read_release_source` refuse
  a record whose commit differs from the workspace base.
- The candidate branch is created under an empty-ref lease and refreshed only under an
  expected-old-value, ancestor-checked lease.
- In feature mode `finish_candidate` never promotes, the launcher never syncs local main, and
  the source branch is only fetched.
- The child refuses a mismatch between the source-mode flag and the record. Abandon requires
  the exact candidate tip to be in fetched `origin/main`.
- Change detection refuses a diverged latest tag instead of planning against it.

## Open findings, not fixed

- **BLOCKED (product decision): resume does not record or reconcile `--set-version`, `--minor`
  or `--major`.**
  - Where: `src/cmru/transaction.py` records only project names; the `cli.py` resume path does not check version flags.
  - Trigger: a pre-tag failure of a run started with `--set-version X`, then `cmru release --resume <candidate>` without it.
  - Consequence: the resume plans version Y, adds a second generated section, and tags Y. The
    curated `[Unreleased]` notes stay folded under the never-released X. This is the same
    class as the recorded nyxloom v1.0.0 `--resume` incident.
  - Decision needed (D-NNN): either record the version intent with the transaction and replay
    it, or refuse a resume whose re-planned version differs from the candidate's committed
    generated section.
  - Oracle: start with `--set-version 2.0.0` and notes, fail the gate, resume without the flag;
    expect a refusal or a 2.0.0 tag, and never another version.
- **Minor: `GitHubReleases.get_tag_commit` uses `/commits/<ref>`** (`src/cmru/release.py:153`,
  chosen in KI-84). That route also resolves a branch of the same name.
  - Consequence: a branch named exactly like a missing tag would let the OCI body handler
    POST a release, and GitHub would then create the tag. The origin tag is checked by
    `ls-remote` before the retry path, so the risk is low.
  - Fix direction: `/git/ref/tags/<tag>` plus peeling.
- **Minor: the snapshot tree comparison ignores `core.fileMode` and `text`/`eol`/`ident` attributes**
  (`gate_snapshot.py` `_verify_tracked_files_match_commit`). A consumer whose repo has
  `.gitattributes` EOL conversion, or `fileMode=false`, gets a false "tracked snapshot file
  differs" refusal. vbpub is unaffected.
- **Minor: `gate_snapshot._git` scrubs only `GIT_DIR` and `GIT_WORK_TREE`.** An ambient
  `GIT_COMMON_DIR`, `GIT_INDEX_FILE` or `GIT_OBJECT_DIRECTORY` could redirect the ref digest and
  status checks. `run_release_gate._hardened_git_environment` already scrubs them.
- **Minor: snapshot removal deletes Assay resume state before `remove_workspace` can refuse**
  (race-only). The R2 resume progress would be lost.
- **Minor: the release-controller "intact wheel" claim** (`release_controller.py`). Installed
  files are hashed for a launcher/child comparison but never checked against the wheel
  `RECORD`.
- **Minor: tester-image Run-Gate identity relies on image labels**
  (`tools/run_release_gate.py:772-779`); the image ID is pinned afterwards. **`oci-image-release`**
  publishes `repo:<version>` without checking the registry digest; this is a documented SPEC S4.7
  limit.
- **Minor: untagged projects record their result only after publish**, so a post-publish
  failure replays the publish on resume.
- **Minor (docs):**
  - The `--ref` deprecated alias outlives its "one release only" statement (`UPGRADING-6.0.md:37`).
  - "Candidate … gate evidence pending" wording in README, CONSUMERS, DESIGN-GUIDE and SKILL must be settled in the release commit.
  - Commit-scope "alias" forms (`scm_dist`, cwd basename, tag prefix) are defined only in CHANGES.
  - `test_docs_config_examples.py` parses TOML but never calls the shipped loader its docstring cites.

## Changed paths

- `cmru/src/cmru/cli.py`, `transaction.py`, `gate_snapshot.py`, `changelog.py`, `release_notes.py`,
  `skills/cmru-cli/SKILL.md`
- `cmru/tests/test_release_end_to_end_real_git.py`, `test_release_coverage_gaps.py`,
  `test_core_orchestration_adversarial.py`, `test_gate_snapshots_cli.py`, `test_release_gate.py`,
  `test_unreleased_notes.py`, `test_bootstrap_from_zero_sequence.py`
- `cmru/README.md`, `cmru/CHANGES.md`, `cmru/docs/{CONSUMERS,DESIGN-GUIDE,SPEC}.md`
- `docs/BOOTSTRAP-FROM-ZERO.md`
- this report

## Remaining for the controller

1. Run the full registered gates (coverage at 100% branch, and `gate-provisional`) on this
   commit through `cmru release`. The new F1 e2e test and the new zombie and removed-snapshot
   tests have never been executed.
2. Decide the BLOCKED version-intent finding (D-NNN) before relying on `--resume` after a
   `--set-version`, `--minor` or `--major` start. The 6.2.0 release itself uses `--minor`.
