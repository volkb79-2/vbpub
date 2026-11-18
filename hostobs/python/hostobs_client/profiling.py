"""``require_profiling``: the five visible profiling states of plan D.2 as one helper.

Standard library only. A consumer calls ``require_profiling(level, required)`` once, shows
``state.notice`` verbatim when it is not empty, and acts on ``state.action``:

| ``name`` | meaning | ``required`` False | ``required`` True |
|---|---|---|---|
| ``absent`` | package not importable | ``run-with-notice`` | ``refuse`` |
| ``unconfigured`` | ``HOSTOBS_<LEVEL>_SOCKET`` unset | ``run-with-notice`` | ``refuse`` |
| ``unreachable`` | connect or hello failed | ``run-with-notice`` | ``refuse`` |
| ``refused`` | capability missing (level, feature, version) | ``run-with-notice`` | ``refuse`` |
| ``lost`` | epoch change, ``session-lost`` or ``window-lost`` after start | ``record-profile-error`` | ``evidence-invalid`` |

``absent`` cannot be produced by this module (it is importable by definition): the consumer's own
``except ImportError`` calls :func:`absent`. A usable connection is the sixth outcome,
``name == "available"`` (``AVAILABLE``), with ``action == "run"`` and the open connection in
``state.connection``; every other state closes what it opened. ``lost`` is produced after start
by :func:`lost` (from :func:`loss_reason` on an exception or on ``Connection.session``).
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Mapping, NamedTuple, Sequence

from .conn import Connection, OperationTimeout, ProtocolError
from .stream import Session
from .wire import ConnectionLost, ServerError, WireError

STATES = ("absent", "unconfigured", "unreachable", "refused", "lost")
AVAILABLE = "available"
LOST_CODES = frozenset(("session-lost", "window-lost"))
#: Server codes that mean "this socket does not offer what was asked" (state ``refused``).
REFUSED_CODES = frozenset(("forbidden", "unsupported-feature", "unsupported-version", "unknown-verb"))


class ProfilingState(NamedTuple):
    name: str
    required: bool
    detail: str
    notice: str  # shown verbatim; empty when available
    action: str  # run | run-with-notice | refuse | record-profile-error | evidence-invalid
    connection: Connection | None = None

    @property
    def refuse(self) -> bool:
        return self.action == "refuse"

    @property
    def outcome(self) -> str | None:
        """``"EVIDENCE_INVALID"`` for a lost state of a required run, else ``None``."""
        return "EVIDENCE_INVALID" if self.action == "evidence-invalid" else None


def _state(name: str, required: bool, detail: str, notice: str) -> ProfilingState:
    action = "refuse" if required else "run-with-notice"
    return ProfilingState(name, required, detail, notice, action)


def environment_variable(level: str) -> str:
    return f"HOSTOBS_{level.upper()}_SOCKET"


def absent(required: bool) -> ProfilingState:
    """The state of a consumer whose ``import hostobs_client`` failed."""
    notice = "hostobs: profiling unavailable: the Python package hostobs_client is not importable"
    if required:
        notice += " (profiling is required: install hostobs_client)"
    return _state("absent", required, "hostobs_client", notice)


def lost(required: bool, reason: str) -> ProfilingState:
    """The state after start: epoch change, ``session-lost`` or ``window-lost``."""
    notice = f"hostobs: profiling evidence lost: {reason}"
    action = "evidence-invalid" if required else "record-profile-error"
    return ProfilingState("lost", required, reason, notice, action)


def loss_reason(subject: BaseException | Connection | str) -> str | None:
    """Why ``subject`` means lost evidence, or ``None``.

    A :class:`ServerError` with ``session-lost``/``window-lost``, a :class:`Connection` (or a
    ``Session`` value) whose session is ``"changed"`` (new ``boot_id`` or ``epoch``, WIRE.md 6.4).
    """
    if isinstance(subject, ServerError) and subject.code in LOST_CODES:
        return f"the server answered {subject.code}"
    session = subject.session if isinstance(subject, Connection) else subject
    if session == Session.CHANGED:
        return "the probe restarted (boot_id or epoch changed)"
    return None


def require_profiling(
    level: str,
    required: bool,
    *,
    features: Sequence[str] = (),
    client: str = "hostobs-profiling",
    timeout_s: float = 5.0,
    clock: Callable[[], int] = time.monotonic_ns,
    environ: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = Connection.open,
) -> ProfilingState:
    """Probe the socket of ``level`` and return its state; ``features`` are required hello features."""
    env = os.environ if environ is None else environ
    variable = environment_variable(level)
    path = env.get(variable)
    if not path:
        notice = f"hostobs: profiling unconfigured: {variable} is unset"
        if required:
            notice += " (profiling is required: run `hostobsd install` and export the variable)"
        return _state("unconfigured", required, variable, notice)
    try:
        connection = opener(path, level, client=client, timeout_s=timeout_s, clock=clock)
    except (ConnectionLost, OperationTimeout) as error:
        return _unreachable(required, path, str(error))
    except OSError as error:
        return _unreachable(required, path, f"{error.strerror or type(error).__name__} (errno {error.errno})")
    except ServerError as error:
        if error.code in REFUSED_CODES:
            return _refused(required, f"{path}: server answered {error.code}")
        return _unreachable(required, path, f"server answered {error.code}")
    except ProtocolError as error:
        if error.detail.get("why") == "level differs from the requested one":
            return _refused(required, f"{path} serves level {error.detail.get('got')}, not {level}")
        return _unreachable(required, path, str(error))
    except WireError as error:
        if error.kind == "unsupported-version":
            return _refused(required, f"{path}: unsupported protocol version")
        return _unreachable(required, path, str(error))
    missing = [name for name in features if name not in connection.features]
    if missing:
        connection.close()
        return _refused(required, f"{path} lacks feature {', '.join(missing)}")
    return ProfilingState(AVAILABLE, required, path, "", "run", connection)


def _unreachable(required: bool, path: str, why: str) -> ProfilingState:
    return _state("unreachable", required, path, f"hostobs: profiling unreachable: {path}: {why}")


def _refused(required: bool, why: str) -> ProfilingState:
    return _state("refused", required, why, f"hostobs: profiling refused: {why}")


__all__ = [
    "AVAILABLE",
    "ProfilingState",
    "STATES",
    "absent",
    "environment_variable",
    "loss_reason",
    "lost",
    "require_profiling",
]
