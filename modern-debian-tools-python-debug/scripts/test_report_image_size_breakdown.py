"""Focused regression tests for report-image-size-breakdown.py.

Covers the deterministic, docker-free parts: size-string parsing, the
heredoc-aware docker-history line folding, layer classification ordering
(the exact bug class that motivated this suite), and tree aggregation. Never
reaches a real docker daemon.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent / "report-image-size-breakdown.py"
_spec = importlib.util.spec_from_file_location("report_image_size_breakdown", MODULE_PATH)
rb = importlib.util.module_from_spec(_spec)
sys.modules["report_image_size_breakdown"] = rb
_spec.loader.exec_module(rb)


class TestParseDockerSize:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("0B", 0),
            ("994MB", 994_000_000),
            ("1.29GB", 1_290_000_000),
            ("28.6MB", 28_600_000),
            ("635B", 635),
        ],
    )
    def test_known_units(self, text: str, expected: int) -> None:
        assert rb.parse_docker_size(text) == expected

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            rb.parse_docker_size("if [ -n \"${BASH_VERSION:-}\" ]")


class TestDockerHistoryHeredocFolding:
    def test_multiline_created_by_folds_into_previous_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mirrors the real dstdns-venv.sh heredoc RUN, which embeds literal
        # newlines in CreatedBy and previously crashed the parser entirely.
        raw = (
            "164B\tset -euo pipefail;         cat > /etc/profile.d/dstdns-venv.sh << 'EOF'\n"
            "if [ -n \"${BASH_VERSION:-}\" ] && [[ $- == *i* ]]; then\n"
            "    source /home/vscode/.venv/bin/activate\n"
            "fi\n"
            "EOF\n"
            "0B\tUSER root\n"
        )
        monkeypatch.setattr(rb, "run", lambda argv, **kw: raw)
        layers = rb.docker_history("fake:image")
        assert len(layers) == 2
        size, created_by = layers[0]
        assert size == 164
        assert "dstdns-venv.sh" in created_by
        assert "source /home/vscode/.venv/bin/activate" in created_by
        assert layers[1] == (0, "USER root")


class TestClassifyLayerOrdering:
    def test_modern_tools_wins_over_incidental_php_text(self) -> None:
        # The big "modern tools" RUN declares a php_system_packages=(...)
        # array literal containing "php${PHP_VERSION}-cli" even though it
        # isn't the PHP install RUN — this regression test is exactly the
        # bug this script shipped with (PHP absorbing 2.6 GB that belonged
        # to a completely different layer).
        created_by = (
            'STAGE_DIR="/tmp/tool-artifacts-staging"; '
            'php_system_packages=(); if [ "${INSTALL_PHP}" = "true" ]; then '
            'php_system_packages=( "composer" "php${PHP_VERSION}-cli" ); fi;'
        )
        assert rb.classify_layer(created_by) == ("mdt layers", '"Modern tools" mega-layer')

    def test_real_php_run_still_classifies_as_php(self) -> None:
        created_by = (
            'curl -fsSL https://packages.sury.org/php/apt.gpg | gpg --dearmor '
            '-o /etc/apt/keyrings/sury-php.gpg; apt-get install -y "php${PHP_VERSION}-cli"'
        )
        assert rb.classify_layer(created_by) == ("mdt layers", "PHP 8.5 runtime")

    def test_unrecognized_layer_returns_none(self) -> None:
        assert rb.classify_layer("RUN echo hello world") is None


class TestNodeAggregation:
    def test_total_sums_own_bytes_and_children(self) -> None:
        root = rb.Node("root")
        rb.insert(root, ("Base OS & build toolchain",), 365)
        rb.insert(root, ("Base OS & build toolchain", "toolchain residual"), 720)
        group = root.children["Base OS & build toolchain"]
        assert group.own_bytes == 365
        assert group.total() == 365 + 720
        assert root.total() == 365 + 720

    def test_leaf_with_no_children_returns_own_bytes(self) -> None:
        root = rb.Node("root")
        rb.insert(root, ("skopeo",), 28_600_000)
        assert root.children["skopeo"].total() == 28_600_000


class TestFmtBytes:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (500, "500 B"),
            (48_000_000, "48.0 MB"),
            (994_000_000, "994.0 MB"),
            (1_290_000_000, "1.3 GB"),
        ],
    )
    def test_human_readable(self, value: int, expected: str) -> None:
        assert rb.fmt_bytes(value) == expected


class TestProbeAccounting:
    def test_python_probe_excludes_later_site_packages(self) -> None:
        leaf = next(leaf for leaf in rb.LEAVES if leaf.key == "base_python_build")
        command = rb.du_leaf_line(leaf)
        assert "--exclude='*/site-packages'" in command

    def test_negative_unattributed_total_is_clamped_and_fails(self, monkeypatch, capsys) -> None:
        layer = rb.Node("root")
        rb.insert(layer, ("over-attributed",), 11)
        monkeypatch.setattr(rb, "git_last_commit", lambda path: None)
        monkeypatch.setattr(rb, "measure", lambda image, threshold: (layer, layer, 10, 0))

        assert rb.main(["--image", "fake:image", "--threshold", "100"]) == 1
        output = capsys.readouterr().out
        assert "UNATTRIBUTED:" in output
        assert "0 B" in output
        assert "ERROR: attribution exceeds image by 1 B" in output


class TestGitLastCommit:
    def test_returns_none_on_git_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        def boom(*args, **kwargs):
            raise subprocess.CalledProcessError(1, "git")

        monkeypatch.setattr(rb.subprocess, "run", boom)
        assert rb.git_last_commit("Dockerfile") is None
