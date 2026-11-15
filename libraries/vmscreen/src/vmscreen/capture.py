"""ocr-pair: a screenshot and its exact text from the same instant (the guest is paused while both are taken).

Safety contract: whatever happens inside the paused window (QMP timeout, protocol error, SIGINT/SIGTERM, a lost ``stop``
reply), the guest is resumed over a FRESH connection (a timed-out socket is poisoned) with retries, and the original error
is the one that propagates (the resume failure is attached as a note).  Only if the resume itself fails does the command fail
with ResumeFailed, whose message says the guest may still be paused.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import sources, textmode
from .errors import NoExactText, OutputExists, QmpError, ResumeFailed, VmscreenError

SCHEMA = "vmscreen.ocr-pair/2"
RESUME_ATTEMPTS = 3


def output_paths(prefix: Path) -> tuple[Path, Path]:
    """Suffixes are APPENDED (``vm-1.5`` -> ``vm-1.5.png``); with_suffix would replace ``.5``."""
    return Path(str(prefix) + ".png"), Path(str(prefix) + ".json")


def build_sidecar(res: sources.TextResult | None, png: bytes, png_name: str, pause_info: dict[str, Any], reason: str | None,
                  tried: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    w, h = textmode.png_size(png)
    side: dict[str, Any] = {
        "schema": SCHEMA, "png": png_name, "sha256": hashlib.sha256(png).hexdigest(), "width": w, "height": h,
        "captured_at": (now or datetime.now(timezone.utc)).isoformat(), **pause_info,
        "exact": res.exact if res else False, "exact_reason": (res.exact_reason if res else reason) or "",
        "source": res.source if res else None, "tried": tried, "assumptions": dict(sources.ASSUMPTIONS),
    }
    if res is None:
        side.update(text=None, reason=reason)
        return side
    side.update(text=res.text, lines=res.lines, geometry=res.geometry, verification=res.verification,
                uncertain_cells=res.geometry.get("uncertain_cells", []))
    if res.screen is not None:  # lossless ground truth for glyph-grid work: per-row hex of char bytes and attribute bytes
        buf, n = res.buffer, res.screen.cols * 2
        side["char_rows_hex"] = [buf[r * n:(r + 1) * n:2].hex() for r in range(res.screen.rows)]
        side["attr_rows_hex"] = [buf[r * n + 1:(r + 1) * n:2].hex() for r in range(res.screen.rows)]
        side["cells"] = [[[c.fg, c.bg, c.blink] for c in row] for row in res.screen.cells]
    return side


def resume_guest(client, reconnect: Callable[[], Any] | None = None, *, healthy: bool = False, attempts: int = RESUME_ATTEMPTS,
                 sleep: Callable[[float], None] = time.sleep) -> None:
    """Make the guest run again and CONFIRM it (query-status).  ``healthy``: try the existing connection first; otherwise (or on
    failure) close it, because QMP serves one client, and retry over fresh connections.  Creating the fresh connection is part
    of the retried step (a refused or timed-out reconnect costs one attempt, not the whole resume).  When every attempt fails,
    ResumeFailed says the guest may still be paused and chains the last cause."""
    last: BaseException | None = None
    if healthy:
        try:
            client.execute("cont")
            if client.execute("query-status").get("status") == "running":
                return
        except (QmpError, OSError, ValueError) as exc:
            last = exc
    client.close()
    for i in range(attempts):
        fresh = None
        try:
            fresh = reconnect() if reconnect else client.fresh()
            fresh.connect()
            fresh.execute("cont")
            if fresh.execute("query-status").get("status") == "running":
                return
            last = QmpError("guest is not running after cont")
        except (QmpError, OSError, ValueError) as exc:  # ValueError: undecodable reply bytes must not abort the retries
            last = exc
        finally:
            if fresh is not None:
                fresh.close()
        if i < attempts - 1:
            sleep(0.2)
    raise ResumeFailed(f"could not resume the guest after {attempts} fresh connections ({last}); it may still be PAUSED: "
                       "run `cont` on its QMP socket") from last


class _Signals:
    """Turn SIGTERM/SIGINT into SystemExit while the guest is stopped, so the resume always runs; while resuming a signal is only
    recorded and re-raised after the guest runs again.  A no-op off the main thread."""

    def __init__(self) -> None:
        self.old: dict[int, Any] = {}
        self.armed = False
        self.pending = 0

    def arm(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum, _frame):
            if self.armed:
                raise SystemExit(128 + signum)
            self.pending = 128 + signum  # resuming: finish first, exit afterwards
        self.armed = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.old[sig] = signal.signal(sig, handler)

    def hold(self) -> None:
        self.armed = False  # the handler now only records the signal

    def restore(self) -> int:
        """Put the old handlers back; return the exit code of a signal recorded meanwhile (0 if none).  It NEVER raises: the
        caller decides whether the pending signal may replace another outcome (it must not replace a ResumeFailed)."""
        for sig, old in self.old.items():
            signal.signal(sig, old)
        self.old.clear()
        code, self.pending = self.pending, 0
        return code


def _commit(png_path: Path, json_path: Path, png: bytes, side: dict[str, Any], force: bool) -> None:
    """Both files via temp files; the JSON is committed LAST and is the commit marker.

    Without ``force`` creation is exclusive (hard link: fails with FileExistsError if the name appeared since the pre-check), so
    a concurrent writer's files are never replaced; a filesystem without hard links gives a clear error naming ``--force``.  With
    ``force`` the old files are moved to backups (``.NAME.PID.bak``) and RESTORED if anything fails, including SIGINT/SIGTERM in
    that window (turned into a rollback); should even the restore fail, the exception carries the backup paths as a note.
    An orphan PNG is never left behind."""
    pid = os.getpid()
    tmp_png = png_path.with_name(f".{png_path.name}.{pid}.tmp")
    tmp_json = json_path.with_name(f".{json_path.name}.{pid}.tmp")
    backups: list[tuple[Path, Path]] = []
    created: list[Path] = []
    signals = _Signals()
    try:
        tmp_png.write_bytes(png)
        tmp_json.write_text(json.dumps(side, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            signals.arm()  # SIGTERM/SIGINT -> SystemExit -> the rollback below
            if force:
                for final in (png_path, json_path):
                    if final.exists():
                        bak = final.with_name(f".{final.name}.{pid}.bak")
                        os.replace(final, bak)
                        backups.append((bak, final))
                for tmp, final in ((tmp_png, png_path), (tmp_json, json_path)):
                    os.replace(tmp, final)
                    created.append(final)
            else:
                for tmp, final in ((tmp_png, png_path), (tmp_json, json_path)):
                    try:
                        os.link(tmp, final)
                    except FileExistsError as exc:
                        raise OutputExists(f"{final} appeared while capturing (use --force to overwrite)") from exc
                    except OSError as exc:
                        raise VmscreenError(f"cannot create {final} exclusively ({exc}); the filesystem may not support hard links: "
                                            "use --force to write by replacement instead (nothing was changed)") from exc
                    created.append(final)
            signals.hold()
        except BaseException as exc:
            signals.hold()
            for final in created:
                final.unlink(missing_ok=True)
            for bak, final in backups:
                try:
                    os.replace(bak, final)
                except OSError as rerr:
                    exc.add_note(f"could not restore {final}: {rerr}; the previous file is kept as {bak}")
            raise
        for bak, _ in backups:
            bak.unlink(missing_ok=True)
    finally:
        signals.restore()
        tmp_png.unlink(missing_ok=True)
        tmp_json.unlink(missing_ok=True)


def ocr_pair(client, out_prefix: Path, *, dump_dir: str | None, blink_mode: bool = True, pause: bool = True,
             allow_inexact: bool = False, accept_partial: bool = False, force: bool = False, reconnect: Callable[[], Any] | None = None,
             sleep: Callable[[float], None] = time.sleep, qemu_dump_dir: str | None = None) -> dict[str, Any]:
    """Write ``<prefix>.png`` and ``<prefix>.json``.  Only the VGA text buffer is a candidate (the screen, not a serial stream).
    Without exact text (graphical screen, ``exact: "unverified"`` or ``"partial"``) exit 4 (NoExactText) unless ``allow_inexact``
    (anything) or ``accept_partial`` (only ``"partial"``: all cells verified except the listed ``uncertain_cells``)."""
    png_path, json_path = output_paths(Path(out_prefix))
    if not force:
        for p in (png_path, json_path):
            if p.exists():
                raise OutputExists(f"{p} exists (use --force to overwrite)")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    status = client.execute("query-status").get("status")
    do_pause = pause and status == "running"
    info = {"vm_status_before": status, "guest_was_paused_before": status != "running", "guest_paused_for_capture": do_pause}
    signals = _Signals()
    stopped = False
    err: BaseException | None = None
    res: sources.TextResult | None = None
    reason: str | None = None
    tried: list[dict[str, Any]] = []
    png = b""
    state: dict[str, Any] = {"resumed": False, "err": None, "pending": 0}

    def do_resume(healthy: bool) -> None:
        if state["resumed"]:  # exactly one resume sequence, whatever interrupts the first
            return
        signals.hold()  # FIRST: a signal arriving before this raises with ``resumed`` still False, so the outer finally resumes
        state["resumed"] = True
        try:
            resume_guest(client, reconnect, healthy=healthy, sleep=sleep)
        except (ResumeFailed, QmpError, OSError, ValueError) as exc:
            if not isinstance(exc, ResumeFailed):
                exc = ResumeFailed(f"resume failed ({exc}); the guest may still be PAUSED: run `cont` on its QMP socket")
            state["err"] = exc

    try:
        try:
            if do_pause:
                signals.arm()
                stopped = True  # set BEFORE the call: a lost reply may still have stopped the guest
                client.execute("stop")
            try:
                res = sources.read_vga(client, dump_dir, blink_mode, stable_check=status == "running" and not do_pause,
                                       qemu_dump_dir=qemu_dump_dir)
                png = res.png or b""
                tried = res.tried
            except NoExactText as exc:
                reason, tried = str(exc), exc.tried
                png = sources.screendump_png(client, dump_dir, qemu_dump_dir)
        except BaseException as exc:  # noqa: BLE001 - everything must pass through the resume below, then be re-raised
            err = exc
        if stopped:
            do_resume(err is None)
    finally:
        try:
            if stopped and not state["resumed"]:  # a signal landed between the capture and the resume: still resume
                do_resume(False)
                inflight = sys.exc_info()[1]
                if inflight is not None and state["err"] is not None:
                    inflight.add_note(str(state["err"]))
        finally:
            state["pending"] = signals.restore()  # always, whatever the resume did; never raises
    resume_err, pending = state["err"], state["pending"]
    if resume_err is not None and pending:
        resume_err.add_note(f"a signal (exit code {pending}) was received while resuming")
    if err is not None:
        if resume_err is not None:
            err.add_note(str(resume_err))
        if pending and not isinstance(err, SystemExit):
            err.add_note(f"a signal (exit code {pending}) was received while resuming")
        raise err
    if resume_err is not None:  # the hint "guest may still be PAUSED" is never replaced by the pending signal
        raise resume_err
    if pending:
        raise SystemExit(pending)
    if res is None or res.exact is not True:
        why = reason or res.exact_reason  # type: ignore[union-attr]
        if not (allow_inexact or (res is not None and res.exact == "partial" and accept_partial)):
            raise NoExactText(f"no verified exact text for this screen ({res.exact if res else False}): {why}", tried)
        reason = why
    side = build_sidecar(res, png, png_path.name, info, reason, tried)
    _commit(png_path, json_path, png, side, force)
    return {"png": str(png_path), "json": str(json_path), "exact": side["exact"], "source": side["source"], **info}
