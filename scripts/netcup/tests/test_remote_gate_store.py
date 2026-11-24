"""Hermetic tests for `remote-gate run --collect-store` (RG-298 part 1): the generated job script runs in real bash against a fake
`docker` and a fake run-gate that builds the store inside the run's own throwaway checkout, and `collect` runs against the scripted
`Fake` runner. The store's content, size and sha256 are checked at the file boundary; no network, no host, no real container."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

from test_remote_gate import SHA, Fake, ctx_for, git_handlers, hosts, psi, rg, run_args  # noqa: F401
from test_remote_gate_repos import LS, _exe, _source_bundle, sandbox  # noqa: F401

STORE = "scripts/.run-gate/assay-shards/k1"

# The fake lane builds whatever FAKE_STORE_SETUP says inside THIS run's checkout (the only rg-* directory under the repo's .worktrees).
STORE_RUN_GATE = r'''#!/usr/bin/env bash
ck=$(ls -d "$ASSAY_GATE_HOST_REPO_ROOT"/.worktrees/rg-*)
cd "$ck" && eval "${FAKE_STORE_SETUP:-:}"
echo "run-gate: lane '$1' verdict ${FAKE_VERDICT:-PASS}; exit_code ${FAKE_RC:-0}; log /tmp/run-gate/lanes/none.log"
exit "${FAKE_RC:-0}"
'''


def run_job(sb, stores, setup="", max_mib=512, rc=0, seeds=()):
    """Render the real job for these (raw, unnormalised) store paths and run it in bash; returns the CompletedProcess."""
    _exe(sb.rgbin, STORE_RUN_GATE)
    sb.env["FAKE_STORE_SETUP"] = setup
    sb.env["FAKE_RC"] = str(rc)
    sb.env["FAKE_VERDICT"] = "PASS" if rc == 0 else "FAIL"
    sha, bundle = _source_bundle(sb.tmp)
    (sb.home / "rg" / "j1" / "in.bundle").write_bytes(bundle.read_bytes())
    host = hosts()["r1002"]
    host.repos = {"vbpub": {"run_gate_bins": [str(sb.rgbin)]}}
    text = rg.render_job(host, "j1", {"sha": sha, "base": None, "tags": []}, "docs", ".", "", 0, [], "vbpub",
                         collect_stores=stores, store_max_mib=max_mib, seed_stores=seeds)
    (sb.tmp / "mem").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    (sb.tmp / "io").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    text = text.replace("/proc/pressure/memory /proc/pressure/io", f"{sb.tmp}/mem {sb.tmp}/io").replace("/tmp/rg-j1.pid", str(sb.tmp / "rg-j1.pid"))
    path = sb.tmp / "job.sh"
    path.write_text(text)
    return subprocess.run(["bash", str(path)], env=sb.env, capture_output=True, text=True, timeout=120)


def rgd(sb):
    return sb.home / "rg" / "j1"


def tree_setup(rel=STORE):
    return (f"mkdir -p {rel}/sub {rel}/empty && echo one > {rel}/a.json && echo two > {rel}/sub/b.json && "
            f"head -c 5000 /dev/zero > {rel}/sub/blob.bin")


def listing(tgz):
    with tarfile.open(tgz) as t:
        return sorted((m.name, m.size) for m in t.getmembers())


# ---- job side: collect before the checkout is removed ----

def test_a_present_store_is_packed_with_a_matching_sha256_and_the_tree_listing(sandbox):
    r = run_job(sandbox, [STORE], tree_setup())
    assert r.returncode == 0, r.stdout + r.stderr
    tgz = rgd(sandbox) / "assay-store.tgz"
    sums = (rgd(sandbox) / "assay-store.tgz.sha256").read_text().split()
    assert sums == [hashlib.sha256(tgz.read_bytes()).hexdigest(), "assay-store.tgz"]
    p = STORE
    assert listing(tgz) == sorted([
        (p, 0), (f"{p}/a.json", 4), (f"{p}/empty", 0),
        (f"{p}/sub", 0), (f"{p}/sub/b.json", 4), (f"{p}/sub/blob.bin", 5000)])
    assert f"STORE-COLLECTED {STORE}" in r.stdout and "STORE-ABSENT" not in r.stdout
    assert not (rgd(sandbox) / "assay-store.tgz.part").exists() and not (rgd(sandbox) / "assay-store.err").exists()


def test_the_store_is_packed_before_the_checkout_is_removed(sandbox):
    r = run_job(sandbox, [STORE], tree_setup())
    lines = r.stdout.splitlines()
    packed = next(i for i, x in enumerate(lines) if "STORE-COLLECTED" in x)
    removed = next(i for i, x in enumerate(lines) if "removed checkout" in x)
    assert packed < removed
    # the checkout really is gone, so an archive that exists was necessarily taken before: a pack after the removal sees STORE-ABSENT
    assert not list((sandbox.home / "checkouts" / "vbpub" / ".worktrees").glob("rg-*"))
    assert (rgd(sandbox) / "assay-store.tgz").stat().st_size > 0


def test_an_absent_store_logs_one_absent_line_packs_nothing_and_keeps_the_lane_rc(sandbox):
    r = run_job(sandbox, [STORE], "", rc=1)
    assert r.returncode == 1 and (rgd(sandbox) / "rc").read_text().strip() == "1"
    assert [x for x in r.stdout.splitlines() if "STORE-" in x][0].endswith(f"STORE-ABSENT {STORE}")
    assert r.stdout.count("STORE-ABSENT") == 1
    assert not (rgd(sandbox) / "assay-store.tgz").exists() and not (rgd(sandbox) / "assay-store.tgz.sha256").exists()
    assert "verdict FAIL" in (rgd(sandbox) / "lane.log").read_text()


def test_a_store_above_the_bound_is_not_packed_and_the_lane_verdict_is_untouched(sandbox):
    r = run_job(sandbox, [STORE], f"mkdir -p {STORE} && head -c 2200000 /dev/zero > {STORE}/big.bin", max_mib=1)
    assert r.returncode == 0, r.stdout + r.stderr
    line = next(x for x in r.stdout.splitlines() if "STORE-TOO-LARGE" in x)
    assert line.split()[-2] == STORE and int(line.split()[-1]) > 2200000
    assert not (rgd(sandbox) / "assay-store.tgz").exists()
    assert "verdict PASS" in (rgd(sandbox) / "lane.log").read_text()


def test_a_small_store_under_the_bound_is_packed(sandbox):
    r = run_job(sandbox, [STORE], f"mkdir -p {STORE} && echo x > {STORE}/f", max_mib=1)
    assert "STORE-TOO-LARGE" not in r.stdout and (rgd(sandbox) / "assay-store.tgz").exists()


@pytest.mark.parametrize("raw,why", [("../x", "invalid-path"), ("/etc", "invalid-path"), ("a/../b", "invalid-path"), ("a//b", "invalid-path"),
                                     ("./a", "invalid-path"), ("a b", "invalid-path"), ("", "invalid-path"), (".", "invalid-path")])
def test_the_host_refuses_a_path_that_is_not_canonical_and_relative(sandbox, raw, why):
    r = run_job(sandbox, [raw], "mkdir -p x && echo 1 > x/f", rc=0)
    assert r.returncode == 0
    assert f"STORE-COLLECT-FAILED {raw} {why}" in r.stdout
    assert not (rgd(sandbox) / "assay-store.tgz").exists()


def test_the_host_refuses_a_symlinked_component_anywhere_in_the_path(sandbox):
    setup = (f"mkdir -p real/store scripts && echo secret > real/store/f && ln -s ../real scripts/.run-gate && "
             f"ln -s real/store storelink && mkdir -p ok/dir && ln -s ../real/store ok/dir/leaf")
    r = run_job(sandbox, ["scripts/.run-gate/store", "storelink", "ok/dir/leaf"], setup)
    assert r.returncode == 0, r.stdout + r.stderr
    for rel in ("scripts/.run-gate/store", "storelink", "ok/dir/leaf"):
        assert f"STORE-COLLECT-FAILED {rel} symlink" in r.stdout, rel
    assert not (rgd(sandbox) / "assay-store.tgz").exists()


def test_several_stores_share_one_archive_and_a_refused_one_does_not_block_the_rest(sandbox):
    setup = "mkdir -p s1 s2 && echo a > s1/f && echo b > s2/f && ln -s s1 sl"
    r = run_job(sandbox, ["s1", "gone", "sl", "s2"], setup)
    assert "STORE-ABSENT gone" in r.stdout and "STORE-COLLECT-FAILED sl symlink" in r.stdout and "STORE-COLLECTED s1 s2" in r.stdout
    names = [n for n, _ in listing(rgd(sandbox) / "assay-store.tgz")]
    assert names == sorted(["s1", "s1/f", "s2", "s2/f"])


FAKE_TAR = """#!/usr/bin/env bash
# a disk that fills up while the archive is written: only the .part archive of the job fails, every other tar is the real one
case "$*" in *assay-store.tgz.part*) echo "tar: write error: No space left on device" >&2; exit 2;; esac
exec /usr/bin/tar "$@"
"""


def test_a_pack_failure_is_reported_per_store_leaves_no_archive_and_keeps_the_lane_rc(sandbox):
    _exe(sandbox.tmp / "bin" / "tar", FAKE_TAR)
    r = run_job(sandbox, ["s1", "s2"], "mkdir -p s1 s2 && echo a > s1/f && echo b > s2/f", rc=1)
    assert r.returncode == 1 and (rgd(sandbox) / "rc").read_text().strip() == "1"
    for rel in ("s1", "s2"):
        assert f"STORE-COLLECT-FAILED {rel} pack-failed tar: write error: No space left on device" in r.stdout
    assert not list(rgd(sandbox).glob("assay-store*"))


def test_a_run_without_collect_store_creates_no_store_files(sandbox):
    r = run_job(sandbox, [], tree_setup())
    assert r.returncode == 0 and "STORE-" not in r.stdout
    assert not list(rgd(sandbox).glob("assay-store*"))


def test_the_job_with_stores_is_valid_bash(tmp_path):
    host = hosts()["r1002"]
    text = rg.render_job(host, "t", {"sha": SHA, "base": None, "tags": []}, "docs", ".", "", 0, [], "vbpub", collect_stores=["a/b", "c"], store_max_mib=7)
    assert "STORES=(a/b c)\n" in text and f"STORE_MAX_BYTES={7 * 1024 * 1024}\n" in text and "@@" not in text
    p = tmp_path / "j.sh"
    p.write_text(text)
    subprocess.run(["bash", "-n", str(p)], check=True)


# ---- local side: refusal before anything is sent ----

@pytest.mark.parametrize("raw", ["/abs/path", "../x", "a/../../x", "", ".", "a b", "a;rm"])
def test_run_refuses_a_bad_collect_store_path_before_a_host_is_contacted(tmp_path, raw):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError) as e:
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "s1", "--collect-store", raw))
    assert e.value.code == 2
    assert not any(c[0][0] == "ssh" or c[0][0] == "apicheck" for c in ctx.runner.calls)
    assert not (tmp_path / "out" / "s1").exists()


def test_normalise_store_rel_cleans_the_path_and_refuses_a_symlinked_component(tmp_path):
    assert rg.normalise_store_rel("a//b/./c/", str(tmp_path)) == "a/b/c"
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real")
    with pytest.raises(rg.GateError, match="symlink") as e:
        rg.normalise_store_rel("link/store", str(tmp_path))
    assert e.value.code == 2
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "leaf").symlink_to("../real")
    with pytest.raises(rg.GateError, match="symlink"):
        rg.normalise_store_rel("d/leaf", str(tmp_path))


def test_run_validates_the_store_bound_and_records_the_stores_in_meta_and_the_job(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="--store-max-mib"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "s2", "--store-max-mib", "0"))
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "s3", "--collect-store", "x//y/",
                                    "--collect-store", "x/y", "--collect-store", "z", "--store-max-mib", "9")) == 0
    meta = json.loads((tmp_path / "out" / "s3" / "meta.json").read_text())
    assert meta["collect_store"] == ["x/y", "z"] and meta["store_max_mib"] == 9
    assert "STORES=(x/y z)\n" in (tmp_path / "out" / "s3" / "job.sh").read_text()


# ---- collect: fetch and verify ----

PAYLOAD = "pretend tgz bytes\n"


def collected(tmp_path, sha_text, payload=PAYLOAD, with_store=True, job_log="", rc="0"):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    extra = ["--collect-store", "x/y"] if with_store else []
    assert rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "c1", *extra)) == 0
    ctx.runner.handlers[:0] = [
        ("assay-store.tgz.sha256", rg.Result(0, sha_text)),
        ("assay-store.tgz\n}", rg.Result(0, payload)),
        ("rg/c1/lane.log", rg.Result(0, f"run-gate: lane 'suite' verdict {'PASS' if rc == '0' else 'FAIL'}; exit_code {rc}\n")),
        ("jobs/rg-c1/job.log", rg.Result(0, job_log)),
        ("/EXIT", rg.Result(0, rc + "\n")),
        ("/rc", rg.Result(0, rc + "\n")),
    ]
    host, meta, run_dir = rg.load_meta(ctx, "c1")
    return ctx, out, rg.collect(ctx, host, meta, run_dir), run_dir


def test_collect_fetches_both_files_and_verifies_the_checksum(tmp_path):
    good = hashlib.sha256(PAYLOAD.encode()).hexdigest()
    ctx, out, rc, run_dir = collected(tmp_path, f"{good}  assay-store.tgz\n")
    assert rc == 0
    assert (run_dir / "assay-store.tgz").read_text() == PAYLOAD
    assert (run_dir / "assay-store.tgz.sha256").read_text().split() == [good, "assay-store.tgz"]
    assert out[-1].startswith("lane suite verdict PASS")
    assert json.loads((run_dir / "result.json").read_text()) == {"lane": "suite", "verdict": "PASS", "exit_code": 0}


def test_a_corrupted_fetched_archive_fails_the_checksum_only_for_collect(tmp_path):
    good = hashlib.sha256(PAYLOAD.encode()).hexdigest()
    ctx, out, rc, run_dir = collected(tmp_path, f"{good}  assay-store.tgz\n", payload="corrupted bytes\n")
    assert rc == rg.STORE_RC
    assert any(x.startswith("STORE-CHECKSUM-MISMATCH assay-store.tgz") for x in out)
    assert not (run_dir / "assay-store.tgz").exists() and (run_dir / "assay-store.tgz.rejected").exists()
    assert json.loads((run_dir / "result.json").read_text()) == {"lane": "suite", "verdict": "PASS", "exit_code": 0}  # the lane verdict is untouched
    assert out[-1].startswith("lane suite verdict PASS")


def test_a_failing_lane_keeps_its_own_exit_code_when_the_store_checksum_is_bad(tmp_path):
    good = hashlib.sha256(PAYLOAD.encode()).hexdigest()
    _, out, rc, run_dir = collected(tmp_path, f"{good}  assay-store.tgz\n", payload="bad\n", rc="1")
    assert rc == 1 and any("STORE-CHECKSUM-MISMATCH" in x for x in out)
    assert json.loads((run_dir / "result.json").read_text())["verdict"] == "FAIL"


def test_an_unreadable_checksum_file_is_an_error_line_not_a_pass(tmp_path):
    _, out, rc, run_dir = collected(tmp_path, "not a checksum\n")
    assert rc == rg.STORE_RC and any(x.startswith("STORE-FETCH-FAILED unreadable checksum file") for x in out)
    assert not (run_dir / "assay-store.tgz").exists()


def test_a_run_whose_job_packed_nothing_collects_no_store_and_surfaces_the_store_lines(tmp_path):
    log = "2026-11-24T10:00:00Z STORE-ABSENT x/y\n2026-11-24T10:00:01Z done rc=0\n"
    _, out, rc, run_dir = collected(tmp_path, "", job_log=log)
    assert rc == 0 and "STORE-ABSENT x/y" in out
    assert not (run_dir / "assay-store.tgz").exists() and not (run_dir / "assay-store.tgz.sha256").exists()


def test_a_run_started_without_collect_store_asks_the_host_for_no_store(tmp_path):
    ctx, _, rc, run_dir = collected(tmp_path, "x", with_store=False)
    assert rc == 0 and not any("assay-store" in " ".join(c[0]) for c in ctx.runner.calls)
    assert not (run_dir / "assay-store.tgz").exists()


def test_a_stale_local_store_file_never_survives_a_recollect(tmp_path):
    good = hashlib.sha256(PAYLOAD.encode()).hexdigest()
    ctx, out, rc, run_dir = collected(tmp_path, f"{good}  assay-store.tgz\n")
    assert rc == 0
    ctx.runner.handlers[:0] = [("assay-store.tgz.sha256", rg.Result(0, ""))]  # the host no longer holds a store
    host, meta, _ = rg.load_meta(ctx, "c1")
    assert rg.collect(ctx, host, meta, run_dir) == 0
    assert not (run_dir / "assay-store.tgz").exists() and not (run_dir / "assay-store.tgz.sha256").exists()


# ---- seed (RG-298 part 2): upload on the controller, unpack on the host before the lane starts ----

def make_seed(dest_dir: Path, rel: str, files: dict, n: int = 0) -> Path:
    """assay-seed-<n>.tgz + .sha256 in dest_dir, built like the controller does."""
    src = dest_dir / f"seed-src-{n}"
    for name, data in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(data)
    tgz = dest_dir / f"assay-seed-{n}.tgz"
    with open(tgz, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        rg.write_seed_archive(str(src), rel, gz)
    (dest_dir / f"assay-seed-{n}.tgz.sha256").write_text(f"{hashlib.sha256(tgz.read_bytes()).hexdigest()}  assay-seed-{n}.tgz\n")
    return tgz


def make_raw_seed(dest_dir: Path, members, n: int = 0) -> Path:
    """members: (name, kind, payload) with kind in file|dir|symlink; written verbatim, no controller-side filter."""
    tgz = dest_dir / f"assay-seed-{n}.tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        for name, kind, payload in members:
            ti = tarfile.TarInfo(name)
            if kind == "dir":
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(ti)
            elif kind == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, payload
                tf.addfile(ti)
            else:
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload.encode()))
    (dest_dir / f"assay-seed-{n}.tgz.sha256").write_text(f"{hashlib.sha256(tgz.read_bytes()).hexdigest()}  assay-seed-{n}.tgz\n")
    return tgz


def mk_ck(tmp_path):
    """A real git checkout with one tracked file (tracked/x); created once, so a test may add untracked state afterwards."""
    ck = tmp_path / "ck"
    if not (ck / ".git").exists():
        ck.mkdir(exist_ok=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        (ck / "tracked").mkdir()
        (ck / "tracked" / "x").write_text("x")
        for argv in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "c"]):
            subprocess.run(["git", "-C", str(ck), *argv], check=True, env=env, capture_output=True)
    return ck


def seed_harness(tmp_path, seeds, max_bytes=10 ** 9):
    """The job's seed_stores run alone: D holds the archives, CKDIR is a real git checkout with one tracked file."""
    d, ck = tmp_path / "D", mk_ck(tmp_path)
    d.mkdir(exist_ok=True)
    script = (f'set -u\nD={d}\nCKDIR={ck}\nSEEDS=({" ".join(seeds)})\nSTORE_MAX_BYTES={max_bytes}\nrc=0\nlog() {{ echo "$*"; }}\n'
              + rg.STORE_FUNCS + "\nseed_stores\n")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True), d, ck


