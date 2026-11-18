# VS Code files for the SSH + devcontainer + integrated terminal setup

The devcontainer template already starts each VS Code integrated terminal in its own tmux session.
The settings file below is a reference for another settings layer or for overriding that profile.
The remaining files are copy-in snippets; pick what you want.

| File | Where it applies | What it does |
|---|---|---|
| `terminal-keys-terminal-first.settings.jsonc` | settings (container or user) | Sends most keys to the terminal while it has focus. |
| `terminal-keys-balanced.settings.jsonc` | settings (container or user) | Keeps most VS Code shortcuts; releases a listed set (Ctrl+J, Ctrl+P/E, F1, F2, Ctrl+F, F3-F11 family) to the terminal. The file lists what each removal costs. |
| `terminal-tmux.settings.jsonc` | settings (container or user) | Defines the template's default tmux terminal profile, with a unique session per terminal. |
| `keybindings.client.jsonc` | **client** `keybindings.json` | Shift+Backspace as delete-word, an editor-focus key, optional Shift+Enter. |
| `multi-root.code-workspace.example` | a **`.code-workspace`** file | Multi-root workspace with the file-watcher exclusions and Git detection settings that keep a large estate under the inotify limit (section "File watcher exhaustion"). |

Use ONE of the two key profiles, not both.

## Which settings layer

The VS Code window runs on your Windows machine and the VS Code server runs in the container. A
setting is read from the layer that owns it:

- `.vscode/settings.json` in a workspace folder: that folder, for everyone who opens it.
- The `"settings"` of a `.code-workspace` file: every folder of that multi-root workspace.
- The devcontainer's `customizations.vscode.settings`: written into the remote/machine settings file
  below, which a rebuild does not rewrite (see next section).
- Remote/machine settings (`~/.vscode-server/data/Machine/settings.json` in the container): every
  workspace opened in this container, kept in the persisted home. These settings control the remote
  Linux terminal; Windows user settings can override them.
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

- Merge the changed keys into `~/.vscode-server/data/Machine/settings.json` by hand, then run `Developer: Reload Window`.
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
activated by a file there (Terraform was, by `.tf` files in Codex's plugin cache). Such a switch is
often window-scoped and ignored in a folder's settings file: `"terraform.languageServer.enable": false`
goes in the `.code-workspace` `"settings"` (or the machine settings) and turns it off for the whole window.

## File watcher exhaustion (measured 2026-11-18)

Symptom: the VS Code server log (`~/.vscode-server/data/logs/<session>/remoteagent.log`) shows
`[File Watcher ('parcel')] Inotify limit reached (ENOSPC) (path: ...)` and
`Failed to watch <file> for changes using fs.watch() ... ENOSPC`; the "unable to watch for file changes"
notification appears; other tools in the container (cargo, pytest, `tail -f`) can fail with "no space for
watches". `fs.inotify.max_user_watches` is a per-user limit, shared by every process and every container of that
user (`host-setup/README.md`, "inotify watch budget").

What was measured (container with a 6-folder multi-root workspace, 490k directories under one repo):

- The watcher process held 522,385 of 524,288 watches; no other process held more than 50. About 99% of the
  directories of the generated trees (`.run-gate` 300k, `.worktrees` 138k, `artifacts` 36k) were watched even though
  `files.watcherExclude` listed them in the container's `devcontainer.metadata` label.
- Trace logging showed why: every watcher request carried only VS Code's built-in excludes (`.git/objects`,
  `.git/subtree-cache`, `.hg/store`, the extensions folder). The template's block had never reached the window,
  which matches MDT-076: the machine settings file was written before the block was added and a rebuild does not
  rewrite it. The request that exhausted the budget was for the largest repo; after the ENOSPC VS Code demoted it to
  polling ("SUSPENDED <polling>") while the other roots stayed watched.
- Disabling the Terraform and HCL extensions changed nothing (checked before the cause was known). Reloading the
  window did not help either: a reload re-reads the same machine settings.
- After the `files.watcherExclude` block was put into the **`.code-workspace` file's `"settings"`** and the window
  reloaded, the container's total inotify watches fell from 523,900 to 9,400 and the ENOSPC errors stopped.

