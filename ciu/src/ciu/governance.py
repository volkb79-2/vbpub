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
CGROUP_PARENT_ENV_VAR  : str              — ambient env override for cgroup_parent
resolve_cgroup_parent(configured) -> str  — errors if unresolved (no hardcoded fallback)
BASELINE_PATH_ENV_VAR  : str              — env override for the baseline file
DEFAULT_BASELINE_PATH  : Path             — neutral default baseline location
HOST_TOOLING_BASELINE_PATH : Path         — mdt host-setup's baseline, last candidate
MEASURE_METHOD_BURST   : str              — provenance marker CIU's own run writes
KNOWN_MEASURE_METHODS  : tuple[str, ...]  — markers whose semantics are known
FALLBACK_READ_IOPS     : int              — used when no baseline is found
BASELINE_MAX_AGE_DAYS  : int              — freshness window for re-measurement
resolve_config(raw) -> dict
resolve_stack_governance(stack_governance, global_config) -> dict | None   — S15.10 (shallow merge, CIU-13)
baseline_search_candidates(configured="") -> list[Path]
resolve_baseline_path(configured="") -> Path | None
read_iops_baseline(path) -> int | None
read_baseline_method(path) -> str | None
derive_read_iops(configured, *, baseline_path=None, configured_path="") -> (int, str)
detect_device() -> str
resolve_device(configured) -> (str, str)
check_slice_unit(slice_name) -> (bool | None, str)                         — D-G9 check 1
parse_size_to_bytes(value) -> int                                          — S15.16
check_slice_memory_min(slice_name, required_bytes) -> (bool | None, str)   — D-G9 check 3, S15.16
slice_ancestor_chain(slice_name) -> list[str]                              — S15.16 (systemd dash-naming)
check_memory_min_ancestor_chain(slice_name, required_bytes) -> (bool | None, str) — S15.16, walks the chain
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
    # "" = not explicitly configured by this stack; resolved at build_injections()
    # time via resolve_cgroup_parent() — no hardcoded slice-name fallback (host
    # dev-tier cgroup governance rollout, nyxloom/docs/plan-resource-governance.md):
    # an unresolvable cgroup_parent is a configuration error, not a silent default.
    "cgroup_parent": "",
    "mem_limit": "1g",
    # Docker's own `mem_swap_limit`/`--memory-swap` semantics: the COMBINED
    # mem+swap total, not swap alone (17g here = 1g RAM + 16g swap). Without
    # this key, Docker's stock default applies instead — 2x mem_limit, i.e.
    # ~2g combined, not the host dev-tier's much larger swap allowance.
    "mem_swap_limit": "17g",
    # memory.low (best-effort protection). CAVEAT (S15.16 WARNING): cgroup v2
    # bounds effective protection by ALL ancestor cgroups' own memory.low, not
    # just this slice's — a value here is a no-op under any ancestor
    # (including this project's own shipped dev-tier slices, as shipped
    # today) that has no memory.low of its own.
    "mem_reservation": "256m",
    "read_iops": 0,          # 0 = derive from the host io-baseline (S15.4)
    "write_iops": 400,
    # S15.14 — proportional IO share, Docker/compose `blkio_config.weight`
    # scale (10..1000). 0 = not set (no `weight` key injected — the container
    # gets whichever default the block device's IO controller applies).
    # CAVEAT (see S15.14): on a host whose block device is scheduled by BFQ,
    # this container's own `io.weight` cgroup file is inert — BFQ reads
    # `io.bfq.weight` instead, a different controller's file. CIU cannot
    # detect the active scheduler from here; this is documentation, not code.
    "io_weight": 0,
    # S15.15 — per-device bandwidth caps in bytes/sec, same `device` target as
    # read_iops/write_iops. 0 = uncapped (no `device_read_bps`/
    # `device_write_bps` key injected). Unlike read_iops there is no baseline-
    # derived default: an arbitrary nonzero number here would not be measured
    # from anything, so uncapped is the only honest default.
    "read_bps": 0,
    "write_bps": 0,
    "device": "",            # "" = autodetect the disk backing /var/lib/docker
    "baseline_path": "",     # "" = search order (S15.4); explicit path wins
    "exempt_services": [],
    # S15.11 — KSM opt-in injection. Repo-relative (or absolute) path to a
    # UNIVERSAL (dependency-free, -nostdlib) LD_PRELOAD shim that calls
    # prctl(PR_SET_MEMORY_MERGE). "" = disabled. When set, every non-exempt
    # service gets LD_PRELOAD + a read-only bind of the shim injected via the
    # overlay. Statically-linked binaries never run a dynamic loader, so the
    # injection is inert for them; a dependency-free .so loads under both
    # glibc and musl (a libc-linked one is FATAL under the other libc).
    "ksm_optin": "",
    # S15.16 — declared memory FLOOR (cgroup-v2 `memory.min`-equivalent), a
    # Docker size string ("2g", "512m") or "" (not declared). There is no
    # Docker/compose field for a per-container memory.min, so this is never
    # injected into the overlay: it is a stated INTENT, checked at deploy time
    # (governance_slice_preflight, S15.16) against the resolved cgroup_parent
    # slice's own live `MemoryMin=`. CIU only PLACES a container under a named
    # slice (S15.8); it never configures that slice's own resource
    # properties — a declared mem_min only means anything once the slice unit
    # itself carries a matching MemoryMin=, provisioned host-side (a static
    # .slice unit, or an optional companion such as
    # modern-debian-tools-python-debug's host-setup — never a CIU dependency).
    # WARNING (S15.16, read it): a "MemoryMin= OK" preflight verdict on that
    # ONE slice does not mean the container is protected — cgroup v2 bounds
    # effective protection by EVERY ancestor's own MemoryMin, and this
    # project's own shipped dev-tier slices set none at any level today.
    "mem_min": "",
}

