"""KI-126: the per-interpreter bundle builder (``[vendor]``/``[stage]``/``[exec]``/``[manifest]``).

Docker-free: every vendor and stage command goes through an injected recording runner.
"""
from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from cmru import bundle, handlers
from cmru.config import VariantConfig

PY311 = VariantConfig(name="py311", build_arg="3.11", label=None)
PY313 = VariantConfig(name="py313", build_arg="3.13", label=None)

BASE = (
    'project_root = "."\n'
    'dist_dir = "dist"\n'
    'bundle_dir = "bundle"\n'
    'client_dir = "client"\n'
    '[wheel]\nenabled = false\n'
    '[archive]\nname_template = "{template}"\nversion_env = "VERSION"\nformat = "xztar"\n'
    '[copy]\nfiles = ["app.py"]\ndirs = []\n'
)


def _write(tmp_path: Path, extra: str = "", template: str = "demo-v{version}-{variant}.tar.xz") -> Path:
    (tmp_path / "app.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "requirements-runtime.txt").write_text("left-pad==1.0\n", encoding="utf-8")
    config = tmp_path / "bundle.toml"
    config.write_text(BASE.replace("{template}", template) + extra, encoding="utf-8")
    return config


VENDOR = (
    '[vendor]\nrequirements = ["requirements-runtime.txt"]\n'
    'platforms = ["manylinux2014_x86_64", "manylinux_2_28_x86_64"]\n'
)
STAGE = '[stage]\nargv = ["python", "seed.py", "{bundle_dir}", "{variant}", "{python_version}"]\n'
EXEC = (
    '[exec]\nprefix = ["docker", "exec", "-w", "{root}", "{container}"]\n'
    '[exec.env]\nroot = "KI126_ROOT"\ncontainer = "KI126_CONTAINER"\n'
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")


class Recorder:
    def __init__(self, fail_on: str | None = None, write: dict | None = None):
        self.calls: list[tuple[list[str], Path]] = []
        self.fail_on = fail_on
        self.write = write or {}

    def __call__(self, argv, cwd):
        self.calls.append((list(argv), Path(cwd)))
        if self.fail_on and self.fail_on in argv:
            raise subprocess.CalledProcessError(7, list(argv))
        for rel, content in self.write.items():
            target = Path(cwd) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def _members(archive: Path) -> list[str]:
    with tarfile.open(archive) as tf:
        return tf.getnames()


# --- schema -----------------------------------------------------------------
@pytest.mark.parametrize("extra, message", [
    ('[vendor]\nrequirements = ["r.txt"]\nplatforms = ["p"]\nbogus = 1\n', "unknown vendor key"),
    ('[vendor]\nrequirements = []\nplatforms = ["p"]\n', "at least one"),
    ('[vendor]\nrequirements = ["r"]\nplatforms = []\n', "at least one"),
    ('[vendor]\nrequirements = ["r"]\nplatforms = ["p"]\ndest = "/abs"\n', "relative path"),
    ('[vendor]\nrequirements = ["r"]\nplatforms = ["p"]\ndest = "../up"\n', "relative path"),
    ('[vendor]\nrequirements = ["r"]\nplatforms = ["p"]\ndest = ".git/vendor"\n', "excluded from the bundle archive"),
    ('[vendor]\nrequirements = ["r"]\nplatforms = ["p"]\ndest = "vendor.toml"\n', "excluded from the bundle archive"),
    ('[stage]\nargv = []\n', "must name a command"),
    ('[stage]\nargv = ["x", "{nope}"]\n', "unknown token"),
    ('[stage]\nargv = ["x", "{variant!r}"]\n', "unknown token"),
    ('[stage]\nargv = ["x", "{variant"]\n', "malformed"),
    ('[stage]\nargv = ["x"]\nextra = 1\n', "unknown stage key"),
    ('[exec]\nprefix = []\n', "must name a command"),
    ('[exec]\nprefix = ["d", "{zzz}"]\n', "unknown token"),
    ('[exec]\nprefix = ["d", "{container}"]\n', "does not name its environment"),
    ('[exec]\nprefix = ["d"]\n[exec.env]\nroot = "has space"\n', "environment variable names"),
    ('[exec]\nprefix = ["d"]\n[exec.env]\nbogus = "X"\n', "unknown exec.env key"),
    ('[exec]\nprefix = ["d"]\nenv = "X"\n', "environment variable names"),
    ('[manifest]\nname = "a/b"\n', "single filename"),
    ('[manifest]\nname = ".."\n', "single filename"),
    ('[manifest]\nname = "secrets.env"\n', "excluded from the bundle archive"),
    ('[manifest]\nname = "settings.toml"\n', "excluded from the bundle archive"),
    ('[manifest]\nother = 1\n', "unknown manifest key"),
])
def test_schema_rejects(tmp_path, extra, message):
    config = _write(tmp_path, extra)
    with pytest.raises(ValueError, match=message):
        bundle.parse_config(config)


def test_schema_tables_must_be_tables(tmp_path):
    config = _write(tmp_path)
    config.write_text('vendor = 1\n' + config.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[vendor\] must be a table"):
        bundle.parse_config(config)


def test_schema_accepts_all_tables(tmp_path):
    parsed = bundle.parse_config(_write(tmp_path, VENDOR + STAGE + EXEC + '[manifest]\n'))
    assert parsed.vendor.dest == "vendor"
    assert parsed.vendor.platforms == ["manylinux2014_x86_64", "manylinux_2_28_x86_64"]
    assert parsed.stage.argv[0] == "python"
    assert parsed.exec_prefix.env == {"root": "KI126_ROOT", "container": "KI126_CONTAINER"}
    assert parsed.manifest_name == "manifest.json"


@pytest.mark.parametrize("template", [
    "demo-{version}-{variant}-{variant}.tar.xz",
    "demo-{version}-{version}.tar.xz",
    "demo-{variant}.tar.xz",
    "demo-{version}-{variant!r}.tar.xz",
    "demo-{version}-{variant:>3}.tar.xz",
    "sub/demo-{version}-{variant}.tar.xz",
])
def test_template_rejects(tmp_path, template):
    with pytest.raises(ValueError, match="archive.name_template"):
        bundle.parse_config(_write(tmp_path, template=template))


def test_template_accepts_variant_in_either_order(tmp_path):
    bundle.parse_config(_write(tmp_path, template="{variant}-demo-{version}.tar.xz"))


def test_template_variant_field_must_match_build_mode(tmp_path):
    config = _write(tmp_path)
    with pytest.raises(ValueError, match="no variant is being built"):
        bundle.run_bundle(config)
    plain = _write(tmp_path, template="demo-v{version}.tar.xz")
    with pytest.raises(ValueError, match="needs a .variant. field"):
        bundle.run_bundle(plain, variant=PY311)


# --- argv construction ------------------------------------------------------
def test_vendor_and_stage_argv_without_prefix(tmp_path):
    config = _write(tmp_path, VENDOR + STAGE)
    recorder = Recorder()
    archive = bundle.run_bundle(config, variant=PY311, runner=recorder)
    assert archive == tmp_path / "dist" / "demo-v1.2.3-py311.tar.xz"
    vendor, stage = recorder.calls
    dest = tmp_path / "dist" / "bundle" / "vendor"
    assert vendor == (
        ["python3", "-m", "pip", "download", "--only-binary=:all:", "--python-version", "3.11",
         "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_28_x86_64",
         "--dest", str(dest), "-r", str(tmp_path / "requirements-runtime.txt")],
        tmp_path,
    )
    bundle_dir = tmp_path / "dist" / "bundle"
    assert stage == (["python", "seed.py", str(bundle_dir), "py311", "3.11"], bundle_dir)


def test_vendor_with_find_links_is_no_index(tmp_path):
    config = _write(tmp_path, VENDOR)
    text = config.read_text(encoding="utf-8").replace(
        "[wheel]\nenabled = false\n", '[wheel]\nenabled = false\nfind_links = "wheelhouse"\npython_bin = "py"\n')
    config.write_text(text, encoding="utf-8")
    recorder = Recorder()
    bundle.run_bundle(config, variant=PY311, runner=recorder)
    argv = recorder.calls[0][0]
    assert argv[0] == "py"
    assert argv[argv.index("--no-index"):argv.index("--no-index") + 3] == [
        "--no-index", "--find-links", str((tmp_path / "wheelhouse").resolve())]


def test_exec_prefix_applies_to_vendor_and_stage_only_with_relative_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("KI126_ROOT", "/work/proj")
    monkeypatch.setenv("KI126_CONTAINER", "naf-dev")
    config = _write(tmp_path, VENDOR + STAGE + EXEC)
    recorder = Recorder()
    bundle.run_bundle(config, variant=PY313, runner=recorder)
    vendor, stage = recorder.calls
    prefix = ["docker", "exec", "-w", "/work/proj", "naf-dev"]
    assert vendor[0][:5] == prefix and vendor[0][5:8] == ["python3", "-m", "pip"]
    assert vendor[0][vendor[0].index("--dest") + 1] == "dist/bundle/vendor"
    assert vendor[0][vendor[0].index("-r") + 1] == "requirements-runtime.txt"
    assert stage[0] == [*prefix, "python", "seed.py", "dist/bundle", "py313", "3.13"]
    assert vendor[1] == stage[1] == tmp_path


def test_exec_prefix_does_not_require_unused_env_tokens(tmp_path):
    exec_config = EXEC.replace(
        'prefix = ["docker", "exec", "-w", "{root}", "{container}"]',
        'prefix = ["docker", "exec", "{container}"]',
    )
    config = _write(tmp_path, VENDOR + exec_config)
    recorder = Recorder()
    bundle.run_bundle(
        config, variant=PY311, runner=recorder,
        environ={"KI126_CONTAINER": "naf-dev"},
    )
    assert recorder.calls[0][0][:3] == ["docker", "exec", "naf-dev"]


def test_exec_prefix_needs_env_and_path_inside_root(tmp_path, monkeypatch):
    config = _write(tmp_path, VENDOR + EXEC)
    with pytest.raises(RuntimeError, match="KI126_ROOT"):
        bundle.run_bundle(config, variant=PY311, runner=Recorder())
    monkeypatch.setenv("KI126_ROOT", "/r")
    monkeypatch.setenv("KI126_CONTAINER", "c")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    text = config.read_text(encoding="utf-8").replace('dist_dir = "dist"', f'dist_dir = "{outside}"')
    config.write_text(text, encoding="utf-8")
    stale_archive = outside / "demo-v1.2.3-py311.tar.xz"
    stale_archive.write_bytes(b"previous archive")
    with pytest.raises(ValueError, match="inside project_root"):
        bundle.run_bundle(config, variant=PY311, runner=Recorder())
    assert stale_archive.read_bytes() == b"previous archive"


def test_runner_preflight_preserves_previous_archive_when_env_is_missing(tmp_path):
    config = _write(tmp_path, VENDOR + EXEC)
    archive = tmp_path / "dist" / "demo-v1.2.3-py311.tar.xz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"previous archive")
    bundle_tree = tmp_path / "dist" / "bundle"
    bundle_tree.mkdir()
    (bundle_tree / "previous.txt").write_text("previous tree", encoding="utf-8")

    with pytest.raises(RuntimeError, match="KI126_"):
        bundle.run_bundle(config, variant=PY311, runner=Recorder(), environ={})

    assert archive.read_bytes() == b"previous archive"
    assert (bundle_tree / "previous.txt").read_text(encoding="utf-8") == "previous tree"


def test_runner_path_of_the_project_root_itself_is_dot(tmp_path):
    parsed = bundle.parse_config(_write(tmp_path))
    assert bundle._runner_path(parsed, parsed.project_root, True) == "."
    assert bundle._runner_path(parsed, parsed.bundle_dir, False) == str(parsed.bundle_dir)


def test_interpreter_version_required(tmp_path):
    config = _write(tmp_path, VENDOR)
    with pytest.raises(ValueError, match="build_arg"):
        bundle.run_bundle(config, variant=VariantConfig("py311", None, None), runner=Recorder())
    with pytest.raises(ValueError, match="needs a variant"):
        bundle.run_bundle(_write(tmp_path, VENDOR, template="demo-v{version}.tar.xz"), runner=Recorder())


def test_stage_tokens_need_their_inputs(tmp_path):
    only_variant = '[stage]\nargv = ["x", "{variant}"]\n'
    config = _write(tmp_path, only_variant, template="demo-v{version}.tar.xz")
    with pytest.raises(ValueError, match="no variant is being built"):
        bundle.run_bundle(config, runner=Recorder())
    version_only = '[stage]\nargv = ["x", "{python_version}"]\n'
    config = _write(tmp_path, version_only)
    with pytest.raises(ValueError, match="build_arg"):
        bundle.run_bundle(config, variant=VariantConfig("py311", None, None), runner=Recorder())


def test_stage_preflight_preserves_previous_archive_when_variant_is_missing(tmp_path):
    config = _write(
        tmp_path, '[stage]\nargv = ["x", "{variant}"]\n',
        template="demo-v{version}.tar.xz",
    )
    archive = tmp_path / "dist" / "demo-v1.2.3.tar.xz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"previous archive")

    with pytest.raises(ValueError, match="no variant is being built"):
        bundle.run_bundle(config, runner=Recorder())

    assert archive.read_bytes() == b"previous archive"


def test_stage_without_variant_tokens_runs_in_legacy_mode(tmp_path):
    config = _write(tmp_path, '[stage]\nargv = ["touch", "{bundle_dir}/ok"]\n', template="demo-v{version}.tar.xz")
    recorder = Recorder()
    archive = bundle.run_bundle(config, runner=recorder)
    assert recorder.calls[0][0] == ["touch", str(tmp_path / "dist" / "bundle") + "/ok"]
    assert archive.name == "demo-v1.2.3.tar.xz"


# --- order, failure, manifest ----------------------------------------------
def test_order_and_manifest_covers_vendored_and_staged_files(tmp_path):
    config = _write(tmp_path, VENDOR + STAGE + '[manifest]\nname = "manifest.json"\n')
    bundle_dir = tmp_path / "dist" / "bundle"

    def runner(argv, cwd):
        if "pip" in argv:
            (bundle_dir / "vendor" / "left_pad-1.0-py3-none-any.whl").write_bytes(b"whl")
        else:
            (bundle_dir / "state.db").write_bytes(b"db")

    archive = bundle.run_bundle(config, variant=PY311, runner=runner,
                                manifest_project="demo", manifest_tag="demo-v1.2.3")
    with tarfile.open(archive) as tf:
        manifest = json.loads(tf.extractfile("bundle/manifest.json").read())
        names = tf.getnames()
    assert sorted(manifest["files"]) == ["app.py", "state.db", "vendor/left_pad-1.0-py3-none-any.whl"]
    assert manifest["project"] == "demo" and manifest["tag"] == "demo-v1.2.3"
    assert {"bundle/app.py", "bundle/state.db", "bundle/vendor/left_pad-1.0-py3-none-any.whl"} <= set(names)


def test_manifest_needs_project_and_tag_and_valid_tag(tmp_path):
    config = _write(tmp_path, '[manifest]\n')
    with pytest.raises(ValueError, match="manifest_project and manifest_tag"):
        bundle.run_bundle(config, variant=PY311)
    with pytest.raises(ValueError, match="invalid --tag"):
        bundle.run_bundle(config, variant=PY311, manifest_project="demo", manifest_tag="bad tag")


def test_invalid_manifest_tag_is_preflighted_before_replacing_archive(tmp_path):
    config = _write(tmp_path, '[manifest]\n')
    archive = tmp_path / "dist" / "demo-v1.2.3-py311.tar.xz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"previous archive")

    with pytest.raises(ValueError, match="invalid --tag"):
        bundle.run_bundle(
            config, variant=PY311, manifest_project="demo", manifest_tag="bad tag",
        )

    assert archive.read_bytes() == b"previous archive"


def test_manifest_refuses_files_the_archive_would_drop(tmp_path):
    config = _write(tmp_path, STAGE + '[manifest]\n')
    recorder = Recorder(write={"secrets.env": "A=1\n"})
    with pytest.raises(ValueError, match="hard-excludes"):
        bundle.run_bundle(config, variant=PY311, runner=recorder,
                          manifest_project="demo", manifest_tag="demo-v1.2.3")
    assert not list((tmp_path / "dist").glob("*.tar.xz"))


@pytest.mark.parametrize("fail_on, calls", [("pip", 1), ("seed.py", 2)])
def test_step_failure_propagates_and_leaves_no_archive(tmp_path, fail_on, calls):
    config = _write(tmp_path, VENDOR + STAGE)
    recorder = Recorder(fail_on=fail_on)
    with pytest.raises(subprocess.CalledProcessError):
        bundle.run_bundle(config, variant=PY311, runner=recorder)
    assert len(recorder.calls) == calls
    assert not list((tmp_path / "dist").glob("*.tar.xz"))


def test_default_runner_runs_a_real_command(tmp_path):
    config = _write(tmp_path, '[stage]\nargv = ["python3", "-c", "open(\'staged.txt\', \'w\').write(\'1\')"]\n')
    archive = bundle.run_bundle(config, variant=PY311)
    assert "bundle/staged.txt" in _members(archive)


def test_default_runner_failure_is_called_process_error(tmp_path):
    config = _write(tmp_path, '[stage]\nargv = ["python3", "-c", "raise SystemExit(5)"]\n')
    with pytest.raises(subprocess.CalledProcessError):
        bundle.run_bundle(config, variant=PY311)


# --- determinism, sibling survival, legacy ---------------------------------
def test_archive_is_byte_identical_across_builds(tmp_path):
    config = _write(tmp_path, VENDOR + '[manifest]\n')
    first = bundle.run_bundle(config, variant=PY311, runner=_vendoring(tmp_path),
                              manifest_project="demo", manifest_tag="demo-v1.2.3").read_bytes()
    second = bundle.run_bundle(config, variant=PY311, runner=_vendoring(tmp_path),
                               manifest_project="demo", manifest_tag="demo-v1.2.3").read_bytes()
    assert first == second


def _vendoring(tmp_path):
    def runner(argv, cwd):
        (tmp_path / "dist" / "bundle" / "vendor" / "w.whl").write_bytes(b"w")
    return runner


def test_variant_builds_keep_sibling_archives(tmp_path):
    config = _write(tmp_path)
    a = bundle.run_bundle(config, variant=PY311)
    b = bundle.run_bundle(config, variant=PY313)
    assert a.is_file() and b.is_file()
    assert {a.name, b.name} == {"demo-v1.2.3-py311.tar.xz", "demo-v1.2.3-py313.tar.xz"}


def test_variant_build_cleans_stale_bundle_tree(tmp_path):
    config = _write(tmp_path)
    (tmp_path / "dist" / "bundle").mkdir(parents=True)
    (tmp_path / "dist" / "bundle" / "stale.txt").write_text("old", encoding="utf-8")
    (tmp_path / "dist" / "client").mkdir()
    (tmp_path / "dist" / "client" / "old.whl").write_text("old", encoding="utf-8")
    archive = bundle.run_bundle(config, variant=PY311)
    assert "bundle/stale.txt" not in _members(archive)
    assert "bundle/client/old.whl" not in _members(archive)


def test_legacy_config_is_unchanged_and_ignores_new_machinery(tmp_path):
    config = _write(tmp_path, template="demo-v{version}.tar.xz")
    recorder = Recorder()
    first = bundle.run_bundle(config, runner=recorder).read_bytes()
    second = bundle.run_bundle(config).read_bytes()
    assert first == second and recorder.calls == []
    assert _members(tmp_path / "dist" / "demo-v1.2.3.tar.xz") == ["bundle/app.py"]


def test_variant_asset_name_matches_publish_expectation(tmp_path):
    from cmru.release import variant_asset_name

    config = _write(tmp_path)
    archive = bundle.run_bundle(config, variant=PY311)
    assert archive.name == variant_asset_name("demo", "1.2.3", "py311", ".tar.xz")


# --- the bundle-build handler ----------------------------------------------
CMRU_TOML = """\
schema_version = 2

[github]
owner = "o"
repo = "r"
owner_type = "user"
token_source = "env"

[targets]
registry = "ghcr.io"
image_namespace = "o"

[project.demo]
prefix = "demo"
artifacts = ["bundle"]
{variants}
"""


def _project(tmp_path: Path, monkeypatch, variants: str, bundle_extra: str = "", template: str = "demo-v{version}-{variant}.tar.xz"):
    from cmru import config as cfg

    bundle_toml = _write(tmp_path, bundle_extra, template=template)
    cmru_toml = tmp_path / "cmru.toml"
    cmru_toml.write_text("placeholder", encoding="utf-8")
    forge = type("Forge", (), {"projects": {"demo": type("P", (), {
        "prefix": "demo",
        "variants": [PY311, PY313] if variants == "two" else [],
    })()}})()
    monkeypatch.setattr(cfg, "load_forge_config", lambda path: forge)
    return bundle_toml, cmru_toml


def _args(bundle_toml, cmru_toml, variant="all", project="demo"):
    import argparse

    return argparse.Namespace(
        bundle_config=str(bundle_toml), cmru_config=str(cmru_toml), name=project, variant=variant,
    )


def test_handler_builds_all_variants(tmp_path, monkeypatch, capsys):
    bundle_toml, cmru_toml = _project(tmp_path, monkeypatch, "two", '[manifest]\n')
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "stale-v0.0.1-py311.tar.xz").write_text("old", encoding="utf-8")
    handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml))
    out = capsys.readouterr().out
    assert sorted(p.name for p in (tmp_path / "dist").glob("*.tar.xz")) == [
        "demo-v1.2.3-py311.tar.xz", "demo-v1.2.3-py313.tar.xz", "stale-v0.0.1-py311.tar.xz"]
    assert out.count("[INFO] Built") == 2
    with tarfile.open(tmp_path / "dist" / "demo-v1.2.3-py313.tar.xz") as tf:
        manifest = json.loads(tf.extractfile("bundle/manifest.json").read())
    assert manifest["tag"] == "demo-v1.2.3" and manifest["project"] == "demo"


