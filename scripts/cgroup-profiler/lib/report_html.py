"""The interactive HTML report. Plotly draws every chart; this module only
configures it and wraps it in hand-written HTML.

No canvas, no zoom maths, no tooltip engine lives here — that would duplicate
what plotly's modebar and ``hovermode="x unified"`` already do correctly.
The only non-trivial logic in this file is bookkeeping: which cgroup gets
which of the eight palette slots, and how a series' raw samples become the
1 s/5 s/30 s trace sets the resolution selector swaps between.

**Why colour is assigned once, up front, by entity.** ``analysis.series`` is
whatever order ``analyze.build`` produced it in, and the same target can
appear in several panels (a subject's memory next to its own PSI). Assigning
palette slots by first-appearance order over the *whole* analysis — not per
panel — means a target keeps one hue everywhere it shows up, and toggling one
legend entry in one panel can never change what colour another panel's trace
happens to be. The cost is that a run with more than eight distinct entities
folds the ninth-and-up into the grey "Other" bucket even in a panel that, on
its own, would have had room for it; that is judged the smaller wrong,
because a colour that means something different in every panel is worse than
a colour that occasionally goes unused.

**Why pandas resampling happens here rather than only in analyze.py.**
``Analysis``/``Series`` (lib/model.py) carry exactly one time series per
metric — there is no field for pre-computed lower-resolution variants. DESIGN
§4.11 describes the resolution selector as swapping between sets "resampled
in analyze.py, not here", but the object this module is handed has no such
sets to swap between. Building the four trace sets locally, with
``pandas.Series.resample``, keeps the promise from DESIGN §1 that report
modules "use real libraries... pandas for resampling" rather than hand-roll
one, while still rendering whatever ``Analysis`` it is actually given.

**Why this file does not import jinja2.** DESIGN §4.11 and this task's brief
both assume it is installed; in this checkout's ``venv`` it is not (absent
from ``requirements.txt``, and this module owns neither that file nor the
venv it would need to be added to). Every place jinja2's autoescaping would
have been used, :func:`html.escape` does the identical job for plain text and
attribute values, and JSON blocks destined for a ``<script>`` tag get their
``</`` sequences broken by :func:`_safe_json` so a label cannot close the tag
early. Untrusted labels never reach the page through raw string
concatenation either way.
"""

from __future__ import annotations

import html
import json
import math
import os
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from . import util
from .model import Analysis, Event, Proposal, Series

# ── palette (DESIGN.md §5 — fixed order, never cycled past slot 8) ─────────
PALETTE: Tuple[Tuple[str, str], ...] = (
    ("#2a78d6", "#3987e5"),
    ("#eb6834", "#d95926"),
    ("#1baf7a", "#199e70"),
    ("#eda100", "#c98500"),
    ("#e87ba4", "#d55181"),
    ("#008300", "#008300"),
    ("#4a3aa7", "#9085e9"),
    ("#e34948", "#e66767"),
)
OTHER_COLOR = ("#898781", "#898781")  # the muted/axis grey — never a 9th hue

