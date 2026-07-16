"""Tests for src/ciu/composefile.py — CIU v2 compose rendering & leak prevention.

Normative contract: docs/SPEC.md §S4.17–S4.23, §S5, §S8.1–S8.2, §S1.3–S1.4.
Each test documents which spec requirement it exercises.

These tests use a minimal ``MaterializedLike`` stub (dataclass with
``spec``/``name``/``value``/``file``) and DO NOT import the parallel P4
materialize module — composefile.py duck-types on ``.value`` / ``.file`` /
``.spec`` only. ``materialized`` is consumed as ``dict[name, MaterializedLike]``.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.composefile import (  # noqa: E402
    ConfigFileMount,
    SecretGuard,
    SecretLeakError,
    compose_file_args,
    compose_process_env,
    generate_overlay,
    guard_config,
    leak_scan,
    redact_config,
    render_compose,
    render_configfiles,
    validate_consumption,
)
from ciu import config_model  # noqa: E402
from ciu import governance as governance_mod  # noqa: E402
from ciu.config_model import render_jinja2_text  # noqa: E402
from ciu.secrets.directives import SecretSpec, discover  # noqa: E402


# ---------------------------------------------------------------------------
# MaterializedLike stub — the interface composefile.py consumes (P4 output).
# Expected shape: obj.value: str|None, obj.file: Path, obj.spec: SecretSpec.
# ---------------------------------------------------------------------------

@dataclass
class MaterializedLike:
    spec: SecretSpec
    name: str
    value: str | None
    file: Path


def _spec(name: str, table_path: str = "app.secrets", **kw) -> SecretSpec:
    return SecretSpec(
        name=name,
        kind=kw.get("kind", "GEN_LOCAL"),
        locator=kw.get("locator", "x"),
        expose_env=kw.get("expose_env"),
        consumed_by=kw.get("consumed_by"),
        table_path=table_path,
    )


def _mat(name: str, value: str | None, file: Path, **kw) -> MaterializedLike:
    return MaterializedLike(spec=_spec(name, **kw), name=name, value=value, file=file)


# ---------------------------------------------------------------------------
# S4.21 — SecretGuard: every stringification path aborts
# ---------------------------------------------------------------------------

class TestSecretGuardStringification:
    """S4.21 — a guard is impossible to materialize as text."""

    def test_str_raises_naming_secret_s4_21(self) -> None:
        g = SecretGuard("db_password")
        with pytest.raises(SecretLeakError, match="db_password"):
            str(g)

    def test_repr_raises_s4_21(self) -> None:
        g = SecretGuard("db_password")
        with pytest.raises(SecretLeakError, match="db_password"):
            repr(g)

    def test_format_raises_s4_21(self) -> None:
        g = SecretGuard("db_password")
        with pytest.raises(SecretLeakError, match="db_password"):
            format(g, "")

    def test_fstring_raises_s4_21(self) -> None:
        g = SecretGuard("db_password")
        with pytest.raises(SecretLeakError, match="db_password"):
            _ = f"{g}"

    def test_str_format_method_raises_s4_21(self) -> None:
        g = SecretGuard("db_password")
        with pytest.raises(SecretLeakError, match="db_password"):
            _ = "{}".format(g)

    def test_eq_against_str_raises_s4_21(self) -> None:
        """Comparing a guard to a str must abort (probing-via-compare leak)."""
        g = SecretGuard("db_password")
        with pytest.raises(SecretLeakError, match="db_password"):
            _ = (g == "guess")

    def test_message_points_to_run_secrets_s4_21(self) -> None:
        g = SecretGuard("pw")
        with pytest.raises(SecretLeakError) as ei:
            str(g)
        msg = str(ei.value)
        assert "secrets: [pw]" in msg
        assert "/run/secrets/pw" in msg

    def test_guard_vs_guard_equality_does_not_leak(self) -> None:
        """Guard==Guard compares names (usable as dict keys), never raises."""
        assert SecretGuard("a") == SecretGuard("a")
        assert SecretGuard("a") != SecretGuard("b")


class TestSecretGuardInJinja2:
    """S4.21 — the REAL Jinja2 render path (autoescape off) hits __str__."""

    def test_jinja_output_of_guard_raises_s4_21(self) -> None:
        """{{ app.secrets.pw }} against guarded config aborts naming the secret.

        This exercises the actual render path used by render_compose /
        render_configfiles: render_jinja2_text -> jinja2.Template(...).render.
        Jinja2 calls __str__ on the output value (confirmed: not __repr__,
        not __format__).
        """
        config = {"app": {"secrets": {"pw": "GEN_LOCAL:app/pw"}}}
        specs = discover("app", config)
        guarded = guard_config(config, specs)
        with pytest.raises(SecretLeakError, match="pw"):
            render_jinja2_text("{{ app.secrets.pw }}", guarded)

    def test_jinja_concat_of_guard_raises_s4_21(self) -> None:
        """The ~ concat operator also routes through __str__ -> aborts."""
        config = {"app": {"secrets": {"pw": "GEN_LOCAL:app/pw"}}}
        guarded = guard_config(config, discover("app", config))
        with pytest.raises(SecretLeakError, match="pw"):
            render_jinja2_text('prefix-{{ app.secrets.pw ~ "y" }}', guarded)


# ---------------------------------------------------------------------------
# S4.21/S4.23 — guard_config vs redact_config
# ---------------------------------------------------------------------------

class TestGuardAndRedact:
    def _config(self) -> dict:
        return {
            "app": {
                "name": "myapp",
                "settings": {"log_level": "INFO"},
                "secrets": {
                    "pw": "GEN_LOCAL:app/pw",
                    "tok": {"directive": "GEN_EPHEMERAL"},
                },
            }
        }

    def test_guard_config_leaves_non_secret_readable_s4_21(self) -> None:
        config = self._config()
        specs = discover("app", config)
        guarded = guard_config(config, specs)
        # Non-secret values remain plain and readable.
        assert guarded["app"]["name"] == "myapp"
        assert guarded["app"]["settings"]["log_level"] == "INFO"
        # Secret entries are guards now.
        assert isinstance(guarded["app"]["secrets"]["pw"], SecretGuard)
        assert isinstance(guarded["app"]["secrets"]["tok"], SecretGuard)

    def test_guard_config_does_not_mutate_input_s4_21(self) -> None:
        config = self._config()
        guard_config(config, discover("app", config))
        # Original directive strings/tables untouched (deep copy).
        assert config["app"]["secrets"]["pw"] == "GEN_LOCAL:app/pw"
        assert config["app"]["secrets"]["tok"] == {"directive": "GEN_EPHEMERAL"}

    def test_redact_config_yields_label_s4_23(self) -> None:
        config = self._config()
        specs = discover("app", config)
        redacted = redact_config(config, specs)
        assert redacted["app"]["secrets"]["pw"] == "<secret:pw>"
        assert redacted["app"]["secrets"]["tok"] == "<secret:tok>"
        # Non-secret untouched.
        assert redacted["app"]["name"] == "myapp"


# ---------------------------------------------------------------------------
# S4.22 — leak_scan
# ---------------------------------------------------------------------------

class TestLeakScan:
    def test_planted_value_raises_naming_secret_s4_22(self, tmp_path: Path) -> None:
        value = "ABCDEF123456"  # 12 chars >= 8
        mats = {"pw": _mat("pw", value, tmp_path / "pw")}
        text = f"environment:\n  FOO={value}\n"
        with pytest.raises(SecretLeakError) as ei:
            leak_scan(text, mats)
        assert "pw" in str(ei.value)

    def test_value_not_in_exception_message_s4_22(self, tmp_path: Path) -> None:
        """The exception names the secret but never prints the value."""
        value = "SUPERSECRETVALUE99"
        mats = {"pw": _mat("pw", value, tmp_path / "pw")}
        with pytest.raises(SecretLeakError) as ei:
            leak_scan(f"x={value}", mats)
        assert value not in str(ei.value)

    def test_short_value_ignored_s4_22(self, tmp_path: Path) -> None:
        """A 7-char value is below the len>=8 threshold and is not scanned."""
        value = "abc1234"  # 7 chars
        mats = {"pw": _mat("pw", value, tmp_path / "pw")}
        leak_scan(f"token={value}", mats)  # must NOT raise

    def test_none_value_ignored_s4_22(self, tmp_path: Path) -> None:
        """ASK_FILE-style secret with value None is skipped."""
        mats = {"crt": _mat("crt", None, tmp_path / "crt", kind="ASK_FILE")}
        leak_scan("anything here", mats)  # must NOT raise

    def test_no_hit_passes_s4_22(self, tmp_path: Path) -> None:
        mats = {"pw": _mat("pw", "LONGSECRET12345", tmp_path / "pw")}
        leak_scan("nothing sensitive here", mats)  # must NOT raise


# ---------------------------------------------------------------------------
# S4.20 — validate_consumption
# ---------------------------------------------------------------------------

class TestValidateConsumption:
    def test_short_form_recognized_s4_20(self) -> None:
        yaml_text = """
