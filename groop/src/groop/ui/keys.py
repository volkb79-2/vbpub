from __future__ import annotations

from textual.binding import Binding


BINDINGS = (
    Binding("q", "quit", "Quit"),
    Binding("f5", "toggle_view", "View"),
    Binding("t", "toggle_view", "View", show=False),
    Binding("p", "cycle_profile", "Profile"),
    Binding("f6", "cycle_sort", "Sort"),
    Binding("s", "cycle_sort", "Sort", show=False),
    Binding("/", "open_filter", "Filter"),
    Binding("b", "toggle_banner", "Banner"),
    Binding("x", "create_snapshot", "Snapshot"),
    Binding("m", "open_host_memory", "Memory"),
    Binding("enter", "open_drill", "Detail", show=False),
    Binding("escape", "close_overlay", "Back", show=False),
    Binding("up", "select_prev", "Up", show=False),
    Binding("down", "select_next", "Down", show=False),
    Binding("f1", "open_help", "Help"),
    Binding("question_mark", "open_help", "Help", show=False),
)


def key_help() -> tuple[str, ...]:
    return (
        "F5/t view toggle",
        "p cycle profile",
        "F6/s cycle sort",
        "/ filter rows",
        "x snapshot selected row",
        "m host memory",
        "Enter drill-down",
        "b collapse banner",
        "F1/? glossary",
        "q quit",
    )
