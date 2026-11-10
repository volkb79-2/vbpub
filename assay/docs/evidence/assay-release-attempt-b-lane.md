<!-- origin: 2026-11-08; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/assay-release-attempt-b-lane.log -->
<!-- relation: release attempt lane log; verbatim source text follows -->

run-gate: config: /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/run-gate.toml
run-gate: 25.4.0 | lane tester-unified | env [environments.bare-host] in central /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/run-gate.root.toml (mode host)
run-gate: resource env (forwarded: COWTREE_MODE, MDT_COW_ROOT, RUN_GATE_IO_SYNC)
run-gate: budget 5h (hard limit; clock starts after admission and lock waits)
ASSAY_B145_WAIT_PROBE_CONTAINER=run-gate-assay-b145-wait-2519-5019-1794179116
ASSAY_GATE_PHASE=b145-bounded-wait-accepted
ASSAY_B145_PROBE_CONTAINER=run-gate-assay-b145-pids-2519-14348-1794179117
.                                                                        [100%]
1 passed in 1.42s
ASSAY_GATE_PHASE=b145-low-pids-accepted
ASSAY_GATE_CONTAINER=run-gate-assay-selfhosted-2519-4096-1794179120
hint: Using 'master' as the name for the initial branch. This default branch name
hint: is subject to change. To configure the initial branch name to use in all
hint: of your new repositories, which will suppress this warning, call:
hint:
hint: 	git config --global init.defaultBranch <name>
hint:
hint: Names commonly chosen instead of 'master' are 'main', 'trunk' and
hint: 'development'. The just-created branch can be renamed via this command:
hint:
hint: 	git branch -m <name>
Looking in links: /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-wheelhouse
Processing ./workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-wheelhouse/setuptools-84.0.0-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-requirements.txt (line 1))
Processing ./workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-wheelhouse/wheel-0.47.0-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-requirements.txt (line 2))
Processing ./workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-wheelhouse/setuptools_scm-10.0.5-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-requirements.txt (line 3))
Processing ./workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-wheelhouse/packaging-26.3-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-requirements.txt (line 4))
Processing ./workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-wheelhouse/vcs_versioning-2.2.4-py3-none-any.whl (from -r /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/gate/distribution/build-requirements.txt (line 5))
Installing collected packages: setuptools, packaging, wheel, vcs-versioning, setuptools-scm

Successfully installed packaging-26.3 setuptools-84.0.0 setuptools-scm-10.0.5 vcs-versioning-2.2.4 wheel-0.47.0
Processing ./tmp/tmp.Zd3NPZuKyf/clone/assay
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Building wheels for collected packages: assay
  Building wheel for assay (pyproject.toml): started
  Building wheel for assay (pyproject.toml): finished with status 'done'
  Created wheel for assay: filename=assay-12.0.1.dev836+g70f6e5b32-py3-none-any.whl size=1057349 sha256=fbc8727d64aaa0c39d13c274c909b110e082abff40dbed1de8ebb235fec6f4f0
  Stored in directory: /tmp/pip-ephem-wheel-cache-zpd5ilqb/wheels/45/b9/0e/b4a4c8cc63f58423e9c785fffef1c831c39d944130297e0df5
Successfully built assay
Processing ./tmp/tmp.Zd3NPZuKyf/dist/assay-12.0.1.dev836+g70f6e5b32-py3-none-any.whl
Installing collected packages: assay
Successfully installed assay-12.0.1.dev836+g70f6e5b32
ASSAY_GATE_PHASE=wheel-installed
.........................                                                [100%]
25 passed, 16 deselected in 1.32s
ASSAY_GATE_PHASE=attestation-hardened
assay: advisory: R0 failed during measured resource pressure: cgroup_cpu.throttled_usec=325667us (0.0% of window), cgroup_psi.cpu.some_us=4211355us (0.4% of window), cgroup_psi.io.full_us=2210853us (0.2% of window), cgroup_psi.io.some_us=2214838us (0.2% of window), cgroup_psi.memory.full_us=4us (0.0% of window), cgroup_psi.memory.some_us=4us (0.0% of window)
tester-unified: FAIL/COMMAND_FAILED (exit 1)
  R0: FAIL (first failing test: tests/core/test_docs_examples_and_vocabulary.py::test_provisional_self_qualification_is_documented_across_the_public_docs)
  commit: 70f6e5b32d24e1eb6818bfd39bd418764c864b2f
  argv: python -m pytest tests gate/tests -q --ignore=gate/tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red; inspecting its captured verdict
tester-unified: FAIL (recorded exit 1) at 70f6e5b32d24e1eb6818bfd39bd418764c864b2f; Assay 12.0.1.dev836+g70f6e5b32
  R0: FAIL / COMMAND_FAILED
  captured stdout tail:
ECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1225: AssertionError
___ test_full_report_checker_refuses_non_regular_or_symlink_manifests[fifo] ____

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_full_report_checker_refus2')
kind = 'fifo'

    @pytest.mark.parametrize("kind", ["fifo", "symlink"])
    def test_full_report_checker_refuses_non_regular_or_symlink_manifests(
        tmp_path, kind
    ):
        document, plan = _r2_case()
        commit, tree = _own_commit_and_tree()
        raw_deadline = _deadline_bytes(document, commit, tree)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        manifest = tmp_path / "manifest-input"
        if kind == "fifo":
            os.mkfifo(manifest)
        else:
            target = tmp_path / "regular-manifest.txt"
            target.write_bytes(_manifest_bytes())
            manifest.symlink_to(target)
    
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            manifest=manifest,
            deadline_raw=raw_deadline,
            timeout=3,
        )
        assert result.returncode == 2
>       assert "R2 manifest sidecar does not match r2_baseline" in result.stderr
E       AssertionError: assert 'R2 manifest sidecar does not match r2_baseline' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1464: AssertionError
__ test_full_report_checker_refuses_non_regular_or_symlink_manifests[symlink] __

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_full_report_checker_refus3')
kind = 'symlink'

    @pytest.mark.parametrize("kind", ["fifo", "symlink"])
    def test_full_report_checker_refuses_non_regular_or_symlink_manifests(
        tmp_path, kind
    ):
        document, plan = _r2_case()
        commit, tree = _own_commit_and_tree()
        raw_deadline = _deadline_bytes(document, commit, tree)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        manifest = tmp_path / "manifest-input"
        if kind == "fifo":
            os.mkfifo(manifest)
        else:
            target = tmp_path / "regular-manifest.txt"
            target.write_bytes(_manifest_bytes())
            manifest.symlink_to(target)
    
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            manifest=manifest,
            deadline_raw=raw_deadline,
            timeout=3,
        )
        assert result.returncode == 2