services:
  api:
    secrets: [db_password]
"""
        unconsumed = validate_consumption(yaml_text, {"db_password"})
        assert unconsumed == []

    def test_long_form_recognized_s4_20(self) -> None:
        yaml_text = """
services:
  api:
    secrets:
      - source: db_password
        target: /run/secrets/db_password
"""
        unconsumed = validate_consumption(yaml_text, {"db_password"})
        assert unconsumed == []

    def test_undeclared_reference_aborts_s4_20(self) -> None:
        yaml_text = """
services:
  api:
    secrets: [ghost]
"""
        with pytest.raises(ValueError, match=r"\[S4\.20\].*ghost"):
            validate_consumption(yaml_text, {"db_password"})

    def test_unconsumed_returned_s4_20(self) -> None:
        yaml_text = """
services:
  api:
    secrets: [used]
"""
        unconsumed = validate_consumption(yaml_text, {"used", "unused_a", "unused_b"})
        assert unconsumed == ["unused_a", "unused_b"]

    def test_mixed_short_and_long_across_services_s4_20(self) -> None:
        yaml_text = """
services:
  api:
    secrets: [a]
  worker:
    secrets:
      - source: b
"""
        unconsumed = validate_consumption(yaml_text, {"a", "b", "c"})
        assert unconsumed == ["c"]

    def test_configfile_consumption_counts_s4_20(self, tmp_path: Path) -> None:
        mount = ConfigFileMount(
            service="api",
            name="main",
            rendered_path=tmp_path / "main",
            target="/etc/app/config.toml",
            consumed_secrets=("db_password",),
        )
        yaml_text = "services:\n  api:\n    image: app\n"
        unconsumed = validate_consumption(
            yaml_text,
            {"db_password", "unused"},
            configfile_mounts=[mount],
        )
        assert unconsumed == ["unused"]

    def test_hook_consumption_marker_counts_s4_20(self) -> None:
        yaml_text = "services:\n  api:\n    image: app\n"
        unconsumed = validate_consumption(
            yaml_text,
            {"bootstrap_token", "unused"},
            hook_consumed={"bootstrap_token"},
        )
        assert unconsumed == ["unused"]


# ---------------------------------------------------------------------------
# S5 — render_configfiles
# ---------------------------------------------------------------------------

class TestRenderConfigfiles:
    def _setup(self, tmp_path: Path, template_body: str) -> dict:
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "config.toml.j2").write_text(template_body, encoding="utf-8")
        config = {
            "app": {
                "name": "myapp",
                "database": {"host": "db", "port": 5432, "user": "admin"},
                "secrets": {"pw": "ASK_VAULT:secret/data/db"},
                "svc": {
                    "configfile": {
                        "main": {
                            "template": "config.toml.j2",
                            "target": "/etc/app/config.toml",
                        }
                    }
                },
            }
        }
        return config, stack

    def test_secret_fn_interpolates_real_value_s5_4(self, tmp_path: Path) -> None:
        """secret('pw') yields the real resolved value in the rendered file."""
        body = 'dsn = "postgres://{{ app.database.user }}:{{ secret(\'pw\') }}@{{ app.database.host }}"\n'
        config, stack = self._setup(tmp_path, body)

        def secret_value_fn(name: str) -> str:
            return {"pw": "S3cretValue"}[name]

        mounts = render_configfiles(stack, "app", config, secret_value_fn)
        assert len(mounts) == 1
        assert mounts[0].consumed_secrets == ("pw",)
        rendered = mounts[0].rendered_path.read_text()
        assert 'dsn = "postgres://admin:S3cretValue@db"' in rendered

    def test_direct_secret_access_in_template_raises_guarded_s5_4(self, tmp_path: Path) -> None:
        """{{ app.secrets.pw }} inside a configfile template still aborts (S4.21)."""
        body = 'pw = "{{ app.secrets.pw }}"\n'
        config, stack = self._setup(tmp_path, body)
        with pytest.raises(SecretLeakError, match="pw"):
            render_configfiles(stack, "app", config, lambda n: "v")

    def test_unknown_secret_name_aborts_s5_4(self, tmp_path: Path) -> None:
        body = 'x = "{{ secret(\'nope\') }}"\n'
        config, stack = self._setup(tmp_path, body)
        with pytest.raises(ValueError, match=r"\[S5\.4\].*nope"):
            render_configfiles(stack, "app", config, lambda n: "v")

    def test_rendered_file_mode_0440_s5_1(self, tmp_path: Path) -> None:
        body = 'log = "{{ app.name }}"\n'
        config, stack = self._setup(tmp_path, body)
        mounts = render_configfiles(stack, "app", config, lambda n: "v")
        mode = mounts[0].rendered_path.stat().st_mode & 0o777
        assert mode == 0o440

    def test_rendered_path_under_ciu_rendered_service_s5_2(self, tmp_path: Path) -> None:
        body = 'log = "{{ app.name }}"\n'
        config, stack = self._setup(tmp_path, body)
        mounts = render_configfiles(stack, "app", config, lambda n: "v")
        rel = mounts[0].rendered_path.relative_to(stack)
        # S5.3a: mirrors the target's own directory structure (minus the
        # leading '/') and basename under .ciu/rendered/<service>/, NOT
        # named after cfg_name — so the parent dir can be bind-mounted
        # whole (see TestGenerateOverlay's configfile tests).
        assert rel == Path(".ciu") / "rendered" / "svc" / "etc" / "app" / "config.toml"
        assert mounts[0].target == "/etc/app/config.toml"
        assert mounts[0].service == "svc"
        assert mounts[0].name == "main"

    def test_stale_file_removed_when_target_changes_s5_3a(self, tmp_path: Path) -> None:
        """A file left behind by a PRIOR render (since-changed target/cfg)
        must not persist in the staging directory — it would otherwise be
        exposed into the container by the directory-level mount (S5.3a)."""
        body = 'log = "{{ app.name }}"\n'
        config, stack = self._setup(tmp_path, body)
        render_configfiles(stack, "app", config, lambda n: "v")
        staging_dir = stack / ".ciu" / "rendered" / "svc" / "etc" / "app"
        assert (staging_dir / "config.toml").exists()

        # Simulate a stray leftover from an earlier, since-abandoned target
        # in the SAME staging directory (e.g. a renamed target basename).
        stray = staging_dir / "old-config.toml"
        stray.write_text("stale", encoding="utf-8")

        render_configfiles(stack, "app", config, lambda n: "v")
        assert not stray.exists(), "stale file must be cleared on re-render"
        assert (staging_dir / "config.toml").exists()

    def test_two_configfiles_same_target_dir_share_staging_dir_s5_3a(
        self, tmp_path: Path
    ) -> None:
        """Two configfiles for one service targeting the same directory
        render into the SAME staging directory (so one directory mount
        covers both — see TestGenerateOverlay)."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "a.toml.j2").write_text("a\n", encoding="utf-8")
        (stack / "b.toml.j2").write_text("b\n", encoding="utf-8")
        config = {
            "app": {
                "svc": {
                    "configfile": {
                        "one": {"template": "a.toml.j2", "target": "/etc/app/a.toml"},
                        "two": {"template": "b.toml.j2", "target": "/etc/app/b.toml"},
                    }
                }
            }
        }
        mounts = render_configfiles(stack, "app", config, lambda n: "v")
        assert len(mounts) == 2
        assert {m.rendered_path.parent for m in mounts} == {
            stack / ".ciu" / "rendered" / "svc" / "etc" / "app"
        }

    def test_custom_mode_honored_s5_1(self, tmp_path: Path) -> None:
        body = 'log = "{{ app.name }}"\n'
        config, stack = self._setup(tmp_path, body)
        config["app"]["svc"]["configfile"]["main"]["mode"] = "0444"
        mounts = render_configfiles(stack, "app", config, lambda n: "v")
        assert mounts[0].mode == "0444"
        assert (mounts[0].rendered_path.stat().st_mode & 0o777) == 0o444

    def test_missing_template_file_aborts_s5_1(self, tmp_path: Path) -> None:
        config, stack = self._setup(tmp_path, "x")
        config["app"]["svc"]["configfile"]["main"]["template"] = "absent.j2"
        with pytest.raises(FileNotFoundError):
            render_configfiles(stack, "app", config, lambda n: "v")

    def test_no_configfiles_returns_empty(self, tmp_path: Path) -> None:
        stack = tmp_path / "s"
        stack.mkdir()
        config = {"app": {"name": "x"}}
        assert render_configfiles(stack, "app", config, lambda n: "v") == []


