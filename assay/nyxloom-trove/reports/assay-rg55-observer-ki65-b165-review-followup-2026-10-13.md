# Follow-up review: cgprofile-observer provisioning, KI-65, B165

> Archived for later inspection. Claude Opus 5.5 high session
> `84f1f032-0abc-4497-be99-de275d398950`; the session remains in Claude's
> history for follow-up. This review bound HEAD `336336d81` and tracked-diff
> SHA-256 `511eb19c…cd97`. It predates later uncommitted changes in this
> worktree. In particular, the current candidate now has an `--observer-only`
> path, so the review's statement that no scoped verb exists is historical;
> the remaining provisioning recommendations still need validation against
> the current source and gate.

## Context

**Binding:** HEAD `336336d81`. The tracked diff has sha256 `511eb19c…cd97` and covers 149 changed paths. Everything here comes from file reads, plus one read-only `ls`/`getent` on the host path. Nothing was edited, tested or run.

**Host state** [confirmed]:
- `/run/cgprofile` exists as `drwxrwx--- root docker`.
- `/run/cgprofile/observer` is missing.
- There is no `cgprofile-observer` group.
- So every contract-2 observer lane refuses to start, which is a safe failure.

### Why the general installer can't be used

1. **A plain `install.sh` run changes the host before it refuses.**
   - With `BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate` and no `--wizard`, `install.sh:269-279` runs `systemctl disable --now mdt-buildkit-guard.service` and then exits 2.
   - Before that, `install.sh:236-237` has already created `/etc/mdt`, `/var/lib/mdt` and the Buildx config directory.
   - **Don't use a plain run as a probe.**
2. **The observer step can't be reached on its own.** It sits at `install.sh:624-636`, after the config load, the key-completeness check, the Buildx policy gate and the slice rendering.
3. **There is no supported way to run only that step.** The only flags are `--wizard`, `--reset`, `--with-baseline` and `--restart-docker` (`:97-106`).
4. **Running the two `systemd-*` commands by hand is a shim.** AGENTS.md "Use supported project workflows" calls that a product gap: extend and review the workflow, don't hand-roll a substitute.

## Recommendation: a scoped, reviewed MDT verb

Add **`sudo ./host-setup/install.sh --only cgprofile-observer`**. The same code could live in a sibling script, `host-setup/scripts/mdt-cgprofile-observer-provision.sh`, that `install.sh` calls; in either form there is a single source of truth.

### How it works
1. **Move the existing block into a shell function.** Lines 624-636 become `provision_cgprofile_sockets()`, which the full install path also calls, so the code isn't duplicated.
2. **Dispatch `--only cgprofile-observer` early.** It runs after the container, root and systemd-PID1 checks (`:108-125`) and **before** the Docker, Buildx, config, slice and service code.
   - It must not read `/etc/mdt/host-setup.env`, create `/etc/mdt` or `/var/lib/mdt`, touch the Buildx guard, or run `daemon-reload`, Docker restarts or `systemctl`.
   - Reject `--only` together with `--wizard`, `--reset`, `--with-baseline` or `--restart-docker`, and reject an unknown component name.
3. **Preflight, read-only, refusing on drift:**
   - `systemd-sysusers` and `systemd-tmpfiles` are present.
   - If `/run/cgprofile` exists, it is a real directory (not a symlink) with `0 <docker-gid> 770`.
   - If `/run/cgprofile/observer` exists, it is a real directory, not a symlink or socket.
   - If `cgprofile-observer` exists, its GID is ≥ 1 and isn't the docker GID.
   - Refuse any mismatch rather than letting tmpfiles silently re-own it. A wrong owner on a live socket directory is a fact to report, not to repair.
4. **Apply, limited to the two files:**
   - `install -m0644` the two unit files into `/etc/sysusers.d/` and `/etc/tmpfiles.d/`. Skip the write when the bytes are already identical, and print a diff when they differ.
   - `systemd-sysusers /etc/sysusers.d/mdt-cgprofile.conf` (this explicit-file form processes only that file).
   - `systemd-tmpfiles --create --prefix=/run/cgprofile /etc/tmpfiles.d/mdt-cgprofile.conf`. Never `--remove` or `--clean`.
   - The `d` lines have age `-`, so nothing inside `/run/cgprofile` is deleted and the live control socket survives.
