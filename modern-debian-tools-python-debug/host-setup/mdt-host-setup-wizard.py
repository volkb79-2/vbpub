#!/usr/bin/env python3
# Interactive wizard for /etc/mdt/host-setup.env — walks host-setup.env.example's
# own section order, showing THIS host's own /proc/meminfo-derived numbers where
# it's relevant (per-tier memory, the memory-min-guaranteed ceiling), and writes
# the result by TEMPLATE SURGERY on host-setup.env.example: only the KEY=value
# line for a key actually walked is replaced, every comment/blank line/section
# header is preserved byte-for-byte. Never generates the file from scratch.
#
# Lives at host-setup/ (a sibling of install.sh), NOT host-setup/scripts/ —
# scripts/ holds the things install.sh actually INSTALLS onto the host
# (/usr/local/sbin/…); this wizard is never installed, it is only ever run out
# of a checkout, exactly like install.sh itself. Moved there in round 2; every
# path constant below is resolved against that location.
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
#   - this file's own _SIZE_RE (the regex parse_size_to_kib already parses
#     with) for the size-string input validators — never a second parser.

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Callable

HERE = Path(__file__).resolve().parent  # host-setup/ (a sibling of install.sh)
# Kept as a named constant purely for readability at the use sites below: since
# round 2's move this script IS in host-setup/, so it is HERE, not HERE.parent
# (which would resolve one level too far up, to the subproject root).
HOST_SETUP_DIR = HERE
DEFAULT_EXAMPLE = HOST_SETUP_DIR / "host-setup.env.example"
DEFAULT_OUTPUT = Path("/etc/mdt/host-setup.env")
# mdt-io-baseline.py did NOT move — it is installed onto the host, so it stays
# in scripts/ with the rest of the installed payload.
DEFAULT_IO_BASELINE_SCRIPT = HOST_SETUP_DIR / "scripts" / "mdt-io-baseline.py"
DEFAULT_INSTALL_SCRIPT = HOST_SETUP_DIR / "install.sh"
DEFAULT_MEMINFO_PATH = Path("/proc/meminfo")
DEFAULT_SWAPS_PATH = Path("/proc/swaps")

# Relative to host-setup/ — a different repo subproject builds the consumer
# (ciu) side of this same design; this wizard only links to it, never
# re-explains ciu's own design (see plan-host-setup-wizard.md Scope/forbid).
# Unchanged by round 2's move: this was always written relative to
# host-setup/, and host-setup/ is now exactly where this script lives.
CIU_P50_RELATIVE_PATH = (
    "../../ciu/nyxloom-trove/handoffs/"
    "ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md"
)


# ─── output layer (terminal-width-aware reflow + conditional highlighting) ──
#
# Every human-facing line in this file goes through out() rather than a bare
# print(): the prose is written as one logical paragraph per line and reflowed
# at run time against the REAL terminal width, so a 100-column terminal stops
# getting text hard-wrapped at 76 columns and an 80-column one still fits.

MAX_WIDTH = 120  # cap: fully-justified prose past ~120 columns is hard to scan

_HIGHLIGHT = "\033[1;36m"  # bold cyan — keys, paths, commands, unit names
_RESET = "\033[0m"
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# A char that cannot appear in this file's own prose, used to keep a
# multi-word `backtick span` from being split across a wrap boundary (which
# would leave an unmatched backtick on each of the two lines).
_NOBREAK = "\x00"


def term_width() -> int:
    """The width to reflow at: the real terminal's, capped at MAX_WIDTH.
    shutil.get_terminal_size() already handles "not a real terminal" itself
    (it consults $COLUMNS first, then falls back to the given default), so
    there is nothing to hand-roll here."""
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns, MAX_WIDTH)


def color_enabled(stream=None) -> bool:
    """ANSI is emitted ONLY into a real interactive terminal. Same three-part
    test ciu/cmru's own output.py already use in this repo (isatty + NO_COLOR
    + TERM=dumb), so the estate has one convention, not two. This is what
    keeps a piped/redirected run — including every manual verification
    transcript in this package's REPORT — free of raw escape bytes."""
    stream = sys.stdout if stream is None else stream
    return (
        bool(getattr(stream, "isatty", lambda: False)())
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM", "").lower() != "dumb"
    )


def _render(line: str) -> str:
    """Turn `backtick spans` into highlighted text when colour is on, and
    leave the backticks exactly as written when it is off — the same Markdown
    convention every comment and doc in this repo already uses, so the
    plain-text rendering reads correctly on its own."""
    if not color_enabled():
        return line
    return _BACKTICK_RE.sub(lambda m: f"{_HIGHLIGHT}{m.group(1)}{_RESET}", line)


