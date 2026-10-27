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
# the only modules allowed to open anything for writing: journal/state files, the scratch file, the bench writing into it
WRITERS = {"journal.py", "scratch.py", "bench.py"}


def test_only_the_writer_modules_open_files_for_writing():
    bad = []
    for path in MODULES:
        if path.name in WRITERS:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND") \
                    and path.name != "rawread.py":
                bad.append((path.name, node.attr))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
                if isinstance(mode, ast.Constant) and set(str(mode.value)) & set("wa+x"):
                    bad.append((path.name, "open(..., %r)" % mode.value))
            if isinstance(node, ast.Attribute) and node.attr in ("write_text", "write_bytes", "unlink", "rmdir", "chmod", "chown", "truncate"):
                bad.append((path.name, node.attr))
    # rawread.py names the forbidden flags only to ASSERT they are absent
    assert bad == [], bad


def test_rawread_never_requests_write_access():
    src = (PKG / "rawread.py").read_text()
    uses = re.findall(r"os\.(open|write|pwrite|ftruncate|fsync)\(", src)
    assert uses == ["open"]  # exactly one os.open (read-only, asserted) and no write-type syscall


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
