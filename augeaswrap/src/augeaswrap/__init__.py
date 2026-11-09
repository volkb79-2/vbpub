"""Augeaswrap's frozen guarded-applier protocol and public value types.

Augeaswrap applies requested host configuration changes with explicit guards.
It keeps transaction journals and pre-images only: there is no ownership
registry, layer stack, tool-level uninstall, or automatic rollback. A caller
removes a resource with an explicit ``state="absent"`` request. A failed
transaction stops at the first failed step and remains available for a
separate, explicit ``revert``; revert refuses if a later transaction changed
one of the same addresses.

The resource protocol below is the HC-1a' boundary. The file, check, unit,
sysctl, sysusers, tmpfiles and endpoint resources implement this exact
contract. Do not change it after the protocol commit unless a documented
defect requires a protocol correction.

Every resource has a stable ``kind`` string and implements exactly these
methods::

    addresses() -> tuple[str, ...]
    observe(ctx) -> Mapping[str, object]
    diff(ctx, observed) -> list[Change]
    stage(ctx, change) -> object
    commit(ctx, staged) -> object
    post_actions(changes) -> list[PostAction]
    verify(ctx) -> list[Problem]

``observe`` is read-only and returns one JSON-safe observation per address.
The engine fingerprints each complete observation for the plan's precondition;
an observation must therefore include every file, path, or external fact read
by the resource. ``diff`` returns one change or noop for each address and does
not retain per-plan mutable state on the resource. ``stage`` validates and
prepares a change without changing the managed root; it may write only below
``ctx.stage_dir``. ``commit`` performs one staged change and returns its
post-image for the journal. ``post_actions`` returns argv-only actions, never
shell command strings. The engine records and runs each action after file
commits. ``verify`` compares committed state with the staged image exposed by
``ctx.expected``.

The stable context fields are ``root`` (the selected real or fake root),
``writer``, ``txn_id``, ``txn_dir``, ``stage_dir``, ``pre_dir``, ``expected``
and the plan-scoped ``planned_gids`` map used by ordered sysusers/endpoint
requests.
The transaction paths and ``txn_id`` may be ``None`` during planning;
``stage_dir`` is a private temporary directory during plan validation and a
transaction-local directory during apply. ``expected`` maps addresses to
staged images during verification. There is no owner or persisted state field.

Addendum (additive; the seven methods above are unchanged): a kind registers
itself with ``augeaswrap.kinds.register_kind`` (TOML parse, TOML serialize,
optional plan rendering) and either supplies ``RevertHooks`` (``snapshot`` and
``restore``) or states ``revert=None``, so a plan can say ``revert: unsupported``
before apply. The engine has no per-kind code. SPEC, "Adding a resource kind",
is the full contract.

``Problem`` remains the shared structured finding used by the endpoint API;
its ``owner`` field names an endpoint fragment and has no ownership semantics
in the applier. This package remains stdlib-only at runtime apart from
cli-extended's CLI failure type and grammar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from cli_extended.parser import CliFailure


@dataclass(frozen=True, eq=False)
class Problem:
    """Structured finding with stable machine and human-readable forms.

    ``owner`` is retained for endpoint fragment findings, where it means a
    fragment name. It is not transaction ownership.
    """

    code: str
    message: str
    path: str | None = None
    owner: str | None = None
    key: str | None = None

    def __str__(self) -> str:
        return self.message

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.message == other
        if isinstance(other, Problem):
            return self.as_dict() == other.as_dict()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.message)

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item in self.message

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "owner": self.owner,
            "key": self.key,
        }


@runtime_checkable
class Resource(Protocol):
    """The frozen internal protocol implemented by every resource kind."""

    kind: str

    def addresses(self) -> tuple[str, ...]: ...

    def observe(self, ctx: object) -> Mapping[str, object]: ...

    def diff(self, ctx: object, observed: Mapping[str, object]) -> list["Change"]: ...

    def stage(self, ctx: object, change: "Change") -> object: ...

    def commit(self, ctx: object, staged: object) -> object: ...

    def post_actions(self, changes: list["Change"]) -> list["PostAction"]: ...

    def verify(self, ctx: object) -> list[Problem]: ...


@dataclass(frozen=True)
class Change:
    """One planned transition for a resource address.

    ``action`` is ``change`` or ``noop``. ``before`` and ``after`` are
    resource-owned, JSON-safe values suitable for the plan and journal.
    ``resource`` is excluded from comparison and representation because the
    resource object itself is not journal data.
    """

    kind: str
    address: str
    action: str
    before: Any
    after: Any
    resource: Any = field(default=None, compare=False, repr=False)
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("change kind is required")
        if not isinstance(self.address, str) or not self.address:
            raise ValueError("change address is required")
        if not isinstance(self.action, str) or self.action not in {"change", "noop"}:
            raise ValueError("change action must be 'change' or 'noop'")
        if not isinstance(self.details, tuple) or any(not isinstance(item, str) for item in self.details):
            raise ValueError("change details must be a tuple of strings")


@dataclass(frozen=True)
class PostAction:
    """One argv-only post-action; shell command strings are not accepted.

    ``pending`` records an intended runtime action without executing it. This is
    for installers that must leave live services alone and hand the action to a
    later maintenance step.

    ``phase`` selects the shared post-action phase. When omitted, the engine
    derives the phase from known argv forms and refuses unknown commands.
    """

    name: str
    argv: tuple[str, ...]
    timeout: float
    pending: bool = False
    phase: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("post-action name is required")
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("post-action argv is required")
        if any(not isinstance(item, str) or not item for item in self.argv):
            raise ValueError("post-action argv must contain non-empty strings")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or self.timeout <= 0
            or not math.isfinite(self.timeout)
        ):
            raise ValueError("post-action timeout must be positive and finite")
        if type(self.pending) is not bool:
            raise ValueError("post-action pending flag must be a boolean")
        if self.phase is not None and self.phase not in {
            "before_commit",
            "sysusers",
            "tmpfiles",
            "daemon_reload",
            "sysctl",
            "runtime",
            "gpasswd",
            "endpoint",
        }:
            raise ValueError("post-action phase is not a registered Augeaswrap phase")
        if self.pending and self.phase == "before_commit":
            raise ValueError("a before-commit action cannot be pending")


@dataclass(frozen=True)
class Request:
    """Normalized request with a non-empty, single-line, NUL-free writer."""

    writer: str
    resources: tuple[Resource, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.writer, str)
            or not self.writer.strip()
            or "\n" in self.writer
            or "\r" in self.writer
            or "\x00" in self.writer
        ):
            raise ValueError("writer is required and must be a single line without NUL")
        if not isinstance(self.resources, tuple):
            raise ValueError("resources must be a tuple")
        for resource in self.resources:
            if not isinstance(resource, Resource):
                raise ValueError("every resource must implement the Augeaswrap resource protocol")

    def to_toml(self) -> str:
        """Render this request as strict Augeaswrap TOML."""

        from .engine import request_to_toml

        return request_to_toml(self)


@dataclass(frozen=True)
class Plan:
    """Read-only plan and the observation fingerprints required to apply it."""

    request: Request
    root: str
    changes: tuple[Change, ...]
    problems: tuple[Problem, ...] = ()
    preconditions: tuple[tuple[str, str | None], ...] = ()
    observed: Any = field(default=None, compare=False, repr=False)

    @property
    def has_changes(self) -> bool:
        return any(change.action == "change" for change in self.changes)

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON-facing representation of this plan."""

        from .engine import plan_action_dicts
        from .kinds import is_revertible

        return {
            "writer": self.request.writer,
            "root": self.root,
            "has_changes": self.has_changes,
            "changes": [
                {
                    "kind": item.kind,
                    "address": item.address,
                    "action": item.action,
                    "before": item.before,
                    "after": item.after,
                    "details": list(item.details),
                    "revertible": is_revertible(item.kind),
                }
                for item in self.changes
            ],
            "problems": [problem.as_dict() for problem in self.problems],
            "preconditions": dict(self.preconditions),
            "post_actions": plan_action_dicts(self),
        }

    def render(self) -> str:
        """Render a deterministic human-readable plan."""

        from .engine import render_plan

        return render_plan(self)


