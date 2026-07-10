# mdt devcontainer template

The supported way to consume the `modern-debian-tools-python-debug-vsc-devcontainer` image.
mdt installs the AI-CLI agents (claude, codex, aider, reasonix, openclaw, opencode, antigravity, copilot)
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

### Directory mounts (subdirectory-level state)

| Mount | Why |
|---|---|
| `~/.claude` | Claude Code config / auth / projects / memory |
| `~/.codex` | Codex CLI auth + history |
| `~/.reasonix` | Reasonix config / `.env` / session state |
| `~/.openclaw` | OpenClaw config / `.env` / session state |
| `~/.local/share/opencode` | OpenCode auth, sessions, logs, and runtime state |
| `~/.config` | `gh` auth + tool configs + mdt customization root |
| `~/.minisign` | Ed25519 signing key (cmru SPEC B / KI-01) |
| `~/.gnupg` | GPG keys |
| `~/.ssh` | Container-scoped SSH keys (readonly: mounted from `~/mdt--mounted-folders/.ssh`) |
| `~/tmp` → `/tmp` | **Persisted, host-backed /tmp** (mode 1777) — git worktrees survive rebuilds |

### File-level mounts (single files at home root)

Some tools store their state as individual files rather than inside a subdirectory.
These are bind-mounted individually:

| Mount | Why |
|---|---|
| `~/.claude.json` | Claude Code auth tokens, page state, tip history (outside `~/.claude/`) |
| `~/.reasonix.toml` | Reasonix global config (MCP servers, rule defaults) — project-agnostic across all repos |

User-editable shell/API bootstrap state lives under `~/.config/modern-debian-tools-python-debug/`.
That directory holds `ai.env` for central API keys, `aliases.sh` for local shell shortcuts, and
`shell.env`/`htoprc`/`mc.ini`/`nanorc` for shipped defaults the user can adjust later.

**Intentionally ephemeral:** `~/.cache`, `~/.npm` — rebuildable, not worth persisting.

## initialize_container_environment.py — host bootstrap (why it exists)
`devcontainer.json` wires `"initializeCommand": "python3 .devcontainer/initialize_container_environment.py"`. It runs **on the
host, before the container is created**, and ensures every `$HOME` bind-mount source exists with correct
modes (0700 for `.ssh`/`.gnupg`/`.minisign`). Without it, a missing source makes Docker create the
path as **root**, and the in-container `vscode` user then can't write its own `~/.codex` etc. — or the
container fails to start outright. It is stdlib-only, idempotent, best-effort (never blocks start), and
**derives its dir list from the mounts** in the same file, so adding a mount auto-creates its dir.

For **file-level mounts** (`.json`, `.toml`, `.yaml`, `.yml`), it creates the **parent directory**
on the host; Docker creates the file itself on first mount. This keeps the bootstrap logic
consistent while supporting both directory and individual file mounts.

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

## GitHub Copilot CLI

The image ships `@github/copilot` (installed globally via npm). Configure your provider in
`~/.config/modern-debian-tools-python-debug/ai.env`:
```
COPILOT_PROVIDER_BASE_URL=https://openrouter.ai/api/v1
COPILOT_PROVIDER_API_KEY=sk-or-v1-xxxxxxxxxx
COPILOT_MODEL=deepseek-v4-flash
```
These vars are auto-exported into every shell session by `profile.sh`. Use `copilot` from the
terminal — no separate login needed.