5. **Post-verify.** The existing exact check stays (`stat` == `0 $observer_gid 751`). Also check:
   - `observer_gid != docker_gid`;
   - `/run/cgprofile` still has `0 <docker-gid> 770`;
   - `ctl.sock` is still present if it was present before.

   Print one machine-readable summary line: GID, modes, which files changed.
6. **`--check` mode** runs steps 3 and 5 only and makes no changes. This is the operator's pre-flight and post-flight view. It also feeds `mdt-host-check.sh`, which today checks only `/run/cgprofile` (`scripts/check.sh:352-375`) and never the observer directory, so it can report "ok" on a host where every observer lane refuses.

### Operator sequence after it's merged
`--check` → `--only cgprofile-observer` → `--check`. Then redeploy the contract-2 daemon and confirm:
- `version.resource_observer` = `{2, /run/cgprofile/observer/resource-observer.sock, listening: true}`;
- the socket is `root:cgprofile-observer 0660`;
- inside a lane container, `id -G` includes the observer GID and not the docker GID.

No Docker restart, devcontainer rebuild or Buildx change is involved.

## Risks to require (and their acceptance)

- **Silent re-ownership.** tmpfiles `d` adjusts the mode and owner of an existing path. The preflight refusal (step 3) is what keeps this from quietly "fixing" a drifted live directory.
- **A planted path.** `/run/cgprofile` is writable by the docker group, so a member could pre-create `observer` as a directory or symlink.
  - systemd-tmpfiles refuses unsafe symlink transitions, and docker membership is root-equivalent anyway, so this is low.
  - Even so, the preflight should refuse a non-directory and the post-verify should check the owner.
- **GID assignment.** sysusers picks a free dynamic system GID.
  - It must never equal the docker GID; assert that.
  - It isn't stable across hosts, which is fine: Run-Gate and the launcher read it from `stat` of the directory.
- **Boot persistence.** `systemd-sysusers.service` runs before `systemd-tmpfiles-setup.service`, so the group and directory are recreated after a reboot. Accept this with a VM reboot test.
- **Drift between the scoped verb and the full installer.** Both call the one function. A later full `--wizard` run must be a no-op for these paths.
- **Partial application.** If sysusers succeeds and tmpfiles fails, the next run must converge. `set -euo pipefail` already makes a failed `systemd-sysusers` fatal.
- **Rollback, documented:** remove the two `.d` files, `rmdir /run/cgprofile/observer` (only while the daemon is stopped or the directory is empty), and `groupdel cgprofile-observer`.
- **The production host.** No game-server service, slice or Docker state is touched; assert this by hashing before and after (tests below).

## Tests to require

The current `tests/test-render.sh:283-316` is **grep-only**: it proves that lines exist, not what they do. Add these behavioral tests in the existing VM harness `tests/run-vm-acceptance.sh`, never on the live host:

1. A fresh VM with `/run/cgprofile` provisioned: the group is created, the directory is `0751 root:cgprofile-observer`, and the exit code is 0.
2. **Isolation from other host policy.** Snapshot hashes before and after of:
   - `/etc/systemd/system/**`;
   - `/etc/docker/daemon.json`;
   - `/etc/mdt/**` (it must not even be created);
   - the Buildx config directory;
   - the `mdt-buildkit-guard` active/enabled state;
   - `systemctl list-units --state=active`.

   Only the two `.d` files and the observer directory may differ.
3. **Idempotence.** A second run changes nothing, and `--check` passes.
4. **Refusals:**
   - inside a container;
   - as a non-root user;
   - with any combined flag;
   - with an unknown component name;
   - with `/run/cgprofile` set to the wrong owner or mode;
   - with `observer` as a symlink or as a file.
5. **The live control socket survives.** A listener on `/run/cgprofile/ctl.sock` before the run still accepts a connection afterwards.
6. **After a reboot,** the group and directory are recreated and `--check` passes.
7. **An end-to-end mount.** A disposable container gets the directory mount plus the observer GID, can `connect()` to a test socket, and can't write to the directory.
8. **`mdt-host-check.sh`** fails when the observer directory is missing or has the wrong mode, and passes after provisioning.

