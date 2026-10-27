"""Static guarantees: the package never writes outside its journal/state/scratch, never touches caps, unit templates are sound,
missing optional test dependencies fail by default."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

import _testdeps

PKG = Path(__file__).resolve().parents[1]
MODULES = sorted(p for p in PKG.glob("*.py"))
DANGER_OS = {"open", "write", "pwrite", "ftruncate", "truncate", "unlink", "remove", "rmdir", "mkdir", "makedirs", "chmod", "chown",
             "utime", "replace", "rename", "link", "symlink", "fdopen", "fsync", "fdatasync", "system", "posix_fadvise", "fallocate",
             "posix_fallocate", "pwritev", "writev", "popen", "spawnl", "execv", "fork"}

# EVERY call site that can modify the file system, by (module, enclosing function, call). Nothing else may appear, and nothing here
# is exempt wholesale: a new call site fails this test until a reviewer adds it on purpose.
ALLOWED_CALLS = {
    ("bench.py", "_pwrite_all", "os.pwrite"),            # the scratch file only
    ("bench.py", "timed_flush", "os.fdatasync"),         # scratch file; the ONLY fdatasync call site (every flush is under the watchdog)
    ("journal.py", "append", "os.write"),                # the journal record
    ("journal.py", "prune", "os.unlink"),                # retention: monthly journal files
    ("journal.py", "read_records", "os.fdopen"),         # read mode (checked below)
    ("rawread.py", "_open_ro", "os.open"),               # O_RDONLY only (asserted)
    ("rawread.py", "device_size_and_block", "fcntl.ioctl"),  # BLKGETSIZE64 / BLKSSZGET only (checked below)
    ("runner.py", "_read_resets", "os.fdopen"),          # read mode
    ("runner.py", "annotate", "os.write"),               # resets.log line
    ("safeio.py", "acquire", "fcntl.flock"),
    ("safeio.py", "release", "fcntl.flock"),
    ("safeio.py", "ensure_dir", "os.makedirs"),          # state/journal/scratch directory creation
    ("safeio.py", "ensure_dir", "os.mkdir"),
    ("safeio.py", "load_json", "os.fdopen"),             # read mode
    ("safeio.py", "open_file", "os.open"),               # O_NOFOLLOW + verified
    ("safeio.py", "save_json", "os.fsync"),
    ("safeio.py", "save_json", "os.replace"),            # state/aggregate atomic replace
    ("safeio.py", "save_json", "os.write"),
    ("safeio.py", "unlink_if_same", "os.unlink"),        # removal only if the name still is our inode
    ("scratch.py", "__enter__", "os.unlink"),            # the O_DIRECT-refused retry removes OUR random name
    ("scratch.py", "_cleanup", "os.unlink"),             # our own scratch file
    ("scratch.py", "_remove_stale_files", "os.unlink"),  # strict-regex regular files in the scratch dir
}
ALLOWED_PLAIN_OPEN = {("collectors.py", "_read"), ("collectors.py", "_scan"), ("covariates.py", "provider_signal"),
                      ("runner.py", "disk_bytes"), ("scratch.py", "fstype_of")}  # builtin open(), read mode only


def call_sites():
    out = []
    for path in MODULES:
        tree = ast.parse(path.read_text())
        parents = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[ch] = node

        def func_of(n):
            while n in parents:
                n = parents[n]
                if isinstance(n, ast.FunctionDef):
                    return n.name
            return "<module>"

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = None
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                base = f.value.id
                if base == "os" and f.attr in DANGER_OS:
                    name = f"os.{f.attr}"
                elif base in ("shutil", "subprocess", "fcntl", "ctypes", "signal") and f.attr in (
                        "ioctl", "flock", "fcntl", "lockf", "copy", "copyfile", "move", "rmtree", "run", "Popen", "call", "kill", "pthread_kill"):
                    name = f"{base}.{f.attr}"
            if isinstance(f, ast.Attribute) and f.attr in ("write_text", "write_bytes", "touch", "unlink", "rename", "mkdir",
                                                           "rmdir", "chmod", "symlink_to", "hardlink_to", "truncate"):
                if not (isinstance(f.value, ast.Name) and f.value.id == "os"):
                    name = f".{f.attr}"
            if isinstance(f, ast.Attribute) and f.attr == "open":
                v = f.value
                is_path = (isinstance(v, ast.Call) and getattr(v.func, "id", "") == "Path") or (isinstance(v, ast.Name) and v.id not in ("os", "self"))
                if is_path:
                    name = ".open"
            if isinstance(f, ast.Name) and f.id == "open":
                name = "open"
            if name:
                out.append((path.name, func_of(node), name, node))
    return out


def test_every_fs_modifying_call_site_is_individually_allow_listed():
    unexpected = []
    for module, func, name, node in call_sites():
        if name == "open":
            ok = (module, func) in ALLOWED_PLAIN_OPEN
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            ok = ok and (mode is None or (isinstance(mode, ast.Constant) and not set(str(mode.value)) & set("wax+")))
        elif name == ".open":
            ok = (module, func) == ("config.py", "load") and isinstance(node.args[0].value, str) and node.args[0].value == "rb"
        else:
            ok = (module, func, name) in ALLOWED_CALLS
        if not ok:
            unexpected.append((module, func, name))
    assert unexpected == [], unexpected


def test_fdopen_is_read_only_and_ioctl_is_only_the_two_size_queries():
    for module, func, name, node in call_sites():
        if name == "os.fdopen":
            mode = node.args[1] if len(node.args) > 1 else None
            assert isinstance(mode, ast.Constant) and mode.value == "r", (module, func)
        if name == "fcntl.ioctl":
            assert module == "rawread.py" and isinstance(node.args[1], ast.Name) and node.args[1].id in ("BLKGETSIZE64", "BLKSSZGET")
    consts = {n.targets[0].id: n.value.value for n in ast.parse((PKG / "rawread.py").read_text()).body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
    assert consts["BLKGETSIZE64"] == 0x80081272 and consts["BLKSSZGET"] == 0x1268  # not BLKDISCARD (0x1277) or any other request


def test_no_fadvise_and_no_shell_out_anywhere():
    for path in MODULES:
        src = path.read_text()
        assert "posix_fadvise" not in src and "DONTNEED" not in src.replace("DONTNEED)", "", 0) or path.name == "test_static.py"
        assert "subprocess" not in src and "os.system" not in src and "shutil" not in src, path.name


def test_rawread_has_a_single_read_only_open():
    src = (PKG / "rawread.py").read_text()
    assert re.findall(r"os\.(open|write|pwrite|ftruncate|fsync)\(", src) == ["open"]


def test_no_module_writes_cgroup_sysfs_or_proc_control_files():
    for path in MODULES:
        src = path.read_text()
        assert not re.search(r"""open\([^)]*(io\.max|io\.cost|io\.weight|nr_requests|max_sectors_kb|scheduler)[^)]*["']\s*,\s*["']w""", src), path.name
        assert "subprocess" not in src and "os.system" not in src, path.name  # nothing is ever executed


def test_only_stdlib_is_imported():
    import sys
    stdlib = set(sys.stdlib_module_names)
    for path in MODULES:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            assert all(n in stdlib for n in names), (path.name, names)


UNIT_DIR = PKG / "units"
TOKEN = re.compile(r"@(IOGOV_[A-Z_]+)@")


def test_unit_templates_are_templates_with_documented_tokens_and_hardening():
    readme = (PKG / "README.md").read_text()
    units = sorted(UNIT_DIR.glob("*.in"))
    assert len(units) >= 7
    for u in units:
        text = u.read_text()
        assert "TEMPLATE, NOT INSTALLED" in text.splitlines()[0], u.name
        for tok in TOKEN.findall(text):
            assert f"@{tok}@" in readme, (u.name, tok)
    for u in UNIT_DIR.glob("iogov-*.service.in"):
        text = u.read_text()
        for must in ("NoNewPrivileges=yes", "ProtectSystem=strict", "DevicePolicy=closed", "KillSignal=SIGTERM", "Slice=iogov-probe.slice"):
            assert must in text, (u.name, must)
        assert "DeviceAllow=" not in text or re.search(r"DeviceAllow=@IOGOV_DISK_NODE@ r\n", text)  # read-only device access, never rw
        assert not re.search(r"DeviceAllow=\S+ (rw|w)", text)
        assert "User=root" not in text and "AmbientCapabilities" not in text and "CAP_SYS_RAWIO" not in text.replace("no CAP_SYS_RAWIO", "")
    assert not list(UNIT_DIR.glob("*.service")) and not list(UNIT_DIR.glob("*.timer"))  # templates only: nothing renders itself into place


def test_timers_reference_existing_services():
    for t in UNIT_DIR.glob("*.timer.in"):
        target = re.search(r"^Unit=(\S+)$", t.read_text(), re.M).group(1)
        assert (UNIT_DIR / (target + ".in")).exists()


def test_udev_template_grants_read_only_acl_on_one_identity():
    text = (UNIT_DIR / "99-iogov-raw-read.rules.in").read_text()
    assert ":r " in text and ":rw" not in text and "ID_PATH" in text and "GROUP" not in text


# --- the optional-dependency policy -------------------------------------------------------------------------


def test_missing_optional_dependencies_fail_by_default_and_skip_only_on_opt_in(monkeypatch):
    monkeypatch.delenv(_testdeps.ALLOW_VAR, raising=False)
    with pytest.raises(pytest.fail.Exception, match="FAIL by default"):
        _testdeps.optional_module("definitely_not_installed_xyz", "needed")
    with pytest.raises(pytest.fail.Exception):
        _testdeps.require_binary("definitely-not-a-binary-xyz", "needed")
    monkeypatch.setenv(_testdeps.ALLOW_VAR, "1")
    with pytest.raises(pytest.skip.Exception):
        _testdeps.optional_module("definitely_not_installed_xyz", "needed")


def test_tests_never_use_bare_importorskip_or_skipif():
    for path in (PKG / "tests").glob("test_*.py"):
        src = path.read_text()
        src = src.replace("importorskip", "IMPORTORSKIP", 0)
        body = "\n".join(l for l in src.splitlines() if "assert" not in l and "re.compile" not in l and "'importorskip'" not in l and '"importorskip"' not in l)
        assert "pytest.importorskip" not in body or path.name == "test_static.py", path.name
        assert "skipif" not in body or path.name == "test_static.py", path.name
        assert "pytest.mark.skip" not in body or path.name == "test_static.py", path.name
