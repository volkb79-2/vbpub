#!/usr/bin/env python3
"""Release exactly the image the contract tested (tester-base R1, V1, V2), stdlib only.

One build, three actors, in cmru's own step order (prepare -> gate -> tag -> build_step -> push):

* ``record``  (prepare) the image just built as ``tester-base:local`` is the one the gate will test:
              its id is written to build/tested-image.json. `prepare` runs BEFORE cmru writes the new
              VERSION, so nothing versioned exists yet and nothing here reads VERSION.
* ``tag``     (build_step) cmru has by now written the final VERSION and tagged: read it (FAIL when it is
              missing or not x.y.z - there is no default), require ``tester-base:local`` to still be the
              recorded id, and give that very image the registry names ``:<VERSION>`` and ``:latest``
              with ``docker tag``. Nothing is rebuilt.
* ``push``    log in (credentials from the step environment only, never ambient: an isolated
              DOCKER_CONFIG, the PAT on stdin, never on argv), re-verify the ids, refuse when the version
              tag already exists in the registry (a release never overwrites one), push ``:latest`` and
              then ``:<VERSION>`` LAST, and require that the registry serves the digest that was pushed.

Partial publish: ``:latest`` is a moving alias and the immutable ``:<VERSION>`` is the commit point. If a
push fails after ``:latest`` moved, ``:latest`` points at the new (tested) image while ``:<VERSION>`` does
not exist yet; nothing else is changed, and re-running ``push`` is safe because the image and its digests
are the same. A failure before ``:latest`` leaves the registry untouched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "build" / "tested-image.json"
VERSION_FILE = ROOT / "VERSION"
LOCAL_TAG = "tester-base:local"
REGISTRY = "ghcr.io"
REPOSITORY = "ghcr.io/volkb79-2/tester-base"
SEMVER = re.compile(r"\d+\.\d+\.\d+")

Runner = Callable[..., str]


def run(argv: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        # the PAT only ever travels on stdin, so an argv in the message cannot leak it
        raise SystemExit(f"release_image: {' '.join(argv)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def read_version(path: Path = VERSION_FILE) -> str:
    """The release version cmru wrote. No default: absent or malformed is a failure."""
    if not path.is_file():
        raise SystemExit(f"release_image: {path} is missing; cmru writes it before the build step")
    version = path.read_text(encoding="utf-8").strip()
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"release_image: {path} holds {version!r}, not an x.y.z version")
    return version


def release_tags(version: str) -> list[str]:
    """latest first, the immutable version tag LAST (it is the commit point of a release)."""
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"release_image: refusing to tag version {version!r}")
    return [f"{REPOSITORY}:latest", f"{REPOSITORY}:{version}"]


def image_id(tag: str, runner: Runner = run) -> str:
    value = runner(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise SystemExit(f"release_image: {tag} has no image id ({value!r})")
    return value


def record(runner: Runner = run, path: Path = RECORD) -> str:
    tested = image_id(LOCAL_TAG, runner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"image_id": tested, "tag": LOCAL_TAG}, indent=2) + "\n", encoding="utf-8")
    return tested


def tested_id(path: Path = RECORD) -> str:
    if not path.is_file():
        raise SystemExit("release_image: nothing was recorded; run `record` right after the build")
    return json.loads(path.read_text(encoding="utf-8"))["image_id"]


def verify(tags: list[str], runner: Runner = run, path: Path = RECORD) -> str:
    tested = tested_id(path)
    for tag in [LOCAL_TAG, *tags]:
        found = image_id(tag, runner)
        if found != tested:
            raise SystemExit(f"release_image: {tag} is {found}, but the contract tested {tested}: refusing to publish a different image")
    return tested


def tag(version_path: Path = VERSION_FILE, runner: Runner = run, path: Path = RECORD) -> list[str]:
    version = read_version(version_path)
    tags = release_tags(version)
    if image_id(LOCAL_TAG, runner) != tested_id(path):
        raise SystemExit(f"release_image: {LOCAL_TAG} is not the image the contract tested: refusing to tag it")
    for name in tags:
        runner(["docker", "tag", LOCAL_TAG, name])
    verify(tags, runner, path)  # re-verify the id AFTER tagging, in the step that knows the final version
    return tags


def local_digest(name: str, runner: Runner = run) -> str:
    repo = name.rsplit(":", 1)[0]
    digests = runner(["docker", "image", "inspect", "--format", "{{range .RepoDigests}}{{println .}}{{end}}", name]).splitlines()
    mine = [d.split("@", 1)[1] for d in digests if d.startswith(repo + "@")]
    if len(set(mine)) != 1:
        raise SystemExit(f"release_image: {name} has no unique pushed digest in RepoDigests: {digests}")
    return mine[0]


def credentials(environ: dict[str, str]) -> tuple[str, str]:
    """The registry identity from the step environment (cmru's required_env). Never ambient."""
    user = environ.get("GITHUB_USERNAME", "").strip()
    token = environ.get("GITHUB_PUSH_PAT", "").strip()
    if not user or not token:
        missing = [n for n, v in (("GITHUB_USERNAME", user), ("GITHUB_PUSH_PAT", token)) if not v]
        raise SystemExit(f"release_image: {', '.join(missing)} not set; the push step authenticates explicitly and never from ambient credentials")
    return user, token


def login(user: str, token: str, config_dir: str, runner: Runner = run) -> None:
    """docker login with the PAT on STDIN into an isolated config directory."""
    runner(["docker", "--config", config_dir, "login", REGISTRY, "-u", user, "--password-stdin"], stdin=token + "\n")


NOT_FOUND = re.compile(r"manifest unknown|not found|no such manifest|name unknown", re.IGNORECASE)


def tag_exists(name: str, config_dir: str, runner: Runner = run) -> bool:
    """True when the registry has the tag; False ONLY for a definite 'not found'. Any other failure
    (auth, network, rate limit) is not evidence of absence and aborts the release."""
    try:
        runner(["docker", "--config", config_dir, "buildx", "imagetools", "inspect", name, "--format", "{{.Manifest.Digest}}"])
    except SystemExit as exc:
        if NOT_FOUND.search(str(exc)):
            return False
        raise
    return True


def push(version_path: Path = VERSION_FILE, runner: Runner = run, path: Path = RECORD, environ: dict[str, str] | None = None) -> str:
    version = read_version(version_path)
    tags = release_tags(version)
    user, token = credentials(dict(os.environ) if environ is None else environ)
    verify(tags, runner, path)
    config_dir = tempfile.mkdtemp(prefix="tester-base-docker-config-")
    try:
        login(user, token, config_dir, runner)
        version_tag = tags[-1]
        if tag_exists(version_tag, config_dir, runner):
            raise SystemExit(f"release_image: {version_tag} already exists in the registry; a release never overwrites a version tag")
        pushed: dict[str, str] = {}
        for name in tags:  # latest first, the version tag LAST
            runner(["docker", "--config", config_dir, "push", name])
            served = runner(["docker", "--config", config_dir, "buildx", "imagetools", "inspect", name, "--format", "{{.Manifest.Digest}}"])
            mine = local_digest(name, runner)
            if served != mine:
                raise SystemExit(f"release_image: the registry serves {served} for {name}, the pushed image is {mine}")
            pushed[name] = served
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)
    if len(set(pushed.values())) != 1:
        raise SystemExit(f"release_image: the release tags point at different digests: {pushed}")
    digest = next(iter(pushed.values()))
    print(f"PUSHED {REPOSITORY}@{digest} ({version})")
    return digest


def main(argv: list[str]) -> int:
    verbs = {"record", "tag", "push"}
    if len(argv) != 2 or argv[1] not in verbs:
        print("usage: release_image.py record|tag|push", file=sys.stderr)
        return 2
    if argv[1] == "record":
        print(f"recorded {record()}")
    elif argv[1] == "tag":
        print("tagged " + ", ".join(tag()))
    else:
        push()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
