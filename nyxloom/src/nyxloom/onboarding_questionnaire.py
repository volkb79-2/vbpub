"""F4b: the guided onboarding questionnaire (one-shot draft). PACKAGE F4b.

docs/nyxloom-operating-model.md §2 step 4: after F2 (`onboarding.run_wizard`,
already merged) records `WizardAnswers` and F3 (`onboarding_scan.
run_assessment_scan`) produces a structured `AssessmentResult`, this module
is the FLOW that ties them together -- it dispatches a read-only agent that
reads the repo + the F3 assessment and proposes the direction spine (north-
star / product-definition / roadmap / backlog), then hands the proposal to
F4a's writer (`spine_writer.py`, already merged) to actually draft it,
fail-closed. This module DECIDES what to write; `spine_writer` OWNS the
writing (see its own module docstring) -- no lint import lives there, so the
self-lint + restore-on-reject logic below lives HERE.

SCOPE: the ONE-SHOT draft only. Deliberately NOT built here: an interactive
multi-turn chat, the greenfield-interactive path (an empty repo has nothing
for a scan to have assessed -- see `GreenfieldQuestionnaireUnsupported`
below, a clean typed refusal rather than a fabricated draft), or any
approval/decision machinery (accepting or rejecting the DRAFTED spine is an
operator decision, handled elsewhere -- this module's own self-lint gate
only catches an internally-broken draft, never makes a product judgment
call).

DISPATCH: reuses onboarding_scan.py's read-only + redacted agent-dispatch
PATTERN (a `frontier-review` tier route via `config.Routes`, read-only tool
allowlist appended unconditionally, `config.redact()` before the reply is
ever parsed or stored) -- re-authored here rather than imported, the same
"MIRROR, NOT FORK" convention onboarding_scan.py itself already used against
decision_chat.py/intake_chat.py: a sibling module independently re-authors
the identical helper shapes (same literal `READONLY_ARGV_SUFFIX`, same
`_extract_reply_text`/`_run_subprocess_turn`/`_read_default_branch` shapes)
instead of importing/depending on onboarding_scan's internals. The genuinely
SHARED consumer API (`AssessmentResult`, `load_assessment`, `assessment_path`
from onboarding_scan; `WizardAnswers`, `load_answers`, `answers_path` from
onboarding; the four `write_*` functions + `Feature`/`Milestone`/
`BacklogItem` from spine_writer) IS imported -- only the one-shot-dispatch
PATTERN is re-authored, not the modules' public data contracts.

GREENFIELD: `assessment.skipped` (F3's own greenfield short-circuit, recorded
when `WizardAnswers.maturity == "empty"`) means there was nothing to scan --
this module raises `GreenfieldQuestionnaireUnsupported` rather than
fabricating a draft from nothing; the interactive greenfield north-star-first
Q&A is an explicit follow-up, out of scope here.

INJECTION BOUNDARY: the agent's reply is model-authored free text; it is (a)
passed through `config.redact()` before it is ever parsed OR written into a
spine doc, (b) dispatched with a read-only tool allowlist (no Edit/Write/
Bash -- READONLY_ARGV_SUFFIX), and (c) NEVER accepted as freeform prose --
see "structured-output discipline" below.

STRUCTURED-OUTPUT DISCIPLINE, TWO INDEPENDENT FAIL-CLOSED GATES:

1. PARSE (envelope shape). The agent's system prompt requires the reply to
   end with a line `SPINE_DRAFT_JSON:` followed by exactly one JSON object
   with keys {north_star_body, product_version, features, non_goals?,
   milestones, backlog_items}, each entry carrying its own required fields
   at the right type. `_parse_draft_reply`/`_validate_draft_dict` require
   this marker, valid JSON, and every required key/field with the right
   TYPE -- ANY deviation raises `UnparseableDraft` (never a silent best-
   effort/partial-draft fallback). This gate does NOT check id PATTERNS or
   cross-doc references -- those are semantic/domain rules, not envelope
   shape, and are exactly what gate 2 below catches.
2. SELF-LINT (domain/semantic validity, the false-green guard). Once the
   envelope parses, this module snapshots the 4 spine docs' current text,
   calls spine_writer's four writers to actually draft them, then re-lints
   the spine (`lint.lint_spine`) -- id-pattern violations (S1), dangling
   cross-doc references (S2), and any other spine-lint error are exactly
   the class of "well-formed JSON but internally inconsistent" draft this
   catches. `lint.has_blocking(...)` True -> RESTORE every doc to its
   pre-call (snapshotted) text and raise `UnapprovableDraft` -- a rejected
   draft must NEVER leave the spine in a worse state than before, and this
   module must NEVER report success for a lint-dirty spine.

A missing 'frontier-review' route raises `NoQuestionnaireRoute` rather than
degrading to a fabricated draft (mirrors onboarding_scan's
`NoScanRouteConfigured` -- this package must never "stub a questionnaire
that returns fake data in production").
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import adapters, config, lint
from .config import ProjectConfig, RouteDef, Routes
from .onboarding import WizardAnswers, load_answers
from .onboarding_scan import AssessmentResult, assessment_path, load_assessment
from .spine_writer import (
    BacklogItem,
    Feature,
    Milestone,
    write_backlog,
    write_north_star,
    write_product_definition,
    write_roadmap,
)

# --- tunables (module constants, same convention as onboarding_scan.py) ----

# Same tier a `/review`-style read-only pass already dispatches to (F3's
# ASSESSMENT_AGENT_TIER) -- a guided-questionnaire draft is the same
# "frontier, careful reading" shape.
QUESTIONNAIRE_AGENT_TIER = "frontier-review"

READONLY_ARGV_SUFFIX = ["--allowedTools", "Read Grep Glob",
                         "--disallowedTools", "Edit Write Bash"]

TURN_TIMEOUT_SECONDS = 180

# (ProjectConfig field / nyxloom.toml [project] key) -> fallback numeric-
# prefixed filename -- mirrors spine_writer._FALLBACK_FILENAMES (re-authored
# here, not imported, since it is a private module-level convention this
# module also needs BEFORE calling the writers, to snapshot pre-write
# content for the restore-on-reject path).
_SPINE_FALLBACK_FILENAMES: dict[str, str] = {
    "north_star": "1-north-star.md",
    "product_definition": "2-product-definition.md",
    "roadmap": "3-roadmap.md",
    "backlog": "4-backlog.md",
}


def _spine_doc_path(project_root: Path, cfg: ProjectConfig, config_key: str,
                     *, trove_name: str) -> Path:
    """The same path spine_writer's own `_resolve_path` would resolve to for
    `config_key` -- computed independently so this module can snapshot the
    doc's content BEFORE the writer overwrites it."""
    relpath = getattr(cfg, config_key, None)
    if not relpath:
        relpath = f"{trove_name}/{_SPINE_FALLBACK_FILENAMES[config_key]}"
    return project_root / relpath


