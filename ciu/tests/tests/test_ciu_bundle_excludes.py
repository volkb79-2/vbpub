"""CIU-175: the non-thin push sync never ships `.ciu/`, `ciu.env` or `.git`, and
applies the host's `bundle_excludes` (SPEC S14.2/S14.6)."""
from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from ciu import activate
from ciu import transport_ssh as tssh

# `tssh.subprocess` is the global module: keep the real run() before patching it.
_REAL_RUN = subprocess.run


def test_bundle_excludes_for_default_is_the_mandatory_set():
    assert activate.bundle_excludes_for({}) == [".git", ".ciu", "ciu.env", "vol-*"]


def test_bundle_excludes_for_merges_host_list_without_duplicates():
    got = activate.bundle_excludes_for({"bundle_excludes": [".git", "ciu.instance.generated.toml"]})
    assert got == [".git", ".ciu", "ciu.env", "vol-*", "ciu.instance.generated.toml"]


@pytest.mark.parametrize("bad", ["x", [1], {"a": 1}])
def test_bundle_excludes_for_rejects_a_non_string_list(bad):
    with pytest.raises(ValueError, match="list of strings"):
        activate.bundle_excludes_for({"bundle_excludes": bad})


def _tree(root: Path) -> None:
    (root / ".ciu").mkdir(parents=True)
    (root / ".ciu" / "ghcr-token").write_text("SECRET")
    (root / "stack" / ".ciu").mkdir(parents=True)
    (root / "stack" / ".ciu" / "pw").write_text("SECRET2")
    (root / "stack" / "ciu.env").write_text("X=1")
    (root / "ciu.env").write_text("DOCKER_GID=1")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")
    (root / "ciu.instance.generated.toml").write_text("control-host-instance")
    (root / "stack" / "compose.yml").write_text("services: {}")


def test_tarball_drops_secrets_at_any_depth_and_host_excludes(tmp_path):
    src = tmp_path / "src"
    _tree(src)
    ball = activate.make_tarball(
        str(src), excludes=activate.bundle_excludes_for(
            {"bundle_excludes": ["ciu.instance.generated.toml"]}))
    try:
        with tarfile.open(ball) as tar:
            names = sorted(n.removeprefix("./") for n in tar.getnames())
    finally:
        Path(ball).unlink()
    assert "stack/compose.yml" in names
    assert not any(".ciu" in n.split("/") or n.endswith("ciu.env") or n.startswith(".git")
                   or n == "ciu.instance.generated.toml" for n in names), names


