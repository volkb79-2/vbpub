#!/usr/bin/env python3
# Interactive wizard for /etc/mdt/host-setup.env — walks host-setup.env.example's
# own section order, showing THIS host's own /proc/meminfo-derived numbers where
# it's relevant (per-tier memory, the memory-min-guaranteed ceiling), and writes
# the result by TEMPLATE SURGERY on host-setup.env.example: only the KEY=value
# line for a key actually walked is replaced, every comment/blank line/section
# header is preserved byte-for-byte. Never generates the file from scratch.
#
# Invoked by install.sh --wizard BEFORE (instead of) its own
# cp host-setup.env.example /etc/mdt/host-setup.env seed step — this script's
# only contract with install.sh is "produce a valid /etc/mdt/host-setup.env,"
# identical in shape to a human hand-edit. Everything downstream (render(),
# RENDER_VARS, unit installation) is untouched and runs exactly as it always
# has, reading whatever this script wrote.
#
# Standalone (no package, argparse-based main() — matches every other script
# in this directory): can also be run directly, outside install.sh, to
# reconfigure an already-installed host (it uses the EXISTING
# /etc/mdt/host-setup.env's own values as prompt defaults where set, on top
# of the example's own defaults).
#
# Reuses, rather than reimplements:
#   - mdt-io-baseline.py's own cache_is_fresh() (imported) for the IO-baseline
#     freshness check, and its own main() (invoked as a subprocess) to
#     actually run the benchmark — never duplicates the 30-day freshness math
#     or the quiet-window warning.
#   - install.sh's own findmnt auto-discovery for IO_DEV_PATH.
#   - the atomic-write pattern from mdt-io-baseline.py's write_cache_atomic
#     (temp file in the same directory, fsync, os.replace) for the produced
#     host-setup.env itself.

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent  # host-setup/scripts
HOST_SETUP_DIR = HERE.parent  # host-setup/
DEFAULT_EXAMPLE = HOST_SETUP_DIR / "host-setup.env.example"
DEFAULT_OUTPUT = Path("/etc/mdt/host-setup.env")
DEFAULT_IO_BASELINE_SCRIPT = HERE / "mdt-io-baseline.py"
DEFAULT_INSTALL_SCRIPT = HOST_SETUP_DIR / "install.sh"
DEFAULT_MEMINFO_PATH = Path("/proc/meminfo")
DEFAULT_SWAPS_PATH = Path("/proc/swaps")

# Relative to host-setup/ — a different repo subproject builds the consumer
# (ciu) side of this same design; this wizard only links to it, never
# re-explains ciu's own design (see plan-host-setup-wizard.md Scope/forbid).
CIU_P50_RELATIVE_PATH = (
    "../../ciu/nyxloom-trove/handoffs/"
    "ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md"
)


# ─── pure functions (parsing/formatting/proposals — no I/O, reviewable and
#     independently testable without root, a real host, or a terminal) ──────


class TemplateError(RuntimeError):
    """The template's shape doesn't match what the wizard expects (a
    KEY=value assignment line missing or duplicated) — a drift guard: fail
    loudly rather than silently writing a corrupt or unsubstituted file."""


