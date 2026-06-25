"""Tests for get_process_info and get_container_pids."""
import os
import sys
import pytest
from damon_analysis import get_process_info


class TestGetProcessInfo:
    def test_pid1_has_comm(self):
        info = get_process_info(1)
        assert info['comm'] != ''
        assert info['pid'] == 1

    def test_pid1_has_nonzero_rss(self):
        info = get_process_info(1)
        assert info['vm_rss_kb'] > 0

    def test_pid1_has_nonzero_vmsize(self):
        info = get_process_info(1)
        assert info['vm_size_kb'] > 0

    def test_vm_size_gte_rss(self):
        info = get_process_info(1)
        assert info['vm_size_kb'] >= info['vm_rss_kb']

    def test_self_pid(self):
        info = get_process_info(os.getpid())
        assert info['pid'] == os.getpid()
        assert info['comm'] != ''

    def test_nonexistent_pid_returns_empty_comm(self):
        info = get_process_info(99999999)
        assert info['comm'] == ''
        assert info['vm_rss_kb'] == 0
        assert info['vm_size_kb'] == 0

    def test_returns_dict_with_expected_keys(self):
        info = get_process_info(1)
        assert set(info.keys()) >= {'pid', 'comm', 'vm_rss_kb', 'vm_size_kb'}

    def test_comm_is_string(self):
        info = get_process_info(1)
        assert isinstance(info['comm'], str)

    def test_zero_pid_graceful(self):
        # PID 0 doesn't have a /proc entry; should not crash
        info = get_process_info(0)
        assert isinstance(info, dict)
