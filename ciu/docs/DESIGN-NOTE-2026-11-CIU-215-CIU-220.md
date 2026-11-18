# Design note 2026-11: CIU-215 (git common-dir mount) and CIU-220 (bounded subprocess readers)

Status (2026-11-18): Decision 1 decided; Decision 2 recommended, pending the operator's answer to its
open question. Implementation not started. Base: `main` at `db8c4321a`.
Every code reference below was read on that commit. The cmru reference is
`cmru/src/cmru/runner.py` on branch `cmru-20261118_045211-ki175-dist-dir-20261118` (`5e2a2bacc`).

---

## Decision 1 — CIU-215: CIU 7 injects the git common-dir mount for services that opt in, per stack

### Decision

A stack opts in with one table in its stack config:

```toml
[<stack root key>.linked_worktree]
git_common_dir = "ro"        # required in the table: "ro" | "rw"
# services = ["test-runner-py311"]   # optional; default: every service of this stack
```

In a **linked worktree only**, `ciu up`/`ciu render` writes `.ciu/ciu.compose.gitdir.yml`. It is the
second engine-side fragment; the ownership fragment is the first, and the secrets/governance overlay comes
from `composefile.py`. It is merged as the last `-f`, so at most it is the fourth `-f` file:
`ciu.compose.yml`, then the overlay, then the ownership fragment, then this one. In a linked but unmanaged checkout, which has no
ownership labels, it can be the only extra `-f`. Each bind is long-form, with the source in the daemon
namespace, the target exactly as the checkout's gitfile names it, and `bind: {create_host_path: false}`, so
Docker never invents an empty dir (CIU-14). The set of binds per selected service is in "Mount layout"
below.

In a primary checkout nothing is written, the file arguments are unchanged and `ciu.compose.yml` is
byte-identical whether or not the table exists. A stack without the table gets nothing anywhere.

### Options considered

| | option | verdict |
|---|---|---|
| A | Export the common dir (logical and physical) as `[ciu.instance.generated]` facts; the project template writes the mount itself | rejected as the mechanism: every project still copies the same block, and the facts file of every primary checkout changes (schema bump) for a feature most stacks never use |
| B | Opt-in declaration that CIU acts on (this decision; a stack-config table rather than a compose `x-ciu-*` field or label) | **chosen** |
| C | Automatic injection for every service that binds the worktree root | rejected: not provably safe (below) |

Why the declaration is a stack-config table and not an `x-` field or label in the compose template: a
field in the template also appears in a primary checkout's rendered output, which breaks byte identity
after adoption. A label also lands on the container. The table is read by `ciu check` like
`governance`. Exec runners already live in stacks of their own (netcup-api-filter `tooling/test-runner`,
dstdns `tools/test-runner`), so "every service of the stack" is the right default. `services` exists
for a mixed stack and does not force a project whose service names come from a Jinja loop to list them.

Why C is unsafe: (1) it adds a mount, rw in the v8 wording, to services that never asked for one, such as
an app container that binds the source read-only; (2) "binds the worktree root" cannot be detected
reliably. The rendered source is still `${PHYSICAL_REPO_ROOT:?...}`, and a service can mount the checkout
at another target (`/app`) and still need the common dir, because the gitfile path is absolute; (3) tools
in the image start to see a working git (version stamping from `git describe`, for example), which
changes behaviour without anyone deciding it.

### Facts and where they come from (derive, never configure)

- Linked-worktree test: `deploy._is_linked_git_worktree(repo_root)` (`git-dir != git-common-dir`). Move it to
  `worktree.py` as `is_linked_git_worktree` and have `deploy.py` import it, so the engine does not import
  `deploy`.
- **Target (logical common dir)**: read `<git worktree top>/.git` (`gitdir: <admin>`), read `<admin>/commondir`
  and join them lexically (`normpath`, no `resolve()`). A relative gitfile (`worktree.useRelativePaths`) is
  joined against the checkout's logical path. This is S16.4.9's derivation. Cross-check:
  `realpath(target) == realpath(git rev-parse --path-format=absolute --git-common-dir)`; if they differ,
  refuse with both paths named. The git top comes from `shared.discover_git_context`, the shared library
  reached through `worktree._shared_worktree()`, because a CIU root can sit below the git top, as `ciu/`
  does in vbpub.
