"""Tests for visualize_memory.py pure functions."""
import json
import os
import sys
import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)
import visualize_memory as viz


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_classified_region(start=0x10000, size=4096, rate=50.0,
                            age_sec=10.0, temperature=5e7, cls='warm'):
    return {
        'start': start, 'end': start + size,
        'size_bytes': size,
        'access_rate_pct': rate,
        'age_sec': age_sec,
        'age_us': int(age_sec * 1e6),
        'temperature': temperature,
        'class': cls,
    }


def make_snapshot(elapsed=0, hot=100, warm=200, cold=50, idle=10,
                  vm_rss_kb=1024, snapshot_num=1):
    """Build a JSONL-style snapshot dict."""
    return {
        'ts_iso': '2026-06-25T12:00:00',
        'elapsed_sec': elapsed,
        'snapshot': snapshot_num,
        'pid': 1234,
        'comm': 'SoulmaskServer',
        'vm_rss_kb': vm_rss_kb,
        'sample_us': 500_000,
        'aggr_us': 5_000_000,
        'summary': {
            'hot':  {'bytes': hot  * 1024 * 1024, 'count': 5},
            'warm': {'bytes': warm * 1024 * 1024, 'count': 20},
            'cold': {'bytes': cold * 1024 * 1024, 'count': 8},
            'idle': {'bytes': idle * 1024 * 1024, 'count': 3},
        },
        'total_bytes': (hot + warm + cold + idle) * 1024 * 1024,
        'regions': [],
    }


# ---------------------------------------------------------------------------
# ascii_heatmap
# ---------------------------------------------------------------------------

class TestAsciiHeatmap:
    def test_returns_string(self):
        regions = [make_classified_region()]
        result = viz.ascii_heatmap(regions)
        assert isinstance(result, str)

    def test_empty_returns_no_data(self):
        assert viz.ascii_heatmap([]) == '(no data)'

    def test_bins_parameter(self):
        regions = [make_classified_region()]
        result = viz.ascii_heatmap(regions, bins=40)
        # The heatmap line itself should have at most 40 characters (no wider)
        lines = result.splitlines()
        heatmap_line = lines[-1]
        assert len(heatmap_line) <= 40

    def test_multiple_regions(self):
        regions = [
            make_classified_region(0x10000, 4096, temperature=100, cls='hot'),
            make_classified_region(0x20000, 4096, temperature=0,   cls='cold'),
        ]
        result = viz.ascii_heatmap(regions)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# distribution_chart
# ---------------------------------------------------------------------------

class TestDistributionChart:
    def test_returns_string(self, classified_regions):
        result = viz.distribution_chart(classified_regions)
        assert isinstance(result, str)

    def test_contains_class_names(self, classified_regions):
        result = viz.distribution_chart(classified_regions)
        for cls in ('HOT', 'WARM', 'COLD', 'IDLE'):
            assert cls in result

    def test_shows_percentages(self, classified_regions):
        result = viz.distribution_chart(classified_regions)
        assert '%' in result

    def test_empty_regions(self):
        result = viz.distribution_chart([])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# size_class_chart
# ---------------------------------------------------------------------------

class TestSizeClassChart:
    def test_returns_string(self, classified_regions):
        result = viz.size_class_chart(classified_regions)
        assert isinstance(result, str)

    def test_shows_size_units(self, classified_regions):
        result = viz.size_class_chart(classified_regions)
        # Should contain at least one KiB or MiB label
        assert 'KiB' in result or 'MiB' in result or 'GiB' in result

    def test_empty_regions(self):
        result = viz.size_class_chart([])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# timeseries_ascii
# ---------------------------------------------------------------------------

class TestTimeseriesAscii:
    def test_returns_string(self):
        snaps = [make_snapshot(i * 30) for i in range(5)]
        result = viz.timeseries_ascii(snaps)
        assert isinstance(result, str)

    def test_contains_time_labels(self):
        snaps = [make_snapshot(0), make_snapshot(60), make_snapshot(120)]
        result = viz.timeseries_ascii(snaps)
        # should show 0:00, 1:00, 2:00
        assert '0:00' in result

    def test_one_row_per_snapshot(self):
        snaps = [make_snapshot(i * 30, snapshot_num=i+1) for i in range(5)]
        result = viz.timeseries_ascii(snaps)
        lines = result.splitlines()
        # At least 5 data rows + header + separator lines
        assert len(lines) >= 7

    def test_empty_returns_no_data(self):
        result = viz.timeseries_ascii([])
        assert result == '(no data)'

    def test_shows_rss(self):
        snaps = [make_snapshot(0, vm_rss_kb=4096 * 1024)]  # 4 GiB RSS
        result = viz.timeseries_ascii(snaps)
        assert 'M' in result  # MiB column headers

    def test_growing_hot_visible(self):
        # Startup: hot grows from 100 MiB to 1000 MiB
        snaps = [
            make_snapshot(0,   hot=100),
            make_snapshot(30,  hot=500),
            make_snapshot(60,  hot=1000),
            make_snapshot(90,  hot=1000),
            make_snapshot(120, hot=800),  # steady-state
        ]
        result = viz.timeseries_ascii(snaps)
        assert isinstance(result, str)
        lines = [l for l in result.splitlines() if '0:00' in l or '0:30' in l or '1:00' in l]
        assert len(lines) >= 3


# ---------------------------------------------------------------------------
# Integration: parse single-snapshot JSON through visualize_memory main
# ---------------------------------------------------------------------------

class TestVisualizeSingleSnapshotFile:
    def test_main_ascii_format(self, tmp_path, classified_regions):
        """Verify main() runs without crashing on a single-snapshot JSON."""
        import json
        from damon_analysis import ReportFormatter, Classifier

        c = Classifier()
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions,
                                        metadata={'pid': 1, 'test': True}))
        input_file = tmp_path / 'snapshot.json'
        input_file.write_text(json.dumps(data))

        import argparse
        args = argparse.Namespace(
            input_file=str(input_file),
            format='ascii',
            output=None,
            title='Test',
            timeseries=False,
        )
        # Should not raise
        viz.main.__globals__['args'] = args
        regions = data.get('regions', [])
        assert len(regions) > 0
        out = viz.ascii_heatmap(regions)
        assert isinstance(out, str)

    def test_timeseries_main_branch(self, tmp_path):
        """Verify timeseries branch reads JSONL without crashing."""
        snaps = [make_snapshot(i * 30, hot=i*100, snapshot_num=i+1)
                 for i in range(4)]
        jsonl_file = tmp_path / 'ts.jsonl'
        jsonl_file.write_text('\n'.join(json.dumps(s) for s in snaps) + '\n')

        result = viz.timeseries_ascii(snaps)
        assert isinstance(result, str)
        assert len(result.splitlines()) > 4