>       assert "R2 manifest sidecar does not match r2_baseline" in result.stderr
E       AssertionError: assert 'R2 manifest sidecar does not match r2_baseline' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1464: AssertionError
______ test_full_report_checker_refuses_oversized_binding_files[manifest] ______

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_full_report_checker_refus5')
kind = 'manifest'

    @pytest.mark.parametrize("kind", ["deadline", "manifest"])
    def test_full_report_checker_refuses_oversized_binding_files(tmp_path, kind):
        document, plan = _r2_case()
        commit, tree = _own_commit_and_tree()
        raw_deadline = _deadline_bytes(document, commit, tree)
        if kind == "deadline":
            raw_deadline += b" " * (64 * 1024 + 1)
            document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
            manifest: object = _AUTO_MANIFEST
        else:
            document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
            manifest = tmp_path / "oversized-manifest.txt"
            manifest.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            manifest=manifest,
            deadline_raw=raw_deadline,
            timeout=5,
        )
        assert result.returncode == 2
        expected = (
            "campaign deadline_sha256 does not match the deadline file"
            if kind == "deadline"
            else "R2 manifest sidecar does not match r2_baseline"
        )
>       assert expected in result.stderr
E       AssertionError: assert 'R2 manifest sidecar does not match r2_baseline' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1498: AssertionError
___ test_other_r2_deadline_digest_is_recomputed_after_existing_report_checks ___

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_other_r2_deadline_digest_0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f8c37a70980>

    def test_other_r2_deadline_digest_is_recomputed_after_existing_report_checks(
        tmp_path, monkeypatch
    ):
        spec = importlib.util.spec_from_file_location(
            "b105_report_check_multi_r2_test", CHECKER
        )
        assert spec is not None and spec.loader is not None
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
    
        document, plan = _r2_case()
        commit, tree = _own_commit_and_tree()
        other_lane = "zzz-other-r2"
        planned_current_ids = [row["id"] for row in plan["candidates"]]
        deadline = json.loads(
            _deadline_bytes(document, commit, tree, plan_ids=planned_current_ids)
        )
        deadline["lanes"].append(other_lane)
        deadline["lanes"].sort()
        deadline["plan_sha256"][other_lane] = "b" * 64
        deadline_raw = json.dumps(deadline, sort_keys=True, indent=2).encode("utf-8")
        document["campaign"]["deadline_sha256"] = hashlib.sha256(
            deadline_raw
        ).hexdigest()
        deadline_path = tmp_path / "multi-r2-deadline.json"
        deadline_path.write_bytes(deadline_raw)
        manifest_path = tmp_path / "multi-r2-manifest.txt"
        manifest_path.write_bytes(_manifest_bytes())
    
        original_committed_lane = checker._committed_lane
    
        def committed_lane(repo_root, expected_commit, lane):
            if lane == other_lane:
                return {"rigor": ["R0", "R1", "R2"]}, "assay/"
            return original_committed_lane(repo_root, expected_commit, lane)
    
        monkeypatch.setattr(checker, "_committed_lane", committed_lane)
        observed = {"digest": "c" * 64}
        recomputations = []
    
        def recompute(_repo_root, *, expected_commit, expected_tree, lane):
            recomputations.append(lane)
            assert (expected_commit, expected_tree) == (commit, tree)
            assert lane == other_lane
            return observed["digest"]
    
        monkeypatch.setattr(checker, "_committed_r2_plan_sha256", recompute)
    
        arguments = {
            "repo_root": REPO_ROOT,
            "expected_commit": commit,
            "expected_tree": tree,
            "expected_lane": SELF_QUALIFICATION,
            "expected_rigor": SELF_QUALIFICATION_RIGOR,
            "expected_version": VERSION,
            "expected_wheel_sha256": WHEEL_SHA256,
            "producer_exit": 0,
            "plan": plan,
            "r2_manifest": manifest_path,
            "deadline": deadline_path,
        }
>       with pytest.raises(
            ValueError,
            match="deadline plan_sha256 for other R2 lane 'zzz-other-r2'",
        ):
E       AssertionError: Regex pattern did not match.
E         Expected regex: "deadline plan_sha256 for other R2 lane 'zzz-other-r2'"
E         Actual message: 'committed qualification lane environment policy differs'

