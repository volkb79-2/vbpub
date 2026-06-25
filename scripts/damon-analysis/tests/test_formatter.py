"""Tests for ReportFormatter — pure output logic, no kernel needed."""
import json
import pytest
from damon_analysis import ReportFormatter, Classifier


class TestFmtBytes:
    def test_bytes(self):
        assert ReportFormatter._fmt_bytes(512) == '512 B'

    def test_kib(self):
        assert '1.0 KiB' == ReportFormatter._fmt_bytes(1024)

    def test_mib(self):
        assert '1.0 MiB' == ReportFormatter._fmt_bytes(1024 ** 2)

    def test_gib(self):
        assert '1.0 GiB' == ReportFormatter._fmt_bytes(1024 ** 3)

    def test_fractional_mib(self):
        result = ReportFormatter._fmt_bytes(int(1.5 * 1024 ** 2))
        assert '1.5 MiB' == result

    def test_zero(self):
        assert '0 B' == ReportFormatter._fmt_bytes(0)


class TestFmtAge:
    def test_microseconds(self):
        result = ReportFormatter._fmt_age(500)
        assert 'µs' in result

    def test_milliseconds(self):
        result = ReportFormatter._fmt_age(5_000)
        assert 'ms' in result

    def test_seconds(self):
        result = ReportFormatter._fmt_age(3_000_000)
        assert 's' in result

    def test_minutes(self):
        result = ReportFormatter._fmt_age(120_000_000)
        assert 'm' in result

    def test_hours(self):
        result = ReportFormatter._fmt_age(4_000_000_000)
        assert 'h' in result


class TestFmtTemp:
    def test_small(self):
        result = ReportFormatter._fmt_temp(42.0)
        assert '42' in result

    def test_kilo(self):
        result = ReportFormatter._fmt_temp(5000.0)
        assert 'K' in result

    def test_mega(self):
        result = ReportFormatter._fmt_temp(3_000_000.0)
        assert 'M' in result

    def test_negative(self):
        result = ReportFormatter._fmt_temp(-1_000_000.0)
        assert 'M' in result
        assert '-' in result


class TestHumanReadable:
    def test_output_is_string(self, classified_regions):
        f = ReportFormatter()
        out = f.human_readable(classified_regions, title='Test')
        assert isinstance(out, str)

    def test_contains_class_labels(self, classified_regions):
        f = ReportFormatter()
        out = f.human_readable(classified_regions)
        for cls in ('HOT', 'WARM', 'COLD', 'IDLE'):
            assert cls in out

    def test_custom_title(self, classified_regions):
        f = ReportFormatter()
        out = f.human_readable(classified_regions, title='My Custom Title')
        assert 'My Custom Title' in out

    def test_empty_regions(self):
        f = ReportFormatter()
        out = f.human_readable([])
        assert isinstance(out, str)
        assert 'TOTAL' in out


class TestJsonReport:
    def test_valid_json(self, classified_regions):
        f = ReportFormatter()
        out = f.json_report(classified_regions, metadata={'pid': 42})
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_contains_summary(self, classified_regions):
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions))
        assert 'summary' in data
        for cls in ('hot', 'warm', 'cold', 'idle'):
            assert cls in data['summary']

    def test_metadata_included(self, classified_regions):
        f = ReportFormatter()
        meta = {'pid': 1234, 'comm': 'soulmask'}
        data = json.loads(f.json_report(classified_regions, metadata=meta))
        assert data['metadata']['pid'] == 1234
        assert data['metadata']['comm'] == 'soulmask'

    def test_regions_list(self, classified_regions):
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions))
        assert isinstance(data['regions'], list)
        assert len(data['regions']) == len(classified_regions)

    def test_region_keys(self, classified_regions):
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions))
        r = data['regions'][0]
        for key in ('start', 'end', 'size_bytes', 'access_rate_pct',
                    'age_sec', 'temperature', 'class'):
            assert key in r, f"missing key: {key}"

    def test_total_bytes(self, classified_regions):
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions))
        expected = sum(r['size_bytes'] for r in classified_regions)
        assert data['total_bytes'] == expected

    def test_summary_percentages_sum_to_100(self, classified_regions):
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions))
        total_pct = sum(data['summary'][c]['percent']
                        for c in ('hot', 'warm', 'cold', 'idle'))
        assert abs(total_pct - 100.0) < 0.5

    def test_no_metadata(self, classified_regions):
        f = ReportFormatter()
        data = json.loads(f.json_report(classified_regions))
        assert 'metadata' not in data


class TestCsvReport:
    def test_has_header(self, classified_regions):
        f = ReportFormatter()
        out = f.csv_report(classified_regions)
        first_line = out.splitlines()[0]
        assert 'start' in first_line
        assert 'class' in first_line

    def test_row_count(self, classified_regions):
        f = ReportFormatter()
        out = f.csv_report(classified_regions)
        lines = out.splitlines()
        assert len(lines) == len(classified_regions) + 1  # +1 for header

    def test_parseable_rows(self, classified_regions):
        import csv, io
        f = ReportFormatter()
        reader = csv.DictReader(io.StringIO(f.csv_report(classified_regions)))
        rows = list(reader)
        assert len(rows) == len(classified_regions)
        for row in rows:
            assert row['class'] in ('hot', 'warm', 'cold', 'idle')
            float(row['access_rate_pct'])
            int(row['start'])
