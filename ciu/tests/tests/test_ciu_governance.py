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
        # No hardcoded slice-name default — resolve_cgroup_parent() resolves
        # this later, at build_injections() time, erroring if unresolvable.
        assert cfg["cgroup_parent"] == ""

    def test_partial_override_keeps_other_defaults(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "write_iops": 999})
        assert cfg["enabled"] is True
        assert cfg["write_iops"] == 999
        assert cfg["mem_limit"] == "1g"  # untouched default
        assert cfg["cgroup_parent"] == ""

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

    # -- D-G9 check 2 — unknown-key warning (forward-compat preserved) -----

    def test_unknown_key_warns_but_does_not_raise(self, capsys) -> None:
        """The typo case: a WARN is printed, but resolve_config still returns
        normally (forward-compat, S15.2) — a hard reject would break a newer
        stack config running against an older CIU."""
        cfg = gov.resolve_config({"cgroup_parnet": "x.slice", "enabled": True})
        assert cfg["enabled"] is True
        out = capsys.readouterr().out
        assert "[WARN]" in out
        assert "[S15.2]" in out
        assert "cgroup_parnet" in out

    def test_unknown_key_value_still_passes_through(self, capsys) -> None:
        """Unknown keys are NOT dropped — they pass through unchanged (S15.2)."""
        cfg = gov.resolve_config({"cgroup_parnet": "x.slice", "enabled": True})
        assert cfg["cgroup_parnet"] == "x.slice"
        capsys.readouterr()

    def test_only_known_keys_prints_no_warning(self, capsys) -> None:
        gov.resolve_config({"enabled": True, "mem_limit": "2g"})
        out = capsys.readouterr().out
        assert "[WARN]" not in out

    def test_none_raw_prints_no_warning(self, capsys) -> None:
        gov.resolve_config(None)
        out = capsys.readouterr().out
        assert out == ""

    def test_multiple_unknown_keys_all_named(self, capsys) -> None:
        gov.resolve_config({"enabled": True, "cgroup_parnet": "x", "mem_limitt": "1g"})
        out = capsys.readouterr().out
        assert "cgroup_parnet" in out
        assert "mem_limitt" in out

    def test_strict_unknown_keys_raises_instead_of_warning(self, capsys) -> None:
        """Opt-in only: strict_unknown_keys=True turns the WARN into a raise;
        the module-level DEFAULT (no kwarg) stays permissive."""
        with pytest.raises(ValueError, match=r"\[S15\.2\].*cgroup_parnet"):
            gov.resolve_config(
                {"cgroup_parnet": "x.slice", "enabled": True}, strict_unknown_keys=True
            )
        # The raise path must not ALSO print — no double-signaling the same fact.
        out = capsys.readouterr().out
        assert "[WARN]" not in out

    def test_strict_unknown_keys_still_passes_with_known_keys_only(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "mem_limit": "2g"}, strict_unknown_keys=True)
        assert cfg["mem_limit"] == "2g"

    def test_non_bool_enabled_still_raises_with_strict_flag(self) -> None:
        """Regression guard: the pre-existing enabled-type check is unaffected
        by the new strict_unknown_keys parameter."""
        with pytest.raises(ValueError, match=r"\[S15\.2\].*enabled"):
            gov.resolve_config({"enabled": "false"}, strict_unknown_keys=True)

    # -- S15.14/S15.15/S15.16 — new keys default to inert/off ---------------

    def test_new_keys_default_to_off(self) -> None:
        cfg = gov.resolve_config(None)
        assert cfg["io_weight"] == 0
        assert cfg["read_bps"] == 0
        assert cfg["write_bps"] == 0
        assert cfg["mem_min"] == ""

    # -- S15.14 — io_weight range validation ---------------------------------

    def test_io_weight_zero_is_valid_unset(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "io_weight": 0})
        assert cfg["io_weight"] == 0

    def test_io_weight_in_range_is_valid(self) -> None:
        assert gov.resolve_config({"enabled": True, "io_weight": 10})["io_weight"] == 10
        assert gov.resolve_config({"enabled": True, "io_weight": 1000})["io_weight"] == 1000

    def test_io_weight_below_range_raises(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.14\].*io_weight"):
            gov.resolve_config({"enabled": True, "io_weight": 9})

    def test_io_weight_above_range_raises(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.14\].*io_weight"):
            gov.resolve_config({"enabled": True, "io_weight": 1001})

    # -- S15.21 (CIU-90) — cpus default + validation -------------------------

    def test_cpus_defaults_to_unset(self) -> None:
        cfg = gov.resolve_config(None)
        assert cfg["cpus"] == ""

    def test_cpus_empty_string_is_valid_unset(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "cpus": ""})
        assert cfg["cpus"] == ""

    def test_cpus_positive_integer_string_is_valid(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "cpus": "2"})
        assert cfg["cpus"] == "2"

    def test_cpus_fractional_string_is_valid(self) -> None:
        cfg = gov.resolve_config({"enabled": True, "cpus": "1.5"})
        assert cfg["cpus"] == "1.5"

    def test_cpus_zero_raises_s15_21(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.21\].*cpus"):
            gov.resolve_config({"enabled": True, "cpus": "0"})

    def test_cpus_negative_raises_s15_21(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.21\].*cpus"):
            gov.resolve_config({"enabled": True, "cpus": "-1"})

    def test_cpus_non_numeric_raises_s15_21(self) -> None:
        with pytest.raises(ValueError, match=r"\[S15\.21\].*cpus"):
            gov.resolve_config({"enabled": True, "cpus": "lots"})


# ---------------------------------------------------------------------------
# S15.10 — resolve_stack_governance (global-default merge layer, CIU-13)
# ---------------------------------------------------------------------------

