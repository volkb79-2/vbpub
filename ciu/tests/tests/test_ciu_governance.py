"""Tests for src/ciu/governance.py — CIU v2 stack-wide resource governance.

Normative contract: docs/SPEC.md §S15.

Pure-logic unit tests for the module's building blocks (config resolution,
baseline-path search order, read_iops derivation, device autodetection/
resolution, per-service injection with author-override precedence, and the
S15.9 `ciu iops-baseline` measurement: fio JSON parsing incl. the prepended
note-line bug, engine selection, fio-absent/freshness/cleanup paths — no
real fio is ever executed here). Integration through
``composefile.generate_overlay`` (the ``governance=`` keyword, the S8.1
overlay-omission rule, and the S15.7 log line) is covered separately in
``test_ciu_composefile.py``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import governance as gov  # noqa: E402


# ---------------------------------------------------------------------------
# S15.2 — resolve_config
# ---------------------------------------------------------------------------

class TestResolveConfig:
    def test_none_yields_defaults_disabled(self) -> None:
        cfg = gov.resolve_config(None)
        assert cfg == gov.GOVERNANCE_DEFAULTS
        assert cfg["enabled"] is False

    def test_empty_dict_yields_defaults(self) -> None:
        cfg = gov.resolve_config({})
        assert cfg["enabled"] is False
        assert cfg["cgroup_parent"] == "besteffort.slice"

    def test_partial_override_keeps_other_defaults(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "write_iops": 999})
        assert cfg["enabled"] is True
        assert cfg["write_iops"] == 999
        assert cfg["mem_limit"] == "1g"  # untouched default
        assert cfg["cgroup_parent"] == "besteffort.slice"

    def test_exempt_services_defaults_to_empty_list(self) -> None:
        cfg = gov.resolve_config({"enabled": True})
        assert cfg["exempt_services"] == []

    def test_non_bool_enabled_raises_s15_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.2\].*enabled"):
            gov.resolve_config({"enabled": "false"})

    def test_non_list_exempt_services_raises_s15_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.2\].*exempt_services"):
            gov.resolve_config({"enabled": True, "exempt_services": "worker"})

    def test_exempt_services_with_non_string_item_raises(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.2\]"):
            gov.resolve_config({"enabled": True, "exempt_services": [1, 2]})


# ---------------------------------------------------------------------------
# S15.4 — read_iops_baseline / derive_read_iops
# ---------------------------------------------------------------------------

class TestReadIopsBaseline:
    def test_parses_riops_max(self, tmp_path: Path) -> None:
        f = tmp_path / "io-baseline.env"
        f.write_text("# comment\nRIOPS_MAX=900\n", encoding="utf-8")
        assert gov.read_iops_baseline(f) == 900

    def test_quoted_value(self, tmp_path: Path) -> None:
        f = tmp_path / "io-baseline.env"
        f.write_text('RIOPS_MAX="450"\n', encoding="utf-8")
        assert gov.read_iops_baseline(f) == 450

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert gov.read_iops_baseline(tmp_path / "does-not-exist.env") is None

    def test_no_riops_max_line_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "io-baseline.env"
        f.write_text("SOME_OTHER_VAR=1\n", encoding="utf-8")
        assert gov.read_iops_baseline(f) is None


class TestDeriveReadIops:
    def test_explicit_nonzero_wins(self, tmp_path: Path) -> None:
        f = tmp_path / "io-baseline.env"
        f.write_text("RIOPS_MAX=900\n", encoding="utf-8")
        value, note = gov.derive_read_iops(500, baseline_path=f)
        assert value == 500
        assert note == "explicit"

    def test_zero_derives_two_thirds_of_baseline(self, tmp_path: Path) -> None:
        f = tmp_path / "io-baseline.env"
        f.write_text("RIOPS_MAX=900\n", encoding="utf-8")
        value, note = gov.derive_read_iops(0, baseline_path=f)
        assert value == 600  # 900 * 2 // 3
        assert "baseline" in note
        assert "900" in note

    def test_zero_with_no_baseline_falls_back(self, tmp_path: Path) -> None:
        value, note = gov.derive_read_iops(0, baseline_path=tmp_path / "missing.env")
        assert value == gov.FALLBACK_READ_IOPS
        assert "fallback" in note

    def test_integer_division_truncates(self, tmp_path: Path) -> None:
        f = tmp_path / "io-baseline.env"
        f.write_text("RIOPS_MAX=100\n", encoding="utf-8")
        value, _ = gov.derive_read_iops(0, baseline_path=f)
        assert value == 66  # 100 * 2 // 3 == 66 (not 66.67)


# ---------------------------------------------------------------------------
# S15.4 — baseline file resolution order (portable search)
# ---------------------------------------------------------------------------

class TestBaselineSearchOrder:
    """S15.4 — (a) config key > (b) env > (c) neutral default > (d) legacy;
    first EXISTING file wins."""

    def _pin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
        """Point (c)/(d) into tmp and clear (b); returns (default, legacy) paths."""
        default = tmp_path / "default" / "io-baseline.env"
        legacy = tmp_path / "legacy" / "io-baseline.env"
        monkeypatch.delenv(gov.BASELINE_PATH_ENV_VAR, raising=False)
        monkeypatch.setattr(gov, "DEFAULT_BASELINE_PATH", default)
        monkeypatch.setattr(gov, "LEGACY_BASELINE_PATH", legacy)
        return default, legacy

    def _touch(self, path: Path, riops: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"RIOPS_MAX={riops}\n", encoding="utf-8")
        return path

    def test_candidate_order(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        default, legacy = self._pin(monkeypatch, tmp_path)
        monkeypatch.setenv(gov.BASELINE_PATH_ENV_VAR, "/env/override.env")
        candidates = gov.baseline_search_candidates("/config/path.env")
        assert candidates == [
            Path("/config/path.env"),
            Path("/env/override.env"),
            default,
            legacy,
        ]

    def test_configured_path_wins_over_all(self, monkeypatch, tmp_path: Path) -> None:
        default, legacy = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        self._touch(legacy, 222)
        configured = self._touch(tmp_path / "configured.env", 333)
        env_file = self._touch(tmp_path / "env.env", 444)
        monkeypatch.setenv(gov.BASELINE_PATH_ENV_VAR, str(env_file))
        assert gov.resolve_baseline_path(str(configured)) == configured

    def test_env_wins_over_default_and_legacy(self, monkeypatch, tmp_path: Path) -> None:
        default, legacy = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        self._touch(legacy, 222)
        env_file = self._touch(tmp_path / "env.env", 444)
        monkeypatch.setenv(gov.BASELINE_PATH_ENV_VAR, str(env_file))
        assert gov.resolve_baseline_path("") == env_file

    def test_default_wins_over_legacy(self, monkeypatch, tmp_path: Path) -> None:
        default, legacy = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        self._touch(legacy, 222)
        assert gov.resolve_baseline_path("") == default

    def test_legacy_fallback_when_only_it_exists(self, monkeypatch, tmp_path: Path) -> None:
        _default, legacy = self._pin(monkeypatch, tmp_path)
        self._touch(legacy, 222)
        assert gov.resolve_baseline_path("") == legacy

    def test_nonexistent_configured_falls_through(self, monkeypatch, tmp_path: Path) -> None:
        """First EXISTING wins: a configured-but-missing path does not block the search."""
        default, _legacy = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        resolved = gov.resolve_baseline_path(str(tmp_path / "missing-configured.env"))
        assert resolved == default

    def test_none_when_no_candidate_exists(self, monkeypatch, tmp_path: Path) -> None:
        self._pin(monkeypatch, tmp_path)
        assert gov.resolve_baseline_path("") is None

    def test_derive_uses_search_order_and_names_searched_paths(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        self._pin(monkeypatch, tmp_path)
        value, note = gov.derive_read_iops(0)
        assert value == gov.FALLBACK_READ_IOPS
        assert "searched" in note

    def test_derive_via_configured_path_key(self, monkeypatch, tmp_path: Path) -> None:
        """The governance table's baseline_path key reaches derivation (S15.4 step a)."""
        self._pin(monkeypatch, tmp_path)
        configured = self._touch(tmp_path / "stack-baseline.env", 600)
        value, note = gov.derive_read_iops(0, configured_path=str(configured))
        assert value == 400  # 600 * 2 // 3
        assert "RIOPS_MAX=600" in note

    def test_build_injections_passes_baseline_path_key(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        self._pin(monkeypatch, tmp_path)
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("/dev/vda", "explicit"))
        configured = self._touch(tmp_path / "stack-baseline.env", 900)
        cfg = gov.resolve_config({"enabled": True, "baseline_path": str(configured)})
        injections, _ = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        rate = injections["redis"]["blkio_config"]["device_read_iops"][0]["rate"]
        assert rate == 600