gate/tests/test_b105_report_check.py:1601: AssertionError
_ test_p3d_v15_source_and_consistency_checks_refuse_each_tamper[C9-<lambda>-archive-hook variables present in the qualification run] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_v15_source_and_consis8')
label = 'C9', mutate = <function <lambda> at 0x7f8c3770db10>
message = 'archive-hook variables present in the qualification run'

    @pytest.mark.parametrize(
        ("label", "mutate", "message"),
        [
            ("C1", lambda d: _r2_policy(d).__setitem__("cold_witness_kills", False),
             "B105 R2 requires cold_witness_kills true"),
            ("C2", lambda d: _r2_policy(d).__setitem__("r2_command", None),
             "B105 R2 report has no r2_command"),
            ("C3", lambda d: d["argv_declared"].append("--ignore=other.py"),
             "declared argv differs from assay.toml"),
            ("C4", lambda d: _r2_policy(d)["r2_command"].__setitem__("transform", "guess"),
             "unexpected R2 transform"),
            ("C5", lambda d: _r2_policy(d)["r2_command"]["argv_transformed"].append("--cov=left"),
             "argv_transformed is not the transform of the committed argv"),
            ("C6", lambda d: _r2_policy(d)["r2_command"].__setitem__("appended", []),
             "unexpected R2 appended argv"),
            ("C7", lambda d: _r2_policy(d)["r2_command"].__setitem__("cwd", "."),
             "unexpected R2 cwd"),
            ("C8", lambda d: _r2_policy(d)["r2_command"]["coverage_baseline"].__setitem__("collection_count", 99),
             "R2 and coverage baseline collections differ"),
            ("C9", lambda d: d["env_effective"].__setitem__("ASSAY_B105_SOURCE_COMMIT", "leak"),
             "archive-hook variables present in the qualification run"),
            ("C13", lambda d: _r2_policy(d)["r2_command"].__setitem__("config_sha256", "0" * 64),
             "R2 pytest config differs from assay/pyproject.toml at"),
        ],
    )
    def test_p3d_v15_source_and_consistency_checks_refuse_each_tamper(
        tmp_path, label, mutate, message
    ):
        document, plan = _r2_case()
        mutate(document)
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
        )
        assert result.returncode == 2, (label, result.stdout, result.stderr)
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'archive-hook variables present in the qualification run' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1800: AssertionError
_ test_p3d_v15_source_and_consistency_checks_refuse_each_tamper[C13-<lambda>-R2 pytest config differs from assay/pyproject.toml at] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_v15_source_and_consis9')
label = 'C13', mutate = <function <lambda> at 0x7f8c3770dbc0>
message = 'R2 pytest config differs from assay/pyproject.toml at'

    @pytest.mark.parametrize(
        ("label", "mutate", "message"),
        [
            ("C1", lambda d: _r2_policy(d).__setitem__("cold_witness_kills", False),
             "B105 R2 requires cold_witness_kills true"),
            ("C2", lambda d: _r2_policy(d).__setitem__("r2_command", None),
             "B105 R2 report has no r2_command"),
            ("C3", lambda d: d["argv_declared"].append("--ignore=other.py"),
             "declared argv differs from assay.toml"),
            ("C4", lambda d: _r2_policy(d)["r2_command"].__setitem__("transform", "guess"),
             "unexpected R2 transform"),
            ("C5", lambda d: _r2_policy(d)["r2_command"]["argv_transformed"].append("--cov=left"),
             "argv_transformed is not the transform of the committed argv"),
            ("C6", lambda d: _r2_policy(d)["r2_command"].__setitem__("appended", []),
             "unexpected R2 appended argv"),
            ("C7", lambda d: _r2_policy(d)["r2_command"].__setitem__("cwd", "."),
             "unexpected R2 cwd"),
            ("C8", lambda d: _r2_policy(d)["r2_command"]["coverage_baseline"].__setitem__("collection_count", 99),
             "R2 and coverage baseline collections differ"),
            ("C9", lambda d: d["env_effective"].__setitem__("ASSAY_B105_SOURCE_COMMIT", "leak"),
             "archive-hook variables present in the qualification run"),
            ("C13", lambda d: _r2_policy(d)["r2_command"].__setitem__("config_sha256", "0" * 64),
             "R2 pytest config differs from assay/pyproject.toml at"),
        ],
    )
    def test_p3d_v15_source_and_consistency_checks_refuse_each_tamper(
        tmp_path, label, mutate, message
    ):
        document, plan = _r2_case()
        mutate(document)
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
        )
        assert result.returncode == 2, (label, result.stdout, result.stderr)
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'R2 pytest config differs from assay/pyproject.toml at' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1800: AssertionError
_____ test_p3d_c10_manifest_must_be_well_formed_and_match_the_r2_baseline ______

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_c10_manifest_must_be_0')

    def test_p3d_c10_manifest_must_be_well_formed_and_match_the_r2_baseline(tmp_path):
        document, plan = _r2_case()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            manifest=b"tests/test_a.py::test_a",
        )
        assert result.returncode == 2
>       assert "R2 manifest sidecar does not match r2_baseline" in result.stderr
E       AssertionError: assert 'R2 manifest sidecar does not match r2_baseline' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1814: AssertionError
_ test_p3d_c11_every_cold_kill_must_name_the_manifest_entry_at_its_failed_index _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_c11_every_cold_kill_m0')

    def test_p3d_c11_every_cold_kill_must_name_the_manifest_entry_at_its_failed_index(tmp_path):
        document, plan = _r2_case()
        mutation = _r2_mutation(document)
        outcome = mutation["killed"][0]
        outcome["execution"] = {
            "mode": "witness-cold",
            "witness": {
                "node_id": "tests/test_wrong.py::test_wrong",
                "when": "call",
                "outcome": "failed",
                "session_exit_status": 1,
                "process_exit_status": 1,
            },
        }
        outcome["evidence"] = {"started_count": 1, "failed_call_index": 0}
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
        )
        assert result.returncode == 2
>       assert f"cold kill {outcome['candidate_id']} is not the manifest's node at its failed index" in result.stderr
E       assert "cold kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 is not the manifest's node at its failed index" in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1840: AssertionError
_ test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[missing-evidence-requires declared-command evidence] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de0')
change = 'missing-evidence', message = 'requires declared-command evidence'

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("missing-evidence", "requires declared-command evidence"),
            ("wrong-command", "requires declared-command evidence"),
            ("missing-field", "must carry the six v15 evidence fields"),
            ("missing-witness", "requires a valid failed-call witness"),
            ("outside-manifest", "witness node is not in the R2 manifest"),
            ("wrong-baseline", "candidate evidence hook_fingerprint_sha256 differs from coverage_baseline"),
        ],
    )
    def test_p3d_full_kill_requires_declared_evidence_and_manifest_witness(
        tmp_path, change, message
    ):
        document, plan = _r2_case()
        outcome = _r2_mutation(document)["killed"][0]
        if change == "missing-evidence":
            outcome.pop("evidence")
        elif change == "wrong-command":
            outcome["evidence"]["command"] = "r2"
        elif change == "missing-field":
            outcome["evidence"].pop("started_count")
        elif change == "missing-witness":
            outcome["execution"].pop("witness")
        elif change == "wrong-baseline":
            outcome["evidence"]["hook_fingerprint_sha256"] = "e" * 64
        else:
            outcome["execution"]["witness"]["node_id"] = "tests/test_wrong.py::test_wrong"
    
        refusal = message
        if change != "wrong-baseline":
            refusal = f"cold full kill {outcome['candidate_id']} {message}"
>       _refused_by_scope(tmp_path, document, plan, refusal)