class TestResolveStackGovernance:
    def test_stack_table_wins_over_global_for_shared_keys(self) -> None:
        stack = {"enabled": True, "mem_limit": "2g"}
        global_cfg = {"governance": {"enabled": True, "mem_limit": "1g"}}
        result = gov.resolve_stack_governance(stack, global_cfg)
        assert result == stack
        assert result is not stack  # defensive copy, not the same object

    def test_falls_back_to_global_when_stack_declares_none(self) -> None:
        global_cfg = {"governance": {"enabled": True, "cgroup_parent": "besteffort.slice"}}
        result = gov.resolve_stack_governance(None, global_cfg)
        assert result == global_cfg["governance"]

    def test_empty_stack_table_inherits_global_in_full(self) -> None:
        # CIU-13 fix: an explicit-but-empty stack table has NOTHING to layer
        # over the base, so the result is the global table, unchanged — this
        # used to return {} (the stack "fully owning" its empty table), which
        # was the bug: the stack's mere presence discarded the global default.
        global_cfg = {"governance": {"enabled": True, "mem_limit": "4g"}}
        result = gov.resolve_stack_governance({}, global_cfg)
        assert result == {"enabled": True, "mem_limit": "4g"}

    def test_ciu13_one_key_stack_override_inherits_rest_of_global(self) -> None:
        """The exact CIU-13 regression: dstdns's global governance table set
        enabled=true, cgroup_parent, ksm_optin, mem_limit, device; a stack
        restated ONLY mem_limit to raise it. The old code discarded every
        other global key (including enabled), silently resolving to
        enabled=false — an unconfined container. The merged raw table must
        now carry every global key the stack didn't restate."""
        global_cfg = {
            "governance": {
                "enabled": True,
                "cgroup_parent": "dev-background.slice",
                "ksm_optin": "tools/ksm-optin/ksm-optin.so",
                "mem_limit": "2g",
                "device": "/dev/vda",
            }
        }
        stack = {"mem_limit": "8g"}
        result = gov.resolve_stack_governance(stack, global_cfg)
        assert result == {
            "enabled": True,
            "cgroup_parent": "dev-background.slice",
            "ksm_optin": "tools/ksm-optin/ksm-optin.so",
            "mem_limit": "8g",  # the stack's override wins
            "device": "/dev/vda",
        }
        # And critically: resolving this against GOVERNANCE_DEFAULTS keeps
        # governance ENABLED, not silently False.
        assert gov.resolve_config(result)["enabled"] is True

    def test_stack_can_still_opt_out_by_restating_enabled_false(self) -> None:
        """The merge layer must not make opting out impossible: a stack that
        explicitly restates enabled=false still disables governance for
        itself, same as before."""
        global_cfg = {"governance": {"enabled": True, "mem_limit": "2g"}}
        stack = {"enabled": False}
        result = gov.resolve_stack_governance(stack, global_cfg)
        assert result["enabled"] is False
        assert gov.resolve_config(result)["enabled"] is False

    def test_no_stack_and_no_global_yields_none(self) -> None:
        assert gov.resolve_stack_governance(None, None) is None
        assert gov.resolve_stack_governance(None, {}) is None
        assert gov.resolve_stack_governance(None, {"deploy": {}}) is None

    def test_global_without_governance_key_yields_none(self) -> None:
        assert gov.resolve_stack_governance(None, {"deploy": {"log_level": "INFO"}}) is None

    def test_mutating_result_does_not_affect_source_tables(self) -> None:
        stack = {"enabled": True}
        result = gov.resolve_stack_governance(stack, None)
        result["enabled"] = False
        assert stack["enabled"] is True

    def test_mutating_result_does_not_affect_global_source_table(self) -> None:
        global_cfg = {"governance": {"enabled": True, "mem_limit": "2g"}}
        result = gov.resolve_stack_governance({"mem_limit": "8g"}, global_cfg)
        result["mem_limit"] = "16g"
        assert global_cfg["governance"]["mem_limit"] == "2g"


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
    """S15.4 — (a) config key > (b) env > (c) neutral default > (d) host tooling;
    first EXISTING file wins."""

    def _pin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
        """Point (c)/(d) into tmp and clear (b); returns (default, host-tooling) paths."""
        default = tmp_path / "default" / "io-baseline.env"
        host = tmp_path / "host-tooling" / "io-baseline.env"
        monkeypatch.delenv(gov.BASELINE_PATH_ENV_VAR, raising=False)
        monkeypatch.setattr(gov, "DEFAULT_BASELINE_PATH", default)
        monkeypatch.setattr(gov, "HOST_TOOLING_BASELINE_PATH", host)
        return default, host

    def _touch(self, path: Path, riops: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"RIOPS_MAX={riops}\n", encoding="utf-8")
        return path

    def test_candidate_order(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        default, host = self._pin(monkeypatch, tmp_path)
        monkeypatch.setenv(gov.BASELINE_PATH_ENV_VAR, "/env/override.env")
        candidates = gov.baseline_search_candidates("/config/path.env")
        assert candidates == [
            Path("/config/path.env"),
            Path("/env/override.env"),
            default,
            host,
        ]

    def test_configured_path_wins_over_all(self, monkeypatch, tmp_path: Path) -> None:
        default, host = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        self._touch(host, 222)
        configured = self._touch(tmp_path / "configured.env", 333)
        env_file = self._touch(tmp_path / "env.env", 444)
        monkeypatch.setenv(gov.BASELINE_PATH_ENV_VAR, str(env_file))
        assert gov.resolve_baseline_path(str(configured)) == configured

    def test_env_wins_over_default_and_host_tooling(self, monkeypatch, tmp_path: Path) -> None:
        default, host = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        self._touch(host, 222)
        env_file = self._touch(tmp_path / "env.env", 444)
        monkeypatch.setenv(gov.BASELINE_PATH_ENV_VAR, str(env_file))
        assert gov.resolve_baseline_path("") == env_file

    def test_default_wins_over_host_tooling(self, monkeypatch, tmp_path: Path) -> None:
        default, host = self._pin(monkeypatch, tmp_path)
        self._touch(default, 111)
        self._touch(host, 222)
        assert gov.resolve_baseline_path("") == default

    def test_host_tooling_fallback_when_only_it_exists(self, monkeypatch, tmp_path: Path) -> None:
        """A host with mdt host-setup installed but no `ciu iops-baseline` run."""
        _default, host = self._pin(monkeypatch, tmp_path)
        self._touch(host, 222)
        assert gov.resolve_baseline_path("") == host

    def test_nonexistent_configured_falls_through(self, monkeypatch, tmp_path: Path) -> None:
        """First EXISTING wins: a configured-but-missing path does not block the search."""
        default, _host = self._pin(monkeypatch, tmp_path)
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
        cfg = gov.resolve_config({
            "enabled": True, "baseline_path": str(configured), "cgroup_parent": "dev-background.slice",
        })
        injections, _ = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        rate = injections["redis"]["blkio_config"]["device_read_iops"][0]["rate"]
        assert rate == 600


# ---------------------------------------------------------------------------
# S15.4 — measurement provenance (MEASURE_METHOD)
# ---------------------------------------------------------------------------

class TestBaselineMethod:
    """S15.4 — the numbers alone cannot say how they were measured, so the
    marker must survive the round trip and reach the derivation note."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        f = tmp_path / "io-baseline.env"
        f.write_text(body, encoding="utf-8")
        return f

    def test_reads_marker(self, tmp_path: Path) -> None:
        f = self._write(tmp_path, "RIOPS_MAX=90000\nMEASURE_METHOD=sustained-v3\n")
        assert gov.read_baseline_method(f) == "sustained-v3"

    def test_quoted_marker(self, tmp_path: Path) -> None:
        f = self._write(tmp_path, 'MEASURE_METHOD="burst-v1"\n')
        assert gov.read_baseline_method(f) == "burst-v1"

    def test_none_when_absent(self, tmp_path: Path) -> None:
        f = self._write(tmp_path, "RIOPS_MAX=90000\n")
        assert gov.read_baseline_method(f) is None

    def test_none_when_unreadable(self, tmp_path: Path) -> None:
        assert gov.read_baseline_method(tmp_path / "nope.env") is None

    def test_derive_reports_sustained(self, tmp_path: Path) -> None:
        f = self._write(tmp_path, "RIOPS_MAX=90000\nMEASURE_METHOD=sustained-v3\n")
        value, note = gov.derive_read_iops(0, baseline_path=f)
        assert value == 60000
        assert "method=sustained-v3" in note
        assert "UNKNOWN" not in note and "UNRECOGNISED" not in note

    def test_derive_flags_missing_marker(self, tmp_path: Path) -> None:
        """Still derives — provenance is lower confidence, not an error."""
        f = self._write(tmp_path, "RIOPS_MAX=90000\n")
        value, note = gov.derive_read_iops(0, baseline_path=f)
        assert value == 60000
        assert "method=UNKNOWN" in note

    def test_derive_flags_unrecognised_marker(self, tmp_path: Path) -> None:
        f = self._write(tmp_path, "RIOPS_MAX=90000\nMEASURE_METHOD=homegrown-v9\n")
        value, note = gov.derive_read_iops(0, baseline_path=f)
        assert value == 60000
        assert "UNRECOGNISED" in note

    def test_derive_warns_burst_reads_high(self, tmp_path: Path) -> None:
        """burst-v1 is known but biased — the note must say which way."""
        f = self._write(tmp_path, f"RIOPS_MAX=90000\nMEASURE_METHOD={gov.MEASURE_METHOD_BURST}\n")
        _value, note = gov.derive_read_iops(0, baseline_path=f)
        assert "reads high" in note

    def test_ciu_own_method_is_known(self) -> None:
        assert gov.MEASURE_METHOD_BURST in gov.KNOWN_MEASURE_METHODS
        assert "sustained-v3" in gov.KNOWN_MEASURE_METHODS


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
# Host dev-tier cgroup governance rollout — resolve_cgroup_parent (no
# hardcoded slice-name fallback: unresolvable is an error, never a default).
# ---------------------------------------------------------------------------


class TestResolveCgroupParent:
    def test_explicit_config_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(gov.CGROUP_PARENT_ENV_VAR, "should-not-be-used.slice")
        assert gov.resolve_cgroup_parent("explicit.slice") == "explicit.slice"

    def test_falls_back_to_ambient_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(gov.CGROUP_PARENT_ENV_VAR, "dev-background.slice")
        assert gov.resolve_cgroup_parent("") == "dev-background.slice"

    def test_neither_configured_nor_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(gov.CGROUP_PARENT_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match=r"\[S15\.2\].*no cgroup_parent is resolvable"):
            gov.resolve_cgroup_parent("")


# ---------------------------------------------------------------------------
# D-G9 check 1 — check_slice_unit (systemd slice-existence probe)
# ---------------------------------------------------------------------------


class TestSystemdIsPid1:
    """The real (unmocked) body of `_systemd_is_pid1` — every other test in
    this file monkeypatches it entirely, so this is the only place its actual
    implementation runs."""

    def test_matches_run_systemd_system_directory(self) -> None:
        assert gov._systemd_is_pid1() == Path("/run/systemd/system").is_dir()

    def test_true_when_marker_directory_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        marker = tmp_path / "run" / "systemd" / "system"
        marker.mkdir(parents=True)
        monkeypatch.setattr(gov, "Path", lambda p: marker if p == "/run/systemd/system" else Path(p))
        assert gov._systemd_is_pid1() is True

    def test_false_when_marker_directory_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        missing = tmp_path / "no-such-run-systemd-system"
        monkeypatch.setattr(gov, "Path", lambda p: missing if p == "/run/systemd/system" else Path(p))
        assert gov._systemd_is_pid1() is False


class TestCheckSliceUnit:
    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulate systemd genuinely being PID 1 by default (most tests here
        exercise the systemctl-parsing logic, not the PID-1 detection itself).
        The dedicated shim-detection test below overrides this back to False."""
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)

    def test_no_systemctl_skips_with_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: None)
        exists, note = gov.check_slice_unit("nyxloom-daemon.slice")
        assert exists is None
        assert "no systemctl" in note

    def test_no_systemctl_never_calls_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: None)

        def fail_run(*a, **k):
            raise AssertionError("subprocess.run must not be called when systemctl is absent")

        monkeypatch.setattr(gov.subprocess, "run", fail_run)
        gov.check_slice_unit("whatever.slice")  # must not raise

    def test_systemd_not_pid1_skips_with_none_even_if_systemctl_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The devcontainer-shim case: `systemctl` binary IS present (so the
        which() check alone would proceed), but systemd is not actually PID 1
        (no /run/systemd/system) — must skip as inconclusive, never call
        subprocess.run and parse the shim's non-KEY=VALUE notice as if it
        were a real (and false) LoadState/MemoryMin answer."""
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/local/bin/systemctl")
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: False)

        def fail_run(*a, **k):
            raise AssertionError("subprocess.run must not be called when systemd is not PID 1")

        monkeypatch.setattr(gov.subprocess, "run", fail_run)
        exists, note = gov.check_slice_unit("nyxloom-daemon.slice")
        assert exists is None
        assert "not PID 1" in note

    def test_loaded_slice_reports_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="LoadState=loaded\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        exists, note = gov.check_slice_unit("besteffort.slice")
        assert exists is True
        assert "loaded" in note

    def test_not_found_slice_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="LoadState=not-found\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        exists, note = gov.check_slice_unit("nyxloom-daemon.slice")
        assert exists is False
        assert "not-found" in note
        assert "not installed" in note

    def test_masked_slice_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LoadState can be other non-'loaded' values too (e.g. 'masked')."""
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="LoadState=masked\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        exists, note = gov.check_slice_unit("disabled.slice")
        assert exists is False
        assert "masked" in note

    def test_empty_output_reports_false_with_unknown_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        exists, note = gov.check_slice_unit("weird.slice")
        assert exists is False
        assert "unknown" in note

    def test_subprocess_error_is_inconclusive_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            raise OSError("boom")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        exists, note = gov.check_slice_unit("nyxloom-daemon.slice")
        assert exists is None
        assert "boom" in note

    def test_timeout_is_inconclusive_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        exists, note = gov.check_slice_unit("nyxloom-daemon.slice")
        assert exists is None
        assert "skipping" in note

    def test_slice_name_passed_through_to_systemctl_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")
        captured = {}

        def fake_run(cmd, **k):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="LoadState=loaded\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        gov.check_slice_unit("nyxloom-daemon.slice")
        assert "nyxloom-daemon.slice" in captured["cmd"]
        assert captured["cmd"][0] == "systemctl"


