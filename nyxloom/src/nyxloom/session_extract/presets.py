"""The four `nyxloom-harness extract --preset` bundles (operator decision 2026-10-06).

A preset is a named, documented bundle of OPTIONS from the five option groups
(see cli_registry.EXTRACT_GROUPS). It adds nothing a user cannot spell by hand:
`--preset review` is exactly its expansion text. Rules:

* An explicit option always wins. A value option the user passed is never
  replaced; a boolean member is skipped when the user passed its contradicting
  flag (CANCELLED_BY below: `--no-ledger`, `--no-stop-state`,
  `--no-strip-cd-prefix`, `--prose-only` vs `--no-prose`).
* `--help` prints every preset with its exact expansion (help_paragraph) and
  one example line per preset (example_lines); tests/test_session_extract_presets.py
  pins each expansion and that every member is a real, grouped option.
* `--successor-brief` implies `--preset successor`.

The four presets:

watch      follow a LIVE session for an operator: operator messages and
           assistant prose only, timestamped, coloured (--color/--no-color),
           or `--jsonl` for an editor extension.
successor  prime a fresh agent: compressed tool-call rendering, whole-session
           ledger, stop state (add --successor-brief for the one document).
review     audit what an agent actually did at full fidelity: every call, the
           call itself for outside effects, errors with their calls, every
           timestamp, no collapsing, the ledger.
ledger     only the external effects, touched files and stop state, no prose,
           for incident triage or cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str
    summary: str
    values: tuple[tuple[str, str], ...]  # (argparse attr, value), shown in this order
    flags: tuple[str, ...]  # boolean attrs the preset turns on
    claude_only: bool  # needs Claude Code transcripts (tool calls / ledger)
    fixed_span: bool  # emits one finished document: no --follow, no --json

    @property
    def expansion(self) -> str:
        parts = [f"--{attr.replace('_', '-')} {value}" for attr, value in self.values]
        parts += [f"--{attr.replace('_', '-')}" for attr in self.flags]
        return " ".join(parts)


PRESETS: dict[str, Preset] = {
    "watch": Preset(
        "watch",
        "follow a live session: operator messages and assistant prose only, timestamped",
        values=(("profile", "all"), ("timestamps", "all")),
        flags=("prose_only",),
        claude_only=False,
        fixed_span=False,
    ),
    "successor": Preset(
        "successor",
        "prime a fresh agent: compressed calls, whole-session ledger, stop state",
        values=(
            ("profile", "all"),
            ("tool_calls", "intent-or-call"),
            ("tool_errors", "show"),
            ("edit_calls", "collapse"),
            ("read_calls", "collapse"),
            ("effect_calls", "always"),
            ("timestamps", "gaps"),
            ("path_aliases", "auto"),
        ),
        flags=("strip_cd_prefix", "ledger", "stop_state"),
        claude_only=True,
        fixed_span=True,
    ),
    "review": Preset(
        "review",
        "audit what an agent did at full fidelity: every call, errors with calls, effects, ledger",
        values=(
            ("profile", "all"),
            ("tool_calls", "intent-or-call"),
            ("tool_errors", "show"),
            ("edit_calls", "show"),
            ("read_calls", "show"),
            ("effect_calls", "always"),
            ("timestamps", "all"),
        ),
        flags=("ledger",),
        claude_only=True,
        fixed_span=True,
    ),
    "ledger": Preset(
        "ledger",
        "only external effects, touched files and stop state, no prose (incident triage)",
        values=(),
        flags=("no_prose", "ledger", "stop_state"),
        claude_only=True,
        fixed_span=True,
    ),
}

# A boolean preset member is NOT applied when any of these attrs is truthy on
# the parsed args: the user asked for its opposite explicitly.
CANCELLED_BY: dict[str, tuple[str, ...]] = {
    "prose_only": ("no_prose",),
    "no_prose": ("prose_only",),
    "strip_cd_prefix": ("no_strip_cd_prefix",),
    "ledger": ("no_ledger",),
    "stop_state": ("no_stop_state",),
}

#: Spelling of a preset's attribute as a command-line option.
def option_of(attr: str) -> str:
    return "--" + attr.replace("_", "-")


def preset_name(args) -> str | None:
    """The preset in force: `--preset NAME`, else `successor` when
    `--successor-brief` implies it."""
    name = getattr(args, "preset", None)
    if name is None and getattr(args, "successor_brief", False):
        return "successor"
    return name


def resolve(args) -> dict[str, object]:
    """attr -> value the preset would set on `args`, honouring explicit options.
    Pure: nothing on `args` is mutated (cmd_extract applies the result)."""
    name = preset_name(args)
    if name is None:
        return {}
    preset = PRESETS[name]
    changes: dict[str, object] = {}
    for attr, value in preset.values:
        if getattr(args, attr, None) is not None:
            continue
        # The deprecated alias spelling of --tool-calls counts as explicit.
        if attr == "tool_calls" and getattr(args, "show_tool_calls", False):
            continue
        changes[attr] = value
    for attr in preset.flags:
        if getattr(args, attr, False):
            continue
        if any(getattr(args, other, False) for other in CANCELLED_BY.get(attr, ())):
            continue
        changes[attr] = True
    return changes


def effective(args, attr: str, default=None):
    """The value of `attr` after the preset is applied (explicit wins)."""
    changes = resolve(args)
    if attr in changes:
        return changes[attr]
    value = getattr(args, attr, default)
    return default if value is None else value


def help_paragraph() -> str:
    """Every preset and its exact option set, for `--help`."""
    lines = ["Presets (--preset NAME; an explicit option always overrides the preset):"]
    for preset in PRESETS.values():
        lines.append(f"  {preset.name}: {preset.summary}.")
        lines.append(f"      = {preset.expansion}")
    lines.append("  --successor-brief implies --preset successor.")
    return "\n".join(lines)


def example_lines() -> tuple[str, ...]:
    """One usage example per preset."""
    tails = {
        "watch": "--follow",
        "successor": "--successor-brief --order @ORDER.md",
        "review": "",
        "ledger": "",
    }
    return tuple(
        " ".join(
            part for part in (
                "nyxloom-harness extract SESSION_LOG", f"--preset {name}", tails[name],
            ) if part
        ) + f"    # {preset.summary}"
        for name, preset in PRESETS.items()
    )


def option_help() -> str:
    """The `--preset` option's own help text."""
    return "Named bundle of options (explicit options override): " + "; ".join(
        f"`{p.name}` = {p.expansion}" for p in PRESETS.values()
    ) + " (successor/review/ledger: Claude Code)"
