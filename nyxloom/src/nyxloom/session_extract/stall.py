"""Mechanical stall verdicts for Codex runs.

The verdict vocabulary is closed.  Detection uses only the raw Codex records
and the final assistant message: no model call, no wall-clock threshold, and no
host-load-dependent observation.  A session may be healthy while its expected
delivery report is missing or still a skeleton, so the run verdict and report
verdict stay separately visible while the primary verdict names the failure a
controller must act on.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .stopstate import (
    CodexRunFacts,
    StopState,
    _codex_stop_state,
    read_codex_facts,
)

RUN_VERDICTS = frozenset({
    "ok",
    "intent-only-ending",
    "tool-call-as-text",
    "empty-run",
    "ended-mid-wait",
})
REPORT_VERDICTS = frozenset({"report-missing", "report-skeleton"})
VERDICTS = RUN_VERDICTS | REPORT_VERDICTS

DEFAULT_REPORT_MIN_BYTES = 1500
DEFAULT_REPORT_MAX_PENDING_MARKERS = 3

_INTENT_RE = re.compile(
    r"\b(i(?:'|\u2019)?ll|i will|next,?\s+i|now i(?:'|\u2019)?ll|"
    r"i(?:'|\u2019)?m\s+(?:now\s+)?"
    r"(?:[a-z]+ing\b.{0,200}?\bnow\b|"
    r"(?:going to|moving|running|rerunning|checking|testing|writing|adding|"
    r"replacing)))\b",
    re.IGNORECASE,
)
_OUTCOME_RE = re.compile(
    r"\b(report(?:ed)?|head|verdict|pass(?:ed)?|fail(?:ed)?|"
    r"complete(?:d)?|done|finished|wrote|created)\b",
    re.IGNORECASE,
)
_WAIT_PHRASE = r"(?:still running|waiting for|in progress)"
_WAIT_RE = re.compile(rf"\b{_WAIT_PHRASE}\b", re.IGNORECASE)
_WAIT_FINISHED_RE = re.compile(
    r"\b(completed|done|passed|failed|finished|returned)\b",
    re.IGNORECASE,
)
_NEGATED_WAIT_RE = re.compile(
    rf"\b(?:"
    rf"no\s+(?:[a-z0-9-]+\s+){{1,2}}(?:is|was|are|were)"
    rf"|(?:the\s+)?[a-z0-9-]+\s+"
    rf"(?:isn't|wasn't|aren't|weren't|(?:is|was|are|were)\s+not)"
    rf"|there\s+(?:is|was)\s+no\s+(?:[a-z0-9-]+\s+){{1,2}}"
    rf")\s+{_WAIT_PHRASE}\b",
    re.IGNORECASE,
)
_TOOL_TEXT_RE = re.compile(
    r"(<tool_call>|\"name\"\s*:\s*\"(?:shell|bash|exec)\"|"
    r"\*\*\*\s*(?:Begin|End) Patch|^\s*\$\s)",
    re.IGNORECASE | re.MULTILINE,
)
_PENDING_RE = re.compile(
    r"^(?:pending|tbd)\b|:\s*pending\b|\|\s*pending\s*\||"
    r"verdict\s*:\s*pending|in progress\.|^[0-9]+\.\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class ReportCheck:
    status: str
    path: str | None
    bytes: int | None
    pending_markers: int | None
    min_bytes: int
    max_pending_markers: int


@dataclass
class StallCheckReport:
    format: str
    source: str
    run_verdict: str
    verdict: str
    detail: str
    stop_state: StopState
    report: ReportCheck

    def as_dict(self) -> dict:
        return {
            "v": 1,
            "format": self.format,
            "source": self.source,
            "verdict": self.verdict,
            "run_verdict": self.run_verdict,
            "detail": self.detail,
            "stop_state": asdict(self.stop_state),
            "report": asdict(self.report),
        }

    def render(self) -> str:
        state = self.stop_state
        lines = [
            "[stall check]",
            f"source: {self.source}",
            f"verdict: {self.verdict}",
            f"run verdict: {self.run_verdict}",
            f"detail: {self.detail}",
            f"cause: {state.cause}",
            f"last assistant text: {state.last_text or '(none)'}",
            f"in-flight call: {state.in_flight or 'none'}",
            f"report: {self.report.status}",
        ]
        return "\n".join(lines)


def classify_run(facts: CodexRunFacts, state: StopState) -> tuple[str, str]:
    """Return one closed-vocabulary run verdict and a concise reason."""
    text = facts.last_text.strip()
    if facts.terminal == "interrupted":
        return "ended-mid-wait", "the terminal error/abort ended the run before a completed outcome"

    if facts.message_count == 0:
        if facts.command_count == 0 and facts.in_flight is None:
            return "empty-run", "no assistant message or tool call was recorded"
        return "ended-mid-wait", "the run ended without an assistant message after recorded tool work"

    if _TOOL_TEXT_RE.search(text[-600:]):
        return "tool-call-as-text", "the last assistant text contains a tool-call serialization"

    if facts.in_flight is not None:
        return "ended-mid-wait", "the run ended while a tool call was still in flight"

    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    final_paragraph = paragraphs[-1] if paragraphs else text
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", final_paragraph) if part.strip()]
    ending = " ".join(sentences[-3:])[-600:]
    for wait in _WAIT_RE.finditer(ending):
        if any(
            match.end() == wait.end()
            for match in _NEGATED_WAIT_RE.finditer(ending[:wait.end()])
        ):
            continue
        if not _WAIT_FINISHED_RE.search(ending[wait.end():]):
            return "ended-mid-wait", "the final text says a gate or process is still running"

    intents = list(_INTENT_RE.finditer(ending))
    if intents and not _OUTCOME_RE.search(ending[intents[-1].end():]):
        return "intent-only-ending", "the final text announces the next action without an outcome"

    return "ok", "the run ends with a completed assistant message"


def check_report(
    path: Path,
    *,
    min_bytes: int = DEFAULT_REPORT_MIN_BYTES,
    max_pending_markers: int = DEFAULT_REPORT_MAX_PENDING_MARKERS,
) -> ReportCheck:
    """Check an expected delivery report by size and explicit skeleton markers."""
    report_path = Path(path)
    if not report_path.exists():
        return ReportCheck("report-missing", str(report_path), None, None,
                           min_bytes, max_pending_markers)
    if not report_path.is_file():
        return ReportCheck("report-skeleton", str(report_path), None, None,
                           min_bytes, max_pending_markers)
    try:
        text = report_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        # The closed verdict vocabulary has no separate "unreadable" value;
        # unreadable content is not usable delivery evidence.
        return ReportCheck("report-skeleton", str(report_path), None, None,
                           min_bytes, max_pending_markers)
    size = len(text.encode("utf-8"))
    pending = len(_PENDING_RE.findall(text))
    status = (
        "report-skeleton"
        if size < min_bytes or pending > max_pending_markers
        else "ok"
    )
    return ReportCheck(status, str(report_path), size, pending,
                       min_bytes, max_pending_markers)


def check_stall(
    path: Path,
    fmt: str,
    *,
    expect_file: Path | None = None,
    report_min_bytes: int = DEFAULT_REPORT_MIN_BYTES,
    report_max_pending_markers: int = DEFAULT_REPORT_MAX_PENDING_MARKERS,
) -> StallCheckReport:
    """Inspect one Codex rollout/event stream and optional expected report."""
    if fmt not in {"codex", "codex-exec"}:
        raise ValueError(f"stall check does not support {fmt!r}; use a Codex run")
    if report_min_bytes < 0:
        raise ValueError("report minimum bytes must be non-negative")
    if report_max_pending_markers < 0:
        raise ValueError("pending-marker maximum must be non-negative")

    facts = read_codex_facts(path)
    state = _codex_stop_state(facts)
    run_verdict, detail = classify_run(facts, state)
    report = (
        check_report(
            expect_file,
            min_bytes=report_min_bytes,
            max_pending_markers=report_max_pending_markers,
        )
        if expect_file is not None
        else ReportCheck("not-checked", None, None, None,
                         report_min_bytes, report_max_pending_markers)
    )
    verdict = report.status if report.status in REPORT_VERDICTS else run_verdict
    return StallCheckReport(fmt, str(path), run_verdict, verdict, detail, state, report)


def render_json(report: StallCheckReport) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
