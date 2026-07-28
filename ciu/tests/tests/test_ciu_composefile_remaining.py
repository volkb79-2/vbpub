"""Remaining behavioural contracts for CIU compose rendering.

These cases deliberately use only temporary compose/config templates: they
exercise parser and rendering boundaries without Docker, a daemon, or a real
secret backend.
"""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from jinja2 import TemplateError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.composefile import (  # noqa: E402
    ConfigFileMount,
    SecretGuard,
    SecretLeakError,
    compose_process_env,
    generate_overlay,
    guard_config,
    leak_scan,
    render_compose,
    render_configfiles,
    validate_consumption,
)
from ciu import governance as governance_mod  # noqa: E402
from ciu.secrets.directives import SecretSpec  # noqa: E402


@dataclass
class Materialized:
    spec: SecretSpec
    value: object | None
    file: Path


def spec(name: str, **kwargs: object) -> SecretSpec:
    return SecretSpec(
        name=name,
        kind="GEN_LOCAL",
        locator="x",
        table_path="app.secrets",
        expose_env=kwargs.get("expose_env"),
        consumed_by=None,
    )


class TestSecretAndTemplateBoundaries:
    def test_autoescape_and_bytes_secret_materialization_are_rejected(self) -> None:
        guard = SecretGuard("token")
        with pytest.raises(SecretLeakError, match="token"):
            guard.__html__()
        with pytest.raises(SecretLeakError, match="token"):
            bytes(guard)
        assert guard != object()
        assert hash(guard) == hash(SecretGuard("token"))

    def test_partial_config_does_not_invent_a_secret_entry(self) -> None:
        original = {"app": {"name": "api"}}
        guarded = guard_config(original, [spec("missing")])
        assert guarded == original
        assert guarded is not original

    def test_template_syntax_error_names_the_source_file(self, tmp_path: Path) -> None:
        template = tmp_path / "broken.compose.yml.j2"
        template.write_text("{{ unterminated", encoding="utf-8")
        with pytest.raises(TemplateError, match="broken.compose.yml.j2"):
            render_compose(template, {"app": {}})

    def test_non_string_materialized_value_is_scanned_without_disclosing_it(self, tmp_path: Path) -> None:
        value = 12345678
        materialized = {"pin": Materialized(spec("pin"), value, tmp_path / "pin")}
        with pytest.raises(SecretLeakError) as raised:
            leak_scan(f"pin={value}", materialized)
        assert "pin" in str(raised.value)
        assert str(value) not in str(raised.value)