def out(text: str = "", *, indent: str = "", hang: str | None = None) -> None:
    """Print `text` reflowed to the current terminal width.

    Each line of `text` is treated as one logical paragraph and wrapped
    independently; an empty line prints as a blank line, so paragraph breaks
    and bullet lists survive. `indent` prefixes the first line of each
    paragraph, `hang` (default: same as `indent`) its continuations — that
    pair is what renders the bullet lists below with their text aligned.

    Long words are never broken and hyphens are never split on: this prose is
    full of `/dev/mapper/...` paths, `dev-interactive.slice` unit names and
    `--with-baseline`-style flags, none of which may be cut in half.
    """
    if not text:
        print()
        return
    width = max(20, term_width())
    subsequent = indent if hang is None else hang
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            print()
            continue
        protected = _BACKTICK_RE.sub(
            lambda m: "`" + m.group(1).replace(" ", _NOBREAK) + "`", paragraph.strip()
        )
        wrapped = textwrap.wrap(
            protected,
            width=width,
            initial_indent=indent,
            subsequent_indent=subsequent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        for line in wrapped or [indent]:
            print(_render(line.replace(_NOBREAK, " ")))


def _prompt(text: str) -> str:
    """input() with two deliberate properties.

    (1) A prompt longer than the terminal is split: the question is printed
    (reflowed) on its own line(s) first, leaving only the short
    "[default]: " tail as the actual input prompt, so the editable area is
    never wrapped mid-line by the terminal.

    (2) The input prompt itself carries NO ANSI, ever. readline counts escape
    bytes as printable columns when it redraws the line during editing, so a
    coloured input() prompt corrupts cursor arithmetic the moment the operator
    presses Home or an arrow key. Colour belongs on the explanatory text above
    (out()); prompt labels in this file therefore contain no backtick spans.
    """
    if len(text) <= term_width():
        return input(text)
    lead, sep, tail = text.rpartition("[")
    if not sep:
        out(text)
        return input("> ")
    out(lead.strip())
    return input(f"[{tail}")


# ─── pure functions (parsing/formatting/proposals — no I/O, reviewable and
#     independently testable without root, a real host, or a terminal) ──────


class TemplateError(RuntimeError):
    """The template's shape doesn't match what the wizard expects (a
    KEY=value assignment line missing or duplicated) — a drift guard: fail
    loudly rather than silently writing a corrupt or unsubstituted file."""


def parse_env_file(text: str, *, strict: bool = False) -> dict[str, str]:
    """Parse a shell-sourced KEY=value file into {key: value}, stripping one
    layer of matching quotes. Used only to recover PRIOR values (an existing
    /etc/mdt/host-setup.env, or host-setup.env.example's own shipped
    defaults) for prompt defaults — never as the base text for output; see
    apply_value() for the actual template-surgery write path.

    Note that a key with an EMPTY value is stored the same as any other:
    `IO_DEV_PATH=""` yields `{"IO_DEV_PATH": ""}`, key present. See
    resolve_default(), which has to distinguish "present" from "actually
    configured" precisely because of this."""
    out_map: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            if strict:
                raise TemplateError(
                    f"invalid host-setup.env line {line_number}: {line!r}"
                )
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if strict and not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise TemplateError(
                f"invalid host-setup.env key on line {line_number}: {key!r}"
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if strict and key in out_map:
            raise TemplateError(f"duplicate {key} in host-setup.env")
        out_map[key] = value
    return out_map


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
    out_map: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            out_map[key.strip()] = int(parts[0])
        except ValueError:
            continue
    return out_map


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
    1 GiB ('…G' at or above, rounded to the nearest 0.5G), floored at
    `min_mib`. A pure formatting function — the live-sizing oracle calls it
    directly against fixture numbers.

    Below-1GiB rounding granularity is 50M, EXCEPT below 50M itself, where
    it drops to 5M: at 50M granularity a genuinely nonzero small value (the
    memory-min-guaranteed suggestion routinely proposes tens-of-MB numbers,
    see CGROUP-NOTES.md — "keep it small") rounds down to a
    self-contradictory "0" (adversarial review, mdt-host-setup-wizard).
    Any nonzero `kib` is guaranteed to format as a nonzero string — the
    result is floored at the active granularity rather than allowed to
    round down to nothing."""
    kib = max(0, int(kib))
    if kib == 0:
        return "0"
    mib = kib / 1024
    if mib < 1024:
        granularity = 5 if mib < 50 else 50
        rounded = max(min_mib, round(mib / granularity) * granularity)
        if rounded <= 0:
            rounded = granularity
        return f"{int(rounded)}M"
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


def is_size_string(value: str) -> bool:
    """Syntax check for the input validators, sharing parse_size_to_kib's own
    _SIZE_RE rather than introducing a second parser.

    It cannot be `parse_size_to_kib(value) > 0`: that function answers a
    DIFFERENT question (how many KiB is this worth, 0 for anything it can't
    use), which conflates the perfectly valid answer "0" — what the memory
    proposals themselves produce for every swap key on a host with no swap —
    with garbage. Syntax and magnitude are separate questions; this is the
    syntax one."""
    return bool(_SIZE_RE.match(value.strip()))


def propose_memory_tiers(total_kib: int, avail_kib: int) -> dict[str, str]:
    """Host-scaled starting proposals for every per-tier memory key the
    wizard walks (Work step 4d). HIGH/LOW/MAX figures scale off
    MemAvailable — what's actually free on THIS host right now, already net
    of anything else (including a co-located production tier) using memory —
    NOT off MemTotal, which would overstate real headroom on a shared host.
    MemoryMin is not invented for ordinary slices: the only MemoryMin in this
    design is the explicit, opt-in shared guarantee prompted separately.
    Percentages are round, easily-explained fractions of this HOST's own live
    numbers — never this project's shipped example figures. Swap-max proposals
    are NOT part of this dict — the slice-first flow asks those per slice."""

    def frac(base_kib: int, percent: int, min_mib: int = 0) -> str:
        return kib_to_size_str(base_kib * percent // 100, min_mib=min_mib)

    return {
        "DEV_MEMORY_HIGH": kib_to_size_str(avail_kib),
        "DEV_MEMORY_MAX": kib_to_size_str(max(total_kib, avail_kib)),
        "DEV_INTERACTIVE_MEMORY_LOW": frac(avail_kib, 15),
        "DEV_INTERACTIVE_MEMORY_HIGH": frac(avail_kib, 30),
        "DEV_INTERACTIVE_MEMORY_MAX": frac(avail_kib, 50),
        "DEV_BACKGROUND_MEMORY_HIGH": frac(avail_kib, 30),
        "DEV_BACKGROUND_MEMORY_MAX": frac(avail_kib, 50),
        "DEV_GATES_MEMORY_HIGH": frac(avail_kib, 10),
        "DEV_GATES_MEMORY_MAX": frac(avail_kib, 20),
        "DEV_BUILDKITD_MEMORY_HIGH": frac(avail_kib, 25),
        "DEV_BUILDKITD_MEMORY_MAX": frac(avail_kib, 45),
    }


def validate_positive_size(value: str) -> str | None:
    """A configured memory limit must be positive; ``0`` is a hard zero,
    not an unset value. Unconfigured fields are represented explicitly by the
    slice flow and are never passed to this validator."""
    error = validate_size_required(value)
    if error:
        return error
    if parse_size_to_kib(value) <= 0:
        return "a memory limit must be greater than zero; use the documented 'none' entry when this slice has no such directive"
    return None


def validate_optional_positive_size(value: str) -> str | None:
    if not value:
        return None
    return validate_positive_size(value)


def validate_weight(value: str) -> str | None:
    if not value:
        return "an IOWeight/CPUWeight is required (systemd accepts an integer from 1 to 10000)"
    try:
        weight = int(value)
    except ValueError:
        return f"{value!r} is not an integer weight"
    if not 1 <= weight <= 10000:
        return f"{weight} is outside systemd's 1-10000 weight range"
    return None


def validate_guard_policy(value: str) -> str | None:
    if value not in ("terminate", "report-only"):
        return "choose 'terminate' (remove unapproved Buildx workers) or 'report-only' (detect and log only)"
    return None


def memory_relationship_errors(values: dict[str, str]) -> list[tuple[str, str]]:
    """Return (key, message) pairs for cgroup memory hierarchy violations.

    Values are normalized to KiB before comparison. Empty entries are the
    explicit architectural ``none`` state and are not compared; configured
    fields are ordered exactly as the cgroup v2 hierarchy requires.
    """
    groups = (
        ("dev.slice", ("DEV_MEMORY_MIN_GUARANTEED_CEILING", "DEV_MEMORY_LOW", "DEV_MEMORY_HIGH", "DEV_MEMORY_MAX")),
        ("dev-interactive.slice", ("DEV_INTERACTIVE_MEMORY_MIN", "DEV_INTERACTIVE_MEMORY_LOW", "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_INTERACTIVE_MEMORY_MAX")),
        ("dev-background.slice", ("DEV_BACKGROUND_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_LOW", "DEV_BACKGROUND_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_MAX")),
        ("dev-gates.slice", ("DEV_GATES_MEMORY_MIN", "DEV_GATES_MEMORY_LOW", "DEV_GATES_MEMORY_HIGH", "DEV_GATES_MEMORY_MAX")),
        ("dev-buildkitd.slice", ("DEV_BUILDKITD_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_LOW", "DEV_BUILDKITD_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_MAX")),
        ("dev-memory_min_guaranteed.slice", ("DEV_MEMORY_MIN_GUARANTEED_CEILING", "DEV_MEMORY_MIN_GUARANTEED_LOW", "DEV_MEMORY_MIN_GUARANTEED_HIGH", "DEV_MEMORY_MIN_GUARANTEED_MAX")),
    )
    errors: list[tuple[str, str]] = []
    for slice_name, keys in groups:
        parsed = [(key, parse_size_to_kib(values.get(key, ""))) for key in keys if values.get(key, "")]
        for (left_key, left), (right_key, right) in zip(parsed, parsed[1:]):
            if left > right:
                errors.append(
                    (
                        right_key,
                        f"{slice_name}: {left_key}={values[left_key]} must be <= {right_key}={values[right_key]} (comparison is in KiB; re-enter the right-hand field)",
                    )
                )
    return errors


MEMORY_CHILD_SPECS = (
    ("dev-interactive.slice", (
        "DEV_INTERACTIVE_MEMORY_MIN", "DEV_INTERACTIVE_MEMORY_LOW",
        "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_INTERACTIVE_MEMORY_MAX",
    )),
    ("dev-background.slice", (
        "DEV_BACKGROUND_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_LOW",
        "DEV_BACKGROUND_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_MAX",
    )),
    ("dev-gates.slice", (
        "DEV_GATES_MEMORY_MIN", "DEV_GATES_MEMORY_LOW",
        "DEV_GATES_MEMORY_HIGH", "DEV_GATES_MEMORY_MAX",
    )),
    ("dev-buildkitd.slice", (
        "DEV_BUILDKITD_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_LOW",
        "DEV_BUILDKITD_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_MAX",
    )),
    ("dev-memory_min_guaranteed.slice", (
        "DEV_MEMORY_MIN_GUARANTEED_CEILING", "DEV_MEMORY_MIN_GUARANTEED_LOW",
        "DEV_MEMORY_MIN_GUARANTEED_HIGH", "DEV_MEMORY_MIN_GUARANTEED_MAX",
    )),
)
MEMORY_CHILD_KEYS = tuple(
    key for _, keys in MEMORY_CHILD_SPECS for key in keys
)


def memory_aggregate_error(avail_kib: int, values: dict[str, str]) -> str | None:
    """Check the aggregate controls at ``dev.slice``.

    The four ordinary slices and ``dev-memory_min_guaranteed.slice`` are
    siblings under ``dev.slice``.  The latter's MemoryMin is the same
    authoritative value rendered on the parent, so it is counted once as a
    child claim (not once as a parent and again as a child).  Max is checked
    as the largest child ceiling because a parent's MemoryMax bounds the
    combined subtree; High, Low, and Min are budgets whose sibling claims are
    summed.  Empty values mean the documented explicit ``none`` state.
    """
    field_index = {"MEMORY_MIN": 0, "MEMORY_LOW": 1, "MEMORY_HIGH": 2, "MEMORY_MAX": 3}

    def sum_field(field: str) -> int:
        return sum(
            parse_size_to_kib(values.get(keys[field_index[field]], ""))
            for _, keys in MEMORY_CHILD_SPECS
        )

    highs = sum_field("MEMORY_HIGH")
    lows = sum_field("MEMORY_LOW")
    child_minimum = sum_field("MEMORY_MIN")
    root_high = parse_size_to_kib(values.get("DEV_MEMORY_HIGH", ""))
    root_low = parse_size_to_kib(values.get("DEV_MEMORY_LOW", ""))
    root_max = parse_size_to_kib(values.get("DEV_MEMORY_MAX", ""))
    minimum = parse_size_to_kib(
        values.get("DEV_MEMORY_MIN_GUARANTEED_CEILING", "")
    )
    child_maximum = max(
        (
            parse_size_to_kib(values.get(keys[field_index["MEMORY_MAX"]], ""))
            for _, keys in MEMORY_CHILD_SPECS
        ),
        default=0,
    )
    if highs > root_high:
        return (
            f"sibling MemoryHigh values ({kib_to_size_str(highs)} combined) exceed "
            f"dev.slice MemoryHigh ({kib_to_size_str(root_high)}); increase the parent "
            "or reduce a child"
        )
    if lows and root_low and lows > root_low:
        return (
            f"sibling MemoryLow values ({kib_to_size_str(lows)} combined) exceed "
            f"dev.slice MemoryLow ({kib_to_size_str(root_low)}); increase the parent "
            "or reduce a child"
        )
    if child_maximum > root_max:
        return (
            f"a child MemoryMax ({kib_to_size_str(child_maximum)}) exceeds dev.slice "
            f"MemoryMax ({kib_to_size_str(root_max)}); increase the parent or reduce "
            "that child"
        )
    if child_minimum and not minimum:
        return (
            "a child MemoryMin was configured while dev.slice MemoryMin is empty; "
            "the parent must explicitly carry the hard protection budget"
        )
    if minimum and child_minimum > minimum:
        return (
            f"sibling MemoryMin values ({kib_to_size_str(child_minimum)} combined) "
            f"exceed dev.slice MemoryMin ({kib_to_size_str(minimum)}); increase the "
            "parent or reduce a child"
        )
    if highs + child_minimum > avail_kib:
        return (
            "aggregate memory exceeds live MemAvailable: "
            f"sibling MemoryHigh values ({kib_to_size_str(highs)} combined) + "
            f"MemoryMin claims ({kib_to_size_str(child_minimum)}) > MemAvailable "
            f"({kib_to_size_str(avail_kib)}); reduce/re-enter a MemoryHigh or "
            "leave an optional MemoryMin empty"
        )
    return None


def _config_value_validators() -> dict[str, Validator]:
    """Return the non-interactive equivalents of the wizard's field checks."""
    validators: dict[str, Validator] = {
        "IO_DEV_PATH": validate_io_dev_path,
        "DEV_STATIC_RIOPS": validate_positive_int,
        "DEV_STATIC_WIOPS": validate_positive_int,
        "DEV_STATIC_RBW": validate_size_required,
        "DEV_STATIC_WBW": validate_size_required,
        "DEV_CPU_RESERVE_CORES": validate_nonneg_int,
        "DEV_SUBSLICE_CPU_RESERVE_CORES": validate_nonneg_int,
        "DEV_CPU_QUOTA": validate_cpu_quota,
        "DEV_SWAP_CASCADE_PCT": validate_pct_1_100,
        "DEV_SWAP_MAX": validate_size_or_auto,
        "DEV_SUBSLICE_IOPS_PCT": validate_pct_1_100,
        "DEV_ZSWAP_WRITEBACK": validate_yes_no,
        "DEV_BUILDKITD_IMAGE": validate_nonempty,
        "BUILDX_ACCIDENTAL_CONTAINER_POLICY": validate_guard_policy,
        "DOCKER_DAEMON_CGROUP_PARENT": validate_cgroup_parent,
        "DOCKER_SCOPE_BACKSTOP_MEMORY_MAX": validate_size_required,
        "DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX": validate_size_required,
        "WATCHER_PER_CONTAINER_MEMORY_MAX": validate_size_required,
        "WATCHER_PER_CONTAINER_GATES_MEMORY_MAX": validate_size_required,
        "WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT": validate_pct_1_100,
        "CGROUP2_FLAGS": validate_cgroup2_flags,
        "DEV_IO_CAP_PCT": validate_cap_pct,
        "SWEEP_IO_CAP_PCT": validate_cap_pct,
        "TESTRUNNER_IMAGE_PATTERNS": validate_nonempty,
        "BUILDKIT_NAME_PATTERNS": validate_nonempty,
        "DEVCONTAINER_NAME_PATTERNS": validate_nonempty,
        "SWEEP_INTERVAL": validate_nonempty,
        "IO_BASELINE_ENV": validate_nonempty,
    }

    for key in (
        "DEV_INTERACTIVE_CPU_QUOTA",
        "DEV_BACKGROUND_CPU_QUOTA",
        "DEV_GATES_CPU_QUOTA",
        "DEV_BUILDKITD_CPU_QUOTA",
    ):
        validators[key] = validate_cpu_quota
    for key in (
        "DEV_INTERACTIVE_MEMORY_SWAP_MAX",
        "DEV_BACKGROUND_MEMORY_SWAP_MAX",
        "DEV_GATES_MEMORY_SWAP_MAX",
        "DEV_BUILDKITD_MEMORY_SWAP_MAX",
    ):
        validators[key] = validate_size_or_auto
    for key in (
        "DEV_INTERACTIVE_ZSWAP_WRITEBACK",
        "DEV_BACKGROUND_ZSWAP_WRITEBACK",
        "DEV_GATES_ZSWAP_WRITEBACK",
        "DEV_BUILDKITD_ZSWAP_WRITEBACK",
    ):
        validators[key] = validate_yes_no
    for key in (
        "DEV_INTERACTIVE_CPU_WEIGHT",
        "DEV_BACKGROUND_CPU_WEIGHT",
        "DEV_GATES_CPU_WEIGHT",
        "DEV_BUILDKITD_CPU_WEIGHT",
        "DEV_INTERACTIVE_IO_WEIGHT",
        "DEV_BACKGROUND_IO_WEIGHT",
        "DEV_GATES_IO_WEIGHT",
        "DEV_BUILDKITD_IO_WEIGHT",
    ):
        validators[key] = validate_weight
    for key in ("DEV_BACKGROUND_OOM_PRESSURE_LIMIT", "DEV_GATES_OOM_PRESSURE_LIMIT"):
        validators[key] = validate_systemd_pct_1_100

    for key in (
        "DEV_MEMORY_HIGH",
        "DEV_MEMORY_MAX",
        "DEV_INTERACTIVE_MEMORY_HIGH",
        "DEV_INTERACTIVE_MEMORY_MAX",
        "DEV_BACKGROUND_MEMORY_HIGH",
        "DEV_BACKGROUND_MEMORY_MAX",
        "DEV_GATES_MEMORY_HIGH",
        "DEV_GATES_MEMORY_MAX",
        "DEV_BUILDKITD_MEMORY_HIGH",
        "DEV_BUILDKITD_MEMORY_MAX",
    ):
        validators[key] = validate_positive_size
    validators["DEV_MEMORY_LOW"] = validate_optional_positive_size
    validators["DEV_MEMORY_MIN_GUARANTEED_CEILING"] = validate_size_or_empty
    for _, keys in MEMORY_CHILD_SPECS:
        for index, key in enumerate(keys):
            if key == "DEV_MEMORY_MIN_GUARANTEED_CEILING":
                continue
            if index in (0, 1):
                validators[key] = validate_optional_positive_size
            elif index in (2, 3):
                validators[key] = (
                    validate_positive_size
                    if key.startswith((
                        "DEV_INTERACTIVE_",
                        "DEV_BACKGROUND_",
                        "DEV_GATES_",
                        "DEV_BUILDKITD_",
                    ))
                    else validate_optional_positive_size
                )
    return validators


def validate_host_setup_config(
    config_path: Path,
    example_path: Path,
    meminfo_path: Path,
) -> None:
    """Apply wizard-equivalent checks to a manually supplied config.

    This is intentionally data-only: it reads the config without sourcing it,
    so install.sh can fail before any host mutation or shell expansion occurs.
    """
    try:
        example = parse_env_file(example_path.read_text(encoding="utf-8"), strict=True)
        values = parse_env_file(config_path.read_text(encoding="utf-8"), strict=True)
        meminfo = parse_meminfo(meminfo_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TemplateError(f"cannot read host-setup validation input: {exc}") from exc

    missing = sorted(set(example) - set(values))
    if missing:
        raise TemplateError(
            f"{config_path} is missing keys from {example_path}: {', '.join(missing)}"
        )

    validators = _config_value_validators()
    unvalidated = sorted(set(example) - set(validators))
    if unvalidated:
        raise TemplateError(
            "no strict validator is registered for: " + ", ".join(unvalidated)
        )

    field_errors: list[str] = []
    for key, validator in validators.items():
        if key not in values:
            continue
        error = validator(values[key])
        if error:
            field_errors.append(f"{key}: {error}")
    if field_errors:
        raise TemplateError("invalid host-setup.env values: " + "; ".join(field_errors))

    avail_kib = meminfo.get("MemAvailable", 0)
    if avail_kib <= 0:
        raise TemplateError(
            f"{meminfo_path} has no positive MemAvailable; refusing aggregate validation"
        )
    relationship_errors = memory_relationship_errors(values)
    if relationship_errors:
        raise TemplateError(
            "invalid memory hierarchy: "
            + "; ".join(message for _, message in relationship_errors)
        )
    aggregate_error = memory_aggregate_error(avail_kib, values)
    if aggregate_error:
        raise TemplateError("invalid memory aggregate: " + aggregate_error)


def propose_memory_min_guaranteed_suggestion(
    avail_kib: int, tier_high_values: dict[str, str]
) -> tuple[int, int, str]:
    """Pure function backing Work step 4e's 'suggestion grounded in THIS
    host's own numbers'. Returns (leftover_kib, suggestion_kib, formula_text).

    Sums every MemoryHigh figure passed in `tier_high_values` (the realistic
    'expected concurrent' load — MemoryMax already assumes swap is absorbing
    overflow, so summing Max figures would understate what's actually free
    day to day), subtracts that from MemAvailable, and proposes a SMALL,
    explicitly conservative 5% of whatever's left over. Callers pass ALL
    FOUR tiers' MemoryHigh as of RG-55 P8 (round-1 review S2): step d's
    three host-scaled figures (interactive/background/buildkitd) plus
    dev-gates.slice's own fixed one, added by the caller since this wizard
    does not host-scale that tier interactively — omitting the fourth would
    systematically OVERSTATE the leftover (leaking protection to
    dev-interactive.slice/dev-background.slice, the exact failure direction
    CGROUP-NOTES.md warns about). This is advisory text only — see
    step_memory_min_guaranteed(): the prompt's actual DEFAULT stays whatever
    is already configured or empty, never this suggestion, so leaving the
    ceiling unset is never the awkward path.

    Doctest (round-1 review S2's "before/after" ask, an illustrative 8 GiB
    host, not this repo's own shipped example figures): BEFORE the fix
    (step d's three tiers only, dev-gates.slice's own claim missing from the
    sum) the leftover and suggested ceiling were both overstated; AFTER
    (main() now adds dev-gates.slice's own MemoryHigh to the same dict) they
    shrink to reflect what's actually earmarked:

    >>> avail_kib = 8 * 1024 * 1024  # 8 GiB
    >>> before = {"DEV_INTERACTIVE_MEMORY_HIGH": "2G", "DEV_BACKGROUND_MEMORY_HIGH": "2G", "DEV_BUILDKITD_MEMORY_HIGH": "1G"}
    >>> propose_memory_min_guaranteed_suggestion(avail_kib, before)
    (3145728, 157286, "MemAvailable (8G) - 3 tiers' MemoryHigh figures (5G combined) = 3G left over; 5% of that leftover = 150M")
    >>> after = dict(before, DEV_GATES_MEMORY_HIGH="4G")
    >>> propose_memory_min_guaranteed_suggestion(avail_kib, after)
    (0, 0, "MemAvailable (8G) - 4 tiers' MemoryHigh figures (9G combined) = 0 left over; 5% of that leftover = 0")
    """
    earmarked_kib = sum(parse_size_to_kib(v) for v in tier_high_values.values())
    leftover_kib = max(0, avail_kib - earmarked_kib)
    suggestion_kib = leftover_kib * 5 // 100
    # "N tiers'" rather than a hardcoded "three" (round-1 review S2): the
    # caller passes step d's three PLUS dev-gates.slice's own fixed figure
    # as of RG-55 P8, and a hardcoded count would go stale again the next
    # time a tier is added.
    formula = (
        f"MemAvailable ({kib_to_size_str(avail_kib)}) - {len(tier_high_values)} "
        f"tiers' MemoryHigh figures ({kib_to_size_str(earmarked_kib)} combined) = "
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
    it is genuinely SET; otherwise a host-scaled proposal (steps d — never
    the shipped example's own hardcoded figures); otherwise the example's
    own shipped default (every other section, and step e's ceiling, which
    must stay empty by default — see step_memory_min_guaranteed()).

    "genuinely SET" is `key in cfg_current and cfg_current[key]`, not merely
    `key in cfg_current`, and the difference is a real bug this fixes rather
    than a stylistic tightening. parse_env_file() cannot tell a key that was
    deliberately given an empty value from one that is simply present and
    blank — `IO_DEV_PATH=""` is a key present with value "" either way — and
    the overwhelmingly common case for ANY host is the latter:
    /etc/mdt/host-setup.env was seeded verbatim from host-setup.env.example,
    which ships most of its optional fields empty. Under the old membership-
    only test, that blank line beat a live, correct, already-computed
    proposal on every such host: the wizard would print
    `auto-discovered: /dev/mapper/...` and then offer `[<empty>]` as the
    default, silently discarding what it had just worked out. Confirmed live
    on a real host for IO_DEV_PATH, and structurally identical for all ten of
    step_memory_tiers()'s host-scaled keys.

    An empty value can therefore no longer be "chosen" by an earlier run and
    remembered. For every key whose proposal is None this costs nothing
    observable: the chain falls through to the example's own default, which
    for the one key where empty is the intended answer
    (DEV_MEMORY_MIN_GUARANTEED_CEILING) is itself empty. ONE exception:
    IO_DEV_PATH's proposal is NOT always None — step_io_device() passes
    `discovered or None`, so on a host where auto-discovery succeeds, an
    operator who had previously, deliberately, left IO_DEV_PATH empty (to
    opt static IO caps out entirely) will now see that fresh discovery
    offered as the default and silently adopt it on a bare Enter. That is a
    real, observable behavior change for that one case — not a defect in
    this fix, since a deliberately-empty value is otherwise
    indistinguishable from a blank one, but worth naming rather than
    claiming universal harmlessness."""
    if key in cfg_current and cfg_current[key]:
        return cfg_current[key]
    if proposal is not None:
        return proposal
    return example_defaults.get(key, "")


# ─── input validators ──────────────────────────────────────────────────────
#
# Contract (see ask()): a validator takes the already-defaulted value and
# returns None if it is acceptable, or a one-line error message if it is not
# — in which case ask() prints the message and asks again. A validator that
# wants to flag something WITHOUT refusing the answer prints its own
# `WARN: …` line via out() and still returns None; that split is deliberate
# and is used exactly where the underlying rule is a strong recommendation
# rather than a syntax requirement.

Validator = Callable[[str], "str | None"]

_SIZE_EXAMPLES = "e.g. '500M', '6G', '2048K', or a bare byte count"


def validate_size_required(value: str) -> str | None:
    """Every per-tier memory key and both docker-scope backstop keys.

    Empty is NOT accepted here, and that is a semantic judgement per field
    rather than a blanket rule: install.sh's render() DROPS any directive
    whose value resolves to empty, so an empty MemoryHigh/Max/SwapMax here
    does not mean "no limit" — it means the tier silently loses that
    governance entirely, which is never what someone answering a prompt
    labelled "MemoryHigh" intends. '0' IS accepted: it is what the proposals
    themselves produce for the swap keys on a host with no swap."""
    if not value:
        return f"a size is required here ({_SIZE_EXAMPLES}); leaving it empty would drop this setting from the rendered unit entirely"
    if not is_size_string(value):
        return f"{value!r} is not a systemd-style size string ({_SIZE_EXAMPLES})"
    return None


def validate_size_or_empty(value: str) -> str | None:
    """DEV_MEMORY_MIN_GUARANTEED_CEILING only — the one key where empty is a
    first-class, documented answer ("leave the whole mechanism inert"), not
    an omission. Any other size field uses validate_size_required."""
    if not value:
        return None
    if not is_size_string(value):
        return f"{value!r} is not a systemd-style size string ({_SIZE_EXAMPLES}) -- press Enter alone to leave the mechanism off"
    return None


def validate_cap_pct(value: str) -> str | None:
    """DEV_IO_CAP_PCT / SWEEP_IO_CAP_PCT.

    Two different rules, deliberately not merged into one:

    * HARD refuse anything that is not an integer in 1..100. A percentage of
      a measured device ceiling that is <= 0 or > 100 is not "an unusual
      choice", it is not a cap at all — 120% of the measured ceiling caps
      nothing, and 0% would wedge the whole tier.
    * WARN, but accept, outside the documented 60-80 band. That band is a
      strong recommendation this file argues for at length, not a protocol
      constraint the way a size string's syntax is: an operator on hardware
      whose measured ceiling is itself pessimistic (a shared SAN, a device
      whose baseline was taken under load) can have a legitimate reason to
      sit outside it, and hard-refusing would also make the wizard unable to
      reproduce a config a knowledgeable operator already hand-wrote. The
      warning is the kindness; the refusal would be presumption.
    """
    if not value:
        return "a percentage is required (an integer, 1-100)"
    try:
        pct = int(value)
    except ValueError:
        return f"{value!r} is not an integer percentage (give a bare number, e.g. 60 -- no '%' sign)"
    if pct < 1 or pct > 100:
        return f"{pct} is outside 1-100; a cap above 100% of the measured ceiling caps nothing, and 0% would stall the tier outright"
    if pct < 60 or pct > 80:
        out(
            f"WARN: {pct}% is outside the recommended 60-80 band explained above "
            f"-- accepted, but re-read that reasoning before keeping it."
        )
    return None


def validate_io_dev_path(value: str) -> str | None:
    """IO_DEV_PATH. Empty is valid and documented (static IO caps omitted).

    A non-empty answer must be an absolute path, and that is ALL that is
    checked: this wizard is explicitly supported for producing a config for a
    DIFFERENT host than the one it runs on, so testing the path against this
    machine's own /dev would wrongly refuse a perfectly valid answer."""
    if not value:
        return None
    if not value.startswith("/"):
        return f"{value!r} is not an absolute path -- give a device node such as /dev/sda or /dev/mapper/vg-root (or leave it empty to omit the static IO caps)"
    return None


_CPU_QUOTA_RE = re.compile(r"^(\d+)%$")


def validate_cpu_quota(value: str) -> str | None:
    """Shared by dev.slice's own DEV_CPU_QUOTA and every child's own
    *_CPU_QUOTA (DEV_INTERACTIVE_CPU_QUOTA, DEV_BACKGROUND_CPU_QUOTA,
    DEV_GATES_CPU_QUOTA, DEV_BUILDKITD_CPU_QUOTA). Empty means "auto-detect
    nproc - DEV_SUBSLICE_CPU_RESERVE_CORES cores at install time" (default
    reserve 3; dev.slice itself uses DEV_CPU_RESERVE_CORES, default 1) —
    this key's own documented convention, NOT "unlimited" — so empty is
    valid. Anything else must be systemd's own CPUQuota= percentage shape:
    an integer followed by '%', greater than zero."""
    if not value:
        return None
    match = _CPU_QUOTA_RE.match(value)
    if not match:
        return f"{value!r} is not a systemd CPUQuota= percentage -- use N% (e.g. '400%' for 4 cores), or leave it empty to auto-detect"
    if int(match.group(1)) <= 0:
        return "a CPUQuota of 0% would stop the slice from running anything at all -- give at least '100%' (one core), or leave it empty to auto-detect"
    return None


def validate_nonneg_int(value: str) -> str | None:
    """DEV_CPU_RESERVE_CORES / DEV_SUBSLICE_CPU_RESERVE_CORES: a plain count
    of cores to reserve for the host/production, never empty (install.sh's
    auto-detect formula is nproc - this value, floored at 1 core, so the
    formula needs a number to subtract)."""
    if not value:
        return "a whole number of cores is required (e.g. '1')"
    try:
        n = int(value)
    except ValueError:
        return f"{value!r} is not a whole number"
    if n < 0:
        return "a negative core count makes no sense here"
    return None


def validate_positive_int(value: str) -> str | None:
    error = validate_nonneg_int(value)
    if error:
        return error
    if int(value) <= 0:
        return "a positive whole number is required"
    return None


def validate_pct_1_100(value: str) -> str | None:
    """A bare integer percentage, 1-100, no band opinion attached — for keys
    where the shipped default is a reasonable starting point rather than a
    documented recommended range (unlike validate_cap_pct's 60-80 band)."""
    if not value:
        return "a percentage is required (an integer, 1-100)"
    try:
        pct = int(value)
    except ValueError:
        return f"{value!r} is not an integer percentage (give a bare number, e.g. 80 -- no '%' sign)"
    if pct < 1 or pct > 100:
        return f"{pct} is outside 1-100"
    return None


def validate_systemd_pct_1_100(value: str) -> str | None:
    """Validate systemd's percentage form, including its required ``%``."""
    if not value:
        return "a percentage is required (an integer followed by '%', 1-100)"
    match = re.fullmatch(r"(\d+)%", value)
    if not match:
        return f"{value!r} is not a systemd percentage (use an integer followed by '%', e.g. '75%')"
    pct = int(match.group(1))
    if not 1 <= pct <= 100:
        return f"{pct}% is outside 1-100%"
    return None


def validate_yes_no(value: str) -> str | None:
    if value not in ("yes", "no"):
        return "choose exactly 'yes' or 'no'"
    return None


def validate_cgroup2_flags(value: str) -> str | None:
    if value not in ("fix", "warn"):
        return "choose exactly 'fix' or 'warn'"
    return None


def validate_size_or_auto(value: str) -> str | None:
    """Per-tier MemorySwapMax and dev.slice's own DEV_SWAP_MAX: empty means
    "auto-detect DEV_SWAP_CASCADE_PCT% of the parent's own derived swap
    ceiling at install time" — a real, documented behavior, not an
    omission — so empty is valid here, unlike validate_size_required."""
    if not value:
        return None
    if not is_size_string(value):
        return f"{value!r} is not a systemd-style size string ({_SIZE_EXAMPLES}) -- or leave it empty to auto-detect from DEV_SWAP_CASCADE_PCT"
    return None


def validate_nonempty(value: str) -> str | None:
    """DEV_BUILDKITD_IMAGE. A deliberately shallow check: a real OCI
    reference grammar here would reject perfectly workable registry/tag/digest
    forms for no gain, and the value is handed straight to docker, which has
    its own (authoritative) parser. Non-empty is the useful bar."""
    if not value:
        return "an image reference is required (e.g. 'moby/buildkit:buildx-stable-1-rootless')"
    return None


def validate_cgroup_parent(value: str) -> str | None:
    """DOCKER_DAEMON_CGROUP_PARENT. Non-empty is a hard requirement (this key
    is merged verbatim into /etc/docker/daemon.json; an empty string there is
    not a valid daemon-wide default). Not ending in '.slice' is only a
    WARNING: a slice name that does not resolve fails OPEN — the container
    lands in an unbounded transient scope, caught by the docker-.scope.d
    backstop — rather than crashing anything, which is this project's
    documented graceful-degradation behaviour (README, CGROUP-NOTES.md). A
    warning is the appropriate response to that; a hard gate is not."""
    if not value:
        return "a cgroup parent is required (e.g. 'dev-background.slice')"
    if not value.endswith(".slice"):
        out(
            f"WARN: {value!r} does not end in '.slice' -- systemd slice units always do. "
            "A name that does not resolve fails OPEN (the container lands in an unbounded "
            "transient scope, bounded only by the docker-.scope.d backstop), so this is "
            "accepted, but check it for a typo."
        )
    return None


# ─── I/O helpers ─────────────────────────────────────────────────────────


def ask(label: str, default: str, validate: Validator | None = None) -> str:
    """Prompt for one value; Enter accepts `default` unchanged.

    With a `validate` callable, an unacceptable answer is reported and asked
    for again rather than silently accepted or coerced.

    A closed or exhausted stdin (canned-answer runs, Ctrl-D) still accepts
    the default, exactly as a bare Enter does. That case also has to break
    the validation loop: re-prompting a stdin that will never produce another
    line would spin forever, so an invalid value at EOF is reported as a
    warning and accepted, which is at least visible in the transcript. It is
    only reachable when the DEFAULT itself is invalid — i.e. the template or
    the existing config already held something bad — since a valid default
    validates fine.
    """
    shown = default if default else "<empty>"
    while True:
        at_eof = False
        try:
            raw = _prompt(f"{label} [{shown}]: ")
        except EOFError:
            print()
            raw = ""
            at_eof = True
        raw = raw.strip()
        value = raw if raw else default
        if validate is None:
            return value
        error = validate(value)
        if error is None:
            return value
        if at_eof:
            out(f"WARN: {error} -- accepting it anyway; stdin is exhausted, so there is nobody left to ask.")
            return value
        out(f"invalid: {error}", indent="  ")


def ask_yn(question: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        raw = _prompt(f"{question} [{hint}]: ").strip().lower()
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
    validate: Validator | None = None,
) -> str:
    default = resolve_default(key, cfg_current, example_defaults, proposal)
    return ask(f"{label} ({key})", default, validate=validate)


def walk_yn_key(
    label: str,
    key: str,
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> str:
    """Same role as walk_key(), for the yes/no MemoryZSwapWriteback keys:
    resolve_default() still supplies the starting point (an already-
    configured value, else the example's own shipped default), ask_yn()
    renders it as a Y/n prompt instead of free text, and the answer is
    written back as the literal 'yes'/'no' string install.sh's render()
    expects, never a Python bool."""
    default_str = resolve_default(key, cfg_current, example_defaults, None)
    default_bool = default_str.strip().lower() not in ("no", "0", "false")
    return "yes" if ask_yn(f"{label} ({key})", default_bool) else "no"


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


# ─── interactive sections (Work step 4a-4i) ────────────────────────────────


def step_io_device(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> str:
    out("\n-- a. IO device (IO_DEV_PATH) --")
    out(
        "The block device that install.sh sizes the STATIC IOPS/bandwidth caps against -- "
        "`dev.slice`'s `IOReadBandwidthMax`, `IOWriteBandwidthMax`, `IOReadIOPSMax` and "
        "`IOWriteIOPSMax`. Those statics are the boot-window fallback: they are what is "
        "actually in force from boot onwards, and they stay in force indefinitely until "
        "the IO baseline (step b, next) has been measured at least once. Only after that "
        "does the runtime sweep replace them with a percentage of this device's real, "
        "measured ceilings."
    )
    out()
    out(
        "Leaving this empty is allowed and simply omits the static IO caps altogether -- "
        "the tiers still get all of their memory and CPU governance, they just have no "
        "boot-window IO ceiling."
    )
    out()
    out(
        "The value below is auto-discovered exactly the way install.sh does it at render "
        "time: `findmnt` against `/var/lib/docker`, falling back to `/`. Note that you are "
        "configuring a device path, not selecting one -- nothing here checks the answer "
        "against this machine's own mounts, so it is fine to name a device that only "
        "exists on the host you are writing this config FOR."
    )
    discovered = discover_io_dev_path_from_findmnt()
    if discovered:
        out(f"auto-discovered on this host: `{discovered}`")
    else:
        out(
            "auto-discovery found nothing (findmnt failed, or returned no source) -- at an "
            "empty value the static IO caps are simply omitted."
        )
    return walk_key(
        "Block device node backing docker's data dir",
        "IO_DEV_PATH",
        cfg_current,
        example_defaults,
        proposal=discovered or None,
        validate=validate_io_dev_path,
    )


def step_io_baseline(baseline_env_path: Path, io_baseline_script: Path) -> bool:
    """Offer to measure this disk's real IO ceilings.

    Returns True ONLY when this call actually invoked mdt-io-baseline.py and
    it exited 0 — i.e. there is now a fresh, successful measurement from THIS
    session. Every other path returns False: cache already fresh, operator
    declined, script missing, or a nonzero exit. main() uses that to decide
    whether the `--with-baseline` sub-question at the very end is worth
    asking at all; re-running a ~4-minute disk-saturating benchmark that just
    completed would buy nothing.
    """
    out("\n-- b. IO baseline --")
    out(f"cache: `{baseline_env_path}`")
    out(
        "`mdt-io-baseline.py` measures this disk's real IOPS and bandwidth ceilings with "
        "`fio` and caches the result for 30 days. The runtime sweep takes its caps as a "
        "PERCENTAGE of those measured numbers (steps c below), so until this has run once "
        "there is nothing to take a percentage OF, and `mdt-apply-dev-caps.sh` falls back to "
        "the deliberately tight `DEV_STATIC_*` caps in host-setup.env."
    )
    status = check_baseline_freshness(baseline_env_path, io_baseline_script)
    if status == "fresh":
        out("This cache is fresh (measured less than 30 days ago) -- leaving it as-is.")
        return False
    reason = (
        "No cache found yet."
        if status == "missing"
        else "This cache is stale (30 days old or more)."
    )
    out(reason)
    if not io_baseline_script.exists():
        out(
            f"WARN: `{io_baseline_script}` not found -- skipping this step. Run it manually "
            "once it is installed; the static caps hold until you do."
        )
        return False
    run_now = ask_yn(
        "Run the IO baseline benchmark now? It takes about 4 minutes and SATURATES THE "
        "DISK for that whole time, so only do it in a quiet window -- anything else using "
        "this disk will crawl. (The script itself also warns and gives you 5 seconds to "
        "Ctrl-C if it finds containers running.)",
        default=False,
    )
    if not run_now:
        out(f"Skipping -- you can run it later with: sudo {io_baseline_script}")
        return False
    # Letting mdt-io-baseline.py own the quiet-window warning AND the 30-day
    # freshness check (no --force): do not duplicate either here.
    # Flush first: our own stdout is block-buffered whenever it is not a
    # terminal (a piped/redirected run, i.e. every recorded transcript), while
    # the child writes to the same fd immediately -- without this the child's
    # output lands ABOVE the lines that introduced it.
    sys.stdout.flush()
    result = subprocess.run([sys.executable, str(io_baseline_script)], check=False)
    if result.returncode != 0:
        out(
            f"WARN: the IO baseline run exited {result.returncode} -- the static caps remain "
            "in force until it succeeds."
        )
        return False
    return True


def step_io_cap_pct(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> tuple[str, str]:
    out("\n-- c. IO cap percentages --")
    out(
        "These are the two percentages the runtime sweep applies to the MEASURED device "
        "ceilings from step b's `io-baseline.env`. Both belong in a 60-80% band, for two "
        "separate reasons: never 100%, because a saturated device queues everything behind "
        "whichever burst got there first -- the exact stall this whole tiering exists to "
        "prevent -- and not much below ~60% either, because past that point you are just "
        "throttling ordinary work without buying any additional protection."
    )
    out()
    out("The two are not variations on one setting; they protect different things:")
    out(
        "`DEV_IO_CAP_PCT` -- the whole-estate ceiling, applied to `dev.slice` itself. It "
        "bounds every dev container TOGETHER, as one pool. What it protects is PRODUCTION "
        "on this host from the dev tier as a whole; it says nothing about how tier members "
        "treat each other.",
        indent="  ",
        hang="    ",
    )
    out(
        "`SWEEP_IO_CAP_PCT` -- the per-container ceiling, applied to each container "
        "individually. This is what protects tier members from EACH OTHER, and it is the "
        "only governance buildkit workers get at all, since placing Buildx builders under "
        "`dev.slice` does not work in the first place.",
        indent="  ",
        hang="    ",
    )
    dev_pct = walk_key(
        "Whole-estate IO cap %",
        "DEV_IO_CAP_PCT",
        cfg_current,
        example_defaults,
        validate=validate_cap_pct,
    )
    sweep_pct = walk_key(
        "Per-container IO cap %",
        "SWEEP_IO_CAP_PCT",
        cfg_current,
        example_defaults,
        validate=validate_cap_pct,
    )
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

    out("\n-- d. Per-tier memory --")
    out(
        f"This host, right now: MemTotal={total_gib:.1f}GiB, MemAvailable={avail_gib:.1f}GiB, "
        f"SwapTotal={swap_gib:.1f}GiB."
    )
    out()
    out(
        "The proposals below scale off MemAvailable rather than MemTotal, deliberately: "
        "MemAvailable is what is genuinely free on THIS host at this moment, already net of "
        "everything else using memory -- including any co-located production tier -- whereas "
        "MemTotal would overstate the real headroom on a shared host."
    )
    out()
    out(
        "They also overlap across the three tiers rather than partitioning 100% of RAM up "
        "front, and that is intentional too. MemoryHigh is a SOFT throttle (the kernel "
        "reclaims harder above it, it does not refuse allocations), and MemoryMax relies on "
        "this host's swap to absorb the overflow (see the README's tiering model). Three "
        "tiers that each occasionally use their full share is normal; three tiers whose "
        "ceilings sum to exactly RAM would just be a partition with extra steps. Override "
        "any of them if you know better for this host."
    )
    out(
        "Swap ceilings for these tiers are NOT asked here -- see step h below, which walks "
        "the whole DEV_SWAP_CASCADE_PCT cascade (host swap -> dev.slice -> each child) in "
        "one place."
    )

    proposals = propose_memory_tiers(total_kib, avail_kib)
    values: dict[str, str] = {}

    out()
    out(
        "`dev-interactive.slice` -- devcontainers and IDE work. Its MemoryMin is a small, "
        "STABLE percentage of MemTotal rather than of MemAvailable: a protection floor that "
        "shrank whenever something else on the host got busy would give way exactly when it "
        "is most needed. Low, High and Max scale off MemAvailable as described above.",
        indent="  ",
        hang="  ",
    )
    for key, label in (
        ("DEV_INTERACTIVE_MEMORY_LOW", "  MemoryLow"),
        ("DEV_INTERACTIVE_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_INTERACTIVE_MEMORY_MAX", "  MemoryMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, proposals.get(key), validate=validate_optional_positive_size if key.endswith(("_MIN", "_LOW")) else validate_positive_size
        )

    out()
    out(
        "`dev-background.slice` -- test, build and gate containers. Its MemorySwapMax "
        "(step h) is deliberately relaxed relative to these: a build that swaps just "
        "finishes slowly, while a build that OOMs fails outright and has to be re-run.",
        indent="  ",
        hang="  ",
    )
    for key, label in (
        ("DEV_BACKGROUND_MEMORY_MIN", "  MemoryMin (empty = none)"),
        ("DEV_BACKGROUND_MEMORY_LOW", "  MemoryLow (empty = none)"),
        ("DEV_BACKGROUND_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_BACKGROUND_MEMORY_MAX", "  MemoryMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, proposals.get(key), validate=validate_optional_positive_size if key.endswith(("_MIN", "_LOW")) else validate_positive_size
        )

    out()
    out(
        "`dev-buildkitd.slice` -- the shared BuildKit worker. Size this for CONCURRENT "
        "multi-project builds, not for one build at a time: a single daemon serves every "
        "project on the host, so its peak is the sum of whatever happens to be building "
        "together, not the cost of the largest single build.",
        indent="  ",
        hang="  ",
    )
    for key, label in (
        ("DEV_GATES_MEMORY_MIN", "  MemoryMin (empty = none)"),
        ("DEV_GATES_MEMORY_LOW", "  MemoryLow (empty = none)"),
        ("DEV_GATES_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_GATES_MEMORY_MAX", "  MemoryMax"),
        ("DEV_BUILDKITD_MEMORY_MIN", "  BuildKit MemoryMin (empty = none)"),
        ("DEV_BUILDKITD_MEMORY_LOW", "  BuildKit MemoryLow (empty = none)"),
        ("DEV_BUILDKITD_MEMORY_HIGH", "  BuildKit MemoryHigh"),
        ("DEV_BUILDKITD_MEMORY_MAX", "  BuildKit MemoryMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, proposals.get(key), validate=validate_optional_positive_size if key.endswith(("_MIN", "_LOW")) else validate_positive_size
        )

    return values


def step_memory_min_guaranteed(
    avail_kib: int,
    tier_high_values: dict[str, str],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> str:
    out("\n-- e. Memory-min-guaranteed ceiling (DEV_MEMORY_MIN_GUARANTEED_CEILING) --")
    out(
        "What it is: a HARD memory floor, as opposed to MemoryLow's soft, best-effort "
        "protection. It is memory a cgroup keeps even under host-wide pressure, protected "
        "from reclaim outright rather than merely reclaimed-last."
    )
    out()
    out(
        "Where it is set, and why in TWO places at once: this one number is written both to "
        "`dev.slice` (the root of the dev estate) and to `dev-memory_min_guaranteed.slice`, "
        "which is a SIBLING of the interactive and background tiers you just configured -- "
        "not nested underneath either of them -- and the two must be pinned to the EXACT "
        "same value on purpose."
    )
    out()
    out(
        "The reason is cgroup v2's redistribution rule: a parent hands its UNCLAIMED "
        "protection down to whichever child is actually using memory, proportionally. So a "
        "generous number on `dev.slice` alone does not sit there in reserve -- the surplus "
        "leaks to the interactive and background tiers, which defeats the entire point of "
        "having a dedicated guaranteed tier. Give `dev.slice` LESS than this ceiling instead "
        "and you get the opposite failure: the leaf slice's own MemoryMin becomes silently "
        "inert, because a child's floor is only as real as what its ancestors hand down. "
        "Both slices need exactly this one number, never two independently-chosen ones."
    )
    out()
    out(
        "The effect, and who actually gets protected: nothing, by default. This mechanism is "
        "opt-in PER CONTAINER -- a stack must explicitly place itself on "
        "`dev-memory_min_guaranteed.slice` (via `governance.cgroup_parent` in its ciu config) "
        "and declare its own claim (`governance.mem_min`). Setting this ceiling on its own "
        "protects NOTHING; all it does is raise the total that placed stacks COULD claim "
        "between them. (The admission control that enforces this ceiling against live claims "
        f"is a separate, in-flight piece of work on ciu's side -- see {CIU_P50_RELATIVE_PATH})"
    )

    leftover_kib, suggestion_kib, formula = propose_memory_min_guaranteed_suggestion(
        avail_kib, tier_high_values
    )
    out()
    out(f"This host: {formula}.")
    if suggestion_kib > 0:
        out(
            f"So, IF you want a nonzero ceiling at all, a conservative suggestion would be "
            f"{kib_to_size_str(suggestion_kib)}. It is meant to stay small: see "
            "CGROUP-NOTES.md 'Per-container memory.min guarantees' for why a generous number "
            "here is actively harmful rather than merely wasteful."
        )
    else:
        out(
            "This host has little to no headroom left after step d's tiers, so leaving this "
            "unset is the reasonable choice here."
        )
    out()
    out(
        "Leaving this EMPTY -- just press Enter -- keeps the whole mechanism fully inert and "
        "matches host-setup.env.example's own shipped default. That empty value, not the "
        "suggestion above, is the default answer offered below."
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
        validate=validate_size_or_empty,
    )


def step_buildkitd(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> tuple[str, str]:
    out("\n-- f. buildkitd --")
    out(
        "The container image the host-managed BuildKit worker runs from "
        "(`mdt-buildkitd.service`). The rootless variant is what the shipped default uses."
    )
    image = walk_key(
        "BuildKit worker image",
        "DEV_BUILDKITD_IMAGE",
        cfg_current,
        example_defaults,
        validate=validate_nonempty,
    )
    out()
    out(
        "CPUQuota uses a DIFFERENT empty-means convention from every other key in this file, "
        "so read this one carefully: empty here does NOT mean 'not applied'. It means "
        "'auto-detect (nproc - DEV_SUBSLICE_CPU_RESERVE_CORES) cores at install time' "
        "(default reserve 3, the same auto-detect every sibling child slice uses, step h "
        "below), floored at one core. An explicit percentage always overrides that "
        "auto-detection -- systemd counts one core as 100%, so `400%` means four cores. Do "
        "not assume empty means uncapped here; it does not."
    )
    cpu_quota = walk_key(
        "CPUQuota (empty = auto-detect nproc - DEV_SUBSLICE_CPU_RESERVE_CORES cores)",
        "DEV_BUILDKITD_CPU_QUOTA",
        cfg_current,
        example_defaults,
        validate=validate_cpu_quota,
    )
    return image, cpu_quota


def step_docker_daemon(
    cfg_current: dict[str, str], example_defaults: dict[str, str]
) -> tuple[str, str, str]:
    out("\n-- g. Docker daemon.json keys this tool owns --")
    out(
        "`DOCKER_DAEMON_CGROUP_PARENT` is the daemon-wide DEFAULT placement (D-G7): the slice "
        "any container lands in when it names no `--cgroup-parent` of its own. install.sh "
        "MERGES this single key into `/etc/docker/daemon.json` -- it never overwrites that "
        "file and never touches any other key in it -- and the daemon needs a full RESTART, "
        "not a reload, before the new default takes effect."
    )
    cgroup_parent = walk_key(
        "Default cgroup-parent",
        "DOCKER_DAEMON_CGROUP_PARENT",
        cfg_current,
        example_defaults,
        validate=validate_cgroup_parent,
    )
    out()
    out(
        "The `docker-.scope.d` backstop (D-G8) is a 'never truly unbounded' floor applied to "
        "EVERY container's transient scope, whichever slice it named -- or none. It exists "
        "for the case where placement went wrong (a typo'd slice, a container started before "
        "the units were installed), so size it GENEROUSLY: comfortably above any legitimate "
        "single container's real ceiling on this host. It is a fail-open backstop, not a "
        "tier limit, and a tight number here would throttle healthy containers to catch a "
        "rare mistake."
    )
    backstop_max = walk_key(
        "Backstop MemoryMax",
        "DOCKER_SCOPE_BACKSTOP_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_size_required,
    )
    backstop_swap = walk_key(
        "Backstop MemorySwapMax",
        "DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX",
        cfg_current,
        example_defaults,
        validate=validate_size_required,
    )
    return cgroup_parent, backstop_max, backstop_swap


def step_cpu_and_swap_cascade(
    cfg_current: dict[str, str], example_defaults: dict[str, str]
) -> dict[str, str]:
    """dev.slice's own absolute CPU/swap ceilings, each child's tighter
    sub-ceiling of the same two, the dev-gates.slice/dev-buildkitd.slice
    IOPS sub-ceiling, and the MemoryZSwapWriteback policy for all five
    slices -- one section, because all of them follow the identical "one
    ceiling at dev.slice, one tighter fraction per child" shape."""
    values: dict[str, str] = {}

    out("\n-- h. CPU ceiling, IOPS sub-ceiling, swap cascade, zswap writeback --")
    out(
        "CPUQuota: dev.slice reserves DEV_CPU_RESERVE_CORES cores for the host/production "
        "(auto-detected as nproc minus this many, floored at 1 core, when the quota itself "
        "is left empty below). Every child slice reserves DEV_SUBSLICE_CPU_RESERVE_CORES "
        "instead -- a LARGER reservation, so no single tier can alone claim dev.slice's "
        "whole budget."
    )
    values["DEV_CPU_RESERVE_CORES"] = walk_key(
        "dev.slice CPU core reserve",
        "DEV_CPU_RESERVE_CORES",
        cfg_current,
        example_defaults,
        validate=validate_nonneg_int,
    )
    values["DEV_SUBSLICE_CPU_RESERVE_CORES"] = walk_key(
        "Child-slice CPU core reserve",
        "DEV_SUBSLICE_CPU_RESERVE_CORES",
        cfg_current,
        example_defaults,
        validate=validate_nonneg_int,
    )
    out()
    out(
        "The quotas themselves: leave any of these empty to auto-detect from the reserve "
        "counts above at install time; an explicit `N%` (systemd counts one core as 100%) "
        "always overrides."
    )
    for key, label in (
        ("DEV_CPU_QUOTA", "  dev.slice CPUQuota"),
        ("DEV_INTERACTIVE_CPU_QUOTA", "  dev-interactive.slice CPUQuota"),
        ("DEV_BACKGROUND_CPU_QUOTA", "  dev-background.slice CPUQuota"),
        ("DEV_GATES_CPU_QUOTA", "  dev-gates.slice CPUQuota"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, validate=validate_cpu_quota
        )
    out(
        "(dev-buildkitd.slice's own CPUQuota was already asked in step f above -- same "
        "auto-detect convention.)"
    )

    out()
    out(
        "IOPS sub-ceiling: dev-gates.slice and dev-buildkitd.slice each additionally get "
        "IOReadIOPSMax/IOWriteIOPSMax at this percentage of whatever the runtime sweep just "
        "measured for dev.slice's own shared ceiling -- IOPS only, bandwidth stays at the "
        "shared ceiling untouched. Applied at runtime (mdt-apply-dev-caps.sh), not a static "
        "unit-file line, because it is a percentage of a number only known once measured."
    )
    values["DEV_SUBSLICE_IOPS_PCT"] = walk_key(
        "dev-gates/dev-buildkitd IOPS sub-ceiling %",
        "DEV_SUBSLICE_IOPS_PCT",
        cfg_current,
        example_defaults,
        validate=validate_pct_1_100,
    )

    out()
    out(
        "MemoryZSwapWriteback: whether a tier's coldest pages may drain from zswap out to "
        "disk swap ('yes', the default, keeps zswap a cache; 'no' pins the cold tail in "
        "compressed RAM). This is a PLAIN PER-CGROUP TOGGLE THAT DOES NOT CASCADE: each "
        "slice's own file value is independent, so every slice below needs its own answer "
        "-- dev.slice's is a safety anchor only, since a 'no' on ANY ancestor silently "
        "disables writeback for its whole subtree regardless of what a child's own file "
        "says (CGROUP-NOTES.md 'zswap writeback -- who may page to disk')."
    )
    for key, label in (
        ("DEV_ZSWAP_WRITEBACK", "  dev.slice (safety anchor)"),
        ("DEV_INTERACTIVE_ZSWAP_WRITEBACK", "  dev-interactive.slice"),
        ("DEV_BACKGROUND_ZSWAP_WRITEBACK", "  dev-background.slice"),
        ("DEV_GATES_ZSWAP_WRITEBACK", "  dev-gates.slice"),
        ("DEV_BUILDKITD_ZSWAP_WRITEBACK", "  dev-buildkitd.slice"),
    ):
        values[key] = walk_yn_key(label, key, cfg_current, example_defaults)

    out()
    out(
        "Swap cascade: dev.slice's own MemorySwapMax auto-detects as this percentage of the "
        "host's real total swap (/proc/meminfo SwapTotal) when left empty; each child's own "
        "MemorySwapMax then auto-detects as the SAME percentage of dev.slice's own derived "
        "value, not of the host total again -- and the reactive watcher's per-container swap "
        "cap (step i, below) applies the same percentage a third time. Absolute ceiling at "
        "every level, same 'children combined still bounded by the parent' reasoning as CPU."
    )
    values["DEV_SWAP_CASCADE_PCT"] = walk_key(
        "Swap cascade %",
        "DEV_SWAP_CASCADE_PCT",
        cfg_current,
        example_defaults,
        validate=validate_pct_1_100,
    )
    out(
        "Leave any of the five below empty to auto-detect from the cascade; an explicit size "
        "always overrides just that one slice."
    )
    for key, label in (
        ("DEV_SWAP_MAX", "  dev.slice MemorySwapMax"),
        ("DEV_INTERACTIVE_MEMORY_SWAP_MAX", "  dev-interactive.slice MemorySwapMax"),
        ("DEV_BACKGROUND_MEMORY_SWAP_MAX", "  dev-background.slice MemorySwapMax"),
        ("DEV_GATES_MEMORY_SWAP_MAX", "  dev-gates.slice MemorySwapMax"),
        ("DEV_BUILDKITD_MEMORY_SWAP_MAX", "  dev-buildkitd.slice MemorySwapMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, validate=validate_size_or_auto
        )

    return values


def _none_memory_field(slice_name: str, field: str, reason: str) -> None:
    out(f"  {field}: none ({slice_name} has no {field}= directive; {reason})")


def _prompt_slice_memory(
    slice_name: str,
    fields: tuple[tuple[str, str, Validator], ...],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
    proposals: dict[str, str],
    values: dict[str, str],
) -> None:
    out(f"\n  {slice_name} memory hierarchy")
    out(
        "  MemoryMin is hard protection that participates in the parent/child "
        "hierarchy; MemoryLow is soft best-effort protection; MemoryHigh is a "
        "soft throttle into reclaim; MemoryMax is the hard RAM ceiling. Values "
        "are entered with binary units (K/M/G, converted to KiB for validation); "
        "MemorySwapMax is asked separately because it is the swap ceiling."
    )
    for field, key, validator in fields:
        value = walk_key(f"  {field}", key, cfg_current, example_defaults, proposals.get(key), validator)
        values[key] = value


def _reprompt_memory_constraints(
    avail_kib: int,
    values: dict[str, str],
) -> None:
    """Reject hierarchy/aggregate mistakes and ask again; never clamp."""
    for _ in range(32):
        errors = memory_relationship_errors(values)
        if errors:
            key, message = errors[0]
            out(f"invalid: {message}", indent="  ")
            before = values[key]
            values[key] = ask(
                f"  corrected value for {key}",
                before,
                validate=validate_optional_positive_size if ("_MIN" in key or "_LOW" in key or key == "DEV_MEMORY_LOW") else validate_positive_size,
            )
            if values[key] == before and memory_relationship_errors(values):
                raise ValueError("memory hierarchy is still invalid after re-prompt")
            continue
        aggregate = memory_aggregate_error(avail_kib, values)
        if aggregate is None:
            return
        out(f"invalid: {aggregate}", indent="  ")
        minimum = parse_size_to_kib(values.get("DEV_MEMORY_MIN_GUARANTEED_CEILING", ""))
        high_keys = (
            "DEV_INTERACTIVE_MEMORY_HIGH",
            "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH",
            "DEV_BUILDKITD_MEMORY_HIGH",
        )
        root_high = parse_size_to_kib(values.get("DEV_MEMORY_HIGH", ""))
        child_high_total = sum(parse_size_to_kib(values[key]) for key in high_keys)
        if child_high_total > root_high:
            key = "DEV_MEMORY_HIGH"
        elif any(parse_size_to_kib(values[key]) > parse_size_to_kib(values["DEV_MEMORY_MAX"]) for key in ("DEV_INTERACTIVE_MEMORY_MAX", "DEV_BACKGROUND_MEMORY_MAX", "DEV_GATES_MEMORY_MAX", "DEV_BUILDKITD_MEMORY_MAX")):
            key = "DEV_MEMORY_MAX"
        elif any(values.get(key, "") for key in ("DEV_INTERACTIVE_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_MIN", "DEV_GATES_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_MIN")) and not minimum:
            key = "DEV_MEMORY_MIN_GUARANTEED_CEILING"
        elif minimum and sum(parse_size_to_kib(values.get(key, "")) for key in ("DEV_INTERACTIVE_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_MIN", "DEV_GATES_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_MIN")) > minimum:
            key = "DEV_MEMORY_MIN_GUARANTEED_CEILING"
        elif minimum:
            key = "DEV_MEMORY_MIN_GUARANTEED_CEILING"
        else:
            key = max(high_keys, key=lambda candidate: parse_size_to_kib(values[candidate]))
        before = values.get(key, "")
        values[key] = ask(
            f"  corrected value for {key}",
            before,
            validate=validate_size_or_empty if key.startswith("DEV_MEMORY_MIN") else validate_positive_size,
        )
        if values[key] == before and memory_aggregate_error(avail_kib, values):
            raise ValueError("aggregate memory remains above live MemAvailable after re-prompt")
    raise ValueError("memory constraints did not converge after 32 correction prompts")


def step_slice_first_resources(
    meminfo: dict[str, int],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> dict[str, str]:
    """Walk each governed slice as one unit of policy, from parent to leaves."""
    total_kib = meminfo.get("MemTotal", 0)
    avail_kib = meminfo.get("MemAvailable", total_kib)
    if avail_kib <= 0:
        raise ValueError("MemAvailable is missing or zero; refusing to invent memory limits")
    proposals = propose_memory_tiers(total_kib, avail_kib)
    values: dict[str, str] = {}

    out("\n-- d. Governed slices (slice-first) --")
    out(
        f"Live host facts: MemTotal={kib_to_size_str(total_kib)}, "
        f"MemAvailable={kib_to_size_str(avail_kib)}. Suggestions use "
        "MemAvailable from /proc/meminfo, then every entered value is checked "
        "in KiB against the cgroup hierarchy and the aggregate live budget."
    )
    out(
        "Configure one slice completely before advancing. The fixed architecture "
        "choices are shown as explicit 'none': ordinary slices do not receive a "
        "fabricated MemoryMin, the root uses absolute measured IO caps rather than "
        "IOWeight, and the guaranteed sibling has only the shared MemoryMin. The "
        "resource values and the terminate/report-only policy remain configurable."
    )
    _, suggestion_kib, formula = propose_memory_min_guaranteed_suggestion(
        avail_kib,
        {key: proposals[key] for key in (
            "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_HIGH",
        )},
    )
    out(f"Computed MemoryMin suggestion (not auto-selected): {formula}.")
    if suggestion_kib:
        out(f"A conservative optional ceiling would be {kib_to_size_str(suggestion_kib)}; Enter keeps it empty.")

    # Parent slice: this existing key is the only authoritative dev.slice
    # MemoryMin and is mirrored byte-for-byte on the guaranteed sibling below.
    out("\n  dev.slice — shared parent")
    out("  MemoryMin is an optional hard protection budget for explicitly admitted containers; it is not a per-IDE floor.")
    values["DEV_MEMORY_MIN_GUARANTEED_CEILING"] = walk_key(
        "  MemoryMin (empty = no guaranteed tier)",
        "DEV_MEMORY_MIN_GUARANTEED_CEILING", cfg_current, example_defaults,
        proposal=None, validate=validate_size_or_empty,
    )
    values["DEV_MEMORY_LOW"] = walk_key("  MemoryLow (empty = none)", "DEV_MEMORY_LOW", cfg_current, example_defaults, validate=validate_optional_positive_size)
    values["DEV_MEMORY_HIGH"] = walk_key("  MemoryHigh", "DEV_MEMORY_HIGH", cfg_current, example_defaults, proposals["DEV_MEMORY_HIGH"], validate=validate_positive_size)
    values["DEV_MEMORY_MAX"] = walk_key("  MemoryMax", "DEV_MEMORY_MAX", cfg_current, example_defaults, proposals["DEV_MEMORY_MAX"], validate=validate_positive_size)
    values["DEV_CPU_RESERVE_CORES"] = walk_key("  host CPU reserve", "DEV_CPU_RESERVE_CORES", cfg_current, example_defaults, validate_nonneg_int)
    values["DEV_SUBSLICE_CPU_RESERVE_CORES"] = walk_key("  child-slice CPU reserve", "DEV_SUBSLICE_CPU_RESERVE_CORES", cfg_current, example_defaults, validate_nonneg_int)
    values["DEV_CPU_QUOTA"] = walk_key("  CPUQuota (empty = auto-detect from host nproc)", "DEV_CPU_QUOTA", cfg_current, example_defaults, validate=validate_cpu_quota)
    values["DEV_ZSWAP_WRITEBACK"] = walk_yn_key("  MemoryZSwapWriteback", "DEV_ZSWAP_WRITEBACK", cfg_current, example_defaults)
    values["DEV_SWAP_CASCADE_PCT"] = walk_key("  swap cascade percentage", "DEV_SWAP_CASCADE_PCT", cfg_current, example_defaults, validate=validate_pct_1_100)
    values["DEV_SWAP_MAX"] = walk_key("  MemorySwapMax (empty = host-swap cascade)", "DEV_SWAP_MAX", cfg_current, example_defaults, validate=validate_size_or_auto)
    values["DEV_SUBSLICE_IOPS_PCT"] = walk_key("  child IOPS sub-ceiling percentage", "DEV_SUBSLICE_IOPS_PCT", cfg_current, example_defaults, validate=validate_pct_1_100)
    out("  IOWeight: none (dev.slice uses one measured IORead/WriteBandwidthMax and IOPSMax pool for all children).")

    # This sibling is fully described even though its MemoryMin is deliberately
    # not independently editable: two independently chosen values would drift.
    out("\n  dev-memory_min_guaranteed.slice — admitted hard-floor sibling")
    out(f"  MemoryMin: exact mirror of dev.slice ({values['DEV_MEMORY_MIN_GUARANTEED_CEILING'] or 'none'})")
    for field, key in (("MemoryLow", "DEV_MEMORY_MIN_GUARANTEED_LOW"), ("MemoryHigh", "DEV_MEMORY_MIN_GUARANTEED_HIGH"), ("MemoryMax", "DEV_MEMORY_MIN_GUARANTEED_MAX")):
        values[key] = walk_key(f"  {field} (empty = none)", key, cfg_current, example_defaults, validate=validate_optional_positive_size)
    out("  CPUQuota, CPUWeight, IOWeight, MemorySwapMax: inherited/not configured on this admission-only sibling.")

    child_specs = (
        ("dev-interactive.slice", "DEV_INTERACTIVE", ("DEV_INTERACTIVE_MEMORY_MIN", "DEV_INTERACTIVE_MEMORY_LOW", "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_INTERACTIVE_MEMORY_MAX")),
        ("dev-background.slice", "DEV_BACKGROUND", ("DEV_BACKGROUND_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_LOW", "DEV_BACKGROUND_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_MAX")),
        ("dev-gates.slice", "DEV_GATES", ("DEV_GATES_MEMORY_MIN", "DEV_GATES_MEMORY_LOW", "DEV_GATES_MEMORY_HIGH", "DEV_GATES_MEMORY_MAX")),
        ("dev-buildkitd.slice", "DEV_BUILDKITD", ("DEV_BUILDKITD_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_LOW", "DEV_BUILDKITD_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_MAX")),
    )
    for slice_name, prefix, keys in child_specs:
        out(f"\n  {slice_name}")
        for field, key in zip(("MemoryMin", "MemoryLow", "MemoryHigh", "MemoryMax"), keys):
            validator = validate_positive_size if field in ("MemoryHigh", "MemoryMax") else validate_optional_positive_size
            values[key] = walk_key(f"  {field} (empty = none)" if field in ("MemoryMin", "MemoryLow") else f"  {field}", key, cfg_current, example_defaults, proposals.get(key), validator)
        values[f"{prefix}_CPU_WEIGHT"] = walk_key("  CPUWeight", f"{prefix}_CPU_WEIGHT", cfg_current, example_defaults, validate=validate_weight)
        values[f"{prefix}_CPU_QUOTA"] = walk_key("  CPUQuota (empty = auto-detect from host nproc)", f"{prefix}_CPU_QUOTA", cfg_current, example_defaults, validate=validate_cpu_quota)
        values[f"{prefix}_IO_WEIGHT"] = walk_key("  IOWeight", f"{prefix}_IO_WEIGHT", cfg_current, example_defaults, validate=validate_weight)
        values[f"{prefix}_MEMORY_SWAP_MAX"] = walk_key("  MemorySwapMax (empty = swap cascade)", f"{prefix}_MEMORY_SWAP_MAX", cfg_current, example_defaults, validate=validate_size_or_auto)
        values[f"{prefix}_ZSWAP_WRITEBACK"] = walk_yn_key("  MemoryZSwapWriteback", f"{prefix}_ZSWAP_WRITEBACK", cfg_current, example_defaults)
        if prefix in ("DEV_BACKGROUND", "DEV_GATES"):
            values[f"{prefix}_OOM_PRESSURE_LIMIT"] = walk_key("  ManagedOOMMemoryPressureLimit", f"{prefix}_OOM_PRESSURE_LIMIT", cfg_current, example_defaults, validate=validate_systemd_pct_1_100)
        if prefix == "DEV_BUILDKITD":
            values["DEV_BUILDKITD_IMAGE"] = walk_key("  BuildKit image", "DEV_BUILDKITD_IMAGE", cfg_current, example_defaults, validate=validate_nonempty)
            values["BUILDX_ACCIDENTAL_CONTAINER_POLICY"] = walk_key("  accidental Buildx container policy", "BUILDX_ACCIDENTAL_CONTAINER_POLICY", cfg_current, example_defaults, validate=validate_guard_policy)

    _reprompt_memory_constraints(avail_kib, values)
    return values


def step_watcher(
    cfg_current: dict[str, str], example_defaults: dict[str, str]
) -> dict[str, str]:
    """Per-container defaults `mdt-dev-cap-watcher.py` applies to an
    unlabelled docker-*.scope the instant it appears under a governed
    slice -- the answer to "a tight ceiling per consumer, with generous
    swap," since cgroup v2 has no anon-only cap of its own."""
    values: dict[str, str] = {}
    out("\n-- i. Reactive per-container cap watcher (mdt-dev-cap-watcher.py) --")
    out(
        "These bound the blast radius of any ONE unlabelled container ballooning inside a "
        "governed slice, applied the instant its docker-*.scope appears (inotify on "
        "cgroupfs, proven live to fire within the same second) rather than waiting for the "
        "next periodic sweep. A container's own explicit `docker run --memory` always wins; "
        "this only fills in ones that asked for nothing."
    )
    values["WATCHER_PER_CONTAINER_MEMORY_MAX"] = walk_key(
        "Per-container MemoryMax (dev-interactive/dev-background)",
        "WATCHER_PER_CONTAINER_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_size_required,
    )
    out(
        "dev-gates.slice gets its OWN, separate ceiling here -- deliberately not the shared "
        "value above -- sized against what a lane container actually needs (see "
        "'dev-gates: why' in the README): too tight here would re-create the exact headroom "
        "incident dev-gates.slice exists to fix."
    )
    values["WATCHER_PER_CONTAINER_GATES_MEMORY_MAX"] = walk_key(
        "Per-container MemoryMax (dev-gates)",
        "WATCHER_PER_CONTAINER_GATES_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_size_required,
    )
    out(
        "MemoryHigh is derived from whichever Max above applies, as this percentage -- a "
        "soft throttle-into-reclaim step below the hard cap, same pairing the slices "
        "themselves use."
    )
    values["WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT"] = walk_key(
        "Per-container MemoryHigh, % of MemoryMax",
        "WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT",
        cfg_current,
        example_defaults,
        validate=validate_pct_1_100,
    )
    out(
        "Per-container MemorySwapMax is NOT asked here -- it is computed at runtime from "
        "whichever matched slice's own LIVE memory.swap.max, times DEV_SWAP_CASCADE_PCT "
        "(step h above), never re-derived from this file. See "
        "scripts/mdt-dev-cap-watcher.py's read_slice_swap_max_bytes()."
    )
    return values


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
        "--validate-config",
        type=Path,
        default=None,
        help="validate an existing host-setup.env without prompting or writing",
    )
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

    if args.validate_config is not None:
        try:
            validate_host_setup_config(
                args.validate_config,
                args.example,
                args.meminfo_path,
            )
        except (OSError, TemplateError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"OK: strict validation passed for {args.validate_config}")
        return 0

    if not args.example.exists():
        print(f"ERROR: template not found: {args.example}", file=sys.stderr)
        return 1
    example_text = args.example.read_text()
    example_defaults = parse_env_file(example_text)

    cfg_current: dict[str, str] = {}
    if args.output.exists():
        try:
            cfg_current = parse_env_file(args.output.read_text())
            out(f"Found an existing `{args.output}` -- using its values as defaults where they are actually set.")
        except OSError as exc:
            out(f"WARN: could not read the existing `{args.output}`: {exc}")

    try:
        meminfo = parse_meminfo(args.meminfo_path.read_text())
    except OSError as exc:
        out(
            f"WARN: could not read `{args.meminfo_path}`: {exc} -- the memory proposals will "
            "be 0-based, so override them by hand."
        )
        meminfo = {}
    try:
        swaps_text = args.swaps_path.read_text()
    except OSError:
        swaps_text = ""
    swap_kib = read_swap_total_kib(meminfo, swaps_text)

    out("== mdt host-setup wizard ==")
    out(f"template: `{args.example}`")
    out(f"writing:  `{args.output}`")
    out("Press Enter at any prompt to accept the shown default.")

    walked: dict[str, str] = {}

    walked["IO_DEV_PATH"] = step_io_device(cfg_current, example_defaults)

    baseline_env_path = Path(
        cfg_current.get("IO_BASELINE_ENV")
        or example_defaults.get("IO_BASELINE_ENV")
        or "/var/lib/mdt/io-baseline.env"
    )
    baseline_measured_now = step_io_baseline(baseline_env_path, args.io_baseline_script)

    dev_pct, sweep_pct = step_io_cap_pct(cfg_current, example_defaults)
    walked["DEV_IO_CAP_PCT"] = dev_pct
    walked["SWEEP_IO_CAP_PCT"] = sweep_pct

    try:
        walked.update(step_slice_first_resources(meminfo, cfg_current, example_defaults))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cgroup_parent, backstop_max, backstop_swap = step_docker_daemon(cfg_current, example_defaults)
    walked["DOCKER_DAEMON_CGROUP_PARENT"] = cgroup_parent
    walked["DOCKER_SCOPE_BACKSTOP_MEMORY_MAX"] = backstop_max
    walked["DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX"] = backstop_swap

    walked.update(step_watcher(cfg_current, example_defaults))

    text = example_text
    try:
        for key, value in walked.items():
            text = apply_value(text, key, value)
    except TemplateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        write_atomic(args.output, text)
    except (PermissionError, OSError) as exc:
        # A full interactive session (~20 prompts) must not end in a raw
        # traceback just because the operator forgot `sudo` -- this script
        # is documented to run standalone, not only via `install.sh
        # --wizard` (which already checks [ "$(id -u)" = 0 ] before ever
        # reaching here). Every answer given above is lost either way; at
        # least say why in one line instead of a stack trace (adversarial
        # review, mdt-host-setup-wizard).
        print(f"\nERROR: could not write {args.output}: {exc}", file=sys.stderr)
        print("Re-run as root (sudo) -- every answer above was lost, sorry.", file=sys.stderr)
        return 1
    out()
    out(f"wrote `{args.output}`")

    if args.skip_run_offer:
        return 0

    if ask_yn("Run install.sh now to render + apply this config?", default=False):
        cmd = [str(args.install_script)]
        if baseline_measured_now:
            # Deliberately NOT asked, and said out loud rather than silently
            # omitted: an operator scanning back through the transcript
            # should be able to see this was a decision, not a prompt that
            # went missing. The benchmark completed successfully minutes ago
            # in step b; --with-baseline would only repeat the same ~4-minute
            # disk-saturating measurement for an identical result.
            out(
                "Not offering --with-baseline: the IO baseline was just measured above in "
                "this same run, so re-running it during install would repeat a ~4-minute "
                "disk-saturating benchmark for nothing."
            )
        elif ask_yn("Also pass --with-baseline (re-run the IO benchmark during install)?", default=False):
            cmd.append("--with-baseline")
        out(f"running: {' '.join(cmd)}")
        sys.stdout.flush()  # see step_io_baseline() for why
        subprocess.run(cmd, check=False)
    else:
        out(f"Next step: sudo {args.install_script}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
