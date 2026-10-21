"""KI-89: every installer project's PRODUCER names its bundle as the contract says.

Contract (``docs/CONSUMERS.md`` "get.py installer", SPEC S6.3): the release asset is
``<tag><asset_suffix>``. The rendered ``get.py`` requests exactly that. pwmcp's
``bundle.toml`` produced ``pwmcp-<version>.tar.xz`` instead, so each pwmcp release's own
installer answered HTTP 404 (PWMCP-07).

``producer_violations`` reads a project directory's own configuration (the bundle
archive ``name_template``, or the ``tarball-publish --glob``) and compares what it would
publish with :func:`cmru.release.bundle_asset_name`. It runs on every real project that
declares ``[project.installer]`` with an ``asset_suffix`` and on synthetic projects (a
deviating producer MUST be reported, a conforming one must not).

Scope, stated honestly: this compares CONFIGURATION, not built bytes (a project's own
publish script, e.g. pwmcp's ``find_bundle``, needs its own local test); only release
versions are checked (``X.Y.Z[-rN]``: a ``+`` local version is never published as an
immutable release, and ``name_template.format`` does not apply ``version_to_tag``'s ``+``
rewrite, so checking one would be a false alarm); and a ``tarball-publish --glob`` is a
selector over what the build produced, so it must match the contract name AND must not
also match the pre-fix spelling ``<prefix>-<version><suffix>`` (a glob such as ``*.tar.xz``
would publish a wrongly named file without complaint).

A project whose producer cannot be found is a violation unless it is listed in
``KNOWN_NO_BUNDLE_PRODUCER`` with its backlog id; the entry is itself checked, so it goes
red as soon as the project does get a producer (the exemption must then be removed).
"""
from __future__ import annotations

import fnmatch
import shlex
import tomllib
from pathlib import Path

import pytest

from cmru.release import bundle_asset_name

REPO = Path(__file__).resolve().parents[2]
SAMPLE_VERSION = "1.2.3-r4"

# The coverage canary builds its tree with `tools/project_fixture.py`: it copies `git ls-files`
# output for explicit pathspecs, so it carries cmru and a few root files but NOT the sibling
# installer projects, and it has no `.git`. The repo-reading checks skip ONLY on that fact about
# the tree (not a git checkout). In a real checkout a missing project is a FAILURE, never a skip:
# "the project went missing" must not be reported as "isolated tree". The synthetic
# controlled-wrong-implementation tests always run. (This module reads `REPO / name / ...`, a
# non-literal path, so the fixture-closure scan in test_project_fixture.py does not see it;
# see the note there.)
ORCHESTRATION = REPO / "cmru.orchestration.toml"
IS_CHECKOUT = (REPO / ".git").exists()  # a file in a worktree, a directory in a clone
needs_estate = pytest.mark.skipif(
    not IS_CHECKOUT, reason="not a git checkout (isolated canary tree)"
)
# The floor the scan test asserts; the expected set itself is derived from the orchestration.
INSTALLER_FLOOR = ("pwmcp", "tls-edge", "ciu")

# project dir -> backlog id of the known "no bundle is produced yet" gap.
KNOWN_NO_BUNDLE_PRODUCER = {"ciu": "CIU-99"}


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _tarball_globs(cfg: dict) -> list[str]:
    """``--glob`` values of every ``tarball-publish`` step command."""
    found: list[str] = []
    for step in (cfg.get("steps") or {}).values():
        for command in (step.get("commands") or []) if isinstance(step, dict) else []:
            argv = [str(a) for a in command.get("argv", [])] if isinstance(command, dict) else []
            if "tarball-publish" in argv and "--glob" in argv[:-1]:
                found.append(argv[argv.index("--glob") + 1])
    return found


