# mdt devcontainer template

The template binds the complete `/home/vscode` directory to one host source.
This keeps agent logins, keys, histories, caches, shell preferences, and any
other user-created home content across rebuilds. Image executables and runtime
payloads live under `/opt/mdt`; default config sources live under
`/usr/local/share/modern-debian-tools-python-debug`.

## Adopt

Copy all host-side files into the consuming repo's `.devcontainer` directory:

```sh
cp <vbpub>/mdt-cockpit/templates/{devcontainer.json,initialize_container_environment.py,migrate_persistent_home.py} .devcontainer/
```

Adjust workspace-specific features, extensions, and mounts. Install and verify
the MDT host setup before starting: the initializer checks the declared cgroup
slices, creates missing bind sources, and refuses to create an empty home source
while old split mounts still contain user data. See the
[host cgroup preflight](../docs/CONSUMERS.md#host-cgroup-preflight).

## Persistent paths

| Host source | Container target | Policy |
|---|---|---|
| `${localEnv:HOME}/mdt--mounted-folders/home-vscode/` | `/home/vscode` | Complete home, mode `0700` on a new source; all contents persist. |
| `${localEnv:HOME}/mdt--mounted-folders/tmp/` | `/tmp` | Separate scratch/worktree source, mode `1777`. |
| `${localEnv:HOME}/.ssh/` | `/run/mdt/host-ssh` | Read-only native host SSH keys, outside the persistent home. |

The home mount includes credentials and private keys stored under `~/.ssh`,
`~/.gnupg`, and `~/.minisign`; protect the host source accordingly. Caches such
as `~/.cache` and `~/.npm` also persist because they are part of the home. Users
may clear rebuildable caches themselves.

The full home also covers Claude, ClaudeLink, Codex profiles and shared SQLite
state, OpenCode, Pi, Reasonix, OpenClaw, GitHub CLI, shell history, `.config`,
`.local`, worktree metadata, and arbitrary files. The template sets
`CODEX_SQLITE_HOME=/home/vscode/.codex/sqlite-shared` and
`shutdownAction: "none"`. See [Codex profile state and container lifetime](../docs/CONSUMERS.md#codex-profile-state-and-container-lifetime).

## Image paths and home defaults

| Image-owned content | Image path | Home behavior after mount |
|---|---|---|
| Primary Python toolkit | `/opt/mdt/venv` | `~/.venv` compatibility symlink when the user has no existing path. |
| Secondary Python environments | `/opt/mdt/venvs/pyNN` | `~/.venv-pyNN` compatibility symlink when free. |
| npm agent CLIs | `/opt/mdt/npm` | Runtime npm installs default to the user's persistent `~/.local` prefix; user commands take precedence on `PATH`. |
| uv-managed Python builds | `/opt/mdt/python` | User installs keep uv's normal per-user data path. |
| Oh My Zsh distribution | `/opt/mdt/oh-my-zsh` | `~/.oh-my-zsh` compatibility symlink when free; personal extensions belong in `~/.oh-my-zsh-custom`. |
| Shell and NvChad seed files | `/usr/local/share/modern-debian-tools-python-debug/home-defaults` | Finalizer seeds missing files and merges only missing config entries. |
| MDT config defaults and manifest | `/usr/local/share/modern-debian-tools-python-debug` | Finalizer seeds missing user config files and the `~/mdt-manifest.md` compatibility link. |

The image itself must have no children under `/home/vscode`. The finalizer runs
after the home mount is active and never replaces an existing user file or
directory. Its home writes are intentional per-user defaults; changing them is
safe and they persist across image updates.

## Migrate an existing container

The migration utility reads the stopped container's merged `/home/vscode`,
including the old split host mounts. It does not modify or delete those sources.
Stop the container first so databases and other stateful writers are quiescent.

```sh
python3 .devcontainer/migrate_persistent_home.py <stopped-container>
python3 .devcontainer/migrate_persistent_home.py <stopped-container> --apply
```

Review the dry-run counts, exclusions, unsupported special files, and free
space before `--apply`. Apply refuses an archive containing unsupported
special files rather than marking a partial copy complete. The utility also
refuses a running container and a non-empty destination. It stages the copy
beside the new source, hashes included regular files in both archive passes,
and refuses to install if any path, mode, or file content changed. It writes
`.mdt-home-migration.json` only after the copy completes, then installs the
home directory. Keep the old split sources as rollback copies until the rebuilt
container is verified.

The migration omits the old image-owned `.venv`, `.venv-pyNN`, `.oh-my-zsh`,
the read-only `.ssh-host` bind, and Codex's
`.codex/packages/standalone` runtime. It preserves `.oh-my-zsh/custom` as
`~/.oh-my-zsh-custom`. The old `.local` tree is preserved in full because it
mixed image CLIs with user-installed commands and state; its commands can
override the image prefix until you remove or update them yourself.

After migration, rebuild with this template. If old split sources contain data
but the marker is absent or invalid, the host bootstrap refuses to start and
prints the migration command. Empty legacy directories alone do not block a
new install.

## Other template notes

The template exposes `/srv/pwmcp-instance/bin` and `PWMCP_MCP_URL` to remote
processes; each agent harness still configures its own MCP connection. See the
[PWMCP browser service](../docs/CONSUMERS.md#pwmcp-browser-service).

The host initializer is stdlib-only and distinguishes existing bind sources by
actual host type. For missing custom sources, `create-by-spelling` treats a
trailing `/` as a directory and no trailing `/` as a regular file. The
host-setup wizard can set `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` to
require sources to exist. See [the Git config example](../docs/CONSUMERS.md#optional-git-config-mount).

The initializer name is `initialize_container_environment.py`. CMRU uses
`get.py` for a different, manually-run project installer.