def test_handler_builds_one_named_variant(tmp_path, monkeypatch):
    bundle_toml, cmru_toml = _project(tmp_path, monkeypatch, "two")
    handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml, variant="py313"))
    assert [p.name for p in (tmp_path / "dist").glob("*.tar.xz")] == ["demo-v1.2.3-py313.tar.xz"]


def test_handler_named_variant_keeps_sibling_archives(tmp_path, monkeypatch):
    bundle_toml, cmru_toml = _project(tmp_path, monkeypatch, "two")
    handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml))
    sibling = tmp_path / "dist" / "demo-v1.2.3-py311.tar.xz"
    before = sibling.read_bytes()
    own = tmp_path / "dist" / "demo-v1.2.3-py313.tar.xz"
    own.write_bytes(b"corrupt")
    handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml, variant="py313"))
    assert sibling.read_bytes() == before
    assert own.read_bytes() != b"corrupt" and tarfile.is_tarfile(own)


def test_archive_name_is_validated_before_vendor_and_stage(tmp_path, monkeypatch):
    config = _write(tmp_path, VENDOR + STAGE)
    monkeypatch.setenv("VERSION", "1/2")
    recorder = Recorder()
    with pytest.raises(ValueError, match="single filename"):
        bundle.run_bundle(config, variant=PY311, runner=recorder)
    monkeypatch.delenv("VERSION")
    with pytest.raises(RuntimeError, match="VERSION must be set"):
        bundle.run_bundle(config, variant=PY311, runner=recorder)
    assert recorder.calls == [] and not (tmp_path / "dist").exists()


