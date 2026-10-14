<!-- Persisted from resumable review session e9366a19-b064-4e2e-9ac3-a60b6b26c5eb. Requested route: Claude Opus 5.5, high effort. The review itself made no edits and ran no tests or gates. -->

# CMRU review: branch `cmru-release-fixes-20261013`, KI-78 plus the original A–G contract

**Bound to:** HEAD `72e05e3f3bacd340abe6cad90a8f6acf328ba686`. `git status --short` was empty at the start and the end. Merge-base with `main` is `aa41988e8`. I changed nothing and ran no tests, gates or releases. I also started no agents and wrote no file.

**Short answer:** most of A–G is done in code, but four things block a release:
- The C-1 and C-2 fixes are only partial (two high findings below).
- No registered gate has passed on any commit.
- The KI-78 code has never been run by any test.
- KI-78 has a post-publish design flaw that can duplicate or strand release notes.

**Scope:** I treated the pasted scope update as your instruction. I read the original intent in `jobs/cd05ec35/state.json`, the implementation report, the Opus readiness report, and the implementer's lane logs in `jobs/cd05ec35/tmp/`.

## What came from where

- **Inherited at checkpoint `91a4ab26a`:** the KI-74 snapshot design (`gate_snapshot.py`, already committed there), KI-69 protected release identity, KI-70, KI-52, the scope-aware bump in `version.py`, the canary ignore rule, and the `--require-live-snapshot` flag.
- **Sonnet's 11 commits, `3cf2e8f37..72e05e3f3`:**
  - C-1 to C-6 fixes, the `cmru gate-snapshots` verb, and the `ref-probe` and `installed-wheel-live` lanes.
  - `gate_lease_state` and the `libraries/worktree` orphan-cleanup guard.
  - KI-75, plus KI-59/KI-68 ported from `cmru-bugs62` (`_is_own_bump_commit` and `_dropped_hand_edits` are absent at the checkpoint and present at HEAD).
  - KI-78 (`07871df27`), and the follow-up that excludes the configured changelog path from change detection (`72e05e3f3`).

## Contract status A–G

| Item | Status | Evidence |
|---|---|---|
| **A · C-1** gate code bound to HEAD | **Partial** | Finding 1 |
| **A · C-2** ref check scope | **Partial** | False alarms are fixed. The live probe passed in `gate-3.log` and `gate-4.log`. Finding 2: refs the lane can actually reach are not policed. |
| **A · C-3** 100% coverage lane | **Done at `ea9ee1b93`, unproven at HEAD** | Passed inside gate attempt 3 (4556 passed). `07871df27` and `72e05e3f3` add code and tests that no lane has run; the report admits only `pyflakes` ran. |
| **A · C-4** lease and namespace safety | **Mostly done** | Foreign-namespace or unexpired leases are never reaped. One residual: finding 10. |
| **B · C-5** installed-wheel lanes | **Done, with a residual** | A 30-minute freshness check, not bound to the lease holder (finding 11). |
| **B · C-6 / C-7 / C-8** | **Done** | Cleanup verb added; canary ignore kept and tested; KI-52 text verified. |
| **B · D-1** | **Not done** | The decision is recorded in DESIGN-GUIDE. |
| **B · D-2 / D-3** | **Filed as KI-77** | The stated reason is wrong (finding 12). |
| **B · D-4** host capacity record | **Not done** | Slice files are not visible from the devcontainer. **Blocker** before the 7h mutation lane. |
| **C · KI-75** release bodies | **Implemented** | Refusals are tested and all three docs are updated. The "byte-identical" oracle is only weakly tested (finding 13). |
| **D · KI-58** | **Done** | `git diff 91a4ab26a HEAD -- run-gate-project/nyxloom-trove/reports/` is empty. The carry-forward is documented. KI-58's title and status are stale now that KI-78 exists. |
| **E · KI-59 / KI-68** | **Done** | Ported selectively; `cmru-bugs62` was not merged. |
| **F · gates** | **Open (blocker)** | No aggregate gate has passed on any commit. Attempt 3 at `ea9ee1b93` failed: ref-probe, installed-wheel-live, assay and coverage passed, then the mutation lane failed on its Assay R1 base. Attempt 4 at `670f99d46` was stopped by you, EXIT=2. Mutation and canary have never run. `--postpone-mutation` was correctly not used. |
| **G · commit and report** | **Partial** | Committed. The report's status line still names `07871df27` as the last code commit; `72e05e3f3` changed code after it. It correctly says it was not independently reviewed. |

