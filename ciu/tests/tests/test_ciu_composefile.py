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
        assert rel == Path(".ciu") / "rendered" / "svc" / "main"
        assert mounts[0].target == "/etc/app/config.toml"
        assert mounts[0].service == "svc"
        assert mounts[0].name == "main"

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
        """Each configfile mount appends '<phys>:<target>:ro' to its service."""
        import yaml

        repo = tmp_path / "repo"
        physical = tmp_path / "host_repo"
        repo.mkdir()
        physical.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical))

        stack = repo / "apps" / "controller"
        stack.mkdir(parents=True)
        rendered = stack / ".ciu" / "rendered" / "ctl" / "main"
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
        phys = str(physical / "apps" / "controller" / ".ciu" / "rendered" / "ctl" / "main")
        # Long-form mount object (colon/space-safe; R2 finding F4).
        assert vols == [{
            "type": "bind",
            "source": phys,
            "target": "/etc/controller/config.toml",
            "read_only": True,
        }]
        assert "secrets" not in doc

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
