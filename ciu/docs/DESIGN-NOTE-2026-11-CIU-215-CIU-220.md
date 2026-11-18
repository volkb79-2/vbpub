# Design note 2026-11: CIU-215 (git common-dir mount) and CIU-220 (bounded subprocess readers)

Status: design decided 2026-11-18, implementation not started. Base: `main` at `db8c4321a`.
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

In a **linked worktree only**, `ciu up`/`ciu render` writes a fourth engine-side compose fragment,
`.ciu/ciu.compose.gitdir.yml`, and merges it as an extra `-f` after the ownership fragment. The fragment
adds one long-form bind per selected service:

```yaml
services:
  <service>:
    volumes:
      - type: bind
        source: <physical git common dir>     # daemon namespace
        target: <logical git common dir>      # exactly the path the checkout's gitfile names
        read_only: true                       # false for "rw"
        bind: {create_host_path: false}       # never let Docker invent an empty dir (CIU-14)
```

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
  refuse with both paths named. The git top comes from `worktree.discover_git_context` (a CIU root can sit
  below the git top, as `ciu/` does in vbpub).
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

### Read-only by default

Verified in code:

- Assay builds its repository snapshot as a private repository: it streams objects with
  `git pack-objects --stdout` into a seed repo (`assay/src/assay/git.py` ~1491) and reads the source only
  through `cat-file`, `ls-tree`, `rev-list` and `status`, with `GIT_OPTIONAL_LOCKS=0`/`--no-optional-locks`.
  It refuses a snapshot that references the source or depends on alternates (`isolation.py` ~1741). There
  is no `git worktree add` in `assay/src`, and `git log -G` on `isolation.py`/`git.py` finds none in their
  history.
- Run-Gate reads only: `rev-parse`, `status --porcelain`, `check-ignore`, `diff --quiet`, `merge-base`,
  `symbolic-ref`, `describe` and `config --file` (`run-gate-project/run_gate.py`).

So `ro` is enough for the known lanes. The "rw because assay runs `git worktree add`" rationale in the
netcup-api-filter and dstdns templates, and in SPEC-V8 S16.4.9 and S6.2.4, cannot be reproduced from
current code. `rw` stays as an explicit value for a lane that writes to the source repository (a test that
creates branches, commits or worktrees). A plain `git status` under `ro` skips its optional index refresh;
the live probe below must confirm that this is silent.

### Cost to consumers

None unless they opt in. An adopter adds two lines to the runner stack's config and deletes its
hand-written `{% if '/.worktrees/' in ciu.repo_root %}` block. That block is also fragile: `../../.git`
assumes exactly `.worktrees/<name>`, so a branch name with `/` breaks it. Until the block is deleted, the
de-duplication keeps it working unchanged (and keeps its `rw`).

### Change list

- `ciu/src/ciu/worktree.py`: `is_linked_git_worktree` (moved from `deploy.py`), and `git_common_dir_mount(repo_root) -> (logical, physical)` with the gitfile/commondir derivation and the cross-check.
- `ciu/src/ciu/config_model.py`: `validate_linked_worktree_table(stack_root_table)` with closed keys `git_common_dir` (`ro|rw`, required) and `services` (list of strings), refusing unknown keys and values (`[CIU-215] ...`).
- `ciu/src/ciu/engine.py`: `GITDIR_OVERLAY_NAME = "ciu.compose.gitdir.yml"`; `write_gitdir_overlay(stack_dir, rendered_compose, table, mount)`, a sibling of `write_ownership_overlay`, with the volume parser and the de-duplication; a call in `main_execution` Step 15 after the ownership fragment, appended to `file_args`; leak-scanned like the other fragments; an unknown name in `services` refuses at render. Add the fragment to `reset_service`'s `down -f` set and its removal targets (engine.py ~1193-1295). The shipped-stack path (engine.py ~2800, no rendered template) refuses the table, so the key is never silently ignored.
- `ciu/src/ciu/deploy.py`: the preflight/`ciu check` (beside the governance shape check, ~3889) reports an invalid table; `_is_linked_git_worktree` becomes an import.
- Docs, in the same change: `ciu/docs/SPEC.md` (new subsection beside S16.9 ownership labels), `CONFIG.md` (the table), `CONSUMERS.md` (a block to paste, plus "delete your hand-written block"), `DESIGN-GUIDE.md` (why opt-in, why ro, why not automatic), and a CHANGES entry.

