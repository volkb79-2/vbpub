"""Tests for the explicit local-dev build mode (D9).

No Docker, no network, no clock: artifacts are tiny files in tmp_path, the build
entry points are driven through their pure functions or with the subprocess layer
replaced.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install_pwmcp_instance as pwmcp_installer  # noqa: E402
import local_dev  # noqa: E402
import manifest_sections  # noqa: E402

WHEEL_NAMES = ["cli-extended", "ciu", "cmru", "topos", "nyxloom"]
PWMCP_VERSION = "1.64.0-r2"


def _load_build_push():
    spec = importlib.util.spec_from_file_location("mdt_build_push", ROOT / "build-push.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_push = _load_build_push()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_artifact(directory: Path, filename: str, data: bytes) -> tuple[Path, str]:
    path = directory / filename
    path.write_bytes(data)
    return path, _sha(data)


def _manifest(path: Path, *entries: dict[str, str], schema: int = 1) -> Path:
    lines = [f"schema_version = {schema}"]
    for entry in entries:
        lines.append("\n[[artifact]]")
        lines.extend(f'{key} = "{value}"' for key, value in entry.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ciu_entry(tmp_path: Path, **overrides: str) -> dict[str, str]:
    wheel, sha = _write_artifact(tmp_path, "ciu-7.11.0-py3-none-any.whl", b"ciu wheel")
    entry = {"name": "ciu", "path": str(wheel), "sha256": sha, "version": "7.11.0"}
    entry.update(overrides)
    return entry


def _load(tmp_path: Path, *entries: dict[str, str], **kwargs):
    manifest = _manifest(tmp_path / "local-dev-artifacts.toml", *entries)
    return local_dev.load_manifest(
        manifest, allowed_wheels=WHEEL_NAMES, pwmcp_version=PWMCP_VERSION, **kwargs
    )


# ── manifest verification: refuse, naming the entry ──────────────────────────


def test_a_verified_manifest_yields_hashed_artifacts(tmp_path: Path) -> None:
    artifacts = _load(tmp_path, _ciu_entry(tmp_path))
    assert [(a.name, a.version) for a in artifacts] == [("ciu", "7.11.0")]
    assert artifacts[0].sha256 == _sha(b"ciu wheel")


def test_a_missing_file_refuses_and_names_the_entry(tmp_path: Path) -> None:
    entry = _ciu_entry(tmp_path)
    Path(entry["path"]).unlink()
    with pytest.raises(local_dev.LocalDevError, match=r"artifact 'ciu': .* is missing or not a regular file"):
        _load(tmp_path, entry)


def test_an_unreadable_file_refuses_and_names_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _ciu_entry(tmp_path)

    def unreadable(_path: Path) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(local_dev, "sha256_of", unreadable)
    with pytest.raises(local_dev.LocalDevError, match=r"artifact 'ciu': cannot read"):
        _load(tmp_path, entry)


def test_a_hash_mismatch_refuses_and_reports_both_hashes(tmp_path: Path) -> None:
    entry = _ciu_entry(tmp_path, sha256="0" * 64)
    with pytest.raises(local_dev.LocalDevError) as excinfo:
        _load(tmp_path, entry)
    message = str(excinfo.value)
    assert "artifact 'ciu'" in message and "mismatch" in message
    assert "0" * 64 in message and _sha(b"ciu wheel") in message


@pytest.mark.parametrize("bad_sha", ["", "xyz", "A" * 10, "f" * 63])
def test_a_malformed_sha256_refuses(tmp_path: Path, bad_sha: str) -> None:
    with pytest.raises(local_dev.LocalDevError, match="ciu"):
        _load(tmp_path, _ciu_entry(tmp_path, sha256=bad_sha))


def test_an_unknown_name_refuses_instead_of_silently_using_the_release(tmp_path: Path) -> None:
    with pytest.raises(local_dev.LocalDevError, match=r"'cmru2'.*not a known artifact"):
        _load(tmp_path, _ciu_entry(tmp_path, name="cmru2"))


def test_a_duplicate_entry_refuses(tmp_path: Path) -> None:
    entry = _ciu_entry(tmp_path)
    with pytest.raises(local_dev.LocalDevError, match="more than once"):
        _load(tmp_path, entry, dict(entry))


def test_a_relative_path_refuses(tmp_path: Path) -> None:
    with pytest.raises(local_dev.LocalDevError, match="absolute"):
        _load(tmp_path, _ciu_entry(tmp_path, path="ciu-7.11.0-py3-none-any.whl"))


def test_the_manifest_version_must_equal_the_wheel_file_version(tmp_path: Path) -> None:
    with pytest.raises(local_dev.LocalDevError, match=r"'7\.12\.0'.*'7\.11\.0'"):
        _load(tmp_path, _ciu_entry(tmp_path, version="7.12.0"))


def test_a_wheel_of_another_distribution_refuses(tmp_path: Path) -> None:
    wheel, sha = _write_artifact(tmp_path, "evil-1.0.0-py3-none-any.whl", b"x")
    entry = {"name": "ciu", "path": str(wheel), "sha256": sha, "version": "1.0.0"}
    with pytest.raises(local_dev.LocalDevError, match="wheel of 'evil'"):
        _load(tmp_path, entry)


def test_a_normalised_distribution_name_matches_its_wheels_list_name(tmp_path: Path) -> None:
    wheel, sha = _write_artifact(tmp_path, "cli_extended-0.4.0-py3-none-any.whl", b"x")
    entry = {"name": "cli-extended", "path": str(wheel), "sha256": sha, "version": "0.4.0"}
    assert [a.name for a in _load(tmp_path, entry)] == ["cli-extended"]


def test_the_pwmcp_version_must_equal_the_pinned_release(tmp_path: Path) -> None:
    bundle, sha = _write_artifact(tmp_path, "pwmcp.tar.xz", b"bundle")
    entry = {"name": "pwmcp-bundle", "path": str(bundle), "sha256": sha, "version": "1.63.0-r3"}
    with pytest.raises(local_dev.LocalDevError, match="pinned PWMCP release"):
        _load(tmp_path, entry)


def test_a_wrong_schema_or_empty_manifest_refuses(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "m.toml", schema=2)
    with pytest.raises(local_dev.LocalDevError, match="schema_version"):
        local_dev.load_manifest(manifest, allowed_wheels=WHEEL_NAMES)
    manifest = _manifest(tmp_path / "m.toml")
    with pytest.raises(local_dev.LocalDevError, match=r"lists no \[\[artifact\]\]"):
        local_dev.load_manifest(manifest, allowed_wheels=WHEEL_NAMES)


def test_an_unknown_field_refuses(tmp_path: Path) -> None:
    with pytest.raises(local_dev.LocalDevError, match="unknown field"):
        _load(tmp_path, _ciu_entry(tmp_path, comment="why"))


def test_the_shipped_example_parses_as_a_manifest_shape() -> None:
    import tomllib

    document = tomllib.loads((ROOT / "local-dev-artifacts.toml.example").read_text())
    assert document["schema_version"] == local_dev.SCHEMA_VERSION
    names = {entry["name"] for entry in document["artifact"]}
    assert names <= set(local_dev.wheel_names(ROOT / "pip" / "wheels.list")) | set(
        local_dev.PWMCP_NAMES
    )
    for entry in document["artifact"]:
        assert set(entry) == {"name", "path", "sha256", "version"}


# ── staging and provenance ───────────────────────────────────────────────────


def test_staging_replaces_a_stale_wheel_of_the_same_distribution(tmp_path: Path) -> None:
    wheels = tmp_path / "pip" / "wheels"
    wheels.mkdir(parents=True)
    (wheels / "ciu-7.10.0-py3-none-any.whl").write_bytes(b"old")
    (wheels / "ciu-7.10.0-py3-none-any.whl.sha256").write_text("x")
    (wheels / "cmru-6.0.0-py3-none-any.whl").write_bytes(b"other")
    artifacts = _load(tmp_path, _ciu_entry(tmp_path))
    staging = tmp_path / "build" / "local-dev"

    local_dev.stage(artifacts, wheels_dir=wheels, staging_dir=staging)

    assert sorted(path.name for path in wheels.iterdir()) == [
        "ciu-7.11.0-py3-none-any.whl",
        "cmru-6.0.0-py3-none-any.whl",
    ]
    assert (wheels / "ciu-7.11.0-py3-none-any.whl").read_bytes() == b"ciu wheel"


def test_staging_records_pwmcp_files_and_their_hashes(tmp_path: Path) -> None:
    bundle, bundle_sha = _write_artifact(tmp_path, "pwmcp-1.64.0-r2.tar.xz", b"bundle")
    getpy, getpy_sha = _write_artifact(tmp_path, "get.py", b"print('get')\n")
    entries = [
        {"name": "pwmcp-bundle", "path": str(bundle), "sha256": bundle_sha, "version": PWMCP_VERSION},
        {"name": "pwmcp-installer", "path": str(getpy), "sha256": getpy_sha, "version": PWMCP_VERSION},
    ]
    artifacts = _load(tmp_path, *entries)
    staging = tmp_path / "build" / "local-dev"

    local_dev.stage(artifacts, wheels_dir=tmp_path / "wheels", staging_dir=staging)

    record = json.loads((staging / "provenance.json").read_text())
    by_name = {item["name"]: item for item in record["artifacts"]}
    assert record["schema"] == 1
    assert (staging / by_name["pwmcp-bundle"]["staged"]).read_bytes() == b"bundle"
    assert (staging / by_name["pwmcp-installer"]["staged"]).read_bytes() == b"print('get')\n"
    assert by_name["pwmcp-bundle"]["sha256"] == bundle_sha
    # The in-image installer accepts exactly what was staged ...
    loaded = pwmcp_installer.load_local_artifacts(staging)
    assert set(loaded) == {"pwmcp-bundle", "pwmcp-installer"}
    # ... and refuses it the moment a staged file no longer matches its record.
    (staging / by_name["pwmcp-bundle"]["staged"]).write_bytes(b"tampered")
    with pytest.raises(pwmcp_installer.LocalDevError, match="pwmcp-bundle.*mismatch"):
        pwmcp_installer.load_local_artifacts(staging)


def test_provenance_records_name_version_hash_and_original_path(tmp_path: Path) -> None:
    artifacts = _load(tmp_path, _ciu_entry(tmp_path))
    records = json.loads(local_dev.provenance_json(artifacts))
    assert records == [
        {
            "name": "ciu",
            "version": "7.11.0",
            "sha256": _sha(b"ciu wheel"),
            "path": str(tmp_path / "ciu-7.11.0-py3-none-any.whl"),
        }
    ]


def test_the_image_manifest_and_labels_carry_the_provenance(tmp_path: Path) -> None:
    artifacts = _load(tmp_path, _ciu_entry(tmp_path))
    raw = local_dev.provenance_json(artifacts)
    parsed = manifest_sections.parse_local_artifacts(raw)
    section = "\n".join(manifest_sections.render_build_provenance_section("local-dev", parsed))
    assert "Build mode: `local-dev`" in section
    assert f"`{_sha(b'ciu wheel')}`" in section and "`7.11.0`" in section
    assert str(tmp_path / "ciu-7.11.0-py3-none-any.whl") in section
    # A release manifest is unchanged by the feature.
    assert manifest_sections.render_build_provenance_section("release", []) == []
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert f'{local_dev.BUILD_MODE_LABEL}="${{MDT_BUILD_MODE}}"' in dockerfile
    assert f'{local_dev.LOCAL_ARTIFACTS_LABEL}="${{MDT_LOCAL_ARTIFACTS_JSON}}"' in dockerfile
    assert '--build-mode "${MDT_BUILD_MODE}"' in dockerfile


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', '[{"name": "ciu"}]'])
def test_a_malformed_provenance_record_is_an_error_never_an_empty_list(raw: str) -> None:
    with pytest.raises(ValueError):
        manifest_sections.parse_local_artifacts(raw)
    assert manifest_sections.parse_local_artifacts("") == []


# ── tag shape ────────────────────────────────────────────────────────────────

DEV_TAGS = [
    "ghcr.io/o/mdt-cockpit:trixie-py3.14-php8.5-dev-20261018",
    "ghcr.io/o/mdt-cockpit:trixie-py3.14-php8.5-dev-latest",
]


def test_local_dev_tags_have_the_dev_shape_and_a_floating_dev_latest() -> None:
    assert local_dev.check_local_dev_tags(DEV_TAGS, "dev-20261018") == DEV_TAGS


def test_a_same_day_counter_stays_in_the_dated_tag() -> None:
    tags = [
        "localhost:5000/o/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-dev-20261018-3",
        "localhost:5000/o/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-dev-latest",
    ]
    assert local_dev.check_local_dev_tags(tags, "dev-20261018-3") == tags


@pytest.mark.parametrize(
    "release_tag",
    [
        "ghcr.io/o/mdt-cockpit:latest",
        "ghcr.io/o/mdt-cockpit-devcontainer:latest-stable",
        "ghcr.io/o/mdt-cockpit:trixie-py3.14-php8.5-latest",
        "ghcr.io/o/mdt-cockpit:trixie-py3.14-php8.5-20261018",
        "ghcr.io/o/mdt-cockpit:trixie-py3.14-php8.5-dev-20260101",
    ],
)
def test_a_release_or_foreign_tag_anywhere_in_the_list_refuses_the_build(release_tag: str) -> None:
    with pytest.raises(local_dev.LocalDevError, match="not a local-dev tag"):
        local_dev.check_local_dev_tags([*DEV_TAGS, release_tag], "dev-20261018")


@pytest.mark.parametrize("coordinate", ["20261018", "dev-2026", "dev-20261018-x", "release"])
def test_a_non_dev_date_coordinate_is_refused(coordinate: str) -> None:
    with pytest.raises(local_dev.LocalDevError, match="dev-YYYYMMDD"):
        local_dev.check_local_dev_tags(DEV_TAGS, coordinate)


def test_only_a_floating_alias_without_a_dated_tag_is_refused() -> None:
    with pytest.raises(local_dev.LocalDevError, match="carries the local-dev coordinate"):
        local_dev.check_local_dev_tags(DEV_TAGS[1:], "dev-20261018")


def _bake_tags(mode: str, build_date: str) -> dict[str, list[str]]:
    """Evaluate the real docker-bake.hcl (no builder or network needed for --print)."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "BUILD_DATE": build_date,
        "GITHUB_USERNAME": "o",
        "GITHUB_REPO": "r",
        "REGISTRY": "ghcr.io",
        "MDT_BUILD_MODE": mode,
        "DEVCONTAINERS_BASE_DYNAMIC_LATEST": "mcr.microsoft.com/devcontainers/python:3.14-trixie",
        "HOME": str(Path.home()),
    }
    result = subprocess.run(
        ["docker", "buildx", "bake", "-f", str(ROOT / "docker-bake.hcl"), "everything", "latest-vsc", "--print"],
        env=env, capture_output=True, text=True, check=True, cwd=ROOT, timeout=120,
    )
    return {name: spec.get("tags", []) for name, spec in json.loads(result.stdout)["target"].items()}


