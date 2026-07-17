"""F3: the `/review`-style onboarding assessment scan. PACKAGE F3.

docs/nyxloom-operating-model.md §2 step 3: after the non-AI wizard (F2,
onboarding.py, already merged) records `WizardAnswers`, this module dispatches
a READ-ONLY agent to read the repo (scoped by `answers.scan_paths`, enriched
by `answers.mode`) and returns a STRUCTURED assessment (maturity, what
docs/spec/tests exist, the intent implied by the code, and gaps vs a
would-be spine). The result is stored in the project's trove
(`onboarding-assessment.json`, reloadable) for F4 (the guided questionnaire,
NOT built here) to consume as its starting point.

GREENFIELD SHORT-CIRCUIT: an empty repo (`answers.maturity == "empty"`) has
nothing to scan -- per the operating model ("Skipped for an empty repo"),
this module returns an empty/skipped AssessmentResult WITHOUT ever
dispatching an agent. F4 goes straight to its north-star-first Q&A in that
case.

DISPATCH: reuses decision_chat.py's read-only + redacted agent-dispatch
PATTERN (a `frontier-review` tier route via `config.Routes`, read-only tool
allowlist appended unconditionally, `config.redact()` before the reply is
ever stored) -- re-authored here rather than imported, the same "MIRROR, NOT
FORK" convention intake_chat.py (P29) already established against
decision_chat.py (P18): a sibling module independently re-authors the
identical helper shapes (same literal READONLY_ARGV_SUFFIX, same
_extract_reply_text) instead of importing/depending on either.

DEVIATIONS FROM THE HANDOFF TEXT (flagged for reviewer sign-off, same
convention decision_chat.py's own docstring uses):

1. SIGNATURE. The handoff sketches `run_assessment_scan(cfg, answers)`. This
   module's entry point is `run_assessment_scan(project_root, answers, *,
   trove_name=...)` instead -- mirroring onboarding.run_wizard's own actual
   signature (which takes a bare `project_root: Path`, never a
   `ProjectConfig`), since F2's `run_wizard` deliberately does not require
   the project to be registered with the daemon or to already have a fully
   loadable `ProjectConfig` (onboarding a BRAND NEW project is exactly the
   case where no such config exists yet beyond the trove's own
   `nyxloom.toml`, which run_wizard itself just wrote). Requiring a
   `ProjectConfig` here would force every caller to construct one purely to
   read `default_branch` and default `redact_patterns` (usually empty at
   onboarding time anyway) -- `_read_default_branch` below reads the one
   value actually needed straight from the trove's own `nyxloom.toml`, and
   `config.redact()` (module-level, default patterns) covers the rest. No
   project registration (paths.project_dir / the daemon registry) is
   touched by this module either -- the raw turn log and the parsed
   assessment both live inside the trove (`agent-logs/` -- already
   scaffolded, already gitignored -- and `onboarding-assessment.json`
   respectively), never under the XDG state root.
2. ONE-SHOT, NOT A CHAT. Unlike decision_chat.py/intake_chat.py (resumable
   multi-turn conversations with persisted transcripts), a `/review`-style
   scan is a single read-only pass: one dispatch, one structured reply, no
   resume, no chat persistence file. There is accordingly no
   `build_resume`/session-id-capture path here -- only `build_dispatch`.

INJECTION BOUNDARY: the agent's reply is model-authored free text; it is (a)
passed through `config.redact()` before it is parsed OR stored, (b)
dispatched with a read-only tool allowlist (no Edit/Write/Bash --
READONLY_ARGV_SUFFIX), and (c) NEVER accepted as freeform prose -- see
"structured-output discipline" below.

STRUCTURED-OUTPUT DISCIPLINE (fail-closed): the agent's first-turn system
prompt requires the reply to end with a line `ASSESSMENT_JSON:` followed by
exactly one JSON object with keys {maturity, existing_docs, existing_tests,
intent_summary, gaps}. `_parse_assessment_reply` requires this marker, valid
JSON, and every required key with the right type -- ANY deviation raises
`UnparseableAssessment` (never a silent best-effort/prose fallback). A
missing 'frontier-review' route raises `NoScanRouteConfigured` rather than
degrading to a fabricated assessment (this package must never "stub a scan
that returns fake data in production" -- see the F3 handoff's BLOCKED rule).
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import adapters, config
from .config import RouteDef, Routes
from .onboarding import MATURITY_CHOICES, WizardAnswers, answers_path
from .types import utc_now

# --- tunables (module constants, same convention as decision_chat.py) -----

# Reuses the SAME tier daemon.py's LaunchReview action / decision_chat.py /
# intake_chat.py already dispatch to -- a read-only assessment pass is the
# same "frontier, careful reading" shape as a review or a decision chat.
ASSESSMENT_AGENT_TIER = "frontier-review"

READONLY_ARGV_SUFFIX = ["--allowedTools", "Read Grep Glob",
                         "--disallowedTools", "Edit Write Bash"]

TURN_TIMEOUT_SECONDS = 180

_ASSESSMENT_FILENAME = "onboarding-assessment.json"


# ---------------------------------------------------------------------------
# errors

class AssessmentScanError(Exception):
    """Base class for F3 assessment-scan errors."""


class NoScanRouteConfigured(AssessmentScanError):
    def __init__(self, tier: str):
        self.tier = tier
        super().__init__(f"no '{tier}' route configured for the assessment scan")


class UnparseableAssessment(AssessmentScanError):
    """Fail-closed: the scan agent's reply could not be parsed into a valid
    AssessmentResult (missing marker, invalid JSON, or a missing/mistyped
    required field). The raw (already-redacted) reply is attached for
    diagnostics -- callers must NOT fall back to storing it as-is."""

    def __init__(self, reason: str, raw_reply: str):
        self.reason = reason
        self.raw_reply = raw_reply
        super().__init__(f"unparseable assessment scan reply: {reason}")


# ---------------------------------------------------------------------------
# the assessment schema

_REQUIRED_STR_FIELDS = ("maturity", "intent_summary")
_REQUIRED_LIST_FIELDS = ("existing_docs", "existing_tests", "gaps")


@dataclass
class AssessmentResult:
    """The F3 structured assessment. `skipped=True` marks the greenfield
    short-circuit (no agent was ever dispatched) -- F4 checks this field
    before trying to read the other fields as a real assessment."""

    schema_version: int = 1
    scanned_at: str = ""
    skipped: bool = False
    skip_reason: str | None = None
    maturity: str = ""
    existing_docs: list[str] = field(default_factory=list)
    existing_tests: list[str] = field(default_factory=list)
    intent_summary: str = ""
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scanned_at": self.scanned_at,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "maturity": self.maturity,
            "existing_docs": list(self.existing_docs),
            "existing_tests": list(self.existing_tests),
            "intent_summary": self.intent_summary,
            "gaps": list(self.gaps),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AssessmentResult":
        return cls(
            schema_version=int(d.get("schema_version", 1)),
            scanned_at=d.get("scanned_at", ""),
            skipped=bool(d.get("skipped", False)),
            skip_reason=d.get("skip_reason"),
            maturity=d.get("maturity", ""),
            existing_docs=list(d.get("existing_docs", [])),
            existing_tests=list(d.get("existing_tests", [])),
            intent_summary=d.get("intent_summary", ""),
            gaps=list(d.get("gaps", [])),
        )


def assessment_path(trove_dir: Path) -> Path:
    """Where a project's onboarding assessment is stored -- inside the trove
    (mirrors onboarding.answers_path), reloadable by F4 regardless of
    whether the project is registered with a daemon."""
    return trove_dir / _ASSESSMENT_FILENAME


def _record_assessment(trove_dir: Path, result: AssessmentResult) -> Path:
    path = assessment_path(trove_dir)
    path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_assessment(trove_dir: Path) -> AssessmentResult:
    """Reload the most recently recorded assessment. Raises
    FileNotFoundError if `run_assessment_scan` has not run for this trove
    yet (mirrors onboarding.load_answers)."""
    path = assessment_path(trove_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    return AssessmentResult.from_dict(data)


# ---------------------------------------------------------------------------
# prompt construction (typed WizardAnswers fields only)

_MODE_CONTEXT_HINTS: dict[str, str] = {
    "derive-from-code": (
        "Mode: derive-from-code -- the codebase is the authoritative source "
        "of truth here; derive the implied intent/north-star from it, since "
        "project docs may be thin, stale, or absent."
    ),
    "code-good-docs-absent": (
        "Mode: code-good-docs-absent -- the code is mature and good, but "
        "project-level docs (north-star/roadmap/etc.) are absent; focus on "
        "inferring intent from code structure, naming, and tests rather "
        "than any docs/ prose."
    ),
    "greenfield-define-it": (
        "Mode: greenfield-define-it (unexpected for a non-empty scan -- "
        "this mode is normally paired with maturity=empty, which skips the "
        "scan entirely; treat any code you do find as exploratory/spike "
        "work only)."
    ),
}


def _build_system_prompt(answers: WizardAnswers) -> str:
    scan_paths_text = ", ".join(answers.scan_paths) if answers.scan_paths else "."
    parts = [
        "You are performing a READ-ONLY onboarding assessment scan of a "
        "software repository for nyxloom (docs/nyxloom-operating-model.md "
        "§2 step 3, PACKAGE F3). Your job is to READ the repo and produce a "
        "STRUCTURED assessment -- you must never write, edit, or execute "
        "anything.",
        f"Wizard context: maturity={answers.maturity!r}, "
        f"docs_present={answers.docs_present}, mode={answers.mode!r}.",
        _MODE_CONTEXT_HINTS.get(answers.mode, ""),
        f"Scan ONLY these paths (Read/Grep/Glob, nothing else): {scan_paths_text}",
        "Assess: (1) the code's maturity, (2) what docs/spec/tests already "
        "exist, (3) the intent implied by the code (what this project is "
        "actually trying to do/be), and (4) gaps versus a would-be direction "
        "spine (north-star / product-definition / roadmap / backlog).",
        "You may reason in prose first, but you MUST end your reply with a "
        "line `ASSESSMENT_JSON:` followed immediately by exactly ONE JSON "
        "object (no markdown code fence, nothing after it) with EXACTLY "
        "these keys:\n"
        '  "maturity": one of "empty" | "partial" | "mature"\n'
        '  "existing_docs": [list of doc paths/names you found]\n'
        '  "existing_tests": [list of test paths/names you found]\n'
        '  "intent_summary": a short prose string\n'
        '  "gaps": [list of short gap descriptions]\n'
        "The JSON must be syntactically valid (no comments, no trailing "
        "commas, all four list fields present even if empty). This output "
        "is machine-parsed; an unparseable reply fails the scan.",
    ]
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# reply extraction (same shape as decision_chat._extract_reply_text /
# intake_chat._extract_reply_text -- independently re-authored per the
# MIRROR, NOT FORK convention, see module docstring)

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
# ASSESSMENT_JSON: parsing (fail-closed -- see module docstring)

_ASSESSMENT_MARKER_RE = re.compile(r"^\s*ASSESSMENT_JSON:\s*(.*)$")


def _strip_code_fence(text: str) -> str:
    """Best-effort: the agent was told NOT to wrap the JSON in a fence, but
    strip one if present anyway rather than failing on a harmless format
    slip -- the actual JSON validity check below still fails closed on any
    OTHER deviation."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _validate_assessment_dict(data: dict[str, Any], raw_reply: str) -> AssessmentResult:
    for key in _REQUIRED_STR_FIELDS:
        if key not in data or not isinstance(data[key], str):
            raise UnparseableAssessment(f"missing/invalid string field {key!r}", raw_reply)
    for key in _REQUIRED_LIST_FIELDS:
        val = data.get(key)
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise UnparseableAssessment(f"missing/invalid string-list field {key!r}", raw_reply)
    if data["maturity"] not in MATURITY_CHOICES:
        raise UnparseableAssessment(
            f"invalid maturity {data['maturity']!r}; must be one of {MATURITY_CHOICES}",
            raw_reply,
        )

    return AssessmentResult(
        scanned_at=utc_now().isoformat(),
        maturity=data["maturity"],
        existing_docs=list(data["existing_docs"]),
        existing_tests=list(data["existing_tests"]),
        intent_summary=data["intent_summary"],
        gaps=list(data["gaps"]),
    )


