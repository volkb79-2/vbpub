"""`ciu init --hooks NAME1,NAME2` — hook template library mechanism (ciu-P20,
CIU-QOL-13, docs/SPEC.md S19.1).

Oracles exercised here:
- O2/O3: the shipped `post_compose_db.py` satisfies the S9.1/S9.4/S9.5
  contract for real, run through the actual `hooks_runner.run_hooks` (not
  just "the file exists"), and `--hooks` copies+stamps it into every
  scaffolded stack at `<stack_dir>/hooks/<name>.py`.
- O3: an unknown `--hooks` name, or `--hooks` with no `--stacks` target,
  refuses with exit 2 BEFORE any file is written; a copied hook file is
  never silently overwritten.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ciu import scaffold  # noqa: E402
from ciu.hooks_runner import HookContext, run_hooks  # noqa: E402

_TEMPLATE_FILE = REPO_ROOT / "src" / "ciu" / "hook_templates" / "post_compose_db.py"


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Template discovery / copy machinery
# ---------------------------------------------------------------------------


def test_available_hook_templates_lists_the_shipped_reference_template():
    """Real, unmocked call: the shipped template is genuinely discoverable."""
    available = scaffold._available_hook_templates()
    assert available == {"post_compose_db": "post_compose_db.py"}


class _FakeEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeHookTemplateDir:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def iterdir(self):
        return [_FakeEntry(n) for n in self._names]


def test_available_hook_templates_filters_init_and_non_py_entries(monkeypatch):
    """Deterministic branch coverage for the discovery filter, independent
    of whatever incidental __pycache__/non-.py entries exist on disk."""
    monkeypatch.setattr(
        scaffold.resources, "files",
        lambda pkg: _FakeHookTemplateDir(
            ["__init__.py", "post_compose_db.py", "__pycache__", "README.md"]
        ),
    )
    assert scaffold._available_hook_templates() == {
        "post_compose_db": "post_compose_db.py",
    }


def test_hook_template_source_matches_the_real_file_on_disk():
    on_disk = _TEMPLATE_FILE.read_text(encoding="utf-8")
    assert scaffold._hook_template_source("post_compose_db.py") == on_disk


def test_hook_template_revision_reads_the_module_attribute():
    assert scaffold._hook_template_revision("post_compose_db") == 1


# ---------------------------------------------------------------------------
# collect_plan / build_files wiring (O3)
# ---------------------------------------------------------------------------


def test_hooks_flag_copies_stamped_template_into_every_scaffolded_stack(workdir):
    plan = scaffold.collect_plan(
        ["--project-name", "demo", "--stacks", "api,worker",
         "--hooks", "post_compose_db"],
        workdir,
    )
    assert plan["hooks"] == ["post_compose_db"]
    files = scaffold.build_files(plan, workdir)
    written = {p.relative_to(workdir).as_posix(): content for p, content in files}

    for stack in ("api", "worker"):
        rel = f"applications/{stack}/hooks/post_compose_db.py"
        assert rel in written
        content = written[rel]
        assert content.startswith("# ciu-hook-template: post_compose_db.py rev=1\n")
        # Everything after the stamp line is the template, byte-for-byte.
        stamp_line, _, rest = content.partition("\n")
        assert rest == _TEMPLATE_FILE.read_text(encoding="utf-8")


def test_hooks_flag_dedupes_repeated_names(workdir):
    plan = scaffold.collect_plan(
        ["--project-name", "demo", "--stacks", "api",
         "--hooks", "post_compose_db,post_compose_db"],
        workdir,
    )
    assert plan["hooks"] == ["post_compose_db"]
    files = scaffold.build_files(plan, workdir)
    hook_paths = [p for p, _ in files if p.name == "post_compose_db.py"]
    assert len(hook_paths) == 1


def test_hooks_flag_absent_plan_has_empty_hooks_list(workdir):
    plan = scaffold.collect_plan(["--project-name", "demo", "--stacks", "api"], workdir)
    assert plan["hooks"] == []
    files = scaffold.build_files(plan, workdir)
    assert not any(p.name == "post_compose_db.py" for p, _ in files)


def test_unknown_hooks_name_refuses_before_any_write(workdir, capsys):
    with pytest.raises(SystemExit) as exc:
        scaffold.collect_plan(
            ["--project-name", "demo", "--stacks", "api", "--hooks", "bogus"],
            workdir,
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "post_compose_db" in err  # the available list
    # Nothing scaffolded — collect_plan raised before build_files ever ran.
    assert list(workdir.iterdir()) == []


def test_unknown_hooks_name_among_valid_ones_still_refuses(workdir, capsys):
    with pytest.raises(SystemExit) as exc:
        scaffold.collect_plan(
            ["--project-name", "demo", "--stacks", "api",
             "--hooks", "post_compose_db,bogus"],
            workdir,
        )
    assert exc.value.code == 2
    assert "bogus" in capsys.readouterr().err


def test_hooks_without_any_stack_refuses(workdir, capsys):
    with pytest.raises(SystemExit) as exc:
        scaffold.collect_plan(
            ["--project-name", "demo", "--hooks", "post_compose_db"], workdir,
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--stacks" in err


def test_existing_hook_file_refuses_overwrite(workdir):
    dest = workdir / "applications" / "api" / "hooks" / "post_compose_db.py"
    dest.parent.mkdir(parents=True)
    dest.write_text("# operator's own hand-written hook\n", encoding="utf-8")

    plan = scaffold.collect_plan(
        ["--project-name", "demo", "--stacks", "api", "--hooks", "post_compose_db"],
        workdir,
    )
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.build_files(plan, workdir)
    # The operator's file is untouched.
    assert dest.read_text(encoding="utf-8") == "# operator's own hand-written hook\n"


def test_cli_end_to_end_with_hooks(workdir, monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["ciu", "init", "--project-name", "solo", "--stacks", "api",
         "--hooks", "post_compose_db"],
    )
    from ciu.cli import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    written = (workdir / "applications" / "api" / "hooks" / "post_compose_db.py")
    assert written.is_file()
    assert written.read_text(encoding="utf-8").startswith(
        "# ciu-hook-template: post_compose_db.py rev=1\n"
    )


# ---------------------------------------------------------------------------
# Shipped post_compose_db.py — the S9.1/S9.4/S9.5 contract, run for real
# (O2: "a test that imports it and checks the contract, not just that the
# file exists")
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path, *, secret_file, wait_healthy=None) -> HookContext:
    return HookContext(
        point="post_compose",
        stack_dir=tmp_path,
        repo_root=tmp_path,
        secret_file=secret_file,
        wait_healthy=wait_healthy,
    )


def _unknown_secret(name: str) -> Path:
    raise KeyError(name)


def test_shipped_template_module_shape():
    import ciu.hook_templates.post_compose_db as mod

    assert isinstance(mod.template_revision, int)
    assert mod.template_revision >= 1
    assert callable(mod.run)
    assert callable(mod.validate_config)


def test_shipped_template_run_defaults_ready_and_reports_missing_secret(tmp_path):
    """No wait_healthy wired (bare construction) and no 'db_password' secret
    declared: run() must not crash, and must report both facts honestly."""
    config = {"deploy": {}}
    state_path = tmp_path / "ciu.toml"
    run_hooks(
        [str(_TEMPLATE_FILE)], "post_compose", config,
        _ctx(tmp_path, secret_file=_unknown_secret, wait_healthy=None),
        state_path,
    )
    with state_path.open("rb") as fh:
        saved = tomllib.load(fh)
    assert saved["state"]["hook_state"] == {
        "db_service_healthy": True,
        "db_password_materialized": False,
    }


def test_shipped_template_run_uses_wait_healthy_and_finds_materialized_secret(tmp_path):
    seen_services: list[str] = []

    def wait_healthy(service: str, *, timeout_s: float = 120.0) -> bool:
        seen_services.append(service)
        return False

    secret_path = tmp_path / "db_password"
    secret_path.write_text("s3cr3t\n", encoding="utf-8")

    config = {"deploy": {"db_service_name": "postgres"}}
    state_path = tmp_path / "ciu.toml"
    run_hooks(
        [str(_TEMPLATE_FILE)], "post_compose", config,
        _ctx(
            tmp_path,
            secret_file=lambda name: secret_path if name == "db_password" else _unknown_secret(name),
            wait_healthy=wait_healthy,
        ),
        state_path,
    )
    assert seen_services == ["postgres"]
    with state_path.open("rb") as fh:
        saved = tomllib.load(fh)
    assert saved["state"]["hook_state"] == {
        "db_service_healthy": False,
        "db_password_materialized": True,
    }


def test_shipped_template_validate_config_flags_missing_service_name():
    import ciu.hook_templates.post_compose_db as mod

    ctx = _ctx(Path("/tmp"), secret_file=_unknown_secret)
    findings = mod.validate_config({"deploy": {}}, ctx)
    assert findings == [
        "deploy.db_service_name is not declared; run() would default "
        "to 'db', which may not match your compose service name"
    ]


def test_shipped_template_validate_config_clean_when_declared():
    import ciu.hook_templates.post_compose_db as mod

    ctx = _ctx(Path("/tmp"), secret_file=_unknown_secret)
    findings = mod.validate_config({"deploy": {"db_service_name": "postgres"}}, ctx)
    assert findings == []
