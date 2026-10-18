# mdt-cockpit customization assets

These files are the image's shipped defaults. They live in the image under
`/usr/local/share/mdt-cockpit/` and are wired in **system-wide** — each tool
reads them from where it already looks for system configuration — so the user's
home starts empty and a user overrides a default simply by creating their own
file. The image never writes these into `/home/vscode`.

Your own overrides go under `~/.config/mdt-cockpit/` (nothing creates that
directory for you):

- `shell.env` — exported shell defaults (pager, editor, less); `shell.env` in
  `~/.config/mdt-cockpit/` is sourced after the image's, so it can override.
- `aliases.sh` — aliases and helper functions; yours is sourced after the image's.
- `ai.env` — your central API keys (no image default; copy `ai.env.example`).

What ships here and where it is wired:

| File | Installed where | Notes |
|---|---|---|
| `profile.sh` | sourced by `/etc/profile.d/50-mdt-cockpit.sh`, the `/etc/bash.bashrc` hook and the `/etc/zsh/zshrc` hook | env + aliases + cgroup/KSM banner (below); never edits `PATH` |
| `bashrc` | hook appended to `/etc/bash.bashrc` | history, colours, completion; your `~/.bashrc` runs after it |
| `zshrc` | hook appended to `/etc/zsh/zshrc` | fzf, autosuggestions, syntax highlighting, Oh My Zsh on the VS Code image family |
| `shell.env`, `aliases.sh` | `/usr/local/share/mdt-cockpit/` | image layer of the two user files above |
| `htoprc` | `/etc/htoprc` | htop reads it when the user has no `~/.config/htop/htoprc` |
| `mc.ini` | `/etc/mc/mc.ini` | mc reads it when the user has no `~/.config/mc/ini` |
| `nanorc` | `include` line appended to `/etc/nanorc` | nano reads `~/.nanorc` after it |
| `nvim/sysinit.vim` | `/etc/xdg/nvim/sysinit.vim`, beside the NvChad tree | redirects a starter-style tree's lazy lockfile into the state directory while the user has no `~/.config/nvim/init.lua` or `init.vim` (Neovim itself starts the tree from `$XDG_CONFIG_DIRS`) |
| `lesspipe.sh` | `LESSOPEN` in `shell.env` | syntax-highlighting preprocessor for `less` |
| `*.example` | `/usr/local/share/mdt-cockpit/` | copy to `~/.config/mdt-cockpit/` and edit |

`profile.sh` also prints, on interactive shells, a compact cgroup-governance
banner (`memory.max`, `memory.high`, `cpu.weight`, `io.max` read from
`/sys/fs/cgroup`, skipped when unreadable/absent) so you can see at a glance
whether the container is host-governed — see "Host resource governance
(cgroups/slices)" in `../DEVCONTAINER-LIFECYCLE.md` — plus a one-line KSM opt-in
status (see "KSM opt-in" in `../README.md`).

- `host-escape` is the compatibility alias for `mdt host-exec`/`mdt host-shell`.
  It enters host namespaces through a privileged helper; `host-escape --help`
  shows the operator warning and examples. See `../docs/CONSUMERS.md` before
  using it.
- `ksm-optin.c` is the `LD_PRELOAD` shim built in a throwaway Dockerfile stage;
  see "KSM opt-in (kernel same-page merging)" in `../README.md`.
- `lnav/formats/nyxloom/nyxloom_events.json` is a bundled `lnav` log-format
  definition for nyxloom's `events.jsonl`, installed image-wide at
  `/etc/lnav/formats/nyxloom/`; see "`lnav`: JSONL inspection" in `../README.md`.

The only thing the in-container finalizer ever writes into the home is a
comment-only `~/.zshrc` stub, and only when none exists (it keeps
`zsh-newuser-install` from taking over the first zsh terminal). See
`../docs/HOME-PERSISTENCE.md`.