def parse_env_file(text: str) -> dict[str, str]:
    """Parse a shell-sourced KEY=value file into {key: value}, stripping one
    layer of matching quotes. Used only to recover PRIOR values (an existing
    /etc/mdt/host-setup.env, or host-setup.env.example's own shipped
    defaults) for prompt defaults — never as the base text for output; see
    apply_value() for the actual template-surgery write path."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


_KEY_LINE_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _key_line_re(key: str) -> re.Pattern[str]:
    pattern = _KEY_LINE_RE_CACHE.get(key)
    if pattern is None:
        pattern = re.compile(rf'^{re.escape(key)}=([\'"]?).*$', re.MULTILINE)
        _KEY_LINE_RE_CACHE[key] = pattern
    return pattern


def apply_value(text: str, key: str, value: str) -> str:
    """Replace ONLY the 'KEY=value' assignment line for `key`, preserving
    that line's original quote style (captured from the template itself, not
    assumed) and leaving every other byte in `text` — every comment, blank
    line, and section header, including any OTHER key's line that merely
    mentions `key` by name in prose — untouched. Anchored per-key (^KEY=...$
    in MULTILINE mode), never a blanket match across the whole file text;
    see plan-host-setup-wizard.md's byte-preservation oracle for why that
    distinction is load-bearing, not stylistic.

    Raises TemplateError if `key` isn't found as an assignment exactly once:
    a template-drift guard (if host-setup.env.example's shape ever changes
    under us, fail loudly rather than silently no-op or double-write).
    """
    pattern = _key_line_re(key)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise TemplateError(
            f"expected exactly one '{key}=...' assignment line in the "
            f"template, found {len(matches)}"
        )
    match = matches[0]
    quote = match.group(1) or ""
    replacement = f"{key}={quote}{value}{quote}"
    return text[: match.start()] + replacement + text[match.end() :]


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo text into {field: value_in_kib}."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            out[key.strip()] = int(parts[0])
        except ValueError:
            continue
    return out


def parse_swaps(text: str) -> int:
    """Sum the 'Size' column (KiB) of /proc/swaps — fallback only, for the
    pathological case where /proc/meminfo itself lacks a SwapTotal field."""
    total = 0
    lines = text.splitlines()[1:]  # header row
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            try:
                total += int(parts[2])
            except ValueError:
                continue
    return total


def read_swap_total_kib(meminfo: dict[str, int], swaps_text: str) -> int:
    if "SwapTotal" in meminfo:
        return meminfo["SwapTotal"]
    return parse_swaps(swaps_text)


def kib_to_size_str(kib: int, min_mib: int = 0) -> str:
    """Round a KiB quantity to a tidy systemd-style size string: '…M' below
    1 GiB (rounded to the nearest 50M, floored at `min_mib`), '…G' at or
    above (rounded to the nearest 0.5G). A pure formatting function — the
    live-sizing oracle calls it directly against fixture numbers."""
    kib = max(0, int(kib))
    if kib == 0:
        return "0"
    mib = kib / 1024
    if mib < 1024:
        rounded = max(min_mib, round(mib / 50) * 50)
        rounded = max(rounded, 50) if rounded else 0
        return f"{int(rounded)}M" if rounded else "0"
    gib = mib / 1024
    rounded_g = round(gib * 2) / 2
    if rounded_g == int(rounded_g):
        return f"{int(rounded_g)}G"
    return f"{rounded_g:g}G"


_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGTPE]?)i?[Bb]?$", re.IGNORECASE)
_SIZE_MULTIPLIER_KIB = {
    "": 1 / 1024,  # bare number = bytes, per systemd's own convention
    "K": 1,
    "M": 1024,
    "G": 1024**2,
    "T": 1024**3,
    "P": 1024**4,
    "E": 1024**5,
}


def parse_size_to_kib(value: str) -> int:
    """Parse a systemd-style size string ('500M', '6G', ...) into KiB.
    Empty or unparseable input -> 0: this is only ever used for an ADVISORY
    sum estimate (step e's 'what's already earmarked' figure), never for
    what actually gets written to the file, so a graceful 0 beats a crash on
    operator-typed free text."""
    value = value.strip()
    if not value:
        return 0
    match = _SIZE_RE.match(value)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = _SIZE_MULTIPLIER_KIB.get(match.group(2).upper(), 1 / 1024)
    return int(number * multiplier)


def propose_memory_tiers(total_kib: int, avail_kib: int, swap_kib: int) -> dict[str, str]:
    """Host-scaled starting proposals for every per-tier memory key the
    wizard walks (Work step 4d). HIGH/LOW/MAX figures scale off
    MemAvailable — what's actually free on THIS host right now, already net
    of anything else (including a co-located production tier) using memory —
    NOT off MemTotal, which would overstate real headroom on a shared host.
    MemoryMin (a small, STABLE protection floor) scales off MemTotal
    instead, deliberately: a protection floor should not shrink just because
    something else is momentarily using more RAM. Swap figures scale off
    SwapTotal. Percentages are round, easily-explained fractions of this
    HOST's own live numbers — never this project's own shipped example
    figures (15.6Gi/~70G swap), which are tied to a different physical
    host."""

    def frac(base_kib: int, percent: int, min_mib: int = 0) -> str:
        return kib_to_size_str(base_kib * percent // 100, min_mib=min_mib)

    # The three MemoryHigh fractions (interactive/background/buildkitd) are
    # deliberately kept BELOW 100% combined (30+30+25=85%, not e.g. 35+35+30)
    # -- step e's leftover-after-step-d suggestion divides MemAvailable minus
    # those three figures, so if they summed to 100% the "leftover" would be
    # degenerate (always ~0, on every host) rather than a real, host-varying
    # number.
    return {
        "DEV_INTERACTIVE_MEMORY_MIN": frac(total_kib, 3, min_mib=128),
        "DEV_INTERACTIVE_MEMORY_LOW": frac(avail_kib, 15),
        "DEV_INTERACTIVE_MEMORY_HIGH": frac(avail_kib, 30),
        "DEV_INTERACTIVE_MEMORY_MAX": frac(avail_kib, 50),
        "DEV_BACKGROUND_MEMORY_HIGH": frac(avail_kib, 30),
        "DEV_BACKGROUND_MEMORY_MAX": frac(avail_kib, 50),
        "DEV_BACKGROUND_MEMORY_SWAP_MAX": frac(swap_kib, 50),
        "DEV_BUILDKITD_MEMORY_HIGH": frac(avail_kib, 25),
        "DEV_BUILDKITD_MEMORY_MAX": frac(avail_kib, 45),
        "DEV_BUILDKITD_MEMORY_SWAP_MAX": frac(swap_kib, 20),
    }


def propose_memory_min_guaranteed_suggestion(
    avail_kib: int, tier_high_values: dict[str, str]
) -> tuple[int, int, str]:
    """Pure function backing Work step 4e's 'suggestion grounded in THIS
    host's own numbers'. Returns (leftover_kib, suggestion_kib, formula_text).

    Sums step d's three MemoryHigh figures (the realistic 'expected
    concurrent' load — MemoryMax already assumes swap is absorbing overflow,
    so summing Max figures would understate what's actually free day to
    day), subtracts that from MemAvailable, and proposes a SMALL, explicitly
    conservative 5% of whatever's left over. This is advisory text only —
    see step_memory_min_guaranteed(): the prompt's actual DEFAULT stays
    whatever's already configured or empty, never this suggestion, so
    leaving the ceiling unset is never the awkward path.
    """
    earmarked_kib = sum(parse_size_to_kib(v) for v in tier_high_values.values())
    leftover_kib = max(0, avail_kib - earmarked_kib)
    suggestion_kib = leftover_kib * 5 // 100
    formula = (
        f"MemAvailable ({kib_to_size_str(avail_kib)}) - step d's three MemoryHigh "
        f"figures ({kib_to_size_str(earmarked_kib)} combined) = "
        f"{kib_to_size_str(leftover_kib)} left over; 5% of that leftover = "
        f"{kib_to_size_str(suggestion_kib)}"
    )
    return leftover_kib, suggestion_kib, formula


def discover_io_dev_path_from_findmnt(runner=subprocess.run) -> str:
    """The SAME auto-discovery install.sh already does at render time:
    findmnt against /var/lib/docker, falling back to /. Injectable `runner`
    only so a test/oracle harness can substitute a fake without touching
    this host's real mounts; production code always uses subprocess.run."""
    for target in ("/var/lib/docker", "/"):
        try:
            result = runner(
                ["findmnt", "-no", "SOURCE", "--target", target],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return ""
        source = (result.stdout or "").strip()
        if result.returncode == 0 and source:
            return source
    return ""


def resolve_default(
    key: str,
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
    proposal: str | None,
) -> str:
    """The one default-resolution rule every walked key goes through
    (Work step 4's intro): an already-configured host's own value wins if
    present; otherwise a host-scaled proposal (steps d — never the shipped
    example's own hardcoded figures); otherwise the example's own shipped
    default (every other section, and step e's ceiling, which must stay
    empty by default — see step_memory_min_guaranteed())."""
    if key in cfg_current:
        return cfg_current[key]
    if proposal is not None:
        return proposal
    return example_defaults.get(key, "")


# ─── I/O helpers ─────────────────────────────────────────────────────────


def ask(label: str, default: str) -> str:
    """Prompt for one value; Enter accepts `default` unchanged. A closed or
    exhausted stdin (canned-answer runs, Ctrl-D) also just accepts the
    default rather than crashing — the same outcome as a bare Enter."""
    shown = default if default else "<empty>"
    try:
        raw = input(f"{label} [{shown}]: ")
    except EOFError:
        print()
        raw = ""
    raw = raw.strip()
    return raw if raw else default


def ask_yn(question: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"{question} [{hint}]: ").strip().lower()
    except EOFError:
        print()
        raw = ""
    if not raw:
        return default
    return raw in ("y", "yes")


def walk_key(
    label: str,
    key: str,
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
    proposal: str | None = None,
) -> str:
    default = resolve_default(key, cfg_current, example_defaults, proposal)
    return ask(f"{label} ({key})", default)


def write_atomic(path: Path, content: str) -> None:
    """Same pattern as mdt-io-baseline.py's write_cache_atomic: a temp file
    in the SAME directory (so os.replace is an atomic same-filesystem
    rename, never a cross-filesystem copy), fsync before replace, so an
    interrupted run never leaves a truncated/partial host-setup.env that
    install.sh's `. /etc/mdt/host-setup.env` would then source."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _load_io_baseline_module(script_path: Path) -> ModuleType | None:
    """Import mdt-io-baseline.py (a hyphenated filename, so a plain `import`
    can't name it) as a module, to reuse its own cache_is_fresh() rather
    than reimplementing the 30-day freshness math inline. Safe to exec: the
    module only defines functions/constants at import time, its own
    benchmark run is gated behind `if __name__ == "__main__":`."""
    if not script_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("mdt_io_baseline", script_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def check_baseline_freshness(baseline_env_path: Path, io_baseline_script: Path) -> str:
    """Returns 'fresh', 'stale', or 'missing'. NEVER runs the benchmark
    itself. Primary path: import mdt-io-baseline.py and call its own
    cache_is_fresh(). Fallback (import genuinely failed): report 'stale' so
    the wizard offers to run it — the subsequent subprocess invocation
    (step_io_baseline, without --force) is itself cheap and safe even if the
    cache turns out to already be fresh: mdt-io-baseline.py's own main()
    checks freshness again before touching the disk, and just prints the
    cached values back out if so."""
    if not baseline_env_path.exists():
        return "missing"
    module = _load_io_baseline_module(io_baseline_script)
    if module is not None and hasattr(module, "cache_is_fresh"):
        try:
            return "fresh" if module.cache_is_fresh(baseline_env_path) else "stale"
        except OSError:
            return "stale"
    return "stale"


# ─── interactive sections (Work step 4a-4g) ────────────────────────────────


def step_io_device(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> str:
    print("\n-- a. IO device (IO_DEV_PATH) --")
    print(
        "The block device install.sh sizes the static IOPS/bandwidth fallback\n"
        "caps against (dev.slice's IOReadBandwidthMax etc. — the boot-window\n"
        "fallback in force until the IO baseline below has actually run).\n"
        "Auto-discovered the SAME way install.sh does at render time: findmnt\n"
        "against /var/lib/docker, falling back to /."
    )
    discovered = discover_io_dev_path_from_findmnt()
    if discovered:
        print(f"auto-discovered: {discovered}")
    else:
        print(
            "auto-discovery found nothing (findmnt failed or returned no "
            "source) -- static IO caps would be omitted at this value."
        )
    return walk_key(
        "Block device node backing docker's data dir",
        "IO_DEV_PATH",
        cfg_current,
        example_defaults,
        proposal=discovered or None,
    )


def step_io_baseline(baseline_env_path: Path, io_baseline_script: Path) -> None:
    print("\n-- b. IO baseline --")
    print(f"cache: {baseline_env_path}")
    status = check_baseline_freshness(baseline_env_path, io_baseline_script)
    if status == "fresh":
        print("fresh (< 30 days old) -- leaving it as-is.")
        return
    reason = "no cache found yet" if status == "missing" else "cache is stale (>= 30 days old)"
    print(
        f"{reason}. Until this is measured, mdt-apply-dev-caps.sh falls back to\n"
        "the deliberately tight DEV_STATIC_* caps in host-setup.env."
    )
    if not io_baseline_script.exists():
        print(f"WARN: {io_baseline_script} not found -- skipping (run it manually once it's installed)")
        return
    run_now = ask_yn(
        "Run the IO baseline benchmark now? It takes ~4 minutes and "
        "SATURATES THE DISK -- only in a quiet window (the script itself "
        "also warns and gives you 5s to Ctrl-C if containers are running)",
        default=False,
    )
    if not run_now:
        print(f"Skipping -- run it later with: sudo {io_baseline_script}")
        return
    # Letting mdt-io-baseline.py own the quiet-window warning AND the 30-day
    # freshness check (no --force): do not duplicate either here.
    result = subprocess.run([sys.executable, str(io_baseline_script)], check=False)
    if result.returncode != 0:
        print(f"WARN: IO baseline run exited {result.returncode} -- statics remain in force until it succeeds")


def step_io_cap_pct(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> tuple[str, str]:
    print("\n-- c. IO cap percentages --")
    print(
        "Both are percentages of the MEASURED device ceilings (io-baseline.env)\n"
        "and both belong in a 60-80% band: never 100% (a saturated device\n"
        "queues everything behind the burst -- the exact stall this tiering\n"
        "exists to prevent), and below ~60% you're just throttling ordinary\n"
        "work for no protective gain.\n"
        "  DEV_IO_CAP_PCT   -- whole-estate ceiling on dev.slice (bounds every\n"
        "                      dev container together; protects PRODUCTION\n"
        "                      from the tier, not tier members from each other).\n"
        "  SWEEP_IO_CAP_PCT -- per-container ceiling (protects tier members\n"
        "                      from EACH OTHER; it's buildkit workers' ONLY\n"
        "                      governance, since Buildx placement under\n"
        "                      dev.slice doesn't work at all)."
    )
    dev_pct = walk_key("Whole-estate IO cap %", "DEV_IO_CAP_PCT", cfg_current, example_defaults)
    sweep_pct = walk_key("Per-container IO cap %", "SWEEP_IO_CAP_PCT", cfg_current, example_defaults)
    return dev_pct, sweep_pct


def step_memory_tiers(
    meminfo: dict[str, int],
    swap_kib: int,
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> dict[str, str]:
    total_kib = meminfo.get("MemTotal", 0)
    avail_kib = meminfo.get("MemAvailable", total_kib)
    total_gib = total_kib / (1024 * 1024)
    avail_gib = avail_kib / (1024 * 1024)
    swap_gib = swap_kib / (1024 * 1024)

    print("\n-- d. Per-tier memory --")
    print(
        f"This host: MemTotal={total_gib:.1f}GiB, MemAvailable={avail_gib:.1f}GiB "
        f"right now, SwapTotal={swap_gib:.1f}GiB."
    )
    print(
        "Proposed splits below scale off MemAvailable (what's actually free\n"
        "on THIS host right now, already net of anything else -- including a\n"
        "co-located production tier -- using memory), NOT off MemTotal, which\n"
        "would overstate real headroom on a shared host. MemoryHigh is a soft\n"
        "throttle and MemoryMax relies on this host's own swap to absorb\n"
        "overflow (see README's tiering model), so these proposals deliberately\n"
        "overlap across tiers rather than partitioning 100% of RAM up front --\n"
        "override any of them if you know better for this host."
    )
    if swap_kib == 0:
        print("This host reports NO SWAP -- swap-ceiling proposals below are 0 accordingly.")

    proposals = propose_memory_tiers(total_kib, avail_kib, swap_kib)
    values: dict[str, str] = {}

    print(
        "\n  dev-interactive.slice (devcontainers/IDE) -- MemoryMin is a small,\n"
        "  STABLE % of MemTotal (a protection floor shouldn't shrink just\n"
        "  because something else is using more RAM right now); Low/High/Max\n"
        "  scale off MemAvailable as above."
    )
    for key, label in (
        ("DEV_INTERACTIVE_MEMORY_MIN", "  MemoryMin"),
        ("DEV_INTERACTIVE_MEMORY_LOW", "  MemoryLow"),
        ("DEV_INTERACTIVE_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_INTERACTIVE_MEMORY_MAX", "  MemoryMax"),
    ):
        values[key] = walk_key(label, key, cfg_current, example_defaults, proposals[key])

    print(
        "\n  dev-background.slice (test/build/gate containers) -- relaxed swap:\n"
        "  a build that swaps just finishes slowly, one that OOMs fails\n"
        f"  outright. Sized against THIS host's own {swap_gib:.1f}GiB swap, not a\n"
        "  fixed number."
    )
    for key, label in (
        ("DEV_BACKGROUND_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_BACKGROUND_MEMORY_MAX", "  MemoryMax"),
        ("DEV_BACKGROUND_MEMORY_SWAP_MAX", "  MemorySwapMax"),
    ):
        values[key] = walk_key(label, key, cfg_current, example_defaults, proposals[key])

    print(
        "\n  dev-buildkitd.slice (shared BuildKit worker) -- must cover\n"
        "  CONCURRENT multi-project builds, not just one build at a time."
    )
    for key, label in (
        ("DEV_BUILDKITD_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_BUILDKITD_MEMORY_MAX", "  MemoryMax"),
        ("DEV_BUILDKITD_MEMORY_SWAP_MAX", "  MemorySwapMax"),
    ):
        values[key] = walk_key(label, key, cfg_current, example_defaults, proposals[key])

    return values


def step_memory_min_guaranteed(
    avail_kib: int,
    tier_high_values: dict[str, str],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> str:
    print("\n-- e. Memory-min-guaranteed ceiling (DEV_MEMORY_MIN_GUARANTEED_CEILING) --")
    print(
        "What it is: a HARD floor (unlike MemoryLow's soft/best-effort\n"
        "protection) -- memory this cgroup keeps even under host-wide\n"
        "pressure, protected from reclaim."
    )
    print(
        "\nWhere it's set, and why TWO places: dev.slice (root) AND\n"
        "dev-memory_min_guaranteed.slice (a SIBLING of the interactive/\n"
        "background tiers you just configured -- NOT nested under either),\n"
        "pinned to the EXACT SAME value on purpose. cgroup v2 hands a parent's\n"
        "UNCLAIMED protection down to whichever child is using memory,\n"
        "proportionally -- so a generous number on dev.slice alone leaks\n"
        "protection to the interactive/background tiers, defeating the point\n"
        "of a dedicated guaranteed tier. Give dev.slice less than this ceiling\n"
        "instead and the leaf's own MemoryMin is silently inert. Both slices\n"
        "need EXACTLY this one number, never two independently-chosen ones."
    )
    print(
        "\nThe effect / who actually gets protected: this is opt-in PER\n"
        "CONTAINER -- a stack must explicitly place itself on\n"
        "dev-memory_min_guaranteed.slice (governance.cgroup_parent in its ciu\n"
        "config) and declare its own claim (governance.mem_min). Setting this\n"
        "ceiling alone protects NOTHING by itself; it only raises the total a\n"
        "stack COULD claim. (ciu's own admission control enforcing this\n"
        "ceiling against live claims is a separate, in-flight piece of work --\n"
        f"see {CIU_P50_RELATIVE_PATH})"
    )

    leftover_kib, suggestion_kib, formula = propose_memory_min_guaranteed_suggestion(
        avail_kib, tier_high_values
    )
    print(f"\nThis host: {formula}.")
    if suggestion_kib > 0:
        print(
            f"A conservative suggestion, IF you want a nonzero ceiling at all: "
            f"{kib_to_size_str(suggestion_kib)}\n"
            "(meant to stay small -- see CGROUP-NOTES.md 'Per-container "
            "memory.min guarantees' for why a generous number here is\n"
            "actively harmful, not just wasteful.)"
        )
    else:
        print(
            "This host has little to no headroom left after step d's tiers --\n"
            "leaving this UNSET is the reasonable choice here."
        )
    print(
        "\nLeaving this EMPTY (just press Enter) keeps the whole mechanism\n"
        "fully inert, matching host-setup.env.example's own shipped default --\n"
        "that stays the default answer below, not the suggestion above."
    )
    # proposal=None, deliberately: the prompt default is whatever's already
    # configured (or empty) -- NEVER the suggestion above. See
    # resolve_default(): leaving this unset must be at least as easy as
    # typing a number, so Enter here always means "leave it empty" on a
    # fresh host, never "accept a computed number."
    return walk_key(
        "Memory-min-guaranteed ceiling (empty = leave the mechanism off)",
        "DEV_MEMORY_MIN_GUARANTEED_CEILING",
        cfg_current,
        example_defaults,
        proposal=None,
    )


def step_buildkitd(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> tuple[str, str]:
    print("\n-- f. buildkitd --")
    image = walk_key("BuildKit worker image", "DEV_BUILDKITD_IMAGE", cfg_current, example_defaults)
    print(
        "CPUQuota uses a DIFFERENT empty-means convention from every other key\n"
        "in this file: empty here does NOT mean 'not applied' -- it means\n"
        "'auto-detect (nproc - 2) cores at install time,' floored at 1 core.\n"
        "An explicit percentage (e.g. '400%' = 4 cores) always overrides the\n"
        "auto-detected value. Don't assume empty = uncapped here."
    )
    cpu_quota = walk_key(
        "CPUQuota (empty = auto-detect nproc-2 cores)",
        "DEV_BUILDKITD_CPU_QUOTA",
        cfg_current,
        example_defaults,
    )
    return image, cpu_quota


def step_docker_daemon(
    cfg_current: dict[str, str], example_defaults: dict[str, str]
) -> tuple[str, str, str]:
    print("\n-- g. Docker daemon.json keys this tool owns --")
    print(
        "DOCKER_DAEMON_CGROUP_PARENT is the daemon-wide default placement\n"
        "(D-G7) for any container that names no --cgroup-parent of its own.\n"
        "install.sh MERGES this single key into /etc/docker/daemon.json; it\n"
        "never overwrites the file, and needs a dockerd RESTART (not reload)\n"
        "to take effect."
    )
    cgroup_parent = walk_key(
        "Default cgroup-parent", "DOCKER_DAEMON_CGROUP_PARENT", cfg_current, example_defaults
    )
    print(
        "\nThe docker-.scope.d backstop (D-G8) is a 'never truly unbounded'\n"
        "floor for EVERY container's transient scope, regardless of which\n"
        "slice (or none) it named -- size it GENEROUSLY, above any legitimate\n"
        "single container's real ceiling on this host; this is a fail-open\n"
        "backstop, not a tier limit."
    )
    backstop_max = walk_key(
        "Backstop MemoryMax", "DOCKER_SCOPE_BACKSTOP_MEMORY_MAX", cfg_current, example_defaults
    )
    backstop_swap = walk_key(
        "Backstop MemorySwapMax",
        "DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX",
        cfg_current,
        example_defaults,
    )
    return cgroup_parent, backstop_max, backstop_swap


# ─── orchestration ─────────────────────────────────────────────────────────


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mdt-host-setup-wizard.py",
        description=(
            "Interactive wizard that walks host-setup.env.example's own "
            "section order and writes a host-sized /etc/mdt/host-setup.env "
            "by template surgery (never generated from scratch)."
        ),
    )
    parser.add_argument("--example", type=Path, default=DEFAULT_EXAMPLE, help="template to read from")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="file to write")
    parser.add_argument(
        "--io-baseline-script", type=Path, default=DEFAULT_IO_BASELINE_SCRIPT, help="path to mdt-io-baseline.py"
    )
    parser.add_argument(
        "--install-script", type=Path, default=DEFAULT_INSTALL_SCRIPT, help="path to install.sh"
    )
    parser.add_argument(
        "--meminfo-path", type=Path, default=DEFAULT_MEMINFO_PATH, help="override for testing/oracles"
    )
    parser.add_argument(
        "--swaps-path", type=Path, default=DEFAULT_SWAPS_PATH, help="override for testing/oracles"
    )
    parser.add_argument(
        "--skip-run-offer",
        action="store_true",
        help=(
            "don't offer to run install.sh at the end -- used when install.sh "
            "itself invoked this script (--wizard) and will already fall "
            "through into render/apply on its own"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_cli_args(argv)

    if not args.example.exists():
        print(f"ERROR: template not found: {args.example}", file=sys.stderr)
        return 1
    example_text = args.example.read_text()
    example_defaults = parse_env_file(example_text)

    cfg_current: dict[str, str] = {}
    if args.output.exists():
        try:
            cfg_current = parse_env_file(args.output.read_text())
            print(f"Found an existing {args.output} -- using its values as defaults where set.")
        except OSError as exc:
            print(f"WARN: could not read existing {args.output}: {exc}")

    try:
        meminfo = parse_meminfo(args.meminfo_path.read_text())
    except OSError as exc:
        print(f"WARN: could not read {args.meminfo_path}: {exc} -- memory proposals will be 0-based, override them")
        meminfo = {}
    try:
        swaps_text = args.swaps_path.read_text()
    except OSError:
        swaps_text = ""
    swap_kib = read_swap_total_kib(meminfo, swaps_text)

    print("== mdt host-setup wizard ==")
    print(f"template: {args.example}")
    print(f"writing:  {args.output}")
    print("Press Enter at any prompt to accept the shown default.")

    walked: dict[str, str] = {}

    walked["IO_DEV_PATH"] = step_io_device(cfg_current, example_defaults)

    baseline_env_path = Path(
        cfg_current.get("IO_BASELINE_ENV")
        or example_defaults.get("IO_BASELINE_ENV")
        or "/var/lib/mdt/io-baseline.env"
    )
    step_io_baseline(baseline_env_path, args.io_baseline_script)

    dev_pct, sweep_pct = step_io_cap_pct(cfg_current, example_defaults)
    walked["DEV_IO_CAP_PCT"] = dev_pct
    walked["SWEEP_IO_CAP_PCT"] = sweep_pct

    tier_values = step_memory_tiers(meminfo, swap_kib, cfg_current, example_defaults)
    walked.update(tier_values)

    high_values = {k: v for k, v in tier_values.items() if k.endswith("_MEMORY_HIGH")}
    avail_kib = meminfo.get("MemAvailable", meminfo.get("MemTotal", 0))
    walked["DEV_MEMORY_MIN_GUARANTEED_CEILING"] = step_memory_min_guaranteed(
        avail_kib, high_values, cfg_current, example_defaults
    )

    image, cpu_quota = step_buildkitd(cfg_current, example_defaults)
    walked["DEV_BUILDKITD_IMAGE"] = image
    walked["DEV_BUILDKITD_CPU_QUOTA"] = cpu_quota

    cgroup_parent, backstop_max, backstop_swap = step_docker_daemon(cfg_current, example_defaults)
    walked["DOCKER_DAEMON_CGROUP_PARENT"] = cgroup_parent
    walked["DOCKER_SCOPE_BACKSTOP_MEMORY_MAX"] = backstop_max
    walked["DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX"] = backstop_swap

    text = example_text
    try:
        for key, value in walked.items():
            text = apply_value(text, key, value)
    except TemplateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    write_atomic(args.output, text)
    print(f"\nwrote {args.output}")

    if args.skip_run_offer:
        return 0

    if ask_yn("Run install.sh now to render + apply this config?", default=False):
        cmd = [str(args.install_script)]
        if ask_yn("Also pass --with-baseline (re-run the IO benchmark during install)?", default=False):
            cmd.append("--with-baseline")
        print(f"running: {' '.join(cmd)}")
        subprocess.run(cmd, check=False)
    else:
        print(f"Next step: sudo {args.install_script}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