# ---------------------------------------------------------------------------
# S15.3 — build_injections: enumeration, exemption, author-override precedence
# ---------------------------------------------------------------------------

class TestBuildInjections:
    def _cfg(self, **overrides) -> dict:
        raw = {"enabled": True, "cgroup_parent": "dev-background.slice"}
        raw.update(overrides)
        cfg = gov.resolve_config(raw)
        return cfg

    def test_injects_all_five_keys_when_author_sets_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("/dev/vda", "explicit"))
        cfg = self._cfg(device="/dev/vda", read_iops=100, write_iops=400)
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert set(injections) == {"redis"}
        frag = injections["redis"]
        assert frag["cgroup_parent"] == "dev-background.slice"
        assert frag["mem_limit"] == "1g"
        assert frag["memswap_limit"] == "17g"
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
        assert frag["cgroup_parent"] == "dev-background.slice"
        assert frag["memswap_limit"] == "17g"
        assert frag["mem_reservation"] == "256m"
        assert "blkio_config" in frag

    def test_author_set_swap_key_is_skipped_others_still_injected(self) -> None:
        """S15.3 — per-key precedence: author's memswap_limit wins; others still injected."""
        cfg = self._cfg(device="/dev/vda")
        block = {"image": "redis", "memswap_limit": "unlimited"}
        injections, _ = gov.build_injections({"redis": block}, cfg)
        frag = injections["redis"]
        assert "memswap_limit" not in frag
        assert frag["cgroup_parent"] == "dev-background.slice"
        assert frag["mem_limit"] == "1g"
        assert frag["mem_reservation"] == "256m"
        assert "blkio_config" in frag

    def test_author_sets_all_five_keys_service_absent_from_injections(self) -> None:
        cfg = self._cfg(device="/dev/vda")
        block = {
            "cgroup_parent": "custom.slice",
            "mem_limit": "2g",
            "memswap_limit": "18g",
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
        assert frag["cgroup_parent"] == "dev-background.slice"
        assert any("none" in n for n in notes)

    def test_empty_compose_services_yields_no_injections(self) -> None:
        cfg = self._cfg(device="/dev/vda")
        injections, notes = gov.build_injections({}, cfg)
        assert injections == {}
        assert any("services_injected=0" in n for n in notes)

    def test_unresolvable_cgroup_parent_raises_even_with_no_services(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Governance enabled, nothing named a slice, no ambient env var: fail
        loud — never silently omit cgroup_parent from the injected fragment."""
        monkeypatch.delenv(gov.CGROUP_PARENT_ENV_VAR, raising=False)
        cfg = gov.resolve_config({"enabled": True, "device": "/dev/vda"})
        with pytest.raises(ValueError, match=r"\[S15\.2\].*no cgroup_parent is resolvable"):
            gov.build_injections({"redis": {"image": "redis"}}, cfg)

    # -- S15.14 — io_weight injection (independent of device resolution) ----

    def test_io_weight_injected_without_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("", "autodetect failed"))
        cfg = self._cfg(io_weight=500)
        injections, _ = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert injections["redis"]["blkio_config"] == {"weight": 500}

    def test_io_weight_zero_omits_weight_key(self) -> None:
        cfg = self._cfg(device="/dev/vda", io_weight=0)
        injections, _ = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert "weight" not in injections["redis"]["blkio_config"]

    def test_io_weight_and_device_iops_coexist_in_one_blkio_config(self) -> None:
        cfg = self._cfg(device="/dev/vda", io_weight=800, read_iops=100, write_iops=400)
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        blk = injections["redis"]["blkio_config"]
        assert blk["weight"] == 800
        assert blk["device_read_iops"] == [{"path": "/dev/vda", "rate": 100}]
        assert any("io_weight=800" in n for n in notes)

    # -- S15.15 — read_bps/write_bps bandwidth caps --------------------------

    def test_bandwidth_caps_injected_when_device_resolves(self) -> None:
        cfg = self._cfg(device="/dev/vda", read_bps=1_000_000, write_bps=500_000)
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        blk = injections["redis"]["blkio_config"]
        assert blk["device_read_bps"] == [{"path": "/dev/vda", "rate": 1_000_000}]
        assert blk["device_write_bps"] == [{"path": "/dev/vda", "rate": 500_000}]
        assert any("read_bps=1000000" in n for n in notes)
        assert any("write_bps=500000" in n for n in notes)

    def test_bandwidth_caps_default_to_uncapped(self) -> None:
        cfg = self._cfg(device="/dev/vda")
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        blk = injections["redis"]["blkio_config"]
        assert "device_read_bps" not in blk
        assert "device_write_bps" not in blk
        assert any("uncapped" in n for n in notes)

    def test_bandwidth_caps_skipped_without_device_even_if_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bandwidth caps are per-device (S15.5); no resolved device means no
        blkio_config device_* fields, same as the pre-existing iops keys."""
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("", "autodetect failed"))
        cfg = self._cfg(read_bps=1_000_000, write_bps=500_000)
        injections, _ = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert "blkio_config" not in injections["redis"]  # io_weight also 0 here

    # -- S15.21 (CIU-90) — cpus injection, explicit-opt-in-only --------------

    def test_cpus_unset_injects_no_cpus_key(self) -> None:
        """Regression guard (CIU-90's whole point): governance.cpus left at
        its default ("") must NOT inject a `cpus` key — uncapped stays
        uncapped, unlike every other always-on governance key."""
        cfg = self._cfg(device="/dev/vda")
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert "cpus" not in injections["redis"]
        assert not any(n.startswith("cpus=") for n in notes)

    def test_cpus_configured_injects_the_key(self) -> None:
        cfg = self._cfg(device="/dev/vda", cpus="2")
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert injections["redis"]["cpus"] == "2"
        assert any("cpus=2" in n for n in notes)

    def test_cpus_author_set_key_is_left_untouched(self) -> None:
        """S15.3 — per-key precedence, same rule already tested for the other
        four keys: the author's own `cpus:` compose key always wins."""
        cfg = self._cfg(device="/dev/vda", cpus="2")
        block = {"image": "redis", "cpus": "4"}
        injections, _ = gov.build_injections({"redis": block}, cfg)
        assert "cpus" not in injections["redis"]
        # the rest of governance still applies to this service
        assert injections["redis"]["cgroup_parent"] == "dev-background.slice"

    # -- S15.16 — mem_min is visible in notes but never injected -------------

    def test_mem_min_never_injected_but_visible_in_notes(self) -> None:
        cfg = self._cfg(device="/dev/vda", mem_min="2g")
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert "mem_min" not in injections["redis"]
        assert any("mem_min=2g" in n for n in notes)

    def test_mem_min_not_declared_notes_say_so(self) -> None:
        cfg = self._cfg(device="/dev/vda")
        _, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert any("not declared" in n for n in notes)


# ---------------------------------------------------------------------------
# S15.16 — parse_size_to_bytes / check_slice_memory_min
# ---------------------------------------------------------------------------

class TestParseSizeToBytes:
    def test_bare_bytes(self) -> None:
        assert gov.parse_size_to_bytes("1024") == 1024

    def test_b_suffix(self) -> None:
        assert gov.parse_size_to_bytes("512b") == 512

    def test_k_suffix(self) -> None:
        assert gov.parse_size_to_bytes("2k") == 2048

    def test_m_suffix(self) -> None:
        assert gov.parse_size_to_bytes("256m") == 256 * 1024 * 1024

    def test_g_suffix(self) -> None:
        assert gov.parse_size_to_bytes("2g") == 2 * 1024 ** 3

    def test_t_suffix(self) -> None:
        assert gov.parse_size_to_bytes("1t") == 1024 ** 4

    def test_uppercase_suffix(self) -> None:
        assert gov.parse_size_to_bytes("2G") == 2 * 1024 ** 3

    def test_fractional_value(self) -> None:
        assert gov.parse_size_to_bytes("1.5g") == int(1.5 * 1024 ** 3)

    def test_unrecognised_suffix_raises(self) -> None:
        with pytest.raises(ValueError, match="unrecognised size suffix"):
            gov.parse_size_to_bytes("2x")

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError, match="not a recognizable size"):
            gov.parse_size_to_bytes("plenty")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            gov.parse_size_to_bytes("")


