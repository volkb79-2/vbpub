"""Shared isolation for the transaction survivor tests' Git subprocesses."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_GIT_PATH_KEYS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_TEMPLATE_DIR", "GIT_TRACE", "GIT_TRACE_PACK_ACCESS", "GIT_TRACE_PACKET",
    "GIT_TRACE_PERFORMANCE", "GIT_TRACE_SETUP", "GIT_TRACE_CURL",
    "GIT_TRACE_CURL_NO_DATA", "GIT_TRACE_SHALLOW", "GIT_TRACE2",
    "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF", "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS", "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND",
    "GIT_PROXY_COMMAND", "GIT_ASKPASS", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR",
    "GIT_PAGER", "GIT_EXTERNAL_DIFF",
)


def isolate_git(monkeypatch, tmp_path: Path) -> Path:
    """Clear inherited Git config and helper overrides before setting test policy.

    Removing ``GIT_CONFIG_PARAMETERS`` prevents command-scope ``-c`` pairs from
    overriding the isolated config. Removing ``GIT_EXEC_PATH`` restores Git's
    compiled-in helper lookup; PATH remains intact for Git and its normal
    system-program lookup. SSH, proxy, askpass, editor, pager, and diff helper
    overrides select external programs, so those host inputs are cleared too.
    """
    isolation = tmp_path / "git-isolation"
    isolation.mkdir(exist_ok=True)
    config = isolation / "gitconfig"
    config.write_text("", encoding="utf-8")
    hooks = isolation / "hooks"
    hooks.mkdir(exist_ok=True)
    template = isolation / "template"
    template.mkdir(exist_ok=True)
    for key in _GIT_PATH_KEYS:
        monkeypatch.delenv(key, raising=False)
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
    for index in range(count):
        monkeypatch.delenv(f"GIT_CONFIG_KEY_{index}", raising=False)
        monkeypatch.delenv(f"GIT_CONFIG_VALUE_{index}", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    for key, value in {
        "GIT_AUTHOR_NAME": "cmru-test",
        "GIT_AUTHOR_EMAIL": "cmru-test@example.invalid",
        "GIT_COMMITTER_NAME": "cmru-test",
        "GIT_COMMITTER_EMAIL": "cmru-test@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00 +0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00 +0000",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_PROTOCOL_FROM_USER": "0",
        "LC_ALL": "C",
        "LANGUAGE": "",
        "TZ": "UTC",
        "GIT_DEFAULT_HASH": "sha1",
    }.items():
        monkeypatch.setenv(key, value)
    config_values = (
        ("core.hooksPath", str(hooks)), ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"), ("push.gpgSign", "false"),
        ("init.defaultBranch", "main"), ("maintenance.auto", "false"),
        ("maintenance.autoDetach", "false"), ("gc.auto", "0"),
        ("gc.autoDetach", "false"),
    )
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(config_values)))
    for index, (key, value) in enumerate(config_values):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return hooks


@pytest.fixture
def git_isolation(monkeypatch, tmp_path):
    return isolate_git(monkeypatch, tmp_path)
