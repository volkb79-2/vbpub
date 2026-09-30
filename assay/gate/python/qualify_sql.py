#!/usr/bin/env python3
"""Self-contained real-PostgreSQL qualification of assay's SQL mutation adapter (A-480).

assay's SQL/DDL adapter (``src/assay/adapters/sql.py``) generates mutants that
only mean something if a real catalog agrees. This harness is the re-runnable
evidence: it owns its schema
(``tests/fixtures/mutation/sql/qualification/01-schema.sql``, tags ``[Knn]``
naming the rows of ``gate/python/fixtures/sql/matrix.json``), its probes
(``fixtures/sql/tests/K*.sql``, one per killed row) and its PostgreSQL image
(:data:`IMAGE`, pinned by digest, never pulled), and depends on no consumer
checkout.

**Flow.** (1) :func:`check_sites` -- the sites the shipped adapter finds in the
schema, mapped to the ``[Knn]`` tags by the tag rule (a line's tags name its
sites left to right by ascending ``start_byte``), equal the matrix rows.
(2) Host checks, one throwaway container, the fixture files copied in.
(3) Baseline: the unmutated schema applies, dumps and passes every probe; two
dumps taken WITHOUT ``--restrict-key`` differ (O5). (4) Every matrix row: the
mutant is applied to a fresh database, and the bucket is DERIVED
(:func:`derive_bucket`) from the apply exit code, the dump against the
baseline dump and the ``ASSAY_SQL_FAILED=`` ids the probes name; it must equal
the row's ``expected``. (5) Two controls that must be ``crashed``: the O4
residue premise (re-applying a mutant on a database that already carries the
schema fails loudly, because the schema is not idempotent) and M11 (a naive
string widen of an integer ``IN`` list). (6) The witness: a REAL ``assay run``
over a disposable repository whose lane executes the same probes, compared
row by row with the matrix (:func:`check_witness_verdict`) and then with the
frozen normalized verdict. (7) No database is left behind.

**Exit codes.** 0 with stdout exactly ``ASSAY_SQL_QUALIFIED=1``; 1 a
:class:`QualificationError`; 3 an :class:`InconclusiveError` with stderr
``ASSAY_SQL_INCONCLUSIVE=<reason>`` (docker unavailable, image absent, host
busy, readiness failsafe, a command timeout, a container name in use, an
incomplete witness): visible and rerunnable, never skipped and never green.

**Environment.** The container runs ``--network none`` in the cgroup slice the
caller passes, one at a time, named ``run-gate-assay-sql-<pid>-<epoch>`` so a
peer's ``docker ps`` sees it, and is removed by its exact name. Before
``docker run`` the harness looks once at ``docker ps``; any ``run-gate-*`` name
means ``host busy`` (exit 3) -- it never polls or waits; ``--allow-shared-host``
(CD50) tolerates other projects' ``run-gate-*`` containers (printing
``ASSAY_SQL_SHARED_HOST=`` on stderr) but still refuses another ``run-gate-assay-sql-*``. The witness lane strips
``DOCKER_HOST`` (its ``env`` is ``PATH`` only) and relies on
``/var/run/docker.sock`` reaching the same daemon as this process's docker CLI.
The witness lane imports :mod:`assay` from this checkout's ``src/``: it
qualifies THIS tree's adapter, so there is no separate wheel boundary.
Requires Python >= 3.11 (host ``python3``, run with ``-I``).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
# Unconditional: a duplicate sys.path entry is harmless, and a guard here
# would be an import-order-dependent branch this module's own test suite
# cannot exercise both sides of (conftest.py already puts src/ on sys.path
# before this module is ever loaded under pytest) -- exactly the
# unreachable-arc trap A-124/A-131 name as a defect, not a decoration.
sys.path.insert(0, str(_SRC_ROOT))

from assay.adapters.sql import SqlAdapter  # noqa: E402
from assay.config import LANE_SCHEMA_VERSION  # noqa: E402
from assay.mutation import MutationSite  # noqa: E402
from assay.verdict import VERDICT_SCHEMA_VERSION  # noqa: E402,F401  (pinned by the witness-schema test)

#: The locally present PostgreSQL 18.6 image, by digest. Never pulled (A-480).
IMAGE = "postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2"
#: Proven against real pg_dump 18 (a hyphenated key is rejected as "invalid
#: restrict key").
RESTRICT_KEY = "assayfixedkey0000000000000000000000000000000000000000000000000000"

SCHEMA_PATH = _PROJECT_ROOT / "tests" / "fixtures" / "mutation" / "sql" / "qualification" / "01-schema.sql"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "sql"
#: sha256 pins of the two fixture texts the matrix is written against (T0).
SCHEMA_SHA256 = "b7b07e973e988202dbc34cb3ff69415d6b79adc801c3b3566595cb3a63703af1"
PROBES_SHA256 = "b5c4eb283dc01ec3d8b15f2441363d5bea9312a49e025bd9eaf318086fd41e10"

ALL_OPERATORS: tuple[str, ...] = (
    "sql:drop-check",
    "sql:drop-unique",
    "sql:drop-not-null",
    "sql:drop-foreign-key",
    "sql:weaken-delete-action",
    "sql:drop-trigger",
    "sql:widen-check-in",
)
BUCKETS = ("killed", "survived", "equivalent", "crashed", "hung", "budget_exceeded")

_CONTAINER_NAME_RE = re.compile(r"run-gate-assay-sql-[0-9]+-[0-9]+")
_TAG_RE = re.compile(r"\[(K[0-9]{2})\]")
_SIGNAL_RE = re.compile(
    r"schema test command failed \(exit [1-9][0-9]*\): ASSAY_SQL_FAILED=(none|K[0-9]{2}(,K[0-9]{2})*)"
)
_DBNAME = "qual"
_SCHEMA_NAME = "01-schema.sql"
_WITNESS_LANE = "sql_qualification"
_WITNESS_DB = "witness"
_EXPECTED_DATABASES = "postgres,template0,template1"
_READY_ATTEMPTS = 300
_WITNESS_TIMEOUT_S = 3900

#: Test seam for the readiness loop: the only clock this module reads.
_sleep = time.sleep


class QualificationError(RuntimeError):
    """A frozen qualification premise or an independent comparison failed."""


class InconclusiveError(RuntimeError):
    """The environment could not answer: rerun later (exit 3)."""


# --- the one subprocess boundary --------------------------------------------


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input: str | None = None,  # noqa: A002 - matches precedent's own name
    timeout: int = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,  # hang failsafe only
        check=False,
    )
    if check and proc.returncode:
        raise QualificationError(
            f"command failed ({proc.returncode}): {list(argv)!r}\n"
            f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _env_with(overrides: Mapping[str, str]) -> dict[str, str]:
    return {**os.environ, **overrides}


def _git_commit(repo: Path, message: str, *, env: Mapping[str, str]) -> None:
    _run(["git", "-C", str(repo), "commit", "-q", "-m", message], env=_env_with(env), check=True)


# --- the fixtures: hashes, matrix, sites --------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_fixture_hashes(schema_path: Path, fixture_root: Path) -> None:
    """T0: the schema and the concatenated probes (in id order) are the bytes
    the matrix was measured against."""
    schema = _sha256(schema_path.read_bytes())
    if schema != SCHEMA_SHA256:
        raise QualificationError(f"schema sha256 {schema} != pinned {SCHEMA_SHA256}")
    probes = sorted((fixture_root / "tests").glob("K*.sql"), key=lambda path: path.name)
    joined = _sha256(b"".join(path.read_bytes() for path in probes))
    if joined != PROBES_SHA256:
        raise QualificationError(f"probes sha256 {joined} != pinned {PROBES_SHA256}")


def load_matrix(fixture_root: Path) -> list[dict[str, Any]]:
    return json.loads((fixture_root / "matrix.json").read_text(encoding="utf-8"))


def discover_sites(
    text: str, *, operators: tuple[str, ...] = ALL_OPERATORS, limit: int = 500
) -> tuple[MutationSite, ...]:
    """Every site the SHIPPED :class:`SqlAdapter` finds in *text*, over ALL lines."""
    lines = set(range(1, text.count("\n") + 3))
    result = SqlAdapter().generate_mutation_sites(text, lines, operators=operators, limit=limit)
    assert result != "UNSUPPORTED"  # SQL never returns the marker (A-242)
    return result


def check_sites(schema_text: str, matrix: Sequence[Mapping[str, Any]]) -> dict[str, MutationSite]:
    """Map the discovered sites to the ``[Knn]`` tags (a line's tags name its
    sites left to right by ascending ``start_byte``) and require every matrix
    row's ``(line, operator)`` to agree. Returns ``{id: site}``; every
    disagreement is collected and named in one :class:`QualificationError`."""
    errors: list[str] = []
    by_line: dict[int, list[MutationSite]] = {}
    for site in discover_sites(schema_text):
        by_line.setdefault(site.lineno, []).append(site)
    tags_by_line = {
        number: _TAG_RE.findall(line) for number, line in enumerate(schema_text.split("\n"), start=1)
    }
    found: dict[str, MutationSite] = {}
    for number in sorted(set(by_line) | {n for n, tags in tags_by_line.items() if tags}):
        sites = sorted(by_line.get(number, []), key=lambda site: site.start_byte)
        tags = tags_by_line.get(number, [])
        if len(sites) != len(tags):
            errors.append(f"line {number}: {len(sites)} site(s) but tags {tags or 'none (untagged line)'}")
            continue
        found.update(zip(tags, sites))
    row_ids = [row["id"] for row in matrix]
    for row in matrix:
        site = found.get(row["id"])
        if site is None:
            errors.append(f"{row['id']}: expected ({row['line']}, {row['operator']}) but no site carries the tag")
        elif (site.lineno, site.operator) != (row["line"], row["operator"]):
            errors.append(
                f"{row['id']}: expected ({row['line']}, {row['operator']}) but the tagged site is "
                f"({site.lineno}, {site.operator})"
            )
    for tag in sorted(set(found) - set(row_ids)):
        errors.append(f"{tag}: tagged in the schema but absent from the matrix")
    if errors:
        raise QualificationError("site check failed: " + "; ".join(errors))
    return found


# --- derivation ---------------------------------------------------------------


def parse_failed_ids(signal_text: str | None) -> frozenset[str]:
    """The probe ids a kill signal names (``None``: no signal, no ids)."""
    if signal_text is None:
        return frozenset()
    body = signal_text[:-1] if signal_text.endswith("\n") else signal_text
    match = _SIGNAL_RE.fullmatch(body)
    if match is None:
        raise QualificationError(f"malformed kill signal: {signal_text!r}")
    if match.group(1) == "none":
        return frozenset()
    return frozenset(match.group(1).split(","))


def derive_bucket(
    row_id: str,
    *,
    exit_code: int,
    dump: str | None,
    baseline_dump: str | None,
    failed_ids: frozenset[str],
) -> str:
    """The bucket a run falls in, first match (mirrors the judge's
    ``_classify_mutant_result_with_equivalence``)."""
    if dump is None:
        return "crashed"
    if dump == baseline_dump:
        return "equivalent"
    if exit_code == 0:
        return "survived"
    if row_id in failed_ids:
        return "killed"
    raise QualificationError(f"{row_id}: test failed without naming {row_id}")


def _require_bucket(row_id: str, bucket: str, expected: str) -> None:
    if bucket != expected:
        raise QualificationError(f"{row_id}: derived {bucket}, expected {expected}")


# --- the throwaway container --------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    dump: str | None
    signal: str | None


class ThrowawayPostgres:
    """One pinned PostgreSQL container, ``--network none``, named by the
    caller, always removed by its exact name (never ``--rm``: the removal
    must be ordered after the ``df`` receipt and be signal-safe)."""

    def __init__(self, name: str, cgroup_parent: str, fixture_root: Path, *, allow_shared_host: bool = False) -> None:
        self.name = name
        self.allow_shared_host = allow_shared_host
        self.cgroup_parent = cgroup_parent
        self.fixture_root = fixture_root
        self._owned = False

    def __enter__(self) -> "ThrowawayPostgres":
        try:
            self._start()
        except BaseException:
            self._remove()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._remove()

    # -- lifecycle --

    def _check_host(self) -> None:
        if shutil.which("docker") is None or _run(["docker", "version"], check=False, timeout=60).returncode != 0:
            raise InconclusiveError("docker unavailable")
        if _run(["docker", "image", "inspect", IMAGE], check=False, timeout=60).returncode != 0:
            raise InconclusiveError(f"image absent: docker pull {IMAGE}")
        listing = _run(["docker", "ps", "--no-trunc", "--format", "{{.Names}}"], timeout=60).stdout
        busy = [name for name in listing.splitlines() if name.startswith("run-gate-")]
        if not self.allow_shared_host:
            if busy:
                raise InconclusiveError(f"host busy — rerun: {','.join(busy)}")
            return
        sql_busy = [name for name in busy if name.startswith("run-gate-assay-sql-")]
        if sql_busy:
            raise InconclusiveError(f"host busy — rerun: {','.join(sql_busy)}")
        if busy:
            print(f"ASSAY_SQL_SHARED_HOST={','.join(busy)}", file=sys.stderr)

    def _docker_run_argv(self) -> list[str]:
        return [
            "docker", "run", "-d", "--pull=never", "--network", "none",
            "--name", self.name,
            f"--cgroup-parent={self.cgroup_parent}",
            "--cpus", "1", "--memory", "512m", "--memory-swap", "512m", "--pids-limit", "256",
            "--mount", "type=tmpfs,destination=/var/lib/postgresql,tmpfs-size=268435456",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            IMAGE,
            "postgres", "-c", "max_wal_size=64MB", "-c", "min_wal_size=32MB",
        ]  # fmt: skip

    def _start(self) -> None:
        self._check_host()
        self._owned = True  # from here a removal is due, even if `docker run` itself fails
        proc = _run(self._docker_run_argv(), check=False)
        if proc.returncode != 0:
            if "Conflict" in proc.stderr:
                self._owned = False  # the name belongs to another process: never touch it
                raise InconclusiveError(f"container name in use: {self.name}")
            raise QualificationError(f"docker run failed ({proc.returncode}): {proc.stderr[-2000:]}")
        self._wait_ready()
        for source, target in (
            (self.fixture_root / "schema-gate.sh", "/schema-gate.sh"),
            (self.fixture_root / "run-assertions.sh", "/run-assertions.sh"),
            (self.fixture_root / "tests", "/tests"),
        ):
            _run(["docker", "cp", str(source), f"{self.name}:{target}"])

    def _wait_ready(self, attempts: int = _READY_ATTEMPTS) -> None:
        for _ in range(attempts):
            proc = _run(
                ["docker", "exec", self.name, "psql", "-h", "127.0.0.1", "-U", "postgres", "-tAc", "SELECT 1"],
                check=False,
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip() == "1":
                return
            _sleep(1)
        raise InconclusiveError(f"readiness failsafe: {self.name} never became ready after {attempts} attempts")

    def _remove(self) -> None:
        """(1) ignore SIGTERM, (2) log the tmpfs use, (3) remove by exact name
        (the last command), (4) restore the saved handler."""
        if not self._owned:
            return
        self._owned = False
        saved = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            try:
                df = _run(["docker", "exec", self.name, "df", "-Pk", "/var/lib/postgresql"], check=False, timeout=30)
                print(f"ASSAY_SQL_DF={df.stdout.strip()!r}", file=sys.stderr)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"ASSAY_SQL_DF_FAILED={exc!r}", file=sys.stderr)
            try:
                _run(["docker", "rm", "-f", "-v", self.name], check=False, timeout=120)
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"ASSAY_SQL_RM_FAILED={exc!r}", file=sys.stderr)
        finally:
            signal.signal(signal.SIGTERM, saved)

    # -- operations --

    def exec(self, argv: Sequence[str], *, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return _run(["docker", "exec", self.name, *argv], check=check, timeout=timeout)

    def replace_corpus(self, host_dir: Path) -> None:
        """Swap the container's ``/corpus`` for *host_dir*'s contents: always a
        full copy, so a scenario never sees a stale file."""
        self.exec(["rm", "-rf", "/corpus", "/corpus_new"])
        _run(["docker", "cp", str(host_dir), f"{self.name}:/corpus_new"])
        self.exec(["mv", "/corpus_new", "/corpus"])

    def create_database(self, name: str = _DBNAME) -> None:
        self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", f"DROP DATABASE IF EXISTS {name};"])
        self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", f"CREATE DATABASE {name};"])

    def drop_database(self, name: str = _DBNAME) -> None:
        self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", f"DROP DATABASE {name};"])

    def _read_if_present(self, path: str) -> str | None:
        if self.exec(["test", "-f", path], check=False).returncode != 0:
            return None
        return self.exec(["cat", path]).stdout

    def run_gate(self, dbname: str = _DBNAME) -> RunResult:
        """One ``apply && dump && test`` run of the real ``schema-gate.sh``."""
        self.exec(["rm", "-f", "/dump.sql", "/kill.txt"])
        proc = self.exec(
            [
                "env",
                "SCHEMA_GATE_INIT_SCRIPTS_DIR=/corpus",
                f"SCHEMA_GATE_DBNAME={dbname}",
                "SCHEMA_GATE_DUMP_PATH=/dump.sql",
                "SCHEMA_GATE_KILL_SIGNAL_PATH=/kill.txt",
                f"SCHEMA_GATE_RESTRICT_KEY={RESTRICT_KEY}",
                "SCHEMA_GATE_TEST_CMD=sh /run-assertions.sh",
                "SCHEMA_GATE_ASSERT_DIR=/tests",
                "sh",
                "/schema-gate.sh",
            ],
            check=False,
        )
        return RunResult(proc.returncode, self._read_if_present("/dump.sql"), self._read_if_present("/kill.txt"))

    def pg_dump(self, dbname: str = _DBNAME) -> str:
        return self.exec(["pg_dump", "--schema-only", "--no-owner", "-U", "postgres", "-d", dbname]).stdout

    def query_one(self, sql: str, dbname: str = "postgres") -> str:
        return self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-tAX", "-U", "postgres", "-d", dbname, "-c", sql]).stdout.strip()


# --- the witness: a REAL `assay run` over a disposable repository --------------

_WITNESS_LANE_TEMPLATE = """\
schema_version = {lane_schema}