class TestCheckSliceMemoryMin:
    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """See TestCheckSliceUnit._systemd_running — same default, same reason."""
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)

    def test_no_systemctl_skips_with_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: None)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is None
        assert "no systemctl" in note

    def test_systemd_not_pid1_skips_with_none_even_if_systemctl_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/local/bin/systemctl")
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: False)

        def fail_run(*a, **k):
            raise AssertionError("subprocess.run must not be called when systemd is not PID 1")

        monkeypatch.setattr(gov.subprocess, "run", fail_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is None
        assert "not PID 1" in note

    def test_adequate_memory_min_reports_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=4294967296\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is True
        assert "4294967296" in note

    def test_insufficient_memory_min_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=1073741824\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False
        assert "< required" in note

    def test_infinity_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=infinity\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False
        assert "no floor" in note

    def test_zero_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=0\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False

    def test_unparseable_value_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=garbage\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False
        assert "unparseable" in note

    def test_subprocess_error_is_inconclusive_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            raise OSError("boom")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is None
        assert "boom" in note

    def test_exactly_equal_is_adequate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=2147483648\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, _ = gov.check_slice_memory_min("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is True


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


class TestSliceAncestorChain:
    def test_single_dash_derives_two_level_chain(self) -> None:
        assert gov.slice_ancestor_chain("dev-background.slice") == [
            "dev-background.slice",
            "dev.slice",
        ]

    def test_no_dash_returns_only_itself(self) -> None:
        assert gov.slice_ancestor_chain("wings.slice") == ["wings.slice"]

    def test_multi_dash_derives_full_chain(self) -> None:
        assert gov.slice_ancestor_chain("a-b-c.slice") == [
            "a-b-c.slice",
            "a-b.slice",
            "a.slice",
        ]

    def test_non_slice_name_raises(self) -> None:
        with pytest.raises(ValueError, match="not a slice name"):
            gov.slice_ancestor_chain("not-a-slice")


class TestCheckMemoryMinAncestorChain:
    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)
        monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/systemctl")

    def test_all_ancestors_adequate_reports_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=4294967296\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_memory_min_ancestor_chain("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is True
        assert "dev-background.slice" in note
        assert "dev.slice" in note

    def test_immediate_slice_inadequate_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact live default-mdt-config scenario: dev-background.slice
        itself has no MemoryMin at all."""

        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=infinity\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_memory_min_ancestor_chain("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False
        assert "dev-background.slice" in note

    def test_only_parent_inadequate_still_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The immediate slice IS configured, but its parent (dev.slice) is
        not — effective protection is still zero; the chain check must catch
        what a single-slice check would miss entirely."""

        def fake_run(cmd, **k):
            if cmd[2] == "dev-background.slice":
                return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=4294967296\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=infinity\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_memory_min_ancestor_chain("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False
        assert "dev.slice" in note

    def test_both_levels_inadequate_reports_both_in_one_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=0\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_memory_min_ancestor_chain("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is False
        assert "dev-background.slice" in note
        assert "dev.slice" in note

    def test_single_segment_slice_checks_only_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []

        def fake_run(cmd, **k):
            calls.append(cmd[2])
            return subprocess.CompletedProcess(cmd, 0, stdout="MemoryMin=4294967296\n", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, _ = gov.check_memory_min_ancestor_chain("wings.slice", 2 * 1024 ** 3)
        assert adequate is True
        assert calls == ["wings.slice"]

    def test_inconclusive_on_first_probe_short_circuits_rest_of_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def fake_run(cmd, **k):
            calls.append(cmd[2])
            raise OSError("boom")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        adequate, note = gov.check_memory_min_ancestor_chain("dev-background.slice", 2 * 1024 ** 3)
        assert adequate is None
        assert "boom" in note
        assert calls == ["dev-background.slice"]  # dev.slice never probed


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
        # S15.9: provenance is mandatory output and round-trips to the reader —
        # this measurement is the unramped 1G/10s one, and must say so.
        assert f"MEASURE_METHOD={gov.MEASURE_METHOD_BURST}" in content
        assert gov.read_baseline_method(out_file) == gov.MEASURE_METHOD_BURST
        assert "MEASURED_AT=" in content
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


class TestKsmOptinInjection:
    """S15.11 — KSM opt-in env + bind injection."""

    def _cfg(self, **overrides) -> dict:
        raw = {"enabled": True, "cgroup_parent": "dev-background.slice"}
        raw.update(overrides)
        return gov.resolve_config(raw)

    def test_default_is_off(self) -> None:
        cfg = gov.resolve_config({"enabled": True})
        assert cfg["ksm_optin"] == ""

    def test_injects_env_and_bind_when_source_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("", "none"))
        cfg = self._cfg(ksm_optin="tools/ksm-optin/ksm-optin.so")
        cfg["_ksm_optin_source"] = "/phys/tools/ksm-optin/ksm-optin.so"
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        frag = injections["redis"]
        assert frag["environment"] == [f"LD_PRELOAD={gov.KSM_PRELOAD_TARGET}"]
        assert frag["volumes"] == [{
            "type": "bind",
            "source": "/phys/tools/ksm-optin/ksm-optin.so",
            "target": gov.KSM_PRELOAD_TARGET,
            "read_only": True,
        }]
        assert any("ksm_optin=tools/ksm-optin/ksm-optin.so" in n for n in notes)

    def test_exempt_service_gets_no_ksm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("", "none"))
        cfg = self._cfg(ksm_optin="x.so", exempt_services=["vault"])
        cfg["_ksm_optin_source"] = "/phys/x.so"
        injections, _ = gov.build_injections({"vault": {"image": "v"}, "redis": {"image": "r"}}, cfg)
        assert "vault" not in injections
        assert "environment" in injections["redis"]

    def test_no_source_resolved_means_no_env_frag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "resolve_device", lambda configured: ("", "none"))
        cfg = self._cfg()  # ksm_optin default ""
        injections, notes = gov.build_injections({"redis": {"image": "redis"}}, cfg)
        assert "environment" not in injections.get("redis", {})
        assert any("ksm_optin=off" in n for n in notes)


# ===========================================================================
# S15.22 — downward slice enumeration + memory_recursiveprot (CIU-95)
# ===========================================================================


class TestSliceCgroupPath:
    """Pure derivation — reverses `slice_ancestor_chain` (TestSliceAncestorChain
    covers that rule itself) into the cgroupfs's nested-directory layout."""

    def test_dash_named_slice_nests_under_its_parent(self, tmp_path: Path) -> None:
        assert gov.slice_cgroup_path("dev-background.slice", tmp_path) == (
            tmp_path / "dev.slice" / "dev-background.slice"
        )

    def test_single_segment_slice_sits_directly_under_the_root(self, tmp_path: Path) -> None:
        assert gov.slice_cgroup_path("wings.slice", tmp_path) == tmp_path / "wings.slice"

    def test_multi_dash_slice_nests_every_level(self, tmp_path: Path) -> None:
        assert gov.slice_cgroup_path("a-b-c.slice", tmp_path) == (
            tmp_path / "a.slice" / "a-b.slice" / "a-b-c.slice"
        )

    def test_default_root_is_the_real_cgroup2_mount_point(self) -> None:
        """The production default must be /sys/fs/cgroup — a wrong default here
        would silently walk the wrong tree on a real host, which no
        tmp_path-parameterised test would ever catch."""
        assert gov.CGROUP_ROOT == Path("/sys/fs/cgroup")
        assert gov.slice_cgroup_path("dev-background.slice") == (
            Path("/sys/fs/cgroup") / "dev.slice" / "dev-background.slice"
        )

    def test_non_slice_name_raises(self) -> None:
        with pytest.raises(ValueError, match="not a slice name"):
            gov.slice_cgroup_path("not-a-slice")


class TestEnumerateSliceChildren:
    """S15.22 — the downward walk CIU-95 filed as entirely missing."""

    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)

    @staticmethod
    def _build_tree(tmp_path: Path) -> Path:
        slice_dir = tmp_path / "dev.slice" / "dev-memory_min_guaranteed.slice"
        for name, value in (("docker-a.scope", "83886080"), ("docker-b.scope", "max")):
            child = slice_dir / name
            child.mkdir(parents=True)
            (child / "memory.min").write_text(value, encoding="utf-8")
        return slice_dir

    def test_lists_each_child_with_its_own_memory_min(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        children, note = gov.enumerate_slice_children(
            "dev-memory_min_guaranteed.slice", cgroup_root=tmp_path
        )
        assert children == [("docker-a.scope", 83886080), ("docker-b.scope", 0)]
        assert "docker-a.scope=83886080" in note

    def test_ignores_non_directory_entries(self, tmp_path: Path) -> None:
        """A slice directory is full of cgroup interface FILES (memory.min,
        cgroup.procs, ...) alongside its child cgroups; only the directories
        are occupants."""
        slice_dir = self._build_tree(tmp_path)
        (slice_dir / "memory.min").write_text("209715200", encoding="utf-8")
        (slice_dir / "cgroup.procs").write_text("", encoding="utf-8")
        children, _ = gov.enumerate_slice_children(
            "dev-memory_min_guaranteed.slice", cgroup_root=tmp_path
        )
        assert [name for name, _ in children] == ["docker-a.scope", "docker-b.scope"]

    def test_unreadable_memory_min_counts_as_zero_not_a_crash(self, tmp_path: Path) -> None:
        """A child cgroup with no memory.min file at all (the normal case for
        an occupant that declared nothing) must read as 0, definitively — this
        is the value the admission sum below depends on."""
        slice_dir = tmp_path / "dev.slice" / "dev-memory_min_guaranteed.slice"
        (slice_dir / "docker-c.scope").mkdir(parents=True)
        children, _ = gov.enumerate_slice_children(
            "dev-memory_min_guaranteed.slice", cgroup_root=tmp_path
        )
        assert children == [("docker-c.scope", 0)]

    def test_absent_slice_directory_is_a_definitive_empty_not_an_abstention(
        self, tmp_path: Path
    ) -> None:
        """An inactive slice has no cgroup directory at all — systemd removes
        it when the last occupant exits. That is a real 'zero occupants'
        answer, NOT the None abstention reserved for 'cannot see the host's
        tree'; conflating the two would make an empty guaranteed slice
        un-admittable."""
        children, note = gov.enumerate_slice_children(
            "dev-memory_min_guaranteed.slice", cgroup_root=tmp_path
        )
        assert children == []
        assert "not active" in note

    def test_not_host_rooted_abstains_without_touching_the_filesystem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A devcontainer's own /sys/fs/cgroup is its OWN namespace-local root,
        not a smaller view of the host's — walking it would enumerate the wrong
        tree and report it as the right one."""
        self._build_tree(tmp_path)
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: False)

        def fail_iterdir(self):
            raise AssertionError("no filesystem access may be attempted when not host-rooted")

        monkeypatch.setattr(Path, "iterdir", fail_iterdir)
        children, note = gov.enumerate_slice_children(
            "dev-memory_min_guaranteed.slice", cgroup_root=tmp_path
        )
        assert children is None
        assert "not PID 1" in note


class TestCheckMemoryRecursiveprot:
    """S15.22 — the host-wide mount flag CIU-95 filed: without it every
    declared mem_min on the host is inert while `systemctl show` keeps
    reporting the value that was set."""

    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)

    @staticmethod
    def _mounts(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
        real_read_text = Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/proc/mounts":
                return content
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", fake_read_text)

    _WITH_FLAG = (
        "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\n"
        "cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime,"
        "nsdelegate,memory_recursiveprot 0 0\n"
    )
    _WITHOUT_FLAG = (
        "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\n"
        "cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime,nsdelegate 0 0\n"
    )

    def test_flag_present_reports_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mounts(monkeypatch, self._WITH_FLAG)
        present, note = gov.check_memory_recursiveprot()
        assert present is True
        assert "/sys/fs/cgroup" in note

    def test_flag_absent_reports_false_with_the_options_it_did_see(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._mounts(monkeypatch, self._WITHOUT_FLAG)
        present, note = gov.check_memory_recursiveprot()
        assert present is False
        assert "WITHOUT memory_recursiveprot" in note
        assert "nsdelegate" in note

    def test_substring_lookalike_option_is_not_a_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Options are compared as whole comma-separated tokens: a naive
        `"memory_recursiveprot" in line` would pass on `nomemory_recursiveprot`
        and report a host as protected when it is not."""
        self._mounts(
            monkeypatch,
            "cgroup2 /sys/fs/cgroup cgroup2 rw,nsdelegate,nomemory_recursiveprot 0 0\n",
        )
        present, _ = gov.check_memory_recursiveprot()
        assert present is False

    def test_no_cgroup2_line_abstains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A pure cgroup-v1 host has no such flag to be missing — reporting
        False there would be a false accusation, not a finding."""
        self._mounts(
            monkeypatch,
            "cgroup /sys/fs/cgroup/memory cgroup rw,nosuid,memory 0 0\n",
        )
        present, note = gov.check_memory_recursiveprot()
        assert present is None
        assert "not a cgroup-v2 host" in note

    def test_not_host_rooted_abstains_without_reading_proc_mounts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: False)

        def fail_read_text(self, *a, **k):
            raise AssertionError("/proc/mounts must not be read when not host-rooted")

        monkeypatch.setattr(Path, "read_text", fail_read_text)
        present, note = gov.check_memory_recursiveprot()
        assert present is None
        assert "not PID 1" in note


# ===========================================================================
# S15.23 — admission control + per-container injection (CIU-94)
# ===========================================================================


class TestCheckMemMinAdmission:
    """S15.23 — CGROUP-NOTES.md's 'static ceiling + admission control' consumer
    half: sum the live occupants' own claims, refuse a candidate that would
    push the total past the slice's own provisioned ceiling."""

    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)

    @staticmethod
    def _build(tmp_path: Path, ceiling: str, occupants: dict[str, str]) -> Path:
        slice_dir = tmp_path / "dev.slice" / "dev-memory_min_guaranteed.slice"
        slice_dir.mkdir(parents=True)
        (slice_dir / "memory.min").write_text(ceiling, encoding="utf-8")
        for name, value in occupants.items():
            child = slice_dir / name
            child.mkdir()
            (child / "memory.min").write_text(value, encoding="utf-8")
        return slice_dir

    def test_no_ceiling_configured_abstains(self, tmp_path: Path) -> None:
        """An unset ceiling means the guarantee infrastructure was never
        provisioned. S15.16's ancestor-chain WARN already reports that from
        the declaration side — blocking a container over it here would be a
        second, worse answer to the same question."""
        self._build(tmp_path, "max", {"docker-a.scope": "83886080"})
        admit, note = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 8388608, cgroup_root=tmp_path
        )
        assert admit is None
        assert "no live memory.min of its own" in note

    def test_under_ceiling_admits(self, tmp_path: Path) -> None:
        self._build(tmp_path, "209715200", {"docker-a.scope": "83886080"})
        admit, note = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 83886080, cgroup_root=tmp_path
        )
        assert admit is True
        assert "admitted" in note

    def test_exactly_full_slice_still_admits(self, tmp_path: Path) -> None:
        """CONTROLLED WRONG IMPLEMENTATION guard: flipping the implementation's
        `<=` to `<` flips exactly this case from admit to reject. The ceiling
        is the total that MAY be guaranteed, not one byte less than it —
        mirroring check_slice_memory_min's own `>=` treatment of an
        exactly-equal floor (test_exactly_equal_is_adequate)."""
        self._build(
            tmp_path, "167772160",
            {"docker-a.scope": "83886080"},
        )
        admit, _ = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 83886080, cgroup_root=tmp_path
        )
        assert admit is True

    def test_one_byte_over_ceiling_refuses_with_a_full_breakdown(self, tmp_path: Path) -> None:
        self._build(tmp_path, "167772160", {"docker-a.scope": "83886080"})
        admit, note = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 83886081, cgroup_root=tmp_path
        )
        assert admit is False
        assert "ceiling=167772160" in note        # the ceiling
        assert "claimed=83886080" in note         # the live sum
        assert "candidate=83886081" in note       # the candidate
        assert "docker-a.scope=83886080" in note  # per-occupant breakdown

    def test_empty_slice_admits_a_candidate_that_fits_the_whole_ceiling(
        self, tmp_path: Path
    ) -> None:
        """An inactive slice enumerates as a definitive [] (not an
        abstention), so its full ceiling is available to the first claimant."""
        slice_dir = tmp_path / "dev.slice" / "dev-memory_min_guaranteed.slice"
        slice_dir.mkdir(parents=True)
        (slice_dir / "memory.min").write_text("167772160", encoding="utf-8")
        admit, _ = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 167772160, cgroup_root=tmp_path
        )
        assert admit is True

    def test_occupants_with_no_declared_claim_do_not_consume_the_ceiling(
        self, tmp_path: Path
    ) -> None:
        """A child with memory.min unset claims 0 as its own base value (the
        surplus it may still absorb under contention is the kernel's business,
        not an admission-time claim) — counting it as anything else would
        make the ceiling unreachable."""
        self._build(
            tmp_path, "167772160",
            {"docker-a.scope": "83886080", "docker-idle.scope": "max"},
        )
        admit, _ = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 83886080, cgroup_root=tmp_path
        )
        assert admit is True

    def test_not_host_rooted_abstains_before_reading_ceiling_or_enumerating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gating-order requirement, pinned. `_read_memory_min_bytes` does
        NOT gate on its own, so checking host-rootedness only AFTER reading
        the ceiling would silently read the devcontainer's OWN namespace-local
        cgroup tree and treat that as the host's answer."""
        self._build(tmp_path, "167772160", {"docker-a.scope": "83886080"})
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: False)

        def fail_read_text(self, *a, **k):
            raise AssertionError("the ceiling must not be read when not host-rooted")

        def fail_enumerate(*a, **k):
            raise AssertionError("children must not be enumerated when not host-rooted")

        monkeypatch.setattr(Path, "read_text", fail_read_text)
        monkeypatch.setattr(gov, "enumerate_slice_children", fail_enumerate)
        admit, note = gov.check_mem_min_admission(
            "dev-memory_min_guaranteed.slice", 83886080, cgroup_root=tmp_path
        )
        assert admit is None
        assert "not PID 1" in note


