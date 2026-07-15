"""Registry, project policy, routes, prices, redaction. FROZEN CORE.

All configuration is TOML (stdlib tomllib). Gates are declared HERE (trusted
project config) as structured argv — model output can never introduce an
executable (SPEC §3, security boundary).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
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
# project policy (.handoffctl/project.toml in the consumer repo)

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
    # Optional inbound command topic (operator -> daemon; e.g. 'handoffctl-cmd').
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
    # Absolute per-attempt wall-clock backstop (P14): interrupt regardless
    # of liveness once exceeded; fm.budget.max_wall_seconds overrides.
    attempt_max_wall_seconds: int = 10800
    reconcile_interval_seconds: int = 30
    wave_max_diffs: int = 3
    http_port: int = 8942           # loopback only


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

    @classmethod
    def load(cls, root: Path) -> "ProjectConfig":
        p = root / ".handoffctl" / "project.toml"
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
        noti = NotifyConfig(**data.get("notify", {}))
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
        )

    def redact(self, text: str) -> str:
        return redact(text, self.redact_patterns)


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