# ---------------------------------------------------------------------------
# S15.5 — device autodetection / resolution
# ---------------------------------------------------------------------------

class TestResolveParentDisk:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("/dev/vda1", "/dev/vda"),
            ("/dev/sda1", "/dev/sda"),
            ("/dev/xvda2", "/dev/xvda"),
            ("/dev/nvme0n1p1", "/dev/nvme0n1"),
            ("/dev/mmcblk0p1", "/dev/mmcblk0"),
            ("/dev/vda", "/dev/vda"),  # already whole-disk
            ("/dev/mapper/vg-lv", "/dev/mapper/vg-lv"),  # LVM passes through
        ],
    )
    def test_partition_suffix_stripped(self, given: str, expected: str) -> None:
        assert gov._resolve_parent_disk(given) == expected


class TestDetectDevice:
    def test_findmnt_success_resolves_parent_disk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            return subprocess.CompletedProcess(a, 0, stdout="/dev/vda1\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        assert gov.detect_device() == "/dev/vda"

    def test_findmnt_missing_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            raise FileNotFoundError("no findmnt")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        assert gov.detect_device() == ""

    def test_findmnt_nonzero_exit_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            return subprocess.CompletedProcess(a, 1, stdout="", stderr="not found")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        assert gov.detect_device() == ""

    def test_non_dev_output_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            return subprocess.CompletedProcess(a, 0, stdout="tmpfs\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        assert gov.detect_device() == ""


class TestResolveDevice:
    def test_explicit_config_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "detect_device", lambda: "/dev/should-not-be-used")
        device, note = gov.resolve_device("/dev/vdb")
        assert device == "/dev/vdb"
        assert note == "explicit"

    def test_empty_config_autodetects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "detect_device", lambda: "/dev/vda")
        device, note = gov.resolve_device("")
        assert device == "/dev/vda"
        assert "autodetect" in note

    def test_autodetect_failure_yields_empty_with_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "detect_device", lambda: "")
        device, note = gov.resolve_device("")
        assert device == ""
        assert "failed" in note