[lanes.{lane}]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["sh", "tools/witness-gate.sh"]
env = {{ PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" }}
env_passthrough = []
budget = "60m"
allow_argv_append = false

[lanes.{lane}.isolation]
snapshot_selection = "repository"

[lanes.{lane}.judge]
language = "sql"
source_roots = ["db/schema"]
base = "@BASE_OID@"

[lanes.{lane}.judge.mutation]
jobs = 1
max_mutants = 24
operators = {operators!r}
equivalence_artifact = ".assay/schema-dump.sql"
kill_signal_artifact = ".assay/kill-signal.txt"
"""


def _assay_argv(python: str, *args: str) -> list[str]:
    bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); from assay.cli import main; sys.exit(main(sys.argv[2:]))"
    return [python, "-c", bootstrap, str(_SRC_ROOT), *args]


def _witness_wrapper_script(*, container_name: str, dbname: str, restrict_key: str) -> str:
    """The host-side wrapper the disposable lane's ``argv`` runs.

    assay executes a lane's ``argv`` as a HOST subprocess inside the
    materialized snapshot, so this thin wrapper bridges it to the isolated
    container: it copies the snapshot's (possibly mutated) ``db/schema`` in as
    ``/corpus`` and ``db/tests`` in as ``/tests`` (copied to a fresh name and
    moved: a plain ``docker cp db/tests NAME:/tests`` would nest as
    ``/tests/tests`` and silently run the fixture-root probes), runs the SAME
    ``schema-gate.sh`` unchanged, then relays the two declared artifacts back
    onto the snapshot's own filesystem."""
    return (
        "set -u\n"
        "mkdir -p .assay\n"
        f"docker exec {container_name} sh -c 'rm -rf /corpus /corpus_new /tests /tests_new'\n"
        f"docker cp db/schema {container_name}:/corpus_new\n"
        f"docker exec {container_name} mv /corpus_new /corpus\n"
        f"docker cp db/tests {container_name}:/tests_new\n"
        f"docker exec {container_name} mv /tests_new /tests\n"
        # DROP/CREATE DATABASE cannot share one simple-query message (it runs
        # in an implicit transaction block), hence two separate `-c` calls.
        f"docker exec {container_name} psql -v ON_ERROR_STOP=1 -U postgres -c "
        f"'DROP DATABASE IF EXISTS {dbname};'\n"
        f"docker exec {container_name} psql -v ON_ERROR_STOP=1 -U postgres -c "
        f"'CREATE DATABASE {dbname};'\n"
        f"docker exec {container_name} rm -f /dump.sql /kill.txt\n"
        f"docker exec -e SCHEMA_GATE_INIT_SCRIPTS_DIR=/corpus -e SCHEMA_GATE_DBNAME={dbname} "
        f"-e SCHEMA_GATE_DUMP_PATH=/dump.sql -e SCHEMA_GATE_KILL_SIGNAL_PATH=/kill.txt "
        f"-e SCHEMA_GATE_RESTRICT_KEY={restrict_key} -e SCHEMA_GATE_ASSERT_DIR=/tests "
        f"-e 'SCHEMA_GATE_TEST_CMD=sh /run-assertions.sh' "
        f"{container_name} sh /schema-gate.sh\n"
        "rc=$?\n"
        f"docker cp {container_name}:/dump.sql .assay/schema-dump.sql 2>/dev/null || true\n"
        f"docker cp {container_name}:/kill.txt .assay/kill-signal.txt 2>/dev/null || true\n"
        f"docker exec {container_name} psql -v ON_ERROR_STOP=1 -U postgres -c 'DROP DATABASE {dbname};'\n"
        "exit $rc\n"
    )


def capture_witness(
    *, container: ThrowawayPostgres, schema_bytes: bytes, fixture_root: Path, scratch: Path, python: str = sys.executable
) -> dict[str, Any]:
    """Drive a REAL ``assay run`` over a SQL R2 lane against the qualification
    schema inside a disposable git repository. Returns the verdict document,
    UNNORMALIZED, with the disposable commits."""
    scratch.mkdir(parents=True)
    repo = scratch / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    identity = {"GIT_AUTHOR_NAME": "Assay SQL qualification", "GIT_AUTHOR_EMAIL": "assay-sql@example.invalid"}
    identity_env: dict[str, str] = {
        **identity,
        "GIT_COMMITTER_NAME": identity["GIT_AUTHOR_NAME"],
        "GIT_COMMITTER_EMAIL": identity["GIT_AUTHOR_EMAIL"],
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }
    # Both declared artifacts live under `.assay/`, which must be gitignored or
    # the judge treats the snapshot as left dirty (NO_MEASUREMENT/DIRTY_TREE).
    (repo / ".gitignore").write_text(".assay/\n", encoding="utf-8")
    shutil.copytree(fixture_root / "tests", repo / "db" / "tests")
    _run(["git", "add", "-A"], cwd=repo)
    _git_commit(repo, "base: probes and ignore rules", env=identity_env)
    base_oid = _git(repo, "rev-parse", "HEAD")

    (repo / "db" / "schema").mkdir()
    (repo / "db" / "schema" / _SCHEMA_NAME).write_bytes(schema_bytes)
    (repo / "tools").mkdir()
    (repo / "tools" / "witness-gate.sh").write_text(
        _witness_wrapper_script(container_name=container.name, dbname=_WITNESS_DB, restrict_key=RESTRICT_KEY),
        encoding="utf-8",
    )
    lane_toml = _WITNESS_LANE_TEMPLATE.format(
        lane_schema=LANE_SCHEMA_VERSION, lane=_WITNESS_LANE, operators=list(ALL_OPERATORS)
    ).replace("@BASE_OID@", base_oid)
    (repo / "assay.toml").write_text(lane_toml, encoding="utf-8")
    _run(["git", "add", "-A"], cwd=repo)
    _git_commit(repo, "head: the qualification schema and the witness lane", env=identity_env)
    head_oid = _git(repo, "rev-parse", "HEAD")

    artifact_path = scratch / "verdict.json"
    _run(
        _assay_argv(
            python, "run", _WITNESS_LANE, "--file", str(repo / "assay.toml"), "--verdict-json", str(artifact_path)
        ),
        cwd=repo,
        check=False,
        timeout=_WITNESS_TIMEOUT_S,
    )
    if not artifact_path.is_file():
        raise QualificationError("assay run wrote no verdict artifact")
    verdict = json.loads(artifact_path.read_text(encoding="utf-8"))
    _require_witness_commit_matches(verdict, head_oid)
    return {"verdict": verdict, "base_oid": base_oid, "head_oid": head_oid}


def _require_witness_commit_matches(verdict: Mapping[str, Any], head_oid: str) -> None:
    if verdict.get("commit") != head_oid:
        raise QualificationError("the witness artifact's commit is not the disposable HEAD")


def _r2_claim(verdict: Mapping[str, Any]) -> Mapping[str, Any]:
    claims = [claim for claim in verdict.get("claims", []) if claim.get("rigor") == "R2"]
    if len(claims) != 1:
        raise QualificationError(f"expected exactly one R2 claim, found {len(claims)}")
    return claims[0]


def cross_check(verdict: Mapping[str, Any], matrix: Sequence[Mapping[str, Any]]) -> None:
    """Row by row (never counts): every matrix row sits in exactly one bucket
    list of the R2 claim, equal to its ``expected``; nothing is unmatched; every
    killed entry's kill signal names its own row id."""
    mutation = _r2_claim(verdict)["mutation"]
    by_key = {(row["line"], row["operator"]): row for row in matrix}
    placed: dict[str, list[str]] = {row["id"]: [] for row in matrix}
    errors: list[str] = []
    for bucket in BUCKETS:
        for entry in mutation.get(bucket, []):
            row = by_key.get((entry.get("lineno"), entry.get("operator")))
            if row is None:
                errors.append(f"unmatched {bucket} entry at ({entry.get('lineno')}, {entry.get('operator')})")
                continue
            placed[row["id"]].append(bucket)
            if bucket == "killed" and row["id"] not in parse_failed_ids(entry.get("kill_signal")):
                errors.append(f"{row['id']}: killed but its kill signal does not name {row['id']}")
    for row in matrix:
        buckets = placed[row["id"]]
        if buckets != [row["expected"]]:
            errors.append(f"{row['id']}: in {buckets or 'no bucket'}, expected [{row['expected']!r}]")
    if errors:
        raise QualificationError("witness cross-check failed: " + "; ".join(errors))


def check_witness_verdict(verdict: Mapping[str, Any], matrix: Sequence[Mapping[str, Any]]) -> None:
    """(1) Completeness first: an incomplete witness is inconclusive (exit 3),
    never a product failure. (2) Only then the verdict shape. (3) Then the
    row-by-row cross-check."""
    outcome, reason = verdict.get("outcome"), verdict.get("reason_code")
    if outcome == "BUDGET_EXCEEDED":
        raise InconclusiveError(f"witness incomplete: {outcome}/{reason}")
    for claim in verdict.get("claims", []):
        if claim.get("reason_code") in ("LANE_TIMEOUT", "CANDIDATE_HUNG"):
            raise InconclusiveError(f"witness incomplete: {outcome}/{claim['reason_code']}")
    mutation = _r2_claim(verdict).get("mutation", {})
    if mutation.get("hung") or mutation.get("budget_exceeded"):
        raise InconclusiveError(f"witness incomplete: {outcome}/{reason}")
    r0 = [claim for claim in verdict.get("claims", []) if claim.get("rigor") == "R0"]
    if len(r0) != 1 or r0[0].get("status") != "PASS":
        raise QualificationError("the witness R0 claim is not a single PASS")
    if outcome != "FAIL" or reason != "MUTANTS_SURVIVED":
        raise QualificationError(f"the witness outcome is {outcome}/{reason}, expected FAIL/MUTANTS_SURVIVED")
    cross_check(verdict, matrix)


def normalize_verdict(document: Mapping[str, Any], *, assay_version: str, head_oid: str, base_oid: str) -> dict[str, Any]:
    """Validate the fields whose real value is known out of band, then replace
    them with placeholders so the WHOLE remaining document compares with ``==``."""
    normalized = copy.deepcopy(dict(document))
    if normalized.get("assay_version") != assay_version:
        raise QualificationError(
            f"artifact assay_version {normalized.get('assay_version')!r} != installed {assay_version!r}"
        )
    if normalized.get("commit") != head_oid:
        raise QualificationError("artifact commit is not the disposable HEAD")
    resolved_base = normalized.get("judgment", {}).get("resolved", {}).get("base")
    if resolved_base != base_oid:
        raise QualificationError(f"artifact judgment.resolved.base {resolved_base!r} != seeded base {base_oid!r}")
    for field in ("started", "ended"):
        if not isinstance(normalized.get(field), str) or not normalized[field]:
            raise QualificationError(f"artifact {field!r} is not a nonempty timestamp")
    normalized["assay_version"] = "@ASSAY_VERSION@"
    normalized["commit"] = "@HEAD_OID@"
    normalized["started"] = "@STARTED@"
    normalized["ended"] = "@ENDED@"
    normalized["judgment"]["resolved"]["base"] = "@BASE_OID@"
    # The auto budget is derived from this run's measured baseline wall time:
    # real, but not a stable witness value.
    normalized.get("judgment", {}).get("r2", {}).pop("budget_per_candidate_derived_s", None)
    return normalized


def compare_with_witness(
    actual: Mapping[str, Any], witness_path: Path, *, assay_version: str, head_oid: str, base_oid: str
) -> None:
    normalized = normalize_verdict(actual, assay_version=assay_version, head_oid=head_oid, base_oid=base_oid)
    expected = json.loads(witness_path.read_text(encoding="utf-8"))
    if normalized != expected:
        raise QualificationError(f"the normalized verdict differs from the frozen witness at {witness_path}")


# --- the flow -------------------------------------------------------------------


def _write_corpus(schema: bytes, directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / _SCHEMA_NAME).write_bytes(schema)
    return directory


def _run_mutant(container: ThrowawayPostgres, schema: bytes, directory: Path) -> RunResult:
    """One scenario on its own database: create, apply, drop."""
    container.replace_corpus(_write_corpus(schema, directory))
    container.create_database()
    try:
        return container.run_gate()
    finally:
        container.drop_database()


def _residue_control(container: ThrowawayPostgres, schema: bytes, mutant: bytes, scratch: Path, baseline_dump: str) -> None:
    """O4-residue: on ONE database the unmutated apply succeeds, then the K01
    mutant fails loudly with no dump (the non-idempotent schema refuses to be
    applied twice), so the old false-survival premise cannot occur."""
    container.create_database()
    try:
        container.replace_corpus(_write_corpus(schema, scratch / "residue-base"))
        first = container.run_gate()
        if first.exit_code != 0:
            raise QualificationError(f"o4-residue: the unmutated apply exited {first.exit_code}")
        container.replace_corpus(_write_corpus(mutant, scratch / "residue-mutant"))
        second = container.run_gate()
    finally:
        container.drop_database()
    bucket = derive_bucket("o4-residue", exit_code=second.exit_code, dump=second.dump, baseline_dump=baseline_dump, failed_ids=frozenset())
    if second.exit_code == 0 or bucket != "crashed":
        raise QualificationError(f"o4-residue: the re-applied mutant exited {second.exit_code} ({bucket}), expected a crash")
    print(f"ASSAY_SQL_CONTROL=o4-residue:{bucket}", file=sys.stderr)


def _assay_version(python: str = sys.executable) -> str:
    proc = _run(_assay_argv(python, "--version"), timeout=60)
    return proc.stdout.strip().removeprefix("assay ")


def run_qualification(
    *, container: ThrowawayPostgres, scratch: Path, fixture_root: Path, witness_out: Path | None
) -> None:
    schema_bytes = SCHEMA_PATH.read_bytes()
    matrix = load_matrix(fixture_root)
    sites = check_sites(schema_bytes.decode("utf-8"), matrix)
    scratch.mkdir(parents=True)
    with container:
        # (3) baseline + O5
        container.replace_corpus(_write_corpus(schema_bytes, scratch / "baseline"))
        container.create_database()
        baseline = container.run_gate()
        if baseline.exit_code != 0 or baseline.dump is None or baseline.signal is not None:
            raise QualificationError(
                f"the unmutated baseline did not apply and pass (exit {baseline.exit_code}, "
                f"dump {'present' if baseline.dump is not None else 'absent'}, signal {baseline.signal!r})"
            )
        if container.pg_dump() == container.pg_dump():
            raise QualificationError("o5: two pg_dump runs without --restrict-key were identical")
        print("ASSAY_SQL_CONTROL=o5:differs", file=sys.stderr)
        container.drop_database()
        # (4) every matrix row
        for row in matrix:
            site = sites[row["id"]]
            result = _run_mutant(container, site.apply(schema_bytes), scratch / row["id"])
            bucket = derive_bucket(
                row["id"],
                exit_code=result.exit_code,
                dump=result.dump,
                baseline_dump=baseline.dump,
                failed_ids=parse_failed_ids(result.signal),
            )
            _require_bucket(row["id"], bucket, row["expected"])
            print(f"ASSAY_SQL_ROW={row['id']}:{bucket}", file=sys.stderr)
        # (5) controls
        _residue_control(container, schema_bytes, sites["K01"].apply(schema_bytes), scratch, baseline.dump)
        k24 = sites["K24"]
        invalid = MutationSite(
            start_byte=k24.start_byte,
            end_byte=k24.end_byte,
            replacement=b", '__assay_widened__')",
            lineno=k24.lineno,
            operator=k24.operator,
            description="HAND-CONSTRUCTED INVALID CONTROL (M11): naive string widen of an integer IN-list",
        )
        result = _run_mutant(container, invalid.apply(schema_bytes), scratch / "m11")
        bucket = derive_bucket("m11", exit_code=result.exit_code, dump=result.dump, baseline_dump=baseline.dump, failed_ids=frozenset())
        _require_bucket("m11", bucket, "crashed")
        print(f"ASSAY_SQL_CONTROL=m11:{bucket}", file=sys.stderr)
        # (6) the witness
        witness = capture_witness(container=container, schema_bytes=schema_bytes, fixture_root=fixture_root, scratch=scratch / "witness")
        check_witness_verdict(witness["verdict"], matrix)
        version = _assay_version()
        if witness_out is not None:
            normalized = normalize_verdict(
                witness["verdict"], assay_version=version, head_oid=witness["head_oid"], base_oid=witness["base_oid"]
            )
            witness_out.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            compare_with_witness(
                witness["verdict"],
                fixture_root / "expected" / "sql-r2-witness.json",
                assay_version=version,
                head_oid=witness["head_oid"],
                base_oid=witness["base_oid"],
            )
        # (7) nothing left behind
        databases = container.query_one("SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database")
        if databases != _EXPECTED_DATABASES:
            raise QualificationError(f"database residue: {databases}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qualify_sql.py")
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--cgroup-parent", required=True)
    parser.add_argument("--witness-out", type=Path, default=None)
    parser.add_argument("--fixture-root", type=Path, default=FIXTURE_ROOT)
    parser.add_argument("--allow-shared-host", action="store_true")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error("Python >= 3.11 is required")
    if args.scratch.exists():
        parser.error("--scratch must be absent")
    if _CONTAINER_NAME_RE.fullmatch(args.container_name) is None:
        parser.error("--container-name must match run-gate-assay-sql-<pid>-<epoch>")
    if not args.cgroup_parent:
        parser.error("--cgroup-parent must not be empty")

    previous = signal.signal(signal.SIGTERM, lambda *_: sys.exit(3))
    try:
        container = ThrowawayPostgres(
            args.container_name, args.cgroup_parent, args.fixture_root, allow_shared_host=args.allow_shared_host
        )
        try:
            run_qualification(
                container=container, scratch=args.scratch, fixture_root=args.fixture_root, witness_out=args.witness_out
            )
        except InconclusiveError as exc:
            print(f"ASSAY_SQL_INCONCLUSIVE={exc}", file=sys.stderr)
            return 3
        except subprocess.TimeoutExpired as exc:
            print(f"ASSAY_SQL_INCONCLUSIVE=command timed out: {list(exc.cmd)!r}", file=sys.stderr)
            return 3
        except QualificationError as exc:
            print(f"ASSAY_SQL_FAILED: {exc}", file=sys.stderr)
            return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    print("ASSAY_SQL_QUALIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
