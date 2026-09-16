#!/usr/bin/env python3
"""Measure and write the MDT disk IO benchmark results with the kernel io.cost matrix.

The benchmark implementation is the vendored Linux ``iocost_coef_gen.py``
already used by ``scripts/debian-install-v2``. This adapter gives MDT the
same six measurements, but always uses a file target: measuring a raw device
would be destructive. The file is retained so the results can record the
filesystem/device on which the numbers were obtained.

The runtime cap consumer receives four derived ceilings: the lower sequential
or random 4-KiB IOPS value for each direction, and sequential bandwidth for
each direction. The complete io.cost matrix is also stored for audit and
future consumers.

Result validity is identity-based, not age-based. A measurement remains
current while its configured test file and Docker data path resolve to the
same filesystem and underlying block-device identity. If the disk or target
changes, the results are rejected even if they were written moments ago.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


OUT = Path(os.environ.get("IO_BASELINE_ENV") or "/var/lib/mdt/io-baseline.env")
DEFAULT_TESTFILE = "/var/lib/mdt/iocost-coef-fio.testfile"
DEFAULT_GENERATOR = "/usr/local/lib/mdt/iocost_coef_gen.py"
DEFAULT_SIZE_GB = 16
DEFAULT_DURATION = 120
DEFAULT_NUMJOBS = 1
METHOD = "iocost-coef-gen"
SCHEMA_VERSION = "2"
MATRIX_KEYS = ("RBPS", "RSEQIOPS", "RRANDIOPS", "WBPS", "WSEQIOPS", "WRANDIOPS")
SAFE_HOST_PATH_RE = re.compile(
    r"/(?:[A-Za-z0-9._+@%=:,-]+(?:/[A-Za-z0-9._+@%=:,-]+)*)?"
)
REQUIRED_RESULT_FIELDS = (
    "SCHEMA_VERSION", "KERNEL_RELEASE", "GENERATOR_SHA256",
    "RIOPS_MAX", "WIOPS_MAX", "RBW_MAX_BPS", "WBW_MAX_BPS",
    "DEVNO", "TESTFILE_STAT_DEV", "DOCKER_STAT_DEV", "FINDMNT_SOURCE",
    "DEVICE_SIZE_SECTORS", "DEVICE_ROTATIONAL", "DEVICE_TOPOLOGY", "TESTFILE",
    "TESTFILE_SIZE_BYTES", *MATRIX_KEYS,
)
RESULT_RE = re.compile(
    r"(?P<devno>\d+:\d+)\s+"
    r"rbps=(?P<rbps>\d+)\s+rseqiops=(?P<rseqiops>\d+)\s+"
    r"rrandiops=(?P<rrandiops>\d+)\s+wbps=(?P<wbps>\d+)\s+"
    r"wseqiops=(?P<wseqiops>\d+)\s+wrandiops=(?P<wrandiops>\d+)"
)


def positive_int(value: object, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not an integer: {value!r}") from exc
    if number <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mdt-io-baseline.py",
        description=(
            "Run the official kernel io.cost coefficient matrix against a "
            "persistent file target and write identity-bound MDT benchmark "
            "results. Host shell only; a container invocation is refused."
        ),
    )
    parser.add_argument("--force", action="store_true", help="bypass current-result reuse and deliberately remeasure after the running-container warning")
    parser.add_argument("--check-results", action="store_true", help="check result identity and structure only; never run fio")
    parser.add_argument("--output", default=os.environ.get("IO_BASELINE_ENV", str(OUT)), help="benchmark-results file (default: IO_BASELINE_ENV or /var/lib/mdt/io-baseline.env)")
    parser.add_argument("--testfile", default=os.environ.get("IO_BASELINE_TESTFILE", DEFAULT_TESTFILE), help="persistent file target (default: IO_BASELINE_TESTFILE or /var/lib/mdt/iocost-coef-fio.testfile)")
    parser.add_argument("--generator", default=os.environ.get("IOCOST_COEF_GENERATOR", DEFAULT_GENERATOR), help="iocost_coef_gen.py path")
    parser.add_argument("--testfile-size-gb", type=float, default=float(os.environ.get("IO_BASELINE_SIZE_GB", DEFAULT_SIZE_GB)), metavar="GIGABYTES", help=f"file size passed to the official generator (default: {DEFAULT_SIZE_GB})")
    parser.add_argument("--duration", type=lambda value: positive_int(value, "--duration"), default=positive_int(os.environ.get("IO_BASELINE_DURATION", DEFAULT_DURATION), "IO_BASELINE_DURATION"), metavar="SECONDS", help=f"duration of each of the six matrix runs (default: {DEFAULT_DURATION})")
    parser.add_argument("--numjobs", type=lambda value: positive_int(value, "--numjobs"), default=positive_int(os.environ.get("IO_BASELINE_NUMJOBS", DEFAULT_NUMJOBS), "IO_BASELINE_NUMJOBS"), metavar="JOBS", help=f"parallel fio jobs per matrix run (default: {DEFAULT_NUMJOBS})")
    return parser.parse_args()


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            values[key] = value
    return values


def _source_for(path: Path) -> str:
    result = subprocess.run(
        ["findmnt", "-no", "SOURCE", "--target", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _top_block_device(source: str) -> Path | None:
    """Resolve a findmnt source to its top block device, including LVM."""
    if not source.startswith("/dev/"):
        return None
    node = Path(os.path.realpath(source))
    if not node.exists():
        return None
    name = node.name
    for _ in range(8):
        slaves = sorted(Path(f"/sys/class/block/{name}/slaves").glob("*"))
        if len(slaves) == 1:
            name = slaves[0].name
            continue
        sys_node = Path(f"/sys/class/block/{name}")
        try:
            resolved = sys_node.resolve()
        except OSError:
            break
        # A partition's resolved path is .../block/<disk>/<partition>.
        if (sys_node / "partition").exists() and resolved.parent.name != "block":
            name = resolved.parent.name
            continue
        break
    candidate = Path("/dev") / name
    return candidate if candidate.exists() else None


def _udev_property(device: Path, name: str) -> str:
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", "--name", str(device)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    prefix = f"{name}="
    for line in (result.stdout or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _device_facts(device: Path) -> dict[str, str]:
    name = device.name
    sys_node = Path(f"/sys/class/block/{name}")
    try:
        topology = str(sys_node.resolve())
    except OSError:
        topology = ""
    facts = {
        "DEVICE_SERIAL": _udev_property(device, "ID_SERIAL") or _udev_property(device, "ID_WWN"),
        "DEVICE_MODEL": _udev_property(device, "ID_MODEL"),
        "DEVICE_SIZE_SECTORS": "",
        "DEVICE_ROTATIONAL": "",
        "DEVICE_TOPOLOGY": topology,
    }
    for key, relative in (("DEVICE_SIZE_SECTORS", "size"), ("DEVICE_ROTATIONAL", "queue/rotational")):
        try:
            facts[key] = (sys_node / relative).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return facts


def device_identity(testfile: Path, io_dev_path: Path | None = None) -> dict[str, str]:
    """Return stable device, filesystem and target identity facts.

    The filesystem device check catches a test file placed on the wrong mount;
    the block facts catch a disk replacement even when a major/minor number is
    reused. Missing udev serials are tolerated, while topology/size/model are
    still compared.
    """
    source = _source_for(testfile)
    device = _top_block_device(source)
    if device is None:
        raise ValueError(
            f"{testfile} is on {source or 'an unknown filesystem'}, not a host block device; "
            "place IO_BASELINE_TESTFILE on the same local disk as Docker data"
        )
    try:
        device_stat = os.stat(device)
        test_stat = os.stat(testfile)
        docker_stat = os.stat("/var/lib/docker")
    except OSError as exc:
        raise ValueError(f"cannot inspect IO baseline device identity: {exc}") from exc
    devno = f"{os.major(device_stat.st_rdev)}:{os.minor(device_stat.st_rdev)}"
    if test_stat.st_dev != docker_stat.st_dev:
        raise ValueError(
            f"{testfile} and /var/lib/docker are on different filesystems "
            f"({test_stat.st_dev} versus {docker_stat.st_dev}); choose a target on the Docker data disk"
        )
    facts = _device_facts(device)
    facts.update({
        "DEVNO": devno,
        "TESTFILE_STAT_DEV": str(test_stat.st_dev),
        "DOCKER_STAT_DEV": str(docker_stat.st_dev),
        "FINDMNT_SOURCE": source,
    })
    if io_dev_path and str(io_dev_path) not in ("", "auto"):
        selected_top = _top_block_device(str(io_dev_path))
        if selected_top is None:
            raise ValueError(f"cannot resolve IO_DEV_PATH {io_dev_path} to a host block device")
        if selected_top.resolve() != device.resolve():
            raise ValueError(
                f"IO_DEV_PATH {io_dev_path} resolves to {selected_top}, but the benchmark target is on {device}; "
                "choose the Docker-data device for both settings"
            )
        try:
            selected = os.stat(io_dev_path)
            selected_devno = f"{os.major(selected.st_rdev)}:{os.minor(selected.st_rdev)}"
        except OSError as exc:
            raise ValueError(f"cannot inspect IO_DEV_PATH {io_dev_path}: {exc}") from exc
        # IO_DEV_PATH can be an LVM mapper while the generator reports the
        # top physical disk. The resolved-top comparison above accepts that
        # legitimate indirection but refuses a cap applied to another disk.
        facts["CONFIGURED_IO_DEVNO"] = selected_devno
        facts["CONFIGURED_IO_DEVICE_TOPOLOGY"] = str(selected_top.resolve())
    return facts


def results_are_valid(path: Path) -> bool:
    values = parse_env(path)
    if (
        values.get("SCHEMA_VERSION") != SCHEMA_VERSION
        or values.get("MEASURE_METHOD") != METHOD
        or not values.get("MEASURED_AT")
    ):
        return False
    if any(not values.get(key) for key in REQUIRED_RESULT_FIELDS):
        return False
    try:
        return all(int(values[key]) > 0 for key in (*MATRIX_KEYS, "RIOPS_MAX", "WIOPS_MAX", "RBW_MAX_BPS", "WBW_MAX_BPS", "TESTFILE_SIZE_BYTES"))
    except (KeyError, ValueError):
        return False


def results_are_current(path: Path, testfile: Path | None = None) -> bool:
    """Return true only when the results describe this current disk target."""
    if not results_are_valid(path):
        return False
    values = parse_env(path)
    target = testfile or Path(values["TESTFILE"])
    try:
        if not target.is_file() or int(values.get("TESTFILE_SIZE_BYTES", "0")) != target.stat().st_size:
            return False
        if values.get("KERNEL_RELEASE") != os.uname().release:
            return False
        generator = discover_generator(
            Path(os.environ.get("IOCOST_COEF_GENERATOR", DEFAULT_GENERATOR))
        )
        if generator is not None and values.get("GENERATOR_SHA256") != generator_digest(generator):
            return False
        configured = os.environ.get("IO_DEV_PATH", "")
        current = device_identity(target, Path(configured) if configured and configured != "auto" else None)
    except (OSError, ValueError, TypeError):
        return False
    if values.get("CONFIGURED_IO_DEVNO") and current.get("CONFIGURED_IO_DEVNO") != values["CONFIGURED_IO_DEVNO"]:
        return False
    return (
        all(values.get(key) == value for key, value in current.items()
            if key in ("DEVNO", "TESTFILE_STAT_DEV", "DOCKER_STAT_DEV",
                       "FINDMNT_SOURCE", "DEVICE_SERIAL", "DEVICE_MODEL",
                       "DEVICE_SIZE_SECTORS", "DEVICE_ROTATIONAL",
                       "DEVICE_TOPOLOGY"))
        and Path(values.get("TESTFILE", "")).resolve() == target.resolve()
    )


def host_context_error(root: Path | None = None) -> str | None:
    root = Path("/") if root is None else root
    if ((root / ".dockerenv").exists() or (root / "run/.containerenv").exists() or
            (root == Path("/") and os.environ.get("container"))):
        return "the IO baseline must run from a host shell, not inside a devcontainer or other container; UID 0 there is not host root"
    try:
        pid1 = (root / "proc/1/comm").read_text(encoding="utf-8").strip()
    except OSError:
        pid1 = ""
    if pid1 != "systemd" or not (root / "run/systemd/system").is_dir():
        return "the IO baseline requires systemd as PID 1 and /run/systemd/system; run it on the Docker host"
    return None


def print_results(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def warn_running_containers() -> None:
    try:
        result = subprocess.run(["docker", "ps", "-q"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    except FileNotFoundError:
        return
    if result.returncode == 0 and result.stdout.split():
        print("WARNING: containers are running. The six io.cost fio runs temporarily saturate the disk; use a quiet maintenance window.")
        print("         Ctrl-C within 5 seconds to abort.")
        import time
        time.sleep(5)


def discover_generator(path: Path) -> Path | None:
    candidates = [path, Path(__file__).resolve().parents[3] / "scripts/debian-install-v2/tools/iocost_coef_gen.py"]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def generator_digest(path: Path) -> str:
    """Fingerprint the exact official benchmark implementation in use."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_results(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path) + ".tmp")
    lines = [f"{key}={value}" for key, value in values.items()]
    lines.append("")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    testfile = Path(args.testfile).expanduser()
    for label, path in (("--output", output), ("--testfile", testfile)):
        if not path.is_absolute():
            print(f"ERROR: {label} must be an absolute host path: {path}", file=sys.stderr)
            return 2
        if any(character.isspace() for character in str(path)):
            print(f"ERROR: {label} contains whitespace; choose a shell-safe host path: {path}", file=sys.stderr)
            return 2
        if not SAFE_HOST_PATH_RE.fullmatch(str(path)):
            print(
                f"ERROR: {label} contains shell-special characters; choose a simple shell-safe host path: {path}",
                file=sys.stderr,
            )
            return 2
    if output.resolve() == testfile.resolve():
        print("ERROR: --output and --testfile must be different paths", file=sys.stderr)
        return 2
    if args.testfile_size_gb <= 0:
        print("ERROR: --testfile-size-gb must be greater than zero", file=sys.stderr)
        return 2
    if args.check_results:
        if results_are_current(output, testfile):
            print(f"current io.cost benchmark results: {output} (device and target identity match)")
            return 0
        print(f"no current io.cost benchmark results: {output} (missing, invalid, or device/target identity changed)", file=sys.stderr)
        return 1
    if os.geteuid() != 0:
        print("ERROR: run as root from the Docker host", file=sys.stderr)
        return 1
    if output.exists() and not args.force and results_are_current(output, testfile):
        print_results(output)
        return 0
    generator = discover_generator(Path(args.generator))
    if generator is None:
        print(f"ERROR: official iocost coefficient generator not found: {args.generator}", file=sys.stderr)
        return 1
    if shutil.which("fio") is None or shutil.which("pv") is None:
        print("ERROR: the official generator needs fio and pv (install fio and pv)", file=sys.stderr)
        return 1
    expected_size = int(args.testfile_size_gb * 2**30)
    if testfile.exists() and testfile.stat().st_size != expected_size:
        print(f"ERROR: refusing to replace {testfile}: existing size is {testfile.stat().st_size} bytes, expected {expected_size}", file=sys.stderr)
        return 1
    try:
        testfile.parent.mkdir(parents=True, exist_ok=True)
        testfile.touch(exist_ok=True)
        identity = device_identity(testfile, Path(os.environ["IO_DEV_PATH"]) if os.environ.get("IO_DEV_PATH") else None)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    warn_running_containers()
    command = [
        sys.executable, str(generator), "--quiet", "--testfile", str(testfile),
        "--testfile-size-gb", str(args.testfile_size_gb), "--duration", str(args.duration),
        "--numjobs", str(args.numjobs),
    ]
    print("Running official io.cost coefficient benchmark: six fio runs, about "
          f"{args.duration * 6 // 60} minutes minimum; the target disk will be saturated.")
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=None, text=True, check=False)
    except OSError as exc:
        print(f"ERROR: could not start official generator: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"ERROR: official io.cost benchmark exited {result.returncode}; existing benchmark results remain untouched", file=sys.stderr)
        return result.returncode
    match = RESULT_RE.search(result.stdout or "")
    if not match:
        print("ERROR: official generator returned no parseable coefficient line; existing benchmark results remain untouched", file=sys.stderr)
        return 1
    if match.group("devno") != identity["DEVNO"]:
        print(f"ERROR: device identity changed during benchmark ({identity['DEVNO']} before, {match.group('devno')} reported)", file=sys.stderr)
        return 1
    try:
        final_identity = device_identity(testfile, Path(os.environ["IO_DEV_PATH"]) if os.environ.get("IO_DEV_PATH") else None)
        if final_identity != identity:
            raise ValueError("device or filesystem identity changed during benchmark")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}; existing benchmark results remain untouched", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    matrix = {key: match.group(name) for key, name in (
        ("RBPS", "rbps"), ("RSEQIOPS", "rseqiops"), ("RRANDIOPS", "rrandiops"),
        ("WBPS", "wbps"), ("WSEQIOPS", "wseqiops"), ("WRANDIOPS", "wrandiops"),
    )}
    results = {
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "MEASURE_METHOD": METHOD,
        "MEASURED_AT": now,
        "KERNEL_RELEASE": os.uname().release,
        "GENERATOR_SHA256": generator_digest(generator),
        **identity,
        "TESTFILE": str(testfile.resolve()),
        "TESTFILE_SIZE_BYTES": str(expected_size),
        # io.max cannot distinguish sequential and random I/O. Use the lower
        # point for IOPS so either access pattern remains within the ceiling.
        "RIOPS_MAX": str(min(int(matrix["RSEQIOPS"]), int(matrix["RRANDIOPS"]))),
        "WIOPS_MAX": str(min(int(matrix["WSEQIOPS"]), int(matrix["WRANDIOPS"]))),
        "RBW_MAX_BPS": matrix["RBPS"], "WBW_MAX_BPS": matrix["WBPS"],
        **matrix, "DURATION_SEC": str(args.duration), "NUMJOBS": str(args.numjobs),
    }
    try:
        write_results(output, results)
    except OSError as exc:
        print(f"ERROR: could not write {output}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote current io.cost benchmark results: {output}")
    print("io.max mapping: read/write IOPS = random 4 KiB matrix points; read/write bandwidth = sequential matrix points")
    print_results(output)
    return 0


def main() -> int:
    args = parse_args()
    context_error = host_context_error()
    if context_error:
        print(f"ERROR: {context_error}", file=sys.stderr)
        print("Leave the devcontainer and rerun this command from the host checkout.", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