@dataclass(frozen=True)
class Result:
    """Outcome of an apply or revert transaction."""

    txn: str | None
    changed: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(self.changed or self.actions or self.pending)


class PlanRefused(CliFailure):
    """The requested state cannot be safely planned or validated."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = list(problems)
        message = "plan refused: " + "; ".join(str(problem) for problem in self.problems)
        super().__init__(message, exit_code=2)


class StalePlan(CliFailure):
    """A plan precondition changed before apply acquired the writer lock."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class ApplyFailed(CliFailure):
    """A partial apply stopped at its first failed step; pre-images remain."""

    def __init__(self, txn: str, done: list[str], failed: str, total: int) -> None:
        self.txn = txn
        self.done = list(done)
        self.failed = failed
        self.remedy = f"augeaswrap revert {txn}; or fix and re-run apply"
        super().__init__(
            f"applied {len(self.done)}/{total}; failed: {failed}; undo: {self.remedy}",
            exit_code=4,
        )


from .check import Check, PathMode
from .engine import apply, journal, load_request, plan, preview_revert, revert
from .file import File
from .unit import Unit
from .sysusers import GroupRemoval, GroupSpec, Sysusers
from .tmpfiles import Tmpfiles
from .sysctl import Sysctl, SysctlEntry
from .endpoint import Endpoint

__all__ = [
    "ApplyFailed",
    "Check",
    "Change",
    "File",
    "Endpoint",
    "GroupRemoval",
    "GroupSpec",
    "Plan",
    "PlanRefused",
    "PostAction",
    "Problem",
    "PathMode",
    "Request",
    "Resource",
    "Result",
    "StalePlan",
    "Sysctl",
    "SysctlEntry",
    "Sysusers",
    "Tmpfiles",
    "Unit",
    "apply",
    "journal",
    "load_request",
    "plan",
    "preview_revert",
    "revert",
]
