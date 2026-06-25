"""Tests for analyze_process.py helpers."""
import os
import sys
import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)
import analyze_process as ap


class TestGetPid:
    def test_numeric_string_returns_int(self):
        assert ap.get_pid('1') == 1

    def test_own_pid(self):
        pid = ap.get_pid(str(os.getpid()))
        assert pid == os.getpid()

    def test_command_launches_process(self):
        # Running 'sleep 1' should return a valid PID
        pid = ap.get_pid('sleep 1')
        assert isinstance(pid, int)
        assert pid > 0
        # Clean up
        try:
            import os as _os
            _os.kill(pid, 9)
        except ProcessLookupError:
            pass


class TestParseArgs:
    def test_default_duration(self):
        args = ap.parse_args.__wrapped__(['12345']) if hasattr(ap.parse_args, '__wrapped__') \
               else None
        # Use argparse directly
        import argparse
        sys.argv = ['analyze_process.py', '12345']
        args = ap.parse_args()
        assert args.duration == 60.0

    def test_duration_flag(self):
        sys.argv = ['analyze_process.py', '12345', '--duration', '120']
        args = ap.parse_args()
        assert args.duration == 120.0

    def test_output_default(self):
        sys.argv = ['analyze_process.py', '12345']
        args = ap.parse_args()
        assert args.output == 'text'

    def test_continuous_flag(self):
        sys.argv = ['analyze_process.py', '12345', '--continuous']
        args = ap.parse_args()
        assert args.continuous is True

    def test_sample_and_aggr_us(self):
        sys.argv = ['analyze_process.py', '12345',
                    '--sample-us', '500000', '--aggr-us', '5000000']
        args = ap.parse_args()
        assert args.sample_us == 500_000
        assert args.aggr_us == 5_000_000
