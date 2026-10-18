"""Tests for the finalizer's only home write: the comment-only ~/.zshrc stub.

D7: every image default is wired system-wide, so the home stays empty. The one
tool that cannot be configured that way is zsh's new-user wizard, which runs when
none of .zshenv/.zprofile/.zshrc/.zlogin exists.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import finalize_container_environment as finalize  # noqa: E402


def _tree(root: Path) -> dict[str, tuple[int, bytes | None]]:
    """Path -> (mode, content) for everything under root, links not followed."""
    entries: dict[str, tuple[int, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        mode = path.lstat().st_mode
        entries[relative] = (mode, path.read_bytes() if path.is_file() else None)
    return entries


def test_fresh_home_receives_only_a_comment_only_zshrc_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    finalize.setup_zshrc_stub()

    assert [path.name for path in home.iterdir()] == [".zshrc"]
    stub = (home / ".zshrc").read_text(encoding="utf-8")
    code = [line for line in stub.splitlines() if line.strip() and not line.startswith("#")]
    assert code == []


def test_second_run_leaves_the_home_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    finalize.setup_zshrc_stub()
    first = _tree(home)
    finalize.setup_zshrc_stub()

    assert _tree(home) == first


@pytest.mark.parametrize("existing", [".zshrc", ".zshenv", ".zprofile", ".zlogin"])
def test_any_user_zsh_startup_file_suppresses_the_stub_and_is_never_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / existing).write_text("# user content\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    finalize.setup_zshrc_stub()

    assert [path.name for path in home.iterdir()] == [existing]
    assert (home / existing).read_text(encoding="utf-8") == "# user content\n"


def test_a_dangling_symlink_counts_as_a_user_startup_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").symlink_to(tmp_path / "not-there")
    monkeypatch.setenv("HOME", str(home))

    finalize.setup_zshrc_stub()

    assert (home / ".zshrc").is_symlink()
    assert not (tmp_path / "not-there").exists()


def test_generic_steps_no_longer_seed_copy_or_link_anything_else_into_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retired steps (config copies, tool links, compatibility symlinks) are gone."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(finalize, "repair_docker_socket_relay", lambda: None)
    monkeypatch.setattr(finalize, "setup_buildkit_builder", lambda *, required=False: True)
    monkeypatch.setattr(finalize, "verify_base_tools", lambda: None)

    assert finalize.run_generic_steps({"env_type": "local"}) is True

    assert sorted(path.name for path in home.rglob("*")) == [".zshrc"]
    for retired in (
        "setup_customization_root",
        "setup_shell_bootstrap",
        "setup_image_home_links",
        "setup_tool_env_links",
        "setup_editor_links",
    ):
        assert not hasattr(finalize, retired), retired


def test_the_user_name_comes_from_the_uid_not_from_a_terminal_or_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first runtime run died in os.getlogin() (no controlling terminal, USER unset)."""
    import pwd

    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)

    def no_terminal() -> str:
        raise OSError(-25, "Inappropriate ioctl for device")

    monkeypatch.setattr(finalize.os, "getlogin", no_terminal)
    monkeypatch.setattr(finalize.os, "getuid", lambda: 4242)
    monkeypatch.setattr(
        finalize.pwd, "getpwuid", lambda uid: pwd.struct_passwd(("vscode", "x", uid, 4242, "", "/h", "/bin/sh"))
    )

    assert finalize._current_user_name() == "vscode"
    assert finalize.detect_environment()["user"] == "vscode"


def test_a_uid_without_a_passwd_entry_is_reported_by_number_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finalize.os, "getuid", lambda: 4343)

    def missing(_uid: int):
        raise KeyError("getpwuid(): uid not found")

    monkeypatch.setattr(finalize.pwd, "getpwuid", missing)

    assert finalize._current_user_name() == "uid-4343"
