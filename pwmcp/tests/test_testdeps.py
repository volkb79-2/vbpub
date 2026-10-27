"""The optional-dependency policy (tests/_testdeps.py): missing => FAIL by default, skip is the opt-in."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import _testdeps
from _testdeps import ALLOW_VAR, REQUIRE_TESSERACT_VAR, missing, optional_module, require_binary

PWMCP = Path(__file__).resolve().parents[1]
GONE = "pwmcp_no_such_module_for_tests"


@pytest.fixture
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_VAR, raising=False)


@pytest.fixture
def lenient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_VAR, "1")


# ------------------------------------------------------------------ the helper itself, both modes


def test_a_present_module_is_returned_in_either_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_VAR, raising=False)
    assert optional_module("json", "stdlib").dumps({}) == "{}"
    monkeypatch.setenv(ALLOW_VAR, "1")
    assert optional_module("json", "stdlib").dumps({}) == "{}"


def test_default_a_missing_module_FAILS_with_a_clear_message(strict) -> None:
    with pytest.raises(pytest.fail.Exception) as caught:
        optional_module(GONE, "needed to test things")
    text = str(caught.value)
    assert GONE in text and "needed to test things" in text
    assert "'test' extra" in text and "pillow" in text and ALLOW_VAR in text  # names the extra and the opt-out


def test_opt_in_a_missing_module_SKIPS_with_the_reason(lenient) -> None:
    with pytest.raises(pytest.skip.Exception) as caught:
        optional_module(GONE, "needed to test things")
    assert "needed to test things" in str(caught.value)


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "2", " 1"])
def test_only_exactly_1_opts_out(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_VAR, value)
    assert _testdeps.allow_missing() is False
    with pytest.raises(pytest.fail.Exception):
        optional_module(GONE, "x")


def test_the_mode_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_VAR, "1")
    with pytest.raises(pytest.skip.Exception):
        missing("thing", "reason")
    monkeypatch.delenv(ALLOW_VAR)
    with pytest.raises(pytest.fail.Exception):
        missing("thing", "reason")


def test_a_missing_binary_fails_by_default_and_skips_on_opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: nothing is found
    monkeypatch.delenv(ALLOW_VAR, raising=False)
    with pytest.raises(pytest.fail.Exception) as caught:
        require_binary("node", "runs the gateway")
    assert "`node` binary" in str(caught.value) and "runs the gateway" in str(caught.value)
    monkeypatch.setenv(ALLOW_VAR, "1")
    with pytest.raises(pytest.skip.Exception):
        require_binary("node", "runs the gateway")


def test_a_present_binary_is_returned(strict) -> None:
    assert Path(require_binary(Path(sys.executable).name, "python itself")).name


def test_module_level_skip_is_allowed_only_when_asked_for(lenient) -> None:
    with pytest.raises(pytest.skip.Exception) as caught:
        optional_module(GONE, "module scope", module_level=True)
    assert "module scope" in str(caught.value)


# ------------------------------------------------------------------ every real site, through pytest itself


def _pytest(tmp_path: Path, node: str, *, hide: str | None = None, env: dict[str, str] | None = None,
            path: str | None = None) -> subprocess.CompletedProcess:
    paths = [str(PWMCP / "client" / "src")]
    if hide:
        stub = tmp_path / "hide" / hide
        stub.mkdir(parents=True, exist_ok=True)
        (stub / "__init__.py").write_text(f"raise ImportError('{hide} hidden for this test')\n", encoding="utf-8")
        paths.insert(0, str(tmp_path / "hide"))
    full = {k: v for k, v in os.environ.items() if k not in {ALLOW_VAR, REQUIRE_TESSERACT_VAR}}
    full["PYTHONPATH"] = os.pathsep.join(paths)
    full["PYTHONDONTWRITEBYTECODE"] = "1"
    if path is not None:
        full["PATH"] = path
    full.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider", "--cli-case-partial", "-rs"],
        cwd=PWMCP, env=full, capture_output=True, text=True, check=False, timeout=240,
    )


SITES = [
    ("PIL", "tests/test_ocr_service.py::test_palette_and_rgba_images_are_flattened_for_the_engine"),
    ("jinja2", "tests/test_ocr_stack.py"),  # module-level
    ("yaml", "client/tests/test_user_agent.py::test_compose_line_renders_valid_yaml_and_escapes_dollar_for_compose"),
    ("websockets", "tests/test_user_agent_server.py::test_ua_override_sends_metadata_for_existing_and_newly_attached_targets"),
]


@pytest.mark.parametrize(("hidden", "node"), SITES)
def test_every_site_fails_by_default_and_skips_on_opt_in(tmp_path: Path, hidden: str, node: str) -> None:
    default = _pytest(tmp_path, node, hide=hidden)
    assert default.returncode != 0, default.stdout[-800:]
    assert hidden in default.stdout and "'test' extra" in default.stdout and ALLOW_VAR in default.stdout
    assert "passed" not in default.stdout.splitlines()[-1]
    optin = _pytest(tmp_path, node, hide=hidden, env={ALLOW_VAR: "1"})
    # (a module-level skip collects nothing: pytest exits 5, still "skipped, not failed")
    assert optin.returncode in (0, 5) and "skipped" in optin.stdout, optin.stdout[-800:]


def test_a_missing_node_fails_by_default_and_skips_on_opt_in(tmp_path: Path) -> None:
    no_node = str(Path(sys.executable).parent)  # python only: no node, no sh lookups through PATH
    node_site = "tests/test_ocr_stack.py::test_gateway_contract_advertises_no_ocr_by_default"
    default = _pytest(tmp_path, node_site, path=no_node)
    assert default.returncode != 0 and "`node` binary" in default.stdout, default.stdout[-800:]
    optin = _pytest(tmp_path, node_site, path=no_node, env={ALLOW_VAR: "1"})
    assert optin.returncode == 0 and "skipped" in optin.stdout, optin.stdout[-800:]


def test_the_real_tesseract_test_skips_explicitly_unless_it_is_required(tmp_path: Path) -> None:
    site = "tests/test_ocr_service.py::test_real_tesseract_recognises_rendered_console_lines"
    bare = str(Path(sys.executable).parent)  # no tesseract on this PATH
    default = _pytest(tmp_path, site, path=bare)
    assert default.returncode == 0 and "skipped" in default.stdout, default.stdout[-800:]
    assert "`tesseract` binary is not on PATH" in default.stdout and REQUIRE_TESSERACT_VAR in default.stdout
    required = _pytest(tmp_path, site, path=bare, env={REQUIRE_TESSERACT_VAR: "1"})
    assert required.returncode != 0 and f"{REQUIRE_TESSERACT_VAR}=1 but real-Tesseract test" in required.stdout
    # the generic opt-out does NOT excuse a REQUIRED tesseract
    both = _pytest(tmp_path, site, path=bare, env={REQUIRE_TESSERACT_VAR: "1", ALLOW_VAR: "1"})
    assert both.returncode != 0


# ------------------------------------------------------------------ no site may bypass the policy


def test_no_test_bypasses_the_shared_policy() -> None:
    offenders = []
    for tree in ("tests", "client/tests"):
        for path in sorted((PWMCP / tree).glob("*.py")):
            if path.name in {"test_testdeps.py", "_testdeps.py"}:  # the policy itself
                continue
            text = path.read_text(encoding="utf-8")
            if "pytest.importorskip(" in text:
                offenders.append(f"{path.relative_to(PWMCP)}: pytest.importorskip")
            for match in re.finditer(r"skipif\(([^\n]*)", text):
                if "is None" in match.group(1) or "which(" in match.group(1):
                    offenders.append(f"{path.relative_to(PWMCP)}: skipif({match.group(1)[:50]}")
            if re.search(r"pytest\.skip\(", text) and path.name not in {"conftest.py", "_testdeps.py"}:
                offenders.append(f"{path.relative_to(PWMCP)}: pytest.skip")
    assert offenders == [], f"use _testdeps.optional_module / require_binary (fail by default): {offenders}"


def test_the_gate_lanes_set_no_opt_out_and_judge_the_sidecar() -> None:
    lanes = tomllib.loads((PWMCP / "assay.toml").read_text(encoding="utf-8"))["lanes"]
    for name in ("r1", "r2"):
        env = lanes[name]["env"]
        assert not any(key.startswith("PWMCP_ALLOW") or key.startswith("PWMCP_REQUIRE") for key in env), env
        assert "containers/ocr/ocr_service.py" in lanes[name]["judge"]["targets"]
        assert "client/src/pwmcp_client/ocr.py" in lanes[name]["judge"]["targets"]
        assert "client/src/pwmcp_client/user_agent.py" in lanes[name]["judge"]["targets"]
