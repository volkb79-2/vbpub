"""The login-shell PATH hook merges; /etc/profile is patched only when it is exactly stock."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_etc_profile  # noqa: E402

HOOK = ROOT / "customization" / "path-hook.sh"
ENTRIES = "/usr/local/share/mdt-cockpit/path-entries"
PREFIX = "/opt/mdt/bin:/opt/mdt/venv/bin:/opt/mdt/npm/bin:/srv/pwmcp-instance/bin"
SUFFIX = "/home/vscode/.local/bin"
SHELLS = [shell for shell in ("bash", "sh", "zsh") if shutil.which(shell)]
SHELL_PATH = {shell: shutil.which(shell) for shell in SHELLS}


@pytest.fixture()
def hook(tmp_path: Path) -> Path:
    entries = tmp_path / "path-entries"
    entries.write_text(f"{PREFIX}\n{SUFFIX}\n", encoding="utf-8")
    copy = tmp_path / "path-hook.sh"
    text = HOOK.read_text(encoding="utf-8")
    assert ENTRIES in text
    copy.write_text(text.replace(ENTRIES, str(entries)), encoding="utf-8")
    return copy


def _run(shell: str, hook: Path, path: str, times: int = 1) -> tuple[list[str], str]:
    script = "".join(f'. "{hook}"; echo "PATH=$PATH"; ' for _ in range(times))
    result = subprocess.run(
        [SHELL_PATH[shell], "-c", script], env={"PATH": path, "HOME": "/nonexistent"}, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return [line.removeprefix("PATH=") for line in result.stdout.splitlines()], result.stderr


def test_the_hook_exists_for_the_shells_under_test() -> None:
    assert "bash" in SHELLS and "sh" in SHELLS


@pytest.mark.parametrize("shell", SHELLS)
def test_a_runtime_added_entry_survives_and_keeps_its_place_among_the_other_entries(shell: str, hook: Path) -> None:
    (merged,), _ = _run(shell, hook, "/opt/probe/bin:/usr/local/bin:/usr/bin:/opt/other/bin:/bin")

    assert merged == f"{PREFIX}:/opt/probe/bin:/usr/local/bin:/usr/bin:/opt/other/bin:/bin:{SUFFIX}"


@pytest.mark.parametrize("shell", SHELLS)
def test_managed_entries_are_moved_wherever_they_occur_and_appear_exactly_once_in_order(shell: str, hook: Path) -> None:
    messy = f"/usr/bin:{SUFFIX}:/opt/mdt/npm/bin:/opt/probe/bin:/opt/mdt/bin:/bin:/opt/mdt/venv/bin"

    (merged,), _ = _run(shell, hook, messy)

    assert merged == f"{PREFIX}:/usr/bin:/opt/probe/bin:/bin:{SUFFIX}"
    entries = merged.split(":")
    for managed in PREFIX.split(":") + [SUFFIX]:
        assert entries.count(managed) == 1


@pytest.mark.parametrize("shell", SHELLS)
def test_sourcing_the_hook_twice_changes_nothing(shell: str, hook: Path) -> None:
    (first, second), _ = _run(shell, hook, "/opt/probe/bin:/usr/bin:/opt/mdt/venv/bin:/bin", times=2)

    assert first == second


@pytest.mark.parametrize("shell", SHELLS)
def test_entries_with_spaces_and_glob_characters_are_kept_verbatim(shell: str, hook: Path) -> None:
    (merged,), _ = _run(shell, hook, "/opt/my tools/bin:/opt/*/bin:/usr/bin")

    assert merged == f"{PREFIX}:/opt/my tools/bin:/opt/*/bin:/usr/bin:{SUFFIX}"


@pytest.mark.parametrize("shell", SHELLS)
def test_an_empty_path_yields_just_the_managed_entries_and_the_patched_profile_supplies_the_default(
    shell: str, hook: Path
) -> None:
    (merged,), _ = _run(shell, hook, "")

    if shell == "zsh":  # zsh substitutes its own default for an empty PATH before the hook runs
        assert merged.startswith(PREFIX + ":") and merged.endswith(":" + SUFFIX)
    else:
        assert merged == f"{PREFIX}:{SUFFIX}"


@pytest.mark.parametrize("shell", SHELLS)
def test_a_missing_entries_file_is_reported_and_leaves_path_alone(shell: str, tmp_path: Path) -> None:
    text = HOOK.read_text(encoding="utf-8").replace(ENTRIES, str(tmp_path / "gone"))
    copy = tmp_path / "hook.sh"
    copy.write_text(text, encoding="utf-8")

    (merged,), stderr = _run(shell, copy, "/usr/bin:/bin")

    assert merged == "/usr/bin:/bin"
    assert "mdt-cockpit: " in stderr and "gone is missing; PATH left as the login shell set it" in stderr


# ── /etc/profile: patched only when it is exactly the stock Debian block ──────

STOCK_PROFILE = (
    "# /etc/profile: system-wide .profile file for the Bourne shell (sh(1))\n"
    "\n" + patch_etc_profile.STOCK + "\n"
    'if [ "${PS1-}" ]; then\n  :\nfi\n'
)


def test_the_stock_profile_is_patched_on_exactly_the_two_assignments() -> None:
    patched = patch_etc_profile.patch(STOCK_PROFILE)

    assert patched.count("${PATH:-") == 2
    assert 'PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"' in patched
    assert 'PATH="${PATH:-/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games}"' in patched
    # nothing else changed: undoing the two substitutions gives the original back
    restored = patched.replace("${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    restored = restored.replace("${PATH:-/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games}", "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games")
    assert restored == STOCK_PROFILE


@pytest.mark.parametrize("uid_branch", ["root", "user"])
def test_the_patched_profile_keeps_an_inherited_path_and_defaults_an_empty_one(tmp_path: Path, uid_branch: str) -> None:
    profile = tmp_path / "profile"
    profile.write_text(patch_etc_profile.patch(STOCK_PROFILE).replace('"$(id -u)" -eq 0', '1 -eq ' + ("1" if uid_branch == "root" else "0")), encoding="utf-8")

    def path_after(env_path: str | None) -> str:
        env = {"PATH": env_path or ""}  # an EMPTY PATH (dash would otherwise invent its own default)
        out = subprocess.run(
            ["/bin/sh", "-c", f'. "{profile}"; echo "$PATH"'], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()
        return out

    assert path_after("/opt/probe/bin:/usr/bin") == "/opt/probe/bin:/usr/bin"
    default = path_after(None)
    assert default == (patch_etc_profile.ROOT_DEFAULT if uid_branch == "root" else patch_etc_profile.USER_DEFAULT)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("/usr/games", "/usr/games:/extra"),                 # a changed default
        lambda text: text.replace("export PATH\n", ""),                                # block altered
        lambda text: "# nothing like it\n",                                            # another distribution
        lambda text: text + patch_etc_profile.STOCK,                                   # the block twice
        lambda text: patch_etc_profile.patch(text),                                    # already patched
    ],
    ids=["changed-default", "no-export", "unrelated-file", "block-twice", "already-patched"],
)
def test_a_profile_that_is_not_exactly_stock_fails_the_build_and_is_left_untouched(tmp_path: Path, mutate) -> None:
    profile = tmp_path / "profile"
    original = mutate(STOCK_PROFILE)
    profile.write_text(original, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "patch_etc_profile.py"), str(profile)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1 and "[ERROR]" in result.stderr
    assert profile.read_text(encoding="utf-8") == original


def test_the_command_patches_a_stock_file_in_place(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.write_text(STOCK_PROFILE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "patch_etc_profile.py"), str(profile)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0
    assert profile.read_text(encoding="utf-8") == patch_etc_profile.patch(STOCK_PROFILE)


def test_the_dockerfile_patches_etc_profile_with_this_script_and_writes_the_entries_file() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/patch_etc_profile.py /tmp/patch_etc_profile.py" in dockerfile
    assert "python3 /tmp/patch_etc_profile.py /etc/profile" in dockerfile
    assert '"${MDT_PATH_PREFIX}" "${MDT_PATH_SUFFIX}" > "${share_dir}/path-entries"' in dockerfile
    assert "!scripts/patch_etc_profile.py" in (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "${share_dir}/path\"" not in dockerfile, "the full-PATH snapshot file is gone"
