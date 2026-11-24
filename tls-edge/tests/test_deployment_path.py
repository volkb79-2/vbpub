"""One supported deployment path: get.py + ciu, with generated trial output."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli_extended import identity as identity_module
from cli_extended import prompts as prompts_module

from tls_edge_cli import cli


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
INSTALL_SCRIPT = "install" + ".sh"
GENERATED_DIR = "edge" + "-proxy"
MIDDLEWARES_CITATION = GENERATED_DIR + "/conf.d/middlewares.yml"
TRIAL_FILES = {
    ".env.example",
    "conf.d/certs.yml",
    "conf.d/middlewares.yml",
    "conf.d/options.yml",
    "docker-compose.yml",
    "traefik.yml",
}


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class FixedPrompts:
    def text(self, _message, *, default=None, required=True):
        return default if default is not None else "edge.example.test"

    def password(self, _message, *, required=True):
        return "unused"

    def confirm(self, _message, *, default=False):
        return default

    def select(self, _message, choices, *, default=None):
        return default if default is not None else choices[0]

    def checkbox(self, _message, choices, *, default=()):
        return list(default)


def _tracked(root: Path, *paths: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *paths],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def _copy_tracked_stack(destination: Path) -> Path:
    """Create a checkout-shaped stack dir from tracked ciu-stack files only."""
    for relative in _tracked(ROOT, "ciu-stack"):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return destination


def _domain_override(stack: Path, domain: str = "edge.example.test") -> Path:
    path = stack / "ciu-stack" / "ciu.toml.j2"
    path.write_text(f'[tls_edge]\ndomain = "{domain}"\n', encoding="utf-8")
    return path


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> tuple[int, str]:
    stdin = TTYBuffer()
    stdout = TTYBuffer()
    stderr = TTYBuffer()
    with monkeypatch.context() as context:
        context.setattr(sys, "stdin", stdin)
        context.setattr(sys, "stdout", stdout)
        context.setattr(sys, "stderr", stderr)
        context.setattr(
            identity_module,
            "installed_version",
            lambda _name: "0.0.0",
        )
        context.setattr(
            prompts_module,
            "_load_questionary_driver",
            lambda *_args, **_kwargs: FixedPrompts(),
        )
        result = cli.main(argv)
    return result, stdout.getvalue() + stderr.getvalue()


def _tree_digest(tree: Path) -> tuple[tuple[str, str], ...]:
    entries = []
    for path in sorted(tree.rglob("*")):
        relative = path.relative_to(tree).as_posix()
        if path.is_file():
            entries.append(
                (relative, hashlib.sha256(path.read_bytes()).hexdigest())
            )
    return tuple(entries)


def _fake_docker(tmp_path: Path, name: str = "") -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.argv"
    docker = bin_dir / "docker"
    docker.write_text(
        """\
#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ "$1" == "ps" && -n "${FAKE_DOCKER_NAME:-}" ]]; then
    printf '%s\\n' "$FAKE_DOCKER_NAME"
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir, log


def test_install_script_is_untracked_and_unnamed_by_live_docs() -> None:
    assert not (ROOT / INSTALL_SCRIPT.replace("install", "scripts/install")).exists()
    offenders = []
    for relative in _tracked(REPO_ROOT, "tls-edge", "voice-screen-share"):
        if (
            "archive/" in relative
            or relative.endswith("/CHANGES.md")
            or "/docs/design/" in relative
        ):
            continue
        path = REPO_ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if INSTALL_SCRIPT in text:
            offenders.append(relative)
    assert offenders == []


def test_generated_tree_is_not_tracked_and_ignored() -> None:
    assert _tracked(ROOT, GENERATED_DIR) == []
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert f"/{GENERATED_DIR}/" in ignored


