"""CIU-20 (O1, S17.3) — machine-readable provenance verdict.

Two layers:

- `deploy.verify_running_provenance` / `ProvenanceResult` — the seven frozen
  grammar shapes (`nyxloom-trove/carve-assets/.../provenance-*.json`,
  carver-owned, read-only here) reproduced via the SAME docker seam the rest
  of the suite uses: `monkeypatch.setattr(deploy.procutil, "docker", fake)`
  (precedent: tests/tests/test_ciu_deploy_actions.py:1368). Fixtures are
  compared as PARSED JSON (`json.load`), never byte-for-byte.
- `cli._provenance` — the ONLY place that turns a verdict into prose, a
  raise, or a warning; `--json` must print ONLY the JSON document, never
  prose mixed onto the same stream.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402
from ciu import deploy  # noqa: E402
from ciu import dev  # noqa: E402

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "nyxloom-trove" / "carve-assets" / "ciu-P01-worktree-isolation-primitives"
)


def _fixture(name: str) -> dict:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _docker_ps(rows):
    """Fake procutil.docker for both `ps` (rows) and `image inspect` (labels)."""
    names, labels = rows

    def fake(cmd, **_kw):
        if cmd and cmd[0] == "ps":
            out = "".join(f"{n}\t{i}\t{p}\n" for n, i, p in names)
            return subprocess.CompletedProcess(cmd, 0, out, "")
        image = cmd[2] if len(cmd) > 2 else ""
        if image not in labels or not labels[image]:
            return subprocess.CompletedProcess(cmd, 0, "<no value>\n", "")
        return subprocess.CompletedProcess(cmd, 0, labels[image] + "\n", "")
    return fake


INSTANCE = "dstdns-dev"


# ---------------------------------------------------------------------------
# The seven frozen fixture shapes, reproduced via deploy.verify_running_provenance
# ---------------------------------------------------------------------------


class TestProvenanceResultGrammar:
    def test_verified_match(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [
                ("dstdns-dev-controller-1", "dstdns/controller:1b369e23", "dstdns-dev"),
                ("dstdns-dev-worker-1", "dstdns/worker:1b369e23", "dstdns-dev"),
            ],
            {
                "dstdns/controller:1b369e23": "1b369e23",
                "dstdns/worker:1b369e23": "1b369e23",
            },
        )))
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture("provenance-verified-match.json")

    def test_mismatch(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [
                ("dstdns-dev-controller-1", "dstdns/controller:aaaa1111", "dstdns-dev"),
                ("dstdns-dev-db-init-1", "postgres:16", "dstdns-dev"),
                ("dstdns-dev-worker-1", "dstdns/worker:1b369e23", "dstdns-dev"),
            ],
            {
                "dstdns/controller:aaaa1111": "aaaa1111",
                "postgres:16": "",
                "dstdns/worker:1b369e23": "1b369e23",
            },
        )))
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture("provenance-mismatch.json")

    def test_not_verified_dirty(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23-dirty")
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture("provenance-not-verified-dirty.json")

    def test_not_verified_unknown(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "dev")
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture("provenance-not-verified-unknown.json")

    def test_not_verified_no_evidence_docker_unavailable(self, monkeypatch):
        """The discriminator: enumeration could not run AT ALL (FileNotFoundError
        from procutil.docker) -> containers: null. Never widened back to []."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")

        def boom(*_a, **_kw):
            raise FileNotFoundError("docker")
        monkeypatch.setattr(deploy.procutil, "docker", boom)
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture("provenance-not-verified-no-evidence.json")
        assert result.containers is None

    def test_not_verified_no_evidence_all_unlabelled(self, monkeypatch):
        """The OTHER discriminator: enumeration SUCCEEDED but found only
        unlabelled containers -> containers is the non-null (but no-match/
        no-mismatch) list. Distinct from the docker-unavailable shape above."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("dstdns-dev-db-1", "postgres:16", "dstdns-dev")],
            {"postgres:16": ""},
        )))
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture(
            "provenance-not-verified-no-evidence-unlabelled.json"
        )
        assert result.containers is not None and result.containers != []

    def test_not_verified_no_evidence_docker_ps_nonzero_exit(self, monkeypatch):
        """The OTHER enumeration-failure path: `docker ps` runs but exits
        non-zero (no exception raised) -> also containers: null, never widened
        back to []."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")

        def fake(cmd, **_kw):
            return subprocess.CompletedProcess(cmd, 1, "", "docker daemon not running")
        monkeypatch.setattr(deploy.procutil, "docker", fake)
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.overall == "not-verified-no-evidence"
        assert result.containers is None

    def test_refused_no_identity_shape(self):
        """Built by cli._provenance BEFORE verify_running_provenance is ever
        called (no project_prefix exists yet to scope a check with)."""
        result = deploy.ProvenanceResult(
            schema_version=1, instance=None, commit_under_test=None,
            tree_state=None, containers=None, overall="refused-no-identity",
        )
        assert result.to_dict() == _fixture("provenance-refused-no-identity.json")

    def test_containers_sorted_by_name_ascending_regardless_of_docker_order(
        self, monkeypatch
    ):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "rev1")
        # docker returns them in REVERSE alphabetical order
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [
                ("z-service", "img:rev1", "dstdns-dev"),
                ("a-service", "img:rev1", "dstdns-dev"),
            ],
            {"img:rev1": "rev1"},
        )))
        result = deploy.verify_running_provenance(INSTANCE)
        assert [c.name for c in result.containers] == ["a-service", "z-service"]

    def test_never_raises_and_never_returns_none(self, monkeypatch):
        """The load-bearing contract change: verify_running_provenance ALWAYS
        builds and returns a result, never bare None, never raising —
        including on a genuine mismatch (that decision moved to cli.py)."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "rev1")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("svc", "img:other", "dstdns-dev")],
            {"img:other": "rev2"},
        )))
        result = deploy.verify_running_provenance(INSTANCE)  # must not raise
        assert result is not None
        assert result.overall == "mismatch"

    def test_verified_match_requires_at_least_one_match_not_zero_checked(
        self, monkeypatch
    ):
        """A green verdict is NEVER emitted from zero checked containers —
        this is the false-green CIU-20 exists to close."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "rev1")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(([], {})))
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.overall != "verified-match"
        assert result.overall == "not-verified-no-evidence"