# Status colours are fixed across themes and reserved for event severity —
# never assigned to a series. model.SEVERITIES is ("info", "warn", "serious",
# "critical"); DESIGN's four-step status scale is ordinally the same thing
# under different names, so "info" (lowest concern) takes the "good" slot.
STATUS_COLOR: Dict[str, str] = {
    "info": "#0ca30c",
    "warn": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

GROUP_LABELS: Dict[str, str] = {
    "memory": "Memory",
    "swap": "Swap",
    "cpu": "CPU",
    "io": "IO",
    "pressure": "PSI pressure",
    "faults": "Faults & refaults",
    "damon": "DAMON",
}
UNIT_LABELS: Dict[str, str] = {
    "bytes": "bytes",
    "bytes/s": "bytes/s",
    "cores": "cores",
    "pct": "%",
    "count": "count",
    "count/s": "count/s",
    "us": "µs",
    "ratio": "ratio",
}

RESOLUTIONS: Tuple[str, ...] = ("raw", "1s", "5s", "30s")
_RESAMPLE_RULE = {"1s": "1s", "5s": "5s", "30s": "30s"}
# Past this many raw points a per-panel chart is heavy enough in the browser
# that starting zoomed out (1 s) beats starting at a resolution nobody can
# see all of anyway; the selector still offers "raw" a click away.
_RAW_POINT_BUDGET = 4000

CHROME_LIGHT = {
    "surface": "#fcfcfb", "plane": "#f9f9f7", "ink": "#0b0b0b",
    "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
}
CHROME_DARK = {
    "surface": "#1a1a19", "plane": "#0d0d0d", "ink": "#ffffff",
    "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
}


def _safe_json(obj: Any) -> str:
    """``json.dumps`` that cannot be used to close a ``<script>`` tag early.

    ``json.dumps`` does not escape ``/`` by default, so a label containing the
    literal text ``</script>`` would terminate the block it sits in. Plotly's
    own serializer already unicode-escapes ``<``/``>``/``/`` (verified against
    this package's 6.9.0); this module's own hand-embedded JSON gets the same
    protection here rather than relying on that being true forever.
    """
    return json.dumps(obj).replace("</", "<\\/")


def _esc(value: Any) -> str:
    """Escape arbitrary, possibly-None, possibly-non-string input for HTML text."""
    return html.escape(str(value if value is not None else ""), quote=True)


# ── colour assignment ───────────────────────────────────────────────────────

def _entity_key(series: Series) -> str:
    return series.target or series.key


def assign_entity_colors(series: Sequence[Series]) -> Dict[str, int]:
    """Map each distinct entity (target) to a palette slot, first-seen order.

    One assignment for the whole report, not per panel: see the module
    docstring for why cross-panel colour stability outranks never wasting a
    slot in a low-cardinality panel.
    """
    slots: Dict[str, int] = {}
    for s in series:
        key = _entity_key(s)
        if key not in slots:
            slots[key] = len(slots)
    return slots


def _color_for(slot: Optional[int], theme_index: int) -> str:
    if slot is None or slot >= len(PALETTE):
        return OTHER_COLOR[theme_index]
    return PALETTE[slot][theme_index]


# ── resampling (pandas — see module docstring) ──────────────────────────────

def _resample_series(s: Series) -> Dict[str, Tuple[List[float], List[Optional[float]]]]:
    """One series' points at every offered resolution.

    ``mean()`` skips NaN by construction, so a bucket straddling a ``None``
    (a genuinely unmeasurable instant, per model.Series) is averaged from
    whatever finite samples it does have rather than being dragged toward
    zero — and a bucket with *no* finite sample stays NaN, which becomes
    ``None`` again on the way out, so the line still breaks there.
    """
    out: Dict[str, Tuple[List[float], List[Optional[float]]]] = {"raw": (list(s.t), list(s.v))}
    if not s.t:
        for res in RESOLUTIONS[1:]:
            out[res] = ([], [])
        return out
    idx = pd.to_timedelta(s.t, unit="s")
    values = pd.Series(s.v, index=idx, dtype="float64")
    for res in RESOLUTIONS[1:]:
        resampled = values.resample(_RESAMPLE_RULE[res]).mean()
        t = (resampled.index.total_seconds()).tolist()
        v = [None if pd.isna(x) else float(x) for x in resampled.to_list()]
        out[res] = (t, v)
    return out


def _default_resolution(series: Sequence[Series]) -> str:
    total = sum(len(s.t) for s in series)
    return "raw" if total <= _RAW_POINT_BUDGET else "1s"


# ── figure construction ─────────────────────────────────────────────────────

def _panel_title(group: str, series_in_group: Sequence[Series]) -> str:
    label = GROUP_LABELS.get(group, group.replace("_", " ").title())
    units = {s.unit for s in series_in_group}
    if len(units) == 1:
        unit_label = UNIT_LABELS.get(next(iter(units)), next(iter(units)))
        return f"{label} ({unit_label})"
    return label  # mixed units inside one group would be an upstream bug


def _event_hover(ev: Event) -> str:
    parts = [f"<b>{html.escape(ev.kind)}</b>", html.escape(ev.severity)]
    if ev.target:
        parts.append(html.escape(ev.target))
    if ev.message:
        parts.append(html.escape(ev.message))
    for k, v in sorted(ev.data.items()):
        parts.append(f"{html.escape(str(k))}={html.escape(str(v))}")
    return "<br>".join(parts)


def build_figure(
    analysis: Analysis, default_resolution: Optional[str] = None
) -> Tuple[go.Figure, Dict[str, Any]]:
    """Build the plotly Figure and the theme-toggle colour bookkeeping for it.

    Returns ``(fig, theme_arrays)`` where ``theme_arrays`` holds the
    light/dark ``line.color`` and ``marker.color`` lists, one entry per trace
    in the order traces were added — the client-side theme toggle restyles
    against these rather than recomputing anything, so it can never disagree
    with what was actually drawn.
    """
    groups = analysis.groups()
    has_events = bool(analysis.events)
    n_rows = len(groups) + (1 if has_events else 0)
    if n_rows == 0:
        n_rows = 1

    row_heights: List[float] = []
    if has_events:
        row_heights.append(0.6)
    row_heights.extend([2.0] * len(groups))
    if not row_heights:
        # Nothing to weight — this is the n_rows==0→1 placeholder row for a
        # run that collected no series and saw no events. make_subplots
        # requires len(row_heights) == rows even then; an empty list raises.
        row_heights = [1.0] * n_rows
    total = sum(row_heights) or 1.0
    row_heights = [h / total for h in row_heights]

    subplot_titles: List[str] = []
    if has_events:
        subplot_titles.append("Events")
    for group in groups:
        subplot_titles.append(_panel_title(group, analysis.series_in(group)))

    fig = make_subplots(
        rows=n_rows, cols=1, shared_xaxes=True, vertical_spacing=0.05 if n_rows > 1 else 0,
        row_heights=row_heights, subplot_titles=subplot_titles or None,
    )
    # A concrete pixel height, not a CSS percentage: the report page has no
    # ancestor with an explicit height for "100%" to resolve against, and a
    # collapsed-to-zero chart is a worse failure mode than a page that
    # scrolls vertically (which is always allowed — only horizontal scroll
    # on the body is not).
    fig.update_layout(height=max(420, 90 + 170 * n_rows))

    entity_slots = assign_entity_colors(analysis.series)
    default_resolution = default_resolution or _default_resolution(analysis.series)

    line_light: List[str] = []
    line_dark: List[str] = []
    marker_light: List[str] = []
    marker_dark: List[str] = []
    trace_resolution: List[Optional[str]] = []

    def add(trace: go.Scatter, *, row: int, lcolor: Tuple[str, str],
            mcolor: Tuple[str, str], resolution: Optional[str]) -> None:
        fig.add_trace(trace, row=row, col=1)
        line_light.append(lcolor[0])
        line_dark.append(lcolor[1])
        marker_light.append(mcolor[0])
        marker_dark.append(mcolor[1])
        trace_resolution.append(resolution)

    row_offset = 1
    if has_events:
        _add_events_row(add, analysis.events, row=1)
        row_offset = 2

    seen_other_in_panel: Dict[int, bool] = {}
    direct_labeled_in_panel: Dict[int, int] = {}
    for panel_idx, group in enumerate(groups):
        row = panel_idx + row_offset
        series_in_panel = analysis.series_in(group)
        for s in series_in_panel:
            slot = entity_slots.get(_entity_key(s))
            is_other = slot is None or slot >= len(PALETTE)
            show_legend_once = True
            if is_other:
                show_legend_once = not seen_other_in_panel.get(row, False)
                seen_other_in_panel[row] = True
            resampled = _resample_series(s)
            colors = (_color_for(slot, 0), _color_for(slot, 1))
            # s.label is untrusted (a cgroup path or container name): plotly's
            # JSON encoder only escapes the </script>-boundary characters, not
            # HTML tags — plotly.js itself renders trace names through a
            # pseudo-HTML pipeline that interprets <a href=...> as a real,
            # clickable link. Every other surface this label reaches
            # (hovertemplate below, the endpoint annotation, the table row)
            # already escapes it; this one must too.
            legend_name = "Other" if is_other else html.escape(s.label)
            for res in RESOLUTIONS:
                t, v = resampled[res]
                trace = go.Scatter(
                    x=t, y=v, mode="lines", name=legend_name,
                    line=dict(color=colors[0], width=2),
                    visible=(res == default_resolution),
                    showlegend=show_legend_once,
                    legendgroup=f"other-{row}" if is_other else s.key + "|" + s.target,
                    hovertemplate=(
                        f"{html.escape(s.label)}: %{{y}}<extra></extra>"
                    ),
                )
                add(trace, row=row, lcolor=colors, mcolor=colors, resolution=res)

            # Direct-label selectively: the endpoint, not every point, and
            # only when a legend alone would leave a reader hunting for which
            # line is which — capped at 4 so a busy panel doesn't sprout a
            # second legend made of text.
            if (
                not is_other
                and len(series_in_panel) >= 2
                and direct_labeled_in_panel.get(row, 0) < 4
            ):
                last_t, last_v = resampled[default_resolution]
                endpoint = next(
                    ((tt, vv) for tt, vv in zip(reversed(last_t), reversed(last_v)) if vv is not None),
                    None,
                )
                if endpoint is not None:
                    fig.add_annotation(
                        x=endpoint[0], y=endpoint[1], text=html.escape(s.label),
                        showarrow=False, xanchor="left", xshift=6,
                        font=dict(size=10, color=colors[0]),
                        row=row, col=1,
                    )
                    direct_labeled_in_panel[row] = direct_labeled_in_panel.get(row, 0) + 1

    fig.update_layout(
        hovermode="x unified",
        template="plotly_white",
        margin=dict(l=60, r=30, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
    )
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05), row=n_rows, col=1)
    for r in range(1, n_rows):
        fig.update_xaxes(rangeslider=dict(visible=False), row=r, col=1)

    for phase in analysis.phases:
        fig.add_vrect(
            x0=phase.t0, x1=phase.t1, row="all", col=1,
            fillcolor="rgba(137,135,129,0.10)", line_width=0, layer="below",
            annotation_text=html.escape(phase.name), annotation_position="top left",
            annotation_font_size=10,
        )

    # Resolution selector: restyle "visible" across every trace at once.
    buttons = []
    for res in RESOLUTIONS:
        visible = [r is None or r == res for r in trace_resolution]
        buttons.append(dict(label=res, method="restyle", args=[{"visible": visible}]))
    fig.update_layout(
        updatemenus=[dict(
            type="buttons", direction="right", buttons=buttons,
            x=0, y=1.12, xanchor="left", yanchor="top",
            showactive=True, active=RESOLUTIONS.index(default_resolution),
        )]
    )

    theme_arrays = {
        "line_light": line_light, "line_dark": line_dark,
        "marker_light": marker_light, "marker_dark": marker_dark,
    }
    return fig, theme_arrays