- **Source (physical common dir)**: `worktree._physical_workspace_target(repo_root, target)`, which
  translates through the primary checkout's physical root (`primary_worktree_root` +
  `workspace_env._detect_physical_repo_root`). Never `stat` it locally (AGENTS.md "Defaults and fallbacks").
  A common dir outside the primary checkout (`--separate-git-dir`) passes through unchanged, which is right
  only when the daemon sees the same path. This is a documented limit, not a case to guess around.

### De-duplication by container path

For each selected service, parse the rendered `volumes`: the long form reads `target`; the short form
splits on `:` after skipping balanced `${...}` spans, because both known workarounds use a source like
`${PHYSICAL_REPO_ROOT:?required host path — source ciu.env}/../../.git` that contains a colon. CIU skips the
service, with one INFO line naming it, when an existing target **equals the common dir or is an ancestor of
it** (a service that mounts the whole primary checkout already sees `.git`). The template's own mount wins:
its mode is not changed. Compose would merge an equal target anyway; the explicit skip keeps the
project's mode and makes the skip visible.

### Mount layout (adopts Run-Gate's RG-NARROW/RG-86 layout) and the recommended value `ro`

`git_common_dir` is required, so there is no default. The value the docs recommend is `ro`.

Run-Gate already defines the stricter layout for its container lanes (`run-gate-project/run_gate.py`:
`container_mount_flags` ~15853, `git_worktrees_mount_flags` ~15770, `git_hook_info_mount_flags` ~15788,
`git_worktree_pointer_mount_flags`, `git_config_overlay_flags` ~15716). CIU 7 uses the same layout and does
not invent a third one. The sanitizer (`sanitize_git_config_entries`, `strip_url_userinfo`,
`_nested_git_config_files`, `_MAX_GIT_OVERLAYS`) is copied and adapted into ciu, as S16.1.1 does for the
v8 port, because ciu cannot import run-gate. A parity test feeds both implementations the same fixtures.

| path under `<common>` | `"rw"` | `"ro"` |
|---|---|---|
| `<common>` itself | rw | ro |
| `worktrees/` (real contents) | ro (keeps `gc` aware of siblings; `git worktree add`/`prune` hit EROFS) | ro (inherited) |
| `worktrees/<this checkout>/` (own admin dir) | rw on top (index, HEAD, in-lane commits) | ro (inherited) |
| `hooks/` | an empty private dir, ro | real contents, ro |
| `info/` | ro, real contents | ro (inherited) |
| `<checkout>/.git`, `worktrees/<this>/gitdir`, `worktrees/<this>/commondir` | ro | ro (inherited) |
| `config`, `config.worktree`, `worktrees/*/config.worktree`, every `config`/`config.worktree` under `modules/` | sanitized copy, ro | sanitized copy, ro |

The sanitized copies and the empty hooks dir live under `.ciu/gitdir/` beside the fragment. Every
`ciu up` rewrites them with `os.replace`; `reset_service` removes them. A running container keeps the copy
it was created with: a rewritten source file under the same bind path keeps the same compose config hash,
so the container keeps the old inode until it is recreated. A change to `<common>/config` (a new remote,
for example) reaches the runner at the runner's next recreate. This is a documented limit.

Two properties of that placement, both stated rather than hidden:

- **Location.** The copies and the empty hooks dir live under `.ciu/gitdir/` inside the checkout, which the
  runner mounts. Run-Gate instead uses a per-run 0700 private dir outside every mount
  (`_make_private_dir`, `run_gate.py` ~15655). A runner can therefore rewrite those source files through
  the checkout mount. This is accepted: the copies are tokenless (sanitized), and host git reads the real
  hooks and config, never these copies, so a rewritten copy affects only the runner's own view.
- **Namespace.** The bind sources need the same daemon-namespace translation as the common dir. The config
  copies and the hooks dir are paths under the logical checkout, so each source is translated to its
  physical path (`worktree._physical_workspace_target`) before it goes into the fragment, exactly like
  `<common>` itself, and is never `stat`ed locally as a physical path.

