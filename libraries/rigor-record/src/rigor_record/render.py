"""Byte-deterministic renderers (SPEC 13): the record, the release-body appendix, ``rigor/index.json`` and ``RIGOR.md``.

Every function is a pure function of its arguments: no clock, no environment, no locale, no dict-order dependence.
The same records always give the same bytes, which is what lets ``check`` detect a hand edit by regeneration. The
views derive a commit's level summary from stored results (``derive``); ``targets_at`` maps a commit to the object
ids of the paths its results name (the caller reads git; tests pass a dict function).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .derive import DECLARED, NOT_JUDGED, NotesProvider, TargetsAt, held_results, latest_label, level_summary, worst
from .privacy import lint_string
from .validate import LIMITS, _semver_key, canonical_json, validate_record

INDEX_SCHEMA = "urn:vbpub:rigor-index:1"
RECORD_SCHEMA = "urn:vbpub:rigor-record:1"
#: Rows of the release table in ``RIGOR.md`` (memo 6.1) and of the "judged since the last release" table.
TABLE_ROWS = 25
#: Unlabelled commits kept in ``index.json`` besides every labelled one.
INDEX_UNLABELLED = 200
#: Open survivors listed in the latest-release detail.
SURVIVORS_SHOWN = 20
_DASH = "—"
_TOOL_LABELS = (("assay", "assay"), ("run_gate", "run-gate"))


class RenderError(Exception):
    """A renderer refused its input (invalid record, size or privacy)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# -- small text helpers ----------------------------------------------------------------------------------------
def _pct(value: float) -> str:
    return f"{value:.1f}"


def _tag_version(tag: str) -> str:
    """``cgprofile-v1.2.0`` -> ``v1.2.0`` (the first ``-v<digits>.`` splits prefix from version)."""
    match = re.fullmatch(r".+?-(v[0-9]+\.[0-9]+\.[0-9]+.*)", tag)
    assert match is not None, tag  # tags are schema-validated before they reach a renderer
    return match.group(1)


def _num(value: Any) -> str:
    return _DASH if value is None else str(value)


def _scope_text(scope: dict) -> str:
    if scope["kind"] == "whole-source":
        return "whole source"
    base = _tag_version(scope["base_label"]) if "base_label" in scope else scope["base_commit"][:12]
    return f"since {base}" + ("" if scope["anchored"] else ", unanchored")


def r2_text(result: Mapping) -> str:
    """One R2 result as text (the lane detail and the release body)."""
    status = result["levels"]["R2"]
    candidates, score = result["candidates"], result["score"]
    if status in ("pass", "pass-with-waivers", "survivors"):
        sv = result["survivors"]
        if status == "survivors":
            head = f"survivors {sv['open']} open / {candidates['total']}"
        elif status == "pass-with-waivers":
            head = f"pass-with-waivers {sv['equivalent_reviewed'] + sv['accepted']} waived / {candidates['total']}"
        else:
            head = f"pass {candidates['total']} candidates"
        shown = "n/a" if score is None else f"{score * 100:.1f} %"
        return f"{head} (score {shown}), {_scope_text(result['scope'])}"
    if status == "incomplete":
        return f"incomplete {candidates['executed']}/{candidates['total']}"
    return status


def _gate_results(record: Mapping) -> list[Mapping]:
    return [r for r in record["results"] if r["kind"] != "sequence"]


def tools_text(record: Mapping) -> str:
    """The tools of the stored results, first version seen per tool, as ``assay 12.0.0, run-gate 25.0.1``."""
    versions: dict[str, str] = {}
    for result in _gate_results(record):
        if result["toolset"] is None or "R2" in result["levels"]:
            continue
        toolset = record["toolsets"][result["toolset"]]
        for key, label in _TOOL_LABELS:
            if key in toolset:
                versions.setdefault(label, toolset[key]["version"])
        cgprofile = toolset.get("cgprofile")
        if cgprofile is not None and cgprofile["daemon_version"] is not None:
            versions.setdefault("cgprofile", cgprofile["daemon_version"])
    return ", ".join(f"{label} {version}" for label, version in versions.items()) or _DASH