gate/tests/test_b105_report_check.py:1875: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de0')
document = {'schema_version': 18, 'assay_version': '7.1.1.dev-b105', 'lane': 'self-qualification', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', ...}
plan = {'status': 'ok', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', 'tree': '9777cced8cb63cd33e7f2cc2a8b3e52918159563', 'shard': None, ...}
message = 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 requires declared-command evidence'

    def _refused_by_scope(tmp_path, document, plan, message):
        commit, tree = _own_commit_and_tree()
        candidates = plan.get("candidates") if isinstance(plan, dict) else None
        plan_ids = [
            row["id"]
            for row in candidates or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw_deadline = _deadline_bytes(document, commit, tree, plan_ids=plan_ids)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            deadline_raw=raw_deadline,
        )
        assert result.returncode == 2, result.stdout
        assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 requires declared-command evidence' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1194: AssertionError
_ test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[wrong-command-requires declared-command evidence] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de1')
change = 'wrong-command', message = 'requires declared-command evidence'

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("missing-evidence", "requires declared-command evidence"),
            ("wrong-command", "requires declared-command evidence"),
            ("missing-field", "must carry the six v15 evidence fields"),
            ("missing-witness", "requires a valid failed-call witness"),
            ("outside-manifest", "witness node is not in the R2 manifest"),
            ("wrong-baseline", "candidate evidence hook_fingerprint_sha256 differs from coverage_baseline"),
        ],
    )
    def test_p3d_full_kill_requires_declared_evidence_and_manifest_witness(
        tmp_path, change, message
    ):
        document, plan = _r2_case()
        outcome = _r2_mutation(document)["killed"][0]
        if change == "missing-evidence":
            outcome.pop("evidence")
        elif change == "wrong-command":
            outcome["evidence"]["command"] = "r2"
        elif change == "missing-field":
            outcome["evidence"].pop("started_count")
        elif change == "missing-witness":
            outcome["execution"].pop("witness")
        elif change == "wrong-baseline":
            outcome["evidence"]["hook_fingerprint_sha256"] = "e" * 64
        else:
            outcome["execution"]["witness"]["node_id"] = "tests/test_wrong.py::test_wrong"
    
        refusal = message
        if change != "wrong-baseline":
            refusal = f"cold full kill {outcome['candidate_id']} {message}"
>       _refused_by_scope(tmp_path, document, plan, refusal)

gate/tests/test_b105_report_check.py:1875: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de1')
document = {'schema_version': 18, 'assay_version': '7.1.1.dev-b105', 'lane': 'self-qualification', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', ...}
plan = {'status': 'ok', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', 'tree': '9777cced8cb63cd33e7f2cc2a8b3e52918159563', 'shard': None, ...}
message = 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 requires declared-command evidence'

    def _refused_by_scope(tmp_path, document, plan, message):
        commit, tree = _own_commit_and_tree()
        candidates = plan.get("candidates") if isinstance(plan, dict) else None
        plan_ids = [
            row["id"]
            for row in candidates or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw_deadline = _deadline_bytes(document, commit, tree, plan_ids=plan_ids)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            deadline_raw=raw_deadline,
        )
        assert result.returncode == 2, result.stdout
        assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 requires declared-command evidence' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1194: AssertionError
_ test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[missing-field-must carry the six v15 evidence fields] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de2')
change = 'missing-field', message = 'must carry the six v15 evidence fields'

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("missing-evidence", "requires declared-command evidence"),
            ("wrong-command", "requires declared-command evidence"),
            ("missing-field", "must carry the six v15 evidence fields"),
            ("missing-witness", "requires a valid failed-call witness"),
            ("outside-manifest", "witness node is not in the R2 manifest"),
            ("wrong-baseline", "candidate evidence hook_fingerprint_sha256 differs from coverage_baseline"),
        ],
    )
    def test_p3d_full_kill_requires_declared_evidence_and_manifest_witness(
        tmp_path, change, message
    ):
        document, plan = _r2_case()
        outcome = _r2_mutation(document)["killed"][0]
        if change == "missing-evidence":
            outcome.pop("evidence")
        elif change == "wrong-command":
            outcome["evidence"]["command"] = "r2"
        elif change == "missing-field":
            outcome["evidence"].pop("started_count")
        elif change == "missing-witness":
            outcome["execution"].pop("witness")
        elif change == "wrong-baseline":
            outcome["evidence"]["hook_fingerprint_sha256"] = "e" * 64
        else:
            outcome["execution"]["witness"]["node_id"] = "tests/test_wrong.py::test_wrong"
    
        refusal = message
        if change != "wrong-baseline":
            refusal = f"cold full kill {outcome['candidate_id']} {message}"
>       _refused_by_scope(tmp_path, document, plan, refusal)

gate/tests/test_b105_report_check.py:1875: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de2')
document = {'schema_version': 18, 'assay_version': '7.1.1.dev-b105', 'lane': 'self-qualification', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', ...}
plan = {'status': 'ok', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', 'tree': '9777cced8cb63cd33e7f2cc2a8b3e52918159563', 'shard': None, ...}
message = 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 must carry the six v15 evidence fields'

    def _refused_by_scope(tmp_path, document, plan, message):
        commit, tree = _own_commit_and_tree()
        candidates = plan.get("candidates") if isinstance(plan, dict) else None
        plan_ids = [
            row["id"]
            for row in candidates or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw_deadline = _deadline_bytes(document, commit, tree, plan_ids=plan_ids)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            deadline_raw=raw_deadline,
        )
        assert result.returncode == 2, result.stdout
        assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 must carry the six v15 evidence fields' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1194: AssertionError
_ test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[missing-witness-requires a valid failed-call witness] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de3')
change = 'missing-witness', message = 'requires a valid failed-call witness'

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("missing-evidence", "requires declared-command evidence"),
            ("wrong-command", "requires declared-command evidence"),
            ("missing-field", "must carry the six v15 evidence fields"),
            ("missing-witness", "requires a valid failed-call witness"),
            ("outside-manifest", "witness node is not in the R2 manifest"),
            ("wrong-baseline", "candidate evidence hook_fingerprint_sha256 differs from coverage_baseline"),
        ],
    )
    def test_p3d_full_kill_requires_declared_evidence_and_manifest_witness(
        tmp_path, change, message
    ):
        document, plan = _r2_case()
        outcome = _r2_mutation(document)["killed"][0]
        if change == "missing-evidence":
            outcome.pop("evidence")
        elif change == "wrong-command":
            outcome["evidence"]["command"] = "r2"
        elif change == "missing-field":
            outcome["evidence"].pop("started_count")
        elif change == "missing-witness":
            outcome["execution"].pop("witness")
        elif change == "wrong-baseline":
            outcome["evidence"]["hook_fingerprint_sha256"] = "e" * 64
        else:
            outcome["execution"]["witness"]["node_id"] = "tests/test_wrong.py::test_wrong"
    
        refusal = message
        if change != "wrong-baseline":
            refusal = f"cold full kill {outcome['candidate_id']} {message}"