def _add_events_row(add, events: Sequence[Event], *, row: int) -> None:
    """One Scatter trace per severity, so severity is legend-toggleable and
    still carries its own reserved colour — never a series colour, never
    ``add_vline`` (which cannot be hovered or toggled).
    """
    by_severity: Dict[str, List[Event]] = {}
    for ev in events:
        by_severity.setdefault(ev.severity, []).append(ev)
    targets = sorted({ev.target for ev in events if ev.target})
    y_of = {t: i for i, t in enumerate(targets)} if targets else {}
    for severity in ("critical", "serious", "warn", "info"):
        evs = by_severity.get(severity)
        if not evs:
            continue
        color = STATUS_COLOR[severity]
        trace = go.Scatter(
            x=[ev.mono for ev in evs],
            y=[y_of.get(ev.target, 0) for ev in evs],
            mode="markers",
            name=severity,
            marker=dict(color=color, size=11, symbol="diamond",
                        line=dict(color=color, width=1)),
            hovertext=[_event_hover(ev) for ev in evs],
            hoverinfo="text",
        )
        add(trace, row=row, lcolor=(color, color), mcolor=(color, color), resolution=None)


# ── surrounding hand-written HTML ───────────────────────────────────────────

def _fmt_value(value: Optional[float], unit: str) -> str:
    # NaN/±inf are not the same thing as None in model.py's vocabulary —
    # nothing in this package's own contract puts one in a Series — but
    # pandas arithmetic upstream (a 0-count resample bucket, a 0/0 or x/0
    # rate) can leak one in without going through the documented "None means
    # unmeasurable" convention. All get the same "—": the alternative is
    # `int(round(nan))`/`int(round(inf))` raising ValueError/OverflowError
    # and taking the whole report down over one bad sample.
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    if unit == "bytes":
        return util.fmt_bytes(int(round(value)))
    if unit == "bytes/s":
        return util.fmt_bytes(int(round(value))) + "/s"
    if unit == "count/s":
        return util.fmt_rate(value, unit="/s", places=1)
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "cores":
        return f"{value:.2f} cores"
    if unit == "us":
        return f"{value:,.0f}µs"
    if unit == "count":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _finite_no_nan(s: Series) -> List[float]:
    """``Series.finite()`` minus any NaN/±inf that slipped in under the
    ``None`` convention's radar (see :func:`_fmt_value`) — excluded here
    rather than left for ``min``/``max``/``statistics.fmean`` to trip over:
    ``fmean`` propagates a single NaN into the whole mean, ``min``/``max``
    give an answer that silently depends on whether the NaN happened to sort
    first, and either one would happily accept an infinity as a real value.
    """
    return [x for x in s.finite() if math.isfinite(x)]


