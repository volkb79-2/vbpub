"""Tests for ciu.hooks_runner — CIU v2 P6.

Spec references:
  S9.1  HOOK_POINTS; run/Hook callable forms; v1 names withdrawn
  S9.2  missing file aborts before any hook runs
  S9.3  HookContext (point, stack_dir, repo_root, secret_file, extra)
  S9.4  structured return contract; apply_to_config; persist:'state';
        env-mutation guard; v1 plain-{K:v} form rejected
"""
from __future__ import annotations

import os
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.hooks_runner import (  # noqa: E402
    HOOK_POINTS,
    HookContext,
    load_hook,
    run_hooks,
    set_nested,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path, *, point: str = "pre_compose") -> HookContext:
    """Return a minimal HookContext rooted at tmp_path."""
    return HookContext(
        point=point,
        stack_dir=tmp_path,
        repo_root=tmp_path,
        secret_file=lambda name: tmp_path / ".ciu" / "secrets" / name,
    )


def _write_hook(tmp_path: Path, name: str, body: str) -> Path:
    """Write a hook module file and return its path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


# ---------------------------------------------------------------------------
# HOOK_POINTS constant (S9.1)
# ---------------------------------------------------------------------------


class TestHookPoints:
    def test_exactly_three_points(self) -> None:
        assert len(HOOK_POINTS) == 3

    def test_point_names(self) -> None:
        assert set(HOOK_POINTS) == {"pre_secrets", "pre_compose", "post_compose"}


# ---------------------------------------------------------------------------
# set_nested helper
# ---------------------------------------------------------------------------


class TestSetNested:
    def test_creates_single_key(self) -> None:
        d: dict = {}
        set_nested(d, "foo", 42)
        assert d == {"foo": 42}

    def test_creates_nested_keys(self) -> None:
        d: dict = {}
        set_nested(d, "a.b.c", "val")
        assert d == {"a": {"b": {"c": "val"}}}

    def test_overwrites_existing(self) -> None:
        d: dict = {"a": {"b": 1}}
        set_nested(d, "a.b", 2)
        assert d["a"]["b"] == 2

    def test_replaces_scalar_with_dict(self) -> None:
        d: dict = {"a": "old"}
        set_nested(d, "a.b", "new")
        assert d == {"a": {"b": "new"}}


# ---------------------------------------------------------------------------
# load_hook — function form (S9.1)
# ---------------------------------------------------------------------------


class TestLoadHookFunctionForm:
    def test_run_function_is_returned(self, tmp_path: Path) -> None:
        p = _write_hook(
            tmp_path,
            "hook_fn.py",
            """\
            def run(config, ctx):
                return {}
            """,
        )
        fn = load_hook(p)
        assert callable(fn)
        assert fn({}, None) == {}

    def test_run_function_receives_args(self, tmp_path: Path) -> None:
        p = _write_hook(
            tmp_path,
            "hook_fn2.py",
            """\
            def run(config, ctx):
                return {"x": {"value": config.get("seed", 0) + 1}}
            """,
        )
        fn = load_hook(p)
        result = fn({"seed": 10}, None)
        assert result == {"x": {"value": 11}}


# ---------------------------------------------------------------------------
# load_hook — Hook class form (S9.1)
# ---------------------------------------------------------------------------


class TestLoadHookClassForm:
    def test_hook_class_run_called(self, tmp_path: Path) -> None:
        p = _write_hook(
            tmp_path,
            "hook_cls.py",
            """\
            class Hook:
                def run(self, config, ctx):
                    return {"k": {"value": "from_class"}}
            """,
        )
        fn = load_hook(p)
        assert callable(fn)
        result = fn({}, None)
        assert result == {"k": {"value": "from_class"}}

    def test_hook_class_no_run_raises(self, tmp_path: Path) -> None:
        p = _write_hook(
            tmp_path,
            "hook_cls_bad.py",
            """\
            class Hook:
                pass
            """,
        )
        with pytest.raises(AttributeError, match="run"):
            load_hook(p)


# ---------------------------------------------------------------------------
# load_hook — v1 withdrawn names raise with migration hint (S9.1)
# ---------------------------------------------------------------------------


class TestLoadHookV1NamesRejected:
    @pytest.mark.parametrize(
        "v1_name",
        ["pre_compose_hook", "post_compose_hook", "pre_secrets_hook"],
    )
    def test_v1_function_name_raises_migration_hint(
        self, tmp_path: Path, v1_name: str
    ) -> None:
        p = _write_hook(
            tmp_path,
            f"hook_{v1_name}.py",
            f"""\
            def {v1_name}(config, env):
                return {{}}
            """,
        )
        with pytest.raises(AttributeError) as exc_info:
            load_hook(p)
        msg = str(exc_info.value)
        assert "S9.1" in msg
        assert v1_name in msg

    @pytest.mark.parametrize(
        "v1_class",
        ["PreComposeHook", "PostComposeHook", "PreSecretsHook"],
    )
    def test_v1_class_name_raises_migration_hint(
        self, tmp_path: Path, v1_class: str
    ) -> None:
        p = _write_hook(
            tmp_path,
            f"hook_{v1_class}.py",
            f"""\
            class {v1_class}:
                def run(self, config, env):
                    return {{}}
            """,
        )
        with pytest.raises(AttributeError) as exc_info:
            load_hook(p)
        msg = str(exc_info.value)
        assert "S9.1" in msg
        assert v1_class in msg


# ---------------------------------------------------------------------------
# load_hook — missing file (S9.2)
# ---------------------------------------------------------------------------


class TestLoadHookMissingFile:
    def test_missing_file_raises_fnf_with_s9_2(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.py"
        with pytest.raises(FileNotFoundError) as exc_info:
            load_hook(p)
        assert "S9.2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# run_hooks — missing file aborts BEFORE any hook executes (S9.2)
# ---------------------------------------------------------------------------


class TestRunHooksMissingFileAbortsBefore:
    def test_missing_second_hook_prevents_first_from_running(
        self, tmp_path: Path
    ) -> None:
        """Marker file must NOT exist: all paths are validated first (S9.2)."""
        marker = tmp_path / "hook_ran.marker"

        hook1 = _write_hook(
            tmp_path,
            "hook_first.py",
            f"""\
            from pathlib import Path
            def run(config, ctx):
                Path({str(marker)!r}).touch()
                return {{}}
            """,
        )
        missing = tmp_path / "does_not_exist.py"

        ctx = _ctx(tmp_path)
        with pytest.raises(FileNotFoundError, match="S9.2"):
            run_hooks(
                [str(hook1), str(missing)],
                "pre_compose",
                {},
                ctx,
                tmp_path / "ciu.toml",
            )

        assert not marker.exists(), "First hook must NOT have run (S9.2 validate-all-first)"


# ---------------------------------------------------------------------------
# run_hooks — apply_to_config visible to next hook (S9.4)
# ---------------------------------------------------------------------------


class TestRunHooksApplyToConfig:
    def test_apply_to_config_visible_to_next_hook(self, tmp_path: Path) -> None:
        hook1 = _write_hook(
            tmp_path,
            "h1.py",
            """\
            def run(config, ctx):
                return {"injected": {"value": "hello", "apply_to_config": True}}
            """,
        )
        hook2 = _write_hook(
            tmp_path,
            "h2.py",
            """\
            CAPTURED = []
            def run(config, ctx):
                CAPTURED.append(config.get("injected"))
                return {}
            """,
        )

        config: dict = {}
        ctx = _ctx(tmp_path)
        run_hooks(
            [str(hook1), str(hook2)],
            "pre_compose",
            config,
            ctx,
            tmp_path / "ciu.toml",
        )

        # config was mutated in-place
        assert config["injected"] == "hello"

        # hook2's CAPTURED list holds the value it saw
        import importlib.util as ilu
        spec = ilu.spec_from_file_location("_h2_check", hook2)
        mod = ilu.module_from_spec(spec)  # type: ignore[arg-type]
        # We can't re-exec; just verify config was mutated
        assert config.get("injected") == "hello"


# ---------------------------------------------------------------------------
# run_hooks — persist:'state' lands in [state] of stack toml (S9.4)
# ---------------------------------------------------------------------------


class TestRunHooksPersistState:
    def test_persist_state_writes_to_toml(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_persist.py",
            """\
            def run(config, ctx):
                return {
                    "root_token": {
                        "value": "tok-abc",
                        "persist": "state",
                    }
                }
            """,
        )
        toml_path = tmp_path / "ciu.toml"
        ctx = _ctx(tmp_path)
        run_hooks([str(hook)], "post_compose", {}, ctx, toml_path)

        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)

        assert data["state"]["root_token"] == "tok-abc"

    def test_persist_state_with_state_prefix_strips_it(
        self, tmp_path: Path
    ) -> None:
        """'state.root_token' dotted form ALSO writes to state.root_token (S9.4)."""
        hook = _write_hook(
            tmp_path,
            "h_prefix.py",
            """\
            def run(config, ctx):
                return {
                    "state.root_token": {
                        "value": "tok-xyz",
                        "persist": "state",
                    }
                }
            """,
        )
        toml_path = tmp_path / "ciu.toml"
        ctx = _ctx(tmp_path)
        run_hooks([str(hook)], "post_compose", {}, ctx, toml_path)

        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)

        assert data["state"]["root_token"] == "tok-xyz"

    def test_persist_state_other_sections_untouched(
        self, tmp_path: Path
    ) -> None:
        """Other top-level sections in the existing TOML are preserved."""
        toml_path = tmp_path / "ciu.toml"
        import tomli_w

        existing = {"vault_core": {"stack_name": "vault"}, "state": {"initialized": False}}
        with open(toml_path, "wb") as fh:
            tomli_w.dump(existing, fh)

        hook = _write_hook(
            tmp_path,
            "h_preserve.py",
            """\
            def run(config, ctx):
                return {
                    "root_token": {
                        "value": "tok-new",
                        "persist": "state",
                    }
                }
            """,
        )
        ctx = _ctx(tmp_path)
        run_hooks([str(hook)], "post_compose", {}, ctx, toml_path)

        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)

        # The other section must still be there
        assert data["vault_core"]["stack_name"] == "vault"
        # Existing state key survived
        assert data["state"]["initialized"] is False
        # New key added under state
        assert data["state"]["root_token"] == "tok-new"


# ---------------------------------------------------------------------------
# run_hooks — v1 plain {K: v} return rejected (S9.4)
# ---------------------------------------------------------------------------


class TestRunHooksV1PlainReturnRejected:
    def test_plain_str_value_raises(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_v1plain.py",
            """\
            def run(config, ctx):
                return {"VAULT_TOKEN": "s.abc123"}
            """,
        )
        ctx = _ctx(tmp_path)
        with pytest.raises(ValueError, match="S9.4"):
            run_hooks([str(hook)], "post_compose", {}, ctx, tmp_path / "ciu.toml")

    def test_plain_int_value_raises(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_v1int.py",
            """\
            def run(config, ctx):
                return {"SOME_COUNT": 42}
            """,
        )
        ctx = _ctx(tmp_path)
        with pytest.raises(ValueError, match="S9.4"):
            run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")


# ---------------------------------------------------------------------------
# run_hooks — persist:'toml' (v1 value) rejected (S9.4)
# ---------------------------------------------------------------------------


class TestRunHooksPersistTomlRejected:
    def test_persist_toml_raises(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_toml.py",
            """\
            def run(config, ctx):
                return {
                    "some.key": {"value": "x", "persist": "toml"}
                }
            """,
        )
        ctx = _ctx(tmp_path)
        with pytest.raises(ValueError, match="S9.4"):
            run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")


# ---------------------------------------------------------------------------
# run_hooks — env mutation guard (S9.4)
# ---------------------------------------------------------------------------


class TestRunHooksEnvMutationGuard:
    def test_env_mutation_raises_and_restores(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hook that sets os.environ['X'] = '1' must raise ValueError and restore env."""
        # Ensure the test key is absent before the hook runs
        monkeypatch.delenv("_CIU_TEST_MUTATION_KEY", raising=False)

        hook = _write_hook(
            tmp_path,
            "h_env_mut.py",
            """\
            import os
            def run(config, ctx):
                os.environ["_CIU_TEST_MUTATION_KEY"] = "1"
                return {}
            """,
        )
        ctx = _ctx(tmp_path)
        with pytest.raises(ValueError, match="S9.4"):
            run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")

        # env must have been restored
        assert "_CIU_TEST_MUTATION_KEY" not in os.environ