# ---------------------------------------------------------------------------
# errors

class QuestionnaireError(Exception):
    """Base class for F4b guided-questionnaire errors."""


class GreenfieldQuestionnaireUnsupported(QuestionnaireError):
    """The recorded assessment is `skipped` (greenfield, maturity=empty) --
    there is nothing for a one-shot read-only draft to work from. The
    interactive greenfield north-star-first path is a follow-up, out of
    scope for this package -- this is a clean typed refusal, never a
    fabricated draft."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        super().__init__(
            f"{project_root}: the recorded assessment is greenfield/skipped "
            "-- the guided questionnaire's one-shot draft has nothing to "
            "work from; the interactive greenfield path is a separate "
            "follow-up, not built here"
        )


class NoQuestionnaireRoute(QuestionnaireError):
    def __init__(self, tier: str):
        self.tier = tier
        super().__init__(f"no '{tier}' route configured for the guided questionnaire")


class UnparseableDraft(QuestionnaireError):
    """Fail-closed gate 1 (envelope shape): the questionnaire agent's reply
    could not be parsed into a valid spine-draft envelope (missing marker,
    invalid JSON, or a missing/mistyped required field/key). The raw
    (already-redacted) reply is attached for diagnostics -- callers must NOT
    fall back to writing a partial draft."""

    def __init__(self, reason: str, raw_reply: str):
        self.reason = reason
        self.raw_reply = raw_reply
        super().__init__(f"unparseable spine draft reply: {reason}")


class UnapprovableDraft(QuestionnaireError):
    """Fail-closed gate 2 (self-lint, the false-green guard): the envelope
    parsed, but writing it produced a lint-dirty spine (bad id shape,
    dangling cross-doc reference, etc). The prior spine content has already
    been RESTORED by the time this is raised -- see the module docstring's
    "SELF-LINT" gate."""

    def __init__(self, findings: list) -> None:
        self.findings = list(findings)
        super().__init__(
            f"drafted spine failed self-lint ({len(self.findings)} blocking "
            "finding(s)) -- restored the prior spine content"
        )


# ---------------------------------------------------------------------------
# the result

@dataclass
class QuestionnaireResult:
    drafted_paths: list[Path]
    feature_count: int
    milestone_count: int
    backlog_count: int
    lint_clean: bool = True


# ---------------------------------------------------------------------------
# prompt construction (typed WizardAnswers + AssessmentResult fields only)

_ID_PATTERN_TEXT = (
    "ID PATTERNS (hard requirement, machine-checked): feature ids must match "
    "^F[0-9]{3,}$ (e.g. F001, F002, ...). Milestone ids must match "
    "^M[0-9]+$ (e.g. M1, M2, ...). Backlog ids must match ^B[A-Za-z0-9-]*$ "
    "(e.g. B1, B-cleanup). Every feature must carry >=1 non-empty "
    "'acceptance' criterion, phrased EARS-style ('the system shall ...', "
    "checkable). CROSS-CONSISTENCY (hard requirement): every roadmap "
    "milestone's 'features' entries MUST be feature ids you actually define "
    "in the 'features' list of THIS SAME reply -- a reference to a feature "
    "id you did not define is a hard error. Every backlog item's "
    "'folds_into' (when set) MUST resolve to a feature id or milestone id "
    "you actually defined in this same reply -- a dangling reference is a "
    "hard error."
)


def _build_system_prompt(answers: WizardAnswers, assessment: AssessmentResult) -> str:
    scan_paths_text = ", ".join(answers.scan_paths) if answers.scan_paths else "."
    existing_docs_text = ", ".join(assessment.existing_docs) if assessment.existing_docs else "(none found)"
    existing_tests_text = ", ".join(assessment.existing_tests) if assessment.existing_tests else "(none found)"
    gaps_text = "; ".join(assessment.gaps) if assessment.gaps else "(none reported)"

    parts = [
        "You are conducting a NORTH-STAR-FIRST guided onboarding "
        "questionnaire for nyxloom (docs/nyxloom-operating-model.md §2 step "
        "4, PACKAGE F4b). This is a ONE-SHOT READ-ONLY pass -- you must "
        "never write, edit, or execute anything. Your job is to PROPOSE a "
        "direction-spine draft (north-star, product-definition, roadmap, "
        "backlog) for a later writer/review step to record.",
        f"Wizard context: maturity={answers.maturity!r}, "
        f"docs_present={answers.docs_present}, mode={answers.mode!r}.",
        f"Scan ONLY these paths (Read/Grep/Glob, nothing else): {scan_paths_text}",
        "A prior read-only assessment scan (PACKAGE F3) already produced "
        "this structured context -- use it, do not re-derive it from "
        f"scratch:\n"
        f"  maturity: {assessment.maturity}\n"
        f"  intent_summary: {assessment.intent_summary}\n"
        f"  existing_docs: {existing_docs_text}\n"
        f"  existing_tests: {existing_tests_text}\n"
        f"  gaps: {gaps_text}",
        "Derive the spine in this ORDER: (1) the north-star FIRST -- the "
        "invariant vision / WHY, as prose, informed by the intent_summary "
        "above; (2) features that FLOW FROM the north-star (not the other "
        "way around), each with >=1 checkable acceptance criterion; (3) "
        "milestones that GROUP those features, referencing their REAL ids; "
        "(4) a backlog of smaller/un-scheduled items, optionally folding "
        "into a real feature or milestone id.",
        _ID_PATTERN_TEXT,
        "You may reason in prose first, but you MUST end your reply with a "
        "line `SPINE_DRAFT_JSON:` followed immediately by exactly ONE JSON "
        "object (no markdown code fence, nothing after it) with EXACTLY "
        "these keys:\n"
        '  "north_star_body": "<vision prose, non-empty>"\n'
        '  "product_version": 1\n'
        '  "features": [{"id": "F001", "title": "...", '
        '"acceptance": ["<checkable criterion>", "..."], '
        '"status": "planned|building|shipped", "milestone": "M1"}, ...]\n'
        '  "non_goals": ["<explicit out-of-scope>", "..."]   (optional)\n'
        '  "milestones": [{"id": "M1", "title": "...", '
        '"target_product_version": 1, "features": ["F001", "F002"], '
        '"status": "planned|active|done"}, ...]\n'
        '  "backlog_items": [{"id": "B1", "title": "...", '
        '"type": "feature|bugfix", "component": "<slug>", '
        '"context_estimate": "small|medium|large", '
        '"folds_into": "F001"}, ...]\n'
        "The JSON must be syntactically valid (no comments, no trailing "
        "commas; 'features'/'milestones'/'backlog_items' present even if "
        "empty). This output is machine-parsed; an unparseable reply, or "
        "one with a malformed/dangling id, fails the draft.",
    ]
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# reply extraction (same shape as onboarding_scan._extract_reply_text --
# independently re-authored per the MIRROR, NOT FORK convention, see module
# docstring)

def _extract_reply_text(log_text: str) -> str:
    lines = log_text.splitlines()
    if not lines:
        return ""

    body_lines = lines
    first = lines[0].strip()
    if first:
        try:
            head = json.loads(first)
        except json.JSONDecodeError:
            head = None
        if isinstance(head, dict) and "session_id" in head:
            body_lines = lines[1:]

    non_blank = [l for l in body_lines if l.strip()]
    if not non_blank:
        return ""

    last = non_blank[-1].strip()
    try:
        data = json.loads(last)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        for key in ("result", "text", "message"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val

    return "\n".join(body_lines).strip()


# ---------------------------------------------------------------------------
# SPINE_DRAFT_JSON: parsing (fail-closed gate 1 -- see module docstring)

_DRAFT_MARKER_RE = re.compile(r"^\s*SPINE_DRAFT_JSON:\s*(.*)$")


def _strip_code_fence(text: str) -> str:
    """Best-effort: the agent was told NOT to wrap the JSON in a fence, but
    strip one if present anyway rather than failing on a harmless format
    slip -- the actual JSON validity/shape checks below still fail closed on
    any OTHER deviation."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _require_str(d: dict, key: str, ctx: str, raw_reply: str, *, allow_empty: bool = False) -> str:
    val = d.get(key)
    if not isinstance(val, str) or (not allow_empty and not val.strip()):
        raise UnparseableDraft(f"{ctx} missing/invalid {key!r} (must be a non-empty string)", raw_reply)
    return val