def _series_stats_row(s: Series) -> str:
    finite = _finite_no_nan(s)
    n = len(s.v)
    missing = n - len(finite)
    lo = _fmt_value(min(finite), s.unit) if finite else "—"
    hi = _fmt_value(max(finite), s.unit) if finite else "—"
    mean = _fmt_value(statistics.fmean(finite), s.unit) if finite else "—"
    last = _fmt_value(s.v[-1], s.unit) if s.v else "—"
    return (
        "<tr>"
        f"<td>{_esc(s.label)}</td><td>{_esc(s.target)}</td><td>{_esc(s.role)}</td>"
        f"<td class='num'>{n}</td><td class='num'>{missing}</td>"
        f"<td class='num'>{lo}</td><td class='num'>{hi}</td>"
        f"<td class='num'>{mean}</td><td class='num'>{last}</td>"
        "</tr>"
    )


def _render_panel_tables(analysis: Analysis) -> str:
    blocks = []
    for group in analysis.groups():
        series_in = analysis.series_in(group)
        if not series_in:
            continue
        title = _panel_title(group, series_in)
        rows = "".join(_series_stats_row(s) for s in series_in)
        blocks.append(
            "<details class='panel-table'>"
            f"<summary>{_esc(title)} — table view</summary>"
            "<div class='table-scroll'><table>"
            "<thead><tr><th>Series</th><th>Target</th><th>Role</th>"
            "<th class='num'>n</th><th class='num'>missing</th>"
            "<th class='num'>min</th><th class='num'>max</th>"
            "<th class='num'>mean</th><th class='num'>last</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></details>"
        )
    return "\n".join(blocks)


