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
#     identity check, and its own main() (invoked as a subprocess) to actually
#     run the benchmark — never duplicates the identity rules or quiet-window
#     warning.
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
_SAFE_HOST_PATH_RE = re.compile(
    r"/(?:[A-Za-z0-9._+@%=:,-]+(?:/[A-Za-z0-9._+@%=:,-]+)*)?"
)
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
        and "NO_COLOR" not in os.environ
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
    # A piped/redirected transcript does not echo the answer typed into
    # input(), so without this explicit newline the next explanation is
    # glued to the prompt.  A real TTY already echoes the user's Enter and
    # must not receive a second newline.
    echoed_by_terminal = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if len(text) <= term_width():
        try:
            answer = input(text)
        except EOFError:
            if not echoed_by_terminal:
                print()
            raise
        if not echoed_by_terminal:
            print()
        return answer
    lead, sep, tail = text.rpartition("[")
    if not sep:
        out(text)
        try:
            answer = input("> ")
        except EOFError:
            if not echoed_by_terminal:
                print()
            raise
        if not echoed_by_terminal:
            print()
        return answer
    out(lead.strip())
    try:
        answer = input(f"[{tail}")
    except EOFError:
        if not echoed_by_terminal:
            print()
        raise
    if not echoed_by_terminal:
        print()
    return answer


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
    """Offer readable caps based on physical RAM, not transient free RAM.

    ``MemAvailable`` changes with whatever happens to be running while the
    wizard is opened. It is shown as context, but using it as a hard sizing
    input would make a busy host propose unusably small limits and would make
    the same hardware produce different policy every minute. The proportions
    below are the host policy: they leave overlap between tiers, while the
    parent aggregate checks prevent the configured hierarchy from exceeding
    its own parent.
    """
    def frac(percent: int) -> str:
        return kib_to_size_str(max(1, total_kib * percent // 100))

    proposals = {
        "DEV_MEMORY_HIGH": kib_to_size_str(max(1, total_kib * 75 // 100)),
        "DEV_MEMORY_MAX": kib_to_size_str(total_kib),
        "DEV_INTERACTIVE_MEMORY_LOW": frac(15),
        "DEV_INTERACTIVE_MEMORY_HIGH": frac(20),
        "DEV_INTERACTIVE_MEMORY_MAX": frac(32),
        "DEV_BACKGROUND_MEMORY_LOW": frac(15),
        "DEV_BACKGROUND_MEMORY_HIGH": frac(32),
        "DEV_BACKGROUND_MEMORY_MAX": frac(50),
        "DEV_GATES_MEMORY_LOW": frac(5),
        "DEV_GATES_MEMORY_HIGH": frac(5),
        "DEV_GATES_MEMORY_MAX": frac(10),
        "DEV_BUILDKITD_MEMORY_LOW": frac(10),
        "DEV_BUILDKITD_MEMORY_HIGH": frac(10),
        "DEV_BUILDKITD_MEMORY_MAX": frac(12),
    }
    # Readable binary rounding must not make the child High proposals exceed
    # their parent on a very small fixture host.  Normal hosts stay at the
    # stated 75%-of-MemTotal root policy.
    child_high_kib = sum(
        parse_size_to_kib(proposals[f"{prefix}_MEMORY_HIGH"])
        for prefix in ("DEV_INTERACTIVE", "DEV_BACKGROUND", "DEV_GATES", "DEV_BUILDKITD")
    )
    root_high_kib = parse_size_to_kib(proposals["DEV_MEMORY_HIGH"])
    if child_high_kib > root_high_kib:
        proposals["DEV_MEMORY_HIGH"] = f"{child_high_kib}K"
    return proposals


def validate_positive_size(value: str) -> str | None:
    """A configured memory limit must be positive; ``0`` is a hard zero,
    not an unset value. Unconfigured fields are represented explicitly by the
    slice flow and are never passed to this validator."""
    error = validate_size_required(value)
    if error:
        return error
    if parse_size_to_kib(value) <= 0:
        return "a memory limit must be greater than zero; do not use 0 or 'no'. For optional fields, press Enter for the documented empty/none state"
    return None


def validate_optional_positive_size(value: str) -> str | None:
    if not value:
        return None
    return validate_positive_size(value)


def validate_absolute_path(value: str) -> str | None:
    """Validate a path used by a host service, not by the wizard itself."""
    if not value:
        return "an absolute path is required (for example '/var/lib/mdt/io-baseline.env')"
    if not value.startswith("/"):
        return f"{value!r} is not an absolute path"
    if any(character.isspace() for character in value):
        return f"{value!r} contains whitespace; choose a path without spaces because the host env file is shell-sourced"
    if not _SAFE_HOST_PATH_RE.fullmatch(value):
        return (
            f"{value!r} contains shell-special characters; use an absolute path "
            "made of letters, digits, '.', '_', '+', '@', '%', ':', '=', ',' "
            "and '-' because the host env file and benchmark helper are shell-sourced"
        )
    return None


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

    del avail_kib  # MemAvailable is advisory context, never a policy limit.

    def field_values(field: str) -> list[tuple[str, int]]:
        return [
            (slice_name, parse_size_to_kib(values.get(keys[field_index[field]], "")))
            for slice_name, keys in MEMORY_CHILD_SPECS
            if values.get(keys[field_index[field]], "")
        ]

    def sum_field(field: str) -> int:
        return sum(value for _, value in field_values(field))

    def detail(field: str) -> str:
        return ", ".join(f"{name}={kib_to_size_str(value)}" for name, value in field_values(field))

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
    if root_high and highs > root_high:
        return (
            f"sibling MemoryHigh values ({kib_to_size_str(highs)} combined: {detail('MEMORY_HIGH')}) exceed "
            f"dev.slice MemoryHigh ({kib_to_size_str(root_high)}); increase the parent "
            "or reduce a child"
        )
    if lows and root_low and lows > root_low:
        return (
            f"sibling MemoryLow values ({kib_to_size_str(lows)} combined: {detail('MEMORY_LOW')}) exceed "
            f"dev.slice MemoryLow ({kib_to_size_str(root_low)}); increase the parent "
            "or reduce a child"
        )
    if root_max and child_maximum > root_max:
        return (
            f"a child MemoryMax ({kib_to_size_str(child_maximum)}; {detail('MEMORY_MAX')}) exceeds dev.slice "
            f"MemoryMax ({kib_to_size_str(root_max)}); increase the parent or reduce "
            "that child"
        )
    if minimum and child_minimum > minimum:
        return (
            f"sibling MemoryMin values ({kib_to_size_str(child_minimum)} combined: {detail('MEMORY_MIN')}) "
            f"exceed dev.slice MemoryMin ({kib_to_size_str(minimum)}); increase the "
            "parent or reduce a child"
        )
    return None


def _config_value_validators() -> dict[str, Validator]:
    """Return the non-interactive equivalents of the wizard's field checks."""
    validators: dict[str, Validator] = {
        "IO_DEV_PATH": validate_io_dev_path,
        "DEV_STATIC_RIOPS": validate_positive_int,
        "DEV_STATIC_WIOPS": validate_positive_int,
        "DEV_STATIC_RBW": validate_positive_size,
        "DEV_STATIC_WBW": validate_positive_size,
        "DEV_CPU_RESERVE_CORES": validate_nonneg_int,
        "DEV_SUBSLICE_CPU_RESERVE_CORES": validate_nonneg_int,
        "DEV_CPU_QUOTA": validate_cpu_quota,
        "DEV_SWAP_CASCADE_PCT": validate_pct_1_100,
        "DEV_SWAP_MAX": validate_size_or_auto,
        "DEV_SUBSLICE_IOPS_PCT": validate_pct_1_100,
        "DEV_ZSWAP_WRITEBACK": validate_yes_no,
        "DEV_BUILDKITD_IMAGE": validate_nonempty,
        "DEV_BUILDKITD_MAX_PARALLELISM": validate_positive_int,
        "BUILDX_ACCIDENTAL_CONTAINER_POLICY": validate_guard_policy,
        "DOCKER_DAEMON_CGROUP_PARENT": validate_cgroup_parent,
        "DOCKER_SCOPE_BACKSTOP_MEMORY_MAX": validate_positive_size,
        "DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX": validate_positive_size,
        "WATCHER_PER_CONTAINER_MEMORY_MAX": validate_positive_size,
        "WATCHER_PER_CONTAINER_GATES_MEMORY_MAX": validate_positive_size,
        "WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT": validate_pct_1_100,
        "CGROUP2_FLAGS": validate_cgroup2_flags,
        "DEV_IO_CAP_PCT": validate_cap_pct,
        "WATCHER_IO_CAP_PCT": validate_cap_pct,
        "WATCHER_TESTRUNNER_IMAGE_PATTERNS": validate_nonempty,
        "WATCHER_BUILDKIT_NAME_PATTERNS": validate_nonempty,
        "WATCHER_DEVCONTAINER_NAME_PATTERNS": validate_nonempty,
        "WATCHER_INTERVAL": validate_nonempty,
        "IO_BASELINE_ENV": validate_absolute_path,
        "IO_BASELINE_TESTFILE": validate_absolute_path,
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
        validators[key] = validate_optional_positive_size
    validators["DEV_MEMORY_LOW"] = validate_optional_positive_size
    validators["DEV_MEMORY_MIN_GUARANTEED_CEILING"] = validate_optional_positive_size
    for _, keys in MEMORY_CHILD_SPECS:
        for index, key in enumerate(keys):
            if key == "DEV_MEMORY_MIN_GUARANTEED_CEILING":
                continue
            if index in (0, 1):
                validators[key] = validate_optional_positive_size
            elif index in (2, 3):
                validators[key] = validate_optional_positive_size
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
    total_kib: int, tier_high_values: dict[str, str]
) -> tuple[int, int, str]:
    """Pure function backing Work step 4e's 'suggestion grounded in THIS
    host's own numbers'. Returns (leftover_kib, suggestion_kib, formula_text).

    Sums every MemoryHigh figure passed in `tier_high_values` (the realistic
    'expected concurrent' load — MemoryMax already assumes swap is absorbing
    overflow, so summing Max figures would understate what's actually free
    day to day), subtracts that from MemTotal, and proposes a SMALL,
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

    >>> total_kib = 8 * 1024 * 1024  # 8 GiB
    >>> before = {"DEV_INTERACTIVE_MEMORY_HIGH": "2G", "DEV_BACKGROUND_MEMORY_HIGH": "2G", "DEV_BUILDKITD_MEMORY_HIGH": "1G"}
    >>> propose_memory_min_guaranteed_suggestion(total_kib, before)
    (3145728, 157286, "MemTotal (8G) - 3 tiers' MemoryHigh figures (5G combined) = 3G left over; 5% of that leftover = 150M")
    >>> after = dict(before, DEV_GATES_MEMORY_HIGH="4G")
    >>> propose_memory_min_guaranteed_suggestion(total_kib, after)
    (0, 0, "MemTotal (8G) - 4 tiers' MemoryHigh figures (9G combined) = 0 left over; 5% of that leftover = 0")
    """
    earmarked_kib = sum(parse_size_to_kib(v) for v in tier_high_values.values())
    leftover_kib = max(0, total_kib - earmarked_kib)
    suggestion_kib = leftover_kib * 5 // 100
    # "N tiers'" rather than a hardcoded "three" (round-1 review S2): the
    # caller passes step d's three PLUS dev-gates.slice's own fixed figure
    # as of RG-55 P8, and a hardcoded count would go stale again the next
    # time a tier is added.
    formula = (
        f"MemTotal ({kib_to_size_str(total_kib)}) - {len(tier_high_values)} "
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
        # findmnt reports the current mount namespace. In a devcontainer this
        # is commonly ``overlay`` (or another filesystem/network source), not
        # the host block-device node accepted by systemd's IO*Max=. Do not
        # offer such a value, and do not stat it: the configured path may
        # belong to a different host mount namespace.
        if result.returncode == 0 and source.startswith("/dev/") and source != "/dev/":
            return source
    return ""


def discover_nproc(runner=subprocess.run) -> int | None:
    """Read the same host CPU count source that install.sh uses."""
    try:
        result = runner(
            ["nproc"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False,
        )
    except FileNotFoundError:
        return None
    raw = (result.stdout or "").strip()
    if result.returncode != 0 or not raw.isdigit() or int(raw) <= 0:
        return None
    return int(raw)


def host_context_error(root: Path | None = None) -> str | None:
    """Reject host policy changes from inside a container.

    UID 0 inside a devcontainer is still not host root: it cannot safely
    operate the host's PID 1/systemd, /etc, or cgroup tree. The explicit
    container markers catch the common case; the PID 1/systemd check catches
    runtimes that do not provide those markers. This deliberately does not
    try to infer host access from a Docker socket bind, because that socket
    controls Docker but does not make the container's filesystem or systemd
    namespace the host's.
    """
    root = Path("/") if root is None else root
    if (
        (root / ".dockerenv").exists()
        or (root / "run/.containerenv").exists()
        or (root == Path("/") and os.environ.get("container"))
    ):
        return (
            "host setup must run from a host shell, not inside a devcontainer or "
            "other container; UID 0 in a container is not host root and cannot "
            "apply the host's systemd/cgroup policy"
        )
    try:
        pid1 = (root / "proc/1/comm").read_text(encoding="utf-8").strip()
    except OSError:
        pid1 = ""
    if pid1 != "systemd" or not (root / "run/systemd/system").is_dir():
        return (
            "host setup requires systemd as PID 1 and /run/systemd/system; "
            "run it on the Docker host, outside the devcontainer"
        )
    return None


def resolve_default(
    key: str,
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
    proposal: str | None,
) -> str:
    """Resolve one prompt's default without hiding an existing choice.

    The precedence is source-based: an existing output file wins, including
    an intentional empty value; a live host proposal is used only when the
    key is absent; and the example is the final fallback. A fresh install has
    no existing output, so its blank example fields can receive proposals.
    During an upgrade, pressing Enter preserves an existing ``KEY=`` choice.

    An empty value is not silently converted into a discovered device, a
    memory proposal, or an auto-detected quota. To clear an optional memory
    value in an interactive run, type ``-``; ``none`` remains accepted for
    compatibility with earlier wizard sessions.
    """
    if key in cfg_current:
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
    """Validate a required positive memory size."""
    if not value:
        return f"a size is required here ({_SIZE_EXAMPLES}); leaving it empty would drop this setting from the rendered unit entirely"
    if not is_size_string(value):
        return f"{value!r} is not a systemd-style size string ({_SIZE_EXAMPLES})"
    if parse_size_to_kib(value) <= 0:
        return "a required memory limit must be greater than zero; do not use 0 or 'no'"
    return None


def validate_size_or_empty(value: str) -> str | None:
    """Validate a size or an explicitly unset optional field."""
    if not value:
        return None
    if not is_size_string(value):
        return f"{value!r} is not a systemd-style size string ({_SIZE_EXAMPLES}); type '-' for the documented empty state"
    return None


def validate_cap_pct(value: str) -> str | None:
    """DEV_IO_CAP_PCT / WATCHER_IO_CAP_PCT.

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
    if pct < 1 or pct > 99:
        return f"{pct} is outside 1-99; 100% or more of the measured ceiling leaves no safety headroom, and 0% would stall the tier outright"
    if pct < 60 or pct > 80:
        out(
            f"WARN: {pct}% is outside the recommended 60-80 band explained above "
            f"-- accepted, but re-read that reasoning before keeping it."
        )
    return None


def validate_io_dev_path(value: str) -> str | None:
    """IO_DEV_PATH. Empty is valid and documented (static IO caps omitted).

    A non-empty answer must name a host block-device node under ``/dev``. This
    is a shape check rather than a filesystem probe so a missing/mounted path
    produces a clear host-side error later instead of a namespace-dependent
    answer. In particular, ``overlay`` is a container filesystem source, not a
    usable IO*Max device node."""
    if not value:
        return None
    if not value.startswith("/dev/") or value == "/dev/":
        return f"{value!r} is not a /dev block-device node -- give a path such as /dev/sda or /dev/mapper/vg-root (or leave it empty/auto to omit static caps if the host device cannot be discovered)"
    if not _SAFE_HOST_PATH_RE.fullmatch(value):
        return f"{value!r} contains shell-special characters; give a simple host device path such as /dev/sda or /dev/mapper/vg-root"
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
    not a valid daemon-wide default). The data-only validator checks syntax;
    install.sh verifies LoadState=loaded and a non-empty FragmentPath after
    rendering the units and refuses to start governed services for an unknown
    slice. This matters because Docker can otherwise accept a typo and fail
    open into an unbounded transient scope."""
    if not value:
        return "a cgroup parent is required (e.g. 'dev-background.slice')"
    if not value.endswith(".slice"):
        out(f"WARN: {value!r} does not end in '.slice' -- install.sh will reject it before starting governed services.")
    return None


# ─── I/O helpers ─────────────────────────────────────────────────────────


def ask(
    label: str,
    default: str,
    validate: Validator | None = None,
    *,
    empty_token: str | None = None,
    empty_tokens: tuple[str, ...] = (),
    default_source: str | None = None,
) -> str:
    """Prompt for one value; Enter accepts `default` unchanged.

    With a `validate` callable, an unacceptable answer is reported and asked
    for again rather than silently accepted or coerced. If ``empty_token`` is
    supplied, typing it explicitly clears the value; a bare Enter still
    accepts the shown default. ``default_source`` makes the provenance
    visible instead of making an old value look like a fresh proposal.

    A closed or exhausted stdin (canned-answer runs, Ctrl-D) still accepts a
    valid default, exactly as a bare Enter does. If that default is invalid,
    the wizard stops instead of writing a known-invalid file: re-prompting an
    exhausted stdin would spin forever, and accepting the value would turn an
    input-abort into a misleading successful write.
    """
    shown_value = default if default else "<empty>"
    shown = f"{default_source}: {shown_value}" if default_source else shown_value
    while True:
        at_eof = False
        try:
            raw = _prompt(f"{label} [{shown}]: ")
        except EOFError:
            print()
            raw = ""
            at_eof = True
        raw = raw.strip()
        tokens = tuple(token.lower() for token in empty_tokens)
        if empty_token:
            tokens += (empty_token.lower(),)
        value = "" if raw.lower() in tokens else (raw if raw else default)
        if validate is None:
            return value
        error = validate(value)
        if error is None:
            return value
        if at_eof:
            raise ValueError(
                f"{error}; stdin is exhausted, so the invalid value cannot be corrected"
            )
        out(f"invalid: {error}", indent="  ")


def ask_yn(question: str, default: bool, *, default_source: str | None = None) -> bool:
    default_word = "yes" if default else "no"
    hint = f"Y/n; default {default_word}" if default else f"y/N; default {default_word}"
    if default_source:
        hint += f"; {default_source}"
    while True:
        try:
            raw = _prompt(f"{question} [{hint}]: ").strip().lower()
        except EOFError:
            print()
            raw = ""
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        out("Please answer `y`/`yes` or `n`/`no`; Enter keeps the shown default.", indent="  ")


def walk_key(
    label: str,
    key: str,
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
    proposal: str | None = None,
    validate: Validator | None = None,
    *,
    allow_empty_token: bool = False,
    empty_token: str | None = None,
) -> str:
    default = resolve_default(key, cfg_current, example_defaults, proposal)
    if key in cfg_current:
        source = "existing"
    elif proposal is not None:
        source = "derived"
    else:
        source = "example"
    return ask(
        f"{label} ({key})",
        default,
        validate=validate,
        empty_token=empty_token,
        empty_tokens=("-", "none") if allow_empty_token else (),
        default_source=source,
    )


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
    default_str = resolve_default(key, cfg_current, example_defaults, None).strip().lower()
    if default_str not in ("yes", "no"):
        out(
            f"WARN: {key} has invalid existing value {default_str!r}; using the example "
            "answer as the starting point. Choose yes or no explicitly."
        )
        default_str = example_defaults.get(key, "yes").strip().lower()
        source = "example (existing value invalid)"
    else:
        source = "existing" if key in cfg_current else "example"
    default_bool = default_str == "yes"
    return "yes" if ask_yn(f"{label} ({key})", default_bool, default_source=source) else "no"


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
    than reimplementing the identity rules inline. Safe to exec: the
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


def check_baseline_freshness(
    baseline_env_path: Path, testfile_path: Path, io_baseline_script: Path
) -> str:
    """Return fresh, stale or missing without running fio."""
    if not baseline_env_path.exists():
        return "missing"
    module = _load_io_baseline_module(io_baseline_script)
    if module is not None and hasattr(module, "cache_is_fresh"):
        try:
            fresh = module.cache_is_fresh(baseline_env_path, testfile_path)
            valid = not hasattr(module, "cache_is_valid") or module.cache_is_valid(baseline_env_path)
            return "fresh" if fresh and valid else "stale"
        except OSError:
            return "stale"
    return "stale"


# ─── interactive sections (Work step 4a-4i) ────────────────────────────────


def step_io_device(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> str:
    out("\n-- storage baseline: what this setup measures --")
    out(
        "MDT measures the Docker data disk with the official Linux kernel "
        "io.cost coefficient matrix: sequential and random read/write "
        "throughput. It does not enable io.cost; it uses those measured "
        "ceilings to populate the existing io.max controls."
    )
    out(
        "The benchmark always writes a persistent file, never the raw block "
        "device. That file must be on the same filesystem/device as "
        "/var/lib/docker, because the runtime caps are intended for that "
        "disk. The file remains after the run and is part of the cache identity."
    )
    out(
        "There is no arbitrary 30-day expiry. A cache is reusable only while "
        "the test-file path, filesystem identity, block-device topology, size, "
        "model/rotation facts, and stable device identifier still match. "
        "Changing the disk or moving Docker data requires a new measurement."
    )
    out("\n-- a. IO device (IO_DEV_PATH) --")
    out(
        "This selects the block-device node that install.sh uses for the STATIC "
        "IOPS/bandwidth caps. Those caps are written as "
        "`dev.slice`'s `IOReadBandwidthMax`, `IOWriteBandwidthMax`, `IOReadIOPSMax` and "
        "`IOWriteIOPSMax`. Those statics are the boot-window fallback: they are what is "
        "actually in force from boot onwards, and they stay in force indefinitely until "
        "a successful baseline exists AND `mdt-host-slices.service` applies it. Only then "
        "does the runtime service replace them with a percentage of the measured ceilings."
    )
    out()
    out(
        "Leaving this empty means auto-discover at install and runtime; it does not disable "
        "static caps if discovery succeeds. Type `auto` for the same explicit choice. If "
        "discovery finds no device, static IO caps are omitted, while memory and CPU "
        "governance still apply. The runtime watcher independently discovers a device "
        "and may still apply measured or fallback per-container caps; there is no global "
        "IO-governance off switch in this setting."
    )
    out()
    out(
        "The value below is auto-discovered exactly the way install.sh does it at render "
        "time: `findmnt` against `/var/lib/docker`, falling back to `/`. This wizard must "
        "run from the Docker host being configured. It checks the answer's `/dev/...` "
        "shape but does not use a local `stat`; if the host device is not visible, "
        "confirm that you left the devcontainer and then enter the host's device path "
        "explicitly."
    )
    discovered = discover_io_dev_path_from_findmnt()
    if discovered:
        out(f"auto-discovered on this host: `{discovered}`")
    else:
        out(
            "auto-discovery found no usable host block-device node (findmnt may have failed, "
            "returned a container filesystem such as `overlay`, or returned no source). "
            "On a supported host this should not be `overlay`; if it is, stop and leave "
            "the devcontainer because host setup is being run in the wrong context. "
            "Otherwise, at an auto value the installer omits static IO caps and the "
            "runtime watcher skips IO caps until its own discovery finds a host device; "
            "review the host mount or enter its `/dev/...` device node explicitly."
        )
    return walk_key(
        "Device node for Docker data (/dev/... path; auto = discover)",
        "IO_DEV_PATH",
        cfg_current,
        example_defaults,
        proposal=discovered or None,
        validate=validate_io_dev_path,
        empty_token="auto",
    )


def step_io_baseline(
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
    io_baseline_script: Path,
) -> tuple[str, str, bool]:
    """Offer to measure this disk's real IO ceilings.

    Returns the selected cache path, target path, and True ONLY when this call
    actually invoked the adapter and it exited 0. Every other path returns
    the selected paths and False. main() uses that to decide whether the
    `--with-baseline` question at the end is worth asking; re-running a
    just-completed disk-saturating benchmark would buy nothing.
    """
    out("\n-- b. IO baseline --")
    out(
        "The cache stores the six raw io.cost coefficients plus the four "
        "compatibility ceilings used by MDT. io.max has one IOPS value per "
        "direction, so MDT uses the lower sequential/random IOPS value; it uses "
        "sequential bytes per second for bandwidth. The runtime service reads "
        "this same file; it is not a report held only in memory."
    )
    out(
        "Enter the cache path and then the persistent benchmark-file path. "
        "Enter accepts the shown default; both paths must be absolute. "
        "The adapter reuses an existing target only when the cache is current. "
        "To deliberately remeasure an unchanged target, run the installed "
        "adapter later with `--force`; that bypasses reuse, not the device "
        "identity checks."
    )
    out(
        "`IO_BASELINE_ENV` is the persistent cache file: the runtime cap service "
        "reads its measured ceilings from it. `IO_BASELINE_TESTFILE` is the "
        "persistent file that the benchmark reads and writes; it stays on disk "
        "so the cache can prove which filesystem and device were measured. "
        "Changing either path changes the identity check and normally requires "
        "a new baseline."
    )
    baseline_env = walk_key(
        "Baseline cache file (absolute path)",
        "IO_BASELINE_ENV",
        cfg_current,
        example_defaults,
        validate=validate_absolute_path,
    )
    baseline_env_path = Path(baseline_env)
    testfile_default = os.environ.get(
        "IO_BASELINE_TESTFILE",
        example_defaults.get("IO_BASELINE_TESTFILE", "/var/lib/mdt/iocost-coef-fio.testfile"),
    )
    testfile_text = walk_key(
        "Benchmark target file (persistent absolute path; same device as Docker data)",
        "IO_BASELINE_TESTFILE",
        cfg_current,
        example_defaults,
        proposal=testfile_default,
        validate=validate_absolute_path,
    )
    testfile = Path(testfile_text)
    out(f"The cache will be read/written at `{baseline_env_path}`.")
    out(f"The benchmark target remains at `{testfile}` so identity can be checked later.")
    status = check_baseline_freshness(baseline_env_path, testfile, io_baseline_script)
    if status == "fresh":
        out("This cache is current: its recorded device and persistent target still match this host. Leaving it as-is.")
        return baseline_env, testfile_text, False
    out("No current cache is available: it is missing, incomplete, or its device/target identity no longer matches.")
    if not io_baseline_script.exists():
        out(
            f"WARN: `{io_baseline_script}` not found -- skipping this step. Run it manually "
            "once it is installed; the static caps hold until you do."
        )
        return baseline_env, testfile_text, False
    if shutil.which("fio") is None or shutil.which("pv") is None:
        out(
            "The official generator needs `fio` and `pv`, which are not both installed. "
            "On a fresh host, install.sh installs them after this candidate is validated; "
            f"run the benchmark later with `sudo {io_baseline_script}` in a quiet window."
        )
        return baseline_env, testfile_text, False
    run_now = ask_yn(
        "Run the io.cost baseline now? It runs six fio measurements for about 12 minutes minimum and SATURATES THE DISK; use a quiet maintenance window",
        default=False,
    )
    if not run_now:
        out(f"Skipping -- run it later with: sudo {io_baseline_script} --output `{baseline_env}` --testfile `{testfile}`")
        return baseline_env, testfile_text, False
    # Flush first: our own stdout is block-buffered whenever it is not a
    # terminal (a piped/redirected run, i.e. every recorded transcript), while
    # the child writes to the same fd immediately -- without this the child's
    # output lands ABOVE the lines that introduced it.
    sys.stdout.flush()
    child_env = os.environ.copy()
    child_env["IO_BASELINE_ENV"] = baseline_env
    child_env["IO_BASELINE_TESTFILE"] = testfile_text
    result = subprocess.run(
        [sys.executable, str(io_baseline_script), "--output", baseline_env, "--testfile", testfile_text],
        env=child_env, check=False
    )
    if result.returncode != 0:
        out(
            f"WARN: the IO baseline run exited {result.returncode} -- the static caps remain "
            "in force until it succeeds."
        )
        return baseline_env, testfile_text, False
    return baseline_env, testfile_text, True


def step_io_cap_pct(cfg_current: dict[str, str], example_defaults: dict[str, str]) -> tuple[str, str]:
    out("\n-- c. IO cap percentages --")
    out(
        "These percentages are multiplied by each measured baseline ceiling: "
        "`cap = floor(measured value × percentage / 100)`. The four measured values are "
        "read IOPS, write IOPS, read bandwidth, and write bandwidth from step b's "
        "`IO_BASELINE_ENV` file. Both percentages belong in a 60-80% band, for two "
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
        "treat each other. The service applies the resulting four `io.max` values to the "
        "parent cgroup at boot and on the periodic cap run.",
        indent="  ",
        hang="    ",
    )
    out(
        "`WATCHER_IO_CAP_PCT` -- the per-container ceiling, applied to each container "
        "individually. The Docker-events watcher applies it when a matching test-runner, "
        "Buildx worker, or devcontainer starts; the periodic sweep is the backstop. This "
        "protects tier members from EACH OTHER. Buildx workers created by the accidental "
        "container-driver path are not reliably placed in `dev.slice`, so their matching "
        "container scope needs this direct cap; the normal host-managed `mdt-buildkitd` "
        "service is governed by `dev-buildkitd.slice` itself. This is a percentage "
        "of the measured device ceilings, NOT a percentage of dev.slice's already "
        "reduced cap; a matched container is still bounded by its parent when nested there.",
        indent="  ",
        hang="    ",
    )
    out(
        "These are rate caps, not weights and not a measurement of each container. "
        "`CPUWeight` and `IOWeight` are separate relative-share settings asked inside "
        "each slice; a weight matters only when peers contend, while `io.max` remains an "
        "absolute ceiling. If no complete baseline or device is available, the runtime "
        "code uses its documented static fallback instead of pretending a percentage was "
        "applied to measured data."
    )
    dev_pct = walk_key(
        "Whole-estate IO cap %",
        "DEV_IO_CAP_PCT",
        cfg_current,
        example_defaults,
        validate=validate_cap_pct,
    )
    sweep_pct = walk_key(
        "Per-container IO cap (percentage of measured device ceiling)",
        "WATCHER_IO_CAP_PCT",
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
    out("\n-- f. Docker daemon.json keys this tool owns --")
    out(
        "This sets Docker's daemon-wide DEFAULT placement (D-G7): "
        "`DOCKER_DAEMON_CGROUP_PARENT` is the slice "
        "any container lands in when it names no `--cgroup-parent` of its own. install.sh "
        "MERGES this single key into `/etc/docker/daemon.json` -- it never overwrites that "
        "file and never touches any other key in it. The value must name the intended "
        "already-installed `.slice`; a typo can make Docker place a container in an "
        "unbounded transient scope, so verify the resulting placement with "
        "`mdt-host-check.sh`. The daemon needs a full RESTART, not a reload, before a "
        "changed default takes effect; the wizard never restarts it by itself."
    )
    cgroup_parent = walk_key(
        "Default cgroup-parent (must be a loaded .slice)",
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
        "rare mistake. Its `MemoryMax` is a RAM limit and its `MemorySwapMax` is a separate "
        "swap limit; both are required positive sizes and should be comfortably above a "
        "legitimate single container's normal peak."
    )
    backstop_max = walk_key(
        "Backstop MemoryMax",
        "DOCKER_SCOPE_BACKSTOP_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_positive_size,
    )
    backstop_swap = walk_key(
        "Backstop MemorySwapMax",
        "DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX",
        cfg_current,
        example_defaults,
        validate=validate_positive_size,
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


def _prompt_shared_memory_min(values: dict[str, str]) -> None:
    """Confirm the guaranteed sibling's ``MemoryMin`` without adding a key.

    ``DEV_MEMORY_MIN_GUARANTEED_CEILING`` is intentionally both the parent
    ``dev.slice`` value and the sibling's value.  This prompt is a second
    operator-facing confirmation in the sibling's complete field sequence,
    not a second configuration field: a different answer updates this one
    shared value before the remaining sibling fields are collected.
    """
    key = "DEV_MEMORY_MIN_GUARANTEED_CEILING"
    authoritative = values[key]
    values[key] = ask(
        f"  MemoryMin ({key}; shared with dev.slice; Enter confirms, a different value updates both)",
        authoritative,
        validate=validate_size_or_empty,
        empty_token="none",
        empty_tokens=("-", "none"),
        default_source="confirmed above",
    )


def _reprompt_memory_constraints(
    avail_kib: int,
    values: dict[str, str],
) -> None:
    """Repair a hierarchy interactively without losing the whole session."""
    def correction(key: str) -> None:
        before = values.get(key, "")
        validator = validate_optional_positive_size
        while True:
            shown = before or "<empty>"
            try:
                raw = _prompt(
                    f"  corrected value for {key} [{shown}] "
                    "(type a value, '-' to unset, q to abort): "
                ).strip()
            except EOFError as exc:
                raise ValueError(
                    "stdin ended while a memory correction was required; "
                    "the candidate was not written"
                ) from exc
            if raw.lower() in ("q", "quit", "abort"):
                raise ValueError("memory correction aborted; the candidate was not written")
            if not raw:
                out("  Enter keeps the invalid value; type a smaller/larger value or '-' to unset.")
                continue
            value = "" if raw.lower() in ("-", "none") else raw
            error = validator(value)
            if error:
                out(f"  invalid: {error}")
                continue
            values[key] = value
            return

    for _ in range(64):
        errors = memory_relationship_errors(values)
        if errors:
            out("invalid memory hierarchy:", indent="  ")
            for _, message in errors:
                out(message, indent="    ")
            correction(errors[0][0])
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
        if "MemoryLow" in aggregate:
            key = "DEV_MEMORY_LOW"
        elif root_high and child_high_total > root_high:
            key = "DEV_MEMORY_HIGH"
        elif parse_size_to_kib(values.get("DEV_MEMORY_MAX", "")) and any(parse_size_to_kib(values.get(key, "")) > parse_size_to_kib(values["DEV_MEMORY_MAX"]) for key in ("DEV_INTERACTIVE_MEMORY_MAX", "DEV_BACKGROUND_MEMORY_MAX", "DEV_GATES_MEMORY_MAX", "DEV_BUILDKITD_MEMORY_MAX")):
            key = "DEV_MEMORY_MAX"
        elif minimum and sum(parse_size_to_kib(values.get(key, "")) for key in ("DEV_INTERACTIVE_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_MIN", "DEV_GATES_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_MIN")) > minimum:
            key = "DEV_MEMORY_MIN_GUARANTEED_CEILING"
        else:
            key = "DEV_MEMORY_HIGH"
        correction(key)
    raise ValueError("memory corrections did not converge after 64 prompts; the candidate was not written")


def _legacy_step_slice_first_resources(
    meminfo: dict[str, int],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> dict[str, str]:
    """Walk each governed slice as one unit of policy, from parent to leaves."""
    total_kib = meminfo.get("MemTotal", 0)
    avail_kib = meminfo.get("MemAvailable", 0)
    if total_kib <= 0 or avail_kib <= 0:
        raise ValueError(
            "positive MemTotal and MemAvailable are required; refusing to invent memory limits"
        )
    proposals = propose_memory_tiers(total_kib, avail_kib)
    values: dict[str, str] = {}
    host_nproc = discover_nproc()
    if host_nproc is None:
        raise ValueError(
            "nproc did not return a positive CPU count; install.sh needs this "
            "fact even when quotas are explicit, so fix CPU-count discovery before "
            "running the wizard"
        )

    out("\n-- d. Governed slices (slice-first) --")
    out(
        f"Live host facts: `MemTotal={kib_to_size_str(total_kib)}`, "
        f"`MemAvailable={kib_to_size_str(avail_kib)}`. Suggestions use the "
        "current `MemAvailable` from `/proc/meminfo`; every entered size is "
        "converted to KiB before hierarchy and aggregate checks. These are "
        "starting points, not facts about how much a slice will actually use."
    )
    out(
        "First configure the shared `dev.slice` policy, then complete the guaranteed "
        "sibling and each child slice before moving on. Shared parent controls are "
        "asked once; a child CPU quota, IOWeight, swap setting, and zswap policy are "
        "shown inside that child's block. Ordinary slices do not receive a fabricated "
        "MemoryMin, the root uses absolute measured IO caps rather than IOWeight, and "
        "the guaranteed sibling's MemoryMin is the same key as the parent's."
    )
    out(
        "Memory meanings: `MemoryMin` is hard reclaim protection, `MemoryLow` is "
        "best-effort protection, `MemoryHigh` is a reclaim/throttling threshold, and "
        "`MemoryMax` is a hard RAM ceiling. `MemoryMax` does NOT include swap; "
        "`MemorySwapMax` is the separate swap-only ceiling. For optional memory fields, "
        "press Enter to keep the shown value or type `none` to remove the directive. "
        "For required `MemoryHigh`/`MemoryMax`, a positive size is mandatory: neither "
        "empty, `0`, nor `no` means unlimited. Sizes use binary `K/M/G` units; a bare "
        "number is bytes."
    )
    out(
        "The host-scaled starting values are: `dev.slice` MemoryHigh = current "
        "MemAvailable and MemoryMax = the larger of MemTotal and MemAvailable; "
        "interactive/background low-high-max = 15%/30%/50% of MemAvailable; "
        "gates = 5%/10%/20%; BuildKit = 10%/25%/45%. Values are rounded to readable "
        "binary sizes. The optional guaranteed MemoryMin suggestion is 5% of the "
        "remaining MemAvailable after the four proposed child MemoryHigh values; it "
        "is advisory and is not selected automatically."
    )
    out(
        f"CPU fact: `nproc={host_nproc}`. With a blank CPUQuota, the root and "
        "each child use the reserve selected below in `max(1, nproc - reserve) × "
        "100%`; the installer reads nproc again, so a later host change can "
        "change the derived quota."
    )
    _, suggestion_kib, formula = propose_memory_min_guaranteed_suggestion(
        avail_kib,
        {key: proposals[key] for key in (
            "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_HIGH",
        )},
    )
    out(
        f"Computed MemoryMin suggestion (advisory only, before your answers and not "
        f"auto-selected): {formula}. It will be checked again against the values "
        "you enter."
    )
    if suggestion_kib:
        out(f"A conservative optional ceiling would be {kib_to_size_str(suggestion_kib)}; Enter keeps it empty.")

    # Parent slice: this existing key is the only authoritative dev.slice
    # MemoryMin and is mirrored byte-for-byte on the guaranteed sibling below.
    out("\n  dev.slice — shared parent")
    out(
        "  MemoryMin is an optional hard protection budget for explicitly admitted "
        "containers; it is not a per-IDE floor and does not pre-allocate RAM. It "
        "matters under reclaim pressure, and is effective only when the ancestor "
        "chain carries the corresponding protection."
    )
    values["DEV_MEMORY_MIN_GUARANTEED_CEILING"] = walk_key(
        "  MemoryMin (optional; none = no guaranteed tier)",
        "DEV_MEMORY_MIN_GUARANTEED_CEILING", cfg_current, example_defaults,
        proposal=None, validate=validate_size_or_empty,
        allow_empty_token=True,
    )
    values["DEV_MEMORY_LOW"] = walk_key(
        "  MemoryLow (optional; none = no directive)",
        "DEV_MEMORY_LOW", cfg_current, example_defaults,
        validate=validate_optional_positive_size, allow_empty_token=True,
    )
    values["DEV_MEMORY_HIGH"] = walk_key(
        "  MemoryHigh (optional; none = no directive)",
        "DEV_MEMORY_HIGH", cfg_current, example_defaults,
        proposals["DEV_MEMORY_HIGH"], validate=validate_positive_size,
    )
    values["DEV_MEMORY_MAX"] = walk_key(
        "  MemoryMax (optional; none = no directive; swap is separate)",
        "DEV_MEMORY_MAX", cfg_current, example_defaults,
        proposals["DEV_MEMORY_MAX"], validate=validate_positive_size,
    )
    out(
        "`MemoryZSwapWriteback` controls whether cold pages in this cgroup may "
        "leave compressed zswap for disk swap. `yes` keeps zswap useful as a "
        "cache; `no` avoids swap-out of the cold tail but permanently consumes "
        "compressed RAM. The setting is asked per cgroup: a `no` on an ancestor "
        "also disables writeback for its descendants."
    )
    values["DEV_CPU_RESERVE_CORES"] = walk_key(
        "  host reserve (whole cores excluded from auto quota)",
        "DEV_CPU_RESERVE_CORES", cfg_current, example_defaults,
        validate=validate_nonneg_int,
    )
    values["DEV_SUBSLICE_CPU_RESERVE_CORES"] = walk_key(
        "  child-slice reserve (whole cores excluded from auto quota)",
        "DEV_SUBSLICE_CPU_RESERVE_CORES", cfg_current, example_defaults,
        validate=validate_nonneg_int,
    )
    out(
        f"A reserve is not a pinned CPU allocation. For this host, a blank root quota "
        f"will become `{max(1, host_nproc - int(values['DEV_CPU_RESERVE_CORES'])) * 100}%` "
        f"and a blank child quota will become `{max(1, host_nproc - int(values['DEV_SUBSLICE_CPU_RESERVE_CORES'])) * 100}%`. "
        "The formula is `max(1, nproc - reserve) × 100%`; an explicit `N%` such as "
        "`400%` overrides it. These are hard aggregate caps, while `CPUWeight` is only "
        "a relative share when CPU is contested."
    )
    values["DEV_CPU_QUOTA"] = walk_key(
        "  CPUQuota (blank = nproc minus reserve; N% = hard cap, e.g. 400%)",
        "DEV_CPU_QUOTA", cfg_current, example_defaults, validate=validate_cpu_quota,
    )
    values["DEV_ZSWAP_WRITEBACK"] = walk_yn_key(
        "  MemoryZSwapWriteback (yes = may page cold zswap to disk)",
        "DEV_ZSWAP_WRITEBACK", cfg_current, example_defaults,
    )
    out(
        "Swap cascade: the percentage below is applied first to the host's total swap "
        "to derive dev.slice's swap ceiling. Each child then gets the same percentage "
        "of its parent's derived ceiling, and the per-container watcher applies it once "
        "more to the matching child value. Blank MemorySwapMax means use that cascade; "
        "it means auto-detect, not unlimited. An explicit size overrides only that slice."
    )
    values["DEV_SWAP_CASCADE_PCT"] = walk_key(
        "  Swap cascade percentage (bare 1-100; host -> parent -> child)",
        "DEV_SWAP_CASCADE_PCT", cfg_current, example_defaults, validate=validate_pct_1_100,
    )
    values["DEV_SWAP_MAX"] = walk_key(
        "  MemorySwapMax (blank = cascade from host swap; swap only)",
        "DEV_SWAP_MAX", cfg_current, example_defaults, validate=validate_size_or_auto,
    )
    values["DEV_SUBSLICE_IOPS_PCT"] = walk_key(
        "  child IOPS sub-ceiling % (gates/BuildKit; bandwidth unchanged)",
        "DEV_SUBSLICE_IOPS_PCT", cfg_current, example_defaults, validate=validate_pct_1_100,
    )
    out(
        "The child IOPS sub-ceiling is applied at runtime to gates and BuildKit only; "
        "bandwidth continues to inherit the parent pool. `IOWeight` is intentionally "
        "absent on the root because its absolute `io.max` pool is the estate cap."
    )

    out("\n-- e. Guaranteed memory floor (opt-in; one shared ceiling) --")
    out(
        "What this is: an optional HARD memory floor for a special class of explicitly "
        "admitted containers. Unlike `MemoryLow`, which is best-effort, `MemoryMin` "
        "is protected from reclaim under pressure. Setting this value does not reserve "
        "RAM immediately and does not protect every devcontainer."
    )
    out(
        "Where it is set: the same `DEV_MEMORY_MIN_GUARANTEED_CEILING` value is written "
        "to both `dev.slice` (the shared parent) and its sibling "
        "`dev-memory_min_guaranteed.slice`. The sibling is beside the interactive, "
        "background, gates, and BuildKit slices; it is not inside one of them. These "
        "two values must stay exactly equal. There is deliberately no second root/sibling "
        "MemoryMin setting to tune independently."
    )
    out(
        "Why the duplicate rendering matters: cgroup v2 redistributes a parent's unused "
        "protection among children that are using memory. If the parent carries a larger "
        "MemoryMin than the dedicated sibling, the surplus can leak to ordinary tiers; "
        "if the parent carries less, the sibling's floor is silently ineffective. The "
        "wizard asks for the one value and mirrors it so this cannot drift."
    )
    out(
        "Who gets the protection: only a container that explicitly joins "
        "`dev-memory_min_guaranteed.slice` and declares its own memory claim. The "
        "ceiling alone protects nothing; it only sets the total that admitted claims "
        "may share. ciu's consumer-side admission check is separate; see "
        f"`{CIU_P50_RELATIVE_PATH}` for that part."
    )
    _, guaranteed_suggestion_kib, guaranteed_formula = propose_memory_min_guaranteed_suggestion(
        avail_kib,
        {key: proposals[key] for key in (
            "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_HIGH",
        )},
    )
    out(
        f"Before your answers, this host's advisory calculation is: {guaranteed_formula}. "
        "It sums the four child MemoryHigh starting points, not their hard Max values, "
        "because High is the expected reclaim/throttle boundary. The suggestion is only "
        "5% of the leftover and is never selected automatically; Enter keeps this "
        "mechanism empty and inert."
    )
    if guaranteed_suggestion_kib:
        out(f"For orientation, that conservative 5% would be `{kib_to_size_str(guaranteed_suggestion_kib)}`; choose a value only if you have measured a workload that needs it.")
    else:
        out("There is no positive suggested headroom after the starting child highs; leave the mechanism empty unless measured evidence says otherwise.")

    # Walk the sibling's complete memory sequence. MemoryMin is prompted as an
    # explicit confirmation, but edits the same authoritative key already used
    # for dev.slice rather than introducing a shadow sibling variable.
    out("\n  dev-memory_min_guaranteed.slice — admitted hard-floor sibling")
    _prompt_shared_memory_min(values)
    out(f"  MemoryMin is now mirrored on dev.slice ({values['DEV_MEMORY_MIN_GUARANTEED_CEILING'] or 'none'}); no separate sibling MemoryMin key exists.")
    out(
        "  The sibling's MemoryLow/High/Max are separate optional controls for this "
        "admission tier. Leave them empty if its admitted containers provide their own "
        "tighter limits; type none to remove an existing directive. They do not replace "
        "the shared MemoryMin ceiling above."
    )
    for field, key in (("MemoryLow", "DEV_MEMORY_MIN_GUARANTEED_LOW"), ("MemoryHigh", "DEV_MEMORY_MIN_GUARANTEED_HIGH"), ("MemoryMax", "DEV_MEMORY_MIN_GUARANTEED_MAX")):
        values[key] = walk_key(
            f"  {field} (optional; `none` = no directive)", key,
            cfg_current, example_defaults,
            validate=validate_optional_positive_size, allow_empty_token=True,
        )
    out("  CPUQuota, CPUWeight, IOWeight, and MemorySwapMax are not rendered on this admission-only sibling; systemd and the ancestor hierarchy provide the effective values.")

    child_specs = (
        ("dev-interactive.slice", "DEV_INTERACTIVE", ("DEV_INTERACTIVE_MEMORY_MIN", "DEV_INTERACTIVE_MEMORY_LOW", "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_INTERACTIVE_MEMORY_MAX")),
        ("dev-background.slice", "DEV_BACKGROUND", ("DEV_BACKGROUND_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_LOW", "DEV_BACKGROUND_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_MAX")),
        ("dev-gates.slice", "DEV_GATES", ("DEV_GATES_MEMORY_MIN", "DEV_GATES_MEMORY_LOW", "DEV_GATES_MEMORY_HIGH", "DEV_GATES_MEMORY_MAX")),
        ("dev-buildkitd.slice", "DEV_BUILDKITD", ("DEV_BUILDKITD_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_LOW", "DEV_BUILDKITD_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_MAX")),
    )
    for slice_name, prefix, keys in child_specs:
        out(f"\n  {slice_name}")
        out(
            "  MemoryMin/Low/High/Max are optional directives. `none` clears a field, while blank "
            "on CPUQuota or MemorySwapMax below means auto-detect."
        )
        for field, key in zip(("MemoryMin", "MemoryLow", "MemoryHigh", "MemoryMax"), keys):
            validator = validate_optional_positive_size
            values[key] = walk_key(
                f"  {field} (optional; none = no directive)",
                key, cfg_current, example_defaults, proposals.get(key), validator,
                allow_empty_token=field in ("MemoryMin", "MemoryLow"),
            )
        values[f"{prefix}_CPU_WEIGHT"] = walk_key(
            "  CPUWeight (1-10000; relative share, not a cap)",
            f"{prefix}_CPU_WEIGHT", cfg_current, example_defaults, validate=validate_weight,
        )
        values[f"{prefix}_CPU_QUOTA"] = walk_key(
            "  CPUQuota (blank = nproc minus reserve; N% = hard cap, e.g. 400%)",
            f"{prefix}_CPU_QUOTA", cfg_current, example_defaults, validate=validate_cpu_quota,
        )
        values[f"{prefix}_IO_WEIGHT"] = walk_key(
            "  IOWeight (1-10000; relative share; BFQ usually <=100)",
            f"{prefix}_IO_WEIGHT", cfg_current, example_defaults, validate=validate_weight,
        )
        values[f"{prefix}_MEMORY_SWAP_MAX"] = walk_key(
            "  MemorySwapMax (blank = derive from parent; swap only)",
            f"{prefix}_MEMORY_SWAP_MAX", cfg_current, example_defaults, validate=validate_size_or_auto,
        )
        values[f"{prefix}_ZSWAP_WRITEBACK"] = walk_yn_key(
            "  MemoryZSwapWriteback (yes/no; per-slice policy)",
            f"{prefix}_ZSWAP_WRITEBACK", cfg_current, example_defaults,
        )
        if prefix in ("DEV_BACKGROUND", "DEV_GATES"):
            values[f"{prefix}_OOM_PRESSURE_LIMIT"] = walk_key(
                "  ManagedOOMMemoryPressureLimit (pressure % before systemd-oomd acts)",
                f"{prefix}_OOM_PRESSURE_LIMIT", cfg_current, example_defaults,
                validate=validate_systemd_pct_1_100,
            )
        if prefix == "DEV_BUILDKITD":
            out(
                "  BuildKit is the host-managed rootless worker shared by projects. "
                "The image is a pinned OCI reference used by the service; the "
                "accidental-worker policy applies only to unapproved Buildx "
                "docker-container workers: `terminate` removes them, while "
                "`report-only` logs them and leaves them running."
            )
            values["DEV_BUILDKITD_IMAGE"] = walk_key(
                "  BuildKit image (rootless OCI reference)", "DEV_BUILDKITD_IMAGE",
                cfg_current, example_defaults, validate=validate_nonempty,
            )
            out(
                "BuildKit also limits its own internal solver parallelism. This "
                "reduces simultaneous RUN/cache operations inside one daemon; "
                "it does not limit the number of client builds and is separate "
                "from the release flow's REPACK_JOBS and REPACK_CONCURRENCY."
            )
            values["DEV_BUILDKITD_MAX_PARALLELISM"] = walk_key(
                "  BuildKit max parallel solver operations (positive integer)",
                "DEV_BUILDKITD_MAX_PARALLELISM", cfg_current, example_defaults,
                proposal=str(max(1, min(4, host_nproc))),
                validate=validate_positive_int,
            )
            values["BUILDX_ACCIDENTAL_CONTAINER_POLICY"] = walk_key(
                "  accidental Buildx policy (terminate or report-only)",
                "BUILDX_ACCIDENTAL_CONTAINER_POLICY", cfg_current, example_defaults,
                validate=validate_guard_policy,
            )

    _reprompt_memory_constraints(avail_kib, values)
    _, _, final_formula = propose_memory_min_guaranteed_suggestion(
        avail_kib,
        {key: values[key] for key in (
            "DEV_INTERACTIVE_MEMORY_HIGH",
            "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH",
            "DEV_BUILDKITD_MEMORY_HIGH",
        )},
    )
    out(f"After your entries, the advisory calculation is: {final_formula}.")
    return values


def step_slice_first_resources(
    meminfo: dict[str, int],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> dict[str, str]:
    """Walk the resource policy in the same order a user reasons about it."""
    total_kib = meminfo["MemTotal"]
    avail_kib = meminfo["MemAvailable"]
    swap_kib = read_swap_total_kib(meminfo, "")
    proposals = propose_memory_tiers(total_kib, avail_kib)
    values: dict[str, str] = {}
    host_nproc = discover_nproc()
    if not host_nproc:
        raise ValueError("nproc did not return a positive CPU count; cannot explain or derive CPU ceilings")

    out("\n-- d. Governed slices (slice-first) --")
    out(
        f"Host facts used for proposals: MemTotal={kib_to_size_str(total_kib)}, "
        f"MemAvailable={kib_to_size_str(avail_kib)}, nproc={host_nproc}, "
        f"SwapTotal={kib_to_size_str(swap_kib)}."
    )
    out(
        "Memory proposals are percentages of physical MemTotal, because "
        "MemAvailable is transient: a busy host must not suddenly receive tiny "
        "limits. MemAvailable is shown as context only, not as a budget. The "
        "proposals are caps and thresholds, not RAM reservations or allocations."
    )
    out(
        "MemoryMin is hard reclaim protection, MemoryLow is best-effort "
        "protection, MemoryHigh starts reclaim/throttling, and MemoryMax is a "
        "hard RAM ceiling. MemoryMax excludes swap. MemorySwapMax is a separate "
        "swap-only ceiling; a process may therefore use up to both limits, subject "
        "to the ancestor limits. Every MemoryMin/Low/High/Max field is optional: "
        "Enter accepts the shown value, while '-' (or the older spelling 'none') "
        "writes no directive. An omitted parent limit is unlimited at that level."
    )
    out(
        "Aggregate checks use hierarchy, not current consumption: sibling "
        "MemoryHigh values must fit under a configured parent MemoryHigh; sibling "
        "MemoryLow values must fit under a configured parent MemoryLow; a child's "
        "MemoryMax must fit under a configured parent MemoryMax; and admitted "
        "MemoryMin claims must fit under the shared guaranteed ceiling. Child "
        "MemoryMax values are not summed because each is its own subtree cap. "
        "The wizard shows every contributing value when a correction is needed."
    )
    out(
        "The starting policy is root High=75% of MemTotal and root Max=100%. "
        "Interactive starts at Low/High/Max=15%/20%/32%; background at "
        "15%/32%/50%; gates at 5%/5%/10%; BuildKit at 10%/10%/12%. "
        "For a 15.5 GiB host this is about 12 GiB root High, 15.5 GiB root "
        "Max, 3/5 GiB interactive, 5/8 GiB background, 800 MiB/1.5 GiB gates, "
        "and 1.5/2 GiB BuildKit. These are starting points to review, not facts."
    )
    _, suggested_min_kib, suggestion_formula = propose_memory_min_guaranteed_suggestion(
        total_kib,
        {key: proposals[key] for key in (
            "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_HIGH",
        )},
    )
    out(
        f"Advisory guaranteed-floor math: {suggestion_formula}. "
        f"This is not a RAM reservation and is never selected automatically; "
        f"the optional ceiling suggestion is {kib_to_size_str(suggested_min_kib)}."
    )

    out("\n  dev.slice — shared parent")
    out(
        "Configure the shared parent first. It bounds the whole dev estate "
        "together; child values below describe each sibling's own workload. "
        "The guaranteed MemoryMin value is mirrored later on the dedicated "
        "sibling, so there is only one ceiling to decide."
    )
    values["DEV_MEMORY_MIN_GUARANTEED_CEILING"] = walk_key(
        "  MemoryMin (optional; '-' = no guaranteed tier)",
        "DEV_MEMORY_MIN_GUARANTEED_CEILING", cfg_current, example_defaults,
        validate=validate_optional_positive_size, allow_empty_token=True,
    )
    for field, key in (
        ("MemoryLow", "DEV_MEMORY_LOW"),
        ("MemoryHigh", "DEV_MEMORY_HIGH"),
        ("MemoryMax", "DEV_MEMORY_MAX"),
    ):
        out(
            f"  {field} is the parent {field}: it applies to the entire dev "
            "estate, not to one container. Leave it unset with '-' when you "
            "want systemd's default at this level."
        )
        values[key] = walk_key(
            f"  {field} (optional; '-' = no directive)",
            key, cfg_current, example_defaults, proposals.get(key),
            validate=validate_optional_positive_size, allow_empty_token=True,
        )

    out(
        "MemoryZSwapWriteback controls whether cold pages in this cgroup may "
        "leave compressed zswap for disk swap. yes permits that cache to drain; "
        "no keeps the cold tail in compressed RAM. A no on an ancestor also "
        "disables writeback for descendants."
    )
    out(
        "CPU has two different concepts. The host reserve is cores deliberately "
        "left outside the aggregate dev quota for host/production load. The "
        "child quota reserve is cores held outside each child's automatically "
        "derived quota, making each child a smaller dev ceiling. Neither reserves "
        "or pins a CPU; both only affect a blank CPUQuota."
    )
    out(
        f"On this host (nproc={host_nproc}), if host reserve is 1, a blank root "
        f"CPUQuota becomes {max(1, host_nproc - 1) * 100}%; if child quota "
        f"reserve is 3, a blank child CPUQuota becomes "
        f"{max(1, host_nproc - 3) * 100}%. An explicit value such as 400% "
        "is the actual hard cap and overrides the derived value. CPUWeight is "
        "only a relative share when peers contend."
    )
    values["DEV_CPU_RESERVE_CORES"] = walk_key(
        "  host/production CPU reserve (cores excluded from root auto-quota)",
        "DEV_CPU_RESERVE_CORES", cfg_current, example_defaults,
        validate=validate_nonneg_int,
    )
    values["DEV_SUBSLICE_CPU_RESERVE_CORES"] = walk_key(
        "  child dev CPU ceiling reserve (cores excluded from each child auto-quota)",
        "DEV_SUBSLICE_CPU_RESERVE_CORES", cfg_current, example_defaults,
        validate=validate_nonneg_int,
    )
    values["DEV_CPU_QUOTA"] = walk_key(
        "  dev.slice CPUQuota (blank = derived from host reserve; N% = hard cap)",
        "DEV_CPU_QUOTA", cfg_current, example_defaults, validate=validate_cpu_quota,
    )
    values["DEV_ZSWAP_WRITEBACK"] = walk_yn_key(
        "  dev.slice MemoryZSwapWriteback", "DEV_ZSWAP_WRITEBACK",
        cfg_current, example_defaults,
    )

    out(
        "Swap policy is separate from RAM. The cascade first applies "
        "DEV_SWAP_CASCADE_PCT to host SwapTotal for dev.slice. A blank "
        "MemorySwapMax means derive that slice's swap-only ceiling; it does "
        "not mean unlimited and it does not include RAM. Each child derives "
        "the same percentage from the parent's derived swap-only ceiling; an "
        "explicit size overrides only that slice."
    )
    values["DEV_SWAP_CASCADE_PCT"] = walk_key(
        "  swap cascade percentage (host -> dev.slice -> child; bare 1-100)",
        "DEV_SWAP_CASCADE_PCT", cfg_current, example_defaults,
        validate=validate_pct_1_100,
    )
    values["DEV_SWAP_MAX"] = walk_key(
        "  dev.slice MemorySwapMax (blank = derive swap-only ceiling)",
        "DEV_SWAP_MAX", cfg_current, example_defaults,
        validate=validate_size_or_auto,
    )
    out(
        "The child IOPS sub-ceiling is applied only to gates and the host-managed "
        "BuildKit slice. It is a percentage of the already-derived dev.slice "
        "IOPS cap, not a percentage of the raw device and not a bandwidth cap. "
        "Bandwidth inherits the parent pool. IOWeight is intentionally absent "
        "on the root because its absolute io.max pool is the estate cap."
    )
    values["DEV_SUBSLICE_IOPS_PCT"] = walk_key(
        "  child IOPS sub-ceiling (percentage of dev.slice IOPS cap; bare 1-100)",
        "DEV_SUBSLICE_IOPS_PCT", cfg_current, example_defaults,
        validate=validate_pct_1_100,
    )

    out("\n-- e. Guaranteed memory floor (opt-in; one shared ceiling) --")
    out(
        "This optional MemoryMin protects only containers explicitly admitted "
        "to dev-memory_min_guaranteed.slice. It does not allocate RAM now and "
        "does not protect ordinary devcontainers. The value above is rendered "
        "on both the parent and this sibling; Enter confirms it, and a new "
        "value changes both."
    )
    out(
        "For orientation, the advisory suggestion is 5% of MemTotal left after "
        "the four child MemoryHigh starting points. It is deliberately not "
        "selected automatically; leave the ceiling unset unless measured "
        "workload evidence calls for it."
    )
    _prompt_shared_memory_min(values)
    out(
        f"  mirrored MemoryMin: {values['DEV_MEMORY_MIN_GUARANTEED_CEILING'] or 'none'}; "
        "the sibling has no second MemoryMin setting."
    )
    out(
        "This sibling's Low/High/Max are also optional and apply only to its "
        "admitted subtree. CPU, IOWeight and swap are inherited rather than "
        "rendered here."
    )
    for field, key in (
        ("MemoryLow", "DEV_MEMORY_MIN_GUARANTEED_LOW"),
        ("MemoryHigh", "DEV_MEMORY_MIN_GUARANTEED_HIGH"),
        ("MemoryMax", "DEV_MEMORY_MIN_GUARANTEED_MAX"),
    ):
        values[key] = walk_key(
            f"  {field} (optional; '-' = no directive)", key,
            cfg_current, example_defaults,
            validate=validate_optional_positive_size, allow_empty_token=True,
        )

    child_specs = (
        ("dev-interactive.slice", "DEV_INTERACTIVE"),
        ("dev-background.slice", "DEV_BACKGROUND"),
        ("dev-gates.slice", "DEV_GATES"),
        ("dev-buildkitd.slice", "DEV_BUILDKITD"),
    )
    descriptions = {
        "DEV_INTERACTIVE": "IDE servers and devcontainer tools; it gets a generous working-set cap.",
        "DEV_BACKGROUND": "stacks, tests and background development containers; it gets the largest ordinary pool.",
        "DEV_GATES": "short-lived gate/test lanes; its cap is higher than a tiny control-plane budget.",
        "DEV_BUILDKITD": "the one host-managed shared BuildKit worker; size it for concurrent projects.",
    }
    for slice_name, prefix in child_specs:
        out(f"\n  {slice_name}")
        out(descriptions[prefix])
        out(
            "For this slice, every MemoryMin/Low/High/Max is optional. "
            "Enter accepts the shown value; '-' explicitly removes the directive. "
            "MemoryMax is RAM only, while MemorySwapMax below is swap only."
        )
        for field in ("MIN", "LOW", "HIGH", "MAX"):
            key = f"{prefix}_MEMORY_{field}"
            values[key] = walk_key(
                f"  Memory{field.title()} (optional; '-' = no directive)",
                key, cfg_current, example_defaults, proposals.get(key),
                validate=validate_optional_positive_size, allow_empty_token=True,
            )
        out(
            "CPUQuota is this slice's hard cap: blank derives from the child "
            "quota-ceiling reserve selected above; an explicit N% wins. "
            "CPUWeight and IOWeight are relative shares only and matter when "
            "siblings contend."
        )
        values[f"{prefix}_CPU_WEIGHT"] = walk_key(
            "  CPUWeight (1-10000; relative share, not a cap)",
            f"{prefix}_CPU_WEIGHT", cfg_current, example_defaults,
            validate=validate_weight,
        )
        values[f"{prefix}_CPU_QUOTA"] = walk_key(
            "  CPUQuota (blank = derived child ceiling; N% = hard cap)",
            f"{prefix}_CPU_QUOTA", cfg_current, example_defaults,
            validate=validate_cpu_quota,
        )
        values[f"{prefix}_IO_WEIGHT"] = walk_key(
            "  IOWeight (1-10000; relative share, not an IO cap)",
            f"{prefix}_IO_WEIGHT", cfg_current, example_defaults,
            validate=validate_weight,
        )
        out(
            "MemorySwapMax here limits swap only. Blank cascades from the live "
            "parent MemorySwapMax; an explicit size affects only this slice."
        )
        values[f"{prefix}_MEMORY_SWAP_MAX"] = walk_key(
            "  MemorySwapMax (blank = derived swap-only ceiling)",
            f"{prefix}_MEMORY_SWAP_MAX", cfg_current, example_defaults,
            validate=validate_size_or_auto,
        )
        out(
            "MemoryZSwapWriteback controls whether this slice may write cold "
            "compressed-zswap pages to disk swap. It is a policy switch, not a "
            "size or a memory limit."
        )
        values[f"{prefix}_ZSWAP_WRITEBACK"] = walk_yn_key(
            "  MemoryZSwapWriteback", f"{prefix}_ZSWAP_WRITEBACK",
            cfg_current, example_defaults,
        )
        if prefix in ("DEV_BACKGROUND", "DEV_GATES"):
            out(
                "systemd-oomd watches memory pressure here and may act at the "
                "configured percentage; it is not the same as MemoryMax."
            )
            values[f"{prefix}_OOM_PRESSURE_LIMIT"] = walk_key(
                "  ManagedOOMMemoryPressureLimit (systemd percentage, e.g. 75%)",
                f"{prefix}_OOM_PRESSURE_LIMIT", cfg_current, example_defaults,
                validate=validate_systemd_pct_1_100,
            )
        if prefix == "DEV_BUILDKITD":
            out(
                "BuildKit is the host-managed rootless worker. The accidental "
                "Buildx policy below concerns only unapproved docker-container "
                "workers: terminate removes them; report-only logs and leaves "
                "them running."
            )
            values["DEV_BUILDKITD_IMAGE"] = walk_key(
                "  BuildKit image (rootless OCI reference)", "DEV_BUILDKITD_IMAGE",
                cfg_current, example_defaults, validate=validate_nonempty,
            )
            out(
                "BuildKit also limits its own internal solver parallelism. This "
                "reduces simultaneous RUN/cache operations inside one daemon; "
                "it does not limit the number of client builds and is separate "
                "from the release flow's REPACK_JOBS and REPACK_CONCURRENCY."
            )
            values["DEV_BUILDKITD_MAX_PARALLELISM"] = walk_key(
                "  BuildKit max parallel solver operations (positive integer)",
                "DEV_BUILDKITD_MAX_PARALLELISM", cfg_current, example_defaults,
                proposal=str(max(1, min(4, host_nproc))),
                validate=validate_positive_int,
            )
            values["BUILDX_ACCIDENTAL_CONTAINER_POLICY"] = walk_key(
                "  accidental Buildx policy (terminate or report-only)",
                "BUILDX_ACCIDENTAL_CONTAINER_POLICY", cfg_current, example_defaults,
                validate=validate_guard_policy,
            )
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
    out("\n-- g. Reactive per-container cap watcher (mdt-dev-cap-watcher.py) --")
    out(
        "These bound the blast radius of any ONE unlabelled container ballooning inside a "
        "governed slice. The memory watcher applies them when a new Docker scope appears "
        "if this host has working inotify; if not, the periodic sweep remains the "
        "backstop. A container's own explicit `docker run --memory` always wins; this "
        "only fills in containers that asked for no memory limit."
    )
    values["WATCHER_PER_CONTAINER_MEMORY_MAX"] = walk_key(
        "Per-container MemoryMax (interactive/background; positive size)",
        "WATCHER_PER_CONTAINER_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_positive_size,
    )
    out(
        "dev-gates.slice gets its OWN, separate ceiling here -- deliberately not the shared "
        "value above -- sized against what a lane container actually needs (see "
        "'dev-gates: why' in the README): too tight here would re-create the exact headroom "
        "incident dev-gates.slice exists to fix."
    )
    values["WATCHER_PER_CONTAINER_GATES_MEMORY_MAX"] = walk_key(
        "Per-container MemoryMax (gates; positive size)",
        "WATCHER_PER_CONTAINER_GATES_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_positive_size,
    )
    out(
        "MemoryHigh is derived from whichever Max above applies, as this percentage -- a "
        "soft throttle-into-reclaim step below the hard cap, same pairing the slices "
        "themselves use."
    )
    values["WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT"] = walk_key(
        "Per-container MemoryHigh (% of that MemoryMax; bare 1-100)",
        "WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT",
        cfg_current,
        example_defaults,
        validate=validate_pct_1_100,
    )
    out(
        "Per-container MemorySwapMax is NOT asked here -- it is computed at runtime from "
        "whichever matched slice's own LIVE memory.swap.max, times DEV_SWAP_CASCADE_PCT "
        "(the slice settings above), never re-derived from this file. See "
        "scripts/mdt-dev-cap-watcher.py's read_slice_swap_max_bytes()."
    )
    return values


# ─── orchestration ─────────────────────────────────────────────────────────


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mdt-host-setup-wizard.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Create a host-sized mdt host-setup.env by asking for each governed "
            "resource in slice order. Values are validated before they are written. "
            "Host shell only: a devcontainer invocation is refused."
        ),
        epilog=textwrap.dedent(
            """
            Default use
              sudo ./mdt-host-setup-wizard.py
                Run this from the Docker host shell. Read the template and (if present)
                the existing output, prompt for values, and atomically write
                /etc/mdt/host-setup.env. This standalone command does not render units
                or restart Docker.

              sudo ./install.sh --wizard
                The installer supplies a temporary candidate, runs this wizard, then
                validates it before installing /etc/mdt, rendering systemd units, and
                applying the policy. Use --force with --wizard only when you want to
                discard the old config's values as defaults and start from the current
                example; the old file is still backed up after validation.
                (The standalone wizard itself has no --force option; that flag belongs
                to install.sh's candidate-handling step.)
                Do not delete the existing output to imitate --force: deletion loses
                its values as defaults and prevents the installer's automatic backup.

            Host-only precondition
              Do not run this wizard or install.sh inside a devcontainer. UID 0 there
              is container root, not root of the host's /etc, PID 1/systemd, or cgroup
              tree. The command refuses before reading or writing configuration. If
              findmnt reports `overlay`, leave the container and rerun from the Docker
              host; that is the container's mount namespace, not the host disk.

            Defaults and empty values
              Existing output values win, including intentional empty values. For a
              missing key, a live-host proposal wins over the example's value. Enter
              accepts the shown default. Every MemoryMin/Low/High/Max field is
              optional; type `-` to clear it (the older spelling `none` is
              also accepted). Blank CPUQuota and MemorySwapMax fields mean derive
              them at install time, not unlimited. Sizes are binary: 2G means 2 GiB;
              a bare number means bytes. Percentage prompts say whether they expect a
              bare number or a systemd value such as 400%.

            What the sizing uses
              Memory proposals are percentages of physical MemTotal; MemAvailable is
              shown as transient context, never used as a hard budget. CPU auto-quota
              uses nproc minus the selected host/child quota reserve, floored at one
              core. IO percentages multiply the measured io.cost-derived ceilings:
              random/sequence IOPS use the lower value, while bandwidth uses sequential
              bytes per second. The cache remains current only while device and target
              identity match; without a current cache, static fallback caps remain.

            Important files and side effects
              Reads: --example, an existing --output, /proc/meminfo and /proc/swaps,
              findmnt's Docker-data mount, IO_BASELINE_ENV, IO_BASELINE_TESTFILE,
              and the baseline adapter/generator. Writes: --output, atomically at
              the end. If the benchmark is selected, it writes/replaces the baseline
              cache and persistent target and temporarily saturates its device.
              install.sh additionally writes /etc/mdt,
              merges its owned Docker keys into /etc/docker/daemon.json, and only
              restarts Docker when --restart-docker is explicitly supplied. These are
              host paths and side effects; the host-only preflight runs first.

            Output formatting
              The interactive wizard uses backticks as markers for important paths,
              keys, commands, and units; on a real TTY it renders them bold cyan,
              while redirected output retains the markers. This --help text is
              argparse output, so its backticks remain literal. Set NO_COLOR
              (presence is enough, including NO_COLOR=) to disable ANSI in the
              interactive wizard.

            Exit status
              0 means the requested wizard/installer action completed. A nonzero
              status means the requested action did not complete. Validation and
              write failures occur before the wizard applies host policy; an
              installer failure reports its own status and may require checking
              the host before retrying.
            """
        ),
    )
    parser.add_argument(
        "--example", type=Path, default=DEFAULT_EXAMPLE,
        metavar="PATH", help="template to read (default: host-setup.env.example)",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        metavar="PATH", help="candidate/config file to write atomically (default: /etc/mdt/host-setup.env)",
    )
    parser.add_argument(
        "--validate-config",
        type=Path,
        default=None,
        help="validate PATH without prompting or writing; reads host memory facts and checks configured hierarchy relationships",
    )
    parser.add_argument(
        "--io-baseline-script", type=Path, default=DEFAULT_IO_BASELINE_SCRIPT,
        metavar="PATH", help="benchmark script (default: host-setup/scripts/mdt-io-baseline.py)",
    )
    parser.add_argument(
        "--install-script", type=Path, default=DEFAULT_INSTALL_SCRIPT,
        metavar="PATH", help="installer offered at the end (default: install.sh)",
    )
    parser.add_argument(
        "--meminfo-path", type=Path, default=DEFAULT_MEMINFO_PATH,
        metavar="PATH", help="MemTotal/MemAvailable source (default: /proc/meminfo)",
    )
    parser.add_argument(
        "--swaps-path", type=Path, default=DEFAULT_SWAPS_PATH,
        metavar="PATH", help="swap source if Meminfo lacks SwapTotal (default: /proc/swaps)",
    )
    parser.add_argument(
        "--skip-run-offer",
        action="store_true",
        help=(
            "internal: don't offer to run install.sh; used by install.sh --wizard, "
            "which validates and applies the candidate itself"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_cli_args(argv)

    context_error = host_context_error()
    if context_error:
        print(f"ERROR: {context_error}", file=sys.stderr)
        print(
            "Leave the devcontainer and run this command from the host checkout, "
            "for example: sudo ./host-setup/install.sh --wizard",
            file=sys.stderr,
        )
        return 2

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
            f"ERROR: could not read `{args.meminfo_path}`: {exc}. A positive "
            "`MemAvailable` value is required to derive safe host-sized limits; "
            "provide a readable `--meminfo-path` or fix the host."
        )
        meminfo = {}
    try:
        swaps_text = args.swaps_path.read_text()
    except OSError:
        swaps_text = ""
    swap_kib = read_swap_total_kib(meminfo, swaps_text)
    meminfo.setdefault("SwapTotal", swap_kib)

    if meminfo.get("MemTotal", 0) <= 0 or meminfo.get("MemAvailable", 0) <= 0:
        print(
            f"ERROR: `{args.meminfo_path}` does not contain positive MemTotal and "
            "MemAvailable values; the wizard cannot derive safe host-sized memory "
            "limits and will not run the IO benchmark or write a configuration.",
            file=sys.stderr,
        )
        return 1

    out("== mdt host-setup wizard ==")
    out(
        "This interactive run builds one complete host configuration. It reads the "
        "template, any existing output file, live memory/swap facts, Docker mount "
        "discovery, and the IO baseline cache. It writes the output atomically only "
        "after the prompts finish. Existing values for template keys not shown by a prompt are "
        "preserved from the existing file; on a fresh run, the template and live-host proposals "
        "supply the defaults."
    )
    out(f"template: `{args.example}`")
    out(f"writing:  `{args.output}`")
    if args.skip_run_offer:
        out(
            "This invocation was started by `install.sh --wizard`: the installer will "
            "strictly validate this candidate, back up an existing installed config, "
            "then render and apply host units only if validation passes."
        )
    else:
        out(
            "Standalone mode writes only the file above. It does not render systemd or "
            "restart Docker until you explicitly run the installer at the final prompt."
        )
    out(
        "At a prompt, Enter accepts the shown default. A default comes from an existing "
        "value first, then a live-host proposal, then the example. Optional fields say "
        "when `-` clears them; blank CPU/swap auto fields mean derive at install time. "
        "A cleared memory directive is omitted from the rendered unit. The wizard never "
        "writes until all prompts and validation finish."
    )
    out(
        "Terminal formatting: text in backticks is highlighted only on an interactive "
        "TTY. Redirected output keeps the backticks as readable markers; set `NO_COLOR` "
        "(even to an empty value) to disable ANSI highlighting."
    )
    out(
        "Important files: the template is read, the output path is written, and the "
        "baseline cache and target paths shown in section b are checked for device identity; "
        "the cache is updated only if "
        "you run the benchmark. `install.sh` additionally writes `/etc/mdt` units/scripts, merges "
        "its keys into `/etc/docker/daemon.json`, and may restart Docker only when "
        "`--restart-docker` is supplied."
    )
    out(
        f"Host swap visible to the later cascade: `{kib_to_size_str(swap_kib)}` "
        "from `/proc/meminfo` (or `/proc/swaps` when the former has no SwapTotal). "
        "A zero value means automatic swap ceilings cannot be derived."
    )

    walked: dict[str, str] = {}
    try:
        walked["IO_DEV_PATH"] = step_io_device(cfg_current, example_defaults)

        baseline_env, baseline_testfile, baseline_measured_now = step_io_baseline(
            cfg_current, example_defaults, args.io_baseline_script
        )
        walked["IO_BASELINE_ENV"] = baseline_env
        walked["IO_BASELINE_TESTFILE"] = baseline_testfile

        dev_pct, sweep_pct = step_io_cap_pct(cfg_current, example_defaults)
        walked["DEV_IO_CAP_PCT"] = dev_pct
        walked["WATCHER_IO_CAP_PCT"] = sweep_pct

        walked.update(step_slice_first_resources(meminfo, cfg_current, example_defaults))
        cgroup_parent, backstop_max, backstop_swap = step_docker_daemon(
            cfg_current, example_defaults
        )
        walked["DOCKER_DAEMON_CGROUP_PARENT"] = cgroup_parent
        walked["DOCKER_SCOPE_BACKSTOP_MEMORY_MAX"] = backstop_max
        walked["DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX"] = backstop_swap

        walked.update(step_watcher(cfg_current, example_defaults))
    except (ValueError, EOFError) as exc:
        sys.stdout.flush()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Start with the template so a current checkout can add newly introduced
    # keys during an upgrade, then overlay every known value from the existing
    # config. The second pass below applies the answers from this run. This
    # preserves non-prompted tuning instead of silently reseeding it from the
    # example, while retaining the template's comments and ordering.
    text = example_text
    try:
        for key, value in cfg_current.items():
            if key in example_defaults:
                text = apply_value(text, key, value)
        for key, value in walked.items():
            text = apply_value(text, key, value)
    except TemplateError as exc:
        sys.stdout.flush()
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
    out(
        "The file is now a candidate/configuration only. If it is installed, "
        "run `sudo mdt-host-check.sh`, recreate containers whose placement should "
        "change, and schedule a Docker restart if `DOCKER_DAEMON_CGROUP_PARENT` "
        "changed. A restart disrupts running containers."
    )

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
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            out(f"install failed with exit status {result.returncode}.")
            return result.returncode
        out("install completed successfully. Run `sudo mdt-host-check.sh` to verify the host.")
    else:
        out(f"Next step: sudo {args.install_script}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
