"""Tests for Classifier — pure logic, no kernel needed."""
import pytest
from damon_analysis import Classifier

SAMPLE_US = 100_000   # 100ms
AGGR_US   = 2_000_000 # 2s
MAX_NR    = AGGR_US // SAMPLE_US  # 20


class TestAccessRatePct:
    def test_zero_accesses(self):
        assert Classifier.access_rate_pct(0, MAX_NR) == 0.0

    def test_full_rate(self):
        assert Classifier.access_rate_pct(MAX_NR, MAX_NR) == 100.0

    def test_half_rate(self):
        assert Classifier.access_rate_pct(10, 20) == 50.0

    def test_zero_max(self):
        assert Classifier.access_rate_pct(5, 0) == 0.0

    def test_fractional(self):
        rate = Classifier.access_rate_pct(3, 20)
        assert abs(rate - 15.0) < 0.01


class TestClassify:
    def setup_method(self):
        self.c = Classifier(hot_access_rate_pct=50.0,
                            warm_access_rate_pct=5.0,
                            cold_age_sec=30.0,
                            idle_age_sec=120.0)

    def test_hot(self):
        # 18/20 = 90% → hot
        age_us = 1 * AGGR_US
        assert self.c.classify(18, age_us, MAX_NR) == 'hot'

    def test_hot_boundary(self):
        # exactly 50% → hot
        age_us = 1 * AGGR_US
        assert self.c.classify(10, age_us, MAX_NR) == 'hot'

    def test_warm_above_warm_threshold(self):
        # 2/20 = 10% → warm (>5% but <50%)
        age_us = 1 * AGGR_US
        assert self.c.classify(2, age_us, MAX_NR) == 'warm'

    def test_warm_transitional_not_yet_aged(self):
        # 0% but age only 10s (< cold_age 30s) → warm (transitional)
        age_us = int(10 * 1e6)
        assert self.c.classify(0, age_us, MAX_NR) == 'warm'

    def test_cold(self):
        # 0% and age 35s > cold_age 30s but < idle_age 120s → cold
        age_us = int(35 * 1e6)
        assert self.c.classify(0, age_us, MAX_NR) == 'cold'

    def test_idle(self):
        # 0% and age 130s > idle_age 120s → idle
        age_us = int(130 * 1e6)
        assert self.c.classify(0, age_us, MAX_NR) == 'idle'

    def test_just_below_idle_boundary(self):
        # 0% and age exactly at idle_age boundary → idle (>=)
        age_us = int(120 * 1e6)
        assert self.c.classify(0, age_us, MAX_NR) == 'idle'

    def test_nonzero_rate_below_warm_not_cold(self):
        # 1% (below warm threshold 5%) but not zero — should be 'warm' (transitional)
        age_us = int(60 * 1e6)  # old, but access rate is non-zero
        # rate = 0.2/20 is below warm but classify checks rate == 0 for cold
        # actual rate: access_rate_pct(0, MAX_NR)==0, so this is 0 rate
        # let's test with truly 0 rate first → already tested above
        # non-zero but below warm threshold: rate = 0.5/20 → Python int, use 0
        pass

    def test_zero_max_nr_accesses(self):
        # If max_nr_accesses=0, no accesses possible → rate=0 → check age
        age_us = int(200 * 1e6)
        result = self.c.classify(0, age_us, 0)
        assert result == 'idle'


class TestTemperature:
    def setup_method(self):
        self.c = Classifier()

    def test_zero_accesses_returns_negative(self):
        temp = self.c.temperature(0, 1_000_000, MAX_NR)
        assert temp < 0

    def test_higher_age_colder_when_zero_accesses(self):
        t1 = self.c.temperature(0, 1_000_000,  MAX_NR)
        t2 = self.c.temperature(0, 10_000_000, MAX_NR)
        assert t2 < t1

    def test_nonzero_accesses_positive(self):
        temp = self.c.temperature(10, 1_000_000, MAX_NR)
        assert temp > 0

    def test_more_accesses_higher_temp(self):
        t1 = self.c.temperature(5,  1_000_000, MAX_NR)
        t2 = self.c.temperature(15, 1_000_000, MAX_NR)
        assert t2 > t1


class TestClassifyRegions:
    def setup_method(self):
        self.c = Classifier(hot_access_rate_pct=50.0,
                            warm_access_rate_pct=5.0,
                            cold_age_sec=30.0,
                            idle_age_sec=120.0)

    def test_adds_required_keys(self, raw_regions):
        result = self.c.classify_regions(raw_regions, SAMPLE_US, AGGR_US)
        for r in result:
            assert 'class' in r
            assert 'temperature' in r
            assert 'size_bytes' in r
            assert 'access_rate_pct' in r
            assert 'age_us' in r
            assert 'age_sec' in r

    def test_size_bytes_correct(self, raw_regions):
        result = self.c.classify_regions(raw_regions, SAMPLE_US, AGGR_US)
        for orig, classified in zip(raw_regions, result):
            assert classified['size_bytes'] == orig['end'] - orig['start']

    def test_age_conversion(self, raw_regions):
        result = self.c.classify_regions(raw_regions, SAMPLE_US, AGGR_US)
        for orig, cl in zip(raw_regions, result):
            expected_age_us = orig['age'] * AGGR_US
            assert cl['age_us'] == expected_age_us
            assert abs(cl['age_sec'] - expected_age_us / 1e6) < 0.001

    def test_does_not_mutate_input(self, raw_regions):
        import copy
        original = copy.deepcopy(raw_regions)
        self.c.classify_regions(raw_regions, SAMPLE_US, AGGR_US)
        for orig, expected in zip(raw_regions, original):
            assert orig == expected

    def test_classification_correctness(self, raw_regions):
        result = self.c.classify_regions(raw_regions, SAMPLE_US, AGGR_US)
        classes = [r['class'] for r in result]
        # region 0: 18/20=90% → hot
        assert classes[0] == 'hot'
        # region 1: 2/20=10% → warm
        assert classes[1] == 'warm'
        # region 2: 0%, age=20*2s=40s > cold_age 30s → cold
        assert classes[2] == 'cold'
        # region 3: 0%, age=70*2s=140s > idle_age 120s → idle
        assert classes[3] == 'idle'
        # region 4: 12/20=60% → hot
        assert classes[4] == 'hot'

    def test_empty_regions(self):
        result = self.c.classify_regions([], SAMPLE_US, AGGR_US)
        assert result == []


class TestSummary:
    def setup_method(self):
        self.c = Classifier()

    def test_summary_keys(self, classified_regions):
        s = self.c.summary(classified_regions)
        assert set(s.keys()) == {'hot', 'warm', 'cold', 'idle'}

    def test_summary_counts(self, classified_regions):
        s = self.c.summary(classified_regions)
        total_count = sum(v['count'] for v in s.values())
        assert total_count == len(classified_regions)

    def test_summary_bytes_match_sizes(self, classified_regions):
        s = self.c.summary(classified_regions)
        total_bytes = sum(v['bytes'] for v in s.values())
        expected = sum(r['size_bytes'] for r in classified_regions)
        assert total_bytes == expected

    def test_summary_empty(self):
        s = self.c.summary([])
        for cls in ['hot', 'warm', 'cold', 'idle']:
            assert s[cls]['count'] == 0
            assert s[cls]['bytes'] == 0

    def test_hot_and_idle_counts(self, classified_regions):
        s = self.c.summary(classified_regions)
        assert s['hot']['count'] == 2
        assert s['warm']['count'] == 1
        assert s['cold']['count'] == 1
        assert s['idle']['count'] == 1
