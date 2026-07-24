"""Tests for the version-isolated, accumulate-never-delete benchmark store."""

from __future__ import annotations

from pathlib import Path

from nyxloom.benchmark_sources import BenchmarkRecord
from nyxloom.benchmark_store import StoredRow, _key, _sort_key, load_store, merge_dataset, write_store


class TestMergeDataset:
    def test_insert_new_model(self):
        rec = BenchmarkRecord(
            model_id="model-a", source="test", scores={"coding": 44.0},
            price_input=1.0, price_output=4.0, context_length=131072,
            raw={"slug": "model-a"},
        )
        merged = merge_dataset([], [rec], now="T1")

        assert len(merged) == 1
        row = merged[0]
        assert row.model_id == "model-a"
        assert row.source == "test"
        assert row.scores == {"coding": 44.0}
        assert row.price_input == 1.0
        assert row.price_output == 4.0
        assert row.context_length == 131072
        assert row.first_seen == "T1"
        assert row.last_seen == "T1"
        assert row.present_upstream is True
        assert row.benchmark is None
        assert row.version is None
        assert row.effort is None
        assert row.raw == {"slug": "model-a"}

    def test_retain_unseen(self):
        row_a = StoredRow(
            benchmark=None, version=None, model_id="model-a", effort=None,
            scores={"coding": 44.0}, price_input=1.0, price_output=4.0,
            context_length=131072, source="test",
            first_seen="T0", last_seen="T0", present_upstream=True, raw={},
        )
        merged = merge_dataset([row_a], [], now="T1")

        assert len(merged) == 1
        row = merged[0]
        assert row.model_id == "model-a"
        assert row.scores == {"coding": 44.0}
        assert row.price_input == 1.0
        assert row.price_output == 4.0
        assert row.context_length == 131072
        assert row.first_seen == "T0"
        assert row.last_seen == "T0"
        assert row.present_upstream is False

    def test_upsert_changed_score(self):
        row_a = StoredRow(
            benchmark=None, version=None, model_id="model-a", effort=None,
            scores={"coding": 40.0}, price_input=1.0, price_output=4.0,
            context_length=131072, source="test",
            first_seen="T0", last_seen="T0", present_upstream=True, raw={},
        )
        rec_a = BenchmarkRecord(
            model_id="model-a", source="test", scores={"coding": 55.0},
            price_input=1.0, price_output=4.0, context_length=131072,
            raw={},
        )
        merged = merge_dataset([row_a], [rec_a], now="T1")

        assert len(merged) == 1
        row = merged[0]
        assert row.scores == {"coding": 55.0}
        assert row.last_seen == "T1"
        assert row.first_seen == "T0"
        assert row.present_upstream is True

    def test_version_isolation(self):
        row_v1 = StoredRow(
            benchmark="deepswe", version="v1.0", model_id="X", effort=None,
            scores={"coding": 40.0}, price_input=None, price_output=None,
            context_length=None, source="test",
            first_seen="T0", last_seen="T0", present_upstream=True, raw={},
        )
        rec_v2 = BenchmarkRecord(
            model_id="X", source="test", scores={"coding": 50.0},
            price_input=None, price_output=None, context_length=None,
            raw={}, benchmark="deepswe", version="v1.1",
        )
        merged = merge_dataset([row_v1], [rec_v2], now="T1")

        assert len(merged) == 2
        by_key = {_key(r.benchmark, r.version, r.model_id, r.effort): r for r in merged}
        assert ("deepswe", "v1.0", "X", None) in by_key
        assert ("deepswe", "v1.1", "X", None) in by_key
        assert by_key[("deepswe", "v1.0", "X", None)].present_upstream is False
        assert by_key[("deepswe", "v1.1", "X", None)].present_upstream is True
        assert by_key[("deepswe", "v1.0", "X", None)].first_seen == "T0"
        assert by_key[("deepswe", "v1.1", "X", None)].first_seen == "T1"

    def test_effort_isolation(self):
        row_high = StoredRow(
            benchmark="b", version="v1", model_id="X", effort="high",
            scores={"coding": 40.0}, price_input=None, price_output=None,
            context_length=None, source="test",
            first_seen="T0", last_seen="T0", present_upstream=True, raw={},
        )
        rec_low = BenchmarkRecord(
            model_id="X", source="test", scores={"coding": 30.0},
            price_input=None, price_output=None, context_length=None,
            raw={}, benchmark="b", version="v1", effort="low",
        )
        merged = merge_dataset([row_high], [rec_low], now="T1")

        assert len(merged) == 2
        by_key = {_key(r.benchmark, r.version, r.model_id, r.effort): r for r in merged}
        assert ("b", "v1", "X", "high") in by_key
        assert ("b", "v1", "X", "low") in by_key
        assert by_key[("b", "v1", "X", "high")].present_upstream is False
        assert by_key[("b", "v1", "X", "low")].present_upstream is True

    def test_duplicate_key_in_scrape_last_wins(self):
        rec1 = BenchmarkRecord(
            model_id="M", source="test", scores={"coding": 10.0},
            price_input=None, price_output=None, context_length=None,
            raw={},
        )
        rec2 = BenchmarkRecord(
            model_id="M", source="test", scores={"coding": 99.0},
            price_input=None, price_output=None, context_length=None,
            raw={},
        )
        merged = merge_dataset([], [rec1, rec2], now="T1")

        assert len(merged) == 1
        assert merged[0].scores == {"coding": 99.0}

    def test_deterministic_order(self):
        rows = [
            StoredRow(benchmark="b", version=None, model_id="Z", effort=None,
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="t",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={}),
            StoredRow(benchmark="a", version=None, model_id="M", effort=None,
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="t",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={}),
            StoredRow(benchmark=None, version=None, model_id="A", effort=None,
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="t",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={}),
            StoredRow(benchmark="a", version="v2", model_id="M", effort=None,
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="t",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={}),
            StoredRow(benchmark="a", version="v1", model_id="M", effort=None,
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="t",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={}),
            StoredRow(benchmark="a", version=None, model_id="M", effort="high",
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="t",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={}),
        ]
        expected = sorted(rows, key=_sort_key)
        merged = merge_dataset(rows, [], now="T1")
        assert merged == expected