**Oracles:** these are met: canary clean, cleanup repeatable, the killed-gate manifest rejected (via the 30-minute rule), and the KI-75 refusals. These are not met: "an untracked helper cannot alter a verdict" (finding 1), "a real lane changing a reachable ref fails the gate" (finding 2), "complete registered gate bound to the final commit" (F), and full coverage at HEAD.

## Confirmed defects

**1. HIGH — C-1 is only partly closed: a stray file can still change the gate verdict before the check runs.**

`cmru/tools/run_release_gate.py:5-18` imports `argparse`, `json`, `subprocess`, `tempfile` and other standard-library modules before `_assert_no_untracked_gate_code` runs (`:425`). The `gate` lane runs `python3 {worktree}/cmru/tools/run_release_gate.py` (`cmru/run-gate.toml:223`) with no `-I` or `-P` flag. Python therefore puts the script's own folder, `cmru/tools/`, first on the import path.

- **Trigger:** put an untracked `cmru/tools/json.py` containing `raise SystemExit(0)` in the checkout. It runs at import time, the gate lane exits 0, and the gate reports PASS without running any sub-lane.
- **Second gap:** every lane's host-side runner is `./run-gate.py`, launched from the caller's checkout (`:340`). That is a symlink to `run-gate-project/run-gate.py`. Python resolves the symlink, so the import path starts at `run-gate-project/`. That folder is not in `_GATE_CODE_ROOTS` (`:35`), and `_verify_source` only checks tracked files. An untracked shadow module there runs inside the process that decides every lane verdict.
- **Why the test misses it:** the existing test (`tests/test_release_gate.py:778`) plants `json.py` under `cmru/src`, and it calls the function in-process, so it cannot catch either path.
- **Repair:**
  - Start the gate with `python3 -I`, and start `run-gate.py` through `sys.executable -I`.
  - Add `run-gate-project/` and the `cmru/run-gate.py` symlink to the inventory.
  - Add a test that runs the script as a subprocess with shadow modules planted in `cmru/tools/` and in `run-gate-project/`.

**2. HIGH — C-2 narrowed the ref check below what lanes can actually reach.**

The protected set is only three refs: the snapshot branch, the source branch, and `refs/cmru-gate/<id>` (`gate_snapshot.py:291-306`). But the real tester containers mount the shared `.git` folder read-write. You can see this in the logged docker command in `gate-3.log` and `gate-4.log`: `-v /home/vb/volkb79-2/vbpub/.git:/workspaces/vbpub/.git`, with only `worktrees/`, `config`, `hooks` and `info` made read-only.

- **Effect:** a lane, including mutated code during the 7h mutation lane, can move `main`, delete or move release tags, or rewrite `packed-refs`, and the gate will not notice.
- **What the report says:** it defers this to "Run-Gate's mount hardening". No such hardening exists and no Run-Gate backlog item was filed.
- **Why this matters:** the contract said "changes to refs the lane can reach must be detected". Its scope rule required a `BLOCKED:` entry when a fix needs Run-Gate.
- **Repair:**
  - Record it as BLOCKED and file it with Run-Gate: mount the shared `.git` read-only, except `objects/` and the snapshot's own admin folder.
  - In the meantime, flag any move or deletion of an existing tag, and any non-fast-forward move of `main`, both of which are append-only by policy.

**3. MEDIUM — the KI-78 consume step can fail after the release is public, and that failure blocks promotion.**

`_consume_published_unreleased_notes` (`cli.py:3024`, `:4273`) runs after the publish and after `write_release_result`, but before `promote_workspace`.

- **Trigger, any of these:**
  - a commit hook rejects the commit (`run_local_git` runs hooks);
  - the changelog is a symlink, so `git add` of the unresolved path stages nothing and `commit` fails with check=True;
  - a write error.
