from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath

from conftest import GitRepo, prepared_snapshot

from assay import mutation, runner
from assay.adapters.sql import SqlAdapter


def test_sql_context_reads_unchanged_sources_from_prepared_manifest(
    git_repo: GitRepo, tmp_path: Path
):
    index_source = "CREATE UNIQUE INDEX idx ON s.t (a);\n"
    attachment_source = (
        "ALTER TABLE s.t ADD CONSTRAINT uq UNIQUE USING INDEX idx;\n"
    )
    git_repo.write("src/01-index.sql", index_source)
    git_repo.write("src/02-attach.sql", attachment_source)
    head = git_repo.commit_all("add SQL index and attachment")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        assert set(prepared.paths_under(PurePosixPath("src"))) == {
            PurePosixPath("src/01-index.sql"),
            PurePosixPath("src/02-attach.sql"),
        }
        context_sources = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(git_repo.path / "src",),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        target = mutation.MutationTarget(
            path="src/01-index.sql",
            text=index_source,
            lines=frozenset({1}),
        )
        jobs = mutation.collect_mutation_sites(
            (target,),
            adapter=SqlAdapter(),
            operators=("sql:drop-unique",),
            limit=10,
            context_sources=context_sources,
        )

    assert jobs == ()


def test_sql_context_ignores_malformed_test_sources_but_keeps_ddl_candidates(
    git_repo: GitRepo, tmp_path: Path
):
    index_source = "CREATE UNIQUE INDEX idx ON s.t (a);\n"
    git_repo.write("src/01-index.sql", index_source)
    git_repo.write("src/tests/test_malformed.sql", "SELECT 'unterminated\n")
    head = git_repo.commit_all("add DDL and malformed SQL test fixture")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        context_sources = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(git_repo.path / "src",),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        target = mutation.MutationTarget(
            path="src/01-index.sql",
            text=index_source,
            lines=frozenset({1}),
        )
        jobs = mutation.collect_mutation_sites(
            (target,),
            adapter=SqlAdapter(),
            operators=("sql:drop-unique",),
            limit=10,
            context_sources=context_sources,
        )

    assert len(jobs) == 1
    assert jobs[0].site.lineno == 1
