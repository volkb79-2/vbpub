# Persisting the complete `vscode` home

The supported template mounts one host directory at `/home/vscode`. The home
mount is user state: it survives container rebuilds, while image-owned tools
and defaults live outside it so the mount cannot hide them. The template source
is `${localEnv:HOME}/mdt--mounted-folders/home-vscode/`.

## Design plan and invariants

1. Keep the runtime home at `/home/vscode`, owned by `vscode` (uid/gid 1000 in
   the standard image), and make the image's home directory empty before the
   final image is produced.
2. Put image-owned executables, Python environments, Node packages, managed
   Python runtimes, and the shared Oh My Zsh distribution under `/opt/mdt`.
   Put generated manifests and source defaults under
   `/usr/local/share/modern-debian-tools-python-debug`.
3. Preserve useful old paths with post-create symlinks (`~/.venv`, configured
   secondary venvs, and `~/.oh-my-zsh`). The symlinks are materialized only
   after the persistent home is mounted; their targets remain image-owned and
   outside the mount.
4. Seed shell and editor defaults only when a destination is absent. Never
   replace an existing user's file or directory. A user may edit the seeded
   copy; a later image rebuild does not overwrite it.
5. Keep credentials, agent history, editor state, user-installed tools,
   caches, worktrees, and other user-created home contents in the persistent
   home. Do not copy image runtime payloads into it during migration.
6. Run a host-side preflight before Docker starts. If the old split state has
   content and the new home has not been migrated, refuse to start instead of
   silently mounting an empty directory over that state.
7. Migrate only from a stopped container. Dry-run first, check free space, and
   copy without replacing any existing destination. Refuse unsupported special
   files and verify regular-file hashes across the scan and copy. Keep old host
   sources as rollback copies until the new home is verified.

## Home inventory and policy

This inventory records the previous image's home roots and all path classes
that the new image or template creates. Contents below a listed directory
inherit that directory's owner and persistence policy. The September image
inspection measured `/home/vscode` at 2.8 GB; the largest roots were `.local`
(npm CLIs), `.venv` (the toolkit), `.codex` (the standalone Codex payload),
`.oh-my-zsh`, and `.config`. The rootfs build check now verifies that the new
image leaves no files or directories below `/home/vscode`.

| Path or path class | Owner / role | New location or policy |
|---|---|---|
| `/home/vscode` | `vscode` home and mount point | Empty in the image; whole directory bind-mounted from `home-vscode/`. |
| `.venv` | MDT image Python toolkit | `/opt/mdt/venv`; post-create compatibility symlink. Root-owned in the image. |
| `.venv-pyNN` | MDT image secondary Python environments | `/opt/mdt/venvs/pyNN`; configured compatibility symlinks. |
| `.local/bin`, `.local/lib/node_modules` | Previously mixed image CLIs and user-installed CLIs | New image copies live in `/opt/mdt/npm`; the persistent `.local` remains the user prefix and can override image commands. |
| `.local/share/uv/python` | Previously uv-managed interpreters | Image-managed versions live in `/opt/mdt/python`; runtime user installs use uv's user data directory in the persistent home. |
| `.codex/packages/standalone` | Previous standalone Codex install payload | New image installs the supported npm package under `/opt/mdt/npm`; `CODEX_HOME` remains user state under `~/.codex`. |
| `.oh-my-zsh` | Base image shell framework | `/opt/mdt/oh-my-zsh`; post-create compatibility symlink. User extensions belong in `~/.oh-my-zsh-custom`. |
| `.bashrc`, `.bash_logout`, `.profile`, `.zshrc` | Base-image shell defaults, editable by the user | Source templates live under `.../home-defaults`; seeded into the mounted home only when missing. |
| `.config/nvim` | MDT-shipped NvChad defaults, editable by the user | Source tree lives under `.../home-defaults/.config/nvim`; seed missing entries without replacing user entries. |
| `.config/modern-debian-tools-python-debug/*` | MDT shell/API examples and user-editable defaults | Source copies live under `/usr/local/share/modern-debian-tools-python-debug`; the finalizer seeds missing files into the mounted home. |
| `.config/mc`, `.config/nano`, `.config/htop`, `.nanorc` | MDT default editor/tool configuration | Defaults remain in the share directory; the finalizer creates compatibility links only when the home target is absent. |
| `mdt-manifest.md` | Compatibility view of the image manifest | Manifest lives in `/usr/local/share/modern-debian-tools-python-debug/manifest.md`; finalizer creates a home symlink only when absent. |
| `.agents`, `.devcontainer` | Agent instructions and consumer-specific devcontainer configuration | Persist as user-owned content; image-wide defaults belong in image/share paths and are seeded only when absent. |
| `.claude`, `.claude.json`, `.claudelink`, `.codex`, `.codex2`, `.copilot`, `.openclaw`, `.pi`, `.reasonix` | User authentication, settings, history, sessions, and state | Persist in the whole home. Never seeded with credentials. |
| `.config` after MDT defaults | User settings, API keys, and tool state | Persist in the whole home; seeded defaults do not replace existing files. |
| `.gnupg`, `.minisign`, `.ssh` | User keys and credentials | Persist in the whole home with their existing private permissions. Native host SSH remains a separate read-only mount outside `/home/vscode`. |
| `.local` after image move | User-installed executables, packages, and application state | Persist in the whole home. User-local commands take precedence over image commands on `PATH`. |
| `.cache`, `.npm`, `.docker`, `.dotnet`, `.redhat`, `.vscode-server`, runtime logs/history, and other tool-created paths | Rebuildable caches, CLI configuration, editor runtime, and user state | Persist because the complete home is mounted. Users may clear rebuildable caches when desired; the image does not own these paths. |
| `.bash_history`, `.zsh_history`, `.zprofile`, `.zshenv`, `.zcompdump*`, and other shell state | User shell preferences, command history, generated completion caches | Persist. Image defaults are seeded only for explicitly listed absent files. |
| Project checkouts, worktrees, gate directories, downloads, and home-level files | User/project data, scratch, or cache | Persist without migration filtering; cleanup remains the user's choice. |
| Any other user-created home file or directory | `vscode` runtime/user data | Persist in the whole home; no image build step may create it. |
| `/home/vscode/.ssh-host` | Read-only native host SSH keys | Re-targeted to `/run/mdt/host-ssh`, outside the persistent home. |
| `/tmp` | Shared scratch/worktree storage | Remains the separate host-backed `/tmp` mount; it is not part of the home. |

