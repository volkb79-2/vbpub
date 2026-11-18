"""hostobs/1 Python client: codec, frame reader, stream dictionaries, rates, connection,
``require_profiling`` and ``hostobs-mark``.

Standard library only and zipimport-safe: no module reads ``__file__``.
"""

from __future__ import annotations

from .conn import BlockStream, Connection, OperationTimeout, ProtocolError, Subscription
from .envelope import EnvelopeError
from .profiling import ProfilingState, require_profiling
from .rates import Reading, rate
from .stream import FrameReader, RawFrame, Session, StreamDicts
from .wire import (
    Block,
    BlockHeader,
    Column,
    ConnectionLost,
    Dictionary,
    HostobsError,
    ServerError,
    WireError,
    decode_block,
)

PROTOCOL = "hostobs/1"

__all__ = [
    "Block",
    "BlockHeader",
    "BlockStream",
    "Column",
    "Connection",
    "ConnectionLost",
    "Dictionary",
    "EnvelopeError",
    "FrameReader",
    "HostobsError",
    "OperationTimeout",
    "PROTOCOL",
    "ProfilingState",
    "ProtocolError",
    "RawFrame",
    "Reading",
    "ServerError",
    "Session",
    "StreamDicts",
    "Subscription",
    "WireError",
    "decode_block",
    "rate",
    "require_profiling",
]
