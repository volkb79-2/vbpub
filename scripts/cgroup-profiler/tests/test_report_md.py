"""Tests for lib/report_md.py."""

from __future__ import annotations

import csv
from pathlib import Path

from lib import report_md
from lib.model import Analysis, Event, Proposal, Series


class TestRender:
    def test_writes_report_and_returns_its_path(self, tmp_path, synthetic_analysis):
        out = report_md.render(synthetic_analysis, str(tmp_path))
        assert out == str(tmp_path / "report.md")
        assert Path(out).exists()

    def test_writes_a_png_and_a_csv_per_populated_panel(self, tmp_path, synthetic_analysis):
        report_md.render(synthetic_analysis, str(tmp_path))
        groups = {s.group for s in synthetic_analysis.series}
        for group in groups:
            assert (tmp_path / "charts" / f"{group}.png").exists()
            assert (tmp_path / "charts" / f"{group}.csv").exists()

    def test_no_panel_for_an_empty_group(self, tmp_path, synthetic_analysis):
        report_md.render(synthetic_analysis, str(tmp_path))
        # "damon" is a known panel (lib.model.PANELS) with no series in the fixture.
        assert not (tmp_path / "charts" / "damon.png").exists()

    def test_every_chart_has_a_markdown_table(self, tmp_path, synthetic_analysis):
        report_md.render(synthetic_analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        for group in {s.group for s in synthetic_analysis.series}:
            heading = f"## {group.capitalize()}"
            assert heading in text
            section = text.split(heading, 1)[1].split("## ", 1)[0]
            assert "| target | unit | n | mean | min | max |" in section
            assert f"charts/{group}.csv" in section

    def test_csv_has_a_row_per_point_and_empty_string_for_none(self, tmp_path):
        analysis = Analysis(
            manifest={"run_id": "r1"},
            targets=[{"key": "gate", "cgroup": "/g", "role": "subject", "label": "gate"}],
            series=[Series(key="mem.current", target="gate", label="gate memory.current",
                            unit="bytes", group="memory", t=[0.0, 1.0, 2.0], v=[10.0, None, 30.0])],
        )
        report_md.render(analysis, str(tmp_path))
        rows = list(csv.DictReader((tmp_path / "charts" / "memory.csv").open()))
        assert [r["value"] for r in rows] == ["10.0", "", "30.0"]

    def test_phases_and_events_tables_present(self, tmp_path, synthetic_analysis):
        report_md.render(synthetic_analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "## Phases" in text
        assert "startup" in text and "job-a" in text and "idle" in text
        assert "## Events" in text
        assert "memory_high_breach" in text

    def test_correlations_table_present_with_n(self, tmp_path, synthetic_analysis):
        report_md.render(synthetic_analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "## Correlations" in text
        assert "| r | n | note |" in text
        assert "0.91" in text

    def test_low_n_correlation_gets_a_caveat_note(self, tmp_path):
        analysis = Analysis(
            manifest={"run_id": "r1"},
            correlations=[{"observer": "obs", "subject": "subj", "metric": "x~y", "r": 0.95, "n": 5}],
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "too few samples to call this a finding" in text

    def test_high_n_correlation_has_no_caveat_note_on_its_row(self, tmp_path):
        analysis = Analysis(
            manifest={"run_id": "r1"},
            correlations=[{"observer": "obs", "subject": "subj", "metric": "x~y", "r": 0.91, "n": 39}],
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        row = [ln for ln in text.splitlines() if "0.91" in ln][0]
        assert "too few samples" not in row

    def test_proposals_section_shows_confidence_evidence_and_change(self, tmp_path, synthetic_analysis):
        report_md.render(synthetic_analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "## Proposals" in text
        assert "confidence: observed" in text
        assert "**Evidence:**" in text
        assert "**Change:**" in text
        assert "systemctl set-property dev.slice MemoryMax=10G" in text

    def test_no_proposals_note_when_list_empty(self, tmp_path):
        analysis = Analysis(manifest={"run_id": "r1"})
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "No proposals" in text

    def test_untrusted_label_is_escaped_not_left_to_break_the_table(self, tmp_path):
        analysis = Analysis(
            manifest={"run_id": "r1"},
            targets=[{"key": "gate", "cgroup": "/g", "role": "subject",
                      "label": "evil | injected"}],
            events=[Event(t=0.0, mono=0.0, kind="k", severity="info",
                           target="weird|target", message="a | b\nc")],
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "evil \\| injected" in text
        assert "weird\\|target" in text
        assert "a \\| b c" in text  # newline collapsed too, so no stray row break


class TestColorAssignment:
    def test_stable_first_seen_order(self):
        colors = report_md._assign_colors(["a", "b", "c"])
        assert colors["a"] == report_md._PALETTE[0]
        assert colors["b"] == report_md._PALETTE[1]
        assert colors["c"] == report_md._PALETTE[2]

    def test_repeat_entity_keeps_its_slot(self):
        colors = report_md._assign_colors(["a", "b", "a"])
        assert len(colors) == 2

    def test_never_cycles_past_eight_slots(self):
        entities = [f"e{i}" for i in range(12)]
        colors = report_md._assign_colors(entities)
        used = set(colors.values())
        # at most the 8 categorical slots plus the single muted overflow colour
        assert used <= set(report_md._PALETTE) | {report_md._CHROME["muted"]}
        ninth_and_on = {colors[e] for e in entities[8:]}
        assert ninth_and_on == {report_md._CHROME["muted"]}

    def test_color_survives_a_series_being_filtered_out_of_a_panel(self, tmp_path):
        """Two targets share a memory-panel entry; only one has a pressure
        series. Its colour in the pressure panel must match its colour in
        the memory panel — filtering series must not repaint survivors."""
        common_t = [0.0, 1.0]
        analysis = Analysis(
            manifest={"run_id": "r1"},
            targets=[
                {"key": "gate", "cgroup": "/g", "role": "subject", "label": "gate"},
                {"key": "obs", "cgroup": "/o", "role": "observer", "label": "obs"},
            ],
            series=[
                Series(key="mem.current", target="gate", label="gate mem", unit="bytes",
                       group="memory", t=common_t, v=[1.0, 2.0]),
                Series(key="mem.current", target="obs", label="obs mem", unit="bytes",
                       group="memory", t=common_t, v=[1.0, 2.0]),
                Series(key="psi_mem.some_avg10", target="gate", label="gate psi", unit="pct",
                       group="pressure", t=common_t, v=[0.1, 0.2]),
            ],
        )
        report_md.render(analysis, str(tmp_path))
        entity_order = [t["key"] for t in analysis.targets]
        colors = report_md._assign_colors(entity_order)
        assert colors["gate"] == report_md._PALETTE[0]
        assert colors["obs"] == report_md._PALETTE[1]


class TestFmtValue:
    """Every unit this package charts must format sanely — a wrong branch
    here is a wrong number in the one place `report_md` disagrees with a
    reader's eyes: the table under the chart."""

    def test_bytes(self):
        assert report_md._fmt_value(2 * 1024**3, "bytes") == "2.0G"

    def test_bytes_per_second(self):
        assert report_md._fmt_value(1024.0, "bytes/s") == "1.0K/s"

    def test_pct(self):
        assert report_md._fmt_value(8.256, "pct") == "8.26%"

    def test_cores(self):
        assert report_md._fmt_value(0.4, "cores") == "0.40 cores"

    def test_count_per_second(self):
        assert report_md._fmt_value(12.5, "count/s") == "12.50 count/s"

    def test_unknown_unit_falls_back_to_plain_number(self):
        assert report_md._fmt_value(3.14159, "ratio") == "3.14"


class TestSeriesTable:
    def test_row_with_no_finite_values_shows_dashes(self):
        s = Series(key="k", target="gate", label="gate", unit="bytes",
                    group="memory", t=[0.0, 1.0], v=[None, None])
        table = report_md._series_table([s])
        assert "| gate | bytes | 0 | - | - | - |" in table


class TestRunHeaderDuration:
    def test_computes_duration_from_started_and_ended_when_both_present(self, tmp_path):
        analysis = Analysis(manifest={"run_id": "r1", "started": 100.0, "ended": 142.5})
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "**duration:** 42.5s" in text

    def test_duration_unknown_when_neither_field_present(self, tmp_path):
        analysis = Analysis(manifest={"run_id": "r1"})
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "**duration:** unknown" in text


class TestLimitsTable:
    def test_renders_resolved_entries(self, tmp_path):
        analysis = Analysis(
            manifest={"run_id": "r1"},
            limits={
                "/wings.slice/wings-prod.slice": {
                    "resolved": {
                        "strict_min": 5 * 1024**3, "recursive_min": 5 * 1024**3,
                        "strict_low": 0, "recursive_low": 6 * 1024**3,
                        "memory_high": 6 * 1024**3, "memory_max": 20 * 1024**3,
                        "protection_mode": "strict",
                    },
                    "described": ["memory.min: 5.0G"],
                    "fingerprint": "abc123",
                },
            },
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "## Effective limits" in text
        assert "/wings.slice/wings-prod.slice" in text
        assert "strict" in text

    def test_entries_without_a_resolved_dict_are_skipped(self, tmp_path):
        """The older bare `describe()` list shape (or any entry missing
        "resolved") must not crash the table and must not appear in it."""
        analysis = Analysis(
            manifest={"run_id": "r1"},
            limits={"/dev.slice/dev-background.slice": ["memory.max: 8.0G"]},
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "No limit snapshot available" in text

    def test_empty_limits_shows_the_no_snapshot_note(self, tmp_path):
        analysis = Analysis(manifest={"run_id": "r1"}, limits={})
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "No limit snapshot available" in text


class TestProposalsSectionEdgeCases:
    def test_proposal_with_no_evidence_or_change_still_renders(self, tmp_path):
        analysis = Analysis(
            manifest={"run_id": "r1"},
            proposals=[Proposal(id="p1", severity="info", title="a bare proposal",
                                 rationale="just because", evidence=[], change=[],
                                 confidence="speculative")],
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "a bare proposal" in text
        assert "**Evidence:**" not in text
        assert "**Change:**" not in text


class TestEntityOrderAndGroupHandling:
    def test_series_target_not_in_declared_targets_still_gets_a_colour(self, tmp_path):
        """A folded "other" series (or any series whose target is not one of
        `analysis.targets`) must still be included in colour assignment,
        not silently dropped from the entity order."""
        analysis = Analysis(
            manifest={"run_id": "r1"},
            targets=[{"key": "gate", "cgroup": "/g", "role": "subject", "label": "gate"}],
            series=[
                Series(key="mem.current", target="gate", label="gate", unit="bytes",
                       group="memory", t=[0.0], v=[1.0]),
                Series(key="mem.current", target="other", label="other (3 cgroups)", unit="bytes",
                       group="memory", t=[0.0], v=[2.0]),
            ],
        )
        report_md.render(analysis, str(tmp_path))
        text = (tmp_path / "report.md").read_text()
        assert "| other | bytes |" in text

    def test_render_tolerates_a_claimed_group_with_no_backing_series(self, tmp_path, synthetic_analysis):
        """`Analysis.groups()` only ever names a group with at least one
        series (see model.py), but `render` guards against a group list that
        says otherwise anyway — proven here by handing it exactly that."""
        synthetic_analysis.groups = lambda: ["memory", "ghost-panel"]
        report_md.render(synthetic_analysis, str(tmp_path))
        assert not (Path(tmp_path) / "charts" / "ghost-panel.png").exists()
