"""The application shell: left navigation mirroring the web UI, a content pane, command palette, key bindings."""
from __future__ import annotations

from typing import Iterable

from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, Tree

from ..menu import MENU, MenuItem, walk
from ..session import Session
from .disks import DisksView

VIEWS = {"storage/disks": DisksView}


def placeholder(item: MenuItem) -> Static:
    where = f"milestone {item.milestone}"
    rows = f" Action-matrix rows: {', '.join(item.matrix)}." if item.matrix else ""
    return Static(f"{item.label}: planned, {where}.{rows}\nSee docs/DESIGN.md and BACKLOG.md.", id="view-planned")


class TntApp(App):
    TITLE = "tnt"
    CSS = """
    #nav { width: 30; border-right: solid $primary; }
    #content { width: 1fr; }
    #banner { height: 1; background: $boost; padding: 0 1; }
    #banner.insecure { background: $error; color: $text; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("ctrl+n", "focus_nav", "Menu"), ("r", "refresh", "Refresh"), ("question_mark", "help", "Help")]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.current: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        banner = Static(self.session.banner(), id="banner")
        if self.session.insecure_tls:
            banner.add_class("insecure")
        yield banner
        with Horizontal():
            tree: Tree[MenuItem] = Tree("TrueNAS", id="nav")
            tree.show_root = False
            for item in MENU:
                self._add(tree.root, item)
            yield tree
            yield Horizontal(id="content")
        yield Footer()

    def _add(self, parent, item: MenuItem) -> None:
        node = parent.add(item.label, data=item, expand=item.status == "implemented") if item.children else parent.add_leaf(item.label, data=item)
        for child in item.children:
            self._add(node, child)

    def on_mount(self) -> None:
        self.query_one("#nav", Tree).focus()
        self.sub_title = self.session.profile.name

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        item = event.node.data
        if item is not None and not item.children:
            self.show(item)

    def show(self, item: MenuItem) -> None:
        content = self.query_one("#content")
        content.remove_children()
        factory = VIEWS.get(item.key)
        content.mount(factory(self.session) if factory else placeholder(item))
        self.current = item.key

    def action_refresh(self) -> None:
        """Refresh the open view; bound on the app so it works while the menu has focus."""
        for view in self.query_one("#content").children:
            refresh = getattr(view, "action_refresh", None)
            if refresh is not None:
                refresh()

    def action_focus_nav(self) -> None:
        self.query_one("#nav", Tree).focus()

    def action_help(self) -> None:
        self.notify("arrows/enter: navigate, ctrl+p: command palette (go to any screen), r: refresh, q: quit", title="Keys")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        for path, item in walk():
            if not item.children:
                yield SystemCommand(f"Go to {' > '.join(path)}", f"{item.status}, {item.milestone}", lambda i=item: self.show(i))
