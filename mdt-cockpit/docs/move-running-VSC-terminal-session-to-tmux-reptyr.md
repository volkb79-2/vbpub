# recipe to move running VSC terminal sessions into `tmux` with `reptyr`

`tmux` keeps terminal processes running when the VS Code window or server disconnects or restarts, as long as the container stays running (`shutdownAction: "none"`). It does not survive a container restart, rebuild or OOM kill. The VS Code templates start new integrated terminals in separate tmux sessions; this recipe is for terminals that were already open before that setting was applied.

The MDT devcontainer image includes `tmux` and `reptyr`. The commands below use `reptyr -T`, which steals the terminal's master PTY instead of changing the target processes. The `-T` mode is experimental, but it can attach all processes running on one terminal. It uses `ptrace` to access the terminal-emulator process. The templates grant `SYS_PTRACE` for this; a rebuild is needed before an existing container gets the capability. The host's ptrace policy still applies. The live migration worked with the current host's `ptrace_scope=0`, without the container capability. The ordinary `reptyr PID` mode uses `ptrace` on the target and does not reliably move a process's children. See the [upstream TTY-stealing explanation](https://blog.nelhage.com/2014/08/new-reptyr-feature-tty-stealing/).

## list terminals and processes

Run these from a shell in the same container. `comm` shows process names without printing command arguments or Claude prompts.

```bash
# all processes with a process tree, including PIDs, parent PIDs and terminal names
ps -u "$(id -u)" -o pid,ppid,sid,pgid,tpgid,tty,stat,etime,comm --forest

# processes attached to VS Code pseudo-terminals only
ps -u "$(id -u)" -o pid=,ppid=,sid=,pgid=,tpgid=,tty=,stat=,comm= |
  awk '$6 ~ /^pts\// { print }'

# foreground Claude CLI processes and their terminals
ps -u "$(id -u)" -o pid=,tty=,pgid=,tpgid=,comm= |
  awk '$2 ~ /^pts\// && $3 == $4 && $5 == "claude" { print $1, $2 }'

# processes attached to one terminal, as a tree (replace pts/1 with the TTY above)
ps -t pts/1 -o pid,ppid,sid,pgid,tpgid,tty,stat,etime,comm --forest

# descendants and ancestors of a selected process (no command arguments)
pstree -p <claude-pid>
pstree -ps <claude-pid>
```

The foreground filter expects the process name to be `claude`. If it finds nothing, inspect that TTY with `ps -t <tty> ...` and identify the foreground process group (`TPGID`) before proceeding. Do not guess a PID. `jobs -l` only shows jobs known to the shell where it runs; it does not list every subprocess Claude started.

The Claude Code VS Code extension has no TTY, so it will not appear in the terminal list. Its recovery path is separate; see [Claude Code VS Code extension](#claude-code-vs-code-extension) below.

## move terminal sessions from outside the sessions

Review the candidate list first. Run this from a separate interactive control terminal in the same container and PID namespace, as the same user that owns the Claude processes. It creates one tmux session per foreground Claude terminal and sends `reptyr -T` to that session; it does not require typing in the source terminal. It asks before moving each PID and checks that the PID is still a foreground Claude process on the same TTY. It cannot be run from Windows or a shell outside the container.

```bash
bash <<'BASH'
set -euo pipefail

candidates="$(ps -u "$(id -u)" -o pid=,tty=,pgid=,tpgid=,comm= |
  awk '$2 ~ /^pts\// && $3 == $4 && $5 == "claude" { print $1, $2 }')"

if [[ -z "$candidates" ]]; then
  echo "No foreground Claude CLI process on a VS Code terminal."
  exit 0
fi

printf 'PID TTY\n%s\n' "$candidates"
while read -r pid tty; do
  session="claude-$(printf '%s' "$tty" | tr '/' '-')"
  if ! current="$(ps -p "$pid" -o tty=,pgid=,tpgid=,comm=)" || [[ -z "$current" ]]; then
    printf 'REFUSED: PID %s exited; inspect the terminal again before moving.\n' "$pid" >&2
    continue
  fi
  read -r current_tty current_pgid current_tpgid current_comm <<< "$current"
  if [[ "$current_tty" != "$tty" || "$current_pgid" != "$current_tpgid" || "$current_comm" != claude ]]; then
    printf 'REFUSED: PID %s changed or is no longer foreground; inspect it again before moving.\n' "$pid" >&2
    continue
  fi
  if tmux has-session -t "$session" 2>/dev/null; then
    printf 'REFUSED: tmux session already exists: %s\n' "$session" >&2
    continue
  fi
  read -r -p "Move Claude PID $pid on $tty to tmux session $session? [y/N] " answer </dev/tty
  case "$answer" in
    y|Y) ;;
    *) continue ;;
  esac
  tmux new-session -d -s "$session"
  tmux send-keys -t "$session" "reptyr -T $pid" C-m
  printf 'Sent reptyr command; attach and verify before closing VS Code: tmux attach -t %s\n' "$session"
done <<< "$candidates"
BASH
```

`send-keys` submits the command; it does not report whether `reptyr` succeeded. Attach to each session and confirm the Claude screen is present and responsive before closing the old VS Code window or allowing a server update:

```bash
tmux ls
tmux attach -t claude-pts-1
```

List the tmux pane processes without printing their command arguments:

```bash
tmux list-panes -a -F '#{session_name} pid=#{pane_pid} tty=#{pane_tty} command=#{pane_current_command}'
```

With `-T`, the tmux pane runs the `reptyr` proxy. Claude keeps its original PID and TTY name; that is expected because `reptyr` steals the PTY master instead of changing Claude's file descriptors. Do not run `reptyr` a second time for that PID.

`/etc/tmux.conf` uses `Ctrl+Space` as the tmux prefix. Detach with `Ctrl+Space`, then `d`. Reattach from a shell outside tmux with `tmux attach -t <session-name>`. From inside another tmux session, use `tmux switch-client -t <session-name>`.

When the work is finished, remove its saved session with `tmux kill-session -t <session-name>`.

## children and jobs

`reptyr -T` transfers the processes attached to the selected terminal. Shell jobs and children that still use that terminal are included. A process with its own TTY needs its own transfer. A process with `TTY ?` has no controlling terminal, so `-T` will not include it. Such a child may keep running while the container stays up, but whether it survives a VS Code server restart depends on its parent, open pipes and signal handling. Inspect those children with `ps`/`pstree`; do not assume they moved or will keep running. If one is important, arrange its own tmux session or another durable owner before the restart.

Do not run `disown` or send signals to the old shell as a cleanup step. `-T` moves the terminal's attached processes together; first verify the new tmux session and inspect the source TTY. When moving one process with ordinary `reptyr PID`, the source shell may show a stopped/background job and `bg; disown` can be relevant, but that is a different mode and does not move the whole terminal tree.

## Claude Code VS Code extension

The extension-host Claude process has no terminal and cannot be moved into tmux with `reptyr`. VS Code owns that process. After a server/window reload, an editor-tab conversation reopens with its tab; a sidebar conversation reopens if it had activity in the last 10 minutes. Otherwise open Claude's Session history and select it. Claude Code v2.1.274 and later can continue a step interrupted by a reload when the session is restored within an hour and is not open elsewhere. The extension process restarts, and separate child jobs are not guaranteed to continue.

See [Claude Code in VS Code: continue conversations after a reload](https://code.claude.com/docs/en/vs-code#continue-conversations-after-a-reload) and [the CLI session commands](https://code.claude.com/docs/en/cli-usage).
