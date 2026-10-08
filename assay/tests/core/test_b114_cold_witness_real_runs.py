"""Real CLI coverage for B114 cold, survivor, fallback, and baseline paths."""

from __future__ import annotations

import io
import json
import sys
import threading
from pathlib import Path

import pytest
from conftest import GitRepo

from assay import cli, mutation


def _seed_campaign(repo: GitRepo, attempt_log: Path, *, max_mutants: int = 3) -> Path:
    repo.write(
        ".gitignore",
        ".assay/\n.pytest_cache/\n__pycache__/\ncoverage.json\n.coverage\n",
    )
    repo.write(
        "src/pkg/mod.py",
        "def early(value):\n    return value > 0\n\n"
        "def survivor(value):\n    return value > 0\n\n"
        "def fallback(value):\n    return value > 0\n",
    )
    repo.write(
        "tests/test_campaign.py",
        "import inspect\n"
        "import os\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "from pkg import mod\n\n"
        "def _flags():\n"
        "    return ''.join('1' if '>= 0' in inspect.getsource(fn) else '0'\n"
        "                   for fn in (mod.early, mod.survivor, mod.fallback))\n\n"
        "def _record(name):\n"
        "    mode = ('cold' if os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1'\n"
        "            else 'replay' if os.environ.get('ASSAY_MUTATION_WITNESS_TARGET')\n"
        "            else 'full')\n"
        "    with Path(os.environ['ASSAY_TEST_ATTEMPT_LOG']).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(f'{mode}|{name}|{_flags()}\\n')\n\n"
        "def test_early_kill():\n"
        "    _record('early')\n"
        "    assert mod.early(0) is False\n\n"
        "def test_survivor():\n"
        "    _record('survivor')\n"
        "    assert mod.survivor(1) is True\n\n"
        "def test_declared_fallback(pytestconfig):\n"
        "    _record('fallback')\n"
        "    if _flags() == '001' and os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1':\n"
        "        class LateHook:\n"
        "            @pytest.hookimpl\n"
        "            def pytest_runtest_logreport(self, report):\n"
        "                return None\n"
        "        pytestconfig.pluginmanager.register(LateHook(), 'cold-only-dynamic-hook')\n"
        "    assert mod.fallback(0) is False\n",
    )
    repo.write(
        "tests/conftest.py",
        "import inspect\n"
        "import os\n"
        "from pkg import mod\n\n"
        "def pytest_configure(config):\n"
        "    if os.environ.get('ASSAY_MUTATION_WITNESS_COLD') != '1':\n"
        "        return\n"
        "    if '>= 0' not in inspect.getsource(mod.survivor):\n"
        "        return\n"
        "    hook = config.hook.pytest_runtest_logreport\n"
        "    impl = next(item for item in hook.get_hookimpls()\n"
        "                if item.plugin_name == 'assay_mutation_witness_plugin')\n"
        "    original = impl.function\n"
        "    namespace = original.__globals__\n"
        "    namespace['_b114_original_report_hook'] = original\n"
        "    source = '''def _b114_forged_report_hook(report):\n"
        "    if (report.when == 'call' and report.outcome == 'passed'\n"
        "            and report.nodeid.endswith('test_early_kill')):\n"
        "        report.outcome = 'failed'\n"
        "        report.longrepr = 'forged candidate failure'\n"
        "    _b114_original_report_hook(report)\n"
        "'''\n"
        "    exec(compile(source, original.__code__.co_filename, 'exec'), namespace)\n"
        "    forged = namespace['_b114_forged_report_hook']\n"
        "    forged.__module__ = original.__module__\n"
        "    forged.__qualname__ = original.__qualname__\n"
        "    forged.__name__ = original.__name__\n"
        "    impl.function = forged\n",
    )
    repo.write(
        "assay.toml",
        f'''\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = [
  {json.dumps(sys.executable)}, "-m", "pytest", "tests", "-q",
  "--cov=src/pkg", "--cov-branch", "--cov-report=json:coverage.json",
  "-p", "pytest_cov.plugin",
]
env = {{ PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1", PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1" }}
env_passthrough = [
  "PATH", "HOME", "TMPDIR", "ASSAY_TEST_ATTEMPT_LOG",
  "ASSAY_B114_LIVENESS_ATTACK_LOG",
]
budget = "10m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src/pkg"]
mode = "whole_target"
targets = ["src/pkg/mod.py"]
fail_under = 100.0
allow_excluded = true
require_branch = false

[lanes.package.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = {max_mutants}
operators = ["python:compare-swap"]
budget_per_candidate = "120s"
liveness = false
''',
    )
    repo.commit_all("seed B114 cold witness integration lane")
    return repo.path / "assay.toml"