def producer_violations(project_dir: Path) -> list[str]:
    """Contract violations of ``project_dir``'s bundle producer ([] when conforming)."""
    cfg = _toml(project_dir / "cmru.toml")
    project = cfg.get("project", {})
    installer = project.get("installer")
    if not isinstance(installer, dict) or not installer.get("asset_suffix"):
        return []
    suffix = installer["asset_suffix"]
    prefix = str(project["prefix"])
    assert prefix.endswith("-v"), f"{project_dir.name}: prefix {prefix!r} must end in '-v'"
    contract = bundle_asset_name(prefix[:-2], SAMPLE_VERSION, suffix)
    name = project_dir.name
    out: list[str] = []
    produced = 0

    bundle_toml = project_dir / "bundle.toml"
    if bundle_toml.is_file():
        template = _toml(bundle_toml).get("archive", {}).get("name_template")
        if template:
            produced += 1
            built = template.format(version=SAMPLE_VERSION)
            if built != contract:
                out.append(f"{name}: bundle.toml name_template builds {built!r}, "
                           f"but its get.py downloads {contract!r}")
    for glob in _tarball_globs(cfg):
        produced += 1
        legacy = f"{prefix[:-2]}-{SAMPLE_VERSION}{suffix}"
        if fnmatch.fnmatchcase(legacy, glob):
            out.append(f"{name}: tarball-publish --glob {shlex.quote(glob)} also matches the "
                       f"non-contract name {legacy!r}")
        if not fnmatch.fnmatchcase(contract, glob):
            out.append(f"{name}: tarball-publish --glob {shlex.quote(glob)} does not match "
                       f"{contract!r}, the name its get.py downloads")

    if produced == 0:
        if name in KNOWN_NO_BUNDLE_PRODUCER:
            return []
        out.append(f"{name}: declares [project.installer] asset_suffix but no bundle "
                   "producer was found (bundle.toml [archive] name_template or a "
                   "tarball-publish --glob); file it or list it with its backlog id")
    elif name in KNOWN_NO_BUNDLE_PRODUCER:
        out.append(f"{name}: now has a bundle producer; remove it from "
                   f"KNOWN_NO_BUNDLE_PRODUCER ({KNOWN_NO_BUNDLE_PRODUCER[name]})")
    return out


def _orchestrated_configs(repo: Path) -> dict[str, Path]:
    """project name -> its cmru.toml path, as the orchestration declares them."""
    orchestration = repo / "cmru.orchestration.toml"
    if not orchestration.is_file():
        return {}
    projects = _toml(orchestration).get("orchestration", {}).get("project", {})
    return {name: repo / entry["config"] for name, entry in projects.items()}


def estate_violations(repo: Path, *, is_checkout: bool, floor=INSTALLER_FLOOR) -> list[str]:
    """What a FULL checkout must carry: the orchestration, every config it names, and the floor
    installer projects. A non-checkout tree (the isolated canary copy) is exempt: [] there."""
    if not is_checkout:
        return []
    out: list[str] = []
    configs = _orchestrated_configs(repo)
    if not configs:
        out.append("cmru.orchestration.toml is missing or names no projects")
    out += [f"{name}: {path.relative_to(repo)} is missing from a full checkout"
            for name, path in sorted(configs.items()) if not path.is_file()]
    out += [f"{name}: installer project missing from a full checkout"
            for name in floor if not (repo / name / "cmru.toml").is_file()]
    return out


def _installer_projects(repo: Path = REPO) -> list[Path]:
    found = []
    for path in sorted(_orchestrated_configs(repo).values()):
        if path.is_file():
            installer = _toml(path).get("project", {}).get("installer")
            if isinstance(installer, dict) and installer.get("asset_suffix"):
                found.append(path.parent)
    return found


def test_a_full_checkout_carries_the_installer_projects():
    """UNSKIPPED: in a git checkout every orchestrated project and the floor must be present."""
    assert estate_violations(REPO, is_checkout=IS_CHECKOUT) == []


@needs_estate
def test_the_scan_finds_the_known_installer_projects():
    names = {p.name for p in _installer_projects()}
    assert set(INSTALLER_FLOOR) <= names


@needs_estate
@pytest.mark.parametrize("project", _installer_projects(), ids=lambda p: p.name)
def test_every_installer_project_publishes_the_contract_name(project):
    assert producer_violations(project) == []


# ─── controlled wrong implementations: the check itself must be able to go red ────────

