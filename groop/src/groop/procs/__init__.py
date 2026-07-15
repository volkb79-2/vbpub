"""groop.procs — the P90 bounded CPU-hot / I/O-hot process model.

Candidate selection is the union of CPU-hot, I/O-hot, selected/pinned and
recently-hot processes (D-013/D-019). See
``handoff/P90-bounded-process-sampler.md``.
"""

from __future__ import annotations

from groop.procs.candidates import CandidateReasons, SelectionResult, select_candidates
from groop.procs.identity import ProcessKey, read_boot_id
from groop.procs.owners import OwnerJoin, join_owner
from groop.procs.sampler import ProcessCoverage, ProcessFrameSource, ProcessSampler, ProcessTick
from groop.procs.sensitivity import classify_process_field, redact_process_row

__all__ = [
    "CandidateReasons",
    "SelectionResult",
    "select_candidates",
    "ProcessKey",
    "read_boot_id",
    "OwnerJoin",
    "join_owner",
    "ProcessCoverage",
    "ProcessFrameSource",
    "ProcessSampler",
    "ProcessTick",
    "classify_process_field",
    "redact_process_row",
]