def _buildx_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "buildx", "version"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not _buildx_available(), reason="docker buildx is needed to evaluate the bake file")
def test_the_bake_file_gives_a_local_dev_build_only_dev_tags_and_leaves_release_tags_alone() -> None:
    local = _bake_tags("local-dev", "dev-20261018")
    assert local, "no targets evaluated"
    for target, tags in local.items():
        for tag in tags:
            name = tag.rsplit(":", 1)[1]
            assert "-dev-" in name, (target, tag)
        if tags:
            local_dev.check_local_dev_tags(tags, "dev-20261018")
    # Family-wide floating tags exist only in a release build.
    release = _bake_tags("release", "20261018")
    all_release_names = [tag.rsplit(":", 1)[1] for tags in release.values() for tag in tags]
    assert "latest" in all_release_names and "latest-stable" in all_release_names
    all_local_names = [tag.rsplit(":", 1)[1] for tags in local.values() for tag in tags]
    assert "latest" not in all_local_names and "latest-stable" not in all_local_names
    assert not any(name.endswith("-latest") and not name.endswith("-dev-latest") for name in all_local_names)


# ── publication is refused ───────────────────────────────────────────────────


def test_a_local_dev_label_is_unpublishable_and_unknown_modes_are_too() -> None:
    with pytest.raises(local_dev.LocalDevError, match="local-dev"):
        local_dev.refuse_unpublishable({local_dev.BUILD_MODE_LABEL: "local-dev"}, "img")
    with pytest.raises(local_dev.LocalDevError, match="surprise"):
        local_dev.refuse_unpublishable({local_dev.BUILD_MODE_LABEL: "surprise"}, "img")


