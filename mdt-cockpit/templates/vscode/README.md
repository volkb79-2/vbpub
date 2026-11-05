# Opt-in VS Code files for the SSH + devcontainer + integrated terminal setup

Nothing here is applied by the template. Each file is a copy-in snippet; pick what you want.

| File | Where it applies | What it does |
|---|---|---|
| `terminal-keys-terminal-first.settings.jsonc` | settings (container or user) | Sends most keys to the terminal while it has focus. |
| `terminal-keys-balanced.settings.jsonc` | settings (container or user) | Keeps most VS Code shortcuts; releases a listed set (Ctrl+J, Ctrl+P/E, F1, F2, Ctrl+F, F3-F11 family) to the terminal. The file lists what each removal costs. |
| `terminal-tmux.settings.jsonc` | settings (container or user) | Adds a "tmux" terminal profile (attach-or-create session `main`). |
| `keybindings.client.jsonc` | **client** `keybindings.json` | Shift+Backspace as delete-word, an editor-focus key, optional Shift+Enter. |

Use ONE of the two key profiles, not both.

## Which settings layer

The VS Code window runs on your Windows machine and the VS Code server runs in the container. A
setting is read from the layer that owns it:

- `.vscode/settings.json` in a workspace folder: that folder, for everyone who opens it.
- The `"settings"` of a `.code-workspace` file: every folder of that multi-root workspace.
- The devcontainer's `customizations.vscode.settings`: written into the remote/machine settings file
  below, which a rebuild does not rewrite (see next section).
- Remote/machine settings (`~/.vscode-server/data/Machine/settings.json` in the container): every
  workspace opened in this container, kept in the persisted home.
- User settings on Windows: every window of yours.
- Keybindings and fonts are client-side: `keybindings.json`, and `terminal.integrated.fontFamily` (the
  font must be installed on Windows; for an icon-heavy prompt use a Nerd Font family name exactly as
  Windows lists it).

## Template settings and the persisted home

The home, including `~/.vscode-server`, survives a rebuild. A rebuild does not rewrite an existing
`~/.vscode-server/data/Machine/settings.json`, so a setting added to the template's
`customizations.vscode.settings` after that file was created never takes effect: a container created
2026-11-03 carried `files.watcherExclude` in its `devcontainer.metadata` label while its machine
settings, written 2026-10-23, did not (MDT-076). Two ways to apply a changed block:

- Merge it into `~/.vscode-server/data/Machine/settings.json` by hand, then run `Developer: Reload Window`.
- Move the file away (`mv ~/.vscode-server/data/Machine/settings.json{,.bak}`), rebuild, then copy back
  any keys of your own from the `.bak` file.

Check the result: `grep -c watcherExclude ~/.vscode-server/data/Machine/settings.json` prints 1.

## A home folder in a multi-root workspace

`/home/vscode` can stay a folder of a `.code-workspace` while its tool state is kept out of watching
and searching. Resource settings (`files.watcherExclude`, `search.exclude`, `search.followSymlinks`)
are read per folder, so put them in that folder's own `/home/vscode/.vscode/settings.json`. It lives
in the persisted home and affects no other folder:

```jsonc
{
  "files.watcherExclude": {
    ".vscode-server/**": true, ".cache/**": true, ".npm/**": true,
    ".codex/**": true, ".codex2/**": true, ".claude/**": true
  },
  "search.exclude": {
    ".vscode-server": true, ".cache": true, ".npm": true,
    ".codex": true, ".codex2": true, ".claude": true
  },
  "search.followSymlinks": false
}
```

Still not covered: an extension's own file search (`workspace.findFiles` with its own exclude) and a
language server's own indexer walk the folder regardless; `search.followSymlinks: false` keeps those
walks from following links out of it. An extension that activates on `workspaceContains:` can also be
activated by a file there (Terraform was, by `.tf` files in Codex's plugin cache); disable such an
extension's server per folder, for example `"terraform.languageServer.enable": false`.

## Verify before you rely on it

The key behaviour depends on your VS Code version and keyboard layout. The files were assembled from
VS Code's documented settings and third-party notes, not tested on every combination. In particular,
whether the `sendSequence` keybindings still fire with `sendKeybindingsToShell` enabled is unverified.

1. Outside tmux run `cat -v`, press the key, then Ctrl+D. Repeat inside tmux. A key that arrives
   differently inside tmux is a tmux/terminfo problem; one that never arrives is a VS Code or Windows problem.
2. For a key VS Code still handles, run `Developer: Toggle Keyboard Shortcuts Troubleshooting`, press
   the key once in the terminal, and read the command and `when` clause it reports.
3. Test: F1-F10 in `mc`, Alt+letter, Ctrl+arrows, Backspace, Alt+Backspace, Shift+Backspace, Shift+Enter
   in your agent CLI, and Ctrl+Space in tmux.

Windows reports AltGr as Ctrl+Alt. No file here binds a Ctrl+Alt combination, for that reason.

## Terminal sessions: what survives

Terminal processes are children of the VS Code server in the container. A window reload or a dropped
SSH connection reconnects to the same shells (`terminal.integrated.enablePersistentSessions`). They end
when the server ends: container restart or rebuild, an OOM kill by the cgroup limit, a VS Code update that
replaces the server, or "Kill VS Code Server". For work that must outlive that, start it detached (`nohup`,
a service) or inside tmux in a container that stays up (`shutdownAction: "none"` is set).

## Entering the container without VS Code

On the Docker host: `docker exec -it -u vscode <container> zsh`, where the container is `mdt-cockpit-<USER>-<folder>` (per-repo template) or `mdt-cockpit-<USER>` (host-folder template); `docker ps --format '{{.Names}}'` lists it. That shell lacks
VS Code's environment (`SSH_AUTH_SOCK`, `BROWSER`, the git credential helper). A tmux session started in a
VS Code terminal can be attached from there with `tmux attach -t main`.
