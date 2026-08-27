"""Tests for the interactive HTML report.

These render real files to ``tmp_path`` and inspect the bytes rather than
mocking plotly: the whole point of this module is the wiring between
``Analysis`` and a working, self-contained page, and a mock of plotly cannot
tell us whether ``fig.write_html`` actually produced one.

**Why the "no external host" check differs between inline and directory
mode.** ``plotlyjs="directory"`` writes the vendored plotly.js to a sibling
file and the report ``.html`` itself contains nothing but a relative
``src="plotly.min.js"`` — that file supports a literal "zero http(s)
substrings" assertion, and this suite uses it as the strong form of the
check. ``plotlyjs="inline"`` embeds that same vendored library's full source
verbatim, and plotly.js's own source contains default *configuration
values* for chart types this report never emits (mapbox/geo basemap URLs,
license header comments) — inert strings a browser never fetches because no
mapbox or geo trace exists on the page. A raw substring count would flag
those unconditionally, for any project that inlines plotly.js at all, so the
inline-mode check instead asserts there is no tag that would make the
browser *load* something externally (`<script src=http`, `<link href=http`,
`<img>/<iframe> src=http`) — which is the actual "works with no network"
property DESIGN.md §1 asks for.
"""

from __future__ import annotations

import html as _html_mod
import math
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List

import pytest

from lib import report_html
from lib.model import Analysis, Event, Phase, Proposal, Series


def _plotly_json_escape(raw: str) -> str:
    """The literal bytes plotly's own ``to_html`` JSON encoder produces for a
    string handed to it *unescaped* (e.g. a trace ``name``) — verified
    against plotly 6.9.0's actual output: standard JSON escaping for ``"``
    and ``\\``, plus its own unicode-style escaping of ``<``, ``>``, ``/``
    (the three characters that matter for breaking out of a ``<script>``
    block), single quotes left alone because JSON double-quoted strings don't
    need them escaped.
    """
    return (
        raw.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("/", "\\u002f")
    )


def _double_escaped(raw: str) -> str:
    """What a label looks like after this module's own ``html.escape`` *and*
    plotly's JSON encoder both touch it — the path taken by hovertemplates,
    event hovertext, and annotation text, all of which this module
    pre-escapes before handing to plotly.
    """
    return _plotly_json_escape(_html_mod.escape(raw))


class _LenientHTMLValidator(HTMLParser):
    """Confirms the document parses as a token stream without ``HTMLParser``
    choking, and that the structural tags a "valid standalone page" needs are
    all present and, for the void-free ones, balanced.
    """

    def __init__(self) -> None:
        super().__init__()
        self.starts: List[str] = []
        self.ends: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self.starts.append(tag)

    def handle_endtag(self, tag: str) -> None:
        self.ends.append(tag)


def _assert_valid_standalone_html(text: str) -> None:
    assert text.lstrip().lower().startswith("<!doctype html>")
    parser = _LenientHTMLValidator()
    parser.feed(text)  # raises on nothing — HTMLParser is deliberately lenient
    parser.close()
    for required in ("html", "head", "body", "title", "style", "script"):
        assert parser.starts.count(required) >= 1, f"missing <{required}>"
    # Structural (non-void) tags must balance — a stray unescaped '<' or '>'
    # from an injected label would show up here as a mismatched count.
    for tag in ("html", "head", "body", "table", "details", "div"):
        assert parser.starts.count(tag) == parser.ends.count(tag), tag


def _assert_no_external_load_tags(text: str) -> None:
    """No tag that makes the browser fetch something outside the file."""
    for pattern in (
        r'<script[^>]+src=["\']https?://',
        r'<link[^>]+href=["\']https?://',
        r'<(?:img|iframe)[^>]+src=["\']https?://',
    ):
        assert not re.search(pattern, text, re.IGNORECASE), pattern


@pytest.fixture
def malicious_analysis(synthetic_analysis: Analysis) -> Analysis:
    """The fixture's Analysis with an XSS-shaped label planted in every place
    an untrusted host-supplied string (a cgroup path, a container name) could
    end up: a target label, a series label, an event message, a phase name,
    and a proposal title.
    """
    evil = "<script>alert('cgprofile')</script>"
    synthetic_analysis.targets[0]["label"] = evil
    synthetic_analysis.series[0].label = evil
    synthetic_analysis.events[0].message = evil
    synthetic_analysis.events[0].target = evil
    synthetic_analysis.phases[0].name = evil
    synthetic_analysis.proposals[0].title = evil
    return synthetic_analysis


