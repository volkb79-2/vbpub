"""hostobs/1 Python client: codec, frame reader, stream dictionaries and rates.

Standard library only and zipimport-safe: no module reads ``__file__``. The connection,
profiling and mark modules are not part of this slice.
"""

from __future__ import annotations

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
    "Column",
    "ConnectionLost",
    "Dictionary",
    "FrameReader",
    "HostobsError",
    "PROTOCOL",
    "RawFrame",
    "Reading",
    "ServerError",
    "Session",
    "StreamDicts",
    "WireError",
    "decode_block",
    "rate",
]
