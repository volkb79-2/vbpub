"""Where the TrueNAS scripts keep ISOs, base images and VM state: asked of the repo-root ``vm-lab.toml`` (RP-03).

No script or test of ``scripts/truenas`` hardcodes ``downloads/`` or ``vm-lab/``: they call the functions below, which
ask ``cowtree.repo_paths`` (``libraries/cowtree``) for the named paths ``iso``, ``vm-images``, ``vm-lab`` (per project)
and ``downloads``. The answer is the CoW subvolume under ``$MDT_COW_ROOT/<repo>/...`` when the CoW area is valid and
the folder exists, else the plain folder of this checkout. ``MDT_COW_ROOT`` is the only environment input.

The repository root is derived from this file's location. A copy of the scripts outside a checkout has no
``vm-lab.toml`` and refuses with one line naming that file; nothing guesses another location. Tests monkeypatch the
public functions (or ``REPO_ROOT`` and ``COWTREE_SOURCE``) in this one place.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: The project directory, relative to the repository root, that the ``vm-lab`` entry of ``vm-lab.toml`` is asked for.
PROJECT = "scripts/truenas"
CONFIG_NAME = "vm-lab.toml"
#: The installed golden-image source VM (golden-iso); its folder is ``<vm-lab folder>/<this name>``.
GOLDEN_SOURCE_VM = "golden-source-27-20GiB"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COWTREE_SOURCE = REPO_ROOT / "libraries" / "cowtree" / "src"


class RepoPathsError(RuntimeError):
    """The shared paths cannot be resolved: the message is one line naming the missing or invalid file."""


def resolve(name: str, project: str | None = None) -> Path:
    """The folder of the named path of ``vm-lab.toml`` (plain or CoW, as the resolver decides)."""
    config = REPO_ROOT / CONFIG_NAME
    if not config.is_file():
        raise RepoPathsError(
            f"{config} not found: ISO, image and VM-state folders come from the repository's {CONFIG_NAME}, "
            "so run this from a vbpub checkout"
        )
    module = COWTREE_SOURCE / "cowtree" / "repo_paths.py"
    if not module.is_file():
        raise RepoPathsError(f"{module} not found: the path resolver (libraries/cowtree) is missing from this checkout")
    if str(COWTREE_SOURCE) not in sys.path:
        sys.path.insert(0, str(COWTREE_SOURCE))
    from cowtree.errors import CowError
    from cowtree.repo_paths import load_repo_paths

    try:
        return load_repo_paths(config.parent).resolve(name, project=project).path
    except CowError as exc:
        raise RepoPathsError(f"{config}: cannot resolve {name!r}: {exc}") from None


def iso_dir() -> Path:
    """The ISO folder (name ``iso``): installer ISOs with their ``.sha256``."""
    return resolve("iso")


def vm_images_dir() -> Path:
    """The shared base-image folder (name ``vm-images``)."""
    return resolve("vm-images")


def downloads_dir() -> Path:
    """The folder of other large immutable inputs (name ``downloads``)."""
    return resolve("downloads")


def vm_state_root() -> Path:
    """The VM state folder of ``scripts/truenas`` (name ``vm-lab``): one ``<vm>/`` directory per VM."""
    return resolve("vm-lab", project=PROJECT)


def golden_source_dir() -> Path:
    """The state folder of the golden-image source VM."""
    return vm_state_root() / GOLDEN_SOURCE_VM
