"""A throw-away git world for the write-path tests: a bare remote, a cockpit clone with a tagged release, a fake ``gh``.

Nothing here touches the network or a real remote: every push goes to a local bare repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from conftest import ASSEMBLE, COMMIT, GOLDEN, TREE, assemble_kwargs
from rigor_record import RepoGit, canonical_json
from rigor_record.assemble import assemble

PROJECT = "cgprofile"
TAG = "cgprofile-v2.0.0"
LANE_DIR = "cgprofile"

FAKE_GH = """#!/bin/sh
# fake gh: gh release download TAG --pattern NAME --dir DIR [--clobber] [--repo R]
tag="$3"; shift 3
while [ $# -gt 0 ]; do
  case "$1" in
    --pattern) name="$2"; shift 2;;
    --dir) dir="$2"; shift 2;;
    --repo) shift 2;;
    *) shift;;
  esac
done
if [ -n "$FAKE_GH_FAIL" ]; then echo "$FAKE_GH_FAIL" >&2; exit 1; fi
src="$FAKE_GH_ASSETS/$tag/$name"
if [ "$FAKE_GH_NOFILE" = "$name" ]; then exit 0; fi
if [ ! -f "$src" ]; then echo "no such asset" >&2; exit 1; fi
cp "$src" "$dir/$name"
"""


def env_for(home: Path) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": str(home), "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        capture_output=True, text=True, check=check, env=env_for(repo),
    )
    return done.stdout.strip()


def retarget(data: bytes | str, commit: str, tree: str) -> bytes:
    text = data.decode() if isinstance(data, bytes) else data
    return text.replace(COMMIT, commit).replace(TREE, tree).encode()


def gate_pair(commit: str, tree: str, *, lane_gate: str = "golden/fragments/run-gate-r2-assay.json",
              lane_assay: str = "golden/fragments/assay-r2-survivors.json", edit=None) -> list[tuple[str, bytes]]:
    """The golden R2 assay + run-gate fragments retargeted to ``commit``/``tree`` (digest reference recomputed)."""
    assay = json.loads(retarget((GOLDEN.parent / lane_assay).read_bytes(), commit, tree))
    assay["project_tree"] = tree
    if edit is not None:
        edit(assay)
    assay_bytes = canonical_json(assay).encode()
    gate = json.loads(retarget((GOLDEN.parent / lane_gate).read_bytes(), commit, tree))
    gate["assay_fragment_sha256"] = hashlib.sha256(assay_bytes).hexdigest()
    return [("assay.json", assay_bytes), ("gate.json", canonical_json(gate).encode())]


@dataclass
class World:
    root: Path
    bare: Path
    clone: Path
    commit: str
    tree: str
    assets: Path
    gh: Path
    record: dict
    record_text: str
    assay_commit: str

    def clone_again(self, name: str) -> Path:
        target = self.root / name
        subprocess.run(["git", "clone", "-q", str(self.bare), str(target)], check=True, env=env_for(self.root))
        return target

    def environ(self) -> dict[str, str]:
        return {"FAKE_GH_ASSETS": str(self.assets)}

    def remote_files(self, path: str, ref: str = "main") -> str:
        return subprocess.run(
            ["git", "-C", str(self.bare), "show", f"{ref}:{path}"], capture_output=True, text=True, check=True,
        ).stdout

    def remote_log(self) -> list[str]:
        return git(self.bare, "log", "--format=%s", "main").splitlines()


def make_world(tmp_path: Path, project_dir: str = LANE_DIR) -> World:
    root = tmp_path
    base = tmp_path / "cockpit" if project_dir == "." else tmp_path / "cockpit" / project_dir
    bare = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=env_for(root))
    clone = root / "cockpit"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    (base / "rigor").mkdir(parents=True)
    (base / "x.txt").write_text("x")
    shutil.copy(ASSEMBLE / "waivers.toml", base / "rigor" / "waivers.toml")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "one")
    git(clone, "tag", "-a", "-m", "release", TAG)
    git(clone, "remote", "add", "origin", str(bare))
    git(clone, "push", "-q", "origin", "main", TAG)
    commit = git(clone, "rev-parse", "HEAD")
    tree = git(clone, "rev-parse", "HEAD^{tree}" if project_dir == "." else f"HEAD:{project_dir}")
    kwargs = assemble_kwargs(
        commit=commit, project_dir=project_dir, git=RepoGit(clone),
        fragments=[(n, retarget(d, commit, tree)) for n, d in assemble_kwargs()["fragments"]],
        review=retarget(assemble_kwargs()["review"], commit, tree),
        artifacts=json.loads(retarget(json.dumps(assemble_kwargs()["artifacts"]), commit, tree)),
    )
    result = assemble(**kwargs)
    assets = root / "assets"
    (assets / TAG).mkdir(parents=True)
    data = result.record_text.encode()
    (assets / TAG / f"{TAG}-rigor.json").write_bytes(data)
    (assets / TAG / f"{TAG}-rigor.json.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {TAG}-rigor.json\n")
    gh = root / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    return World(root, bare, clone, commit, tree, assets, gh, result.record, result.record_text, commit)