>       _refused_by_scope(tmp_path, document, plan, refusal)

gate/tests/test_b105_report_check.py:1875: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de3')
document = {'schema_version': 18, 'assay_version': '7.1.1.dev-b105', 'lane': 'self-qualification', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', ...}
plan = {'status': 'ok', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', 'tree': '9777cced8cb63cd33e7f2cc2a8b3e52918159563', 'shard': None, ...}
message = 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 requires a valid failed-call witness'

    def _refused_by_scope(tmp_path, document, plan, message):
        commit, tree = _own_commit_and_tree()
        candidates = plan.get("candidates") if isinstance(plan, dict) else None
        plan_ids = [
            row["id"]
            for row in candidates or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw_deadline = _deadline_bytes(document, commit, tree, plan_ids=plan_ids)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            deadline_raw=raw_deadline,
        )
        assert result.returncode == 2, result.stdout
        assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 requires a valid failed-call witness' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1194: AssertionError
_ test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[outside-manifest-witness node is not in the R2 manifest] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de4')
change = 'outside-manifest', message = 'witness node is not in the R2 manifest'

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("missing-evidence", "requires declared-command evidence"),
            ("wrong-command", "requires declared-command evidence"),
            ("missing-field", "must carry the six v15 evidence fields"),
            ("missing-witness", "requires a valid failed-call witness"),
            ("outside-manifest", "witness node is not in the R2 manifest"),
            ("wrong-baseline", "candidate evidence hook_fingerprint_sha256 differs from coverage_baseline"),
        ],
    )
    def test_p3d_full_kill_requires_declared_evidence_and_manifest_witness(
        tmp_path, change, message
    ):
        document, plan = _r2_case()
        outcome = _r2_mutation(document)["killed"][0]
        if change == "missing-evidence":
            outcome.pop("evidence")
        elif change == "wrong-command":
            outcome["evidence"]["command"] = "r2"
        elif change == "missing-field":
            outcome["evidence"].pop("started_count")
        elif change == "missing-witness":
            outcome["execution"].pop("witness")
        elif change == "wrong-baseline":
            outcome["evidence"]["hook_fingerprint_sha256"] = "e" * 64
        else:
            outcome["execution"]["witness"]["node_id"] = "tests/test_wrong.py::test_wrong"
    
        refusal = message
        if change != "wrong-baseline":
            refusal = f"cold full kill {outcome['candidate_id']} {message}"
>       _refused_by_scope(tmp_path, document, plan, refusal)

gate/tests/test_b105_report_check.py:1875: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de4')
document = {'schema_version': 18, 'assay_version': '7.1.1.dev-b105', 'lane': 'self-qualification', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', ...}
plan = {'status': 'ok', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', 'tree': '9777cced8cb63cd33e7f2cc2a8b3e52918159563', 'shard': None, ...}
message = 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 witness node is not in the R2 manifest'

    def _refused_by_scope(tmp_path, document, plan, message):
        commit, tree = _own_commit_and_tree()
        candidates = plan.get("candidates") if isinstance(plan, dict) else None
        plan_ids = [
            row["id"]
            for row in candidates or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw_deadline = _deadline_bytes(document, commit, tree, plan_ids=plan_ids)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            deadline_raw=raw_deadline,
        )
        assert result.returncode == 2, result.stdout
        assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'cold full kill e00f116aa61e6bdb2066417ee4bd95b89ec81840791a2e3a7bcf9168cdbc1d87 witness node is not in the R2 manifest' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1194: AssertionError
_ test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[wrong-baseline-candidate evidence hook_fingerprint_sha256 differs from coverage_baseline] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de5')
change = 'wrong-baseline'
message = 'candidate evidence hook_fingerprint_sha256 differs from coverage_baseline'

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("missing-evidence", "requires declared-command evidence"),
            ("wrong-command", "requires declared-command evidence"),
            ("missing-field", "must carry the six v15 evidence fields"),
            ("missing-witness", "requires a valid failed-call witness"),
            ("outside-manifest", "witness node is not in the R2 manifest"),
            ("wrong-baseline", "candidate evidence hook_fingerprint_sha256 differs from coverage_baseline"),
        ],
    )
    def test_p3d_full_kill_requires_declared_evidence_and_manifest_witness(
        tmp_path, change, message
    ):
        document, plan = _r2_case()
        outcome = _r2_mutation(document)["killed"][0]
        if change == "missing-evidence":
            outcome.pop("evidence")
        elif change == "wrong-command":
            outcome["evidence"]["command"] = "r2"
        elif change == "missing-field":
            outcome["evidence"].pop("started_count")
        elif change == "missing-witness":
            outcome["execution"].pop("witness")
        elif change == "wrong-baseline":
            outcome["evidence"]["hook_fingerprint_sha256"] = "e" * 64
        else:
            outcome["execution"]["witness"]["node_id"] = "tests/test_wrong.py::test_wrong"
    
        refusal = message
        if change != "wrong-baseline":
            refusal = f"cold full kill {outcome['candidate_id']} {message}"
>       _refused_by_scope(tmp_path, document, plan, refusal)

gate/tests/test_b105_report_check.py:1875: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_full_kill_requires_de5')
document = {'schema_version': 18, 'assay_version': '7.1.1.dev-b105', 'lane': 'self-qualification', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', ...}
plan = {'status': 'ok', 'commit': '70f6e5b32d24e1eb6818bfd39bd418764c864b2f', 'tree': '9777cced8cb63cd33e7f2cc2a8b3e52918159563', 'shard': None, ...}
message = 'candidate evidence hook_fingerprint_sha256 differs from coverage_baseline'

    def _refused_by_scope(tmp_path, document, plan, message):
        commit, tree = _own_commit_and_tree()
        candidates = plan.get("candidates") if isinstance(plan, dict) else None
        plan_ids = [
            row["id"]
            for row in candidates or []
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        ]
        raw_deadline = _deadline_bytes(document, commit, tree, plan_ids=plan_ids)
        document["campaign"]["deadline_sha256"] = hashlib.sha256(raw_deadline).hexdigest()
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
            deadline_raw=raw_deadline,
        )
        assert result.returncode == 2, result.stdout
        assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
