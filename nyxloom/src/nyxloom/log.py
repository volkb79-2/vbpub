"""Structured, level-gated logging core (structlog-based). PACKAGE P01.

See ``docs/plan-logging.md`` §2–§4.1 for the full design rationale. Summary
of the load-bearing decisions this module implements:

- **logs != events** (§2): this is a *disposable* diagnostic stream, never
  the event-sourced ``events.jsonl`` domain log. Nothing here is replayed.
- **structlog** (D-L1) renders one JSONL line per record via a shared
  processor chain, bridged to stdlib ``logging`` (via
  ``structlog.stdlib.ProcessorFormatter``) purely for the sink (a rotating
  file) + an optional human-readable console mirror. structlog does the
  structuring/context/rendering; stdlib does the I/O.
- **Scoped, never global.** Every stdlib logger this module creates lives
  under the top-level ``nyxloom`` channel (``nyxloom``, ``nyxloom.daemon``,
  ``nyxloom.paths``, ...). ``configure()`` only ever touches
  ``logging.getLogger("nyxloom")`` (and its children) — it NEVER touches
  ``logging.getLogger()`` (the real stdlib root) or any sibling logger.
  ``nyxloom``'s own ``propagate`` is False, so nothing leaks upward either.
- **Live-adjustable level (D-L3).** ``configure()``/``set_level()`` install
  a *fresh* filtering wrapper class into structlog's global config on every
  call, and every ``get_logger()`` caller keeps re-resolving that global
  config on each log call (``cache_logger_on_first_use=False`` is load-
  bearing here — see the long comment above ``configure()``). This is what
  makes ``set_level()`` change already-imported modules' behaviour without
  a restart, which is the entire point of D-L3/G2.
- **TRACE(5)** is not a level structlog ships out of the box, so it is
  hand-added as a bound method on top of whichever base filtering class
  structlog gives us (see ``_make_wrapper_class``).

Usage (every other module):
    from .log import get_logger
    log = get_logger(__name__.split(".")[-1])   # module-level, at import
    ...
    with log.bind(project=project_id):
        log.info("dispatched", task=task_id, route=route)
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import structlog
import structlog.contextvars
from structlog.typing import EventDict, FilteringBoundLogger, WrappedLogger

# ---------------------------------------------------------------------------
# Level taxonomy (D-L2). TRACE is custom -- structlog/stdlib both stop at
# DEBUG=10; nyxloom's firehose tier sits below that.

TRACE = 5
DEBUG = logging.DEBUG        # 10
INFO = logging.INFO          # 20
WARNING = logging.WARNING    # 30
ERROR = logging.ERROR        # 40
CRITICAL = logging.CRITICAL  # 50

_LEVEL_NAME_TO_VALUE: dict[str, int] = {
    "trace": TRACE,
    "debug": DEBUG,
    "info": INFO,
    "warning": WARNING,
    "warn": WARNING,
    "error": ERROR,
    "critical": CRITICAL,
}

_LOGGER_NAME = "nyxloom"      # the ONLY stdlib channel this module ever touches
_LOG_FILENAME = "nyxloom.jsonl"


def _normalize_level(level: int | str) -> int:
    """Accept either an int (any of the constants above) or a case-
    insensitive level name; raise on anything else -- callers (D-L3's
    ``resolve_level`` in P02) are expected to validate user input before it
    reaches here, but this module never silently accepts garbage."""
    if isinstance(level, str):
        try:
            return _LEVEL_NAME_TO_VALUE[level.strip().lower()]
        except KeyError as exc:
            raise ValueError(f"unknown nyxloom log level: {level!r}") from exc
    return int(level)


# ---------------------------------------------------------------------------
# Logger factory: every nyxloom module's structlog logger backs onto a REAL
# stdlib channel under "nyxloom.<name>" (a child of "nyxloom", never of the
# real root). This is what lets configure()/set_level() attach handlers to
# exactly one place ("nyxloom") without ever calling logging.getLogger() bare
# or logging.basicConfig() (both of which would mutate the shared root that
# every OTHER library/test in the process also uses).

class _NyxloomLoggerFactory:
    def __call__(self, *args: Any) -> logging.Logger:
        name = args[0] if args else _LOGGER_NAME
        if not name or name == _LOGGER_NAME:
            return logging.getLogger(_LOGGER_NAME)
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def _short_logger_name(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Processor: render the ``logger`` field as the short module name
    (``"daemon"``, ``"paths"``, ...) rather than the internal
    ``nyxloom.<name>`` stdlib channel name -- matches the plan's illustrative
    record (§4.1) and keeps the field stable if the internal namespacing
    ever changes."""
    record = event_dict.get("_record")
    raw = record.name if record is not None else getattr(logger, "name", _LOGGER_NAME)
    if raw == _LOGGER_NAME:
        short = _LOGGER_NAME
    elif raw.startswith(_LOGGER_NAME + "."):
        short = raw[len(_LOGGER_NAME) + 1:]
    else:
        short = raw
    event_dict["logger"] = short
    return event_dict