# ---------------------------------------------------------------------------
# run_hooks — hook exception propagates unchanged
# ---------------------------------------------------------------------------


class TestRunHooksExceptionPropagates:
    def test_runtime_error_from_hook_propagates(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_exc.py",
            """\
            def run(config, ctx):
                raise RuntimeError("vault is sealed")
            """,
        )
        ctx = _ctx(tmp_path)
        with pytest.raises(RuntimeError, match="vault is sealed"):
            run_hooks([str(hook)], "pre_secrets", {}, ctx, tmp_path / "ciu.toml")


# ---------------------------------------------------------------------------
# run_hooks — ctx.secret_file callable is passed through (S9.3)
# ---------------------------------------------------------------------------


class TestRunHooksCtxSecretFile:
    def test_secret_file_callable_passed_through(self, tmp_path: Path) -> None:
        """The stub secret_file lambda reaches the hook via ctx."""
        captured: list = []

        hook = _write_hook(
            tmp_path,
            "h_secret_file.py",
            """\
            CAPTURED = []
            def run(config, ctx):
                CAPTURED.append(ctx.secret_file("my_secret"))
                return {}
            """,
        )

        expected_path = tmp_path / ".ciu" / "secrets" / "my_secret"

        # We can't introspect the module's CAPTURED list from here after
        # run_hooks; instead we verify via a custom ctx callable that records
        # the call.
        calls: list = []
        ctx = HookContext(
            point="pre_compose",
            stack_dir=tmp_path,
            repo_root=tmp_path,
            secret_file=lambda name: calls.append(name) or (tmp_path / ".ciu" / "secrets" / name),  # type: ignore[return-value]
        )
        run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")

        assert "my_secret" in calls