def _generic_table(rows: Sequence[Dict[str, Any]], caption: str) -> str:
    """Render a list-of-dicts of unknown, analyze.py-defined shape.

    ``per_phase``/``per_target``/``correlations`` come from a sibling module
    this file must not assume a fixed schema for; the columns are whatever
    keys are actually present, unioned across rows, so this still renders
    something useful rather than guessing field names that might not exist.
    """
    if not rows:
        return ""
    columns: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in columns:
                columns.append(k)
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body = []
    for r in rows:
        cells = "".join(f"<td>{_esc(r.get(c, ''))}</td>" for c in columns)
        body.append(f"<tr>{cells}</tr>")
    return (
        "<details class='panel-table'>"
        f"<summary>{_esc(caption)}</summary>"
        f"<div class='table-scroll'><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></details>"
    )


def _render_header(analysis: Analysis, title: str) -> str:
    manifest = analysis.manifest or {}
    run_id = manifest.get("run_id", "")
    duration = manifest.get("duration")
    if duration is None and analysis.phases:
        duration = max((p.t1 for p in analysis.phases), default=None)
    duration_text = f"{duration:.1f}s" if isinstance(duration, (int, float)) else "—"

    target_rows = []
    for t in analysis.targets:
        target_rows.append(
            "<tr>"
            f"<td>{_esc(t.get('label', t.get('key', '')))}</td>"
            f"<td>{_esc(t.get('cgroup', ''))}</td>"
            f"<td>{_esc(t.get('role', 'subject'))}</td>"
            f"<td>{_esc(t.get('kind', ''))}</td>"
            "</tr>"
        )
    targets_table = (
        "<table><thead><tr><th>Target</th><th>Cgroup</th><th>Role</th><th>Kind</th></tr></thead>"
        f"<tbody>{''.join(target_rows)}</tbody></table>"
        if target_rows else "<p class='muted'>No targets recorded.</p>"
    )

    limits_html = _render_limits(analysis.limits)

    return f"""
<header class="report-header">
  <h1>{_esc(title)}</h1>
  <dl class="run-meta">
    <div><dt>Run</dt><dd>{_esc(run_id)}</dd></div>
    <div><dt>Duration</dt><dd class="num">{_esc(duration_text)}</dd></div>
    <div><dt>Targets</dt><dd class="num">{len(analysis.targets)}</dd></div>
  </dl>
  <div class="table-scroll">{targets_table}</div>
  {limits_html}
</header>
"""