# S15.11 — in-container path the shim is bound to / preloaded from.
KSM_PRELOAD_TARGET = "/opt/ksm/ksm-optin.so"

# S15.17 — the sentinel value of `governance.ksm_optin` that means "use the shim
# CIU ships", built on demand into the consumer's `.ciu/ksm/`. Any other
# non-empty value is still a path to a consumer-supplied shim (S15.11), so this
# is additive: nothing that worked before changes meaning.
BUILTIN_KSM = "builtin"

# The compose service-level keys governance may inject. Precedence (S15.3):
# any of these keys already present in the AUTHOR's rendered service block
# is left untouched — governance only fills in what the author didn't set.
# NOTE: the Compose Specification's actual key is `memswap_limit` (no
# underscore between "mem" and "swap") — this tuple lists real compose keys,
# NOT the `governance.mem_swap_limit` config-table key name (see
# build_injections() below, which translates between the two).
INJECTED_KEYS: tuple[str, ...] = (
    "cgroup_parent",
    "mem_limit",
    "memswap_limit",
    "mem_reservation",
    "blkio_config",
)

# Ambient override for cgroup_parent, mirroring BASELINE_PATH_ENV_VAR below.
# Injected by devcontainer.json's containerEnv for the shared dev/test/build
# tier (see AGENTS.md) — a stack's own explicit governance.cgroup_parent
# still always wins.
CGROUP_PARENT_ENV_VAR = "CGROUP_PARENT_DEV_BACKGROUND"


