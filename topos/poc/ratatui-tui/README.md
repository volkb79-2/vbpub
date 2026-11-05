# topos ratatui PoC

Throwaway comparison build of the redesigned topos main screen in ratatui, to try next to the
Python TUI. Not product code. Two sources:

- `--socket PATH`: live, from `topos-sampler` (protocol: `topos/docs/SAMPLER-PROTOCOL.md`). Nothing is invented.
- `<recording.jsonl>`: replay of a `topos record` file in a loop. The recording has no per-CPU, D-state,
  process or zswap-rate data, so those are synthetic there, drawn in magenta with `~`/`demo` labels.

        topos-ratatui-poc --socket /run/topos/sampler.sock [--config columns.toml]
        topos-ratatui-poc <recording.jsonl> [--tick-ms 1000] [--config columns.toml]
        topos-ratatui-poc (<recording.jsonl> | --socket PATH) --bench N [--size WxH]      # CPU ms/frame, max RSS
        topos-ratatui-poc (<recording.jsonl> | --socket PATH) --dump WxH [--frame N] [--noise all|middle|hot] [--select KEY] [--filter TEXT]
        topos-ratatui-poc --plan TABLE_WIDTH | --plan-term TERMINAL_WIDTH                 # which columns survive

## Try it live on a host (copy-paste)

You need two static binaries and two files, from `/home/vscode/topos-poc-20261105/` on the cockpit:
`topos-sampler`, `topos-ratatui-poc`, `sampler.example.toml`, and (optional) `columns.example.toml`.
Copy them to the host (for example `scp` into `/tmp/topos-try/`), then on the host:

    # 1. a private working directory and a config in /tmp (the sampler needs root to read every cgroup and /proc)
    D=/tmp/topos-try; mkdir -p $D/tiers $D/cold && chmod 755 $D
    sed -e "s#/run/topos/sampler.sock#$D/sampler.sock#" \
        -e "s#/var/lib/topos/events.jsonl#$D/events.jsonl#" \
        -e "s#/run/topos/tiers#$D/tiers#" \
        -e "s#/var/lib/topos\"#$D/cold\"#" \
        $D/sampler.example.toml > $D/sampler.toml

    # 2. terminal A: the sampler, as root, in the foreground (Ctrl-C stops it)
    sudo $D/topos-sampler $D/sampler.toml

    # 3. terminal B: the TUI (the socket is mode 0600 root, so the TUI runs as root too)
    sudo $D/topos-ratatui-poc --socket $D/sampler.sock --config $D/columns.example.toml
    #    q quits the TUI; ? shows the keys

    # 4. stop and remove everything
    #    q in terminal B, Ctrl-C in terminal A, then:
    sudo rm -rf /tmp/topos-try

The sampler writes only under `$D` (socket, hot/warm tiers, cold tier, events); it starts no service and
changes nothing else. Without root it still runs but sees only what it may read (`n/a`, never zero).
If the socket path is wrong or the sampler is stopped the TUI does not freeze: the status line shows
`WAITING` or `STALE` with the reason and reconnects by itself.

## One view, columns from a file

No lenses: usage, configuration and stall detail share one row. Column order and priority come from
`columns.example.toml` (`--config`, else `topos-columns.toml` beside the binary, else the example embedded at
build time). Narrowing drops the lowest-priority columns, never clips the right side. The file is a superset of
the Textual view's `assets/columns.toml` ids (every id there is accepted), so one file drives both views. A
hand-written TOML subset parser: `[[column]]` with `id`, `pri`, `width`, `head`, and an optional `[ui]` table:

    [ui]
    pane_width = 46             # side pane columns at any terminal width (at most half the screen)
    pane_auto_min_width = 140   # the pane shows by itself from this width; Tab forces it on or off
    proc_cap = 3                # hot or blocked process rows kept under a collapsed cgroup

The Textual loader ignores the `[ui]` table here (it keeps the same keys in its own `[ui]` config).

Keys: `?` help, `n` noise (all/middle/hot), `/` filter, `s`/`S` sort (or click a header), `Tab` side pane,
`space` freeze, `,` `.` step (live: through the last 120 frames), arrows/jk move, Right/Enter/Left fold,
`e`/`c` expand/collapse all, `p` process rows, `g` flat process ranking, wheel and click, `q` quit.

## Live mode

- A background thread owns the socket: `hello`, then `current` with processes once per sampler tick (it
  retries 100 ms later if the tick has not advanced), `status` every 5 s. It never blocks the UI. Any failure
  (no socket, closed, timeout, bad JSON, protocol error) becomes a message and a reconnect after 0.5 s growing to 5 s.
- The status line always shows the source: `LIVE` (green) / `WAITING` (never answered) / `STALE Ns` (red, no answer
  for 3 ticks or the feed is down, last data kept and the reason shown) / `FROZEN`, then the socket path, sampler
  version, pid, `view:host|namespaced`, epoch, and the sampler's own CPU and RSS. A changed epoch means the sampler
  restarted: history is cleared and `restarts seen N` is shown. `TRUNCATED` shows when the sampler cut the answer.
- Value states: `value` is a number; `not_applicable` is a dim `·`; `unavailable` and anything the sampler does
  not report are `n/a`. Never a zero. `·` also means "no limit" (max).
- Sources: per-CPU bars from `cpu:<n>` `cpupct.*`; `BLK` = `procs.d_state` per cgroup, summed over the subtree
  (root = `pids.d_state`); process rows from `current.procs`; zswap rates from `vm.zswp*` and `zswap.*` counters
  (pages/s); IO lines from `dev:*` (partitions folded into their disk); effective memory.min computed along the
  ancestor chain; memory stack from `memstack.*`.
- Not in the sampler, shown `n/a`: per-cgroup network (NET_*), swap used per cgroup (SWAP), `pids.max` (PIDS.MAX),
  IOCOST per cgroup, and who set a limit (ORIGIN, DRIFT, DRIFTED).
- Process rows float under their owning cgroup when hot (cpu >= 3%, io >= 1 MiB/s) or blocked (D), blocked first
  then by CPU, at most `proc_cap` per collapsed cgroup; an expanded cgroup lists every process the sampler sent
  (the sampler's own cap is `process.cap`, default 64). `g` ranks all processes flat.

Recording mode details: everything from the recording is real (cgroup tree, limits, PSI, IO, memory, zswap pool,
swap, interfaces); magenta/`~` is synthetic. `n/a` = the recording has no value, `·` = not set, `0` = real zero.

## Build (static musl binary, in a container; no toolchain in the cockpit)

    docker build -t topos-ratatui-poc-build topos/poc/ratatui-tui
    docker create --name topos-ratatui-poc-extract topos-ratatui-poc-build
    docker cp topos-ratatui-poc-extract:/out/topos-ratatui-poc ./topos-ratatui-poc
    docker rm topos-ratatui-poc-extract
    # the sampler:  docker build -f topos/sampler/Dockerfile --target artifact -o type=local,dest=out topos   (-> out/topos-sampler)

The build runs the unit tests (ratatui `TestBackend`, plus a fake sampler on a Unix socket for the live feed:
values, n/a vs dot, process floating and cap, dead socket, sampler going away, reconnect, epoch change) before
producing the binary. Dependencies are pinned by the committed `Cargo.lock`; every locked crate is at least 14 days
past release as of 2026-11-05.
