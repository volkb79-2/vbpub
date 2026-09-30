#!/usr/bin/env python3
"""R10 probe: per-candidate snapshot-equivalent cost on this host.

Mimics isolation._build (init, pack copy, read-tree, child commit, full child
closure walk, cat-file worktree write with fixed mtime, status proof) and the
post-command dirt check, then rmtree.  Variants: full pack vs HEAD-only pack,
whole tree vs assay/ only, with/without update-index --refresh.
Scratch-only; never touches the source checkout except read-only git reads.
"""
import os, shutil, subprocess, sys, tempfile, time, json
from pathlib import Path

FIXED = 946684800
SPARSE_SKIP = b""
ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
           GIT_OPTIONAL_LOCKS="0", LC_ALL="C.UTF-8")
CFG = ["-c", "core.quotePath=false", "-c", "core.hooksPath=/dev/null",
       "-c", "core.fsmonitor=", "-c", "core.preloadIndex=false",
       "-c", "commit.gpgSign=false", "-c", "core.excludesFile="]
OMIT = {b"topos/tests/fixtures/inspect_files/_danger/passwd_link",
        b"topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
        b"topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current"}


def git(gd, wt, *args, inp=b"", extra=()):
    argv = ["git", "--no-pager", *CFG, *extra, f"--git-dir={gd}"]
    if wt is not None:
        argv.append(f"--work-tree={wt}")
    p = subprocess.run(argv + list(args), input=inp, capture_output=True, env=ENV,
                       cwd=wt or gd)
    return p


def entries(seed, commit, prefix):
    p = git(seed, None, "ls-tree", "-r", "-z", "--full-tree", commit)
    out = []
    for rec in p.stdout.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, typ, oid = meta.split()
        if typ != b"blob" or path in OMIT:
            continue
        if prefix and not path.startswith(prefix):
            continue
        out.append((mode.decode(), oid.decode(), path.decode()))
    return out


