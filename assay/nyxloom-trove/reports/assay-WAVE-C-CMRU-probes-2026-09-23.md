# Wave C CMRU remap probes — 2026-09-23

CMRU version: `5.4.2.dev262+ge434b293`. Commands ran from
`/workspaces/vbpub/.worktrees/assay-wave-c-controller`. Each command's full
stdout/stderr and captured command exit marker are transcribed from the
retained `/tmp/assay-wave-c-cmru-probe-{1..5}.log` files. The first three
commands each failed after creating a retained dry-run transaction. The
`--abandon` option then abandoned only the exact path supplied and continued
into a new dry-run, which failed with the same remap. The fourth command used
the wrong (nested) path while trying to clean the third transaction and did
not remove it. The fifth command used the exact root-level path, removed it,
and stopped before allocating another transaction because the supplied ref
was intentionally invalid.

These are bounded `--dry-run` probes. No release candidate was promoted. The
main checkout stayed at `db29266f8a006b22a30609a74de7645d1e4c50b7`; final
`git -C /workspaces/vbpub status --short --branch` was `## main...origin/main`.
The final worktree/ref check found none of the three transaction paths or
branches below.

## Probe 1 — default relative config

Command:

```text
cmru release assay --dry-run
```

Exit: `1` (`PROBE_EXIT=1`). Full output:

```text
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
[INFO] Release transaction cmru-release-20260923_232547-assay-bmlaxm: snapshot db29266f8a00 at /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232547-assay-bmlaxm
Traceback (most recent call last):
  File "/home/vscode/.venv/bin/cmru", line 6, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/vscode/.venv/lib/python3.14/site-packages/cmru/cli.py", line 2690, in main
    (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
                                                  ~~~~~~~~~~~^^^^^^^^^^
  File "/home/vscode/.venv/lib/python3.14/site-packages/cmru/cli.py", line 488, in load_config
    raise ValueError(
    ...<2 lines>...
    )
ValueError: assay: transaction project config is missing from the isolated worktree: /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232547-assay-bmlaxm/.worktrees/cmru-release-20260923_232547-assay-bmlaxm/assay/cmru.toml
[ERROR] Release candidate was not promoted; origin/main was left at the last fully completed project. The durable candidate branch cmru-release-20260923_232547-assay-bmlaxm was retained for inspection.
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
fatal: cannot force update the branch 'main' used by worktree at '/workspaces/vbpub'
[WARN] Could not sync local main automatically: updating the non-current local main ref failed; local main was left untouched. Inspect the ref and synchronize it manually.
[ERROR] Release transaction failed; retained /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232547-assay-bmlaxm on branch cmru-release-20260923_232547-assay-bmlaxm for inspection/resume.

PROBE_EXIT=1
```

## Probe 2 — worktree config path, abandoning probe 1

Command:

```text
cmru release assay --dry-run --config /workspaces/vbpub/.worktrees/assay-wave-c-controller/cmru.orchestration.toml --abandon /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232547-assay-bmlaxm
```

Exit: `1` (`PROBE_EXIT=1`). Full output:

```text
[INFO] Abandoned release attempt: cmru-release-20260923_232547-assay-bmlaxm
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
[INFO] Release transaction cmru-release-20260923_232615-assay-k8pmsu: snapshot db29266f8a00 at /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232615-assay-k8pmsu
Traceback (most recent call last):
  File "/home/vscode/.venv/bin/cmru", line 6, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/vscode/.venv/lib/python3.14/site-packages/cmru/cli.py", line 2690, in main
    (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
                                                  ~~~~~~~~~~~^^^^^^^^^^
  File "/home/vscode/.venv/lib/python3.14/site-packages/cmru/cli.py", line 488, in load_config
    raise ValueError(
    ...<2 lines>...
    )
ValueError: assay: transaction project config is missing from the isolated worktree: /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232615-assay-k8pmsu/.worktrees/cmru-release-20260923_232615-assay-k8pmsu/assay/cmru.toml
[ERROR] Release candidate was not promoted; origin/main was left at the last fully completed project. The durable candidate branch cmru-release-20260923_232615-assay-k8pmsu was retained for inspection.
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
fatal: cannot force update the branch 'main' used by worktree at '/workspaces/vbpub'
[WARN] Could not sync local main automatically: updating the non-current local main ref failed; local main was left untouched. Inspect the ref and synchronize it manually.
[ERROR] Release transaction failed; retained /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232615-assay-k8pmsu on branch cmru-release-20260923_232615-assay-k8pmsu for inspection/resume.

PROBE_EXIT=1
```

