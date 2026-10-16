"""cli-extended adoption of pwmcp's release-step scripts (PWMCP-02, CX-D3)."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import cli_extended
from cli_extended import VersionLookupError, invoke_script

PWMCP = Path(__file__).resolve().parents[1]
SCRIPTS = PWMCP / "scripts"
RELEASE = json.loads((PWMCP / "pwmcp.contract.json").read_text(encoding="utf-8"))["release"]

# script path -> (display command, identity long name)
SCRIPT_TABLE = {
    PWMCP / "build-push.py": ("build-push.py", "image build and push"),
    SCRIPTS / "build-bundle.py": ("build-bundle.py", "stack bundle build"),
    SCRIPTS / "publish-bundle.py": ("publish-bundle.py", "stack bundle publish"),
    SCRIPTS / "resolve-playwright-version.py": ("resolve-playwright-version.py", "version projection"),
}
SCRIPT_IDS = [path.name for path in SCRIPT_TABLE]

# Files a dry run or a help/version call must never touch.
WATCHED = (
    "cmru.vars",
    "pwmcp.contract.json",
    "ciu.defaults.toml.j2",
    "ciu.toml.j2",
    "docker-bake.hcl",
    "dist",
)


def _load(path: Path, name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot() -> dict[str, object]:
    state: dict[str, object] = {}
    for name in WATCHED:
        path = PWMCP / name
        if path.is_dir():
            state[name] = sorted(str(p.relative_to(path)) for p in path.rglob("*"))
        elif path.exists():
            state[name] = path.read_bytes()
        else:
            state[name] = None
    return state


def _run(tmp_path: Path, script: Path, *argv: str):
    """Run a script in a subprocess with a hermetic HOME (never the real one)."""
    return invoke_script(script, argv, home=tmp_path / "home", cwd=tmp_path)


@pytest.mark.parametrize("script", list(SCRIPT_TABLE), ids=SCRIPT_IDS)
def test_version_and_help_are_identity_headed_and_side_effect_free(tmp_path: Path, script: Path) -> None:
    command, long_name = SCRIPT_TABLE[script]
    before = _snapshot()

    version = _run(tmp_path, script, "--version")
    assert (version.returncode, version.stdout, version.stderr) == (0, f"{command} {RELEASE}\n", "")

    for argv in (("--help",), ("help",)):
        helped = _run(tmp_path, script, *argv)
        assert helped.returncode == 0, helped.stderr
        assert helped.stdout.startswith(f"PWMCP {RELEASE} — {long_name}\n")
        assert "--dry-run" in helped.stdout
        assert "--yes" not in helped.stdout  # CMRU runs these non-interactively; see script_cli
        assert helped.stderr == ""

    assert _snapshot() == before


@pytest.mark.parametrize("script", list(SCRIPT_TABLE), ids=SCRIPT_IDS)
def test_unknown_operand_is_a_usage_error_without_a_traceback(tmp_path: Path, script: Path) -> None:
    result = _run(tmp_path, script, "--no-such-option")
    assert result.returncode == 2
    assert result.stderr.startswith("[ERROR]")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "script, extra",
    [
        (PWMCP / "build-push.py", ("--build",)),
        (PWMCP / "build-push.py", ("--push",)),
        (SCRIPTS / "build-bundle.py", ()),
        (SCRIPTS / "publish-bundle.py", ()),
        (SCRIPTS / "resolve-playwright-version.py", ("--check",)),
        (SCRIPTS / "resolve-playwright-version.py", ("--refresh", "--age-window-days", "30")),
    ],
    ids=lambda value: value.name if isinstance(value, Path) else "_".join(value) or "bare",
)
def test_dry_run_previews_and_changes_nothing(tmp_path: Path, script: Path, extra: tuple[str, ...]) -> None:
    before = _snapshot()
    result = _run(tmp_path, script, *extra, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "dry run: would" in result.stderr
    assert _snapshot() == before


def test_build_push_requires_exactly_one_operation(tmp_path: Path) -> None:
    script = PWMCP / "build-push.py"
    bare = _run(tmp_path, script)
    assert bare.returncode == 2  # bare invocation reaches the parser; it never prints help and exits 0
    assert "one of the arguments --build --push is required" in bare.stderr
    both = _run(tmp_path, script, "--build", "--push")
    assert both.returncode == 2
    assert "not allowed with argument" in both.stderr


def test_age_window_default_and_preview(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    resolver = _load(SCRIPTS / "resolve-playwright-version.py", "pwmcp_script_cli_resolver")
    monkeypatch.setattr(resolver, "refresh_upstream_projection", lambda days: pytest.fail("dry run must not refresh"))
    monkeypatch.setattr(resolver, "check_committed_inputs", lambda: pytest.fail("dry run must not check"))
    resolver.main(["--refresh", "--dry-run"])
    assert f"at least {resolver.TEMPORARY_AGE_WINDOW_DAYS} days old" in capsys.readouterr().err
    resolver.main(["--refresh", "--dry-run", "--age-window-days", "3"])
    assert "at least 3 days old" in capsys.readouterr().err
    resolver.main(["--dry-run"])
    assert "would validate the committed version projection" in capsys.readouterr().err
    with pytest.raises(SystemExit) as refused:
        resolver.main(["--check", "--refresh"])
    assert refused.value.code == 2


def test_build_push_dry_run_never_calls_the_operations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_push = _load(PWMCP / "build-push.py", "pwmcp_script_cli_build_push")
    monkeypatch.setattr(build_push, "do_build", lambda: pytest.fail("dry run must not build"))
    monkeypatch.setattr(build_push, "do_push", lambda: pytest.fail("dry run must not push"))
    build_push.main(["--build", "--dry-run"])
    assert "would build the pwmcp images locally" in capsys.readouterr().err
    build_push.main(["--push", "--dry-run"])
    assert "would log in to ghcr.io, push the pwmcp images" in capsys.readouterr().err


def test_main_keeps_the_historical_contract_none_on_success_systemexit_on_failure() -> None:
    vars_module = _load(SCRIPTS / "_vars.py", "pwmcp_script_cli_vars")
    ok = vars_module.script_cli(
        "t.py", long_name="t", description="d", handler=lambda a: None, preview=lambda a: "p"
    )
    assert vars_module.run_script(ok, ["--dry-run"]) is None

    def failing(_args: object) -> None:
        raise SystemExit(7)

    bad = vars_module.script_cli(
        "t.py", long_name="t", description="d", handler=failing, preview=lambda a: "p"
    )
    with pytest.raises(SystemExit) as raised:
        vars_module.run_script(bad, [])
    assert raised.value.code == 7


def test_unexpected_handler_exception_is_one_error_line_unless_traceback_is_requested(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Intentional change (AC-10): scripts used to end in a Python traceback.
    vars_module = _load(SCRIPTS / "_vars.py", "pwmcp_script_cli_vars")

    def boom(_args: object) -> None:
        raise RuntimeError("kaput")

    cli = vars_module.script_cli(
        "t.py", long_name="t", description="d", handler=boom, preview=lambda a: "p"
    )
    with pytest.raises(SystemExit) as raised:
        vars_module.run_script(cli, [])
    assert raised.value.code == 1
    assert "[ERROR] unexpected RuntimeError: kaput" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="kaput"):
        cli.run(argv=["--traceback"])


def test_release_identity_comes_from_the_committed_contract_with_no_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vars_module = _load(SCRIPTS / "_vars.py", "pwmcp_script_cli_vars")
    assert vars_module._contract_release() == RELEASE

    for text, expected in (
        (json.dumps({"release": "9.9.9-r1"}), "9.9.9-r1"),
        ("[]", None),
        (json.dumps({"release": 7}), None),
        ("{", None),
    ):
        path = tmp_path / "contract.json"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(vars_module, "_CONTRACT_FILE", path)
        assert vars_module._contract_release() == expected

    monkeypatch.setattr(vars_module, "_CONTRACT_FILE", tmp_path / "absent.json")
    assert vars_module._contract_release() is None
    with pytest.raises(VersionLookupError):
        vars_module.script_cli(
            "t.py", long_name="t", description="d", handler=lambda a: None, preview=lambda a: "p"
        )


# --- the release-step bodies behind the registrations (not coverage-judged) ----------


def test_build_bundle_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(SCRIPTS / "build-bundle.py", "pwmcp_script_cli_build_bundle")
    monkeypatch.setattr(module, "load_vars", lambda: {})
    monkeypatch.delenv("PWMCP_VERSION", raising=False)
    with pytest.raises(SystemExit) as refused:
        module.main([])
    assert refused.value.code == 1
    assert "PWMCP_VERSION not set in cmru.vars" in capsys.readouterr().err

    monkeypatch.setenv("PWMCP_VERSION", "1.2.3-r4")
    monkeypatch.setattr(module, "run_bundle", lambda toml: toml.parent / "dist" / "bundle.tar.xz")
    monkeypatch.setattr(module, "_write_installer_manifest", lambda version: None)
    monkeypatch.setattr(module, "parse_config", lambda toml: toml)
    monkeypatch.setattr(module, "create_archive", lambda _config: tmp_path / "bundle.tar.xz")
    module.main([])
    out = capsys.readouterr().out
    assert "Building bundle for pwmcp-1.2.3-r4" in out
    assert "bundle.tar.xz" in out


def test_installer_wheel_projection_covers_every_bundled_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load(SCRIPTS / "build-bundle.py", "pwmcp_script_cli_build_bundle_wheels")
    bundle_root = tmp_path / "bundle"
    wheels_dir = bundle_root / "wheels"
    wheels_dir.mkdir(parents=True)
    declared = wheels_dir / "pyee-13.0.1-py3-none-any.whl"
    missing = wheels_dir / "typing_extensions-4.16.0-py3-none-any.whl"
    declared.write_bytes(b"declared wheel")
    missing.write_bytes(b"transitive wheel")

    project_file = tmp_path / "cmru.toml"
    project_file.write_text(
        '[project.installer]\n'
        'wheels = [\n'
        '  { path = "wheels/pyee-*.whl", distribution = "pyee" },\n'
        ']\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "PROJECT_TOML", project_file)
    monkeypatch.setattr(module, "OUTPUT_BUNDLE", bundle_root)
    monkeypatch.setattr(module, "build_bundle_manifest", lambda **_kwargs: {})
    written: list[tuple[dict[str, object], Path]] = []
    monkeypatch.setattr(
        module,
        "write_manifest",
        lambda manifest, path: written.append((manifest, path)),
    )

    with pytest.raises(RuntimeError, match="missing from .*typing_extensions-4.16.0"):
        module._write_installer_manifest("1.2.3-r1")

    project_file.write_text(
        '[project.installer]\n'
        'wheels = [\n'
        '  { path = "wheels/pyee-*.whl", distribution = "pyee" },\n'
        '  { path = "wheels/typing_extensions-*.whl", distribution = "typing_extensions" },\n'
        ']\n',
        encoding="utf-8",
    )
    module._write_installer_manifest("1.2.3-r1")
    assert written[0][0]["typing_extensions"]["wheel"] == missing.name
    assert written[0][0]["pyee"]["wheel"] == declared.name


def test_generated_installer_wheel_projection_matches_cmru_config() -> None:
    config = tomllib.loads((PWMCP / "cmru.toml").read_text(encoding="utf-8"))
    expected = [
        (spec["path"], spec["distribution"])
        for spec in config["project"]["installer"]["wheels"]
    ]
    tree = ast.parse((PWMCP / "get.py").read_text(encoding="utf-8"))
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "WHEEL_SPECS"
            for target in node.targets
        )
    )
    actual = [tuple(item) for item in ast.literal_eval(assignment.value)]
    assert actual == expected
    assert "typing_extensions" in {distribution for _, distribution in actual}


def test_publish_bundle_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(SCRIPTS / "publish-bundle.py", "pwmcp_script_cli_publish_bundle")
    monkeypatch.setattr(module, "load_vars", lambda: {})
    for name in ("PWMCP_VERSION", "GITHUB_PUSH_PAT", "GITHUB_USERNAME", "GITHUB_REPO"):
        monkeypatch.delenv(name, raising=False)
    for missing, message in (
        ((), "PWMCP_VERSION not set"),
        (("PWMCP_VERSION",), "GITHUB_PUSH_PAT is required"),
        (("PWMCP_VERSION", "GITHUB_PUSH_PAT"), "GITHUB_USERNAME and GITHUB_REPO are required"),
    ):
        for name in missing:
            monkeypatch.setenv(name, "x")
        with pytest.raises(SystemExit) as refused:
            module.main([])
        assert refused.value.code == 1
        assert message in capsys.readouterr().err

    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setenv("PWMCP_VERSION", "1.2.3-r4")
    monkeypatch.setattr(module, "DIST_DIR", tmp_path)
    with pytest.raises(SystemExit):
        module.main([])
    assert "Expected prepared bundle" in capsys.readouterr().err

    (tmp_path / "pwmcp-1.2.3-r4.tar.xz").write_bytes(b"x")
    calls: list[dict[str, object]] = []

    def publish(gh: object, **kwargs: object) -> dict[str, str]:
        calls.append(kwargs)
        return {"sha256": "abc", "asset_url": "https://example.test/a", "release_tag": "pwmcp-v1.2.3-r4"}

    monkeypatch.setattr(module, "GitHubReleases", lambda owner, repo, token: (owner, repo, token))
    monkeypatch.setattr(module, "publish_versioned", publish)
    module.main([])
    out = capsys.readouterr().out
    assert calls[0]["version"] == "1.2.3-r4" and calls[0]["prefix"] == "pwmcp"
    assert "PWMCP_BUNDLE_SHA256=abc" in out and "git tag -a pwmcp-v1.2.3-r4" in out


# --- AC-24/AC-25 for the scripts: installed packages, no source-path hacks -----------

_SOURCE_PATH_HACK = re.compile(
    r"""["']cmru["']\s*/\s*["']src["']|libraries[/\\]cli-extended|cli-extended[/\\]src|CIU_FORGE_SRC"""
)


def test_scripts_import_the_installed_libraries_not_source_trees() -> None:
    sources = [PWMCP / "build-push.py", *sorted(SCRIPTS.glob("*.py"))]
    assert len(sources) >= 5
    for path in sources:
        code = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        offending = [line for line in code if _SOURCE_PATH_HACK.search(line)]
        assert not offending, (path, offending)
    library = Path(cli_extended.__file__).resolve()
    assert not library.is_relative_to(PWMCP.resolve()), library  # cli-extended: allow-path-assertion
