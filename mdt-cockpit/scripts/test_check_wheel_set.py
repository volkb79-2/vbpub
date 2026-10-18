"""An image must be built from exactly the first-party wheels it should contain (C6, R1)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_wheel_set  # noqa: E402
import local_dev  # noqa: E402

DECLARED = "# comment\ncli-extended  # trailing\nciu\ncmru\n\ntopos\nnyxloom\n"
COMPLETE = [
    "cli_extended-0.4.0-py3-none-any.whl",
    "ciu-7.16.0-py3-none-any.whl",
    "cmru-6.1.0-py3-none-any.whl",
    "topos-0.4.1-py3-none-any.whl",
    "nyxloom-0.10.1-py3-none-any.whl",
]


def _sha(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _release_dir(tmp_path: Path, wheels: list[str], *, report: list[str] | None = None) -> tuple[Path, Path]:
    """pip/wheels with files `wheels`; the resolver report names `report` (default: the same)."""
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    for name in wheels:
        (wheels_dir / name).write_bytes(name.encode())
    reported = wheels if report is None else report
    (wheels_dir / "RESOLVED.json").write_text(
        json.dumps({"schema": 1, "wheels": [
            {"name": n.split("-")[0], "filename": n, "sha256": _sha(n)} for n in reported]}),
        encoding="utf-8",
    )
    wheels_list = tmp_path / "wheels.list"
    wheels_list.write_text(DECLARED, encoding="utf-8")
    return wheels_dir, wheels_list


def _local_context(tmp_path: Path, wheels: list[str], *, recorded: list[str] | None = None) -> Path:
    context = tmp_path / "ctx"
    (context / "wheels").mkdir(parents=True)
    for name in wheels:
        (context / "wheels" / name).write_bytes(name.encode())
    (context / "provenance.json").write_text(
        json.dumps({"schema": 1, "artifacts": [
            {"name": n.split("-")[0], "staged": f"wheels/{n}", "sha256": _sha(n)}
            for n in (wheels if recorded is None else recorded)]}),
        encoding="utf-8",
    )
    return context


def _problems(wheels_dir: Path, wheels_list: Path, **kwargs) -> list[str]:
    return check_wheel_set.check(wheels_dir, wheels_list, **kwargs)[0]


def test_a_complete_reported_set_passes_and_names_are_compared_as_distributions(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)

    problems, wheels = check_wheel_set.check(wheels_dir, wheels_list)

    assert problems == []
    assert sorted(w["name"] for w in wheels) == sorted(["cli-extended", "ciu", "cmru", "topos", "nyxloom"])
    assert {w["source"] for w in wheels} == {"release"}


def test_an_empty_wheel_directory_is_an_error(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, [])

    problems = _problems(wheels_dir, wheels_list)

    assert any("no first-party wheels at all" in p and "cli-extended" in p for p in problems)


def test_a_missing_wheel_directory_is_an_error(tmp_path: Path) -> None:
    wheels_list = tmp_path / "wheels.list"
    wheels_list.write_text(DECLARED, encoding="utf-8")

    assert _problems(tmp_path / "does-not-exist", wheels_list)


def test_a_missing_resolver_report_is_an_error(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)
    (wheels_dir / "RESOLVED.json").unlink()

    assert any("resolver report" in p for p in _problems(wheels_dir, wheels_list))


def test_a_release_build_refuses_a_stray_wheel_the_resolver_did_not_report(tmp_path: Path) -> None:
    """R1: a local-dev wheel (or any leftover) in pip/wheels/ satisfies the declared name,
    so only the equality with the resolver's report can catch it."""
    resolved = [w for w in COMPLETE if not w.startswith("nyxloom")]  # no release exists yet
    wheels_dir, wheels_list = _release_dir(tmp_path, resolved + ["nyxloom-0.10.1-py3-none-any.whl"], report=resolved)

    problems = _problems(wheels_dir, wheels_list)

    assert any("nobody reported" in p and "nyxloom-0.10.1" in p for p in problems)


def test_a_reported_wheel_that_is_missing_or_changed_is_an_error(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)
    (wheels_dir / "ciu-7.16.0-py3-none-any.whl").write_bytes(b"tampered")
    (wheels_dir / "topos-0.4.1-py3-none-any.whl").unlink()

    problems = " ".join(_problems(wheels_dir, wheels_list))

    assert "missing" in problems and "topos" in problems
    assert "hashes to" in problems and "ciu-7.16.0" in problems


def test_a_declared_distribution_that_is_absent_is_named(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, [w for w in COMPLETE if not w.startswith("topos")])

    problems = _problems(wheels_dir, wheels_list)

    assert any("not supplied" in p and "topos" in p for p in problems)


def test_a_wheel_of_another_name_does_not_satisfy_a_declaration(tmp_path: Path) -> None:
    """Prefix matching would let `cmru-extras` stand in for `cmru`."""
    wheels = [w for w in COMPLETE if not w.startswith("cmru")] + ["cmru_extras-1.0-py3-none-any.whl"]
    wheels_dir, wheels_list = _release_dir(tmp_path, wheels)

    problems = " ".join(_problems(wheels_dir, wheels_list))

    assert "not supplied: cmru" in problems and "does not declare: cmru-extras" in problems