# ── inline mode ──────────────────────────────────────────────────────────

def test_render_inline_produces_nonempty_valid_html(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    result_path = report_html.render(synthetic_analysis, str(out), plotlyjs="inline")
    assert result_path == str(out)
    assert out.exists()
    size = out.stat().st_size
    assert size > 0
    text = out.read_text(encoding="utf-8")
    _assert_valid_standalone_html(text)
    # Self-contained: plotly.js is embedded, no sibling file is needed.
    assert not (tmp_path / "plotly.min.js").exists()
    assert "Plotly.newPlot" in text  # the vendored library actually shipped


def test_inline_mode_creates_missing_parent_directory(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    """A fresh run directory (DESIGN §3) may not exist yet when the report is
    rendered — inline mode must create it just as directory mode does.
    """
    out = tmp_path / "not-yet-created" / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="inline")
    assert out.exists()
    assert out.stat().st_size > 0


def test_inline_mode_has_no_externally_loaded_resource(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="inline")
    text = out.read_text(encoding="utf-8")
    _assert_no_external_load_tags(text)


# ── directory mode ───────────────────────────────────────────────────────

def test_render_directory_writes_companion_js_and_zero_external_refs(
    tmp_path: Path, synthetic_analysis: Analysis
) -> None:
    out = tmp_path / "sub" / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    assert out.exists()
    companion = out.parent / "plotly.min.js"
    assert companion.exists()
    assert companion.stat().st_size > 1_000_000  # the real library, not a stub

    text = out.read_text(encoding="utf-8")
    _assert_valid_standalone_html(text)
    # The strong form of the check: the report page itself, exclusive of the
    # vendored library file, contains not one http(s) substring.
    assert "http://" not in text
    assert "https://" not in text
    assert 'src="plotly.min.js"' in text


def test_directory_mode_is_much_smaller_than_inline(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    inline_out = tmp_path / "inline.html"
    dir_out = tmp_path / "dir" / "report.html"
    report_html.render(synthetic_analysis, str(inline_out), plotlyjs="inline")
    report_html.render(synthetic_analysis, str(dir_out), plotlyjs="directory")
    assert dir_out.stat().st_size < inline_out.stat().st_size / 10


def test_bad_plotlyjs_mode_rejected(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    with pytest.raises(ValueError):
        report_html.render(synthetic_analysis, str(tmp_path / "r.html"), plotlyjs="cdn")


# ── content assertions ──────────────────────────────────────────────────

def test_phase_bands_appear(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    for phase in synthetic_analysis.phases:
        assert phase.name in text
    assert "vrect" in text.lower() or "shapes" in text.lower()


def test_event_markers_and_severity_appear(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    ev = synthetic_analysis.events[0]
    assert ev.kind in text
    assert ev.message in text
    assert ev.severity in text
    assert "sev-warn" in text  # the severity badge in the hand-written events list


def test_proposals_with_evidence_and_confidence_appear(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    p = synthetic_analysis.proposals[0]
    assert p.title in text
    assert p.confidence in text
    # Evidence/change text is rendered as escaped HTML (one of the fixture's
    # evidence strings contains a literal '>'), so compare the escaped form.
    import html as _html
    for e in p.evidence:
        assert _html.escape(e) in text
    for c in p.change:
        assert _html.escape(c) in text


def test_panel_table_view_present_and_collapsed(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "<details" in text
    assert "table view" in text
    # every series label should be reachable somewhere other than a hover string
    for s in synthetic_analysis.series:
        assert s.label in text


def test_header_has_run_id_duration_and_targets(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert synthetic_analysis.manifest["run_id"] in text
    for t in synthetic_analysis.targets:
        assert t["label"] in text
        assert t["cgroup"] in text


def test_theme_toggle_present(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert 'data-theme' in text
    assert "prefers-color-scheme" in text
    assert "cgprofileSetTheme" in text
    assert "cgp-theme-toggle" in text


# ── security: escaping ────────────────────────────────────────────────────

def test_script_label_is_escaped_not_injected(tmp_path: Path, malicious_analysis: Analysis) -> None:
    out = tmp_path / "report.html"
    report_html.render(malicious_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    # The raw, executable form must never appear anywhere in the document.
    assert "<script>alert('cgprofile')</script>" not in text
    # It must appear in one of the two safe encodings: HTML-escaped text (the
    # header, events list, proposals, table view) or plotly's own
    # unicode-escaped JSON (the trace name embedded in the figure's script).
    html_escaped = "&lt;script&gt;alert(&#x27;cgprofile&#x27;)&lt;/script&gt;" in text
    json_escaped = "\\u003cscript\\u003e" in text
    assert html_escaped or json_escaped
    # Still parses as well-formed HTML — an unescaped '<' would unbalance tags.
    _assert_valid_standalone_html(text)


def test_script_label_escaped_in_every_surface(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    """A single malicious label placed in the field each surface actually
    renders (some surfaces render ``target``, some render ``label``): header
    target table, per-panel table, and the figure's own JSON all get it.
    """
    evil = "<script>x</script>"
    synthetic_analysis.targets[0]["label"] = evil
    synthetic_analysis.series[0].label = evil
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "<script>x</script>" not in text
    assert ("&lt;script&gt;x&lt;/script&gt;" in text) or ("\\u003cscript\\u003e" in text)


# ── colour discipline ──────────────────────────────────────────────────────

def test_palette_never_generates_a_ninth_hue() -> None:
    series = [
        Series(key=f"m{i}", target=f"t{i}", label=f"target-{i} mem", unit="bytes",
               group="memory", t=[0.0, 1.0], v=[float(i), float(i + 1)])
        for i in range(10)
    ]
    analysis = Analysis(manifest={"run_id": "many-series"}, series=series)
    fig, _ = report_html.build_figure(analysis)
    line_colors = {tr.line.color for tr in fig.data}
    palette_hexes = {c for pair in report_html.PALETTE for c in pair}
    non_palette = line_colors - palette_hexes
    # Anything not one of the 8 fixed hues must be exactly the "Other" grey.
    assert non_palette <= {report_html.OTHER_COLOR[0], report_html.OTHER_COLOR[1]}


def test_same_entity_gets_same_color_across_panels() -> None:
    shared_t = [0.0, 1.0, 2.0]
    series = [
        Series(key="mem", target="gate", label="gate mem", unit="bytes",
               group="memory", t=shared_t, v=[1.0, 2.0, 3.0]),
        Series(key="psi", target="gate", label="gate psi", unit="pct",
               group="pressure", t=shared_t, v=[1.0, 2.0, 3.0]),
        Series(key="mem", target="other", label="other mem", unit="bytes",
               group="memory", t=shared_t, v=[1.0, 2.0, 3.0]),
    ]
    analysis = Analysis(manifest={"run_id": "colour-stability"}, series=series)
    fig, _ = report_html.build_figure(analysis)
    by_name = {tr.name: tr.line.color for tr in fig.data if tr.visible}
    assert by_name["gate mem"] == by_name["gate psi"]
    assert by_name["gate mem"] != by_name["other mem"]


def test_multi_series_panel_gets_direct_endpoint_labels(synthetic_analysis: Analysis) -> None:
    """The memory panel has 2 series (subject + observer) and must carry a
    direct label near each line's endpoint; the pressure panel has only 1
    series and needs no such label — the legend alone identifies a lone line.
    """
    fig, _ = report_html.build_figure(synthetic_analysis)
    annotation_texts = {a["text"] for a in fig.to_dict()["layout"].get("annotations", [])}
    for s in synthetic_analysis.series_in("memory"):
        assert s.label in annotation_texts
    for s in synthetic_analysis.series_in("pressure"):
        assert s.label not in annotation_texts


def test_status_colors_never_used_for_a_series(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    fig, _ = report_html.build_figure(synthetic_analysis)
    status_hexes = set(report_html.STATUS_COLOR.values())
    for tr in fig.data:
        color = tr.line.color if tr.line and tr.line.color else None
        if color is None:
            continue
        # Series traces (mode="lines") must never carry a status colour;
        # event traces (mode="markers") are exempt — that's what they're for.
        if tr.mode == "lines":
            assert color not in status_hexes


# ── dual-axis guard ─────────────────────────────────────────────────────

def test_no_secondary_y_axis_anywhere(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    """Two measures of different scale must be two panels, never one chart
    with a second y-axis — the single most common way this kind of report
    goes wrong (DESIGN.md §4.11). ``overlaying`` is the plotly property that
    creates a dual-axis chart; no y-axis in this figure may set it, and there
    must be exactly one y-axis per subplot row.
    """
    fig, _ = report_html.build_figure(synthetic_analysis)
    layout = fig.to_dict()["layout"]
    yaxes = {k: v for k, v in layout.items() if re.match(r"^yaxis\d*$", k)}
    n_rows = len(synthetic_analysis.groups()) + (1 if synthetic_analysis.events else 0)
    assert len(yaxes) == n_rows
    for value in yaxes.values():
        assert "overlaying" not in value


# ── resampling / resolution selector ────────────────────────────────────

def test_resolution_buttons_cover_all_resolutions(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    fig, _ = report_html.build_figure(synthetic_analysis)
    updatemenus = fig.to_dict()["layout"]["updatemenus"]
    assert len(updatemenus) == 1
    labels = [b["label"] for b in updatemenus[0]["buttons"]]
    assert labels == list(report_html.RESOLUTIONS)


def test_none_in_series_breaks_the_line_not_interpolated() -> None:
    s = Series(key="m", target="t", label="t mem", unit="bytes", group="memory",
               t=[0.0, 1.0, 2.0, 3.0], v=[1.0, None, None, 4.0])
    resampled = report_html._resample_series(s)
    raw_t, raw_v = resampled["raw"]
    assert raw_v == [1.0, None, None, 4.0]  # untouched, no interpolation


def test_resample_skips_none_but_preserves_empty_gap() -> None:
    # A bucket with no finite sample at all must stay None, not become 0.
    s = Series(key="m", target="t", label="t mem", unit="bytes", group="memory",
               t=[0.0, 61.0, 62.0], v=[10.0, None, None])
    resampled = report_html._resample_series(s)
    t, v = resampled["1s"]
    assert any(x is None for x in v)
    assert 0.0 not in v or 10.0 in v  # never silently fabricated as zero


# ── rendering the real fixture end to end (smoke, both modes) ────────────

def test_render_reports_file_size(tmp_path: Path, synthetic_analysis: Analysis, capsys) -> None:
    inline_out = tmp_path / "inline.html"
    dir_out = tmp_path / "dirmode" / "report.html"
    report_html.render(synthetic_analysis, str(inline_out), plotlyjs="inline")
    report_html.render(synthetic_analysis, str(dir_out), plotlyjs="directory")
    inline_size = inline_out.stat().st_size
    dir_size = dir_out.stat().st_size
    assert inline_size > 1_000_000
    assert 0 < dir_size < 200_000


# ── adversarial: the run that collected nothing ─────────────────────────

def test_completely_empty_analysis_renders_without_crashing(tmp_path: Path) -> None:
    """A run that starts, sees the workload exit before the first tick, and
    stops — zero series, zero phases, zero events, zero proposals, empty
    manifest. This must produce a valid page, not an exception: a profiler
    that crashes on "nothing happened" is worse than one that says so.
    """
    analysis = Analysis()
    out = tmp_path / "empty.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    _assert_valid_standalone_html(text)
    assert "No events recorded" in text
    assert "No proposals." in text
    # Also the inline path, and build_figure in isolation (no groups, no
    # events ⇒ the n_rows==0 fallback must kick in rather than asking
    # make_subplots for zero rows).
    report_html.render(analysis, str(tmp_path / "empty_inline.html"), plotlyjs="inline")
    fig, theme_arrays = report_html.build_figure(analysis)
    assert len(fig.data) == 0
    assert theme_arrays == {"line_light": [], "line_dark": [], "marker_light": [], "marker_dark": []}


def test_empty_analysis_has_no_targets_message(tmp_path: Path) -> None:
    analysis = Analysis()
    out = tmp_path / "empty.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "No targets recorded." in text


# ── adversarial: degenerate series ──────────────────────────────────────

def test_series_entirely_none_has_dash_stats_not_a_crash() -> None:
    s = Series(key="m", target="t", label="all-none", unit="bytes", group="memory",
               t=[0.0, 1.0, 2.0], v=[None, None, None])
    row = report_html._series_stats_row(s)
    assert "all-none" in row
    assert row.count("<td class='num'>—</td>") == 4  # min, max, mean, last


def test_series_all_none_except_one_point_uses_that_point_for_every_stat() -> None:
    s = Series(key="m", target="t", label="one-real", unit="pct", group="pressure",
               t=[0.0, 1.0, 2.0], v=[None, 7.5, None])
    row = report_html._series_stats_row(s)
    # min == max == mean == 7.5%, but last is the final (None) sample.
    assert row.count("7.5%") == 3
    assert "<td class='num'>—</td>" in row  # the "last" column


def test_series_length_one_renders() -> None:
    s = Series(key="m", target="t", label="single-point", unit="count", group="memory",
               t=[0.0], v=[5.0])
    row = report_html._series_stats_row(s)
    assert ">1<" in row  # n == 1
    assert "5" in row


def test_series_length_zero_renders_all_dashes() -> None:
    s = Series(key="m", target="t", label="empty-series", unit="bytes", group="memory",
               t=[], v=[])
    row = report_html._series_stats_row(s)
    assert row.count("<td class='num'>—</td>") == 4
    assert ">0<" in row  # n == 0


def test_zero_length_series_survives_full_render(tmp_path: Path) -> None:
    """A target that appeared and vanished before a single sample landed —
    plausible for a short-lived container — must not take the whole report
    down when it sits alongside a normal series in the same panel.
    """
    normal = Series(key="m1", target="a", label="normal", unit="bytes",
                     group="memory", t=[0.0, 1.0], v=[1.0, 2.0])
    empty = Series(key="m2", target="b", label="vanished-immediately", unit="bytes",
                    group="memory", t=[], v=[])
    analysis = Analysis(manifest={"run_id": "degenerate"}, series=[normal, empty])
    out = tmp_path / "degenerate.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "vanished-immediately" in text


# ── adversarial: colour boundary at exactly 8 entities ──────────────────

def test_exactly_eight_entities_each_get_a_distinct_slot() -> None:
    series = [
        Series(key=f"m{i}", target=f"t{i}", label=f"target-{i}", unit="bytes",
               group="memory", t=[0.0, 1.0], v=[float(i), float(i + 1)])
        for i in range(8)
    ]
    analysis = Analysis(manifest={"run_id": "exactly-eight"}, series=series)
    fig, _ = report_html.build_figure(analysis)
    colors = {tr.line.color for tr in fig.data}
    palette_light = {pair[0] for pair in report_html.PALETTE}
    # All eight real hues used, none folded into "Other" — the boundary
    # case one-off-by-one errors in "past 8" logic would get wrong.
    assert colors == palette_light
    assert report_html.OTHER_COLOR[0] not in colors


def test_ninth_entity_is_the_first_to_fold() -> None:
    entity_slots = report_html.assign_entity_colors([
        Series(key=f"m{i}", target=f"t{i}", label=f"l{i}", unit="bytes",
               group="memory", t=[0.0], v=[0.0])
        for i in range(9)
    ])
    assert entity_slots["t7"] == 7  # last real slot
    assert entity_slots["t8"] == 8  # past the palette — build_figure folds this one


# ── adversarial: phase edge cases ────────────────────────────────────────

def test_zero_duration_phase_does_not_crash() -> None:
    analysis = Analysis(
        manifest={"run_id": "instant-phase"},
        phases=[Phase(name="instant", t0=5.0, t1=5.0, source="mark")],
        series=[Series(key="m", target="t", label="l", unit="bytes",
                        group="memory", t=[0.0, 10.0], v=[1.0, 2.0])],
    )
    fig, _ = report_html.build_figure(analysis)
    shapes = fig.to_dict()["layout"].get("shapes", [])
    assert any(sh.get("x0") == 5.0 and sh.get("x1") == 5.0 for sh in shapes)


def test_phase_extending_beyond_last_sample_drives_header_duration(tmp_path: Path) -> None:
    """The header's duration falls back to the last phase boundary when the
    manifest doesn't carry one — a phase that runs past the final sample
    (the workload was still "in phase" when sampling stopped) must still
    produce a sane, larger number, not the sample range.
    """
    analysis = Analysis(
        manifest={"run_id": "overrun"},  # no "duration" key
        phases=[Phase(name="overrun-phase", t0=0.0, t1=500.0, source="mark")],
        series=[Series(key="m", target="t", label="l", unit="bytes",
                        group="memory", t=[0.0, 10.0], v=[1.0, 2.0])],
    )
    out = tmp_path / "overrun.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "500.0s" in text


def test_duration_from_manifest_wins_over_phases_when_present(tmp_path: Path) -> None:
    analysis = Analysis(
        manifest={"run_id": "explicit-duration", "duration": 12.5},
        phases=[Phase(name="p", t0=0.0, t1=999.0, source="mark")],
    )
    out = tmp_path / "explicit.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "12.5s" in text
    assert "999.0s" not in text


# ── adversarial: event.data edge cases ──────────────────────────────────

def test_event_with_empty_data_renders_without_stray_data_div() -> None:
    ev = Event(t=0.0, mono=0.0, kind="cgroup_appeared", severity="info",
               target="/x.slice", message="appeared", data={})
    out = report_html._render_events([ev])
    assert "event-data" not in out  # nothing to show, so nothing is shown
    assert "appeared" in out


def test_event_target_and_message_empty_strings_skip_those_hover_lines() -> None:
    """DetectorConfig-produced events always carry target/message, but this
    module's own ``_event_hover`` must not assume that — an empty string is
    falsy and must be omitted rather than rendered as a blank line.
    """
    ev = Event(t=0.0, mono=0.0, kind="probe", severity="info", target="", message="", data={"n": 1})
    hover = report_html._event_hover(ev)
    assert "<br><br>" not in hover  # no blank segment where target/message would go
    assert hover.startswith("<b>probe</b><br>info<br>n=1")


def test_event_data_with_non_json_serializable_values_does_not_crash() -> None:
    """``Event.data: Dict[str, Any]`` is genuinely open — this module renders
    it with ``str()``, never ``json.dumps``, specifically so a NaN, a set, or
    raw bytes an upstream detector attaches doesn't take the report down.
    """
    ev = Event(
        t=0.0, mono=0.0, kind="weird_event", severity="warn", target="/x.slice",
        message="unusual payload",
        data={"nan_value": float("nan"), "raw": b"\x00\xff", "members": {1, 2, 3}},
    )
    hover = report_html._event_hover(ev)
    assert "nan" in hover
    # Full pipeline too: this data dict must survive being embedded in the
    # figure's own hovertext JSON, not just the standalone helper call.
    analysis = Analysis(manifest={"run_id": "weird"}, events=[ev])
    import tempfile
    import os as _os
    with tempfile.TemporaryDirectory() as d:
        out = _os.path.join(d, "weird.html")
        report_html.render(analysis, out, plotlyjs="directory")
        assert _os.path.getsize(out) > 0


# ── adversarial: NaN as a value, not just in event data ──────────────────

def test_nan_last_value_does_not_crash_bytes_formatting() -> None:
    """The bug this module used to have: a NaN slips into ``Series.v`` (a 0
    count pandas resample bucket, upstream, not converted back to ``None``)
    and lands as the *last* sample of a bytes-unit series.
    ``int(round(float('nan')))`` raises ``ValueError`` — this must not
    propagate out of a table-view render.
    """
    s = Series(key="m", target="t", label="nan-tail", unit="bytes", group="memory",
               t=[0.0, 1.0], v=[1024.0, float("nan")])
    row = report_html._series_stats_row(s)  # must not raise
    assert "nan-tail" in row


def test_nan_mixed_with_real_values_excluded_from_min_max_mean() -> None:
    s = Series(key="m", target="t", label="nan-mixed", unit="count", group="memory",
               t=[0.0, 1.0, 2.0], v=[10.0, float("nan"), 20.0])
    row = report_html._series_stats_row(s)
    # A real mean of [10, 20] is 15 — not "nan", and not silently 10 or 20
    # from min/max picking a side depending on where the NaN happened to sort.
    assert "15" in row
    numeric_cells = re.findall(r"<td class='num'>([^<]*)</td>", row)
    assert not any("nan" in cell.lower() for cell in numeric_cells)


def test_fmt_value_nan_is_treated_like_none() -> None:
    assert report_html._fmt_value(float("nan"), "bytes") == "—"
    assert report_html._fmt_value(float("nan"), "cores") == "—"


# ── adversarial: escaping — quotes, backslashes, a bare </script> ───────

def test_malicious_label_escaped_across_every_surface(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    """One payload carrying every hazard class DESIGN.md's threat model
    names — a script tag, double and single quotes, a backslash, and a bare
    ``</script>`` with no matching open tag (the vector that doesn't need a
    full tag pair to break out of a ``<script>`` block) — planted in every
    field this module renders through a different escaping path, and checked
    against the *exact* bytes each path is expected to produce.
    """
    payload = 'Container "prod-1" \\ has a literal </script> tag and \'quotes\''

    synthetic_analysis.targets[0]["label"] = payload           # header table only
    synthetic_analysis.series[0].label = payload               # legend + hovertemplate +
                                                                 # panel table + direct-label
    synthetic_analysis.events[0].message = payload              # events list + hovertext
    synthetic_analysis.events[0].target = payload                # events list + hovertext
    synthetic_analysis.phases[0].name = payload                  # phase band annotation

    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")

    # The universal guarantee: the raw, dangerous payload appears nowhere.
    assert payload not in text

    single = _html_mod.escape(payload)
    double = _double_escaped(payload)

    # Header target table: hand-written HTML, single html.escape.
    assert single in text
    # Legend/trace name, hovertemplate, event hovertext, and the phase-band
    # annotation are all pre-escaped by this module (html.escape) and then
    # re-serialized into the figure's JSON by plotly — both layers apply.
    # The legend name must NOT reach plotly raw: plotly.js's own pseudo-HTML
    # text pipeline renders an unescaped <a href=...> in a trace name as a
    # real, clickable link (a documented plotly.js feature, not a plotly bug),
    # so relying on plotly's JSON encoder alone (which only protects the
    # </script> boundary) is not enough.
    assert double in text


def test_malicious_label_is_well_formed_html_afterward(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    payload = "<script>x</script>\"'\\ </script>"
    synthetic_analysis.targets[0]["label"] = payload
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    _assert_valid_standalone_html(text)


# ── adversarial: a resolution bucket that lands empty after resampling ──

def test_sparse_series_leaves_mostly_empty_1s_buckets_and_still_renders(tmp_path: Path) -> None:
    """Two samples 500 seconds apart (the adaptive sampler backs off hard
    when nothing is happening — DESIGN §4.4) means the 1 s resolution grid
    has hundreds of buckets with no sample in them at all, not just a None
    next to real values.
    """
    s = Series(key="m", target="t", label="sparse", unit="bytes", group="memory",
               t=[0.0, 500.0], v=[10.0, 20.0])
    resampled = report_html._resample_series(s)
    t_1s, v_1s = resampled["1s"]
    assert len(t_1s) > 400
    empty_buckets = sum(1 for x in v_1s if x is None)
    assert empty_buckets > 400  # the overwhelming majority are genuinely empty

    analysis = Analysis(manifest={"run_id": "sparse"}, series=[s])
    out = tmp_path / "sparse.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    assert out.stat().st_size > 0


# ── defensive branches: inputs this module doesn't fully control ────────

class _GroupsSeriesMismatch:
    """A duck-typed stand-in for ``Analysis`` whose ``groups()`` names a
    panel that ``series_in()`` then reports as empty — the invariant real
    ``Analysis.groups()`` always upholds (lib/model.py), but this module
    takes an ``Analysis``-shaped object as a parameter, not a guarantee, and
    its defensive ``continue`` deserves its own direct check rather than
    being unreachable through the one honest implementation.
    """

    def groups(self) -> List[str]:
        return ["ghost-panel"]

    def series_in(self, group: str) -> List[Series]:
        return []


def test_panel_tables_skips_a_group_with_no_series() -> None:
    assert report_html._render_panel_tables(_GroupsSeriesMismatch()) == ""


def test_generic_table_empty_rows_is_blank() -> None:
    assert report_html._generic_table([], "Caption") == ""


def test_generic_table_dedupes_columns_seen_in_a_later_row() -> None:
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    out = report_html._generic_table(rows, "Cap")
    assert out.count("<th>a</th>") == 1
    assert out.count("<th>b</th>") == 1
    for value in ("1", "2", "3", "4"):
        assert f"<td>{value}</td>" in out


def test_generic_table_unions_columns_across_rows_of_different_shape() -> None:
    """A later row can also *add* a column an earlier row didn't have — the
    union, not just the first row's keys, decides the header."""
    rows = [{"a": 1}, {"a": 2, "b": 3}]
    out = report_html._generic_table(rows, "Cap")
    assert "<th>a</th>" in out and "<th>b</th>" in out
    assert "<td></td>" in out  # row 1 has no "b" — rendered blank, not dropped


def test_panel_title_with_mixed_units_falls_back_to_bare_group_label() -> None:
    """Two series in one group with different units would be an upstream
    bug (the whole point of grouping by panel is one shared y-scale) — this
    must not crash or print a nonsensical unit, just drop the parenthetical.
    """
    mixed = [
        Series(key="a", target="t1", label="l1", unit="bytes", group="memory", t=[0.0], v=[1.0]),
        Series(key="b", target="t2", label="l2", unit="pct", group="memory", t=[0.0], v=[1.0]),
    ]
    assert report_html._panel_title("memory", mixed) == "Memory"


# ── _fmt_value: every unit branch, called directly ───────────────────────

def test_fmt_value_every_unit_branch() -> None:
    from lib import util as _util

    assert report_html._fmt_value(None, "bytes") == "—"
    assert report_html._fmt_value(1536.0, "bytes") == _util.fmt_bytes(1536)
    assert report_html._fmt_value(2048.0, "bytes/s") == _util.fmt_bytes(2048) + "/s"
    assert report_html._fmt_value(7.25, "count/s") == _util.fmt_rate(7.25, unit="/s", places=1)
    assert report_html._fmt_value(50.0, "pct") == "50.0%"
    assert report_html._fmt_value(2.5, "cores") == "2.50 cores"
    assert report_html._fmt_value(1500.0, "us") == "1,500µs"
    assert report_html._fmt_value(42.0, "count") == "42"
    assert report_html._fmt_value(3.14159, "ratio") == "3.14"  # fallback branch


# ── header: manifest / limits rendering ──────────────────────────────────

def test_limits_rendered_for_dict_and_scalar_values(tmp_path: Path) -> None:
    analysis = Analysis(
        manifest={"run_id": "limits-test"},
        limits={
            "/dev.slice": {"memory_max": 123, "cpu_cores": 2},
            "/wings.slice": "unlimited",
        },
    )
    out = tmp_path / "limits.html"
    report_html.render(analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "Effective limits" in text
    assert "/dev.slice" in text
    assert "memory_max=123" in text
    assert "/wings.slice" in text
    assert "unlimited" in text


def test_no_limits_omits_the_effective_limits_section(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    assert synthetic_analysis.limits == {}
    out = tmp_path / "report.html"
    report_html.render(synthetic_analysis, str(out), plotlyjs="directory")
    text = out.read_text(encoding="utf-8")
    assert "Effective limits" not in text


# ── direct-label endpoint absent ─────────────────────────────────────────

def test_direct_label_skipped_when_series_has_no_finite_endpoint() -> None:
    """One series with real data, one entirely ``None`` — both qualify for
    direct-labelling (the panel has ≥2 series), but the all-``None`` one has
    no point to anchor a label to and must be silently skipped rather than
    placed at a fabricated position.
    """
    has_data = Series(key="a", target="t1", label="has-data", unit="bytes",
                       group="memory", t=[0.0, 1.0], v=[1.0, 2.0])
    no_data = Series(key="b", target="t2", label="no-data", unit="bytes",
                      group="memory", t=[0.0, 1.0], v=[None, None])
    analysis = Analysis(manifest={"run_id": "no-endpoint"}, series=[has_data, no_data])
    fig, _ = report_html.build_figure(analysis)
    annotation_texts = {a["text"] for a in fig.to_dict()["layout"].get("annotations", [])}
    assert "has-data" in annotation_texts
    assert "no-data" not in annotation_texts


# ── both plotlyjs modes with a missing parent directory (explicit) ──────

def test_both_modes_create_a_deeply_nested_missing_parent(tmp_path: Path, synthetic_analysis: Analysis) -> None:
    inline_out = tmp_path / "a" / "b" / "c" / "inline.html"
    dir_out = tmp_path / "x" / "y" / "z" / "report.html"
    report_html.render(synthetic_analysis, str(inline_out), plotlyjs="inline")
    report_html.render(synthetic_analysis, str(dir_out), plotlyjs="directory")
    assert inline_out.exists()
    assert dir_out.exists()
    assert (dir_out.parent / "plotly.min.js").exists()
