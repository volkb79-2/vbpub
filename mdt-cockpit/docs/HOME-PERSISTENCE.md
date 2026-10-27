# The persistent `vscode` home (fresh-home design)

The supported template mounts one host directory per checkout at `/home/vscode`:
`${localEnv:HOME}/mdt-cockpit-${localWorkspaceFolderBasename}-home/` (mode `0700`; the host
initializer expands the variable to the checkout's folder name). The home is **user state**: it
survives container rebuilds, the image never puts anything into it, and a new home
**starts empty**. Image-owned tools live outside the mount (`/opt/mdt`, `/srv`) and every
image default is wired where its tool reads system-wide configuration (`/etc`,
`/usr/local/share/mdt-cockpit`), so the mount can neither hide the image nor go stale
against it. There is **no migration**: moving from an older layout means copying what you
want by hand ([below](#moving-from-modern-debian-tools-python-debug)).

## Design and invariants

1. The runtime home is `/home/vscode`, owned by `vscode` (uid/gid 1000 in the image; Dev
   Containers may re-number it and re-owns only the home). The image's own home is
   **empty**: the Dockerfile deletes what the base image left there and fails the build if
   anything unclassified remains.
2. Image-owned executables, Python environments, Node packages, managed Python runtimes
   and the Oh My Zsh distribution live under `/opt/mdt` (and PWMCP under `/srv`), owned by
   group `mdt` — see [The `mdt` group](DESIGN-GUIDE.md#the-mdt-group). The runtime user is a
   member, so image tools can be updated in place without `sudo`.
3. Defaults are **system-wide, never copied** ([System-wide defaults](DESIGN-GUIDE.md#system-wide-defaults)):
   a user overrides one by creating their own file in the home. Only when a tool cannot be
   configured system-wide may the finalizer write a minimal stub, only if the target is
   absent. The single such case is a comment-only `~/.zshrc` that keeps
   `zsh-newuser-install` quiet.
4. Credentials, agent history, editor state, user-installed tools, caches, worktrees and
   every other user-created file stay in the home. The finalizer never replaces, extends or
   links over a user file, and a second run leaves the home byte-identical.
5. `PATH` is defined once, by the image ([One PATH order](DESIGN-GUIDE.md#one-path-order)).
   `~/.local/bin` is **last**: it holds the user's own additions and never shadows an
   image tool. The template does not set `PATH` or `NPM_CONFIG_PREFIX`.
6. The host bootstrap creates the two sources (`mdt-cockpit-<checkout>-home` `0700`,
   `mdt-cockpit-<checkout>-tmp` `1777`), names any further source `mdt-cockpit-<purpose>`, and — when the previous
   `~/mdt--mounted-folders/` still holds files while the new home is empty — prints one
   informational line pointing here. That line never changes the exit status.

## Home inventory and policy

Every path class the image, the template or the finalizer can create in or around the home.
Contents below a listed directory inherit that directory's owner and persistence policy.

| Path or path class | Owner / role | Policy |
|---|---|---|
| `/home/vscode` | `vscode` home and mount point | Empty in the image; the whole directory is bind-mounted from `mdt-cockpit-<checkout>-home/`. |
| `.zshrc` | comment-only stub | The only file the finalizer ever writes (only when none of `.zshenv`, `.zprofile`, `.zshrc`, `.zlogin` exists). Yours from then on. |
| `.bashrc`, `.bash_logout`, `.profile`, `.zprofile` | optional user shell startup | **Not created.** bash and zsh load the image defaults from `/etc/bash.bashrc` and `/etc/zsh/zshrc`; create your own file to add to or override them. |
| `.config/mdt-cockpit/` | **your** MDT overrides: `ai.env`, optional `shell.env`, `aliases.sh` | Not created by the image. Sourced after the image's own files. Examples: `/usr/local/share/mdt-cockpit/*.example`. |
| `.config/htop/htoprc`, `.config/mc/ini`, `.nanorc` / `.config/nano/nanorc`, `.config/nvim/init.lua` | per-tool user config | **Not created.** The image defaults load from `/etc/htoprc`, `/etc/mc/mc.ini`, `/etc/nanorc`, `/etc/xdg/nvim`; creating your file overrides them (nano: per setting). Tools write these files themselves when you save settings. |
| `.local/bin` | the user's own CLIs | Last on `PATH`. An image tool of the same name wins; call the user copy by path or put it earlier in your own startup file. |
| `.local/share`, `.local/state`, `.cache` (including `.cache/oh-my-zsh`) | tool data, state and rebuildable caches | Created by the tools themselves on first use; persist. |
| `.local/share/uv` | uv's user data (user-installed interpreters/tools) | Persists. Image-managed interpreters are in `/opt/mdt/python` (`UV_PYTHON_INSTALL_DIR`). |
| `.npm` | npm's cache | Persists; rebuildable. Global installs go to `/opt/mdt/npm` (`NPM_CONFIG_PREFIX`), not here. |
| `.claude`, `.claude.json`, `.claudelink`, `.codex`, `.codex2`, `.copilot`, `.openclaw`, `.pi`, `.reasonix` | agent authentication, settings, history, sessions | Persist. Never seeded with credentials. `CODEX_SQLITE_HOME` is `~/.codex/sqlite-shared`. |
| `.gnupg`, `.minisign`, `.ssh` | keys and credentials | Persist with their private permissions. The host's native `~/.ssh` is a separate read-only mount at `/run/mdt/host-ssh`. |
| `.bash_history`, `.zsh_history`, `.zcompdump*`, `.lesshst` | shell/pager state and caches | Created by the tools; persist. |
| `.vscode-server`, `.docker`, `.dotnet`, `.redhat`, runtime logs | editor runtime and CLI configuration | Created by their tools; persist. |
| Project checkouts, worktrees, gate directories, downloads | user/project data | Persist; cleanup is the user's choice. |
| Any other user-created file or directory | `vscode` runtime/user data | Persists; no image build step may create it. |
| `/opt/mdt/{bin,venv,venvs,npm,python,oh-my-zsh}`, `/srv/pwmcp-instance` | image-managed tools | **Not in the home.** Group `mdt`, directories `2775`, files group-writable. Changes made at runtime live in the container filesystem. |
| `/usr/local/share/mdt-cockpit/` | image support files, examples, manifest | Not in the home; no `~/mdt-manifest.md` link. |
| `/tmp` | shared scratch/worktree storage | A separate host-backed mount (`mdt-cockpit-tmp/`, `1777`); not part of the home. |

The final image may create `/home/vscode` itself as the Docker `USER` home but must not bake any
child entry beneath it; the image-runtime lane asserts this on both image families.

## Moving from modern-debian-tools-python-debug

mdt-cockpit is an opt-in fork: a user starts with an **empty** home and copies old content
by hand. Nothing is migrated, nothing is detected beyond the one-line bootstrap note, and the
old state is never touched or deleted.

The previous template kept its state under `~/mdt--mounted-folders/` (split mounts such as
`.claude`, `.codex`, `.ssh`, `.local`, `opencode-data`; an interim unreleased variant used
`~/mdt--mounted-folders/home-vscode/`). Worth copying:

| Old item | Copy to | Notes |
|---|---|---|
| `.claude/`, `.claude.json` | same name | Claude Code auth, settings, history. |
| `.codex*/` (`.codex`, `.codex2`) | same name | **Without** `packages/standalone` (the old image's Codex payload). |
| `.ssh/`, `.gnupg/`, `.minisign/` | same name | Keep their modes (`cp -a`). |
| `.claudelink/`, `.pi/`, `.reasonix/`, `.openclaw/` | same name | Agent state. `.reasonix.toml` and `.gitconfig` (single files) too. |
| `opencode-data/` or `.local/share/opencode/` | `.local/share/opencode/` | OpenCode data. |
| `.config/<tool>/` | `.config/<tool>/` | **Selectively**: `gh`, `git`, `docker`, `opencode`, … — only directories you recognise as yours. |
| `.config/modern-debian-tools-python-debug/ai.env` | `.config/mdt-cockpit/ai.env` | Your API keys. |

Stop the container first (so SQLite WAL/SHM files are quiescent), then, on the host:

```bash
OLD=~/mdt--mounted-folders
NEW=~/mdt-cockpit-<checkout>-home   # the checkout's folder name, e.g. ~/mdt-cockpit-myrepo-home
mkdir -p "$NEW" "$NEW/.config/mdt-cockpit" "$NEW/.local/share"

for item in .claude .claude.json .ssh .gnupg .minisign .claudelink .pi .reasonix .openclaw \
            .reasonix.toml .gitconfig; do
    [ -e "$OLD/$item" ] && cp -a "$OLD/$item" "$NEW/"
done
for codex in .codex .codex2; do
    [ -d "$OLD/$codex" ] && cp -a "$OLD/$codex" "$NEW/" && rm -rf "$NEW/$codex/packages/standalone"
done
[ -d "$OLD/opencode-data" ] && cp -a "$OLD/opencode-data" "$NEW/.local/share/opencode"
[ -d "$OLD/.local/share/opencode" ] && cp -a "$OLD/.local/share/opencode" "$NEW/.local/share/"
[ -f "$OLD/.config/modern-debian-tools-python-debug/ai.env" ] \
    && cp -a "$OLD/.config/modern-debian-tools-python-debug/ai.env" "$NEW/.config/mdt-cockpit/ai.env"
for tool in gh git docker opencode; do
    [ -d "$OLD/.config/$tool" ] && cp -a "$OLD/.config/$tool" "$NEW/.config/"
done
```

Run it as the host user who owns the cockpit — Dev Containers maps `vscode` to that user's uid,
so `cp -a` keeps every file readable inside the container. Verify logins and keys, then delete
the old directory yourself when you are satisfied.

**Do not copy** the old image's runtime payloads — they are replaced by the new image and
would shadow it or point at paths that no longer exist:

- `~/.venv*` (the old toolkit and secondary venvs) and `~/.oh-my-zsh` — now `/opt/mdt/venv`,
  `/opt/mdt/venvs/*`, `/opt/mdt/oh-my-zsh`.
- `~/.local/bin` and `~/.local/lib/node_modules` — the old image's CLIs (now `/opt/mdt/bin` and
  `/opt/mdt/npm`); copy only a CLI of your own, deliberately.
- `~/.codex/packages/standalone` — the old standalone Codex payload.
- The seeded copies of image defaults (`~/.bashrc`, `~/.zshrc`, `~/.config/nvim`,
  `~/.config/htop`, `~/.config/mc`, `~/.nanorc`, `~/.config/modern-debian-tools-python-debug/{htoprc,mc.ini,nanorc,zshrc,shell.env,lesspipe.sh,aliases.sh}`)
  — they would shadow the system-wide defaults and stop image updates reaching you. Re-create
  only the customisations you actually made.

## Verification contract

- Image build: the home is empty after every install and manifest step; `mdt-perms check`
  proves the `mdt` group contract over `/opt/mdt` and `/srv`; image tools exist outside the home.
- Image runtime (`run-gate image-runtime`, both image families): empty home, group
  membership, the group contract re-checked in a running container, one `PATH` in every shell,
  writes through the group under a re-numbered uid (and `EACCES` without it), a finalizer run
  that leaves a fresh home with only the stub and is idempotent, silent interactive shells, and
  every system-wide default loading with an empty home.
- Finalizer (unit): the stub is the only write, a second run is byte-identical, any user zsh
  startup file suppresses it and is never modified.
- Host bootstrap (unit): exactly the declared `$HOME` sources are prepared with their modes, the
  note appears only for old state with an empty new home, and the exit status never depends on it.
- Template (unit): exactly one mount targets `/home/vscode`, host integration mounts stay
  outside it, and the template defines neither `PATH` nor `NPM_CONFIG_PREFIX`.
