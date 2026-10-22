"""The two shipped devcontainer.json variants must stay identical except for a documented set of keys.

`templates/devcontainer.json` is the per-repo variant; `templates/host-folder/devcontainer.json` is the
one-cockpit-for-several-repos variant, configured from a host folder. Both also carry the same opt-in,
commented-out btrfs CoW lines that `docs/BTRFS-COW.md` quotes.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "templates"))
import initialize_container_environment as bootstrap  # noqa: E402

PER_REPO_PATH = ROOT / "templates" / "devcontainer.json"
HOST_FOLDER_PATH = ROOT / "templates" / "host-folder" / "devcontainer.json"
PER_REPO_TEXT = PER_REPO_PATH.read_text(encoding="utf-8")
HOST_FOLDER_TEXT = HOST_FOLDER_PATH.read_text(encoding="utf-8")
COW_DOC = (ROOT / "docs" / "BTRFS-COW.md").read_text(encoding="utf-8")

COW_MOUNT = (
    '"source=/srv/devcontainers-cow/${localEnv:USER},target=/srv/devcontainers-cow/${localEnv:USER},'
    'type=bind,bind-propagation=rslave"'
)
COW_ENV = '"MDT_COW_ROOT": "/srv/devcontainers-cow/${localEnv:USER}"'

# The ONLY places the host-folder variant may differ from the per-repo one.
HOST_FOLDER_ONLY_KEYS = {"workspaceMount"}
DIFFERING_KEYS = {"name", "workspaceFolder", "runArgs"}
CONFIG_MOUNT = "source=${localWorkspaceFolder}/.devcontainer,target=/run/mdt/devcontainer,type=bind,readonly"
CONFIG_ENV = {"MDT_DEVCONTAINER_DIR": "/run/mdt/devcontainer"}


def _doc(text: str) -> dict:
    return bootstrap._parse_jsonc_object(text)


def _uncomment_cow(text: str) -> str:
    """What an adopter does: remove the leading `// ` from the two opt-in lines."""
    out = []
    for line in text.splitlines():
        if re.match(r'^\s*// ("source=/srv/devcontainers-cow/|"MDT_COW_ROOT")', line):
            line = line.replace("// ", "", 1)
        out.append(line)
    return "\n".join(out)


def test_both_variants_parse_as_jsonc_objects() -> None:
    assert _doc(PER_REPO_TEXT)
    assert _doc(HOST_FOLDER_TEXT)


def test_host_folder_variant_differs_only_in_the_documented_keys() -> None:
    per_repo, host = _doc(PER_REPO_TEXT), _doc(HOST_FOLDER_TEXT)
    assert set(host) - set(per_repo) == HOST_FOLDER_ONLY_KEYS
    assert set(per_repo) - set(host) == set()
    for key in set(per_repo) - DIFFERING_KEYS:
        if key in ("mounts", "containerEnv"):
            continue
        assert host[key] == per_repo[key], f"{key} drifted between the variants"


def test_host_folder_variant_adds_exactly_the_config_mount_and_hook_dir() -> None:
    per_repo, host = _doc(PER_REPO_TEXT), _doc(HOST_FOLDER_TEXT)
    assert [m for m in host["mounts"] if m not in per_repo["mounts"]] == [CONFIG_MOUNT]
    assert [m for m in per_repo["mounts"] if m not in host["mounts"]] == []
    extra_env = {k: v for k, v in host["containerEnv"].items() if per_repo["containerEnv"].get(k) != v}
    assert extra_env == CONFIG_ENV
    assert all(host["containerEnv"][k] == v for k, v in per_repo["containerEnv"].items())


def test_host_folder_identity_is_the_cockpits_not_a_repos() -> None:
    host = _doc(HOST_FOLDER_TEXT)
    args = host["runArgs"]
    assert args[args.index("--name") + 1] == args[args.index("--hostname") + 1] == host["name"]
    assert "${localWorkspaceFolderBasename}" not in host["name"]
    assert host["workspaceFolder"] == "/workspaces"
    assert host["workspaceMount"].endswith("target=/workspaces,type=bind,consistency=cached")


def test_host_folder_runargs_equal_the_per_repo_ones_apart_from_identity() -> None:
    per_repo, host = _doc(PER_REPO_TEXT), _doc(HOST_FOLDER_TEXT)

    def scrub(args: list) -> list:
        out, skip = [], False
        for i, arg in enumerate(args):
            if skip:
                skip = False
                continue
            if arg in ("--name", "--hostname"):
                skip = True
                continue
            out.append(arg)
        return out

    assert scrub(per_repo["runArgs"]) == scrub(host["runArgs"])
    assert "--cgroup-parent=dev-interactive.slice" in host["runArgs"]


def test_the_hook_dir_env_matches_the_config_mount_target() -> None:
    host = _doc(HOST_FOLDER_TEXT)
    target = re.search(r"target=([^,]+)", CONFIG_MOUNT)[1]
    assert host["containerEnv"]["MDT_DEVCONTAINER_DIR"] == target


def test_cow_lines_ship_commented_out_in_both_variants() -> None:
    for text in (PER_REPO_TEXT, HOST_FOLDER_TEXT):
        doc = _doc(text)
        assert not any("devcontainers-cow" in m for m in doc["mounts"])
        assert "MDT_COW_ROOT" not in doc["containerEnv"]
        assert f"// {COW_MOUNT}," in text
        assert f"// {COW_ENV}," in text


def test_uncommenting_the_cow_lines_yields_one_mount_and_one_matching_env_var() -> None:
    for text in (PER_REPO_TEXT, HOST_FOLDER_TEXT):
        doc = _doc(_uncomment_cow(text))
        cow = [m for m in doc["mounts"] if "devcontainers-cow" in m]
        assert cow == [COW_MOUNT.strip('"')]
        target = re.search(r"target=([^,]+)", cow[0])[1]
        assert doc["containerEnv"]["MDT_COW_ROOT"] == target
        assert target == "/srv/devcontainers-cow/${localEnv:USER}"


def test_the_guide_quotes_exactly_the_template_lines() -> None:
    assert COW_MOUNT in COW_DOC
    assert COW_ENV in COW_DOC


def test_cow_mount_is_scoped_to_the_user_level_only() -> None:
    # Binding the whole filesystem would expose every user's area; the default is <mount>/<user>.
    assert 'source=/srv/devcontainers-cow,' not in PER_REPO_TEXT
    assert 'source=/srv/devcontainers-cow,' not in HOST_FOLDER_TEXT


def _links_and_paths(doc_path: Path) -> list[Path]:
    text = doc_path.read_text(encoding="utf-8")
    found = []
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if not re.match(r"[a-z]+:", target):
            found.append((doc_path.parent / target).resolve())
    for rel in re.findall(r"`((?:host-setup/scripts|templates)/[\w./-]+)`", text):
        if rel.endswith("/"):
            continue
        found.append((ROOT / rel).resolve())
    return found


def test_every_file_and_link_the_guide_cites_exists() -> None:
    for doc in (ROOT / "docs" / "BTRFS-COW.md", ROOT / "templates" / "host-folder" / "README.md"):
        for path in _links_and_paths(doc):
            assert path.exists(), f"{doc.name} cites {path}"


def test_the_host_scripts_the_guide_names_are_executable_and_lint_clean_in_syntax() -> None:
    import subprocess

    for name in ("mdt-cow-init-user.sh", "mdt-cow-selfcheck.sh"):
        script = ROOT / "host-setup" / "scripts" / name
        assert script.stat().st_mode & 0o111, f"{name} is not executable"
        assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0
        assert f"host-setup/scripts/{name}" in COW_DOC