def test_find_links_in_runner_is_mapped_or_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("KI126_ROOT", "/work/proj")
    monkeypatch.setenv("KI126_CONTAINER", "c")
    config = _write(tmp_path, VENDOR + EXEC)
    inside = config.read_text(encoding="utf-8").replace(
        "[wheel]\nenabled = false\n", '[wheel]\nenabled = false\nfind_links = "wheelhouse"\n')
    config.write_text(inside, encoding="utf-8")
    recorder = Recorder()
    bundle.run_bundle(config, variant=PY311, runner=recorder)
    argv = recorder.calls[0][0]
    assert argv[argv.index("--find-links") + 1] == "wheelhouse"
    outside = tmp_path.parent / (tmp_path.name + "-wh")
    config.write_text(inside.replace('find_links = "wheelhouse"', f'find_links = "{outside}"'), encoding="utf-8")
    with pytest.raises(ValueError, match="inside project_root"):
        bundle.run_bundle(config, variant=PY311, runner=Recorder())


def test_handler_without_variants_builds_the_legacy_archive(tmp_path, monkeypatch):
    bundle_toml, cmru_toml = _project(tmp_path, monkeypatch, "none", template="demo-v{version}.tar.xz")
    handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml))
    assert (tmp_path / "dist" / "demo-v1.2.3.tar.xz").is_file()