# ---------------------------------------------------------------------------
# S15.3 — build_injections: enumeration, exemption, author-override precedence
# ---------------------------------------------------------------------------

class TestBuildInjections:
    def _cfg(self, **overrides) -> dict:
        cfg = gov.resolve_config({"enabled": True, **overrides})
        return cfg

    def test_injects_all_four_keys_when_author_sets_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("/dev/vda", "explicit"))
        cfg = self._cfg(device="/dev/vda", read_iops=100, write_iops=400)
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert set(injections) == {"redis"}
        frag = injections["redis"]
        assert frag["cgroup_parent"] == "besteffort.slice"
        assert frag["mem_limit"] == "1g"
        assert frag["mem_reservation"] == "256m"
        assert frag["blkio_config"] == {
            "device_read_iops": [{"path": "/dev/vda", "rate": 100}],
            "device_write_iops": [{"path": "/dev/vda", "rate": 400}],
        }
        assert any("services_injected=1" in n for n in notes)

    def test_author_set_key_is_skipped_others_still_injected(self) -> None:
        """S15.3 — per-key precedence: author's mem_limit wins; others still injected."""
        cfg = self._cfg(device="/dev/vda")
        block = {"image": "redis", "mem_limit": "4g"}
        injections, _ = gov.build_injections({"redis": block}, cfg)
        frag = injections["redis"]
        assert "mem_limit" not in frag
        assert frag["cgroup_parent"] == "besteffort.slice"
        assert frag["mem_reservation"] == "256m"
        assert "blkio_config" in frag

    def test_author_sets_all_four_keys_service_absent_from_injections(self) -> None:
        cfg = self._cfg(device="/dev/vda")
        block = {
            "cgroup_parent": "custom.slice",
            "mem_limit": "2g",
            "mem_reservation": "512m",
            "blkio_config": {"weight": 500},
        }
        injections, notes = gov.build_injections({"redis": block}, cfg)
        assert "redis" not in injections
        assert any("services_injected=0" in n for n in notes)

    def test_exempt_service_skipped_entirely(self) -> None:
        cfg = self._cfg(device="/dev/vda", exempt_services=["worker"])
        injections, notes = gov.build_injections(
            {"redis": {"image": "redis"}, "worker": {"image": "w"}}, cfg
        )
        assert set(injections) == {"redis"}
        assert any("exempt=1" in n for n in notes)

    def test_no_resolved_device_skips_blkio_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("", "autodetect failed"))
        cfg = self._cfg()
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        frag = injections["redis"]
        assert "blkio_config" not in frag
        assert frag["cgroup_parent"] == "besteffort.slice"
        assert any("none" in n for n in notes)

    def test_empty_compose_services_yields_no_injections(self) -> None:
        cfg = self._cfg(device="/dev/vda")
        injections, notes = gov.build_injections({}, cfg)
        assert injections == {}
        assert any("services_injected=0" in n for n in notes)


