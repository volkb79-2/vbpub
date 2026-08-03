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
from .log import get_logger
from .stages import (
    DEFAULT_PIPELINE, compose, validate_pipeline, validate_stage_overrides,
)
from .types import Basis, Usage

log = get_logger("config")


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
    log.info("project registered", project_id=project_id, root=str(root))


# ---------------------------------------------------------------------------
# project policy (.nyxloom/project.toml in the consumer repo)

@dataclass
class GateDef:
    gate_id: str
    argv: list[str]                 # trusted; {worktree} placeholder allowed
    phase: str                      # implementation|review|pre-merge|post-merge
    timeout_seconds: int
    environment: str = "local"      # fingerprint label, e.g. "test-runner"
    # GA2 (docs/plan-gate-adoption.md checklist item 7 "rigor declared"): an
    # optional, self-reported list of what this gate actually enforces --
    # constrained by the schema enum to {tests-pass, changed-line-coverage,
    # mutation, canary-verified}. Defaults to [] (undeclared -- the GA1
    # behavior, unchanged). `nyxloom gate verify` cross-checks it against
    # its own observed verdict (a declared rigor claim the probe can
    # actively contradict is a DECLARATION MISMATCH, not just decoration --
    # see cli.cmd_gate_verify), which is what keeps this field off the P43
    # dead-stub list.
    asserts: list[str] = field(default_factory=list)


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
        "FINDING_RECORDED",
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
    # NEVER on host-network, where 0.0.0.0 would expose it to the LAN.
    # 2026-07-20: INFRA-SOURCED, not a toml [policy] key. ProjectConfig.load
    # drops any toml http_bind and sources it SOLELY from the NYXLOOM_HTTP_BIND
    # env var (or this loopback default) -- nyxloom.toml is bind-mounted and
    # shared verbatim host<->container, so it can't differ per target, and the
    # bind is what limits the reach of the OPEN READ surface (CR-15 2026-08-02:
    # mutations require an operator credential -- see control_auth.py -- but
    # every GET is deliberately unauthenticated). Kept on Policy (not a
    # separate infra struct) only so cfg.policy.http_bind consumers are
    # unchanged; it is deliberately absent from nyxloom-config.schema.json.
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
    # GA4 2026-07-25 (module contract item 16, mirrors test_health_interval_days
    # above): cadence in DAYS for the periodic re-verification that this
    # project's declared gate still REJECTS a known-bad canary (GA1's `nyxloom
    # gate verify` probe, re-run on a schedule instead of only ever run by
    # hand). 0 disables it (the default: a gate can quietly stop
    # discriminating -- a lint exclusion widens, a test gets skipped -- with
    # nothing else in the daemon noticing, but a project must opt in before
    # nyxloom starts spending a background subprocess probe on it unasked).
    gate_verify_interval_days: int = 0
    # F007 2026-07-27 (gap-engine, module contract item 17): activity-counted
    # threshold (in changed LINES across gap_audit_source_paths) for the
    # periodic gap-audit carve trigger. UNLIKE its neighbors (time-based cadences
    # *_interval_days), this is activity-counted: the trigger fires when
    # accumulated changed production lines exceed this threshold since the last
    # gap-audit carve, because an idle project must not accrue carve budget on
    # an unchanged codebase. 0 disables (the default: a project must opt in).
    # See reconcile.py module contract item 17 (the trigger) and daemon's
    # _changed_lines_since_gap_audit for the implementation.
    gap_audit_after_changed_lines: int = 0
    # F007 2026-07-27 (module contract item 17): a git pathspec (repo-root-
    # relative) bounding which file changes count as activity for the gap-audit
    # threshold. Example: ["src", "tests"] counts only production+test changes,
    # ignoring docs or config rewrites. An EMPTY list (the default) deliberately
    # counts the whole repo -- set it to your production source roots so a docs-
    # only week will not trigger an audit with no code delta to find gaps in.
    gap_audit_source_paths: list[str] = field(default_factory=list)
    # D-CORRECT-1 2026-07-17: deterministic pre-merge gate on the merged tree
    # in the scratch worktree, BEFORE the ref is published. True by default
    # (safety), settable per-project.
    pre_merge_gate: bool = True
    # F017 2026-07-24: opt-in deterministic mutation gate on the merged tree
    # in the scratch worktree, after the coverage gate, before publish. False
    # by default (expensive: re-runs tests per mutant).
    mutation_gate: bool = False
    # F (factory-hardening) 2026-07-25: when a POST-merge gate fails on the
    # already-published tree, auto-revert the default branch back to the merge
    # commit's parent (CAS update-ref) instead of only transitioning BLOCKED --
    # so a broken tree that slipped past (pre_merge_gate off, a --force manual
    # merge, or a flaky pass) is healed, not just flagged. True by default
    # (safety); the CAS guard means a newer merge landed on top is never
    # clobbered. See daemon._run_post_merge_gate + reference/LESSONS.md L4.
    auto_revert_failed_merge: bool = True
    # F019 P1b 2026-07-25 (D-F019-3, diagnose-first): a pre-merge/mutation gate
    # failure routes the task to REVIEW_REJECTED but carries NO reviewer class,
    # so it always lands in the blind mechanical-retry branch. Once this many
    # CONSECUTIVE gate failures accrue (counted since the last PASSING gate, not
    # reset by the approving reviews between merge attempts), the warm reviewer
    # is dispatched in gate-diagnosis mode to CLASSIFY the failure so the
    # existing triage table routes it (architectural -> re-scope, product ->
    # operator, transient -> plain retry, fixable -> targeted retry with the gate
    # output embedded). Default 1: a warm-session diagnosis is cheaper than one
    # blind implementer re-dispatch. Raise it to absorb a flaky first failure
    # with a plain retry before spending a diagnosis.
    gate_diagnosis_after_failures: int = 1
    # DR8 2026-07-27 (routing-model-redesign.md D-R8, refined; operator
    # decision 2026-07-27): let an already-engaged independent reviewer fix a
    # small test-side gap it finds (missed branch, absent fixture, missing
    # assertion) inline instead of forcing a full carve/implement/review
    # round-trip -- BOUNDED to `reviewer_repair_paths` and mechanically
    # re-checked after the fact (daemon.py's EmitAttemptExit REVIEW_INDEPENDENT
    # consumption), never merely prompted. Unlike this dataclass's other
    # `*_interval_days`/`mutation_gate` opt-in-off knobs -- which default off
    # because ENABLING them makes nyxloom spend budget the operator never
    # asked for (a new carve dispatch, a background probe) -- reviewer_repair
    # spends nothing extra: it is a permission INSIDE an already-dispatched
    # review session, so the opt-in convention does not apply here.
    # `pre_merge_gate: bool = True` above is the existing precedent for a
    # safety/behaviour knob defaulting ON.
    reviewer_repair: bool = True
    # fnmatch globs matched against repo-root-relative POSIX paths; anything
    # a repair touches that matches none of these is out of bounds (and the
    # repair is reverted -- see daemon.py's _enforce_reviewer_repair).
    reviewer_repair_paths: list[str] = field(default_factory=lambda: [
        "*/tests/*", "tests/*", "*/conftest.py", "*/test_*.py",
    ])
    # F007 2026-07-27 (gap-engine wave 2, GAP2 -- plan-gap-engine-and-reviewer-
    # repair.md §GAP2): bounds AND enables the verdict-audit extension appended
    # to the gap-audit carve packet (reconcile.py module contract item 17,
    # unchanged -- GAP2 adds no new trigger/knob for CADENCE, it reuses
    # gap_audit_after_changed_lines). Each gap-audit carve samples at most this
    # many recently-COMPLETED tasks and asks the carver to judge each BLIND
    # (oracles + final merged diff only -- never the recorded verdict/
    # rationale/REJECT_CLASS) before the daemon compares that blind judgment
    # against what it already recorded. 0 disables (the default: a project must
    # opt in) -- with the knob at 0 the gap-audit packet and REQUIRED OUTPUT
    # CONTRACT are byte-identical to pre-GAP2 output, same "opt-in must not
    # perturb existing behaviour" convention gap_audit_after_changed_lines
    # itself follows.
    verdict_audit_sample_size: int = 0
    # CR-16 2026-08-03 (RISK-007 deadman): the durable heartbeat gauge
    # (storage.record_heartbeat, stamped once per completed run_pass) is stale --
    # and doctor.liveness_findings' 'reconcile-deadman' check fires -- once
    # no evidence of a completed pass has landed within
    # reconcile_interval_seconds * deadman_multiple. A multiple, not a flat
    # second count, so the threshold scales automatically with a project's
    # own configured cadence instead of a second knob that can drift out of
    # sync with it.
    deadman_multiple: int = 5


