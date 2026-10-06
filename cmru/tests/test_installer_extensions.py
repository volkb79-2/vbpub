"""Tests for the get.py `extensions` mechanism (cmru program 2026-10, W1-CIU-ENROLL, O4).

`[project.installer] extensions = ["<relpath>.py", ...]` inlines project-owned
fragments into the rendered, single-file get.py at the `# @@EXTENSIONS@@` marker.
Covered: render with 0/1/2 fragments, determinism, the sha256 banner, every
render-time refusal (a)-(e), config path validation, the runtime duplicate-command
refusal, extension dispatch with token passing, `--help`, and that a render without
extensions carries no enrollment code at all.

Fixture fragments live in tests/fixtures/ (they are test fixtures, not ciu's code).
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from cmru.getpy import ExtensionError, getpy_main, render_from_config, render_get_py

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parents[2]
HELLO = ("fixtures/ext_hello.py", (FIXTURES / "ext_hello.py").read_bytes())
SECOND = ("fixtures/ext_second.py", (FIXTURES / "ext_second.py").read_bytes())

BASE = dict(
    project_name="demo", repo_owner="o", repo_name="r", tag_prefix="demo-v",
    install_dir_system="/opt/demo", install_dir_user="demo", required_commands=[],
)


def _render(*extensions, **kw) -> str:
    return render_get_py(**{**BASE, **kw}, extensions=list(extensions) or None)


def _frag(source: str, name: str = "x.py"):
    return (name, source.encode("utf-8"))


def _ns(src: str) -> dict:
    ns: dict = {}
    exec(compile(src, "<rendered-get.py>", "exec"), ns)
    ns["check_prerequisites"] = lambda: None
    return ns


def _run_main(ns: dict, *argv: str) -> None:
    with mock.patch.object(sys, "argv", ["get.py", *argv]):
        ns["main"]()


def _template_names(src: str) -> set:
    names = set()
    for stmt in ast.parse(src).body:
        if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            names |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
    return names


GOOD_TAIL = "\ndef _reg(subparsers):\n    return {}\n\n_EXTENSIONS.append(_reg)\n"


class TestRender:
    def test_zero_extensions_marker_is_replaced_by_nothing(self):
        out = _render()
        assert "@@EXTENSIONS@@" not in out
        assert "# --- extension:" not in out
        compile(out, "<get.py>", "exec")

    def test_zero_extensions_has_no_enroll_and_no_authorized_keys(self):
        """A project that renders get.py gains no root-run authorized_keys writer."""
        out = _render()
        assert "authorized_keys" not in out
        assert "enroll" not in out
        assert "do_enroll" not in out
        ns = _ns(out)
        assert "do_enroll" not in ns and "_parse_authorized_key" not in ns

    def test_zero_extensions_help_lists_only_core_commands(self, capsys):
        ns = _ns(_render())
        with pytest.raises(SystemExit) as exc:
            _run_main(ns, "--help")
        assert exc.value.code == 0
        helptext = capsys.readouterr().out
        assert "enroll" not in helptext
        for core in ("install", "update", "status", "rollback"):
            assert core in helptext

    def test_one_extension_is_inlined_verbatim_with_sha256_banner(self):
        out = _render(HELLO)
        rel, raw = HELLO
        digest = hashlib.sha256(raw).hexdigest()
        begin = f"# --- extension: {rel} sha256={digest} ---\n"
        end = f"# --- end extension: {rel} ---\n"
        assert begin in out and end in out
        assert out.index(begin) < out.index(raw.decode()) < out.index(end)
        assert out.count("@@EXTENSIONS@@") == 0

    def test_extension_sits_after_core_helpers_before_main(self):
        out = _render(HELLO)
        pos = out.index("# --- extension:")
        assert out.index("def do_install") < pos
        assert out.index("def do_status") < pos
        assert pos < out.index("def check_prerequisites") < out.index("def main")

    def test_two_extensions_in_declared_order(self):
        out = _render(HELLO, SECOND)
        assert out.index("ext_hello.py sha256") < out.index("ext_second.py sha256")
        swapped = _render(SECOND, HELLO)
        assert swapped.index("ext_second.py sha256") < swapped.index("ext_hello.py sha256")
        assert out != swapped
        compile(out, "<get.py>", "exec")

    def test_output_is_one_file_and_byte_identical_across_renders(self):
        first = _render(HELLO, SECOND)
        assert first == _render(HELLO, SECOND)
        assert _render() == _render()
        assert first.count("#!/usr/bin/env python3") == 1

    def test_banner_sha_tracks_the_fragment_bytes(self):
        changed = (HELLO[0], HELLO[1] + b"# one more comment\n")
        a, b = _render(HELLO), _render(changed)
        assert hashlib.sha256(HELLO[1]).hexdigest() in a
        assert hashlib.sha256(changed[1]).hexdigest() in b
        assert a != b

    def test_fragment_without_trailing_newline_is_terminated(self):
        raw = ("def _r(subparsers):\n    return {}\n_EXTENSIONS.append(_r)").encode()
        out = _render(("n.py", raw))
        assert "_EXTENSIONS.append(_r)\n# --- end extension: n.py ---\n" in out
        assert hashlib.sha256(raw).hexdigest() in out

    def test_template_placeholders_work_inside_fragments(self):
        frag = _frag(
            "def _r(subparsers):\n    print('[[PROJECT_NAME]]')\n    return {}\n"
            "_EXTENSIONS.append(_r)\n"
        )
        out = _render(frag, project_name="my-proj")
        assert "print('my-proj')" in out
        assert "[[PROJECT_NAME]]" not in out

    def test_extension_api_names_all_exist_in_the_template(self):
        out = _render()
        ns = _ns(out)
        api = ns["EXTENSION_API"]
        assert isinstance(api, tuple) and api == tuple(sorted(api))
        assert set(api) <= _template_names(out)
        assert "_EXTENSIONS" in api


class TestRefusals:
    """Render-time contract checks; each names the fragment and a line."""

    def test_a_syntax_error_names_fragment_and_line(self):
        with pytest.raises(ExtensionError, match=r"extension bad\.py: does not parse \(line 2\)"):
            _render(_frag("x = 1\ndef (:\n", "bad.py"))

    def test_b_collision_with_a_template_name(self):
        src = "def info(msg):\n    pass\n" + GOOD_TAIL
        with pytest.raises(ExtensionError, match=r"x\.py: line 1: top-level name 'info' collides"):
            _render(_frag(src))

    def test_b_collision_with_a_template_assignment(self):
        with pytest.raises(ExtensionError, match="'RELEASES_API' collides"):
            _render(_frag("RELEASES_API = 1\n" + GOOD_TAIL))

    def test_b_import_rebinding_a_template_name_collides(self):
        with pytest.raises(ExtensionError, match="'fatal' collides"):
            _render(_frag("from os import path as fatal\n" + GOOD_TAIL))

    def test_b_collision_between_two_fragments(self):
        one = _frag("def shared():\n    pass\n" + GOOD_TAIL, "one.py")
        two = _frag("def shared():\n    pass\n" + GOOD_TAIL.replace("_reg", "_reg2"), "two.py")
        with pytest.raises(ExtensionError, match=r"two\.py: line 1: .*'shared' collides with extension one\.py"):
            _render(one, two)

    def test_b_same_stdlib_import_in_two_fragments_is_fine(self):
        one = _frag("import os\n" + GOOD_TAIL, "one.py")
        two = _frag("import os\n" + GOOD_TAIL.replace("_reg", "_reg2"), "two.py")
        compile(_render(one, two), "<get.py>", "exec")

    def test_c_template_name_outside_the_api_is_refused(self):
        src = "def _r(subparsers):\n    _gh_request('x')\n    return {}\n_EXTENSIONS.append(_r)\n"
        with pytest.raises(ExtensionError, match=r"x\.py: line 2: uses template name '_gh_request'.*EXTENSION_API"):
            _render(_frag(src))

    def test_c_api_names_and_local_shadows_are_allowed(self):
        src = (
            "def _r(subparsers):\n"
            "    info('hi'); ok('x'); warn('w'); hr(); fatal\n"
            "    def inner(err, token=_c):\n"
            "        return [err for err in (err,)]\n"
            "    return {}\n"
            "_EXTENSIONS.append(_r)\n"
        )
        compile(_render(_frag(src)), "<get.py>", "exec")

    def test_c_uses_in_default_args_decorators_and_comprehensions_are_checked(self):
        for body, name in (
            ("def _r(subparsers, d=_ALLOWED_HOSTS):\n    return {}\n", "_ALLOWED_HOSTS"),
            ("def _r(subparsers):\n    return {c: 1 for c in [_gh_json]}\n", "_gh_json"),
            ("def _r(subparsers):\n    return {}\n_x = [_semver_key(t) for t in ()]\n", "_semver_key"),
        ):
            with pytest.raises(ExtensionError, match=name):
                _render(_frag(body + "_EXTENSIONS.append(_r)\n"))

    def test_c_scope_analysis_handles_lambdas_decorators_classes_and_varargs(self):
        """Constructs a real fragment may use must neither false-positive nor hide a use."""
        ok_src = (
            "def deco(fn):\n    return fn\n"
            "class Box:\n    value = 1\n    def get(self, *args, **kwargs):\n"
            "        return info\n"
            "@deco\n"
            "def _r(subparsers, *rest, **opts):\n"
            "    try:\n        pass\n    except OSError as err:\n        print(err, rest, opts)\n"
            "    f = lambda info: info\n"
            "    return {}\n"
            "_EXTENSIONS.append(_r)\n"
        )
        compile(_render(_frag(ok_src)), "<get.py>", "exec")
        # a lambda local does not shadow a template name used OUTSIDE the lambda
        bad = ("def _r(subparsers):\n    f = lambda _gh_json: 1\n    return _gh_json\n"
               "_EXTENSIONS.append(_r)\n")
        with pytest.raises(ExtensionError, match="_gh_json"):
            _render(_frag(bad))
        # a decorator is a use of a template name
        with pytest.raises(ExtensionError, match="_semver_key"):
            _render(_frag("@_semver_key\ndef _r(subparsers):\n    return {}\n"
                          "_EXTENSIONS.append(_r)\n"))

    def test_extensions_without_a_project_directory_are_refused(self):
        from cmru.getpy import _read_extensions
        with pytest.raises(ExtensionError, match="project directory"):
            _read_extensions("demo", ["x.py"], None)
        assert _read_extensions("demo", [], None) == []

    def test_d_non_stdlib_import_is_refused(self):
        with pytest.raises(ExtensionError, match=r"x\.py: line 1: import of 'requests' is not standard library"):
            _render(_frag("import requests\n" + GOOD_TAIL))
        with pytest.raises(ExtensionError, match="'yaml.loader'"):
            _render(_frag("from yaml.loader import Loader\n" + GOOD_TAIL))

    def test_d_relative_import_is_refused(self):
        with pytest.raises(ExtensionError, match="relative import"):
            _render(_frag("from . import sibling\n" + GOOD_TAIL))

    def test_e_missing_registration_is_refused(self):
        with pytest.raises(ExtensionError, match=r"x\.py: no top-level `_EXTENSIONS.append\(<name>\)`"):
            _render(_frag("def _r(subparsers):\n    return {}\n"))

    def test_e_registration_must_be_top_level(self):
        src = "def _r(subparsers):\n    return {}\nif True:\n    _EXTENSIONS.append(_r)\n"
        with pytest.raises(ExtensionError, match="no top-level"):
            _render(_frag(src))

    def test_non_utf8_fragment_is_refused(self):
        with pytest.raises(ExtensionError, match="not valid UTF-8"):
            _render(("x.py", b"\xff\xfe"))

    def test_template_without_exactly_one_marker_is_refused(self, tmp_path):
        tmpl = tmp_path / "t.tmpl"
        tmpl.write_text("x = 1\n")
        with pytest.raises(ValueError, match="exactly one"):
            render_get_py(**BASE, template_path=tmpl, extensions=[HELLO])
        # without extensions a marker-less template is simply rendered
        assert render_get_py(**BASE, template_path=tmpl) == "x = 1\n"


def _project(tmp_path: Path, extensions_line: str, files: dict | None = None) -> Path:
    for rel, text in (files or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    cfg = tmp_path / "cmru.toml"
    cfg.write_text(
        'schema_version = 1\n[github]\nowner = "octocat"\nrepo = "demo"\nowner_type = "user"\n'
        '[targets]\nhost = "github"\nregistry = []\n[runtime]\nkind = "none"\n'
        '[project]\nid = "demo"\ndescription = "t"\ntemplate_revision = 2\n'
        'prefix = "demo-v"\nartifacts = ["tarball"]\n'
        '[project.version]\nstrategy = "file:VERSION"\nbump = "conventional"\n'
        '[project.release]\ngit_tag = true\nbuild_step = "build"\n'
        '[project.installer]\ninstall_dir_system = "/opt/demo"\ninstall_dir_user = "demo"\n'
        + extensions_line + "\n"
        '[steps.run-tests]\nquiet = true\ncommands = [{ label = "t", argv = ["true"], cwd = "." }]\n'
        '[steps.build]\nquiet = true\ncommands = [{ label = "b", argv = ["true"], cwd = "." }]\n'
        '[steps.push]\nquiet = true\ncommands = [{ label = "p", argv = ["true"], cwd = "." }]\n'
    )
    return cfg


GOOD_FILE = (FIXTURES / "ext_hello.py").read_text()


class TestConfig:
    def test_valid_extensions_render_from_config(self, tmp_path):
        cfg = _project(tmp_path, 'extensions = ["ext/hello.py", "second.py"]',
                       {"ext/hello.py": GOOD_FILE, "second.py": (FIXTURES / "ext_second.py").read_text()})
        out = render_from_config("demo", cfg)
        assert out.index("ext/hello.py sha256") < out.index("second.py sha256")
        assert out == render_from_config("demo", cfg)

    def test_no_extensions_key_is_the_plain_installer(self, tmp_path):
        cfg = _project(tmp_path, "")
        plain = render_get_py(**{**BASE, "repo_owner": "octocat", "repo_name": "demo",
                                 "required_commands": None})
        assert render_from_config("demo", cfg) == plain

    @pytest.mark.parametrize("value, message", [
        ('"hello.py"', "must be a list"),
        ('[1]', r"extensions\[0\] must be a non-empty string"),
        ('[""]', r"extensions\[0\] must be a non-empty string"),
        ('["/etc/hello.py"]', "not absolute"),
        ('["../hello.py"]', r"no '\.\.'"),
        ('["a/../../hello.py"]', r"no '\.\.'"),
        ('["hello.txt"]', "must end in .py"),
        ('["hello.py", "hello.py"]', "duplicates an earlier entry"),
    ])
    def test_invalid_paths_are_refused_with_exit_2(self, tmp_path, capsys, value, message):
        cfg = _project(tmp_path, f"extensions = {value}", {"hello.py": GOOD_FILE})
        with pytest.raises(SystemExit) as exc:
            render_from_config("demo", cfg)
        assert exc.value.code == 2
        assert re.search(message, capsys.readouterr().err), message

    def test_missing_fragment_is_a_render_error(self, tmp_path):
        cfg = _project(tmp_path, 'extensions = ["nope.py"]')
        with pytest.raises(ExtensionError, match=r"nope\.py: file not found"):
            render_from_config("demo", cfg)

    def test_symlink_escaping_the_project_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "evil.py").write_text(GOOD_FILE)
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "link.py").symlink_to(outside / "evil.py")
        cfg = _project(proj, 'extensions = ["link.py"]')
        with pytest.raises(ExtensionError, match="resolves outside the project directory"):
            render_from_config("demo", cfg)

    def test_cli_reports_a_render_error_as_exit_2_without_traceback(self, tmp_path, capsys):
        cfg = _project(tmp_path, 'extensions = ["nope.py"]')
        assert getpy_main(["demo", "--config", str(cfg)]) == 2
        assert "file not found" in capsys.readouterr().err

    def test_cli_renders_with_extensions_to_a_file(self, tmp_path):
        cfg = _project(tmp_path, 'extensions = ["hello.py"]', {"hello.py": GOOD_FILE})
        out = tmp_path / "get.py"
        assert getpy_main(["demo", "--config", str(cfg), "--output", str(out)]) == 0
        result = subprocess.run([sys.executable, str(out), "hello", "--help"],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert "--name" in result.stdout


class TestRuntime:
    def test_extension_command_is_dispatched_with_args_and_token(self, monkeypatch, capsys):
        monkeypatch.setenv("CMRU_GITHUB_TOKEN", "tok123")
        ns = _ns(_render(HELLO))
        _run_main(ns, "hello", "--name", "ada")
        out = capsys.readouterr().out
        assert "hello name=ada token=tok123" in out
        assert "exit-codes=2,3" in out

    def test_both_extensions_are_dispatchable(self, capsys):
        ns = _ns(_render(HELLO, SECOND))
        _run_main(ns, "second")
        assert "second:local-err" in capsys.readouterr().out
        _run_main(ns, "hello")
        assert "hello name=world" in capsys.readouterr().out

    def test_core_commands_still_dispatch_beside_extensions(self):
        ns = _ns(_render(HELLO))
        seen = {}
        ns["do_status"] = lambda args: seen.setdefault("status", args.scope)
        _run_main(ns, "status", "--scope", "user")
        assert seen == {"status": "user"}

    def test_help_lists_the_extension_command(self, capsys):
        ns = _ns(_render(HELLO))
        with pytest.raises(SystemExit) as exc:
            _run_main(ns, "--help")
        assert exc.value.code == 0
        helptext = capsys.readouterr().out
        assert "hello" in helptext and "Say hello (test fixture)" in helptext

    def test_extension_that_reuses_a_core_command_name_is_refused(self, capsys):
        dup = _frag(
            "def _h(args, token):\n    pass\n"
            "def _r(subparsers):\n"
            "    subparsers.add_parser('install')\n"
            "    return {'install': _h}\n"
            "_EXTENSIONS.append(_r)\n", "dup.py")
        ns = _ns(_render(dup))
        with pytest.raises(SystemExit) as exc:
            _run_main(ns, "status")
        assert exc.value.code == 2
        assert "install" in capsys.readouterr().err

    def test_handler_name_duplicating_core_without_a_parser_clash_is_refused(self, capsys):
        dup = _frag(
            "def _h(args, token):\n    pass\n"
            "def _r(subparsers):\n"
            "    subparsers.add_parser('other')\n"
            "    return {'install': _h, 'other': _h}\n"
            "_EXTENSIONS.append(_r)\n", "dup.py")
        ns = _ns(_render(dup))
        with pytest.raises(SystemExit) as exc:
            _run_main(ns, "status")
        assert exc.value.code == 2
        assert "duplicates an existing command" in capsys.readouterr().err

    def test_two_extensions_registering_the_same_command_are_refused(self, capsys):
        def frag(name):
            return _frag(
                f"def _h_{name}(args, token):\n    pass\n"
                f"def _r_{name}(subparsers):\n"
                "    subparsers.add_parser('same')\n"
                f"    return {{'same': _h_{name}}}\n"
                f"_EXTENSIONS.append(_r_{name})\n", f"{name}.py")
        ns = _ns(_render(frag("a"), frag("b")))
        with pytest.raises(SystemExit) as exc:
            _run_main(ns, "status")
        assert exc.value.code == 2
        assert "same" in capsys.readouterr().err

    def test_handler_without_a_subparser_is_refused(self, capsys):
        bad = _frag(
            "def _h(args, token):\n    pass\n"
            "def _r(subparsers):\n    return {'ghost': _h}\n"
            "_EXTENSIONS.append(_r)\n", "ghost.py")
        ns = _ns(_render(bad))
        with pytest.raises(SystemExit) as exc:
            _run_main(ns, "status")
        assert exc.value.code == 2
        assert "ghost" in capsys.readouterr().err


class TestRealProjects:
    """The two renders committed in this monorepo, from their real configs."""

    CENTRAL = REPO / "cmru.orchestration.toml"

    def test_tls_edge_has_no_enrollment_and_keeps_its_own_settings(self):
        if not (REPO / "tls-edge" / "cmru.toml").exists():
            pytest.skip("tls-edge not present")
        out = render_from_config("tls-edge", self.CENTRAL)
        assert "enroll" not in out and "authorized_keys" not in out
        assert 'REQUIRED_COMMANDS: List[str] = ["python3", "docker"]' in out
        assert '"ciu-stack/ciu.toml.j2"' in out and '"edge-proxy/.env"' in out
        assert out == (REPO / "tls-edge" / "get.py").read_text(encoding="utf-8")

    def test_ciu_inlines_its_own_enroll_fragment_and_matches_the_committed_file(self):
        if not (REPO / "ciu" / "installer" / "enroll.py").exists():
            pytest.skip("ciu enroll fragment not present")
        out = render_from_config("ciu", self.CENTRAL)
        raw = (REPO / "ciu" / "installer" / "enroll.py").read_bytes()
        assert f"# --- extension: installer/enroll.py sha256={hashlib.sha256(raw).hexdigest()} ---" in out
        assert "def do_enroll" in out
        assert out == (REPO / "ciu" / "get.py").read_text(encoding="utf-8")
