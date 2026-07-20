"""Registry, project policy, routes, prices, redaction. FROZEN CORE.

All configuration is TOML (stdlib tomllib). Gates are declared HERE (trusted
project config) as structured argv — model output can never introduce an
executable (SPEC §3, security boundary).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .stages import (
    DEFAULT_PIPELINE, compose, validate_pipeline, validate_stage_overrides,
)
from .types import Basis, Usage


# ---------------------------------------------------------------------------
# registry (multi-project)

def load_registry() -> dict[str, Path]:
    """project_id -> repo root. Empty dict if no registry yet."""
    p = paths.registry_path()
    if not p.exists():
        return {}
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    return {pid: Path(spec["root"]) for pid, spec in data.get("projects", {}).items()}


def register_project(project_id: str, root: Path) -> None:
    """Idempotent add; rewrites registry.toml (tiny file, no TOML writer dep)."""
    paths.ensure_layout()
    reg = load_registry()
    reg[project_id] = root
    lines = []
    for pid, r in sorted(reg.items()):
        lines.append(f'[projects.{pid}]\nroot = "{r}"\n')
    paths.registry_path().write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# project policy (.nyxloom/project.toml in the consumer repo)

@dataclass
class GateDef:
    gate_id: str
    argv: list[str]                 # trusted; {worktree} placeholder allowed
    phase: str                      # implementation|review|pre-merge|post-merge
    timeout_seconds: int
    environment: str = "local"      # fingerprint label, e.g. "test-runner"


@dataclass
class MutexDef:
    name: str
    scope: str = "project"          # project|host
    capacity: int = 1
    global_alias: str | None = None

    def lease_name(self, project_id: str) -> str:
        if self.scope == "host":
            return self.global_alias or self.name
        return f"{project_id}.{self.name}"


@dataclass
class NotifyConfig:
    ntfy_url: str | None = None     # e.g. https://ntfy.sh or self-hosted
    ntfy_topic: str | None = None
    # Env var holding the ntfy access token (deny-all servers need it).
    # The TOKEN VALUE never appears in config files — only the var name.
    token_env: str = "NTFY_TOKEN"
    # Optional inbound command topic (operator -> daemon; e.g. 'nyxloom-cmd').
    # Read with a SEPARATE read-only identity: the publisher token above is
    # write-only and must never be able to read commands.
    cmd_topic: str | None = None
    cmd_token_env: str = "NTFY_CMD_TOKEN"
    webhook_url: str | None = None
    push_classes: list[str] = field(default_factory=lambda: [
        "DECISION_OPENED", "TASK_BLOCKED", "PROVIDER_STATE_CHANGED",
        "BUDGET_WARNING", "BUDGET_EXHAUSTED", "SPEC_ATTENTION",
        "NEEDS_OPERATOR", "WAVE_CLOSED", "ATTEMPT_STALLED",
    ])
    digest_classes: list[str] = field(default_factory=lambda: [
        "MERGE_RECORDED", "TASK_TRANSITIONED",
    ])


@dataclass
class Policy:
    max_active_tasks: int = 4
    ready_queue_target: int = 5
    max_attempts_per_task: int = 3
    merge_mode: str = "manual"      # manual|guarded-automatic (latter gated on M5 decision)
    retention_days: int = 60
    max_cost: float | None = None
    cost_currency: str | None = None
    max_consecutive_zero_progress_merges: int = 3
    stall_log_quiet_seconds: int = 300     # v2 §5.4 tier-1 threshold
    # P34 2026-07-16 (resume-safety re-cut): a poisoned session_handle that
    # keeps failing resumes is fresh-started rather than resumed forever
    # once its aged resume-log count reaches this threshold; the grace
    # window below is only a race guard against a just-launched resume.
    max_resume_failures: int = 2
    resume_progress_grace_seconds: int = 120
    # Absolute per-attempt wall-clock backstop (P14): interrupt regardless
    # of liveness once exceeded; fm.budget.max_wall_seconds overrides.
    attempt_max_wall_seconds: int = 10800
    reconcile_interval_seconds: int = 30
    wave_max_diffs: int = 3
    http_port: int = 8942           # loopback only
    # P38 2026-07-16 (dashboard bridge network): HTTP bind address, loopback
    # by default (safe). A containerized nyxloomd on a private ciu bridge
    # network sets this to "0.0.0.0" so the devcontainer can reach it --
    # NEVER on host-network, where 0.0.0.0 would expose it to the LAN. The
    # NYXLOOM_HTTP_BIND env var (see ProjectConfig.load) overrides the toml
    # value, since nyxloom.toml is bind-mounted and shared verbatim between
    # host and container runs -- it can't itself differ per target.
    http_bind: str = "127.0.0.1"
    # P16 2026-07-15 (carver automation): queue-refill target, carve
    # execution/admission mode, and the headroom threshold below which the
    # tick flags SPEC_ATTENTION('headroom-low'). See reconcile.py's (carve
    # trigger) and daemon.py's (CarveDispatch execution + summary
    # persistence) module docstrings for the full contract.
    carve_ahead_target: int = 5
    carve_authority: str = "branch"   # branch|main|files
    headroom_warn: int = 5
    # D-065 2026-07-20 (B63, strategic test-health): cadence in DAYS for the
    # project-WIDE test-health carve trigger (reconcile.py module contract
    # item 15) -- a seldom-run sibling of item 9's headroom refill that steps
    # back from per-task work and carves test-IMPROVEMENT packages for the
    # suite's standing debt. 0 disables it (the default: a project must opt in
    # before nyxloom starts spending carve budget on test debt it never asked
    # about). nyxloom's own nyxloom.toml sets 14 -- dogfooding, and the reason
    # this is not a dead stub (P43's guard).
    test_health_interval_days: int = 0


@dataclass
class ProjectConfig:
    project_id: str
    root: Path
    default_branch: str
    worktree_root: str              # relative to root, e.g. ".worktrees"
    handoff_globs: list[str]
    gates: dict[str, GateDef]
    mutexes: dict[str, MutexDef]
    policy: Policy
    infra_globs: list[str] = field(default_factory=list)   # lint L9
    redact_patterns: list[str] = field(default_factory=list)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    decisions_inbox: str = "docs/DECISIONS-INBOX.md"
    reports_dir: str = "handoff/reports"
    # Direction-spine docs (docs/spine-documents-spec.md, PACKAGE F1),
    # trove-relative paths. north_star/product_definition are new and have
    # no fallback (a project need not have adopted the spine yet). roadmap/
    # backlog default to None here too -- callers that need the legacy
    # unprefixed-filename convention (backlog_items.DEFAULT_RELPATH, the
    # daemon's carve-context notes) keep their own hardcoded fallback; this
    # field only carries an EXPLICIT repoint (e.g. nyxloom's own
    # nyxloom-trove/nyxloom.toml points these at 3-roadmap.md/4-backlog.md).
    north_star: str | None = None
    product_definition: str | None = None
    roadmap: str | None = None
    backlog: str | None = None
    # D-060 stages-as-data (docs/spec-flow-stages.md): the composed, validated
    # per-project pipeline as an ordered list of stage-kind names. Defaults to
    # the current hardcoded flow (DEFAULT_PIPELINE) so a project with no
    # `pipeline` key is byte-identical. load() resolves a preset name / explicit
    # list here and validates closure against the frozen graph.
    pipeline: list[str] = field(default_factory=lambda: list(DEFAULT_PIPELINE))
    # B3/P71 per-stage scheduling: `[stage.<name>]` TOML tables, e.g.
    # `[stage.implement] concurrency = 4`. Resolved per stage by
    # stages.effective_concurrency(); empty (the default) means every stage uses
    # its registry default -- implement inherits policy.max_active_tasks (parity).
    stage_overrides: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> "ProjectConfig":
        # B2 2026-07-16: prefer the nyxloom-trove layout; fall back to the
        # legacy .nyxloom/project.toml for un-migrated projects.
        p = root / "nyxloom-trove" / "nyxloom.toml"
        if not p.exists():
            p = root / ".nyxloom" / "project.toml"
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        gates = {
            gid: GateDef(gate_id=gid, argv=list(g["argv"]), phase=g["phase"],
                         timeout_seconds=int(g["timeout_seconds"]),
                         environment=g.get("environment", "local"))
            for gid, g in data.get("gates", {}).items()
        }
        mutexes = {
            name: MutexDef(name=name, scope=m.get("scope", "project"),
                           capacity=int(m.get("capacity", 1)),
                           global_alias=m.get("global_alias"))
            for name, m in data.get("mutexes", {}).items()
        }
        pol = Policy(**data.get("policy", {}))
        # P38 2026-07-16: env override for the bind address (see Policy.http_bind
        # docstring above) -- lets a ciu compose file flip the containerized
        # daemon onto its private bridge (0.0.0.0) without editing the toml
        # that the host process shares via the same bind mount.
        env_bind = os.environ.get("NYXLOOM_HTTP_BIND")
        if env_bind:
            pol.http_bind = env_bind
        # B2 2026-07-16: accept the two-channel names (notifications_topic /
        # feedback_topic) from the nyxloom-trove layout, mapping them onto the
        # internal ntfy_topic / cmd_topic fields (new names win; legacy names
        # still honoured). Also drop unknown [project]/[refs] keys the trove
        # adds — tomllib already isolates sections, and only [notify] flows
        # into NotifyConfig(**), so the extra trove fields are simply not read.
        notify_data = dict(data.get("notify", {}))
        if "notifications_topic" in notify_data:
            notify_data["ntfy_topic"] = notify_data.pop("notifications_topic")
        if "feedback_topic" in notify_data:
            notify_data["cmd_topic"] = notify_data.pop("feedback_topic")
        # P39: the ntfy server (not each project) is authoritative for its own
        # URL — it's a deployment fact (tls-edge/PUBLIC_FQDN), so NTFY_URL wins
        # over a project's toml value, keeping every project's nyxloom.toml from
        # re-hardcoding + drifting on the FQDN. Chain: env -> toml -> None
        # (notifications disabled). Resolved HERE, at config load, rather than in
        # NotifyConfig.__post_init__: the env is authoritative over the TOML
        # source only. A caller constructing NotifyConfig(...) directly keeps the
        # url it passes — otherwise an explicit NotifyConfig(ntfy_url=None) could
        # not express "disabled", and callers aiming at a specific endpoint would
        # be silently retargeted at the deployment server.
        env_url = os.environ.get("NTFY_URL")
        if env_url:
            notify_data["ntfy_url"] = env_url
        noti = NotifyConfig(**notify_data)
        # D-060: resolve + validate the pipeline at load. A preset name or an
        # explicit list under top-level `pipeline` (or [project].pipeline);
        # absent -> DEFAULT_PIPELINE. validate_pipeline raises ValueError on a
        # composition that does not close against the frozen graph (unknown
        # kind, illegal exit edge, dead-end routing, or no terminal) -- config
        # load fails loudly rather than the daemon planning an invalid flow.
        pipeline = compose(data.get("pipeline", data.get("project", {}).get("pipeline")))
        validate_pipeline(pipeline)
        # B3: per-stage `[stage.<name>]` overrides (currently just concurrency).
        stage_overrides = {name: dict(tbl) for name, tbl in data.get("stage", {}).items()}
        validate_stage_overrides(stage_overrides)
        return cls(
            project_id=data["project"]["id"],
            root=root,
            default_branch=data["project"].get("default_branch", "main"),
            worktree_root=data["project"].get("worktree_root", ".worktrees"),
            handoff_globs=list(data["project"]["handoff_globs"]),
            gates=gates,
            mutexes=mutexes,
            policy=pol,
            infra_globs=list(data["project"].get("infra_globs", [])),
            redact_patterns=list(data.get("redact", {}).get("patterns", [])),
            notify=noti,
            decisions_inbox=data["project"].get("decisions_inbox", "docs/DECISIONS-INBOX.md"),
            reports_dir=data["project"].get("reports_dir", "handoff/reports"),
            north_star=data["project"].get("north_star"),
            product_definition=data["project"].get("product_definition"),
            roadmap=data["project"].get("roadmap"),
            backlog=data["project"].get("backlog"),
            pipeline=pipeline,
            stage_overrides=stage_overrides,
        )

    def redact(self, text: str) -> str:
        return redact(text, self.redact_patterns)


# ---------------------------------------------------------------------------
# P15 2026-07-15: UI config mutation (the two functions this package is
# allowed to add to this otherwise-frozen module — see handoff/
# P15-ui-config.md). Both are SURGICAL line edits: only the matched
# anchor line(s) change, every other byte (including comments) is
# preserved, and the whole file is rewritten in one shot only after every
# requested key has been located (never a partial write). Callers
# (daemon.py) are responsible for validating keys/bounds/route-ids BEFORE
# calling — these two functions raise ValueError when an anchor cannot be
# found, which the caller turns into a 400 with no write performed.

def update_project_policy(root: Path, changes: dict[str, int]) -> None:
    """Rewrite ONLY the named `<key> = <value>` lines inside the [policy]
    section of `<root>/.nyxloom/project.toml`. `changes` maps policy key
    -> new int value. Raises ValueError (no write at all) if the [policy]
    section, or any requested key's anchor line inside it, is not found."""
    p = root / "nyxloom-trove" / "nyxloom.toml"
    if not p.exists():
        p = root / ".nyxloom" / "project.toml"
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    section_start: int | None = None
    section_end = len(lines)
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "[policy]":
            section_start = i
        elif section_start is not None and i > section_start and stripped.startswith("[") and stripped.endswith("]"):
            section_end = i
            break
    if section_start is None:
        raise ValueError("no [policy] section found in project.toml")

    remaining = dict(changes)
    key_line_re = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)([^#]*?)(\s*(?:#.*)?)$")
    for i in range(section_start + 1, section_end):
        raw = lines[i]
        body = raw[:-1] if raw.endswith("\n") else raw
        m = key_line_re.match(body)
        if not m:
            continue
        indent, key, eq, _old_val, trail = m.groups()
        if key in remaining:
            newline_suffix = "\n" if raw.endswith("\n") else ""
            lines[i] = f"{indent}{key}{eq}{remaining.pop(key)}{trail}{newline_suffix}"

    if remaining:
        raise ValueError(f"policy key(s) not found in [policy] section: {sorted(remaining)}")

    p.write_text("".join(lines), encoding="utf-8")


def update_routes(changes: dict[str, list[str]]) -> None:
    """Rewrite ONLY the `routes = [...]` line under each named
    `[tiers.<tier>]` header in the LIVE routes state file
    (paths.routes_path()) — `changes` maps tier name -> new ordered list of
    route ids. Never touches [routes.*] definitions (v1 only remaps which
    already-DEFINED routes a tier points at). Raises ValueError (no write
    at all) if a tier's section, or its `routes = [...]` line inside it, is
    not found."""
    p = paths.routes_path()
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    remaining = dict(changes)
    routes_line_re = re.compile(r"^(\s*routes\s*=\s*)\[[^\]]*\](.*)$")
    for tier in list(remaining):
        header = f"[tiers.{tier}]"
        start = None
        for i, raw in enumerate(lines):
            if raw.strip() == header:
                start = i
                break
        if start is None:
            continue
        end = len(lines)
        for i in range(start + 1, len(lines)):
            s = lines[i].strip()
            if s.startswith("[") and s.endswith("]"):
                end = i
                break
        for i in range(start + 1, end):
            raw = lines[i]
            body = raw[:-1] if raw.endswith("\n") else raw
            m = routes_line_re.match(body)
            if m:
                prefix, trail = m.groups()
                rendered = ", ".join(f'"{r}"' for r in remaining[tier])
                newline_suffix = "\n" if raw.endswith("\n") else ""
                lines[i] = f"{prefix}[{rendered}]{trail}{newline_suffix}"
                del remaining[tier]
                break

    if remaining:
        raise ValueError(f"tier(s) not found or missing routes= line: {sorted(remaining)}")

    p.write_text("".join(lines), encoding="utf-8")


_DEFAULT_REDACT = [
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+",
    r"sk-[A-Za-z0-9_-]{16,}",
    r"Bearer\s+[A-Za-z0-9._-]{8,}",
]


def redact(text: str, patterns: list[str] | None = None) -> str:
    """Apply default + project redaction patterns; replacement '[REDACTED]'."""
    for pat in _DEFAULT_REDACT + list(patterns or []):
        text = re.sub(pat, "[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# routes

@dataclass
class RouteDef:
    route_id: str
    cli: str
    model: str
    variant: str | None = None
    effort: str | None = None
    sandbox: str | None = None
    argv_max: int | None = None
    prompt_hints: list[str] = field(default_factory=list)
    probe: Any = None               # argv list, or named builtin str
    resume: list[str] = field(default_factory=list)      # argv template w/ {session},{worktree},{prompt}
    dispatch_extra: list[str] = field(default_factory=list)
    session_capture: str | None = None                    # e.g. "newest-jsonl"
    session_discover: list[str] = field(default_factory=list)
    usage_source: str | None = None
    status: str | None = None       # e.g. "fallback-only"
    role_default: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Routes:
    revision: str
    tiers: dict[str, list[str]]
    routes: dict[str, RouteDef]

    @classmethod
    def load(cls, path: Path | None = None) -> "Routes":
        p = path or paths.routes_path()
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        raw_routes: dict[str, dict[str, Any]] = dict(data.get("routes", {}))

        def resolve(rid: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
            if rid in seen:
                raise ValueError(f"routes.toml inherit cycle at {rid}")
            spec = dict(raw_routes[rid])
            parent = spec.pop("inherit", None)
            if parent:
                base = resolve(parent, seen + (rid,))
                merged = dict(base)
                merged.update(spec)
                return merged
            return spec

        routes: dict[str, RouteDef] = {}
        known = {f for f in RouteDef.__dataclass_fields__ if f not in ("route_id", "raw")}
        for rid in raw_routes:
            spec = resolve(rid)
            kw = {k: v for k, v in spec.items() if k in known}
            routes[rid] = RouteDef(route_id=rid, raw=spec, **kw)
        tiers = {t: list(spec["routes"]) for t, spec in data.get("tiers", {}).items()}
        return cls(revision=str(data.get("revision", "unversioned")), tiers=tiers, routes=routes)

    def for_tier(self, tier: str) -> list[RouteDef]:
        return [self.routes[rid] for rid in self.tiers.get(tier, [])]


# ---------------------------------------------------------------------------
# prices

@dataclass
class Prices:
    revision: str
    models: dict[str, dict[str, Any]]   # model -> {input, output, cached, currency} per 1M tokens

    @classmethod
    def load(cls, path: Path | None = None) -> "Prices":
        p = path or paths.prices_path()
        if not p.exists():
            return cls(revision="absent", models={})
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        return cls(revision=str(data.get("revision", "unversioned")),
                   models=dict(data.get("models", {})))

    def price_tokens(self, model: str, usage: Usage) -> Usage:
        """Fill usage.cost/currency from token counts when a price exists.

        Leaves cost=None (basis unchanged) when the model is unpriced or
        token counts are missing. Never overwrites an existing actual cost.
        """
        if usage.cost is not None or model not in self.models:
            return usage
        m = self.models[model]
        if usage.tokens_in is None or usage.tokens_out is None:
            return usage
        cached = usage.cached_in or 0
        fresh_in = max(usage.tokens_in - cached, 0)
        cost = (fresh_in * float(m["input"])
                + cached * float(m.get("cached", m["input"]))
                + usage.tokens_out * float(m["output"])) / 1_000_000
        usage.cost = round(cost, 6)
        usage.currency = m.get("currency", "USD")
        usage.price_rev = self.revision
        if usage.basis is Basis.UNKNOWN:
            usage.basis = Basis.ESTIMATED
        return usage
