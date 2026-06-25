"""Tests for damon_cli.py pure helper functions."""
import argparse
import importlib
import os
import sys
import pytest

# Load damon_cli without executing main()
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)
import damon_cli


class TestParseDuration:
    def test_plain_integer(self):
        assert damon_cli.parse_duration('60') == 60

    def test_seconds_suffix(self):
        assert damon_cli.parse_duration('90s') == 90

    def test_minutes_suffix(self):
        assert damon_cli.parse_duration('2m') == 120

    def test_hours_suffix(self):
        assert damon_cli.parse_duration('1h') == 3600

    def test_fractional_minutes(self):
        assert damon_cli.parse_duration('1.5m') == 90

    def test_case_insensitive(self):
        assert damon_cli.parse_duration('5M') == 300

    def test_invalid_raises(self):
        with pytest.raises(argparse.ArgumentTypeError):
            damon_cli.parse_duration('notatime')


class TestParseSize:
    def test_bytes(self):
        assert damon_cli.parse_size('1024') == 1024

    def test_kilobytes(self):
        assert damon_cli.parse_size('1K') == 1024

    def test_megabytes(self):
        assert damon_cli.parse_size('128M') == 128 * 1024 ** 2

    def test_gigabytes(self):
        assert damon_cli.parse_size('2G') == 2 * 1024 ** 3

    def test_mib_suffix(self):
        assert damon_cli.parse_size('512MiB') == 512 * 1024 ** 2

    def test_fractional(self):
        assert damon_cli.parse_size('1.5G') == int(1.5 * 1024 ** 3)

    def test_invalid_raises(self):
        with pytest.raises(argparse.ArgumentTypeError):
            damon_cli.parse_size('notasize')


class TestHumanBytes:
    def test_bytes(self):
        assert damon_cli.human_bytes(512) == '512 B'

    def test_kib(self):
        assert damon_cli.human_bytes(1024) == '1.0 KiB'

    def test_gib(self):
        result = damon_cli.human_bytes(1024 ** 3)
        assert '1.0 GiB' == result


class TestParserBuilt:
    def test_parser_builds_without_error(self):
        parser = damon_cli.build_parser()
        assert parser is not None

    def test_diagnose_subcommand_exists(self):
        parser = damon_cli.build_parser()
        args = parser.parse_args(['diagnose'])
        assert args.command == 'diagnose'

    def test_classify_requires_pid(self):
        parser = damon_cli.build_parser()
        args = parser.parse_args(['classify', '1234'])
        assert args.pid == 1234

    def test_timeseries_pid_subcommand(self):
        parser = damon_cli.build_parser()
        args = parser.parse_args(['timeseries-pid', '9999',
                                  '--duration', '300',
                                  '--interval', '15'])
        assert args.pid == 9999
        assert args.duration == 300.0
        assert args.interval == 15.0

    def test_timeseries_container_subcommand(self):
        parser = damon_cli.build_parser()
        args = parser.parse_args(['timeseries-container', 'my-container',
                                  '--duration', '900'])
        assert args.container == 'my-container'
        assert args.duration == 900.0

    def test_auto_reclaim_defaults_to_status(self):
        parser = damon_cli.build_parser()
        args = parser.parse_args(['auto-reclaim'])
        assert args.action == 'status'

    def test_profile_pid_with_options(self):
        parser = damon_cli.build_parser()
        args = parser.parse_args(['profile-pid', '42', '--duration', '120',
                                  '--output', 'json', '--sample-us', '500000'])
        assert args.pid == 42
        assert args.duration == 120.0
        assert args.output == 'json'
        assert args.sample_us == 500000
