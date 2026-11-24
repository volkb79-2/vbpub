from __future__ import annotations

import io
import importlib.util
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

from ciu import config_model, deploy
from ciu.deploy_pkg import profiles as profiles_pkg
from cli_extended import identity as identity_module
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
    rendered = {}
    for entry in selection:
        stack_dir = root / "stack" / entry["path"]
        rendered[entry["path"]] = config_model.render_stack(
            stack_dir,
            global_config=profile.config,
            preserve_state=False,
            ciu_context=context,
            write_rendered=False,
            environ=environ,
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
        context.setattr(identity_module, "installed_version", lambda _name: "0.0.0")
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
    ("case_id", "override", "configure_answers", "expected_message"),
    [
        (
            "acme-tls-non-443",
            _override('[tls_edge.tls]\nmode = "acme-tls"\n'
                      "[tls_edge.ports]\nhttps = 8443\n"),
            '[tls_edge.tls]\nmode = "acme-tls"\n'
            "[tls_edge.ports]\nhttps = 8443\n",
            "acme-tls requires the public HTTPS port to be 443",
        ),
        (
            "calls-conflicts-with-https",
            _override("[tls_edge.calls]\nenabled = true\npublic_port = 443\n"),
            "[tls_edge.ports]\nhttps = 443\n"
            "[tls_edge.calls]\nenabled = true\npublic_port = 443\n",
            "Calls TCP conflicts with the public HTTPS TCP port",
        ),
        (
            "static-without-lineages",
            _override('[tls_edge.tls]\nmode = "static"\n'),
            '[tls_edge.tls]\nmode = "static"\n'
            "[tls_edge.static]\ndomains = []\n",
            "requires at least one static certificate lineage",
        ),
        (
            "unknown-http3-key",
            _override("[tls_edge.http3]\nenable = true\n"),
            '[tls_edge]\ndomain = "edge.example.test"\n',
            "unknown override key tls_edge.http3.enable",
        ),
        (
            "newer-schema",
            '[tls_edge]\nschema = 99\ndomain = "edge.example.test"\n',
            '[tls_edge]\ndomain = "edge.example.test"\n',
            "tls_edge.schema 99 is newer than this release supports (maximum 1)",
        ),
        (
            "good",
            _override(),
            '[tls_edge]\ndomain = "edge.example.test"\n'
            '[tls_edge.tls]\nmode = "acme-tls"\n'
            "[tls_edge.ports]\nhttps = 443\n",
            None,
        ),
        (
            "hostdir-empty-path",
            _override(
                '[tls_edge.acme]\nstore = "hostdir"\n'
                '[tls_edge.traefik.hostdir]\n'
                'acme = { path = "", uid = 0, gid = 0, mode = "0700" }\n'
            ),
            '[tls_edge]\ndomain = "edge.example.test"\n',
            'tls_edge.traefik.hostdir.acme.path is required when '
            'tls_edge.acme.store = "hostdir"',
        ),
        (
            "hostdir-inside-stack",
            _override(
                '[tls_edge.acme]\nstore = "hostdir"\n'
                '[tls_edge.traefik.hostdir]\n'
                'acme = { path = "./vol-traefik-acme", uid = 0, gid = 0, mode = "0700" }\n'
            ),
            '[tls_edge]\ndomain = "edge.example.test"\n',
            "tls_edge.traefik.hostdir.acme.path must resolve outside stack/",
        ),
        (
            "hostdir-non-root",
            _override(
                '[tls_edge.acme]\nstore = "hostdir"\n'
                '[tls_edge.traefik.hostdir]\n'
                'acme = { path = "../../shared/acme", uid = 1000, gid = 0, mode = "0700" }\n'
            ),
            '[tls_edge]\ndomain = "edge.example.test"\n',
            'tls_edge.traefik.hostdir.acme.uid must be 0 when '
            'tls_edge.acme.store = "hostdir"',
        ),
        (
            "hostdir-with-volume-name",
            _override(
                '[tls_edge.acme]\nstore = "hostdir"\n'
                'volume_name = "edge-proxy_acme-data"\n'
                '[tls_edge.traefik.hostdir]\n'
                'acme = { path = "../../shared/acme", uid = 0, gid = 0, mode = "0700" }\n'
            ),
            '[tls_edge]\ndomain = "edge.example.test"\n',
            'tls_edge.acme.volume_name and tls_edge.acme.store = "hostdir" '
            "are mutually exclusive",
        ),
    ],
)
def test_every_validation_path_refuses_the_same_site_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    override: str,
    configure_answers: str,
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

    answers_path = root / "configure-answers.toml"
    answers_path.write_text(configure_answers, encoding="utf-8")
    configure_rc, configure_output = _run_cli_with_prompts(
        monkeypatch,
        [
            "configure", "--root", str(root), "--answers", str(answers_path),
            "--dry-run", "--yes",
        ],
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


def test_override_rejects_table_for_default_scalar(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        _override('[tls_edge.ports.http]\nvalue = 8080\n'),
    )
    defaults, overlay, _ = config.load_site(stack_dir)
    with pytest.raises(config.ConfigError, match="tls_edge.ports.http"):
        config.check_override(defaults, overlay)


def test_override_schema_must_be_an_integer(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        '[tls_edge]\nschema = "one"\ndomain = "edge.example.test"\n',
    )
    defaults, overlay, _ = config.load_site(stack_dir)
    with pytest.raises(config.ConfigError, match="tls_edge.schema must be an integer"):
        config.check_override(defaults, overlay)


def test_override_schema_must_be_at_least_one(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        '[tls_edge]\nschema = 0\ndomain = "edge.example.test"\n',
    )
    defaults, overlay, _ = config.load_site(stack_dir)
    with pytest.raises(config.ConfigError, match="tls_edge.schema must be at least 1"):
        config.check_override(defaults, overlay)


@pytest.mark.parametrize(
    "pre_secrets",
    (
        [],
        ["hooks/other.py"],
        "hooks/validate_edge.py",
    ),
)
def test_override_pre_secrets_must_include_the_validator(
    tmp_path: Path,
    pre_secrets: object,
) -> None:
    value = repr(pre_secrets)
    _, stack_dir = _case_root(
        tmp_path,
        _override(f"[tls_edge.hooks]\npre_secrets = {value}\n"),
    )
    defaults, overlay, _ = config.load_site(stack_dir)
    with pytest.raises(
        config.ConfigError,
        match="tls_edge.hooks.pre_secrets must contain hooks/validate_edge.py",
    ):
        config.check_override(defaults, overlay)


def test_override_open_hooks_table_accepts_the_validator(tmp_path: Path) -> None:
    _, stack_dir = _case_root(
        tmp_path,
        _override(
            "[tls_edge.hooks]\n"
            'pre_secrets = ["hooks/audit.py", "hooks/validate_edge.py"]\n'
        ),
    )
    defaults, overlay, effective = config.load_site(stack_dir)
    config.check_override(defaults, overlay)
    assert effective["tls_edge"]["hooks"]["pre_secrets"] == [
        "hooks/audit.py",
        "hooks/validate_edge.py",
    ]


def test_removing_validator_hook_is_refused_by_ciu_and_validate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _case_root(
        tmp_path,
        _override("[tls_edge.hooks]\npre_secrets = []\n"),
    )
    message = "tls_edge.hooks.pre_secrets must contain hooks/validate_edge.py"

    ciu_rc = _run_ciu_check(root)
    ciu_output = "".join(capsys.readouterr())
    assert ciu_rc == 2
    assert message in ciu_output

    validate_rc, validate_output = _run_cli_with_prompts(
        monkeypatch, ["validate", "--root", str(root)]
    )
    assert validate_rc == 2
    assert message in validate_output


def test_standalone_check_requires_a_site_domain(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        '[tls_edge]\nschema = 1\ndomain = ""\n',
    )
    result = _run_standalone(stack_dir)
    assert result.returncode != 0
    assert "domain is required" in result.stdout + result.stderr


def test_standalone_refuses_hostdir_acme_storage(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        _override(
            '[tls_edge.acme]\nstore = "hostdir"\n'
            '[tls_edge.traefik.hostdir]\n'
            'acme = { path = "../../shared/acme", uid = 0, gid = 0, mode = "0700" }\n'
        ),
    )
    result = _run_standalone(stack_dir)
    assert result.returncode != 0
    assert (
        'tls_edge.acme.store = "hostdir" requires ciu; '
        'the standalone renderer only supports "volume"'
    ) in result.stdout + result.stderr


def test_override_rejects_non_utf8_text(tmp_path: Path):
    _, stack_dir = _case_root(tmp_path, _override())
    (stack_dir / "ciu.toml.j2").write_bytes(b"\xff\xfe")
    with pytest.raises(config.ConfigError, match="cannot read site override"):
        config.load_site(stack_dir)


def test_missing_schema_means_schema_one(tmp_path: Path):
    _, stack_dir = _case_root(
        tmp_path,
        '[tls_edge]\ndomain = "edge.example.test"\n',
    )
    defaults, overlay, effective = config.load_site(stack_dir)
    assert config.migrate(overlay) == overlay
    config.check_override(defaults, overlay)
    assert effective["tls_edge"]["schema"] == 1


def test_hook_run_rechecks_the_sparse_override(tmp_path: Path) -> None:
    _, stack_dir = _case_root(tmp_path, _override("[tls_edge.http3]\nenable = true\n"))
    _, _, effective = config.load_site(stack_dir)
    spec = importlib.util.spec_from_file_location(
        "validate_edge_hook", stack_dir / "hooks" / "validate_edge.py"
    )
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    with pytest.raises(
        config.ConfigError,
        match="unknown override key tls_edge.http3.enable",
    ):
        hook.run(effective, None)
    (stack_dir / "ciu.toml.j2").write_text(_override(), encoding="utf-8")
    _, _, effective = config.load_site(stack_dir)
    assert hook.run(effective, None) == {}


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
