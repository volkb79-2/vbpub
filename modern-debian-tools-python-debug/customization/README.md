# modern-debian-tools-python-debug customization assets

These files are the shipped defaults that land under the visible user customization root:
`/home/vscode/.config/modern-debian-tools-python-debug/`.

- `ai.env` holds central API keys.
- `aliases.sh` holds shell aliases and helper functions.
- `shell.env` holds exported shell defaults such as pager and editor behavior;
  when Neovim is present it prefers `nvim` as the editor.
- `zshrc` holds shared interactive zsh defaults: fzf key bindings, Ctrl-R
  history search, zsh completions, autosuggestions, and syntax highlighting.
- `htoprc`, `mc.ini`, and `nanorc` hold the tool defaults.
- `lesspipe.sh` is the syntax-highlighting preprocessor used by `less`.
- `profile.sh` is the login-shell bootstrap (installed at
  `/etc/profile.d/50-modern-debian-tools.sh`); on interactive shells it also prints
  a compact cgroup-governance banner (`memory.max`, `memory.high`, `cpu.weight`,
  `io.max` read from `/sys/fs/cgroup`, skipped when unreadable/absent) so you can
  see at a glance whether the container is host-governed — see
  "Host resource governance (cgroups/slices)" in `../DEVCONTAINER-LIFECYCLE.md` —
  plus a one-line KSM opt-in status (see "KSM opt-in" in `../README.md`).
- `ksm-optin.c` is the `LD_PRELOAD` shim built in a throwaway Dockerfile stage;
  see "KSM opt-in (kernel same-page merging)" in `../README.md`.
- `lnav/formats/nyxloom/nyxloom_events.json` is a bundled `lnav` log-format
  definition for nyxloom's `events.jsonl`, installed image-wide at
  `/etc/lnav/formats/nyxloom/`; see "`lnav`: JSONL inspection" in `../README.md`.

The image also installs compatibility links into the standard tool paths where needed.
