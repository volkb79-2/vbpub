"""Non-blocking, bounded read-only overview screens for the It2 core views."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from ..session import Session
from .session_work import run as run_session_work


class CoreReadView(Vertical):
    """One schema-checked read model, with a single in-flight poll and common operator controls."""

    DEFAULT_CSS = """
    CoreReadView { height: 1fr; }
    CoreReadView .core-note { height: auto; padding: 0 1; color: $text-muted; }
    CoreReadView .core-error { height: auto; padding: 0 1; color: $error; }
    CoreReadView .core-help { height: auto; padding: 0 1; display: none; }
    CoreReadView .core-help.visible { display: block; }
    CoreReadView #core-empty { display: none; height: auto; padding: 0 1; color: $text; }
    CoreReadView #core-controls { height: 3; padding: 0 1; }
    CoreReadView #core-search { width: 1fr; }
        CoreReadView #jobs-prev, CoreReadView #jobs-next { display: none; }
    CoreReadView #core-table { height: 1fr; }
    """
    BINDINGS = [("/", "search", "Search"), ("c", "copy", "Copy")]

    VIEW_KEY = ""
    INTERVAL_SECONDS = 60
    COLUMNS: tuple[str, ...] = ()

    def __init__(self, session: Session):
        super().__init__(id=f"view-{self.VIEW_KEY}", classes="core-read-view")
        self.session = session
        self.loading = False
        self.last_success: datetime | None = None
        self.raw_rows: list[tuple[str, ...]] = []
        self.last_payload: Any = None
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static(f"{self.VIEW_KEY.title()} · read-only", classes="core-note", id="core-status")
        with Horizontal(id="core-controls"):
            yield Input(placeholder="Filter visible rows", id="core-search")
            if self.VIEW_KEY == "jobs":
                yield Button("Previous", id="jobs-prev", disabled=True)
                yield Button("Next", id="jobs-next", disabled=True)
            yield Button("Refresh", id="core-refresh")
            yield Button("Copy", id="core-copy")
            yield Button("Help", id="core-help-button")
        yield Static("Refresh runs in the background. Press / to filter; r refreshes; c copies.",
                     classes="core-help", id="core-help")
        yield Static("", classes="core-error", id="core-error")
        yield Static("", id="core-empty")
        table = DataTable(id="core-table", cursor_type="row", zebra_stripes=True)
        table.add_columns(*self.COLUMNS)
        yield table

    def on_mount(self) -> None:
        self.action_refresh()
        self._timer = self.set_interval(self.INTERVAL_SECONDS, self.action_refresh)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.pause()

    def action_refresh(self) -> None:
        if self.loading:
            return
        self.loading = True
        self.query_one("#core-status", Static).update(
            f"{self.VIEW_KEY.title()} · refreshing in background · poll limit {self.INTERVAL_SECONDS}s")
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        run_session_work(self, self._fetch, "_loaded", "_failed")

    def _fetch(self, session: Session) -> Any:
        return getattr(session.read_views, self.VIEW_KEY)()

    def _failed(self, message: str) -> None:
        self.loading = False
        safe_message = self.session.job_registry.sanitize_error(message)
        self.query_one("#core-error", Static).update(f"Unavailable: {safe_message}")
        if self.last_success is None:
            self.query_one("#core-status", Static).update("No successful sample is available.")
        else:
            self.query_one("#core-status", Static).update(
                f"Showing last successful sample from {self.last_success.astimezone().strftime('%H:%M:%S %Z')}; refresh failed.")

    def _loaded(self, payload: Any) -> None:
        self.loading = False
        self.last_success = datetime.now(timezone.utc)
        self.last_payload = payload
        self.query_one("#core-error", Static).update("")
        self._render_payload(payload)
        stamp = self.last_success.astimezone().strftime("%H:%M:%S %Z")
        visible_rows = self.query_one("#core-table", DataTable).row_count
        self.query_one("#core-status", Static).update(
            f"{self.VIEW_KEY.title()} · {visible_rows} visible row(s) · refreshed {stamp} · "
            f"next poll in {self.INTERVAL_SECONDS}s · read-only")

    def _rows(self, payload: Any) -> list[tuple[str, ...]]:
        raise NotImplementedError

    @staticmethod
    def _cell(value: Any) -> str:
        if value is None:
            return "Unknown"
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, sort_keys=True, default=str)
        return str(value)

    def _render_payload(self, payload: Any) -> None:
        self.raw_rows = self._rows(payload)
        self._apply_filter()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "core-search":
            self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.query_one("#core-search", Input).value.strip().casefold()
        rows = [row for row in self.raw_rows if not query or query in " ".join(row).casefold()]
        table = self.query_one("#core-table", DataTable)
        table.clear()
        for index, row in enumerate(rows):
            table.add_row(*row, key=str(index))
        empty = self.query_one("#core-empty", Static)
        empty.update("No rows match this filter." if query else "No rows are available.")
        empty.display = not rows
        if self.last_success is not None:
            self.query_one("#core-status", Static).update(
                f"{self.VIEW_KEY.title()} · {len(rows)} visible row(s) · refreshed "
                f"{self.last_success.astimezone().strftime('%H:%M:%S %Z')} · next poll in {self.INTERVAL_SECONDS}s")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "core-refresh":
            self.action_refresh()
        elif event.button.id == "core-copy":
            self.action_copy()
        elif event.button.id == "core-help-button":
            self.action_help()

    def action_search(self) -> None:
        self.query_one("#core-search", Input).focus()

    def action_copy(self) -> None:
        visible = [row for row in self.raw_rows
                   if not self.query_one("#core-search", Input).value.strip().casefold()
                   or self.query_one("#core-search", Input).value.strip().casefold() in " ".join(row).casefold()]
        self.app.copy_to_clipboard("\n".join("\t".join(row) for row in visible))
        self.app.notify(f"Copied {len(visible)} visible row(s).")

    def action_help(self) -> None:
        panel = self.query_one("#core-help", Static)
        panel.toggle_class("visible")


class DashboardView(CoreReadView):
    VIEW_KEY = "dashboard"
    INTERVAL_SECONDS = 30
    COLUMNS = ("Fact", "Value")

    def _rows(self, payload: dict[str, Any]) -> list[tuple[str, ...]]:
        rows = [("Host", self._cell(payload["host"]["name"])),
                ("TrueNAS hostname", self._cell(payload["host"]["hostname"])),
                ("Version", self._cell(payload["host"].get("version"))),
                ("CPU model", self._cell(payload.get("model"))),
                ("Cores", self._cell(payload.get("cores"))),
                ("Physical cores", self._cell(payload.get("physical_cores"))),
                ("Load average (1/5/15 min)", self._cell(payload.get("loadavg"))),
                ("Memory bytes", self._cell(payload.get("physmem"))),
                ("Uptime", self._cell(payload.get("uptime")))]
        for graph in ("cpu", "memory"):
            data = payload.get(graph, {})
            if data.get("status") == "available":
                rows.append((f"{graph.upper()} history (min / mean / max)", self._cell(data.get("aggregations"))))
            else:
                rows.append((f"{graph.upper()} history", f"Unavailable: {data.get('error', 'unknown reason')}"))
        return rows


class AlertsView(CoreReadView):
    VIEW_KEY = "alerts"
    INTERVAL_SECONDS = 60
    COLUMNS = ("Level", "Alert", "Source", "First seen", "Last seen", "Dismissed", "Node")

    def _rows(self, payload: list[dict[str, Any]]) -> list[tuple[str, ...]]:
        return [(self._cell(row["level"]), self._cell(row["text"]), self._cell(row["source"]),
                 self._cell(row["datetime"]), self._cell(row["last_occurrence"]),
                 self._cell(row["dismissed"]), self._cell(row["node"])) for row in payload]


class JobsView(CoreReadView):
    VIEW_KEY = "jobs"
    INTERVAL_SECONDS = 5
    COLUMNS = ("Job ID", "Method", "State", "Progress", "Log", "Error")

    def __init__(self, session: Session):
        super().__init__(session)
        self.page_offset = 0

    def _fetch(self, session: Session) -> Any:
        return session.read_views.jobs(limit=200, offset=self.page_offset)

    def on_mount(self) -> None:
        self.query_one("#jobs-prev", Button).display = True
        self.query_one("#jobs-next", Button).display = True
        super().on_mount()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "jobs-prev":
            self.page_offset = max(0, self.page_offset - 200)
            self.action_refresh()
        elif event.button.id == "jobs-next":
            if isinstance(self.last_payload, dict) and self.last_payload.get("possibly_more"):
                self.page_offset += 200
                self.action_refresh()
        else:
            super().on_button_pressed(event)

    def _loaded(self, payload: dict[str, Any]) -> None:
        super()._loaded(payload)
        self.query_one("#jobs-prev", Button).disabled = self.page_offset == 0
        self.query_one("#jobs-next", Button).disabled = not payload["possibly_more"]
        role_status = payload["role_status"]
        if payload.get("role_error"):
            role_status += f" ({payload['role_error'][:160]})"
        local_status = f"{len(payload['local_jobs'])} local"
        if payload["local_jobs_possibly_more"]:
            local_status += f"; recent {payload['local_jobs_limit']} only"
        self.query_one("#core-status", Static).update(
            f"Jobs · {payload['scope']} · roles {role_status} · "
            f"page {self.page_offset // 200 + 1}, {len(payload['rows'])} remote row(s), {local_status} · "
            f"{'more rows available · ' if payload['possibly_more'] else ''}poll every {self.INTERVAL_SECONDS}s")

    def _apply_filter(self) -> None:
        super()._apply_filter()
        if self.last_success is not None and isinstance(self.last_payload, dict):
            payload = self.last_payload
            role_status = payload["role_status"]
            if payload.get("role_error"):
                role_status += f" ({payload['role_error'][:160]})"
            local_status = f"{len(payload['local_jobs'])} local"
            if payload["local_jobs_possibly_more"]:
                local_status += f"; recent {payload['local_jobs_limit']} only"
            self.query_one("#core-status", Static).update(
                f"Jobs · {payload['scope']} · roles {role_status} · "
                f"page {self.page_offset // 200 + 1}, {len([row for row in self.raw_rows if not self.query_one('#core-search', Input).value.strip().casefold() or self.query_one('#core-search', Input).value.strip().casefold() in ' '.join(row).casefold()])} visible, {local_status} · "
                f"{'more rows available · ' if payload['possibly_more'] else ''}poll every {self.INTERVAL_SECONDS}s")

    def _rows(self, payload: dict[str, Any]) -> list[tuple[str, ...]]:
        result = []
        for row in payload["rows"]:
            progress = row.get("progress")
            if isinstance(progress, dict):
                percent = progress.get("percent")
                progress_text = f"{percent}%" if percent is not None else "Unknown"
                if progress.get("description"):
                    progress_text += f" {progress['description']}"
            else:
                progress_text = "Unknown"
            result.append((self._cell(row["id"]), self._cell(row["method"]), self._cell(row["state"]),
                           progress_text, self._cell(row.get("logs_excerpt")), self._cell(row.get("error"))))
        for row in payload.get("local_jobs", []):
            progress = row.get("progress")
            if isinstance(progress, dict):
                percent = progress.get("percent")
                progress_text = f"{percent}%" if percent is not None else "Unknown"
                if progress.get("description"):
                    progress_text += f" {progress['description']}"
            else:
                progress_text = "Unknown"
            result.append((self._cell(row["job_id"]), f"{row['method']} · local", self._cell(row["state"]),
                           progress_text, self._cell(row.get("logs_excerpt")), self._cell(row.get("error"))))
        return result


class ServicesView(CoreReadView):
    VIEW_KEY = "services"
    INTERVAL_SECONDS = 60
    COLUMNS = ("Service", "State", "Enabled", "PIDs")

    def _rows(self, payload: dict[str, Any]) -> list[tuple[str, ...]]:
        rows = [(self._cell(row["service"]), self._cell(row["state"]), self._cell(row["enable"]),
                 self._cell(row["pids"])) for row in payload["rows"]]
        if payload["possibly_more"]:
            rows.append(("Result limit reached", "Unknown", "Unknown", "Query limit: 10000"))
        return rows


class StorageHealthView(CoreReadView):
    VIEW_KEY = "storage_health"
    INTERVAL_SECONDS = 60
    COLUMNS = ("Kind", "Name", "Health / State", "Warning", "Details")

    def _rows(self, payload: dict[str, Any]) -> list[tuple[str, ...]]:
        rows = []
        for pool in payload["pools"]:
            rows.append(("Pool", self._cell(pool["name"]),
                         self._cell(pool.get("status_code") or pool.get("status") or pool.get("healthy")),
                         self._cell(pool.get("warning")), self._cell({"scan": pool.get("scan"), "expand": pool.get("expand")})))
        for disk in payload["disks"]:
            rows.append(("Disk", self._cell(disk["name"]), self._cell(disk.get("pool")), "Unknown",
                         self._cell({"size": disk.get("size"), "model": disk.get("model"),
                                     "serial": disk.get("serial"), "devname": disk.get("devname"),
                                     "type": disk.get("type")})))
        if payload.get("pools_possibly_more") or payload.get("disks_possibly_more"):
            rows.append(("Result", "", "Possibly truncated", "Unknown", "Query reached the v27 row limit of 10000."))
        return rows


VIEWS = {"dashboard": DashboardView, "alerts": AlertsView, "jobs": JobsView,
         "services": ServicesView, "storage/health": StorageHealthView}
