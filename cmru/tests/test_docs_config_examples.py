"""The adopter-facing CMRU config pair remains loadable by the shipped reader."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from cmru import config
from cmru.config import load_forge_config
from cmru.version_config import SOURCE_FIELDS


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md"]


def _slug(heading: str) -> str:
    value = re.sub(r"^#+\s*", "", heading.strip()).lower()
    value = re.sub(r"[^\w\s-]", "", value)
    return value.replace(" ", "-")


def test_every_documented_toml_example_is_current_toml_and_versioned(tmp_path):
    block_counts = {}
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
        block_counts[document.name] = len(blocks)
        for index, block in enumerate(blocks):
            parsed = tomllib.loads(block)
            assert parsed.get("schema_version") == 1, document
            if (
                set(parsed) == {"schema_version", "github"}
                and set(parsed["github"]) == {"token"}
            ):
                secret_path = tmp_path / f"{document.stem}-{index}.secret.toml"
                secret_path.write_text(block, encoding="utf-8")
                assert config._read_secret_document(secret_path) == parsed
    # The consumer document owns the only complete config pair. README and the
    # secret-file example; README and the design guide link to those rather than
    # publishing partial TOML fragments that cannot pass the installed reader.
    assert block_counts == {"README.md": 0, "DESIGN-GUIDE.md": 0, "CONSUMERS.md": 3}


def test_runtime_vocabulary_and_workspace_contract_are_documented():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for value in ("kind = \"none\"", "kind = \"ciu\"", "per Git family", "workspace-id"):
        assert value in corpus
    for document in (ROOT / "README.md", ROOT / "docs" / "SPEC.md", ROOT / "docs" / "RELEASE-TRANSACTIONS.md"):
        text = document.read_text(encoding="utf-8")
        assert "cmru-build-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>" in text


def test_version_policy_vocabulary_is_documented():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for document in DOCS:
        normalized = " ".join(document.read_text(encoding="utf-8").lower().split())
        assert "timestamp exactly at the age cutoff is eligible" in normalized, document
    for source_type in SOURCE_FIELDS:
        assert f".{source_type}" in corpus
    for value in ('mode = "single"', 'mode = "aligned"'):
        assert value in corpus
    for value in ('selection = "semver"', 'selection = "rolling"', 'Docker-Content-Digest'):
        assert value in corpus
    assert 'vnd.docker.reference.type = "attestation-manifest"' in corpus
    assert "commit time rather than the proxy publication time" in corpus
    assert "age-window refusal" in corpus
    for field in (
        "age_window_days", "constraint", "version", "reason", "expires", "resolved",
        "pypi_extras", "requirements_files", "digest",
    ):
        assert field in corpus
    for value in ('scope = "shipped"', 'scope = "all"'):
        assert value in corpus


def test_estate_project_steps_use_the_bound_cmru_handler_not_the_module_form():
    # BG-04/REL-07: `python3 -m cmru.handlers` bypasses the bound launcher.
    repo = ROOT.parent
    configs = sorted([*repo.glob("*/cmru.toml"), *repo.glob("libraries/*/cmru.toml")])
    assert ROOT / "cmru.toml" in configs
    for path in configs:
        text = path.read_text(encoding="utf-8")
        assert "cmru.handlers" not in text, f"{path} still calls python -m cmru.handlers"
    scaffold = (ROOT / "src" / "cmru" / "scaffold.py").read_text(encoding="utf-8")
    template = (ROOT / "src" / "cmru" / "templates" / "project-wheel.toml").read_text(
        encoding="utf-8",
    )
    assert "cmru.handlers" not in scaffold and "cmru.handlers" not in template
    assert '"cmru", "handler", "wheel-build"' in template


def test_cmru_own_env_pythonpath_keeps_the_worktree_root_and_no_cli_extended_source():
    document = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
    roots = document["env"]["PYTHONPATH"].split(":")
    assert roots == ["src", "../libraries/worktree/src"]


def test_assay_and_release_gate_split_rigor_without_empty_release_mutation():
    lane = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))["lanes"]["cmru"]
    assert lane["rigor"] == ["R0", "R1", "R3"]
    # BG-03: fail-fast stops pytest-cov writing coverage.json and hides the failure.
    assert not any(arg.startswith("--maxfail") for arg in lane["argv"])

    assert lane["isolation"]["snapshot_selection"] == "repository-minus-unsafe-symlinks"
    assert lane["judge"]["coverage"]["artifact"] == "coverage.json"
    assert lane["judge"]["source_roots"] == ["src"]
    declared_base = lane["judge"]["base"]
    assert isinstance(declared_base, str) and declared_base.startswith("cmru-v")
    assert lane["env_passthrough"] == ["PATH"]
    assert "mutation" not in lane["judge"]
    assert lane["judge"]["canary"]["mechanism"] == "import-break"

    for document in (ROOT / "docs" / "SPEC.md", ROOT / "docs" / "DESIGN-GUIDE.md"):
        assert f'`{lane["judge"]["base"]}`' in document.read_text(encoding="utf-8")

    gate = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    gate_command = " ".join(gate["lanes"]["gate"]["argv"])
    assert gate_command == (
        "python3 {worktree}/cmru/tools/run_release_gate.py --worktree {worktree}"
    )
    # KI-62: the provisional lane is `gate` plus exactly the postponement option.
    provisional = gate["lanes"]["gate-provisional"]
    assert provisional["argv"] == gate["lanes"]["gate"]["argv"] + ["--postpone-mutation", "KI-62"]
    assert provisional["environment"] == gate["lanes"]["gate"]["environment"]
    assert "KI-62" in (ROOT / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text(encoding="utf-8")
    release_gate = (ROOT / "tools" / "run_release_gate.py").read_text(encoding="utf-8")
    for name in ("installed-wheel", "assay", "coverage", "mutation", "canary"):
        assert f'"{name}"' in release_gate
    assert '"enroll"' not in release_gate  # moved to ciu/run-gate.toml (O4)
    assert "enroll" not in gate["lanes"]
    assert "_mask_secret_overlays(" in release_gate
    assert "RUN_GATE_EXTRA_MOUNTS" in release_gate
    mutation_command = " ".join(gate["lanes"]["mutation"]["argv"])
    assert "--cov" not in mutation_command
    assert "--cov-fail-under" not in mutation_command
    assert gate["lanes"]["mutation"]["environment"] == "cmru-mutation"
    assert gate["lanes"]["mutation"]["required_env"] == ["CMRU_ASSAY_BASELINE_FACTS"]
    mutation_environment = gate["environments"]["cmru-mutation"]
    assert "CMRU_ASSAY_BASELINE_FACTS" in mutation_environment["forward_env"]
    assert "CGROUP_PARENT_DEV_BACKGROUND" in mutation_environment["forward_env"]
    assert (
        "BASE=$(/opt/tester-venv/bin/python tools/check_assay_baseline.py "
        "--skip-evidence)"
    ) in mutation_command
    assert (ROOT / "tools" / "check_assay_baseline.py").is_file()
    assert 'if [ -z "$BASE" ]; then' in mutation_command
    assert "explicit skip evidence recorded" in mutation_command
    assert "exit 0" in mutation_command
    assert mutation_command.index('if [ -z "$BASE" ]; then') < mutation_command.index(
        "tools/mutation_campaign.py",
    )
    assert "git diff --quiet" not in mutation_command
    baseline_checker = (ROOT / "tools" / "check_assay_baseline.py").read_text(encoding="utf-8")
    assert '"reason": "no-changed-source"' in baseline_checker
    assert 'PROJECT_ROOT / ".assay" / "mutation-cmru.json"' in baseline_checker
    assert "assay_git.resolve_base" in baseline_checker
    assert "assay_git.base_resolution_mode" in baseline_checker
    assert "assay_git.run(" in baseline_checker
    assert 'os.environ.get("CMRU_ASSAY_BASELINE_FACTS"' in baseline_checker
    assert "latest_ancestor_release_tag" in baseline_checker
    assert '"head": head_commit' in baseline_checker
    assert 'source_roots = judge["source_roots"]' in baseline_checker
    assert "relative_to(repo_top)" in baseline_checker
    assert "subprocess.run" not in baseline_checker
    assert "path.is_symlink()" in baseline_checker
    assert "os.replace(temporary, path)" in baseline_checker
    assert "_remove_stale_skip_evidence" in baseline_checker
    baseline_preparer = (ROOT / "tools" / "prepare_assay_baseline.py").read_text(encoding="utf-8")
    assert "load_config(config_path, validate_dependencies=False)" in baseline_preparer
    assert "_remote_tag_commits" in baseline_preparer
    assert "run_remote_git(" in baseline_preparer
    assert '"remote_tag_commits": remote_tags' in baseline_preparer
    assert '"head_release_tags": head_release_tags' in baseline_preparer
    assert '"schema_version": 3' in baseline_preparer
    assert "GITHUB_PUSH_PAT" not in baseline_preparer
    assert "remote_head_commit != head_commit" in baseline_checker
    assert "--require-candidates" in mutation_command

    # BG-03: only the mutation lane is fail-fast.
    assert "--maxfail=1" in " ".join(gate["lanes"]["mutation"]["argv"])
    for name in ("coverage", "canary"):
        assert "--maxfail" not in " ".join(gate["lanes"][name]["argv"])
    assert "--junitxml=junit-coverage.xml" in " ".join(gate["lanes"]["coverage"]["argv"])
    assert "junit-coverage.xml" in gate["lanes"]["coverage"]["artifacts"]

    coverage_command = " ".join(gate["lanes"]["coverage"]["argv"])
    source_assay_install = (
        "/opt/tester-venv/bin/python -m pip install --quiet "
        "--disable-pip-version-check --no-input --no-deps --no-build-isolation "
        "--editable ../assay"
    )
    assert source_assay_install in coverage_command
    assert coverage_command.index(source_assay_install) < coverage_command.index("-m pytest tests")

    canary_command = " ".join(gate["lanes"]["canary"]["argv"])
    assert source_assay_install in canary_command
    assert canary_command.index(source_assay_install) < canary_command.index(
        "tools/coverage_canary.py",
    )
    assert canary_command.index("tools/coverage_canary.py") < canary_command.index(
        "-m pytest tests",
    )

    for document in (
        ROOT / "README.md",
        ROOT / "docs" / "SPEC.md",
        ROOT / "docs" / "DESIGN-GUIDE.md",
        ROOT / "docs" / "CONSUMERS.md",
    ):
        text = document.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        assert "--maxfail=1" in text
        assert "120 seconds" in text or "120-second" in text
        assert "progress" in text.lower()
        assert "highest-version published" in normalized
        assert "cmru-v*" in text
        assert "selected tag" in normalized.lower()
        assert "Assay R1 base" in text
        assert "latest published CMRU release" in normalized
        assert "latest published tag may be one of the verified tags at head" in normalized.lower()
        assert "effective comparison commit" in normalized.lower()
        assert "first parent" in normalized.lower()
        assert "full ancestry" in normalized.lower()
        assert "missing tag" in normalized.lower()
        assert "mismatch" in normalized.lower()
        assert "fails the gate" in normalized.lower()
        assert "next release candidate" in normalized.lower()
        assert "every local cmru release tag at head" in normalized.lower()
        assert "excludes every local cmru release tag at head" in normalized.lower()


def test_release_gate_credential_boundary_is_documented_in_all_user_guides():
    for document in DOCS:
        text = " ".join(document.read_text(encoding="utf-8").split())
        assert "publisher-token and extra-mount variables" in text
        assert "gate credential design" in text or document.name == "DESIGN-GUIDE.md"
        assert "if restoration fails" in text.lower()
        assert "private backup" in text.lower()
    consumer_text = " ".join(DOCS[-1].read_text(encoding="utf-8").split())
    assert "individual tester component lane bypasses this host wrapper" in consumer_text


def test_cross_document_links_resolve():
    headings: dict[Path, set[str]] = {}
    failures: list[str] = []
    for document in DOCS:
        headings[document] = {
            _slug(line) for line in document.read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("#")
        }
    for document in DOCS:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if target.startswith(("http", "#")):
                continue
            path_part, _, anchor = target.partition("#")
            resolved = (document.parent / path_part).resolve()
            if not resolved.exists():
                failures.append(f"{document}: {target}")
            elif anchor:
                if resolved not in headings:
                    headings[resolved] = {
                        _slug(line) for line in resolved.read_text(encoding="utf-8").splitlines()
                        if line.lstrip().startswith("#")
                    }
                if anchor not in headings[resolved]:
                    failures.append(f"{document}: {target}")
    assert failures == []


def test_git_version_for_local_tag_inspection_is_documented_across_user_guides():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    readme_flat = " ".join(readme.split())
    design_flat = " ".join(design.split())
    consumers_flat = " ".join(consumers.split())

    assert "Git 2.43 or newer" in readme_flat
    assert "local-tag-inspection-requires-git-243" in readme_flat
    assert "each fetched `origin/main` snapshot" in readme_flat
    assert "all tagged" in readme_flat and "before dispatching any family" in readme_flat
    assert "passes those exact commits to" in readme_flat
    assert "fetches `origin/main` again and refuses if" in readme_flat
    assert "never copied into the snapshot" in readme_flat
    assert "targets must retain the `cmru.toml` filename" in readme_flat
    assert "from the selected repository path" in readme_flat
    assert "CMRU refuses to resume when a tag push may have landed" in readme_flat
    assert "same-name origin tag pointing to another object is a conflict" in readme_flat
    assert "selected `--config` link may use another basename" in readme_flat
    assert "checks the committed candidate's tag" in readme_flat
    assert "Git 2.43" in design_flat and "git show-ref --exists" in design_flat
    assert "exit 2 means absent; exit 1 means lookup error" in design_flat
    assert "before creating the candidate worktree" in design_flat
    assert "reads each" in design_flat and "tag policy from that snapshot" in design_flat
    assert "before dispatching" in design_flat
    assert "Each launcher receives the checked commit" in design_flat
    assert "reads the selected project's config path from that snapshot" in design_flat
    assert "private inherited pipe" in design_flat
    assert "fetches `origin/main` again under its" in design_flat
    assert "Project config symlinks are" in design_flat
    assert "preserves the link path when the caller's checkout" in design_flat
    assert "must remain named `cmru.toml`" in design_flat
    assert "tag policy from the committed retained candidate" in design_flat
    assert "Resume checks attempted release tags" in design_flat
    assert "release-plan refusal" in design_flat
    assert "different ref object is a conflict" in design_flat
    assert "classify it by the resolved target's canonical config filename" in design_flat
    assert "origin-absence proof" in design_flat
    assert "Git 2.43 or newer" in consumers
    assert "git --version" in consumers
    assert "local-tag-inspection-requires-git-243" in consumers
    assert "before creating its candidate" in consumers
    assert "tag policy from its fetched `origin/main` snapshot" in consumers_flat
    assert "before starting any family release" in consumers_flat
    assert "fetches again and refuses if `origin/main` moved" in consumers_flat
    assert "never copied into the snapshot" in consumers_flat
    assert "resolved target must keep the `cmru.toml` filename" in consumers_flat
    assert "preserves the selected repository link path" in consumers_flat
    assert "checks tag-push attempts and recorded release results" in consumers_flat
    assert "keeps the existing worktree and origin backup branch" in consumers_flat
    assert "same tag name at a different object" in consumers_flat
    assert "selected `--config` link" in consumers_flat
    assert "freshly generated annotated tag object" in consumers_flat
    assert "reads `project.release.git_tag` from the committed retained candidate" in consumers_flat


def test_consumers_central_config_example_is_complete_and_loadable(tmp_path: Path):
    document = (
        Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md"
    ).read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", document, flags=re.DOTALL)
    assert len(blocks) >= 2

    (tmp_path / "example-wheel").mkdir()
    (tmp_path / "example-wheel" / "cmru.toml").write_text(blocks[0], encoding="utf-8")
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text(blocks[1], encoding="utf-8")

    config = load_forge_config(central, require_orchestration=True)
    assert config.orchestration is not None
    assert config.orchestration.execution_mode == "project-first"
    assert config.cleanup is not None
    assert config.projects["example-wheel"].name == "example-wheel"
    assert config.versions["age_window_days"] == 14
    assert config.versions["discovery"]["scope"] == "shipped"
    assert config.versions["targets"]["pypi.requests"]["pypi"]["name"] == "requests"
    project_versions = config.projects["example-wheel"].versions
    assert project_versions["age_window_days"] == 21
    assert project_versions["discovery"]["scope"] == "shipped"
    assert project_versions["targets"]["pypi.requests"]["mode"] == "single"
    assert project_versions["targets"]["oci.node"]["oci"]["selection"] == "rolling"
    assert project_versions["targets"]["go.golang.org.x.text"]["go"]["module"] == "golang.org/x/text"


def test_registered_projects_track_their_shared_pwmcp_runtime_image():
    workspace = ROOT.parent
    for project in ("topos", "nyxloom"):
        config = tomllib.loads((workspace / project / "cmru.toml").read_text(encoding="utf-8"))
        target = config["versions"]["targets"]["oci.pwmcp"]
        assert target["mode"] == "single"
        assert target["oci"]["image"] == "ghcr.io/volkb79-2/pwmcp"
        assert target["oci"]["tag"] == "{version}"