def test_a_list_that_declares_nothing_is_an_error(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)
    wheels_list.write_text("# nothing\n", encoding="utf-8")

    assert "declares no first-party wheels" in _problems(wheels_dir, wheels_list)[0]


def test_local_dev_wheels_come_from_the_context_and_complete_the_set(tmp_path: Path) -> None:
    release = [w for w in COMPLETE if not w.startswith("nyxloom")]
    wheels_dir, wheels_list = _release_dir(tmp_path, release)
    context = _local_context(tmp_path, ["nyxloom-0.10.1-py3-none-any.whl"])

    problems, wheels = check_wheel_set.check(wheels_dir, wheels_list, build_mode="local-dev", local_dir=context)

    assert problems == []
    assert {w["name"]: w["source"] for w in wheels}["nyxloom"] == "local-dev"


def test_a_release_build_refuses_local_dev_wheels_in_the_context(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)
    context = _local_context(tmp_path, ["nyxloom-0.10.1-py3-none-any.whl"])

    problems = _problems(wheels_dir, wheels_list, build_mode="release", local_dir=context)

    assert any("release build received local-dev wheel" in p for p in problems)


def test_a_local_dev_wheel_changed_after_staging_or_unrecorded_is_refused(tmp_path: Path) -> None:
    release = [w for w in COMPLETE if not w.startswith("nyxloom")]
    wheels_dir, wheels_list = _release_dir(tmp_path, release)
    context = _local_context(tmp_path, ["nyxloom-0.10.1-py3-none-any.whl"])
    (context / "wheels" / "nyxloom-0.10.1-py3-none-any.whl").write_bytes(b"stale")

    problems = _problems(wheels_dir, wheels_list, build_mode="local-dev", local_dir=context)
    assert any("hashes to" in p for p in problems)

    sub = tmp_path / "sub"
    sub.mkdir()
    context2 = _local_context(sub, ["nyxloom-0.10.1-py3-none-any.whl"], recorded=[])
    problems = _problems(wheels_dir, wheels_list, build_mode="local-dev", local_dir=context2)
    assert any("nobody reported" in p for p in problems)


def test_a_distribution_supplied_twice_is_refused(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)
    context = _local_context(tmp_path, ["ciu-7.17.0-py3-none-any.whl"])

    problems = _problems(wheels_dir, wheels_list, build_mode="local-dev", local_dir=context)

    assert any("supplied more than once" in p and "ciu" in p for p in problems)


def test_an_unknown_build_mode_is_refused(tmp_path: Path) -> None:
    wheels_dir, wheels_list = _release_dir(tmp_path, COMPLETE)

    assert "unknown build mode" in _problems(wheels_dir, wheels_list, build_mode="staging")[0]


@pytest.mark.parametrize("drop", [None, "topos"])
def test_the_command_writes_the_install_list_and_record_only_on_success(tmp_path: Path, drop: str | None) -> None:
    wheels = [w for w in COMPLETE if drop is None or not w.startswith(drop)]
    wheels_dir, wheels_list = _release_dir(tmp_path, wheels)
    install, record = tmp_path / "install.list", tmp_path / "record.json"

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_wheel_set.py"),
         "--wheels-dir", str(wheels_dir), "--wheels-list", str(wheels_list), "--build-mode", "release",
         "--install-list", str(install), "--record", str(record)],
        capture_output=True, text=True, check=False,
    )

    if drop is None:
        assert result.returncode == 0
        assert sorted(Path(line).name for line in install.read_text().splitlines()) == sorted(wheels)
        recorded = json.loads(record.read_text())
        assert recorded["build_mode"] == "release" and len(recorded["wheels"]) == 5
        assert all("path" not in w for w in recorded["wheels"])
    else:
        assert result.returncode == 1 and result.stderr.startswith("[ERROR]")
        assert not install.exists() and not record.exists()


def test_the_shipped_wheel_list_declares_the_five_names_the_lane_and_docs_rely_on() -> None:
    assert check_wheel_set.declared_names(ROOT / "pip" / "wheels.list") == [
        "cli-extended", "ciu", "cmru", "topos", "nyxloom",
    ]


def test_staged_local_dev_wheels_pass_the_check_in_the_image(tmp_path: Path) -> None:
    """The producer (local_dev.stage) and the consumer (the Dockerfile's check) agree."""
    wheel = tmp_path / "nyxloom-0.10.1-py3-none-any.whl"
    wheel.write_bytes(b"nyxloom")
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        'schema_version = 1\n[[artifact]]\nname = "nyxloom"\n'
        f'path = "{wheel}"\nsha256 = "{hashlib.sha256(b"nyxloom").hexdigest()}"\nversion = "0.10.1"\n',
        encoding="utf-8",
    )
    artifacts = local_dev.load_manifest(manifest, allowed_wheels=["cli-extended", "ciu", "cmru", "topos", "nyxloom"])
    context = tmp_path / "build" / "local-dev"
    local_dev.stage(artifacts, staging_dir=context)
    release = [w for w in COMPLETE if not w.startswith("nyxloom")]
    wheels_dir, wheels_list = _release_dir(tmp_path, release)

    assert _problems(wheels_dir, wheels_list, build_mode="local-dev", local_dir=context) == []