- **Effect:** the child process fails.
  - The tag commit is never promoted and later projects in the run do not release.
  - `--resume` is refused ("publication or promotion is incomplete", `cli.py:3836`).
  - Fresh runs refuse with "tag AHEAD" (`version.py:535`).
  - If the file was written but not committed, the dirty worktree also trips `assert_resume_workspace_committed`.
- **Docs mismatch:** the KI-78 docs say "the existing post-publication recovery applies", but the recovery text in `_promotion_recovery` is never printed on this path.
- **Repair:** make the step non-fatal: restore the file, warn with the exact manual command, and still promote. Better, use the pre-tag design described under Design.

**4. MEDIUM — notes can be published and never consumed, so the next version publishes them again.**

- **`--no-build`, then a later `cmru publish` of that tag:** the handler appends `[Unreleased]`, nothing consumes it, `main` keeps it, and the next release carries it into a second version's body and history. Before this change, `main` refused a non-empty `[Unreleased]` for tagged releases (KI-30). That guard is now removed (`changelog.py:498`).
- **Promotion fails after a successful publish:** README (~486-488) and CONSUMERS (~878-884) describe `main` keeping the notes "for the next attempt" as a benefit. But the notes were already published, and a re-run of the same version is impossible.
- **Repair:** fold the notes into the version section before tagging (Design), or consume them when the release is promoted no matter which publish path ran.

**5. MEDIUM — direct `cmru publish` reads release notes from the mutable working tree.**

`handlers.py:71-73` falls back to `commit=None` when `CMRU_RELEASE_COMMIT` is unset (`run_project_step` removes it, `cli.py:358`) and there is no `--build-output`.

- **Trigger:** after a successful release, `main`'s changelog holds the consumed notes inside the version section plus new `[Unreleased]` notes for the next version. Re-publishing version X appends the next version's notes to X's body. Uncommitted local edits are published too.
- **Repair:** for a release version, read from `<prefix>v<version>^{commit}`, and refuse a working-tree read.

**6. MEDIUM — tagged non-GitHub publishers get "published" notes that were never published.**

Example: `scripts/cgroup-profiler/cmru.toml:40,65-68` has `git_tag = true` and pushes an image.

- **Effect:** consume runs anyway and adds the marker "unreleased notes published with this release", although no GitHub body exists. `--dry-run` also prints a body that will never be sent.
- **Contradicted docs:**
  - KI-78: "an image-only push has no GitHub body, so its `[Unreleased]` is neither carried nor consumed".
  - CONSUMERS (~897): "its notes stay in the changelog".
- **Repair:** consume only on positive evidence. For example, the CMRU handler writes a result record saying its body included the notes, and consume checks that record.

**7. LOW — a `## ` line inside a comment or code fence truncates the notes.**

`unreleased_span` and `extract_release_section` (`release_notes.py:70-91`, `:99-136`) end a section at any line starting with `## `. If one sits inside an HTML comment in `[Unreleased]`, the span stops mid-comment. The leftover unclosed `<!--` is not stripped, so the comment text is published and consume splices the file mid-comment. DESIGN-GUIDE claims fenced code survives byte for byte. No current changelog has such a line (checked across the repo). **Repair:** a comment- and fence-aware scanner, or refuse an unbalanced span.

**8. LOW — near-miss `[Unreleased]` headings are silently ignored.**

`## [unreleased]`, `## Unreleased`, or a second `## [Unreleased]` are neither published nor consumed. That contradicts the module's own "absence is never silent" rule. **Repair:** refuse these in prepare.

**9. LOW — change-detection message and exclusion scope.**

- After a consume commit, `_unchanged_reason` (`version.py:549`) says "no commits under `<paths>/` since `<tag>`", which is now false.
- Only each project's own changelog is excluded (`version.py:631-639`). If project A uses a custom changelog name inside project B's `paths`, A's consume commit schedules a release of B.
- **Repair:** reword the message, and exclude every configured changelog in the repository.

**10. LOW — "Git says prunable" is not cross-namespace evidence (C-4 residual).**