def test_release_and_pre_label_images_remain_publishable() -> None:
    local_dev.refuse_unpublishable({local_dev.BUILD_MODE_LABEL: "release"}, "img")
    local_dev.refuse_unpublishable({}, "img")
    local_dev.refuse_unpublishable(None, "img")


class _FakeRegistryTools:
    """Replaces the subprocess layer of _push_oci_layouts; records every call."""

    def __init__(self, labels: dict[str, str] | None) -> None:
        self.labels = labels
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[:3] == ["regctl", "image", "config"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.labels), stderr="")
        if argv[:3] == ["regctl", "manifest", "digest"]:
            return subprocess.CompletedProcess(argv, 0, stdout="sha256:abc\n", stderr="")
        if argv[:2] == ["crane", "digest"]:
            return subprocess.CompletedProcess(argv, 0, stdout="sha256:abc\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def _prepare_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, labels) -> _FakeRegistryTools:
    layout = tmp_path / "layouts" / "trixie-py314-php85"
    layout.mkdir(parents=True)
    (layout / "index.json").write_text("{}", encoding="utf-8")
    fake = _FakeRegistryTools(labels)
    monkeypatch.setattr(build_push, "_oci_layout_dir", lambda: tmp_path / "layouts")
    monkeypatch.setattr(build_push, "_require_crane_regctl", lambda: None)
    monkeypatch.setattr(
        build_push,
        "_enumerate_bake_targets",
        lambda: (
            {"trixie-py314-php85": {"tags": ["ghcr.io/o/mdt-cockpit:trixie-py3.14-php8.5-20261018"]}},
            ["trixie-py314-php85"],
        ),
    )
    monkeypatch.setattr(build_push.subprocess, "run", fake)
    return fake


def test_push_refuses_an_image_labelled_local_dev_and_copies_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _prepare_push(monkeypatch, tmp_path, {local_dev.BUILD_MODE_LABEL: "local-dev"})
    with pytest.raises(SystemExit) as excinfo:
        build_push._push_oci_layouts({})
    assert "local-dev" in str(excinfo.value)
    assert not any(call[:3] == ["regctl", "image", "copy"] for call in fake.calls)
    assert not any(call[0] == "crane" for call in fake.calls)


def test_push_proceeds_for_a_release_image_positive_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _prepare_push(monkeypatch, tmp_path, {local_dev.BUILD_MODE_LABEL: "release"})
    build_push._push_oci_layouts({})
    assert any(call[:3] == ["regctl", "image", "copy"] for call in fake.calls)


def test_push_refuses_when_the_labels_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _prepare_push(monkeypatch, tmp_path, {})

    def failing(argv, **kwargs):
        if argv[:3] == ["regctl", "image", "config"]:
            raise subprocess.CalledProcessError(1, argv)
        return fake(argv, **kwargs)

    monkeypatch.setattr(build_push.subprocess, "run", failing)
    with pytest.raises(SystemExit, match="cannot read the labels"):
        build_push._push_oci_layouts({})
    assert not any(call[:3] == ["regctl", "image", "copy"] for call in fake.calls)


def test_push_refuses_a_saved_local_dev_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        build_push, "load_build_env", lambda: {"MDT_BUILD_MODE": "local-dev", "RELEASE_IMAGE_FLOW": "load"}
    )
    called: list[str] = []
    monkeypatch.setattr(build_push, "_push_oci_layouts", lambda _env: called.append("push"))
    with pytest.raises(SystemExit, match="only a release build"):
        build_push.do_push()
    assert called == []


