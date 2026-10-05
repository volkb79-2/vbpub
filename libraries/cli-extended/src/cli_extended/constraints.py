"""Declarative option constraints for registered verbs.

A constraint is a structural relationship between options of one verb:
"A needs one of B, C", "A and B never together", "A needs --mode to be X".
Rules that depend on configuration, runtime state, or domain data stay in the
consumer's handler. Presence means "the parsed value differs from the
action's default"; registration refuses any referenced action whose default
is not ``None``, ``False`` or an empty list/tuple, so presence cannot be
misdetected.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_MISSING = object()


def _check_flag(flag: Any, *, what: str) -> None:
    if not isinstance(flag, str) or not flag.startswith("--") or flag == "--":
        raise ValueError(f"{what} must be a long option flag starting with '--': {flag!r}")


def _check_flags(flags: Any, *, what: str, minimum: int) -> None:
    if not isinstance(flags, tuple):
        raise ValueError(f"{what} must be a tuple of option flags")
    if len(flags) < minimum:
        raise ValueError(f"{what} needs at least {minimum} option flag(s)")
    for flag in flags:
        _check_flag(flag, what=what)
    if len(set(flags)) != len(flags):
        raise ValueError(f"{what} must not contain duplicate flags")


def _check_reason(reason: Any) -> None:
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or "\n" in reason
        or "\r" in reason
    ):
        raise ValueError("reason must be a non-empty single-line string")


@dataclass(frozen=True)
class Requires:
    """If ``option`` is present, at least one of ``any_of`` must be present."""

    option: str
    any_of: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _check_flag(self.option, what="option")
        _check_flags(self.any_of, what="any_of", minimum=1)
        if self.option in self.any_of:
            raise ValueError("option must not appear in its own any_of")
        _check_reason(self.reason)

    @property
    def flags(self) -> tuple[str, ...]:
        return (self.option, *self.any_of)

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": "requires",
            "option": self.option,
            "any_of": list(self.any_of),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Conflicts:
    """At most one of ``options`` may be present."""

    options: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _check_flags(self.options, what="options", minimum=2)
        _check_reason(self.reason)

    @property
    def flags(self) -> tuple[str, ...]:
        return self.options

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": "conflicts",
            "options": list(self.options),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RequiresChoice:
    """If ``option`` is present, ``target`` must be present with a value in ``values``."""

    option: str
    target: str
    values: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _check_flag(self.option, what="option")
        _check_flag(self.target, what="target")
        if self.option == self.target:
            raise ValueError("option and target must differ")
        if (
            not isinstance(self.values, tuple)
            or not self.values
            or any(not isinstance(value, str) for value in self.values)
        ):
            raise ValueError("values must be a non-empty tuple of strings")
        if len(set(self.values)) != len(self.values):
            raise ValueError("values must not contain duplicates")
        _check_reason(self.reason)

    @property
    def flags(self) -> tuple[str, ...]:
        return (self.option, self.target)

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": "requires-choice",
            "option": self.option,
            "target": self.target,
            "values": list(self.values),
            "reason": self.reason,
        }


Constraint = Requires | Conflicts | RequiresChoice


def rule_text(record: Mapping[str, Any]) -> str:
    """Render a constraint record as its help/Markdown line."""

    kind = record["kind"]
    if kind == "requires":
        return (
            f"{record['option']} requires {' or '.join(record['any_of'])}: "
            f"{record['reason']}"
        )
    if kind == "conflicts":
        return (
            f"{' and '.join(record['options'])} cannot be used together: "
            f"{record['reason']}"
        )
    return (
        f"{record['option']} requires {record['target']} "
        f"{'|'.join(record['values'])}: {record['reason']}"
    )


def rule_lines(constraints: Sequence[Constraint]) -> tuple[str, ...]:
    """One help line per constraint, in declaration order."""

    return tuple(rule_text(constraint.to_record()) for constraint in constraints)


def help_epilog(constraints: Sequence[Constraint]) -> str | None:
    """Return the ``CONSTRAINTS`` help section, or ``None`` when there are none."""

    if not constraints:
        return None
    return "CONSTRAINTS\n" + "\n".join(f"  {line}" for line in rule_lines(constraints))


@dataclass(frozen=True)
class ResolvedConstraint:
    """A constraint bound to the destinations and defaults of one parser."""

    constraint: Constraint
    slots: Mapping[str, tuple[str, Any]]


def _default_is_supported(default: Any) -> bool:
    return (
        default is None
        or default is False
        or (type(default) in (list, tuple) and len(default) == 0)
    )


def _is_present(value: Any, default: Any) -> bool:
    if default is None:
        return value is not None
    return bool(value)


def resolve_constraints(
    constraints: Sequence[Constraint],
    *,
    verb: str,
    parser: argparse.ArgumentParser,
    root: argparse.ArgumentParser,
) -> tuple[ResolvedConstraint, ...]:
    """Validate ``constraints`` against a built parser and bind their flags.

    ``parser`` is the verb's parser; ``root`` the registry's root parser, which
    holds the real defaults of options the verb parser repeats with suppressed
    defaults. Violations raise ``ValueError`` naming the verb and the flag.
    """

    resolved: list[ResolvedConstraint] = []
    for constraint in constraints:
        slots: dict[str, tuple[str, Any]] = {}
        for flag in constraint.flags:
            action = parser._option_string_actions.get(flag)
            if action is None:
                raise ValueError(
                    f"verb {verb!r} constraint references {flag}, which the verb "
                    "does not accept"
                )
            default = action.default
            if default is argparse.SUPPRESS:
                root_action = root._option_string_actions.get(flag)
                default = None if root_action is None else root_action.default
                if default is argparse.SUPPRESS:
                    default = None
            if not _default_is_supported(default):
                raise ValueError(
                    f"verb {verb!r} constraint references {flag}, whose default "
                    f"{default!r} is not None, False or an empty list/tuple"
                )
            if any(
                other is not action and other.dest == action.dest
                for other in parser._actions
            ):
                raise ValueError(
                    f"verb {verb!r} constraint references {flag}, whose destination "
                    f"{action.dest!r} is shared with another option"
                )
            if action.nargs == "*" or (
                action.nargs == "?" and action.const == default
            ):
                raise ValueError(
                    f"verb {verb!r} constraint references {flag}, whose presence "
                    "cannot be detected from its value"
                )
            slots[flag] = (action.dest, default)
        if isinstance(constraint, RequiresChoice):
            target = parser._option_string_actions[constraint.target]
            if isinstance(target, argparse._AppendAction) or target.nargs not in (
                None,
                "?",
            ):
                raise ValueError(
                    f"verb {verb!r} constraint target {constraint.target} is "
                    "list-valued"
                )
            choices = target.choices
            if choices is None:
                raise ValueError(
                    f"verb {verb!r} constraint target {constraint.target} declares "
                    "no choices"
                )
            for value in constraint.values:
                if not any(value == choice for choice in choices):
                    raise ValueError(
                        f"verb {verb!r} constraint value {value!r} is not a choice "
                        f"of {constraint.target}"
                    )
        resolved.append(ResolvedConstraint(constraint, slots))
    return tuple(resolved)


def first_violation(
    resolved: Sequence[ResolvedConstraint], args: argparse.Namespace
) -> str | None:
    """Return the refusal message for the first violated constraint, if any."""

    def value_of(item: ResolvedConstraint, flag: str) -> Any:
        dest, _default = item.slots[flag]
        return getattr(args, dest, _MISSING)

    def present(item: ResolvedConstraint, flag: str) -> bool:
        value = value_of(item, flag)
        return value is not _MISSING and _is_present(value, item.slots[flag][1])

    for item in resolved:
        constraint = item.constraint
        record = constraint.to_record()
        if isinstance(constraint, Requires):
            if present(item, constraint.option) and not any(
                present(item, flag) for flag in constraint.any_of
            ):
                return rule_text(record)
        elif isinstance(constraint, Conflicts):
            active = [flag for flag in constraint.options if present(item, flag)]
            if len(active) > 1:
                return (
                    f"{' and '.join(active)} cannot be used together: "
                    f"{constraint.reason}"
                )
        elif present(item, constraint.option) and not (
            present(item, constraint.target)
            and value_of(item, constraint.target) in constraint.values
        ):
            return rule_text(record)
    return None
