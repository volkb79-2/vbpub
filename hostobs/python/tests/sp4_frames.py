"""SP-4-shaped BLOCK frames (8 columns, n cgroups) built with the reference encoder.

Shared by `test_client_budgets.py` and `bench_decode.py` (WIRE.md 7.8). Two variants of a
frame carry distinct rows bodies (the entity references of two rows are swapped), so a decoder
that alternates them never takes the byte-identical skip.
"""

from __future__ import annotations

import sys
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python" / "reference"))

import hostobs_wire_ref as ref  # noqa: E402

METRICS = (
    ("cpu.usage_usec", "usec", "counter", "u64"),
    ("mem.current", "bytes", "gauge", "u64"),
    ("mem.anon", "bytes", "gauge", "u64"),
    ("pids.current", "count", "gauge", "u64"),
    ("io.rbytes", "bytes", "counter", "u64"),
    ("io.wbytes", "bytes", "counter", "u64"),
    ("psi.cpu.some.avg10", "ratio", "gauge", "f64"),
    ("psi.io.some.total", "usec", "counter", "u64"),
)


def dictionary(n: int) -> dict:
    entries = [{"kind": "entity", "inc": 1000 + i, "text": f"/system.slice/unit-{1000 + i}.scope"} for i in range(n)]
    for name, unit, kind, _type in METRICS:
        entries.append(
            {"kind": "metric", "text": name, "facts": {"unit": unit, "kind": kind, "scope": "cgroup", "source": "cgroupfs"}}
        )
    return {"generation": 1, "entries": entries}


def frame(n: int, variant: int = 0, with_dictionary: bool = False) -> bytes:
    ents = list(range(n))
    if variant:
        ents[0], ents[1] = ents[1], ents[0]
    columns = []
    for c, (_name, _unit, kind, vtype) in enumerate(METRICS):
        column = {"metric": n + c, "type": vtype, "kind": kind, "quality": ["ok"] * n}
        if vtype == "f64":
            column["value_bits"] = [f"0x{0x3FD0000000000000 + r:016x}" for r in range(n)]
        else:
            column["values"] = [str(1_000_000_000 + r * 1000 + c * 17) for r in range(n)]
        columns.append(column)
    spec = {
        "type": "block",
        "corr": 5,
        "sections": ["header"] + (["dictionary"] if with_dictionary else []) + ["rows", "columns"],
        "header": {
            "boot_id": "00112233-4455-6677-8899-aabbccddeeff",
            "epoch": 1,
            "seq": 1,
            "mono_start_ns": 1,
            "mono_end_ns": 2,
            "boot_ns": 3,
            "real_ns": 4,
            "dict_gen": 1,
            "n_rows": n,
            "dropped": 0,
            "flags": 0,
        },
        "rows": [{"ent": e, "t_off_us": 20 * i} for i, e in enumerate(ents)],
        "columns": columns,
    }
    if with_dictionary:
        spec["dictionary"] = dictionary(n)
    return ref.encode_frame(spec)