class TestContainerTransientScope:
    """S15.23 — the mdt-apply-dev-caps.sh cgroup-derivation, ported."""

    @staticmethod
    def _proc_cgroup(monkeypatch: pytest.MonkeyPatch, pid: int, content: str) -> None:
        real_read_text = Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == f"/proc/{pid}/cgroup":
                return content
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", fake_read_text)

    def test_returns_the_scope_unit_name_not_a_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`systemctl set-property` takes a UNIT NAME; handing it the cgroup
        path would fail on every real host."""
        self._proc_cgroup(
            monkeypatch, 4242,
            "0::/dev.slice/dev-memory_min_guaranteed.slice/docker-abc123.scope\n",
        )
        scope, note = gov.container_transient_scope(4242)
        assert scope == "docker-abc123.scope"
        assert "4242" in note

    def test_trims_buildkitd_style_nested_subcgroups_to_the_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """buildkitd nests its own sub-cgroups INSIDE the container's scope; a
        property set on the scope covers the whole subtree, and the nested path
        is not a unit systemd knows about."""
        self._proc_cgroup(
            monkeypatch, 77,
            "0::/dev.slice/docker-deadbeef.scope/buildkit/xyz\n",
        )
        scope, _ = gov.container_transient_scope(77)
        assert scope == "docker-deadbeef.scope"

    def test_ignores_cgroup_v1_controller_lines_and_uses_the_unified_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._proc_cgroup(
            monkeypatch, 9,
            "12:memory:/docker/notascope\n"
            "0::/dev.slice/docker-cafe01.scope\n",
        )
        scope, _ = gov.container_transient_scope(9)
        assert scope == "docker-cafe01.scope"

    def test_cgroup_v1_only_host_has_no_unified_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._proc_cgroup(monkeypatch, 5, "12:memory:/docker/abc\n11:cpu:/docker/abc\n")
        scope, note = gov.container_transient_scope(5)
        assert scope is None
        assert "no '0::' line" in note

    def test_no_scope_component_in_the_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._proc_cgroup(monkeypatch, 6, "0::/user.slice/user-1000.slice\n")
        scope, note = gov.container_transient_scope(6)
        assert scope is None
        assert "no .scope component" in note

    def test_nonpositive_pid_never_touches_proc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`docker inspect .State.Pid` reports 0 for a container that is not
        running; /proc/0/cgroup is not a thing to go looking for."""
        def fail_read_text(self, *a, **k):
            raise AssertionError("/proc must not be read for a non-positive pid")

        monkeypatch.setattr(Path, "read_text", fail_read_text)
        scope, note = gov.container_transient_scope(0)
        assert scope is None
        assert "not running" in note

    def test_vanished_process_is_reported_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A container can die between `docker inspect` and this call."""
        def fake_read_text(self, *a, **k):
            raise OSError("No such file or directory")

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        scope, note = gov.container_transient_scope(31337)
        assert scope is None
        assert "may have exited" in note


class TestSetScopeMemoryMin:
    """S15.23 — the ONE write CIU makes (docs/DESIGN-NOTES.md D9)."""

    @pytest.fixture(autouse=True)
    def _systemd_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: True)

    def test_not_host_rooted_never_calls_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """D9's narrowness, pinned: inside CIU's own devcontainer this is a
        silent no-op, not an attempted write. The pid the scope came from is a
        HOST pid-namespace number and is meaningless here anyway."""
        monkeypatch.setattr(gov, "_systemd_is_pid1", lambda: False)

        def fail_run(*a, **k):
            raise AssertionError("systemctl must not run when systemd is not PID 1")

        monkeypatch.setattr(gov.subprocess, "run", fail_run)
        applied, note = gov.set_scope_memory_min("docker-abc.scope", 83886080)
        assert applied is False
        assert "not PID 1" in note

    def test_success_uses_set_property_runtime_with_the_byte_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--runtime` is not decoration: systemd re-applies its own recorded
        properties to a scope on every daemon-reload, so a raw cgroupfs write
        would be silently wiped by an unrelated `apt install`."""
        seen: list[list[str]] = []

        def fake_run(cmd, **k):
            seen.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        applied, note = gov.set_scope_memory_min("docker-abc.scope", 83886080)
        assert applied is True
        assert seen == [[
            "systemctl", "set-property", "--runtime",
            "docker-abc.scope", "MemoryMin=83886080",
        ]]
        assert "83886080" in note

    def test_nonzero_exit_surfaces_stderr_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **k):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="",
                stderr="Failed to set unit properties: Access denied\n",
            )

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        applied, note = gov.set_scope_memory_min("docker-abc.scope", 83886080)
        assert applied is False
        assert "Access denied" in note
        assert "exited 1" in note

    def test_subprocess_failure_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **k):
            raise OSError("systemctl vanished")

        monkeypatch.setattr(gov.subprocess, "run", fake_run)
        applied, note = gov.set_scope_memory_min("docker-abc.scope", 1)
        assert applied is False
        assert "systemctl vanished" in note