def _render_limits(limits: Dict[str, Any]) -> str:
    if not limits:
        return ""
    rows = []
    for cgroup, info in limits.items():
        if isinstance(info, dict):
            cells = ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in info.items())
        else:
            cells = _esc(info)
        rows.append(f"<tr><td>{_esc(cgroup)}</td><td>{cells}</td></tr>")
    return (
        "<details class='panel-table' open><summary>Effective limits</summary>"
        "<div class='table-scroll'><table><thead><tr><th>Cgroup</th><th>Effective</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></details>"
    )


def _render_events(events: Sequence[Event]) -> str:
    if not events:
        return "<p class='muted'>No events recorded.</p>"
    items = []
    for ev in sorted(events, key=lambda e: e.mono):
        data_bits = ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in sorted(ev.data.items()))
        items.append(
            "<li class='event-item'>"
            f"<span class='badge sev-{_esc(ev.severity)}'>{_esc(ev.severity)}</span> "
            f"<span class='num'>t={ev.mono:.1f}s</span> "
            f"<strong>{_esc(ev.kind)}</strong> on {_esc(ev.target)}"
            f"<div class='event-msg'>{_esc(ev.message)}</div>"
            + (f"<div class='event-data muted'>{data_bits}</div>" if data_bits else "")
            + "</li>"
        )
    return f"<ul class='event-list'>{''.join(items)}</ul>"


def _render_proposals(proposals: Sequence[Proposal]) -> str:
    if not proposals:
        return "<p class='muted'>No proposals.</p>"
    items = []
    for p in proposals:
        evidence = "".join(f"<li>{_esc(e)}</li>" for e in p.evidence)
        change = "".join(f"<li><code>{_esc(c)}</code></li>" for c in p.change)
        items.append(
            "<li class='proposal-item'>"
            f"<span class='badge sev-{_esc(p.severity)}'>{_esc(p.severity)}</span> "
            f"<span class='badge confidence'>{_esc(p.confidence)}</span> "
            f"<strong>{_esc(p.title)}</strong>"
            f"<p>{_esc(p.rationale)}</p>"
            + (f"<div class='muted'>Evidence</div><ul>{evidence}</ul>" if evidence else "")
            + (f"<div class='muted'>Change</div><ul>{change}</ul>" if change else "")
            + "</li>"
        )
    return f"<ul class='proposal-list'>{''.join(items)}</ul>"


