#!/usr/bin/env python3
"""mdt host-setup cpu-benchmark: measure this host's CPU ONCE and report the persisted state (HB-3).

This is a thin wrapper. The measurement itself is the shared, sha256-pinned tool
``scripts/debian-install-v2/tools/cpu_benchmark.py`` (installed by ``install.sh`` as
``/usr/local/lib/mdt/cpu_benchmark.py`` with its ``.sha256`` pin and ``cpu_benchmark.pins.json``);
there is no second implementation. The wrapper adds only the host-setup policy:

* refuses to run when the installed tool does not match its pin file (exit 3);
* refuses to re-measure when ``cpu-benchmark.json`` exists unless ``--force`` (exit 2);
* refuses while the host is under CPU pressure: CPU ``some avg10`` at or above 5 (exit 2);
* passes BOTH Geekbench pins (version and archive sha256) for this architecture from the pins file; an
  architecture without a pin is refused, never run unverified (exit 2);
* ``--resolve`` re-reads the public result page of the existing record and never runs Geekbench.

``doctor`` is read-only and prints one ``OK|WARN|INFO <message>`` line per finding; ``check.sh``
(``mdt-host-check.sh``) turns them into its own OK/WARN lines. It warns when the measurement is
missing, partial (no scores) or STALE (the stored ``cpu_model`` / ``cpus_online`` differ from the live
host, e.g. after a VM resize or migration), or when the claim file is readable by others.

State directory: ``/var/lib/mdt/host-facts`` (override ``MDT_HOST_FACTS_DIR`` for tests only).
Exit codes of ``cpu-benchmark``: the tool's own (0 ok, 2 refused, 3 failed, 4 partial).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable

TOOL_DIR = Path("/usr/local/lib/mdt")
STATE_DIR = Path("/var/lib/mdt/host-facts")
TOOL_NAME = "cpu_benchmark.py"
PINS_NAME = "cpu_benchmark.pins.json"
SCHEMA = "mdt-host-cpu-benchmark/1"
MAX_PSI = 5.0  # memo 4.6: host-setup refuses at CPU PSI some_avg10 >= 5
INSTALLED_AS = "/usr/local/sbin/mdt-host-cpu-benchmark.py"
RERUN = f"sudo {INSTALLED_AS} cpu-benchmark"


class Refused(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def mdt_version() -> str:
    return os.environ.get("MDT_VERSION", "").strip() or os.environ.get("MDT_IMAGE_VERSION", "").strip() or "unknown"


def state_dir() -> Path:
    return Path(os.environ.get("MDT_HOST_FACTS_DIR") or STATE_DIR)


def verify_tool(tool_dir: Path) -> Path:
    """The installed tool must equal its installed pin file, byte for byte (same rule as the installer)."""
    tool = tool_dir / TOOL_NAME
    pin = tool_dir / (TOOL_NAME + ".sha256")
    try:
        digest = hashlib.sha256(tool.read_bytes()).hexdigest()
    except OSError as exc:
        raise Refused(f"the shared tool {tool} is missing ({exc.strerror}); re-run install.sh", 3) from None
    try:
        committed = pin.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        raise Refused(f"tool integrity check failed: {pin} is missing or empty; re-run install.sh", 3) from None
    if committed != digest:
        raise Refused(f"tool integrity check failed: {tool} sha256 {digest} does not match {pin.name}; re-run install.sh", 3)
    return tool


def load_pin(tool_dir: Path, machine: str) -> tuple[str, str]:
    try:
        data = json.loads((tool_dir / PINS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"{tool_dir / PINS_NAME} is missing or unreadable; re-run install.sh", 3) from None
    entry = data.get(machine) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not entry.get("version") or not entry.get("sha256"):
        raise Refused(f"no pinned Geekbench archive for architecture {machine!r}; this host cannot be measured unverified")
    return str(entry["version"]), str(entry["sha256"])


def cpu_psi_some_avg10(proc_pressure: Path = Path("/proc/pressure/cpu")) -> float | None:
    try:
        for line in proc_pressure.read_text().splitlines():
            if line.startswith("some "):
                for field in line.split():
                    if field.startswith("avg10="):
                        return float(field.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def run_cpu_benchmark(
    *, force: bool, resolve: bool, tool_dir: Path = TOOL_DIR, facts: Path | None = None,
    psi: Callable[[], float | None] = cpu_psi_some_avg10, machine: str | None = None,
    runner: Callable[[list[str]], int] | None = None,
) -> int:
    facts = facts or state_dir()
    tool = verify_tool(tool_dir)
    record = facts / "cpu-benchmark.json"
    if resolve:
        if force:
            raise Refused("--resolve and --force cannot be combined")
        if not record.is_file():
            raise Refused(f"nothing to resolve: {record} does not exist; run `cpu-benchmark` first")
        argv = [sys.executable, str(tool), "--resolve", "--state-dir", str(facts)]
    else:
        if record.exists() and not force:
            raise Refused(
                f"{record} already exists: a host is measured once. Re-measure deliberately with --force "
                "(or complete a partial record with --resolve)"
            )
        pressure = psi()
        if pressure is None:
            raise Refused("cannot read /proc/pressure/cpu; refusing to measure without knowing the CPU pressure")
        if pressure >= MAX_PSI:
            raise Refused(f"CPU pressure is {pressure:g} (some avg10), at or above {MAX_PSI:g}: not measuring a busy host; retry when quiet")
        version, sha256 = load_pin(tool_dir, machine or platform.machine())
        argv = [
            sys.executable, str(tool), "--state-dir", str(facts), "--geekbench-version", version, "--sha256", sha256,
            "--max-psi", f"{MAX_PSI:g}", "--produced-by", f"mdt host-setup {mdt_version()}",
        ]
        if force:
            argv.append("--force")
    if runner is not None:
        return runner(argv)
    return subprocess.run(argv, check=False).returncode


def _load_tool(tool: Path):
    spec = importlib.util.spec_from_file_location("mdt_cpu_benchmark_tool", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def doctor(*, tool_dir: Path = TOOL_DIR, facts: Path | None = None, env=None) -> list[tuple[str, str]]:
    """Read-only findings as (level, message). Never raises on a bad file: a bad file is a WARN."""
    facts = facts or state_dir()
    record_path = facts / "cpu-benchmark.json"
    claim_path = facts / "cpu-benchmark.claim.json"
    findings: list[tuple[str, str]] = []
    if not record_path.exists():
        return [("WARN", f"no CPU benchmark measurement ({record_path}); CPU-speed normalisation falls back to the table. Measure once: {RERUN}")]
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema") != SCHEMA:
            raise ValueError("unexpected schema")
    except (OSError, ValueError) as exc:
        return [("WARN", f"{record_path} is unreadable or not {SCHEMA} ({exc}); re-measure deliberately: {RERUN} --force")]
    mode = stat.S_IMODE(record_path.stat().st_mode)
    if mode & 0o022:
        findings.append(("WARN", f"{record_path} is writable by group/others (mode {mode:04o}); expected 0644 root"))
    if claim_path.exists():
        claim_mode = stat.S_IMODE(claim_path.stat().st_mode)
        if claim_mode & 0o077:
            findings.append(("WARN", f"{claim_path} (Geekbench claim link) is accessible to group/others (mode {claim_mode:04o}); expected 0600"))
    if "claim" in json.dumps(record).lower():
        findings.append(("WARN", f"{record_path} mentions a claim link; the readable file must never carry it"))
    if not isinstance(record.get("single_core"), int) or not isinstance(record.get("multi_core"), int):
        findings.append(("WARN", f"CPU benchmark is partial (no scores, reason {record.get('reason')}); complete it without re-running: {RERUN} --resolve"))
        return findings
    try:
        tool = verify_tool(tool_dir)
        module = _load_tool(tool)
        live = module.cpu_identity(env or module.Env())
    except (Refused, Exception) as exc:  # noqa: BLE001 - doctor reports, never crashes the check
        findings.append(("WARN", f"cannot compare the measurement with this host: {exc}"))
        return findings
    stale = []
    if record.get("cpu_model") != live.get("cpu_model"):
        stale.append(f"cpu_model {record.get('cpu_model')!r} != live {live.get('cpu_model')!r}")
    if record.get("cpus_online") != live.get("cpus_online"):
        stale.append(f"cpus_online {record.get('cpus_online')} != live {live.get('cpus_online')}")
    if stale:
        findings.append((
            "WARN",
            f"CPU benchmark is STALE ({'; '.join(stale)}): the host was resized or migrated; consumers fall back to the "
            f"table (HOST_MEASUREMENT_STALE). Re-measure deliberately: {RERUN} --force",
        ))
    else:
        findings.append((
            "OK",
            f"CPU benchmark current: single={record['single_core']} multi={record['multi_core']} "
            f"({record.get('tool')} {record.get('tool_version')}, {str(record.get('measured_at', ''))[:10]}, "
            f"{record.get('cpus_online')} vCPU, {record.get('cpu_model')})",
        ))
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mdt-host-cpu-benchmark", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("cpu-benchmark", help="measure the host CPU once with Geekbench (shared pinned tool)")
    run.add_argument("--force", action="store_true", help="re-measure although a measurement exists")
    run.add_argument("--resolve", action="store_true", help="re-read the result page of the existing record; never runs Geekbench")
    sub.add_parser("doctor", help="report the persisted measurement (read-only; OK/WARN/INFO lines)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        for level, message in doctor():
            print(f"{level} {message}")
        return 0
    try:
        return run_cpu_benchmark(force=args.force, resolve=args.resolve)
    except Refused as exc:
        print(f"mdt-host-cpu-benchmark: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
