#!/usr/bin/env python3
"""Measured dev.slice caps from the iobench result (MDT-TESTHOST-IO).

Reads `proposals.caps.dev_aggregate.{RIOPS,WIOPS,RBPS,WBPS}` of the iobench
`result.json` (schema 1) and prints `KEY=value` lines, one per usable value.
It is the ONE reader of that section for the runtime half of host-setup
(install.sh and the reconcile service source it through
`_mdt_load_iobench_caps` in mdt-container-io-caps.lib.sh) and for the wizard.

A value is usable only when ALL of these hold; otherwise nothing is printed and
the reason goes to stderr (exit 3):
  * the file parses and is schema 1 (a newer schema is refused, never guessed);
  * run.status == "complete" and quality.plausible is true;
  * identity.disk.devno equals the ROOT disk's major:minor, and at least one more
    identity field (model, serial, size_bytes, wwn) is present on both sides and
    equal, none differing (a devno-only match is NOT enough to size a cap);
  * the value is a positive integer and its status is "proven" or "unproven"
    ("absent" values are skipped).  An `unproven` value is printed together with
    its reasons on stderr: it is a measured worst-case figure already scaled by
    the iobench utilisation factor U, not a guess.

No numbers are invented and nothing is written to the host.
Exit codes: 0 at least one value printed, 3 not usable, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_RESULT = Path("/var/lib/iobench/result.json")
SUPPORTED_SCHEMA = 1
CAP_KEYS = ("RIOPS", "WIOPS", "RBPS", "WBPS")
IDENTITY_FIELDS = ("model", "serial", "size_bytes", "wwn")


class NotUsable(Exception):
    """The result cannot size a cap; the message is the operator-facing reason."""


def load_caps(path: Path, live_devno: str | None, live_identity: dict | None) -> tuple[dict[str, int], dict[str, str]]:
    """Return (values, notes) or raise NotUsable.  values: KEY -> positive int; notes: KEY -> provenance."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NotUsable(f"no benchmark result at {path}") from None
    except OSError as exc:
        raise NotUsable(f"cannot read {path}: {exc}") from None
    try:
        doc = json.loads(raw)
        schema = doc["schema"]
    except (ValueError, KeyError, TypeError):
        raise NotUsable(f"{path} is not a result document (no schema)") from None
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise NotUsable(f"{path} has an invalid schema {schema!r}")
    if schema > SUPPORTED_SCHEMA:
        raise NotUsable(f"{path} is schema {schema}; this reader knows schema {SUPPORTED_SCHEMA}")
    try:
        status = doc["run"]["status"]
        plausible = doc["quality"]["plausible"]
        caps = doc["proposals"]["caps"]["dev_aggregate"]
        disk = doc["identity"]["disk"]
        devno = disk["devno"]
    except (KeyError, TypeError):
        raise NotUsable(f"{path} lacks run.status, quality.plausible, identity.disk.devno or proposals.caps.dev_aggregate "
                        "(a quick/matrix-only run carries no caps; run `iobench calibrate`)") from None
    if status != "complete":
        raise NotUsable(f"the benchmark run status is {status!r}, not complete")
    if plausible is not True:
        raise NotUsable("the benchmark marked its own result implausible (quality.plausible is not true)")
    if not live_devno:
        raise NotUsable("this host's root disk could not be derived, so the result's identity cannot be compared")
    if devno != live_devno:
        raise NotUsable(f"the result is for disk {devno}, this host's root disk is {live_devno} (recalibrate)")
    compared = 0
    for field in IDENTITY_FIELDS:
        theirs, mine = disk.get(field), (live_identity or {}).get(field)
        if theirs in (None, "") or mine in (None, ""):
            continue
        if str(theirs).strip() != str(mine).strip():
            raise NotUsable(f"the result's disk {field} ({theirs}) differs from this host's root disk ({mine}) (recalibrate)")
        compared += 1
    if compared == 0:
        raise NotUsable("the result matches this disk's device number only; no model/serial/size/wwn could be compared")
    values: dict[str, int] = {}
    notes: dict[str, str] = {}
    for key in CAP_KEYS:
        entry = caps.get(key) if isinstance(caps, dict) else None
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if entry.get("status") not in ("proven", "unproven"):
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            continue
        values[key] = value
        reasons = ",".join(str(r) for r in entry.get("reasons", []) or [])
        notes[key] = f"{entry['status']}" + (f" ({reasons})" if reasons else "") + f", rule {entry.get('rule', '?')}"
    if not values:
        raise NotUsable("proposals.caps.dev_aggregate carries no positive proven/unproven value")
    return values, notes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--result", type=Path, default=None, help=f"result.json (default {DEFAULT_RESULT})")
    p.add_argument("--devno", default=None, help="root disk major:minor (from the shell library)")
    for field in IDENTITY_FIELDS:
        p.add_argument(f"--{field.replace('_', '-')}", default=None, help=f"root disk {field}")
    args = p.parse_args(argv)
    identity = {f: getattr(args, f) for f in IDENTITY_FIELDS if getattr(args, f)}
    try:
        values, notes = load_caps(args.result or DEFAULT_RESULT, args.devno, identity)
    except NotUsable as exc:
        print(f"iobench caps not usable: {exc}", file=sys.stderr)
        return 3
    for key in CAP_KEYS:
        if key in values:
            print(f"{key}={values[key]}")
            print(f"  measured {key}: {notes[key]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