def test_standalone_trial_render_has_full_tree_and_stamp(tmp_path: Path) -> None:
    out = tmp_path / "trial"
    base_argv = [
        sys.executable,
        str(ROOT / "scripts" / "render_standalone.py"),
        "--stack-dir",
        str(ROOT / "ciu-stack"),
        "--out",
        str(out),
        "--defaults-only",
    ]
    subprocess.run([*base_argv, "--stamp"], cwd=ROOT, check=True)
    assert {
        path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
    } == TRIAL_FILES
    certs = (out / "conf.d" / "certs.yml").read_text(encoding="utf-8")
    assert re.search(r"^# rendered: \S+$", certs, re.MULTILINE)


def test_checkout_validate_works_without_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _copy_tracked_stack(tmp_path / "checkout")
    _domain_override(stack)
    assert not (stack / "consumer.env").exists()
    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(stack)],
    )
    assert result == 0, output
    assert "tls-edge configuration is valid." in output


def test_checkout_show_works_without_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _copy_tracked_stack(tmp_path / "checkout")
    assert not (stack / "consumer.env").exists()
    result, output = _run_cli(
        monkeypatch,
        ["show", "--root", str(stack)],
    )
    assert result == 0, output
    assert "Base domain: (unset)" in output


def test_checkout_configure_dry_run_works_without_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _copy_tracked_stack(tmp_path / "checkout")
    answers = tmp_path / "answers.toml"
    answers.write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n',
        encoding="utf-8",
    )
    assert not (stack / "consumer.env").exists()
    result, output = _run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(stack),
            "--answers",
            str(answers),
            "--dry-run",
            "--yes",
        ],
    )
    assert result == 0, output
    assert "Dry run: no files were changed." in output
    assert not (stack / "consumer.env").exists()


def test_cli_refuses_a_directory_without_ciu_stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_a_stack = tmp_path / "not-a-stack"
    not_a_stack.mkdir()
    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(not_a_stack)],
    )
    assert result == 2, output
    assert "is not a tls-edge stack directory" in output
    assert str(not_a_stack / "ciu-stack") in output


def test_certbot_hook_restarts_detected_container_without_tree_writes(
    tmp_path: Path,
) -> None:
    stack = _copy_tracked_stack(tmp_path / "instance")
    script = stack / "scripts" / "certbot-deploy-hook.sh"
    script.parent.mkdir()
    shutil.copy2(ROOT / "scripts" / "certbot-deploy-hook.sh", script)
    certs = stack / GENERATED_DIR / "conf.d" / "certs.yml"
    certs.parent.mkdir(parents=True)
    certs.write_text("# rendered: sentinel\n", encoding="utf-8")
    before = _tree_digest(stack)

    bin_dir, log = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DOCKER_NAME": "tls-edge-example-traefik",
    }
    result = subprocess.run(
        ["bash", str(script)],
        cwd=stack,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert "restart --time 5 tls-edge-example-traefik" in calls
    assert before == _tree_digest(stack)


def test_certbot_hook_without_a_container_is_a_noop(tmp_path: Path) -> None:
    stack = _copy_tracked_stack(tmp_path / "instance")
    script = stack / "scripts" / "certbot-deploy-hook.sh"
    script.parent.mkdir()
    shutil.copy2(ROOT / "scripts" / "certbot-deploy-hook.sh", script)
    certs = stack / GENERATED_DIR / "conf.d" / "certs.yml"
    certs.parent.mkdir(parents=True)
    certs.write_text("# rendered: sentinel\n", encoding="utf-8")
    before = _tree_digest(stack)

    bin_dir, log = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
    }
    result = subprocess.run(
        ["bash", str(script)],
        cwd=stack,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no running Traefik container found" in result.stdout
    assert "restart" not in log.read_text(encoding="utf-8")
    assert before == _tree_digest(stack)


def test_external_middleware_citations_use_the_template_source() -> None:
    cited = (
        REPO_ROOT / "voice-screen-share" / "mattermost-server" / "README.md",
        REPO_ROOT
        / "voice-screen-share"
        / "mattermost-server"
        / "ciu.compose.yml.j2",
        REPO_ROOT
        / "nyxloom"
        / "nyxloom-trove"
        / "reports"
        / "nyxloom-P110-REPORT.md",
    )
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in cited
        if MIDDLEWARES_CITATION in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
