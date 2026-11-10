"""Exercise T3 bundle imports without changing the shared source-checkout link."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "sync-from-devcontainer"
BRANCH = "t3/tool-v1"


def git(*args, env=None):
    return subprocess.run(
        ["git", *map(str, args)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def bundle_for(tmp_path: Path):
    source = tmp_path / "source"
    incoming = tmp_path / "home" / "incoming"
    incoming.mkdir(parents=True)
    git("init", "-q", source)
    git("-C", source, "config", "user.name", "test")
    git("-C", source, "config", "user.email", "test@example.invalid")
    git("-C", source, "checkout", "-qb", BRANCH)
    (source / "README.md").write_text("fixture\n")
    git("-C", source, "add", "README.md")
    git("-C", source, "commit", "-qm", "fixture")
    bundle = incoming / "t3-tool-v1-r2.bundle"
    git("-C", source, "bundle", "create", bundle, f"refs/heads/{BRANCH}")
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return bundle, env


def import_bundle(tmp_path, *, preserve_current=True, existing_link=True):
    bundle, env = bundle_for(tmp_path)
    home = Path(env["HOME"])
    src = home / "src"
    src.mkdir(parents=True, exist_ok=True)
    previous = tmp_path / "previous-checkout"
    previous.mkdir()
    current = src / "vbpub-current"
    if existing_link:
        current.symlink_to(previous)
    args = ["bash", str(SCRIPT)]
    if preserve_current:
        args.append("--no-current-link")
    args.extend([str(bundle), BRANCH])
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    return result, home, current, previous


def test_t3_import_keeps_an_existing_vbpub_current_symlink_unchanged(tmp_path):
    result, home, current, previous = import_bundle(tmp_path)
    assert result.returncode == 0, result.stderr
    assert current.is_symlink()
    assert os.readlink(current) == str(previous)
    checkout = home / "checkouts" / result.stdout.split("CHECKOUT=", 1)[1].splitlines()[0].rsplit("/", 1)[-1]
    assert checkout.is_dir()
    assert (checkout / "README.md").read_text() == "fixture\n"


def test_t3_import_does_not_create_vbpub_current_when_it_was_absent(tmp_path):
    result, home, current, _ = import_bundle(tmp_path, existing_link=False)
    assert result.returncode == 0, result.stderr
    assert not current.exists() and not current.is_symlink()
    assert len(list((home / "checkouts").iterdir())) == 1


def test_default_import_still_updates_vbpub_current_for_non_t3_callers(tmp_path):
    result, home, current, _ = import_bundle(tmp_path, preserve_current=False)
    assert result.returncode == 0, result.stderr
    checkout = home / "checkouts" / result.stdout.split("CHECKOUT=", 1)[1].splitlines()[0].rsplit("/", 1)[-1]
    assert current.is_symlink()
    assert os.readlink(current) == str(checkout)
