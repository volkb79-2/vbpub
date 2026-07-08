#!/usr/bin/env python3
# Measure the disk's r/w IOPS and r/w bandwidth ceilings with fio (4 passes)
# and cache them at /var/lib/gstammtisch/io-baseline.env.
#
# Consumers:
#   - setup-cgroups.sh sources /var/lib/gstammtisch/io-baseline.env
#   - ciu governance parses the same plain KEY=VALUE cache
# Policy:
#   setup-cgroups.sh derives bench/buildkit/devcontainer io.max caps as
#   IO_CAP_PCT (default 80%) of the measured ceilings in this cache.
#
# Incident notes carried over from the shell predecessor:
#   - libaio, NOT the psync default: psync silently caps the queue at depth 1
#     and measures single-request latency, not the ceiling (6928 vs 90197
#     IOPS on this host, 2026-07-07).
#   - refuse a pre-existing testfile: cleanup must never delete a real file an
#     operator pointed IO_BASELINE_TESTFILE at (codex review, 2026-07-07).
#   - atomic cache write: an interrupted run must never leave a truncated
#     cache that consumers then trust for 30 days.
#   - sustained-v3 (2026-07-08): burst measurement through the hypervisor
#     cache produced 4.3 GB/s 'seq read' - ramp_time + 4G span + incompressible
#     buffers measure the storage system's real sustained capacity, which is
#     what the 80% caps must protect.

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


OUT = Path("/var/lib/gstammtisch/io-baseline.env")
DEFAULT_TESTFILE = "/var/lib/pterodactyl/io-baseline.testfile"
DEFAULT_SIZE = "4G"
DEFAULT_RUNTIME = 40
DEFAULT_RAMP = 10
FRESHNESS_SECONDS = 30 * 86400


