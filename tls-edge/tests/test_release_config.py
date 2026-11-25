"""The release config publishes exactly the variants and retained inputs declared.

Oracles that only repeated a literal from `cmru.toml` (and identical duplicates of
`cmru`'s own suite) are gone; what remains guards behaviour or an agreement between
two declarations that must not drift apart (design review M3/M6, cmru KI-194/KI-197).
"""
from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import PurePosixPath

from cmru.bundle import parse_config as parse_bundle_config
from cmru.config import load_forge_config
from conftest import ROOT

CONFIG = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
ORCHESTRATION = ROOT.parent / "cmru.orchestration.toml"
_VERSION = re.compile(r"\d+\.\d")


def _load_tls_edge(monkeypatch) -> object:
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "test.slice")
    forge = load_forge_config(ORCHESTRATION, require_orchestration=True)
    return forge.projects["tls-edge"]


def _publish_argv() -> list[str]:
    commands = CONFIG["steps"]["push"]["commands"]
    assert len(commands) == 1
    return commands[0]["argv"]


def _markdown_section(path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = re.search(rf"^{re.escape(heading)}\s*$", text, flags=re.MULTILINE)
    assert start is not None, f"{path}: missing heading {heading!r}"
    remainder = text[start.end():]
    end = re.search(r"^#{2,3} .+\s*$", remainder, flags=re.MULTILINE)
    section = remainder[:end.start()] if end is not None else remainder
    return " ".join(section.split())


def test_operator_docs_name_the_bundle_root_and_reproducibility_inputs():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "single staging directory as its top-level directory, `bundle/`" in readme
    assert "`tar -xJf … --strip-components=1` still extracts cleanly" in readme

    changed = _markdown_section(ROOT / "CHANGES.md", "### Changed")
    assert "archives are byte-identical for identical inputs" in changed
    assert "`ciu` and `cli-extended` track their latest release" in changed
    assert "`TLS_EDGE_CIU_VERSION` / `TLS_EDGE_CLI_EXTENDED_VERSION` pin them" in changed
    assert "`scripts/verify.sh` check 1 now runs the bundled `<root>/ciu check`" in changed
    assert "SKIPs when the ciu stub is absent" in changed

    install = " ".join((ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8").split())
    assert (
        "Build release bundles only from a clean tree (`cmru release` does); a dirty checkout's "
        "untracked files under `ciu-stack/` and `consumer-examples/` would be bundled."
    ) in install


def test_closed_te_05_uses_the_archive_table_shape():
    archive = (ROOT / "archive" / "2026-Q4.md").read_text(encoding="utf-8")
    assert "TE-05 | hand-rolled bundle build | <this merge> | 2026-11-24" in archive


def test_install_doc_carries_no_release_history():
    """INSTALL describes one install path, not the version history (TE-06).

    Version prose lives in the pending release notes before a tag; INSTALL keeps only
    the operational facts an instance of the current major needs.
    """
    install = " ".join((ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8").split())

    for stale in (
        "older than 4.0",
        "no in-place path",
        "A 3.1 flat answers file",
        "older than 3.2",
        "A 3.2 rollback keeps",
    ):
        assert stale not in install, stale


def test_release_notes_are_version_agnostic(monkeypatch):
    """The published headline is install/verify only; version prose lives in CHANGES.

    A dotted version in the notes string is what TE-06 removed: the headline outlives
    the release it was written for, so it must not name one (design review M2). The key
    is DERIVED from the publish step, so a renamed env var cannot slip past the pin.
    """
    argv = _publish_argv()
    notes = _load_tls_edge(monkeypatch).env[argv[argv.index("--notes-env") + 1]]

    assert _VERSION.search(notes) is None, notes
    assert "Breaking" not in notes
    assert "get.py install --variant py313" in notes
    assert "sha256sum -c" in notes
    assert "docs/INSTALL.md" in notes


def test_the_publish_step_is_cmru_s_tarball_handler_and_variants_agree(monkeypatch):
    """The project keeps no release script; cmru owns the publish step.

    The `--variant` repeat is kept until cmru KI-197 makes `tarball-publish` read
    `[[project.variants]]` itself; see the plan's KI list.
    """
    argv = _publish_argv()
    assert argv[:3] == ["cmru", "handler", "tarball-publish"]
    assert not (ROOT / "tools" / "release.sh").exists()

    specs = [argv[i + 1] for i, item in enumerate(argv) if item == "--variant"]
    declared = [f"{v['name']}={v['label']}" for v in CONFIG["project"]["variants"]]
    assert specs == declared
    assert argv[argv.index("--prefix") + 1] == CONFIG["project"]["prefix"].removesuffix("-v")


def test_publisher_and_build_agree_on_the_retained_version_inventory(monkeypatch):
    """KI-194's inventory oracle, moved from cmru's suite into the owning project."""
    argv = _publish_argv()
    version_file = PurePosixPath(argv[argv.index("--version-file") + 1])
    extra_asset = PurePosixPath(argv[argv.index("--extra-asset") + 1])
    assert version_file.parent == extra_asset.parent

    build = _load_tls_edge(monkeypatch).steps["build"]
    retain = build["commands"][-1]["argv"]
    assert retain[2:4] == ["retain", str(version_file.parent)]
    assert build["commands"][0]["argv"][:3] == ["cmru", "handler", "bundle-build"]


def test_bundle_copy_allowlist_is_operator_only():
    bundle = parse_bundle_config(ROOT / "bundle.toml")

    assert bundle.copy_files == [
        "VERSION",
        "get.py",
        "README.md",
        "ARCHITECTURE.md",
        "CONSUMER_GUIDE.md",
        "KNOWN_ISSUES_TODO_BACKLOG.md",
        "ciu.global.defaults.toml.j2",
        "packaging/instance-root/.gitignore",
    ]
    assert bundle.copy_dirs == [
        "ciu-stack", "consumer-examples", "docs", "installer", "scripts",
    ]
    assert sorted(path.name for path in (ROOT / "scripts").iterdir() if path.is_file()) == [
        "certbot-deploy-hook.sh",
        "dev-certs.sh",
        "gen-guard-secret.sh",
        "verify-external.sh",
        "verify.sh",
    ]
    # Maintainer tooling never ships, and the release has one entry point (cmru).
    assert sorted(path.name for path in (ROOT / "tools").iterdir() if path.is_file()) == [
        "bundle_contract.py",
        "bundle_wheels.py",
        "render.sh",
        "render_standalone.py",
        "update-rendered.sh",
    ]


def test_publish_glob_matches_every_variant_but_not_the_retired_single_asset_name():
    glob = _publish_argv()[_publish_argv().index("--glob") + 1]
    variants = [variant["name"] for variant in CONFIG["project"]["variants"]]

    assert all(fnmatch.fnmatchcase(f"tls-edge-v4.0.0-{name}.tar.xz", glob) for name in variants)
    assert not fnmatch.fnmatchcase("tls-edge-v4.0.0.tar.xz", glob)


def test_release_runs_a_registered_release_gate_lane():
    argv = CONFIG["steps"]["run-tests"]["commands"][0]["argv"]
    assert argv[0] == "run-gate"
    lane = argv[1]

    gate = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    assert gate["lanes"][lane]["kind"] == "assay"
    assert gate["lanes"][lane]["assay_lane"] == lane