**What each mode exposes.** A flat mount, even `ro`, would expose `<common>/config` and submodule
configs. Remote URLs there can carry tokens, which is why both modes get the sanitized overlay. Both modes
show every sibling worktree's admin dir (HEAD, index, logs: branch names and paths, no credentials). That
matches a primary checkout's runner, which sees all of them, and run-gate gave up hiding siblings in RG-86
round 4 because hiding them let `gc --prune=now` delete objects only the siblings referenced. A flat `rw`
would let a runner plant hooks, edit config, rewrite the gitfile pointers or run `git worktree prune` on
siblings. The `rw` layout above closes those holes and keeps object writes and in-lane commits, as
Run-Gate does.

**What `ro` means.** Git inside the runner is read-only. `add`, `commit`, `checkout`, `stash` and anything
else that takes `<common>/worktrees/<name>/index.lock` fail with EROFS. `status`, `diff`, `log`,
`describe` and `rev-parse` work, and a plain `git status` silently skips its optional index refresh (the
reviewer verified this with bwrap, 2026-11-18).

**Why `ro` is enough for the known lanes (verified in code).**

- Assay builds its repository snapshot as a private repository seeded by `git pack-objects --stdout`
  (`assay/src/assay/git.py` ~1491, the P22 substrate, `487deaf14`, 2026-08-10). It refuses a snapshot that
  references the source or depends on alternates (`isolation.py` ~1741). Its git calls are `rev-parse`,
  `config --list -z` (`git.py` ~1750), `pack-objects`, `cat-file`, `ls-tree`, `rev-list`, `merge-base`,
  `diff`, `ls-files`, `check-ignore` and `status`, under `GIT_OPTIONAL_LOCKS=0`/`--no-optional-locks`. The
  `write-tree`/`read-tree`/`update-index`/`hash-object` calls run in the private snapshot, not in the
  source. There is no `git worktree add` in `assay/src`, and `git log -G` finds none in the history of
  `isolation.py`/`git.py`.
- Run-Gate reads only: `rev-parse`, `status --porcelain`, `check-ignore`, `diff --quiet`, `merge-base`,
  `symbolic-ref`, `describe` and `config --file --list -z`. Its plain `git status --porcelain`
  (`run_gate.py` 5635, 5776, 15251) runs without `--no-optional-locks` (`host_git_environment()` does not
  set `GIT_OPTIONAL_LOCKS`). That is harmless under `ro`, because the refresh write is optional and skipped.

**The contrary evidence and its status.** dstdns `tools/test-runner/ciu.compose.yml.j2` lines 115-123
(`dstdns@7a2c7b9c`, 2026-09-03) records `:ro` measured on lane `p128_persister_lineage`
(`snapshot_selection = "repository"`) as a bare ERROR/GIT_FAILED. The RG-NARROW report
(`run-gate-project/nyxloom-trove/reports/run-gate-RG-NARROW-2026-10-REPORT.md` line 113) found no
`git worktree add` (the snapshot then used `clone --no-local`) and asks for the S16.4.9 premise to be
re-checked.

Assay history does not corroborate either account of the snapshot. `git log -G` over `assay/src` finds no
clone-based lane snapshot and no `worktree add`; the only `--no-local` hits are the P24 self-hosting build
(`c7ff15a1c`, `c16a74367`), which is not the lane snapshot. The P22 pack-stream snapshot (`487deaf14`,
2026-08-10) already existed at the 2026-09-03 measurement. The RG-NARROW report's `clone --no-local`
remark is that report's own assertion and is not corroborated by assay history.

The measurement is therefore **unproven against current assay, not shown superseded**, and its cause was
never recorded. Which assay version produced the failure is unknown: dstdns today vendors a pinned judge
(`tools/assay/assay-12.0.0.pyz`, floor `assay>=12.0.0` in `requirements-legacy.txt`), but the pin in force
on 2026-09-03 was not checked. The `git worktree add` explanation in the dstdns comment fits neither the
pack-stream code in this repo nor anything in its history. Live probe 10, on both the netcup-api-filter
lane and the dstdns `p128_persister_lineage` lane, has to settle it. `ro` stays the recommendation only
once probe 10 passes on that dstdns lane. If it fails, record the cause and recommend `rw`.

### Cost to consumers

None unless they opt in. An adopter adds two lines to the runner stack's config and deletes its
hand-written `{% if '/.worktrees/' in ciu.repo_root %}` block. That block is also fragile: `../../.git`
assumes exactly `.worktrees/<name>`, so a branch name with `/` breaks it. Until the block is deleted, the
de-duplication keeps it working unchanged (and keeps its `rw`).

### Change list

