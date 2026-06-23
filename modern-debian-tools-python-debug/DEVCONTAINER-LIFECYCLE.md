# mdt devcontainer lifecycle — pre/post scripts & grouped-persistence mounts

This is the **canonical** reference for the two lifecycle hooks an mdt-based devcontainer wires up,
and for the grouped-persistence mount layout. Consuming repos (e.g. dstdns) point here and keep only
their project-specific notes.

## The two lifecycle hooks (set in the consuming repo's `devcontainer.json`)

| Hook | Runs | Where | Script |
|---|---|---|---|
| `initializeCommand` | **before** the container is created | on the **host** | `initialize_container_environment.py` (the "pre" script) |
| `postCreateCommand` | **after** the container is created | **inside** the container | `finalize_container_environment.py` (the "post" script — **baked into the mdt image**) |

> **Why one is vendored and the other is baked in.** `initializeCommand` is the *only* devcontainer
> hook that runs on the **host**, *before* the container exists — at that moment the mdt image
> filesystem isn't available, so its script (`initialize_container_environment.py`) **cannot** come
> from the image; it must be a file in your repo checkout. `postCreateCommand` runs **inside** the
> container, so its script (`finalize_container_environment.py`) **is** baked into the mdt image and
> you reference it by name. `devcontainer.json` itself is inherently repo-specific, so it is also
> vendored. Hence mdt ships two **templates** to copy (`devcontainer.json`,
> `initialize_container_environment.py`) and one **baked-in** script you just call (finalize).

### Pre script — `initialize_container_environment.py` (host)
Runs on the host so every bind-mount **source** exists with sane permissions *before* Docker starts
(a missing source makes Docker fail, or silently create it as root → the container user can't write its
own `~/.codex` etc.). Design: stdlib-only, idempotent, best-effort (always exits 0). It parses the
sibling `devcontainer.json`, finds every `type=bind` source under `$HOME`, and creates it as a **real
dir** with the right mode. Secret dirs (`.ssh`/`.gnupg`/`.minisign`) get `0700`; `tmp` gets `1777`;
everything else `0755`.

### Post script — `finalize_container_environment.py` (container, baked into mdt)
Lives in the mdt image at `/usr/local/bin/finalize_container_environment.py`; a consuming repo wires
only `"postCreateCommand": "finalize_container_environment.py"`. Symmetric to the host-side
`initialize_container_environment.py`. stdlib-only, idempotent.

It does the **generic, ciu-AGNOSTIC** setup every mdt devcontainer wants — `~/.local/bin` on PATH,
convenience shell aliases, `.vscode/settings.json` (global python), and a base-image tool check — and
**brackets** that with the consumer's own hooks. mdt **never** imports or calls ciu: mdt *ships and
encourages* ciu, but a repo that doesn't use it still gets a fully working devcontainer.

#### Consumer hook contract
finalize discovers and runs hooks from your repo's `.devcontainer/` (no need to fork the script):

```
.devcontainer/finalize.pre.d/*.sh     # run (sorted) BEFORE the generic mdt steps
<generic mdt steps>
.devcontainer/finalize.post.d/*.sh    # run (sorted) AFTER  the generic mdt steps
```

- Single-file forms `finalize.pre.sh` / `finalize.post.sh` are also honoured (run after the `.d` dir).
- Order hooks with a numeric prefix (`10-…`, `20-…`). Executable hooks run directly; others via `bash`.
- Each hook inherits the environment plus: `MDT_FINALIZE=1`, `MDT_ENV_TYPE`, `MDT_WORKSPACE_DIR`,
  `MDT_DEVCONTAINER_DIR`, `MDT_USER`/`MDT_UID`/`MDT_GID`/`MDT_DOCKER_GID`.
- **Enforcement boundary:** mdt's own steps only *warn* on failure (they never fail the build). A
  **consumer** hook that exits non-zero is reported and makes finalize's exit non-zero — because that
  hook is where *you* put *your* critical setup. By default the remaining hooks still run; set
  `MDT_FINALIZE_STRICT=1` to abort on the first failing hook.
- Flags: `--no-hooks` (generic only), `--hooks-only` (skip generic), `--devcontainer-dir PATH`.

**This is where ciu (if you use it) goes** — e.g. a repo that deploys with ciu drops
`.devcontainer/finalize.post.d/10-<repo>-ciu.sh` that exports `REPO_ROOT`, runs `ciu env generate`,
installs repo deps, etc. mdt provides the bracket; the consumer owns the contents.

## Grouped-persistence mounts — `~/mdt--mounted-folders/`

Devcontainer-persisted state is grouped under a single host parent so a rebuild never wipes it and one
`ls -la ~/mdt--mounted-folders/` shows the whole set. These are **real dirs** (not symlinks):

| Source (host) | Target (container) | Notes |
|---|---|---|
| `~/mdt--mounted-folders/.ssh` | `/home/vscode/.ssh` (ro) | container-persisted ssh state |
| `~/mdt--mounted-folders/.claude` `.codex` `.config` `.minisign` `.gnupg` | matching `/home/vscode/*` | agent/tool state; secret dirs `0700` |
| `~/mdt--mounted-folders/tmp` | `/tmp` | **persisted, host-backed `/tmp`** (`1777`) |
| `~/.ssh` (host, native) | `/home/vscode/.ssh-host` (ro) | **dual-use exception**: the host's NATIVE keys, so the same keys work natively AND in the devcontainer |

**Why a persisted host-backed `/tmp`:** `/tmp` git worktrees then survive rebuilds AND are visible to
**sibling containers** (e.g. the `test-runner`) that bind the same host dir — so `/tmp`-based worktrees
can be gated. (Without this, `/tmp` is a container-local overlay with no host path; a sibling container
can't see it — `.worktrees/` under the repo bind mount was the prior workaround.)

**`HOST_MDT_TMP`** (the host path of that persisted `/tmp`) is **autodetected by `ciu env generate`**
(it inspects the devcontainer's own `/tmp` bind-mount source) and written to `.env.ciu`; sibling
containers read it from there. No hardcoded host path, no raw `containerEnv` var.

## Adopting the consolidation in a consuming repo

1. Point the `mounts` in `devcontainer.json` at `${localEnv:HOME}/mdt--mounted-folders/<name>` (+ keep
   the native `~/.ssh → .ssh-host` readonly mount), and add `~/mdt--mounted-folders/tmp → /tmp`.
2. **Migrate once on the host** (the bootstrap only creates EMPTY dirs — existing state isn't copied):
   ```
   for d in .claude .codex .config .minisign .gnupg; do
     [ -d ~/"$d" ] && cp -a ~/"$d"/. ~/mdt--mounted-folders/"$d"/ 2>/dev/null || true
   done
   ```
   ⚠️ The `.minisign` key (cmru release signing), gpg keys, and gh auth live here — migrate or you lose them.
3. Rebuild the container (the host bootstrap creates the structure first).
4. Recreate sibling containers (e.g. `ciu render` + restart the test-runner) so they pick up the new `/tmp`.
5. **Verify:** `ls ~/.claude`, `ls ~/.minisign`, `gpg --list-keys`, `ls /home/vscode/.ssh-host` non-empty;
   `touch /tmp/__probe` then on the host `ls ~/mdt--mounted-folders/tmp/__probe`.
6. **Rollback:** revert `devcontainer.json` + `initialize_container_environment.py` and rebuild; the host
   `~/mdt--mounted-folders/` is harmless leftover (canonical `~/.ssh` etc. are untouched).
