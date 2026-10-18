# mdt devcontainer template

The template binds the complete `/home/vscode` directory to one host source.
This keeps agent logins, keys, histories, caches, shell preferences, and any
other user-created home content across rebuilds. **A new home starts empty**:
image executables and runtime payloads live under `/opt/mdt` and `/srv`, and
every image default is wired system-wide (`/etc`,
`/usr/local/share/mdt-cockpit`), so nothing is copied into the home.

## Adopt

Copy the host-side files into the consuming repo's `.devcontainer` directory:

```sh
cp <vbpub>/mdt-cockpit/templates/{devcontainer.json,initialize_container_environment.py} .devcontainer/
```

Adjust workspace-specific features, extensions, and mounts. Install and verify
the MDT host setup before starting: the initializer checks the declared cgroup
slices and creates missing bind sources. See the
[host cgroup preflight](../docs/CONSUMERS.md#host-cgroup-preflight).

## Persistent paths

| Host source | Container target | Policy |
|---|---|---|
| `${localEnv:HOME}/mdt-cockpit-home/` | `/home/vscode` | Complete home, mode `0700` on a new source; all contents persist. |
| `${localEnv:HOME}/mdt-cockpit-tmp/` | `/tmp` | Separate scratch/worktree source, mode `1777`. |
| `${localEnv:HOME}/.ssh/` | `/run/mdt/host-ssh` | Read-only native host SSH keys, outside the persistent home. |

Any further mount source you add is named `${localEnv:HOME}/mdt-cockpit-<purpose>`.

The home mount includes credentials and private keys stored under `~/.ssh`,
`~/.gnupg`, and `~/.minisign`; protect the host source accordingly. Caches such
as `~/.cache` and `~/.npm` also persist because they are part of the home. Users
may clear rebuildable caches themselves.

The full home also covers Claude, ClaudeLink, Codex profiles and shared SQLite
state, OpenCode, Pi, Reasonix, OpenClaw, GitHub CLI, shell history, `.config`,
`.local`, worktree metadata, and arbitrary files. The template sets
`CODEX_SQLITE_HOME=/home/vscode/.codex/sqlite-shared` and
`shutdownAction: "none"`. See [Codex profile state and container lifetime](../docs/CONSUMERS.md#codex-profile-state-and-container-lifetime).

The template deliberately sets neither `PATH` nor `NPM_CONFIG_PREFIX`: the image
defines both once ([One PATH order](../docs/DESIGN-GUIDE.md#one-path-order)).

## Image paths and home defaults

| Image-owned content | Image path | In the home |
|---|---|---|
| Standalone CLIs (`antigravity`, `agy`) | `/opt/mdt/bin` | Nothing. Group `mdt`, updatable without `sudo`. |
| Primary Python toolkit | `/opt/mdt/venv` | Nothing; `python` resolves there via `PATH` (no `~/.venv`). |
| Secondary Python environments | `/opt/mdt/venvs/pyNN` | Nothing (no `~/.venv-pyNN`). |
| npm agent CLIs (including `claude`) | `/opt/mdt/npm` | Nothing; `npm install -g` and `claude update` write here (`NPM_CONFIG_PREFIX`). |
| uv-managed Python builds | `/opt/mdt/python` | Nothing; user installs keep uv's per-user data path. |
| Oh My Zsh distribution (VS Code image) | `/opt/mdt/oh-my-zsh` | Cache only, in `~/.cache/oh-my-zsh`. |
| PWMCP instance | `/srv/pwmcp-instance` | Nothing. |
| Shell, editor and tool defaults | `/etc/bash.bashrc`, `/etc/zsh/zshrc`, `/etc/nanorc`, `/etc/htoprc`, `/etc/mc/mc.ini`, `/etc/xdg/nvim` | Nothing - create your own file to override. |
| MDT shell files, examples and manifest | `/usr/local/share/mdt-cockpit` | Nothing; your overrides go in `~/.config/mdt-cockpit/`. |

The image itself must have no children under `/home/vscode`. The finalizer
runs after the home mount is active; its only home write is a comment-only
`~/.zshrc` stub (when you have no zsh startup file) so zsh's new-user wizard
does not take over your first terminal. It never replaces or edits an existing
file, and running it twice changes nothing. Full tool-by-tool table:
[System-wide defaults](../docs/DESIGN-GUIDE.md#system-wide-defaults).

## Moving from the previous template

There is no migration. A new install starts with an empty home; if the previous
`~/mdt--mounted-folders/` still holds files, the initializer prints one
informational line (it never fails or blocks) and you copy what you want by
hand: [Moving from modern-debian-tools-python-debug](../docs/HOME-PERSISTENCE.md#moving-from-modern-debian-tools-python-debug)
has a pasteable `cp -a` recipe and the old-image paths that must not be copied.

## Other template notes

The template exports `PWMCP_MCP_URL` to remote processes (the `pwmcp` CLI is on
the image `PATH`); each agent harness still configures its own MCP connection.
See the [PWMCP browser service](../docs/CONSUMERS.md#pwmcp-browser-service).

The host initializer is stdlib-only and distinguishes existing bind sources by
actual host type. For missing custom sources, `create-by-spelling` treats a
trailing `/` as a directory and no trailing `/` as a regular file. The
host-setup wizard can set `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` to
require sources to exist. See [the Git config example](../docs/CONSUMERS.md#optional-git-config-mount).

The initializer name is `initialize_container_environment.py`. CMRU uses
`get.py` for a different, manually-run project installer.
