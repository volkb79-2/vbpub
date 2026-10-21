"""Minimal apt_preferences(5) reader, shared by the pure-parsing oracle and the image-level check.

Only what the preview-archive pins need: stanzas of `Package`, `Pin: release k=v[, k=v...]` and
`Pin-Priority`. The conditions of ONE `Pin: release` line are a logical AND, so each stanza exposes them
as a dict and a repeated key is reported rather than silently collapsed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class PreferenceError(ValueError):
    pass


@dataclass(frozen=True)
class Stanza:
    package: str
    pin: str
    priority: int
    conditions: tuple[tuple[str, str], ...] = field(default=())

    @property
    def is_release_pin(self) -> bool:
        return self.pin.startswith("release ")


def parse_preferences(text: str) -> list[Stanza]:
    stanzas: list[Stanza] = []
    fields: dict[str, str] = {}

    def flush() -> None:
        if not fields:
            return
        missing = {"Package", "Pin", "Pin-Priority"} - fields.keys()
        if missing:
            raise PreferenceError(f"stanza {fields!r} lacks {sorted(missing)}")
        pin = fields["Pin"].strip()
        conditions: tuple[tuple[str, str], ...] = ()
        if pin.startswith("release "):
            pairs = []
            for part in pin[len("release "):].split(","):
                key, sep, value = part.strip().partition("=")
                if not sep or not key or not value:
                    raise PreferenceError(f"unreadable release condition {part.strip()!r} in {pin!r}")
                pairs.append((key.strip(), value.strip()))
            conditions = tuple(pairs)
        try:
            priority = int(fields["Pin-Priority"].strip())
        except ValueError as exc:
            raise PreferenceError(f"Pin-Priority is not an integer: {fields['Pin-Priority']!r}") from exc
        stanzas.append(Stanza(fields["Package"].strip(), pin, priority, conditions))
        fields.clear()

    for raw in text.splitlines():
        if not raw.strip():
            flush()
            continue
        if raw.lstrip().startswith("#"):
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise PreferenceError(f"not a `Field: value` line: {raw!r}")
        key = key.strip()
        if key in fields:
            raise PreferenceError(f"duplicate field {key!r} inside one stanza")
        fields[key] = value.strip()
    flush()
    return stanzas


def archive_pin_problems(stanzas: list[Stanza], archives: tuple[str, ...], priority: int) -> list[str]:
    """Why `stanzas` do not pin every archive in `archives` to `priority` on its own; empty = correct.

    A stanza counts for an archive only when its sole release condition is `a=<archive>`: a multi-condition
    line is an AND and matches no archive, which is exactly the mistake this guards against.
    """
    problems: list[str] = []
    for stanza in stanzas:
        if stanza.is_release_pin and len(stanza.conditions) > 1:
            problems.append(
                f"`Pin: {stanza.pin}` has {len(stanza.conditions)} conditions that must ALL match (AND); "
                "use one stanza per archive"
            )
    for archive in archives:
        matching = [s for s in stanzas if s.is_release_pin and s.package == "*" and s.conditions == (("a", archive),)]
        if len(matching) != 1:
            problems.append(f"archive {archive!r} needs exactly one `Package: *` stanza of its own, found {len(matching)}")
        elif matching[0].priority != priority:
            problems.append(f"archive {archive!r} is pinned to {matching[0].priority}, expected {priority}")
    return problems