def test_a_seed_replaces_the_target_directory_and_removes_the_archive(tmp_path):
    ck0 = mk_ck(tmp_path)
    (ck0 / "s" / "a").mkdir(parents=True)
    (ck0 / "s" / "a" / "stale").write_text("old")
    (tmp_path / "D").mkdir()
    make_seed(tmp_path / "D", "s/a", {"f.json": "new", "sub/g": "deep"})
    r, d, ck = seed_harness(tmp_path, ["s/a"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.relative_to(ck).as_posix() for p in (ck / "s").rglob("*")) == ["s/a", "s/a/f.json", "s/a/sub", "s/a/sub/g"]  # `stale` is gone
    assert (ck / "s" / "a" / "f.json").read_text() == "new" and "STORE-SEEDED s/a" in r.stdout
    assert not (d / "assay-seed-0.tgz").exists() and not (d / "assay-seed-0.tgz.sha256").exists()


def test_two_seeds_are_installed_from_their_own_archives(tmp_path):
    d = tmp_path / "D"
    d.mkdir()
    make_seed(d, "one", {"f": "1"}, n=0)
    make_seed(d, "two/x", {"f": "2"}, n=1)
    r, _, ck = seed_harness(tmp_path, ["one", "two/x"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert (ck / "one" / "f").read_text() == "1" and (ck / "two" / "x" / "f").read_text() == "2"


def _corrupt(d):
    (d / "assay-seed-0.tgz").write_bytes((d / "assay-seed-0.tgz").read_bytes() + b"x")


def _special(d):
    make_raw_seed(d, [("s", "dir", ""), ("s/l", "symlink", "/etc")])


def _outside(d):
    make_raw_seed(d, [("s", "dir", ""), ("other/f", "file", "x")])


def _dotdot(d):
    make_raw_seed(d, [("s", "dir", ""), ("s/../escape", "file", "x")])


def _missing(d):
    (d / "assay-seed-0.tgz").unlink()


@pytest.mark.parametrize("prep,rel,why,max_bytes", [
    (_corrupt, "s", "checksum", 10 ** 9),
    (_special, "s", "special-member", 10 ** 9),
    (_outside, "s", "member-outside-path", 10 ** 9),
    (_dotdot, "s", "member-outside-path", 10 ** 9),
    (_missing, "s", "missing-archive", 10 ** 9),
    (lambda d: None, "s", "too-large", 100),
    (lambda d: make_seed(d, "tracked", {"f": "1"}), "tracked", "tracked-files", 10 ** 9),
    (lambda d: None, "../s", "invalid-path", 10 ** 9),
    (lambda d: None, "/abs", "invalid-path", 10 ** 9),
])
def test_a_refused_seed_exits_78_and_leaves_the_checkout_untouched(tmp_path, prep, rel, why, max_bytes):
    d = tmp_path / "D"
    d.mkdir()
    make_seed(d, "s", {"f": "1"})
    prep(d)
    r, _, ck = seed_harness(tmp_path, [rel], max_bytes)
    assert r.returncode == 78, r.stdout + r.stderr
    assert f"STORE-SEED-FAILED {rel} {why}" in r.stdout
    assert not (ck / "s").exists() and (ck / "tracked" / "x").read_text() == "x"


def test_a_symlinked_component_refuses_the_seed(tmp_path):
    mk_ck(tmp_path)
    (tmp_path / "real").mkdir()
    (tmp_path / "ck" / "lnk").symlink_to(tmp_path / "real")
    d = tmp_path / "D"
    d.mkdir()
    make_seed(d, "lnk/s", {"f": "1"})
    r, _, _ = seed_harness(tmp_path, ["lnk/s"])
    assert r.returncode == 78 and "STORE-SEED-FAILED lnk/s symlink" in r.stdout and not list((tmp_path / "real").iterdir())


def test_the_seed_is_in_place_when_the_lane_starts(sandbox):
    d = rgd(sandbox)
    make_seed(d, STORE, {"f.json": "seeded"})
    out = sandbox.tmp / "seen"
    r = run_job(sandbox, [], f"cat {STORE}/f.json > {out}", seeds=[STORE])
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.read_text() == "seeded" and "STORE-SEEDED " + STORE in r.stdout
    lines = r.stdout.splitlines()
    assert next(i for i, x in enumerate(lines) if "STORE-SEEDED" in x) < next(i for i, x in enumerate(lines) if "lane exited" in x)


def test_a_failed_seed_ends_the_job_as_a_transport_error_before_the_lane_starts(sandbox):
    d = rgd(sandbox)
    make_seed(d, STORE, {"f.json": "x"})
    _corrupt(d)
    r = run_job(sandbox, [], "echo lane-ran > " + str(sandbox.tmp / "ran"), seeds=[STORE])
    assert r.returncode == rg.SEED_RC == 78 and (d / "rc").read_text().strip() == "78"
    assert f"STORE-SEED-FAILED {STORE} checksum" in r.stdout
    assert not (sandbox.tmp / "ran").exists() and not (d / "lane.log").exists()
    assert not list((sandbox.home / "checkouts" / "vbpub" / ".worktrees").glob("rg-*"))  # the checkout is cleaned on the way out


def test_collect_names_a_failed_seed_and_prints_its_reason(tmp_path):
    log = f"2026-11-24T10:00:00Z STORE-SEED-FAILED {STORE} checksum\n2026-11-24T10:00:01Z done rc=78\n"
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "c2")) == 0
    ctx.runner.handlers[:0] = [("jobs/rg-c2/job.log", rg.Result(0, log)), ("/EXIT", rg.Result(0, "78\n")), ("/rc", rg.Result(0, "78\n"))]
    host, meta, run_dir = rg.load_meta(ctx, "c2")
    assert rg.collect(ctx, host, meta, run_dir) == 78
    assert out[-1].startswith(f"lane suite verdict STORE_SEED_FAILED; exit_code 78; reason STORE-SEED-FAILED {STORE} checksum")


# ---- seed, controller side ----

class Uploading(Fake):
    """Keeps the bytes of every file piped to `cat > <host path>` and answers sha256sum for the uploaded archive."""

    def __init__(self, handlers):
        super().__init__(handlers)
        self.up = {}

    def run(self, argv, stdin_path=None, timeout=300):
        m = re.search(r"cat > \S+/(assay-seed-\d+\.tgz(?:\.sha256)?)", " ".join(argv))
        if m and stdin_path:
            self.up[m.group(1)] = Path(stdin_path).read_bytes()
        return super().run(argv, stdin_path, timeout)


def seeded_ctx(tmp_path, tamper=False):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    ctx.runner = Uploading(ctx.runner.handlers)

    def sha(argv):
        m = re.search(r"(assay-seed-\d+\.tgz)", " ".join(argv))
        if not m:
            return rg.Result(0, hashlib.sha256(b"bundle").hexdigest() + "  in.bundle\n")
        return rg.Result(0, ("0" * 64 if tamper else hashlib.sha256(ctx.runner.up[m.group(1)]).hexdigest()) + "  x\n")

    ctx.runner.handlers.insert(0, ("sha256sum", sha))
    return ctx, out


def seed_src(tmp_path):
    src = tmp_path / "seedsrc"
    (src / "sub").mkdir(parents=True)
    (src / "a.json").write_text("one")
    (src / "sub" / "b.json").write_text("two")
    return src


def test_run_uploads_each_seed_with_a_verified_checksum_and_names_it_in_the_job(tmp_path):
    src = seed_src(tmp_path)
    ctx, _ = seeded_ctx(tmp_path)
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "u1", "--seed-store", f"{src}=x/store")) == 0
    up = ctx.runner.up
    assert sorted(up) == ["assay-seed-0.tgz", "assay-seed-0.tgz.sha256"]
    assert up["assay-seed-0.tgz.sha256"].decode().split() == [hashlib.sha256(up["assay-seed-0.tgz"]).hexdigest(), "assay-seed-0.tgz"]
    with tarfile.open(fileobj=io.BytesIO(up["assay-seed-0.tgz"])) as t:
        assert sorted((m.name, m.size) for m in t.getmembers()) == [("x/store", 0), ("x/store/a.json", 3), ("x/store/sub", 0), ("x/store/sub/b.json", 3)]
    assert not list((tmp_path / "out" / "u1").glob("assay-seed*"))  # nothing of the seed is left in the run dir
    assert "SEEDS=(x/store)\n" in (tmp_path / "out" / "u1" / "job.sh").read_text()
    assert json.loads((tmp_path / "out" / "u1" / "meta.json").read_text())["seed_store"] == ["x/store"]
    flat = [" ".join(c[0]) for c in ctx.runner.calls]
    assert max(i for i, c in enumerate(flat) if "assay-seed-0.tgz.sha256" in c or "assay-seed-0.tgz" in c) < next(i for i, c in enumerate(flat) if "run-job" in c)


