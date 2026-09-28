"""Interactive managed-backlog create/edit flows over cli-extended prompts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cli_extended import CliFailure

from . import backlog_entries

_FORM_FIELDS = (
    "title",
    "type",
    "severity",
    "priority",
    "component",
    "context_estimate",
    "folds_into",
    "provenance",
    "filed_by",
    "spec_owner",
)
_CHOICES = {
    "type": ("feature", "bugfix"),
    "severity": ("low", "medium", "high"),
    "context_estimate": ("small", "medium", "large"),
}
_UNSET = "(unset)"


def _field_hint(name: str, command: str) -> str:
    return f"correct {name} and rerun `{command}`; no files were changed"


def _prompt_text(runtime: Any, name: str, seed: Any, *, required: bool) -> str:
    default = None if seed is None else str(seed)
    try:
        return runtime.prompts.text(
            f"{name.replace('_', ' ').title()}",
            default=default,
            required=required,
        )
    except CliFailure as exc:
        if "require both stdin and stdout" in exc.message or "optional Questionary" in exc.message:
            raise
        raise CliFailure(
            f"invalid {name.replace('_', ' ')} answer: {exc.message}",
            exit_code=exc.exit_code,
            hint=_field_hint(name, "nyxloom backlog new --interactive"),
        ) from exc


def _prompt_choice(runtime: Any, name: str, seed: Any) -> str | None:
    choices = (*_CHOICES[name], _UNSET)
    default = seed if seed in _CHOICES[name] else _UNSET
    try:
        value = runtime.prompts.select(
            f"{name.replace('_', ' ').title()}", choices, default=default
        )
    except CliFailure as exc:
        if "require both stdin and stdout" in exc.message or "optional Questionary" in exc.message:
            raise
        raise CliFailure(
            f"invalid {name.replace('_', ' ')} choice: {exc.message}",
            exit_code=exc.exit_code,
            hint=_field_hint(name, "nyxloom backlog new --interactive"),
        ) from exc
    return None if value == _UNSET else value


def collect_values(runtime: Any, seed: Mapping[str, Any]) -> dict[str, Any]:
    """Collect every editable field before the caller may write anything."""
    values: dict[str, Any] = {
        "title": _prompt_text(runtime, "title", seed.get("title"), required=True)
    }
    for name in ("type", "severity"):
        values[name] = _prompt_choice(runtime, name, seed.get(name))

    raw_priority = _prompt_text(
        runtime, "priority", seed.get("priority"), required=False
    )
    if raw_priority.strip():
        try:
            values["priority"] = int(raw_priority, 10)
        except ValueError as exc:
            raise CliFailure(
                f"priority must be an integer, got {raw_priority!r}",
                exit_code=2,
                hint=_field_hint("priority", "nyxloom backlog new --interactive"),
            ) from exc
    else:
        values["priority"] = None

    for name in ("component",):
        raw = _prompt_text(runtime, name, seed.get(name), required=False)
        values[name] = raw if raw.strip() else None
    values["context_estimate"] = _prompt_choice(
        runtime, "context_estimate", seed.get("context_estimate")
    )
    for name in ("folds_into", "provenance", "filed_by", "spec_owner"):
        raw = _prompt_text(runtime, name, seed.get(name), required=False)
        values[name] = raw if raw.strip() else None
    return values


def create(
    cfg: Any,
    runtime: Any,
    title_seed: str | None,
    values: Mapping[str, Any],
    *,
    body: str | None = None,
) -> Any:
    """Prompt for a complete create candidate and validate before file writes."""
    seeds = dict(values)
    seeds["title"] = title_seed
    collected = collect_values(runtime, seeds)
    try:
        return backlog_entries.create_entry(cfg, **collected, body=body)
    except ValueError as exc:
        raise CliFailure(
            str(exc),
            exit_code=1,
            hint="review the schema error, correct the listed field, then rerun `nyxloom backlog new --interactive`; no files were changed",
        ) from exc


def edit(cfg: Any, runtime: Any, entry_id: str) -> Any:
    """Prompt for editable frontmatter and preserve the entry body."""
    try:
        entry = backlog_entries._find(cfg, entry_id)
    except KeyError as exc:
        raise CliFailure(str(exc.args[0]), exit_code=1) from exc
    seed = {field: entry.raw.get(field) for field in _FORM_FIELDS}
    collected = collect_values(runtime, seed)
    try:
        return backlog_entries.edit_fields(cfg, entry_id, collected)
    except ValueError as exc:
        raise CliFailure(
            str(exc),
            exit_code=1,
            hint=f"review the schema error and rerun `nyxloom backlog edit {entry_id}`; no files were changed",
        ) from exc