Git marks a worktree prunable by checking the path from the executor's own namespace. So `gate_snapshot.py:993` and `libraries/worktree/.../core.py:1232` still infer "gone" from local invisibility, which is the exact case Opus raised for host-path worktrees seen from the devcontainer. It is mitigated because the snapshot sits next to its source and is usually just as invisible, so it gets retained. **Repair:** require recorded namespace identity, or correct the code comment's claim.

**11. LOW — the live-snapshot proof is a time window (C-5 residual).**

`installed_wheel_smoke.py:120,164` accepts any manifest refreshed in the last 30 minutes, not one bound to the live lease holder. A SIGKILLed gate's manifest still passes `--require-live-snapshot` on that snapshot for up to 30 minutes.

**12. LOW — KI-77's stated blocker is wrong.**

KI-77 says a Run-Gate revision floor "needs a Run-Gate-side revision API". Run-Gate already has one: `./run-gate.py --version` prints `run-gate rev 68` (`run-gate.py:13912`), and each lane records `run_gate_revision`. A CMRU-side floor check fit inside this wave's scope.

**13. LOW — the tests do not exercise real transaction behaviour.**

- KI-78's sequence tests (`tests/test_unreleased_notes.py:226-365`) mock prepare, gate, git, tag, publish, promote, and the consume step itself.
- The KI-75 "byte-identical" test (`test_release_notes.py:484`) feeds the preview's own captured notes into the same builder. It never runs the handler path that reads the tag commit.
- Not tested with real git: consume then promote then next-release detection, resume after consume, multiple projects in one run, or telling GitHub publishers from other publishers.

**Docs bookkeeping:** `cmru/CHANGES.md`'s `[Unreleased]` comment still cites the removed KI-30 refusal. KI-58's title ("…will make their next tagged releases fail") no longer holds.

## Design tradeoff (not a defect by itself)

Moving the notes after publication creates findings 3, 4 and 6, plus a history problem: the tag's tree shows the notes as "Unreleased" while the GitHub body shows them as released.

**Simpler, safer approach:** fold `[Unreleased]` into the generated version section, inside a marked block, in the same pre-gate prepare commit.
- The tag tree then equals the published notes, and there is no post-publish commit.
- Any failure leaves `main` untouched, because the candidate is never promoted.
- `--no-build`, `cmru publish`, and resume all read one consistent section.
- Cost: KI-68 regeneration has to keep the marked block instead of refusing it as a hand edit.

## Checked and safe

- **Body ordering:** generic block, then the exact dated section, then the pending notes (`compose_release_body`).
- **Shared builder:** dry-run and the publisher use it.
- **Dry run:** restores the changelog bytes and runs in a disposable candidate.
- **Notes source inside a release:** the publisher reads the tag commit via `CMRU_RELEASE_COMMIT`.
- **Detection after consume:** a release followed by its consume commit reports the project as unchanged ("behind"), not "ahead".
- **Default changelog names:** the global `**/CHANGES.md` and `**/CHANGELOG.md` excludes keep cross-project consume commits from scheduling releases.
- **Release identity:** `CMRU_RELEASE_NOTES_SOURCE` and the release-identity keys cannot be declared by a project.
- **Empty and duplicate sections:** comment-only `[Unreleased]` consumes nothing; a missing, undated, duplicate or empty version section refuses before the gate or tag.
- **Path escape:** changelog paths outside the project refuse.
- **Partial multi-project runs:** an earlier project's promoted consume stays, and the later project replays on resume.

## Not covered

- Live GitHub behaviour.
- The `file:` and `counter` version strategies, against the changelog heading version.
- The internals of the `mutation_campaign.py` and `tester-unified` changes.
- Run-Gate source, beyond the mount argv and `--version`.
- Anything that needs execution: the coverage result at HEAD, and the KI-78 test outcomes.

**Next step:** after you lift the gate hold, run `cd cmru && ./run-gate.py coverage` and then the aggregate `./run-gate.py gate` on the final commit. Fix findings 1 and 2 first, or record them as BLOCKED, and decide on the KI-78 design before any CMRU release.
