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
    """Load a FROZEN grammar fixture. CIU-39 widened the closed vocabularies
    (vendor-pinned, vendor drift) and bumped schema_version to 2; the seven
    P01 fixtures stay untouched as the historical v1 record, and every
    assertion now reads the v2 set (same seven shapes, new vocabulary)."""
    v2_name = f"provenance-v2-{name.split('provenance-', 1)[1]}"
    with (FIXTURE_DIR / v2_name).open(encoding="utf-8") as fh:
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
            schema_version=2, instance=None, commit_under_test=None,
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
# CIU-39 — declared vendor baseline: vendor-pinned, drift, verified-match reachability
# ---------------------------------------------------------------------------


class TestVendorPinnedVerdicts:
    VENDOR = ["hashicorp/vault:1.15", "ghcr.io/goauthentik/server:2024.2.2", "hashicorp/consul:1.18"]

    def test_all_vendor_deployment_reaches_verified_match(self, monkeypatch):
        """The CIU-39 headline oracle: a deployment whose containers are ALL
        pinned vendor artifacts leaves not-verified-no-evidence forever no
        more — the verdict is green, and the document says which containers
        were vendor-pinned."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [
                ("dstdns-dev-vault-1", "hashicorp/vault:1.15", "dstdns-dev"),
                ("dstdns-dev-authentik-1", "ghcr.io/goauthentik/server:2024.2.2", "dstdns-dev"),
                ("dstdns-dev-consul-1", "hashicorp/consul:1.18", "dstdns-dev"),
            ],
            {},  # vendor images carry no revision label
        )))
        result = deploy.verify_running_provenance(INSTANCE, vendor_images=self.VENDOR)
        assert result.to_dict() == _fixture("provenance-verified-match-all-vendor.json")
        assert result.overall == "verified-match"

    def test_vendor_drift_is_mismatch(self, monkeypatch):
        """The declaration vouches for ONE artifact; same image name at any
        other reference is drift — a mismatch, not a silent unlabelled."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("dstdns-dev-vault-1", "hashicorp/vault:1.16", "dstdns-dev")],
            {},
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["hashicorp/vault:1.15"]
        )
        assert result.to_dict() == _fixture("provenance-mismatch-vendor-drift.json")

    def test_declared_image_with_label_is_never_commit_compared(self, monkeypatch):
        """A declared image carrying a revision label (upstream-stamped) must
        NOT be judged against OUR commit — reference equality wins, and the
        label is still reported verbatim in the document."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("dstdns-dev-vault-1", "hashicorp/vault:1.15", "dstdns-dev")],
            {"hashicorp/vault:1.15": "upstream-build-999"},
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["hashicorp/vault:1.15"]
        )
        assert result.overall == "verified-match"
        assert result.containers[0].status == "vendor-pinned"
        assert result.containers[0].labelled_revision == "upstream-build-999"

    def test_mixed_match_and_vendor_pinned_is_verified_match(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [
                ("dstdns-dev-controller-1", "dstdns/controller:1b369e23", "dstdns-dev"),
                ("dstdns-dev-vault-1", "hashicorp/vault:1.15", "dstdns-dev"),
            ],
            {"dstdns/controller:1b369e23": "1b369e23"},
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["hashicorp/vault:1.15"]
        )
        assert result.overall == "verified-match"
        assert {c.status for c in result.containers} == {"match", "vendor-pinned"}

    def test_undeclared_unlabelled_stays_unlabelled_even_with_declarations(
        self, monkeypatch
    ):
        """The escape hatch cannot fake a verdict: an unlabelled image nobody
        declared is still exactly 'unlabelled' — never silently vendor-pinned.
        (Overall stays green here because S17.2's shipped semantics are 'only
        labelled/expected images are checked': an unlabelled container never
        BLOCKS green, it just contributes nothing — same as postgres:16 next
        to a matching own image pre-CIU-39.)"""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [
                ("dstdns-dev-vault-1", "hashicorp/vault:1.15", "dstdns-dev"),
                ("dstdns-dev-controller-1", "dstdns/controller:forgotten", "dstdns-dev"),
            ],
            {},  # controller image unbaked: no label
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["hashicorp/vault:1.15"]
        )
        statuses = {c.name: c.status for c in result.containers}
        assert statuses == {
            "dstdns-dev-vault-1": "vendor-pinned",
            "dstdns-dev-controller-1": "unlabelled",
        }
        assert result.overall == "verified-match"

    def test_forgotten_bake_with_no_declarations_stays_no_evidence(self, monkeypatch):
        """The mask-check: an unbaked OWN image with an EMPTY declaration list
        can never go green — restoring the bare unlabelled-skip without the
        declaration requirement keeps today's fail-closed posture."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("dstdns-dev-controller-1", "dstdns/controller:forgotten", "dstdns-dev")],
            {},
        )))
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.overall == "not-verified-no-evidence"

    def test_no_declarations_reproduces_pre_ciu39_verdict_exactly(self, monkeypatch):
        """Nothing declared → byte-identical semantics to the pre-CIU-39
        verdict (the v2 all-unlabelled fixture)."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "1b369e23")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("dstdns-dev-db-1", "postgres:16", "dstdns-dev")],
            {"postgres:16": ""},
        )))
        result = deploy.verify_running_provenance(INSTANCE)
        assert result.to_dict() == _fixture(
            "provenance-not-verified-no-evidence-unlabelled.json"
        )

    def test_image_reference_name_extraction(self):
        f = deploy._image_reference_name  # CANONICAL names (review fix)
        assert f("hashicorp/vault:1.15") == "hashicorp/vault"
        assert f("vault@sha256:abc") == "docker.io/library/vault"
        assert f("nginx:1") == "docker.io/library/nginx"
        assert f("docker.io/nginx:2") == "docker.io/library/nginx"
        assert f("GHCR.io/X/Y:1") == "ghcr.io/X/Y"  # host lowercased, ns kept
        assert f("localhost:5000/img:tag") == "localhost:5000/img"
        assert f("bare-name") == "docker.io/library/bare-name"


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

    def test_no_preflight_skips_before_any_provenance_input(
        self, monkeypatch, capsys
    ):
        """Break-glass must not require config, Git, or Docker to be available."""
        monkeypatch.setattr(
            deploy, "load_global_config",
            lambda *_a, **_kw: pytest.fail("--no-preflight loaded configuration"),
        )
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda *_a, **_kw: pytest.fail("--no-preflight inspected Docker"),
        )
        assert cli._provenance(["--no-preflight"]) == 0
        captured = capsys.readouterr()
        assert captured.out == "[INFO] --no-preflight: skipping provenance check\n"
        assert captured.err == ""

    def test_no_preflight_rejects_json_without_inventing_a_verdict(self, capsys):
        with pytest.raises(SystemExit) as raised:
            cli._provenance(["--no-preflight", "--json"])
        assert raised.value.code == 2
        assert "no provenance verdict is produced" in capsys.readouterr().err

    def test_json_flag_is_store_true_like_diagnose(self, monkeypatch, tmp_path, capsys):
        """Precedent: cli.py:726's `ciu diagnose --json` (store_true) — NOT a
        [PATH|-] positional. `--json` alone must be a complete, valid parse."""
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "rev1", "clean", [], "not-verified-no-evidence",
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
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "rev1", "clean",
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
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "abc12345", "clean",
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
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "abc12345", "clean",
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
        """Byte-identical to the OLD CLI (O1's own promise): the OLD
        verify_running_provenance warned-and-returned-normally on this path,
        so the old cli._provenance always fell through to print "provenance
        OK" afterward. A warning immediately followed by "OK" reads as
        self-contradictory, but that contradiction IS the documented old
        behaviour -- silently dropping the OK line here would be an
        undisclosed deviation from what O1/S17.3 promises."""
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "abc12345", "clean",
                [deploy.ContainerProvenance("svc", "img:deadbeef", "deadbeef", "mismatch")],
                "mismatch",
            ),
        )
        code = cli._provenance(["--ignore-mismatch"])
        assert code == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out and "S17" in out and "deadbeef" in out
        assert "provenance OK — running containers match abc12345" in out

    def test_mismatch_json_exit_2_unless_ignore_mismatch(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
        monkeypatch.setattr(
            deploy, "verify_running_provenance",
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "abc12345", "clean",
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
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "abc12345-dirty", "dirty", None, "not-verified-dirty",
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
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "dev", "not-a-checkout", None, "not-verified-unknown",
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
            lambda prefix, **_kw: deploy.ProvenanceResult(
                2, prefix, "abc12345", "clean", None, "not-verified-no-evidence",
            ),
        )
        code = cli._provenance([])
        assert code == 0
        out = capsys.readouterr().out
        assert "[WARN]" in out
        assert "provenance OK" not in out


# ---------------------------------------------------------------------------
# CIU-39 — CLI surface: declaration plumbing, malformed-config refusal, prose
# ---------------------------------------------------------------------------


def _vendor_cfg(vendor_images):
    cfg = {
        "deploy": {
            "project_name": "dstdns",
            "environment_tag": "dev",
            "provenance": {"vendor_images": vendor_images},
        }
    }
    return cfg


def test_vendor_images_passed_through_to_verdict_builder(monkeypatch, tmp_path):
    """CIU-39: the CLI reads [deploy.provenance] vendor_images and hands the
    list to verify_running_provenance verbatim (whitespace-stripped)."""
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)
    seen = {}

    def fake_verify(prefix, *, vendor_images=None):
        seen["prefix"] = prefix
        seen["vendor_images"] = vendor_images
        return deploy.ProvenanceResult(2, prefix, "rev1", "clean", [], "verified-match")

    monkeypatch.setattr(
        deploy, "load_global_config",
        lambda repo_root: _vendor_cfg(["hashicorp/vault:1.15 ", " ghcr.io/goauthentik/server:2024.2.2"]),
    )
    monkeypatch.setattr(deploy, "verify_running_provenance", fake_verify)

    assert cli._provenance([]) == 0
    assert seen["prefix"] == "dstdns-dev"
    assert seen["vendor_images"] == [
        "hashicorp/vault:1.15",
        "ghcr.io/goauthentik/server:2024.2.2",
    ]


def test_malformed_vendor_images_refuse_exit_2(monkeypatch, tmp_path, capsys):
    """A silently ignored declaration would certify exactly the deployment it
    was written to vouch for — malformed config refuses loudly."""
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)

    def fail_verify(prefix, **_kw):
        raise AssertionError("must not reach the verdict builder")

    monkeypatch.setattr(
        deploy, "load_global_config", lambda repo_root: _vendor_cfg("hashicorp/vault")
    )
    monkeypatch.setattr(deploy, "verify_running_provenance", fail_verify)

    assert cli._provenance(["--json"]) == 2
    err = capsys.readouterr().err
    assert "vendor_images" in err and "list" in err


def test_drift_mismatch_prose_names_the_declaration_not_the_commit(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(
        deploy, "load_global_config", lambda repo_root: _vendor_cfg(["hashicorp/vault:1.15"])
    )
    monkeypatch.setattr(
        deploy, "verify_running_provenance",
        lambda prefix, **_kw: deploy.ProvenanceResult(
            2, prefix, "1b369e23", "clean",
            [deploy.ContainerProvenance("dstdns-dev-vault-1", "hashicorp/vault:1.16", None, "mismatch")],
            "mismatch",
        ),
    )
    assert cli._provenance([]) == 2
    err = capsys.readouterr().err
    assert "not the declared vendor reference" in err
    assert "hashicorp/vault:1.16" in err


def test_ok_line_names_pinned_count_when_vendors_declared(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
    monkeypatch.setattr(
        deploy, "verify_running_provenance",
        lambda prefix, **_kw: deploy.ProvenanceResult(
            2, prefix, "rev1", "clean",
            [
                deploy.ContainerProvenance("svc-a", "img:rev1", "rev1", "match"),
                deploy.ContainerProvenance("svc-b", "hashicorp/vault:1.15", None, "vendor-pinned"),
            ],
            "verified-match",
        ),
    )
    assert cli._provenance([]) == 0
    out = capsys.readouterr().out
    assert out == (
        "provenance OK — running containers match rev1 or their declared "
        "vendor references (1 pinned)\n"
    )


def test_ok_line_without_pins_is_byte_identical_to_pre_ciu39(
    monkeypatch, tmp_path, capsys
):
    """No vendor-pinned containers → the pre-CIU-39 OK line, byte-identical."""
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: _config())
    monkeypatch.setattr(
        deploy, "verify_running_provenance",
        lambda prefix, **_kw: deploy.ProvenanceResult(
            2, prefix, "rev1", "clean",
            [deploy.ContainerProvenance("svc-a", "img:rev1", "rev1", "match")],
            "verified-match",
        ),
    )
    assert cli._provenance([]) == 0
    out = capsys.readouterr().out
    assert out == "provenance OK — running containers match rev1\n"


# ---------------------------------------------------------------------------
# Review fixes — canonical reference comparison; malformed-table refusal
# ---------------------------------------------------------------------------


class TestCanonicalReferenceComparison:
    def test_name_canonicalizes_docker_hub_defaults_and_host_case(self):
        f = deploy._image_reference_name
        assert f("nginx:1") == "docker.io/library/nginx"
        assert f("docker.io/library/nginx:1") == "docker.io/library/nginx"
        assert f("docker.io/nginx:2") == "docker.io/library/nginx"
        assert f("GHCR.io/X/Y:1") == "ghcr.io/X/Y"
        assert f("localhost:5000/img:tag") == "localhost:5000/img"

    def test_full_reference_normalization_keeps_tag_verbatim(self):
        f = deploy._normalized_image_reference
        assert f("NGINX:1.25") == "docker.io/library/NGINX:1.25"  # tag case kept? no—see below
        # tags are case-SENSITIVE and survive verbatim; the NAME canonicalizes
        assert f("nginx:1.25").endswith(":1.25")
        assert f("vault@sha256:abc") == "docker.io/library/vault@sha256:abc"

    def test_registry_prefix_spelling_cannot_hide_drift(self, monkeypatch):
        """Review major (probe A): declared docker.io/library/nginx:1, running
        nginx:2 — Docker treats these as the same name, so this is DRIFT
        (mismatch), never a benign unlabelled."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "rev1")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("web-1", "nginx:2", "dstdns-dev")],
            {},
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["docker.io/library/nginx:1"]
        )
        assert result.overall == "mismatch"
        assert result.containers[0].status == "mismatch"

    def test_registry_prefix_spelling_recognizes_correct_pin(self, monkeypatch):
        """Review major (probe B): the same spelling mismatch must not break
        PIN recognition either."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "rev1")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("web-1", "nginx:1", "dstdns-dev")],
            {},
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["docker.io/library/nginx:1"]
        )
        assert result.containers[0].status == "vendor-pinned"
        assert result.overall == "verified-match"

    def test_uppercase_registry_host_pin_is_recognized(self, monkeypatch):
        """Only the REGISTRY HOST is case-insensitive (Docker rule); the
        namespace stays case-sensitive — so host-case spelling differences
        cannot break a pin, but a genuinely different namespace is drift."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "rev1")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps((
            [("app-1", "ghcr.io/x/app:2.0", "dstdns-dev")],
            {},
        )))
        result = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["GHCR.io/x/app:2.0"]
        )
        assert result.containers[0].status == "vendor-pinned"

        # a different NAMESPACE is a genuinely different repository (Docker
        # namespaces are case-sensitive) — the declaration does not vouch for
        # it, so it is an ordinary undeclared image: unlabelled, not drift.
        other_ns = deploy.verify_running_provenance(
            INSTANCE, vendor_images=["GHCR.io/X/app:2.0"]
        )
        assert other_ns.containers[0].status == "unlabelled"


def test_malformed_provenance_table_refuses_exit_2(monkeypatch, tmp_path, capsys):
    """Review minor: [deploy.provenance] set to a non-table TOML value used to
    crash with a traceback — it refuses with the same loudness as a malformed
    vendor_images list."""
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)

    def fail_verify(prefix, **_kw):
        raise AssertionError("must not reach the verdict builder")

    monkeypatch.setattr(
        deploy,
        "load_global_config",
        lambda repo_root: {"deploy": {"project_name": "d", "environment_tag": "v",
                                      "provenance": "oops"}},
    )
    monkeypatch.setattr(deploy, "verify_running_provenance", fail_verify)
    assert cli._provenance(["--json"]) == 2
    err = capsys.readouterr().err
    assert "[deploy.provenance]" in err and "table" in err