@pytest.mark.parametrize("variants, variant, project, message", [
    ("two", "py399", "demo", "not declared"),
    ("none", "py311", "demo", "declares no variants"),
    ("two", "all", "ghost", "no project"),
])
def test_handler_refusals(tmp_path, monkeypatch, capsys, variants, variant, project, message):
    bundle_toml, cmru_toml = _project(tmp_path, monkeypatch, variants)
    with pytest.raises(SystemExit) as raised:
        handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml, variant=variant, project=project))
    assert raised.value.code == 2
    assert message in capsys.readouterr().err


def test_handler_error_mapping(tmp_path, monkeypatch, capsys):
    bundle_toml, cmru_toml = _project(tmp_path, monkeypatch, "two", '[stage]\nargv = ["python3", "-c", "raise SystemExit(5)"]\n')
    with pytest.raises(SystemExit) as raised:
        handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml))
    assert raised.value.code == 1
    assert "step failed (exit 5)" in capsys.readouterr().err
    monkeypatch.delenv("VERSION")
    with pytest.raises(SystemExit) as raised:
        handlers.cmd_bundle_build(_args(bundle_toml, cmru_toml))
    assert raised.value.code == 3
    monkeypatch.setenv("VERSION", "1.2.3")
    bad = tmp_path / "bad.toml"
    bad.write_text("project_root = '.'\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        handlers.cmd_bundle_build(_args(bad, cmru_toml))
    assert raised.value.code == 2
    missing = _write(tmp_path, VENDOR)
    missing.write_text(missing.read_text(encoding="utf-8").replace('"app.py"', '"nope.py"'), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        handlers.cmd_bundle_build(_args(missing, cmru_toml))
    assert raised.value.code == 2


def test_handler_is_registered_with_default_all():
    cli = handlers.handlers_cli()
    assert cli.run(argv=["bundle-build", "--help"]) == 0


def test_consumers_bundle_build_example_parses(tmp_path):
    import re

    text = (Path(__file__).parents[1] / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    (block,) = re.findall(r"```bundle-toml\n(.*?)```", text, flags=re.DOTALL)
    config = tmp_path / "bundle.toml"
    config.write_text('project_root = "."\n[copy]\nfiles = []\ndirs = []\n' + block, encoding="utf-8")
    parsed = bundle.parse_config(config)
    assert parsed.vendor and parsed.stage and parsed.exec_prefix and parsed.manifest_name
    assert parsed.archive_template.format(version="1.2.3", variant="py311") == "myproj-v1.2.3-py311.tar.xz"


@pytest.mark.parametrize("build_arg, expected", [("3.11", "3.11"), ("PYTHON_VERSION=3.13", "3.13"), ("311", "311")])
def test_interpreter_version_forms(tmp_path, build_arg, expected):
    recorder = Recorder()
    bundle.run_bundle(_write(tmp_path, VENDOR), variant=VariantConfig("py", build_arg, None), runner=recorder)
    argv = recorder.calls[0][0]
    assert argv[argv.index("--python-version") + 1] == expected


@pytest.mark.parametrize("build_arg", ["OTHER=3.11", "PYTHON_VERSION=", "py311", "3.11; rm", "PYTHON_VERSION=x"])
def test_interpreter_version_rejects_other_forms(tmp_path, build_arg):
    with pytest.raises(ValueError, match="not an interpreter version"):
        bundle.run_bundle(_write(tmp_path, VENDOR), variant=VariantConfig("py", build_arg, None), runner=Recorder())