def _require_str_list(d: dict, key: str, ctx: str, raw_reply: str) -> list[str]:
    val = d.get(key)
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise UnparseableDraft(f"{ctx} missing/invalid {key!r} (must be a list of strings)", raw_reply)
    return val


def _optional_str(d: dict, key: str, ctx: str, raw_reply: str) -> str | None:
    if key not in d or d[key] is None:
        return None
    val = d[key]
    if not isinstance(val, str):
        raise UnparseableDraft(f"{ctx} invalid {key!r} (must be a string when present)", raw_reply)
    return val


def _validate_draft_dict(data: dict[str, Any], raw_reply: str) -> dict[str, Any]:
    """Fail-closed gate 1: envelope SHAPE only (marker/JSON validity, every
    required key present, every field the right TYPE). Deliberately does
    NOT check id patterns (^F.../^M.../^B...) or cross-doc references (a
    milestone's features / a backlog item's folds_into resolving to a real
    id) -- those are DOMAIN/semantic rules, checked by gate 2
    (`lint.lint_spine`, after the draft is actually written) so a
    well-formed-but-inconsistent draft is caught by the SAME machinery that
    would catch a human's mistake, not duplicated here."""
    if not isinstance(data.get("product_version"), int) or isinstance(data.get("product_version"), bool) \
            or data["product_version"] < 1:
        raise UnparseableDraft("missing/invalid 'product_version' (must be an int >= 1)", raw_reply)
    _require_str(data, "north_star_body", "top-level", raw_reply)

    features = data.get("features")
    if not isinstance(features, list):
        raise UnparseableDraft("missing/invalid 'features' (must be a list)", raw_reply)
    for i, f in enumerate(features):
        if not isinstance(f, dict):
            raise UnparseableDraft(f"features[{i}] is not an object", raw_reply)
        _require_str(f, "id", f"features[{i}]", raw_reply)
        _require_str(f, "title", f"features[{i}]", raw_reply)
        _require_str_list(f, "acceptance", f"features[{i}]", raw_reply)
        _require_str(f, "status", f"features[{i}]", raw_reply)
        _optional_str(f, "milestone", f"features[{i}]", raw_reply)

    if "non_goals" in data and data["non_goals"] is not None:
        _require_str_list(data, "non_goals", "top-level", raw_reply)

    milestones = data.get("milestones")
    if not isinstance(milestones, list):
        raise UnparseableDraft("missing/invalid 'milestones' (must be a list)", raw_reply)
    for i, m in enumerate(milestones):
        if not isinstance(m, dict):
            raise UnparseableDraft(f"milestones[{i}] is not an object", raw_reply)
        _require_str(m, "id", f"milestones[{i}]", raw_reply)
        _require_str(m, "title", f"milestones[{i}]", raw_reply)
        if not isinstance(m.get("target_product_version"), int) or isinstance(m.get("target_product_version"), bool):
            raise UnparseableDraft(f"milestones[{i}] missing/invalid 'target_product_version' (must be an int)", raw_reply)
        _require_str_list(m, "features", f"milestones[{i}]", raw_reply)
        _require_str(m, "status", f"milestones[{i}]", raw_reply)

    backlog_items = data.get("backlog_items")
    if not isinstance(backlog_items, list):
        raise UnparseableDraft("missing/invalid 'backlog_items' (must be a list)", raw_reply)
    for i, b in enumerate(backlog_items):
        if not isinstance(b, dict):
            raise UnparseableDraft(f"backlog_items[{i}] is not an object", raw_reply)
        _require_str(b, "id", f"backlog_items[{i}]", raw_reply)
        _require_str(b, "title", f"backlog_items[{i}]", raw_reply)
        _require_str(b, "type", f"backlog_items[{i}]", raw_reply)
        _optional_str(b, "component", f"backlog_items[{i}]", raw_reply)
        _optional_str(b, "context_estimate", f"backlog_items[{i}]", raw_reply)
        _optional_str(b, "folds_into", f"backlog_items[{i}]", raw_reply)

    return data


