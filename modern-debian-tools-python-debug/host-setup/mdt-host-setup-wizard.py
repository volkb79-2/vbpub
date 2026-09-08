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


def parse_env_file(text: str) -> dict[str, str]:
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
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
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
    remembered. That is the correct trade and costs nothing observable: on
    every key whose proposal is None the chain simply falls through to the
    example's own default, which for the one key where empty is the intended
    answer (DEV_MEMORY_MIN_GUARANTEED_CEILING) is itself empty."""
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
    """DEV_BUILDKITD_CPU_QUOTA. Empty means "auto-detect nproc-2 cores at
    install time" (this key's own documented convention, NOT "unlimited"),
    so empty is valid. Anything else must be systemd's own CPUQuota=
    percentage shape: an integer followed by '%', greater than zero."""
    if not value:
        return None
    match = _CPU_QUOTA_RE.match(value)
    if not match:
        return f"{value!r} is not a systemd CPUQuota= percentage -- use N% (e.g. '400%' for 4 cores), or leave it empty to auto-detect"
    if int(match.group(1)) <= 0:
        return "a CPUQuota of 0% would stop buildkitd from running at all -- give at least '100%' (one core), or leave it empty to auto-detect"
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
        "there is nothing to take a percentage OF, and the deliberately tight `DEV_STATIC_*` "
        "caps in host-setup.env remain in force."
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
    if swap_kib == 0:
        out(
            "This host reports NO SWAP -- the swap-ceiling proposals below are 0 accordingly."
        )

    proposals = propose_memory_tiers(total_kib, avail_kib, swap_kib)
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
        ("DEV_INTERACTIVE_MEMORY_MIN", "  MemoryMin"),
        ("DEV_INTERACTIVE_MEMORY_LOW", "  MemoryLow"),
        ("DEV_INTERACTIVE_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_INTERACTIVE_MEMORY_MAX", "  MemoryMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, proposals[key], validate=validate_size_required
        )

    out()
    out(
        "`dev-background.slice` -- test, build and gate containers. Its swap ceiling is "
        "deliberately relaxed, because the two failure modes are not equally bad here: a "
        "build that swaps just finishes slowly, while a build that OOMs fails outright and "
        f"has to be re-run. Sized against THIS host's own {swap_gib:.1f}GiB of swap, not a "
        "fixed number carried over from another machine.",
        indent="  ",
        hang="  ",
    )
    for key, label in (
        ("DEV_BACKGROUND_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_BACKGROUND_MEMORY_MAX", "  MemoryMax"),
        ("DEV_BACKGROUND_MEMORY_SWAP_MAX", "  MemorySwapMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, proposals[key], validate=validate_size_required
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
        ("DEV_BUILDKITD_MEMORY_HIGH", "  MemoryHigh"),
        ("DEV_BUILDKITD_MEMORY_MAX", "  MemoryMax"),
        ("DEV_BUILDKITD_MEMORY_SWAP_MAX", "  MemorySwapMax"),
    ):
        values[key] = walk_key(
            label, key, cfg_current, example_defaults, proposals[key], validate=validate_size_required
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
        "'auto-detect (nproc - 2) cores at install time', floored at one core. An explicit "
        "percentage always overrides that auto-detection -- systemd counts one core as 100%, "
        "so `400%` means four cores. Do not assume empty means uncapped here; it does not."
    )
    cpu_quota = walk_key(
        "CPUQuota (empty = auto-detect nproc-2 cores)",
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
