#!/usr/bin/env python3
"""Release exactly the image the contract tested (tester-base R1, V1, V2, W1-W5), stdlib only.

One build, four actors, in cmru's own step order (prepare -> gate -> tag -> build_step -> push):

* ``build``   (prepare) builds the image ONCE under a PRIVATE per-release tag
              ``tester-base:release-<source-commit>`` (derived from HEAD; the shared ``tester-base:local``
              is the developer bootstrap tag that other sessions rebuild, so a release never tests or
              pushes it) and writes the tag and image id to build/tested-image.json. `prepare` runs BEFORE
              cmru writes the new VERSION, so nothing versioned exists yet.
* ``gate``    (run-tests) runs the ``contract`` lane against that private tag (TESTER_BASE_IMAGE is set
              from the record, never typed).
* ``tag``     (build_step) cmru has by now written the final VERSION and tagged: read it (FAIL when it is
              missing or not x.y.z - there is no default), require the private tag to still be the
              recorded id, and give that very image the registry names ``:<VERSION>`` and ``:latest``
              with ``docker tag``. Nothing is rebuilt.
* ``push``    log in (credentials from the step environment only, never ambient: an isolated
              DOCKER_CONFIG, the PAT on stdin, never on argv), re-verify the ids, push ``:latest`` and
              then ``:<VERSION>`` LAST, and require that the registry serves the digest that was pushed.
              A version tag that already exists is never overwritten: if it already serves THIS image
              (same image id) the release is treated as already published and only the final checks run;
              if it serves another image the release stops.

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


def private_tag(commit: str) -> str:
    """The per-release candidate tag, derived from the source commit prepare ran at."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit(f"release_image: {commit!r} is not a commit id")
    return f"tester-base:release-{commit[:12]}"


def source_commit(runner: Runner = run) -> str:
    return runner(["git", "rev-parse", "HEAD"])


def image_id(tag: str, runner: Runner = run) -> str:
    value = runner(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise SystemExit(f"release_image: {tag} has no image id ({value!r})")
    return value


def build(runner: Runner = run, path: Path = RECORD) -> str:
    """Build once under the private per-release tag and record (tag, id). Returns the tag."""
    tag_name = private_tag(source_commit(runner))
    runner(["docker", "buildx", "bake", "-f", str(ROOT / "docker-bake.hcl"), "tester-base-local",
            "--set", f"tester-base-local.tags={tag_name}", "--load"])
    return record(tag_name, runner, path)


def record(tag_name: str, runner: Runner = run, path: Path = RECORD) -> str:
    tested = image_id(tag_name, runner)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"image_id": tested, "tag": tag_name}, indent=2) + "\n", encoding="utf-8")
    return tag_name


def recorded(path: Path = RECORD) -> tuple[str, str]:
    """(private tag, image id) of the build prepare made."""
    if not path.is_file():
        raise SystemExit("release_image: nothing was recorded; run `build` in the prepare step")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("tag") == "tester-base:local":
        raise SystemExit("release_image: the shared developer tag tester-base:local is never a release candidate")
    return data["tag"], data["image_id"]


def verify(names: list[str], runner: Runner = run, path: Path = RECORD) -> str:
    candidate, tested = recorded(path)
    for name in [candidate, *names]:
        found = image_id(name, runner)
        if found != tested:
            raise SystemExit(f"release_image: {name} is {found}, but the contract tested {tested}: refusing to publish a different image")
    return tested


def gate(runner_exec: Callable[[list[str], dict[str, str]], int] | None = None, path: Path = RECORD) -> int:
    """Run the contract lane against the recorded private tag (the lane's image env is derived)."""
    candidate, _tested = recorded(path)
    env = {**os.environ, "TESTER_BASE_IMAGE": candidate}
    argv = ["./run-gate.py", "contract"]
    if runner_exec is not None:
        return runner_exec(argv, env)
    return subprocess.run(argv, cwd=ROOT, env=env, check=False).returncode


