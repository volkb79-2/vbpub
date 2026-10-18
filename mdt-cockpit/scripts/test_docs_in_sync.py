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
        "NPM_CONFIG_PREFIX", "UV_PYTHON_INSTALL_DIR", "MDT_IMAGE_PATH",
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


# Names the retirement of which two documents (the design rationale and the home
# inventory) must be free to explain; every other document must not use them at all.
RETIRED_EVERYWHERE = (
    "migrate_persistent_home", ".mdt-home-migration", "localEnv:HOME}/mdt--mounted-folders",
    "~/.config/modern-debian-tools-python-debug/ai.env\n",
)
RETIRED_OUTSIDE_THE_EXPLAINERS = ("home-vscode", "~/mdt-manifest.md", "~/.oh-my-zsh-custom", "home-defaults")
EXPLAINERS = ("docs/DESIGN-GUIDE.md", "docs/HOME-PERSISTENCE.md")


@pytest.mark.parametrize("doc", LIVE_DOCS)
def test_no_live_doc_still_directs_the_reader_to_a_retired_name(doc: str) -> None:
    text = _read(doc)
    retired = RETIRED_EVERYWHERE + (() if doc in EXPLAINERS else RETIRED_OUTSIDE_THE_EXPLAINERS)
    for name in retired:
        assert name not in text, f"{doc} still mentions {name!r}"


def test_the_namespace_rename_left_no_live_code_on_the_old_name() -> None:
    offenders = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            continue  # run-gate.py is the shared estate runner, not this project's code
        if (
            not path.is_file() or path.suffix not in {".py", ".sh", ".hcl", ".toml", ".json", ".example"}
            and path.name not in {"Dockerfile", "mdt", "host-escape", "mdt-perms"}
        ):
            continue
        if any(part in {".hypothesis", "__pycache__", "benchmarks", "handoff", "logs", "build", ".pytest_cache", ".run-gate"}
               for part in path.parts) or relative.startswith("tests/runtime/__"):
            continue
        if relative == "scripts/test_docs_in_sync.py":
            continue
        # The doc section that explains moving FROM the original project names it by
        # title; that anchor is the one legitimate live mention.
        text = path.read_text(encoding="utf-8", errors="replace")
        text = text.replace("moving-from-modern-debian-tools-python-debug", "").replace(
            "Moving from modern-debian-tools-python-debug", ""
        )
        if "modern-debian-tools-python-debug" in text:
            offenders.append(relative)
    assert offenders == []


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