# ---------------------------------------------------------------------------
# S4.17 / S1.4 / S8.1 — generate_overlay
# ---------------------------------------------------------------------------

class TestGenerateOverlay:
    def test_secrets_shape_with_physical_remap_s4_17(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Overlay secrets carry PHYSICAL paths (S1.4 remap of the file path)."""
        import yaml

        repo = tmp_path / "repo"
        physical = tmp_path / "host_repo"
        repo.mkdir()
        physical.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical))

        stack = repo / "infra" / "redis-core"
        stack.mkdir(parents=True)
        secret_file = stack / ".ciu" / "secrets" / "pw"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("value", encoding="utf-8")

        mats = {"pw": _mat("pw", "value", secret_file)}
        path = generate_overlay(stack, mats, [])
        assert path == stack / ".ciu" / "ciu.compose.overlay.yml"

        doc = yaml.safe_load(path.read_text())
        file_str = doc["secrets"]["pw"]["file"]
        # Physical prefix asserted: file path is under PHYSICAL_REPO_ROOT.
        assert file_str.startswith(str(physical))
        assert file_str == str(physical / "infra" / "redis-core" / ".ciu" / "secrets" / "pw")
        assert "services" not in doc

    def test_configfile_volume_entry_physical_s4_17(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S5.3a: the mount binds the STAGING DIRECTORY over the target's
        parent directory — not the rendered file over the target file."""
        import yaml

        repo = tmp_path / "repo"
        physical = tmp_path / "host_repo"
        repo.mkdir()
        physical.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical))

        stack = repo / "apps" / "controller"
        stack.mkdir(parents=True)
        # Mirrors what render_configfiles actually produces (S5.3a): the
        # rendered file lives under a directory that mirrors the target's
        # own parent path, named after the target's basename.
        rendered = stack / ".ciu" / "rendered" / "ctl" / "etc" / "controller" / "config.toml"
        rendered.parent.mkdir(parents=True)
        rendered.write_text("cfg", encoding="utf-8")

        mount = ConfigFileMount(
            service="ctl",
            name="main",
            rendered_path=rendered,
            target="/etc/controller/config.toml",
        )
        path = generate_overlay(stack, {}, [mount])
        doc = yaml.safe_load(path.read_text())
        vols = doc["services"]["ctl"]["volumes"]
        phys = str(physical / "apps" / "controller" / ".ciu" / "rendered" / "ctl" / "etc" / "controller")
        # Long-form mount object (colon/space-safe; R2 finding F4). Directory
        # source/target (S5.3a), not the file itself.
        assert vols == [{
            "type": "bind",
            "source": phys,
            "target": "/etc/controller",
            "read_only": True,
        }]
        assert "secrets" not in doc

    def test_two_configfiles_same_target_dir_consolidate_to_one_mount_s5_3a(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two configfiles for one service, same target directory, produce
        ONE directory mount, not two (S5.3a)."""
        import yaml

        repo = tmp_path / "repo"
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))

        stack = repo / "apps" / "controller"
        staging_dir = stack / ".ciu" / "rendered" / "ctl" / "etc" / "controller"
        staging_dir.mkdir(parents=True)
        (staging_dir / "a.toml").write_text("a", encoding="utf-8")
        (staging_dir / "b.toml").write_text("b", encoding="utf-8")

        mounts = [
            ConfigFileMount(
                service="ctl", name="one",
                rendered_path=staging_dir / "a.toml", target="/etc/controller/a.toml",
            ),
            ConfigFileMount(
                service="ctl", name="two",
                rendered_path=staging_dir / "b.toml", target="/etc/controller/b.toml",
            ),
        ]
        path = generate_overlay(stack, {}, mounts)
        doc = yaml.safe_load(path.read_text())
        vols = doc["services"]["ctl"]["volumes"]
        assert len(vols) == 1
        assert vols[0]["target"] == "/etc/controller"

    def test_configfile_base_service_fans_out_to_instances_s5_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CIU-2: a base configfile section mounts to worker-1/worker-2."""
        import yaml

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))

        stack = repo / "apps" / "workers"
        stack.mkdir(parents=True)
        rendered = stack / ".ciu" / "rendered" / "worker" / "main"
        rendered.parent.mkdir(parents=True)
        rendered.write_text("cfg", encoding="utf-8")

        mount = ConfigFileMount(
            service="worker",
            name="main",
            rendered_path=rendered,
            target="/etc/worker/config.toml",
        )
        compose_yaml = """
services:
  worker-2:
    image: worker
  worker-1:
    image: worker
  api:
    image: api
"""
        path = generate_overlay(stack, {}, [mount], compose_yaml_text=compose_yaml)
        doc = yaml.safe_load(path.read_text())
        assert sorted(doc["services"]) == ["worker-1", "worker-2"]
        assert doc["services"]["worker-1"]["volumes"] == doc["services"]["worker-2"]["volumes"]

    def test_configfile_exact_service_wins_over_instance_fanout_s5_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))

        stack = repo / "apps" / "workers"
        stack.mkdir(parents=True)
        rendered = stack / ".ciu" / "rendered" / "worker" / "main"
        rendered.parent.mkdir(parents=True)
        rendered.write_text("cfg", encoding="utf-8")

        mount = ConfigFileMount(
            service="worker",
            name="main",
            rendered_path=rendered,
            target="/etc/worker/config.toml",
        )
        compose_yaml = """
services:
  worker:
    image: worker
  worker-1:
    image: worker
"""
        path = generate_overlay(stack, {}, [mount], compose_yaml_text=compose_yaml)
        doc = yaml.safe_load(path.read_text())
        assert sorted(doc["services"]) == ["worker"]

    def test_configfile_phantom_selector_warns_s5_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """CIU-2: a selector matching neither an exact key nor any instance warns."""
        import yaml

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))

        stack = repo / "apps" / "workers"
        stack.mkdir(parents=True)
        rendered = stack / ".ciu" / "rendered" / "worker" / "main"
        rendered.parent.mkdir(parents=True)
        rendered.write_text("cfg", encoding="utf-8")

        mount = ConfigFileMount(
            service="worker",
            name="main",
            rendered_path=rendered,
            target="/etc/worker/config.toml",
        )
        # Compose has only 'api' — no 'worker' key and no 'worker-<N>' instances.
        compose_yaml = "services:\n  api:\n    image: api\n"
        path = generate_overlay(stack, {}, [mount], compose_yaml_text=compose_yaml)
        out = capsys.readouterr().out
        assert "[WARN]" in out and "phantom service" in out and "worker" in out
        # Selector is preserved (so compose surfaces the bad name), not dropped.
        doc = yaml.safe_load(path.read_text())
        assert sorted(doc["services"]) == ["worker"]

    def test_returns_none_when_nothing_to_wire_s8_1(self, tmp_path: Path) -> None:
        stack = tmp_path / "stack"
        stack.mkdir()
        assert generate_overlay(stack, {}, []) is None
        # No overlay file is written.
        assert not (stack / ".ciu" / "ciu.compose.overlay.yml").exists()

    def test_no_secret_values_in_overlay_text_s4_22(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The overlay text contains only paths — never any value string."""
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))  # native identity

        stack = repo / "s"
        stack.mkdir()
        sf = stack / ".ciu" / "secrets" / "pw"
        sf.parent.mkdir(parents=True)
        secret_value = "THE-PLAINTEXT-SECRET-VALUE"
        sf.write_text(secret_value, encoding="utf-8")

        mats = {"pw": _mat("pw", secret_value, sf)}
        path = generate_overlay(stack, mats, [])
        text = path.read_text()
        assert secret_value not in text
        # The secret name and its file path ARE present (those are not secret).
        assert "pw" in text

    def test_explicit_repo_physical_args_override_env(self, tmp_path: Path) -> None:
        """repo_root/physical_root kwargs are forwarded to to_physical_path."""
        import yaml

        repo = tmp_path / "repo"
        physical = tmp_path / "phys"
        repo.mkdir()
        physical.mkdir()
        stack = repo / "s"
        stack.mkdir()
        sf = stack / ".ciu" / "secrets" / "pw"
        sf.parent.mkdir(parents=True)
        sf.write_text("v", encoding="utf-8")

        mats = {"pw": _mat("pw", "v", sf)}
        path = generate_overlay(
            stack, mats, [], repo_root=repo, physical_root=physical
        )
        doc = yaml.safe_load(path.read_text())
        assert doc["secrets"]["pw"]["file"] == str(physical / "s" / ".ciu" / "secrets" / "pw")


# ---------------------------------------------------------------------------
# S15 — generate_overlay: stack-wide resource governance
# ---------------------------------------------------------------------------

class TestGenerateOverlayGovernance:
    """S15 — the ``governance=`` keyword of :func:`generate_overlay`.

    Device autodetection (``governance_mod.detect_device``) is monkeypatched
    in every enabled-path test so these stay hermetic (no real ``findmnt``
    dependency) — S15.5's autodetect logic itself is unit-tested directly in
    ``test_ciu_governance.py``.
    """

    def _stack(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))  # native identity
        stack = repo / "infra" / "redis-core"
        stack.mkdir(parents=True)
        return stack

    def test_no_governance_table_is_silent_and_unchanged_s15_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """governance=None (no [<root>.governance] table at all): no log, no-op."""
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        path = generate_overlay(stack, {}, [], compose_yaml_text=compose_yaml, governance=None)
        assert path is None
        assert "GOVERNANCE" not in capsys.readouterr().out

    def test_disabled_is_no_op_but_logs_one_line_s15_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """Declared but enabled=false: overlay stays None, one 'disabled' line logged."""
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml, governance={"enabled": False}
        )
        assert path is None
        out = capsys.readouterr().out
        assert "[GOVERNANCE] disabled" in out

    def test_disabled_overlay_identical_to_governance_omitted_s15(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With secrets present, enabled=false must not change the overlay at all."""
        stack = self._stack(tmp_path, monkeypatch)
        sf = stack / ".ciu" / "secrets" / "pw"
        sf.parent.mkdir(parents=True)
        sf.write_text("v", encoding="utf-8")
        mats = {"pw": _mat("pw", "v", sf)}
        compose_yaml = "services:\n  redis:\n    image: redis\n"

        path_omitted = generate_overlay(stack, mats, [], compose_yaml_text=compose_yaml)
        text_omitted = path_omitted.read_text()

        path_disabled = generate_overlay(
            stack, mats, [], compose_yaml_text=compose_yaml,
            governance={"enabled": False},
        )
        assert path_disabled.read_text() == text_omitted

    def test_injection_happy_path_s15_1_s15_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """Enabled with no secrets/configfiles: overlay is generated purely for governance."""
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        compose_yaml = "services:\n  redis:\n    image: redis\n"

        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True, "read_iops": 100, "write_iops": 400},
        )
        assert path is not None
        doc = yaml.safe_load(path.read_text())
        assert "secrets" not in doc
        redis = doc["services"]["redis"]
        assert redis["cgroup_parent"] == "besteffort.slice"
        assert redis["mem_limit"] == "1g"
        assert redis["mem_reservation"] == "256m"
        assert redis["blkio_config"] == {
            "device_read_iops": [{"path": "/dev/vda", "rate": 100}],
            "device_write_iops": [{"path": "/dev/vda", "rate": 400}],
        }
        assert "[GOVERNANCE] enabled" in capsys.readouterr().out

    def test_author_override_key_is_not_repeated_others_still_injected_s15_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Author already set mem_limit on the service: overlay leaves it alone."""
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        compose_yaml = (
            "services:\n"
            "  redis:\n"
            "    image: redis\n"
            "    mem_limit: 4g\n"
        )
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True},
        )
        doc = yaml.safe_load(path.read_text())
        redis = doc["services"]["redis"]
        # mem_limit is the author's to keep — the overlay does not carry it at all.
        assert "mem_limit" not in redis
        assert redis["cgroup_parent"] == "besteffort.slice"
        assert redis["mem_reservation"] == "256m"
        assert "blkio_config" in redis

    def test_author_sets_all_four_keys_service_absent_from_overlay_s15_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        compose_yaml = (
            "services:\n"
            "  redis:\n"
            "    image: redis\n"
            "    cgroup_parent: custom.slice\n"
            "    mem_limit: 4g\n"
            "    mem_reservation: 1g\n"
            "    blkio_config:\n"
            "      weight: 500\n"
        )
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True},
        )
        # Nothing at all to wire -> overlay omitted entirely (S8.1/S15).
        assert path is None

    def test_exempt_service_receives_no_keys_s15_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        compose_yaml = (
            "services:\n"
            "  redis:\n    image: redis\n"
            "  sidecar:\n    image: busybox\n"
        )
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True, "exempt_services": ["sidecar"]},
        )
        doc = yaml.safe_load(path.read_text())
        assert "redis" in doc["services"]
        assert "sidecar" not in doc["services"]

    def test_governance_and_configfile_share_the_same_service_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Governance keys land alongside a configfile mount's volumes, not replacing them."""
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        rendered = stack / ".ciu" / "rendered" / "redis" / "main"
        rendered.parent.mkdir(parents=True)
        rendered.write_text("cfg", encoding="utf-8")
        mount = ConfigFileMount(
            service="redis", name="main", rendered_path=rendered,
            target="/etc/redis/redis.conf",
        )
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        path = generate_overlay(
            stack, {}, [mount], compose_yaml_text=compose_yaml,
            governance={"enabled": True},
        )
        doc = yaml.safe_load(path.read_text())
        redis = doc["services"]["redis"]
        assert "volumes" in redis
        assert redis["cgroup_parent"] == "besteffort.slice"

    def test_no_device_skips_blkio_config_only_s15_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "")  # autodetect fails
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True},
        )
        doc = yaml.safe_load(path.read_text())
        redis = doc["services"]["redis"]
        assert "blkio_config" not in redis
        assert redis["cgroup_parent"] == "besteffort.slice"

    def test_read_iops_derivation_from_baseline_flows_through_overlay_s15_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S15.4 end-to-end: read_iops=0 (default) derives 2/3 of the baseline RIOPS_MAX."""
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        # Hermetic S15.4 search order: only the neutral default candidate
        # exists, pointed at a tmp file (env override cleared, legacy absent).
        baseline = tmp_path / "io-baseline.env"
        baseline.write_text("RIOPS_MAX=900\n", encoding="utf-8")
        monkeypatch.delenv(governance_mod.BASELINE_PATH_ENV_VAR, raising=False)
        monkeypatch.setattr(governance_mod, "DEFAULT_BASELINE_PATH", baseline)
        monkeypatch.setattr(
            governance_mod, "LEGACY_BASELINE_PATH", tmp_path / "legacy-missing.env"
        )
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True},  # read_iops omitted -> defaults to 0 -> derive
        )
        doc = yaml.safe_load(path.read_text())
        rate = doc["services"]["redis"]["blkio_config"]["device_read_iops"][0]["rate"]
        assert rate == 600  # 900 * 2 // 3

    def test_read_iops_no_baseline_falls_back_s15_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        # No candidate in the S15.4 search order exists.
        monkeypatch.delenv(governance_mod.BASELINE_PATH_ENV_VAR, raising=False)
        monkeypatch.setattr(
            governance_mod, "DEFAULT_BASELINE_PATH", tmp_path / "default-missing.env"
        )
        monkeypatch.setattr(
            governance_mod, "LEGACY_BASELINE_PATH", tmp_path / "legacy-missing.env"
        )
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True},
        )
        doc = yaml.safe_load(path.read_text())
        rate = doc["services"]["redis"]["blkio_config"]["device_read_iops"][0]["rate"]
        assert rate == governance_mod.FALLBACK_READ_IOPS
        assert "fallback" in capsys.readouterr().out

    def test_multi_service_stack_injects_every_enumerated_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setattr(governance_mod, "detect_device", lambda: "/dev/vda")
        compose_yaml = (
            "services:\n"
            "  api:\n    image: api\n"
            "  worker-1:\n    image: w\n"
            "  worker-2:\n    image: w\n"
        )
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": True},
        )
        doc = yaml.safe_load(path.read_text())
        assert sorted(doc["services"]) == ["api", "worker-1", "worker-2"]
        for svc in doc["services"].values():
            assert svc["cgroup_parent"] == "besteffort.slice"

    def test_invalid_enabled_type_raises_s15_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = "services:\n  redis:\n    image: redis\n"
        with pytest.raises(ValueError, match=r"\[S15\.2\]"):
            generate_overlay(
                stack, {}, [], compose_yaml_text=compose_yaml,
                governance={"enabled": "yes"},
            )


# ---------------------------------------------------------------------------
# S8.2 — compose_process_env
# ---------------------------------------------------------------------------

class TestComposeProcessEnv:
    def test_contains_pwd_s8_2(self, tmp_path: Path) -> None:
        env = compose_process_env([], {}, base={})
        assert env["PWD"] == __import__("os").getcwd()

    def test_compose_profiles_only_when_given_s8_2(self) -> None:
        # Not given -> absent.
        env = compose_process_env([], {}, base={})
        assert "COMPOSE_PROFILES" not in env
        # Empty iterable -> still absent (joined value would be empty).
        env2 = compose_process_env([], {}, base={}, compose_profiles=[])
        assert "COMPOSE_PROFILES" not in env2
        # Given -> comma-joined.
        env3 = compose_process_env([], {}, base={}, compose_profiles=["a", "b"])
        assert env3["COMPOSE_PROFILES"] == "a,b"

    def test_expose_env_secret_present_others_absent_s4_19(self, tmp_path: Path) -> None:
        exposed = _spec("token", expose_env="API_TOKEN")
        plain = _spec("pw")  # no expose_env
        mats = {
            "token": MaterializedLike(exposed, "token", "tok-value", tmp_path / "token"),
            "pw": MaterializedLike(plain, "pw", "pw-value", tmp_path / "pw"),
        }
        env = compose_process_env([exposed, plain], mats, base={})
        assert env["API_TOKEN"] == "tok-value"
        # The non-exposed secret's value never reaches the env under any name.
        assert "pw" not in env
        assert "pw-value" not in env.values()

    def test_base_not_mutated_s8_2(self, tmp_path: Path) -> None:
        base = {"EXISTING": "1"}
        spec = _spec("token", expose_env="API_TOKEN")
        mats = {"token": MaterializedLike(spec, "token", "v", tmp_path / "t")}
        env = compose_process_env([spec], mats, base=base)
        assert env["EXISTING"] == "1"
        assert env["API_TOKEN"] == "v"
        # The caller's base dict is untouched.
        assert base == {"EXISTING": "1"}

    def test_expose_env_with_none_value_skipped_s4_19(self, tmp_path: Path) -> None:
        spec = _spec("crt", expose_env="CERT", kind="ASK_FILE")
        mats = {"crt": MaterializedLike(spec, "crt", None, tmp_path / "crt")}
        env = compose_process_env([spec], mats, base={})
        assert "CERT" not in env

    def test_no_extra_keys_added_s8_2(self) -> None:
        """Only PWD is added beyond base when no profiles/expose_env."""
        env = compose_process_env([], {}, base={"A": "1"})
        assert set(env.keys()) == {"A", "PWD"}


# ---------------------------------------------------------------------------
# S8.1 — compose_file_args
# ---------------------------------------------------------------------------

class TestComposeFileArgs:
    def test_base_only_without_overlay_s8_1(self, tmp_path: Path) -> None:
        assert compose_file_args(tmp_path, None) == ["-f", "ciu.compose.yml"]

    def test_includes_overlay_when_present_s8_1(self, tmp_path: Path) -> None:
        overlay = tmp_path / ".ciu" / "ciu.compose.overlay.yml"
        assert compose_file_args(tmp_path, overlay) == [
            "-f",
            "ciu.compose.yml",
            "-f",
            ".ciu/ciu.compose.overlay.yml",
        ]


# ---------------------------------------------------------------------------
# S4.21 — render_compose end-to-end against a real template file
# ---------------------------------------------------------------------------

class TestRenderCompose:
    def test_render_compose_leaks_guard_raises_s4_21(self, tmp_path: Path) -> None:
        tmpl = tmp_path / "ciu.compose.yml.j2"
        tmpl.write_text("services:\n  api:\n    x: {{ app.secrets.pw }}\n", encoding="utf-8")
        config = {"app": {"secrets": {"pw": "GEN_LOCAL:app/pw"}}}
        guarded = guard_config(config, discover("app", config))
        with pytest.raises(SecretLeakError, match="pw"):
            render_compose(tmpl, guarded)

    def test_render_compose_non_secret_ok_s4_21(self, tmp_path: Path) -> None:
        tmpl = tmp_path / "ciu.compose.yml.j2"
        tmpl.write_text("services:\n  api:\n    image: {{ app.name }}\n", encoding="utf-8")
        config = {"app": {"name": "myimage", "secrets": {"pw": "GEN_LOCAL:app/pw"}}}
        guarded = guard_config(config, discover("app", config))
        out = render_compose(tmpl, guarded)
        assert "image: myimage" in out

    def test_render_compose_host_label_composes_from_env_public_fqdn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Host() router label built from a `public_host` key that is itself
        composed as `<sub>.{{ env.PUBLIC_FQDN }}` in the TOML defaults must
        track PUBLIC_FQDN dynamically end-to-end, not bake in a fixed value.

        Regression for the nyxloom/ntfy hardcode: a prior `sed` had baked
        `nyxloom.gstammtisch.dchive.de` directly into ciu.defaults.toml.j2
        instead of composing it from `env.PUBLIC_FQDN`, so the Host() label
        (and the ntfy server.yml base-url) stayed fixed regardless of
        PUBLIC_FQDN. This exercises the same two-stage pipeline ntfy uses:
        render_toml_template resolves `public_host` from env at the TOML
        step, then render_compose reads the resolved value into the
        Traefik label.
        """
        toml_tmpl = tmp_path / "ciu.defaults.toml.j2"
        toml_tmpl.write_text(
            '[app]\npublic_host = "sub.{{ env.PUBLIC_FQDN }}"\n', encoding="utf-8"
        )
        compose_tmpl = tmp_path / "ciu.compose.yml.j2"
        compose_tmpl.write_text(
            "services:\n"
            "  api:\n"
            "    labels:\n"
            '      traefik.http.routers.api.rule: "Host(`{{ app.public_host }}`)"\n',
            encoding="utf-8",
        )

        def render_for(fqdn: str) -> str:
            monkeypatch.setenv("PUBLIC_FQDN", fqdn)
            stack_config = config_model.render_toml_template(
                toml_tmpl, config_model._make_render_context({})
            )
            return render_compose(compose_tmpl, stack_config)

        out_a = render_for("gstammtisch.dchive.de")
        assert "Host(`sub.gstammtisch.dchive.de`)" in out_a

        out_b = render_for("example.test")
        assert "Host(`sub.example.test`)" in out_b
        assert "gstammtisch.dchive.de" not in out_b