class TestPersistence:
    def test_round_trip(self, tmp_path: Path):
        rows = [
            StoredRow(benchmark="b", version="v1", model_id="M", effort="high",
                      scores={"coding": 44.0, "agentic": 30.0},
                      price_input=1.0, price_output=4.0, context_length=131072,
                      source="test", first_seen="T0", last_seen="T1",
                      present_upstream=True,
                      raw={"nested": {"list": [1, 2, 3], "null": None}}),
            StoredRow(benchmark=None, version=None, model_id="A", effort=None,
                      scores={}, price_input=None, price_output=None,
                      context_length=None, source="test",
                      first_seen="T0", last_seen="T0", present_upstream=False,
                      raw={"simple": "value"}),
        ]
        path = tmp_path / "store.toml"
        write_store(path, rows)
        loaded = load_store(path)
        expected = sorted(rows, key=_sort_key)
        assert loaded == expected
        assert loaded[0].raw == {"simple": "value"}
        assert loaded[1].raw == {"nested": {"list": [1, 2, 3], "null": None}}

    def test_byte_identical_idempotence(self, tmp_path: Path):
        rows = [
            StoredRow(benchmark="b", version="v1", model_id="M", effort=None,
                      scores={"coding": 44.0}, price_input=1.0, price_output=4.0,
                      context_length=131072, source="test",
                      first_seen="T0", last_seen="T0", present_upstream=True,
                      raw={"x": 1}),
        ]
        p1 = tmp_path / "s1.toml"
        p2 = tmp_path / "s2.toml"
        write_store(p1, rows)
        write_store(p2, rows)
        assert p1.read_bytes() == p2.read_bytes()

    def test_load_missing_file(self, tmp_path: Path):
        assert load_store(tmp_path / "nope.toml") == []
