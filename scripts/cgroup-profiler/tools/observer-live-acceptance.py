#!/usr/bin/env python3
"""Bounded live acceptance probe for the contract-2 resource observer (Sol B5).

Run INSIDE an opted-in gated peer (a Run-Gate container that has the
`/run/cgprofile/observer` directory mounted read-only). It performs no write
and creates no cgroup; the events it classifies are caused by the operator
steps documented in docs/CONSUMERS.md ("Live observer acceptance").

    observer-live-acceptance.py contract1  [--socket PATH]
    observer-live-acceptance.py read       [--socket PATH] [--tid N] --out FILE
    observer-live-acceptance.py compare    --before FILE --after FILE
                                           --expect sibling-only|enforced

`contract1` passes only when a contract-1 request is refused with
`bad-argument` naming the unsupported contract. `compare` passes only when the
observed counter deltas match the expectation: a sibling-only event moves a
shared ancestor's HIERARCHICAL counter but not its local one; an event
enforced at the shared ancestor moves the ancestor's LOCAL counter. Exit 0 is
a pass, 1 a failed expectation, 2 a usage or transport problem.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any, Dict, List, Optional

DEFAULT_SOCKET = "/run/cgprofile/observer/resource-observer.sock"
TIMEOUT_SECONDS = 6.0
_PAIRS = (("pids_events", "pids_events_local"), ("memory_events", "memory_events_local"))


def request(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """One newline-delimited request/response over the observer socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect(path)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode("utf-8"))


def contract1_refused(response: Dict[str, Any]) -> bool:
    error = response.get("error")
    return (
        response.get("ok") is False
        and isinstance(error, dict)
        and error.get("code") == "bad-argument"
        and "contract" in str(error.get("message", "")).lower()
    )


def check_shape(response: Dict[str, Any]) -> List[str]:
    """Problems with a contract-2 success response ([] when well formed)."""
    problems: List[str] = []
    if response.get("ok") is not True or response.get("contract") != 2:
        return [f"not a contract-2 success: {str(response)[:120]}"]
    if response.get("pids_events_scope") != "hierarchical":
        problems.append("pids_events_scope must be 'hierarchical'")
    if response.get("memory_events_scope") not in ("hierarchical", "local"):
        problems.append("memory_events_scope must be 'hierarchical' or 'local'")
    cgroups = response.get("cgroups")
    if not isinstance(cgroups, list) or not cgroups:
        return problems + ["cgroups must be a non-empty leaf-to-root list"]
    if cgroups[-1].get("path") != "/":
        problems.append("ancestry must end at '/'")
    for entry in cgroups:
        if not isinstance(entry.get("path"), str) or not isinstance(entry.get("mount_id"), int):
            problems.append(f"entry without path/mount_id: {str(entry)[:80]}")
        for hier, local in _PAIRS:
            if entry.get(hier) is not None and entry.get(local) is None:
                problems.append(f"{entry.get('path')}: {hier} has no {local}")
    return problems


def _total(events: Optional[Dict[str, Any]]) -> int:
    if not isinstance(events, dict):
        return 0
    return sum(v for v in events.values() if isinstance(v, int) and not isinstance(v, bool))


def deltas(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Per common cgroup path: hierarchical and local counter growth."""
    old = {e["path"]: e for e in before.get("cgroups", [])}
    out: Dict[str, Dict[str, int]] = {}
    for entry in after.get("cgroups", []):
        prev = old.get(entry["path"])
        if prev is None:
            continue
        row = {"hierarchical": 0, "local": 0}
        for hier, local in _PAIRS:
            row["hierarchical"] += max(0, _total(entry.get(hier)) - _total(prev.get(hier)))
            row["local"] += max(0, _total(entry.get(local)) - _total(prev.get(local)))
        out[entry["path"]] = row
    return out


def evaluate(before: Dict[str, Any], after: Dict[str, Any], expect: str) -> List[str]:
    """Failures of the expectation ([] means pass)."""
    rows = deltas(before, after)
    ancestors = [p for p in rows if p != "/"]
    if expect == "sibling-only":
        hit = [p for p in ancestors[1:] if rows[p]["hierarchical"] > 0 and rows[p]["local"] == 0]
        return [] if hit else [
            "no shared ancestor shows hierarchical growth with unchanged local counters"
        ]
    if expect == "enforced":
        hit = [p for p in ancestors[1:] if rows[p]["local"] > 0]
        return [] if hit else ["no shared ancestor shows local counter growth"]
    return [f"unknown expectation {expect!r}"]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ("contract1", "read"):
        p = sub.add_parser(name)
        p.add_argument("--socket", default=DEFAULT_SOCKET)
        if name == "read":
            p.add_argument("--tid", type=int, default=None)
            p.add_argument("--out", required=True)
    cmp_p = sub.add_parser("compare")
    cmp_p.add_argument("--before", required=True)
    cmp_p.add_argument("--after", required=True)
    cmp_p.add_argument("--expect", required=True, choices=("sibling-only", "enforced"))
    args = parser.parse_args(argv)
    try:
        if args.step == "contract1":
            tid = threading_tid()
            response = request(args.socket, {
                "verb": "resource-limits", "args": {"thread_id": tid}, "contract": 1,
            })
            ok = contract1_refused(response)
            print(("PASS" if ok else "FAIL") + f": contract-1 request -> {json.dumps(response)[:160]}")
            return 0 if ok else 1
        if args.step == "read":
            tid = args.tid if args.tid is not None else threading_tid()
            response = request(args.socket, {
                "verb": "resource-limits", "args": {"thread_id": tid}, "contract": 2,
            })
            problems = check_shape(response)
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(response, fh, indent=1, sort_keys=True)
            print(("PASS" if not problems else "FAIL") + f": read -> {args.out}"
                  + "".join(f"\n  - {p}" for p in problems))
            return 0 if not problems else 1
        with open(args.before, encoding="utf-8") as fh:
            before = json.load(fh)
        with open(args.after, encoding="utf-8") as fh:
            after = json.load(fh)
        failures = evaluate(before, after, args.expect)
        print(("PASS" if not failures else "FAIL") + f": {args.expect}"
              + "".join(f"\n  - {p}" for p in failures))
        return 0 if not failures else 1
    except (OSError, ValueError) as exc:
        print(f"observer-live-acceptance: {exc}", file=sys.stderr)
        return 2


def threading_tid() -> int:
    return os.getpid() if not hasattr(os, "gettid") else os.gettid()


if __name__ == "__main__":
    sys.exit(main())
