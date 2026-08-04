"""Tests for the file readers and unit helpers.

The theme running through these: a profiler samples files that appear and
vanish underneath it, on counters that can reset. The distinction between
"absent", "unlimited" and "zero" is load-bearing, and so is refusing to invent
a rate across a reset.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import util


class TestReaders:
    def test_missing_file_is_none_not_zero(self, tmp_path: Path):
        missing = str(tmp_path / "nope")
        assert util.read_text(missing) is None
        assert util.read_int(missing) is None
        assert util.read_lines(missing) is None
        assert util.read_kv(missing) == {}
        assert util.read_flat_keyed(missing) == {}
        assert util.read_pressure(missing) == {}

    def test_max_reads_back_as_none(self, tmp_path: Path):
        path = tmp_path / "memory.max"
        path.write_text("max\n")
        assert util.read_int(str(path)) is None

    def test_zero_is_zero_not_none(self, tmp_path: Path):
        path = tmp_path / "memory.min"
        path.write_text("0\n")
        assert util.read_int(str(path)) == 0

    def test_read_kv_skips_non_integer_without_losing_the_rest(self, tmp_path: Path):
        path = tmp_path / "memory.stat"
        path.write_text("anon 100\nweird notanumber\nfile 200\n")
        assert util.read_kv(str(path)) == {"anon": 100, "file": 200}

    def test_flat_keyed_drops_max_fields(self, tmp_path: Path):
        path = tmp_path / "io.max"
        path.write_text("254:0 rbps=1000 wbps=max riops=50\n")
        assert util.read_flat_keyed(str(path)) == {"254:0": {"rbps": 1000, "riops": 50}}

    def test_pressure_parses_both_kinds(self, tmp_path: Path):
        path = tmp_path / "memory.pressure"
        path.write_text(
            "some avg10=1.50 avg60=2.00 avg300=3.00 total=1234\n"
            "full avg10=0.50 avg60=0.60 avg300=0.70 total=567\n"
        )
        parsed = util.read_pressure(str(path))
        assert parsed["some_avg10"] == 1.5
        assert parsed["full_total"] == 567.0

    def test_meminfo_converts_kb_to_bytes(self, tmp_path: Path):
        path = tmp_path / "meminfo"
        path.write_text("MemTotal:       16374620 kB\nHugePagesize:   2048 kB\nHugePages_Total:  0\n")
        parsed = util.read_proc_meminfo(str(path))
        assert parsed["MemTotal"] == 16374620 * 1024
        assert parsed["HugePages_Total"] == 0

    def test_meminfo_missing_file_is_empty(self, tmp_path: Path):
        assert util.read_proc_meminfo(str(tmp_path / "nope")) == {}

    def test_meminfo_skips_a_key_with_nothing_after_the_colon(self, tmp_path: Path):
        path = tmp_path / "meminfo"
        path.write_text("Weird:\nMemTotal:       1024 kB\n")
        assert util.read_proc_meminfo(str(path)) == {"MemTotal": 1024 * 1024}

    def test_meminfo_skips_a_non_numeric_value(self, tmp_path: Path):
        path = tmp_path / "meminfo"
        path.write_text("MemTotal:       notanumber kB\nMemFree:        512 kB\n")
        assert util.read_proc_meminfo(str(path)) == {"MemFree": 512 * 1024}

    def test_read_int_rejects_garbage_text(self, tmp_path: Path):
        path = tmp_path / "memory.current"
        path.write_text("not-a-number\n")
        assert util.read_int(str(path)) is None

    def test_read_kv_skips_a_line_with_only_one_token(self, tmp_path: Path):
        path = tmp_path / "memory.stat"
        path.write_text("lonely\nanon 100\n")
        assert util.read_kv(str(path)) == {"anon": 100}

    def test_flat_keyed_skips_a_whitespace_only_line(self, tmp_path: Path):
        path = tmp_path / "io.max"
        path.write_text("254:0 rbps=1000\n   \n8:0 rbps=2000\n")
        assert util.read_flat_keyed(str(path)) == {
            "254:0": {"rbps": 1000}, "8:0": {"rbps": 2000},
        }

    def test_flat_keyed_skips_a_field_without_equals(self, tmp_path: Path):
        path = tmp_path / "io.max"
        path.write_text("254:0 rbps=1000 garbage riops=50\n")
        assert util.read_flat_keyed(str(path)) == {"254:0": {"rbps": 1000, "riops": 50}}

    def test_flat_keyed_skips_a_non_integer_value(self, tmp_path: Path):
        path = tmp_path / "io.max"
        path.write_text("254:0 rbps=notanumber wbps=500\n")
        assert util.read_flat_keyed(str(path)) == {"254:0": {"wbps": 500}}

    def test_pressure_skips_a_whitespace_only_line(self, tmp_path: Path):
        path = tmp_path / "memory.pressure"
        path.write_text("some avg10=1.00 total=1\n   \nfull avg10=0.50 total=2\n")
        parsed = util.read_pressure(str(path))
        assert parsed["some_avg10"] == 1.0
        assert parsed["full_avg10"] == 0.5

    def test_pressure_skips_a_non_numeric_value(self, tmp_path: Path):
        path = tmp_path / "memory.pressure"
        path.write_text("some avg10=notanumber total=1\n")
        # Only the unparseable field is dropped; its neighbour on the same
        # line still comes through.
        assert util.read_pressure(str(path)) == {"some_total": 1.0}


class TestRate:
    def test_normal_rate(self):
        assert util.rate(100, 200, 2.0) == 50.0

    def test_counter_reset_is_unknown_not_negative(self):
        # A cgroup recreated mid-run resets its counters. Recording -1e9/s
        # would put a spike in the chart that never happened.
        assert util.rate(500, 10, 1.0) is None

    def test_missing_sample_is_unknown(self):
        assert util.rate(None, 10, 1.0) is None
        assert util.rate(10, None, 1.0) is None

    def test_nonpositive_dt_is_unknown(self):
        assert util.rate(10, 20, 0.0) is None
        assert util.rate(10, 20, -1.0) is None


class TestSizes:
    @pytest.mark.parametrize(
        "text,expected",
        [("2G", 2 * 1024**3), ("512M", 512 * 1024**2), ("1.5GiB", int(1.5 * 1024**3)),
         ("1024", 1024), ("max", None), ("", None), ("garbage", None)],
    )
    def test_parse_size_binary_default(self, text, expected):
        # systemd's convention, which is what host-setup.env means by "1G".
        assert util.parse_size(text) == expected

    def test_parse_size_decimal_when_asked(self):
        assert util.parse_size("1G", binary=False) == 1000**3

    def test_parse_size_none_is_none(self):
        # A cgroup file that read back as an absent value upstream (rather
        # than the literal text "max") must not raise trying to .strip() it.
        assert util.parse_size(None) is None

    def test_parse_size_unrecognized_suffix_is_none(self):
        assert util.parse_size("5xyz") is None

    def test_fmt_bytes_says_max_for_none(self):
        assert util.fmt_bytes(None) == "max"

    def test_fmt_bytes_binary_units(self):
        assert util.fmt_bytes(2 * 1024**3) == "2.0G"
        assert util.fmt_bytes(-1536) == "-1.5K"

    def test_fmt_bytes_plain_bytes_below_a_kib(self):
        assert util.fmt_bytes(500) == "500B"
        assert util.fmt_bytes(-500) == "-500B"

    def test_fmt_rate_says_dash_for_none(self):
        assert util.fmt_rate(None) == "-"

    def test_fmt_rate_formats_a_value(self):
        assert util.fmt_rate(12.345, unit=" MB/s", places=1) == "12.3 MB/s"


class TestChainHelpers:
    def test_ancestors_is_leaf_first_and_includes_root(self):
        assert util.ancestors("/dev.slice/dev-background.slice/docker-x.scope") == [
            "/dev.slice/dev-background.slice/docker-x.scope",
            "/dev.slice/dev-background.slice",
            "/dev.slice",
            "/",
        ]

    def test_ancestors_of_root(self):
        assert util.ancestors("/") == ["/"]

    def test_tightest_ignores_unlimited(self):
        assert util.tightest([None, 500, None, 200]) == 200
        assert util.tightest([None, None]) is None
        assert util.tightest([]) is None

    def test_loosest_is_dominated_by_unlimited(self):
        assert util.loosest([100, None]) is None
        assert util.loosest([100, 200]) == 200

    def test_relpath_of(self):
        assert util.relpath_of("/a/b/c", "/a") == "/b/c"
        assert util.relpath_of("/a", "/a") == "/"
        assert util.relpath_of("/a/b", "/") == "/a/b"
        assert util.relpath_of("/x/y", "/a") == "/x/y"


class TestLabels:
    def test_docker_scope_is_abbreviated(self):
        # A 64-hex scope name destroys any legend it lands in.
        label = util.short_label("/dev.slice/dev-background.slice/docker-" + "a" * 64 + ".scope")
        assert label == "dev-background.slice/docker-" + "a" * 12 + "…"

    def test_plain_path_keeps_last_two(self):
        assert util.short_label("/wings.slice/wings-prod.slice") == "wings.slice/wings-prod.slice"

    def test_root(self):
        assert util.short_label("/") == "/"


class TestCpuMax:
    def test_unlimited(self):
        assert util.parse_cpu_max("max 100000") == (None, 100000)

    def test_quota(self):
        assert util.parse_cpu_max("600000 100000") == (600000, 100000)

    def test_missing(self):
        assert util.parse_cpu_max(None) == (None, None)

    def test_empty_string_is_missing(self):
        assert util.parse_cpu_max("") == (None, None)

    def test_whitespace_only_splits_to_nothing(self):
        # Not caught by `if not text` (a single space is truthy), so the
        # `if not parts` guard after .split() is the one that must catch it.
        assert util.parse_cpu_max("   ") == (None, None)

    def test_non_numeric_quota_and_period_fall_back_to_none(self):
        assert util.parse_cpu_max("abc 100000") == (None, 100000)
        assert util.parse_cpu_max("100000 abc") == (100000, None)


class TestClamp:
    def test_within_range_is_unchanged(self):
        assert util.clamp(5.0, 0.0, 10.0) == 5.0

    def test_below_low_is_clamped_up(self):
        assert util.clamp(-5.0, 0.0, 10.0) == 0.0

    def test_above_high_is_clamped_down(self):
        assert util.clamp(50.0, 0.0, 10.0) == 10.0


class TestDevFromMajmin:
    def test_malformed_input_is_returned_unchanged(self):
        assert util.dev_from_majmin("not-a-majmin") == "not-a-majmin"
        assert util.dev_from_majmin("1:2:3") == "1:2:3"

    def test_resolves_through_the_symlink(self, monkeypatch):
        # dev_from_majmin hardcodes /sys/dev/block, so the real filesystem
        # call is monkeypatched rather than depending on this host's devices.
        monkeypatch.setattr(
            util.os.path, "realpath",
            lambda link: "/sys/devices/virtual/block/sda" if link == "/sys/dev/block/254:0" else link,
        )
        assert util.dev_from_majmin("254:0") == "sda"

    def test_missing_device_link_returns_input_unchanged(self, monkeypatch):
        def raise_oserror(link):
            raise OSError("no such device")

        monkeypatch.setattr(util.os.path, "realpath", raise_oserror)
        assert util.dev_from_majmin("254:0") == "254:0"
