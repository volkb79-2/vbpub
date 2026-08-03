#!/usr/bin/env python3
# mdt host-setup — audit the live cgroup tree for memory.min/memory.low
# values that are silently a NO-OP because some ancestor cgroup provides no
# protection of its own.
#
# cgroup v2's own documentation: "effective min/low boundary is limited by
# memory.min/memory.low values of ALL ancestor cgroups" — one ancestor at 0
# (unset) zeroes everything below it, regardless of what a deeper cgroup
# declares. This is NOT hypothetical on a host running this project's own
# shipped dev-tier config: dev.slice sets no MemoryMin/MemoryLow at all, and
# dev-background.slice sets neither either — so any memory.min/low set
# anywhere under either tier is currently a complete no-op. See
# ciu/docs/DESIGN-NOTES.md D1/D3/D4/D6 for the cross-project design
# discussion that identified this and led to this script.
#
# Companion to mdt-apply-dev-caps.sh, which APPLIES caps; this script only
# AUDITS and logs — it never writes anything to any cgroup or unit. Run by
# mdt-host-slices.service (a second ExecStart= on the same oneshot, so it
# rides the existing boot + mdt-host-slices.timer cadence — no new timer).
#
# Why Python and not another shell function: this needs a full recursive
# tree walk carrying per-node state (a cgroup's own value AND its complete
# ancestor chain), which is a straightforward `pathlib` walk here and a real
# undertaking in POSIX sh/bash's array/string primitives — mdt-apply-dev-caps.sh
# already shows the strain (manual awk-based cgroup-path parsing, a shared
# fixed-path stderr tempfile). Kept as a SEPARATE script (not folded into that
# one, and not a rewrite of it) so the existing, working sweep is untouched.
#
# Never fails the boot/timer: audit-only, always exits 0. Config: SCAN_ROOT
# env var (default: dev.slice under CG, default /sys/fs/cgroup).

from __future__ import annotations

import os
import sys
from pathlib import Path

CG = Path(os.environ.get("CG", "/sys/fs/cgroup"))
SCAN_ROOT = Path(os.environ.get("MDT_AUDIT_SCAN_ROOT") or (CG / "dev.slice"))
PROPERTIES = ("memory.min", "memory.low")


def log(message: str) -> None:
    print(f"[mdt-slice-audit] {message}", flush=True)


def read_protection(cgroup_dir: Path, prop: str) -> int:
    """Read a memory.min/memory.low value; ``0`` for unset/'max'/unreadable.

    cgroup v2 reports a plain integer, or the literal ``"max"`` (no
    protection requested — equivalent to ``0``, "no floor"). A directory
    that isn't a real cgroup (or is unreadable) also reads as ``0`` — this
    function is only ever used to ask "does this provide protection", and
    "can't tell" is the same answer as "no" for that question.
    """
    try:
        text = (cgroup_dir / prop).read_text().strip()
    except OSError:
        return 0
    if text in ("", "max"):
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def ancestors(cgroup_dir: Path, cgroup_root: Path) -> list[Path]:
    """Ancestor cgroup directories from *cgroup_dir*'s parent up to, but NOT
    including, *cgroup_root* (the cgroup2 mount root itself has no
    memory.min/low file — it IS the whole machine, unconstrained by
    definition, not a misconfiguration).

    Uses the REAL filesystem hierarchy, not any unit-naming convention —
    correct alike for named slices (``dev-background.slice``, whose parent
    IS the directory it lives in) and opaquely-named transient scopes
    (``docker-<id>.scope``, which don't follow slice dash-naming at all).
    """
    chain: list[Path] = []
    current = cgroup_dir.parent
    while current != cgroup_root:
        chain.append(current)
        if current.parent == current:
            break  # filesystem root guard — should never actually hit this
        current = current.parent
    return chain


def audit_tree(scan_root: Path, cgroup_root: Path) -> int:
    """Walk every cgroup under *scan_root*; for each with a nonzero
    memory.min/memory.low, verify every ancestor up to *cgroup_root* also
    has a nonzero value of that SAME property. Logs one WARN per broken
    (cgroup, property) pair found. Returns the count found.
    """
    if not scan_root.is_dir():
        log(f"WARN: {scan_root} not found or not active yet — skipping this run")
        return 0

    found = 0
    candidates = [scan_root] + sorted(p for p in scan_root.rglob("*") if p.is_dir())
    for cgroup_dir in candidates:
        chain = ancestors(cgroup_dir, cgroup_root)
        for prop in PROPERTIES:
            own = read_protection(cgroup_dir, prop)
            if not own:
                continue  # nothing declared here — nothing to audit
            broken = [a for a in chain if read_protection(a, prop) == 0]
            if not broken:
                continue
            label = cgroup_dir.relative_to(cgroup_root)
            broken_labels = ", ".join(str(a.relative_to(cgroup_root)) for a in broken)
            found += 1
            log(
                f"WARN: {label}: {prop}={own} is a NO-OP — ancestor(s) with no "
                f"{prop} of their own: {broken_labels}. cgroup v2 bounds "
                "effective protection by EVERY ancestor cgroup's own value; "
                "add a matching value there, or this floor protects nothing."
            )
    return found


def main() -> int:
    found = audit_tree(SCAN_ROOT, CG)
    if found:
        log(f"done — {found} no-op memory.min/memory.low configuration(s) found under {SCAN_ROOT}")
    else:
        log(f"done — no no-op memory.min/memory.low configurations found under {SCAN_ROOT}")
    return 0  # audit-only: a finding is a WARN, never a failure


if __name__ == "__main__":
    sys.exit(main())
