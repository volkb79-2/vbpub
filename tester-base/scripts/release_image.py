#!/usr/bin/env python3
"""Release exactly the image the contract tested (tester-base R1), stdlib only.

The release used to build the image twice - the `contract` lane tested the first build and the
push step rebuilt from floating inputs (apt, a registry index) - so what reached the registry was
never what was tested. Now there is ONE build (`docker buildx bake tester-base-release --load`,
which tags it `tester-base:local` AND the registry names), and this script is the only other
actor:

* ``record``  right after the build: write the loaded image's id to build/tested-image.json;
* ``verify``  after the contract lane: every release tag still resolves to the recorded id;
* ``push``    ``docker push`` of those same local tags (a push never rebuilds), then read the digest
              the REGISTRY serves for each tag and require it to equal the digest of the pushed
              local image and to be identical for every tag; print ``tester-base@sha256:...``.

Everything is derived from docker-bake.hcl's release target; nothing is typed in.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "build" / "tested-image.json"
LOCAL_TAG = "tester-base:local"

Runner = Callable[[list[str]], str]


def run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"release_image: {' '.join(argv)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def release_tags(bake_json: str) -> list[str]:
    """The registry tags of the release target (the local tag excluded)."""
    targets = json.loads(bake_json)["target"]
    tags = targets["tester-base-release"]["tags"]
    registry = [t for t in tags if t != LOCAL_TAG]
    if LOCAL_TAG not in tags or len(registry) < 1:
        raise SystemExit(f"release_image: the release target must carry {LOCAL_TAG} plus registry tags, got {tags}")
    return registry


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


def verify(tags: list[str], runner: Runner = run, path: Path = RECORD) -> str:
    if not path.is_file():
        raise SystemExit("release_image: nothing was recorded; run `record` right after the build")
    tested = json.loads(path.read_text(encoding="utf-8"))["image_id"]
    for tag in [LOCAL_TAG, *tags]:
        found = image_id(tag, runner)
        if found != tested:
            raise SystemExit(f"release_image: {tag} is {found}, but the contract tested {tested}: refusing to publish a different image")
    return tested


def local_digest(tag: str, runner: Runner = run) -> str:
    repo = tag.rsplit(":", 1)[0]
    digests = runner(["docker", "image", "inspect", "--format", "{{range .RepoDigests}}{{println .}}{{end}}", tag]).splitlines()
    mine = [d.split("@", 1)[1] for d in digests if d.startswith(repo + "@")]
    if len(set(mine)) != 1:
        raise SystemExit(f"release_image: {tag} has no unique pushed digest in RepoDigests: {digests}")
    return mine[0]


def push(tags: list[str], runner: Runner = run, path: Path = RECORD) -> str:
    verify(tags, runner, path)
    pushed = {}
    for tag in tags:
        runner(["docker", "push", tag])
        served = runner(["crane", "digest", tag])
        mine = local_digest(tag, runner)
        if served != mine:
            raise SystemExit(f"release_image: the registry serves {served} for {tag}, the pushed image is {mine}")
        pushed[tag] = served
    if len(set(pushed.values())) != 1:
        raise SystemExit(f"release_image: the release tags point at different digests: {pushed}")
    digest = next(iter(pushed.values()))
    repo = tags[0].rsplit(":", 1)[0]
    print(f"PUSHED {repo}@{digest}")
    return digest


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"record", "verify", "push"}:
        print("usage: release_image.py record|verify|push", file=sys.stderr)
        return 2
    tags = release_tags(run(["docker", "buildx", "bake", "-f", str(ROOT / "docker-bake.hcl"), "tester-base-release", "--print"]))
    if argv[1] == "record":
        print(f"recorded {record()}")
    elif argv[1] == "verify":
        print(f"verified {verify(tags)}")
    else:
        push(tags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
