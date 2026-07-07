"""Stack-wide resource governance — CIU v2.

Normative contract: docs/SPEC.md S15 (stack-wide resource governance).

Pure-logic module: no engine.py/deploy.py imports, no CIU config-model
imports. ``composefile.generate_overlay`` consumes :func:`resolve_config` +
:func:`build_injections` to compute the per-service overlay fragment;
``workspace_env.generate_ciu_env`` consumes :func:`derive_read_iops` to expose
``CIU_GOV_READ_IOPS`` for shell/template consumption without requiring a full
CIU render (S15.6).

Public API
----------
GOVERNANCE_DEFAULTS    : dict[str, Any]   — code-level defaults (S15.2)
INJECTED_KEYS          : tuple[str, ...]  — compose keys governance may inject
BASELINE_PATH_ENV_VAR  : str              — env override for the baseline file
DEFAULT_BASELINE_PATH  : Path             — neutral default baseline location
LEGACY_BASELINE_PATH   : Path             — legacy (gstammtisch) fallback location
FALLBACK_READ_IOPS     : int              — used when no baseline is found
BASELINE_MAX_AGE_DAYS  : int              — freshness window for re-measurement
resolve_config(raw) -> dict
baseline_search_candidates(configured="") -> list[Path]
resolve_baseline_path(configured="") -> Path | None
read_iops_baseline(path) -> int | None
derive_read_iops(configured, *, baseline_path=None, configured_path="") -> (int, str)
detect_device() -> str
resolve_device(configured) -> (str, str)
build_injections(compose_services, config) -> (dict[str, dict], list[str])
parse_fio_json(text) -> int
select_fio_engine(fio_bin="fio") -> (str, str | None)
run_iops_baseline(output_path=None, *, runtime_s=10, force=False) -> int
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# S15.2 — code-level defaults (the stack's [<root>.governance] table overrides)
# ---------------------------------------------------------------------------

GOVERNANCE_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "cgroup_parent": "besteffort.slice",
    "mem_limit": "1g",
    "mem_reservation": "256m",
    "read_iops": 0,          # 0 = derive from the host io-baseline (S15.4)
    "write_iops": 400,
    "device": "",            # "" = autodetect the disk backing /var/lib/docker
    "baseline_path": "",     # "" = search order (S15.4); explicit path wins
    "exempt_services": [],
}

# The compose service-level keys governance may inject. Precedence (S15.3):
# any of these keys already present in the AUTHOR's rendered service block
# is left untouched — governance only fills in what the author didn't set.
INJECTED_KEYS: tuple[str, ...] = (
    "cgroup_parent",
    "mem_limit",
    "mem_reservation",
    "blkio_config",
)

# ---------------------------------------------------------------------------
# S15.4 — read_iops derivation from the host's measured I/O baseline
# ---------------------------------------------------------------------------

# CIU ships as a wheel to arbitrary hosts, so the baseline location must not
# couple to any one host's tooling. Search order (first EXISTING file wins):
#   (a) governance table key `baseline_path`   (per-stack config)
#   (b) env CIU_GOV_BASELINE_PATH              (per-host override)
#   (c) DEFAULT_BASELINE_PATH                  (neutral default; written by
#                                               `ciu iops-baseline`, S15.9)
#   (d) LEGACY_BASELINE_PATH                   (gstammtisch host tooling)
BASELINE_PATH_ENV_VAR = "CIU_GOV_BASELINE_PATH"
DEFAULT_BASELINE_PATH = Path("/var/lib/ciu/io-baseline.env")
LEGACY_BASELINE_PATH = Path("/var/lib/gstammtisch/io-baseline.env")
FALLBACK_READ_IOPS = 200

_RIOPS_MAX_RE = re.compile(r'^\s*RIOPS_MAX\s*=\s*"?(\d+)"?\s*$')


def resolve_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge *raw* (a stack's ``[<root>.governance]`` table) over the defaults.

    *raw* is ``None`` when the stack declares no ``governance`` table at all
    (the common case — this function is only ever called once a caller has
    already decided the table is present, see composefile.generate_overlay).
    Unknown/extra keys in *raw* pass through unchanged (no schema — S15.2).
    """
    cfg = dict(GOVERNANCE_DEFAULTS)
    if raw:
        cfg.update(raw)

    if not isinstance(cfg.get("enabled"), bool):
        raise ValueError(
            f"[S15.2] [<root>.governance].enabled must be a boolean, got "
            f"{cfg.get('enabled')!r}"
        )

    exempt = cfg.get("exempt_services") or []
    if not isinstance(exempt, list) or not all(isinstance(x, str) for x in exempt):
        raise ValueError(
            "[S15.2] [<root>.governance].exempt_services must be a list of "
            f"service-name strings, got {exempt!r}"
        )
    cfg["exempt_services"] = list(exempt)
    return cfg


