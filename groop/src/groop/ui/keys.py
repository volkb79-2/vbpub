from __future__ import annotations

from textual.binding import Binding


BINDINGS = (
    Binding("q", "quit", "Quit"),
    Binding("f5", "toggle_view", "View"),
    Binding("t", "toggle_view", "View", show=False),
    Binding("tab", "cycle_profile", "Profile"),
    Binding("p", "cycle_profile", "Profile"),
    Binding("f6", "cycle_sort", "Sort"),
    Binding("s", "cycle_sort", "Sort", show=False),
    Binding("/", "open_filter", "Filter"),
    Binding("left,h", "collapse_tree", "Collapse", show=False),
    Binding("right,l", "expand_tree", "Expand", show=False),
    Binding("b", "toggle_banner", "Banner"),
    Binding("space", "toggle_replay_pause", "Play/Pause", show=False),
    Binding("comma", "replay_step_back", "Replay-", show=False),
    Binding("full_stop", "replay_step_forward", "Replay+", show=False),
    Binding("plus", "replay_speed_up", "ReplayFast", show=False),
    Binding("minus", "replay_speed_down", "ReplaySlow", show=False),
    Binding("k", "reserved_v2_action", "Admin", show=False),
    Binding("x", "create_snapshot", "Snapshot"),
    Binding("m", "open_host_memory", "Memory"),
    Binding("enter", "open_drill", "Detail", show=False),
    Binding("escape", "close_overlay", "Back", show=False),
    Binding("j", "select_next", "Down", show=False),
    Binding("up", "select_prev", "Up", show=False),
    Binding("down", "select_next", "Down", show=False),
    Binding("f1", "open_help", "Help"),
    Binding("question_mark", "open_help", "Help", show=False),
)


def key_help() -> tuple[str, ...]:
    return (
        "F5/t view toggle",
        "Left/h collapse tree branch",
        "Right/l expand tree branch",
        "Tab/p cycle profile",
        "F6/s cycle sort",
        "/ filter rows",
        "Space replay play/pause",
        ",/. replay back/forward",
        "+/- replay speed",
        "k reserved v2 admin action",
        "x snapshot selected row",
        "m host memory",
        "Enter drill-down",
        "b collapse banner",
        "F1/? glossary",
        "q quit",
    )