## Docs to require (all three, in the same change)

- **host-setup README** (the WHAT): the `--only cgprofile-observer` and `--check` verbs. State explicitly that they touch no slices, Docker, Buildx or config, include the rollback steps, and say that a plain `install.sh` under the `terminate` policy stops the Buildx guard before refusing.
- **DESIGN** (the WHY): why a scoped verb exists, and why it isn't a way around the Buildx second confirmation (it never reads or touches Buildx state). Also why it refuses drift rather than repairing it.
- **CONSUMERS / remedy text.** Each refusal message must name the scoped verb as its remedy (the "prescribe a correct fix" corollary):
  - Run-Gate observer-directory refusal (`run-gate.py` around 7037-7061);
  - cgprofile `serve.py:2435`;
  - Assay launcher.

  The Run-Gate and Assay consumer docs should add the verb as the first step for an observer lane on a new host.

## KI-65 (CMRU secret-overlay scope): accept, with one gap

The change itself is correct and tested:
- `_secret_overlay_paths` (`cmru/tools/run_release_gate.py:329-388`) walks only `repo_root`, with `followlinks=False`.
- It prunes `.git` and `.worktrees` and skips symlinked directories.
- Tests `cmru/tests/test_release_gate.py:88-129` and `:170-190` prove that a sibling in `.worktrees/` is excluded, and that the sibling's secret is byte-identical afterwards.

**Gap [confirmed in code; real-world occurrence a hypothesis].** Pruning goes by **name** only. A linked worktree nested under any other name inside an opted-in main-checkout lane is still walked: Claude Code's `.claude/worktrees/<x>`, or `git worktree add` to any path. The sibling secret would then be masked and restored.
- **Fix:** also prune any directory below the root that contains a `.git` entry (a gitfile means a linked worktree; a directory means a nested repository).
- **Test:** a `.claude/worktrees/other/` with its own `.git` file and a `cmru.secret.toml` is excluded.
- **Severity:** minor. It applies only to opted-in main-checkout lanes, and the outcome is the original false-failure class, not a leak.

## B165 / SA_NOCLDWAIT: not a release blocker for the standalone CLI scope

The supported boundary is a dedicated Assay CLI process (`README.md:543-553`, `CONSUMERS.md:3448-3460`, `4-backlog.md:12736-12750`).

**Why the CLI can't get SA_NOCLDWAIT:**
- `_sigchld_is_ignored` (`process_tree.py:194-207`) detects only `SIG_IGN` through `SigIgn`; procfs doesn't expose `SA_NOCLDWAIT`.
- But on `execve`, Linux resets every caught signal to `SIG_DFL` and clears `sa_flags` (`flush_signal_handlers`). Only `SIG_IGN` is inherited, and the CLI resets that and then checks it.
- CPython never sets `SA_NOCLDWAIT`.
- Assay's process loads no third-party code: adapters and plugins run in candidate subprocesses.
- In-repo embedders don't set it either. CMRU's `mutation_campaign.py:128` runs a window in its own fresh tool process.

**Why it still matters for embedders.** If an embedder does set it, the failure is a **false certification**, not a crash:
- children are auto-reaped;
- `subprocess` maps the resulting `ECHILD` to `returncode = 0`, so a failing baseline or candidate reads as PASS;
- `_has_any_children` reports no children, so the empty-tree proof holds trivially.

**Recommendation:**
- Ship 10.0.0 with the CLI-only scope as documented. Keep B165 open for embedding and parallel isolation, coordinated with CP-23.
- As **cheap hardening** (low, could go in this wave), read the current `SIGCHLD` `sigaction` through `ctypes` (`sigaction(SIGCHLD, NULL, &old)`, and check `sa_flags & SA_NOCLDWAIT`) at window entry and refuse when it is set. This turns the documented embedder constraint into a mechanical refusal.
- Test: set the flag through `ctypes` in a subprocess and assert that `ProcessTreeError` is raised.

**It becomes a blocker only if** 10.0.0 advertises in-process embedding (an importable `ProcessTreeWindow` or `run_lane` for third parties) as supported. CMRU's in-repo use is acceptable because it runs as its own process and in a single thread.
