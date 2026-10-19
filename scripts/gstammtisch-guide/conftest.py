"""pytest configuration: registers the ``os_integration`` marker (MDTC-02 T7).

A test that executes a real low-level OS binary is marked ``os_integration`` and deselected in the
gate containers (``-m "not os_integration"`` in the lane argv; the minimal ``tester-base`` carries no
such binaries). This project has no QEMU VM lane yet, so nothing selects the marker: see
KNOWN_ISSUES_TODO_BACKLOG.md GG-1.
"""
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "os_integration: executes a real low-level OS binary; deselected in gate containers",
    )