def _synthetic(tmp_path: Path, *, template: str | None = None, glob: str | None = None,
               name: str = "demo") -> Path:
    root = tmp_path / name
    root.mkdir()
    steps = ""
    if glob:
        steps = ('[steps.publish]\ncommands = [{ argv = ["cmru", "handler", "tarball-publish", '
                 f'"--glob", "{glob}"] }}]\n')
    (root / "cmru.toml").write_text(
        '[project]\nprefix = "demo-v"\n[project.installer]\nasset_suffix = ".tar.xz"\n' + steps,
        encoding="utf-8")
    if template:
        (root / "bundle.toml").write_text(
            f'[archive]\nname_template = "{template}"\n', encoding="utf-8")
    return root


def test_a_producer_that_keeps_its_own_name_is_reported(tmp_path):
    root = _synthetic(tmp_path, template="demo-{version}.tar.xz")  # the pwmcp bug
    violations = producer_violations(root)
    assert len(violations) == 1 and "demo-1.2.3-r4.tar.xz" in violations[0]


def test_a_conforming_bundle_producer_passes(tmp_path):
    assert producer_violations(_synthetic(tmp_path, template="demo-v{version}.tar.xz")) == []


def test_a_deviating_tarball_glob_is_reported_and_a_conforming_one_passes(tmp_path):
    assert producer_violations(_synthetic(tmp_path, glob="demo-1*.tar.xz", name="bad"))
    broad = producer_violations(_synthetic(tmp_path, glob="*.tar.xz", name="broad"))
    assert len(broad) == 1 and "also matches" in broad[0]
    assert producer_violations(_synthetic(tmp_path, glob="demo-v*.tar.xz", name="good")) == []


def test_an_installer_project_without_any_producer_is_reported(tmp_path):
    violations = producer_violations(_synthetic(tmp_path))
    assert len(violations) == 1 and "no bundle producer" in violations[0]


def test_a_stale_known_gap_exemption_is_reported(tmp_path, monkeypatch):
    monkeypatch.setitem(KNOWN_NO_BUNDLE_PRODUCER, "demo", "CIU-99")
    root = _synthetic(tmp_path, template="demo-v{version}.tar.xz")
    assert any("remove it from KNOWN_NO_BUNDLE_PRODUCER" in v for v in producer_violations(root))
    root2 = _synthetic(tmp_path, name="demo2")
    monkeypatch.setitem(KNOWN_NO_BUNDLE_PRODUCER, "demo2", "CIU-99")
    assert producer_violations(root2) == []


def _tree(tmp_path: Path, *, git: bool, configs: dict[str, bool]) -> Path:
    """A tmp estate: orchestration naming every key of ``configs``; value = config present."""
    root = tmp_path / ("co" if git else "iso")
    root.mkdir()
    if git:
        (root / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    body = "".join(f'[orchestration.project.{n}]\nconfig = "{n}/cmru.toml"\n' for n in configs)
    (root / "cmru.orchestration.toml").write_text(body, encoding="utf-8")
    for name, present in configs.items():
        if present:
            (root / name).mkdir()
            (root / name / "cmru.toml").write_text("[project]\n", encoding="utf-8")
    return root


def test_a_full_checkout_missing_a_project_fails(tmp_path):
    root = _tree(tmp_path, git=True, configs={"pwmcp": True, "tls-edge": False, "ciu": True})
    violations = estate_violations(root, is_checkout=True)
    assert any("tls-edge" in v and "missing" in v for v in violations), violations


def test_a_full_checkout_missing_a_floor_project_not_in_the_orchestration_fails(tmp_path):
    root = _tree(tmp_path, git=True, configs={"pwmcp": True, "ciu": True})  # tls-edge dropped
    assert any(v.startswith("tls-edge:") for v in estate_violations(root, is_checkout=True))


def test_a_complete_full_checkout_passes_the_guard(tmp_path):
    root = _tree(tmp_path, git=True, configs={n: True for n in INSTALLER_FLOOR})
    assert estate_violations(root, is_checkout=True) == []


def test_a_tree_without_git_is_the_only_way_the_estate_checks_are_bypassed(tmp_path):
    root = _tree(tmp_path, git=False, configs={"pwmcp": False})
    assert not (root / ".git").exists()
    assert estate_violations(root, is_checkout=(root / ".git").exists()) == []
    # the same missing project in a checkout is a failure, so the exemption is not the absence
    assert estate_violations(root, is_checkout=True)