The final image may create `/home/vscode` itself as the Docker `USER` home, but
it must not bake any child entry beneath it. Build caches use temporary paths
and are not image contents.

The path classes above were cross-checked against Dockerfile home writes,
template mounts, finalizer writes, migration exclusions, and the live cockpit
home roots. The inventory classifies user and project data by owner and policy;
it does not copy one developer's filenames into the shared image manifest.

## Moving existing state

The old template mounted selected directories from
`${localEnv:HOME}/mdt--mounted-folders/` and left the rest of the live home in
the container. Before adopting the whole-home mount:

1. Copy the new template and `migrate_persistent_home.py` into the consumer's
   `.devcontainer` directory.
2. Close editors/agents and stop writers such as ClaudeLink so databases and
   SQLite WAL/SHM files are quiescent. Stop the devcontainer.
3. Run `python3 .devcontainer/migrate_persistent_home.py <container-name>` to
   inspect the stopped container and estimate the copy. Review the dry-run
   exclusions and required free space.
4. Run the same command with `--apply`. It creates the new home source only
   when absent or empty, copies user state without overwriting, omits the old
   image toolkit/runtime paths, and writes a migration marker after success.
   Apply refuses archives containing unsupported special entries so it cannot
   mark a partial copy complete. The native `.ssh-host` bind is omitted because
   it will remain available at `/run/mdt/host-ssh`.
5. Leave the old split sources in place as rollback copies. Rebuild, verify
   authentication, shell/editor defaults, Python and CLI paths, and only then
   remove old sources manually.

`initialize_container_environment.py` refuses startup when legacy split
sources contain data and the new home has no migration marker. A new install
with no legacy state may create an empty source; post-create seeds only the
intentional defaults listed above. The migration tool never copies file
contents to its report, never removes old sources, and refuses non-empty
destination conflicts.

## Verification contract

- Image build: assert `/home/vscode` is empty after all install and manifest
  steps; assert image-owned paths and binaries exist outside the home.
- Finalizer: seed into a temporary empty home, rerun, and prove it is
  idempotent; prove user files and directories are never replaced.
- Host bootstrap: parse the full-home source, refuse missing migration when
  legacy state exists, and accept a valid migration marker.
- Migration utility: dry-run and apply against a stopped fixture container;
  verify exclusions, symlink preservation, no-overwrite behavior, and marker
  ordering. A running container and a conflicting destination must be refused.
- Template: exactly one persistent home mount targets `/home/vscode`; host
  integration mounts stay outside that root; no individual home-state mount
  can mask the home source.