def _run_cold_campaign(
    repo: GitRepo,
    config: Path,
    *,
    tmp_path: Path,
    attempt_log: Path,
    run_id: str = "package",
    reuse_from: Path | None = None,
):
    (repo.path / ".assay").mkdir(exist_ok=True)
    progress = repo.path / f".assay/progress-{run_id}.jsonl"
    manifest = repo.path / f".assay/r2-manifest-{run_id}.txt"
    verdict = repo.path / f".assay/verdict-{run_id}.json"
    state_dir = repo.path / f".assay/mutation-state-{run_id}"
    argv = [
        "run",
        "package",
        "--file",
        str(config),
        "--cold-witness",
        "--r2-manifest",
        str(manifest),
        "--resume",
        "--progress",
        str(progress),
        "--state-dir",
        str(state_dir),
        "--verdict-json",
        str(verdict),
    ]
    if reuse_from is not None:
        argv.extend(["--reuse-from", str(reuse_from)])
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdout=stdout, stderr=stderr)
    return (
        code,
        stdout.getvalue(),
        stderr.getvalue(),
        progress,
        manifest,
        verdict,
        state_dir,
    )


def test_reuse_cold_witness_rejects_a_replay_receipt_with_collection_error(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A collection error in an unrelated module cannot certify a reused kill.

    The phase file is outside the judged tree and declared as passthrough. It
    makes the collection error occur only during targeted replay; the
    subsequent cold attempt collects the full suite cleanly.
    """
    attempt_log = tmp_path / "attempts.log"
    phase_file = tmp_path / "collection-phase.txt"
    phase_file.write_text("clean", encoding="utf-8")
    config = _seed_campaign(git_repo, attempt_log, max_mutants=3)
    config_text = config.read_text(encoding="utf-8")
    config_text = config_text.replace(
        '"tests", "-q",\n',
        '"tests", "-q", "--continue-on-collection-errors",\n',
    ).replace(
        '"ASSAY_B114_LIVENESS_ATTACK_LOG",\n',
        '"ASSAY_B114_LIVENESS_ATTACK_LOG", "ASSAY_B114_COLLECTION_PHASE_FILE",\n',
    )
    git_repo.write("assay.toml", config_text)
    git_repo.write(
        "tests/test_collection_bomb.py",
        "import os\n"
        "from pathlib import Path\n\n"
        "target = os.environ.get('ASSAY_MUTATION_WITNESS_TARGET')\n"
        "phase = os.environ.get('ASSAY_B114_COLLECTION_PHASE_FILE')\n"
        "if target and phase and Path(phase).read_text(encoding='utf-8') == 'replay':\n"
        "    raise ImportError('B114 replay-only collection error')\n",
    )
    campaign_tests = git_repo.path / "tests/test_campaign.py"
    campaign_source = campaign_tests.read_text(encoding="utf-8")
    campaign_source_with_all_mutants_killed = campaign_source.replace(
        "    assert mod.survivor(1) is True\n",
        "    assert mod.survivor(1) is True\n"
        "    assert mod.survivor(0) is False\n",
    )
    assert campaign_source_with_all_mutants_killed != campaign_source
    git_repo.write(
        "tests/test_campaign.py",
        campaign_source_with_all_mutants_killed,
    )
    git_repo.commit_all("add replay-only collection error fixture")
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))
    monkeypatch.setenv("ASSAY_B114_COLLECTION_PHASE_FILE", str(phase_file))

    first = _run_cold_campaign(
        git_repo,
        config,
        tmp_path=tmp_path,
        attempt_log=attempt_log,
        run_id="initial",
    )
    assert first[0] == 0, f"stdout:\n{first[1]}\nstderr:\n{first[2]}"
    first_verdict = json.loads(first[5].read_text(encoding="utf-8"))
    first_r2 = next(claim for claim in first_verdict["claims"] if claim["rigor"] == "R2")
    first_early = next(
        outcome
        for outcome in first_r2["mutation"]["killed"]
        if outcome["lineno"] == 2
    )
    assert first_early["execution"]["mode"] == "witness-cold"

    git_repo.write("README.md", "Advance the reuse base without changing the judged suite.\n")
    git_repo.commit_all("advance commit for cold witness reuse")
    attempt_log.unlink(missing_ok=True)
    phase_file.write_text("replay", encoding="utf-8")
    second = _run_cold_campaign(
        git_repo,
        config,
        tmp_path=tmp_path,
        attempt_log=attempt_log,
        run_id="reuse",
        reuse_from=first[5],
    )
    assert second[0] == 0, f"stdout:\n{second[1]}\nstderr:\n{second[2]}"
    document = json.loads(second[5].read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    second_early = next(
        outcome
        for outcome in r2["mutation"]["killed"]
        if outcome["lineno"] == 2
    )
    assert second_early["execution"]["mode"] == "witness-cold"
    assert all(
        outcome["execution"]["mode"] != "witness-prefix"
        for outcome in r2["mutation"]["killed"]
    )
    rows = [line.split("|") for line in attempt_log.read_text().splitlines()]
    replay_early = next(
        index
        for index, row in enumerate(rows)
        if row == ["replay", "early", "100"]
    )
    cold_early = next(
        index
        for index, row in enumerate(rows)
        if row == ["cold", "early", "100"]
    )
    assert replay_early < cold_early
    assert cli.main(["verify", str(second[5])], stdout=io.StringIO(), stderr=io.StringIO()) == 0


@pytest.mark.parametrize("tamper", ["missing", "collection", "hook"])
def test_reuse_cold_witness_falls_back_when_replay_facts_do_not_match_baseline(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
):
    attempt_log = tmp_path / "attempts.log"
    config = _seed_campaign(git_repo, attempt_log, max_mutants=3)
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))

    first = _run_cold_campaign(
        git_repo,
        config,
        tmp_path=tmp_path,
        attempt_log=attempt_log,
        run_id="initial",
    )
    assert first[0] == 1, f"stdout:\n{first[1]}\nstderr:\n{first[2]}"

    git_repo.write("README.md", "Advance the reuse base without changing the judged suite.\n")
    git_repo.commit_all("advance commit for cold witness reuse")
    attempt_log.unlink(missing_ok=True)

    original_finish = mutation.ReceiptCapture.finish
    tampered: list[str] = []

    def tamper_replay_receipt(capture):
        receipt = original_finish(capture)
        if receipt is None or receipt.get("target_node_id") is None:
            return receipt
        tampered.append(str(receipt["target_node_id"]))
        if tamper == "missing":
            receipt.pop("collection_sha256", None)
        elif tamper == "collection":
            receipt["collection_sha256"] = "0" * 64
        else:
            receipt["hook_fingerprint_sha256"] = "0" * 64
        return receipt

    monkeypatch.setattr(
        mutation.ReceiptCapture, "finish", tamper_replay_receipt
    )
    second = _run_cold_campaign(
        git_repo,
        config,
        tmp_path=tmp_path,
        attempt_log=attempt_log,
        run_id="reuse",
        reuse_from=first[5],
    )
    assert tampered
    assert second[0] == 1, f"stdout:\n{second[1]}\nstderr:\n{second[2]}"
    document = json.loads(second[5].read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    early = next(
        outcome for outcome in r2["mutation"]["killed"] if outcome["lineno"] == 2
    )
    assert early["execution"]["mode"] == "witness-cold"
    rows = [line.split("|") for line in attempt_log.read_text().splitlines()]
    assert [row[:2] for row in rows if row[1] == "early" and row[2] == "100"][:2] == [
        ["replay", "early"],
        ["cold", "early"],
    ]
    assert cli.main(["verify", str(second[5])], stdout=io.StringIO(), stderr=io.StringIO()) == 0


def test_assay_run_cold_witness_covers_early_kill_survivor_and_one_fallback(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempt_log = tmp_path / "attempts.log"
    config = _seed_campaign(git_repo, attempt_log)
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))

    plan_stdout, plan_stderr = io.StringIO(), io.StringIO()
    plan_code = cli.main(
        ["plan", "package", "--file", str(config)],
        stdout=plan_stdout,
        stderr=plan_stderr,
    )
    assert plan_code == 0, plan_stderr.getvalue()
    plan = json.loads(plan_stdout.getvalue())
    assert plan["status"] == "ok"
    assert plan["candidate_count"] == 3
    candidate_id_by_line = {
        row["lineno"]: row["id"] for row in plan["candidates"]
    }
    assert set(candidate_id_by_line) == {2, 5, 8}
    early_id = candidate_id_by_line[2]
    survivor_id = candidate_id_by_line[5]
    fallback_id = candidate_id_by_line[8]
    assert len(plan["candidates"]) == 3

    code, stdout, stderr, progress_path, manifest_path, verdict_path, state_dir = (
        _run_cold_campaign(
            git_repo, config, tmp_path=tmp_path, attempt_log=attempt_log
        )
    )
    assert code == 1, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    mutation = r2["mutation"]
    command = document["judgment"]["r2"]["r2_command"]
    assert (
        command["coverage_baseline"]["hook_fingerprint_sha256"]
        != command["r2_baseline"]["hook_fingerprint_sha256"]
    )
    assert len(mutation["killed"]) == 2
    assert len(mutation["survived"]) == 1
    assert {outcome["candidate_id"] for outcome in mutation["killed"]} == {
        early_id,
        fallback_id,
    }
    assert {outcome["candidate_id"] for outcome in mutation["survived"]} == {
        survivor_id
    }

    cold_kills = [
        outcome
        for outcome in mutation["killed"]
        if outcome["execution"]["mode"] == "witness-cold"
    ]
    declared_kills = [
        outcome
        for outcome in mutation["killed"]
        if outcome["execution"]["mode"] == "full"
        and outcome["evidence"]["command"] == "declared"
    ]
    assert len(cold_kills) == 1
    assert cold_kills[0]["evidence"]["hook_fingerprint_sha256"] == command[
        "r2_baseline"
    ]["hook_fingerprint_sha256"]
    assert cold_kills[0]["evidence"]["collection_sha256"] == command[
        "r2_baseline"
    ]["collection_sha256"]
    assert cold_kills[0]["execution"]["witness"]["node_id"].endswith(
        "test_campaign.py::test_early_kill"
    )
    assert cold_kills[0]["evidence"]["started_count"] == 1
    assert cold_kills[0]["evidence"]["failed_call_index"] == 0
    assert len(declared_kills) == 1
    assert declared_kills[0]["evidence"]["hook_fingerprint_sha256"] == command[
        "coverage_baseline"
    ]["hook_fingerprint_sha256"]
    assert declared_kills[0]["evidence"]["collection_sha256"] == command[
        "coverage_baseline"
    ]["collection_sha256"]
    assert mutation["survived"][0]["execution"]["mode"] == "full"
    assert mutation["survived"][0]["evidence"]["command"] == "declared"
    assert mutation["survived"][0]["evidence"]["started_count"] is None

    rows = [line.split("|") for line in attempt_log.read_text(encoding="utf-8").splitlines()]
    rows_by_attempt = {
        (mode, flags): [name for row_mode, name, row_flags in rows if (row_mode, row_flags) == (mode, flags)]
        for mode, flags in {(row[0], row[2]) for row in rows}
    }
    assert rows_by_attempt[("cold", "100")] == ["early"]
    assert rows_by_attempt[("cold", "010")] == ["early"]
    assert rows_by_attempt[("cold", "001")] == ["early", "survivor", "fallback"]
    assert rows_by_attempt[("full", "001")] == ["early", "survivor", "fallback"]
    assert rows_by_attempt[("full", "010")] == ["early", "survivor", "fallback"]
    assert sum(1 for mode, flags in rows_by_attempt if mode == "full" and flags != "000") == 2

    manifest_nodes = manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(manifest_nodes) == 3
    assert all("test_campaign.py::" in node for node in manifest_nodes)
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert sum(event.get("event") == "candidate" for event in events) == 3
    assert len(list(state_dir.glob("*.json"))) == 3
    assert cli.main(["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()) == 0


def test_assay_run_liveness_hook_replacement_falls_back_to_declared_command(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempt_log = tmp_path / "attempts.log"
    attack_log = tmp_path / "liveness-attacks.jsonl"
    config = _seed_campaign(git_repo, attempt_log)
    repo = git_repo
    config_text = config.read_text(encoding="utf-8").replace(
        "liveness = false", "liveness = true"
    )
    repo.write("assay.toml", config_text)
    repo.write(
        "tests/conftest.py",
        "import inspect\n"
        "import os\n"
        "from pkg import mod\n\n"
        "def pytest_configure(config):\n"
        "    if os.environ.get('ASSAY_MUTATION_WITNESS_COLD') != '1':\n"
        "        return\n"
        "    flags = ''.join('1' if '>= 0' in inspect.getsource(fn) else '0'\n"
        "                   for fn in (mod.early, mod.survivor, mod.fallback))\n"
        "    if flags not in ('100', '010'):\n"
        "        with open(os.environ['ASSAY_B114_LIVENESS_ATTACK_LOG'], 'a', encoding='utf-8') as stream:\n"
        "            stream.write(f'configured:{flags}:0\\n')\n"
        "        return\n"
        "    hook = config.hook.pytest_runtest_logreport\n"
        "    impl = next(item for item in hook.get_hookimpls()\n"
        "                if item.plugin_name == 'assay_liveness_plugin')\n"
        "    original = impl.function\n"
        "    namespace = original.__globals__\n"
        "    namespace['_b114_original_liveness_hook'] = original\n"
        "    namespace['_b114_attack_flags'] = flags\n"
        "    source = '''def _b114_forged_liveness_hook(report):\n"
        "    flags = _b114_attack_flags\n"
        "    attack = {'100': 'suppress', '010': 'forge'}.get(flags)\n"
        "    before = report.outcome\n"
        "    if (os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1'\n"
        "            and report.when == 'call'\n"
        "            and report.nodeid.endswith('test_early_kill')):\n"
        "        if attack == 'suppress' and before == 'failed':\n"
        "            report.outcome = 'passed'\n"
        "            report.longrepr = None\n"
        "        elif attack == 'forge' and before == 'passed':\n"
        "            report.outcome = 'failed'\n"
        "            report.longrepr = 'forged liveness failure'\n"
        "    return _b114_original_liveness_hook(report)\n"
        "'''\n"
        "    exec(compile(source, original.__code__.co_filename, 'exec'), namespace)\n"
        "    forged = namespace['_b114_forged_liveness_hook']\n"
        "    forged.__module__ = original.__module__\n"
        "    forged.__qualname__ = original.__qualname__\n"
        "    forged.__name__ = original.__name__\n"
        "    impl.function = forged\n"
        "    with open(os.environ['ASSAY_B114_LIVENESS_ATTACK_LOG'], 'a', encoding='utf-8') as stream:\n"
        "        stream.write(f'configured:{flags}:1\\n')\n",
    )
    repo.commit_all("seed B114 active-liveness hook replacement attack")
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))
    monkeypatch.setenv("ASSAY_B114_LIVENESS_ATTACK_LOG", str(attack_log))

    plan_stdout, plan_stderr = io.StringIO(), io.StringIO()
    plan_code = cli.main(
        ["plan", "package", "--file", str(config)],
        stdout=plan_stdout,
        stderr=plan_stderr,
    )
    assert plan_code == 0, plan_stderr.getvalue()
    plan = json.loads(plan_stdout.getvalue())
    assert plan["status"] == "ok"
    assert plan["candidate_count"] == 3
    candidate_id_by_line = {
        row["lineno"]: row["id"] for row in plan["candidates"]
    }
    assert set(candidate_id_by_line) == {2, 5, 8}
    early_id = candidate_id_by_line[2]
    survivor_id = candidate_id_by_line[5]
    fallback_id = candidate_id_by_line[8]

    code, stdout, stderr, _, _, verdict_path, _ = _run_cold_campaign(
        repo, config, tmp_path=tmp_path, attempt_log=attempt_log
    )
    assert code == 1, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    mutation = next(
        claim["mutation"] for claim in document["claims"] if claim["rigor"] == "R2"
    )
    outcomes = [
        outcome
        for bucket in ("killed", "survived")
        for outcome in mutation[bucket]
    ]
    assert len(mutation["killed"]) == 2
    assert len(mutation["survived"]) == 1
    assert {outcome["candidate_id"] for outcome in mutation["killed"]} == {
        early_id,
        fallback_id,
    }
    assert {outcome["candidate_id"] for outcome in mutation["survived"]} == {
        survivor_id
    }
    assert all(outcome["execution"]["mode"] == "full" for outcome in outcomes)
    assert all(outcome["evidence"]["command"] == "declared" for outcome in outcomes)
    assert cli.main(
        ["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0

    rows = [line.split("|") for line in attempt_log.read_text(encoding="utf-8").splitlines()]
    rows_by_attempt = {
        (mode, flags): [
            name for row_mode, name, row_flags in rows
            if (row_mode, row_flags) == (mode, flags)
        ]
        for mode, flags in {(row[0], row[2]) for row in rows}
    }
    for flags in ("100", "010", "001"):
        assert rows_by_attempt[("full", flags)] == ["early", "survivor", "fallback"]

    assert attack_log.read_text(encoding="utf-8").splitlines() == [
        "configured:100:1",
        "configured:010:1",
        "configured:001:0",
    ]


def test_cold_witness_reader_thread_start_failure_is_typed_and_never_a_kill(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    attempt_log = tmp_path / "attempts.log"
    config = _seed_campaign(git_repo, attempt_log, max_mutants=1)
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))
    original_receipt_capture = mutation.ReceiptCapture
    original_start = threading.Thread.start
    candidate_capture_calls: list[str] = []
    failed_from: list[str] = []

    def fail_receipt_reader_on_candidate(thread: threading.Thread) -> None:
        if thread.name == "assay-witness-receipt":
            failed_from.append(threading.current_thread().name)
            raise RuntimeError("injected process-limit thread-start failure")
        original_start(thread)

    def fail_candidate_receipt_capture():
        # This alias is called at mutation.py's candidate-only ReceiptCapture
        # site. runner.py uses its own import for the coverage baseline, so the
        # injection cannot turn an R0 failure into a false R2 regression pass.
        candidate_capture_calls.append("candidate")
        with monkeypatch.context() as capture_patch:
            capture_patch.setattr(
                threading.Thread, "start", fail_receipt_reader_on_candidate
            )
            return original_receipt_capture()

    monkeypatch.setattr(mutation, "ReceiptCapture", fail_candidate_receipt_capture)
    code, stdout, stderr, _progress, _manifest, verdict_path, _state_dir = (
        _run_cold_campaign(
            git_repo, config, tmp_path=tmp_path, attempt_log=attempt_log
        )
    )

    assert candidate_capture_calls == ["candidate"], (
        f"candidate ReceiptCapture was not reached: code={code}, "
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert failed_from
    assert code == 2, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    assert r2["status"] == "ERROR"
    assert r2["reason_code"] == "EXEC_FAILED"
    mutation_payload = r2.get("mutation")
    assert mutation_payload is None or mutation_payload["killed"] == []
    assert cli.main(
        ["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0


@pytest.mark.parametrize("failed_baseline", ["coverage", "r2"])
def test_cold_witness_rejects_a_masked_failing_baseline_before_candidates(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_baseline: str,
):
    attempt_log = tmp_path / "attempts.log"
    repo = git_repo
    repo.write(
        ".gitignore",
        ".assay/\n.pytest_cache/\n__pycache__/\ncoverage.json\n.coverage\n",
    )
    repo.write("src/pkg/mod.py", "def flag(value):\n    return value > 0\n")
    fail_with_cov = failed_baseline == "coverage"
    repo.write(
        "tests/conftest.py",
        "def pytest_sessionfinish(session, exitstatus):\n"
        f"    if session.config.pluginmanager.hasplugin('_cov') is {fail_with_cov!r} and exitstatus != 0:\n"
        "        session.exitstatus = 0\n",
    )
    repo.write(
        "tests/test_masked_baseline.py",
        "import os\n"
            "from pathlib import Path\n"
            "from pkg.mod import flag\n\n"
        "def test_baseline(pytestconfig):\n"
        "    if os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1':\n"
        "        Path(os.environ['ASSAY_TEST_ATTEMPT_LOG']).write_text('candidate-started')\n"
        "    has_coverage = pytestconfig.pluginmanager.hasplugin('_cov')\n"
        f"    assert flag(0) is (has_coverage is {fail_with_cov!r})\n",
    )
    config = repo.write(
        "assay.toml",
        f'''\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = [
  {json.dumps(sys.executable)}, "-m", "pytest", "tests", "-q",
  "--cov=src/pkg", "--cov-branch", "--cov-report=json:coverage.json",
]
env = {{ PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH", "HOME", "TMPDIR", "ASSAY_TEST_ATTEMPT_LOG"]
budget = "10m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src/pkg"]
mode = "whole_target"
targets = ["src/pkg/mod.py"]
fail_under = 100.0
allow_excluded = true
require_branch = false

[lanes.package.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 1
operators = ["python:compare-swap"]
budget_per_candidate = "120s"
liveness = false
''',
    )
    repo.commit_all(f"seed masked {failed_baseline} baseline")
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))

    code, stdout, stderr, _progress_path, _manifest_path, verdict_path, _state_dir = (
        _run_cold_campaign(
            repo, config, tmp_path=tmp_path, attempt_log=attempt_log
        )
    )
    assert code != 0, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    assert r2["status"] == "ERROR"
    assert r2["reason_code"] == "BAD_LANE_CONFIG"
    assert "cold witness:" in r2["detail"]
    assert not attempt_log.exists()
    assert cli.main(["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()) == 0
