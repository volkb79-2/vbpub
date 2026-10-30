"""PWMCP-11: one pwmcp release builds, pushes and records BOTH images (offline plan checks).

Nothing here runs docker, cmru or a registry. It checks the committed release inputs against each
other (bake plan, both Dockerfiles, defaults, cmru.toml, the prepared-coordinate loader) and that
the push step visits both GHCR packages.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PWMCP = Path(__file__).resolve().parents[1]
BAKE = (PWMCP / "docker-bake.hcl").read_text(encoding="utf-8")
OCR_DOCKERFILE = (PWMCP / "containers" / "ocr" / "Dockerfile").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("pwmcp_build_push_ocr_wiring", PWMCP / "build-push.py")
assert _spec and _spec.loader
build_push = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = build_push
_spec.loader.exec_module(build_push)


def _bake_var(name: str) -> str:
    match = re.search(r'variable\s+"' + name + r'"\s*\{[^}]*default\s*=\s*"([^"]+)"', BAKE, re.DOTALL)
    assert match, name
    return match.group(1)


def _block(kind: str, name: str) -> str:
    match = re.search(kind + r'\s+"' + re.escape(name) + r'"\s*\{(.*?)\n\}', BAKE, re.DOTALL)
    assert match, (kind, name)
    return match.group(1)


def test_the_all_group_builds_both_images() -> None:
    targets = re.findall(r'"([^"]+)"', _block("group", "all"))
    assert targets == ["pwmcp-latest", "pwmcp-ocr"]  # build-push.py bakes "all" for --build and --push


def test_ocr_target_uses_the_ocr_dockerfile_with_the_pwmcp_build_context() -> None:
    block = _block("target", "pwmcp-ocr")
    main = _block("target", "pwmcp-latest")
    assert 'dockerfile = "containers/ocr/Dockerfile"' in block
    assert re.search(r'context\s*=\s*"\."', block) and re.search(r'context\s*=\s*"\."', main)  # same context rules
    keys = lambda text: set(re.findall(r"^  (\w[\w-]*)\s*=", text, re.MULTILINE))  # noqa: E731
    # exactly the same set of settings as the main target (context, dockerfile, args, tags): no extra
    # platforms/cache/output/labels/pull policy that would make the two images build differently
    assert keys(block) == keys(main) == {"context", "dockerfile", "args", "tags"}


def test_ocr_tags_use_the_same_release_coordinate_in_their_own_repository() -> None:
    tags = re.findall(r'"([^"]+)"', re.search(r"tags\s*=\s*\[(.*?)\]", _block("target", "pwmcp-ocr"), re.DOTALL).group(1))
    assert tags == ["${REGISTRY}/${NAMESPACE}/pwmcp-ocr:${PWMCP_VERSION}", "${REGISTRY}/${NAMESPACE}/pwmcp-ocr:latest"]
    main_tags = re.findall(r'"([^"]+)"', re.search(r"tags\s*=\s*\[(.*?)\]", _block("target", "pwmcp-latest"), re.DOTALL).group(1))
    assert "${REGISTRY}/${NAMESPACE}/pwmcp:${PWMCP_VERSION}" in main_tags  # same variable, sibling repository


def test_bake_build_args_match_the_dockerfile_args_and_the_digest_is_pinned() -> None:
    args = dict(re.findall(r'(\w+)\s*=\s*"\$\{(\w+)\}"', _block("target", "pwmcp-ocr")))
    assert args == {"DEBIAN_RELEASE": "DEBIAN_RELEASE", "DEBIAN_IMAGE_DIGEST": "DEBIAN_IMAGE_DIGEST"}
    for name in args:
        assert re.search(rf"^ARG {name}=" + re.escape(_bake_var(name)) + r"$", OCR_DOCKERFILE, re.MULTILINE), name
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", _bake_var("DEBIAN_IMAGE_DIGEST"))
    assert "FROM debian:${DEBIAN_RELEASE}-slim@${DEBIAN_IMAGE_DIGEST}" in OCR_DOCKERFILE
    # the stale "known violation" wording is gone now that the pin is real
    assert "KNOWN VIOLATION" not in OCR_DOCKERFILE and "TAG ONLY" not in OCR_DOCKERFILE


def test_defaults_ship_the_ocr_image_on_the_pwmcp_coordinate() -> None:
    defaults = tomllib.loads((PWMCP / "ciu.defaults.toml.j2").read_text(encoding="utf-8"))["pwmcp"]
    assert defaults["ocr"]["image"]["tag"] == defaults["unified"]["image"]["tag"] == _bake_var("PWMCP_VERSION")
    assert defaults["ocr"]["image"]["name"] == "pwmcp-ocr" and defaults["unified"]["image"]["name"] == "pwmcp"
    assert defaults["ocr"]["image"]["registry"] == defaults["unified"]["image"]["registry"] == "ghcr.io"
    assert defaults["ocr"]["image"]["namespace"] == defaults["unified"]["image"]["namespace"]
    assert json.loads((PWMCP / "pwmcp.contract.json").read_text())["release"] == defaults["ocr"]["image"]["tag"]


def test_the_ocr_dockerfile_is_a_generated_input_so_a_refreshed_pin_is_committed() -> None:
    project = tomllib.loads((PWMCP / "cmru.toml").read_text(encoding="utf-8"))["project"]["release"]
    assert "containers/ocr/Dockerfile" in project["commit_generated"]
    assert "containers/pwmcp/Dockerfile" in project["commit_generated"]


def test_prepared_coordinate_requires_the_ocr_base_pin() -> None:
    sys.path.insert(0, str(PWMCP / "scripts"))
    try:
        import _vars
    finally:
        sys.path.pop(0)
    assert {"DEBIAN_RELEASE", "DEBIAN_IMAGE_DIGEST"} <= set(_vars._REQUIRED_KEYS)
    complete = {key: "x" for key in _vars._REQUIRED_KEYS}
    assert _vars._is_complete(complete)
    complete.pop("DEBIAN_IMAGE_DIGEST")
    assert not _vars._is_complete(complete)  # a pre-OCR cmru.vars fails closed instead of building unpinned


def test_the_committed_check_projects_both_images_into_cmru_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs the real committed-input check, but writes the prepared coordinate to a temp file."""

    spec = importlib.util.spec_from_file_location("pwmcp_resolver_wiring", PWMCP / "scripts" / "resolve-playwright-version.py")
    assert spec and spec.loader
    resolver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = resolver
    spec.loader.exec_module(resolver)
    target = tmp_path / "cmru.vars"
    live = PWMCP / "cmru.vars"
    before = live.read_bytes() if live.exists() else None
    monkeypatch.setattr(resolver, "RELEASE_VARS_FILE", target)
    resolver.check_committed_inputs()
    written = target.read_text(encoding="utf-8")
    assert "GHCR_PACKAGE_NAMES=pwmcp,pwmcp-ocr" in written
    assert f"DEBIAN_IMAGE_DIGEST={_bake_var('DEBIAN_IMAGE_DIGEST')}" in written
    assert (live.read_bytes() if live.exists() else None) == before  # the live prepared file is untouched


