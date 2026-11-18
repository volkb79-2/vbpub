"""The golden-image source VM follows the estate storage layout (AGENTS.md)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

GOLDEN_ROOT = Path(__file__).resolve().parents[1]
TRUENAS_ROOT = GOLDEN_ROOT.parent
spec = importlib.util.spec_from_file_location("golden_step1_layout", GOLDEN_ROOT / "step1-install-prepare-truenas.py")
assert spec is not None and spec.loader is not None
STEP1 = importlib.util.module_from_spec(spec)
sys.modules["golden_step1_layout"] = STEP1
spec.loader.exec_module(STEP1)

VM_LAB = TRUENAS_ROOT / "vm-lab"


def _defaults(monkeypatch, tmp_path: Path) -> Path:
    """The parsed defaults of step 1 with the resolver's vm-lab answer replaced by *tmp_path*."""
    monkeypatch.setattr(STEP1.repo_paths_access, "vm_state_root", lambda: tmp_path / "resolved-vm-lab")
    return tmp_path / "resolved-vm-lab" / "golden-source-27-20GiB"


def test_source_vm_state_is_one_directory_of_the_resolved_vm_lab_folder(monkeypatch, tmp_path) -> None:
    source = _defaults(monkeypatch, tmp_path)
    args = STEP1.make_parser().parse_args(["--api-key-expiry-days", "30"])
    for path in (args.vm_disk, args.vm_vars, args.ssh_private_key, args.known_hosts, args.qemu_log):
        assert path.parent == source, path
    assert STEP1.vm_dir() == source


def test_qmp_default_socket_is_computed_from_the_resolved_vm_lab_folder(monkeypatch, tmp_path) -> None:
    import qmp

    source = _defaults(monkeypatch, tmp_path)
    assert qmp.default_socket() == source / "qmp.sock"


def test_step1_source_names_no_literal_vm_folder() -> None:
    for name in ("step1-install-prepare-truenas.py", "qmp.py"):
        text = (GOLDEN_ROOT / name).read_text()
        assert "artifacts/vm-lab" not in text and '"vm-lab"' not in text, name


def test_missing_repo_path_config_is_a_step_refusal_naming_the_file(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(STEP1.repo_paths_access, "REPO_ROOT", tmp_path)
    assert STEP1.main(["--api-key-expiry-days", "30"]) == 2
    err = capsys.readouterr().err
    assert str(tmp_path / STEP1.repo_paths_access.config_name()) in err and len(err.strip().splitlines()) == 1


def test_built_outputs_stay_in_the_project_artifacts_directory() -> None:
    assert STEP1.DEFAULT_IMAGE.parent == GOLDEN_ROOT / "artifacts"
    assert STEP1.DEFAULT_SECRETS.parent == GOLDEN_ROOT / "artifacts"


def test_installed_source_disk_is_a_golden_base_and_never_cache_unsafe() -> None:
    assert STEP1.SOURCE_DISK_CACHE in {"writeback", "none"}


def test_plain_mode_vm_lab_folder_is_the_committed_project_placeholder(monkeypatch) -> None:
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    assert STEP1.vm_dir() == VM_LAB / "golden-source-27-20GiB"


def test_vm_lab_placeholder_is_committed_with_readme_and_ignore_all_rule() -> None:
    assert (VM_LAB / "README.md").is_file()
    assert (VM_LAB / ".gitignore").read_text().splitlines() == ["*", "!.gitignore", "!README.md"]
