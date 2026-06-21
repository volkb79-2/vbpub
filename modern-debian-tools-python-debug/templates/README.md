# mdt devcontainer template

The supported way to consume the `modern-debian-tools-python-debug-vsc-devcontainer` image.
mdt installs the AI-CLI agents (claude, codex, aider, reasonix, openclaw, antigravity, deepcode)
and signing/auth tooling (minisign, gh, gnupg) — this template defines **how their state persists**
so a "Rebuild Container" never wipes logins, keys, or history.

## Adopt
```sh
cp <vbpub>/modern-debian-tools-python-debug/templates/{devcontainer.json,initialize_container_environment.py} .devcontainer/
# adjust workspaceFolder-adjacent bits (sibling mounts, features, extensions) for your repo
# then VS Code: "Dev Containers: Rebuild Container"
```

## What persists (and what doesn't)
Persisted via host bind mounts (login once, keys + history survive rebuilds):

| Mount | Why |
|---|---|
| `~/.claude` (+ `~/.claude.json`) | Claude Code config / auth / projects / memory |
| `~/.codex` | Codex CLI auth + history |
| `~/.config` | `gh` auth + tool configs |
| `~/.minisign` | Ed25519 signing key (cmru SPEC B / KI-01) |
| `~/.gnupg` | GPG keys |
| `~/.ssh` → `~/.ssh-host` (ro) | git over SSH |

**Intentionally ephemeral:** `~/.cache`, `~/.npm` — rebuildable, not worth persisting.

## initialize_container_environment.py — host bootstrap (why it exists)
`devcontainer.json` wires `"initializeCommand": "python3 .devcontainer/initialize_container_environment.py"`. It runs **on the
host, before the container is created**, and `mkdir -p`s every `$HOME` bind-mount source with correct
modes (0700 for `.ssh`/`.gnupg`/`.minisign`). Without it, a missing source makes Docker create the
path as **root**, and the in-container `vscode` user then can't write its own `~/.codex` etc. — or the
container fails to start outright. It is stdlib-only, idempotent, best-effort (never blocks start), and
**derives its dir list from the mounts** in the same file, so adding a mount auto-creates its dir.

> **Naming:** this bootstrap is `initialize_container_environment.py`. The name `get.py` is reserved for
> the CMRU release *installer* (`cmru/templates/get.py.tmpl`) — a different, manually-run host-side tool.

## Named-volume variant
The default uses host bind mounts (host-visible state). For host-path-free portability, swap the
state mounts to named volumes (shared across all your devcontainers → log in once everywhere; the
`~/.claude/projects` tree is internally namespaced by workspace path, so sharing is safe):
```jsonc
"source=agentstate-claude,target=/home/vscode/.claude,type=volume"
```
With named volumes, `initialize_container_environment.py` is unnecessary for those targets (Docker manages the volume), but a fresh
volume may mount as root — add a `postCreateCommand` chown if so.
