"""Tests for funnel evaluation harness — STAT-BLOCKER C and D.

Covers:
- Coverage metrics (STAT-BLOCKER D)
- Turnover metrics (STAT-BLOCKER D)
- Concentration metrics (STAT-BLOCKER D)
- Rank bucket performance (STAT-BLOCKER D)
- Threshold sensitivity (STAT-BLOCKER D)
- Baseline comparison on identical candidate set (STAT-BLOCKER C)
- Report generation
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantlab.candidate_funnel import (
    CandidateFunnel,
    RankingKey,
    StageBConfig,
    StageBEvaluator,
    StageBResult,
)
from quantlab.funnel_evaluation import (
    compare_baseline,
    compute_concentration,
    compute_coverage,
    compute_rank_buckets,
    compute_threshold_sensitivity,
    compute_turnover,
    generate_report,
)
from quantlab.market_data import CorporateAction, CorporateActionKind, Observation, XNYSCalendar

NOW = datetime(2026, 9, 21, 22, tzinfo=UTC)
DAYS = XNYSCalendar().sessions_between(date(2026, 1, 2), date(2026, 9, 21))


def make_observations(
    instrument_id: str = "asset-1",
    symbol: str = "TEST",
    sessions: tuple[date, ...] | None = None,
    close: Decimal = Decimal("100"),
    volume: Decimal = Decimal("20000"),
) -> list[Observation]:
    sessions = sessions if sessions is not None else DAYS
    return [
        Observation(
            f"{instrument_id}-{i}",
            instrument_id,
            "alpaca:iex",
            "1d",
            d,
            XNYSCalendar().session_close(d),
            close,
            close,
            close,
            close,
            volume,
            NOW,
            f"src-{i}",
            f"hash-{i}",
            "import",
        )
        for i, d in enumerate(sessions)
    ]


def make_instrument(
    instrument_id: str = "asset-1",
    symbol: str = "TEST",
    exchange: str = "XNYS",
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "exchange": exchange,
    }


def make_corporate_action(
    instrument_id: str = "asset-1",
    known_at: datetime | None = None,
) -> CorporateAction:
    return CorporateAction(
        f"ca-{instrument_id}",
        instrument_id,
        CorporateActionKind.CASH_DIVIDEND,
        XNYSCalendar().session_open(DAYS[-10]),
        known_at or NOW,
        Decimal("1.00"),
    )


class RecordingStageBEvaluator(StageBEvaluator):
    """Test evaluator that records calls and returns deterministic scores."""

    def __init__(self, config: StageBConfig, score_map: dict[str, Decimal] | None = None):
        super().__init__(config)
        self.calls: list[str] = []
        self.score_map = score_map or {}

    def evaluate_one(
        self,
        instrument_id: str,
        symbol: str,
        stage_a_evidence: dict[str, object],
    ) -> StageBResult:
        self.calls.append(instrument_id)
        self.record_provider_request(1)
        score = self.score_map.get(instrument_id, Decimal("0.5"))
        return StageBResult(
            instrument_id=instrument_id,
            symbol=symbol,
            passed=True,
            rejection_reason=None,
            score=score,
            evidence={"evaluator": "test"},
        )


def _funnel_universe(n: int, close_by_index: dict[int, Decimal] | None = None):
    """Build n instruments whose Stage A screening metrics differ by construction."""
    instruments = [make_instrument(f"asset-{i:03d}", f"A{i}") for i in range(n)]
    observations = {}
    actions = {}
    for i in range(n):
        close = (close_by_index or {}).get(i, Decimal("100"))
        observations[f"asset-{i:03d}"] = make_observations(f"asset-{i:03d}", f"A{i}", close=close)
        actions[f"asset-{i:03d}"] = [make_corporate_action(f"asset-{i:03d}")]
    readiness = {f"asset-{i:03d}": f"r{i}" for i in range(n)}
    return instruments, observations, actions, readiness


def _run_funnel(
    n: int = 5,
    max_candidates: int = 3,
    ranking_key: RankingKey = RankingKey.INSTRUMENT_ID_ASC,
    snapshot_id: str = "snapshot-1",
    close_by_index: dict[int, Decimal] | None = None,
):
    instruments, observations, actions, readiness = _funnel_universe(n, close_by_index)
    config = StageBConfig(max_candidates=max_candidates, ranking_key=ranking_key)
    funnel = CandidateFunnel(stage_b_config=config)
    return funnel.run(
        snapshot_id,
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )


# ---------------------------------------------------------------------------
# Coverage metrics (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


def test_coverage_metrics_basic():
    """CoverageMetrics computes correct rates."""
    run = _run_funnel(n=10, max_candidates=3)
    cov = compute_coverage(run)

    assert cov.universe_size == 10
    assert cov.stage_a_accepted == 10
    assert cov.stage_b_evaluated == 3
    assert cov.final_candidates == 3
    assert cov.coverage_rate == pytest.approx(0.3)
    assert cov.stage_a_pass_rate == pytest.approx(1.0)
    assert cov.stage_b_pass_rate == pytest.approx(1.0)


def test_coverage_metrics_empty_universe():
    """CoverageMetrics handles empty universe."""
    run = _run_funnel(n=0, max_candidates=3)
    cov = compute_coverage(run)

    assert cov.universe_size == 0
    assert cov.coverage_rate == 0.0
    assert cov.stage_a_pass_rate == 0.0
    assert cov.stage_b_pass_rate == 0.0


def test_coverage_metrics_partial_stage_a():
    """CoverageMetrics with some Stage A rejections."""
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(5)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(5)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(5)}
    readiness = {f"asset-{i}": f"r{i}" for i in range(5)}

    # Make asset-3 fail Stage A by setting price too low
    observations["asset-3"] = make_observations("asset-3", "A3", close=Decimal("1"))

    config = StageBConfig(max_candidates=5)
    funnel = CandidateFunnel(stage_b_config=config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )

    cov = compute_coverage(run)
    assert cov.universe_size == 5
    assert cov.stage_a_accepted == 4
    assert cov.stage_a_pass_rate == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Turnover metrics (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


def test_turnover_identical_runs():
    """TurnoverMetrics for identical runs has Jaccard = 1.0."""
    run1 = _run_funnel(n=5, max_candidates=3)
    run2 = _run_funnel(n=5, max_candidates=3)

    turnover = compute_turnover(run1, run2)

    assert turnover.jaccard_index == pytest.approx(1.0)
    assert turnover.intersection_size == 3
    assert turnover.union_size == 3
    assert turnover.added == ()
    assert turnover.removed == ()


def test_turnover_different_candidates():
    """TurnoverMetrics for different candidate sets."""
    # Vary volume so FEED_DOLLAR_VOLUME_20_DESC ranking differs from INSTRUMENT_ID_ASC
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(5)]
    observations = {}
    actions = {}
    for i in range(5):
        observations[f"asset-{i}"] = make_observations(
            f"asset-{i}", f"A{i}", volume=Decimal(str(20000 + i * 1000))
        )
        actions[f"asset-{i}"] = [make_corporate_action(f"asset-{i}")]
    readiness = {f"asset-{i}": f"r{i}" for i in range(5)}

    config1 = StageBConfig(max_candidates=3, ranking_key=RankingKey.INSTRUMENT_ID_ASC)
    config2 = StageBConfig(max_candidates=3, ranking_key=RankingKey.FEED_DOLLAR_VOLUME_20_DESC)
    funnel1 = CandidateFunnel(stage_b_config=config1)
    funnel2 = CandidateFunnel(stage_b_config=config2)

    run1 = funnel1.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config1),
        readiness_ids=readiness,
    )
    run2 = funnel2.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config2),
        readiness_ids=readiness,
    )

    turnover = compute_turnover(run1, run2)

    assert turnover.jaccard_index < 1.0
    assert len(turnover.added) > 0 or len(turnover.removed) > 0


def test_turnover_empty_sets():
    """TurnoverMetrics handles empty candidate sets."""
    run1 = _run_funnel(n=0, max_candidates=3)
    run2 = _run_funnel(n=0, max_candidates=3)

    turnover = compute_turnover(run1, run2)

    assert turnover.jaccard_index == pytest.approx(1.0)
    assert turnover.union_size == 0


# ---------------------------------------------------------------------------
# Concentration metrics (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


def test_concentration_single_run():
    """ConcentrationMetrics for a single run."""
    run = _run_funnel(n=5, max_candidates=3)
    conc = compute_concentration([run])

    assert sum(conc.by_asset.values()) == 3
    assert conc.hhi_asset > 0.0
    assert conc.hhi_sector > 0.0


def test_concentration_multiple_runs():
    """ConcentrationMetrics across multiple runs."""
    runs = [_run_funnel(n=5, max_candidates=3) for _ in range(3)]
    conc = compute_concentration(runs)

    assert sum(conc.by_asset.values()) == 9
    assert len(conc.by_date) >= 1


def test_concentration_empty_runs():
    """ConcentrationMetrics handles empty runs."""
    conc = compute_concentration([])

    assert conc.by_asset == {}
    assert conc.hhi_asset == 0.0
    assert conc.hhi_sector == 0.0


# ---------------------------------------------------------------------------
# Rank bucket performance (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


def test_rank_buckets_basic():
    """RankBucketPerformance computes correct bucket stats."""
    run = _run_funnel(n=10, max_candidates=5)
    buckets = compute_rank_buckets(run, bucket_size=2)

    assert len(buckets) > 0
    total_count = sum(b.count for b in buckets)
    assert total_count == 10


def test_rank_buckets_empty():
    """RankBucketPerformance handles empty results."""
    run = _run_funnel(n=0, max_candidates=3)
    buckets = compute_rank_buckets(run)

    assert buckets == ()


def test_rank_buckets_score_aggregation():
    """RankBucketPerformance aggregates scores correctly."""
    instruments, observations, actions, readiness = _funnel_universe(4)
    config = StageBConfig(max_candidates=4)
    score_map = {
        "asset-000": Decimal("0.9"),
        "asset-001": Decimal("0.7"),
        "asset-002": Decimal("0.5"),
        "asset-003": Decimal("0.3"),
    }
    funnel = CandidateFunnel(stage_b_config=config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config, score_map),
        readiness_ids=readiness,
    )

    buckets = compute_rank_buckets(run, bucket_size=2)
    assert len(buckets) == 2
    # First bucket should have higher mean score
    assert buckets[0].mean_score > buckets[1].mean_score


# ---------------------------------------------------------------------------
# Threshold sensitivity (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


def test_threshold_sensitivity_same_candidates():
    """ThresholdSensitivity for identical candidate sets."""
    run1 = _run_funnel(n=5, max_candidates=3)
    run2 = _run_funnel(n=5, max_candidates=3)

    sens = compute_threshold_sensitivity(run1, run2, "max_candidates")

    assert sens.jaccard_index == pytest.approx(1.0)
    assert not sens.changed


def test_threshold_sensitivity_different_candidates():
    """ThresholdSensitivity for different candidate sets."""
    run1 = _run_funnel(n=5, max_candidates=2)
    run2 = _run_funnel(n=5, max_candidates=4)

    sens = compute_threshold_sensitivity(run1, run2, "max_candidates")

    assert sens.jaccard_index < 1.0
    assert sens.changed
    assert sens.baseline_value == "2"
    assert sens.perturbed_value == "4"


def test_threshold_sensitivity_empty():
    """ThresholdSensitivity handles empty candidate sets."""
    run1 = _run_funnel(n=0, max_candidates=3)
    run2 = _run_funnel(n=0, max_candidates=3)

    sens = compute_threshold_sensitivity(run1, run2)

    assert sens.jaccard_index == pytest.approx(1.0)
    assert not sens.changed


# ---------------------------------------------------------------------------
# Baseline comparison (STAT-BLOCKER C)
# ---------------------------------------------------------------------------


def test_baseline_comparison_same_snapshot():
    """BaselineComparison on same universe snapshot."""
    run1 = _run_funnel(n=5, max_candidates=3, snapshot_id="snapshot-1")
    run2 = _run_funnel(n=5, max_candidates=3, snapshot_id="snapshot-1")

    comp = compare_baseline(run1, run2)

    assert comp.same_opportunity_timestamps is True
    assert comp.candidate_set_size == 3
    assert comp.stage_b_mean_score is not None
    assert comp.baseline_mean_score is not None


def test_baseline_comparison_different_snapshot():
    """BaselineComparison fails closed on different universe snapshots."""
    run1 = _run_funnel(n=5, max_candidates=3, snapshot_id="snapshot-1")
    run2 = _run_funnel(n=5, max_candidates=3, snapshot_id="snapshot-2")

    comp = compare_baseline(run1, run2)

    assert comp.same_opportunity_timestamps is False


def test_baseline_comparison_lift():
    """BaselineComparison computes lift correctly."""
    instruments, observations, actions, readiness = _funnel_universe(3)
    config = StageBConfig(max_candidates=3)

    # Stage B with high scores
    score_map_b = {
        "asset-000": Decimal("0.9"),
        "asset-001": Decimal("0.8"),
        "asset-002": Decimal("0.7"),
    }
    # Baseline with low scores
    score_map_base = {
        "asset-000": Decimal("0.3"),
        "asset-001": Decimal("0.2"),
        "asset-002": Decimal("0.1"),
    }

    funnel = CandidateFunnel(stage_b_config=config)
    run_b = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config, score_map_b),
        readiness_ids=readiness,
    )
    run_base = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config, score_map_base),
        readiness_ids=readiness,
    )

    comp = compare_baseline(run_b, run_base)

    assert comp.lift is not None
    assert comp.lift > 0.0
    assert comp.stage_b_mean_score > comp.baseline_mean_score


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def test_generate_report_single_run():
    """FunnelEvaluationReport for a single run."""
    run = _run_funnel(n=5, max_candidates=3)
    report = generate_report([run])

    assert report.funnel_version == run.funnel_version
    assert report.ranking_key == run.ranking_key
    assert len(report.coverage) == 1
    assert len(report.turnover) == 0
    assert report.baseline_comparison is None


def test_generate_report_multiple_runs():
    """FunnelEvaluationReport for multiple runs."""
    runs = [_run_funnel(n=5, max_candidates=3) for _ in range(3)]
    report = generate_report(runs)

    assert len(report.coverage) == 3
    assert len(report.turnover) == 2


def test_generate_report_with_baseline():
    """FunnelEvaluationReport with baseline comparison."""
    run = _run_funnel(n=5, max_candidates=3, snapshot_id="snapshot-1")
    baseline = _run_funnel(n=5, max_candidates=3, snapshot_id="snapshot-1")
    report = generate_report([run], baseline_run=baseline)

    assert report.baseline_comparison is not None
    assert report.baseline_comparison.same_opportunity_timestamps is True


def test_generate_report_with_threshold_sensitivity():
    """FunnelEvaluationReport with threshold sensitivity."""
    run = _run_funnel(n=5, max_candidates=3)
    perturbed = _run_funnel(n=5, max_candidates=4)
    report = generate_report([run], threshold_perturbed_run=perturbed)

    assert len(report.threshold_sensitivity) == 1
    assert report.threshold_sensitivity[0].parameter == "max_candidates"


def test_generate_report_empty_runs_fails():
    """generate_report fails closed on empty runs."""
    with pytest.raises(ValueError, match="At least one FunnelRun"):
        generate_report([])


def test_generate_report_trial_family_key():
    """FunnelEvaluationReport exposes trial_family_key."""
    run = _run_funnel(n=5, max_candidates=3)
    report = generate_report([run])

    assert report.trial_family_key == run.trial_family_key()