>       assert message in result.stderr, result.stderr
E       AssertionError: B105_REPORT_REJECTED=committed qualification lane environment policy differs
E         
E       assert 'candidate evidence hook_fingerprint_sha256 differs from coverage_baseline' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1194: AssertionError
_______ test_p3d_c12_survivor_evidence_must_match_its_selected_baseline ________

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_p3d_c12_survivor_evidence0')

    def test_p3d_c12_survivor_evidence_must_match_its_selected_baseline(tmp_path):
        document, plan = _r2_case()
        mutation = _r2_mutation(document)
        outcome = mutation["killed"].pop()
        mutation["survived"].append(outcome)
        outcome["execution"] = {"mode": "full"}
        outcome["evidence"] = {
            "command": "r2",
            "collection_count": 2,
            "collection_sha256": "f" * 64,
            "hook_fingerprint_sha256": "c" * 64,
            "started_count": None,
            "failed_call_index": None,
        }
        result = _run_checker(
            tmp_path,
            document,
            lane=SELF_QUALIFICATION,
            rigor=SELF_QUALIFICATION_RIGOR,
            plan=plan,
        )
        assert result.returncode == 2
>       assert "candidate evidence collection differs from r2_baseline" in result.stderr
E       AssertionError: assert 'candidate evidence collection differs from r2_baseline' in 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n'
E        +  where 'B105_REPORT_REJECTED=committed qualification lane environment policy differs\n' = CompletedProcess(args=['/tmp/tmp.Zd3NPZuKyf/run-venv/bin/python', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32...'0'], returncode=2, stdout='', stderr='B105_REPORT_REJECTED=committed qualification lane environment policy differs\n').stderr

gate/tests/test_b105_report_check.py:1900: AssertionError
__ test_release_gate_checks_remote_verdict_and_returns_lane_status[PASS-0-0] ___

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_checks_remot0')
verdict = 'PASS', lane_status = 0, expected_status = 0

    @pytest.mark.parametrize(
        ("verdict", "lane_status", "expected_status"),
        [("PASS", 0, 0), ("FAIL", 1, 1), ("FAIL", 0, 3), ("UNKNOWN", 0, 3)],
    )
    def test_release_gate_checks_remote_verdict_and_returns_lane_status(
        tmp_path: Path, verdict: str, lane_status: int, expected_status: int
    ) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, verdict=verdict, lane_status=lane_status)
        proc = _run(tmp_path, "self-qualification-provisional", env)
    
>       assert proc.returncode == expected_status, proc.stderr
E       AssertionError: /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable
E         
E       assert 1 == 0
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:241: AssertionError
__ test_release_gate_checks_remote_verdict_and_returns_lane_status[FAIL-1-1] ___

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_checks_remot1')
verdict = 'FAIL', lane_status = 1, expected_status = 1

    @pytest.mark.parametrize(
        ("verdict", "lane_status", "expected_status"),
        [("PASS", 0, 0), ("FAIL", 1, 1), ("FAIL", 0, 3), ("UNKNOWN", 0, 3)],
    )
    def test_release_gate_checks_remote_verdict_and_returns_lane_status(
        tmp_path: Path, verdict: str, lane_status: int, expected_status: int
    ) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, verdict=verdict, lane_status=lane_status)
        proc = _run(tmp_path, "self-qualification-provisional", env)
    
        assert proc.returncode == expected_status, proc.stderr
>       assert (f"refusing self-qualification-provisional verdict {verdict}" in proc.stderr) is (
            verdict != "PASS"
        )
E       AssertionError: assert ('refusing self-qualification-provisional verdict FAIL' in '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n') is ('FAIL' != 'PASS')
E        +  where '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n' = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').stderr

gate/tests/test_release_gate.py:242: AssertionError
__ test_release_gate_checks_remote_verdict_and_returns_lane_status[FAIL-0-3] ___

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_checks_remot2')
verdict = 'FAIL', lane_status = 0, expected_status = 3

    @pytest.mark.parametrize(
        ("verdict", "lane_status", "expected_status"),
        [("PASS", 0, 0), ("FAIL", 1, 1), ("FAIL", 0, 3), ("UNKNOWN", 0, 3)],
    )
    def test_release_gate_checks_remote_verdict_and_returns_lane_status(
        tmp_path: Path, verdict: str, lane_status: int, expected_status: int
    ) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, verdict=verdict, lane_status=lane_status)
        proc = _run(tmp_path, "self-qualification-provisional", env)
    
>       assert proc.returncode == expected_status, proc.stderr
E       AssertionError: /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable
E         
E       assert 1 == 3
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:241: AssertionError
_ test_release_gate_checks_remote_verdict_and_returns_lane_status[UNKNOWN-0-3] _

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_checks_remot3')
verdict = 'UNKNOWN', lane_status = 0, expected_status = 3

    @pytest.mark.parametrize(
        ("verdict", "lane_status", "expected_status"),
        [("PASS", 0, 0), ("FAIL", 1, 1), ("FAIL", 0, 3), ("UNKNOWN", 0, 3)],
    )
    def test_release_gate_checks_remote_verdict_and_returns_lane_status(
        tmp_path: Path, verdict: str, lane_status: int, expected_status: int
    ) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, verdict=verdict, lane_status=lane_status)
        proc = _run(tmp_path, "self-qualification-provisional", env)
    
>       assert proc.returncode == expected_status, proc.stderr
E       AssertionError: /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable
E         
E       assert 1 == 3
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:241: AssertionError
____ test_release_gate_scrubs_publisher_and_gh_credentials_from_remote_gate ____

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_scrubs_publi0')

    def test_release_gate_scrubs_publisher_and_gh_credentials_from_remote_gate(
        tmp_path: Path,
    ) -> None:
        env = _environment(tmp_path)
        credentials = {
            "GITHUB_PUSH_PAT": "publisher-secret",
            "GITHUB_TOKEN": "github-token-secret",
            "CMRU_GIT_AUTH_TOKEN": "git-auth-secret",
            "GH_TOKEN": "gh-token-secret",
            "GH_ENTERPRISE_TOKEN": "gh-enterprise-secret",
        }
        env.update(credentials)
        proc = _run(tmp_path, "tester-unified", env)
    