## Probe 3 — root config path, abandoning probe 2

Command:

```text
cmru release assay --dry-run --config /workspaces/vbpub/cmru.orchestration.toml --abandon /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232615-assay-k8pmsu
```

Exit: `1` (`PROBE_EXIT=1`). Full output:

```text
[INFO] Abandoned release attempt: cmru-release-20260923_232615-assay-k8pmsu
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
[INFO] Release transaction cmru-release-20260923_232641-assay-0o403o: snapshot db29266f8a00 at /workspaces/vbpub/.worktrees/cmru-release-20260923_232641-assay-0o403o
Traceback (most recent call last):
  File "/home/vscode/.venv/bin/cmru", line 6, in <module>
    sys.exit(main())
             ~~~~^^
  File "/home/vscode/.venv/lib/python3.14/site-packages/cmru/cli.py", line 2690, in main
    (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
                                                  ~~~~~~~~~~~^^^^^^^^^^
  File "/home/vscode/.venv/lib/python3.14/site-packages/cmru/cli.py", line 488, in load_config
    raise ValueError(
    ...<2 lines>...
    )
ValueError: assay: transaction project config is missing from the isolated worktree: /workspaces/vbpub/.worktrees/cmru-release-20260923_232641-assay-0o403o/.worktrees/cmru-release-20260923_232641-assay-0o403o/assay/cmru.toml
[ERROR] Release candidate was not promoted; origin/main was left at the last fully completed project. The durable candidate branch cmru-release-20260923_232641-assay-0o403o was retained for inspection.
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
[WARN] Could not sync local main automatically: the caller checkout is dirty (tracked or untracked changes, including ignored files and directories), so no rebase or rebase-abort was attempted and local main plus those files were left untouched. Commit or stash all changes (including ignored files with `git stash -a`), then run `git rebase origin/main` from the clean checkout.
[ERROR] Release transaction failed; retained /workspaces/vbpub/.worktrees/cmru-release-20260923_232641-assay-0o403o on branch cmru-release-20260923_232641-assay-0o403o for inspection/resume.

PROBE_EXIT=1
```

## Probe 4 — incorrect cleanup path (no removal)

Command:

```text
cmru release assay --dry-run --config /workspaces/vbpub/cmru.orchestration.toml --abandon /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232641-assay-0o403o --ref wave-c-abandon-only-invalid-ref
```

Exit: `1` (`PROBE_EXIT=1`). Full output:

```text
[ERROR] release worktree does not exist: /workspaces/vbpub/.worktrees/assay-wave-c-controller/.worktrees/cmru-release-20260923_232641-assay-0o403o

PROBE_EXIT=1
```

This was an operator path mistake. The transaction reported in Probe 3 was at
the root-level path `/workspaces/vbpub/.worktrees/cmru-release-20260923_232641-assay-0o403o`.

## Probe 5 — exact cleanup path

Command:

```text
cmru release assay --dry-run --config /workspaces/vbpub/cmru.orchestration.toml --abandon /workspaces/vbpub/.worktrees/cmru-release-20260923_232641-assay-0o403o --ref wave-c-abandon-only-invalid-ref
```

Exit: `1` (`PROBE_EXIT=1`). Full output:

```text
[INFO] Abandoned release attempt: cmru-release-20260923_232641-assay-0o403o
From https://github.com/volkb79-2/vbpub
 * branch              main       -> FETCH_HEAD
[ERROR] Cannot compare 'wave-c-abandon-only-invalid-ref' with origin/main; fetch/repair the ref, or pass a different --ref, before starting a release.

PROBE_EXIT=1
```

The invalid ref intentionally stops the `--abandon` call after it removes the
selected worktree/ref and before a fresh transaction is allocated.
