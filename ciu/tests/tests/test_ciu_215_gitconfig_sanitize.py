"""CIU-215 -- the sanitizer port is held to Run-Gate's behaviour.

Run-Gate is loaded by PATH in this test only; ciu never imports it at runtime.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import gitconfig_sanitize as port  # noqa: E402

RUN_GATE = Path(__file__).resolve().parents[3] / "run-gate-project" / "run_gate.py"

CONFIG = """\
[core]
\trepositoryformatversion = 0
\tsshCommand = ssh -i /key
\taskpass = /bin/pw
[remote "origin"]
\turl = https://tok:secret@example.invalid/r.git
\tpushurl = ssh://git@example.invalid/r.git
\tfetch = +refs/heads/*:refs/heads/*
[remote "mirror"]
\turl = ssh://user:pw@example.invalid/r.git
[http "https://example.invalid/"]
\textraheader = Authorization: bearer abc
[credential]
\thelper = store
[sendemail]
\tsmtppass = hunter2
[url "https://tok@example.invalid/"]
\tinsteadOf = gh:
[url "https://example.invalid/"]
\tinsteadOf = plain:
[user]
\tapiToken = zzz
\tname = Test "Q" User
\tflag
[include]
\tpath = /host/file
"""


@pytest.fixture(scope="module")
def run_gate():
    if not RUN_GATE.is_file():
        pytest.fail(f"run-gate reference implementation is missing: {RUN_GATE}")
    spec = importlib.util.spec_from_file_location("_run_gate_parity", RUN_GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sanitized_text_matches_run_gate_on_the_same_file(run_gate, tmp_path):
    path = tmp_path / "config"
    path.write_text(CONFIG, encoding="utf-8")
    ours = port.sanitized_git_config_text(path)
    assert ours == run_gate.sanitized_git_config_text(path)
    for secret in ("secret", "tok", "hunter2", "bearer", "/key", "zzz", "store", "/host/file"):
        assert secret not in ours
    assert "ssh://git@example.invalid/r.git" in ours and "plain:" in ours


@pytest.mark.parametrize("text", [
    "https://t@h/x", "git+https://t@h/x", "ssh://git@h/x", "ssh://u:p@h/x",
    "no url here", "url.https://tok@h/.insteadof",
])
def test_strip_url_userinfo_matches_run_gate(run_gate, text):
    assert port.strip_url_userinfo(text) == run_gate.strip_url_userinfo(text)


def test_entries_and_rendering_match_run_gate(run_gate):
    entries = [("a.b", "v"), ("a.b", None), ("sec.sub.name", 'q"\\\n\t'), ("x.y", None)]
    assert port.sanitize_git_config_entries(entries) == run_gate.sanitize_git_config_entries(entries)
    assert port.render_git_config(entries) == run_gate.render_git_config(entries)


def test_nested_walk_and_cap_match_run_gate(run_gate, tmp_path):
    assert port.MAX_GIT_OVERLAYS == run_gate._MAX_GIT_OVERLAYS
    modules = tmp_path / "modules"
    for rel in ("libs/a", "libs/a/modules/b", "libs/a/objects"):
        (modules / rel).mkdir(parents=True)
    for rel in ("libs/a", "libs/a/modules/b"):
        (modules / rel / "HEAD").write_text("ref: x\n")
        (modules / rel / "config").write_text("[a]\n")
    (modules / "libs/a/config.worktree").write_text("[a]\n")
    (modules / "libs/a/objects/config").write_text("[skipped]\n")
    assert port.nested_git_config_files(modules) == run_gate._nested_git_config_files(modules)
    assert len(port.nested_git_config_files(modules)) == 3


def test_a_value_less_key_and_blank_items_round_trip(tmp_path):
    path = tmp_path / "config"
    path.write_text("[user]\n\tflag\n", encoding="utf-8")
    assert port.read_git_config_entries(path) == [("user.flag", None)]