def baseline_search_candidates(configured: str = "") -> list[Path]:
    """Ordered baseline-file candidates (S15.4 resolution order).

    ``(a)`` the governance table's ``baseline_path`` (when non-empty) →
    ``(b)`` env :data:`BASELINE_PATH_ENV_VAR` (when set) →
    ``(c)`` :data:`DEFAULT_BASELINE_PATH` →
    ``(d)`` :data:`LEGACY_BASELINE_PATH`.
    Constants are read at call time so tests can monkeypatch them.
    """
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    env_path = os.environ.get(BASELINE_PATH_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(DEFAULT_BASELINE_PATH)
    candidates.append(LEGACY_BASELINE_PATH)
    return candidates


def resolve_baseline_path(configured: str = "") -> Path | None:
    """First **existing** file among :func:`baseline_search_candidates`, else ``None``."""
    for candidate in baseline_search_candidates(configured):
        if candidate.is_file():
            return candidate
    return None


def read_iops_baseline(path: Path) -> int | None:
    """Parse ``RIOPS_MAX=<int>`` from a shell-style env file; ``None`` if absent/unparseable.

    The file is written by ``ciu iops-baseline`` (S15.9) or an external host
    measurement (e.g. the gstammtisch cgroup tooling's ``io-baseline.sh``);
    this function only reads it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _RIOPS_MAX_RE.match(line.strip())
        if m:
            return int(m.group(1))
    return None


def derive_read_iops(
    configured: int,
    *,
    baseline_path: Path | None = None,
    configured_path: str = "",
) -> tuple[int, str]:
    """Resolve the effective ``read_iops`` value and a human-readable source note.

    S15.4: ``configured == 0`` means "derive" — read ``RIOPS_MAX`` from the
    baseline file and take two thirds of it (matches the host's
    ``setup-cgroups.sh`` bench-cap formula). Any nonzero *configured* value is
    explicit and wins outright. Falls back to :data:`FALLBACK_READ_IOPS` when
    no baseline file resolves or the resolved file has no ``RIOPS_MAX`` line.

    *baseline_path* pins one exact file (tests / direct callers); when ``None``
    the S15.4 search order applies via :func:`resolve_baseline_path`, with
    *configured_path* as the governance table's ``baseline_path`` value.
    """
    if configured:
        return int(configured), "explicit"
    path = baseline_path if baseline_path is not None else resolve_baseline_path(configured_path)
    if path is None:
        searched = ", ".join(str(c) for c in baseline_search_candidates(configured_path))
        return (
            FALLBACK_READ_IOPS,
            f"fallback default (no io-baseline file found; searched: {searched})",
        )
    baseline = read_iops_baseline(path)
    if baseline is not None:
        return (baseline * 2) // 3, f"derived: 2/3 of baseline RIOPS_MAX={baseline} ({path})"
    return (
        FALLBACK_READ_IOPS,
        f"fallback default ({path} not found or has no RIOPS_MAX)",
    )


# ---------------------------------------------------------------------------
# S15.5 — device autodetection (blkio target)
# ---------------------------------------------------------------------------

# /dev/vda1 -> /dev/vda ; /dev/sda1 -> /dev/sda ; /dev/xvda1 -> /dev/xvda
_PARTITION_SUFFIX_RE = re.compile(r"^(/dev/[a-zA-Z]+)\d+$")
# /dev/nvme0n1p1 -> /dev/nvme0n1 ; /dev/mmcblk0p1 -> /dev/mmcblk0
_NVME_PARTITION_SUFFIX_RE = re.compile(r"^(/dev/(?:nvme\d+n\d+|mmcblk\d+))p\d+$")


def _resolve_parent_disk(device: str) -> str:
    """Strip a partition suffix so blkio applies to the whole disk (S15.5).

    LVM/mapper devices (``/dev/mapper/...``) and already-whole-disk paths
    match neither pattern and are returned unchanged.
    """
    m = _NVME_PARTITION_SUFFIX_RE.match(device)
    if m:
        return m.group(1)
    m = _PARTITION_SUFFIX_RE.match(device)
    if m:
        return m.group(1)
    return device


def detect_device() -> str:
    """Autodetect the block device backing ``/var/lib/docker`` (S15.5).

    Runs ``findmnt -no SOURCE --target /var/lib/docker`` and resolves a
    partition source to its parent disk (e.g. ``/dev/vda1`` -> ``/dev/vda``).
    Returns ``""`` on any failure (missing ``findmnt``, non-Linux, non-zero
    exit, unparseable/non-device output) — the caller then skips
    ``blkio_config`` injection entirely and logs a notice.
    """
    try:
        result = subprocess.run(
            ["findmnt", "-no", "SOURCE", "--target", "/var/lib/docker"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return ""
    source = lines[0]
    if not source.startswith("/dev/"):
        return ""
    return _resolve_parent_disk(source)


def resolve_device(configured: str) -> tuple[str, str]:
    """Resolve the blkio device path: an explicit config value always wins."""
    if configured:
        return configured, "explicit"
    detected = detect_device()
    if detected:
        return detected, "autodetected from /var/lib/docker mount"
    return "", "autodetect failed (findmnt unavailable, or /var/lib/docker not on a block device)"


# ---------------------------------------------------------------------------
# S15.3 — per-service injection with author-override precedence
# ---------------------------------------------------------------------------

def build_injections(
    compose_services: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Compute the per-service overlay fragment for every non-exempt service.

    Parameters
    ----------
    compose_services : ``{service_name: rendered_block}`` from the base
        (already-rendered) compose file — the AUTHOR's keys are read off each
        block so an author-set key is never overridden (S15.3).
    config : an already-:func:`resolve_config`-d governance table.

    Returns
    -------
    (injections, notes)
        ``injections`` maps service name -> the subset of
        ``cgroup_parent``/``mem_limit``/``mem_reservation``/``blkio_config``
        not already set by the author. Exempt services and services with
        nothing left to inject (author set every key) are absent from the
        dict entirely. ``notes`` is a list of human-readable strings for the
        caller's one-line summary log (S15.7).
    """
    exempt = set(config.get("exempt_services") or [])
    device, device_note = resolve_device(config.get("device", ""))
    read_iops, read_note = derive_read_iops(
        int(config.get("read_iops", 0) or 0),
        configured_path=str(config.get("baseline_path") or ""),
    )
    write_iops = int(config.get("write_iops", 0) or 0)

    injections: dict[str, dict[str, Any]] = {}
    skipped_exempt = 0
    services_touched = 0

    for svc_name, block in compose_services.items():
        if svc_name in exempt:
            skipped_exempt += 1
            continue
        author_keys = set(block.keys()) if isinstance(block, Mapping) else set()
        frag: dict[str, Any] = {}
        if "cgroup_parent" not in author_keys:
            frag["cgroup_parent"] = config["cgroup_parent"]
        if "mem_limit" not in author_keys:
            frag["mem_limit"] = config["mem_limit"]
        if "mem_reservation" not in author_keys:
            frag["mem_reservation"] = config["mem_reservation"]
        if "blkio_config" not in author_keys and device:
            frag["blkio_config"] = {
                "device_read_iops": [{"path": device, "rate": read_iops}],
                "device_write_iops": [{"path": device, "rate": write_iops}],
            }
        if frag:
            injections[svc_name] = frag
            services_touched += 1

    notes = [
        f"cgroup_parent={config['cgroup_parent']}",
        f"mem_limit={config['mem_limit']}",
        f"mem_reservation={config['mem_reservation']}",
        f"read_iops={read_iops} ({read_note})",
        f"write_iops={write_iops}",
        f"device={device or '(none — blkio_config skipped)'} ({device_note})",
        f"services_injected={services_touched} exempt={skipped_exempt}",
    ]
    return injections, notes


# ---------------------------------------------------------------------------
# S15.9 — self-contained baseline measurement (`ciu iops-baseline`)
# ---------------------------------------------------------------------------

BASELINE_MAX_AGE_DAYS = 30

_FIO_TESTFILE_NAME = "ciu-iops-baseline.testfile"
_FIO_OUTPUT_NAME = "ciu-iops-baseline.fio.json"


def parse_fio_json(text: str) -> int:
    """Extract the randread IOPS from fio JSON output text; round to int.

    fio prepends human-readable ``note: ...`` lines even into the file named
    by ``--output`` (observed live on this feature's origin host), which
    breaks a naive ``json.load``. Parse from the FIRST ``{`` onward.

    Raises ``ValueError`` when no JSON object is present or the document does
    not carry ``jobs[0].read.iops``.
    """
    idx = text.find("{")
    if idx < 0:
        raise ValueError("no JSON object found in fio output")
    try:
        data = json.loads(text[idx:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"fio output is not valid JSON after the first '{{': {exc}") from exc
    jobs = data.get("jobs") if isinstance(data, Mapping) else None
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("fio JSON has no jobs[] array")
    read_block = jobs[0].get("read") if isinstance(jobs[0], Mapping) else None
    iops = read_block.get("iops") if isinstance(read_block, Mapping) else None
    if iops is None:
        raise ValueError("fio JSON has no jobs[0].read.iops")
    return int(round(float(iops)))


def select_fio_engine(fio_bin: str = "fio") -> tuple[str, str | None]:
    """Pick the fio ioengine: ``libaio`` when available, else ``psync`` + warning.

    fio's default engine is psync, which silently caps the effective iodepth
    at 1 — the result is then queue-depth-1 latency, NOT the device's IOPS
    ceiling. ``--enghelp`` lists the compiled-in engines; when libaio is
    absent we fall back to psync and return a warning string the caller MUST
    surface (the measurement is still written, flagged via ``RIOPS_ENGINE``).
    """
    try:
        result = subprocess.run(
            [fio_bin, "--enghelp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and "libaio" in (result.stdout or ""):
        return "libaio", None
    return (
        "psync",
        "[WARN] fio has no libaio engine — falling back to psync. psync caps "
        "iodepth at 1, so the result is queue-depth-1 latency, not the "
        "device's IOPS ceiling (RIOPS_MAX will read low).",
    )


def _pick_test_dir(output_path: Path) -> Path:
    """Directory for fio's scratch test file: alongside the output, else /var/tmp."""
    parent = output_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if os.access(parent, os.W_OK):
            return parent
    except OSError:
        pass
    return Path("/var/tmp")


def _write_baseline_file(output_path: Path, riops: int, engine: str) -> None:
    """Write the shell-style baseline env file atomically (tmp + os.replace)."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = (
        f"# CIU io-baseline — generated by `ciu iops-baseline` at {stamp}\n"
        f"# fio randread 4k direct=1 (see docs/SPEC.md S15.9)\n"
        f"RIOPS_MAX={riops}\n"
        f"RIOPS_ENGINE={engine}\n"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, output_path)


def run_iops_baseline(
    output_path: Path | str | None = None,
    *,
    runtime_s: int = 10,
    force: bool = False,
) -> int:
    """Measure the disk's randread IOPS ceiling with fio and write the baseline (S15.9).

    Explicit opt-in only — never invoked automatically (not from
    ``ciu env generate``, not from the overlay generator). Returns a CLI exit
    code (0 also covers the two benign no-op paths: fio absent, fresh result
    kept).

    Behavior contract (each point learned from a live incident or S15.9):
      * fio absent → clear notice, exit 0, nothing written (derivation will
        use the S15.4 fallback).
      * an existing result younger than :data:`BASELINE_MAX_AGE_DAYS` days is
        kept unless *force* (exit 0, nothing written).
      * ioengine libaio, psync fallback with an explicit warning (see
        :func:`select_fio_engine`).
      * fio JSON goes to a temp file via ``--output``/``--output-format=json``
        and is parsed from the first ``{`` (see :func:`parse_fio_json`).
      * the scratch test file (and the fio output temp file) are ALWAYS
        deleted, success or failure (``finally``).
    """
    output_path = Path(output_path) if output_path else DEFAULT_BASELINE_PATH

    fio_bin = shutil.which("fio")
    if fio_bin is None:
        print(
            "[NOTICE] fio not installed — iops-baseline skipped; read_iops "
            f"derivation will use the fallback {FALLBACK_READ_IOPS}. Install "
            "fio and re-run `ciu iops-baseline` to measure this host.",
            flush=True,
        )
        return 0

    if output_path.exists() and not force:
        age_days = (time.time() - output_path.stat().st_mtime) / 86400.0
        if age_days < BASELINE_MAX_AGE_DAYS:
            print(
                f"[NOTICE] existing baseline {output_path} is {age_days:.1f} "
                f"days old (< {BASELINE_MAX_AGE_DAYS}) — keeping it. Use "
                "--force to re-measure.",
                flush=True,
            )
            return 0

    engine, engine_warning = select_fio_engine(fio_bin)
    if engine_warning:
        print(engine_warning, flush=True)

    print(
        f"[WARN] measuring the disk's randread IOPS ceiling: this generates "
        f"~{runtime_s}s of SATURATING read I/O on the device backing the "
        "test-file location. Avoid running it while latency-sensitive "
        "workloads are active.",
        flush=True,
    )

    test_dir = _pick_test_dir(output_path)
    test_file = test_dir / _FIO_TESTFILE_NAME
    fio_output = test_dir / _FIO_OUTPUT_NAME

    cmd = [
        fio_bin,
        "--name=riops-baseline",
        f"--filename={test_file}",
        "--size=1G",
        "--rw=randread",
        "--bs=4k",
        "--direct=1",
        f"--ioengine={engine}",
        "--iodepth=32",
        "--numjobs=1",
        "--time_based",
        f"--runtime={runtime_s}",
        f"--output={fio_output}",
        "--output-format=json",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=runtime_s * 6 + 120,  # layout of the 1G file can dwarf the run itself
            check=False,
        )
        if result.returncode != 0:
            print(
                f"[ERROR] fio exited {result.returncode}: "
                f"{(result.stderr or result.stdout or '').strip()}",
                flush=True,
            )
            return 1
        try:
            output_text = fio_output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[ERROR] fio wrote no output file ({fio_output}): {exc}", flush=True)
            return 1
        try:
            riops = parse_fio_json(output_text)
        except ValueError as exc:
            print(f"[ERROR] could not parse fio JSON output: {exc}", flush=True)
            return 1
    except subprocess.TimeoutExpired:
        print("[ERROR] fio timed out; no baseline written.", flush=True)
        return 1
    finally:
        # ALWAYS remove the scratch artifacts — a leftover 1G test file is
        # exactly the kind of silent disk-eater this feature exists to govern.
        test_file.unlink(missing_ok=True)
        fio_output.unlink(missing_ok=True)

    try:
        _write_baseline_file(output_path, riops, engine)
    except OSError as exc:
        print(
            f"[ERROR] could not write {output_path}: {exc} "
            "(does the directory exist / do you need sudo?)",
            flush=True,
        )
        return 1

    print(
        f"[SUCCESS] wrote {output_path}: RIOPS_MAX={riops} RIOPS_ENGINE={engine} "
        f"(derived read_iops cap would be {riops * 2 // 3})",
        flush=True,
    )
    return 0
