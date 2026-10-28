"""Storage > Disks: a DataTable fed by ``Session.disks()``, loaded in a worker thread."""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..domain import Disk
from ..query import HEADERS, disk_row
from ..session import Session


class DisksView(Vertical):
    DEFAULT_CSS = """
    DisksView { height: 1fr; }
    DisksView #disks-note { height: auto; color: $text-muted; padding: 0 1; }
    """
    def __init__(self, session: Session):
        super().__init__(id="view-disks")
        self.session = session
        self.disks: list[Disk] = []

    def compose(self) -> ComposeResult:
        yield Static("Loading disks...", id="disks-note")
        table = DataTable(id="disks-table", cursor_type="row", zebra_stripes=True)
        table.add_columns(*HEADERS)
        yield table

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#disks-note", Static).update("Loading disks...")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        try:
            disks = self.session.disks()
        except Exception as exc:  # shown in the view; the CLI path reports the same failure as one [ERROR] line
            self.app.call_from_thread(self._failed, str(exc))
        else:
            self.app.call_from_thread(self._loaded, disks)

    def _failed(self, message: str) -> None:
        self.query_one("#disks-note", Static).update(f"Failed: {message}")

    def _loaded(self, disks: list[Disk]) -> None:
        self.disks = disks
        table = self.query_one("#disks-table", DataTable)
        table.clear()
        for d in disks:
            table.add_row(*disk_row(d), key=d.name)
        unknown = sum(1 for d in disks if d.partitions is None)
        note = f"{len(disks)} disk(s)."
        if unknown:
            note += f" {unknown} without partition data (free space shown as ?)."
        self.query_one("#disks-note", Static).update(note)
