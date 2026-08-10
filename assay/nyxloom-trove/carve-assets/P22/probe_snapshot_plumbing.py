#!/usr/bin/env python3
"""P22 tracer bullet: real Git pack transfer and fixed child commit.

This intentionally does not import Assay. It exercises the proposed Git
construction through an independent, tiny implementation and prints one JSON
receipt. It is not production code and does not choose production helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path


GIT = shutil.which("git")
if GIT is None:
    raise SystemExit("git is absent from the declared PATH")

BASE_ENV = {
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "",
    "PAGER": "",
}
FIXED_IDENTITY = {
    "GIT_AUTHOR_NAME": "Assay",
    "GIT_AUTHOR_EMAIL": "assay@invalid",
    "GIT_COMMITTER_NAME": "Assay",
    "GIT_COMMITTER_EMAIL": "assay@invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}
FIXED_CONFIG = (
    "-c", "core.quotePath=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=",
    "-c", "core.commitGraph=false",
    "-c", "core.multiPackIndex=false",
    "-c", "commit.gpgSign=false",
    "-c", "core.excludesFile=",
)


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    data: bytes | None = None,
    extra_env: dict[str, str] | None = None,
) -> bytes:
    env = dict(BASE_ENV)
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{argv!r} failed ({completed.returncode}): "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


def source_git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return run(
        [
            GIT,
            "--no-pager",
            "--no-optional-locks",
            "--literal-pathspecs",
            f"--git-dir={repo / '.git'}",
            f"--work-tree={repo}",
            *FIXED_CONFIG,
            "-C",
            str(repo),
            *args,
        ],
        data=data,
    )


def private_git(
    root: Path,
    *args: str,
    data: bytes | None = None,
    identity: bool = False,
) -> bytes:
    extra = dict(FIXED_IDENTITY) if identity else None
    return run(
        [
            GIT,
            "--no-pager",
            "--no-optional-locks",
            "--literal-pathspecs",
            f"--git-dir={root / '.git'}",
            f"--work-tree={root}",
            *FIXED_CONFIG,
            "-C",
            str(root),
            *args,
        ],
        data=data,
        extra_env=extra,
    )


def write(root: Path, name: str, data: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def digest_objects(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((repo / ".git/objects").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(repo / ".git/objects").as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="assay-p22-probe-") as temporary:
        top = Path(temporary)
        source = top / "source"
        private = top / "private"
        naive = top / "naive-project-copy"
        archive = top / "archive"
        source.mkdir()
        private.mkdir()
        archive.mkdir()

        run([GIT, "init", "-q", str(source)])
        write(source, ".gitignore", b"apps/p/coverage.json\n")
        write(source, ".gitattributes", b"filtered.txt filter=trap\n")
        write(source, "apps/p/main.py", b"print(open('../../shared/input.txt').read())\n")
        write(source, "shared/input.txt", b"tracked sibling\n")
        write(source, "filtered.txt", b"raw committed bytes\n")
        (source / "links").mkdir()
        os.symlink("../shared/input.txt", source / "links/in")
        run([GIT, "-C", str(source), "add", "-A"])
        run(
            [GIT, "-C", str(source), "commit", "-q", "-m", "base"],
            extra_env=FIXED_IDENTITY,
        )
        base = run([GIT, "-C", str(source), "rev-parse", "HEAD"]).decode().strip()

        write(source, "shared/input.txt", b"replace-ref lie\n")
        run([GIT, "-C", str(source), "add", "-A"])
        run(
            [GIT, "-C", str(source), "commit", "-q", "-m", "evil"],
            extra_env=FIXED_IDENTITY,
        )
        evil = run([GIT, "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
        run([GIT, "-C", str(source), "reset", "--hard", base])
        run([GIT, "-C", str(source), "replace", base, evil])
        sentinel = top / "consumer-program-ran"
        run([GIT, "-C", str(source), "config", "filter.trap.smudge", f"touch {sentinel}"])
        run([GIT, "-C", str(source), "config", "filter.trap.clean", f"touch {sentinel}"])
        decoy = top / "decoy"
        decoy.mkdir()
        run([GIT, "-C", str(source), "config", "core.worktree", str(decoy)])
        write(source, "apps/p/coverage.json", b"stale ignored evidence\n")

        source_before = digest_objects(source)
        object_ids = source_git(
            source, "rev-list", "--objects", "--no-object-names", base
        ).splitlines()
        metadata = source_git(
            source,
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            data=b"\n".join(object_ids) + b"\n",
        ).splitlines()
        total_uncompressed = sum(int(line.rsplit(b" ", 1)[1]) for line in metadata)
        if sentinel.exists():
            raise RuntimeError("consumer program ran during object inventory")

        run([GIT, "init", "-q", str(private)])
        pack = source_git(
            source,
            "pack-objects",
            "--stdout",
            data=b"\n".join(object_ids) + b"\n",
        )
        private_git(private, "index-pack", "--stdin", data=pack)
        if sentinel.exists():
            raise RuntimeError("consumer program ran during pack transfer")
        (private / ".git/HEAD").write_text(f"{base}\n", encoding="ascii")

        records = source_git(
            source, "ls-tree", "-rz", "--full-tree", "-r", base
        ).split(b"\x00")[:-1]
        for record in records:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, oid = header.split(b" ")
            if kind != b"blob":
                raise RuntimeError(f"unexpected probe kind {kind!r}")
            path = private / raw_path.decode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = private_git(private, "cat-file", "blob", oid.decode())
            if mode == b"120000":
                os.symlink(raw.decode("utf-8"), path)
            else:
                path.write_bytes(raw)
                path.chmod(0o755 if mode == b"100755" else 0o644)
        private_git(private, "read-tree", base)
        status = private_git(private, "status", "--porcelain=v1")
        if status:
            raise RuntimeError(f"private base is not clean: {status!r}")
        if sentinel.exists():
            raise RuntimeError("consumer program ran during private materialization")

        old_blob = private_git(private, "rev-parse", f"{base}:shared/input.txt").decode().strip()
        new_bytes = b"mutated sibling\n"
        new_blob = private_git(private, "hash-object", "-w", "--stdin", data=new_bytes).decode().strip()
        private_git(
            private,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{new_blob},shared/input.txt",
        )
        tree = private_git(private, "write-tree").decode().strip()
        commit_argv = ("commit-tree", tree, "-p", base)
        child_a = private_git(
            private,
            *commit_argv,
            data=b"assay snapshot replacement\n",
            identity=True,
        ).decode().strip()
        child_b = private_git(
            private,
            *commit_argv,
            data=b"assay snapshot replacement\n",
            identity=True,
        ).decode().strip()
        if child_a != child_b:
            raise RuntimeError("fixed replacement identity was not deterministic")
        (private / "shared/input.txt").write_bytes(new_bytes)
        (private / ".git/HEAD").write_text(f"{child_a}\n", encoding="ascii")
        if private_git(private, "status", "--porcelain=v1"):
            raise RuntimeError("private replacement is not clean")
        if sentinel.exists():
            raise RuntimeError("consumer program ran during replacement construction")

        shutil.copytree(source / "apps/p", naive)
        if (naive / "../../shared/input.txt").resolve().is_file():
            raise RuntimeError("project-only copy unexpectedly retained the sibling")
        if not (naive / "coverage.json").is_file():
            raise RuntimeError("project-only copy did not reproduce stale ignored input")
        if sentinel.exists():
            raise RuntimeError("consumer program ran during naive filesystem copy")

        # `git archive` is one of the tempting constructions the handoff
        # explicitly forbids. With this committed attribute and local filter,
        # the real command executes consumer configuration; record that as a
        # witnessed negative after the safe construction has already proved
        # the sentinel absent.
        archive_bytes = source_git(source, "archive", "--format=tar", base)
        tar_path = top / "commit.tar"
        tar_path.write_bytes(archive_bytes)
        with tarfile.open(tar_path) as tar:
            tar.extractall(archive, filter="data")
        if (archive / ".git").exists():
            raise RuntimeError("git archive unexpectedly created a private repository")
        if not sentinel.exists():
            raise RuntimeError("hostile git archive filter did not execute as expected")

        if digest_objects(source) != source_before:
            raise RuntimeError("source object store changed during transfer")
        if (private / ".git/objects/info/alternates").exists():
            raise RuntimeError("the private repository depends on alternates")
        if private_git(private, "rev-parse", "HEAD").decode().strip() != child_a:
            raise RuntimeError("private HEAD is not the child commit")
        parent_line = private_git(
            private, "rev-list", "--parents", "-n", "1", child_a
        ).decode().strip()
        if parent_line != f"{child_a} {base}":
            raise RuntimeError(f"wrong child parent: {parent_line}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "base": base,
                    "child": child_a,
                    "old_blob": old_blob,
                    "new_blob": new_blob,
                    "objects": len(object_ids),
                    "total_uncompressed_bytes": total_uncompressed,
                    "pack_bytes": len(pack),
                    "private_status": "clean",
                    "source_object_store_unchanged": True,
                    "consumer_program_executed_by_product_construction": False,
                    "naive_project_copy_missing_sibling": True,
                    "naive_project_copy_contains_stale_ignored": True,
                    "git_archive_has_private_repository": False,
                    "git_archive_executed_consumer_filter": True,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
