# The local image 

```text
Created: 2026-09-22T17:08:15Z
Image size: 7.99 GB
/home/vscode: 2.8 GB
```

Its home directory contains:

| Path | Size | Contents |
|---|---:|---|
| `/home/vscode/.local` | 1.5 GB | npm-installed CLIs and dependencies |
| `/home/vscode/.venv` | 1.1 GB | 207 Python packages |
| `/home/vscode/.codex` | 354 MB | Codex standalone runtime |
| `/home/vscode/.oh-my-zsh` | 16 MB | shell framework |
| `/home/vscode/.config` | 268 KB | nvim and MDT config |
| dotfiles | small | `.bashrc`, `.zshrc`, `.profile`, etc. |

The current versions include:

```text
Codex       0.155.1
OpenClaw    2026.9.5
OpenCode    1.18.32
Copilot     1.0.87
ClaudeLink  1.6.1
Pi          0.87.0
Reasonix    1.38.11
CIU         7.15.0
CMRU        5.4.1
```

So mounting the whole `/home/vscode` would hide all three major payloads:

```text
/home/vscode/.local
/home/vscode/.codex
/home/vscode/.venv
```

The image has already put most native binaries outside the home directory, in `/usr/local/bin` and `/opt`. The remaining relocation wor
k is therefore fairly focused:

- Move `.venv` to `/opt/mdt/venv`.
- Move npm’s image-managed prefix to `/opt/mdt/npm`, or deliberately make it a mutable persisted prefix.
- Handle Codex’s runtime package separately; its executable link is in `.local/bin`, but the 354 MB runtime is in `.codex`.
- Move `.oh-my-zsh` to `/opt/mdt/oh-my-zsh` and set `ZSH` accordingly.
- Treat `.config` as materialized defaults rather than image-owned home content.

I would still choose `/opt/mdt/venv` over installing the 207 packages into global Python. The current system Python has only 14 packages; putting the complete development stack into `/usr/local/lib/python3.14/site-packages` is possible, but creates one large shared dependency graph and makes base-image upgrades more fragile. A root-owned venv outside `$HOME` gives the whole-home mount design without sacrificing dependency isolation.

# Release Flow

It was never loaded as a local Docker image by this release flow.

The confusing part is:

```toml
RELEASE_IMAGE_FLOW = "load"
```

Here, `load` means “build into a local OCI layout, then publish that exact layout.” It does not mean Docker Buildx’s `--load`.

The flow is:

1. `cmru` runs `build-push.py --build`.
2. `release-bake.sh build` writes OCI layouts under:

   ```text
   build/oci-layouts/
   ```

   using `type=oci`, not `type=docker`.

3. After the gate, `release-bake.sh push` uses `regctl image copy` to push those layouts to GHCR.
4. The isolated release worktree is removed.
5. The host BuildKit cache may remain, but there is no tagged image in `docker images`.

This is visible in:

- [`cmru.toml`](</workspaces/vbpub/modern-debian-tools-python-debug/cmru.toml:8>)
- [`release-bake.sh`](</workspaces/vbpub/modern-debian-tools-python-debug/scripts/release-bake.sh:48>)
- [`build-push.py`](</workspaces/vbpub/modern-debian-tools-python-debug/build-push.py:406>)

That is why local Docker still showed the August image until I pulled. The release produced and published the September image, but did 
not update the local Docker image store.

To inspect the separate BuildKit cache:

```bash
docker buildx ls
docker buildx du --verbose
```

To get the exact published image locally:

```bash
docker pull \
  ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest
```

The package page confirms that the latest tag now points to the September release ([GHCR package page](https://github.com/volkb79-2/vbp
ub/pkgs/container/modern-debian-tools-python-debug-vsc-devcontainer)).

This design is intentional: it avoids loading an approximately 8 GB multi-target image into the local Docker store and guarantees that 
the bytes reviewed and manifested are the same bytes pushed to GHCR.