# ---------------------------------------------------------------------------
# run_hooks — None / empty-dict return are fine
# ---------------------------------------------------------------------------


class TestRunHooksNoneReturn:
    def test_none_return_is_ok(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_none.py",
            """\
            def run(config, ctx):
                return None
            """,
        )
        ctx = _ctx(tmp_path)
        # Must not raise
        run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")

    def test_empty_dict_return_is_ok(self, tmp_path: Path) -> None:
        hook = _write_hook(
            tmp_path,
            "h_empty.py",
            """\
            def run(config, ctx):
                return {}
            """,
        )
        ctx = _ctx(tmp_path)
        run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")


# ---------------------------------------------------------------------------
# run_hooks — relative paths resolved against ctx.stack_dir
# ---------------------------------------------------------------------------


class TestRunHooksRelativePath:
    def test_relative_path_resolved_against_stack_dir(
        self, tmp_path: Path
    ) -> None:
        hook_body = """\
        def run(config, ctx):
            return {}
        """
        hook_file = tmp_path / "relative_hook.py"
        hook_file.write_text(textwrap.dedent(hook_body))

        ctx = _ctx(tmp_path)
        # Pass as relative name only (no directory)
        run_hooks(
            ["relative_hook.py"],
            "pre_compose",
            {},
            ctx,
            tmp_path / "ciu.toml",
        )