def test_the_dockerfile_runs_the_check_with_the_build_mode_and_has_no_empty_set_escape() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "no first-party wheels to install" not in dockerfile
    run = dockerfile[dockerfile.index('wheels_dir="/tmp/pip/wheels"'):]
    check = run[: run.index("pip install")]
    assert 'check_wheel_set.py' in check and '--build-mode "${MDT_BUILD_MODE}"' in check
    assert "--local-dir /tmp/mdt-local-dev" in check
    # only the files the check emitted are installed - never a bare glob of the directory
    assert '"${install_files[@]}"' in run and "*.whl" not in run.split("mdt-perms fix")[0]
    assert "COPY scripts/check_wheel_set.py /tmp/check_wheel_set.py" in dockerfile
    assert "!scripts/check_wheel_set.py" in (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "from=mdt-local-dev,source=/,target=/tmp/mdt-local-dev" in dockerfile[
        dockerfile.index("# FATAL in every build mode"):dockerfile.index('wheels_dir="/tmp/pip/wheels"')
    ]


def _resolver():
    import importlib.util

    spec = importlib.util.spec_from_file_location("mdt_resolver", ROOT / "scripts" / "resolve-devcontainers-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_resolver_purges_stale_wheels_by_distribution_name_not_by_spelling(tmp_path: Path) -> None:
    resolver = _resolver()
    for name in ("cli_extended-0.3.0-py3-none-any.whl", "cli_extended-0.3.0-py3-none-any.whl.sha256",
                 "cli_extended-0.4.0-py3-none-any.whl", "cmru-6.1.0-py3-none-any.whl", "cmru_extras-1-py3-none-any.whl"):
        (tmp_path / name).write_bytes(b"")

    resolver.purge_stale_wheels(tmp_path, "cli-extended", keep=tmp_path / "cli_extended-0.4.0-py3-none-any.whl")

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "cli_extended-0.4.0-py3-none-any.whl", "cmru-6.1.0-py3-none-any.whl", "cmru_extras-1-py3-none-any.whl",
    ]


def test_the_resolver_report_lists_release_wheels_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = _resolver()
    monkeypatch.setattr(resolver, "WHEELS_CONTEXT_DIR", tmp_path)

    report = resolver.write_wheels_report([
        {"name": "ciu", "whl_filename": "ciu-1-py3-none-any.whl", "sha256": "a" * 64, "source": "release"},
        {"name": "nyxloom", "whl_filename": "nyxloom-1-py3-none-any.whl", "sha256": "b" * 64, "source": "local-dev"},
    ])

    assert [w["filename"] for w in json.loads(report.read_text())["wheels"]] == ["ciu-1-py3-none-any.whl"]
    assert report.name == check_wheel_set.REPORT_NAME


def test_the_resolver_writes_the_report_after_every_resolution() -> None:
    source = (ROOT / "scripts" / "resolve-devcontainers-release.py").read_text(encoding="utf-8")
    assert "first_party_wheels = resolve_first_party_wheels(github_owner, github_repo)\n    write_wheels_report(first_party_wheels)" in source


def test_a_local_dev_build_purges_a_release_wheel_of_the_same_distribution_left_by_an_earlier_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N2: the local-dev branch used to `continue` before the same-distribution purge, so a
    release-run wheel in pip/wheels/ made the next local-dev build fail 'unreported'."""
    resolver = _resolver()
    monkeypatch.setattr(resolver, "WHEELS_CONTEXT_DIR", tmp_path)
    (tmp_path / "nyxloom-0.9.0-py3-none-any.whl").write_bytes(b"release")
    (tmp_path / "cli_extended-0.3.0-py3-none-any.whl").write_bytes(b"release")
    (tmp_path / "ciu-7.0.0-py3-none-any.whl").write_bytes(b"other distribution, not local")
    source = (ROOT / "scripts" / "resolve-devcontainers-release.py").read_text(encoding="utf-8")
    branch = source[source.index("if name in local_wheels:"):source.index('print(f"[INFO] Resolving first-party wheel: {name}"')]
    assert "purge_stale_wheels(WHEELS_CONTEXT_DIR, name" in branch, "the local-dev branch must purge too"

    class Artifact:
        path = Path("/x/nyxloom-0.10.1-py3-none-any.whl")

    resolver.purge_stale_wheels(tmp_path, "nyxloom", keep=tmp_path / Artifact.path.name)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["ciu-7.0.0-py3-none-any.whl", "cli_extended-0.3.0-py3-none-any.whl"]
