# topos ratatui PoC

Throwaway comparison build of the redesigned topos main screen in ratatui, to try next to the
Python TUI. Not product code. It replays a recording (`topos record` JSONL) in a loop as a stand-in
for a live 1 s feed.

    topos-ratatui-poc <recording.jsonl> [--tick-ms 1000] [--config columns.toml]
    topos-ratatui-poc <recording.jsonl> --bench N [--size WxH]     # CPU ms/frame, max RSS
    topos-ratatui-poc <recording.jsonl> --dump WxH [--frame N] [--noise all|middle|hot] [--select KEY] [--filter TEXT]
    topos-ratatui-poc <recording.jsonl> --plan TABLE_WIDTH         # which columns survive at that width

One view, no lenses: usage and configuration share the row. Column order and priority come from
`columns.example.toml` (looked up as `--config`, then `topos-columns.toml` beside the binary, then the
copy embedded at build time). Narrowing drops the lowest-priority columns, never clips the right side.

Keys: `?` help, `n` noise (all/middle/hot), `/` filter, `s`/`S` sort (or click a header), `Tab` side pane
(automatic from 140 columns), `space` freeze, `,` `.` step, arrows/jk move, Right/Enter/Left fold,
`e`/`c` expand/collapse all, `p` demo processes, `g` flat process ranking, wheel and click, `q` quit.

Real vs demo: everything from the recording is real (cgroup tree, limits, PSI, IO, memory, zswap
pool, swap, interfaces). The recording has no per-CPU, us/sy/io/st, D-state or process data, so
those are drawn from a synthetic generator (`src/demo.rs`) and shown in magenta with `~`/`demo`
labels. "n/a" means the recording has no value, a dim `·` means not set, `0` is a real zero.

Build (static musl binary, in a container; no toolchain in the cockpit):

    docker build -t topos-ratatui-poc-build topos/poc/ratatui-tui
    docker create --name topos-ratatui-poc-extract topos-ratatui-poc-build
    docker cp topos-ratatui-poc-extract:/out/topos-ratatui-poc ./topos-ratatui-poc
    docker rm topos-ratatui-poc-extract

The build runs the unit tests (ratatui `TestBackend`) before producing the binary. Dependencies are
pinned by the committed `Cargo.lock`; every locked crate is at least 14 days past release as of 2026-11-05.
