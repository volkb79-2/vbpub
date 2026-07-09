from __future__ import annotations

from collections.abc import Callable

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from groop.damon.control import APPROVAL_TEXT


class DamonConfirmScreen(Screen[str | None]):
    BINDINGS = (
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
    )

    def __init__(
        self,
        *,
        title: str,
        plan_text: str,
        apply_confirmed: Callable[[str], str],
    ) -> None:
        super().__init__()
        self.title_text = title
        self.plan_text = plan_text
        self.apply_confirmed = apply_confirmed
        self._error = ""

    def compose(self):
        yield VerticalScroll(
            Static(id="damon-confirm-body"),
            Input(placeholder=f"type {APPROVAL_TEXT} to apply", id="damon-confirm-input"),
        )

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#damon-confirm-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            result = self.apply_confirmed(event.value)
        except Exception as exc:
            self._error = f"\n\nERROR\n  {exc}"
            self._refresh()
            return
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _refresh(self) -> None:
        body = (
            f"{self.title_text}\n\n"
            f"{self.plan_text}\n\n"
            f"Type {APPROVAL_TEXT} exactly, then press Enter. Escape cancels."
            f"{self._error}"
        )
        self.query_one("#damon-confirm-body", Static).update(body)
