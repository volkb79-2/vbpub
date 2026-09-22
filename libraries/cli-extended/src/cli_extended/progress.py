"""TTY-aware progress with stable plain and JSONL fallbacks."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from enum import Enum
from typing import Any, Self, TextIO

from .output import LogLevel, redact_text


class ProgressMode(str, Enum):
    AUTO = "auto"
    TTY = "tty"
    PLAIN = "plain"
    QUIET = "quiet"
    RAWJSON = "rawjson"

    @classmethod
    def parse(cls, value: str) -> ProgressMode:
        try:
            return cls(value.lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(
                f"invalid progress mode {value!r}; expected one of: {allowed}"
            ) from exc


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


class ProgressRenderer:
    """Render progress without contaminating a command's result stdout."""

    def __init__(
        self,
        mode: ProgressMode | str = ProgressMode.AUTO,
        *,
        stream: TextIO | None = None,
        color: bool | None = None,
        level: LogLevel | str = LogLevel.INFO,
        json_mode: bool = False,
        secrets: Sequence[str] = (),
        debug_raw: bool = False,
    ) -> None:
        requested = ProgressMode.parse(mode) if isinstance(mode, str) else mode
        level = LogLevel.parse(level) if isinstance(level, str) else level
        if requested is ProgressMode.RAWJSON and stream is None:
            stream = sys.stdout
        elif stream is None:
            stream = sys.stderr
        self.requested_mode = requested
        self.stream = stream
        self.color = color
        self.level = level
        self.secrets = tuple(secrets)
        self.debug_raw = debug_raw
        if requested is ProgressMode.AUTO:
            self.mode = ProgressMode.TTY if _is_tty(stream) else ProgressMode.PLAIN
        else:
            self.mode = requested
        if json_mode and self.mode is ProgressMode.RAWJSON:
            self.mode = ProgressMode.QUIET
        if level.numeric > LogLevel.INFO.numeric:
            self.mode = ProgressMode.QUIET
        self._active = False

    def _color_enabled(self) -> bool:
        if self.color is False or self.mode in (
            ProgressMode.PLAIN,
            ProgressMode.RAWJSON,
        ):
            return False
        if self.color is True:
            return True
        return _is_tty(self.stream) and not os.environ.get("NO_COLOR")

    def _event(
        self, message: str, *, current: float | None, total: float | None, done: bool
    ) -> dict[str, Any]:
        if not self.debug_raw:
            message = redact_text(message, self.secrets)
        event: dict[str, Any] = {"type": "progress", "message": message, "done": done}
        if current is not None:
            event["current"] = current
        if total is not None:
            event["total"] = total
        return event

    def update(
        self,
        message: str,
        *,
        current: float | None = None,
        total: float | None = None,
    ) -> None:
        """Publish one progress update."""

        if self.mode is ProgressMode.QUIET:
            return
        self._active = True
        if not self.debug_raw:
            message = redact_text(message, self.secrets)
        if self.mode is ProgressMode.RAWJSON:
            json.dump(
                self._event(message, current=current, total=total, done=False),
                self.stream,
            )
            self.stream.write("\n")
            self.stream.flush()
            return
        suffix = ""
        if current is not None and total is not None:
            suffix = f" ({current}/{total})"
        rendered = f"[INFO] {message}{suffix}"
        if self.mode is ProgressMode.TTY:
            if self._color_enabled():
                rendered = f"\033[36m{rendered}\033[0m"
            self.stream.write("\r\033[2K" + rendered)
            self.stream.flush()
            return
        print(rendered, file=self.stream, flush=True)

    def finish(
        self,
        message: str = "Done.",
        *,
        current: float | None = None,
        total: float | None = None,
    ) -> None:
        """Publish completion and leave a TTY cursor on a fresh line."""

        if self.mode is ProgressMode.QUIET:
            return
        if not self.debug_raw:
            message = redact_text(message, self.secrets)
        if self.mode is ProgressMode.RAWJSON:
            json.dump(
                self._event(message, current=current, total=total, done=True),
                self.stream,
            )
            self.stream.write("\n")
            self.stream.flush()
        elif self.mode is ProgressMode.TTY:
            if self._active:
                self.stream.write("\r\033[2K" + message + "\n")
                self.stream.flush()
        else:
            print(f"[INFO] {message}", file=self.stream, flush=True)
        self._active = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.mode is ProgressMode.TTY and self._active:
            self.stream.write("\n")
            self.stream.flush()
        self._active = False
