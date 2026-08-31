"""S16.9 (CIU-25 substrate) — the worktree-instance LEASE: record schema v2,
the `[ciu.worktree].lease_ttl_hours` policy key, and the explicit
`ciu worktree lease` operator verb.

This package ships the SUBSTRATE a future `ciu worktree reap` (ciu-P27) will
read, never a destroyer: nothing here removes a container, a volume, a network
or a checkout. The properties that matter are therefore all about what the
record says and, just as importantly, what it does NOT silently say —

- a schema-v1 record (no lease concept at all) reads fine, in memory, as
  `lease=None`, and is byte-for-byte unchanged by any read;
- an expiry is REQUIRED for a bounded (`held`) claim and FORBIDDEN for an
  explicitly unbounded (`perpetual`) one;
- a naive, offset-less timestamp is refused rather than guessed at;
- `lease_ttl_hours` absent means NO lease behavior at all — the additive
  default that keeps every existing consumer at exactly today's behavior.

No Docker, no wall clock (every lifecycle assertion injects `now`), no
network. `tmp_repo`/`fake_generate_env` mirror `test_ciu_worktree.py`'s own
fixtures — real Git worktrees, faked CIU env generation.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy, worktree  # noqa: E402


NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 26, 9, 30, 0, tzinfo=timezone.utc)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ciu.env\n", encoding="utf-8")
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


@pytest.fixture
def fake_generate_env(monkeypatch):
    def fake(path: Path, *, identity_only: bool = False) -> int:
        instance_id = hashlib.sha256(
            str(path.resolve()).encode("utf-8")
        ).hexdigest()[:6]
        (path / "ciu.env").write_text(
            f'export INSTANCE_ID="{instance_id}"\n'
            f'export DOCKER_NETWORK_INTERNAL="repo-{instance_id}-network"\n',
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(worktree, "_generate_env_in", fake)
    monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
    return fake


def _raw_v1(root: Path, *, state: str = "ready") -> dict:
    """One VALID schema-v1 record body, as it exists on disk today."""
    return {
        "schema_version": 1,
        "logical_name": "logical",
        "display_name": "display",
        "branch": "branch",
        "git_worktree_path": str(root.resolve()),
        "ciu_root_offset": ".",
        "created_at_utc": "2026-08-17T12:34:56Z",
        "base_ref": "main",
        "state": state,
        "runtime": {"instance_id": "abc123", "network": "repo-network"},
        "recovery_status": None,
    }


def _held(**overrides) -> dict:
    lease = {
        "holder": "ciu@box:abc123",
        "acquired_at_utc": "2026-08-25T12:00:00Z",
        "renewed_at_utc": "2026-08-25T12:00:00Z",
        "expires_at_utc": "2026-08-26T12:00:00Z",
        "mode": "held",
    }
    lease.update(overrides)
    return lease


# ===========================================================================
# O1 — schema v2, the lease field, and its validation
# ===========================================================================


class TestSchemaVersionConstants:
    def test_current_version_is_two_and_both_are_supported(self):
        assert worktree.WORKTREE_INSTANCE_SCHEMA_VERSION == 2
        assert worktree.WORKTREE_INSTANCE_BASE_SCHEMA_VERSION == 1
        assert worktree.WORKTREE_INSTANCE_SCHEMA_VERSIONS == {1, 2}

    def test_lease_vocabularies_are_closed(self):
        assert worktree.WORKTREE_LEASE_MODES == {"held", "perpetual"}
        assert worktree.WORKTREE_LEASE_KEYS == {
            "holder", "acquired_at_utc", "renewed_at_utc",
            "expires_at_utc", "mode",
        }


class TestReadingV1AndV2Records:
    def test_v1_record_reads_as_lease_none_at_version_one(self, tmp_path):
        record = worktree._record_from_dict(_raw_v1(tmp_path), tmp_path / "r.json")
        assert record.lease is None
        assert record.schema_version == 1

    def test_v1_record_round_trips_with_no_lease_key_at_all(self, tmp_path):
        """The structural guarantee behind 'a read never rewrites a v1
        record': there is no v2 shape to emit until a lease sets the version."""
        raw = _raw_v1(tmp_path)
        record = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert record.to_dict() == raw
        assert "lease" not in record.to_dict()

    def test_v2_record_with_null_lease_reads_and_round_trips(self, tmp_path):
        raw = {**_raw_v1(tmp_path), "schema_version": 2, "lease": None}
        record = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert record.lease is None
        assert record.schema_version == 2
        assert record.to_dict() == raw

    def test_v2_record_with_held_lease_reads_and_round_trips(self, tmp_path):
        raw = {**_raw_v1(tmp_path), "schema_version": 2, "lease": _held()}
        record = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert record.lease == worktree.WorktreeLease(
            holder="ciu@box:abc123",
            acquired_at_utc="2026-08-25T12:00:00Z",
            renewed_at_utc="2026-08-25T12:00:00Z",
            expires_at_utc="2026-08-26T12:00:00Z",
            mode="held",
        )
        assert record.to_dict() == raw

    def test_v2_record_with_perpetual_lease_reads(self, tmp_path):
        raw = {
            **_raw_v1(tmp_path), "schema_version": 2,
            "lease": _held(mode="perpetual", expires_at_utc=None),
        }
        record = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert record.lease is not None
        assert record.lease.mode == "perpetual"
        assert record.lease.expires_at_utc is None

    def test_v2_record_missing_the_lease_key_is_refused(self, tmp_path):
        """O1's key-set rule: v2 REQUIRES `lease`. The refusal names the
        schema_version, so the reason is legible without diffing key sets."""
        raw = {**_raw_v1(tmp_path), "schema_version": 2}
        with pytest.raises(worktree.WorktreeError, match="schema_version 2"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_v1_record_carrying_a_lease_key_is_refused(self, tmp_path):
        raw = {**_raw_v1(tmp_path), "lease": _held()}
        with pytest.raises(worktree.WorktreeError, match="unknown="):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_unsupported_future_version_is_refused(self, tmp_path):
        raw = {**_raw_v1(tmp_path), "schema_version": 3}
        with pytest.raises(worktree.WorktreeError, match="unsupported"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_a_plain_read_never_rewrites_a_v1_record_on_disk(self, tmp_path):
        """review_focus: `inspect`/`list` must not upgrade a v1 record."""
        path = tmp_path / worktree.WORKTREE_INSTANCE_RECORD
        body = json.dumps(_raw_v1(tmp_path), indent=2, sort_keys=True) + "\n"
        path.write_text(body, encoding="utf-8")
        record = worktree.read_instance_record(path)
        assert record.schema_version == 1
        assert path.read_text(encoding="utf-8") == body


class TestLeaseValidation:
    @pytest.mark.parametrize(
        ("lease", "message"),
        [
            ([], "malformed lease in"),
            ({"holder": "h"}, "malformed lease in"),
            (_held(holder=""), "malformed lease holder"),
            (_held(holder=3), "malformed lease holder"),
            (_held(mode="borrowed"), "unknown lease mode"),
        ],
    )
    def test_shape_and_vocabulary_refusals(self, tmp_path, lease, message):
        raw = {**_raw_v1(tmp_path), "schema_version": 2, "lease": lease}
        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_held_without_expiry_is_refused(self, tmp_path):
        raw = {
            **_raw_v1(tmp_path), "schema_version": 2,
            "lease": _held(expires_at_utc=None),
        }
        with pytest.raises(worktree.WorktreeError, match="requires expires_at_utc"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_perpetual_with_expiry_is_refused(self, tmp_path):
        raw = {
            **_raw_v1(tmp_path), "schema_version": 2,
            "lease": _held(mode="perpetual"),
        }
        with pytest.raises(worktree.WorktreeError, match="forbids expires_at_utc"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    @pytest.mark.parametrize(
        "field", ["acquired_at_utc", "renewed_at_utc", "expires_at_utc"]
    )
    def test_naive_timestamp_anywhere_in_a_lease_is_refused(self, tmp_path, field):
        raw = {
            **_raw_v1(tmp_path), "schema_version": 2,
            "lease": _held(**{field: "2026-08-25T12:00:00"}),
        }
        with pytest.raises(worktree.WorktreeError, match="has no UTC offset"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_explicit_non_zulu_offset_is_accepted(self, tmp_path):
        """'explicit UTC offset', not 'literally spelled Z' — a record written
        by a tool that emits +00:00 (or +02:00) is unambiguous and readable."""
        raw = {
            **_raw_v1(tmp_path), "schema_version": 2,
            "lease": _held(acquired_at_utc="2026-08-25T14:00:00+02:00"),
        }
        record = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert record.lease.acquired_at_utc == "2026-08-25T14:00:00+02:00"

    @pytest.mark.parametrize("value", ["", None, 17, "not-a-date"])
    def test_unparseable_timestamps_are_refused(self, tmp_path, value):
        raw = {
            **_raw_v1(tmp_path), "schema_version": 2,
            "lease": _held(renewed_at_utc=value),
        }
        with pytest.raises(worktree.WorktreeError, match="lease renewed_at_utc"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_utc_stamp_matches_the_records_existing_timestamp_spelling(self):
        assert worktree._utc_stamp(NOW) == "2026-08-25T12:00:00Z"


# ===========================================================================
# O1/O3 — pure lease transitions (no disk, no clock)
# ===========================================================================


class TestLeaseTransitions:
    def _record(self, tmp_path: Path) -> worktree.WorktreeInstanceRecord:
        return worktree._record_from_dict(_raw_v1(tmp_path), tmp_path / "r.json")

    def test_host_identity_prefers_the_devcontainer_name(self, monkeypatch):
        monkeypatch.setenv("DEVCONTAINER_NAME", "dev-box")
        monkeypatch.setenv("HOSTNAME", "ignored")
        assert worktree._host_identity() == "dev-box"

    def test_host_identity_falls_back_to_hostname(self, monkeypatch):
        monkeypatch.delenv("DEVCONTAINER_NAME", raising=False)
        monkeypatch.setenv("HOSTNAME", "runner-7")
        assert worktree._host_identity() == "runner-7"

    def test_host_identity_never_produces_an_empty_holder(self, monkeypatch):
        monkeypatch.delenv("DEVCONTAINER_NAME", raising=False)
        monkeypatch.delenv("HOSTNAME", raising=False)
        assert worktree._host_identity() == "unknown-host"

    def test_holder_names_the_host_and_the_instance(self, monkeypatch):
        monkeypatch.setenv("DEVCONTAINER_NAME", "dev-box")
        assert worktree.lease_holder("abc123") == "ciu@dev-box:abc123"

    def test_acquire_sets_a_held_lease_at_now_plus_ttl(self, tmp_path):
        updated = worktree.acquire_lease(
            self._record(tmp_path), ttl_hours=24, holder="ciu@box:abc123", now=NOW
        )
        assert updated.schema_version == 2
        assert updated.lease == worktree.WorktreeLease(
            holder="ciu@box:abc123",
            acquired_at_utc="2026-08-25T12:00:00Z",
            renewed_at_utc="2026-08-25T12:00:00Z",
            expires_at_utc="2026-08-26T12:00:00Z",
            mode="held",
        )

    def test_fractional_ttl_is_honoured(self, tmp_path):
        updated = worktree.acquire_lease(
            self._record(tmp_path), ttl_hours=0.5, holder="h", now=NOW
        )
        assert updated.lease.expires_at_utc == "2026-08-25T12:30:00Z"

    def test_renewal_preserves_the_original_acquisition_instant(self, tmp_path):
        first = worktree.acquire_lease(
            self._record(tmp_path), ttl_hours=24, holder="h", now=NOW
        )
        second = worktree.acquire_lease(first, ttl_hours=24, holder="h", now=LATER)
        assert second.lease.acquired_at_utc == "2026-08-25T12:00:00Z"
        assert second.lease.renewed_at_utc == "2026-08-26T09:30:00Z"
        assert second.lease.expires_at_utc == "2026-08-27T09:30:00Z"

    @pytest.mark.parametrize("ttl", [0, -1, -0.5])
    def test_non_positive_ttl_is_refused(self, tmp_path, ttl):
        with pytest.raises(worktree.WorktreeError, match="positive number of hours"):
            worktree.acquire_lease(
                self._record(tmp_path), ttl_hours=ttl, holder="h", now=NOW
            )

    def test_perpetual_has_no_expiry(self, tmp_path):
        updated = worktree.make_lease_perpetual(
            self._record(tmp_path), holder="h", now=NOW
        )
        assert updated.lease.mode == "perpetual"
        assert updated.lease.expires_at_utc is None
        assert updated.schema_version == 2

    def test_perpetual_from_an_existing_lease_keeps_acquisition(self, tmp_path):
        held = worktree.acquire_lease(
            self._record(tmp_path), ttl_hours=1, holder="h", now=NOW
        )
        perpetual = worktree.make_lease_perpetual(held, holder="h", now=LATER)
        assert perpetual.lease.acquired_at_utc == "2026-08-25T12:00:00Z"
        assert perpetual.lease.renewed_at_utc == "2026-08-26T09:30:00Z"

    def test_release_keeps_the_record_at_v2_with_a_null_lease(self, tmp_path):
        """'participates in leasing, claims nothing' is a different fact from
        'predates leasing entirely' — a reader must be able to tell them apart."""
        held = worktree.acquire_lease(
            self._record(tmp_path), ttl_hours=1, holder="h", now=NOW
        )
        released = worktree.release_lease(held)
        assert released.lease is None
        assert released.schema_version == 2
        assert released.to_dict()["lease"] is None

    def test_every_transition_round_trips_through_the_reader(self, tmp_path):
        record = self._record(tmp_path)
        for candidate in (
            worktree.acquire_lease(record, ttl_hours=24, holder="h", now=NOW),
            worktree.make_lease_perpetual(record, holder="h", now=NOW),
            worktree.release_lease(record),
        ):
            reread = worktree._record_from_dict(
                candidate.to_dict(), tmp_path / "r.json"
            )
            assert reread == candidate


# ===========================================================================
# O1/O3 — record-on-disk operations, gated on "is this a managed instance?"
# ===========================================================================


class TestOwnRecordOperations:
    def _write(self, root: Path, raw: dict) -> Path:
        path = worktree.instance_record_path(root)
        path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", "utf-8")
        return path

    def test_unmanaged_checkout_has_no_record(self, tmp_path):
        assert worktree.read_own_instance_record(tmp_path) is None

    def test_acquire_on_an_unmanaged_checkout_writes_nothing(self, tmp_path):
        """review_focus: a PRIMARY/unmanaged checkout is provably untouched."""
        before = sorted(p.name for p in tmp_path.iterdir())
        assert worktree.acquire_own_lease(tmp_path, ttl_hours=24) is None
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_release_on_an_unmanaged_checkout_writes_nothing(self, tmp_path):
        before = sorted(p.name for p in tmp_path.iterdir())
        assert worktree.release_own_lease(tmp_path) is None
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_acquire_writes_a_held_lease_naming_this_host_and_instance(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("DEVCONTAINER_NAME", "dev-box")
        path = self._write(tmp_path, _raw_v1(tmp_path))
        updated = worktree.acquire_own_lease(tmp_path, ttl_hours=24, now=NOW)
        assert updated.lease.holder == "ciu@dev-box:abc123"
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["schema_version"] == 2
        assert stored["lease"]["expires_at_utc"] == "2026-08-26T12:00:00Z"

    def test_acquire_falls_back_to_the_workspaces_own_ciu_env_identity(
        self, tmp_path, monkeypatch
    ):
        """An `allocating` record has no runtime identity yet; the INSTANCE_ID
        then comes from THIS workspace's own ciu.env by exact path."""
        monkeypatch.setenv("DEVCONTAINER_NAME", "dev-box")
        raw = _raw_v1(tmp_path, state="allocating")
        raw["runtime"] = {"instance_id": None, "network": None}
        self._write(tmp_path, raw)
        (tmp_path / "ciu.env").write_text(
            'export INSTANCE_ID="fromenv"\n'
            'export DOCKER_NETWORK_INTERNAL="n"\n',
            encoding="utf-8",
        )
        updated = worktree.acquire_own_lease(tmp_path, ttl_hours=1, now=NOW)
        assert updated.lease.holder == "ciu@dev-box:fromenv"

    def test_release_clears_a_held_lease(self, tmp_path):
        path = self._write(tmp_path, _raw_v1(tmp_path))
        worktree.acquire_own_lease(tmp_path, ttl_hours=24, now=NOW)
        released = worktree.release_own_lease(tmp_path)
        assert released.lease is None
        assert json.loads(path.read_text(encoding="utf-8"))["lease"] is None

    def test_release_never_drags_a_v1_record_up_to_v2(self, tmp_path):
        """Nothing was claimed, so nothing is written — a teardown of an
        instance that never leased leaves its record byte-identical."""
        path = self._write(tmp_path, _raw_v1(tmp_path))
        before = path.read_text(encoding="utf-8")
        result = worktree.release_own_lease(tmp_path)
        assert result.schema_version == 1
        assert path.read_text(encoding="utf-8") == before


# ===========================================================================
# O2 — [ciu.worktree].lease_ttl_hours
# ===========================================================================


class TestLeaseTtlConfig:
    def test_the_table_key_set_is_closed_and_now_holds_three_keys(self):
        assert worktree.WORKTREE_TABLE_KEYS == {
            "max_concurrent_instances", "lease_ttl_hours", "exec_targets",
        }

    def test_absent_table_means_no_lease_behavior_at_all(self):
        assert worktree.resolve_lease_ttl_hours(None) is None

    def test_absent_key_means_no_lease_behavior_at_all(self):
        """O2's negative: there is no non-zero default. A consumer who
        configured only a capacity cap must not start expiring leases."""
        assert worktree.resolve_lease_ttl_hours({"max_concurrent_instances": 3}) is None

    def test_declared_integer_hours(self):
        assert worktree.resolve_lease_ttl_hours({"lease_ttl_hours": 24}) == 24.0

    def test_declared_fractional_hours(self):
        assert worktree.resolve_lease_ttl_hours({"lease_ttl_hours": 1.5}) == 1.5

    @pytest.mark.parametrize("value", [0, -1, "24h", True, None, [24]])
    def test_non_positive_or_non_numeric_is_refused(self, value):
        with pytest.raises(worktree.WorktreeError, match="positive"):
            worktree.resolve_lease_ttl_hours({"lease_ttl_hours": value})

    def test_unknown_key_still_refuses_through_the_ttl_reader(self):
        with pytest.raises(worktree.WorktreeError, match="unknown key"):
            worktree.resolve_lease_ttl_hours({"lease_ttl_hourz": 24})

    def test_non_table_still_refuses_through_the_ttl_reader(self):
        with pytest.raises(worktree.WorktreeError, match="must be a table"):
            worktree.resolve_lease_ttl_hours([])

    def test_the_capacity_reader_accepts_the_new_sibling_key(self):
        """Both keys share ONE closed table: declaring a TTL must not make
        the capacity reader reject the file."""
        assert worktree.resolve_max_concurrent_instances(
            {"max_concurrent_instances": 2, "lease_ttl_hours": 24}
        ) == 2

    def test_all_three_families_coexist_in_one_table(self):
        """CIU-69: `[ciu.worktree]` is documented as carrying THREE key
        families (S16.3 `max_concurrent_instances`, S16.9 `lease_ttl_hours`,
        S16.7 `exec_targets.<alias>`), but `WORKTREE_TABLE_KEYS` used to omit
        `exec_targets` — so a consumer who declared a budget or a lease TTL
        in the SAME table as an exec target got a spurious
        `[S16.3] unknown key(s)` refusal, even though `exec_targets`'s own
        per-alias grammar is validated separately by
        `resolve_exec_targets_config`. Declare all three together and prove
        budget resolution, lease resolution, AND exec-target resolution all
        accept the table without refusing."""
        worktree_table = {
            "max_concurrent_instances": 2,
            "lease_ttl_hours": 24,
            "exec_targets": {
                "tester": {"stack": "test", "service": "tester", "workdir": "/workspace"},
            },
        }
        global_config = {"ciu": {"worktree": worktree_table}}

        assert worktree.resolve_max_concurrent_instances(worktree_table) == 2
        assert worktree.resolve_lease_ttl_hours(worktree_table) == 24.0
        targets = worktree.resolve_exec_targets_config(global_config)
        assert set(targets) == {"tester"}
        assert (targets["tester"].stack, targets["tester"].service, targets["tester"].workdir) == (
            "test", "tester", "/workspace",
        )

    def test_resolve_from_a_repo_reads_the_primary_ciu_roots_table(
        self, tmp_repo, monkeypatch
    ):
        monkeypatch.setattr(
            worktree.config_model, "render_global_chain",
            lambda *_a, **_kw: {"ciu": {"worktree": {"lease_ttl_hours": 6}}},
        )
        assert worktree.resolve_worktree_lease_ttl(tmp_repo) == 6.0

    def test_outside_a_git_worktree_there_is_no_file_policy(self, tmp_path):
        assert worktree.resolve_worktree_lease_ttl(tmp_path) is None


# ===========================================================================
# O3 — the `--extend` duration grammar (ONE grammar, strict form)
# ===========================================================================


class TestDurationGrammar:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [("24h", 86400.0), ("90m", 5400.0), ("30s", 30.0), ("45", 45.0)],
    )
    def test_strict_parser_accepts_the_existing_grammar(self, text, seconds):
        assert deploy.parse_duration_seconds(text) == seconds

    def test_strict_parser_refuses_instead_of_substituting_a_default(self):
        with pytest.raises(ValueError, match="not a duration"):
            deploy.parse_duration_seconds("forever")

    def test_the_lenient_config_wrapper_still_falls_back(self, capsys):
        """`_seconds` keeps its documented lenient behavior — the strict form
        is an ADDITION for command-line input, not a change to config parsing."""
        assert deploy._seconds("forever", 7.0) == 7.0
        assert "could not parse duration" in capsys.readouterr().out

    def test_hours_conversion(self):
        assert worktree._lease_duration_hours("24h") == 24.0
        assert worktree._lease_duration_hours("90m") == 1.5

    def test_unparseable_duration_is_a_tagged_refusal(self):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16.9\] invalid lease"):
            worktree._lease_duration_hours("forever")

    def test_zero_duration_is_refused(self):
        with pytest.raises(worktree.WorktreeError, match="must be positive"):
            worktree._lease_duration_hours("0")


# ===========================================================================
# O3 — `ciu worktree lease LOGICAL ...`
# ===========================================================================


class TestApplyLease:
    def test_extend_sets_a_held_lease(self, tmp_repo, fake_generate_env, monkeypatch):
        monkeypatch.setenv("DEVCONTAINER_NAME", "dev-box")
        worktree.create(tmp_repo, "one", base="main")
        record = worktree.apply_lease(tmp_repo, "one", extend="24h")
        assert record.lease.mode == "held"
        assert record.lease.expires_at_utc is not None
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert stored["schema_version"] == 2
        assert stored["lease"]["holder"].startswith("ciu@dev-box:")

    def test_perpetual_sets_an_unbounded_lease(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "one", base="main")
        record = worktree.apply_lease(tmp_repo, "one", perpetual=True)
        assert record.lease.mode == "perpetual"
        assert record.lease.expires_at_utc is None

    def test_release_drops_the_claim(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "one", base="main")
        worktree.apply_lease(tmp_repo, "one", extend="24h")
        record = worktree.apply_lease(tmp_repo, "one", release=True)
        assert record.lease is None
        assert json.loads(record.record_path.read_text("utf-8"))["lease"] is None

    def test_release_is_unconditional_and_normalizes_a_v1_record(
        self, tmp_repo, fake_generate_env
    ):
        worktree.create(tmp_repo, "one", base="main")
        record = worktree.apply_lease(tmp_repo, "one", release=True)
        assert record.schema_version == 2
        assert record.lease is None

    def test_extend_renews_rather_than_restarting_the_claim(
        self, tmp_repo, fake_generate_env
    ):
        worktree.create(tmp_repo, "one", base="main")
        first = worktree.apply_lease(tmp_repo, "one", extend="1h")
        second = worktree.apply_lease(tmp_repo, "one", extend="24h")
        assert second.lease.acquired_at_utc == first.lease.acquired_at_utc

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"extend": "24h", "perpetual": True},
            {"perpetual": True, "release": True},
            {"extend": "24h", "release": True},
        ],
    )
    def test_exactly_one_mode_is_required(self, tmp_repo, kwargs):
        with pytest.raises(worktree.WorktreeError, match="exactly one of"):
            worktree.apply_lease(tmp_repo, "one", **kwargs)

    def test_unknown_logical_identity_is_a_refusal(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="no managed worktree"):
            worktree.apply_lease(tmp_repo, "nope", release=True)

    def test_works_on_a_stopped_instance_without_touching_docker(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """O3's negative: leasing must never require the instance to be up.
        Any Docker call at all here is a failure, so make one impossible."""
        worktree.create(tmp_repo, "one", base="main")

        def explode(*_a, **_kw):  # pragma: no cover - must never run
            raise AssertionError("lease must not query Docker")

        monkeypatch.setattr(worktree.procutil, "docker", explode)
        assert worktree.apply_lease(tmp_repo, "one", extend="24h").lease is not None

    def test_lease_is_a_recognized_document_operation(
        self, tmp_repo, fake_generate_env
    ):
        assert "lease" in worktree.WORKTREE_JSON_OPERATIONS
        worktree.create(tmp_repo, "one", base="main")
        record = worktree.apply_lease(tmp_repo, "one", perpetual=True)
        doc = worktree.build_instance_document("lease", record)
        assert doc["operation"] == "lease"
        assert doc["instance"]["lease"]["mode"] == "perpetual"


class TestLeaseCli:
    def _run(self, capsys, argv: list[str]) -> tuple[int, str]:
        code = cli._worktree(argv)
        return code, capsys.readouterr().out

    def test_extend_human_output(self, tmp_repo, fake_generate_env, capsys):
        worktree.create(tmp_repo, "one", base="main")
        code, out = self._run(
            capsys,
            ["lease", "one", "--extend", "24h", "--define-root", str(tmp_repo)],
        )
        assert code == 0
        assert "mode: held" in out
        assert "expires: 2026-" in out or "expires: 20" in out

    def test_perpetual_human_output_names_the_unbounded_expiry(
        self, tmp_repo, fake_generate_env, capsys
    ):
        worktree.create(tmp_repo, "one", base="main")
        code, out = self._run(
            capsys, ["lease", "one", "--perpetual", "--define-root", str(tmp_repo)]
        )
        assert code == 0
        assert "never (perpetual)" in out

    def test_release_human_output(self, tmp_repo, fake_generate_env, capsys):
        worktree.create(tmp_repo, "one", base="main")
        code, out = self._run(
            capsys, ["lease", "one", "--release", "--define-root", str(tmp_repo)]
        )
        assert code == 0
        assert "lease: none (released)" in out

    def test_json_emits_one_versioned_document(
        self, tmp_repo, fake_generate_env, capsys
    ):
        worktree.create(tmp_repo, "one", base="main")
        code, out = self._run(
            capsys,
            ["lease", "one", "--extend", "2h", "--json",
             "--define-root", str(tmp_repo)],
        )
        assert code == 0
        doc = json.loads(out)
        assert doc["operation"] == "lease"
        assert doc["instance"]["schema_version"] == 2
        assert doc["instance"]["lease"]["mode"] == "held"

    def test_unknown_instance_exits_two(self, tmp_repo, capsys):
        code, _ = self._run(
            capsys, ["lease", "nope", "--release", "--define-root", str(tmp_repo)]
        )
        assert code == 2

    def test_no_mode_is_an_argparse_refusal(self, tmp_repo):
        with pytest.raises(SystemExit):
            cli._worktree(["lease", "one", "--define-root", str(tmp_repo)])

    def test_the_verb_is_documented_in_usage_and_verb_help(self):
        assert "worktree lease" in cli._USAGE
        assert "--perpetual" in cli._VERB_HELP["worktree"]


# ===========================================================================
# O3 — teardown clears the lease ON SUCCESS ONLY
# ===========================================================================


class TestTeardownClearsTheLease:
    def test_worktree_rm_clears_the_lease_after_a_successful_clean(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "one", base="main")
        worktree.apply_lease(tmp_repo, "one", extend="24h")
        seen: list[dict] = []

        def clean_ok(wt, *, yes):
            return 0

        real_git = worktree._git

        def fail_remove(args, cwd):
            if args[:2] == ["worktree", "remove"]:
                # Snapshot the record at the exact moment the checkout would
                # be destroyed, then refuse — the record is otherwise gone.
                seen.append(json.loads(record.record_path.read_text("utf-8")))
                return subprocess.CompletedProcess(["git"], 1, "", "locked")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_clean_in", clean_ok)
        monkeypatch.setattr(worktree, "_git", fail_remove)
        with pytest.raises(worktree.WorktreeError, match="locked"):
            worktree.remove(tmp_repo, "one")
        assert seen[0]["lease"] is None
        assert seen[0]["schema_version"] == 2

    def test_a_failed_clean_leaves_the_lease_exactly_as_it_was(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """review_focus: the lease is the evidence that something still owns
        these resources — a failed teardown must never erase it."""
        record = worktree.create(tmp_repo, "one", base="main")
        worktree.apply_lease(tmp_repo, "one", extend="24h")
        before = record.record_path.read_text(encoding="utf-8")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu clean"):
            worktree.remove(tmp_repo, "one", force=False)
        assert record.record_path.read_text(encoding="utf-8") == before

    def test_force_over_a_failed_clean_still_does_not_clear_the_lease(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "one", base="main")
        worktree.apply_lease(tmp_repo, "one", extend="24h")
        before = record.record_path.read_text(encoding="utf-8")
        real_git = worktree._git
        seen: list[str] = []

        def fail_remove(args, cwd):
            if args[:2] == ["worktree", "remove"]:
                seen.append(record.record_path.read_text(encoding="utf-8"))
                return subprocess.CompletedProcess(["git"], 1, "", "locked")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        monkeypatch.setattr(worktree, "_git", fail_remove)
        with pytest.raises(worktree.WorktreeError, match="locked"):
            worktree.remove(tmp_repo, "one", force=True)
        assert seen == [before]