So: put the exclusion block where the window reads it on every open, the workspace file (template:
[`multi-root.code-workspace.example`](multi-root.code-workspace.example)), or merge it into the machine settings file
as described above. A single-folder window can use `.vscode/settings.json` in that folder. Keep the patterns in the
`**/name/**` form. Relative paths are taken relative to each watch root and absolute paths as given; an absolute
path with a glob such as `/home/vscode/.codex*/**` was not honoured in one measurement (the watcher still tried a
temporary directory under it), so use the `**/` form.

How to check what the watcher uses (read-only):

1. Total watches (the limit is `cat /proc/sys/fs/inotify/max_user_watches`):
   `find /proc/[0-9]*/fdinfo -type f 2>/dev/null | xargs grep -h '^inotify wd:' 2>/dev/null | wc -l`
2. Per process, find the process with the most: sum the `inotify wd:` lines of each `/proc/<pid>/fdinfo/*`; the
   VS Code watcher is the `node` process whose command line contains `fileWatcher`.
3. Which requests it holds, with their excludes: `Developer: Set Log Level...`, pick **Server** (not "All"), choose
   **Trace**, reload the window, and read the `[File Watcher] request stats` block in `remoteagent.log` (recursive
   requests with their `excludes:`, and a `[SUSPENDED <polling>]` mark on one that failed). Set the level back to
   **Info** afterwards: the log directory is under the watched home, so trace logging feeds itself.
4. A directory count shows where the budget goes: `dust -d 1 <folder>` or `gdu` for sizes; for directories,
   `find <folder> -type d | wc -l`.

Two more causes of a high count, independent of the settings: scratch trees that tools leave behind (tens of
thousands of small directories under a repo's `.run-gate`, one per finished gate run) and a multi-root workspace
that includes the home folder (tool state such as `~/.codex`, `~/.npm`, `~/.cache`). Clean the scratch with its own
tool; exclude the home state as in the template. The host-side sysctl (`host-setup/README.md`) is the last resort,
not the first.

The Git extension adopts repositories it finds, including linked worktrees that scratch tools create and delete.
The template sets `git.autoRepositoryDetection: "subFolders"`, `git.openRepositoryInParentFolders: "never"` and
the ignored-folders list; whether a given VS Code version stops opening a scratch worktree as a repository was not
verified (no scratch worktree was created after the setting was applied).

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

The template starts each new VS Code terminal in a separate tmux session named `vscode-<UTC time>-<PID>`.
The profile starts zsh inside that session. The unique name keeps separate VS Code terminals from sharing
one pane. A VS Code window/server restart or dropped connection does not stop the tmux server while the
container stays up (`shutdownAction: "none"` is set). A container restart, rebuild or OOM kill does stop
it. To list sessions, run `tmux ls`. From a shell outside tmux, attach with `tmux attach -t <session>`;
from inside tmux, switch with `tmux switch-client -t <session>`. After the work is finished, remove
its session with `tmux kill-session -t <session>`.

Before restarting the VS Code server, move any already-running integrated-terminal processes with
`reptyr -T`; the recipe has the process list, tree view, and guarded command:
[`move-running-VSC-terminal-session-to-tmux-reptyr.md`](../../docs/move-running-VSC-terminal-session-to-tmux-reptyr.md).
The `-T` mode transfers the processes attached to that PTY. A process with `TTY ?` is detached from it
and needs separate inspection. The Claude Code extension-host process has no terminal and cannot be
moved to tmux; after reload, restore its conversation from the extension's Session history if it did not
reopen automatically.

## Entering the container without VS Code

On the Docker host: `docker exec -it -u vscode <container> zsh`, where the container is `mdt-cockpit-<USER>-<folder>` (per-repo template) or `mdt-cockpit-<USER>` (host-folder template); `docker ps --format '{{.Names}}'` lists it. That shell lacks
VS Code's environment (`SSH_AUTH_SOCK`, `BROWSER`, the git credential helper). List sessions with
`tmux ls`, then attach from there with `tmux attach -t <session>`.
