from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import fixture_root
from groop.collect.procs import list_processes

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


def test_process_drilldown_reads_proc_fixture(tmp_path: Path) -> None:
    cgroot = tmp_path / "cg"
    entity = cgroot / "x.slice"
    entity.mkdir(parents=True)
    (entity / "cgroup.procs").write_text("123\n")
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("python\n")
    (proc / "cmdline").write_text("python\0app.py\0")
    (proc / "status").write_text("Name:\tpython\nVmRSS:\t42 kB\nVmSwap:\t7 kB\n")
    assert list_processes(cgroot, "x.slice", tmp_path / "proc") == [{"pid": 123, "comm": "python", "cmdline": "python app.py", "rss": 43008, "swap": 7168}]


def test_cli_once_json_works_without_textual_import() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    proc = subprocess.run([sys.executable, "-m", "groop.cli", "--once", "--json", "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch")], check=True, cwd=fixture_root().parents[1], env=env, text=True, stdout=subprocess.PIPE)
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    assert GAME_KEY in payload["entities"]
