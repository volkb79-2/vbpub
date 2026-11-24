from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = next(
    parent for parent in (ROOT, *ROOT.parents)
    if (parent / "ciu" / "src" / "ciu").is_dir()
)
CIU_SOURCE = REPO_ROOT / "ciu" / "src"
if str(CIU_SOURCE) not in sys.path:
    sys.path.insert(0, str(CIU_SOURCE))

from ciu import deploy, profiles as profiles_pkg
from cli_extended import prompts as prompts_module
from tls_edge_cli import cli, config


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


def _tracked_stack_files() -> list[Path]:
    result = subprocess.run(
        [
            "git", "-C", str(REPO_ROOT), "ls-files", "-z",
            "--cached", "--others", "--exclude-standard",
            "--", "tls-edge/ciu-stack",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    stack_prefix = Path("tls-edge/ciu-stack")
    return [
        Path(relative)
        for relative in result.stdout.split("\0")
        if relative and Path(relative).relative_to(stack_prefix).name != "ciu.toml.j2"
    ]


def _case_root(tmp_path: Path, override: str) -> tuple[Path, Path]:
    root = tmp_path / "instance"
    stack_root = root / "stack"
    stack_dir = stack_root / "ciu-stack"
    for relative in _tracked_stack_files():
        source = REPO_ROOT / relative
        target = stack_dir / relative.relative_to(Path("tls-edge/ciu-stack"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(ROOT / "ciu.global.defaults.toml.j2", stack_root)
    (stack_dir / "ciu.toml.j2").write_text(override, encoding="utf-8")
    return root, stack_dir


def _run_ciu_check(root: Path) -> int:
    environ = {
        "CONTAINER_UID": "1000",
        "DOCKER_GID": "1000",
        "DOCKER_NETWORK_INTERNAL": "ciu-test",
        "INSTANCE_ID": "test",
    }
    global_config = deploy.load_global_config(
        root / "stack", write_rendered=False, environ=environ
    )
    profile = deploy.resolve_profiles(global_config, None, environ=environ)
    selection = deploy.build_selection(profile)
    context = profiles_pkg.render_ciu_context(profile, selection)
    rendered = deploy.render_selected_stacks(
        root / "stack", profile, selection, ciu_context=context
    )
    return deploy.action_check(
        root / "stack", profile, selection, rendered,
        live=False, json_output=False,
    )


def _run_standalone(stack_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "render_standalone.py"),
            "--stack-dir", str(stack_dir),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_cli_with_prompts(
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
            prompts_module,
            "_load_questionary_driver",
            lambda *_args, **_kwargs: FixedPrompts(),
        )
        result = cli.main(argv)
    return result, stdout.getvalue() + stderr.getvalue()


def _override(body: str = "") -> str:
    return (
        '[tls_edge]\n'
        'schema = 1\n'
        'domain = "edge.example.test"\n'
        f"{body}"
    )


@pytest.mark.parametrize(
    ("case_id", "override", "expected_message"),
    [
        (
            "acme-tls-non-443",
            _override('[tls_edge.tls]\nmode = "acme-tls"\n'
                      "[tls_edge.ports]\nhttps = 8443\n"),
            "acme-tls requires the public HTTPS port to be 443",
        ),
        (
            "calls-conflicts-with-https",
            _override("[tls_edge.calls]\nenabled = true\npublic_port = 443\n"),
            "Calls TCP conflicts with the public HTTPS TCP port",
        ),
        (
            "static-without-lineages",
            _override('[tls_edge.tls]\nmode = "static"\n'),
            "requires at least one static certificate lineage",
        ),
        (
            "unknown-http3-key",
            _override("[tls_edge.http3]\nenable = true\n"),
            "unknown override key tls_edge.http3.enable",
        ),
        (
            "newer-schema",
            '[tls_edge]\nschema = 99\ndomain = "edge.example.test"\n',
            "tls_edge.schema 99 is newer than this release supports (maximum 1)",
        ),
        ("good", _override(), None),
    ],
)
def test_every_validation_path_refuses_the_same_site_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    override: str,
    expected_message: str | None,
) -> None:
    root, stack_dir = _case_root(tmp_path / case_id, override)
    expected_rc = 0 if expected_message is None else 2

    failures: list[str] = []

    ciu_rc = _run_ciu_check(root)
    ciu_output = "".join(capsys.readouterr())
    if ciu_rc != expected_rc or (
        expected_message is not None and expected_message not in ciu_output
    ):
        failures.append(
            f"ciu check: rc={ciu_rc}, message={expected_message in ciu_output if expected_message else True}"
        )

    standalone = _run_standalone(stack_dir)
    standalone_output = standalone.stdout + standalone.stderr
    if (standalone.returncode == 0) != (expected_rc == 0) or (
        expected_message is not None and expected_message not in standalone_output
    ):
        failures.append(
            f"render_standalone --check: rc={standalone.returncode}, "
            f"message={expected_message in standalone_output if expected_message else True}"
        )

    validate_rc, validate_output = _run_cli_with_prompts(
        monkeypatch, ["validate", "--root", str(root)]
    )
    if validate_rc != expected_rc or (
        expected_message is not None and expected_message not in validate_output
    ):
        failures.append(
            f"tls-edge validate: rc={validate_rc}, "
            f"message={expected_message in validate_output if expected_message else True}"
        )

    configure_rc, configure_output = _run_cli_with_prompts(
        monkeypatch,
        ["configure", "--root", str(root), "--dry-run", "--yes"],
    )
    if configure_rc != expected_rc or (
        expected_message is not None and expected_message not in configure_output
    ):
        failures.append(
            f"tls-edge configure --dry-run: rc={configure_rc}, "
            f"message={expected_message in configure_output if expected_message else True}"
        )

    assert not failures, (
        f"{case_id}: validation paths disagree:\n" + "\n".join(failures)
    )


def test_override_open_environment_table_is_accepted(tmp_path: Path):
    root, stack_dir = _case_root(
        tmp_path,
        _override(
            "[tls_edge.acme.dns.environment]\n"
            'CF_ZONE = "example.test"\n'
        ),
    )
    defaults, overlay, effective = config.load_site(stack_dir)
    overlay = config.migrate(overlay)
    config.check_override(defaults, overlay)
    assert effective["tls_edge"]["acme"]["dns"]["environment"]["CF_ZONE"] == (
        "example.test"
    )


def test_override_rejects_jinja_marker_and_names_the_file(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        _override('note = "{{ rendered_later }}"\n'),
    )
    with pytest.raises(config.ConfigError, match=f"{stack_dir / 'ciu.toml.j2'}"):
        config.load_site(stack_dir)


def test_override_rejects_scalar_for_default_table(tmp_path: Path):
    _, stack_dir = _case_root(tmp_path, _override("http3 = true\n"))
    defaults, overlay, _ = config.load_site(stack_dir)
    with pytest.raises(config.ConfigError, match="tls_edge.http3"):
        config.check_override(defaults, overlay)


def test_missing_schema_means_schema_one(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        '[tls_edge]\ndomain = "edge.example.test"\n',
    )
    defaults, overlay, effective = config.load_site(stack_dir)
    assert config.migrate(overlay) == overlay
    config.check_override(defaults, overlay)
    assert effective["tls_edge"]["schema"] == 1


def test_configure_writes_schema_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _case_root(tmp_path, _override())
    override_path = root / "stack" / "ciu-stack" / "ciu.toml.j2"
    override_path.unlink()

    result, _ = _run_cli_with_prompts(
        monkeypatch,
        ["configure", "--root", str(root), "--yes"],
    )
    assert result == 0
    written = tomllib.loads(override_path.read_text(encoding="utf-8"))
    assert written["tls_edge"]["schema"] == 1
