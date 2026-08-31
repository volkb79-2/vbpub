"""CIU-71: a stack's relative ``build.context`` must resolve against the
repo root, not the compose file's own directory.

Filed by dstdns-P147b (see ``KNOWN_ISSUES_TODO_BACKLOG.md`` ``## CIU-71``):
a stack's own Dockerfile ``COPY``s a repo-root-relative path (e.g.
``COPY tests/fixtures/mock_data ...``), which only resolves correctly if
``docker compose`` treats a relative ``build.context`` as repo-root-relative.
It does not, by default -- Compose resolves a relative ``context:`` against
the COMPOSE FILE's own directory unless invoked with
``--project-directory <repo-root>``. Live repro:
``resolve : lstat .../infra/mock-targets/tests: no such file or directory``.

This test does NOT invoke a real ``docker``/``docker compose`` binary: the
ciu gate runs inside ``tester-unified``, which has no Docker socket at all
(``tester-unified/Dockerfile``: "no systemd and no Docker socket -- only
this closure"), and the rest of this suite is deliberately Docker-free
(``tests/tests/README.md``). Instead it calls the REAL
``engine.execute_docker_compose_with_logs`` -- the exact function CIU-71
fixes, with the exact ``cwd``/``project``/``repo_root`` shape
``main_execution`` passes it -- against a REAL directory tree shaped exactly
like the dstdns repro (only ``subprocess.Popen`` is stubbed, to CAPTURE the
final argv rather than run it), then applies Compose's own documented
``--project-directory`` resolution rule to the captured argv and asserts the
Dockerfile's COPY source is actually reachable from the resolved build
context -- exactly the fact whose absence produced the live repro's
``lstat`` error.

**Controlled-wrong-implementation** (manually verified while authoring this
fix -- see ``nyxloom-trove/reports/ciu-P37-REPORT.md``): reverting CIU-71's
fix (dropping ``--project-directory`` from
``execute_docker_compose_with_logs``) makes this test fail --
``resolved_context`` falls back to the compose file's own directory (the
stack dir), and the Dockerfile's COPY source is not reachable from there,
which is exactly the failure class the live dstdns repro hit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import engine  # noqa: E402


def _resolved_build_context(cmd: list[str], cwd: Path) -> Path:
    """Docker compose's own ``--project-directory`` resolution rule
    (verified manually against a real ``docker compose config`` -- see
    ciu-P37-REPORT.md), applied to a CAPTURED argv: a relative path in the
    compose file (a ``build.context`` chief among them) resolves against
    ``--project-directory`` when it is passed; otherwise it resolves against
    the directory ``docker compose`` itself runs in (``cwd``) -- ciu always
    passes ``-f`` compose-file arguments as bare relative filenames, so that
    directory IS the compose file's own directory.
    """
    if "--project-directory" in cmd:
        idx = cmd.index("--project-directory")
        project_directory = Path(cmd[idx + 1])
    else:
        project_directory = Path(cwd)
    return (project_directory / ".").resolve()


def _fake_popen(monkeypatch, captured: dict):
    class FakeProc:
        returncode = 0
        stdout = iter(())

        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(engine.subprocess, "Popen", fake_popen)


def test_relative_build_context_resolves_against_repo_root_not_stack_dir(
    tmp_path, monkeypatch
):
    # ---- Fixture: the exact shape of the dstdns-P147b live repro ----
    repo_root = tmp_path / "repo"
    copy_source = repo_root / "tests" / "fixtures" / "mock_data"
    copy_source.parent.mkdir(parents=True)
    copy_source.write_text("fixture payload\n", encoding="utf-8")

    stack_dir = repo_root / "infra" / "mock-targets"
    stack_dir.mkdir(parents=True)
    (stack_dir / "Dockerfile").write_text(
        "FROM scratch\nCOPY tests/fixtures/mock_data /data/mock_data\n",
        encoding="utf-8",
    )
    # Already "rendered" (this is what a ciu.compose.yml.j2 -> ciu.compose.yml
    # render of `build_context = "."` produces verbatim -- rendering is not
    # what CIU-71 is about, only the invocation that follows it).
    (stack_dir / "ciu.compose.yml").write_text(
        "services:\n"
        "  mock_targets:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n",
        encoding="utf-8",
    )

    # ---- Call the REAL function CIU-71 fixes, exactly as main_execution
    # (and run_shipped) call it: cwd=<stack dir>, repo_root=<repo root>. ----
    captured: dict = {}
    _fake_popen(monkeypatch, captured)

    result = engine.execute_docker_compose_with_logs(
        ["-f", "ciu.compose.yml"],
        cwd=stack_dir,
        project="dstdns-abc123-mock-targets",
        repo_root=repo_root,
    )

    assert result["status"] == "success"
    assert "cmd" in captured, "expected a docker compose invocation"

    # ---- The actual CIU-71 assertion ----
    resolved_context = _resolved_build_context(captured["cmd"], captured["cwd"])
    assert resolved_context == repo_root.resolve(), (
        f"build.context resolved to {resolved_context}, not the repo root "
        f"{repo_root.resolve()} -- CIU-71 regressed"
    )
    dockerfile_copy_source = resolved_context / "tests" / "fixtures" / "mock_data"
    assert dockerfile_copy_source.is_file(), (
        "the Dockerfile's COPY source is not reachable from the resolved "
        f"build context ({resolved_context}) -- this is the exact failure "
        "class the live dstdns repro hit: "
        f"'resolve : lstat {stack_dir / 'tests'}: no such file or directory'"
    )


def test_shipped_path_also_resolves_against_repo_root(tmp_path, monkeypatch):
    """CIU-71 covers BOTH real invocation sites (S8.1's native `up` AND
    S8.5's `--shipped` passthrough share execute_docker_compose_with_logs) --
    a maintainer's own pre-shipped compose file gets the same fix."""
    repo_root = tmp_path / "repo"
    copy_source = repo_root / "vendor" / "data" / "seed.sql"
    copy_source.parent.mkdir(parents=True)
    copy_source.write_text("-- seed\n", encoding="utf-8")

    stack_dir = repo_root / "services" / "seeder"
    stack_dir.mkdir(parents=True)
    (stack_dir / "Dockerfile").write_text(
        "FROM scratch\nCOPY vendor/data/seed.sql /seed.sql\n", encoding="utf-8"
    )
    (stack_dir / "docker-compose.yml").write_text(
        "services:\n"
        "  seeder:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n",
        encoding="utf-8",
    )

    captured: dict = {}
    _fake_popen(monkeypatch, captured)

    result = engine.execute_docker_compose_with_logs(
        ["-f", "docker-compose.yml"],
        cwd=stack_dir,
        project="dstdns-abc123-seeder",
        repo_root=repo_root,
    )

    assert result["status"] == "success"
    resolved_context = _resolved_build_context(captured["cmd"], captured["cwd"])
    assert resolved_context == repo_root.resolve()
    assert (resolved_context / "vendor" / "data" / "seed.sql").is_file()
