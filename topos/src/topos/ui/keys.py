from __future__ import annotations

from textual.binding import Binding


BINDINGS = (
    Binding("q", "quit", "Quit"),
    Binding("question_mark", "open_help", "Help"),
    Binding("f1", "open_help", "Help", show=False),
    Binding("a", "cycle_noise", "Rows"),
    Binding("slash", "open_filter", "Filter"),
    Binding("s", "cycle_sort", "Sort"),
    Binding("f6", "cycle_sort", "Sort", show=False),
    Binding("S", "reverse_sort", "Reverse", show=False),
    Binding("tab", "toggle_pane", "Pane", priority=True),
    Binding("space", "toggle_replay_pause", "Freeze"),
    Binding("comma", "replay_step_back", "Step-", show=False),
    Binding("full_stop", "replay_step_forward", "Step+", show=False),
    Binding("left,h", "collapse_tree", "Collapse", show=False),
    Binding("right,l", "expand_tree", "Expand", show=False),
    Binding("e", "expand_all", "Expand all", show=False),
    Binding("c", "collapse_all", "Collapse all", show=False),
    Binding("p", "toggle_procs", "Procs", show=False),
    Binding("g", "toggle_flat_procs", "Flat procs", show=False),
    Binding("v", "toggle_view", "View"),
    Binding("f5", "toggle_view", "View", show=False),
    Binding("plus", "replay_speed_up", "ReplayFast", show=False),
    Binding("minus", "replay_speed_down", "ReplaySlow", show=False),
    Binding("home", "replay_first", "ReplayFirst", show=False),
    Binding("end", "replay_last", "ReplayLast", show=False),
    Binding("j", "replay_jump_prompt", "ReplayJump", show=False),
    Binding("x", "create_snapshot", "Snapshot"),
    Binding("m", "open_host_memory", "Memory"),
    Binding("escape", "close_overlay", "Back", show=False),
)


def key_help() -> tuple[str, ...]:
    return (
        "a rows: all / middle (default) / hot only, with a hidden count",
        "/ filter by name, path or image (Esc clears)",
        "s / S cycle sort column / reverse; click a header to sort",
        "Tab side pane on/off (shown by itself from the configured width)",
        "Space freeze (live and replay); , . step back / forward one frame",
        "Left/h collapse or go to parent, Right/l expand, e / c expand / collapse all",
        "Up/Down move; click a row to select, click its fold glyph to fold",
        "p processes on/off, g flat process ranking (needs a process source)",
        "v flip tree / flat container list / CIU-grouped containers",
        "+/- replay speed, Home/End replay first/last, j replay jump to frame/ts",
        "Enter drill-down, x snapshot the selected row, m host memory",
        "? or F1 this help",
        "q quit",
    )
