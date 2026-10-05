"""B105's direct boundary coverage for Assay's Go-oracle adapter.

The tests exercise the Python invoker and its strict document reader. They do
not claim that a synthetic report is evidence about the Go toolchain; the
toolchain's semantics are witnessed separately by the committed helper
fixtures and registered Go lane.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from assay.adapters import go_stmtpos
from assay.errors import AssayError, Outcome, ReasonCode


def _source_dir(root: Path) -> Path:
    root.mkdir()
    (root / "a.go").write_text("package a\n", encoding="utf-8")
    (root / "stmtpos.go").write_text("package main\n", encoding="utf-8")
    (root / "go.mod").write_text("module helper\n", encoding="utf-8")
    (root.parent / "a.go").write_text("package a\n", encoding="utf-8")
    return root


def _output(path: str) -> bytes:
    return json.dumps({
        "schema": go_stmtpos.OUTPUT_SCHEMA,
        "go_version": "go1.25.14",
        "files": [{"path": path, "blocks": []}],
    }).encode("utf-8")


def test_explicit_helper_directory_is_yielded_without_staging(tmp_path: Path):
    from assay.adapters.go_stmtpos import _staged_helper

    helper = _source_dir(tmp_path / "helper")

    with _staged_helper(helper) as staged:
        assert staged == helper
        assert (staged / "stmtpos.go").is_file()


def test_package_resources_are_staged_into_a_real_helper_directory():
    from assay.adapters.go_stmtpos import _staged_helper

    with _staged_helper(None) as staged:
        assert staged.is_dir()
        assert {name for name in go_stmtpos.HELPER_RESOURCES} == {
            path.name for path in staged.iterdir()
        }
        assert (staged / "stmtpos.go").read_bytes()
        assert (staged / "go.mod").read_bytes()


def test_a_missing_package_resource_is_an_installation_refusal(
    monkeypatch: pytest.MonkeyPatch,
):
    class MissingResource:
        def joinpath(self, _name: str):
            return self

        def read_bytes(self):
            raise FileNotFoundError("resource omitted from artifact")

    monkeypatch.setattr(go_stmtpos, "resource_files", lambda _package: MissingResource())
    from assay.adapters.go_stmtpos import _staged_helper

    with pytest.raises(AssayError, match="missing from the installation") as caught:
        with _staged_helper(None):
            pytest.fail("a missing required helper resource must refuse")
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_missing_go_tool_is_a_no_measurement_terminal(tmp_path: Path, monkeypatch):
    helper = _source_dir(tmp_path / "helper")
    monkeypatch.setattr(go_stmtpos.shutil, "which", lambda _name: None)

    with pytest.raises(AssayError, match="needs `go` on PATH") as caught:
        go_stmtpos._derive(tmp_path, ["a.go"], None, helper, ())

    assert caught.value.outcome is Outcome.NO_MEASUREMENT
    assert caught.value.reason_code is ReasonCode.MISSING_EXTERNAL_TOOL


@pytest.mark.parametrize("remaining, expected_timeout", [(None, None), (lambda: 0.1, 5.0)])
def test_invocation_pins_its_environment_and_parses_the_helper_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remaining, expected_timeout
):
    helper = _source_dir(tmp_path / "helper")
    monkeypatch.setattr(go_stmtpos.shutil, "which", lambda _name: "/tools/go")

    def run(argv, **kwargs):
        assert argv[:3] == ["/tools/go", "run", "."]
        assert argv[-1] == str((tmp_path / "a.go").resolve())
        assert kwargs["cwd"] == helper
        assert kwargs["timeout"] == expected_timeout
        assert kwargs["env"]["GOPROXY"] == "off"
        assert kwargs["env"]["GOWORK"] == "off"
        assert kwargs["env"]["GOTOOLCHAIN"] == "local"
        assert kwargs["env"]["GOFLAGS"] == "-mod=mod"
        return subprocess.CompletedProcess(argv, 0, stdout=_output(argv[-1]), stderr=b"")

    monkeypatch.setattr(go_stmtpos.subprocess, "run", run)
    report = go_stmtpos._derive(tmp_path, ["a.go"], remaining, helper, ())

    assert report.blocks_by_path == {"a.go": ()}
    assert report.helper.identity == "go version go1.25.14"


def test_a_go_timeout_is_preserved_as_the_lane_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    helper = _source_dir(tmp_path / "helper")
    monkeypatch.setattr(go_stmtpos.shutil, "which", lambda _name: "/tools/go")

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(go_stmtpos.subprocess, "run", timeout)
    with pytest.raises(AssayError, match="did not finish") as caught:
        go_stmtpos._derive(tmp_path, ["a.go"], lambda: 0.1, helper, ())
    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_a_go_launch_oserror_is_an_oracle_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    helper = _source_dir(tmp_path / "helper")
    monkeypatch.setattr(go_stmtpos.shutil, "which", lambda _name: "/tools/go")

    def launch_error(*_args, **_kwargs):
        raise PermissionError("cannot execute tool")

    monkeypatch.setattr(go_stmtpos.subprocess, "run", launch_error)
    with pytest.raises(AssayError, match="could not run the Go") as caught:
        go_stmtpos._derive(tmp_path, ["a.go"], None, helper, ())
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_nonzero_go_exit_uses_a_bounded_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    helper = _source_dir(tmp_path / "helper")
    monkeypatch.setattr(go_stmtpos.shutil, "which", lambda _name: "/tools/go")
    monkeypatch.setattr(
        go_stmtpos.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 2, stdout=b"", stderr=b"  compiler failed  \n"
        ),
    )

    with pytest.raises(AssayError, match="exited 2.*compiler failed"):
        go_stmtpos._derive(tmp_path, ["a.go"], None, helper, ())


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        json.dumps({"schema": 1, "go_version": "go1.25", "files": {}}).encode(),
        json.dumps({"schema": 1, "go_version": "go1.25", "files": [None]}).encode(),
        json.dumps({
            "schema": 1, "go_version": "go1.25",
            "files": [{"path": "/repo/a.go", "blocks": [None]}],
        }).encode(),
        json.dumps({
            "schema": 1, "go_version": "go1.25",
            "files": [{"path": "/repo/a.go", "blocks": [{
                "start_line": 1, "start_col": 0, "end_line": 1,
                "end_col": 1, "num_stmts": 1, "stmt_lines": [True],
            }]}],
        }).encode(),
    ],
)
def test_untrusted_helper_document_shapes_refuse(payload: bytes):
    with pytest.raises(AssayError):
        go_stmtpos._read_document(payload, {"/repo/a.go": "a.go"}, "/tools/go")


def test_go_diagnostic_tail_has_an_explicit_empty_form_and_bound():
    assert go_stmtpos._tail(b" \n") == "(no stderr)"
    assert len(go_stmtpos._tail(b"x" * 1000)) == 400