def test_local_dev_is_only_a_build_and_targets_need_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (
        ["build-push.py", "--push", "--local-dev", "m.toml"],
        ["build-push.py", "--rebuild", "--local-dev", "m.toml"],
        ["build-push.py", "--build", "--target", "trixie-py314-php85"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as excinfo:
            build_push.parse_args()
        assert excinfo.value.code != 0, argv
    monkeypatch.setattr(
        sys, "argv", ["build-push.py", "--build", "--local-dev", "m.toml", "--target", "a", "--target", "b"]
    )
    args = build_push.parse_args()
    assert args.local_dev == Path("m.toml") and args.target == ["a", "b"]


def test_a_release_date_in_the_environment_is_refused_for_a_local_dev_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUILD_DATE", "20261018")
    with pytest.raises(SystemExit, match="not a local-dev coordinate"):
        build_push._local_dev_build_date()
    monkeypatch.setenv("BUILD_DATE", "dev-20261018-2")
    assert build_push._local_dev_build_date() == "dev-20261018-2"


def test_local_dev_tag_plan_refuses_unknown_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push, "_bake_print_targets", lambda names: {})
    with pytest.raises(SystemExit, match="unknown bake target"):
        build_push.local_dev_tag_plan(["nope"], "dev-20261018")


def test_local_dev_tag_plan_accepts_dev_tags_and_refuses_a_release_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_push, "_bake_print_targets", lambda names: {"t": {"tags": DEV_TAGS}})
    assert build_push.local_dev_tag_plan(["t"], "dev-20261018") == {"t": DEV_TAGS}
    monkeypatch.setattr(
        build_push,
        "_bake_print_targets",
        lambda names: {"t": {"tags": [*DEV_TAGS, "ghcr.io/o/mdt-cockpit:latest"]}},
    )
    with pytest.raises(SystemExit, match="not a local-dev tag"):
        build_push.local_dev_tag_plan(["t"], "dev-20261018")


# ── the bake wrapper: no publishing flow is reachable with local-dev state ───


def _run_bake_wrapper(action: str, **env: str) -> subprocess.CompletedProcess:
    base = {"PATH": "/usr/bin:/bin"}
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "release-bake.sh"), action],
        cwd=ROOT, env={**base, **env}, capture_output=True, text=True, check=False, timeout=60,
    )


@pytest.mark.parametrize("action", ["push"])
def test_the_bake_wrapper_has_no_publishing_step_for_a_local_dev_build(action: str) -> None:
    for flow in ("load", "push", "repack"):
        result = _run_bake_wrapper(action, MDT_BUILD_MODE="local-dev", RELEASE_IMAGE_FLOW=flow)
        assert result.returncode == 2, flow
        assert "never published" in result.stderr


def test_the_local_dev_flow_requires_the_local_dev_mode() -> None:
    result = _run_bake_wrapper("build", RELEASE_IMAGE_FLOW="local-dev")
    assert result.returncode == 2
    assert "requires MDT_BUILD_MODE=local-dev" in result.stderr
