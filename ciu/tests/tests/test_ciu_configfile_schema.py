"""
Tests for CIU-37 — configfile entries accept schema=<path>: the rendered app
config is validated against the app's JSON schema at render time, failing
with the key path.

Normative contract: docs/SPEC.md S5.7, docs/CONFIG.md configfile section.

The consumer's schema is opaque input: ciu only checks (no authoring,
defaulting, coercion). v1 validates TOML targets only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.composefile import render_configfiles  # noqa: E402

VALID_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "log": {"type": "string"},
        "port": {"type": "integer"},
    },
    "additionalProperties": False,
    "required": ["log"],
}


def _write_schema(stack: Path, schema: dict, name: str = "config.schema.json") -> Path:
    schema_path = stack / name
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    return schema_path


class TestConfigfileSchemaValidation:
    def _setup(self, tmp_path: Path, template_body: str, schema: dict | None = None) -> dict:
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "config.toml.j2").write_text(template_body, encoding="utf-8")
        cfg_entry: dict = {
            "template": "config.toml.j2",
            "target": "/etc/app/config.toml",
        }
        if schema is not None:
            cfg_entry["schema"] = "config.schema.json"
            _write_schema(stack, schema)
        config = {
            "app": {
                "name": "myapp",
                "database": {"host": "db", "port": 5432, "user": "admin"},
                "secrets": {"pw": "ASK_VAULT:secret/data/db"},
                "svc": {
                    "configfile": {
                        "main": cfg_entry,
                    }
                },
            }
        }
        return config, stack

    # ------------------------------------------------------------------
    # O2 — post-write validation against the app's JSON schema
    # ------------------------------------------------------------------

    def test_valid_render_passes_and_mount_is_emitted(self, tmp_path: Path) -> None:
        """A rendered config matching the schema renders and mounts normally."""
        config, stack = self._setup(
            tmp_path, 'log = "{{ app.name }}"\nport = 8080\n', VALID_SCHEMA
        )
        mounts = render_configfiles(stack, "app", config, lambda n: "v")
        assert len(mounts) == 1
        assert mounts[0].rendered_path.read_text() == 'log = "myapp"\nport = 8080'

    def test_violation_fails_naming_key_path_and_removes_file(self, tmp_path: Path) -> None:
        """An extra key with additionalProperties:false fails with the key
        path; the invalid rendered file is removed and never consumable."""
        config, stack = self._setup(
            tmp_path, 'log = "{{ app.name }}"\ncolour = "blue"\n', VALID_SCHEMA
        )
        with pytest.raises(ValueError) as exc:
            render_configfiles(stack, "app", config, lambda n: "v")
        message = str(exc.value)
        assert "[S5.7]" in message
        assert "svc" in message          # service
        assert "'main'" in message        # configfile name
        assert "'colour'" in message      # offending KEY PATH
        staging = stack / ".ciu" / "rendered" / "svc" / "etc" / "app" / "config.toml"
        assert not staging.exists(), "invalid rendered file must be removed"

    def test_multi_instance_error_names_instance_suffix(self, tmp_path: Path) -> None:
        """With instances>1 the failing instance's suffix appears in the
        error (cfg-2 of svc-2), not just the base names."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "config.toml.j2").write_text(
            'name = "{{ instance_id }}"\n', encoding="utf-8"
        )
        _write_schema(
            stack,
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"name": {"type": "string", "pattern": "^svc-1$"}},
                "additionalProperties": False,
                "required": ["name"],
            },
        )
        config = {
            "app": {
                "svc": {
                    "configfile": {
                        "cfg": {
                            "template": "config.toml.j2",
                            "target": "/etc/app/config.toml",
                            "instances": 2,
                            "schema": "config.schema.json",
                        }
                    }
                }
            }
        }
        with pytest.raises(ValueError) as exc:
            render_configfiles(stack, "app", config, lambda n: "v")
        message = str(exc.value)
        assert "cfg-2" in message
        assert "svc-2" in message
        assert "'name'" in message  # key path

    def test_non_toml_rendered_output_with_schema_fails_tagged(self, tmp_path: Path) -> None:
        """Schema validation covers TOML targets only: a render that is not
        parseable TOML is a tagged error, and the file is removed."""
        config, stack = self._setup(tmp_path, '{"log": "not-toml"}\n', VALID_SCHEMA)
        with pytest.raises(ValueError) as exc:
            render_configfiles(stack, "app", config, lambda n: "v")
        message = str(exc.value)
        assert "[S5.7]" in message
        assert "not valid TOML" in message
        staging = stack / ".ciu" / "rendered" / "svc" / "etc" / "app" / "config.toml"
        assert not staging.exists()

    def test_invalid_json_schema_file_fails_tagged(self, tmp_path: Path) -> None:
        """A declared schema file that is not valid JSON fails loudly and
        removes the rendered file."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "config.toml.j2").write_text('log = "myapp"\n', encoding="utf-8")
        (stack / "config.schema.json").write_text("{ not json", encoding="utf-8")
        config = {
            "app": {
                "svc": {
                    "configfile": {
                        "main": {
                            "template": "config.toml.j2",
                            "target": "/etc/app/config.toml",
                            "schema": "config.schema.json",
                        }
                    }
                }
            }
        }
        with pytest.raises(ValueError) as exc:
            render_configfiles(stack, "app", config, lambda n: "v")
        message = str(exc.value)
        assert "[S5.7]" in message
        assert "not valid JSON" in message
        staging = stack / ".ciu" / "rendered" / "svc" / "etc" / "app" / "config.toml"
        assert not staging.exists()

    # ------------------------------------------------------------------
    # O1 — declaration-time validation of the schema key
    # ------------------------------------------------------------------

    def test_schema_missing_file_fails_before_any_render(self, tmp_path: Path) -> None:
        """A declared schema whose file does not exist is a declaration-time
        error (before any render), tagged like the other key errors."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "config.toml.j2").write_text('log = "myapp"\n', encoding="utf-8")
        config = {
            "app": {
                "svc": {
                    "configfile": {
                        "main": {
                            "template": "config.toml.j2",
                            "target": "/etc/app/config.toml",
                            "schema": "missing.schema.json",
                        }
                    }
                }
            }
        }
        with pytest.raises(FileNotFoundError) as exc:
            render_configfiles(stack, "app", config, lambda n: "v")
        message = str(exc.value)
        assert "[S5.1]" in message
        assert "schema file not found" in message
        assert "missing.schema.json" in message

    @pytest.mark.parametrize("bad_schema", [123, ""])
    def test_schema_key_shape_fails(self, tmp_path: Path, bad_schema) -> None:
        """schema must be a non-empty string path."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "config.toml.j2").write_text('log = "myapp"\n', encoding="utf-8")
        config = {
            "app": {
                "svc": {
                    "configfile": {
                        "main": {
                            "template": "config.toml.j2",
                            "target": "/etc/app/config.toml",
                            "schema": bad_schema,
                        }
                    }
                }
            }
        }
        with pytest.raises(ValueError, match=r"\[S5\.1\].*'schema' must be a file path"):
            render_configfiles(stack, "app", config, lambda n: "v")

    # ------------------------------------------------------------------
    # O3 — optional dependency: fail-loud, never silent skip
    # ------------------------------------------------------------------

    def test_absent_jsonschema_fails_loud_with_install_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With jsonschema unimportable and a schema key declared, the run
        fails with a tagged error pointing at ciu[schema] — never a silent
        skip (sys.modules None makes the import raise ImportError)."""
        monkeypatch.setitem(sys.modules, "jsonschema", None)
        config, stack = self._setup(
            tmp_path, 'log = "{{ app.name }}"\nport = 8080\n', VALID_SCHEMA
        )
        with pytest.raises(ValueError) as exc:
            render_configfiles(stack, "app", config, lambda n: "v")
        message = str(exc.value)
        assert "[S5.7]" in message
        assert "ciu[schema]" in message

    def test_no_schema_key_never_imports_jsonschema(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a schema key anywhere, the jsonschema import is never
        attempted: with the module forced unimportable, rendering still
        succeeds (any attempt would raise ImportError)."""
        monkeypatch.setitem(sys.modules, "jsonschema", None)
        config, stack = self._setup(tmp_path, 'log = "{{ app.name }}"\n', schema=None)
        mounts = render_configfiles(stack, "app", config, lambda n: "v")
        assert len(mounts) == 1
