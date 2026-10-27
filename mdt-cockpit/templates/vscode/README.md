# Opt-in VS Code files for the SSH + devcontainer + integrated terminal setup

Nothing here is applied by the template. Each file is a copy-in snippet; pick what you want.

| File | Where it applies | What it does |
|---|---|---|
| `terminal-keys-terminal-first.settings.jsonc` | settings (container or user) | Sends most keys to the terminal while it has focus. |
| `terminal-keys-balanced.settings.jsonc` | settings (container or user) | Keeps most VS Code shortcuts; releases a listed set (Ctrl+J, Ctrl+P/E, F1, Ctrl+F, F3-F11 family) to the terminal. The file lists what each removal costs. |
| `terminal-tmux.settings.jsonc` | settings (container or user) | Adds a "tmux" terminal profile (attach-or-create session `main`). |
| `keybindings.client.jsonc` | **client** `keybindings.json` | Shift+Backspace as delete-word, an editor-focus key, optional Shift+Enter. |

Use ONE of the two key profiles, not both.

## Which settings layer

The VS Code window runs on your Windows machine and the VS Code server runs in the container. A
setting is read from the layer that owns it:

- `.vscode/settings.json` in the workspace, or the devcontainer's `customizations.vscode.settings`:
  applies to everyone who opens this workspace in the container.
- Remote/machine settings (`~/.vscode-server/data/Machine/settings.json` in the container): this
  container only, kept in the persisted home.
- User settings on Windows: every window of yours.
- Keybindings and fonts are client-side: `keybindings.json`, and `terminal.integrated.fontFamily` (the
  font must be installed on Windows; for an icon-heavy prompt use a Nerd Font family name exactly as
  Windows lists it).

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

On the Docker host: `docker exec -it -u vscode <container> zsh`, where the container is `<folder>-devcontainer-<USER>` (per-repo template) or `mdt-cockpit-<USER>` (host-folder template); `docker ps --format '{{.Names}}'` lists it. That shell lacks
VS Code's environment (`SSH_AUTH_SOCK`, `BROWSER`, the git credential helper). A tmux session started in a
VS Code terminal can be attached from there with `tmux attach -t main`.
