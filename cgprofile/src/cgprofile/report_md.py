"""Markdown twin of the interactive report: matplotlib PNGs plus tables, so
every value a chart shows is also readable as text (DESIGN.md §4.10).

Colours come from the fixed palette in DESIGN.md §5 — the same slots the
plotly HTML report uses — assigned once per entity in :func:`render` and
threaded through every panel, so a target keeps its colour whether it shows
up in one panel or five, and whether or not some other target got filtered
down to nothing in a given panel.
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # a report renderer has no display and must not try to open one

import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)

from . import util
from .model import Analysis, Event, Phase, Proposal, Series

# DESIGN.md §5 — light-mode values, since a static PNG commits to one theme.
_PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
            "#e87ba4", "#008300", "#4a3aa7", "#e34948")
_CHROME = {
    "chart_surface": "#fcfcfb",
    "primary_ink": "#0b0b0b",
    "secondary_ink": "#52514e",
    "muted": "#898781",
    "gridline": "#e1e0d9",
    "baseline": "#c3c2b7",
}
_MIN_CORR_N = 30  # below this, a correlation is not a finding — DESIGN.md §4.9


def _esc(text: Any) -> str:
    """Escape a value bound for a markdown table cell.

    Cgroup paths and container names are untrusted, operator-controlled
    strings (DESIGN.md §4.11 states this rule for the HTML report; it applies
    here just as much) — a ``|`` or newline in one must not be able to break
    the table it lands in.
    """
    return str(text).replace("|", "\\|").replace("\n", " ")


def _assign_colors(entity_order: Sequence[str]) -> Dict[str, str]:
    """Stable entity -> palette slot, assigned once in first-seen order.

    Never cycles past the 8 categorical slots: past that, every further
    entity shares the muted chrome colour rather than repeating slot 1 and
    being read as the same series it is not.
    """
    colors: Dict[str, str] = {}
    for key in entity_order:
        if key in colors:
            continue
        colors[key] = _PALETTE[len(colors)] if len(colors) < len(_PALETTE) else _CHROME["muted"]
    return colors


def _fmt_value(value: float, unit: str) -> str:
    if unit == "bytes":
        return util.fmt_bytes(int(round(value)))
    if unit == "bytes/s":
        return f"{util.fmt_bytes(int(round(value)))}/s"
    if unit == "pct":
        return util.fmt_rate(value, unit="%", places=2)
    if unit in ("cores", "count/s"):
        return util.fmt_rate(value, unit=f" {unit}", places=2)
    return f"{value:.3g}"


def _plot_group(series_list: List[Series], colors: Dict[str, str], out_path: str) -> None:
    """One panel: a line per series, coloured by entity. A ``None`` in
    ``Series.v`` becomes ``NaN`` for the plot call only — matplotlib already
    breaks a line at a NaN instead of drawing through it, which is the
    behaviour DESIGN.md requires and exactly why no interpolation happens
    before this point.
    """
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=150)
    fig.patch.set_facecolor(_CHROME["chart_surface"])
    ax.set_facecolor(_CHROME["chart_surface"])
    for s in series_list:
        color = colors.get(s.target, _CHROME["muted"])
        ys = [float("nan") if v is None else v for v in s.v]
        ax.plot(s.t, ys, color=color, linewidth=1.4, label=s.label)
    ax.set_xlabel("t (s)", color=_CHROME["muted"])
    ax.set_ylabel(series_list[0].unit, color=_CHROME["muted"])
    ax.grid(True, color=_CHROME["gridline"], linewidth=0.6)
    for spine in ax.spines.values():
        spine.set_color(_CHROME["baseline"])
    ax.tick_params(colors=_CHROME["muted"], labelsize=8)
    if len(series_list) > 1:
        legend = ax.legend(loc="upper left", fontsize=7, frameon=False)
        for text in legend.get_texts():
            text.set_color(_CHROME["secondary_ink"])
    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)


def _write_csv(series_list: List[Series], out_path: str) -> None:
    """The chart's own values, one row per point, per series — the "or a
    link to the CSV" half of DESIGN.md §4.10's "no value is chart-only".
    """
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["target", "key", "mono_s", "value"])
        for s in series_list:
            for mono, value in s.points():
                writer.writerow([s.target, s.key, mono, "" if value is None else value])


def _series_table(series_list: List[Series]) -> str:
    lines = ["| target | unit | n | mean | min | max |", "|---|---|---|---|---|---|"]
    for s in series_list:
        finite = s.finite()
        if not finite:
            lines.append(f"| {_esc(s.target)} | {s.unit} | 0 | - | - | - |")
            continue
        mean_v = sum(finite) / len(finite)
        lines.append(
            f"| {_esc(s.target)} | {s.unit} | {len(finite)} | "
            f"{_fmt_value(mean_v, s.unit)} | {_fmt_value(min(finite), s.unit)} | "
            f"{_fmt_value(max(finite), s.unit)} |"
        )
    return "\n".join(lines)


def _run_header(analysis: Analysis) -> str:
    manifest = analysis.manifest or {}
    duration: Optional[float] = None
    started, ended = manifest.get("started"), manifest.get("ended")
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
        duration = ended - started
    elif isinstance(manifest.get("duration"), (int, float)):
        duration = manifest["duration"]

    lines = [f"- **run id:** {_esc(manifest.get('run_id', 'unknown'))}"]
    lines.append(f"- **duration:** {duration:.1f}s" if duration is not None else "- **duration:** unknown")
    if analysis.targets:
        lines.append("- **targets:**")
        for t in analysis.targets:
            lines.append(
                f"  - `{_esc(t.get('key'))}` ({_esc(t.get('role', 'subject'))}) — "
                f"{_esc(t.get('label', t.get('cgroup', '?')))}"
            )
    return "\n".join(lines)


def _phase_table(phases: List[Phase]) -> str:
    if not phases:
        return "_No phases recorded._"
    lines = ["| phase | t0 (s) | t1 (s) | duration (s) | source |", "|---|---|---|---|---|"]
    for p in phases:
        lines.append(f"| {_esc(p.name)} | {p.t0:.1f} | {p.t1:.1f} | {p.duration:.1f} | {p.source} |")
    return "\n".join(lines)


def _event_table(events: List[Event]) -> str:
    if not events:
        return "_No events detected._"
    lines = ["| t (s) | kind | severity | target | message |", "|---|---|---|---|---|"]
    for e in sorted(events, key=lambda ev: ev.mono):
        lines.append(
            f"| {e.mono:.1f} | {_esc(e.kind)} | {_esc(e.severity)} | "
            f"{_esc(e.target)} | {_esc(e.message)} |"
        )
    return "\n".join(lines)


def _correlation_table(correlations: List[Dict[str, Any]]) -> str:
    if not correlations:
        return "_No correlations computed (no observer/subject pair had overlapping data)._"
    lines = ["| observer | subject | metric | r | n | note |", "|---|---|---|---|---|---|"]
    for c in correlations:
        r = c.get("r")
        n = c.get("n", 0)
        r_text = f"{r:.2f}" if r is not None else "-"
        note = "too few samples to call this a finding" if n < _MIN_CORR_N else ""
        lines.append(f"| {_esc(c['observer'])} | {_esc(c['subject'])} | {_esc(c['metric'])} | {r_text} | {n} | {note} |")
    return "\n".join(lines)


def _limits_table(limits: Dict[str, Any]) -> str:
    """One row per cgroup with a structured limit snapshot.

    ``manifest["limits"]`` is ``{cgroup_path: {"resolved": {...}, "described":
    [...], "fingerprint": "..."}}`` (the schema ``analyze.py``'s proposals
    section consumes — see its comment for why). A path whose entry has no
    "resolved" dict (an older run's bare ``describe()`` string list, or no
    snapshot at all) is left out of the table rather than guessed at, same
    as ``make_proposals`` treats it.
    """
    limits = limits or {}
    rows = {
        path: entry["resolved"]
        for path, entry in limits.items()
        if isinstance(entry, dict) and isinstance(entry.get("resolved"), dict)
    }
    if not rows:
        return "_No limit snapshot available for this run._"
    lines = ["| cgroup | memory.min (strict/recursive) | memory.low (strict/recursive) | "
             "memory.high | memory.max | protection mode |", "|---|---|---|---|---|---|"]
    for path in sorted(rows):
        r = rows[path]
        lines.append(
            f"| {_esc(path)} | {util.fmt_bytes(r.get('strict_min'))} / {util.fmt_bytes(r.get('recursive_min'))} | "
            f"{util.fmt_bytes(r.get('strict_low'))} / {util.fmt_bytes(r.get('recursive_low'))} | "
            f"{util.fmt_bytes(r.get('memory_high'))} | {util.fmt_bytes(r.get('memory_max'))} | "
            f"{_esc(r.get('protection_mode', '?'))} |"
        )
    return "\n".join(lines)


def _proposals_section(proposals: List[Proposal]) -> str:
    if not proposals:
        return "_No proposals — nothing in the declared limits or observed series triggered a check._"
    parts: List[str] = []
    for p in proposals:
        parts.append(f"### [{p.severity.upper()}] {_esc(p.title)} _(confidence: {p.confidence})_")
        parts.append(_esc(p.rationale))
        if p.evidence:
            parts.append("**Evidence:**")
            parts.extend(f"- {_esc(e)}" for e in p.evidence)
        if p.change:
            parts.append("**Change:**")
            parts.extend(f"- `{_esc(c)}`" for c in p.change)
        parts.append("")
    return "\n".join(parts)


def render(analysis: Analysis, out_dir: str) -> str:
    """Write ``report.md`` (and ``charts/*.png`` + ``charts/*.csv``) under
    ``out_dir``, and return the report's path.
    """
    charts_dir = os.path.join(out_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    entity_order = [t["key"] for t in analysis.targets if t.get("key")]
    for s in analysis.series:
        if s.target not in entity_order:
            entity_order.append(s.target)
    colors = _assign_colors(entity_order)

    lines: List[str] = [
        f"# cgroup-profiler report — {_esc((analysis.manifest or {}).get('run_id', 'unknown run'))}",
        "",
        _run_header(analysis),
        "",
        "## Phases",
        _phase_table(analysis.phases),
        "",
        "## Events",
        _event_table(analysis.events),
        "",
    ]

    for group in analysis.groups():
        series_list = analysis.series_in(group)
        if not series_list:
            continue
        png_name, csv_name = f"{group}.png", f"{group}.csv"
        _plot_group(series_list, colors, os.path.join(charts_dir, png_name))
        _write_csv(series_list, os.path.join(charts_dir, csv_name))
        lines.extend([
            f"## {group.capitalize()}",
            f"![{group}](charts/{png_name})",
            "",
            _series_table(series_list),
            "",
            f"[raw samples for this panel](charts/{csv_name})",
            "",
        ])

    lines.extend([
        "## Correlations",
        _correlation_table(analysis.correlations),
        "",
        "## Effective limits",
        _limits_table(analysis.limits),
        "",
        "## Proposals",
        _proposals_section(analysis.proposals),
    ])

    report_path = os.path.join(out_dir, "report.md")
    with open(report_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return report_path