- `ciu/src/ciu/worktree.py`: `is_linked_git_worktree` (moved from `deploy.py`), and `git_common_dir_mount(repo_root) -> GitMountPlan` (logical and physical common dir, own admin dir, pointer files) with the gitfile/commondir derivation and the cross-check. It uses `shared.discover_git_context` via `_shared_worktree()`.
- `ciu/src/ciu/gitconfig_sanitize.py` (new): the copy and adaptation of Run-Gate's sanitizer and nested-config walk, with the same cap.
- `ciu/src/ciu/config_model.py`: `validate_linked_worktree_table(stack_root_table)` with closed keys `git_common_dir` (`ro|rw`, required) and `services` (list of strings), refusing unknown keys and values (`[CIU-215] ...`).
- `ciu/src/ciu/engine.py`: `GITDIR_OVERLAY_NAME = "ciu.compose.gitdir.yml"`; `write_gitdir_overlay(stack_dir, rendered_compose, table, mount)`, a sibling of `write_ownership_overlay`, with the volume parser and the de-duplication; a call in `main_execution` Step 15 after the ownership fragment, appended to `file_args`; leak-scanned like the other fragments; an unknown name in `services` refuses at render. Add the fragment to `reset_service`'s `down -f` set, and add it and `.ciu/gitdir/` to the removal targets (engine.py ~1193-1295). The shipped-stack path (engine.py ~2800, no rendered template) refuses the table, so the key is never silently ignored.
- `ciu/src/ciu/oneshot.py` `_compose_file_args` (~720-747) passes the gitdir fragment through unchanged. It copies only the ownership fragment to its private per-run dir. The gitdir fragment's content is a pure function of the checkout and the stack config, and it is replaced atomically, so a concurrent `ciu up` cannot change it under a one-shot. `PreparedStack` needs no new field because `file_args` already carries the fragment.
- `ciu/src/ciu/deploy.py`: the preflight/`ciu check` (beside the governance shape check, ~3889) reports an invalid table; `_is_linked_git_worktree` becomes an import.
- Docs, in the same change: `ciu/docs/SPEC.md` (new subsection beside S16.9 ownership labels), `CONFIG.md` (the table), `CONSUMERS.md` (a block to paste, plus "delete your hand-written block"), `DESIGN-GUIDE.md` (why opt-in, why ro, why not automatic), and a CHANGES entry.

### Tests that must exist

1. A primary checkout with the table: `ciu.compose.yml` is byte-identical to the render without it, no `ciu.compose.gitdir.yml` exists, and `file_args` is unchanged.
2. A linked worktree without the table: no fragment.
3. A linked worktree with `"ro"`, using a fake physical root that differs from the logical one: every stack service gets the `ro` column of the layout table (common dir with physical source, gitfile-derived target, `read_only: true`, `create_host_path: false`, plus the sanitized config overlays). In a managed checkout the fragment follows the ownership fragment in `file_args`; in a linked but unmanaged checkout (no ownership labels) it is the only extra `-f` after the overlay.
4. `"rw"` produces exactly the `rw` column: `worktrees/` ro with the own admin dir rw on top, the empty ro hooks dir, `info/` and the three pointer files ro, and sanitized config overlays, including nested `modules/` configs and sibling `config.worktree`. More files than the cap refuse. No credential form survives in any file under `.ciu/gitdir/`; the parity test runs with Run-Gate's fixtures. `services` narrows the selection; an unknown service name refuses with the name in the message.
5. De-duplication: the literal netcup-api-filter short-form line (a colon inside `${...}`) is detected and that service is skipped with INFO; an ancestor target is skipped; a long-form equal target is skipped.
6. Derivation: a relative gitfile is joined lexically; a commondir that disagrees with `git rev-parse --git-common-dir` refuses; a CIU root below the git top uses the top's `.git`.
7. An invalid value, an unknown key or a missing `git_common_dir` refuses at render and in `ciu check`; the table on a shipped stack refuses.
8. `reset_service` passes the fragment to `down` and removes it.
9. Docs oracles (AGENTS.md): the CONSUMERS example parses with the shipped loader, and `ro`/`rw` appear in the docs.
10. **Live acceptance probe** (the CIU-215 close-when, through the registered lane path). Two runs, each with the project's hand-written block deleted and `git_common_dir = "ro"`. In each, `git rev-parse HEAD` succeeds, `git status` prints no error, `git commit` fails with EROFS, and the named lane reaches a verdict:
    - a netcup-api-filter linked worktree, with one `snapshot_selection = "repository"` assay lane;
    - a dstdns linked worktree, with lane `p128_persister_lineage`, which settles the contrary measurement.

    If the dstdns lane fails under `ro`, record the cause and repeat with `"rw"`. In both runs, check the `rw` layout once with in-lane commits.

