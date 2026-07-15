"""Shared fixtures. FROZEN — implementation agents add local fixtures in
their own test files, never here."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from handoffctl import paths
from handoffctl.config import ProjectConfig, register_project


@pytest.fixture()
def tmp_state(tmp_path, monkeypatch) -> Path:
    """Isolated XDG state root for every test."""
    root = tmp_path / "xdg-state"
    monkeypatch.setenv("HANDOFFCTL_STATE", str(root))
    paths.ensure_layout()
    return root


SAMPLE_PROJECT_TOML = """\
[project]
id = "demo"
default_branch = "main"
worktree_root = ".worktrees"
handoff_globs = ["handoff/*.md"]
infra_globs = ["infra/**"]

[gates.pytest-q]
argv = ["true"]
phase = "implementation"
timeout_seconds = 60
environment = "local"

[mutexes.stack]
scope = "project"
capacity = 1

[policy]
max_active_tasks = 2
ready_queue_target = 3

[notify]
"""

SAMPLE_ROUTES_TOML = """\
revision = "test-rev"

[tiers.flash-high]
routes = ["fake-cli"]

[routes.fake-cli]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
"""

SAMPLE_HANDOFF = """\
---
schema_version: 1
id: demo-P01-sample
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "0000000"
source: {kind: roadmap, ref: docs/ROADMAP.md}
scope:
  touch: ["src/demo/thing.py", "tests/test_thing.py"]
  forbid: ["src/demo/core.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Contract body. If a named contract cannot be met as specified, STOP, write
`BLOCKED: <reason>` to the LOG, commit, exit.
"""


@pytest.fixture()
def sample_project(tmp_state, tmp_path) -> ProjectConfig:
    """A registered git repo with project.toml, one valid handoff, routes."""
    root = tmp_path / "demo-repo"
    (root / ".handoffctl").mkdir(parents=True)
    (root / "handoff").mkdir()
    (root / "docs").mkdir()
    (root / ".handoffctl" / "project.toml").write_text(SAMPLE_PROJECT_TOML)
    (root / "handoff" / "demo-P01-sample.md").write_text(SAMPLE_HANDOFF)
    (root / "docs" / "DECISIONS-INBOX.md").write_text(
        "# Decisions inbox\n\n---\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=root, check=True)
    paths.routes_path().write_text(SAMPLE_ROUTES_TOML)
    register_project("demo", root)
    paths.ensure_layout("demo")
    return ProjectConfig.load(root)


@pytest.fixture()
def handoff_text() -> str:
    return SAMPLE_HANDOFF


def make_handoff(**overrides) -> str:
    """Return SAMPLE_HANDOFF with frontmatter keys textually overridden.

    Simple line-level override for top-level scalar keys; for structured
    edits, tests should build their own YAML.
    """
    text = SAMPLE_HANDOFF
    for key, value in overrides.items():
        out, done = [], False
        for line in text.splitlines():
            if not done and line.startswith(f"{key}:"):
                out.append(f"{key}: {value}")
                done = True
            else:
                out.append(line)
        text = "\n".join(out) + "\n"
    return text