def resolve_cgroup_parent(configured: str) -> str:
    """Resolve governance's cgroup_parent: explicit stack config, else the
    ambient :data:`CGROUP_PARENT_ENV_VAR`, else a hard error.

    Only called once a caller has already confirmed governance is enabled
    (composefile.generate_overlay gates on ``gov_cfg["enabled"]`` before ever
    reaching :func:`build_injections`) — so "unresolvable" here always means
    "governance is on and nothing named a slice," never "governance is off."
    No hardcoded slice-name fallback: a stack that enables governance without
    naming a slice, and without the ambient env var present, must fail loudly
    rather than launch into whichever default happened to be baked in (see
    nyxloom/docs/plan-resource-governance.md D-G0a — this is exactly the class
    of implicit-default bug that section documents).
    """
    if configured:
        return configured
    env_value = os.environ.get(CGROUP_PARENT_ENV_VAR)
    if env_value:
        return env_value
    raise ValueError(
        "[S15.2] governance is enabled but no cgroup_parent is resolvable — "
        "set [<root>.governance].cgroup_parent explicitly, or ensure "
        f"${CGROUP_PARENT_ENV_VAR} is present in the environment (normally "
        "injected by devcontainer.json's containerEnv; see AGENTS.md)."
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
#   (d) HOST_TOOLING_BASELINE_PATH             (mdt host-setup, if installed)
# (d) is a search CANDIDATE, not a dependency: CIU never requires mdt, it just
# reuses a measurement already on the host instead of saturating the disk a
# second time to learn the same number. It ranks last so an explicit
# `ciu iops-baseline` always wins.
BASELINE_PATH_ENV_VAR = "CIU_GOV_BASELINE_PATH"
DEFAULT_BASELINE_PATH = Path("/var/lib/ciu/io-baseline.env")
HOST_TOOLING_BASELINE_PATH = Path("/var/lib/mdt/io-baseline.env")
FALLBACK_READ_IOPS = 200

_RIOPS_MAX_RE = re.compile(r'^\s*RIOPS_MAX\s*=\s*"?(\d+)"?\s*$')
_MEASURE_METHOD_RE = re.compile(r'^\s*MEASURE_METHOD\s*=\s*"?([A-Za-z0-9._-]+)"?\s*$')

# S15.4 — measurement provenance.
#
# A baseline file is a handful of KEY=VALUE lines; nothing in the format says
# HOW the numbers were produced, and different fio invocations of "randread 4k"
# do not measure the same thing. Two are known to write this format:
#
#   burst-v1      CIU's own `ciu iops-baseline`: 1G span, 10s, no ramp_time.
#                 The window includes the cache-warm burst, so on a VM (where
#                 direct=1 bypasses the guest page cache but not the
#                 hypervisor's) RIOPS_MAX reads HIGH relative to what the
#                 device sustains.
#   sustained-v3  mdt host-setup's `mdt-io-baseline.py`: 4G span, 10s ramp +
#                 40s measure, incompressible buffers. Deliberately excludes
#                 the burst; the conservative number, and the better input to
#                 a cap.
#
# A file with no MEASURE_METHOD predates this marker or came from a third
# tool — usable, but its provenance is unknown and derivation says so.
MEASURE_METHOD_BURST = "burst-v1"
KNOWN_MEASURE_METHODS: tuple[str, ...] = (MEASURE_METHOD_BURST, "sustained-v3")


def resolve_config(
    raw: Mapping[str, Any] | None,
    *,
    strict_unknown_keys: bool = False,
) -> dict[str, Any]:
    """Merge *raw* (a stack's ``[<root>.governance]`` table) over the defaults.

    *raw* is ``None`` when the stack declares no ``governance`` table at all
    (the common case — this function is only ever called once a caller has
    already decided the table is present, see composefile.generate_overlay).
    Unknown/extra keys in *raw* pass through unchanged (no schema — S15.2):
    a newer stack config running against an older CIU may legitimately name
    a key this version doesn't know yet, and that MUST NOT hard-fail.

    D-G9 check 2 — but a silently-swallowed unknown key is also exactly how
    a typo (``cgroup_parnet``) or a key misplaced into the wrong table goes
    unnoticed for a long time (a dead-config-key incident, S15.8-adjacent).
    So by default this now prints a ``[WARN] [S15.2]`` line naming every
    unrecognised key, loud enough to catch a typo, while still returning
    normally (forward-compat is preserved — this is a WARN, not a raise).
    Pass ``strict_unknown_keys=True`` to instead raise ``ValueError`` on any
    unknown key (opt-in only; the module-level default stays permissive).
    """
    cfg = dict(GOVERNANCE_DEFAULTS)
    if raw:
        unknown = sorted(k for k in raw if k not in GOVERNANCE_DEFAULTS)
        if unknown:
            message = (
                "[WARN] [S15.2] [<root>.governance] has unrecognised key(s): "
                + ", ".join(unknown)
                + " — passed through unchanged (forward-compat). If this isn't "
                "a newer key for an older CIU, check for a typo or a key meant "
                "for a different table."
            )
            if strict_unknown_keys:
                raise ValueError(message.replace("[WARN] ", ""))
            print(message, flush=True)
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

    # S15.14 — 0 means "not set, inject nothing"; any other value must be a
    # valid Docker/compose blkio weight (10..1000) or `docker compose up`
    # would itself reject it later, far from where the typo was made.
    io_weight = cfg.get("io_weight") or 0
    if io_weight and not (10 <= int(io_weight) <= 1000):
        raise ValueError(
            f"[S15.14] [<root>.governance].io_weight must be 0 (unset) or in "
            f"10..1000 (Docker's blkio weight range), got {io_weight!r}"
        )
    return cfg


def resolve_stack_governance(
    stack_governance: Mapping[str, Any] | None,
    global_config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """S15.10: merge the stack's ``governance`` table (if any) over the global
    default ``[governance]`` table (if any) — CIU-13 fix.

    Layers, shallow-merged, last-wins (mirrors S15.2's own merge rule exactly:
    ``GOVERNANCE_DEFAULTS`` -> global -> stack):

    1. *global_config* — a bare top-level ``[governance]`` table in
       ``ciu.global.toml`` (reserved namespace, S3.7): the estate-wide
       default, when present. This is the BASE layer.
    2. *stack_governance* — the stack's own ``[<root>.governance]`` table,
       already extracted by the caller from ``merged[root_key]["governance"]``
       (which itself already folds in ``ciu.global.toml``'s root-key-scoped
       section per the existing S3.3 merge — nothing new there). Its keys are
       applied OVER the base layer, one key at a time.
    3. Neither present anywhere: return ``None`` (governance stays disabled —
       no behavior change for hosts that declare it nowhere).

    **CIU-13** (reported by dstdns, 2026-08-03): the previous behavior treated
    a present *stack_governance* as winning wholesale — any stack table,
    however small, fully replaced the global default rather than layering
    over it. A stack that restated only ``mem_limit = "8g"`` therefore lost
    the global's ``enabled = true`` along with everything else, silently
    resolving (via :func:`resolve_config`'s own merge over
    ``GOVERNANCE_DEFAULTS``) to ``enabled = false`` — a one-key tuning edit
    that produced a completely unconfined container, logged identically to a
    deliberate opt-out. Making this a merge layer means a stack only needs to
    restate the keys it actually wants to change; every other key still comes
    from the global policy.

    Returns a fresh dict whenever either layer is present (defensive copy —
    mutating the result never affects either source table).
    """
    global_governance: Mapping[str, Any] | None = None
    if global_config:
        global_governance = global_config.get("governance")
    if stack_governance is None and global_governance is None:
        return None
    merged: dict[str, Any] = dict(global_governance) if global_governance else {}
    if stack_governance is not None:
        merged.update(stack_governance)
    return merged


def baseline_search_candidates(configured: str = "") -> list[Path]:
    """Ordered baseline-file candidates (S15.4 resolution order).

    ``(a)`` the governance table's ``baseline_path`` (when non-empty) →
    ``(b)`` env :data:`BASELINE_PATH_ENV_VAR` (when set) →
    ``(c)`` :data:`DEFAULT_BASELINE_PATH` →
    ``(d)`` :data:`HOST_TOOLING_BASELINE_PATH`.
    Constants are read at call time so tests can monkeypatch them.
    """
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    env_path = os.environ.get(BASELINE_PATH_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(DEFAULT_BASELINE_PATH)
    candidates.append(HOST_TOOLING_BASELINE_PATH)
    return candidates


def resolve_baseline_path(configured: str = "") -> Path | None:
    """First **existing** file among :func:`baseline_search_candidates`, else ``None``."""
    for candidate in baseline_search_candidates(configured):
        if candidate.is_file():
            return candidate
    return None


def read_iops_baseline(path: Path) -> int | None:
    """Parse ``RIOPS_MAX=<int>`` from a shell-style env file; ``None`` if absent/unparseable.

    The file is written by ``ciu iops-baseline`` (S15.9) or by an external host
    measurement (e.g. mdt host-setup's ``mdt-io-baseline.py``); this function
    only reads it. See :func:`read_baseline_method` for provenance.
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


def read_baseline_method(path: Path) -> str | None:
    """Parse ``MEASURE_METHOD=<token>``; ``None`` when the file omits it (S15.4).

    The marker records HOW the numbers were measured — see
    :data:`KNOWN_MEASURE_METHODS`. ``None`` means the file predates the marker
    or came from a third tool: still usable, provenance simply unknown.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _MEASURE_METHOD_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def _method_note(path: Path) -> str:
    """Provenance clause appended to the derivation note (never raises)."""
    method = read_baseline_method(path)
    if method is None:
        return (
            "; method=UNKNOWN (no MEASURE_METHOD marker — cannot tell a sustained "
            "measurement from a cache-warm burst; re-run `ciu iops-baseline --force` "
            "to get a marked file)"
        )
    if method not in KNOWN_MEASURE_METHODS:
        return (
            f"; method={method} (UNRECOGNISED — not one of "
            f"{', '.join(KNOWN_MEASURE_METHODS)}; treat the derived cap as unverified)"
        )
    if method == MEASURE_METHOD_BURST:
        return (
            f"; method={method} (unramped 1G/10s burst — reads high on a VM; "
            "mdt host-setup's sustained-v3 baseline is the more conservative input)"
        )
    return f"; method={method}"


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
        return (
            (baseline * 2) // 3,
            f"derived: 2/3 of baseline RIOPS_MAX={baseline} ({path}){_method_note(path)}",
        )
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
# D-G9 check 1 — named-slice existence probe (S15.8 rationale, now checkable)
# ---------------------------------------------------------------------------
#
# S15.8 explains WHY this matters: `cgroup_parent` only *places* a container
# under a named systemd slice; with the systemd cgroup driver, a slice with
# no static unit file is implicitly/transiently created on first reference,
# with NO resource limits of its own. The compose file "looks" governed
# (`cgroup_parent` is set) but the container runs completely unconfined at
# the slice level. This was previously undetectable from CIU's overlay
# generator (no host access there); the deploy-time preflight below DOES
# have host access, so it can catch it before `up` ever starts a container.


def _systemd_is_pid1() -> bool:
    """``sd_booted(3)``'s own check: does THIS mount namespace actually run
    systemd as PID 1?

    Module-level function (not a constant) so tests can monkeypatch it
    directly, mirroring how :func:`shutil.which` is monkeypatched elsewhere
    in this file.

    Checking `shutil.which("systemctl") is not None` alone is not enough: a
    common devcontainer base image ships a `systemctl` SHIM at that path
    which, when systemd is not actually running, prints a fixed
    human-readable notice ("'systemd' is not running in this container...")
    and exits **0** — no `KEY=VALUE` property line at all. A caller that
    naively parses that output for e.g. `LoadState=` would read the absence
    of a match as a definitive **False** (slice missing / no floor
    configured) rather than "cannot tell," turning an inconclusive
    environment into a false preflight ABORT — exactly the class of bug this
    governance work exists to prevent, just aimed at itself. `sd_booted(3)`
    documents `/run/systemd/system` existing as the canonical, namespace-
    local signal that systemd is genuinely PID 1 here — which is also
    exactly what that same devcontainer shim checks internally before
    deciding whether to exec the real `systemctl` or print its notice, so
    this check agrees with the shim's own logic rather than second-guessing
    its output text.
    """
    return Path("/run/systemd/system").is_dir()


def check_slice_unit(slice_name: str) -> tuple[bool | None, str]:
    """Probe whether a systemd slice UNIT is loaded on this host (D-G9 check 1).

    Returns ``(exists, note)``:

    - ``exists is None`` — this host has no ``systemctl`` at all (not a
      systemd host — CI runner, macOS, a minimal container), OR
      ``systemctl`` is present but systemd is not actually PID 1 here
      (:func:`_systemd_is_pid1` is False — a devcontainer whose `systemctl`
      is a non-systemd shim is the common case). Governed cgroup slices
      cannot be honored here regardless of what's configured, so the caller
      must SKIP the check, not fail it; *note* explains why.
    - ``exists is True`` — ``systemctl show <slice> --property=LoadState``
      reports ``LoadState=loaded``: the unit is installed and known to
      systemd.
    - ``exists is False`` — systemd is present but the slice is NOT loaded
      (typically ``LoadState=not-found`` — never installed, or installed
      under a different name than configured). This is the fail-closed case:
      Docker would silently auto-create this slice transiently with no
      limits (S15.8).

    Uses ``systemctl show --property=LoadState`` rather than ``systemctl
    cat``: ``show`` always exits 0 (even for a unit that doesn't exist) and
    reports the load state as a parseable ``KEY=VALUE`` line, so failure is a
    STRING to read, not a process exit code that a transient/permission
    error could be confused with. Any subprocess error (timeout, systemctl
    missing mid-probe, unexpected OSError) is treated the same as
    "inconclusive" and reported via ``exists is None`` — this probe never
    raises; the caller decides what an inconclusive probe means for the
    deploy.
    """
    if shutil.which("systemctl") is None:
        return None, (
            "no systemctl on this host — not a systemd host, so governed "
            "cgroup slices cannot be honored here regardless; skipping the "
            "slice-existence preflight"
        )
    if not _systemd_is_pid1():
        return None, (
            "systemctl is present but systemd is not PID 1 in this mount "
            "namespace (no /run/systemd/system — common in devcontainers, "
            "whose `systemctl` is often a non-systemd shim) — governed "
            "cgroup slices cannot be checked from inside this container; "
            "skipping the slice-existence preflight"
        )
    try:
        result = subprocess.run(
            ["systemctl", "show", slice_name, "--property=LoadState"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"systemctl probe failed ({exc}) — skipping the slice-existence preflight"

    output = (result.stdout or "").strip()
    load_state = output.split("=", 1)[1] if "=" in output else ""
    if load_state == "loaded":
        return True, f"{slice_name}: LoadState=loaded"
    return False, (
        f"{slice_name}: LoadState={load_state or '(unknown — systemctl returned no LoadState)'} "
        "— the unit is not installed on this host"
    )


# ---------------------------------------------------------------------------
# S15.16 — declared memory floor (`mem_min`) preflight (D-G9 check 3)
# ---------------------------------------------------------------------------
#
# There is no Docker/compose field for a per-container cgroup-v2 memory.min:
# a floor only has effect at the SLICE a container is placed under (S15.8),
# and CIU never configures a slice's own resource properties — only its
# existence is checked (D-G9 check 1, check_slice_unit above). `mem_min` is
# therefore a stated intent, verified rather than enforced: this preflight
# reads the resolved cgroup_parent slice's live `MemoryMin=` and reports
# whether it actually meets the declared floor.

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]?)\s*$")
_SIZE_MULTIPLIERS: dict[str, int] = {
    "": 1, "b": 1,
    "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4,
}


def parse_size_to_bytes(value: str) -> int:
    """Parse a Docker-style size string to bytes (S15.16).

    Accepts the same ``b``/``k``/``m``/``g``/``t`` (1024-based, case-
    insensitive) suffix convention Docker itself uses for ``mem_limit``/
    ``mem_reservation`` values, or a bare byte count with no suffix. Raises
    ``ValueError`` on anything else — this feeds a preflight comparison, so a
    silently-mis-parsed size would compare against the wrong number.
    """
    m = _SIZE_RE.match(value)
    if not m:
        raise ValueError(
            f"not a recognizable size (want e.g. '2g', '512m', or a raw byte "
            f"count): {value!r}"
        )
    unit = m.group(2).lower()
    if unit not in _SIZE_MULTIPLIERS:
        raise ValueError(f"unrecognised size suffix {m.group(2)!r} in {value!r}")
    return int(float(m.group(1)) * _SIZE_MULTIPLIERS[unit])


def check_slice_memory_min(slice_name: str, required_bytes: int) -> tuple[bool | None, str]:
    """Probe whether a systemd slice's live ``MemoryMin=`` meets *required_bytes*.

    Returns ``(adequate, note)``, mirroring :func:`check_slice_unit`'s shape:

    - ``adequate is None`` — no ``systemctl`` on this host, OR ``systemctl``
      is present but systemd is not actually PID 1 here
      (:func:`_systemd_is_pid1` is False — same rationale as
      :func:`check_slice_unit`): skip, don't fail.
    - ``adequate is True`` — the slice's ``MemoryMin`` (bytes) is ``>=
      required_bytes``.
    - ``adequate is False`` — the slice reports no floor (``infinity`` — the
      systemd default when unset — or ``0``, which is indistinguishable from
      "genuinely unset" and is treated the same, failing closed) or a floor
      below *required_bytes*, or the property could not be parsed.

    Never raises: any subprocess error is reported as inconclusive
    (``adequate is None``), matching :func:`check_slice_unit`.
    """
    if shutil.which("systemctl") is None:
        return None, (
            "no systemctl on this host — not a systemd host, so a slice "
            "MemoryMin= cannot be honored here regardless; skipping the "
            "mem_min preflight"
        )
    if not _systemd_is_pid1():
        return None, (
            "systemctl is present but systemd is not PID 1 in this mount "
            "namespace (no /run/systemd/system — common in devcontainers, "
            "whose `systemctl` is often a non-systemd shim) — a slice "
            "MemoryMin= cannot be checked from inside this container; "
            "skipping the mem_min preflight"
        )
    try:
        result = subprocess.run(
            ["systemctl", "show", slice_name, "--property=MemoryMin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"systemctl probe failed ({exc}) — skipping the mem_min preflight"

    output = (result.stdout or "").strip()
    raw = output.split("=", 1)[1] if "=" in output else ""
    if raw in ("", "infinity", "0"):
        return False, (
            f"{slice_name}: MemoryMin={raw or '(unknown)'} — no floor is "
            "configured on the slice unit"
        )
    try:
        live_bytes = int(raw)
    except ValueError:
        return False, f"{slice_name}: MemoryMin={raw!r} (unparseable) — treating as no floor"
    if live_bytes >= required_bytes:
        return True, f"{slice_name}: MemoryMin={live_bytes} bytes (>= required {required_bytes})"
    return False, f"{slice_name}: MemoryMin={live_bytes} bytes (< required {required_bytes})"


def slice_ancestor_chain(slice_name: str) -> list[str]:
    """Derive a systemd slice's full ancestor chain from its name alone.

    Per ``systemd.slice(5)``, a slice's name IS its ancestor path,
    dash-joined: ``dev-background.slice``'s parent is ``dev.slice``, whose
    parent is the implicit (unnamed, un-probeable) root slice. No D-Bus or
    filesystem lookup is needed — this is a pure string derivation, always
    correct for any conforming slice name.

    Returns the chain from *slice_name* itself up to (but not including) the
    implicit root, nearest-first — e.g.
    ``slice_ancestor_chain("dev-background.slice") == ["dev-background.slice", "dev.slice"]``.
    A single-segment name (e.g. ``"wings.slice"``) has no named parent short
    of root, so it returns just itself: ``["wings.slice"]``.
    """
    if not slice_name.endswith(".slice"):
        raise ValueError(f"not a slice name: {slice_name!r}")
    parts = slice_name[: -len(".slice")].split("-")
    return ["-".join(parts[:i]) + ".slice" for i in range(len(parts), 0, -1)]


def check_memory_min_ancestor_chain(slice_name: str, required_bytes: int) -> tuple[bool | None, str]:
    """Walk *slice_name*'s FULL ancestor chain checking every level's
    ``MemoryMin=``, not just *slice_name* itself (S15.16 D1/D3/D4/D5).

    cgroup v2's memory protection is bounded by EVERY ancestor cgroup's own
    ``memory.min`` (see ``docs/SPEC.md`` S15.16's WARNING): a floor on one
    slice provides ZERO real protection if any ancestor above it — all the
    way to the cgroup root — has none of its own.
    :func:`check_slice_memory_min` alone only reports on ONE link; this
    walks systemd's dash-derived ancestor chain (:func:`slice_ancestor_chain`)
    and checks each level with that same probe.

    This is a simplification of the kernel's full proportional-overcommit
    algorithm (effective protection can be *split* across siblings under
    contention) — it answers the practical question "does every ancestor
    budget AT LEAST *required_bytes*", which is exact in the common
    uncontended case and conservative (never falsely reports "protected")
    otherwise.

    Returns ``(adequate, note)``:

    - ``adequate is None`` — the FIRST probe in the chain was inconclusive
      (no ``systemctl`` / systemd not PID 1 here). This is an
      environment-wide condition, not a per-unit one, so every other level
      would be equally inconclusive — the rest of the chain is not probed.
    - ``adequate is True`` — every ancestor in the chain has an adequate
      ``MemoryMin=``.
    - ``adequate is False`` — at least one ancestor does not; *note* lists
      EVERY inadequate link found (not just the first), so an operator sees
      the whole broken chain in one preflight run rather than fixing one
      link at a time across repeated deploys.
    """
    chain = slice_ancestor_chain(slice_name)
    inadequate: list[str] = []
    for ancestor in chain:
        adequate, note = check_slice_memory_min(ancestor, required_bytes)
        if adequate is None:
            return None, note
        if not adequate:
            inadequate.append(note)
    if inadequate:
        return False, "; ".join(inadequate)
    return True, (
        f"{slice_name}: full ancestor chain ({', '.join(chain)}) all meet "
        f">= {required_bytes} bytes"
    )


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
        ``cgroup_parent``/``mem_limit``/``memswap_limit``/``mem_reservation``/
        ``blkio_config`` not already set by the author. ``blkio_config`` itself carries
        whichever of ``device_read_iops``/``device_write_iops`` (device
        resolved), ``device_read_bps``/``device_write_bps`` (device resolved
        AND a nonzero cap configured, S15.15) and ``weight`` (nonzero
        ``io_weight`` configured, S15.14 — independent of device resolution)
        actually apply; it is omitted entirely when none of those do. Exempt
        services and services with nothing left to inject (author set every
        key) are absent from the dict entirely. ``notes`` is a list of
        human-readable strings for the caller's one-line summary log (S15.7).
        ``mem_min`` (S15.16) is never injected — see :func:`check_slice_memory_min`
        — but its declared value is included in ``notes`` for visibility.
    """
    exempt = set(config.get("exempt_services") or [])
    cgroup_parent = resolve_cgroup_parent(str(config.get("cgroup_parent") or ""))
    device, device_note = resolve_device(config.get("device", ""))
    read_iops, read_note = derive_read_iops(
        int(config.get("read_iops", 0) or 0),
        configured_path=str(config.get("baseline_path") or ""),
    )
    write_iops = int(config.get("write_iops", 0) or 0)
    io_weight = int(config.get("io_weight", 0) or 0)
    read_bps = int(config.get("read_bps", 0) or 0)
    write_bps = int(config.get("write_bps", 0) or 0)
    mem_min = str(config.get("mem_min") or "")

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
            frag["cgroup_parent"] = cgroup_parent
        if "mem_limit" not in author_keys:
            frag["mem_limit"] = config["mem_limit"]
        # Compose's real key is `memswap_limit` (no underscore) — `docker
        # compose` schema-validates service keys and rejects `mem_swap_limit`
        # outright ("additional properties ... not allowed"), verified live
        # 2026-08-04 against docker 29.7.1. `config["mem_swap_limit"]` on the
        # right-hand side is still the GOVERNANCE_DEFAULTS/config-table key
        # (S15.1's documented TOML name) — only the emitted compose key
        # changes.
        if "memswap_limit" not in author_keys:
            frag["memswap_limit"] = config["mem_swap_limit"]
        if "mem_reservation" not in author_keys:
            frag["mem_reservation"] = config["mem_reservation"]
        blk: dict[str, Any] = {}
        if device:
            blk["device_read_iops"] = [{"path": device, "rate": read_iops}]
            blk["device_write_iops"] = [{"path": device, "rate": write_iops}]
            if read_bps:
                blk["device_read_bps"] = [{"path": device, "rate": read_bps}]
            if write_bps:
                blk["device_write_bps"] = [{"path": device, "rate": write_bps}]
        if io_weight:
            blk["weight"] = io_weight
        if "blkio_config" not in author_keys and blk:
            frag["blkio_config"] = blk
        # S15.11 — KSM opt-in: additive env + bind, injected for every
        # non-exempt service once generate_overlay has validated the configured
        # source as an existing physical file. (environment/volumes are MERGE
        # keys in the overlay, so the S15.3 author-precedence rule for scalar
        # keys does not apply; an author-set LD_PRELOAD would win the per-key
        # env merge anyway.) `_ksm_optin_source` is the already-physical shim
        # path, resolved and validated by generate_overlay.
        ksm_src = str(config.get("_ksm_optin_source") or "")
        if ksm_src:
            frag["environment"] = [f"LD_PRELOAD={KSM_PRELOAD_TARGET}"]
            frag["volumes"] = [{
                "type": "bind",
                "source": ksm_src,
                "target": KSM_PRELOAD_TARGET,
                "read_only": True,
            }]
        if frag:
            injections[svc_name] = frag
            services_touched += 1

    notes = [
        f"cgroup_parent={cgroup_parent}",
        f"mem_limit={config['mem_limit']}",
        f"mem_swap_limit={config['mem_swap_limit']}",
        f"mem_reservation={config['mem_reservation']}",
        f"mem_min={mem_min or '(not declared)'}",
        f"read_iops={read_iops} ({read_note})",
        f"write_iops={write_iops}",
        f"io_weight={io_weight or '(not set)'}",
        f"read_bps={read_bps or '(uncapped)'}",
        f"write_bps={write_bps or '(uncapped)'}",
        f"device={device or '(none — blkio_config skipped)'} ({device_note})",
        f"ksm_optin={config.get('ksm_optin') or 'off'}",
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
        # Provenance marker: readers cannot otherwise tell this unramped 1G/10s
        # measurement apart from a sustained one. See KNOWN_MEASURE_METHODS.
        f"MEASURE_METHOD={MEASURE_METHOD_BURST}\n"
        f"MEASURED_AT={stamp}\n"
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