### Migration and compatibility

Additive minor release of CIU 7 (the next 7.x). Primary checkouts and non-adopting stacks are unchanged,
and projects with the hand-written block keep working through the de-duplication. Follow-ups: the
netcup-api-filter and dstdns backlogs (dstdns via its own backlog, cross-repo annotation) delete their
blocks after adopting. v8: S6.2.4 derives the service from `exec_in` and needs no table, so the v8
migration drops `[<stack>.linked_worktree]`. **OPEN QUESTION (v8 spec, not blocking CIU 7):** S16.4.9 and
S6.2.4 mandate a flat `rw` on the `git worktree add` rationale, which cannot be reproduced from current
code. Recommendation: once probe 10 passes on both lanes, amend v8 to `ro`, plus an opt-in `rw` with the
RG-NARROW/RG-86 layout above, which Run-Gate already uses.

---

## Decision 2 — CIU-220 / cmru KI-178 items 3 and 4: one bounded subprocess reader (recommended: cli-extended, pending operator)

### Recommendation (pending the open question below)

The helper becomes a new module **`cli_extended.procio`** in `libraries/cli-extended` (stdlib-only, like
the rest of the library), ported from cmru's landed `runner.py` code. cmru, ciu, nyxloom and mdt-cockpit
`build-push.py` call it, and none of them keeps a private copy.

### Options considered

| | option | verdict |
|---|---|---|
| A | `cli_extended.procio` | **chosen**: no new dependency edge, because every consumer already has it (below); "clean cancellation" is already in cli-extended's stated scope (README) |
| B | A copy per tool plus a conformance test | rejected: four copies drift (the reason CX-D1 un-vendored cli-extended), and a cross-tool conformance test still needs a shared corpus, which amounts to a library |
| C | A new small library (e.g. `libraries/procwatch`) | rejected for now: a new release stream, a new `wheels.list` entry, three new pyproject edges and the get.py install ordering (KI-51), all for about 300 lines |

The dependency graph, verified: `ciu/pyproject.toml` `cli-extended>=0.5.0`, `cmru/pyproject.toml`
`cli-extended>=0.3.0` and `nyxloom/pyproject.toml` `cli-extended>=0.4.0` are real wheel dependencies (CX-D1).
mdt-cockpit `build-push.py` puts `libraries/cli-extended/src` on `sys.path`, and the cockpit image ships the
wheel (`mdt-cockpit/pip/wheels.list`). ciu depends on neither cmru nor nyxloom, so "ciu imports cmru"
is not available, and A needs no new edge. The module adds no library-owned CLI control, so
cli-extended's `CONTRACT_VERSION` does not change.

**OPEN QUESTION (operator):** this widens cli-extended from "CLI contract layer" to "CLI contract plus
child-process supervision". Recommendation: accept A. If you prefer C, the API below moves unchanged to the
new package.

### API (`cli_extended.procio`)

