"""S16.10 / CIU-25 (docker half) — `ciu worktree reap`: survey + destroy.

This is the highest-blast-radius surface in the S16 family: the only verb
that deletes Docker resources CIU did not just create in the same command.
Every test below therefore has the same shape — build a host state, survey
it, and assert BOTH what happened and what provably did NOT.

Oracles:

- **O1, closed partition.** Every resource group lands in exactly ONE of the
  seven categories, never zero and never two. The negatives matter more than
  the positives here: age, directory-basename similarity and "no CIU process
  is running" NEVER move a group out of `owned`, an unlabelled group is
  always `unattributable`, a colliding claim is always `ambiguous`, a
  schema-v1 record surveys cleanly with no lease, and an inconsistent record
  becomes a FINDING rather than an exception that kills the whole survey.
- **O2, destructive safety.** `-y` acts on exactly
  checkout-missing/lease-expired/orphaned/partial-cleanup. `owned`,
  `unattributable` and `ambiguous` are unreachable under EVERY flag
  combination — `--category` refuses their names outright — and a network
  another instance is still joined to is never removed.
- **O3, transactional isolation.** One group's failure lands in `failed` with
  the real error text while every other targeted group is still processed;
  the returned document is a post-pass RE-SURVEY; a partial pass exits 1.
- **O4, envelope + docs.** `schema_version == 1`, `counts` keyed by all seven
  categories including the zero-valued ones, and the two new capability
  identifiers advertised.

No live Docker anywhere: `worktree.procutil.docker` is replaced with
`FakeDocker`, a stateful host model that actually applies removals, so
"the network survived" and "the other instance's volumes are still there"
are assertions about real post-state rather than about which argv was
issued. Git IS real — `git worktree add` against a tmp_path repo — because
the registration facts this verb reasons about are exactly the ones a mocked
git would let us get wrong. The clock is always injected; the real one is
never consulted.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, worktree  # noqa: E402


NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
REPO_NAME = "repo"


# ---------------------------------------------------------------------------
# Fake host
# ---------------------------------------------------------------------------


class _R:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeDocker:
    """A STATEFUL fake host, not a script of canned answers.

    Removals actually mutate it, which is what lets a test assert the thing
    that matters — "instance B's volumes are still present after A was
    reaped" — instead of the much weaker "some argv was or was not issued".
    An unrecognised command raises, so a regression that invents a new
    Docker call fails loudly rather than silently succeeding.
    """

    def __init__(self) -> None:
        self.containers: list[dict[str, str]] = []
        self.volumes: list[dict[str, str]] = []
        self.networks: list[dict[str, str]] = []
        self.members: dict[str, set[str]] = {}
        self.calls: list[list[str]] = []
        self.failures: list[tuple[object, str]] = []
        self.hooks: list[tuple[object, object]] = []
        self.explosions: list[object] = []
        self.absent = False

    # -- host construction --------------------------------------------------

    def container(self, cid, name, project, instance="", repo_root="", network=None):
        self.containers.append({
            "id": cid, "name": name, "project": project,
            "instance": instance, "repo_root": repo_root,
        })
        if network:
            self.members.setdefault(network, set()).add(cid)
        return cid

    def volume(self, name, project, instance="", repo_root=""):
        self.volumes.append({
            "name": name, "project": project,
            "instance": instance, "repo_root": repo_root,
        })

    def network(self, name, project="", instance="", repo_root=""):
        self.networks.append({
            "name": name, "project": project,
            "instance": instance, "repo_root": repo_root,
        })
        self.members.setdefault(name, set())

    def fail(self, predicate, stderr: str) -> None:
        self.failures.append((predicate, stderr))

    def boom(self, predicate) -> None:
        """Make a matching call raise OSError — the daemon socket dying
        mid-pass, which is not the same thing as docker being absent."""
        self.explosions.append(predicate)

    def when(self, predicate, effect) -> None:
        """Mutate the host just BEFORE a matching call — a concurrent
        operator (`docker network prune`, another reap) racing this one."""
        self.hooks.append((predicate, effect))

    def drop_repo_root(self, root: str) -> None:
        """What a successful `ciu clean` in *root* does to this host."""
        self.containers = [c for c in self.containers if c["repo_root"] != root]
        self.volumes = [v for v in self.volumes if v["repo_root"] != root]
        self.networks = [n for n in self.networks if n["repo_root"] != root]
        alive = {c["id"] for c in self.containers}
        for name in list(self.members):
            self.members[name] = self.members[name] & alive

    # -- the seam -----------------------------------------------------------

    def __call__(self, args, **_kw):
        args = list(args)
        self.calls.append(args)
        if self.absent:
            raise FileNotFoundError("docker")
        for predicate in self.explosions:
            if predicate(args):
                raise OSError("docker daemon socket went away")
        for predicate, effect in self.hooks:
            if predicate(args):
                effect(self)
        for predicate, stderr in self.failures:
            if predicate(args):
                return _R(1, stderr=stderr)
        return self._dispatch(args)

    def _dispatch(self, args):
        if args[0] == "ps":
            return _R(0, stdout="".join(
                f"{c['id']}\t{c['name']}\t{c['project']}\t"
                f"{c['instance']}\t{c['repo_root']}\n"
                for c in self.containers
            ))
        if args[:2] == ["volume", "ls"]:
            return _R(0, stdout="".join(
                f"{v['name']}\t{v['project']}\t{v['instance']}\t{v['repo_root']}\n"
                for v in self.volumes
            ))
        if args[:2] == ["network", "ls"]:
            if "--filter" in args:
                wanted = args[args.index("--filter") + 1][len("name=^"):-1]
                hit = any(n["name"] == wanted for n in self.networks)
                return _R(0, stdout=f"{wanted}\n" if hit else "")
            return _R(0, stdout="".join(
                f"{n['name']}\t{n['project']}\t{n['instance']}\t{n['repo_root']}\n"
                for n in self.networks
            ))
        if args[:2] == ["network", "inspect"]:
            name = args[2]
            if not any(n["name"] == name for n in self.networks):
                return _R(1, stderr=f"No such network: {name}")
            return _R(0, stdout=" ".join(sorted(self.members.get(name, set()))) + " ")
        if args[:2] == ["rm", "-f"]:
            doomed = set(args[2:])
            self.containers = [c for c in self.containers if c["id"] not in doomed]
            for name in list(self.members):
                self.members[name] = self.members[name] - doomed
            return _R(0)
        if args[:2] == ["volume", "rm"]:
            doomed = set(args[2:])
            self.volumes = [v for v in self.volumes if v["name"] not in doomed]
            return _R(0)
        if args[:2] == ["network", "rm"]:
            doomed = set(args[2:])
            self.networks = [n for n in self.networks if n["name"] not in doomed]
            for name in doomed:
                self.members.pop(name, None)
            return _R(0)
        raise AssertionError(f"unscripted docker call: {args!r}")

    # -- assertions ---------------------------------------------------------

    @property
    def container_ids(self) -> set[str]:
        return {c["id"] for c in self.containers}

    @property
    def volume_names(self) -> set[str]:
        return {v["name"] for v in self.volumes}

    @property
    def network_names(self) -> set[str]:
        return {n["name"] for n in self.networks}


# ---------------------------------------------------------------------------
# Git + record fixtures
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _stamp(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def held(*, hours_from_now: float) -> dict:
    """A `held` lease expiring *hours_from_now* relative to the frozen NOW."""
    return {
        "holder": "ciu@testhost:deadbe",
        "acquired_at_utc": _stamp(NOW - timedelta(days=3)),
        "renewed_at_utc": _stamp(NOW - timedelta(hours=6)),
        "expires_at_utc": _stamp(NOW + timedelta(hours=hours_from_now)),
        "mode": "held",
    }


PERPETUAL = {
    "holder": "ciu@testhost:deadbe",
    "acquired_at_utc": _stamp(NOW - timedelta(days=400)),
    "renewed_at_utc": _stamp(NOW - timedelta(days=400)),
    "expires_at_utc": None,
    "mode": "perpetual",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(["init", "-b", "main"], root).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], root).returncode == 0
    assert _git(["config", "user.name", "Test"], root).returncode == 0
    (root / "README.md").write_text("init\n", encoding="utf-8")
    assert _git(["add", "README.md"], root).returncode == 0
    assert _git(["commit", "-m", "init"], root).returncode == 0
    return root


@pytest.fixture
def docker(monkeypatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(worktree.procutil, "docker", fake)
    return fake


def network_of(instance_id: str) -> str:
    return f"{REPO_NAME}-{instance_id}-network"


def project_of(instance_id: str, stack: str = "stack") -> str:
    """The identity-form compose project (engine.identity_compose_project_name)."""
    return f"{REPO_NAME}-{instance_id}-{stack}"


def write_env(ciu_root: Path, instance_id: str) -> None:
    """CIU-75: a checkout looks like a live CIU instance because it carries the
    generated `[ciu.instance.generated]` overlay table — that is what
    `_runtime_identity` and `_reap_uses_clean` read now, not `ciu.env`."""
    from ciu.workspace_env import upsert_generated_facts

    ciu_root.mkdir(parents=True, exist_ok=True)
    upsert_generated_facts(
        ciu_root,
        {
            "repo_name": REPO_NAME,
            "instance_id": instance_id,
            "network": network_of(instance_id),
            "physical_repo_root": str(ciu_root),
            "repo_root": str(ciu_root),
            "public_fqdn": "",
        },
    )


def write_record(
    ciu_root: Path,
    *,
    logical: str,
    branch: str,
    instance_id: str | None,
    state: str = "ready",
    lease: dict | None = None,
    schema: int = 1,
    recovery: str | None = None,
    git_path: Path | None = None,
    offset: str = ".",
    extra: dict | None = None,
) -> None:
    doc: dict = {
        "schema_version": schema,
        "logical_name": logical,
        "display_name": logical,
        "branch": branch,
        "git_worktree_path": str(git_path or ciu_root),
        "ciu_root_offset": offset,
        "created_at_utc": _stamp(NOW - timedelta(days=365)),
        "base_ref": "main",
        "state": state,
        "runtime": {
            "instance_id": instance_id,
            "network": network_of(instance_id) if instance_id else None,
        },
        "recovery_status": recovery,
    }
    if schema >= 2:
        doc["lease"] = lease
    if extra:
        doc.update(extra)
    ciu_root.mkdir(parents=True, exist_ok=True)
    (ciu_root / worktree.WORKTREE_INSTANCE_RECORD).write_text(
        json.dumps(doc, indent=2), encoding="utf-8"
    )


def add_instance(
    repo: Path,
    *,
    logical: str,
    instance_id: str,
    path: Path | None = None,
    branch: str | None = None,
    env: bool = True,
    record: bool = True,
    **record_kw,
) -> Path:
    """One real linked worktree, with its record and generated identity facts."""
    branch = branch or f"feat/{logical}"
    target = path or (repo.parent / "wt" / logical)
    target.parent.mkdir(parents=True, exist_ok=True)
    res = _git(["worktree", "add", "-b", branch, str(target), "main"], repo)
    assert res.returncode == 0, res.stderr
    if env:
        write_env(target, instance_id)
    if record:
        write_record(
            target, logical=logical, branch=branch, instance_id=instance_id,
            **record_kw,
        )
    return target


def deploy_instance(
    docker: FakeDocker,
    ciu_root: Path,
    instance_id: str,
    *,
    stack: str = "stack",
    labelled: bool = True,
    containers: int = 1,
    volumes: int = 1,
    network: bool = True,
) -> str:
    """What a managed `ciu up` leaves on the host (ciu-P26's label pair)."""
    project = project_of(instance_id, stack)
    label = instance_id if labelled else ""
    root = str(ciu_root) if labelled else ""
    net = network_of(instance_id)
    if network:
        if net not in docker.network_names:
            docker.network(net)  # external: no compose project, no ciu labels
    for n in range(containers):
        docker.container(
            f"{instance_id}{stack}c{n}" * 3, f"{project}-svc{n}", project,
            instance=label, repo_root=root, network=net if network else None,
        )
    for n in range(volumes):
        docker.volume(f"{project}_data{n}", project, instance=label, repo_root=root)
    return project


def survey(repo: Path, *, now: datetime = NOW) -> dict:
    return worktree.survey_reap_groups(repo, now=now)


def categories(doc: dict) -> dict[str, str]:
    return {g["key"]: g["category"] for g in doc["groups"]}


def clean_seam(docker: FakeDocker, calls: list):
    """A `_clean_in` stand-in that behaves like a real successful clean."""
    def _clean(ciu_root, *, yes):
        calls.append((Path(ciu_root), yes))
        docker.drop_repo_root(str(ciu_root))
        return 0
    return _clean


# ===========================================================================
# O1 — the closed, non-overlapping partition
# ===========================================================================


class TestClosedPartition:
    def test_age_alone_never_reaps(self, repo, docker, monkeypatch):
        """#1 — a year-old, lease-less (schema v1) instance is `owned`.

        The controlled wrong implementation is an age rule: `created_at_utc`
        is a full year before the survey clock and every container has been
        there just as long. A survey that reasoned about age would file this
        under something destructible; the shipped one has no age input at
        all, so it cannot.
        """
        root = add_instance(repo, logical="ancient", instance_id="aaa111")
        project = deploy_instance(docker, root, "aaa111")

        doc = survey(repo)
        assert categories(doc)[project] == "owned"
        assert doc["counts"]["owned"] == 1

        monkeypatch.setattr(worktree, "_clean_in", clean_seam(docker, []))
        before = (docker.container_ids, docker.volume_names, docker.network_names)
        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["status"] == "reaped"
        assert out["reaped"] == [] and out["failed"] == []
        assert (docker.container_ids, docker.volume_names, docker.network_names) == before

    def test_missing_process_never_reaps(self, repo, docker):
        """#2 — no CIU process runs anywhere; a valid lease still means owned.

        Every container in this fixture is EXITED (the fake host does not even
        model a run state, which is the point: run state is not an input).
        """
        root = add_instance(
            repo, logical="stopped", instance_id="bbb222",
            schema=2, lease=held(hours_from_now=6),
        )
        project = deploy_instance(docker, root, "bbb222")
        assert categories(survey(repo))[project] == "owned"

    def test_basename_similarity_never_cross_reaps(self, repo, docker, monkeypatch):
        """#3 — the CIU-19 regression shape, one layer down.

        Two worktrees whose directories have the IDENTICAL basename and whose
        instance ids differ by one character. One lease has lapsed, the other
        has not. Reaping must leave every single resource of the second
        instance standing.
        """
        stale = add_instance(
            repo, logical="alpha", instance_id="ccc333",
            path=repo.parent / "one" / "same-name",
            schema=2, lease=held(hours_from_now=-1),
        )
        live = add_instance(
            repo, logical="beta", instance_id="ccc334",
            path=repo.parent / "two" / "same-name",
            schema=2, lease=held(hours_from_now=+1),
        )
        assert stale.name == live.name
        stale_project = deploy_instance(docker, stale, "ccc333")
        live_project = deploy_instance(docker, live, "ccc334")
        live_state = (
            {c["id"] for c in docker.containers if c["project"] == live_project},
            {v["name"] for v in docker.volumes if v["project"] == live_project},
        )

        doc = survey(repo)
        assert categories(doc) == {
            stale_project: "lease-expired", live_project: "owned",
        }

        calls: list = []
        monkeypatch.setattr(worktree, "_clean_in", clean_seam(docker, calls))
        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        assert [c[0] for c in calls] == [stale]
        assert (
            {c["id"] for c in docker.containers if c["project"] == live_project},
            {v["name"] for v in docker.volumes if v["project"] == live_project},
        ) == live_state
        assert network_of("ccc334") in docker.network_names

    def test_unlabelled_with_no_identity_match_is_unattributable(self, repo, docker):
        """#6 (survey half) — somebody else's compose project on the host."""
        docker.container("otherid" * 8, "other-web-1", "someone-elses-project")
        docker.volume("someone-elses-project_pgdata", "someone-elses-project")

        doc = survey(repo)
        assert categories(doc) == {"someone-elses-project": "unattributable"}
        assert "NEVER reaped" in doc["hint"]
        assert "docker compose -p <project> down -v" in doc["hint"]

    def test_colliding_project_name_is_ambiguous(self, repo, docker):
        """#7 — two live records whose identity prefixes both match."""
        one = add_instance(repo, logical="one", instance_id="ddd444")
        two = add_instance(repo, logical="two", instance_id="eee555")
        project = project_of("ddd444")
        # One group, two claims: the label says one instance, a stray
        # container of the other instance sits in the same compose project.
        docker.container("c1" * 16, "a", project, instance="ddd444", repo_root=str(one))
        docker.container("c2" * 16, "b", project, instance="eee555", repo_root=str(two))

        doc = survey(repo)
        group = doc["groups"][0]
        assert group["category"] == "ambiguous"
        assert "ddd444" in group["reason"] and "eee555" in group["reason"]

    def test_duplicate_instance_id_across_records_is_ambiguous(self, repo, docker):
        """Two records claiming ONE INSTANCE_ID: no single owner resolves."""
        one = add_instance(repo, logical="one", instance_id="fff666")
        add_instance(repo, logical="two", instance_id="fff666")
        project = deploy_instance(docker, one, "fff666")

        doc = survey(repo)
        assert categories(doc)[project] == "ambiguous"
        assert "more than one instance record" in doc["groups"][0]["reason"]

    def test_v1_record_surveys_as_owned_and_is_never_rewritten(self, repo, docker):
        """#13 — a schema-v1 record has no lease concept and is left alone."""
        root = add_instance(repo, logical="legacy", instance_id="a1b2c3")
        project = deploy_instance(docker, root, "a1b2c3")
        record_path = root / worktree.WORKTREE_INSTANCE_RECORD
        before = record_path.read_bytes()

        doc = survey(repo)
        group = next(g for g in doc["groups"] if g["key"] == project)
        assert group["category"] == "owned"
        assert group["lease"] is None
        assert record_path.read_bytes() == before

    def test_released_v2_lease_is_owned_not_expired(self, repo, docker):
        """`lease: null` means "claims nothing", never "abandoned"."""
        root = add_instance(
            repo, logical="released", instance_id="b2c3d4", schema=2, lease=None
        )
        project = deploy_instance(docker, root, "b2c3d4")
        assert categories(survey(repo))[project] == "owned"

    def test_perpetual_lease_never_expires(self, repo, docker):
        root = add_instance(
            repo, logical="forever", instance_id="c3d4e5", schema=2, lease=PERPETUAL
        )
        project = deploy_instance(docker, root, "c3d4e5")
        far_future = NOW + timedelta(days=10_000)
        assert categories(survey(repo, now=far_future))[project] == "owned"

    def test_inconsistent_record_is_a_finding_and_never_licenses_destruction(
        self, repo, docker
    ):
        """#16 — `list_instance_records` would RAISE here; the survey must not.

        A survey that dies on one bad record is useless exactly when it is
        most needed. The group is reported as `ambiguous`, which is a
        never-destroyed category: a record Git contradicts cannot be the
        proof a destructive verb relies on.
        """
        root = add_instance(
            repo, logical="drifted", instance_id="d4e5f6",
            branch="feat/drifted",
        )
        write_record(
            root, logical="drifted", branch="a-branch-git-does-not-have",
            instance_id="d4e5f6",
        )
        project = deploy_instance(docker, root, "d4e5f6")

        with pytest.raises(worktree.WorktreeError, match="claims branch"):
            worktree.list_instance_records(repo)

        doc = survey(repo)
        assert categories(doc)[project] == "ambiguous"
        kinds = {f["kind"] for f in doc["findings"]}
        assert kinds == {"inconsistent-record"}
        assert "claims branch" in doc["findings"][0]["detail"]

    def test_every_cross_check_list_instance_records_raises_on_is_a_finding(
        self, repo, docker
    ):
        """All four checks, and more than one from a single record.

        `list_instance_records` refuses the whole family on the FIRST of
        these; the survey reports every one of them and keeps going, because
        "the survey crashed" is the worst possible answer to "what is safe to
        delete?".
        """
        first = add_instance(repo, logical="twin", instance_id="ab0001")
        second = add_instance(
            repo, logical="second", instance_id="ab0002", branch="feat/second"
        )
        # one record, TWO contradictions at once (wrong git path AND wrong
        # branch), plus a duplicate logical identity and a wrong CIU offset
        write_record(
            second, logical="twin", branch="a-branch-git-does-not-have",
            instance_id="ab0002", git_path=repo, offset="not-this-subdir",
        )
        deploy_instance(docker, first, "ab0001")
        project = deploy_instance(docker, second, "ab0002")

        with pytest.raises(worktree.WorktreeError):
            worktree.list_instance_records(repo)

        doc = survey(repo)
        details = " || ".join(f["detail"] for f in doc["findings"])
        assert "claims Git path" in details
        assert "claims CIU-root offset" in details
        assert "claims branch" in details
        assert "duplicate logical worktree identity" in details
        assert categories(doc)[project] == "ambiguous"

    def test_an_allocating_record_with_no_runtime_identity_is_skipped(
        self, repo, docker
    ):
        """A record mid-allocation claims no INSTANCE_ID yet; it cannot
        attribute anything, and the checkout's own ciu.env answers instead."""
        root = add_instance(
            repo, logical="allocating", instance_id=None, state="allocating",
        )
        write_env(root, "ab0003")
        project = deploy_instance(docker, root, "ab0003")

        doc = survey(repo)
        group = next(g for g in doc["groups"] if g["key"] == project)
        assert group["category"] == "owned"
        assert group["logical_name"] is None      # attributed by ciu.env, not the record
        assert doc["findings"] == []

    def test_an_inconsistent_record_with_no_instance_id_distrusts_nothing(
        self, repo, docker
    ):
        """A mid-allocation record can be contradicted by Git too, but it
        claims no INSTANCE_ID, so there is nothing for the contradiction to
        cast doubt ON. The finding is still reported; the checkout's own
        ciu.env still attributes its resources, and they stay `owned`.
        """
        root = add_instance(
            repo, logical="halfallocated", instance_id=None, state="allocating",
        )
        write_record(
            root, logical="halfallocated", branch="a-branch-git-does-not-have",
            instance_id=None, state="allocating",
        )
        write_env(root, "ab0004")
        project = deploy_instance(docker, root, "ab0004")

        doc = survey(repo)
        assert [f["kind"] for f in doc["findings"]] == ["inconsistent-record"]
        assert categories(doc)[project] == "owned"

    def test_unreadable_record_is_a_finding_and_the_checkout_still_owns(
        self, repo, docker
    ):
        """A corrupt record must never make a LIVE instance look unclaimed.

        The checkout's own ciu.env still declares the identity, so the group
        is `owned` — the single most important false-positive this module has
        to avoid, because the alternative reading is `orphaned`, which IS
        destructible.
        """
        root = add_instance(repo, logical="corrupt", instance_id="e5f6a7")
        (root / worktree.WORKTREE_INSTANCE_RECORD).write_text("{ not json",
                                                              encoding="utf-8")
        project = deploy_instance(docker, root, "e5f6a7")

        doc = survey(repo)
        assert categories(doc)[project] == "owned"
        assert [f["kind"] for f in doc["findings"]] == ["unreadable-record"]
        assert doc["identity_complete"] is True

    def test_labelled_id_of_a_recordless_registered_checkout_is_owned(
        self, repo, docker
    ):
        """The precedence rule the seven-category table needs to stay closed.

        `orphaned` is defined as "no record AND no known worktree". A
        checkout whose record was deleted but which Git still registers and
        whose own ciu.env declares that INSTANCE_ID is therefore neither
        orphaned nor record-backed — and a live checkout still owns what it
        created, so it is `owned`.
        """
        root = add_instance(
            repo, logical="recordless", instance_id="f6a7b8", record=False
        )
        project = deploy_instance(docker, root, "f6a7b8")

        doc = survey(repo)
        group = next(g for g in doc["groups"] if g["key"] == project)
        assert group["category"] == "owned"
        assert str(root) in group["reason"]
        assert group["logical_name"] is None

    def test_every_group_lands_in_exactly_one_of_the_seven(self, repo, docker):
        """The adversarial mixed host: one group per category, all at once."""
        owned = add_instance(
            repo, logical="owned", instance_id="100001",
            schema=2, lease=held(hours_from_now=5),
        )
        expired = add_instance(
            repo, logical="expired", instance_id="100002",
            schema=2, lease=held(hours_from_now=-5),
        )
        broken = add_instance(
            repo, logical="broken", instance_id="100003",
            state="recovery-required", recovery="env-generation-failed",
        )
        drift = add_instance(repo, logical="drift", instance_id="100004")
        write_record(
            drift, logical="drift", branch="not-the-git-branch",
            instance_id="100004",
        )
        projects = {
            "owned": deploy_instance(docker, owned, "100001"),
            "lease-expired": deploy_instance(docker, expired, "100002"),
            "partial-cleanup": deploy_instance(docker, broken, "100003"),
            "ambiguous": deploy_instance(docker, drift, "100004"),
        }
        # orphaned: labelled for an id nothing claims, root still present
        docker.container("o" * 32, "orphan-1", "gone-project",
                         instance="999999", repo_root=str(repo))
        projects["orphaned"] = "gone-project"
        # checkout-missing: same, but the labelled repo root is gone
        docker.container("m" * 32, "missing-1", "vanished-project",
                         instance="888888", repo_root=str(repo / "no" / "such"))
        projects["checkout-missing"] = "vanished-project"
        # unattributable: no labels, no identity-form name
        docker.container("u" * 32, "stranger-1", "stranger")
        projects["unattributable"] = "stranger"

        doc = survey(repo)
        got = categories(doc)
        assert {v: k for k, v in projects.items()} == got
        assert set(got.values()) == set(worktree.REAP_CATEGORIES)
        # exactly one category each, and every group accounted for exactly once
        assert len(doc["groups"]) == len(projects)
        assert sum(doc["counts"].values()) == len(doc["groups"])

    def test_envelope_is_versioned_and_counts_carry_all_seven(self, repo, docker):
        """#11 — including the zero-valued categories."""
        root = add_instance(repo, logical="solo", instance_id="200001")
        deploy_instance(docker, root, "200001")

        doc = survey(repo)
        assert doc["schema_version"] == worktree.REAP_SCHEMA_VERSION == 1
        assert doc["operation"] == "reap"
        assert doc["status"] == "survey" and doc["status"] in worktree.REAP_STATUSES
        assert set(doc["counts"]) == set(worktree.REAP_CATEGORIES)
        assert doc["counts"] == {
            "owned": 1, "lease-expired": 0, "checkout-missing": 0,
            "orphaned": 0, "partial-cleanup": 0, "unattributable": 0,
            "ambiguous": 0,
        }
        assert {g["category"] for g in doc["groups"]} <= set(worktree.REAP_CATEGORIES)

    def test_survey_of_an_empty_host_is_an_empty_partition(self, repo, docker):
        doc = survey(repo)
        assert doc["groups"] == []
        assert set(doc["counts"]) == set(worktree.REAP_CATEGORIES)
        assert doc["hint"] == "nothing provably disposable."

    def test_resources_compose_never_created_are_not_surveyed_at_all(
        self, repo, docker
    ):
        """A project-less container/volume/network is not this verb's business.

        Not "unattributable" — not surveyed at all, so no flag combination
        can even name it.
        """
        docker.container("x" * 32, "some-hand-run-container", "")
        docker.volume("a-hand-made-volume", "")
        docker.network("bridge")

        assert survey(repo)["groups"] == []

    def test_an_unrecognised_loose_network_is_ignored(self, repo, docker):
        docker.network("host")
        docker.network("unrelated-a1b2c3-network")
        assert survey(repo)["groups"] == []

    def test_a_known_identity_network_with_no_compose_group_is_its_own_group(
        self, repo, docker
    ):
        """`ciu env generate` ran, `ciu up` never did: only the network exists."""
        root = add_instance(
            repo, logical="netonly", instance_id="300001",
            schema=2, lease=held(hours_from_now=-2),
        )
        docker.network(network_of("300001"))

        doc = survey(repo)
        group = doc["groups"][0]
        assert group["key"] == network_of("300001")
        assert group["compose_project"] is None
        assert group["category"] == "lease-expired"
        assert group["networks"] == [network_of("300001")]
        assert str(root) in group["ciu_root"]

    def test_identity_form_project_name_attributes_an_unlabelled_group(
        self, repo, docker
    ):
        """A pre-ciu-P26 `ciu up` labelled nothing; the project name still
        carries `{repo}-{INSTANCE_ID}-`, which is derived from the identity
        network so there is only ever one spelling of the convention."""
        root = add_instance(
            repo, logical="prelabel", instance_id="400001",
            schema=2, lease=held(hours_from_now=-3),
        )
        project = deploy_instance(docker, root, "400001", labelled=False)

        group = next(g for g in survey(repo)["groups"] if g["key"] == project)
        assert group["category"] == "lease-expired"
        assert group["instance_id"] == "400001"


# ===========================================================================
# O2 — destructive safety
# ===========================================================================


class TestDestructiveSafety:
    def test_expired_lease_delegates_to_clean_in_with_that_exact_ciu_root(
        self, repo, docker, monkeypatch
    ):
        """#4 — the ciu-P28 lesson: a MANAGED instance is cleaned, never
        bare-deleted. `ciu clean` knows the rendered config, the `vol-*`
        hostdirs and the root-helper path a `docker rm` knows nothing about.
        """
        root = add_instance(
            repo, logical="lapsed", instance_id="500001",
            schema=2, lease=held(hours_from_now=-1),
        )
        deploy_instance(docker, root, "500001")
        calls: list = []
        monkeypatch.setattr(worktree, "_clean_in", clean_seam(docker, calls))

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert calls == [(root, True)]
        assert out["status"] == "reaped"
        assert out["reaped"][0]["notes"] == [f"cleaned in {root}"]
        assert docker.container_ids == set()
        # and no bare docker removal was attempted anywhere
        assert not any(c[:2] == ["rm", "-f"] for c in docker.calls)

    def test_checkout_missing_removes_docker_only_and_leaves_git_alone(
        self, repo, docker
    ):
        """#5 — the checkout is gone, so the record went with it.

        `ciu.repo-root` is the only durable, checkout-EXTERNAL evidence of
        where the instance lived, which is why the category is decided from
        the label rather than from a record that cannot exist.
        """
        vanished = repo.parent / "wt" / "deleted-by-a-crashed-dispatcher"
        docker.container("k" * 32, "ghost-1", "ghost-project",
                         instance="600001", repo_root=str(vanished),
                         network="ghost-net")
        docker.volume("ghost-project_data", "ghost-project",
                      instance="600001", repo_root=str(vanished))
        docker.network("ghost-net", project="ghost-project",
                       instance="600001", repo_root=str(vanished))
        branches_before = _git(["branch", "--list"], repo).stdout

        doc = survey(repo)
        assert categories(doc) == {"ghost-project": "checkout-missing"}
        assert "worktree branches" in doc["groups"][0]["reason"]

        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["status"] == "reaped"
        assert docker.container_ids == set()
        assert docker.volume_names == set()
        assert docker.network_names == set()
        assert _git(["branch", "--list"], repo).stdout == branches_before

    def test_orphaned_is_reaped_but_only_when_nothing_claims_the_id(
        self, repo, docker
    ):
        live = add_instance(repo, logical="live", instance_id="700001")
        live_project = deploy_instance(docker, live, "700001")
        docker.container("z" * 32, "orphan-1", "orphan-project",
                         instance="700002", repo_root=str(repo))
        docker.volume("orphan-project_data", "orphan-project",
                      instance="700002", repo_root=str(repo))

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        assert [r["group"] for r in out["reaped"]] == ["orphan-project"]
        assert docker.container_ids == {
            c["id"] for c in docker.containers if c["project"] == live_project
        }
        assert "orphan-project_data" not in docker.volume_names

    def test_unattributable_is_never_reaped_and_cannot_be_selected(
        self, repo, docker
    ):
        """#6 (destructive half) — no flag combination reaches this category."""
        docker.container("q" * 32, "stranger-1", "stranger")
        docker.volume("stranger_data", "stranger")
        before = (docker.container_ids, docker.volume_names)

        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["status"] == "reaped"
        assert out["reaped"] == [] and out["failed"] == []
        assert (docker.container_ids, docker.volume_names) == before

        with pytest.raises(worktree.WorktreeError) as exc:
            worktree.reap_groups(
                repo, yes=True, categories="unattributable", now=NOW
            )
        assert "never acted on" in str(exc.value)
        assert (docker.container_ids, docker.volume_names) == before

    def test_ambiguous_is_never_reaped_and_cannot_be_selected(self, repo, docker):
        """#7 (destructive half), including the deliberately adversarial
        `--category orphaned,ambiguous`: naming a legal category alongside an
        illegal one does not smuggle the illegal one through."""
        one = add_instance(repo, logical="one", instance_id="800001")
        two = add_instance(repo, logical="two", instance_id="800002")
        project = project_of("800001")
        docker.container("m1" * 16, "a", project, instance="800001", repo_root=str(one))
        docker.container("m2" * 16, "b", project, instance="800002", repo_root=str(two))
        before = docker.container_ids

        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["failed"] == [] and out["reaped"] == []
        assert docker.container_ids == before

        for selector in ("ambiguous", "orphaned,ambiguous", "ambiguous,orphaned"):
            with pytest.raises(worktree.WorktreeError, match="never acted on"):
                worktree.reap_groups(
                    repo, yes=True, categories=selector, now=NOW
                )
        assert docker.container_ids == before

    def test_owned_is_not_selectable_either(self, repo, docker):
        with pytest.raises(worktree.WorktreeError, match="never acted on"):
            worktree.resolve_reap_categories("owned")

    @pytest.mark.parametrize("selector", ["nonsense", "prunable", "reaped"])
    def test_unknown_category_is_refused(self, selector):
        with pytest.raises(worktree.WorktreeError, match="unknown --category"):
            worktree.resolve_reap_categories(selector)

    def test_empty_category_selector_is_refused(self):
        with pytest.raises(worktree.WorktreeError, match="at least one category"):
            worktree.resolve_reap_categories(" , ")

    def test_default_category_set_is_exactly_the_four_destructible(self):
        assert worktree.resolve_reap_categories(None) == (
            worktree.REAP_DESTRUCTIBLE_CATEGORIES
        )
        assert set(worktree.REAP_DESTRUCTIBLE_CATEGORIES) == {
            "checkout-missing", "lease-expired", "orphaned", "partial-cleanup",
        }
        assert set(worktree.REAP_CATEGORIES) - set(
            worktree.REAP_DESTRUCTIBLE_CATEGORIES
        ) == {"owned", "unattributable", "ambiguous"}

    def test_category_narrows_what_a_yes_pass_touches(self, repo, docker, monkeypatch):
        expired = add_instance(
            repo, logical="expired", instance_id="900001",
            schema=2, lease=held(hours_from_now=-1),
        )
        expired_project = deploy_instance(docker, expired, "900001")
        docker.container("p" * 32, "orphan-1", "orphan-project",
                         instance="900002", repo_root=str(repo))
        monkeypatch.setattr(worktree, "_clean_in", clean_seam(docker, []))

        out = worktree.reap_groups(
            repo, yes=True, categories="orphaned", now=NOW
        )

        assert out["categories"] == ["orphaned"]
        assert [r["group"] for r in out["reaped"]] == ["orphan-project"]
        assert any(c["project"] == expired_project for c in docker.containers)

    def test_partial_cleanup_is_the_declared_recovery_state_only(
        self, repo, docker, monkeypatch
    ):
        """#8, as corrected by the ciu-P27 amendment.

        A "containers gone, volumes remain" residue is NOT the criterion —
        `ciu down` deliberately preserves volumes, so that shape is also what
        a perfectly owned, stopped instance looks like, and nothing anywhere
        records what "all its resources" would have been. The criterion is
        the record's OWN declared `recovery-required` state.
        """
        broken = add_instance(
            repo, logical="halfbuilt", instance_id="a00001",
            state="recovery-required", recovery="env-generation-failed",
        )
        project = project_of("a00001")
        docker.volume(f"{project}_data", project,
                      instance="a00001", repo_root=str(broken))
        monkeypatch.setattr(worktree, "_clean_in", clean_seam(docker, []))

        doc = survey(repo)
        assert categories(doc)[project] == "partial-cleanup"
        assert "recovery-required" in doc["groups"][0]["reason"]

        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["status"] == "reaped"
        assert docker.volume_names == set()
        assert out["groups"] == []          # the re-survey shows it gone

    def test_a_volumes_only_owned_instance_is_never_partial_cleanup(
        self, repo, docker
    ):
        """The catastrophic false positive the narrowing exists to prevent.

        Containers gone, volumes standing, lease valid: this is a normal
        stopped instance, and its volumes hold the data. It must classify
        `owned` and survive `-y` untouched.
        """
        root = add_instance(
            repo, logical="stopped", instance_id="a00002",
            schema=2, lease=held(hours_from_now=8),
        )
        project = deploy_instance(
            docker, root, "a00002", containers=0, volumes=2, network=False
        )

        assert categories(survey(repo))[project] == "owned"
        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["reaped"] == []
        assert len(docker.volume_names) == 2

    def test_shared_network_survives_reaping_one_joined_instance(
        self, repo, docker
    ):
        """#9 — S16.1 shared infra: B's containers hold a SECOND membership on
        A's identity network. Reaping A must not disconnect a live B."""
        gone = repo.parent / "wt" / "reference-instance-deleted"
        shared = network_of("b00001")
        docker.container("A" * 32, "a-web-1", "a-project",
                         instance="b00001", repo_root=str(gone), network=shared)
        docker.volume("a-project_data", "a-project",
                      instance="b00001", repo_root=str(gone))
        docker.network(shared, project="a-project",
                       instance="b00001", repo_root=str(gone))
        # instance B (owned, live) joined the same network via shared-infra
        other = add_instance(repo, logical="joiner", instance_id="b00002")
        docker.container("B" * 32, "b-api-1", project_of("b00002"),
                         instance="b00002", repo_root=str(other), network=shared)

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        assert shared in docker.network_names
        note = " ".join(out["reaped"][0]["notes"])
        assert f"network {shared} left standing" in note
        assert "still joined" in note
        assert "B" * 32 in note
        assert "B" * 32 in docker.container_ids
        assert "A" * 32 not in docker.container_ids

    def test_a_sibling_stack_of_the_same_identity_blocks_the_shared_network(
        self, repo, docker
    ):
        """One workspace, two stacks, ONE identity network (S2.6): the network
        is removed only once every group that shares it is gone — the last one
        out turns off the light.

        The checkout keeps its record (so the identity, and therefore the
        identity network's name, is known) but has lost its generated ciu.env,
        so `ciu clean` cannot run there and the direct removal path is the one
        under test.
        """
        root = add_instance(
            repo, logical="twostack", instance_id="c00001", env=False,
            schema=2, lease=held(hours_from_now=-2),
        )
        net = network_of("c00001")
        docker.network(net)
        for stack in ("api", "db"):
            docker.container(f"{stack}" * 8, f"{stack}-1", f"proj-{stack}",
                             instance="c00001", repo_root=str(root), network=net)

        doc = survey(repo)
        assert {g["key"]: g["networks"] for g in doc["groups"]} == {
            "proj-api": [net], "proj-db": [net],
        }

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        notes = {r["group"]: " ".join(r["notes"]) for r in out["reaped"]}
        assert "left standing: still needed by proj-db" in notes["proj-api"]
        assert f"removed network {net}" in notes["proj-db"]
        assert net not in docker.network_names

    def test_a_network_removed_by_a_concurrent_operator_is_not_an_error(
        self, repo, docker
    ):
        """A racing `docker network prune` between the survey and the removal
        is a no-op, not a failure: the desired state was reached anyway."""
        gone = repo.parent / "wt" / "absent"
        docker.container("n" * 32, "n-1", "n-project",
                         instance="d00001", repo_root=str(gone))
        docker.network("n-net", project="n-project",
                       instance="d00001", repo_root=str(gone))
        docker.when(
            lambda a: a[:2] == ["rm", "-f"],
            lambda host: host.networks.clear(),
        )

        doc = survey(repo)
        assert doc["groups"][0]["networks"] == ["n-net"]

        out = worktree.reap_groups(repo, yes=True, now=NOW)
        assert out["status"] == "reaped"
        assert "network n-net was already gone" in " ".join(
            out["reaped"][0]["notes"]
        )


# ===========================================================================
# The identity-completeness interlock
# ===========================================================================


class TestIdentityInterlock:
    def _blind_checkout(self, repo: Path) -> Path:
        """A registered checkout that WAS managed but whose identity is
        unreadable from both its record and its ciu.env."""
        root = add_instance(repo, logical="blind", instance_id="e00001", env=False)
        (root / worktree.WORKTREE_INSTANCE_RECORD).write_text("{}", encoding="utf-8")
        return root

    def test_an_unresolvable_checkout_marks_the_survey_incomplete(
        self, repo, docker
    ):
        root = self._blind_checkout(repo)
        doc = survey(repo)
        assert doc["identity_complete"] is False
        assert doc["unresolved_checkouts"] == [str(root)]
        assert "DISARMED" in doc["hint"]

    def test_orphaned_is_refused_while_an_identity_is_unresolvable(
        self, repo, docker
    ):
        """The worst false positive this module can produce: a corrupted
        record on a LIVE instance making its own labelled resources look
        unclaimed. The refusal is LOUD (status partial, exit 1), never a
        silent skip."""
        self._blind_checkout(repo)
        docker.container("r" * 32, "maybe-orphan-1", "maybe-orphan",
                         instance="e00001", repo_root=str(repo))
        before = docker.container_ids

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert out["reaped"] == []
        assert out["failed"][0]["group"] == "maybe-orphan"
        assert "could not read an identity" in out["failed"][0]["reason"]
        assert docker.container_ids == before

    def test_the_interlock_only_disarms_orphaned(self, repo, docker, monkeypatch):
        """A record-backed category still reaps: it never depended on the
        "nothing claims this id" premise the interlock protects."""
        self._blind_checkout(repo)
        expired = add_instance(
            repo, logical="expired", instance_id="e00002",
            schema=2, lease=held(hours_from_now=-4),
        )
        deploy_instance(docker, expired, "e00002")
        calls: list = []
        monkeypatch.setattr(worktree, "_clean_in", clean_seam(docker, calls))

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        assert [c[0] for c in calls] == [expired]


# ===========================================================================
# O3 — transactional isolation, post-state truth, purity
# ===========================================================================


class TestTransactionalIsolation:
    def _two_orphans(self, repo: Path, docker: FakeDocker) -> None:
        for tag in ("aa", "bb"):
            docker.container(tag * 16, f"{tag}-1", f"{tag}-project",
                             instance=f"f000{tag}", repo_root=str(repo))
            docker.volume(f"{tag}-project_data", f"{tag}-project",
                          instance=f"f000{tag}", repo_root=str(repo))

    def test_one_volume_rm_failure_isolates_that_group(self, repo, docker):
        """#10 — the whole sweep must not die with one group."""
        self._two_orphans(repo, docker)
        docker.fail(
            lambda a: a[:2] == ["volume", "rm"] and "aa-project_data" in a,
            "Error response from daemon: volume is in use",
        )

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert [r["group"] for r in out["reaped"]] == ["bb-project"]
        assert out["failed"] == [{
            "group": "aa-project",
            "reason": "volume rm: Error response from daemon: volume is in use",
        }]
        # the failed group's volume survives; the healthy group is fully gone
        assert docker.volume_names == {"aa-project_data"}
        assert docker.container_ids == set()

    def test_a_container_rm_failure_aborts_that_group_before_its_volumes(
        self, repo, docker
    ):
        """Strict order: a volume still attached to a container we failed to
        remove cannot be removed either, and the derived error would bury the
        real one."""
        self._two_orphans(repo, docker)
        docker.fail(
            lambda a: a[:2] == ["rm", "-f"] and "aa" * 16 in a,
            "Error response from daemon: cannot remove",
        )

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert out["failed"][0]["reason"].startswith("container rm:")
        assert "aa-project_data" in docker.volume_names
        assert not any(
            c[:2] == ["volume", "rm"] and "aa-project_data" in c
            for c in docker.calls
        )

    def test_a_volumes_only_group_skips_straight_to_the_volumes(
        self, repo, docker
    ):
        """An orphaned residue whose containers are already gone."""
        docker.volume("leftover-project_data", "leftover-project",
                      instance="f00009", repo_root=str(repo))

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        assert out["reaped"][0]["notes"] == ["removed 1 volume(s)"]
        assert docker.volume_names == set()
        assert not any(c[:2] == ["rm", "-f"] for c in docker.calls)

    def test_the_daemon_dying_mid_pass_is_a_failure_not_a_crash(
        self, repo, docker
    ):
        """docker being ABSENT means "no resources" during a survey; the
        socket dying during a REMOVAL is a real failure and must be reported
        as one, with the OS error text."""
        docker.container("y" * 32, "y-1", "y-project",
                         instance="f00010", repo_root=str(repo))
        docker.boom(lambda a: a[:2] == ["rm", "-f"])

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert out["failed"][0]["reason"] == (
            "container rm: docker daemon socket went away"
        )
        assert docker.container_ids == {"y" * 32}

    def test_a_network_rm_failure_lands_in_failed(self, repo, docker):
        gone = repo.parent / "wt" / "absent"
        docker.container("g" * 32, "g-1", "g-project",
                         instance="f00003", repo_root=str(gone), network="g-net")
        docker.network("g-net", project="g-project",
                       instance="f00003", repo_root=str(gone))
        docker.fail(lambda a: a[:2] == ["network", "rm"], "network has endpoints")

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert out["failed"][0]["reason"] == (
            "network rm g-net: network has endpoints"
        )

    def test_a_failing_clean_never_falls_back_to_a_bare_docker_removal(
        self, repo, docker, monkeypatch
    ):
        """The ciu-P28 lesson stated as a negative: when the instance's own
        teardown fails, CIU reports it — it does not second-guess it by
        deleting the resources itself."""
        root = add_instance(
            repo, logical="stubborn", instance_id="f00004",
            schema=2, lease=held(hours_from_now=-2),
        )
        deploy_instance(docker, root, "f00004")
        monkeypatch.setattr(worktree, "_clean_in", lambda r, *, yes: 3)
        before = docker.container_ids

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert "exit 3" in out["failed"][0]["reason"]
        assert docker.container_ids == before
        assert not any(c[:2] == ["rm", "-f"] for c in docker.calls)

    def test_a_clean_that_refuses_outright_is_reported_not_raised(
        self, repo, docker, monkeypatch
    ):
        root = add_instance(
            repo, logical="noenv", instance_id="f00005",
            schema=2, lease=held(hours_from_now=-2),
        )
        deploy_instance(docker, root, "f00005")

        def _raise(_root, *, yes):
            raise worktree.WorktreeError(
                "[S16] carries no generated instance identity"
            )

        monkeypatch.setattr(worktree, "_clean_in", _raise)
        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert "could not run" in out["failed"][0]["reason"]

    def test_an_unexpected_refusal_mid_sweep_still_processes_the_rest(
        self, repo, docker, monkeypatch
    ):
        """Nothing escapes the loop — the ciu-P28 defect shape one layer down,
        where a mid-loop raise returned NO document and silently skipped every
        later candidate."""
        self._two_orphans(repo, docker)
        original = worktree._reap_one_group

        def _explodes_on_the_first_group(group, blocked):
            if group["key"] == "aa-project":
                raise worktree.WorktreeError("[S16.10] something unforeseen")
            return original(group, blocked)

        monkeypatch.setattr(worktree, "_reap_one_group", _explodes_on_the_first_group)

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "partial"
        assert [r["group"] for r in out["reaped"]] == ["bb-project"]
        assert "remaining groups still processed" in out["failed"][0]["reason"]

    def test_a_full_success_is_reaped_and_the_document_is_a_re_survey(
        self, repo, docker
    ):
        """O3's post-state-truth clause: the returned counts/groups describe
        the host AFTER the pass, not the plan that produced it."""
        self._two_orphans(repo, docker)
        pre = survey(repo)
        assert pre["counts"]["orphaned"] == 2

        out = worktree.reap_groups(repo, yes=True, now=NOW)

        assert out["status"] == "reaped"
        assert out["counts"]["orphaned"] == 0
        assert out["groups"] == []
        assert out["hint"] == "nothing provably disposable."
        assert sorted(r["group"] for r in out["reaped"]) == [
            "aa-project", "bb-project",
        ]

    def test_two_consecutive_surveys_are_byte_identical_and_change_nothing(
        self, repo, docker
    ):
        """#12 — survey purity. The document deliberately carries no timestamp
        of its own, so "modulo timestamps" is simply "identical"."""
        root = add_instance(
            repo, logical="pure", instance_id="f00006",
            schema=2, lease=held(hours_from_now=3),
        )
        deploy_instance(docker, root, "f00006")
        docker.container("s" * 32, "stranger-1", "stranger")
        before = (
            list(docker.containers), list(docker.volumes), list(docker.networks)
        )
        record_bytes = (root / worktree.WORKTREE_INSTANCE_RECORD).read_bytes()

        first = json.dumps(survey(repo), sort_keys=True)
        second = json.dumps(survey(repo), sort_keys=True)

        assert first == second
        assert (
            list(docker.containers), list(docker.volumes), list(docker.networks)
        ) == before
        assert (root / worktree.WORKTREE_INSTANCE_RECORD).read_bytes() == record_bytes
        assert not any(
            c[:2] in (["rm", "-f"], ["volume", "rm"], ["network", "rm"])
            for c in docker.calls
        )

    def test_without_yes_the_survey_is_returned_verbatim(self, repo, docker):
        self._two_orphans(repo, docker)
        assert worktree.reap_groups(repo, now=NOW) == survey(repo)

    def test_dry_run_prints_the_plan_and_touches_nothing(
        self, repo, docker, monkeypatch
    ):
        managed = add_instance(
            repo, logical="lapsed", instance_id="f00007",
            schema=2, lease=held(hours_from_now=-1),
        )
        deploy_instance(docker, managed, "f00007")
        self._two_orphans(repo, docker)
        monkeypatch.setattr(
            worktree, "_clean_in",
            lambda *a, **k: pytest.fail("dry run must not clean"),
        )
        before = (docker.container_ids, docker.volume_names, docker.network_names)

        out = worktree.reap_groups(repo, yes=True, dry_run=True, now=NOW)

        assert out["status"] == "dry-run"
        assert (docker.container_ids, docker.volume_names, docker.network_names) == before
        plans = {p["group"]: p["commands"] for p in out["plan"]}
        assert plans[project_of("f00007")] == [f"(cd {managed} && ciu clean -y)"]
        assert plans["aa-project"] == [
            "docker rm -f " + "aa" * 16,
            "docker volume rm aa-project_data",
        ]
        assert "reaped" not in out and "failed" not in out

    def test_dry_run_of_a_group_with_nothing_left_says_so(self, repo, docker):
        root = add_instance(
            repo, logical="netonly", instance_id="f00008",
            schema=2, lease=held(hours_from_now=-1), env=False,
        )
        write_env(root, "f00008")
        (root / "ciu.global.worktree.toml.j2").unlink()
        docker.network(network_of("f00008"))

        out = worktree.reap_groups(repo, yes=True, dry_run=True, now=NOW)
        commands = out["plan"][0]["commands"]
        assert commands == [
            f"docker network rm {network_of('f00008')}"
            "  # only if no container is still joined"
        ]


# ===========================================================================
# #14 lease lifecycle · #15 clock discipline
# ===========================================================================


class TestLeaseLifecycleChangesTheNextSurvey:
    @pytest.fixture
    def leased(self, repo, docker):
        root = add_instance(
            repo, logical="lifecycle", instance_id="aa0001",
            schema=2, lease=held(hours_from_now=-1),
        )
        project = deploy_instance(docker, root, "aa0001")
        assert categories(survey(repo))[project] == "lease-expired"
        return root, project

    def test_extend_moves_it_back_to_owned(self, leased, repo):
        _root, project = leased
        worktree.apply_lease(repo, "lifecycle", extend="48h")
        assert categories(survey(repo))[project] == "owned"

    def test_perpetual_moves_it_back_to_owned_forever(self, leased, repo):
        _root, project = leased
        worktree.apply_lease(repo, "lifecycle", perpetual=True)
        assert categories(
            survey(repo, now=NOW + timedelta(days=3650))
        )[project] == "owned"

    def test_release_is_owned_never_lease_expired(self, leased, repo):
        """"claims nothing" is not "abandoned": with no TTL configured, a
        released instance can never lapse, so it can never be reaped."""
        _root, project = leased
        worktree.apply_lease(repo, "lifecycle", release=True)
        assert categories(survey(repo))[project] == "owned"

    def test_re_expiring_after_an_extend_becomes_lease_expired_again(
        self, leased, repo
    ):
        """CIU-76: `apply_lease` must accept the same frozen `now` its
        lower-level `acquire_lease`/`make_lease_perpetual` already do —
        without it, the 1h extend anchors to the REAL wall clock while the
        survey check below anchors to the fixture's frozen `NOW`, so the
        assertion's truth silently depends on how far the real clock has
        drifted from `NOW` since this fixture was written (reproduced
        2026-08-31: failed on a clean checkout for exactly this reason)."""
        _root, project = leased
        worktree.apply_lease(repo, "lifecycle", extend="1h", now=NOW)
        assert categories(
            survey(repo, now=NOW + timedelta(days=2))
        )[project] == "lease-expired"


class TestClockDiscipline:
    def test_a_naive_survey_clock_is_refused(self, repo, docker):
        with pytest.raises(worktree.WorktreeError, match="timezone-aware"):
            worktree.survey_reap_groups(repo, now=datetime(2026, 8, 25, 12, 0))

    def test_expiry_is_evaluated_against_the_injected_clock_only(
        self, repo, docker
    ):
        root = add_instance(
            repo, logical="edge", instance_id="aa0002",
            schema=2, lease=held(hours_from_now=1),
        )
        project = deploy_instance(docker, root, "aa0002")
        assert categories(survey(repo, now=NOW))[project] == "owned"
        one_hour_later = NOW + timedelta(hours=1)
        assert categories(survey(repo, now=one_hour_later))[project] == "lease-expired"

    def test_an_unparseable_stored_expiry_does_not_crash_and_is_not_expired(
        self, tmp_path
    ):
        """#15's tail: the read path already refuses a naive timestamp, but if
        one ever reaches here, "I cannot read the claim" must never be rounded
        down to "there is no claim"."""
        record = worktree.WorktreeInstanceRecord(
            logical_name="x", display_name="x", branch="b",
            git_worktree_path=tmp_path, ciu_root_offset=Path("."),
            created_at_utc=_stamp(NOW), base_ref="main", state="ready",
            instance_id="aa0003", network=network_of("aa0003"),
            schema_version=2,
            lease=worktree.WorktreeLease(
                holder="h", acquired_at_utc=_stamp(NOW),
                renewed_at_utc=_stamp(NOW),
                expires_at_utc="2026-08-25 12:00:00 yesterday-ish", mode="held",
            ),
        )
        assert worktree._lease_is_expired(record, NOW) is False


# ===========================================================================
# Docker-enumeration boundaries
# ===========================================================================


class TestEnumerationBoundaries:
    def test_no_docker_at_all_surveys_an_empty_host(self, repo, docker):
        """A CIU workspace can legitimately be local-only; an empty survey
        destroys nothing, which is the honest answer."""
        docker.absent = True
        doc = survey(repo)
        assert doc["groups"] == [] and doc["counts"]["owned"] == 0

    def test_a_failed_docker_query_is_a_refusal_not_an_empty_result(
        self, repo, docker
    ):
        """A survey that silently under-reports would be the input to a
        destructive pass — and the group it failed to see is exactly the one
        whose absence would make a shared network look unused."""
        docker.fail(lambda a: a[0] == "ps", "Cannot connect to the Docker daemon")
        with pytest.raises(worktree.WorktreeError) as exc:
            survey(repo)
        assert "could not enumerate containers" in str(exc.value)
        assert "refusing rather than surveying" in str(exc.value)

    @pytest.mark.parametrize(
        "which,predicate,what",
        [
            ("volumes", lambda a: a[:2] == ["volume", "ls"], "volumes"),
            ("networks", lambda a: a[:2] == ["network", "ls"] and "--filter" not in a,
             "networks"),
        ],
    )
    def test_every_enumeration_refuses_on_failure(
        self, repo, docker, which, predicate, what
    ):
        docker.fail(predicate, "daemon error")
        with pytest.raises(worktree.WorktreeError, match=f"could not enumerate {what}"):
            survey(repo)

    def test_a_malformed_docker_row_is_refused(self, repo, docker, monkeypatch):
        def _bad(args, **kw):
            if args[0] == "ps":
                return _R(0, stdout="only\ttwo\n")
            return docker._dispatch(args)

        monkeypatch.setattr(worktree.procutil, "docker", _bad)
        with pytest.raises(worktree.WorktreeError, match="unparseable containers row"):
            survey(repo)

    def test_blank_enumeration_lines_are_ignored(self, repo, docker, monkeypatch):
        def _padded(args, **kw):
            result = docker._dispatch(args)
            if args[0] == "ps":
                return _R(0, stdout="\n   \n" + result.stdout)
            return result

        monkeypatch.setattr(worktree.procutil, "docker", _padded)
        assert survey(repo)["groups"] == []


# ===========================================================================
# O4 — capabilities, CLI surface, docs
# ===========================================================================


class TestCapabilitiesAndCli:
    def test_both_new_capability_identifiers_are_advertised(self):
        doc = worktree.capabilities_document()
        assert "worktree.reap.v1" in doc["capabilities"]
        assert "worktree.lease.v1" in doc["capabilities"]
        assert doc["capabilities"] == sorted(set(doc["capabilities"]))

    def test_cli_survey_exits_zero_and_emits_the_document(
        self, repo, docker, monkeypatch, capsys
    ):
        root = add_instance(
            repo, logical="cli", instance_id="bb0001",
            schema=2, lease=held(hours_from_now=-1),
        )
        deploy_instance(docker, root, "bb0001")
        monkeypatch.setattr(worktree, "_utc_now", lambda: NOW)

        code = cli._worktree(["reap", "--define-root", str(repo), "--json"])

        assert code == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["operation"] == "reap" and doc["status"] == "survey"
        assert doc["counts"]["lease-expired"] == 1
        assert docker.container_ids  # a survey removed nothing

    def test_cli_human_output_names_every_category_and_the_hint(
        self, repo, docker, monkeypatch, capsys
    ):
        root = add_instance(repo, logical="cli", instance_id="bb0002")
        write_record(
            root, logical="cli", branch="not-the-git-branch",
            instance_id="bb0002",
        )
        deploy_instance(docker, root, "bb0002")
        docker.container("h" * 32, "stranger-1", "stranger")
        monkeypatch.setattr(worktree, "_utc_now", lambda: NOW)

        code = cli._worktree(["reap", "--define-root", str(repo)])
        out = capsys.readouterr().out

        assert code == 0
        for category in worktree.REAP_CATEGORIES:
            assert category in out
        assert "FINDING [inconsistent-record]" in out
        assert "NEVER reaped" in out

    def test_cli_dry_run_prints_the_exact_commands(
        self, repo, docker, monkeypatch, capsys
    ):
        docker.container("d" * 32, "orphan-1", "orphan-project",
                         instance="bb0003", repo_root=str(repo))
        monkeypatch.setattr(worktree, "_utc_now", lambda: NOW)

        code = cli._worktree(
            ["reap", "-y", "--dry-run", "--define-root", str(repo)]
        )
        out = capsys.readouterr().out

        assert code == 0
        assert "would reap orphan-project (orphaned):" in out
        assert "docker rm -f " + "d" * 32 in out
        assert docker.container_ids == {"d" * 32}

    def test_cli_reap_reports_removals_and_exits_zero(
        self, repo, docker, monkeypatch, capsys
    ):
        docker.container("e" * 32, "orphan-1", "orphan-project",
                         instance="bb0004", repo_root=str(repo))
        monkeypatch.setattr(worktree, "_utc_now", lambda: NOW)

        code = cli._worktree(["reap", "-y", "--define-root", str(repo)])
        out = capsys.readouterr().out

        assert code == 0
        assert "reaped: orphan-project" in out
        assert "removed 1 container(s)" in out
        assert docker.container_ids == set()

    def test_cli_exits_one_on_a_partial_pass_in_both_output_modes(
        self, repo, docker, monkeypatch, capsys
    ):
        for tag in ("aa", "bb"):
            docker.container(tag * 16, f"{tag}-1", f"{tag}-project",
                             instance="bb0005", repo_root=str(repo))
        docker.fail(lambda a: a[:2] == ["rm", "-f"] and "aa" * 16 in a, "nope")
        monkeypatch.setattr(worktree, "_utc_now", lambda: NOW)

        human = cli._worktree(["reap", "-y", "--define-root", str(repo)])
        text = capsys.readouterr().out
        assert human == 1
        assert "FAILED: aa-project" in text

        docker.fail(lambda a: a[:2] == ["rm", "-f"], "nope")
        machine = cli._worktree(["reap", "-y", "--json", "--define-root", str(repo)])
        doc = json.loads(capsys.readouterr().out)
        assert machine == 1
        assert doc["status"] == "partial"

    def test_cli_refuses_a_protected_category_with_exit_two(
        self, repo, docker, monkeypatch, capsys
    ):
        docker.container("f" * 32, "orphan-1", "orphan-project",
                         instance="bb0006", repo_root=str(repo))
        monkeypatch.setattr(worktree, "_utc_now", lambda: NOW)

        code = cli._worktree(
            ["reap", "-y", "--category", "ambiguous", "--define-root", str(repo)]
        )

        assert code == 2
        assert "never acted on" in capsys.readouterr().err
        assert docker.container_ids == {"f" * 32}

    def test_the_verb_is_documented_in_usage_and_verb_help(self):
        assert "worktree reap" in cli._USAGE
        assert "ciu worktree reap" in cli._VERB_HELP["worktree"]
        assert "--category" in cli._VERB_HELP["worktree"]


class TestSpecAndDocs:
    ROOT = Path(__file__).resolve().parents[2]

    def test_spec_documents_the_closed_category_vocabulary(self):
        spec = (self.ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        assert "### S16.10" in spec
        for category in worktree.REAP_CATEGORIES:
            assert f"`{category}`" in spec
        assert "worktree.reap.v1" in spec

    def test_readme_and_consumers_carry_the_verb(self):
        # The worktree verb list lives in the repo-root README (docs/README.md
        # is a pure document index) — see the ciu-P27 LOG's scope note.
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        consumers = (self.ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
        assert "|reap`" in readme.replace(" ", "")
        assert "worktree reap" in consumers
        assert "unattributable" in consumers
        assert "worktree.reap.v1" in consumers

    def test_the_backlog_marks_ciu_25_fixed_naming_both_packages(self):
        backlog = (
            self.ROOT / "KNOWN_ISSUES_TODO_BACKLOG.md"
        ).read_text(encoding="utf-8")
        entry = backlog.split("## CIU-25")[1].split("## CIU-26")[0]
        assert "ciu-P26" in entry and "ciu-P27" in entry
        assert "FIXED" in backlog.split("| CIU-25 |")[1].split("|")[2]