def test_a_seed_whose_upload_checksum_differs_aborts_with_a_transport_error(tmp_path):
    ctx, _ = seeded_ctx(tmp_path, tamper=True)
    with pytest.raises(rg.GateError, match="seed checksum mismatch") as e:
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "u2", "--seed-store", f"{seed_src(tmp_path)}=x/store"))
    assert e.value.code == 3
    assert not any("run-job" in " ".join(c[0]) for c in ctx.runner.calls)


@pytest.mark.parametrize("raw", ["MISSING=x/y", "SRC", "SRC=/abs", "SRC=../x", "SRC=a=b", "=x/y"])
def test_run_refuses_a_bad_seed_before_a_host_is_contacted(tmp_path, raw):
    src = seed_src(tmp_path)
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError) as e:
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "u3", "--seed-store", raw.replace("SRC", str(src))))
    assert e.value.code == 2
    assert not any(c[0][0] in ("ssh", "apicheck") for c in ctx.runner.calls) and not (tmp_path / "out" / "u3").exists()


def test_a_seed_directory_with_a_symlink_is_refused_locally(tmp_path):
    src = seed_src(tmp_path)
    (src / "evil").symlink_to("/etc")
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="not a regular file or directory") as e:
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "u4", "--seed-store", f"{src}=x/s"))
    assert e.value.code == 2 and not any(c[0][0] == "ssh" for c in ctx.runner.calls)


def test_a_seed_above_the_bound_is_refused_locally_and_the_same_target_twice_too(tmp_path):
    src = seed_src(tmp_path)
    (src / "big.bin").write_bytes(b"\0" * 1_200_000)
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="exceed --store-max-mib 1") as e:
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "u5", "--store-max-mib", "1", "--seed-store", f"{src}=x/s"))
    assert e.value.code == 2
    with pytest.raises(rg.GateError, match="same target path twice"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "u6", "--seed-store", f"{seed_src(tmp_path)}=x/s", "--seed-store", f"{seed_src(tmp_path)}=x//s/"))
