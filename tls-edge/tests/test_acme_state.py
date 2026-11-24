"""Behavioral coverage for ACME storage in new and pre-3.2 instances."""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from cli_extended import identity as identity_module

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = next(
    parent for parent in (ROOT, *ROOT.parents)
    if (parent / "ciu" / "src" / "ciu").is_dir()
)
CIU_SOURCE = REPO_ROOT / "ciu" / "src"
if str(CIU_SOURCE) not in sys.path:
    sys.path.insert(0, str(CIU_SOURCE))

from ciu import composefile, config_model, deploy
from tls_edge_cli import cli

ENVIRON = {
    "CONTAINER_UID": "1000",
    "DOCKER_GID": "1000",
    "DOCKER_NETWORK_INTERNAL": "ciu-test",
    "INSTANCE_ID": "test",
}


@pytest.fixture(autouse=True)
def _cli_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity_module, "installed_version", lambda _name: "0.0.0")


def _copy_stack(stack_dir: Path) -> None:
    for source in (ROOT / "ciu-stack").iterdir():
        if source.name == "ciu.toml.j2":
            continue
        target = stack_dir / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def _instance(tmp_path: Path, *, marker: bool) -> tuple[Path, Path]:
    root = tmp_path / "instance" / "stack"
    stack_dir = root / "ciu-stack"
    _copy_stack(stack_dir)
    shutil.copy2(ROOT / "ciu.global.defaults.toml.j2", root)
    if marker:
        (root.parent / "shared" / "acme").mkdir(parents=True, mode=0o700)
    return root, stack_dir


def _configure(root: Path, tmp_path: Path, extra_answers: str = "") -> None:
    answers = tmp_path / "answers.toml"
    answers.write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n' + extra_answers,
        encoding="utf-8",
    )
    assert cli.main(
        ["configure", "--root", str(root), "--answers", str(answers), "--yes"]
    ) == 0


def _render_compose(stack_dir: Path) -> dict:
    rendered = config_model.render_stack(
        stack_dir,
        {},
        preserve_state=False,
        write_rendered=False,
        environ=ENVIRON,
    )
    deploy._resolve_hostdirs_for_render(rendered, stack_dir, REPO_ROOT)
    text = composefile.render_compose(
        stack_dir / "ciu.compose.yml.j2",
        rendered,
        environ=ENVIRON,
    )
    return yaml.safe_load(text)


def _acme_mount(compose: dict) -> str:
    return next(
        mount
        for mount in compose["services"]["traefik"]["volumes"]
        if mount.endswith(":/var/lib/traefik")
    )


def test_new_instance_uses_hostdir_outside_the_stack(tmp_path: Path) -> None:
    root, stack_dir = _instance(tmp_path, marker=True)
    _configure(root, tmp_path)

    override = tomllib.loads((stack_dir / "ciu.toml.j2").read_text(encoding="utf-8"))
    assert override["tls_edge"]["acme"]["store"] == "hostdir"
    assert override["tls_edge"]["traefik"]["hostdir"]["acme"]["path"] == (
        "../../shared/acme"
    )

    compose = _render_compose(stack_dir)
    source = Path(_acme_mount(compose).removesuffix(":/var/lib/traefik"))
    assert source == (root.parent / "shared" / "acme").resolve()
    assert not source.is_relative_to(root.resolve())
    assert "acme-data" not in (compose.get("volumes") or {})


@pytest.mark.parametrize("configure_it", [True, False], ids=["upgrade", "checkout"])
def test_no_marker_keeps_volume_storage(
    tmp_path: Path, configure_it: bool
) -> None:
    root, stack_dir = _instance(tmp_path, marker=False)
    if configure_it:
        _configure(root, tmp_path)
        override = tomllib.loads(
            (stack_dir / "ciu.toml.j2").read_text(encoding="utf-8")
        )
        assert override["tls_edge"]["acme"]["store"] == "volume"

    compose = _render_compose(stack_dir)
    assert _acme_mount(compose) == "acme-data:/var/lib/traefik"
    assert "acme-data" in compose["volumes"]


def test_external_volume_name_is_unchanged(tmp_path: Path) -> None:
    _, stack_dir = _instance(tmp_path, marker=False)
    (stack_dir / "ciu.toml.j2").write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n'
        '[tls_edge.acme]\nvolume_name = "edge-proxy_acme-data"\n',
        encoding="utf-8",
    )

    compose = _render_compose(stack_dir)
    assert _acme_mount(compose) == "acme-data:/var/lib/traefik"
    assert compose["volumes"]["acme-data"] == {
        "name": "edge-proxy_acme-data",
        "external": True,
    }


def test_new_instance_can_still_adopt_an_external_volume(tmp_path: Path) -> None:
    root, stack_dir = _instance(tmp_path, marker=True)
    _configure(
        root,
        tmp_path,
        '[tls_edge.acme]\nvolume_name = "edge-proxy_acme-data"\n',
    )

    override = tomllib.loads((stack_dir / "ciu.toml.j2").read_text(encoding="utf-8"))
    assert override["tls_edge"]["acme"]["store"] == "volume"
    assert override["tls_edge"]["acme"]["volume_name"] == "edge-proxy_acme-data"

    compose = _render_compose(stack_dir)
    assert _acme_mount(compose) == "acme-data:/var/lib/traefik"
    assert compose["volumes"]["acme-data"] == {
        "name": "edge-proxy_acme-data",
        "external": True,
    }


def test_marker_does_not_override_an_explicit_volume_store(tmp_path: Path) -> None:
    root, stack_dir = _instance(tmp_path, marker=True)
    (stack_dir / "ciu.toml.j2").write_text(
        '[tls_edge]\ndomain = "old.example.test"\n'
        '[tls_edge.acme]\nstore = "volume"\n',
        encoding="utf-8",
    )
    _configure(root, tmp_path)

    override = tomllib.loads((stack_dir / "ciu.toml.j2").read_text(encoding="utf-8"))
    assert override["tls_edge"]["acme"]["store"] == "volume"
    assert "traefik" not in override["tls_edge"]
    assert _acme_mount(_render_compose(stack_dir)) == "acme-data:/var/lib/traefik"


def test_existing_hostdir_store_and_path_are_preserved(tmp_path: Path) -> None:
    root, stack_dir = _instance(tmp_path, marker=False)
    (stack_dir / "ciu.toml.j2").write_text(
        '[tls_edge]\ndomain = "old.example.test"\n'
        '[tls_edge.acme]\nstore = "hostdir"\n'
        '[tls_edge.traefik.hostdir]\n'
        'acme = { path = "../../shared/custom-acme", uid = 0, gid = 0, mode = "0700" }\n',
        encoding="utf-8",
    )
    _configure(root, tmp_path)

    override = tomllib.loads((stack_dir / "ciu.toml.j2").read_text(encoding="utf-8"))
    assert override["tls_edge"]["acme"]["store"] == "hostdir"
    assert override["tls_edge"]["traefik"]["hostdir"]["acme"]["path"] == (
        "../../shared/custom-acme"
    )
