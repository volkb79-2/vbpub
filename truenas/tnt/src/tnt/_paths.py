"""Where tnt finds ISOs, base images and VM state: asked of the repo path config of cowtree (RP-03).

No tnt source or test hardcodes ``downloads/`` or ``vm-lab/``: they call the functions below, which ask
``cowtree.repo_paths`` (``libraries/cowtree``) for the named paths ``iso``, ``vm-images``, ``vm-lab`` (per project) and
``downloads``. The answer is the CoW subvolume under ``$MDT_COW_ROOT/<repo>/...`` when the CoW area is valid and the
folder exists, else the plain folder of this checkout. ``MDT_COW_ROOT`` is the only environment input.

The repository root is derived from this file's location (``<repo>/truenas/tnt/src/tnt/_paths.py``). A copy of tnt
outside a checkout has no repo path config and refuses with one line naming that file; nothing guesses another
location. The file name is cowtree's (``cowtree.repo_paths.CONFIG_NAME``): ask ``config_name()``, never write it down.
Tests monkeypatch the public functions (or ``REPO_ROOT`` and ``COWTREE_SOURCE``) in this one place.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from .errors import Refused

#: The project directory, relative to the repository root, that the ``vm-lab`` entry of the repo path config is asked for.
PROJECT = "truenas/tnt"
#: Used only in the refusal for a missing cowtree module, where the real name cannot be asked.
_FALLBACK_CONFIG_NAME = "cowtree.toml"
_PARENTS = Path(__file__).resolve().parents
REPO_ROOT = _PARENTS[min(4, len(_PARENTS) - 1)]
COWTREE_SOURCE = REPO_ROOT / "libraries" / "cowtree" / "src"


def _repo_paths_module() -> ModuleType:
    """The ``cowtree.repo_paths`` module of this checkout; a missing one is a refusal."""
    module = COWTREE_SOURCE / "cowtree" / "repo_paths.py"
    if not module.is_file():
        raise Refused("repo-paths", f"{module} not found: the path resolver (libraries/cowtree) is missing from this "
                                    f"checkout, so the repo path config ({_FALLBACK_CONFIG_NAME}) cannot be read")
    if str(COWTREE_SOURCE) not in sys.path:
        sys.path.insert(0, str(COWTREE_SOURCE))
    import cowtree.repo_paths

    return cowtree.repo_paths


def config_name() -> str:
    """The file name of the repo path config, as cowtree names it."""
    try:
        return _repo_paths_module().CONFIG_NAME
    except Refused:
        return _FALLBACK_CONFIG_NAME


def resolve(name: str, project: str | None = None) -> Path:
    """The folder of the named path of the repo path config (plain or CoW, as the resolver decides)."""
    repo_paths = _repo_paths_module()
    from cowtree.errors import CowError

    config = REPO_ROOT / repo_paths.CONFIG_NAME
    if not config.is_file():
        try:
            repo_paths.load_repo_paths(config.parent)
        except CowError as exc:
            if (config.parent / repo_paths.LEGACY_CONFIG_NAME).is_file():  # a repo with only the pre-rename file
                raise Refused("repo-paths", str(exc)) from None  # cowtree's own migration hint, one line
        raise Refused("repo-paths", f"{config} not found: ISO, image and VM-state folders come from the repository's "
                                    f"{repo_paths.CONFIG_NAME}, so run tnt from a vbpub checkout")

    try:
        return repo_paths.load_repo_paths(config.parent).resolve(name, project=project).path
    except CowError as exc:
        raise Refused("repo-paths", f"{config}: cannot resolve {name!r}: {exc}") from None


def iso_dir() -> Path:
    """The ISO folder (name ``iso``): installer ISOs with their ``.sha256``."""
    return resolve("iso")


def vm_images_dir() -> Path:
    """The shared base-image folder (name ``vm-images``): cloud images and golden disks with their ``.sha256``."""
    return resolve("vm-images")


def downloads_dir() -> Path:
    """The folder of other large immutable inputs (name ``downloads``)."""
    return resolve("downloads")


def vm_state_root() -> Path:
    """The VM state folder of tnt (name ``vm-lab``): one ``<vm>/`` directory per VM."""
    return resolve("vm-lab", project=PROJECT)