- `run_streaming(argv, *, cwd, env, on_line, drain_seconds=2.0, no_progress_seconds, progress_paths=(), term_grace_seconds=5.0) -> StreamResult`. Starts with `start_new_session=True` and `stdout=PIPE, stderr=STDOUT`, and reads with a selector (not `select(2)`; descriptors >= 1024). It stops `drain_seconds` after the child exits. The result carries `returncode` (always the child's own), `outcome` (`exited` | `watchdog`), `abandoned_holders` (pids from the `/proc/*/fd` scan, or `None` when the scan failed) and `last_line`.
- `run_capture(argv, *, cwd, env, input=None, timeout=None, text=True, drain_seconds=2.0) -> subprocess.CompletedProcess`: the bounded replacement for `subprocess.run(capture_output=True)`. Both pipes go on one selector, the same drain applies, and on timeout the group is reaped and `TimeoutExpired` is raised as `subprocess.run` does.
- `terminate_as_exit()` (SIGTERM/SIGHUP to `SystemExit`, main thread only; a signal that is `SIG_IGN` at entry, such as `nohup`'s SIGHUP, stays ignored; only what was installed is restored), `reap_process_group(proc, grace)`, `pipe_holder_pids(inode)` and `process_tree(pid)` (from `/proc/<pid>/stat` ppid links).
- WARN text (one line, printed by the caller through its own logger; the library returns it): `'<argv0>' exited with code N but its output pipe is still held open by pid(s) P; stopped reading after 2s and kept the exit code.`

### The no-progress watchdog (KI-178 item 3)

- **Progress** means bytes read from the pipe, or a size or mtime change of any path in `progress_paths` (cmru passes the step's own log file, for tools that write to a log rather than to stdout). The clock is monotonic.
- **On firing**: SIGTERM, then after `term_grace_seconds` SIGKILL, to the child's own process group only. Anything that escaped the group with `setsid` is named, never killed. The result is `outcome = "watchdog"`.
- **What it prints** (an ERROR block from the caller): `'<argv0>' produced no output for Ns (<key> = N); last output line: <line>`, then the live process tree as one line per process (`pid ppid state argv`, argv cut to 200 characters), taken **before** the group is signalled.
- **Configuration and default** (a policy choice that is correct without information, so it is a legitimate default): it is never infinite, there is no off value, and a step that is silent for longer declares a larger number.
  - cmru: `[steps.<name>] no_progress_timeout_seconds`, a positive integer, default `1800`.
  - ciu: `[ciu] subprocess_no_progress_timeout_seconds`, default `1800`, applied to the streaming compose/build path only.
  - The library has no default: the caller passes the value it resolved.
- Tests do not depend on timing beyond what they assert: a test uses a small declared period (for example 1 s) and asserts the outcome and the printed tree, never an upper wall-clock bound tighter than generous (`< 60s`).

### CIU readers: in and out (grep on `db8c4321a`)

**In scope:** these read a pipe with no timeout, and the child may spawn helpers (docker credential
helpers, buildkit clients, ssh masters, user hooks).

| site | change |
|---|---|
| `engine.py:1599` `execute_docker_compose_with_logs` (`Popen`, `for line in proc.stdout`) | `procio.run_streaming` with the watchdog; the KeyboardInterrupt branch becomes the group reap |
| `procutil.run_cmd` (`procutil.py:75`, `subprocess.run(capture_output=capture, timeout=None)` by default) | the capture body becomes `procio.run_capture`, so every caller inherits the drain: 84 real call sites (30 `procutil.run_cmd(` + 54 `procutil.docker(`) plus `procutil.docker`'s own internal call. The signature is unchanged; add `text=False` support for bytes callers |
| `procutil.run_cmd(capture=False)` (`deploy.py:910`, `worktree.py:5063`, both through `procutil.docker`) | both are `docker exec` callers, and `deploy.py:910` is the interactive path (it adds `-it` when stdin and stdout are ttys). No pipe, so no drain is needed, and a child on the terminal must stay in the terminal's session and process group: a new session would stop a terminal Ctrl-C from reaching it and change job control. **Decision (closed):** this branch stays a plain `subprocess.run` with inherited stdio, with no `start_new_session`, no drain and no watchdog. `run_cmd` is unchanged for `capture=False` |
| 17 direct unbounded `subprocess.run(capture_output=True)`: `workspace_env.py` 746, 808, 942, 997, 1011, 1058, 1109, 1119, 1153 (docker; 1153 is `docker run`, which can pull through a credential helper); `cli.py:909` (`buildx bake --print`); `diagnose.py:32`; `host_enroll.py:115`; `worktree.py` 1218 (git) and 2074 (child `ciu env generate`); `workspace.py` 204, 228, 251 (git, 251 is bytes) | move to `procutil.run_cmd` |
| bounded captures (`doctor.py:22`, `ksm.py` 256/364/403 (403 has `timeout=30`), `deploy_pkg/health.py` 419/438, `governance.py` 719/847/941/1417/1637/1769) | move to `procutil.run_cmd(timeout=...)`: today a held pipe turns a finished child into a false `TimeoutExpired` |

**Out of scope, as justified exceptions:** these have no pipe, so there is no EOF to wait for, and the
wait is on the child itself.

- `subprocess.run(cmd)` with inherited stdio (7): `transport_ssh.py` 203, 329, 397; `workspace_env.py:2469`; `worktree.py` 2063, 2073, 4698.
- `subprocess.call` with inherited stdio (2): `cli.py:953`, `dev.py:329`.
- `procutil.popen` in `oneshot.py:1218`: inherited stdout, already `start_new_session=True`.

The guard test greps `ciu/src` for `subprocess.run(`, `subprocess.Popen(`, `subprocess.call(`,
`check_call(`, `check_output(` and `communicate(` outside `procutil.py`. It fails on any hit that is not
in an exemption list, and each list entry carries a reason string. The list holds exactly the nine
out-of-scope sites above. The two `capture=False` callers add no entry of their own: both reach
`subprocess.run` through `procutil.docker` and `run_cmd`, inside `procutil.py`, which the grep skips. That
branch is nevertheless a recorded exemption with the reason "interactive or inherited-stdio child, kept in
the terminal's session". The rewritten `procutil.py` docstring states it, and a test pins that
`run_cmd(capture=False)` passes no `start_new_session` and uses no `procio` call.

The change list adds the rewrite of `procutil.py`'s module docstring. It says "Subprocess discipline for
CIU v2" and claims (line 8) that `run_cmd` and `popen` are the only places CIU spawns children, which the
direct sites above contradict today. After the change it states the real rule: every child goes through
`procutil`, captures go through `procio`, and the exemptions live in the guard test.

### Adoption order

1. **cli-extended 0.7.0**: `procio` ported from cmru `runner.py` (`_output_lines`, `_pipe_holder_pids`, `_warn_pipe_holders`, `_reap_process_group`, `_terminate_as_exit`), plus `run_capture` and the watchdog. cmru's KI-178 acceptance tests move here as the library's conformance tests. Docs: README (what), SPEC section (contract), CONSUMERS (a call site to paste).
2. **cmru, next release after the KI-175 branch lands** with its private copy (that branch already fixes items 1 and 2): `runner.py` calls `procio` and deletes its copy, adds `no_progress_timeout_seconds` (KI-178 item 3) and raises its floor to `cli-extended>=0.7.0`. cmru goes first because it had the incident and its code is the source of the port.
3. **mdt-cockpit `build-push.py`** (MDT-104) in the same wave, through its source-path import; the image picks up 0.7.0 through `wheels.list`. `release-bake.sh` is shell and stays MDT-104's own fix.
4. **ciu, next 7.x minor**: the table above, `[ciu] subprocess_no_progress_timeout_seconds`, the guard test and the floor `cli-extended>=0.7.0`.
5. **nyxloom** (NL-83) last: `wrapper.py`'s agent supervision has its own lifecycle and needs its own review of which outcome a watchdog maps to.

**Floors.** Each floor is the first release that ships `procio`, recorded with that reason in the existing
pyproject floor comment. The 14-day freshness buffer applies to third-party dependencies only, and our own
tools take the latest release. Raising a floor before the release exists is refused (CX-D2 install
ordering).

### Tests that must exist (library; each consumer keeps one integration test of its own call site)

1. A child starts `sleep 600` with inherited stdout and exits 3: `run_streaming` and `run_capture` return 3 after the drain; `abandoned_holders` contains the sleep's pid; the WARN names it.
2. A silent live child with a 1 s period: `outcome == "watchdog"`, the tree taken before the kill is printed, the group is gone afterwards, and a process outside the group is untouched.
3. A size change of a `progress_paths` file counts as progress and resets the clock.
4. SIGINT, SIGTERM and SIGHUP to the caller reap the whole group. Under `nohup`, SIGHUP stays ignored for caller and child. Handlers are restored afterwards, including after a partial install.
5. More than 1024 open descriptors before the call: the selector path still works.
6. `run_capture` with a timeout: the group is reaped and `TimeoutExpired` is raised; with bytes it returns `bytes`; partial UTF-8 at a chunk edge decodes the same as a text pipe.
7. ciu: the CIU-220 close-when test through `execute_docker_compose_with_logs` with a fake compose, plus the grep guard.

### Migration and compatibility

No public behaviour changes except that a command whose pipe is held after exit now returns with its exit
code and a WARN instead of hanging, and the new watchdog. Its 1800 s default can fail a step that used to
hang silently for longer, which is the intent. `procutil.run_cmd` keeps its signature. Tool releases are
independent minors that take cli-extended 0.7.0 as a floor.