_TIMESTAMPER = structlog.processors.TimeStamper(fmt="%Y-%m-%dT%H:%M:%S", utc=True, key="ts")

# The chain every record (native structlog AND foreign stdlib) passes through
# before final rendering (§4.1): merge bound context -> stamp level -> stamp
# UTC ts -> stamp the short logger name -> rename structlog's positional
# "event" to our "msg" (so it doesn't collide with the domain Event concept,
# §2). Shared between the "native" path (fed via structlog.configure) and
# the "foreign" path (any plain stdlib logging.getLogger("nyxloom.x") call
# that didn't go through structlog -- foreign_pre_chain below).
_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    _TIMESTAMPER,
    _short_logger_name,
    structlog.processors.EventRenamer("msg"),
]


def _make_wrapper_class(min_level: int) -> type[FilteringBoundLogger]:
    """Build a structlog bound-logger class gated at *min_level*, with a
    hand-added ``.trace()`` method (structlog has no native TRACE level).

    TRACE(5) sits below structlog's own floor (NOTSET=0 is its most
    permissive built-in), so when TRACE is the configured level we base the
    class on NOTSET (accepts everything down to 0) and only then does our
    own ``trace()`` method decide, from *min_level*, whether TRACE calls are
    live. For any other configured level, ``trace()`` is a static no-op --
    exactly mirroring how structlog itself compiles below-threshold methods
    to a `return None` (see structlog._native._make_filtering_bound_logger).

    ``trace()`` can't reuse ``_proxy_to_logger`` as-is: that helper calls
    ``getattr(self._logger, method_name)`` on the WRAPPED stdlib
    ``logging.Logger``, which has no ``.trace`` method (stdlib stops at
    DEBUG). It calls the documented lower-level ``_process_event`` +
    ``self._logger.log(<numeric level>, ...)`` instead -- stdlib's generic
    ``Logger.log()`` accepts an arbitrary level number, exactly what a
    custom level needs. structlog explicitly sanctions reaching for
    ``_process_event`` from a custom wrapper class for this kind of
    extension (see structlog's "custom wrapper classes" docs).
    """
    base_level = logging.NOTSET if min_level <= TRACE else min_level
    base = structlog.make_filtering_bound_logger(base_level)
    trace_enabled = min_level <= TRACE

    def trace(self: Any, event: str, *args: Any, **kw: Any) -> Any:
        if not trace_enabled:
            return None
        if args:
            event = event % args
        try:
            proxy_args, proxy_kw = self._process_event("trace", event, kw)
        except structlog.DropEvent:
            return None
        return self._logger.log(TRACE, *proxy_args, **proxy_kw)

    def is_enabled_for(self: Any, level: int) -> bool:
        return level >= min_level

    def get_effective_level(self: Any) -> int:
        return min_level

    trace.__name__ = "trace"
    return type(
        f"NyxloomBoundLogger_{min_level}",
        (base,),
        {
            "trace": trace,
            "is_enabled_for": is_enabled_for,
            "get_effective_level": get_effective_level,
        },
    )