# ---------------------------------------------------------------------------
# S15.9 — parse_fio_json (fio prepends note lines even into --output files)
# ---------------------------------------------------------------------------

# Trimmed-down but structurally accurate fio --output-format=json document.
_FIO_JSON_BODY = """\
{
  "fio version": "fio-3.39",
  "jobs": [
    {
      "jobname": "riops-baseline",
      "read": {
        "io_bytes": 1073741824,
        "iops": 1234.56,
        "bw": 4938
      },
      "write": {
        "io_bytes": 0,
        "iops": 0.0
      }
    }
  ]
}
"""


class TestParseFioJson:
    def test_plain_json_parses(self) -> None:
        assert gov.parse_fio_json(_FIO_JSON_BODY) == 1235  # rounded

    def test_prepended_note_lines_are_skipped(self) -> None:
        """The live bug: fio writes 'note: ...' lines before the JSON even
        with --output=<file>; parsing must start at the first '{'."""
        text = (
            "note: libaio not available, falling back\n"
            "note: another human line\n" + _FIO_JSON_BODY
        )
        assert gov.parse_fio_json(text) == 1235

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="no JSON object"):
            gov.parse_fio_json("note: nothing here\n")

    def test_invalid_json_after_brace_raises(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            gov.parse_fio_json("{ this is not json")

    def test_missing_jobs_raises(self) -> None:
        with pytest.raises(ValueError, match="jobs"):
            gov.parse_fio_json('{"fio version": "fio-3.39"}')

    def test_missing_read_iops_raises(self) -> None:
        with pytest.raises(ValueError, match="iops"):
            gov.parse_fio_json('{"jobs": [{"jobname": "x", "read": {}}]}')


# ---------------------------------------------------------------------------
# S15.9 — select_fio_engine (libaio preferred; psync fallback is flagged)
# ---------------------------------------------------------------------------

class TestSelectFioEngine:
    def test_libaio_selected_when_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            return subprocess.CompletedProcess(a, 0, stdout="sync\npsync\nlibaio\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        engine, warning = gov.select_fio_engine("fio")
        assert engine == "libaio"
        assert warning is None

    def test_psync_fallback_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            return subprocess.CompletedProcess(a, 0, stdout="sync\npsync\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        engine, warning = gov.select_fio_engine("fio")
        assert engine == "psync"
        assert warning is not None and "queue-depth-1" in warning

    def test_enghelp_failure_falls_back_to_psync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a, **k):
            raise OSError("boom")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        engine, warning = gov.select_fio_engine("fio")
        assert engine == "psync"
        assert warning is not None


# ---------------------------------------------------------------------------
# S15.9 — run_iops_baseline (no real fio is ever executed in tests)
# ---------------------------------------------------------------------------

class TestRunIopsBaseline:
    def test_fio_absent_notice_exit_zero_nothing_written(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: None)
        out_file = tmp_path / "io-baseline.env"
        rc = gov.run_iops_baseline(out_file)
        assert rc == 0
        assert not out_file.exists()
        out = capsys.readouterr().out
        assert "fio not installed" in out
        assert str(gov.FALLBACK_READ_IOPS) in out

    def test_fresh_result_kept_without_force(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/fio")
        out_file = tmp_path / "io-baseline.env"
        out_file.write_text("RIOPS_MAX=555\n", encoding="utf-8")  # mtime = now
        rc = gov.run_iops_baseline(out_file)
        assert rc == 0
        assert out_file.read_text() == "RIOPS_MAX=555\n"  # untouched
        assert "--force" in capsys.readouterr().out

    def test_stale_result_is_remeasured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A result older than BASELINE_MAX_AGE_DAYS is re-measured without --force."""
        import os as _os

        out_file = tmp_path / "io-baseline.env"
        out_file.write_text("RIOPS_MAX=555\n", encoding="utf-8")
        stale = gov.time.time() - (gov.BASELINE_MAX_AGE_DAYS + 1) * 86400
        _os.utime(out_file, (stale, stale))
        self._wire_fake_fio(monkeypatch, riops_json=_FIO_JSON_BODY)
        rc = gov.run_iops_baseline(out_file)
        assert rc == 0
        assert "RIOPS_MAX=1235" in out_file.read_text()

    # -- fake fio plumbing -------------------------------------------------

    @staticmethod
    def _wire_fake_fio(
        monkeypatch: pytest.MonkeyPatch,
        *,
        riops_json: str,
        returncode: int = 0,
        prepend_note: bool = False,
    ) -> dict:
        """Monkeypatch which/enghelp/fio-run; the fake fio writes *riops_json*
        to the file named by --output=. Returns a dict capturing the fio argv."""
        captured: dict = {}
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/fio")
        monkeypatch.setattr(gov, "select_fio_engine", lambda fio_bin: ("libaio", None))

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            out_arg = next(a for a in cmd if a.startswith("--output="))
            out_path = Path(out_arg.split("=", 1)[1])
            text = riops_json
            if prepend_note:
                text = "note: something human\n" + text
            out_path.write_text(text, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="fio said no")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        return captured

    def test_force_measures_and_writes_riops_max_and_engine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        out_file = tmp_path / "io-baseline.env"
        out_file.write_text("RIOPS_MAX=1\n", encoding="utf-8")  # fresh, but --force
        captured = self._wire_fake_fio(monkeypatch, riops_json=_FIO_JSON_BODY, prepend_note=True)
        rc = gov.run_iops_baseline(out_file, runtime_s=7, force=True)
        assert rc == 0
        content = out_file.read_text()
        assert "RIOPS_MAX=1235" in content
        assert "RIOPS_ENGINE=libaio" in content
        # The parsed file is shell-sourceable by the S15.4 reader.
        assert gov.read_iops_baseline(out_file) == 1235
        # fio argv carries the required knobs (S15.9 item 4) + runtime.
        cmd = captured["cmd"]
        for expected in (
            "--rw=randread", "--bs=4k", "--direct=1", "--iodepth=32",
            "--numjobs=1", "--time_based", "--runtime=7",
            "--ioengine=libaio", "--output-format=json", "--size=1G",
        ):
            assert expected in cmd
        # Saturating-I/O warning was printed.
        assert "SATURATING" in capsys.readouterr().out

    def test_scratch_files_always_deleted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_file = tmp_path / "io-baseline.env"
        self._wire_fake_fio(monkeypatch, riops_json=_FIO_JSON_BODY)
        rc = gov.run_iops_baseline(out_file, force=True)
        assert rc == 0
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "io-baseline.env"]
        assert leftovers == []

    def test_fio_nonzero_exit_is_error_and_cleans_up(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        out_file = tmp_path / "io-baseline.env"
        self._wire_fake_fio(monkeypatch, riops_json=_FIO_JSON_BODY, returncode=42)
        rc = gov.run_iops_baseline(out_file, force=True)
        assert rc == 1
        assert not out_file.exists()
        assert "fio exited 42" in capsys.readouterr().out
        leftovers = [p.name for p in tmp_path.iterdir()]
        assert leftovers == []

    def test_unparseable_fio_output_is_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        out_file = tmp_path / "io-baseline.env"
        self._wire_fake_fio(monkeypatch, riops_json="note: no json at all\n")
        rc = gov.run_iops_baseline(out_file, force=True)
        assert rc == 1
        assert not out_file.exists()
        assert "could not parse fio JSON" in capsys.readouterr().out