# ---------------------------------------------------------------------------
# cli._provenance — the ONLY place that decides prose/raise/warn
# ---------------------------------------------------------------------------


def _config(project="dstdns", env_tag="dev"):
    cfg = {}
    if project or env_tag:
        cfg["deploy"] = {}
        if project:
            cfg["deploy"]["project_name"] = project
        if env_tag:
            cfg["deploy"]["environment_tag"] = env_tag
    return cfg


class TestCliProvenanceDispatch:
    def _patch_repo_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)

    def test_json_flag_is_store_true_like_diagnose(self, monkeypatch, tmp_path, capsys):
        """Precedent: cli.py:726's `ciu diagnose --json` (store_true) — NOT a
        [PATH|-] positional. `--json` alone must be a complete, valid parse."""
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "rev1", "clean", [], "not-verified-no-evidence",
            ),
        )
        code = cli._provenance(["--json"])
        assert code == 0
        out = capsys.readouterr().out
        doc = json.loads(out)  # must parse as a single JSON document
        assert doc["overall"] == "not-verified-no-evidence"

    def test_json_prints_only_json_no_prose_mixed_in(self, monkeypatch, tmp_path, capsys):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "rev1", "clean",
                [deploy.ContainerProvenance("svc", "img:rev1", "rev1", "match")],
                "verified-match",
            ),
        )
        code = cli._provenance(["--json"])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        # the ENTIRE stdout must be exactly one JSON document — no "provenance
        # OK" line, no [WARN], nothing else
        doc = json.loads(captured.out)
        assert doc["overall"] == "verified-match"

    def test_refused_no_identity_prose_is_byte_identical_to_before(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config(project=None))
        code = cli._provenance([])
        assert code == 2
        err = capsys.readouterr().err
        assert err == (
            "ciu provenance: deploy.project_name and deploy.environment_tag "
            "are required to scope the check to this instance (S8.7).\n"
        )

    def test_refused_no_identity_json_emits_the_frozen_shape(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config(env_tag=None))
        code = cli._provenance(["--json"])
        assert code == 2
        doc = json.loads(capsys.readouterr().out)
        assert doc == _fixture("provenance-refused-no-identity.json")

    def test_config_load_failure_unchanged(self, monkeypatch, tmp_path, capsys):
        self._patch_repo_root(monkeypatch, tmp_path)

        def boom(repo_root):
            raise ValueError("bad toml")
        monkeypatch.setattr(deploy, "load_global_config", boom)
        code = cli._provenance([])
        assert code == 2
        assert "could not load the global config" in capsys.readouterr().err

    def test_verified_match_prose_unchanged(self, monkeypatch, tmp_path, capsys):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "abc12345", "clean",
                [deploy.ContainerProvenance("svc", "img:abc12345", "abc12345", "match")],
                "verified-match",
            ),
        )
        code = cli._provenance([])
        assert code == 0
        assert capsys.readouterr().out == "provenance OK — running containers match abc12345\n"

    def test_mismatch_refuses_with_exit_2_and_names_containers(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "abc12345", "clean",
                [deploy.ContainerProvenance("svc", "img:deadbeef", "deadbeef", "mismatch")],
                "mismatch",
            ),
        )
        code = cli._provenance([])
        assert code == 2
        err = capsys.readouterr().err
        assert "[S17]" in err and "deadbeef" in err and "svc" in err

    def test_mismatch_ignore_mismatch_downgrades_to_warning_exit_0(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "abc12345", "clean",
                [deploy.ContainerProvenance("svc", "img:deadbeef", "deadbeef", "mismatch")],
                "mismatch",
            ),
        )
        code = cli._provenance(["--ignore-mismatch"])
        assert code == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out and "S17" in out and "deadbeef" in out

    def test_mismatch_json_exit_2_unless_ignore_mismatch(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "abc12345", "clean",
                [deploy.ContainerProvenance("svc", "img:deadbeef", "deadbeef", "mismatch")],
                "mismatch",
            ),
        )
        assert cli._provenance(["--json"]) == 2
        capsys.readouterr()
        assert cli._provenance(["--json", "--ignore-mismatch"]) == 0

    def test_dirty_warns_exit_0(self, monkeypatch, tmp_path, capsys):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "abc12345-dirty", "dirty", None, "not-verified-dirty",
            ),
        )
        code = cli._provenance([])
        assert code == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out and "dirty" in out

    def test_not_a_checkout_is_silent_exit_0(self, monkeypatch, tmp_path, capsys):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "dev", "not-a-checkout", None, "not-verified-unknown",
            ),
        )
        code = cli._provenance([])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""

    def test_no_evidence_warns_exit_0_not_ok(self, monkeypatch, tmp_path, capsys):
        """The false-green fix: this must NEVER print 'provenance OK'."""
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix: deploy.ProvenanceResult(
                1, prefix, "abc12345", "clean", None, "not-verified-no-evidence",
            ),
        )
        code = cli._provenance([])
        assert code == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out
        assert "provenance OK" not in out