_CSS = """
:root {
  --surface: #fcfcfb; --plane: #f9f9f7; --ink: #0b0b0b; --ink2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7;
}
@media (prefers-color-scheme: dark) {
  :root { --surface: #1a1a19; --plane: #0d0d0d; --ink: #ffffff; --ink2: #c3c2b7;
          --muted: #898781; --grid: #2c2c2a; --axis: #383835; }
}
:root[data-theme="light"] {
  --surface: #fcfcfb; --plane: #f9f9f7; --ink: #0b0b0b; --ink2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7;
}
:root[data-theme="dark"] {
  --surface: #1a1a19; --plane: #0d0d0d; --ink: #ffffff; --ink2: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; overflow-x: hidden; }
body {
  background: var(--plane); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.45;
}
.report-shell { max-width: 1400px; margin: 0 auto; padding: 16px 20px 60px; }
h1, h2, h3 { color: var(--ink); }
.muted { color: var(--muted); }
.num { font-variant-numeric: tabular-nums; text-align: right; }
a { color: inherit; }
.table-scroll { overflow-x: auto; max-width: 100%; }
table { border-collapse: collapse; width: 100%; font-size: 0.9em; }
th, td { padding: 4px 10px; border-bottom: 1px solid var(--grid); text-align: left; white-space: nowrap; }
th { color: var(--ink2); font-weight: 600; border-bottom: 1px solid var(--axis); }
.run-meta { display: flex; gap: 28px; flex-wrap: wrap; margin: 8px 0 16px; }
.run-meta > div { display: flex; flex-direction: column; }
.run-meta dt { color: var(--muted); font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.04em; }
.run-meta dd { margin: 0; font-size: 1.1em; }
.panel-table { margin: 10px 0; border: 1px solid var(--grid); border-radius: 6px; padding: 6px 10px; }
.panel-table summary { cursor: pointer; color: var(--ink2); padding: 6px 0; min-height: 24px; }
.figure-shell { border: 1px solid var(--grid); border-radius: 6px; background: var(--surface);
                overflow-x: auto; margin: 16px 0; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.78em;
         color: #fff; text-transform: uppercase; letter-spacing: 0.03em; }
.badge.confidence { background: var(--muted); }
.sev-info { background: #0ca30c; }
.sev-warn { background: #fab219; color: #241a00; }
.sev-serious { background: #ec835a; }
.sev-critical { background: #d03b3b; }
.event-list, .proposal-list { list-style: none; padding: 0; margin: 0; }
.event-item, .proposal-item { padding: 10px 0; border-bottom: 1px solid var(--grid); min-height: 24px; }
.event-msg { margin-top: 2px; }
.event-data { font-variant-numeric: tabular-nums; }
code { background: var(--grid); padding: 1px 5px; border-radius: 4px; }
.theme-toggle { position: sticky; top: 8px; float: right; z-index: 5;
                background: var(--surface); color: var(--ink); border: 1px solid var(--axis);
                border-radius: 6px; padding: 6px 12px; cursor: pointer; min-height: 24px; }
section { margin: 28px 0; }
"""