>       assert proc.returncode == 0, proc.stderr
E       AssertionError: /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable
E         
E       assert 1 == 0
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:276: AssertionError
_______________ test_release_gate_refuses_a_remote_sha_mismatch ________________

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_refuses_a_re0')

    def test_release_gate_refuses_a_remote_sha_mismatch(tmp_path: Path) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, meta_sha="f" * 40)
        proc = _run(tmp_path, "tester-unified", env)
    
>       assert proc.returncode == 3
E       AssertionError: assert 1 == 3
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:304: AssertionError
____________ test_release_gate_rejects_remote_vs_lane_exit_mismatch ____________

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_rejects_remo0')

    def test_release_gate_rejects_remote_vs_lane_exit_mismatch(tmp_path: Path) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, remote_status=0, lane_status=1)
    
        proc = _run(tmp_path, "tester-unified", env)
    
>       assert proc.returncode == 3
E       AssertionError: assert 1 == 3
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:330: AssertionError
_______ test_release_gate_propagates_remote_failure_without_result_files _______

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_propagates_r0')

    def test_release_gate_propagates_remote_failure_without_result_files(
        tmp_path: Path,
    ) -> None:
        env = _environment(tmp_path)
        _configure_fake(env, remote_status=75, write_meta=False, write_result=False)
    
        proc = _run(tmp_path, "tester-unified", env)
    
>       assert proc.returncode == 75
E       AssertionError: assert 1 == 75
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:342: AssertionError
______ test_release_gate_cancels_unattended_remote_job_after_failed_polls ______

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_cancels_unat0')

    def test_release_gate_cancels_unattended_remote_job_after_failed_polls(
        tmp_path: Path,
    ) -> None:
        env = _environment(tmp_path)
        secret_names = (
            "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
            "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
        )
        env.update({name: f"secret-{name}" for name in secret_names})
        _configure_fake(env, remote_status=3, write_meta=True, write_result=False)
    
        proc = _run(tmp_path, "tester-unified", env)
    
