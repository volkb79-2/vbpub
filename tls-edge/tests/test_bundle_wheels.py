"""Behavioural tests for generated bundle requirements and closed wheel selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "bundle_wheels.py"
SPEC = importlib.util.spec_from_file_location("tls_edge_bundle_wheels", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bundle_wheels = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_wheels)


def test_merge_requirements_uses_the_strongest_floor_once() -> None:
    merged = bundle_wheels.merge_requirements([
        "Jinja2>=3.1.2",
        "jinja2>=3.1.6",
        "cli-extended>=0.8.0",
        "tls-edge-cli>=4",
        "PyYAML>=6.0.1",
    ])

    assert merged == ["Jinja2>=3.1.6", "PyYAML>=6.0.1"]


def test_merge_requirements_refuses_an_unmergeable_constraint() -> None:
    with pytest.raises(SystemExit) as error:
        bundle_wheels.merge_requirements(["Jinja2~=3.1"])

    assert error.value.code == 1


def test_requirements_are_rendered_as_a_sorted_exact_pinned_closure(tmp_path: Path) -> None:
    output = tmp_path / "requirements-bundle.txt"
    bundle_wheels.write_requirements(
        output,
        resolved={
            "jinja2": ("Jinja2", "3.1.6"),
            "markupsafe": ("MarkupSafe", "3.0.4"),
            "pyyaml": ("PyYAML", "6.0.3"),
            "questionary": ("questionary", "2.1.1"),
            "tomli-w": ("tomli_w", "1.2.0"),
            "tomlkit": ("tomlkit", "0.15.2"),
        },
    )

    assert output.read_text(encoding="utf-8") == bundle_wheels.render_requirements({
        "jinja2": ("Jinja2", "3.1.6"),
        "markupsafe": ("MarkupSafe", "3.0.4"),
        "pyyaml": ("PyYAML", "6.0.3"),
        "questionary": ("questionary", "2.1.1"),
        "tomli-w": ("tomli_w", "1.2.0"),
        "tomlkit": ("tomlkit", "0.15.2"),
    })
    dependency_lines = [
        line for line in output.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    assert dependency_lines
    assert all("==" in line for line in dependency_lines)


def test_committed_bundle_requirements_are_an_exact_third_party_lock() -> None:
    lines = [
        line
        for line in bundle_wheels.REQUIREMENTS_OUT.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    names = {
        bundle_wheels.normal_name(line.partition("==")[0])
        for line in lines
        if "==" in line
    }

    assert len(lines) == len(names)
    assert all(line.count("==") == 1 for line in lines)
    assert {"jinja2", "pyyaml", "questionary", "tomli-w", "tomlkit"} <= names
    assert not (names & bundle_wheels.FIRST_PARTY)


def test_requirements_check_is_red_for_a_stale_file(tmp_path: Path) -> None:
    output = tmp_path / "requirements-bundle.txt"
    output.write_text("Jinja2>=0\n", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        bundle_wheels.write_requirements(
            output,
            check=True,
            resolved={
                "jinja2": ("Jinja2", "3.1.6"),
                "markupsafe": ("MarkupSafe", "3.0.4"),
                "pyyaml": ("PyYAML", "6.0.3"),
                "questionary": ("questionary", "2.1.1"),
                "tomli-w": ("tomli_w", "1.2.0"),
                "tomlkit": ("tomlkit", "0.15.2"),
            },
        )
    assert error.value.code == 1


def test_wheel_requirements_are_derived_from_the_resolved_files(tmp_path: Path) -> None:
    wheels = [
        tmp_path / "Jinja2-3.1.6-py3-none-any.whl",
        tmp_path / "MarkupSafe-3.0.4-cp313-cp313-manylinux_2_17_x86_64.whl",
    ]
    for wheel in wheels:
        wheel.write_bytes(b"")

    assert bundle_wheels.wheel_requirements(wheels) == {
        "jinja2": ("Jinja2", "3.1.6"),
        "markupsafe": ("MarkupSafe", "3.0.4"),
    }


def test_requirements_render_refuses_a_missing_direct_dependency() -> None:
    with pytest.raises(SystemExit) as error:
        bundle_wheels.render_requirements({"jinja2": ("Jinja2", "3.1.6")})

    assert error.value.code == 1


def test_wheel_requirements_refuse_an_unparseable_asset(tmp_path: Path) -> None:
    (tmp_path / "not-a-wheel.whl").write_bytes(b"")

    with pytest.raises(SystemExit) as error:
        bundle_wheels.wheel_requirements([tmp_path / "not-a-wheel.whl"])

    assert error.value.code == 1


def test_version_env_comes_from_the_committed_version_file() -> None:
    version = (bundle_wheels.TLS_EDGE_DIR / "VERSION").read_text(encoding="utf-8").strip()

    assert bundle_wheels.version_env() == f"TLS_EDGE_VERSION={version}"


def test_wheel_inventory_refuses_an_extra_distribution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = tmp_path / "bundle"
    wheels = bundle / "wheels"
    wheels.mkdir(parents=True)
    for pattern, _distribution in bundle_wheels.wheel_specs():
        name = Path(pattern).name.replace("*", "1.0.0")
        (wheels / name).write_bytes(b"")
    (wheels / "evil-1.0.0-py3-none-any.whl").write_bytes(b"")

    with pytest.raises(SystemExit) as error:
        bundle_wheels.verify_wheel_inventory(bundle)

    assert error.value.code == 1
    assert "absent from [project.installer.wheels]" in capsys.readouterr().err


def test_bytecode_cache_is_refused(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    cache = bundle / "__pycache__"
    cache.mkdir()
    (cache / "evil.pyc").write_bytes(b"")

    with pytest.raises(SystemExit) as error:
        bundle_wheels.reject_bytecode(bundle)

    assert error.value.code == 1


def test_retain_copies_the_publisher_inputs(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    bundle_wheels.retain(dist)
    assert (dist / "VERSION").read_bytes() == (bundle_wheels.TLS_EDGE_DIR / "VERSION").read_bytes()
    assert (dist / "get.py").read_bytes() == (bundle_wheels.TLS_EDGE_DIR / "get.py").read_bytes()


def test_github_pin_is_refused_when_latest_differs(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    class Host:
        def __init__(self, *_args):
            pass

        def resolve_latest(self, *_args, **_kwargs):
            return {
                "version": "0.9.0",
                "asset": "cli_extended-0.9.0-py3-none-any.whl",
                "url": "https://github.com/example/repo/releases/download/x/w.whl",
                "sha256": "0" * 64,
            }

    import cmru.hosts.github
    monkeypatch.setattr(cmru.hosts.github, "GitHubReleaseHost", Host)
    monkeypatch.setattr(bundle_wheels, "_github_settings", lambda: ("example", "repo"))

    with pytest.raises(SystemExit) as error:
        bundle_wheels.fetch_github_wheel(
            tmp_path,
            distribution="cli-extended",
            tag_prefix="cli-extended-v",
            pinned="0.8.0",
        )

    assert error.value.code == 1
    assert "but 0.8.0 is pinned" in capsys.readouterr().err


def test_github_fetcher_refuses_another_distributions_asset(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    class Host:
        def __init__(self, *_args):
            pass

        def resolve_latest(self, *_args, **_kwargs):
            return {
                "version": "1.0.0",
                "asset": "evil-1.0.0-py3-none-any.whl",
                "url": "https://github.com/example/repo/releases/download/x/evil.whl",
                "sha256": "0" * 64,
            }

    import cmru.hosts.github
    monkeypatch.setattr(cmru.hosts.github, "GitHubReleaseHost", Host)
    monkeypatch.setattr(bundle_wheels, "_github_settings", lambda: ("example", "repo"))

    with pytest.raises(SystemExit) as error:
        bundle_wheels.fetch_github_wheel(
            tmp_path,
            distribution="cli-extended",
            tag_prefix="cli-extended-v",
        )

    assert error.value.code == 1
    assert "unexpected cli-extended wheel" in capsys.readouterr().err


def test_build_flags_do_not_require_a_local_python_resolution(monkeypatch, tmp_path: Path) -> None:
    wheel = tmp_path / "tls_edge_cli-4.0.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    monkeypatch.setattr(
        bundle_wheels.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    result = bundle_wheels._build_tls_edge_cli(tmp_path)

    assert result == wheel