def _parse_draft_reply(reply_text: str) -> dict[str, Any]:
    """`SPINE_DRAFT_JSON: <json>` (marker line, JSON may continue on
    following lines) -> the parsed+shape-validated dict. Raises
    UnparseableDraft on ANY deviation (no marker, invalid JSON, wrong shape)
    -- fail-closed, never a silent partial-draft fallback."""
    lines = reply_text.splitlines()
    marker_idx = None
    inline = ""
    for i, line in enumerate(lines):
        m = _DRAFT_MARKER_RE.match(line.strip())
        if m:
            marker_idx = i
            inline = m.group(1).strip()
            break
    if marker_idx is None:
        raise UnparseableDraft("no SPINE_DRAFT_JSON: marker found", reply_text)

    json_lines = ([inline] if inline else []) + lines[marker_idx + 1:]
    json_text = _strip_code_fence("\n".join(json_lines).strip())
    if not json_text:
        raise UnparseableDraft("SPINE_DRAFT_JSON: marker had no JSON body", reply_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise UnparseableDraft(f"invalid JSON: {exc}", reply_text) from exc

    if not isinstance(data, dict):
        raise UnparseableDraft("JSON body is not an object", reply_text)

    return _validate_draft_dict(data, reply_text)


# ---------------------------------------------------------------------------
# turn execution (same shape as onboarding_scan._run_subprocess_turn, minus
# session capture -- this is a one-shot dispatch, never resumed)

def _run_subprocess_turn(argv: list[str], *, worktree: str, log_path: Path) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8") as f:
            subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, text=True,
                            cwd=worktree or None, timeout=TURN_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as exc:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[onboarding questionnaire turn failed: {exc!r}]\n")

    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return _extract_reply_text(text)


def _pick_route(routes_obj: Routes) -> RouteDef | None:
    candidates = routes_obj.for_tier(QUESTIONNAIRE_AGENT_TIER)
    return candidates[0] if candidates else None


def _read_default_branch(toml_path: Path, fallback: str = "main") -> str:
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return fallback
    branch = data.get("project", {}).get("default_branch")
    return branch if isinstance(branch, str) and branch else fallback


# ---------------------------------------------------------------------------
# the entry point

def run_questionnaire(project_root: Path, *, trove_name: str = "nyxloom-trove") -> QuestionnaireResult:
    """The F4b entry point -- run AFTER onboarding.run_wizard (F2) AND
    onboarding_scan.run_assessment_scan (F3) have both already recorded
    their state for this project (see module docstring's "DEVIATIONS"-style
    rationale in onboarding_scan.py for why this takes a bare
    `project_root: Path` rather than a `ProjectConfig` -- same reasoning
    applies here: a `ProjectConfig` IS constructed internally below, once
    the trove/nyxloom.toml written by F2 is known to already exist).

    Raises `GreenfieldQuestionnaireUnsupported` if the recorded assessment
    is `skipped` (greenfield). Raises `NoQuestionnaireRoute` if no
    'frontier-review' route is configured (no dispatch attempted). Raises
    `UnparseableDraft` if the agent's reply does not parse into a valid
    spine-draft envelope (no spine doc is touched). Raises
    `UnapprovableDraft` if the parsed draft, once WRITTEN via spine_writer,
    fails self-lint (`lint.lint_spine`) -- the prior spine content is
    restored before this is raised. Returns a `QuestionnaireResult` only
    once the drafted spine is confirmed lint-clean.
    """
    trove_dir = project_root / trove_name
    cfg = ProjectConfig.load(project_root)

    answers = load_answers(trove_dir)
    assessment = load_assessment(trove_dir)

    if assessment.skipped:
        raise GreenfieldQuestionnaireUnsupported(project_root)

    routes_obj = Routes.load()
    route = _pick_route(routes_obj)
    if route is None:
        raise NoQuestionnaireRoute(QUESTIONNAIRE_AGENT_TIER)

    worktree = str(project_root)
    branch = _read_default_branch(trove_dir / "nyxloom.toml")
    system_prompt = _build_system_prompt(answers, assessment)

    argv, _prompt = adapters.build_dispatch(
        route, handoff_path=str(assessment_path(trove_dir)), worktree=worktree,
        branch=branch, task_id="onboarding-questionnaire",
        gate_hint="onboarding-questionnaire", receipt_path="",
    )
    argv = list(argv) + ["--append-system-prompt", system_prompt] + READONLY_ARGV_SUFFIX

    log_path = trove_dir / "agent-logs" / "onboarding-questionnaire-turn.log"
    reply_raw = _run_subprocess_turn(argv, worktree=worktree, log_path=log_path)
    reply = config.redact(reply_raw)

    draft = _parse_draft_reply(reply)

    # Snapshot the 4 spine docs' CURRENT text before touching anything --
    # the restore-on-reject path below (gate 2) needs the exact prior bytes.
    doc_keys = ("north_star", "product_definition", "roadmap", "backlog")
    doc_paths = {k: _spine_doc_path(project_root, cfg, k, trove_name=trove_name) for k in doc_keys}
    snapshot = {k: (p.read_text(encoding="utf-8") if p.exists() else None) for k, p in doc_paths.items()}

    features = [
        Feature(id=f["id"], title=f["title"], acceptance=list(f["acceptance"]),
                status=f["status"], milestone=f.get("milestone"))
        for f in draft["features"]
    ]
    milestones = [
        Milestone(id=m["id"], title=m["title"], target_product_version=m["target_product_version"],
                  features=list(m["features"]), status=m["status"])
        for m in draft["milestones"]
    ]
    backlog_items = [
        BacklogItem(id=b["id"], title=b["title"], type=b["type"],
                    component=b.get("component"), context_estimate=b.get("context_estimate"),
                    folds_into=b.get("folds_into"))
        for b in draft["backlog_items"]
    ]

    p_north_star = write_north_star(project_root, body=draft["north_star_body"], trove_name=trove_name)
    p_product_definition = write_product_definition(
        project_root, product_version=draft["product_version"], features=features,
        non_goals=draft.get("non_goals"), trove_name=trove_name,
    )
    p_roadmap = write_roadmap(project_root, milestones=milestones, trove_name=trove_name)
    p_backlog = write_backlog(project_root, items=backlog_items, trove_name=trove_name)

    # Gate 2: self-lint. A rejected draft must never leave the spine worse
    # than before -- restore the snapshotted text on ANY blocking finding.
    findings_by_doc = lint.lint_spine(ProjectConfig.load(project_root))
    all_findings = [f for findings in findings_by_doc.values() for f in findings]
    if lint.has_blocking(all_findings):
        for key, path in doc_paths.items():
            prior_text = snapshot[key]
            if prior_text is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(prior_text, encoding="utf-8")
        raise UnapprovableDraft(all_findings)

    return QuestionnaireResult(
        drafted_paths=[p_north_star, p_product_definition, p_roadmap, p_backlog],
        feature_count=len(features),
        milestone_count=len(milestones),
        backlog_count=len(backlog_items),
        lint_clean=True,
    )
