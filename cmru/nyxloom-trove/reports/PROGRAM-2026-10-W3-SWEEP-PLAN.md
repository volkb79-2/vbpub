# W3 sweep plan: KI-35 / REL-09 origin `cmru-release-*` branches (PLAN ONLY)

Prepared 2026-10-06 in worktree `cmru-w3-prep` (base `90193c4d3`). **Nothing was deleted, pushed or
modified remotely.** Every command below is for the controller to run after review.

## Findings that change the brief's premise

- The brief expects about 25 origin branches. `git ls-remote origin 'refs/heads/cmru-release-*'` returns **16** (the rest of the
  earlier 24 retained transactions were abandoned in the 2026-09-28 cleanup, KI-35 "Observed").
- **None has a local worktree or a local branch.** `git worktree list` (102 entries) has no `cmru-release-*`/`cmru-build-*` entry;
  `git for-each-ref` has no local `cmru-release-*`; `cmru worktrees --json` prints `[]`. All 16 are **origin-only**.
- Before this package `cmru abandon BRANCH` could not touch an origin-only branch (it only matched local worktrees: "no exact managed
  CMRU build or release branch"). W3-PREP adds that path (see "Code" below); without it the only route is raw `git push --delete`.
- **`xm4okg` is already gone.** `cmru-release-20261003_061427-cmru-xm4okg` has no origin ref, no local ref, no worktree and no sidecar
  (`git ls-remote origin '*xm4okg*'` empty; `ls .git/cmru-release-scopes | grep xm4` empty). The `abandon-dry.txt` that named it is
  stale. **No action needed for xm4okg.**

## Method (read-only)

`git ls-remote origin 'refs/heads/cmru-release-*'`; `git for-each-ref`; `git worktree list`; `cmru worktrees --json`; for each tip:
`git log -1`, `git merge-base --is-ancestor <tip> origin/main`, `git cherry origin/main <tip>`, `git tag --contains <tip>`;
sidecars under `/workspaces/vbpub/.git/cmru-release-scopes/`. Then this tree's `cmru abandon <branch> --dry-run` on every ref
(scratchpad `w3prep-sweep-dry.txt`): **12 reported "Retire ... commits not on origin/main: 0" (exit 0), 4 were "Withheld" (exit 4)**.
`origin/main` at planning time: `75a6385396f4a3e99e0e63de0c1a27a742f48901`.

Classes (the three the brief asks for, with one refinement):
- **published**: the tip is the release-inputs commit (`chore(<p>): prepare release inputs`) that is on `origin/main`, and a release
  tag of that project contains it.
- **abandoned pre-tag**: the tip is a plain snapshot of `main` (a merge commit) with no release-inputs commit on top; nothing was
  prepared or released from it. Every commit is already on `origin/main`.
- **ambiguous**: the tip holds a release-inputs commit that never reached `origin/main` (modern-debian-tools publishes image tags
  to GHCR, not git tags, so a git tag cannot prove or disprove publication).

**Safety criterion for "safe to delete":** every commit on the branch is already on `origin/main`, so deleting the ref loses no
source; origin/main, tags and published assets are never touched.

## Per-ref table

Retire command for the 6.0 grammar (needs the 6.0 `cmru` of this tree; the installed 5.5 cannot do it):
`cmru abandon <BRANCH> --dry-run` first, then `cmru abandon <BRANCH> --yes`. Raw-git fallback (also lease-protected, equals what
`abandon` runs): `git push --force-with-lease=refs/heads/<BRANCH>:<TIP> origin :refs/heads/<BRANCH>`.
Branch names below omit the `cmru-release-` prefix; the full branch is `cmru-release-<name>`.

| # | name | tip | class | evidence | safe to delete |
|---|---|---|---|---|---|
| 1 | `20260913_030829-all-97ede827` | `b8e942ea7f00` | published (tls-edge) | tip "chore(tls-edge): prepare release inputs", ancestor of main, contained in `tls-edge-v2.0.0`; dry-run: 0 commits not on main | yes |
| 2 | `20260914_112138-modern-debian-tools-python-debug-62d21723` | `da2ba041d4b3` | abandoned pre-tag | tip "Merge MDT release flow fix" (a main snapshot), ancestor of main; no release-inputs commit; dry-run: 0 | yes |
| 3 | `20260916_235834-all-8f857070` | `3d40d361a756` | published (nyxloom) | tip "chore(nyxloom): prepare release inputs", ancestor of main, contained in `nyxloom-v0.6.0`; dry-run: 0 | yes |
| 4 | `20260917_004929-all-2d088b95` | `8f9f917d98ae` | abandoned pre-tag | tip "Merge MDT dependency and VM harness hardening", ancestor of main; has sidecars (`.json .progress .backup-pushed`, scope `[modern-debian-tools-python-debug, run-gate-project]`); dry-run: 0 | yes (also removes its 3 sidecars) |
| 5 | `20260917_011930-all-b723925b` | `b2e436854dfa` | abandoned pre-tag | tip "Merge MDT venv reconciliation fix", ancestor of main; dry-run: 0 | yes |
| 6 | `20260917_014107-all-bec1ed5d` | `b2e436854dfa` | abandoned pre-tag | same tip as #5; dry-run: 0 | yes |
| 7 | `20260917_023826-all-a3cdc55d` | `d7d53a8a7f0d` | abandoned pre-tag | tip "Merge branch 'debian-install-vm-review-fixes-20260917'", ancestor of main; dry-run: 0 | yes |
| 8 | `20260917_052243-all-5f46c4a8` | `1cdbde6de5d6` | abandoned pre-tag | tip "Merge branch 'debian-install-vm-doc-cleanup-20260917'", ancestor of main; dry-run: 0 | yes |
| 9 | `20260917_055914-all-1cc00202` | `f96a333820b8` | abandoned pre-tag | tip "Merge branch 'mdt-zstd-cache-fix-20260917'", ancestor of main; dry-run: 0 | yes |
| 10 | `20260919_170629-ciu-cmru-assay-topos-nyxloom-modern-debian-tools-python-debug-pwmcp-tls-edge-run-gate-project-a0a6bbdc` | `c22fa2f40edd` | published (multi-project) | tip "chore(cmru): prepare release inputs", ancestor of main, contained in `cmru-v5.4.1`; KI-35 records it published assay-v6.5.0; dry-run: 0 | yes |
| 11 | `20260920_004430-ciu-cmru-assay-topos-nyxloom-modern-debian-tools-python-debug-pwmcp-tls-edge-run-gate-project-a9e7fdd0` | `304471a3489a` | ambiguous | 1 commit not on main: "chore(modern-debian-tools-python-debug): prepare release inputs" (CHANGES `source-467a889f70e5`, manifests `trixie-py3.14-php8.5-20260920.md`); `git cherry` `+` (not on main); no tag contains it | NO, hold (see below) |
| 12 | `20260920_141731-modern-debian-tools-python-debug-53deb8cf` | `467a889f70e5` | abandoned pre-tag | tip "merge reviewed estate integration and release fixes", ancestor of main; dry-run: 0 | yes |
| 13 | `20260920_143231-modern-debian-tools-python-debug-ba6a2670` | `3665f2d1c628` | ambiguous | 1 commit not on main: MDT "prepare release inputs" (`source-686f3d77c283`, `...-20260920.md`); `git cherry` `+` | NO, hold |
| 14 | `20260925_203056-assay-tc7qb1` | `f8999975bdc5` | published (assay) | tip "chore(assay): prepare release inputs", ancestor of main, contained in `assay-v7.1.0`; KI-35 records assay-v7.1.0; dry-run: 0 | yes |
| 15 | `20260927_000642-modern-debian-tools-python-debug-9rjl8c` | `6821515d5c40` | abandoned pre-tag (content already on main) | 2 commits not on main by ancestry, but `git cherry origin/main <tip>` shows both as `-` (patch-equivalent in main): "fix(mdt): make build inputs readable under release umask" is `6981838f1` on main; the other is `09bc62646`. The tool WITHHOLDS it (ancestry rule) | yes by content; needs the raw-git command (the tool refuses) |
| 16 | `20260929_235325-modern-debian-tools-python-debug-hf7ycz` | `a4bb4168d95a` | ambiguous | 1 commit not on main: MDT "prepare release inputs" (`source-1fc1a96183db`, `...-20260929.md`); `git cherry` `+`; main has its own newer MDT release sections (`source-ec95f86b1edd`, `source-998a43552c36`) | NO, hold |

### Execution plan for the controller

**Safe set: 12 refs (#1-10, 12, 14)** (published or abandoned pre-tag with every commit on main). With the 6.0 `cmru` from the
integration tree, run from the vbpub repo root, one at a time (the library `--yes` skips the prompt; the dry run first is mandatory):

```sh
cmru abandon <BRANCH> --dry-run     # expect "commits not on origin/main: 0", exit 0
cmru abandon <BRANCH> --yes
```

(The 12 dry-runs already exited 0 on this tree.) If the installed `cmru` is still 5.5, use the raw-git lease form with the tip from
the table (full oids: `git ls-remote origin 'refs/heads/cmru-release-*'`), for example:
`git push --force-with-lease=refs/heads/cmru-release-20260917_004929-all-2d088b95:8f9f917d98ae3ae39e4627f8df291f875eb65dd3 origin :refs/heads/cmru-release-20260917_004929-all-2d088b95`
(then remove sidecar tokens by hand, see below).

**#15 (`9rjl8c`)**: not deletable with `cmru abandon` (2 ancestry-unique commits). Equivalent by content (verified with `git cherry`).
If the controller accepts that, delete with the raw lease command above (tip `6821515d5c40285950f7a64ef9b317b4169fe26f`).

**#11, #13, #16 (ambiguous; do NOT delete yet)**: each is an unpromoted generated release-inputs commit (CHANGES section + package
manifests) for modern-debian-tools-python-debug. They are the only copies of those generated manifests. Whether the corresponding
images were pushed to GHCR cannot be proven from git. Before deleting, check GHCR for the image tags dated `20260920` (#11, #13)
and `20260929` (#16); if the images exist the manifests are the only record, so archive them
(`git show <tip> > ...`, or keep the branch); if no image exists they are plain abandoned candidates and
`git push --force-with-lease=refs/heads/<BRANCH>:<TIP> origin :refs/heads/<BRANCH>` is correct (the tool refuses them by design:
exit 4 "Withheld ... N commit(s) are not on origin/main").

## Orphan sidecars (no supported removal)

`/workspaces/vbpub/.git/cmru-release-scopes/` holds 44 files for 18 transaction tokens. Only `20260917_004929-all-2d088b95` (#4) still has
an origin branch (removed by the retire command). The other 17 tokens have **no origin ref, no local branch and no worktree**:
`20260819_183949-cmru-cf770d19 20260823_024403-ciu-68c28a8f 20260823_024421-cmru-e63e8d15 20260823_030507-cmru-f382461e
20260823_045259-cmru-41d00968 20260825_191817-assay-6e8ca61d 20260917_001743-all-06e898e7 20260917_011831-all-79440bff
20260919_192057-ciu-cmru-assay-topos-nyxloom-modern-debian-tools-python-debug-pwmcp-tls-edge-run-gate-project-51a5deed
5bfd4fc21729 609462a96195 7ac5979ed559 8dd6ba15c58a 916fc24f77f8 a2ea21f41d1f ed51a0565536 ee0f43844374`.
They are small state files (`.json .progress .backup-pushed ...`) with no live transaction to attach to. `cmru abandon` removes sidecars only
as part of retiring a transaction it can see, so these have no cmru command; the manual route is `rm` of the exact
`<token>.*` files inside that directory after the controller agrees. This is a remaining KI-35 gap (not implemented: the verb grammar
would need a new target form and the catalog/SPEC regeneration); recorded for the controller to decide between a manual `rm` and a follow-up.

## Code added for KI-35 (this package)

`transaction.inspect_remote_candidate` / `transaction.retire_remote_candidate` / `RemoteCandidate`, and `cmru abandon BRANCH` falling
through to them when no local worktree matches a `cmru-release-*` name. Refuses (exit 4) when any commit is not on origin/main; deletes with
`--force-with-lease=<ref>:<oid>`, reads the deletion back, removes the transaction's sidecars; never touches main, tags or assets.
Tests `tests/test_w3_prep_remote_retire.py` (real bare origin + clone). Docs: `docs/RELEASE-TRANSACTIONS.md` "Retiring an origin-only candidate".
Not done: KI-35 wanted items 2 (a per-fact "what is missing" report for ambiguous LOCAL worktrees) and 3 (whether a successful release removes its own worktree).