### Tests that must exist

1. A primary checkout with the table: `ciu.compose.yml` is byte-identical to the render without it, no `ciu.compose.gitdir.yml` exists, and `file_args` is unchanged.
2. A linked worktree without the table: no fragment.
3. A linked worktree with `"ro"`, using a fake physical root that differs from the logical one: every stack service gets exactly one long-form bind with physical source, gitfile-derived target, `read_only: true` and `create_host_path: false`, and the fragment follows the ownership fragment in `file_args`.
4. `"rw"` gives `read_only: false`. `services` narrows the selection; an unknown service name refuses with the name in the message.
5. De-duplication: the literal netcup-api-filter short-form line (a colon inside `${...}`) is detected and that service is skipped with INFO; an ancestor target is skipped; a long-form equal target is skipped.
6. Derivation: a relative gitfile is joined lexically; a commondir that disagrees with `git rev-parse --git-common-dir` refuses; a CIU root below the git top uses the top's `.git`.
7. An invalid value, an unknown key or a missing `git_common_dir` refuses at render and in `ciu check`; the table on a shipped stack refuses.
8. `reset_service` passes the fragment to `down` and removes it.
9. Docs oracles (AGENTS.md): the CONSUMERS example parses with the shipped loader, and `ro`/`rw` appear in the docs.
10. **Live acceptance probe** (the CIU-215 close-when, one run through the registered lane path): a netcup-api-filter linked worktree with the project block deleted and `git_common_dir = "ro"` runs `git rev-parse HEAD` and one `snapshot_selection = "repository"` assay lane to a verdict, and `git status` prints no error.

### Migration and compatibility

Additive minor release of CIU 7 (the next 7.x). Primary checkouts and non-adopting stacks are unchanged,
and projects with the hand-written block keep working through the de-duplication. Follow-ups: the
netcup-api-filter and dstdns backlogs (dstdns via its own backlog, cross-repo annotation) delete their
blocks after adopting. v8: S6.2.4 derives the service from `exec_in` and needs no table, so the v8
migration drops `[<stack>.linked_worktree]`. **OPEN QUESTION (v8 spec, not blocking CIU 7):** S16.4.9 and
S6.2.4 mandate `rw` on the stale `git worktree add` rationale. Recommendation: amend v8 to `ro` with an
opt-in `rw`, after probe 10 confirms `ro` on a live lane.

---

## Decision 2 — CIU-220 / cmru KI-178 items 3 and 4: one bounded subprocess reader in cli-extended

### Decision

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
| `procutil.run_cmd` (`procutil.py:75`, `subprocess.run(capture_output=..., timeout=None)` by default; 86 call sites) | the body becomes `procio.run_capture`, so every caller inherits the drain; signature unchanged; add `text=False` support for bytes callers |
| direct `subprocess.run(capture_output=True)` without timeout: `workspace_env.py` 746, 808, 942, 997, 1011, 1058, 1109, 1119, 1153 (docker; 1153 is `docker run`, which can pull through a credential helper); `cli.py:909` (`buildx bake --print`); `diagnose.py:32`; `host_enroll.py:115`; `ksm.py:403`; `worktree.py` 1218 (git) and 2074 (child `ciu env generate`); `workspace.py` 204, 228, 251 (git, 251 is bytes) | move to `procutil.run_cmd` |
| bounded captures (`doctor.py:22`, `ksm.py` 256/364, `deploy_pkg/health.py` 419/438, `governance.py` 719/847/941/1417/1637/1769) | move to `procutil.run_cmd(timeout=...)`: today a held pipe turns a finished child into a false `TimeoutExpired` |

**Out of scope, as justified exceptions:** these have no pipe, so there is no EOF to wait for, and the
wait is on the child itself.

- `subprocess.run(cmd)` with inherited stdio: `transport_ssh.py` 203, 329, 397; `workspace_env.py:2469`; `worktree.py` 2063, 2073, 4698.
- `procutil.popen` in `oneshot.py:1218`: inherited stdout, already `start_new_session=True`.

The guard test greps `ciu/src` for `subprocess.run(`, `subprocess.Popen(`, `check_output(` and
`communicate(` outside `procutil.py`. It fails on any hit that is not in an exemption list, and each list
entry carries a reason string. The list holds exactly the out-of-scope sites above.

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