>       assert proc.returncode == 3
E       AssertionError: assert 1 == 3
E        +  where 1 = CompletedProcess(args=['/bin/bash', '/workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f...y-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tools/release-gate.sh: line 64: HOME: unbound variable\n').returncode

gate/tests/test_release_gate.py:361: AssertionError
__ test_release_gate_forwards_real_signals_and_cancels_before_waiting[2-130] ___

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_forwards_rea0')
sig = <Signals.SIGINT: 2>, expected_status = 130

    @pytest.mark.parametrize(
        ("sig", "expected_status"),
        [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
    )
    def test_release_gate_forwards_real_signals_and_cancels_before_waiting(
        tmp_path: Path,
        sig: signal.Signals,
        expected_status: int,
    ) -> None:
        env = _environment(tmp_path)
        secret_names = (
            "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
            "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
        )
        env.update({name: f"secret-{name}" for name in secret_names})
        ready_fifo = tmp_path / "remote-ready.fifo"
        os.mkfifo(ready_fifo)
        _configure_fake(env, signal_mode=True, ready_fifo=str(ready_fifo))
        wait_hook = tmp_path / "bash-env.sh"
        wait_calls = tmp_path / "wrapper-wait-calls.txt"
        wait_reaped = tmp_path / "wrapper-wait-completed.txt"
        wait_hook.write_text(
            "wait() {\n"
            '  local child_pid=${1:-unknown} status=0\n'
            '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_CALLS"\n'
            '  builtin wait "$@" || status=$?\n'
            '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_REAPED"\n'
            '  return "$status"\n'
            "}\n",
            encoding="utf-8",
        )
        env.update(
            {
                "BASH_ENV": str(wait_hook),
                "ASSAY_WAIT_CALLS": str(wait_calls),
                "ASSAY_WAIT_REAPED": str(wait_reaped),
            }
        )
        ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
        stdout_path = tmp_path / "wrapper.stdout"
        stderr_path = tmp_path / "wrapper.stderr"
        stdout_stream = stdout_path.open("w", encoding="utf-8")
        stderr_stream = stderr_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            ["/bin/bash", str(SCRIPT), "tester-unified"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=stdout_stream,
            stderr=stderr_stream,
            start_new_session=True,
        )
        try:
            ready, _, _ = select.select([ready_fd], [], [], 15)
>           assert ready, "fake remote-gate did not reach its signal-ready handshake"
E           AssertionError: fake remote-gate did not reach its signal-ready handshake
E           assert []

gate/tests/test_release_gate.py:432: AssertionError
__ test_release_gate_forwards_real_signals_and_cancels_before_waiting[15-143] __

tmp_path = PosixPath('/tmp/pytest-of-tester/pytest-1/test_release_gate_forwards_rea1')
sig = <Signals.SIGTERM: 15>, expected_status = 143

    @pytest.mark.parametrize(
        ("sig", "expected_status"),
        [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
    )
    def test_release_gate_forwards_real_signals_and_cancels_before_waiting(
        tmp_path: Path,
        sig: signal.Signals,
        expected_status: int,
    ) -> None:
        env = _environment(tmp_path)
        secret_names = (
            "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
            "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
        )
        env.update({name: f"secret-{name}" for name in secret_names})
        ready_fifo = tmp_path / "remote-ready.fifo"
        os.mkfifo(ready_fifo)
        _configure_fake(env, signal_mode=True, ready_fifo=str(ready_fifo))
        wait_hook = tmp_path / "bash-env.sh"
        wait_calls = tmp_path / "wrapper-wait-calls.txt"
        wait_reaped = tmp_path / "wrapper-wait-completed.txt"
        wait_hook.write_text(
            "wait() {\n"
            '  local child_pid=${1:-unknown} status=0\n'
            '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_CALLS"\n'
            '  builtin wait "$@" || status=$?\n'
            '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_REAPED"\n'
            '  return "$status"\n'
            "}\n",
            encoding="utf-8",
        )
        env.update(
            {
                "BASH_ENV": str(wait_hook),
                "ASSAY_WAIT_CALLS": str(wait_calls),
                "ASSAY_WAIT_REAPED": str(wait_reaped),
            }
        )
        ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
        stdout_path = tmp_path / "wrapper.stdout"
        stderr_path = tmp_path / "wrapper.stderr"
        stdout_stream = stdout_path.open("w", encoding="utf-8")
        stderr_stream = stderr_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            ["/bin/bash", str(SCRIPT), "tester-unified"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=stdout_stream,
            stderr=stderr_stream,
            start_new_session=True,
        )
        try:
            ready, _, _ = select.select([ready_fd], [], [], 15)
>           assert ready, "fake remote-gate did not reach its signal-ready handshake"
E           AssertionError: fake remote-gate did not reach its signal-ready handshake
E           assert []

gate/tests/test_release_gate.py:432: AssertionError
=============================== warnings summary ===============================
tests/core/test_process_tree.py::test_failed_cleanup_blocks_later_windows_and_preserves_subreaper
  /workspaces/vbpub/.worktrees/rg-assay-rel-70f6e5b32d24-tester-unified-0to7hfh-70f6e5b32d24/assay/tests/core/test_process_tree.py:364: DeprecationWarning: This process (pid=279) is multi-threaded, use of fork() may lead to deadlocks in the child.
    pid = os.fork()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/core/test_docs_examples_and_vocabulary.py::test_provisional_self_qualification_is_documented_across_the_public_docs
FAILED gate/tests/test_b105_report_check.py::test_accepts_only_exact_pass_for_both_registered_lanes[self-qualification-rigor1]
FAILED gate/tests/test_b105_report_check.py::test_receipt_reader_is_bounded_regular_and_nofollow[regular-full]
FAILED gate/tests/test_b105_report_check.py::test_plan_reader_is_bounded_regular_and_nofollow[regular]
FAILED gate/tests/test_b105_report_check.py::test_report_reader_is_bounded_regular_and_nofollow[regular]
FAILED gate/tests/test_b105_report_check.py::test_accepts_full_3760_candidate_plan_report_and_deadline
FAILED gate/tests/test_b105_report_check.py::test_o11_the_plan_that_agrees_with_the_report_is_accepted
FAILED gate/tests/test_b105_report_check.py::test_reserved_non_null_equivalence_ledger_is_refused_until_b110_p10b
FAILED gate/tests/test_b105_report_check.py::test_full_report_checker_refuses_non_regular_or_symlink_manifests[fifo]
FAILED gate/tests/test_b105_report_check.py::test_full_report_checker_refuses_non_regular_or_symlink_manifests[symlink]
FAILED gate/tests/test_b105_report_check.py::test_full_report_checker_refuses_oversized_binding_files[manifest]
FAILED gate/tests/test_b105_report_check.py::test_other_r2_deadline_digest_is_recomputed_after_existing_report_checks
FAILED gate/tests/test_b105_report_check.py::test_p3d_v15_source_and_consistency_checks_refuse_each_tamper[C9-<lambda>-archive-hook variables present in the qualification run]
FAILED gate/tests/test_b105_report_check.py::test_p3d_v15_source_and_consistency_checks_refuse_each_tamper[C13-<lambda>-R2 pytest config differs from assay/pyproject.toml at]
FAILED gate/tests/test_b105_report_check.py::test_p3d_c10_manifest_must_be_well_formed_and_match_the_r2_baseline
FAILED gate/tests/test_b105_report_check.py::test_p3d_c11_every_cold_kill_must_name_the_manifest_entry_at_its_failed_index
FAILED gate/tests/test_b105_report_check.py::test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[missing-evidence-requires declared-command evidence]
FAILED gate/tests/test_b105_report_check.py::test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[wrong-command-requires declared-command evidence]
FAILED gate/tests/test_b105_report_check.py::test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[missing-field-must carry the six v15 evidence fields]
FAILED gate/tests/test_b105_report_check.py::test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[missing-witness-requires a valid failed-call witness]
FAILED gate/tests/test_b105_report_check.py::test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[outside-manifest-witness node is not in the R2 manifest]
FAILED gate/tests/test_b105_report_check.py::test_p3d_full_kill_requires_declared_evidence_and_manifest_witness[wrong-baseline-candidate evidence hook_fingerprint_sha256 differs from coverage_baseline]
FAILED gate/tests/test_b105_report_check.py::test_p3d_c12_survivor_evidence_must_match_its_selected_baseline
FAILED gate/tests/test_release_gate.py::test_release_gate_checks_remote_verdict_and_returns_lane_status[PASS-0-0]
FAILED gate/tests/test_release_gate.py::test_release_gate_checks_remote_verdict_and_returns_lane_status[FAIL-1-1]
FAILED gate/tests/test_release_gate.py::test_release_gate_checks_remote_verdict_and_returns_lane_status[FAIL-0-3]
FAILED gate/tests/test_release_gate.py::test_release_gate_checks_remote_verdict_and_returns_lane_status[UNKNOWN-0-3]
FAILED gate/tests/test_release_gate.py::test_release_gate_scrubs_publisher_and_gh_credentials_from_remote_gate
FAILED gate/tests/test_release_gate.py::test_release_gate_refuses_a_remote_sha_mismatch
FAILED gate/tests/test_release_gate.py::test_release_gate_rejects_remote_vs_lane_exit_mismatch
FAILED gate/tests/test_release_gate.py::test_release_gate_propagates_remote_failure_without_result_files
FAILED gate/tests/test_release_gate.py::test_release_gate_cancels_unattended_remote_job_after_failed_polls
FAILED gate/tests/test_release_gate.py::test_release_gate_forwards_real_signals_and_cancels_before_waiting[2-130]
FAILED gate/tests/test_release_gate.py::test_release_gate_forwards_real_signals_and_cancels_before_waiting[15-143]
34 failed, 11050 passed, 125 skipped, 1 warning in 980.49s (0:16:20)

  stdout dropped bytes: 26256
  captured stderr tail:

  stderr dropped bytes: 0
ASSAY_GATE_DIAGNOSTIC=worktree-status-after-the-lane
ASSAY_GATE_DIAGNOSTIC=worktree-untracked-by-assays-own-query
ASSAY_GATE_CONTAINER_EXIT=1
run-gate: lane 'tester-unified' verdict FAIL; exit_code 1; log /tmp/run-gate/lanes/tester-unified/912f52d7d18f1f4515974829206cc2e7.log
