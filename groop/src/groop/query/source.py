"""The one typed ``FrameSource`` boundary (P88 Contract 1).

A ``FrameSource`` turns a recording or daemon history into a single canonical,
bounded, ordered sequence of ``SourceFrame`` records.  Adapters PRESERVE
timestamps, sequence, provenance and eviction/gap information; they never
aggregate and never derive metrics.  Reset detection is a value-semantic concern
handled by the engine, not the source.

Both concrete adapters consume the frames their upstream reader already produces
(``RecordReader`` for P2 recordings, ``DaemonClient.request_history`` for the
P52/P63 typed history read), so neither the P2 nor the P52 wire format changes:
the boundary is a thin, additive wrapper over existing canonical ``Frame``
objects.

Absolute sequence numbers stay INTERNAL.  They drive gap/eviction detection but
are deliberately absent from the engine's emitted payload, because a recording
numbers frames ``0..N-1`` while a live daemon uses its own ring sequence.
Emitting them would break the Contract-7 requirement that a recording fixture
and a daemon fixture over the same frames produce byte-identical payloads apart
from the declared source provenance.  Gaps/eviction are therefore reported
structurally (observed positions, timestamps and flags), which is
source-independent.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from groop.model import Frame


@dataclass(frozen=True)
class SourceFrame:
    """One canonical frame plus the source bookkeeping the engine needs.

    Attributes:
        seq: Source-local monotonic sequence (recording index, or daemon ring
            sequence).  Strictly increasing across a source.  Internal only.
        frame: The canonical ``Frame`` exactly as the upstream reader produced
            it — never mutated, never aggregated.
        gap_before: True when at least one frame is known to be missing
            immediately before this one (a daemon sequence jump, or evicted
            history preceding the first retained frame).  Recordings never set
            this: a completed recording is contiguous by construction.
    """

    seq: int
    frame: Frame
    gap_before: bool = False


@dataclass(frozen=True)
class SourceProvenance:
    """Declared origin of a source's frames.

    ``kind`` and ``detail`` are the ONLY source-dependent content in a query
    result.  Two sources with identical frames but different provenance must
    differ in the emitted payload only here.
    """

    kind: str  # "recording" | "daemon-history"
    detail: dict[str, str] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind}
        if self.detail:
            out["detail"] = {k: self.detail[k] for k in sorted(self.detail)}
        return out


class FrameSource:
    """Abstract canonical frame boundary.

    Concrete adapters implement :meth:`iter_source_frames` (ordered, ascending
    ``seq``) and expose :attr:`provenance`.  ``evicted`` is True when the source
    knows that frames older than the oldest yielded frame existed but are no
    longer retained (a daemon ring wrapped).  This is distinct from a
    ``gap_before`` inside the yielded run.
    """

    provenance: SourceProvenance
    evicted: bool = False

    def iter_source_frames(self) -> Iterator[SourceFrame]:  # pragma: no cover - abstract
        raise NotImplementedError


class RecordingFrameSource(FrameSource):
    """FrameSource over a P2 recording via the existing ``RecordReader``.

    A completed recording is contiguous and complete: no eviction, no
    ``gap_before``.  Sequence is the 0-based frame index.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        # The recording path is the operator's own local file; naming it in
        # provenance is safe here (it never crosses a socket).  Only the file
        # name is exposed, not any absolute directory chain.
        self.provenance = SourceProvenance(
            kind="recording", detail={"name": self._path.name}
        )
        self.evicted = False

    def iter_source_frames(self) -> Iterator[SourceFrame]:
        from groop.record.reader import RecordReader

        reader = RecordReader(self._path)
        for index, frame in enumerate(reader.iter_frames()):
            yield SourceFrame(seq=index, frame=frame, gap_before=False)


class DaemonHistoryFrameSource(FrameSource):
    """FrameSource over a P52/P63 typed daemon-history read.

    Constructed from the already-validated ``(seq, Frame)`` entries the typed
    client returns, plus the ring's ``gap`` marker and ``oldest_seq``.  The
    entries are the daemon's own words: this adapter re-tags eviction/gap in the
    source-independent form the engine expects and does NOT re-open a socket, so
    it is fully testable without a live daemon.

    ``gap`` (the daemon's ring-eviction marker) means retained history older than
    the first returned frame was evicted, so :attr:`evicted` is set and the first
    frame carries ``gap_before``.  Interior sequence jumps (``seq`` increasing by
    more than one) also set ``gap_before`` on the later frame.
    """

    def __init__(
        self,
        entries: tuple[tuple[int, Frame], ...],
        *,
        gap: bool = False,
        oldest_seq: int | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        self._entries = tuple(entries)
        self._gap = bool(gap)
        self._oldest_seq = oldest_seq
        self.provenance = SourceProvenance(
            kind="daemon-history", detail=dict(detail or {})
        )
        # Evicted when the ring reported a gap, or when the oldest retained
        # sequence is ahead of the first entry we hold (older frames dropped).
        first_seq = self._entries[0][0] if self._entries else None
        self.evicted = self._gap or (
            oldest_seq is not None
            and first_seq is not None
            and oldest_seq < first_seq
        )

    @classmethod
    def from_history_result(cls, result: object) -> DaemonHistoryFrameSource:
        """Build from a ``DaemonHistoryResult`` (duck-typed to avoid a hard
        import cycle with the daemon client)."""
        entries = tuple(getattr(result, "entries"))
        return cls(
            entries=entries,
            gap=bool(getattr(result, "gap", False)),
            oldest_seq=getattr(result, "oldest_seq", None),
        )

    def iter_source_frames(self) -> Iterator[SourceFrame]:
        prev_seq: int | None = None
        for i, (seq, frame) in enumerate(self._entries):
            if i == 0:
                gap_before = self.evicted
            else:
                gap_before = prev_seq is not None and seq > prev_seq + 1
            yield SourceFrame(seq=seq, frame=frame, gap_before=gap_before)
            prev_seq = seq