@dataclass
class CarveStageConfig:
    """F018 P1 (plan-long-running-carver.md §9): per-stage config for the
    `carve` stage. ALL fields are optional with safe defaults that mean
    'feature off'. Resolved from `[stage.carve]` in TOML. Not wired into any
    scheduling/behavior in P1 — inert config until P2+."""
    session: str = "fresh"                    # "fresh" | "project-persistent"
    compact_context_ratio: float = 0.70
    compact_after_turns: int = 24
    compact_hard_after_turns: int = 32
    retain_merge_digests: int = 10
    max_resume_failures: int = 2
    max_proposal_repairs: int = 2
    compaction_strategy: str = "rotate"         # "rotate" | "<driver-name>"


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
    # P02 2026-07-21 (docs/plan-logging.md §3 D-L3, layer 3 of the verbosity
    # precedence chain): an OPTIONAL static default log level from this
    # project's own `[logging] level` TOML table. None when the project sets
    # no preference (daemon.resolve_level then falls through to hardcoded
    # INFO). Deliberately top-level (not nested under [policy]) -- same
    # rationale as http_bind's own carve-out (this is a daemon-global
    # runtime-control knob, not a per-project policy value, even though this
    # ONE field happens to be read from a specific project's config as the
    # "static default if nothing else overrides it" layer).
    logging_level: str | None = None
    # F018 P1 (plan-long-running-carver.md §9): carve stage config. Default
    # instance means 'feature off' (session="fresh"). Not wired into
    # scheduling/behavior in P1 -- inert config until P2+.
    carve: CarveStageConfig = field(default_factory=CarveStageConfig)

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
                         environment=g.get("environment", "local"),
                         asserts=list(g.get("asserts", [])))
            for gid, g in data.get("gates", {}).items()
        }
        mutexes = {
            name: MutexDef(name=name, scope=m.get("scope", "project"),
                           capacity=int(m.get("capacity", 1)),
                           global_alias=m.get("global_alias"))
            for name, m in data.get("mutexes", {}).items()
        }
        # http_bind is INFRA-sourced, NOT a toml [policy] key (2026-07-20). It is
        # the address the daemon's HTTP control plane binds to -- whose GET/read
        # half is unauthenticated by design (CR-15 authenticates only mutations),
        # so it is security-relevant AND must differ per deployment target (loopback
        # on the host; 0.0.0.0 on a private ciu bridge). nyxloom.toml is bind-
        # mounted and shared VERBATIM between host and container runs, so it
        # structurally cannot carry a per-target value -- only the infra layer
        # (the ciu/compose NYXLOOM_HTTP_BIND env var) can. Drop any toml value
        # before constructing Policy so the env var (or the loopback default) is
        # the SOLE authority and a hand-edited toml can never force a non-loopback
        # bind on the host. lint's CFG1 (policy is additionalProperties:false, and
        # http_bind is deliberately absent from the schema) already flags a toml
        # http_bind as an unknown key, so this drop is belt-and-braces, not silent.
        policy_data = dict(data.get("policy", {}))
        policy_data.pop("http_bind", None)
        pol = Policy(**policy_data)
        # P38 2026-07-16 / 2026-07-20: the env var is the authority (see above).
        # Absent, the safe loopback default from Policy stands.
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
        # P02 (D-L3 layer 3): `[logging] level`, a plain optional string --
        # no further validation here. An unrecognised name is caught by
        # daemon.resolve_level (which treats it as absent and falls through
        # to the next precedence layer) rather than by config load itself,
        # so a typo in one project's toml never breaks config loading for
        # every project sharing this frozen module.
        logging_level = data.get("logging", {}).get("level")
        # DEBUG only -- NEVER the token/secret VALUE. token_env/cmd_token_env
        # below are the env var NAMEs a secret lives under (see NotifyConfig's
        # own docstring: "the TOKEN VALUE never appears in config files --
        # only the var name"), so logging them is safe by construction.
        log.debug(
            "config resolved",
            project_id=data["project"]["id"],
            root=str(root),
            config_path=str(p),
            pipeline=list(pipeline),
            logging_level=logging_level,
            token_env=noti.token_env,
            cmd_token_env=noti.cmd_token_env,
        )
        # F018 P1: resolve carve stage config from [stage.carve] TOML.
        carve_data = stage_overrides.get("carve", {})
        carve_kw = {k: v for k, v in carve_data.items()
                     if k in CarveStageConfig.__dataclass_fields__}
        carve_cfg = CarveStageConfig(**carve_kw)
        log.debug("carve stage config resolved", session=carve_cfg.session)
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
            logging_level=logging_level if isinstance(logging_level, str) else None,
            carve=carve_cfg,
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
        log.warning("policy update failed: key(s) not found", keys=sorted(remaining))
        raise ValueError(f"policy key(s) not found in [policy] section: {sorted(remaining)}")

    p.write_text("".join(lines), encoding="utf-8")
    log.info("policy updated", keys=sorted(changes))


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
        log.warning("routes update failed: tier(s) not found", tiers=sorted(remaining))
        raise ValueError(f"tier(s) not found or missing routes= line: {sorted(remaining)}")

    p.write_text("".join(lines), encoding="utf-8")
    log.info("routes updated", tiers=sorted(changes))


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
        log.debug("routes resolved", route_count=len(routes), tier_count=len(tiers))
        return cls(revision=str(data.get("revision", "unversioned")), tiers=tiers, routes=routes)

    def for_tier(self, tier: str) -> list[RouteDef]:
        return [self.routes[rid] for rid in self.tiers.get(tier, [])]

    def for_role(self, role: str) -> list[RouteDef]:
        """Routes a role defaults to — decouples call sites from tier names.
        Prefers routes explicitly flagged via RouteDef.role_default (D-R1);
        falls back to a tier named after the role, so tier-named routing
        configs keep resolving (back-compat: the review role's value equals
        the pre-migration tier name, so no flag-day).
        Read-compat (D-CORRECT-2): the review role's value was "frontier-review"
        before the rename; a pre-rename routes.toml (no role_default flag, tier
        still named "frontier-review") still resolves via the legacy alias below."""
        flagged = [r for r in self.routes.values() if r.role_default == role]
        if flagged:
            return flagged
        tier_routes = self.for_tier(role)
        if tier_routes:
            return tier_routes
        if role == "review-independent":          # legacy tier name, pre-D-CORRECT-2
            return self.for_tier("frontier-review")
        return []


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
            log.debug("prices resolved", present=False)
            return cls(revision="absent", models={})
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        log.debug("prices resolved", present=True, model_count=len(data.get("models", {})))
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
