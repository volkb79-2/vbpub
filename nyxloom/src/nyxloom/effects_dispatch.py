"""Shared launch primitives for the effect families that start agents.

WHY THIS IS A MODULE AND NOT A BASE CLASS (CR-05b)
--------------------------------------------------
Four families launch an agent -- implementer dispatch, resume, self-review,
and independent review -- and a fifth (carve) launches one too. They share a
small set of decisions that are NOT specific to any of them: may this launch
happen at all, which mutex leases does it hold, what does the handoff say,
what gate hint goes in the prompt. Before this package those lived as methods
on ``Daemon``, which is how five families came to share state by sharing an
object.

They are plain FUNCTIONS over the effect context. Inheritance would put the
shared decisions on a base an effector could override, and "may this launch
happen" is exactly the decision an effector must not be able to soften.

THE ADMISSION GATE IS THE POINT
-------------------------------
:func:`admissible` is the execute-time re-check at the EFFECT boundary. Every
guard the planner evaluates runs against a snapshot, and the daemon then
performs minutes of side effects with no re-check -- so a mid-pass auto-pause
(the watchdog can write the pause flag partway through a pass) or any planner
gap could still launch an agent. This runs immediately before every wrapper
launch, so pause, budget and the snapshot verdict are authoritative at the
moment of effect rather than at plan time.

It does NOT yet cover the non-daemon launch sites (intake, decision,
onboarding). The fully structural form -- an admission token minted only here
and required by ``wrapper.launch_detached`` -- remains a follow-up; this
closes the gap for every launch the autonomous loop makes.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import containment, effects, frontmatter, paths
from .config import ProjectConfig
from .log import get_logger
from .types import Event, EventType, TaskStateFile

log = get_logger("effects.dispatch")

#: The launch kinds the admission gate distinguishes. A pause mode blocks
#: some and not others, so the vocabulary is closed rather than free text.
LAUNCH_KINDS = frozenset({"dispatch", "resume", "review", "carve", "self-review"})


def route_by_id(routes_obj: Any, route_id: str, *, project: str,
                kind: str) -> Any | None:
    """The pinned route, or ``None`` -- a REFUSAL, never a ``KeyError``.

    CR-13a review. The planner picks ``route_id`` out of the routes in ITS
    snapshot; the effector then re-reads ``routes.toml`` FROM DISK and looks it
    up. Any divergence between those two reads -- an operator editing the file
    mid-pass, or the routes sync this package's own SYNC OBLIGATION instructs
    them to perform -- makes the id absent. As a bare ``[]`` lookup that raised
    ``KeyError``, which ``run_pass``'s outer net turns into a ``TICK_ERROR``
    that kills the WHOLE pass for the project, not just this one launch.

    A route id that no longer resolves is precisely the fail-closed case this
    package exists for, so it refuses like every other one: the caller returns
    no events, the task stays where it was and stays visible, and the next pass
    re-plans against the routes that now exist. ERROR rather than WARNING for
    the same reason ``containment-refused`` is -- it repeats every pass until a
    human changes the deployment.

    ``effects_carve``'s carve path already had this discipline ("routes.toml
    could change between planning and execution within the same pass. Never
    mint a synthetic task / worktree we cannot actually dispatch into"); this
    is that rule, made reusable so the three attempt legs and the two review
    legs cannot each get it subtly differently.
    """
    route = getattr(routes_obj, "routes", {}).get(route_id)
    if route is None:
        log.error("route-unknown", project=project, kind=kind,
                  reason=f"route-unknown:{route_id}")
    return route


def route_for_role(routes_obj: Any, role: str, *, project: str,
                   kind: str) -> Any | None:
    """The first route serving ``role``, or ``None`` -- never an ``IndexError``.

    The role-indexed twin of :func:`route_by_id`, for the legs that ask for a
    role rather than naming a route. ``for_role(...)[0]`` on an empty list is
    the same defect in a different shape: a deployment with no
    review-independent route took the pass down instead of declining to launch
    a review.
    """
    candidates = routes_obj.for_role(role)
    if not candidates:
        log.error("role-unroutable", project=project, kind=kind,
                  reason=f"role-unroutable:{role}")
        return None
    return candidates[0]


def pause_mode(files: Any, project: str) -> str:
    """The project's pause MODE, read from the flag file's content.

    Absent -> ``run``. An explicit ``drain-agents`` selects that mode;
    anything else, INCLUDING an empty flag file, is ``drain-handoffs`` --
    because a bare boolean pause flag has always meant exactly that (block
    new dispatch only), and reading a legacy empty file as the stricter mode
    would silently stop in-flight work an operator expected to finish.
    """
    flag = paths.pause_flag(project)
    if not files.exists(flag):
        return "run"
    try:
        content = files.read_text(flag).strip()
    except OSError:
        content = ""
    return "drain-agents" if content == "drain-agents" else "drain-handoffs"


def budget_remaining(cfg: ProjectConfig,
                     states: dict[str, TaskStateFile]) -> float | None:
    """Configured cost cap minus recorded spend, or None when uncapped.

    Only usage in the configured currency counts; an attempt whose usage was
    recorded in another currency is not converted, because a guessed rate is
    a worse answer than a missing one.
    """
    if cfg.policy.max_cost is None:
        return None
    spent = 0.0
    for tsf in states.values():
        for att in tsf.attempts:
            if att.usage is not None and att.usage.cost is not None:
                if (cfg.policy.cost_currency is None
                        or att.usage.currency == cfg.policy.cost_currency):
                    spent += att.usage.cost
    return cfg.policy.max_cost - spent


def route_currently_paused(ctx: effects.EffectContext, route: Any) -> bool:
    """Is ``route`` paused RIGHT NOW, per the live
    :class:`~nyxloom.effects.ProviderPauseRegistry` -- not the
    ``provider_ok`` snapshot baked once before the pass's action loop.

    ``ctx.provider_pause`` is ``None`` when no registry is wired (e.g. a
    test-constructed context exercising an unrelated check) -- degrades to
    "not paused" rather than crashing; this mirrors every other advisory
    signal here (a probe that cannot run only ever REMOVES a dispatch
    option, so an absent registry silently doing nothing is at worst a
    missed refusal, never a wrongful one -- the plan-time ``provider_ok``
    check upstream already covers the ordinary case).
    """
    registry = ctx.provider_pause
    if registry is None:
        return False
    paused_until = registry.paused_until(getattr(route, "route_id", None))
    if paused_until is None:
        return False
    return ctx.ports.clock.monotonic() < paused_until


def admissible(ctx: effects.EffectContext, kind: str,
               route: Any = None) -> tuple[bool, str]:
    """May this launch happen right now? Reason string when not.

    Order matters. The snapshot verdict is checked FIRST, for the same reason
    pause and budget are checked here at all: a guard evaluated only at plan
    time is a guard with a window. ``run_pass`` already refuses to plan on a
    failed audit, so in the autonomous loop this is belt-and-braces -- and it
    became load-bearing the moment CR-05a put execution behind a registry
    that can be driven from elsewhere.

    Pause semantics deliberately MATCH the planner's own per-kind rules, so
    this closes the plan/execute gap without changing any decision:
    ``drain-agents`` blocks every kind; ``drain-handoffs`` blocks new work
    (dispatch, carve) but lets in-flight legs finish (resume, review,
    self-review -- the self-review leg continues a task the implementer
    already ran, so it drains). An exhausted budget blocks ALL kinds,
    including the review and resume legs the planner never gated, which are
    the most expensive to launch into a spent budget.

    ROUTE HEALTH (CR-11a) is checked next, only when a ``route`` is given: a
    route can be paused by ANOTHER task's outcome DURING this same pass --
    ``LifecycleEffector.pause_provider`` mutates the same
    :class:`~nyxloom.effects.ProviderPauseRegistry` this reads, from a LIMIT
    receipt processed earlier in the same pass's action list -- but
    ``ReconcileInput.provider_ok`` is a snapshot baked ONCE before the pass's
    action loop starts and is never refreshed. Without this, a route paused
    by task A's LIMIT receipt could still launch task B's already-planned
    dispatch/resume into it later in the SAME pass, self-correcting only
    after burning attempts against a route already known bad -- the gap
    CR-08 Slice 2 named as CR-11's charter, not CR-08's, and sharpest for
    ``ResumeAttempt``'s ordinary (non-poisoned) branch, which the PLANNER
    never route-health-gates at all (unlike dispatch/fresh-start, which at
    least check the STALE plan-time snapshot). A pause is a time-bounded
    backoff (``PROVIDER_PAUSE_SECONDS``), so this refusal is SELF-HEALING
    exactly like pause-mode/budget above -- it clears on its own once the
    deadline passes, never a permanent stall.

    CONTAINMENT (CR-13a, D-R7) is checked LAST and only when a ``route`` is
    given: it is the most expensive question (two calls to the docker daemon)
    and the one most likely to be answered by a cheaper refusal above it. A
    route that requires containment (see
    ``containment.requires_containment`` -- everything but an explicitly
    operator-trusted, non-free route) and cannot get it is refused HERE so
    the daemon does not mint an attempt that can only fail. The wrapper
    re-checks at the moment of launch regardless; the two are separated by a
    process start, and an image pruned in between must stop the launch rather
    than the previous check.

    Passing no route is NOT "containment does not apply" -- it is a caller
    that has not resolved its route yet, and the wrapper's own gate is what
    covers it. There is no path on which a required containment is skipped.
    """
    audit = ctx.snapshot_audit
    if audit is not None and not audit.permits_effects:
        return (False, f"snapshot-unavailable:{audit.summary()}")
    mode = pause_mode(ctx.ports.files, ctx.project)
    if mode == "drain-agents":
        return (False, "paused:drain-agents")
    if mode == "drain-handoffs" and kind in ("dispatch", "carve"):
        return (False, "paused:drain-handoffs")
    budget = budget_remaining(ctx.cfg, ctx.states)
    if budget is not None and budget <= 0:
        return (False, "budget-exhausted")
    if route is not None and route_currently_paused(ctx, route):
        route_id = getattr(route, "route_id", "?")
        return (False, f"route-paused:{route_id}")
    if route is not None and containment.requires_containment(route):
        reason = containment.unavailable_reason(
            route, repo_root=str(ctx.cfg.root), run=ctx.ports.processes.run)
        if reason:
            # The only refusal here logged at ERROR. Pause and budget are
            # normal operation and heal by themselves; this one repeats
            # every pass until a human changes the deployment, and the
            # attempt it prevents is one that would have carried the reason
            # in a receipt.
            log.error("containment-refused", project=ctx.project, kind=kind,
                      route=getattr(route, "route_id", "?"), reason=reason)
            return (False, f"containment-unavailable:{reason}")
    return (True, "")


def ensure_worktree(ports: Any, root, branch: str, worktree_path,
                    default_branch: str) -> None:
    """A worktree for ``branch``, created if absent.

    Two shapes: the branch already EXISTS (a re-dispatch after a rejected
    review, or a carve turn resuming an authority branch -- whose commits
    must be preserved) or it does not (a first dispatch, branching from the
    default). Checking first is what keeps a re-dispatch from silently
    starting over on an empty branch.

    A refused add RAISES. Launching an agent into a directory that is not
    there is worse than a TICK_ERROR and a re-plan.
    """
    if ports.files.exists(worktree_path):
        return
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if ports.git.resolve_verify(str(root), branch) is not None:
        ports.git.worktree_add(str(root), str(worktree_path), branch)
    else:
        ports.git.worktree_add_new_branch(str(root), branch,
                                          str(worktree_path), default_branch)


def gate_hint(cfg: ProjectConfig) -> str:
    """The gate command an agent is told to run, as a single string.

    The lowest ``gate_id`` when a project declares several: the hint is
    orientation for the agent, not the authoritative gate, so a stable
    arbitrary choice beats a configurable one nobody would tune.
    """
    if not cfg.gates:
        return ""
    gate = sorted(cfg.gates.values(), key=lambda g: g.gate_id)[0]
    return " ".join(gate.argv)


def frontmatter_for(cfg: ProjectConfig, tsf: TaskStateFile):
    """The task's parsed handoff frontmatter, or None.

    None on every failure -- no handoff path, no file, unparseable. This is
    ADVISORY input: it shapes a prompt and selects mutex leases, and it can
    only ever REDUCE what a launch is allowed to hold. A task whose handoff
    cannot be read therefore launches holding NO leases rather than not
    launching at all, which is the pre-existing behaviour and the reason the
    broad handler below is degradation rather than a fail-open.
    """
    if not tsf.handoff_path:
        return None
    path = cfg.root / tsf.handoff_path
    if not path.exists():
        return None
    try:
        fm, _body = frontmatter.parse_handoff(path)
        return fm
    except Exception:  # census: advisory-degradation (CR-02b)
        return None


def lease_specs(cfg: ProjectConfig, fm) -> list[dict[str, Any]]:
    """The mutex leases a launch for this task must hold."""
    if fm is None:
        return []
    out = []
    for m in fm.effective_mutexes():
        mdef = cfg.mutexes.get(m)
        if mdef is None:
            continue
        out.append({"name": mdef.lease_name(cfg.project_id), "capacity": mdef.capacity})
    return out


def scope_amendment_files(events: Sequence[Event], task_id: str) -> list[str]:
    """Files a prior scope amendment already approved for this task.

    In approval order, de-duplicated. Fed to the dispatch prompt for BOTH the
    implementer re-dispatch (the widened allowlist reaches the agent through
    the PROMPT, never by rewriting the handoff's `scope.touch` on disk) and
    the reviewer (so it does not reject the now-legitimate out-of-scope
    edit). The APPROVED events are the cap's enforcement, so this reads them
    rather than any derived counter.
    """
    files: list[str] = []
    for ev in events:
        if (ev.type is EventType.SCOPE_AMENDMENT_APPROVED
                and ev.task_id == task_id):
            f = ev.payload.get("file")
            if f and f not in files:
                files.append(f)
    return files
