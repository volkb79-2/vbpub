# mdt devcontainer template

The supported way to consume the `modern-debian-tools-python-debug-vsc-devcontainer` image.
mdt installs the AI-CLI agents (claude, codex, aider, reasonix, openclaw, opencode, claudelink, pi, antigravity, copilot)
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
| `~/.claudelink` | ClaudeLink durable hub state: `nexus.db`, scheduler state/logs, and related runtime files |
| `~/.codex` | Codex CLI auth + history |
| `~/.reasonix` | Reasonix config / `.env` / session state |
| `~/.openclaw` | OpenClaw config / `.env` / session state |
| `~/.config` | `gh` auth + tool configs + mdt customization root |
| `~/.local` | User-local CLI installs and state; OpenCode data is mounted separately below |
| `~/.minisign` | Ed25519 signing key (cmru SPEC B / KI-01) |
| `~/.gnupg` | GPG keys |
| `~/.pi` | Pi config and session root, including `~/.pi/agent/sessions/` |
| `~/.ssh` | Container-scoped SSH keys (readonly: mounted from `~/mdt--mounted-folders/.ssh`) |
| `~/.local/share/opencode` | OpenCode auth, sessions, logs, and runtime state (from the dedicated `opencode-data` source) |
| `~/mdt--mounted-folders/tmp` → `/tmp` | **Persisted, host-backed /tmp** (mode 1777) — git worktrees survive rebuilds |

The Pi and ClaudeLink container paths are whole-directory mounts. Their host sources are
`${localEnv:HOME}/mdt--mounted-folders/.pi` and
`${localEnv:HOME}/mdt--mounted-folders/.claudelink`; Pi sessions and ClaudeLink's durable
database must not be split into guessed file-level mounts. In the container, those targets
are `/home/vscode/.pi` and `/home/vscode/.claudelink`. OpenCode's dedicated target is
`/home/vscode/.local/share/opencode`, backed by `${localEnv:HOME}/mdt--mounted-folders/opencode-data`.

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

### Migrate existing Pi, ClaudeLink, and OpenCode state once

The host bootstrap creates empty source directories; it never copies existing state. Before
the first rebuild after adopting this template, run this on the host. It contains no
credentials; it copies the state already present on your machine:

```sh
mdt_state="$HOME/mdt--mounted-folders"
mkdir -p "$mdt_state"
for d in .claude .claudelink .codex .config .gnupg .local .minisign .openclaw .pi .reasonix; do
  if [ -d "$HOME/$d" ]; then
    mkdir -p "$mdt_state/$d"
    cp -a "$HOME/$d/." "$mdt_state/$d/"
  fi
done
if [ -d "$HOME/.local/share/opencode" ]; then
  mkdir -p "$mdt_state/opencode-data"
  cp -a "$HOME/.local/share/opencode/." "$mdt_state/opencode-data/"
fi
```

The dedicated `opencode-data` mount overlays that path inside the broader `.local` mount,
so copy OpenCode's `~/.local/share/opencode` separately as shown. Pi state is restored from
`~/.pi`; ClaudeLink state is restored from `~/.claudelink`.

If Pi or ClaudeLink state currently exists only in the running devcontainer, do not rebuild
or copy the live database. First follow the [running-container migration runbook](../DEVCONTAINER-LIFECYCLE.md#migrating-a-running-devcontainer-before-adopting-the-mounts),
which quiesces ClaudeLink, stops and verifies the container, and uses `docker cp` to copy the
complete roots (including SQLite WAL/SHM sidecars) to these host sources. Then use the host-path
recipe above for any state that already lives on the host.

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