def _parse_assessment_reply(reply_text: str) -> AssessmentResult:
    """`ASSESSMENT_JSON: <json>` (marker line, JSON may continue on
    following lines) -> AssessmentResult. Raises UnparseableAssessment on
    ANY deviation (no marker, invalid JSON, wrong shape) -- fail-closed,
    never a silent prose fallback."""
    lines = reply_text.splitlines()
    marker_idx = None
    inline = ""
    for i, line in enumerate(lines):
        m = _ASSESSMENT_MARKER_RE.match(line.strip())
        if m:
            marker_idx = i
            inline = m.group(1).strip()
            break
    if marker_idx is None:
        raise UnparseableAssessment("no ASSESSMENT_JSON: marker found", reply_text)

    json_lines = ([inline] if inline else []) + lines[marker_idx + 1:]
    json_text = _strip_code_fence("\n".join(json_lines).strip())
    if not json_text:
        raise UnparseableAssessment("ASSESSMENT_JSON: marker had no JSON body", reply_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise UnparseableAssessment(f"invalid JSON: {exc}", reply_text) from exc

    if not isinstance(data, dict):
        raise UnparseableAssessment("JSON body is not an object", reply_text)

    return _validate_assessment_dict(data, reply_text)


# ---------------------------------------------------------------------------
# turn execution (same shape as decision_chat._run_subprocess_turn, minus
# session capture -- this is a one-shot dispatch, never resumed)

def _run_subprocess_turn(argv: list[str], *, worktree: str, log_path: Path) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8") as f:
            subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, text=True,
                            cwd=worktree or None, timeout=TURN_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as exc:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[onboarding scan turn failed: {exc!r}]\n")

    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return _extract_reply_text(text)


def _pick_route(routes_obj: Routes) -> RouteDef | None:
    candidates = routes_obj.for_tier(ASSESSMENT_AGENT_TIER)
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

def run_assessment_scan(
    project_root: Path,
    answers: WizardAnswers,
    *,
    trove_name: str = "nyxloom-trove",
) -> AssessmentResult:
    """The F3 entry point -- run AFTER onboarding.run_wizard (which must have
    already written `<trove>/nyxloom.toml` and recorded `answers`). See the
    module docstring's "DEVIATIONS" note #1 for why this takes a bare
    `project_root: Path` rather than a `ProjectConfig`.

    Greenfield short-circuit: `answers.maturity == "empty"` returns an
    empty/skipped AssessmentResult WITHOUT ever dispatching an agent (no
    route lookup, no subprocess). Otherwise dispatches a read-only
    'frontier-review' agent turn (raising NoScanRouteConfigured if no such
    route exists), parses its ASSESSMENT_JSON: reply (raising
    UnparseableAssessment on any deviation -- fail-closed, see module
    docstring), and records the result to
    `<trove>/onboarding-assessment.json` (reloadable via load_assessment).
    """
    trove_dir = project_root / trove_name

    if answers.maturity == "empty":
        result = AssessmentResult(
            scanned_at=utc_now().isoformat(),
            skipped=True,
            skip_reason="greenfield project (maturity=empty): nothing to scan",
            maturity="empty",
        )
        _record_assessment(trove_dir, result)
        return result

    routes_obj = Routes.load()
    route = _pick_route(routes_obj)
    if route is None:
        raise NoScanRouteConfigured(ASSESSMENT_AGENT_TIER)

    worktree = str(project_root)
    branch = _read_default_branch(trove_dir / "nyxloom.toml")
    system_prompt = _build_system_prompt(answers)

    argv, _prompt = adapters.build_dispatch(
        route, handoff_path=str(answers_path(trove_dir)), worktree=worktree,
        branch=branch, task_id="onboarding-scan", gate_hint="onboarding-scan",
        receipt_path="",
    )
    argv = list(argv) + ["--append-system-prompt", system_prompt] + READONLY_ARGV_SUFFIX

    log_path = trove_dir / "agent-logs" / "onboarding-scan-turn.log"
    reply_raw = _run_subprocess_turn(argv, worktree=worktree, log_path=log_path)
    reply = config.redact(reply_raw)

    result = _parse_assessment_reply(reply)
    _record_assessment(trove_dir, result)
    return result