def _generated_rsync(tmp_path, monkeypatch, host_extra=None):
    """Run the real ssh_sync argv builder with subprocess captured."""
    key = tmp_path / "id"
    key.write_text("KEY")
    host = {"ssh_host": "h", "ssh_user": "u", "ssh_key": str(key), "known_host": "k"}
    host.update(host_extra or {})
    seen: list[list[str]] = []
    monkeypatch.setattr(tssh.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(tssh, "_known_hosts_file", lambda cfg: None)
    assert tssh.ssh_sync(host, "SRC", "DST", config={}, repo_root=tmp_path,
                         excludes=activate.bundle_excludes_for(host)) == 0
    return seen[0]


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")
def test_generated_rsync_command_protects_target_instance_file_and_never_ships_secrets(
    tmp_path, monkeypatch
):
    cmd = _generated_rsync(tmp_path, monkeypatch,
                           {"bundle_excludes": ["ciu.instance.generated.toml"]})
    # Rewrite only the transport: drop `-e <ssh>` and point source/destination at temp dirs.
    i = cmd.index("-e")
    argv = cmd[:i] + [str(tmp_path / "src") + "/", str(tmp_path / "dst") + "/"]
    assert argv[0] == "rsync" and "--exclude" in argv
    src, dst = tmp_path / "src", tmp_path / "dst"
    _tree(src)
    dst.mkdir()
    (dst / "ciu.instance.generated.toml").write_text("target-instance")
    (dst / "ciu.env").write_text("target-env")
    _REAL_RUN(argv, check=True)
    assert (dst / "stack" / "compose.yml").read_text() == "services: {}"
    assert (dst / "ciu.instance.generated.toml").read_text() == "target-instance"
    assert (dst / "ciu.env").read_text() == "target-env"
    assert not (dst / ".ciu").exists()
    assert not (dst / "stack" / ".ciu").exists()
    assert not (dst / "stack" / "ciu.env").exists()
    assert not (dst / ".git").exists()


# ---------------------------------------------------------------------------
# F2/F6: the tar fallback applies rsync's pattern semantics; vol-* never ships
# ---------------------------------------------------------------------------

_FIXTURE_FILES = [
    "keep.txt", "sub/keep.txt", "sub/.git/c", "sub/.git", ".git/config", "wt/.git",
    ".ciu/secrets/t", "a/.ciu/secrets/t", "b/.ciu", "ciu.env", "x/ciu.env",
    "vol-svc-data/db", "stack/vol-svc-data/db", "app.log", "deep/er/x.log", "node_modules/m",
    "deep/node_modules/m", "build/out", "deep/build/out", "dironly/f", "deep/dironly",
    "ab1", "ac2", "ad3", "star*name", "spaced name/f", "x/y/z", "q/x/y/z",
]


_PATTERNS = [
    [],
    list(activate.ALWAYS_BUNDLE_EXCLUDES),
    [*activate.ALWAYS_BUNDLE_EXCLUDES, "*.log", "node_modules", "/build", "dironly/", "ab?",
     "a[bc]*", "a[!b]3", "x/y/z", "**/y/z", "star\\*name", "spaced name", "deep/**", "q/**/z"],
]


@pytest.mark.skipif(not (shutil.which("rsync") and shutil.which("tar")), reason="rsync/tar missing")
@pytest.mark.parametrize("patterns", _PATTERNS, ids=["none", "mandatory", "mandatory+host-globs"])
def test_tar_fallback_ships_the_same_file_set_as_real_rsync(tmp_path, patterns):
    src = tmp_path / "src"
    src.mkdir()
    for rel in _FIXTURE_FILES:
        if rel in ("sub/.git",):  # sub/.git is a directory (sub/.git/c)
            continue
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(rel)
    (src / "link.log").symlink_to("keep.txt")
    (src / "c").mkdir()
    (src / "c" / ".ciu").symlink_to("/etc")
    (src / "deep" / "dironly").unlink()
    (src / "deep" / "dironly").mkdir()
    (src / "deep" / "dironly" / "g").write_text("g")

    dst = tmp_path / "dst"
    dst.mkdir()
    cmd = ["rsync", "-a"]
    for ex in patterns:
        cmd += ["--exclude", ex]
    subprocess.run([*cmd, f"{src}/", f"{dst}/"], check=True)
    via_rsync = sorted(
        str(p.relative_to(dst)) for p in dst.rglob("*")
    )

    ball = activate.make_tarball(str(src), excludes=patterns)
    try:
        with tarfile.open(ball) as tar:
            via_tar = sorted(n.removeprefix("./") for n in tar.getnames() if n != ".")
    finally:
        Path(ball).unlink()

    assert via_tar == via_rsync


def test_tarball_drops_nested_git_and_vol_dirs_and_ciu_symlinks(tmp_path):
    root = tmp_path / "r"
    (root / "sub" / ".git").mkdir(parents=True)
    (root / "sub" / ".git" / "c").write_text("x")
    (root / "vol-a-b").mkdir()
    (root / "vol-a-b" / "k").write_text("x")
    (root / "k.txt").write_text("x")
    (root / ".ciu").symlink_to("k.txt")
    ball = activate.make_tarball(str(root), excludes=list(activate.ALWAYS_BUNDLE_EXCLUDES))
    try:
        with tarfile.open(ball) as tar:
            names = sorted(n.removeprefix("./") for n in tar.getnames())
    finally:
        Path(ball).unlink()
    assert names == [".", "k.txt", "sub"]


def test_glob_regex_edge_cases():
    rx = activate._glob_regex
    assert rx("[abc").match("[abc")           # unterminated bracket is literal
    assert rx("trail\\").match("trail\\")   # a lone trailing backslash is literal
    assert rx("a*").match("abc") and not rx("a*").match("a/c")
    assert rx("a**").match("a/b/c")


# ---------------------------------------------------------------------------
# F4 on the activate side: `~` expansion and bundle_dir validation
# ---------------------------------------------------------------------------

def test_remote_path_word_expands_home_and_quotes_the_rest():
    word = activate.remote_path_word
    assert word("/opt/ciu") == "/opt/ciu"
    assert word("/opt/a b") == "'/opt/a b'"
    assert word("~") == '"$HOME"'
    assert word("~/ciu x") == "\"$HOME\"'/ciu x'"
    with pytest.raises(ValueError, match="~user"):
        word("~bob/x")
    with pytest.raises(ValueError, match="'p' must be a non-empty string"):
        word("", "'p'")


def test_bundle_dir_for_defaults_and_validates():
    assert activate.bundle_dir_for({}) == "/opt/ciu/current"
    assert activate.bundle_dir_for({"bundle_dir": "~/ciu"}) == "~/ciu"
    with pytest.raises(ValueError, match="whitespace"):
        activate.bundle_dir_for({"bundle_dir": "/a b"})


def test_scp_fallback_expands_a_tilde_bundle_dir(tmp_path, monkeypatch):
    (tmp_path / "f").write_text("x")
    sent = []
    monkeypatch.setattr(activate, "ssh_exec", lambda cfg, argv, **kw: sent.append(argv[0]) or 0)
    monkeypatch.setattr(activate, "scp_file", lambda *a, **k: 0)
    rc = activate._push_scp({}, str(tmp_path), "~/ciu/", config={}, repo_root=tmp_path, excludes=[])
    assert rc == 0
    assert sent == [
        'mkdir -p "$HOME"/ciu',
        'tar xzf "$HOME"/ciu/.ciu_bundle.tar.gz -C "$HOME"/ciu && rm -f "$HOME"/ciu/.ciu_bundle.tar.gz',
    ]


def test_push_bundle_refuses_a_bundle_dir_with_whitespace_before_sending(tmp_path, monkeypatch):
    monkeypatch.setattr(activate, "ssh_sync", lambda *a, **k: pytest.fail("must not sync"))
    with pytest.raises(ValueError, match="whitespace"):
        activate.push_bundle({}, str(tmp_path), "/a b", config={}, repo_root=tmp_path)


def test_run_activation_expands_a_tilde_bundle_dir(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(activate, "ssh_exec", lambda cfg, argv, **kw: sent.append(argv[0]) or 0)
    activate.run_activation({"activate": "sh a.sh"}, "apply", config={}, repo_root=tmp_path,
                            bundle_dir="~/ciu")
    assert sent == ['cd "$HOME"/ciu && sh a.sh apply']
