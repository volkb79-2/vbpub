"""Tests for SysfsInterface and Monitor.

Pure-availability and read-only tests run without root.
Write/start/collect tests require root + DAMON sysfs and are skipped otherwise.
"""
import os
import sys
import time
import pytest
from conftest import requires_root, requires_root_and_damon, DAMON_SYSFS
from damon_analysis import SysfsInterface, Monitor, Classifier


# ---------------------------------------------------------------------------
# SysfsInterface — static checks (no root needed on a DAMON-capable kernel)
# ---------------------------------------------------------------------------

class TestSysfsAvailability:
    def test_is_available_returns_bool(self):
        result = SysfsInterface.is_available()
        assert isinstance(result, bool)

    def test_is_available_true_on_this_system(self):
        # This host has CONFIG_DAMON_SYSFS=y
        assert SysfsInterface.is_available() is True

    def test_kdamonds_dir_exists(self):
        assert os.path.isdir(DAMON_SYSFS)


class TestSysfsReadOnly:
    @requires_root_and_damon
    def test_read_nr_kdamonds(self):
        val = SysfsInterface._read(os.path.join(DAMON_SYSFS, 'nr_kdamonds'))
        assert val.isdigit()

    @requires_root_and_damon
    def test_read_int_nr_kdamonds(self):
        val = SysfsInterface._read_int(os.path.join(DAMON_SYSFS, 'nr_kdamonds'))
        assert isinstance(val, int)
        assert val >= 0


# ---------------------------------------------------------------------------
# disable_damon_stat — root needed for the write but readable without
# ---------------------------------------------------------------------------

class TestDisableDamonStat:
    def test_returns_true_when_module_absent(self, monkeypatch):
        # disable_damon_stat() checks os.path.isfile first; simulate missing module
        monkeypatch.setattr('damon_analysis.os.path.isfile', lambda p: False)
        result = SysfsInterface.disable_damon_stat()
        assert result is True

    @requires_root
    def test_idempotent_when_already_disabled(self):
        path = '/sys/module/damon_stat/parameters/enabled'
        if not os.path.isfile(path):
            pytest.skip("damon_stat module not loaded")
        # Disable, then disable again — should not raise
        SysfsInterface.disable_damon_stat()
        SysfsInterface.disable_damon_stat()
        # Restore
        SysfsInterface._write(path, 'Y')


# ---------------------------------------------------------------------------
# SysfsInterface read_tried_regions — live test after damo start
# ---------------------------------------------------------------------------

@requires_root_and_damon
class TestLiveSysfsCollect:
    """Full live test: start damo on PID 1, collect tried_regions, stop."""

    DAMO_BIN = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'venv', 'bin', 'damo')

    def _damo_available(self):
        return os.path.isfile(self.DAMO_BIN) or bool(
            __import__('shutil').which('damo'))

    def test_read_tried_regions_returns_list(self):
        if not self._damo_available():
            pytest.skip("damo not installed")

        monitor = Monitor(damo_bin=self.DAMO_BIN)
        monitor.configure_vaddr(pid=1, sample_us=100_000, aggr_us=2_000_000,
                                min_regions=10, max_regions=200)
        try:
            monitor.start()
            time.sleep(3)
            regions = monitor.collect()
            assert isinstance(regions, list)
        finally:
            monitor.stop()

    def test_regions_have_required_keys(self):
        if not self._damo_available():
            pytest.skip("damo not installed")

        monitor = Monitor(damo_bin=self.DAMO_BIN)
        monitor.configure_vaddr(pid=1, sample_us=100_000, aggr_us=2_000_000,
                                min_regions=10, max_regions=200)
        try:
            monitor.start()
            time.sleep(4)
            regions = monitor.collect()
            if regions:
                r = regions[0]
                assert 'start' in r
                assert 'end' in r
                assert 'nr_accesses' in r
                assert 'age' in r
                assert r['end'] > r['start']
        finally:
            monitor.stop()

    def test_total_tried_bytes_nonzero(self):
        if not self._damo_available():
            pytest.skip("damo not installed")

        monitor = Monitor(damo_bin=self.DAMO_BIN)
        monitor.configure_vaddr(pid=1, sample_us=100_000, aggr_us=2_000_000)
        try:
            monitor.start()
            time.sleep(4)
            monitor.collect()
            total = SysfsInterface.read_total_tried_bytes(
                monitor._kdidx, monitor._ctxidx, monitor._scheme_idx)
            assert total > 0
        finally:
            monitor.stop()

    def test_is_running_true_after_start(self):
        if not self._damo_available():
            pytest.skip("damo not installed")

        monitor = Monitor(damo_bin=self.DAMO_BIN)
        monitor.configure_vaddr(pid=1)
        try:
            monitor.start()
            assert monitor.is_running() is True
        finally:
            monitor.stop()

    def test_is_running_false_after_stop(self):
        if not self._damo_available():
            pytest.skip("damo not installed")

        monitor = Monitor(damo_bin=self.DAMO_BIN)
        monitor.configure_vaddr(pid=1)
        monitor.start()
        monitor.stop()
        assert monitor.is_running() is False

    def test_classify_after_collect(self):
        if not self._damo_available():
            pytest.skip("damo not installed")

        monitor = Monitor(damo_bin=self.DAMO_BIN)
        monitor.configure_vaddr(pid=1, sample_us=100_000, aggr_us=2_000_000)
        classifier = Classifier()
        try:
            monitor.start()
            time.sleep(4)
            regions = monitor.collect()
            classified = classifier.classify_regions(
                regions, monitor.sample_us, monitor.aggr_us)
            assert isinstance(classified, list)
            for r in classified:
                assert r['class'] in ('hot', 'warm', 'cold', 'idle')
        finally:
            monitor.stop()