def _json_formatter() -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )


def _console_formatter() -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )


def get_logger(name: str = "") -> FilteringBoundLogger:
    """Every module does ``log = get_logger(__name__.split('.')[-1])`` at
    import time. Returns structlog's lazy proxy immediately (cheap, no I/O);
    the real bound logger + current level/handlers are resolved fresh on
    EVERY subsequent call (see the ``cache_logger_on_first_use=False`` note
    on ``configure()``), which is exactly what makes ``set_level()`` reach
    already-imported callers without a restart."""
    return structlog.get_logger(name)


@contextmanager
def bind(**ctx: Any) -> Iterator[None]:
    """Context manager binding *ctx* onto every log record emitted while
    inside it (via ``structlog.contextvars``). Nests correctly -- an inner
    ``bind()`` shadows an outer one for the block it wraps and reverts back
    to the outer values on exit; a call with no enclosing ``bind()`` reverts
    to fully unbound. Always resets, including when the block raises."""
    tokens = structlog.contextvars.bind_contextvars(**ctx)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


def configure(level: int | str = INFO, log_dir: str | Path | None = None, console: bool = True) -> None:
    """(Re-)configure nyxloom's logging. Idempotent: safe to call more than
    once (each call fully replaces the previous handler set on the
    ``nyxloom`` logger, so repeat calls never duplicate output lines or leak
    handlers pointed at a stale directory -- important since tests call this
    once per test with a fresh tmp dir).

    Deliberately does NOT set ``cache_logger_on_first_use=True``. structlog's
    own docs note that a filtering bound logger, once resolved, is normally
    "static" -- baked in at first use and can't be changed short of the
    stdlib-integration approach. We get the live-reconfigure behaviour D-L3
    requires (flip to DEBUG without a restart, and have modules that
    imported their logger at start already obey it) for free from
    structlog's *lazy proxy*: as long as caching stays off, every
    ``get_logger()``-returned proxy re-resolves ``structlog.configure()``'s
    current global state on EVERY call, not just the first. That is the
    mechanism ``set_level()`` relies on.

    Scoped to the ``nyxloom`` stdlib channel only -- never touches
    ``logging.getLogger()`` (the bare root) or any other logger, so a
    sibling ``logging.getLogger("other")`` is never affected.
    """
    min_level = _normalize_level(level)

    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=_NyxloomLoggerFactory(),
        wrapper_class=_make_wrapper_class(min_level),
        cache_logger_on_first_use=False,
    )

    root = logging.getLogger(_LOGGER_NAME)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.propagate = False
    # The real gate is structlog's wrapper_class above; keep this logger's
    # OWN level maximally permissive so it never redundantly re-filters
    # records that already passed (or were already dropped by) that gate.
    # NB: logging.NOTSET (0) here would NOT mean "permissive" -- for a
    # non-root logger, NOTSET means "delegate to the parent chain", which
    # would walk all the way up to the real stdlib root's default WARNING
    # and silently drop INFO/DEBUG/TRACE records structlog already decided
    # to let through. `1` is the lowest real level, distinct from NOTSET's
    # delegate-to-parent meaning.
    root.setLevel(1)

    if log_dir is not None:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir_path / _LOG_FILENAME,
            maxBytes=10_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.NOTSET)
        file_handler.setFormatter(_json_formatter())
        root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        # Fixed at INFO regardless of the global effective level (§4.1) --
        # the file always gets everything the effective level allows
        # through; the console (-> `docker logs`) stays terse on purpose.
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(_console_formatter())
        root.addHandler(console_handler)


def set_level(level: int | str) -> None:
    """Change the live effective level (D-L3 runtime override). Reaches
    already-imported callers with no restart -- see ``configure()``'s
    docstring for the mechanism."""
    min_level = _normalize_level(level)
    structlog.configure(wrapper_class=_make_wrapper_class(min_level))