# ---------------------------------------------------------------------------
# §6 / S7.5b — dynamic per-instance configfile selector
# ---------------------------------------------------------------------------

class TestRenderConfigfilesDynamicInstances:
    """§8 AC#8: dynamic configfiles render N instances with unique mounts."""

    def _make_stack(self, tmp_path: Path, template_body: str) -> tuple:
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "worker.conf.j2").write_text(template_body, encoding="utf-8")
        return stack

    def _make_config(self, instances: int | None, template: str = "worker.conf.j2") -> dict:
        cfgfile: dict = {
            "template": template,
            "target": "/etc/worker/worker.conf",
        }
        if instances is not None:
            cfgfile["instances"] = instances
        return {
            "mystack": {
                "worker": {
                    "configfile": {
                        "cfg": cfgfile,
                    }
                }
            }
        }

    def test_single_instance_default_no_index_suffix(self, tmp_path: Path) -> None:
        """Single instance (no 'instances' key): behaves identically to before."""
        stack = self._make_stack(tmp_path, "id={{ instance_index }}\n")
        config = self._make_config(instances=None)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        assert len(mounts) == 1
        m = mounts[0]
        assert m.service == "worker"
        assert m.name == "cfg"
        rel = m.rendered_path.relative_to(stack)
        # S5.3a: mirrors the target's directory structure (target is
        # /etc/worker/worker.conf), not cfg_name ("cfg").
        assert rel == Path(".ciu") / "rendered" / "worker" / "etc" / "worker" / "worker.conf"

    def test_instances_one_behaves_same_as_no_instances(self, tmp_path: Path) -> None:
        """instances=1: same behavior as no 'instances' key (single-instance path)."""
        stack = self._make_stack(tmp_path, "id={{ instance_index }}\n")
        config = self._make_config(instances=1)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        assert len(mounts) == 1
        m = mounts[0]
        assert m.service == "worker"
        assert m.name == "cfg"

    def test_two_instances_emit_two_mounts(self, tmp_path: Path) -> None:
        """§8 AC#8: instances=2 → 2 unique ConfigFileMount objects."""
        stack = self._make_stack(tmp_path, "id={{ instance_index }}\n")
        config = self._make_config(instances=2)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        assert len(mounts) == 2

    def test_two_instances_unique_service_names(self, tmp_path: Path) -> None:
        """Each mount has a unique service name: worker-1, worker-2."""
        stack = self._make_stack(tmp_path, "ok\n")
        config = self._make_config(instances=2)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        services = [m.service for m in mounts]
        assert services == ["worker-1", "worker-2"]

    def test_two_instances_unique_names(self, tmp_path: Path) -> None:
        """Each mount has a unique name: cfg-1, cfg-2."""
        stack = self._make_stack(tmp_path, "ok\n")
        config = self._make_config(instances=2)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        names = [m.name for m in mounts]
        assert names == ["cfg-1", "cfg-2"]

    def test_n_instances_all_unique_paths(self, tmp_path: Path) -> None:
        """N instances → N unique rendered paths."""
        n = 4
        stack = self._make_stack(tmp_path, "idx={{ instance_index }}\n")
        config = self._make_config(instances=n)
        mounts = render_configfiles(stack, "mystack", config, lambda n_: "v")
        paths = [m.rendered_path for m in mounts]
        assert len(set(paths)) == n

    def test_instance_index_exposed_in_template(self, tmp_path: Path) -> None:
        """instance_index is 1-based and exposed in the render context."""
        stack = self._make_stack(tmp_path, "idx={{ instance_index }}\n")
        config = self._make_config(instances=3)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        contents = [m.rendered_path.read_text().strip() for m in mounts]
        assert contents[0] == "idx=1"
        assert contents[1] == "idx=2"
        assert contents[2] == "idx=3"

    def test_instance_id_exposed_in_template(self, tmp_path: Path) -> None:
        """instance_id is '<service>-<index>' and exposed in the render context."""
        stack = self._make_stack(tmp_path, "{{ instance_id }}\n")
        config = self._make_config(instances=2)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        assert mounts[0].rendered_path.read_text().strip() == "worker-1"
        assert mounts[1].rendered_path.read_text().strip() == "worker-2"

    def test_single_instance_no_regression_no_index_in_content(self, tmp_path: Path) -> None:
        """Single-instance (no 'instances') still gets instance_index in context
        but the service/name are unchanged (backward compat)."""
        stack = self._make_stack(tmp_path, "idx={{ instance_index }}\n")
        config = self._make_config(instances=None)
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        assert len(mounts) == 1
        # instance_index is still 1 in context even for single-instance
        assert mounts[0].rendered_path.read_text().strip() == "idx=1"
        assert mounts[0].service == "worker"  # no "-1" suffix

    def test_instances_zero_raises_valueerror(self, tmp_path: Path) -> None:
        """instances=0 is invalid → ValueError."""
        stack = self._make_stack(tmp_path, "ok\n")
        config = self._make_config(instances=0)
        with pytest.raises(ValueError, match="instances"):
            render_configfiles(stack, "mystack", config, lambda n: "v")

    def test_instances_negative_raises_valueerror(self, tmp_path: Path) -> None:
        """instances=-1 is invalid → ValueError."""
        stack = self._make_stack(tmp_path, "ok\n")
        config = self._make_config(instances=-1)
        with pytest.raises(ValueError, match="instances"):
            render_configfiles(stack, "mystack", config, lambda n: "v")

    def test_instances_string_raises_valueerror(self, tmp_path: Path) -> None:
        """instances='2' (string) is invalid → ValueError."""
        stack = self._make_stack(tmp_path, "ok\n")
        config = self._make_config(instances="2")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="instances"):
            render_configfiles(stack, "mystack", config, lambda n: "v")

    def test_existing_single_instance_unchanged(self, tmp_path: Path) -> None:
        """Configfile without 'instances' key: service and name unchanged (no regression)."""
        stack = self._make_stack(tmp_path, "hello\n")
        config = {
            "mystack": {
                "svc": {
                    "configfile": {
                        "myfile": {
                            "template": "worker.conf.j2",
                            "target": "/etc/svc/conf",
                        }
                    }
                }
            }
        }
        mounts = render_configfiles(stack, "mystack", config, lambda n: "v")
        assert len(mounts) == 1
        assert mounts[0].service == "svc"
        assert mounts[0].name == "myfile"
        assert mounts[0].target == "/etc/svc/conf"