def test_push_syncs_visibility_for_both_packages_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_push, "load_vars", lambda: {})
    monkeypatch.setattr(build_push, "load_builder_config", lambda: build_push.BuilderConfig("b", ""))
    monkeypatch.setattr(build_push, "ensure_builder", lambda config: None)
    monkeypatch.setenv("GITHUB_USERNAME", "owner")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
    monkeypatch.delenv("GHCR_PACKAGE_NAMES", raising=False)
    monkeypatch.setattr(build_push.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0))
    baked: list[list[str]] = []
    monkeypatch.setattr(build_push, "run", lambda argv, cwd=None: baked.append(argv))
    synced: list[list[str]] = []
    monkeypatch.setattr(build_push, "sync_ghcr_package_visibility", synced.append)

    build_push.do_push()
    assert baked[0][-2:] == ["all", "--push"]  # one bake of the "all" group pushes both images
    assert synced == [["pwmcp", "pwmcp-ocr"]]


def test_the_ocr_image_carries_the_main_images_label_set() -> None:
    main = (PWMCP / "containers" / "pwmcp" / "Dockerfile").read_text(encoding="utf-8")
    for label in ("org.opencontainers.image.source", "org.opencontainers.image.documentation"):
        wanted = re.search(label + r'="([^"]+)"', main).group(1)
        assert f'{label}="{wanted}"' in OCR_DOCKERFILE  # the same repository link: it links the GHCR package
    assert 'org.opencontainers.image.title="pwmcp-ocr"' in OCR_DOCKERFILE
    assert "org.opencontainers.image.description=" in OCR_DOCKERFILE


def test_the_docs_state_the_decisions_and_the_first_release_checklist() -> None:
    readme = (PWMCP / "README.md").read_text(encoding="utf-8")
    backlog = (PWMCP / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text(encoding="utf-8")
    for text in (readme, backlog):
        assert "write:packages" in text and "KI-97" in text and "--set-version" in text
        assert "imagetools inspect debian:trixie-slim" in text
        assert "NEXT" in text and "PWMCP_ALLOW_MISSING_TEST_DEPS" in text and "PWMCP_REQUIRE_TESSERACT" in text
    assert "DECIDED BY DESIGN, 2026-10-27" in backlog and "open operator decision" not in (readme + backlog).lower()
    assert "400 `invalid_scale`" in readme and "`drop_low_conf`" in readme
    cmru = (PWMCP.parent / "cmru" / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text(encoding="utf-8")
    assert "KI-97 - `mirror_package_visibility` hard-fails" in cmru
