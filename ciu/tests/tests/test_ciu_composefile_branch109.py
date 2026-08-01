from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import governance as governance_mod  # noqa: E402
from ciu.composefile import generate_overlay, render_configfiles, validate_consumption  # noqa: E402


class TestComposefileBranchCoverage109:
    @pytest.mark.parametrize(
        "compose_yaml",
        ["- not a compose mapping\n", "services: not a mapping\n"],
    )
    def test_non_mapping_service_shapes_leave_all_secrets_unconsumed(
        self, compose_yaml: str
    ) -> None:
        assert validate_consumption(compose_yaml, {"alpha", "beta"}) == ["alpha", "beta"]

    def test_configfile_staging_clears_stale_files_but_keeps_nested_directories(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "current.j2").write_text("rendered", encoding="utf-8")
        config = {
            "app": {
                "api": {
                    "configfile": {
                        "current": {
                            "template": "current.j2",
                            "target": "/etc/app/config.toml",
                        }
                    }
                }
            }
        }
        staging = tmp_path / ".ciu" / "rendered" / "api" / "etc" / "app"
        staging.mkdir(parents=True)
        nested = staging / "stale-directory"
        nested.mkdir()
        (nested / "retained.txt").write_text("keep", encoding="utf-8")
        (staging / "stale.txt").write_text("remove", encoding="utf-8")
        (staging / "stale-link").symlink_to(tmp_path / "missing")

        mounts = render_configfiles(tmp_path, "app", config, lambda _: "unused")

        rendered = staging / "config.toml"
        assert [mount.rendered_path for mount in mounts] == [rendered]
        assert rendered.read_text(encoding="utf-8") == "rendered"
        assert stat.S_IMODE(rendered.stat().st_mode) == 0o440
        assert nested.is_dir()
        assert (nested / "retained.txt").read_text(encoding="utf-8") == "keep"
        assert not (staging / "stale.txt").exists()
        assert not (staging / "stale-link").is_symlink()
        assert not (staging / "config.toml.tmp").exists()

    def test_absolute_governance_ksm_path_is_preserved_in_overlay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "")
        absolute_shim = "/outside/ksm-optin.so"
        overlay = generate_overlay(
            tmp_path / "stack",
            {},
            [],
            compose_yaml_text="services:\n  api: {image: example}\n",
            governance={"enabled": True, "ksm_optin": absolute_shim},
            repo_root=tmp_path / "logical",
            physical_root=tmp_path / "physical",
        )

        assert overlay is not None
        service = yaml.safe_load(overlay.read_text(encoding="utf-8"))["services"]["api"]
        assert service["volumes"][0]["source"] == absolute_shim
        assert service["environment"] == ["LD_PRELOAD=/opt/ksm/ksm-optin.so"]
