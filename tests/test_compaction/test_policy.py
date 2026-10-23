"""Tests for compaction/policy.py — CompactionPolicy."""

import pytest

from code_agent.compaction.policy import CompactionPolicy


class TestCompactionPolicy:
    @pytest.fixture
    def policy(self):
        return CompactionPolicy(
            context_window=100_000,
            threshold_percent=85,
            min_tokens=5000,
        )

    def test_below_min_tokens(self, policy):
        assert not policy.should_compact(1000)

    def test_below_threshold(self, policy):
        assert not policy.should_compact(50_000)

    def test_above_threshold(self, policy):
        # 85% of 100k = 85k, so 90k should trigger
        assert policy.should_compact(90_000)

    def test_at_threshold_boundary(self, policy):
        threshold = 100_000 * 85 // 100  # 85_000
        assert policy.should_compact(threshold)

    def test_target_budget(self, policy):
        assert policy.target_budget() == 50_000  # 50% of 100k

    def test_update_context_window(self, policy):
        policy.update_context_window(200_000)
        assert policy.context_window == 200_000
        assert policy.target_budget() == 100_000

    def test_default_values(self):
        policy = CompactionPolicy()
        assert policy.context_window == 200_000
        assert policy.target_budget() == 100_000

    def test_custom_values(self):
        policy = CompactionPolicy(
            context_window=50_000, threshold_percent=80, min_tokens=1000,
        )
        assert not policy.should_compact(500)  # Below min
        assert not policy.should_compact(30_000)  # Below 80% of 50k
        assert policy.should_compact(42_000)  # Above 80% of 50k = 40k