def _theme_script(figure_div_id: str, theme_arrays: Dict[str, Any],
                   light_template: Dict[str, Any], dark_template: Dict[str, Any]) -> str:
    return f"""
<script>
(function() {{
  var FIG_ID = {json.dumps(figure_div_id)};
  var COLORS = {_safe_json(theme_arrays)};
  var TEMPLATES = {{ light: {_safe_json(light_template)}, dark: {_safe_json(dark_template)} }};
  var root = document.documentElement;

  function systemTheme() {{
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
      ? 'dark' : 'light';
  }}
  function currentTheme() {{
    return root.getAttribute('data-theme') || systemTheme();
  }}
  function applyToFigure(theme) {{
    var gd = document.getElementById(FIG_ID);
    if (!gd || !window.Plotly) return;
    var lineColors = theme === 'dark' ? COLORS.line_dark : COLORS.line_light;
    var markerColors = theme === 'dark' ? COLORS.marker_dark : COLORS.marker_light;
    var template = theme === 'dark' ? TEMPLATES.dark : TEMPLATES.light;
    Plotly.relayout(gd, {{template: template}});
    Plotly.restyle(gd, {{'line.color': lineColors, 'marker.color': markerColors}});
  }}
  function setTheme(theme) {{
    root.setAttribute('data-theme', theme);
    try {{ localStorage.setItem('cgprofile-theme', theme); }} catch (e) {{}}
    applyToFigure(theme);
    var btn = document.getElementById('cgp-theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
  }}
  window.cgprofileSetTheme = setTheme;
  document.addEventListener('DOMContentLoaded', function() {{
    var saved = null;
    try {{ saved = localStorage.getItem('cgprofile-theme'); }} catch (e) {{}}
    if (saved) root.setAttribute('data-theme', saved);
    applyToFigure(currentTheme());
    var btn = document.getElementById('cgp-theme-toggle');
    if (btn) {{
      btn.textContent = currentTheme() === 'dark' ? 'Light mode' : 'Dark mode';
      btn.addEventListener('click', function() {{
        setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      }});
    }}
  }});
}})();
</script>
"""


def render(
    analysis: Analysis,
    out_path: str,
    title: Optional[str] = None,
    plotlyjs: str = "inline",
) -> str:
    """Render ``analysis`` to a standalone HTML report at ``out_path``.

    ``plotlyjs="inline"`` embeds the ~5 MiB plotly.js from the *installed*
    package straight into the page (one self-contained file, works with no
    network). ``plotlyjs="directory"`` writes a sibling ``plotly.min.js`` and
    references it instead, for the slimmer variant DESIGN §4.11 describes.
    Both read the bytes from ``plotly.offline.get_plotlyjs()`` — never a CDN.
    """
    if plotlyjs not in ("inline", "directory"):
        raise ValueError(f"plotlyjs must be 'inline' or 'directory', got {plotlyjs!r}")

    run_title = title or f"cgroup-profiler — {analysis.manifest.get('run_id', 'run')}"

    fig, theme_arrays = build_figure(analysis)
    figure_div_id = "cgp-figure"

    import plotly.offline as poff

    # Both branches need this directory to exist — a fresh run directory
    # (DESIGN §3) may not have been created by anything else yet.
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    if plotlyjs == "inline":
        plotly_js_tag = f"<script>{poff.get_plotlyjs()}</script>"
    else:
        with open(os.path.join(out_dir, "plotly.min.js"), "w", encoding="utf-8") as fh:
            fh.write(poff.get_plotlyjs())
        plotly_js_tag = '<script src="plotly.min.js"></script>'

    figure_snippet = fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        div_id=figure_div_id,
        config={"scrollZoom": True, "displaylogo": False, "responsive": True},
    )

    light_template = pio.templates["plotly_white"].to_plotly_json()
    dark_template = pio.templates["plotly_dark"].to_plotly_json()
    theme_script = _theme_script(figure_div_id, theme_arrays, light_template, dark_template)

    body = f"""
<div class="report-shell">
  <button id="cgp-theme-toggle" class="theme-toggle" type="button">Dark mode</button>
  {_render_header(analysis, run_title)}
  <section>
    <h2>Timeline</h2>
    <div class="figure-shell">{figure_snippet}</div>
    {_render_panel_tables(analysis)}
  </section>
  <section>
    <h2>Events</h2>
    {_render_events(analysis.events)}
  </section>
  <section>
    <h2>Proposals</h2>
    {_render_proposals(analysis.proposals)}
  </section>
  <section>
    <h2>Additional data</h2>
    {_generic_table(analysis.per_phase, "Per-phase statistics")}
    {_generic_table(analysis.per_target, "Per-target statistics")}
    {_generic_table(analysis.correlations, "Correlations")}
  </section>
  {"".join(f"<p class='muted'>{_esc(n)}</p>" for n in analysis.notes)}
</div>
{theme_script}
"""

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(run_title)}</title>
<style>{_CSS}</style>
</head>
<body>
{plotly_js_tag}
{body}
</body>
</html>
"""

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return out_path
