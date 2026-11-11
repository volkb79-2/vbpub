"""The adopter-facing CMRU config pair remains loadable by the shipped reader."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path

from cmru import config
from cmru.config import load_forge_config
from cmru.version_config import SOURCE_FIELDS


ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md"]
KI143_BRANCH = "cmru-release-20261109_071957-example-ab12cd"
KI143_TAG = "example-v1.2.3"


def _slug(heading: str) -> str:
    value = re.sub(r"^#+\s*", "", heading.strip()).lower()
    value = re.sub(r"[^\w\s-]", "", value)
    return value.replace(" ", "-")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _ki143_cleanup_script(
    consumers: str, *, repo: Path, candidate: Path, branch: str, tag: str,
) -> str:
    section = consumers.split("### Recovering candidates without scope metadata", 1)[1]
    section = section.split("\n### ", 1)[0]
    match = re.search(r"```bash\n(.*?)```", section, flags=re.DOTALL)
    assert match is not None
    script = match.group(1)
    values = {
        'repo="/absolute/path/to/repository-checkout"': f"repo={shlex.quote(str(repo))}",
        'candidate="/absolute/path/from/the-release-error"': (
            f"candidate={shlex.quote(str(candidate))}"
        ),
        'branch="cmru-release-20261109_071957-example-ab12cd"': f"branch={shlex.quote(branch)}",
        'tag="example-v1.2.3"': f"tag={shlex.quote(tag)}",
    }
    for placeholder, value in values.items():
        assert placeholder in script
        script = script.replace(placeholder, value, 1)
    return script


def _ki143_repository(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "--initial-branch=main"],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "config", "user.name", "CMRU docs test")
    _git(repo, "config", "user.email", "cmru-docs-test@example.invalid")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "base")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo, origin


def _ki143_candidate(repo: Path, branch: str, path: Path | None = None) -> Path:
    candidate = path or repo / ".worktrees" / branch
    candidate.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(candidate), "HEAD")
    return candidate


def _ki143_branch_exists(repo: Path, branch: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    ).returncode == 0


def _run_ki143_cleanup(
    consumers: str, *, repo: Path, candidate: Path, branch: str, tag: str,
) -> subprocess.CompletedProcess[str]:
    script = _ki143_cleanup_script(
        consumers, repo=repo, candidate=candidate, branch=branch, tag=tag,
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            break
    return result


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
    # Its fourth block is the `oci-image-release` push-step excerpt (KI-78); its fifth is
    # the `bundle.toml` `[archive] name_template` contract-name example (KI-89), which
    # `test_consumers_bundle_name_template_example_is_the_contract_name` loads with the
    # shipped archive-template validator.
    # A sixth block is the `[github] token_source` credential-source example (KI-94).
    assert block_counts == {"README.md": 0, "DESIGN-GUIDE.md": 0, "CONSUMERS.md": 6}


def test_consumers_bundle_name_template_example_is_the_contract_name():
    from cmru.bundle import _validate_archive_template
    from cmru.release import bundle_asset_name

    text = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    blocks = [
        tomllib.loads(b) for b in re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
    ]
    (example,) = [b for b in blocks if "archive" in b]
    template = example["archive"]["name_template"]
    _validate_archive_template(template)
    assert template.format(version="1.2.3") == bundle_asset_name("myproj", "1.2.3", ".tar.xz")


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
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert "source roots MUST be unchanged between them" in spec

    assay_config = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))["lanes"]
    lane = assay_config["cmru"]
    assert lane["rigor"] == ["R0", "R1", "R3"]
    # R1 needs the complete baseline run to produce its whole-target coverage judgment.
    assert not any(arg.startswith("--maxfail") for arg in lane["argv"])

    assert lane["isolation"]["snapshot_selection"] == "repository-minus-unsafe-symlinks"
    assert lane["judge"]["source_roots"] == ["src"]
    assert lane["judge"]["mode"] == "whole_target"
    assert "base" not in lane["judge"]
    assert lane["judge"]["coverage"]["artifact"] == "coverage.json"
    assert lane["env_passthrough"] == ["PATH"]
    assert "mutation" not in lane["judge"]
    assert lane["judge"]["canary"]["mechanism"] == "import-break"

    for name, rigor in (
        ("cmru-first-release", ["R0", "R1", "R3"]),
        ("cmru-first-release-r2", ["R0", "R1", "R2", "R3"]),
    ):
        first_lane = assay_config[name]
        assert first_lane["rigor"] == rigor
        assert first_lane["judge"]["mode"] == "whole_target"
        assert "base" not in first_lane["judge"]
        assert first_lane["judge"]["coverage"]["artifact"] == "coverage.json"

    t1_lane = assay_config["cmru-r0-r1"]
    assert t1_lane["rigor"] == ["R0", "R1"]
    assert t1_lane["judge"]["mode"] == "whole_target"
    assert t1_lane["judge"]["targets"] == lane["judge"]["targets"]
    assert t1_lane["judge"]["fail_under"] == 100.0
    assert t1_lane["judge"]["coverage"]["artifact"] == "coverage.json"
    assert "canary" not in t1_lane["judge"]
    assert "--maxfail=1" not in t1_lane["argv"]

    changelog_preflight_ignore = "--ignore=tests/test_w3_prep_changelog_preflight.py"
    for name in ("cmru", "cmru-r0-r1", "cmru-first-release", "cmru-first-release-r2"):
        assert changelog_preflight_ignore in assay_config[name]["argv"]

    first_r2_mutation = assay_config["cmru-first-release-r2"]["judge"]["mutation"]
    assert first_r2_mutation == {
        "jobs_max": 2,
        "max_mutants": 10000,
        "operators": [
            "python:compare-swap",
            "python:boolop-swap",
            "python:bool-const-flip",
            "python:falsy-swap",
        ],
        "budget_per_candidate": "auto",
        "liveness": True,
    }

    gate = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    gate_command = " ".join(gate["lanes"]["gate"]["argv"])
    assert gate_command == (
        "python3 -I -B {worktree}/cmru/tools/run_release_gate.py --worktree {worktree}"
    )
    survivor_command = " ".join(gate["lanes"]["survivor-regressions"]["argv"])
    assert "--editable ../assay" in survivor_command
    provisional = gate["lanes"]["gate-provisional"]
    assert provisional["argv"][-2:] == ["--postpone-mutation", "KI-79"]
    release_preconditions = " ".join(gate["lanes"]["release-preconditions"]["argv"])
    assert "tests/test_w3_prep_changelog_preflight.py" in release_preconditions
    release_gate = (ROOT / "tools" / "run_release_gate.py").read_text(encoding="utf-8")
    assert 'RELEASE_PRECONDITIONS_LANE = "release-preconditions"' in release_gate
    cmru = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
    release_steps = cmru["steps"]["run-tests"]["commands"]
    assert len(release_steps) == 1
    assert release_steps[0]["argv"] == ["run-gate", "gate-provisional"]
    backlog = (ROOT / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text(encoding="utf-8")
    assert "KI-79" in backlog
    for name in (
        "assay", "assay-r0-r1", "installed-wheel", "installed-wheel-live", "ref-probe",
        "mutation",
    ):
        lane = gate["lanes"][name]
        # Only the R2 (mutation) lane is observed by cgprofile; the other lanes
        # keep resource ceilings but mount no observer socket (08632f747).
        if name == "mutation":
            assert lane["resource_observer"] == "cgprofile"
        else:
            assert "resource_observer" not in lane
        assert lane["resources"]["pids"] == 8192
        assert lane["resources"]["memory"] == "2g"
    for name in ("assay", "mutation"):
        assert f'"{name}"' in release_gate
    assert '"coverage"' not in release_gate
    assert '"canary"' not in release_gate
    assert '"enroll"' not in release_gate  # moved to ciu/run-gate.toml (O4)
    assert "enroll" not in gate["lanes"]
    assert "_mask_secret_overlays(" not in release_gate  # KI-74: no in-place masking
    assert "RUN_GATE_EXTRA_MOUNTS" in release_gate
    for lane in ("installed-wheel", "installed-wheel-live", "ref-probe"):
        assert lane in gate["lanes"]
    assert "--require-live-snapshot" not in " ".join(gate["lanes"]["installed-wheel"]["argv"])
    assert "--require-live-snapshot" in " ".join(gate["lanes"]["installed-wheel-live"]["argv"])
    assert 'LIVE_WHEEL_LANE = "installed-wheel-live"' in release_gate
    assert 'REF_PROBE_LANE = "ref-probe"' in release_gate
    assert 'FIRST_RELEASE_ASSAY_LANE = "assay-first-release"' in release_gate
    assert 'FIRST_RELEASE_R2_FOLLOW_UP_LANE = "assay-first-release-r2"' in release_gate
    assert "published_release_state" in release_gate
    first_lane = gate["lanes"]["assay-first-release"]
    assert first_lane["assay_lane"] == "cmru-first-release"
    t1_assay = gate["lanes"]["assay-r0-r1"]
    assert t1_assay["kind"] == "assay"
    assert t1_assay["assay_lane"] == "cmru-r0-r1"
    t1 = gate["lanes"]["r0-r1"]
    assert t1["kind"] == "sequence"
    assert t1["lanes"] == ["release-preconditions", "assay-r0-r1"]
    first_r2 = gate["lanes"]["assay-first-release-r2"]
    assert first_r2["assay_lane"] == "cmru-first-release-r2"
    assert first_r2["resource_observer"] == "cgprofile"
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
    r2 = gate["lanes"]["r2"]
    assert r2["kind"] == "command"
    assert r2["environment"] == "cmru-mutation"
    assert r2["resource_observer"] == "cgprofile"
    assert r2["clean_tree"] is True
    assert "required_env" not in r2
    r2_command = " ".join(r2["argv"])
    for required in (
        'BASE="{base}"',
        'tools/local_baseline_facts.py --base "$BASE"',
        'tools/check_assay_baseline.py --base "$BASE" --skip-evidence',
        'tools/mutation_campaign.py',
        '--base "$BASE"',
        '--resume',
        '--progress .assay/progress-mutation-cmru.jsonl',
        '--evidence .assay/mutation-cmru.json',
    ):
        assert required in r2_command
    assert gate["lanes"]["mutation"]["required_env"] == [
        "CMRU_ASSAY_BASELINE_FACTS",
    ]
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "run-gate --base cmru-v6.1.0 r2" in consumers
    assert "KI-151" in consumers
    issue_anchor = "KNOWN_ISSUES_TODO_BACKLOG.md#ki-151---the-t3-r2-lane-treats-bundled-local-cmru-tags-as-published"
    assert issue_anchor in consumers
    assert issue_anchor in readme
    backlog = (ROOT / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text(encoding="utf-8")
    assert "KI-151 - the T3 `r2` lane treats bundled local CMRU tags as published" in backlog
    assert "git diff --quiet" not in mutation_command
    baseline_checker = (ROOT / "tools" / "check_assay_baseline.py").read_text(encoding="utf-8")
    assert '"reason": "no-changed-source"' in baseline_checker
    assert 'PROJECT_ROOT / ".assay" / "mutation-cmru.json"' in baseline_checker
    assert 'parser.add_argument(\n        "--base"' in baseline_checker
    assert "given base {args.base!r}" in baseline_checker
    assert "assay_git.resolve_base" in baseline_checker
    assert "assay_git.base_resolution_mode" in baseline_checker
    assert "assay_git.run(" in baseline_checker
    assert 'os.environ.get("CMRU_ASSAY_BASELINE_FACTS"' in baseline_checker
    assert "latest_ancestor_release_tag" in baseline_checker
    assert '"head": head_commit' in baseline_checker
    assert 'source_roots = judge["source_roots"]' in baseline_checker
    assert 'declared = judge["base"]' not in baseline_checker
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

    # Whole-target Assay owns coverage; only mutation remains fail-fast.
    assert "--maxfail=1" in " ".join(gate["lanes"]["mutation"]["argv"])
    assert gate["lanes"]["assay"]["kind"] == "assay"
    assay_command = " ".join(assay_config["cmru"]["argv"])
    assert "--cov-fail-under=100" not in assay_command
    assert "junit-coverage.xml" not in assay_command
    assert not (ROOT / "tools" / "coverage_canary.py").exists()

    for document in (
        ROOT / "README.md",
        ROOT / "docs" / "SPEC.md",
        ROOT / "docs" / "DESIGN-GUIDE.md",
        ROOT / "docs" / "CONSUMERS.md",
    ):
        text = document.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        assert "--maxfail=1" in text
        assert "600 seconds" in text or "600-second" in text
        assert "progress" in text.lower()
        assert "whole-target" in text or "whole_target" in text
        assert "cmru-v*" in text
        assert "published" in normalized.lower()
        assert "R1" in text and "R2" in text

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert "run-gate --base cmru-v6.1.0 r2" in readme
    assert "controller chooses the newest tag with a recorded" in design.lower()
    assert "run-gate r0-r1" in consumers and "run-gate --base cmru-v6.1.0 r2" in consumers
    assert "T1 lane design" in readme
    assert "CMRU T1 `r0-r1` lane" in design
    assert "S16.3a — T1 R0/R1 mapping" in spec
    assert "S16.3b — T3 explicit R2 base" in spec


def test_release_gate_credential_boundary_is_documented_in_all_user_guides():
    for document in DOCS:
        text = " ".join(document.read_text(encoding="utf-8").split())
        assert "gate credential design" in text or document.name == "DESIGN-GUIDE.md"
        # KI-74 follow-up (C-1..C-6): every user guide states each part of the
        # boundary a reader must know before relying on, or bypassing, it.
        for phrase in (
            "installed-wheel-live", "ref-probe", "cmru gate-snapshots",
            "protected Git refs", "bound to the claimed commit",
        ):
            assert phrase in text, (document.name, phrase)
    consumer_text = " ".join(DOCS[-1].read_text(encoding="utf-8").split())
    assert "individual tester component lane bypasses this boundary" in consumer_text


def test_release_body_contract_is_documented_in_all_user_guides():
    for document in DOCS:
        text = " ".join(document.read_text(encoding="utf-8").split())
        required = ["begin release body", "exact", "[Unreleased]"]
        required.append("changelog = false" if document.name == "README.md" else "CMRU_RELEASE_NOTES_SOURCE")
        # KI-78: promotion carries staged release notes only after publication succeeds.
        required.append("successful publish")
        for phrase in required:
            assert phrase in text, (document.name, phrase)


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


def test_ki142_external_release_order_and_ki143_manual_cleanup_are_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    consumers_flat = " ".join(consumers.split())

    assert "before candidate push, gate, build, or publication" in readme
    assert "#external-version-preparation-and-tag-preflight" in readme
    assert "### External version preparation and tag preflight" in design
    assert "before writing scope metadata, pushing the candidate" in design
    assert "cmru abandon` refuses even with" in consumers
    assert (
        "When `project_scope_state = \"recorded\"`, use `cmru abandon`"
        in consumers
    )
    assert (
        "removes the origin candidate branch when the transaction records that it pushed one"
        in consumers_flat
    )
    safety_rule = (
        "Continue only when every check below succeeds. If any check fails or a query errors, "
        "stop; do not run the removal commands."
    )
    assert safety_rule in " ".join(consumers.split())


def test_ki143_documented_cleanup_removes_only_a_safe_local_candidate(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(repo, KI143_BRANCH)

    script = _ki143_cleanup_script(
        consumers, repo=repo, candidate=candidate, branch=KI143_BRANCH, tag=KI143_TAG,
    )
    for guard in (
        "case \"$candidate\" in",
        'git -C "$candidate" rev-parse --abbrev-ref HEAD',
        'git -C "$candidate" status --short --untracked-files=all',
        'git -C "$repo" ls-remote --heads origin "refs/heads/$branch"',
        'git -C "$repo" ls-remote --tags origin "refs/tags/$tag"',
        'git -C "$repo" worktree remove --force "$candidate"',
        'git -C "$repo" branch -D -- "$branch"',
    ):
        assert guard in script
    for command in (
        'git -C "$repo" ls-remote --heads origin "refs/heads/$branch"',
        'git -C "$repo" ls-remote --tags origin "refs/tags/$tag"',
    ):
        line = next(line for line in script.splitlines() if command in line)
        assert line.rstrip().endswith("|| exit 1")

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate, branch=KI143_BRANCH, tag=KI143_TAG,
    )
    assert result.returncode == 0, result.stderr
    assert not candidate.exists()
    assert not _ki143_branch_exists(repo, KI143_BRANCH)


def test_ki143_documented_cleanup_refuses_a_candidate_outside_release_worktrees(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(
        repo, "cmru-release-outside", tmp_path / "outside" / "candidate",
    )

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate,
        branch="cmru-release-outside", tag=KI143_TAG,
    )
    assert result.returncode != 0
    assert candidate.is_dir()
    assert _ki143_branch_exists(repo, "cmru-release-outside")


def test_ki143_documented_cleanup_refuses_a_branch_that_is_not_checked_out(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(repo, KI143_BRANCH)
    unrelated_branch = "cmru-release-unrelated"
    _git(repo, "branch", unrelated_branch)

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate,
        branch=unrelated_branch, tag=KI143_TAG,
    )
    assert result.returncode != 0
    assert candidate.is_dir()
    assert _ki143_branch_exists(repo, KI143_BRANCH)
    assert _ki143_branch_exists(repo, unrelated_branch)


def test_ki143_documented_cleanup_refuses_a_dirty_candidate(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(repo, KI143_BRANCH)
    (candidate / "untracked.txt").write_text("keep me\n", encoding="utf-8")

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate, branch=KI143_BRANCH, tag=KI143_TAG,
    )
    assert result.returncode != 0
    assert (candidate / "untracked.txt").is_file()
    assert _ki143_branch_exists(repo, KI143_BRANCH)


def test_ki143_documented_cleanup_refuses_a_present_origin_candidate_branch(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(repo, KI143_BRANCH)
    _git(repo, "push", "origin", KI143_BRANCH)

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate, branch=KI143_BRANCH, tag=KI143_TAG,
    )
    assert result.returncode != 0
    assert candidate.is_dir()
    assert _ki143_branch_exists(repo, KI143_BRANCH)


def test_ki143_documented_cleanup_refuses_a_present_origin_release_tag(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(repo, KI143_BRANCH)
    _git(repo, "tag", KI143_TAG)
    _git(repo, "push", "origin", KI143_TAG)

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate, branch=KI143_BRANCH, tag=KI143_TAG,
    )
    assert result.returncode != 0
    assert candidate.is_dir()
    assert _ki143_branch_exists(repo, KI143_BRANCH)


def test_ki143_documented_cleanup_refuses_when_origin_cannot_be_queried(tmp_path: Path):
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repo, _origin = _ki143_repository(tmp_path)
    candidate = _ki143_candidate(repo, KI143_BRANCH)
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing-origin.git"))

    result = _run_ki143_cleanup(
        consumers, repo=repo, candidate=candidate, branch=KI143_BRANCH, tag=KI143_TAG,
    )
    assert result.returncode != 0
    assert candidate.is_dir()
    assert _ki143_branch_exists(repo, KI143_BRANCH)


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


def test_cmru_self_release_recipe_covers_engine_identity_gate_and_recovery():
    release_doc = (ROOT / "docs" / "RELEASE-TRANSACTIONS.md").read_text(encoding="utf-8")
    bootstrap_doc = (ROOT.parent / "docs" / "BOOTSTRAP-FROM-ZERO.md").read_text(
        encoding="utf-8",
    )
    section_start = release_doc.index("### Releasing cmru itself")
    section_end = release_doc.index("### Run-Gate compatibility bootstrap before CGProfile")
    recipe = release_doc[section_start:section_end]

    for required in (
        "./build-initial-standalone.sh",
        "CGROUP_PARENT_DEV_BACKGROUND",
        "command -v cmru",
        "cmru --version",
        "installed_release_controller_identity",
        "cmru abandon <candidate> --yes",
        "background Bash task",
        "Bootstrap Run-Gate version",
    ):
        assert required in recipe

    shell_blocks = re.findall(r"```bash\n(.*?)```", recipe, flags=re.DOTALL)
    install_blocks = [block for block in shell_blocks if "pip install" in block]
    assert len(install_blocks) == 1
    install_command = " ".join(install_blocks[0].split())
    assert "--no-index --no-deps --force-reinstall" in install_command
    assert (
        '"cmru @ file://${BOOTSTRAP_CMRU_WHEEL}'
        '#sha256=${BOOTSTRAP_CMRU_WHEEL_SHA256}"'
    ) in install_command
    assert not re.search(r"(?<!\S)(?:-e|--editable)(?=\s|=)", install_command)

    release_blocks = [
        block
        for block in shell_blocks
        if "RUN_GATE_PROFILE=off cmru release cmru" in block
    ]
    assert len(release_blocks) == 1
    release_lines = [line.strip() for line in release_blocks[0].splitlines()]
    release_command = "RUN_GATE_PROFILE=off cmru release cmru --set-version 7.2.5"
    release_index = release_lines.index(release_command)
    assert release_lines[release_index + 1 : release_index + 4] == [
        "release_rc=$?",
        "set -e",
        r'''printf 'EXIT=%s\n' "$release_rc"''',
    ]

    assert (
        '[`cmru/docs/RELEASE-TRANSACTIONS.md`, "Releasing cmru itself"]'
        "(../cmru/docs/RELEASE-TRANSACTIONS.md#releasing-cmru-itself)"
    ) in bootstrap_doc


def test_registered_projects_track_their_shared_pwmcp_runtime_image():
    workspace = ROOT.parent
    for project in ("topos", "nyxloom"):
        config = tomllib.loads((workspace / project / "cmru.toml").read_text(encoding="utf-8"))
        target = config["versions"]["targets"]["oci.pwmcp"]
        assert target["mode"] == "single"
        assert target["oci"]["image"] == "ghcr.io/volkb79-2/pwmcp"
        assert target["oci"]["tag"] == "{version}"
