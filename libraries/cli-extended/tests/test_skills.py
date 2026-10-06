"""W4: packaged agent skills (CLI-EXT-05).  Every filesystem effect is in tmp_path."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import pytest

from cli_extended import (
    CliIdentity,
    CliRegistry,
    SkillError,
    SkillState,
    VerbSpec,
    register_skills_verbs,
    skill_states,
    validate_skill_source,
)
from cli_extended import skills as skills_module

REPO_ROOT = Path(__file__).resolve().parents[3]
STAMP = ".cli-extended-stamp.json"


# ------------------------------------------------------------------ helpers


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    before = set(sys.modules)
    yield home
    for name in set(sys.modules) - before:
        if name.startswith("skpkg_"):
            del sys.modules[name]


def md(name, desc="Does things.", front="", body="\n# Body\n"):
    return f"---\nname: {name}\ndescription: {desc}\n{front}---\n{body}"


def write_tree(base: Path, tree: dict) -> None:
    for relative, data in tree.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)


def write_skills(skills_dir: Path, skills: dict) -> None:
    for name, files in skills.items():
        write_tree(skills_dir / name, files)


_counter = {"n": 0}


def make_pkg(tmp_path, monkeypatch, skills, resource_dir="skills") -> str:
    _counter["n"] += 1
    package = f"skpkg_{_counter['n']}_{os.getpid()}"
    root = tmp_path / "pkgs"
    (root / package).mkdir(parents=True)
    (root / package / "__init__.py").write_text("")
    write_skills(root / package / resource_dir, skills)
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    return package


def pkg_skills_dir(tmp_path, package, resource_dir="skills") -> Path:
    return tmp_path / "pkgs" / package / resource_dir


def make_registry(package, version="1.0.0", tool="mytool", **kwargs):
    identity = CliIdentity("MYTOOL", version, "My tool", tool)
    registry = CliRegistry(identity, prog=tool, description="My tool.", **kwargs)
    register_skills_verbs(registry, package=package)
    return registry


def run(package, *args, version="1.0.0", tool="mytool"):
    app = make_registry(package, version=version, tool=tool).build()
    stdout, stderr = io.StringIO(), io.StringIO()
    code = app.run(argv=["skills", *args], stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def snapshot(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def source_hash(files: dict) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        data = files[relative].encode() if isinstance(files[relative], str) else files[relative]
        digest.update(relative.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return "sha256:" + digest.hexdigest()


def banner(tool="mytool", version="1.0.0"):
    return (
        f"> Installed by {tool} {version} via cli-extended. "
        f"If this disagrees with `{tool} --help`, run `{tool} skills check`."
    )


def states(code_out: str) -> list[str]:
    return [line.split()[0] for line in code_out.splitlines()]


SKILLS = {
    "alpha": {"SKILL.md": md("alpha"), "notes/extra.txt": "extra\n", "bin.dat": b"\x00\xff\x01"},
    "beta": {"SKILL.md": md("beta", desc="'Quoted: desc'")},
}


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    return make_pkg(tmp_path, monkeypatch, SKILLS)


def stamp_dir(path: Path, tool: str, version="1.0.0", digest="sha256:x") -> None:
    files = {
        rel: sha(data)
        for rel, data in ((p.relative_to(path).as_posix(), p.read_bytes())
                          for p in sorted(path.rglob("*")) if p.is_file())
    }
    (path / STAMP).write_text(json.dumps({
        "schema_version": 1, "tool": tool, "version": version,
        "source_hash": digest, "files": files,
    }))


# ----------------------------------------------------- rendering / contract


def test_install_renders_exact_tree_stamp_and_sidecar(pkg, tmp_path):
    dest = tmp_path / "dest"
    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"installed alpha -> {dest}",
        f"installed beta -> {dest}",
    ]
    digest = source_hash(SKILLS["alpha"])
    expected_md = (
        "---\nname: alpha\ndescription: Does things.\nmetadata:\n"
        "  cli-extended-tool: mytool\n  cli-extended-version: 1.0.0\n"
        f"  cli-extended-source-hash: {digest}\n---\n"
        f"{banner()}\n\n\n# Body\n"
    )
    installed = dest / "alpha"
    assert (installed / "SKILL.md").read_text() == expected_md
    assert (installed / "notes/extra.txt").read_bytes() == b"extra\n"
    assert (installed / "bin.dat").read_bytes() == b"\x00\xff\x01"
    sidecar_text = (installed / STAMP).read_text()
    assert sidecar_text.endswith("}\n")
    assert sidecar_text == json.dumps({
        "files": {
            "SKILL.md": sha(expected_md.encode()),
            "bin.dat": sha(b"\x00\xff\x01"),
            "notes/extra.txt": sha(b"extra\n"),
        },
        "schema_version": 1,
        "source_hash": digest,
        "tool": "mytool",
        "version": "1.0.0",
    }, sort_keys=True, indent=2) + "\n"
    assert sorted(p.name for p in dest.iterdir()) == ["alpha", "beta"]


def test_render_extends_existing_metadata_block(tmp_path, monkeypatch):
    skill = md("m1", front="metadata:\n  author: me\n  tier: 'x y'\nextra: z\n")
    package = make_pkg(tmp_path, monkeypatch, {"m1": {"SKILL.md": skill}})
    dest = tmp_path / "d"
    assert run(package, "install", "--dest", str(dest))[0] == 0
    digest = source_hash({"SKILL.md": skill})
    assert (dest / "m1/SKILL.md").read_text() == (
        "---\nname: m1\ndescription: Does things.\nmetadata:\n  author: me\n"
        "  tier: 'x y'\n"
        f"  cli-extended-tool: mytool\n  cli-extended-version: 1.0.0\n"
        f"  cli-extended-source-hash: {digest}\nextra: z\n---\n{banner()}\n\n\n# Body\n"
    )


def test_render_empty_metadata_line_gets_stamp_keys(tmp_path, monkeypatch):
    skill = md("m2", front="metadata:\n")
    package = make_pkg(tmp_path, monkeypatch, {"m2": {"SKILL.md": skill}})
    dest = tmp_path / "d"
    assert run(package, "install", "--dest", str(dest))[0] == 0
    digest = source_hash({"SKILL.md": skill})
    assert (dest / "m2/SKILL.md").read_text() == (
        "---\nname: m2\ndescription: Does things.\nmetadata:\n"
        "  cli-extended-tool: mytool\n  cli-extended-version: 1.0.0\n"
        f"  cli-extended-source-hash: {digest}\n---\n{banner()}\n\n\n# Body\n"
    )


def test_tool_is_the_command_name_not_the_display_name(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {"a": {"SKILL.md": md("a")}})
    dest = tmp_path / "d"
    assert run(package, "install", "--dest", str(dest), tool="cmd-name")[0] == 0
    side = json.loads((dest / "a" / STAMP).read_text())
    assert side["tool"] == "cmd-name"
    assert "Installed by cmd-name 1.0.0" in (dest / "a/SKILL.md").read_text()


# ------------------------------------------------------------------- O1


def test_o1_install_twice_is_unchanged_and_does_not_rewrite(pkg, tmp_path):
    dest = tmp_path / "dest"
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    files = [p for p in sorted(dest.rglob("*")) if p.is_file()]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"unchanged alpha -> {dest}",
        f"unchanged beta -> {dest}",
    ]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    assert sorted(p.name for p in dest.iterdir()) == ["alpha", "beta"]


def test_current_with_different_rendered_bytes_is_updated(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    # Same stamp and consistent sidecar, but bytes differ from today's rendering.
    skill = dest / "beta" / "SKILL.md"
    skill.write_text(skill.read_text() + "tail\n")
    side = json.loads((dest / "beta" / STAMP).read_text())
    side["files"]["SKILL.md"] = sha(skill.read_bytes())
    (dest / "beta" / STAMP).write_text(json.dumps(side))
    assert [s for n, _, s in skill_states(
        package=pkg, resource_dir="skills", tool="mytool", version="1.0.0",
        destinations=[dest]) if n == "beta"] == [SkillState.CURRENT]
    code, out, _ = run(pkg, "install", "--dest", str(dest))
    assert code == 0
    assert out.splitlines() == [f"unchanged alpha -> {dest}", f"updated beta -> {dest}"]
    assert "tail" not in skill.read_text()


# ------------------------------------------------------------------- O2


def test_o2_version_bump_is_stale_then_install_makes_it_current(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    code, out, err = run(pkg, "check", "--dest", str(dest), version="2.0")
    assert code == 1
    assert states(out) == ["stale", "stale"]
    assert "2 skill(s) are not current" in err
    assert "mytool skills install" in err
    code, out, _ = run(pkg, "install", "--dest", str(dest), version="2.0")
    assert (code, out.splitlines()) == (
        0, [f"updated alpha -> {dest}", f"updated beta -> {dest}"]
    )
    text = (dest / "alpha/SKILL.md").read_text()
    assert "  cli-extended-version: 2.0\n" in text
    assert banner(version="2.0") in text
    assert "1.0.0" not in text
    assert json.loads((dest / "alpha" / STAMP).read_text())["version"] == "2.0"
    assert run(pkg, "check", "--dest", str(dest), version="2.0")[0] == 0


def test_changed_source_with_same_version_is_stale(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    write_tree(pkg_skills_dir(tmp_path, pkg) / "alpha", {"notes/extra.txt": "changed\n"})
    code, out, _ = run(pkg, "check", "--dest", str(dest))
    assert code == 1
    assert states(out) == ["stale", "current"]
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    assert (dest / "alpha/notes/extra.txt").read_text() == "changed\n"


# ------------------------------------------------------------------- O3


def test_o3_modified_is_refused_unless_overwrite_modified(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    rendered = (dest / "alpha/SKILL.md").read_bytes()
    (dest / "alpha/SKILL.md").write_bytes(rendered + b"my edit\n")
    assert states(run(pkg, "check", "--dest", str(dest))[1]) == ["modified", "current"]

    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert code == 1
    assert out.splitlines() == [f"unchanged beta -> {dest}"]
    assert f"[ERROR] alpha -> {dest}: locally modified; refusing to overwrite" in err
    assert "Hint: rerun with --overwrite-modified to replace it" in err
    assert (dest / "alpha/SKILL.md").read_bytes() == rendered + b"my edit\n"

    code, out, err = run(pkg, "install", "--dest", str(dest), "--overwrite-modified")
    assert (code, err) == (0, "")
    assert f"updated alpha -> {dest}" in out.splitlines()
    assert (dest / "alpha/SKILL.md").read_bytes() == rendered


def test_added_and_removed_files_count_as_modified(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    (dest / "alpha" / "stray.txt").write_text("x")
    assert states(run(pkg, "list", "--dest", str(dest))[1]) == ["modified", "current"]
    (dest / "alpha" / "stray.txt").unlink()
    (dest / "alpha" / "bin.dat").unlink()
    assert states(run(pkg, "list", "--dest", str(dest))[1]) == ["modified", "current"]


# ------------------------------------------------------------------- O4


def test_o4_foreign_and_unmanaged_directories_are_never_touched(pkg, tmp_path):
    dest = tmp_path / "dest"
    write_tree(dest / "alpha", {"SKILL.md": "foreign\n", "other.txt": "o"})
    stamp_dir(dest / "alpha", "other")
    write_tree(dest / "beta", {"SKILL.md": "mine, unstamped\n"})
    before = snapshot(dest)

    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert (code, out) == (1, "")
    assert f"alpha -> {dest}: stamped by another tool; refusing to overwrite" in err
    assert f"beta -> {dest}: exists without a cli-extended stamp; refusing to overwrite" in err
    assert "Hint: remove the directory yourself" in err
    assert "Hint: move or remove the directory yourself" in err
    assert snapshot(dest) == before

    code, out, _ = run(pkg, "install", "--dest", str(dest), "--overwrite-modified")
    assert code == 1
    assert snapshot(dest) == before
    assert states(run(pkg, "check", "--dest", str(dest))[1]) == ["foreign", "unmanaged"]


def test_other_skills_are_still_processed_after_a_refusal(pkg, tmp_path):
    dest = tmp_path / "dest"
    write_tree(dest / "alpha", {"SKILL.md": "unstamped\n"})
    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert code == 1
    assert out.splitlines() == [f"installed beta -> {dest}"]
    assert (dest / "beta" / STAMP).is_file()
    assert (dest / "alpha/SKILL.md").read_text() == "unstamped\n"


def test_a_file_where_a_skill_dir_belongs_is_unmanaged(pkg, tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "alpha").write_text("i am a file")
    code, _, err = run(pkg, "install", "--dest", str(dest))
    assert code == 1
    assert "alpha" in err and "without a cli-extended stamp" in err
    assert (dest / "alpha").read_text() == "i am a file"


@pytest.mark.parametrize("sidecar", [
    "not json {",
    "[1, 2]",
    json.dumps({"schema_version": 2, "tool": "mytool", "version": "1", "source_hash": "h", "files": {}}),
    json.dumps({"schema_version": 1, "tool": 5, "version": "1", "source_hash": "h", "files": {}}),
    json.dumps({"schema_version": 1, "tool": "mytool", "version": 1, "source_hash": "h", "files": {}}),
    json.dumps({"schema_version": 1, "tool": "mytool", "version": "1", "source_hash": None, "files": {}}),
    json.dumps({"schema_version": 1, "tool": "mytool", "version": "1", "source_hash": "h"}),
    json.dumps({"schema_version": 1, "tool": "mytool", "version": "1", "source_hash": "h", "files": []}),
    json.dumps({"schema_version": 1, "tool": "mytool", "version": "1", "source_hash": "h", "files": {"a": 1}}),
    "\xff\xfe",
])
def test_invalid_sidecars_make_the_directory_unmanaged(pkg, tmp_path, sidecar):
    dest = tmp_path / "dest"
    write_tree(dest / "alpha", {"SKILL.md": "x", STAMP: sidecar.encode("latin-1")})
    rows = skill_states(package=pkg, resource_dir="skills", tool="mytool",
                        version="1.0.0", destinations=[dest])
    assert rows[0] == ("alpha", dest, SkillState.UNMANAGED)


def test_sidecar_that_is_valid_but_files_mismatch_is_modified_not_unmanaged(pkg, tmp_path):
    dest = tmp_path / "dest"
    write_tree(dest / "alpha", {"SKILL.md": "x"})
    stamp_dir(dest / "alpha", "mytool")
    (dest / "alpha/SKILL.md").write_text("y")
    rows = skill_states(package=pkg, resource_dir="skills", tool="mytool",
                        version="1.0.0", destinations=[dest])
    assert rows[0][2] is SkillState.MODIFIED


# ------------------------------------------------------------------- O5


def _foreign_scenario(tmp_path, monkeypatch, wrong: bool):
    skills_a = {"shared": {"SKILL.md": md("shared", desc="tool A version")}}
    skills_b = {"shared": {"SKILL.md": md("shared", desc="tool B version")}}
    pkg_a = make_pkg(tmp_path, monkeypatch, skills_a)
    pkg_b = make_pkg(tmp_path, monkeypatch, skills_b)
    dest = tmp_path / ("dest-wrong" if wrong else "dest-real")
    with monkeypatch.context() as patch:
        if wrong:
            real = skills_module._render

            def renders_without_sidecar(*args, **kwargs):
                tree = real(*args, **kwargs)
                del tree[STAMP]
                return tree

            patch.setattr(skills_module, "_render", renders_without_sidecar)
        code_a, _, _ = run(pkg_a, "install", "--dest", str(dest), tool="tool-a")
        a_bytes = (dest / "shared/SKILL.md").read_bytes()
        check_a = run(pkg_a, "check", "--dest", str(dest), tool="tool-a")
        code_b, _, err_b = run(pkg_b, "install", "--dest", str(dest), tool="tool-b")
        b_untouched = (dest / "shared/SKILL.md").read_bytes() == a_bytes
    return {
        "a_installed": code_a == 0,
        "a_check_exit": check_a[0],
        "a_state": states(check_a[1]),
        "b_exit": code_b,
        "b_reason": "another tool" in err_b,
        "b_untouched": b_untouched,
    }


def test_o5_oracle_detects_the_copy_without_stamping_bug(tmp_path, monkeypatch):
    real = _foreign_scenario(tmp_path, monkeypatch, wrong=False)
    wrong = _foreign_scenario(tmp_path, monkeypatch, wrong=True)
    assert real == {
        "a_installed": True, "a_check_exit": 0, "a_state": ["current"],
        "b_exit": 1, "b_reason": True, "b_untouched": True,
    }
    # Without the sidecar tool A cannot recognise its own install and tool B's
    # refusal is no longer the foreign-tool refusal: the scenario differs.
    assert wrong["a_state"] == ["unmanaged"] and wrong["a_check_exit"] == 1
    assert wrong["b_reason"] is False
    assert real != wrong


# ------------------------------------------------------------------- O6


def test_o6_orphans_are_reported_removed_and_foreign_dirs_survive(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {
        "a": {"SKILL.md": md("a")}, "b": {"SKILL.md": md("b")},
    })
    dest = tmp_path / "dest"
    assert run(package, "install", "--dest", str(dest))[0] == 0
    write_tree(dest / "foreign", {"SKILL.md": "f"})
    stamp_dir(dest / "foreign", "other")
    write_tree(dest / "plain", {"SKILL.md": "p"})
    (dest / ".hidden").mkdir()
    (dest / "afile").write_text("x")

    import shutil
    shutil.rmtree(pkg_skills_dir(tmp_path, package) / "b")
    code, out, err = run(package, "check", "--dest", str(dest))
    assert code == 1
    assert out.splitlines() == [
        f"{'current':<10} a  {dest}",
        f"{'orphaned':<10} b  {dest}",
    ]
    code, out, err = run(package, "install", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [f"unchanged a -> {dest}", f"removed b -> {dest}"]
    assert not (dest / "b").exists()

    code, out, err = run(package, "uninstall", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [f"removed a -> {dest}"]
    assert sorted(p.name for p in dest.iterdir()) == [".hidden", "afile", "foreign", "plain"]
    assert (dest / "foreign" / STAMP).is_file()


def test_modified_orphan_needs_overwrite_for_install_and_uninstall(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {
        "a": {"SKILL.md": md("a")}, "b": {"SKILL.md": md("b")},
    })
    dest = tmp_path / "dest"
    run(package, "install", "--dest", str(dest))
    import shutil
    shutil.rmtree(pkg_skills_dir(tmp_path, package) / "b")
    (dest / "b/SKILL.md").write_text("edited")
    assert states(run(package, "check", "--dest", str(dest))[1]) == ["current", "modified"]

    for verb in ("install", "uninstall"):
        code, _, err = run(package, verb, "--dest", str(dest))
        assert code == 1
        assert "locally modified" in err
        assert (dest / "b/SKILL.md").read_text() == "edited"

    code, out, _ = run(package, "install", "--dest", str(dest), "--overwrite-modified")
    assert code == 0 and f"removed b -> {dest}" in out.splitlines()
    assert not (dest / "b").exists()


# ----------------------------------------------------------- uninstall


def test_uninstall_skips_absent_foreign_unmanaged_without_error(pkg, tmp_path):
    dest = tmp_path / "dest"
    write_tree(dest / "alpha", {"SKILL.md": "u"})
    code, out, err = run(pkg, "uninstall", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"skipped alpha -> {dest} (unmanaged)",
        f"skipped beta -> {dest} (absent)",
    ]
    stamp_dir(dest / "alpha", "other")
    code, out, _ = run(pkg, "uninstall", "--dest", str(dest))
    assert code == 0 and f"skipped alpha -> {dest} (foreign)" in out
    assert (dest / "alpha").is_dir()


def test_uninstall_removes_current_stale_and_modified_with_overwrite(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    (dest / "beta/SKILL.md").write_text("edited")
    code, out, err = run(pkg, "uninstall", "--dest", str(dest), version="9.9")
    assert code == 1
    assert out.splitlines() == [f"removed alpha -> {dest}"]
    assert "beta" in err and "locally modified" in err
    code, out, _ = run(pkg, "uninstall", "--dest", str(dest), "--overwrite-modified")
    assert (code, out.splitlines()) == (0, [
        f"skipped alpha -> {dest} (absent)", f"removed beta -> {dest}",
    ])
    assert list(dest.iterdir()) == []


# ------------------------------------------------------------------- O8


def _tree_with_every_state(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {
        n: {"SKILL.md": md(n)} for n in ("a", "b", "c", "d")
    })
    dest = tmp_path / "dest"
    assert run(package, "install", "--dest", str(dest))[0] == 0
    root = pkg_skills_dir(tmp_path, package)
    write_tree(root / "b", {"SKILL.md": md("b", desc="changed")})  # stale
    import shutil
    shutil.rmtree(root / "d")  # orphan
    write_tree(root / "e", {"SKILL.md": md("e")})  # absent
    (dest / "c/SKILL.md").write_text("edited")  # modified
    return package, dest


def test_o8_dry_run_install_prints_plan_and_changes_nothing(tmp_path, monkeypatch):
    package, dest = _tree_with_every_state(tmp_path, monkeypatch)
    before = {p: p.stat().st_mtime_ns for p in dest.rglob("*")}
    snap = snapshot(dest)
    code, out, err = run(package, "install", "--dest", str(dest), "--dry-run")
    assert code == 1
    assert out.splitlines() == [
        f"would skip a -> {dest} (unchanged)",
        f"would update b -> {dest}",
        f"would remove d -> {dest}",
        f"would install e -> {dest}",
    ]
    assert f"c -> {dest}: locally modified" in err
    assert "Dry run: no changes made." in err
    assert snapshot(dest) == snap
    assert {p: p.stat().st_mtime_ns for p in dest.rglob("*")} == before
    code, out, _ = run(package, "install", "--dest", str(dest), "--dry-run",
                       "--overwrite-modified")
    assert code == 0
    assert f"would update c -> {dest}" in out.splitlines()
    assert snapshot(dest) == snap


def test_o8_dry_run_uninstall_prints_plan_and_changes_nothing(tmp_path, monkeypatch):
    package, dest = _tree_with_every_state(tmp_path, monkeypatch)
    snap = snapshot(dest)
    code, out, err = run(package, "uninstall", "--dest", str(dest), "--dry-run",
                         "--overwrite-modified")
    assert (code, snapshot(dest)) == (0, snap)
    assert out.splitlines() == [
        f"would remove a -> {dest}",
        f"would remove b -> {dest}",
        f"would remove c -> {dest}",
        f"would remove d -> {dest}",
        f"would skip e -> {dest} (absent)",
    ]
    assert "Dry run: no changes made." in err


def test_dry_run_on_missing_destination_does_not_create_it(pkg, tmp_path):
    dest = tmp_path / "not" / "yet"
    code, out, _ = run(pkg, "install", "--dest", str(dest), "--dry-run")
    assert code == 0
    assert out.splitlines() == [f"would install alpha -> {dest}", f"would install beta -> {dest}"]
    assert not (tmp_path / "not").exists()


# ------------------------------------------------------------------- O9


def test_o9_zip_backed_package_installs_like_the_directory_one(tmp_path, monkeypatch):
    skills = {
        "zs": {"SKILL.md": md("zs"), "sub/deep.txt": "deep\n"},
        "zt": {"SKILL.md": md("zt")},
    }
    package = "skpkg_zipped"
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"{package}/__init__.py", "")
        for name, files in skills.items():
            for rel, data in files.items():
                bundle.writestr(f"{package}/skills/{name}/{rel}", data)
    monkeypatch.syspath_prepend(str(archive))
    importlib.invalidate_caches()
    from_zip = tmp_path / "from-zip"
    code, out, err = run(package, "install", "--dest", str(from_zip))
    assert (code, err) == (0, "")
    assert out.splitlines() == [f"installed zs -> {from_zip}", f"installed zt -> {from_zip}"]

    on_disk = make_pkg(tmp_path, monkeypatch, skills)
    from_dir = tmp_path / "from-dir"
    assert run(on_disk, "install", "--dest", str(from_dir))[0] == 0
    assert snapshot(from_zip) == snapshot(from_dir)
    assert (from_zip / "zs/sub/deep.txt").read_text() == "deep\n"
    assert run(package, "check", "--dest", str(from_zip))[0] == 0


# ------------------------------------------------------------------- O10


def test_o10_claude_config_dir_selects_the_claude_target(pkg, tmp_path, isolated_home, monkeypatch):
    claude = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    code, out, _ = run(pkg, "install", "--harness", "claude")
    assert code == 0
    assert (claude / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (isolated_home / ".claude").exists()
    assert out.splitlines()[0] == f"installed alpha -> {claude / 'skills'}"


def test_o10_unset_or_empty_config_dir_falls_back_to_home(pkg, isolated_home, monkeypatch):
    assert run(pkg, "install", "--harness", "claude")[0] == 0
    assert (isolated_home / ".claude/skills/alpha/SKILL.md").is_file()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    code, out, _ = run(pkg, "list", "--harness", "claude")
    assert code == 0
    assert str(isolated_home / ".claude/skills") in out


def test_harness_agents_and_all_defaults(pkg, isolated_home, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert run(pkg, "install", "--harness", "agents")[0] == 0
    assert (isolated_home / ".agents/skills/beta/SKILL.md").is_file()
    assert not (tmp_path / "cc").exists()
    code, out, _ = run(pkg, "install")  # default is all
    assert code == 0
    assert out.splitlines() == [
        f"installed alpha -> {tmp_path / 'cc' / 'skills'}",
        f"installed beta -> {tmp_path / 'cc' / 'skills'}",
        f"unchanged alpha -> {isolated_home / '.agents/skills'}",
        f"unchanged beta -> {isolated_home / '.agents/skills'}",
    ]
    code, out, _ = run(pkg, "check", "--harness", "all")
    assert code == 0 and len(out.splitlines()) == 4


# ------------------------------------------------------------------- O11


@pytest.mark.parametrize("verb", ["install", "uninstall", "check", "list"])
def test_o11_dest_and_harness_are_mutually_exclusive(pkg, tmp_path, verb):
    code, out, err = run(pkg, verb, "--dest", str(tmp_path / "d"), "--harness", "claude")
    assert code == 2
    assert out == ""
    assert "not allowed with argument" in err
    assert not (tmp_path / "d").exists()


# ------------------------------------------------------ check / list output


def test_check_and_list_json_are_sorted_by_destination_then_name(pkg, tmp_path, isolated_home):
    assert run(pkg, "install", "--harness", "agents")[0] == 0
    code, out, _ = run(pkg, "list", "--json")
    assert code == 0
    payload = json.loads(out)
    claude = str(isolated_home / ".claude/skills")
    agents = str(isolated_home / ".agents/skills")
    assert payload == {
        "tool": "mytool",
        "version": "1.0.0",
        "leftovers": [],
        "skills": [
            {"name": "alpha", "destination": agents, "state": "current"},
            {"name": "beta", "destination": agents, "state": "current"},
            {"name": "alpha", "destination": claude, "state": "absent"},
            {"name": "beta", "destination": claude, "state": "absent"},
        ],
    }
    code, out, err = run(pkg, "check", "--json")
    assert code == 1
    assert json.loads(out) == payload
    assert "2 skill(s) are not current" in err


def test_list_exits_zero_whatever_the_state_and_prints_table(pkg, tmp_path):
    dest = tmp_path / "dest"
    code, out, err = run(pkg, "list", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [f"{'absent':<10} alpha  {dest}", f"{'absent':<10} beta  {dest}"]


def test_check_exit_zero_only_when_all_current(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    code, out, err = run(pkg, "check", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert states(out) == ["current", "current"]


# ---------------------------------------------------------- source errors


def test_missing_resource_dir_and_package_are_clean_failures(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {})
    assert not pkg_skills_dir(tmp_path, package).exists()
    code, _, err = run(package, "check", "--dest", str(tmp_path / "d"))
    assert code == 1
    assert f"package {package!r} has no skills resource dir 'skills'" in err
    code, _, err = run("skpkg_does_not_exist", "list", "--dest", str(tmp_path / "d"))
    assert code == 1
    assert "cannot locate package 'skpkg_does_not_exist' resource dir 'skills'" in err


def test_directory_without_skill_md_and_loose_files(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {"ok": {"SKILL.md": md("ok")}, "bad": {"x.txt": "x"}})
    write_tree(pkg_skills_dir(tmp_path, package), {"README.txt": "loose files are ignored"})
    code, _, err = run(package, "list", "--dest", str(tmp_path / "d"))
    assert code == 1
    assert "skill 'bad': directory has no SKILL.md" in err
    import shutil
    shutil.rmtree(pkg_skills_dir(tmp_path, package) / "bad")
    code, out, _ = run(package, "list", "--dest", str(tmp_path / "d"))
    assert code == 0 and states(out) == ["absent"]


def test_custom_resource_dir_and_api_attribute(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {"c": {"SKILL.md": md("c")}}, resource_dir="agent_skills")
    identity = CliIdentity("T", "1.0.0", "t", "t")
    registry = CliRegistry(identity, prog="t", description="t")
    register_skills_verbs(registry, package=package, resource_dir="agent_skills")
    assert registry._cli_extended_skills == (package, "agent_skills")
    rows = skill_states(package=package, resource_dir="agent_skills", tool="t",
                        version="1.0.0", destinations=[tmp_path / "d"])
    assert rows == [("c", tmp_path / "d", SkillState.ABSENT)]


# ------------------------------------------------------------ registration


def test_registration_shape_and_double_registration(pkg):
    registry = make_registry(pkg)
    (verb,) = registry.verbs
    assert verb.name == "skills"
    assert verb.description == "install, check, or remove this tool's packaged agent skills"
    assert verb.group == "MAINTENANCE"
    child = verb.delegate
    assert child.identity.headline == "MYTOOL 1.0.0 — My tool — agent skills"
    assert child.identity.command_name == "mytool"
    assert child.unexpected_exceptions == "raise"
    assert child.logging_logger == "mytool"
    with pytest.raises(ValueError, match="already registered"):
        register_skills_verbs(registry, package=pkg)
    assert registry._cli_extended_skills == (pkg, "skills")


def test_child_verb_flags(pkg):
    child = make_registry(pkg).verbs[0].delegate
    names = {}
    stdout, stderr = io.StringIO(), io.StringIO()
    for verb in ("install", "uninstall", "check", "list"):
        stdout = io.StringIO()
        assert child.run(argv=[verb, "--help"], stdout=stdout, stderr=io.StringIO()) == 0
        names[verb] = stdout.getvalue()
    assert "--dry-run" in names["install"] and "--dry-run" in names["uninstall"]
    assert "--overwrite-modified" in names["install"]
    assert "--overwrite-modified" in names["uninstall"]
    assert "replace skills you modified locally" in names["install"]
    for verb in ("install", "uninstall"):
        assert "--yes" not in names[verb]
        assert "--json" not in names[verb]
    for verb in ("check", "list"):
        assert "--json" in names[verb]
        assert "--dry-run" not in names[verb]
        assert "--overwrite-modified" not in names[verb]
    assert all("--harness" in text and "--dest" in text for text in names.values())


def test_report_policy_is_propagated_to_the_child(pkg):
    registry = make_registry(pkg, unexpected_exceptions="report")
    assert registry.verbs[0].delegate.unexpected_exceptions == "report"
    registry.build()


def test_skills_verb_coexists_with_other_verbs(pkg, tmp_path):
    registry = make_registry(pkg)
    registry.register(VerbSpec("hello", description="say hi", handler=lambda a, r: 0))
    app = registry.build()
    assert app.run(argv=["hello"]) == 0
    stdout = io.StringIO()
    assert app.run(argv=["skills", "list", "--dest", str(tmp_path / "d")], stdout=stdout) == 0
    assert states(stdout.getvalue()) == ["absent", "absent"]


# --------------------------------------------------------- atomic writing


def test_failed_write_leaves_no_temp_dir_and_keeps_the_old_install(pkg, tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    before = snapshot(dest)
    write_tree(pkg_skills_dir(tmp_path, pkg) / "alpha", {"notes/extra.txt": "new\n"})
    real_rename = os.rename
    calls = []

    def flaky(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        if Path(src).name.startswith(".alpha.cli-extended-mytool-tmp-"):
            raise OSError("disk says no")
        return real_rename(src, dst)

    monkeypatch.setattr(skills_module.os, "rename", flaky)
    code, _out, err = run(pkg, "install", "--dest", str(dest))
    assert code == 1
    assert f"alpha -> {dest}: disk says no" in err
    monkeypatch.undo()
    assert snapshot(dest) == before
    assert sorted(p.name for p in dest.iterdir()) == ["alpha", "beta"]
    assert any(re.fullmatch(r"\.alpha\.cli-extended-mytool-old-[0-9a-f]{16}", name) for _, name in calls)


def test_failed_first_install_leaves_nothing_behind(pkg, tmp_path, monkeypatch):
    dest = tmp_path / "dest"

    def boom(src, dst):
        raise OSError("nope")

    monkeypatch.setattr(skills_module.os, "rename", boom)
    code, _out, err = run(pkg, "install", "--dest", str(dest))
    assert code == 1
    assert f"alpha -> {dest}: nope" in err
    monkeypatch.undo()
    assert list(dest.iterdir()) == []


def test_install_creates_missing_destination_parents(pkg, tmp_path):
    dest = tmp_path / "a" / "b" / "skills"
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    assert (dest / "alpha" / "SKILL.md").is_file()


# -------------------------------------------------------- O7 schema checks


def _name_of(text_name):  # helper kept trivial for parametrisation readability
    return text_name


FM = "---\n"
CASES = [
    ("no frontmatter", "alpha", "# hi\n", 1, "must start with a '---'"),
    ("blank first line", "alpha", "\n---\nname: alpha\n---\n", 1, "must start with a '---'"),
    ("missing name", "alpha", FM + "description: d\n---\n", 1, "requires 'name'"),
    ("name mismatch", "alpha", FM + "name: other\ndescription: d\n---\n", 1,
     "name 'other' must equal the directory name"),
    ("bad chars", "Alpha", FM + "name: Alpha\ndescription: d\n---\n", 1, "1-64 characters"),
    ("underscore", "a_b", FM + "name: a_b\ndescription: d\n---\n", 1, "1-64 characters"),
    ("leading hyphen", "-a", FM + "name: -a\ndescription: d\n---\n", 1, "1-64 characters"),
    ("double hyphen", "a--b", FM + "name: a--b\ndescription: d\n---\n", 1, "1-64 characters"),
    ("too long", "a" * 65, FM + f"name: {'a' * 65}\ndescription: d\n---\n", 1, "1-64 characters"),
    ("missing description", "alpha", FM + "name: alpha\n---\n", 1, "requires 'description'"),
    ("empty quoted description", "alpha", FM + 'name: alpha\ndescription: ""\n---\n', 1,
     "description must be 1-1024"),
    ("too long description", "alpha", FM + f"name: alpha\ndescription: {'d' * 1025}\n---\n", 1,
     "description must be 1-1024"),
    ("empty description", "alpha", FM + "name: alpha\ndescription:\n---\n", 3, "unsupported frontmatter syntax"),
    ("folded", "alpha", FM + "name: alpha\ndescription: >\n  text\n---\n", 3,
     "unsupported frontmatter syntax; use a single-line value"),
    ("literal", "alpha", FM + "name: alpha\ndescription: |\n  text\n---\n", 3, "unsupported frontmatter syntax"),
    ("list item", "alpha", FM + "name: alpha\ndescription: d\nmetadata:\n  - a\n---\n", 5,
     "unsupported frontmatter syntax"),
    ("top level list", "alpha", FM + "name: alpha\n- a\ndescription: d\n---\n", 3, "unsupported frontmatter syntax"),
    ("deep indent", "alpha", FM + "name: alpha\ndescription: d\nmetadata:\n    deep: x\n---\n", 5,
     "unsupported frontmatter syntax"),
    ("indent outside metadata", "alpha", FM + "name: alpha\n  stray: x\ndescription: d\n---\n", 3,
     "unsupported frontmatter syntax"),
    ("indent after other key", "alpha",
     FM + "name: alpha\nmetadata:\n  a: b\ndescription: d\n  c: x\n---\n", 6,
     "unsupported frontmatter syntax"),
    ("tab", "alpha", FM + "name: alpha\ndescription:\td\n---\n", 3, "unsupported frontmatter syntax"),
    ("lone carriage return", "alpha", FM + "name: alpha\rx\ndescription: d\n---\n", 2, "unsupported frontmatter syntax"),
    ("no space after colon", "alpha", FM + "name:alpha\ndescription: d\n---\n", 2, "unsupported frontmatter syntax"),
    ("metadata with value", "alpha", FM + "name: alpha\ndescription: d\nmetadata: x\n---\n", 4,
     "unsupported frontmatter syntax"),
    ("unterminated quote", "alpha", FM + 'name: alpha\ndescription: "abc\n---\n', 3, "unsupported frontmatter syntax"),
    ("mismatched quotes", "alpha", FM + "name: alpha\ndescription: 'abc\"\n---\n", 3, "unsupported frontmatter syntax"),
    ("lone quote", "alpha", FM + "name: alpha\ndescription: '\n---\n", 3, "unsupported frontmatter syntax"),
    ("unterminated metadata quote", "alpha",
     FM + "name: alpha\ndescription: d\nmetadata:\n  k: 'v\n---\n", 5, "unsupported frontmatter syntax"),
    ("metadata no value", "alpha",
     FM + "name: alpha\ndescription: d\nmetadata:\n  k:\n---\n", 5, "unsupported frontmatter syntax"),
    ("duplicate key", "alpha", FM + "name: alpha\ndescription: d\nname: alpha\n---\n", 4, "duplicate key 'name'"),
    ("duplicate metadata", "alpha",
     FM + "name: alpha\ndescription: d\nmetadata:\n  a: 1\nmetadata:\n  b: 2\n---\n", 6, "duplicate key 'metadata'"),
    ("duplicate metadata key", "alpha",
     FM + "name: alpha\ndescription: d\nmetadata:\n  a: 1\n  a: 2\n---\n", 6, "duplicate metadata key 'a'"),
    ("reserved key", "alpha",
     FM + "name: alpha\ndescription: d\nmetadata:\n  cli-extended-tool: x\n---\n", 5, "reserved"),
    ("reserved prefix only", "alpha",
     FM + "name: alpha\ndescription: d\nmetadata:\n  a: 1\n  cli-extended-zzz: x\n---\n", 6, "reserved"),
    ("no closing", "alpha", FM + "name: alpha\ndescription: d\n", 4, "no closing '---'"),
    ("indented line before any metadata key", "alpha",
     FM + "  foo: bar\nname: alpha\ndescription: d\n---\n", 2,
     "unsupported frontmatter syntax"),
]


@pytest.mark.parametrize(
    "label,name,text,line,message", CASES, ids=[case[0] for case in CASES]
)
def test_o7_invalid_sources_raise_skill_error(label, name, text, line, message):
    with pytest.raises(SkillError) as info:
        validate_skill_source(name, {"SKILL.md": text.encode()})
    shown = str(info.value)
    assert f"skill {name!r}" in shown
    assert message in shown
    if line is not None:
        assert f"line {line}:" in shown


def test_o7_structural_errors_without_line_numbers():
    with pytest.raises(SkillError, match=r"skill 'x': directory has no SKILL\.md$"):
        validate_skill_source("x", {"other.md": b""})
    with pytest.raises(SkillError, match="source must not contain .cli-extended-stamp.json"):
        validate_skill_source("alpha", {"SKILL.md": md("alpha").encode(), STAMP: b"{}"})
    with pytest.raises(SkillError, match="not valid UTF-8"):
        validate_skill_source("alpha", {"SKILL.md": b"---\n\xff\n---\n"})


@pytest.mark.parametrize("text", [
    md("alpha"),
    "---\nname: alpha\ndescription: 'single quoted'\n---\n",
    '---\nname: "alpha"\ndescription: "double quoted: with colon"\n---\n',
    "---\n\nname: alpha\n\ndescription: d\nmetadata:\n  author: me\n  v: '1'\n\n  x: \"y\"\n---\nbody",
    "---\nname: alpha\ndescription: d\nmetadata:\n---\n",
    "---\nname: alpha\ndescription: d\nlicense: MIT\nallowed-tools: Bash Read\n---\n",
    "---\nname: alpha\ndescription: d\nmetadata:\n  cli-extended: ok-no-trailing-hyphen\n---\n",
])
def test_o7_valid_sources_pass(text):
    assert validate_skill_source("alpha", {"SKILL.md": text.encode()}) is None


def test_o7_boundary_lengths_are_valid():
    name = "a" * 64
    assert validate_skill_source(
        name, {"SKILL.md": md(name, desc="d" * 1024).encode()}
    ) is None
    assert validate_skill_source("a", {"SKILL.md": md("a", desc="d").encode()}) is None
    assert validate_skill_source("a1-b2", {"SKILL.md": md("a1-b2").encode()}) is None


@pytest.mark.parametrize("name,path", [
    ("cmru-cli", REPO_ROOT / "cmru/.claude/skills/cmru-cli"),
    ("nyxloom-dispatch", REPO_ROOT / "nyxloom/.claude/skills/nyxloom-dispatch"),
])
def test_o7_real_repo_skills_validate_and_install(name, path, tmp_path, monkeypatch):
    assert (path / "SKILL.md").is_file(), path
    files = {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*")) if p.is_file()
    }
    assert validate_skill_source(name, files) is None
    package = make_pkg(tmp_path, monkeypatch, {name: files})
    dest = tmp_path / "dest"
    assert run(package, "install", "--dest", str(dest))[0] == 0
    installed = (dest / name / "SKILL.md").read_text()
    assert installed.startswith("---\nname: " + name + "\n")
    assert "  cli-extended-tool: mytool\n" in installed
    assert banner() in installed
    assert run(package, "check", "--dest", str(dest))[0] == 0


def test_nested_source_directories_hash_by_posix_relpath(tmp_path, monkeypatch):
    skill = {"SKILL.md": md("n"), "a/b/c.txt": "deep", "a.txt": "top"}
    package = make_pkg(tmp_path, monkeypatch, {"n": skill})
    dest = tmp_path / "dest"
    run(package, "install", "--dest", str(dest))
    side = json.loads((dest / "n" / STAMP).read_text())
    assert side["source_hash"] == source_hash(skill)
    assert sorted(side["files"]) == ["SKILL.md", "a.txt", "a/b/c.txt"]


# ----------------------------------------------------- review round 1


def test_installed_modes_follow_the_umask_not_mkdtemp(tmp_path, monkeypatch):
    package = make_pkg(tmp_path, monkeypatch, {
        "m": {"SKILL.md": md("m"), "sub/deep/f.txt": "x"},
    })
    dest = tmp_path / "dest"
    old = os.umask(0o022)
    try:
        assert run(package, "install", "--dest", str(dest))[0] == 0
        assert run(package, "install", "--dest", str(dest), version="2.0")[0] == 0
    finally:
        os.umask(old)
    assert (dest / "m").stat().st_mode & 0o777 == 0o755
    seen = 0
    for path in (dest / "m").rglob("*"):
        expected = 0o755 if path.is_dir() else 0o644
        assert path.stat().st_mode & 0o777 == expected, path
        seen += 1
    assert seen >= 5


def test_symlinked_skill_directories_are_unmanaged_and_never_followed(pkg, tmp_path):
    dest = tmp_path / "dest"
    real = tmp_path / "elsewhere"
    run(pkg, "install", "--dest", str(real))
    dest.mkdir()
    (dest / "alpha").symlink_to(real / "alpha", target_is_directory=True)
    (dest / "beta").symlink_to(tmp_path / "dangling")
    before = snapshot(real)
    assert states(run(pkg, "list", "--dest", str(dest))[1]) == ["unmanaged", "unmanaged"]
    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert (code, out) == (1, "")
    assert "without a cli-extended stamp" in err
    code, out, err = run(pkg, "uninstall", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"skipped alpha -> {dest} (unmanaged)",
        f"skipped beta -> {dest} (unmanaged)",
    ]
    assert (dest / "alpha").is_symlink() and (dest / "beta").is_symlink()
    assert snapshot(real) == before


def test_symlinked_orphan_candidates_are_ignored(pkg, tmp_path):
    dest = tmp_path / "dest"
    real = tmp_path / "elsewhere"
    run(pkg, "install", "--dest", str(real))
    (real / "alpha").rename(real / "ghost")
    dest.mkdir()
    (dest / "ghost").symlink_to(real / "ghost", target_is_directory=True)
    rows = skill_states(package=pkg, resource_dir="skills", tool="mytool",
                        version="1.0.0", destinations=[dest])
    assert [r[0] for r in rows] == ["alpha", "beta"]
    assert run(pkg, "uninstall", "--dest", str(dest))[0] == 0
    assert (real / "ghost" / "SKILL.md").is_file()


@pytest.mark.parametrize("hidden", ["__pycache__", ".git", "_private", ".hidden"])
def test_hidden_and_underscore_entries_in_resource_dir_are_skipped(tmp_path, monkeypatch, hidden):
    package = make_pkg(tmp_path, monkeypatch, {
        "ok": {"SKILL.md": md("ok")}, hidden: {"mod.pyc": b"\x00junk"},
    })
    dest = tmp_path / "dest"
    code, out, err = run(package, "install", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert out.splitlines() == [f"installed ok -> {dest}"]
    assert sorted(p.name for p in dest.iterdir()) == ["ok"]


def test_o7_empty_bom_and_crlf_sources_name_the_actual_cause():
    good = md("alpha")
    with pytest.raises(SkillError, match=r"SKILL\.md is empty"):
        validate_skill_source("alpha", {"SKILL.md": b""})
    with pytest.raises(SkillError, match="starts with a UTF-8 BOM"):
        validate_skill_source("alpha", {"SKILL.md": b"\xef\xbb\xbf" + good.encode()})
    with pytest.raises(SkillError, match="uses CRLF line endings"):
        validate_skill_source("alpha", {"SKILL.md": good.replace("\n", "\r\n").encode()})
    # a CRLF only in the body is still rejected: the file must be LF throughout
    with pytest.raises(SkillError, match="uses CRLF line endings"):
        validate_skill_source("alpha", {"SKILL.md": good.encode() + b"x\r\ny\n"})
    assert validate_skill_source("alpha", {"SKILL.md": good.encode()}) is None


def test_interrupted_install_leftovers_are_reported_and_cleaned(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    tmp_dir = dest / ".alpha.cli-extended-mytool-tmp-0123456789abcdef"
    old_dir = dest / ".beta.cli-extended-mytool-old-ffeeddccbbaa0011"
    old_file = dest / ".alpha.cli-extended-mytool-old-aaaaaaaaaaaaaaaa"
    unrelated = dest / ".ghost.cli-extended-mytool-tmp-9999999999999999"  # unowned
    other_tool = dest / ".alpha.cli-extended-othertool-tmp-0123456789abcdef"
    short_hex = dest / ".alpha.cli-extended-mytool-tmp-1"
    plain = dest / ".alpha.notes"
    write_tree(tmp_dir, {"SKILL.md": "half"})
    write_tree(old_dir, {"SKILL.md": "old"})
    old_file.write_text("f")
    write_tree(unrelated, {"x": "y"})
    write_tree(other_tool, {"x": "theirs"})
    write_tree(short_hex, {"x": "not ours by pattern"})
    plain.write_text("keep")

    code, out, err = run(pkg, "check", "--dest", str(dest))
    assert code == 1
    assert out.splitlines() == [
        f"{'current':<10} alpha  {dest}",
        f"{'current':<10} beta  {dest}",
        f"leftover {old_file}",
        f"leftover {tmp_dir}",
        f"leftover {old_dir}",
        f"leftover {unrelated}",
    ]
    assert str(other_tool) not in out and str(short_hex) not in out
    assert "4 leftover temporary path(s) from an interrupted install" in err
    assert "skill(s) are not current" not in err
    code, out, _ = run(pkg, "list", "--dest", str(dest))
    assert code == 0 and out.count("leftover ") == 4
    code, out, _ = run(pkg, "check", "--dest", str(dest), "--json")
    payload = json.loads(out)
    assert code == 1
    assert payload["leftovers"] == [str(p) for p in (old_file, tmp_dir, old_dir, unrelated)]
    assert [s["state"] for s in payload["skills"]] == ["current", "current"]

    code, out, _ = run(pkg, "install", "--dest", str(dest), "--dry-run")
    assert code == 0
    assert out.splitlines()[:4] == [
        f"would remove leftover {old_file}",
        f"would remove leftover {tmp_dir}",
        f"would remove leftover {old_dir}",
        f"would remove leftover {unrelated}",
    ]
    assert tmp_dir.exists() and unrelated.exists()
    code, out, _ = run(pkg, "install", "--dest", str(dest))
    assert code == 0
    assert out.splitlines()[:4] == [
        f"removed leftover {old_file}",
        f"removed leftover {tmp_dir}",
        f"removed leftover {old_dir}",
        f"removed leftover {unrelated}",
    ]
    assert not (tmp_dir.exists() or old_dir.exists() or old_file.exists() or unrelated.exists())
    assert other_tool.is_dir() and short_hex.is_dir() and plain.read_text() == "keep"
    code, out, err = run(pkg, "check", "--dest", str(dest))
    assert (code, err) == (0, "")
    assert "leftover" not in out


def test_uninstall_removes_unowned_leftovers_and_dry_run_keeps_them(pkg, tmp_path):
    dest = tmp_path / "dest"
    run(pkg, "install", "--dest", str(dest))
    run(pkg, "uninstall", "--dest", str(dest))
    ghost = dest / ".gone.cli-extended-mytool-old-0123456789abcdef"
    write_tree(ghost, {"x": "y"})
    assert run(pkg, "check", "--dest", str(dest))[0] == 1
    code, out, _ = run(pkg, "uninstall", "--dest", str(dest), "--dry-run")
    assert code == 0
    assert out.splitlines()[0] == f"would remove leftover {ghost}"
    assert ghost.exists()
    code, out, _ = run(pkg, "uninstall", "--dest", str(dest))
    assert code == 0
    assert out.splitlines()[0] == f"removed leftover {ghost}"
    assert not ghost.exists()
    assert states(run(pkg, "check", "--dest", str(dest))[1]) == ["absent", "absent"]


def test_another_tools_leftovers_are_invisible_in_all_four_verbs(pkg, tmp_path):
    dest = tmp_path / "dest"
    theirs = dest / ".alpha.cli-extended-othertool-tmp-0123456789abcdef"
    prefix_tool = dest / ".alpha.cli-extended-mytool-x-tmp-0123456789abcdef"
    write_tree(theirs, {"x": "theirs"})
    write_tree(prefix_tool, {"x": "tool named mytool-x"})
    for verb in ("check", "list"):
        code, out, err = run(pkg, verb, "--dest", str(dest), "--json")
        assert json.loads(out)["leftovers"] == []
        assert "leftover" not in err
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    assert run(pkg, "uninstall", "--dest", str(dest))[0] == 0
    assert (theirs / "x").read_text() == "theirs"
    assert (prefix_tool / "x").read_text() == "tool named mytool-x"
    # and symmetrically the other tool only sees its own
    code, out, _ = run(pkg, "list", "--dest", str(dest), "--json", tool="othertool")
    assert json.loads(out)["leftovers"] == [str(theirs)]


def test_leftover_symlink_is_unlinked_not_followed(pkg, tmp_path):
    dest = tmp_path / "dest"
    keep = tmp_path / "keep"
    write_tree(keep, {"f": "x"})
    dest.mkdir()
    link = dest / ".alpha.cli-extended-mytool-tmp-0123456789abcdef"
    link.symlink_to(keep, target_is_directory=True)
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    assert not link.is_symlink()
    assert (keep / "f").read_text() == "x"


# ------------------------------------------- W8b: filesystem errors are domain failures

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permission bits"
)


def assert_domain_failure(code, out, err, name, dest):
    assert code == 1
    assert out == ""
    error_lines = [line for line in err.splitlines() if line.startswith("[ERROR]")]
    assert len(error_lines) == 1
    assert error_lines[0].startswith(f"[ERROR] {name} -> {dest}: ")
    assert "unexpected" not in err.lower()
    assert "Traceback" not in err


@needs_non_root
def test_w8b_install_into_read_only_destination_is_exit_one(pkg, tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    dest.chmod(0o555)
    try:
        code, out, err = run(pkg, "install", "--dest", str(dest))
    finally:
        dest.chmod(0o755)
    assert_domain_failure(code, out, err, "alpha", dest)
    assert "Permission denied" in err
    assert list(dest.iterdir()) == []


def test_w8b_install_under_a_regular_file_is_exit_one(pkg, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    dest = blocker / "sub"
    code, out, err = run(pkg, "install", "--dest", str(dest))
    assert_domain_failure(code, out, err, "alpha", dest)
    assert blocker.read_text() == "not a directory"


@needs_non_root
def test_w8b_uninstall_where_removal_fails_is_exit_one(pkg, tmp_path):
    dest = tmp_path / "dest"
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    dest.chmod(0o555)
    try:
        code, out, err = run(pkg, "uninstall", "--dest", str(dest))
    finally:
        dest.chmod(0o755)
    assert_domain_failure(code, out, err, "alpha", dest)
    assert (dest / "beta" / "SKILL.md").is_file()


@needs_non_root
def test_w8b_leftover_removal_failure_is_exit_one(pkg, tmp_path):
    dest = tmp_path / "dest"
    assert run(pkg, "install", "--dest", str(dest))[0] == 0
    leftover = dest / ".alpha.cli-extended-mytool-tmp-0123456789abcdef"
    write_tree(leftover, {"x": "y"})
    dest.chmod(0o555)
    try:
        code, out, err = run(pkg, "install", "--dest", str(dest))
    finally:
        dest.chmod(0o755)
    assert_domain_failure(code, out, err, leftover.name, dest)
    assert leftover.is_dir()


def test_w8b_non_filesystem_bugs_still_report_as_unexpected(pkg, tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError("a bug, not a filesystem error")

    monkeypatch.setattr(skills_module, "_write_atomic", broken)
    with pytest.raises(ValueError, match="a bug"):
        run(pkg, "install", "--dest", str(tmp_path / "dest"))


# ------------------------------------------------ W8b: one ordering for list and install


def test_w8b_dry_run_install_and_list_share_one_order(tmp_path, monkeypatch, isolated_home):
    package = make_pkg(tmp_path, monkeypatch, {
        n: {"SKILL.md": md(n)} for n in ("zeta", "alpha", "mid")
    })
    code, listed, _ = run(package, "list")
    assert code == 0
    claude = isolated_home / ".claude" / "skills"
    agents = isolated_home / ".agents" / "skills"
    expected = [
        (agents, "alpha"), (agents, "mid"), (agents, "zeta"),
        (claude, "alpha"), (claude, "mid"), (claude, "zeta"),
    ]
    assert listed.splitlines() == [
        f"{'absent':<10} {name}  {dest}" for dest, name in expected
    ]
    code, planned, _ = run(package, "install", "--dry-run")
    assert code == 0
    assert planned.splitlines() == [f"would install {name} -> {dest}" for dest, name in expected]


@pytest.mark.parametrize("verb", ["install", "uninstall"])
def test_w8c_dry_run_notice_survives_quiet(pkg, tmp_path, verb):
    dest = tmp_path / "dest"
    code, out, err = run(pkg, verb, "--dest", str(dest), "--dry-run", "--quiet")
    assert code == 0
    assert "Dry run: no changes made." in err
    assert not dest.exists()


# ------------------------------------ CLI-EXT-26: delegates inherit global options


def _registry_with_global(package):
    from cli_extended import OptionSpec

    identity = CliIdentity("MYTOOL", "1.0.0", "My tool", "mytool")
    registry = CliRegistry(
        identity, prog="mytool", description="My tool.",
        global_options=(OptionSpec(
            ("--tag-prefix",), "prefix", parser_kwargs={"action": "store_true"},
        ),),
    )
    registry.register(VerbSpec("own", description="own", handler=lambda *_: 0))
    register_skills_verbs(registry, package=package)
    return registry


@pytest.mark.parametrize("verb", ["install", "uninstall", "check", "list"])
def test_cx26_skills_verbs_accept_the_consumer_global_option(pkg, tmp_path, verb):
    app = _registry_with_global(pkg).build()
    out, err = io.StringIO(), io.StringIO()
    code = app.run(
        argv=["skills", verb, "--tag-prefix", "--dest", str(tmp_path / "d")],
        stdout=out, stderr=err,
    )
    assert "unrecognized arguments" not in err.getvalue()
    assert code in (0, 1)


def test_cx26_surface_has_no_incomplete_syntax_and_lists_the_global(pkg):
    from cli_extended.surface import export_cli_surface

    surface = export_cli_surface(_registry_with_global(pkg).build())
    assert not [r for r in surface["incomplete"] if "inherited global" in r]
    leaves = {r["id"].rsplit("/", 1)[-1]: r for r in surface["routes"]
              if r["id"].startswith("route:entrypoint:mytool/skills/")}
    assert set(leaves) == {"install", "uninstall", "check", "list"}
    for name, route in leaves.items():
        flags = {f for a in route["actions"] for f in a.get("flags", ())}
        assert "--tag-prefix" in flags, name
