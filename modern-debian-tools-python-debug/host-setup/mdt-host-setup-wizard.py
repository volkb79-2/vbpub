#!/usr/bin/env python3
# Interactive wizard for /etc/mdt/host-setup.env — walks a slice-first resource
# order, showing THIS host's own /proc/meminfo-derived numbers where
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

# These names were public in the first host-setup release. The implementation
# now uses WATCHER_* because both the Docker-events watcher and its periodic
# sweep consume them; migrate them when --wizard sees an old config so an
# upgrade does not silently reseed custom values from the example.
LEGACY_CONFIG_KEYS = {
    "SWEEP_IO_CAP_PCT": "WATCHER_IO_CAP_PCT",
    "TESTRUNNER_IMAGE_PATTERNS": "WATCHER_TESTRUNNER_IMAGE_PATTERNS",
    "BUILDKIT_NAME_PATTERNS": "WATCHER_BUILDKIT_NAME_PATTERNS",
    "DEVCONTAINER_NAME_PATTERNS": "WATCHER_DEVCONTAINER_NAME_PATTERNS",
    "SWEEP_INTERVAL": "WATCHER_INTERVAL",
}


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
    # Prompt labels are passed with section-local indentation by callers, but
    # input() has no paragraph layout and long labels take a different path
    # through the wrapper below. Normalize that indentation here so a short
    # label cannot look different from the same label after wrapping.
    text = text.lstrip()

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
    below are the host policy: they leave overlap between tiers.  Child
    ``MemoryLow`` is intentionally not proposed on a fresh configuration
    because the parent ``MemoryLow`` is unset by default; a protection value
    that cannot reach through its ancestor is a misleading default.  Each
    child control is independent; ordering within one slice is a hard
    relationship, and a single child High/Max above its configured parent is
    also refused, while sibling totals and Min/Low effectiveness remain review
    information.
    """
    def frac(percent: int) -> str:
        return kib_to_size_str(max(1, total_kib * percent // 100))

    proposals = {
        "DEV_MEMORY_HIGH": kib_to_size_str(max(1, total_kib * 75 // 100)),
        "DEV_MEMORY_MAX": kib_to_size_str(total_kib),
        "DEV_INTERACTIVE_MEMORY_HIGH": frac(20),
        "DEV_INTERACTIVE_MEMORY_MAX": frac(32),
        "DEV_BACKGROUND_MEMORY_HIGH": frac(32),
        "DEV_BACKGROUND_MEMORY_MAX": frac(50),
        "DEV_GATES_MEMORY_HIGH": frac(5),
        "DEV_GATES_MEMORY_MAX": frac(10),
        "DEV_BUILDKITD_MEMORY_HIGH": frac(10),
        "DEV_BUILDKITD_MEMORY_MAX": frac(12),
    }
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
    # The parent is an ancestor ceiling. Several sibling thresholds may
    # legitimately add up to more than that ceiling because the kernel
    # applies the parent to the combined subtree. One child declaring a
    # larger High or Max than its parent, however, makes the child's displayed
    # policy misleading and is almost certainly a typo. Check only those two
    # ceiling controls here; Min/Low are advisory below because their
    # effectiveness depends on the ancestor protection chain.
    parent_checks = (
        ("MemoryHigh", "DEV_MEMORY_HIGH", 2),
        ("MemoryMax", "DEV_MEMORY_MAX", 3),
    )
    for slice_name, keys in MEMORY_CHILD_SPECS:
        for field, parent_key, child_index in parent_checks:
            parent = parse_size_to_kib(values.get(parent_key, ""))
            child_key = keys[child_index]
            child = parse_size_to_kib(values.get(child_key, ""))
            if parent and child > parent:
                errors.append(
                    (
                        child_key,
                        f"{slice_name} {field}={values[child_key]} exceeds its "
                        f"parent dev.slice {field}={values[parent_key]}; reduce "
                        "this child or increase the parent (sibling totals are "
                        "not summed for this check)",
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


def memory_review_warnings(values: dict[str, str]) -> list[str]:
    """Return non-blocking parent/child memory observations.

    Sibling MemoryMin/Low/High values are independent hierarchical controls,
    not a budget to add together. A child Min/Low without the matching
    ancestor protection is worth naming: it is valid configuration, but the
    requested protection may be ineffective at that hierarchy boundary. A
    single child High/Max above its parent is a hard error (see
    memory_relationship_errors); sibling High/Low totals above the parent are
    only review information because the kernel applies the parent control to
    the combined subtree rather than treating it as a sum budget.
    """
    warnings: list[str] = []
    parent_fields = (
        ("MemoryMin", "DEV_MEMORY_MIN_GUARANTEED_CEILING", 0),
        ("MemoryLow", "DEV_MEMORY_LOW", 1),
        ("MemoryHigh", "DEV_MEMORY_HIGH", 2),
        ("MemoryMax", "DEV_MEMORY_MAX", 3),
    )
    for slice_name, keys in MEMORY_CHILD_SPECS:
        for field, parent_key, child_index in parent_fields:
            parent = parse_size_to_kib(values.get(parent_key, ""))
            child_key = keys[child_index]
            child = parse_size_to_kib(values.get(child_key, ""))
            if not child:
                continue
            if not parent and field in ("MemoryMin", "MemoryLow"):
                warnings.append(
                    f"{slice_name} {field}={values[child_key]} has no configured "
                    f"ancestor dev.slice {field}; the requested protection may be "
                    "ineffective"
                )
            elif parent and child > parent and field in ("MemoryMin", "MemoryLow"):
                warnings.append(
                    f"{slice_name} {field}={values[child_key]} exceeds parent "
                    f"dev.slice {field}={values[parent_key]}; review the effective "
                    "ancestor limit"
                )
    aggregate_fields = (
        ("MemoryLow", "DEV_MEMORY_LOW", 1),
        ("MemoryHigh", "DEV_MEMORY_HIGH", 2),
    )
    for field, parent_key, child_index in aggregate_fields:
        parent = parse_size_to_kib(values.get(parent_key, ""))
        if not parent:
            continue
        contributing: list[str] = []
        total = 0
        for slice_name, keys in MEMORY_CHILD_SPECS:
            child_key = keys[child_index]
            child = parse_size_to_kib(values.get(child_key, ""))
            if child:
                total += child
                contributing.append(f"{slice_name}={values[child_key]}")
        if total > parent and contributing:
            warnings.append(
                f"configured child {field} values ({', '.join(contributing)}; "
                f"{kib_to_size_str(total)} combined) exceed dev.slice {field}="
                f"{values[parent_key]}; advisory only, not a summed-budget "
                "error—the parent still caps the combined subtree"
            )
    return warnings


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
        "DEV_BUILDKITD_IMAGE": validate_buildkit_image,
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
        "WATCHER_TESTRUNNER_IMAGE_PATTERNS": validate_watcher_patterns,
        "WATCHER_BUILDKIT_NAME_PATTERNS": validate_watcher_patterns,
        "WATCHER_DEVCONTAINER_NAME_PATTERNS": validate_watcher_patterns,
        "WATCHER_INTERVAL": validate_systemd_timespan,
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


def validate_host_setup_values(
    values: dict[str, str],
    example: dict[str, str],
    meminfo: dict[str, int],
    *,
    config_label: str,
    example_label: str,
    meminfo_label: str,
) -> list[str]:
    """Apply the complete data-only validator to already parsed values.

    Keeping this separate from file reading lets both ``--validate-config``
    and the standalone wizard validate the exact candidate text that is about
    to be written.  In particular, values preserved from an existing config
    are not trusted merely because the current prompt did not show them.
    """
    missing = sorted(set(example) - set(values))
    if missing:
        raise TemplateError(
            f"{config_label} is missing keys from {example_label}: {', '.join(missing)}"
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

    total_kib = meminfo.get("MemTotal", 0)
    if total_kib <= 0:
        raise TemplateError(
            f"{meminfo_label} has no positive MemTotal; cannot validate host-sized memory policy"
        )
    relationship_errors = memory_relationship_errors(values)
    if relationship_errors:
        raise TemplateError(
            "invalid memory hierarchy: "
            + "; ".join(message for _, message in relationship_errors)
        )
    # Parent/child High/Max differences are review-only observations.  Return
    # them so the CLI can report them without turning them into a refusal.
    return memory_review_warnings(values)


def validate_host_setup_config(
    config_path: Path,
    example_path: Path,
    meminfo_path: Path,
) -> list[str]:
    """Apply wizard-equivalent checks to a manually supplied config.

    This is intentionally data-only: it reads the config without sourcing it,
    so install.sh can fail before any host mutation or shell expansion occurs.
    """
    try:
        example = parse_env_file(example_path.read_text(encoding="utf-8"), strict=True)
        values = parse_env_file(config_path.read_text(encoding="utf-8"), strict=True)
        meminfo = parse_meminfo(meminfo_path.read_text(encoding="utf-8"))
    except (OSError, TemplateError) as exc:
        raise TemplateError(f"cannot read host-setup validation input: {exc}") from exc
    return validate_host_setup_values(
        values,
        example,
        meminfo,
        config_label=str(config_path),
        example_label=str(example_path),
        meminfo_label=str(meminfo_path),
    )


def migrate_legacy_config_keys(cfg_current: dict[str, str]) -> dict[str, str]:
    """Translate pre-WATCHER public names without losing operator tuning."""
    migrated = dict(cfg_current)
    for old_key, new_key in LEGACY_CONFIG_KEYS.items():
        if old_key not in cfg_current:
            continue
        if new_key in cfg_current:
            out(f"WARN: both `{old_key}` and current `{new_key}` exist; keeping `{new_key}`")
            continue
        migrated[new_key] = cfg_current[old_key]
        out(
            f"Migrating legacy `{old_key}` to `{new_key}`; the current name covers "
            "the event watcher and periodic sweep."
        )
    return migrated


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
    the guaranteed-floor prompt's actual DEFAULT stays whatever
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


_SYSTEMD_TIMESPAN_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?|\.\d+)(?:us|ms|s|min|m|h|d|w|M|y)?$",
    re.IGNORECASE,
)


def validate_systemd_timespan(value: str) -> str | None:
    """Validate the single-token duration rendered into the timer.

    Systemd also accepts compound durations such as ``1h 30min``.  The env
    file deliberately uses one token so a shell-sourced assignment remains
    unambiguous; a bare number means seconds and a suffix makes the unit
    obvious (``5min``, ``30s``).
    """
    if not value:
        return "a positive timer duration is required (for example '5min', '30s', '1.5h', or '10' seconds)"
    if not _SYSTEMD_TIMESPAN_RE.fullmatch(value):
        return (
            f"{value!r} is not a supported single-token systemd duration "
            "(use e.g. '5min', '30s', '1.5h', or a bare number of seconds)"
        )
    number = re.match(r"(?:\d+(?:\.\d+)?|\.\d+)", value)
    if number is not None and float(number.group(0)) <= 0:
        return "the sweep interval must be greater than zero"
    return None


_WATCHER_PATTERN_RE = re.compile(r"^[A-Za-z0-9_.*?\[\]!/^:+,@%=-]+$")


def validate_watcher_patterns(value: str) -> str | None:
    """Validate the space-separated shell globs used by the IO watcher.

    These values are later read from a shell-sourced env file.  Restricting
    them to glob syntax and ordinary image/name characters prevents command
    substitution or shell-control text from becoming executable config.
    """
    if not value:
        return "at least one match pattern is required"
    if not re.fullmatch(r"[^\s]+(?: [^\s]+)*", value):
        return "patterns must be one or more space-separated globs with no tabs, newlines, or repeated separators"
    for pattern in value.split(" "):
        if not _WATCHER_PATTERN_RE.fullmatch(pattern):
            return (
                f"{pattern!r} contains unsafe or unsupported characters; use "
                "letters/digits plus '*', '?', '[]', '/', ':', '.', '_', '^', '!' and '-'"
            )
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
    """Validate a value whose consumer owns the detailed grammar.

    The prompt label identifies whether this is an image, glob, or duration;
    the shared validator must not claim that every empty value is an image
    reference (that made preserved ``WATCHER_INTERVAL=`` failures misleading).
    """
    if not value:
        return "a non-empty value is required"
    return None


def validate_buildkit_image(value: str) -> str | None:
    """Require a shell-safe image reference while leaving OCI grammar to Docker.

    The installed config is sourced by shell helpers before this value reaches
    ``docker``. Docker remains the authority for whether the reference exists
    and is otherwise valid, but shell syntax must be rejected here rather than
    becoming executable configuration.
    """
    if not value:
        return "an image reference is required (e.g. 'moby/buildkit:buildx-stable-1-rootless')"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:+\[\]-]*", value):
        return (
            f"{value!r} contains shell-special characters; use a shell-safe OCI "
            "reference such as 'moby/buildkit:buildx-stable-1-rootless'"
        )
    return None


def validate_cgroup_parent(value: str) -> str | None:
    """DOCKER_DAEMON_CGROUP_PARENT. Non-empty is a hard requirement (this key
    is merged verbatim into /etc/docker/daemon.json; an empty string there is
    not a valid daemon-wide default). The data-only validator checks the
    required `.slice` shape;
    install.sh verifies LoadState=loaded and a non-empty FragmentPath after
    rendering the units and refuses to start governed services for an unknown
    slice. This matters because Docker can otherwise accept a typo and fail
    open into an unbounded transient scope."""
    if not value:
        return "a cgroup parent is required (e.g. 'dev-background.slice')"
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.slice", value):
        return (
            f"{value!r} is not a safe systemd slice name; use letters, digits, "
            "'.', '_', '@', ':' or '-' and end in '.slice' (for example "
            "'dev-background.slice')"
        )
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
    out("Measurement:")
    for bullet in (
        "Official kernel `io.cost` matrix: sequential and random read/write throughput.",
        "The benchmark fills `io.max` ceilings; it does not enable `io.cost`.",
        "The persistent target file stays on the Docker-data filesystem/device.",
        "Cache reuse requires matching target, filesystem, device identity, topology, and kernel/generator.",
    ):
        out(f"- {bullet}", hang="  ")
    out("\n-- a. IO device (IO_DEV_PATH) --")
    out("Ownership and fallback:")
    for bullet in (
        "`IO_DEV_PATH` selects the device for static `dev.slice` IOPS/bandwidth caps.",
        "Static fallback values come from `DEV_STATIC_RIOPS/WIOPS/RBW/WBW` in the template/config.",
        "Those four static values are not prompted here; edit the config deliberately if you need a different boot fallback.",
        "They are authoritative from boot until a valid baseline is applied.",
        "Empty or `auto` discovers with `findmnt`; no device omits static IO caps but keeps CPU/memory governance.",
        "No valid baseline disables matched-container IO caps; there is no global IO off switch for other governance.",
        "This answer is checked for `/dev/...` shape only; host setup must run outside the devcontainer.",
    ):
        out(f"- {bullet}", hang="  ")
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
    out("Cache contract:")
    for bullet in (
        "Stores six raw coefficients plus four `io.max` ceilings.",
        "Uses the lower sequential/random IOPS value and sequential bytes/s bandwidth.",
        "`IO_BASELINE_ENV` is read by the runtime service; it is durable state, not a report.",
        "Both paths are absolute; `--force` remeasures but never bypasses identity checks.",
    ):
        out(f"- {bullet}", hang="  ")
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
    out("Formula: `cap = floor(measured ceiling × percentage / 100)`.")
    out("Two owners, two protections:")
    for bullet in (
        "`DEV_IO_CAP_PCT` -> `dev.slice`; one pool protects production from all dev IO together.",
        "`WATCHER_IO_CAP_PCT` -> each matched container; sweep + Docker events protect tier members from each other.",
        "Buildx placement can bypass `dev.slice`, so direct watcher caps remain necessary.",
        "`IOWeight` is a relative share; `io.max` is an absolute rate cap.",
        "No valid baseline -> matched-container IO caps stay disabled; no guessed rate is applied.",
        "No host device -> the watcher skips IO caps; it does not invent a device or number.",
    ):
        out(f"- {bullet}", hang="  ")
    out("Keep both percentages below 100%: saturation queues the next latency-sensitive burst.")
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


def step_docker_daemon(
    cfg_current: dict[str, str], example_defaults: dict[str, str]
) -> tuple[str, str, str]:
    out("\n-- f. Docker daemon.json keys this tool owns --")
    out("Default placement:")
    for bullet in (
        "DOCKER_DAEMON_CGROUP_PARENT is Docker's default for containers without their own --cgroup-parent.",
        "install.sh merges only this owned key into /etc/docker/daemon.json; other keys are preserved.",
        "The value must name the intended loaded .slice; a typo can create an unbounded transient slice.",
        "A changed daemon default needs a full Docker restart; the wizard never restarts Docker itself.",
        "Verify placement after install with mdt-host-check.sh.",
    ):
        out(f"- {bullet}", hang="  ")
    cgroup_parent = walk_key(
        "Default cgroup-parent (must be a loaded .slice)",
        "DOCKER_DAEMON_CGROUP_PARENT",
        cfg_current,
        example_defaults,
        validate=validate_cgroup_parent,
    )
    out("Transient-scope backstop:")
    for bullet in (
        "docker-.scope.d applies to every Docker transient scope, even when placement is wrong or absent.",
        "It is a generous fail-open safety floor, not a tier limit; keep it above a legitimate container's peak.",
        "MemoryMax limits RAM; MemorySwapMax limits swap separately.",
        "Both values are required positive sizes and should not be tight enough to throttle healthy work.",
    ):
        out(f"- {bullet}", hang="  ")
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
        validate=validate_optional_positive_size,
        empty_token="none",
        empty_tokens=("-", "none"),
        default_source="confirmed above",
    )


def _reprompt_memory_constraints(
    avail_kib: int,
    values: dict[str, str],
) -> None:
    """Repair hard per-slice ordering without losing the whole session.

    Parent/child High/Max differences are printed as review-only warnings;
    they are never sent through ``correction()``.
    """
    del avail_kib  # MemAvailable is context, not a memory-policy budget.

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
        for warning in memory_review_warnings(values):
            out(f"review warning (does not block): {warning}", indent="  ")
        return
    raise ValueError("memory corrections did not converge after 64 prompts; the candidate was not written")


def step_slice_first_resources(
    meminfo: dict[str, int],
    cfg_current: dict[str, str],
    example_defaults: dict[str, str],
) -> dict[str, str]:
    """Walk the resource policy in the same order a user reasons about it."""
    total_kib = meminfo["MemTotal"]
    avail_kib = meminfo.get("MemAvailable", 0)
    swap_kib = read_swap_total_kib(meminfo, "")
    proposals = propose_memory_tiers(total_kib, avail_kib)
    values: dict[str, str] = {}
    host_nproc = discover_nproc()
    if not host_nproc:
        raise ValueError("nproc did not return a positive CPU count; cannot explain or derive CPU ceilings")

    out("\n-- d. Governed slices (slice-first) --")
    out("Proposal inputs:")
    for bullet in (
        f"MemTotal={kib_to_size_str(total_kib)} -> sizing source.",
        f"MemAvailable={kib_to_size_str(avail_kib) if avail_kib > 0 else 'unavailable'} -> context only.",
        f"nproc={host_nproc}; SwapTotal={kib_to_size_str(swap_kib)}.",
        "Proposals are caps/thresholds, not RAM reservations; they use physical MemTotal because MemAvailable is transient.",
    ):
        out(f"- {bullet}", hang="  ")
    out("Memory controls:")
    for bullet in (
        "`MemoryMin` -> hard reclaim protection.",
        "`MemoryLow` -> soft, best-effort protection.",
        "`MemoryHigh` -> soft reclaim/throttling threshold.",
        "`MemoryMax` -> hard RAM ceiling; `MemorySwapMax` is separate swap.",
        "Every field is optional; Enter keeps the default and `-`/`none` omits it.",
    ):
        out(f"- {bullet}", hang="  ")
    out("Memory hierarchy:")
    for line in (
        "dev.slice (parent: whole dev estate)",
        "+-- dev-interactive.slice (child: IDE/devcontainers)",
        "+-- dev-background.slice (child: stacks/tests)",
        "+-- dev-gates.slice (child: disposable lanes)",
        "+-- dev-buildkitd.slice (child: shared builder)",
        "+-- dev-memory_min_guaranteed.slice (sibling: admitted floor)",
    ):
        out(line, indent="  ")
    out("Validation policy:")
    for bullet in (
        "Sibling Min/Low/High controls are independent; their values are not summed.",
        "Each slice still requires Min <= Low <= High <= Max for configured fields.",
        "A child Min/Low without matching parent protection is allowed but may be ineffective; the wizard reports it.",
        "A single child High/Max above its parent is re-prompted; sibling totals above the parent are advisory only.",
    ):
        out(f"- {bullet}", hang="  ")
    out("Starting proposals (review before accepting):")
    for line in (
        "dev.slice: MemoryHigh=75% / MemoryMax=100% of physical RAM",
        "dev-interactive.slice: MemoryLow=unset / High=20% / Max=32%",
        "dev-background.slice: MemoryLow=unset / High=32% / Max=50%",
        "dev-gates.slice: MemoryLow=unset / High=5% / Max=10%",
        "dev-buildkitd.slice: MemoryLow=unset / High=10% / Max=12%",
        "MemoryLow is unset because the parent MemoryLow is unset; configure the parent first if soft protection is wanted.",
        "These are independent slice controls; the wizard does not add sibling values together.",
    ):
        out(f"- {line}", hang="  ")
    _, suggested_min_kib, suggestion_formula = propose_memory_min_guaranteed_suggestion(
        total_kib,
        {key: proposals[key] for key in (
            "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_HIGH",
            "DEV_GATES_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_HIGH",
        )},
    )
    out("Advisory guaranteed-floor math:")
    for bullet in (
        suggestion_formula,
        "This is not a RAM reservation and is never selected automatically.",
        f"Optional ceiling suggestion: {kib_to_size_str(suggested_min_kib)}.",
    ):
        out(f"- {bullet}", hang="  ")

    out("\n  dev.slice — shared parent")
    out("Parent role:")
    for bullet in (
        "One control applies to the whole dev estate; child controls shape each sibling subtree.",
        "The guaranteed `MemoryMin` value is mirrored on its dedicated sibling; there is one ceiling to decide.",
        "An unset parent control leaves that level at systemd's default.",
    ):
        out(f"- {bullet}", hang="  ")
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
        out(f"- `{field}` is the parent control for the whole dev estate, not one container; `-` leaves it unset.", hang="  ")
        values[key] = walk_key(
            f"  {field} (optional; '-' = no directive)",
            key, cfg_current, example_defaults, proposals.get(key),
            validate=validate_optional_positive_size, allow_empty_token=True,
        )

    out("Zswap policy:")
    for bullet in (
        "yes lets cold compressed pages drain to disk swap, keeping zswap available as a cache.",
        "no keeps the cold tail in compressed RAM, reducing swap-out but consuming RAM.",
        "A no on any ancestor disables writeback for its descendants.",
    ):
        out(f"- {bullet}", hang="  ")
    out("CPU ceilings:")
    for bullet in (
        "Host/production reserve: cores excluded from the aggregate dev.slice auto-quota.",
        "Child reserve: cores excluded from each child slice's auto-quota.",
        "Neither reserve pins a CPU; they affect only a blank CPUQuota.",
        f"With nproc={host_nproc}, reserve 1 -> root {max(1, host_nproc - 1) * 100}%; reserve 3 -> child {max(1, host_nproc - 3) * 100}%.",
        "An explicit N% is the hard cap and overrides auto-detection; CPUWeight is only a relative share.",
    ):
        out(f"- {bullet}", hang="  ")
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

    out("Swap cascade:")
    for bullet in (
        "Swap is separate from RAM; MemorySwapMax limits swap only.",
        "dev.slice blank -> DEV_SWAP_CASCADE_PCT% of host SwapTotal.",
        "Child blank -> the same percentage of dev.slice's derived swap ceiling.",
        "An explicit size overrides only that slice; blank means derived, not unlimited.",
    ):
        out(f"- {bullet}", hang="  ")
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
    out("IOPS sub-ceiling:")
    for bullet in (
        "Applies only to dev-gates.slice and dev-buildkitd.slice.",
        "Percentage of dev.slice's already-derived IOPS cap, not the raw device ceiling.",
        "IOPS only; bandwidth inherits the parent pool.",
        "The root uses absolute io.max caps, so it has no IOWeight setting.",
    ):
        out(f"- {bullet}", hang="  ")
    values["DEV_SUBSLICE_IOPS_PCT"] = walk_key(
        "  child IOPS sub-ceiling (percentage of dev.slice IOPS cap; bare 1-100)",
        "DEV_SUBSLICE_IOPS_PCT", cfg_current, example_defaults,
        validate=validate_pct_1_100,
    )

    out("Cgroup mount safety:")
    for bullet in (
        "The periodic host service checks `memory_recursiveprot`; without it, slice MemoryMin/Low may not protect container pages.",
        "`fix` remounts the host cgroup filesystem when the required flag is missing.",
        "`warn` only records the problem; protection may remain ineffective until an operator repairs the mount.",
        "This setting is host-only and is unrelated to Docker container placement.",
    ):
        out(f"- {bullet}", hang="  ")
    values["CGROUP2_FLAGS"] = walk_key(
        "  cgroup2 mount policy (fix missing flags / warn only)",
        "CGROUP2_FLAGS", cfg_current, example_defaults,
        validate=validate_cgroup2_flags,
    )

    out("\n-- e. Guaranteed memory floor (opt-in; one shared ceiling) --")
    out("Guaranteed floor:")
    for bullet in (
        "Optional hard `MemoryMin` for explicitly admitted containers only.",
        "It does not allocate RAM or protect ordinary devcontainers.",
        "The value is rendered on both `dev.slice` and its sibling; a new answer changes both.",
        "The advisory 5%-of-leftover suggestion is never selected automatically.",
    ):
        out(f"- {bullet}", hang="  ")
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
        out("Child controls:")
        for bullet in (
            "Apply to this slice's subtree; sibling values are independent and are not summed.",
            "Optional fields: Enter keeps the default; `-` removes the directive.",
            "Hard rule: this slice's Min <= Low <= High <= Max.",
            "Child High/Max above the parent -> re-prompt that child; sibling totals above the parent do not block.",
            "`MemoryMax` is RAM; `MemorySwapMax` below is swap only.",
        ):
            out(f"- {bullet}", hang="  ")
        for field in ("MIN", "LOW", "HIGH", "MAX"):
            key = f"{prefix}_MEMORY_{field}"
            values[key] = walk_key(
                f"  Memory{field.title()} (optional; '-' = no directive)",
                key, cfg_current, example_defaults, proposals.get(key),
                validate=validate_optional_positive_size, allow_empty_token=True,
            )
        out("CPU and IO controls:")
        for bullet in (
            "CPUQuota is this slice's hard cap; blank derives from the child reserve above.",
            "Explicit N% wins; CPUWeight and IOWeight are relative shares, not caps.",
            "Weights matter only when siblings contend.",
        ):
            out(f"- {bullet}", hang="  ")
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
        out("Swap control:")
        for bullet in (
            "MemorySwapMax limits swap only.",
            "Blank cascades from the live parent; an explicit size affects only this slice.",
        ):
            out(f"- {bullet}", hang="  ")
        values[f"{prefix}_MEMORY_SWAP_MAX"] = walk_key(
            "  MemorySwapMax (blank = derived swap-only ceiling)",
            f"{prefix}_MEMORY_SWAP_MAX", cfg_current, example_defaults,
            validate=validate_size_or_auto,
        )
        out("Zswap control:")
        for bullet in (
            "MemoryZSwapWriteback is a per-slice yes/no policy, not a size or memory limit.",
            "A no on an ancestor still disables writeback below it.",
        ):
            out(f"- {bullet}", hang="  ")
        values[f"{prefix}_ZSWAP_WRITEBACK"] = walk_yn_key(
            "  MemoryZSwapWriteback", f"{prefix}_ZSWAP_WRITEBACK",
            cfg_current, example_defaults,
        )
        if prefix in ("DEV_BACKGROUND", "DEV_GATES"):
            out("OOM policy:")
            out("- systemd-oomd may act at the configured pressure percentage; this is separate from MemoryMax.", hang="  ")
            values[f"{prefix}_OOM_PRESSURE_LIMIT"] = walk_key(
                "  ManagedOOMMemoryPressureLimit (systemd percentage, e.g. 75%)",
                f"{prefix}_OOM_PRESSURE_LIMIT", cfg_current, example_defaults,
                validate=validate_systemd_pct_1_100,
            )
        if prefix == "DEV_BUILDKITD":
            out("BuildKit-specific controls:")
            for bullet in (
                "The host-managed rootless worker is the only approved normal worker.",
                "Accidental docker-container workers: terminate removes them; report-only logs them.",
            ):
                out(f"- {bullet}", hang="  ")
            values["DEV_BUILDKITD_IMAGE"] = walk_key(
                "  BuildKit image (rootless OCI reference)", "DEV_BUILDKITD_IMAGE",
                cfg_current, example_defaults, validate=validate_buildkit_image,
            )
            out("Solver parallelism:")
            for bullet in (
                "Limits simultaneous RUN/cache operations inside this daemon.",
                "Does not limit the number of client builds.",
                "Separate from release REPACK_JOBS and REPACK_CONCURRENCY.",
            ):
                out(f"- {bullet}", hang="  ")
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
    out("Purpose and precedence:")
    for bullet in (
        "Caps the blast radius of one unlabelled container inside a governed slice.",
        "The memory watcher applies caps on cgroup creation when inotify works; the periodic sweep is the backstop.",
        "A container's explicit docker run --memory wins; these values fill only an omitted memory limit.",
    ):
        out(f"- {bullet}", hang="  ")
    out("Matching and cadence:")
    for bullet in (
        "Docker events apply IO caps immediately; the periodic timer is the restart-gap/backstop sweep.",
        "The same interval also checks cgroup mount flags, so a shorter interval reduces that repair window at some host overhead.",
        "Image patterns match `.Config.Image`; name patterns match the Docker container name.",
        "Patterns are space-separated shell globs such as `*test-runner*`; at least one pattern is required in each field.",
        "Changing these values affects future sweeps/events after the service reloads its config; re-run install.sh or restart the watcher.",
    ):
        out(f"- {bullet}", hang="  ")
    values["WATCHER_INTERVAL"] = walk_key(
        "Periodic sweep interval (systemd duration; e.g. 5min or 30s)",
        "WATCHER_INTERVAL", cfg_current, example_defaults,
        validate=validate_systemd_timespan,
    )
    for label, key in (
        ("test-runner image match patterns", "WATCHER_TESTRUNNER_IMAGE_PATTERNS"),
        ("BuildKit container-name match patterns", "WATCHER_BUILDKIT_NAME_PATTERNS"),
        ("devcontainer-name match patterns", "WATCHER_DEVCONTAINER_NAME_PATTERNS"),
    ):
        values[key] = walk_key(
            f"{label} (space-separated shell globs)", key,
            cfg_current, example_defaults,
            validate=validate_watcher_patterns,
        )
    values["WATCHER_PER_CONTAINER_MEMORY_MAX"] = walk_key(
        "Per-container MemoryMax (interactive/background; positive size)",
        "WATCHER_PER_CONTAINER_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_positive_size,
    )
    out("Gate-container ceiling:")
    for bullet in (
        "dev-gates.slice uses its own per-container MemoryMax, separate from interactive/background.",
        "It is sized for one lane container; making it too small recreates the headroom incident this tier fixes.",
    ):
        out(f"- {bullet}", hang="  ")
    values["WATCHER_PER_CONTAINER_GATES_MEMORY_MAX"] = walk_key(
        "Per-container MemoryMax (gates; positive size)",
        "WATCHER_PER_CONTAINER_GATES_MEMORY_MAX",
        cfg_current,
        example_defaults,
        validate=validate_positive_size,
    )
    out("Per-container MemoryHigh:")
    out("- Derived as this percentage of the selected per-container MemoryMax; it is a soft throttle below the hard cap.", hang="  ")
    values["WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT"] = walk_key(
        "Per-container MemoryHigh (% of that MemoryMax; bare 1-100)",
        "WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT",
        cfg_current,
        example_defaults,
        validate=validate_pct_1_100,
    )
    out("Per-container swap:")
    for bullet in (
        "Not prompted here.",
        "At runtime it uses the matched slice's live memory.swap.max and applies DEV_SWAP_CASCADE_PCT.",
        "It is not re-derived from this file; see read_slice_swap_max_bytes() in mdt-dev-cap-watcher.py.",
    ):
        out(f"- {bullet}", hang="  ")
    return values


# ─── orchestration ─────────────────────────────────────────────────────────


def explain_install_map() -> None:
    """Orient the operator before the first technical prompt.

    Keep this as a terminal-width-friendly list: it is the wizard's map of
    installed consumers, not another configuration section to answer.
    """
    out("\n-- install map: what this creates and enables --")
    out("The prompts fill one host configuration. This is the installation shape:")
    for line in (
        "dev.slice",
        "+-- dev-interactive.slice / dev-background.slice / dev-gates.slice",
        "+-- dev-memory_min_guaranteed.slice / dev-buildkitd.slice",
        "+-- host services, Docker drop-ins, scripts, and health check",
    ):
        out(line, indent="  ")
    out("What each part does:")
    for bullet in (
        "`dev*.slice` units — static CPU, memory, swap, weights, and tight IO fallback.",
        "`dev-gates.slice` is ready for gate consumers; run-gate, cmru tester-gate, and srdm still use `dev-background.slice` until they adopt `CGROUP_PARENT_DEV_GATES`.",
        "`mdt-host-slices.service` + timer — measured root IO caps, zswap fallback, sweep, and audit.",
        "`mdt-io-cap-watcher.service` — Docker start events -> immediate IO caps.",
        "Transient scopes and unreliable Buildx placement are why this watcher exists; the sweep is its backstop.",
        "`mdt-dev-cap-watcher.service` — inotify memory backstop, only when available.",
        "`mdt-buildkitd.service` + remote — rootless worker in `dev-buildkitd.slice`.",
        "BuildKit image and maximum solver parallelism are configured here.",
        "`mdt-buildkit-guard.service` — enforce accidental Buildx terminate/report-only policy.",
        "Docker `cgroup-parent` merge + `docker-.scope` backstop — placement and a transient-scope floor.",
        "Installed scripts + `mdt-host-check.sh` — baseline, runtime control, guard, and health evidence.",
    ):
        out(f"- {bullet}", hang="  ")
    out("Where answers go:")
    for bullet in (
        "`DEV_IO_CAP_PCT` -> `mdt-host-slices.service` root cap.",
        "`WATCHER_IO_CAP_PCT` -> periodic sweep + IO watcher.",
        "Slice resource keys -> their matching slice units.",
        "BuildKit image/parallelism/policy -> BuildKit + guard.",
        "Docker parent/backstop -> daemon.json + scope drop-in.",
        "Watcher memory keys -> memory watcher.",
    ):
        out(f"- {bullet}", hang="  ")
    out(
        "Safety boundary: no host mutation occurs until prompting and validation "
        "complete. Docker is not restarted automatically; it restarts only when "
        "the operator explicitly requests it."
    )


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
              - Existing output values win, including intentional empty values.
              - For a missing key, a live-host proposal wins over the example.
              - Enter accepts the shown default; `-` (or `none`) clears optional memory.
              - Blank CPUQuota and MemorySwapMax mean derive at install time, not unlimited.
              - Sizes are binary (`2G` means 2 GiB); a bare number means bytes.
              - Percentage prompts say whether they expect a bare number or `400%`.
              - Old watcher names are migrated to their `WATCHER_*` names when an
                existing config is opened; a plain install asks for this migration first.

            What the sizing uses
              - Memory proposals use physical MemTotal; MemAvailable is context only.
              - Sibling Min/Low/High controls are independent, not a summed budget.
              - Each slice enforces Min <= Low <= High <= Max; one child High/Max
                above its parent is re-prompted, while sibling totals above the
                parent are advisory and do not block.
              - CPU auto-quota uses nproc minus the selected host/child reserve, floored
                at one core.
              - IO percentages use measured io.cost ceilings: lower IOPS and sequential
                bandwidth; no current cache leaves root unit statics authoritative and
                disables guessed per-container IO caps.

            Important files and side effects
              - Reads: --example, existing --output, memory/swap facts, Docker mount,
                IO_BASELINE_ENV/TESTFILE, and the baseline adapter/generator.
              - Writes --output atomically at the end. Selecting the benchmark also
                writes the baseline cache/target and temporarily saturates its device.
              - install.sh writes /etc/mdt and merges owned Docker keys into
                /etc/docker/daemon.json.
              - Docker restarts only when --restart-docker is explicitly supplied.
              - Host-only preflight runs before these reads/writes and prompts.

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
            review_warnings = validate_host_setup_config(
                args.validate_config,
                args.example,
                args.meminfo_path,
            )
        except (OSError, TemplateError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"OK: strict validation passed for {args.validate_config}")
        for warning in review_warnings:
            print(f"REVIEW WARNING (does not block): {warning}")
        return 0

    if not args.example.exists():
        print(f"ERROR: template not found: {args.example}", file=sys.stderr)
        return 1
    example_text = args.example.read_text()
    example_defaults = parse_env_file(example_text)

    cfg_current: dict[str, str] = {}
    if args.output.exists():
        try:
            cfg_current = parse_env_file(args.output.read_text(), strict=True)
            out(f"Found an existing `{args.output}` -- using its values as defaults where they are actually set.")
            cfg_current = migrate_legacy_config_keys(cfg_current)
        except (OSError, TemplateError) as exc:
            print(
                f"ERROR: could not safely read existing `{args.output}`: {exc}",
                file=sys.stderr,
            )
            print(
                "No configuration was written. Repair the existing file or use "
                "install.sh --wizard --force after reviewing the replacement.",
                file=sys.stderr,
            )
            return 1

    try:
        meminfo = parse_meminfo(args.meminfo_path.read_text())
    except OSError as exc:
        out(
            f"ERROR: could not read `{args.meminfo_path}`: {exc}. A positive "
            "`MemTotal` value is required for host-sized limits; `MemAvailable` "
            "is optional context. Provide a readable `--meminfo-path` or fix the host."
        )
        meminfo = {}
    try:
        swaps_text = args.swaps_path.read_text()
    except OSError:
        swaps_text = ""
    swap_kib = read_swap_total_kib(meminfo, swaps_text)
    meminfo.setdefault("SwapTotal", swap_kib)

    if meminfo.get("MemTotal", 0) <= 0:
        print(
            f"ERROR: `{args.meminfo_path}` does not contain a positive MemTotal; "
            "the wizard cannot derive host-sized memory limits and will not run "
            "the IO benchmark or write a configuration. MemAvailable is optional "
            "context, not a sizing input.",
            file=sys.stderr,
        )
        return 1

    out("== mdt host-setup wizard ==")
    out("Run contract:")
    for bullet in (
        "Reads the template, existing output, host memory/swap facts, Docker mount, and IO cache.",
        "Existing values win; otherwise defaults come from a live-host proposal, then the example.",
        "Writes the candidate atomically only after all prompts and validation finish.",
    ):
        out(f"- {bullet}", hang="  ")
    out(f"template: `{args.example}`")
    out(f"writing:  `{args.output}`")
    if args.skip_run_offer:
        out("Installer mode:")
        out("- `install.sh` validates this candidate before backup, rendering, or host apply.", hang="  ")
    else:
        out("Standalone mode:")
        out("- Writes only the candidate; rendering and Docker restart wait for the final install offer.", hang="  ")
    out("Prompt conventions:")
    for bullet in (
        "Enter accepts the shown default; `-` clears optional memory directives.",
        "Blank CPUQuota and MemorySwapMax mean derive at install time, not unlimited.",
        "A cleared memory directive is omitted from the rendered unit.",
    ):
        out(f"- {bullet}", hang="  ")
    out(
        "Terminal formatting: text in backticks is highlighted only on an interactive "
        "TTY. Redirected output keeps the backticks as readable markers; set `NO_COLOR` "
        "(even to an empty value) to disable ANSI highlighting."
    )
    out("Important files and side effects:")
    for bullet in (
        "Template and output paths are read/written; baseline cache and target identity are checked in section b.",
        "The cache and target change only if the benchmark is selected.",
        "`install.sh` writes `/etc/mdt`, merges owned Docker keys, and restarts Docker only with `--restart-docker`.",
    ):
        out(f"- {bullet}", hang="  ")
    out(
        f"Host swap visible to the later cascade: `{kib_to_size_str(swap_kib)}` "
        "from `/proc/meminfo` (or `/proc/swaps` when the former has no SwapTotal). "
        "A zero value means automatic swap ceilings cannot be derived."
    )
    explain_install_map()

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

    # Do the same complete validation used by install.sh against the exact
    # candidate text before touching the requested output.  Prompt validators
    # cover answers shown in this run, but an existing config also contributes
    # keys that are not prompted (watcher patterns, interval, and other future
    # additions); those preserved values must not bypass validation in
    # standalone mode.
    try:
        candidate_values = parse_env_file(text, strict=True)
        final_warnings = validate_host_setup_values(
            candidate_values,
            example_defaults,
            meminfo,
            config_label=str(args.output),
            example_label=str(args.example),
            meminfo_label=str(args.meminfo_path),
        )
    except (TemplateError, ValueError) as exc:
        sys.stdout.flush()
        print(f"ERROR: candidate validation failed: {exc}", file=sys.stderr)
        print("No configuration was written; correct the existing values or re-run with a clean candidate.", file=sys.stderr)
        return 1

    if final_warnings:
        out("Final review warnings (does not block):")
        for warning in final_warnings:
            out(f"- {warning}", hang="  ")

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
            # in step b; --with-baseline would only repeat the same ~12-minute
            # disk-saturating measurement for an identical result.
            out(
                "Not offering --with-baseline: the IO baseline was just measured above in "
                "this same run, so re-running it during install would repeat a ~12-minute "
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