def tag(version_path: Path = VERSION_FILE, runner: Runner = run, path: Path = RECORD) -> list[str]:
    version = read_version(version_path)
    names = release_tags(version)
    candidate, tested = recorded(path)
    if image_id(candidate, runner) != tested:
        raise SystemExit(f"release_image: {candidate} is not the image the contract tested: refusing to tag it")
    for name in names:
        runner(["docker", "tag", candidate, name])
    verify(names, runner, path)  # re-verify the id AFTER tagging, in the step that knows the final version
    return names


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


# ONLY a registry's own "this does not exist" answers. `docker manifest inspect` prints the registry error
# code text ("manifest unknown"); other tools print MANIFEST_UNKNOWN / NAME_UNKNOWN or an explicit 404.
# `no such manifest: <ref>` is the docker CLI's own definite text in other versions. Measured (docker 29.8.2,
# ghcr.io, 2026-10-19): `docker manifest inspect` on a missing tag of an existing repository AND on a
# repository that does not exist yet (tester-base's first release) both print exactly `manifest unknown`,
# rc=1 (pinned in tests/test_release_image.py). A bare "not found" is NOT accepted: it also matches `executable file not found in $PATH` (a broken
# buildx or docker under the isolated config), which would read as "the tag is free".
NOT_FOUND = re.compile(
    r"MANIFEST_UNKNOWN|NAME_UNKNOWN|\bmanifest unknown\b|\bname unknown\b|\bno such manifest: \S+|\b404\b", re.IGNORECASE
)


def tag_exists(name: str, config_dir: str, runner: Runner = run) -> bool:
    """True when the registry has the tag; False ONLY for the registry's own 'does not exist' answer. Any
    other failure (auth, network, rate limit, a broken tool) is not evidence of absence and aborts."""
    try:
        runner(["docker", "--config", config_dir, "manifest", "inspect", name])
    except SystemExit as exc:
        if NOT_FOUND.search(str(exc)):
            return False
        raise
    return True


def served_image_id(name: str, config_dir: str, runner: Runner = run) -> str:
    """The image id (config digest) the registry serves for `name` - the content identity that equals a
    local `docker image inspect` id, independent of how the manifest was wrapped."""
    def raw(ref: str) -> dict:
        return json.loads(runner(["docker", "--config", config_dir, "buildx", "imagetools", "inspect", ref, "--raw"]))

    manifest = raw(name)
    if "config" in manifest:
        return manifest["config"]["digest"]
    repo = name.rsplit(":", 1)[0]
    for entry in manifest.get("manifests", []):
        platform = entry.get("platform") or {}
        if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
            return raw(f"{repo}@{entry['digest']}")["config"]["digest"]
    raise SystemExit(f"release_image: {name} serves no linux/amd64 image")


def push(version_path: Path = VERSION_FILE, runner: Runner = run, path: Path = RECORD, environ: dict[str, str] | None = None) -> str:
    version = read_version(version_path)
    names = release_tags(version)
    user, token = credentials(dict(os.environ) if environ is None else environ)
    tested = verify(names, runner, path)
    config_dir = tempfile.mkdtemp(prefix="tester-base-docker-config-")
    try:
        login(user, token, config_dir, runner)
        version_tag = names[-1]
        already_published = False
        if tag_exists(version_tag, config_dir, runner):
            served = served_image_id(version_tag, config_dir, runner)
            if served != tested:
                raise SystemExit(
                    f"release_image: {version_tag} already exists and serves image {served}, not the tested {tested}; "
                    "a release never overwrites a version tag"
                )
            already_published = True
            print(f"release_image: {version_tag} is already published with the tested image {tested}; continuing to the final checks")
        pushed: dict[str, str] = {}
        for name in names:  # latest first, the version tag LAST
            if name == version_tag and already_published:
                pushed[name] = runner(["docker", "--config", config_dir, "buildx", "imagetools", "inspect", name, "--format", "{{.Manifest.Digest}}"])
                continue
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
    verbs = {"build", "gate", "tag", "push"}
    if len(argv) != 2 or argv[1] not in verbs:
        print("usage: release_image.py build|gate|tag|push", file=sys.stderr)
        return 2
    if argv[1] == "build":
        print(f"built and recorded {build()}")
    elif argv[1] == "gate":
        return gate()
    elif argv[1] == "tag":
        print("tagged " + ", ".join(tag()))
    else:
        push()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
