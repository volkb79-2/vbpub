"""The user-facing documents are part of the change (README = what, DESIGN-GUIDE = why,
CONSUMERS = how). These checks can each go red:

1. every config example in the three documents parses with the SHIPPED loader;
2. every value of every closed public vocabulary a consumer must type appears in at
   least one of the three;
3. every cross-document anchor resolves;
4. no live document still shows the retired names as something to use.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "templates"))
import local_dev  # noqa: E402
import initialize_container_environment as bootstrap  # noqa: E402

TRIAD = ("README.md", "docs/DESIGN-GUIDE.md", "docs/CONSUMERS.md")
LIVE_DOCS = (
    *TRIAD,
    "USAGE.md",
    "DEVCONTAINER-LIFECYCLE.md",
    "docs/HOME-PERSISTENCE.md",
    "templates/README.md",
    "customization/README.md",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _fenced_blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, flags=re.DOTALL)


def _slug(heading: str) -> str:
    heading = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def _headings(path: Path) -> set[str]:
    found, in_code = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and re.match(r"#{1,6} ", line):
            found.add(_slug(re.sub(r"^#+\s*", "", line)))
    return found


def test_the_local_dev_manifest_example_in_the_consumer_guide_parses_with_the_shipped_loader() -> None:
    blocks = [block for block in _fenced_blocks(_read("docs/CONSUMERS.md"), "toml") if "[[artifact]]" in block]
    assert blocks, "docs/CONSUMERS.md must show a local-dev manifest"
    allowed = set(local_dev.wheel_names(ROOT / "pip" / "wheels.list")) | set(local_dev.PWMCP_NAMES)
    for block in blocks:
        document = tomllib.loads(block)
        assert document["schema_version"] == local_dev.SCHEMA_VERSION
        for entry in document["artifact"]:
            assert set(entry) == {"name", "path", "sha256", "version"}
            assert entry["name"] in allowed


def test_the_consumer_devcontainer_example_parses_with_the_shipped_jsonc_loader() -> None:
    section = _read("docs/CONSUMERS.md").split("## Consumer devcontainer", 1)[1]
    for block in _fenced_blocks(section, "jsonc")[:2]:
        assert isinstance(bootstrap._parse_jsonc_object(block), dict)


def test_the_shipped_template_declares_no_path_and_the_docs_say_so() -> None:
    document = bootstrap._parse_jsonc_object(_read("templates/devcontainer.json"))
    assert "PATH" not in json.dumps(document["containerEnv"]) and "PATH" not in document["remoteEnv"]
    assert "sets neither `PATH` nor `NPM_CONFIG_PREFIX`" in _read("templates/README.md")


def test_every_closed_vocabulary_value_a_consumer_types_is_documented() -> None:
    corpus = "\n".join(_read(path) for path in TRIAD)
    typed = [
        *local_dev.wheel_names(ROOT / "pip" / "wheels.list"),
        *local_dev.PWMCP_NAMES,
        "--local-dev", "--target", "local-dev-artifacts.toml",
        local_dev.BUILD_MODE_LABEL, local_dev.LOCAL_ARTIFACTS_LABEL,
        "MDT_GID", "MDT_BUILD_MODE",
        "MDT_RUNTIME_IMAGE_NATIVE", "MDT_RUNTIME_IMAGE_VSC", "MDT_RUNTIME_LOCAL_DEV_MANIFEST",
        "image-runtime", "mdt-perms",
        "mdt-cockpit-home", "mdt-cockpit-tmp", "mdt-cockpit-<purpose>",
        "~/.config/mdt-cockpit", "/usr/local/share/mdt-cockpit",
        "/opt/mdt/bin", "/opt/mdt/npm", "/opt/mdt/venv", "/srv/pwmcp-instance",
        "NPM_CONFIG_PREFIX", "UV_PYTHON_INSTALL_DIR", "/usr/local/share/mdt-cockpit/path", "/etc/zsh/zprofile",
        "@anthropic-ai/claude-code", "npm install -g reasonix@latest", "npm install -g opencode-linux-x64-baseline@latest",
        "rebuilding the image is the durable way", "first-party-wheels.json",
        "ZSH_DISABLE_COMPFIX", "ZSH_CACHE_DIR",
        "/etc/xdg/nvim", "/etc/htoprc", "/etc/mc/mc.ini", "/etc/nanorc", "/etc/zsh/zshrc", "/etc/bash.bashrc",
    ]
    missing = [value for value in typed if value not in corpus]
    assert missing == []


def test_every_cross_document_anchor_resolves() -> None:
    headings = {(ROOT / doc).resolve(): _headings(ROOT / doc) for doc in LIVE_DOCS}
    unresolved = []
    for doc in LIVE_DOCS:
        path = (ROOT / doc).resolve()
        for target, anchor in re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", path.read_text(encoding="utf-8")):
            destination = (path.parent / target).resolve() if target else path
            if destination in headings and anchor not in headings[destination]:
                unresolved.append((doc, target, anchor))
    assert unresolved == []


# Names that must not appear in ANY live document: they describe mechanisms that no longer
# exist. (Every entry is a plain single-line substring, so it can actually match.)
RETIRED_EVERYWHERE = (
    "migrate_persistent_home",
    ".mdt-home-migration",
    "mdt--mounted-folders/home",
    "~/.config/modern-debian-tools-python-debug/ai.env",
)
RETIRED_OUTSIDE_THE_EXPLAINERS = ("home-vscode", "~/mdt-manifest.md", "~/.oh-my-zsh-custom", "home-defaults")
EXPLAINERS = ("docs/DESIGN-GUIDE.md", "docs/HOME-PERSISTENCE.md")
# The one document that walks a user through the old layout may name those paths.
EXEMPT_FROM_EVERYWHERE = ("docs/HOME-PERSISTENCE.md",)


@pytest.mark.parametrize("doc", LIVE_DOCS)
def test_no_live_doc_still_directs_the_reader_to_a_retired_name(doc: str) -> None:
    text = _read(doc)
    retired = (() if doc in EXEMPT_FROM_EVERYWHERE else RETIRED_EVERYWHERE) + (
        () if doc in EXPLAINERS else RETIRED_OUTSIDE_THE_EXPLAINERS
    )
    for name in retired:
        assert name not in text, f"{doc} still mentions {name!r}"


def test_every_retired_name_is_a_plain_substring_that_can_match() -> None:
    for name in RETIRED_EVERYWHERE + RETIRED_OUTSIDE_THE_EXPLAINERS:
        assert name and name == name.strip() and "\n" not in name, repr(name)


# ── the rename guard: every text file of the project, both spellings of the old name ──

OLD_NAME = re.compile(r"modern[- ]debian[- ]tools", re.IGNORECASE)
# The link text/anchor of the one section that explains how to move from the old project.
ALLOWED_PHRASES = (
    "moving-from-modern-debian-tools-python-debug",
    "Moving from modern-debian-tools-python-debug",
    "(`modern-debian-tools-python-debug`) are not carried over",
)
# Files that legitimately name the old project, each with its reason. Anything else that
# mentions it is a straggler of the rename.
HISTORICAL = {
    "CHANGES.md": "generated release history",
    "plan-image-info-do-not-use-home.md": "dated planning record",
    "docs/HOME-PERSISTENCE.md": "the section that explains moving from the old project",
    "GHCR-ACCESS-GUIDE.md": "explains old-package tags",
    "package-manifests-versioned/README.md": "links the original project's output",
    "scripts/test_docs_in_sync.py": "this guard names the patterns",
    "scripts/test_devcontainer_template.py": "pins the anchor and the old-layout notice",
}
HISTORICAL_PREFIXES = {
    "handoff/": "dated handoff packages",
    "benchmarks/results/": "recorded measurements",
}
# host-setup design records and reports (markdown only - its scripts are live code).
HISTORICAL_MARKDOWN_PREFIXES = {"host-setup/": "design records and reports predate the rename"}
# Never part of the tracked tree (ignored, generated, or caches).
SKIPPED_DIRECTORIES = {
    ".git", "__pycache__", ".pytest_cache", ".hypothesis", ".run-gate", "logs", "build",
}


def _project_text_files(root: Path) -> list[Path]:
    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            path.is_symlink() or not path.is_file()
            or any(part in SKIPPED_DIRECTORIES for part in relative.parts)
        ):
            continue
        name = relative.as_posix()
        if name.startswith(("package-manifests-versioned/", "pip/wheels/")) and name != "package-manifests-versioned/README.md":
            continue  # generated per build / downloaded
        if name == "local-dev-artifacts.toml" or name.startswith(".build-env"):
            continue  # operator-local, git-ignored
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary
        found.append(path)
    return found


def _is_historical(relative: str) -> bool:
    return (
        relative in HISTORICAL
        or any(relative.startswith(prefix) for prefix in HISTORICAL_PREFIXES)
        or (relative.endswith(".md") and any(relative.startswith(p) for p in HISTORICAL_MARKDOWN_PREFIXES))
    )


def _stragglers(root: Path, files: list[Path]) -> list[str]:
    offenders = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if _is_historical(relative):
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in ALLOWED_PHRASES:
            text = text.replace(phrase, "")
        if OLD_NAME.search(text):
            offenders.append(relative)
    return offenders


def test_the_rename_left_no_text_file_on_either_spelling_of_the_old_name() -> None:
    files = _project_text_files(ROOT)
    suffixes = {path.suffix for path in files}
    # the scan really covers the suffixes the previous allowlist-based guard missed
    assert {".md", ".in", ".list", ".conf", ".vim", ".ini", ".py", ".sh", ".hcl"} <= suffixes
    assert _stragglers(ROOT, files) == []


def test_the_rename_guard_catches_both_spellings_in_any_suffix(tmp_path: Path) -> None:
    for name, content in (
        ("a.vim", '" the Modern Debian Tools + Python Debug config'),
        ("b.conf", "path=/usr/local/share/modern-debian-tools-python-debug"),
        ("c.in", "MODERN DEBIAN TOOLS"),
    ):
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "d.md").write_text(
        "see [Moving from modern-debian-tools-python-debug](x.md#moving-from-modern-debian-tools-python-debug)",
        encoding="utf-8",
    )

    assert _stragglers(tmp_path, _project_text_files(tmp_path)) == ["a.vim", "b.conf", "c.in"]


@pytest.mark.parametrize(
    "path",
    [
        "cmru.toml",
        "docker-bake.hcl",
        "scripts/resolve-devcontainers-release.py",
        "templates/AGENTS.md",
        "templates/README.md",
        "templates/devcontainer.json",
        "Dockerfile",
    ],
)
def test_shipped_text_and_image_labels_use_the_new_product_name(path: str) -> None:
    """OCI descriptions become labels on every build and templates are copied into consumer
    repositories, so the retired display name must not survive in either."""
    assert "Modern Debian Tools" not in _read(path)