def _own_targets(record: Mapping) -> TargetsAt:
    merged = {path: oid for r in _gate_results(record) for path, oid in r["targets"].items()}
    return lambda _commit: merged


def _coverage_cell(status: str, results: Iterable[Mapping]) -> str:
    covered = [r["coverage"] for r in results if "coverage" in r]
    if not covered or status != "pass":
        return status
    cov = covered[0]
    return f"{_pct(cov['line_pct'])}/{_pct(cov['branch_pct'])}"


def _waived(results: Iterable[Mapping]) -> int:
    return sum(1 for r in results if "waiver" in r)


# -- the record ------------------------------------------------------------------------------------------------
def render_record(record: dict, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> str:
    """Canonical text of a valid record. An invalid record is refused, never normalised."""
    violations = validate_record(record, hosts=hosts, users=users)
    if violations:
        raise RenderError([str(v) for v in violations])
    return canonical_json(record)


# -- the release-body appendix ---------------------------------------------------------------------------------
def render_body(record: dict, view_path: str, *, hosts: Iterable[str] = (), users: Iterable[str] = ()) -> str:
    """The text cmru appends under ``## Rigor at publication`` (memo 5.1): at most 8 KiB, privacy-clean."""
    summary = level_summary([record], record["commit"], _own_targets(record))
    held = {name: held_results([record], record["commit"], _own_targets(record), name).values() for name in ("R1", "R3")}
    cells = {}
    for name in ("R0", "R1", "R3"):
        text = summary[name]
        results = [r for r in _gate_results(record) if name in r["levels"]]
        if name == "R1" and text == "pass":
            text = "pass (" + ", ".join(
                f"line {_pct(r['coverage']['line_pct'])} %, branch {_pct(r['coverage']['branch_pct'])} %" for r in held["R1"]
            ) + ")"
        if _waived(results):
            text += f" ({_waived(results)} waived lane{'' if _waived(results) == 1 else 's'})"
        cells[name] = text
    lines = [
        "| Level | Result |",
        "|---|---|",
        f"| R0 | {cells['R0']} |",
        f"| R1 | {cells['R1']} |",
        f"| R3 | {cells['R3']} |",
        f"| Review | {record['review']['status']} |",
        "| R2 | pending: runs after release on the latest release only |",
        "",
        "R1-R3 count only when assay judged them; command lanes are declared, not judged.",
        f"Tools: {tools_text(record)}",
        f"Record: {view_path}/RIGOR.md on main",
    ]
    text = "\n".join(lines) + "\n"
    problems = [
        f"release body line {number}: privacy rule {f.rule}"
        for number, line in enumerate(lines, 1) for f in lint_string(line, "$", hosts=hosts, users=users)
    ]
    size = len(text.encode("utf-8"))
    if size > LIMITS["release-body"]:
        problems.append(f"release body is {size} bytes, over the {LIMITS['release-body']} byte limit")
    if problems:
        raise RenderError(problems)
    return text


# -- ordering --------------------------------------------------------------------------------------------------
def judged_at(record: Mapping) -> str | None:
    """The label's publication time, else the newest R2 campaign completion, else ``None``."""
    stamps = [label["published_at"] for label in record["labels"]]
    stamps = stamps or [r["campaign"]["completed_at"] for r in _gate_results(record) if "campaign" in r]
    return max(stamps, default=None)


def storage_order(records: Iterable[dict]) -> list[dict]:
    """Oldest first by judged time, then commit: the order ``derive`` reads records in (later records win)."""
    return sorted(records, key=lambda r: (judged_at(r) or "", r["commit"]))


def _release_order(records: list[dict]) -> list[dict]:
    """Labelled records, newest first by their newest label's version."""
    labelled = [r for r in records if r["labels"]]
    return sorted(labelled, key=lambda r: max(_semver_key(label["version"]) for label in r["labels"]), reverse=True)


def _latest_record(records: list[dict]) -> dict | None:
    tag = latest_label(records)
    return next((r for r in records if any(label["tag"] == tag for label in r["labels"])), None)


def _stable_tag(ordered: list[dict]) -> str | None:
    """The newest current label whose commit's ``promotion`` qualification passed (memo 5.4; null when none)."""
    for record in ordered:
        promotion = record["qualifications"].get("promotion")
        if promotion is None or worst(r["levels"]["R0"] for r in promotion["results"]) not in ("pass", "pass-with-waivers"):
            continue
        current = [label for label in record["labels"] if label["state"] == "current"]
        if current:
            return max(current, key=lambda label: _semver_key(label["version"]))["tag"]
    return None


# -- index.json ------------------------------------------------------------------------------------------------
def _r2_scope(record: dict, commit_held: Mapping[str, Mapping]) -> tuple[dict | None, bool | None]:
    results = [commit_held[lane] for lane in sorted(commit_held)]
    if not results:
        return None, None
    scope = results[0]["scope"]
    return {"kind": scope["kind"], "base_commit": scope["base_commit"]}, scope["anchored"]


def build_index(records: Iterable[dict], project: str, targets_at: TargetsAt, notes: NotesProvider | None = None) -> dict:
    stored = storage_order(records)
    release_order = _release_order(stored)
    unlabelled = sorted((r for r in stored if not r["labels"]), key=lambda r: (judged_at(r) or "", r["commit"]), reverse=True)
    kept = release_order + unlabelled[:INDEX_UNLABELLED]
    entries = []
    for record in kept:
        held = held_results(stored, record["commit"], targets_at, "R2")
        scope, anchored = _r2_scope(record, held)
        entries.append({
            "commit": record["commit"],
            "labels": sorted(label["tag"] for label in record["labels"]),
            "judged_at": judged_at(record),
            "levels": level_summary(stored, record["commit"], targets_at, notes),
            "r2_scope": scope,
            "anchored": anchored,
        })
    return {
        "schema": INDEX_SCHEMA,
        "project": project,
        "latest": latest_label(stored),
        "stable": _stable_tag(release_order),
        "adopted_from": None,
        "labels": {label["tag"]: r["commit"] for r in release_order for label in r["labels"]},
        "commits": entries,
    }


def render_index(records: Iterable[dict], project: str, targets_at: TargetsAt, notes: NotesProvider | None = None) -> str:
    return canonical_json(build_index(records, project, targets_at, notes))


# -- RIGOR.md --------------------------------------------------------------------------------------------------
_HEADER = [
    "# Rigor evidence",
    "",
    "Generated by `rigor-record render`; do not edit. Schema: `urn:vbpub:rigor-record:1`.",
    "",
    "- R0: the project's release sequence passed (every member lane).",
    "- R1: the line and branch coverage gate passed, as judged by assay.",
    "- R3: the gate-canary check passed (a planted fault is detected), as judged by assay.",
    "- R2: the mutation campaign; it runs on the latest release only and counts when its targets match.",
    "- R1-R3 count only when assay judged them; command lanes are declared, not judged.",
]

_LEGEND = [
    "## Legend",
    "",
    "- `pass`: every lane passed. `pass-with-waivers`: a failed lane or survivor is waived with an expiry or a reviewed reason.",
    f"- `{NOT_JUDGED}`: no valid assay result holds the level at this commit. A result is valid at a commit exactly when every path it judged has the same object id there.",
    "- R2 `k/n`: k of the n R2 lanes have a valid result. `pending`: not yet run. `superseded`: a newer release exists and this one was never started.",
    "- R2 scope `unanchored`: the incremental chain does not end in a whole-source campaign; earlier code is not mutation-judged.",
    f"- {_DASH}: no value (percentile below its minimum sample count, or not measured).",
]


def _row(record: dict, stored: list[dict], targets_at: TargetsAt, notes: NotesProvider | None) -> str:
    summary = level_summary(stored, record["commit"], targets_at, notes)
    r1 = list(held_results(stored, record["commit"], targets_at, "R1").values())
    tags = ", ".join(
        label["tag"] + (" (withdrawn)" if label["state"] == "withdrawn" else "")
        for label in sorted(record["labels"], key=lambda label: _semver_key(label["version"]), reverse=True)
    )
    stamp = max(label["published_at"] for label in record["labels"])
    cells = [
        tags, stamp[:10], summary["R0"], _coverage_cell(summary["R1"], r1), summary["R3"], summary["review"],
        summary["R2"], tools_text(record),
    ]
    return "| " + " | ".join(cells) + " |"


def _since_row(record: dict, stored: list[dict], targets_at: TargetsAt, notes: NotesProvider | None) -> str:
    summary = level_summary(stored, record["commit"], targets_at, notes)
    r1 = list(held_results(stored, record["commit"], targets_at, "R1").values())
    cells = [
        record["commit"][:12], _num(judged_at(record))[:10], summary["R0"], _coverage_cell(summary["R1"], r1),
        summary["R3"], summary["R2"],
    ]
    return "| " + " | ".join(cells) + " |"


def _distribution_cells(dist: dict) -> list[str]:
    return [str(dist["n"])] + [_num(dist[k]) for k in ("p50", "p90", "p98", "p99", "max")]


def _metric_table(entry: dict, normalised: bool) -> list[str]:
    rows = []
    for name in sorted(entry["metrics"]):
        if name.endswith("_ref") != normalised:
            continue
        dist = entry["metrics"][name]
        if "unavailable" in dist:
            rows.append(f"| {name} | " + " | ".join([_DASH] * 7) + f" | unavailable: {dist['reason']} |")
        else:
            rows.append(f"| {name} | {dist['unit']} | " + " | ".join(_distribution_cells(dist)) + " | |")
    if not rows:
        return []
    caption = "Normalised to the Geekbench baseline core:" if normalised else "Measured on the host:"
    return [caption, "", "| Metric | Unit | n | p50 | p90 | p98 | p99 | max | Note |", "|---|---|---|---|---|---|---|---|---|", *rows, ""]


def _machine_line(machine: dict) -> str:
    speed = machine["speed"]
    if "unavailable" in speed:
        text = f"speed unavailable ({speed['reason']})"
    else:
        text = f"Geekbench {speed['geekbench_major']} single-core {speed['single_core']} ({speed['basis']})"
    hypervisor = "hypervisor" if machine["hypervisor"] else "bare metal"
    return f"- {machine['id']}: {machine['cpu_model']}, {machine['cpus_available']} CPUs, {hypervisor}, {text}"


def _lane_cell(result: Mapping) -> str:
    if result["kind"] == "command":
        return DECLARED
    return ", ".join(f"{name} {result['levels'][name]}" for name in ("R0", "R1", "R3") if name in result["levels"])


def _detail(record: dict, stored: list[dict], targets_at: TargetsAt) -> list[str]:
    tags = ", ".join(label["tag"] for label in record["labels"])
    out = [f"## Latest release detail: {tags}", "", "### Lanes", "", "| Lane | Judged by | Levels | Waiver |", "|---|---|---|---|"]
    for result in sorted(_gate_results(record), key=lambda r: r["lane"]):
        if "R2" in result["levels"]:
            continue
        waiver = result.get("waiver")
        cell = _DASH if waiver is None else f"until before {waiver['expires_before']}: {waiver['reason']}"
        out.append(f"| {result['lane']} | {result['judged_by']} | {_lane_cell(result)} | {cell} |")
    for name in sorted(record["qualifications"]):
        for result in record["qualifications"][name]["results"]:
            out.append(f"| {result['lane']} (qualification {name}) | {result['judged_by']} | {_lane_cell(result)} | {_DASH} |")
    out.append("")
    r2_held = held_results(stored, record["commit"], targets_at, "R2")
    out += ["### R2 mutation campaigns", ""]
    out += [f"- {lane}: {r2_text(r2_held[lane])}" for lane in sorted(r2_held)] or ["- none valid at this commit"]
    out.append("")
    open_listed = [
        s for lane in sorted(r2_held) if "survivors" in r2_held[lane]
        for s in r2_held[lane]["survivors"]["listed"] if s["disposition"] == "open"
    ]
    if open_listed:
        out += [f"### Open survivors ({len(open_listed)})", ""]
        out += [f"- `{s['path']}:{s['line']}` {s['operator']}" for s in open_listed[:SURVIVORS_SHOWN]]
        if len(open_listed) > SURVIVORS_SHOWN:
            out.append(f"- and {len(open_listed) - SURVIVORS_SHOWN} more")
        out.append("")
    waivers = [
        f"- lane {r['lane']}: valid until before {r['waiver']['expires_before']}"
        for r in sorted(_gate_results(record), key=lambda r: r["lane"]) if "waiver" in r
    ]
    for lane in sorted(r2_held):
        sv = r2_held[lane].get("survivors")
        if sv is not None and sv["equivalent_reviewed"] + sv["accepted"]:
            waivers.append(f"- R2 survivors of {lane}: {sv['equivalent_reviewed']} equivalent-reviewed, {sv['accepted']} accepted")
    out += ["### Waivers in force", "", *(waivers or ["- none"]), ""]
    deviations = [d for lane in sorted(r2_held) for d in r2_held[lane].get("deviations", [])]
    out += ["### Deviations", "", *([f"- `{d['file']}`: {d['note']}" for d in deviations] or ["- none"]), ""]
    out += ["### Performance", ""]
    if not record["perf"]:
        out += ["- none recorded", ""]
    for entry in record["perf"]:
        out += [f"#### {entry['lane']} ({entry['level']}, {entry['subject']})", ""]
        out += _metric_table(entry, normalised=False) + _metric_table(entry, normalised=True)
    out += ["Machine classes:", "", *([_machine_line(m) for m in record["machine_classes"]] or ["- none"]), ""]
    return out


def _project_md(stored: list[dict], rows: int, targets_at: TargetsAt, notes: NotesProvider | None) -> str:
    ordered = _release_order(stored)
    latest = _latest_record(stored)
    lines = [*_HEADER, ""]
    latest_tag, stable_tag = latest_label(stored), _stable_tag(ordered)
    stamp = None if latest is None else next(la["published_at"] for la in latest["labels"] if la["tag"] == latest_tag)
    lines += ["## Current guarantees", ""]
    lines.append("- Latest: " + (_DASH if latest is None else f"{latest_tag} ({stamp[:10]})"))
    lines.append("- Stable: " + (stable_tag or _DASH))
    lines += ["", "## Releases", ""]
    if ordered:
        lines += [
            "| Release | Date | R0 | R1 line/branch | R3 | Review | R2 | Tools |",
            "|---|---|---|---|---|---|---|---|",
            *(_row(r, stored, targets_at, notes) for r in ordered[:rows]),
            "",
        ]
        if len(ordered) > rows:
            lines += [f"{len(ordered) - rows} older releases are in `rigor/commits/`.", ""]
    else:
        lines += ["No release has a record yet.", ""]
    since = sorted((r for r in stored if not r["labels"]), key=lambda r: (judged_at(r) or "", r["commit"]), reverse=True)
    lines += ["## Judged since the last release", ""]
    if since:
        lines += [
            "| Commit | Judged | R0 | R1 line/branch | R3 | R2 |",
            "|---|---|---|---|---|---|",
            *(_since_row(r, stored, targets_at, notes) for r in since[:min(rows, TABLE_ROWS)]),
            "",
        ]
    else:
        lines += ["No unreleased commit has a stored result.", ""]
    if latest is not None:
        lines += _detail(latest, stored, targets_at)
    lines += _LEGEND
    return "\n".join(lines) + "\n"


def render_project_md(
    records: Iterable[dict], targets_at: TargetsAt, notes: NotesProvider | None = None, *, limit: int = LIMITS["view"],
) -> str:
    """``<project>/RIGOR.md``. Over ``limit`` bytes the tables lose rows (oldest first); never silently truncated text."""
    stored = storage_order(records)
    for rows in range(TABLE_ROWS, -1, -1):
        text = _project_md(stored, rows, targets_at, notes)
        if len(text.encode("utf-8")) <= limit:
            return text
    raise RenderError([f"RIGOR.md is over the {limit} byte limit even without table rows"])


def render_views(
    records: list[dict], project: str, targets_at: TargetsAt, notes: NotesProvider | None = None, *,
    limit: int = LIMITS["view"],
) -> dict[str, str]:
    """Both generated views of one project, keyed by their path relative to the project directory."""
    return {
        "rigor/index.json": render_index(records, project, targets_at, notes),
        "RIGOR.md": render_project_md(records, targets_at, notes, limit=limit),
    }