def write_worktree(gd, root, ents):
    handles = {}
    dirs = set()
    for mode, oid, path in ents:
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dirs.add(dest.parent)
        if mode == "120000":
            tgt = git(gd, None, "cat-file", "blob", oid).stdout
            os.symlink(tgt, dest)
            os.utime(dest, (FIXED, FIXED), follow_symlinks=False)
        else:
            handles.setdefault(oid, []).append(dest)
    oids = list(handles)
    p = subprocess.Popen(["git", *CFG, f"--git-dir={gd}", "cat-file", "--batch"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=ENV)
    import threading
    def _feed():
        p.stdin.write(("\n".join(oids) + "\n").encode())
        p.stdin.close()
    th = threading.Thread(target=_feed); th.start()
    for oid in oids:
        hdr = p.stdout.readline().split()
        size = int(hdr[2])
        data = p.stdout.read(size)
        p.stdout.read(1)
        with open(handles[oid][0], "wb") as f:
            f.write(data)
    th.join(); p.wait()
    for paths in handles.values():
        for d in paths[1:]:
            shutil.copyfile(paths[0], d)
    for mode, oid, path in ents:
        if mode == "120000":
            continue
        q = root / path
        q.chmod(0o755 if mode == "100755" else 0o644)
        os.utime(q, (FIXED, FIXED))
    for d in sorted(dirs, key=lambda x: len(x.parts), reverse=True):
        if d != root:
            d.chmod(0o755)
            os.utime(d, (FIXED, FIXED))


def one(seed, commit, ents, scratch, refresh, fsync_none, mutate_path):
    extra = ("-c", "core.fsync=none") if fsync_none else ()
    t = {}
    t0 = time.monotonic()
    root = Path(tempfile.mkdtemp(dir=scratch, prefix="snap-"))
    tmpl = scratch / "empty-template"
    tmpl.mkdir(exist_ok=True)
    subprocess.run(["git", *CFG, *extra, "init", "-q", f"--template={tmpl}", str(root)],
                   env=ENV, check=True)
    gd = root / ".git"
    t["init"] = time.monotonic() - t0
    t1 = time.monotonic()
    dp = gd / "objects" / "pack"
    dp.mkdir(parents=True, exist_ok=True)
    for item in sorted((seed / "objects" / "pack").iterdir()):
        if item.is_file():
            shutil.copyfile(item, dp / item.name)
    if (seed / "shallow").exists():
        shutil.copyfile(seed / "shallow", gd / "shallow")
    t["pack_copy"] = time.monotonic() - t1
    t2 = time.monotonic()
    git(gd, root, "read-tree", commit, extra=extra)
    if SPARSE_SKIP:
        git(gd, root, "update-index", "--skip-worktree", "-z", "--stdin",
            inp=SPARSE_SKIP, extra=extra)
    new =git(gd, root, "hash-object", "-w", "--stdin", inp=b"mutant\n", extra=extra).stdout.decode().strip()
    git(gd, root, "update-index", "--cacheinfo", f"100644,{new},{mutate_path}", extra=extra)
    tree = git(gd, root, "write-tree", extra=extra).stdout.decode().strip()
    child = git(gd, root, "commit-tree", tree, "-p", commit, inp=b"m\n", extra=extra + (
        "-c", "user.name=A", "-c", "user.email=a@invalid")).stdout.decode().strip()
    t["child_commit"] = time.monotonic() - t2
    t3 = time.monotonic()
    ro = git(gd, None, "rev-list", "--objects", "--no-object-names", child).stdout
    git(gd, None, "cat-file", "--batch-check", inp=ro)
    git(gd, None, "rev-list", "--objects", "--no-walk", "--no-object-names", child)
    t["closure"] = time.monotonic() - t3
    t4 = time.monotonic()
    ents2 = [(m, (new if p == mutate_path else o), p) for m, o, p in ents]
    write_worktree(gd, root, ents2)
    (gd / "HEAD").write_text(child + "\n")
    t["worktree"] = time.monotonic() - t4
    t5 = time.monotonic()
    if refresh:
        git(gd, root, "update-index", "--refresh", extra=extra)
    t["refresh"] = time.monotonic() - t5
    t6 = time.monotonic()
    git(gd, root, "rev-parse", "HEAD")
    git(gd, root, "rev-list", "--count", "HEAD")
    st = git(gd, root, "status", "--porcelain=v1", "-z")
    git(gd, root, "write-tree", extra=extra)
    git(gd, root, "ls-files", "-v", "-z")
    t["verify"] = time.monotonic() - t6
    t7 = time.monotonic()
    git(gd, root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    git(gd, root, "ls-files", "--others", "--exclude-per-directory=.gitignore", "-z")
    t["dirt"] = time.monotonic() - t7
    t8 = time.monotonic()
    shutil.rmtree(root)
    t["rmtree"] = time.monotonic() - t8
    t["total"] = time.monotonic() - t0
    t["status_dirty_bytes"] = len(st.stdout)
    return t


def main():
    seed = Path(sys.argv[1]).resolve(); commit = sys.argv[2]; scratch = Path(sys.argv[3])
    variant = sys.argv[4]; reps = int(sys.argv[5])
    global SPARSE_SKIP
    prefix = b"assay/" if "sparse" in variant else None
    ents = entries(seed, commit, prefix)
    if prefix:
        allp = git(seed, None, "ls-tree", "-r", "-z", "--name-only", "--full-tree", commit).stdout
        SPARSE_SKIP = b"".join(p + b"\0" for p in allp.split(b"\0") if p and not p.startswith(prefix))
    else:
        SPARSE_SKIP = b"".join(p + b"\0" for p in sorted(OMIT))
    res = []
    for _ in range(reps):
        res.append(one(seed, commit, ents, scratch, refresh="refresh" in variant,
                       fsync_none="nofsync" in variant,
                       mutate_path="assay/src/assay/__init__.py"))
    keys = [k for k in res[0]]
    med = {k: sorted(r[k] for r in res)[len(res) // 2] for k in keys}
    print(json.dumps({"variant": variant, "files": len(ents), "reps": reps,
                      "median": {k: round(v, 3) for k, v in med.items()},
                      "all_total": [round(r["total"], 3) for r in res]}))


main()
