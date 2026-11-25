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

# Version prose in the published headline: a dotted release, a minor-line placeholder
# (3.x), a product version (v4, v4.0) or a schema-number phrase all tie the string to
# one release, which outlives it (TE-06, review N4).
_VERSION = re.compile(
    r"\d+\.\d+"               # 3.2, 4.0.1
    r"|[0-9]+\.x"             # 3.x
    r"|\bv[0-9]+(?:\.\d+)?\b"  # v4, v4.0
    r"|\bschema\s+[0-9]+\b"    # schema 2
)
# The one version-shaped fact the headline is entitled to: the Traefik major it
# targets. It is a stack dependency, not a tls-edge release.
_DEPENDENCY_VERSION = "Traefik v3"

# CHANGES' released section headings: `## [3.2.0] - 2026-11-24`.
_RELEASED_HEADING = re.compile(r"^## \[(\d+(?:\.\d+){1,2})\]", flags=re.MULTILINE)
# A version token: a dotted version or the minor-line placeholder shape a history
# sentence also uses (4.x).
_VERSION_TOKEN = re.compile(r"\d+\.\d+(?:\.\d+)?|[0-9]+\.x")
# The non-release contexts INSTALL is entitled to name, each anchored on its own
# wording so a bare "4.1" cannot hide in them: an HTTP protocol version, a ciu step
# id (S6.5), and another tool's version floor (`ciu >= 7.28.0`). The bundle
# interpreters are stripped from the token list derived from [[project.variants]].
_NON_RELEASE_CONTEXTS = (
    re.compile(r"\bHTTP/\d+\.\d+"),                              # HTTP/1.1
    re.compile(r"\bS\d+\.\d+"),                                   # S6.5
    re.compile(r"`?\bciu`?\s*(?:>=|<=|>|<)?\s*\d+\.\d+(?:\.\d+)?"),  # ciu >= 7.28.0
)
_INTERPRETER_VERSIONS = [
    arg.split("=", 1)[1].removeprefix("PYTHON_VERSION=")
    for variant in CONFIG["project"]["variants"]
    for arg in [variant["build_arg"]]
    if arg.startswith("PYTHON_VERSION=")
]


def _load_tls_edge(monkeypatch) -> object:
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "test.slice")
    forge = load_forge_config(ORCHESTRATION, require_orchestration=True)
    return forge.projects["tls-edge"]


def _publish_argv() -> list[str]:
    commands = CONFIG["steps"]["push"]["commands"]
    assert len(commands) == 1
    return commands[0]["argv"]


def _install_prose() -> str:
    return " ".join((ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8").split())


def _release_tokens(prose: str) -> list[str]:
    """Version tokens INSTALL states outside the non-release contexts it may name."""
    scrubbed = prose
    for version in _INTERPRETER_VERSIONS:
        scrubbed = scrubbed.replace(version, "")
    for context in _NON_RELEASE_CONTEXTS:
        scrubbed = context.sub("", scrubbed)
    return _VERSION_TOKEN.findall(scrubbed)


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
    assert "TE-05 | hand-rolled bundle build | ac90d7953 | 2026-11-24" in archive


def test_install_doc_carries_no_release_history():
    """INSTALL describes one install path, not the version history (TE-06).

    Version prose lives in the pending release notes before a tag; INSTALL keeps only
    the operational facts an instance of the current major needs. The forbidden set is
    DERIVED -- the released section headings of CHANGES plus every dotted token INSTALL
    is not entitled to name -- so a re-added release sentence cannot stay green (review
    S6).
    """
    install = _install_prose()
    changes = (ROOT / "CHANGES.md").read_text(encoding="utf-8")

    released = _RELEASED_HEADING.findall(changes)
    assert "3.2.0" in released, released
    for version in released:
        assert re.search(rf"\b{re.escape(version)}\b", install) is None, version

    # Derived, not a literal list: once the contexts INSTALL may name are stripped, no
    # release token may remain -- so "4.1 removes the alternate HTTPS port" or a re-added
    # "released in 3.2.0" fails here without a new literal being added (review S6).
    assert _release_tokens(install) == []
    assert _release_tokens(install + " 4.1 removes the alternate HTTPS port.") == ["4.1"]
    assert _release_tokens(install + " released in 3.2.0") == ["3.2.0"]
    assert _release_tokens(install + " 4.x dropped the preset.") == ["4.x"]
    # And the allowed contexts are stripped by their own wording, not by luck of the
    # token list: an HTTP version and a ciu floor survive in INSTALL and still pass.
    assert _release_tokens("HTTP/1.1 and ciu >= 7.28.0 are supported.") == []
    assert _release_tokens("ciu >= 4.1 is supported.") == []

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
    version_prose = notes.replace(_DEPENDENCY_VERSION, "")

    assert _VERSION.search(version_prose) is None, notes
    assert "Breaking" not in notes
    assert "get.py install --variant py313" in notes
    assert "sha256sum -c" in notes
    assert "docs/INSTALL.md" in notes

    # The pin is not inert: each shape the review named trips it, the variant labels the
    # headline must keep (py313/py314) do not, and the single allowed phrase is cleared
    # only because it was stripped, not because the pattern misses it (N4).
    for sample in ("3.2", "3.x", "v4", "v4.0", "schema 2"):
        assert _VERSION.search(sample), sample
    assert _VERSION.search(_DEPENDENCY_VERSION) is not None
    assert _VERSION.search("--variant py313") is None
    assert _VERSION.search("sha256sum -c tls-edge-v*-py313.tar.xz.sha256") is None


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
    # The installer must be published beside the bundles under its own name: a
    # dist/README or dist/VERSION asset would 404 INSTALL's `get.py install --version`
    # (review S5).
    assert extra_asset.name == "get.py"
    assert version_file.parent == extra_asset.parent

    project = _load_tls_edge(monkeypatch)
    build = project.steps["build"]
    # A build that stops cleaning or declaring the version file's dir republishes stale
    # artifacts; both are derived from the version file the publish step names.
    assert project.artifact_dirs == [str(version_file.parent)]
    assert build["clean_dirs"] == [str(version_file.parent)]
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

    # The lane's declared rigour, not just its name: a wave that drops R3 or sets
    # fail_under = 95 would otherwise release 4.0.0 under a weaker gate than it declares
    # (review S4). `judge.mode`/`targets` are pinned in tests/test_coverage_policy.py.
    assay = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))
    declared = assay["lanes"][lane]
    assert declared["rigor"] == ["R0", "R1", "R3"]
    assert declared["judge"]["fail_under"] == 100.0
    assert declared["judge"]["require_branch"] is True
    assert declared["judge"]["canary"]["mechanism"] == "import-break"
    assert declared["judge"]["canary"]["target"] == "src/tls_edge_cli/config.py"