def positive_int(value: object, label: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not an integer: {value!r}") from exc
    if ivalue <= 0:
        raise ValueError(f"{label} must be > 0: {ivalue}")
    return ivalue


def env_positive_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    try:
        return positive_int(raw, name)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc


def env_string(name: str, fallback: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    return raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="io-baseline.py")
    parser.add_argument("--force", action="store_true", help="ignore a fresh cache")
    parser.add_argument(
        "--runtime",
        type=lambda s: positive_int(s, "--runtime"),
        default=env_positive_int("IO_BASELINE_RUNTIME", DEFAULT_RUNTIME),
        help="fio runtime in seconds (default: IO_BASELINE_RUNTIME or 40)",
    )
    parser.add_argument(
        "--ramp",
        type=lambda s: positive_int(s, "--ramp"),
        default=env_positive_int("IO_BASELINE_RAMP", DEFAULT_RAMP),
        help="fio ramp time in seconds (default: IO_BASELINE_RAMP or 10)",
    )
    parser.add_argument(
        "--testfile",
        default=os.environ.get("IO_BASELINE_TESTFILE", DEFAULT_TESTFILE),
        help="benchmark file path (default: IO_BASELINE_TESTFILE or /var/lib/pterodactyl/io-baseline.testfile)",
    )
    return parser.parse_args()


def cache_is_fresh(path: Path) -> bool:
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < FRESHNESS_SECONDS


def print_cached_cache(path: Path) -> None:
    text = path.read_text()
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def maybe_warn_soulmask_running() -> None:
    try:
        ps = subprocess.run(
            ["docker", "ps", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return
    if ps.returncode != 0:
        return

    for cid in ps.stdout.split():
        try:
            top = subprocess.run(
                ["docker", "top", cid],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return
        if top.returncode == 0 and "WSServer-Linux-Shipping" in top.stdout:
            print(
                "WARNING: Soulmask is RUNNING - the 4 fio passes will hold the disk saturated "
                "roughly 4 x (ramp + runtime) seconds plus a one-time 4G layout write "
                "(~3.5-4 min with defaults)."
            )
            print("         Ctrl-C within 5s to abort...")
            time.sleep(5)
            return


def detect_engine() -> str:
    help_proc = subprocess.run(
        ["fio", "--enghelp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if "libaio" in (help_proc.stdout or ""):
        return "libaio"
    print(
        "WARN: libaio engine unavailable - psync fallback measures queue-depth-1 latency, "
        "not the true IOPS ceiling"
    )
    return "psync"


def run_fio_json(
    name: str,
    filename: str,
    size: str,
    runtime: int,
    ramp: int,
    engine: str,
    rw: str,
    bs: str,
    iodepth: int,
    path: Path,
) -> None:
    cmd = [
        "fio",
        f"--name={name}",
        f"--filename={filename}",
        f"--size={size}",
        f"--rw={rw}",
        f"--bs={bs}",
        "--direct=1",
        "--randrepeat=0",
        "--norandommap",
        "--refill_buffers",
        "--buffer_compress_percentage=0",
        f"--ioengine={engine}",
        f"--iodepth={iodepth}",
        "--numjobs=1",
        "--time_based",
        f"--runtime={runtime}",
        f"--ramp_time={ramp}",
        "--output-format=json",
        f"--output={path}",
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=None, check=True)


def parse_fio_report(path: Path, direction: str, metric: str, fallback_metric: str | None = None) -> int:
    raw = path.read_text()
    start = raw.find("{")
    if start < 0:
        raise ValueError(f"fio report {path} did not contain JSON")
    report = json.loads(raw[start:])
    try:
        job = report["jobs"][0]
        section = job[direction]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"fio report {path} missing jobs[0].{direction}") from exc

    value = section.get(metric)
    if value is None and fallback_metric is not None:
        fallback = section.get(fallback_metric)
        if fallback is None:
            raise ValueError(f"fio report {path} missing {direction}.{metric} and fallback {fallback_metric}")
        value = int(fallback) * 1024

    return positive_int(value, f"{direction}.{metric}")


def parse_fio_p99_us(path: Path, direction: str) -> int | None:
    raw = path.read_text()
    start = raw.find("{")
    if start < 0:
        raise ValueError(f"fio report {path} did not contain JSON")
    report = json.loads(raw[start:])
    try:
        job = report["jobs"][0]
        section = job[direction]
        clat_ns = section["clat_ns"]
        percentile = clat_ns["percentile"]
        value = percentile["99.000000"]
    except (KeyError, IndexError, TypeError):
        return None
    return int(value) // 1000


def clean_path(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def write_cache_atomic(values: dict[str, int | str], engine: str, out: Path) -> None:
    measured_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    tmp = Path(str(out) + ".tmp")
    lines = [
        f"RIOPS_MAX={values['RIOPS_MAX']}",
        f"WIOPS_MAX={values['WIOPS_MAX']}",
        f"RBW_MAX_BPS={values['RBW_MAX_BPS']}",
        f"WBW_MAX_BPS={values['WBW_MAX_BPS']}",
        f"IO_ENGINE={engine}",
        f"RIOPS_ENGINE={engine}",
        f"MEASURED_AT={measured_at}",
    ]
    for key in ("RIOPS_P99_US", "RBW_P99_US", "WIOPS_P99_US", "WBW_P99_US"):
        if key in values:
            lines.append(f"{key}={values[key]}")
    lines.extend(
        [
            "MEASURE_METHOD=sustained-v3",
            f"RAMP_SEC={values['RAMP_SEC']}",
            f"RUNTIME_SEC={values['RUNTIME_SEC']}",
            f"TESTFILE_SIZE={values['TESTFILE_SIZE']}",
            "",
        ]
    )
    body = "\n".join(lines)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out)


def pct80(value: int) -> int:
    return value * 4 // 5


def main() -> int:
    args = parse_args()

    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 1
    if shutil.which("fio") is None:
        print("fio not installed (apt install fio)", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)

    if OUT.exists() and not args.force and cache_is_fresh(OUT):
        print_cached_cache(OUT)
        return 0

    maybe_warn_soulmask_running()

    testfile = Path(args.testfile)
    if testfile.exists() or testfile.is_symlink():
        print(
            f"ERROR: test file {testfile} already exists - refusing to reuse/delete it.",
            file=sys.stderr,
        )
        print(
            "       Remove it or set IO_BASELINE_TESTFILE to a fresh path.",
            file=sys.stderr,
        )
        return 1

    engine = detect_engine()
    fio_tmpfiles: list[Path] = []
    values: dict[str, int | str] = {}

    try:
        runs = [
            ("riops-baseline", "randread", "4k", 32, "read", "iops", None, "RIOPS_MAX", "RIOPS_P99_US"),
            ("rbw-baseline", "read", "128k", 8, "read", "bw_bytes", "bw", "RBW_MAX_BPS", "RBW_P99_US"),
            ("wiops-baseline", "randwrite", "4k", 32, "write", "iops", None, "WIOPS_MAX", "WIOPS_P99_US"),
            ("wbw-baseline", "write", "128k", 8, "write", "bw_bytes", "bw", "WBW_MAX_BPS", "WBW_P99_US"),
        ]

        testfile_size = env_string("IO_BASELINE_SIZE", DEFAULT_SIZE)
        values["RAMP_SEC"] = args.ramp
        values["RUNTIME_SEC"] = args.runtime
        values["TESTFILE_SIZE"] = testfile_size

        for name, rw, bs, iodepth, direction, metric, fallback, key, p99_key in runs:
            fd, raw_path = tempfile.mkstemp(prefix="io-baseline-", suffix=".json")
            os.close(fd)
            report = Path(raw_path)
            fio_tmpfiles.append(report)
            run_fio_json(name, str(testfile), testfile_size, args.runtime, args.ramp, engine, rw, bs, iodepth, report)
            values[key] = parse_fio_report(report, direction, metric, fallback)
            p99_value = parse_fio_p99_us(report, direction)
            if p99_value is not None:
                values[p99_key] = p99_value

        numeric_keys = {"RIOPS_MAX", "RBW_MAX_BPS", "WIOPS_MAX", "WBW_MAX_BPS", "RAMP_SEC", "RUNTIME_SEC"}
        p99_keys = {"RIOPS_P99_US", "RBW_P99_US", "WIOPS_P99_US", "WBW_P99_US"}
        for key, value in values.items():
            if key in numeric_keys:
                if not isinstance(value, int) or value <= 0:
                    raise ValueError(f"{key} must be > 0: {value}")
                continue
            if key in p99_keys:
                if not isinstance(value, int) or value < 0:
                    raise ValueError(f"{key} must be >= 0: {value}")
                continue
            if key == "TESTFILE_SIZE":
                if not isinstance(value, str) or not value:
                    raise ValueError("TESTFILE_SIZE must be a non-empty string")
                continue
            raise ValueError(f"unexpected cache key: {key}")

        write_cache_atomic(values, engine, OUT)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        clean_path(testfile)
        for path in fio_tmpfiles:
            clean_path(path)
        clean_path(Path(str(OUT) + ".tmp"))

    print(f"RIOPS_MAX={values['RIOPS_MAX']} (80%={pct80(values['RIOPS_MAX'])})")
    print(f"RBW_MAX_BPS={values['RBW_MAX_BPS']} (80%={pct80(values['RBW_MAX_BPS'])})")
    print(f"WIOPS_MAX={values['WIOPS_MAX']} (80%={pct80(values['WIOPS_MAX'])})")
    print(f"WBW_MAX_BPS={values['WBW_MAX_BPS']} (80%={pct80(values['WBW_MAX_BPS'])})")
    for key in ("RIOPS_P99_US", "RBW_P99_US", "WIOPS_P99_US", "WBW_P99_US"):
        if key in values:
            print(f"{key}={values[key]}us")
    print("applied on next setup-cgroups.sh run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