class TestConsumptionAndConfigfileBoundaries:
    def test_malformed_compose_entries_do_not_count_as_secret_consumption(self) -> None:
        yaml_text = "services:\n  api:\n    secrets: [{source: 7}, {target: ignored}]\n"
        assert validate_consumption(yaml_text, {"declared"}) == ["declared"]

    def test_configfile_and_hook_unknown_secret_are_each_rejected(self, tmp_path: Path) -> None:
        mount = ConfigFileMount("api", "cfg", tmp_path / "cfg", "/etc/app/cfg", consumed_secrets=("ghost",))
        with pytest.raises(ValueError, match=r"configfile 'api.cfg'.*ghost"):
            validate_consumption("services: {}\n", set(), configfile_mounts=[mount])
        with pytest.raises(ValueError, match=r"hook consumption marker.*ghost"):
            validate_consumption("services: {}\n", set(), hook_consumed=["ghost"])

    def test_non_mapping_root_has_no_configfile_contract(self, tmp_path: Path) -> None:
        assert render_configfiles(tmp_path, "app", {"app": "not-a-table"}, lambda _: "x") == []

    @pytest.mark.parametrize(
        ("cfg", "message"),
        [
            ("not-a-table", "must be a table"),
            ({"target": "/etc/app/cfg"}, "missing a `template`"),
            ({"template": "cfg.j2"}, "missing a `target`"),
        ],
    )
    def test_configfile_shape_errors_prevent_uncontrolled_render(
        self, tmp_path: Path, cfg: object, message: str
    ) -> None:
        config = {"app": {"api": {"configfile": {"main": cfg}}}}
        with pytest.raises(ValueError, match=message):
            render_configfiles(tmp_path, "app", config, lambda _: "secret")

    def test_relative_target_is_rejected_before_a_host_file_is_written(self, tmp_path: Path) -> None:
        (tmp_path / "cfg.j2").write_text("safe", encoding="utf-8")
        config = {"app": {"api": {"configfile": {"main": {"template": "cfg.j2", "target": "etc/app/cfg"}}}}}
        with pytest.raises(ValueError, match="non-absolute"):
            render_configfiles(tmp_path, "app", config, lambda _: "secret")
        assert not (tmp_path / ".ciu").exists()

    def test_rerender_removes_stale_symlink_before_directory_mount(self, tmp_path: Path) -> None:
        (tmp_path / "cfg.j2").write_text("new config", encoding="utf-8")
        config = {"app": {"api": {"configfile": {"main": {"template": "cfg.j2", "target": "/etc/app/cfg"}}}}}
        staging = tmp_path / ".ciu" / "rendered" / "api" / "etc" / "app"
        staging.mkdir(parents=True)
        (staging / "stale-link").symlink_to(tmp_path / "nonexistent")
        mounts = render_configfiles(tmp_path, "app", config, lambda _: "secret")
        assert [mount.rendered_path.name for mount in mounts] == ["cfg"]
        assert not (staging / "stale-link").is_symlink()
        assert stat.S_IMODE(mounts[0].rendered_path.stat().st_mode) == 0o440


class TestOverlayAndEnvironmentBoundaries:
    def test_service_less_compose_preserves_explicit_compatibility_selector(self, tmp_path: Path) -> None:
        rendered = tmp_path / "rendered" / "cfg"
        rendered.parent.mkdir()
        rendered.write_text("cfg", encoding="utf-8")
        mount = ConfigFileMount("api", "cfg", rendered, "/etc/app/cfg")
        overlay = generate_overlay(
            tmp_path, {}, [mount], compose_yaml_text="services: []\n",
            repo_root=tmp_path, physical_root=tmp_path,
        )
        assert overlay is not None
        text = overlay.read_text(encoding="utf-8")
        assert "api:" in text  # compatibility fallback remains explicit
        assert "/etc/app" in text

    def test_ksm_governance_resolves_relative_shim_to_physical_overlay_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S15.11 keeps a repo-relative shim a physical Docker bind source."""
        repo = tmp_path / "logical"
        physical = tmp_path / "physical"
        repo.mkdir()
        physical.mkdir()
        stack = repo / "stack"
        stack.mkdir()
        shim = repo / "hooks" / "ksm-optin.so"
        shim.parent.mkdir()
        shim.write_bytes(b"not-loaded-by-test")
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "")
        overlay = generate_overlay(
            stack, {}, [],
            compose_yaml_text="services:\n  api: {image: example}\n",
            governance={"enabled": True, "ksm_optin": "hooks/ksm-optin.so"},
            repo_root=repo, physical_root=physical,
        )
        assert overlay is not None
        rendered = overlay.read_text(encoding="utf-8")
        assert str(physical / "hooks" / "ksm-optin.so") in rendered
        assert "LD_PRELOAD=/opt/ksm/ksm-optin.so" in rendered

    def test_profiles_filter_empty_values_and_missing_or_none_exposed_secret_is_not_added(self, tmp_path: Path) -> None:
        token = spec("token", expose_env="TOKEN")
        absent = spec("absent", expose_env="ABSENT")
        env = compose_process_env(
            [token, absent],
            {"token": Materialized(token, 42, tmp_path / "token")},
            base={"KEEP": "yes"},
            compose_profiles=["web", "", "worker"],
        )
        assert env["COMPOSE_PROFILES"] == "web,worker"
        assert env["TOKEN"] == "42"
        assert "ABSENT" not in env
        assert env["KEEP"] == "yes"
        assert env["PWD"] == os.getcwd()
