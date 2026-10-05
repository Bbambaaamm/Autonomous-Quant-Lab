"""Tests for two-stage candidate funnel (#268).

Covers acceptance criteria:
- Stage A uses existing canonical data/screening primitives
- Stage A result is immutable candidate snapshot
- Rejection reason stored for every evaluated instrument
- Stage B never expands scope beyond Stage A candidate set
- Stage B has hard candidate/request/runtime/concurrency budget
- Broad-universe test: expensive evaluation complexity follows candidate count
- Deterministic replay returns same candidate set
- PIT/known_at semantics preserved
- Fail-closed on incomplete data
- No screening/candidate agent has execution authority
- Resource pressure can delay Stage B without losing Stage A evidence
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from quantlab.candidate_funnel import (
    CandidateFunnel,
    CompletionState,
    FunnelConfigMismatch,
    FunnelRankingError,
    RankingKey,
    RejectionReason,
    SelectionReason,
    StageAConfig,
    StageBConfig,
    StageBEvaluator,
    StageBResult,
    deterministic_replay_check,
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


class FailingStageBEvaluator(StageBEvaluator):
    """Test evaluator that fails on specific instruments."""

    def __init__(self, config: StageBConfig, fail_on: set[str]):
        super().__init__(config)
        self.fail_on = fail_on

    def evaluate_one(
        self,
        instrument_id: str,
        symbol: str,
        stage_a_evidence: dict[str, object],
    ) -> StageBResult:
        if instrument_id in self.fail_on:
            raise RuntimeError("Simulated evaluation failure")
        return StageBResult(
            instrument_id=instrument_id,
            symbol=symbol,
            passed=True,
            rejection_reason=None,
            score=Decimal("0.5"),
        )


def test_stage_a_uses_existing_screening_primitive():
    """Stage A must use evaluate_screen from market_screening, not a parallel pipeline."""
    funnel = CandidateFunnel()
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "readiness-1"},
    )

    assert len(results) == 1
    assert results[0].passed
    assert results[0].rejection_reason is None
    # Verify it used the existing screening evidence
    assert "policy" in results[0].screening_evidence
    assert "momentum" in results[0].screening_evidence


def test_stage_a_rejects_instrument_not_in_universe():
    """Instrument with no observations gets NOT_IN_UNIVERSE rejection."""
    funnel = CandidateFunnel()
    instruments = [make_instrument("missing-1", "MISS")]
    observations: dict[str, list[Observation]] = {}
    actions: dict[str, list[CorporateAction]] = {}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.NOT_IN_UNIVERSE


def test_stage_a_rejects_future_data_fail_closed():
    """Future observations cause FUTURE_DATA rejection (fail-closed)."""
    funnel = CandidateFunnel()
    instruments = [make_instrument()]
    obs = make_observations()
    # Make last observation appear from the future
    obs[-1] = replace(obs[-1], observed_at=NOW + timedelta(seconds=1))
    observations = {"asset-1": obs}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "readiness-1"},
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.FUTURE_DATA


def test_stage_a_rejects_low_price():
    """Price below minimum gets STAGE_A_FILTERED rejection."""
    config = StageAConfig(minimum_price_usd=Decimal("50"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations(close=Decimal("10"))}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "readiness-1"},
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED


def test_stage_a_rejects_low_liquidity():
    """Volume below minimum gets STAGE_A_FILTERED rejection."""
    config = StageAConfig(minimum_feed_dollar_volume_20=Decimal("10000000"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations(volume=Decimal("100"))}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "readiness-1"},
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED


def test_stage_a_rejects_disallowed_exchange():
    """Exchange not in allowlist gets STAGE_A_FILTERED rejection."""
    config = StageAConfig(allowed_exchanges=frozenset({"XNYS"}))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument(exchange="NASDAQ")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "readiness-1"},
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED
    assert results[0].rejection_details.get("exchange_not_allowed") == "NASDAQ"


def test_stage_a_rejects_unverified_corporate_actions():
    """Missing corporate action evidence gets STAGE_A_FILTERED rejection."""
    config = StageAConfig(require_corporate_actions_verified=True)
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions: dict[str, list[CorporateAction]] = {}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": None},
    )

    assert len(results) == 1
    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED


def test_stage_b_never_expands_beyond_stage_a_candidates():
    """Stage B only evaluates instruments that passed Stage A."""
    funnel = CandidateFunnel()
    instruments = [
        make_instrument("pass-1", "PASS1"),
        make_instrument("fail-1", "FAIL1"),
    ]
    observations = {
        "pass-1": make_observations("pass-1", "PASS1"),
        "fail-1": make_observations("fail-1", "FAIL1", close=Decimal("1")),
    }
    actions = {
        "pass-1": [make_corporate_action("pass-1")],
        "fail-1": [make_corporate_action("fail-1")],
    }

    stage_a_results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"pass-1": "r1", "fail-1": "r2"},
    )

    evaluator = RecordingStageBEvaluator(StageBConfig())
    stage_b_results = funnel.run_stage_b(stage_a_results, evaluator)

    # Only pass-1 should have been evaluated
    assert evaluator.calls == ["pass-1"]
    assert len(stage_b_results) == 1
    assert stage_b_results[0].instrument_id == "pass-1"


def test_stage_b_hard_candidate_budget():
    """Stage B stops after max_candidates evaluations."""
    config = StageBConfig(max_candidates=2)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(5)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(5)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(5)}

    stage_a_results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(5)},
    )

    evaluator = RecordingStageBEvaluator(config)
    stage_b_results = funnel.run_stage_b(stage_a_results, evaluator)

    assert len(evaluator.calls) == 2
    # Remaining candidates get STAGE_B_BUDGET_EXHAUSTED
    budget_exhausted = [r for r in stage_b_results if not r.passed]
    assert len(budget_exhausted) == 3
    assert all(
        r.rejection_reason is RejectionReason.STAGE_B_BUDGET_EXHAUSTED for r in budget_exhausted
    )


def test_stage_b_hard_provider_request_budget():
    """Stage B stops after max_provider_requests evaluations."""
    config = StageBConfig(max_candidates=10, max_provider_requests=3)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(5)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(5)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(5)}

    stage_a_results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(5)},
    )

    evaluator = RecordingStageBEvaluator(config)
    funnel.run_stage_b(stage_a_results, evaluator)

    assert evaluator._provider_requests == 3
    assert len(evaluator.calls) == 3


def test_stage_b_evaluation_failure_is_captured():
    """Stage B evaluation failures produce STAGE_B_REJECTED, not crashes."""
    config = StageBConfig()
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument("fail-1", "FAIL1")]
    observations = {"fail-1": make_observations("fail-1", "FAIL1")}
    actions = {"fail-1": [make_corporate_action("fail-1")]}

    stage_a_results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"fail-1": "r1"},
    )

    evaluator = FailingStageBEvaluator(config, fail_on={"fail-1"})
    stage_b_results = funnel.run_stage_b(stage_a_results, evaluator)

    assert len(stage_b_results) == 1
    assert not stage_b_results[0].passed
    assert stage_b_results[0].rejection_reason is RejectionReason.STAGE_B_REJECTED
    assert "error" in stage_b_results[0].evidence


def test_deterministic_replay_returns_same_candidate_set():
    """Same inputs produce same final candidates and rejection counts."""
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}
    readiness = {f"asset-{i}": f"r{i}" for i in range(3)}

    funnel = CandidateFunnel()
    evaluator1 = RecordingStageBEvaluator(StageBConfig())
    evaluator2 = RecordingStageBEvaluator(StageBConfig())

    run1 = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator1,
        readiness_ids=readiness,
    )
    run2 = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator2,
        readiness_ids=readiness,
    )

    assert deterministic_replay_check(run1, run2)
    assert run1.final_candidates == run2.final_candidates
    assert run1.rejection_counts == run2.rejection_counts


def test_broad_universe_candidate_count_drives_stage_b_complexity():
    """Stage B work is proportional to Stage A candidates, not universe size."""
    # Large universe, few pass Stage A
    n_universe = 100
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(n_universe)]
    observations = {}
    actions = {}
    for i in range(n_universe):
        if i < 5:
            # These pass Stage A
            observations[f"asset-{i}"] = make_observations(f"asset-{i}", f"A{i}")
            actions[f"asset-{i}"] = [make_corporate_action(f"asset-{i}")]
        else:
            # These fail Stage A (low price)
            observations[f"asset-{i}"] = make_observations(
                f"asset-{i}", f"A{i}", close=Decimal("1")
            )
            actions[f"asset-{i}"] = [make_corporate_action(f"asset-{i}")]

    funnel = CandidateFunnel()
    evaluator = RecordingStageBEvaluator(StageBConfig())

    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(n_universe)},
    )

    # Stage B only evaluated the 5 that passed Stage A
    assert len(evaluator.calls) == 5
    assert len(run.final_candidates) == 5
    assert run.rejection_counts.get(RejectionReason.STAGE_A_FILTERED.value, 0) == 95


def test_funnel_run_has_immutable_lineage():
    """FunnelRun contains complete lineage from universe to final candidates."""
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    funnel = CandidateFunnel()
    evaluator = RecordingStageBEvaluator(StageBConfig())

    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={"asset-1": "r1"},
    )

    assert run.universe_snapshot_id == "snapshot-1"
    assert run.stage_a_config_hash
    assert run.stage_b_config_hash
    assert run.content_hash
    assert run.run_id
    assert len(run.stage_a_results) == 1
    assert len(run.stage_b_results) == 1
    assert run.final_candidates == ("asset-1",)
    assert run.created_at is not None


def test_stage_a_config_hash_is_deterministic():
    """Same config produces same hash."""
    config1 = StageAConfig()
    config2 = StageAConfig()
    assert config1.config_hash == config2.config_hash


def test_stage_b_config_hash_is_deterministic():
    """Same config produces same hash."""
    config1 = StageBConfig()
    config2 = StageBConfig()
    assert config1.config_hash == config2.config_hash


def test_stage_a_config_validation():
    """Invalid Stage A config raises ValueError."""
    with pytest.raises(ValueError):
        StageAConfig(minimum_sessions=0)
    with pytest.raises(ValueError):
        StageAConfig(minimum_coverage=Decimal("0"))
    with pytest.raises(ValueError):
        StageAConfig(minimum_price_usd=Decimal("0"))


def test_stage_b_config_validation():
    """Invalid Stage B config raises ValueError."""
    with pytest.raises(ValueError):
        StageBConfig(max_candidates=0)
    with pytest.raises(ValueError):
        StageBConfig(max_runtime_seconds=0)
    with pytest.raises(ValueError):
        StageBConfig(max_concurrency=0)


def test_pit_semantics_preserved():
    """Observations after as_of are rejected (PIT/known_at preserved)."""
    funnel = CandidateFunnel()
    instruments = [make_instrument()]
    obs = make_observations()
    # Last observation is from the future relative to as_of
    future_obs = replace(obs[-1], observed_at=NOW + timedelta(hours=1))
    obs[-1] = future_obs
    observations = {"asset-1": obs}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.FUTURE_DATA


def test_rejection_reason_stored_for_every_evaluated_instrument():
    """Every instrument in Stage A has a rejection reason if not passed."""
    instruments = [
        make_instrument("pass-1", "P1"),
        make_instrument("fail-1", "F1"),
        make_instrument("missing-1", "M1"),
    ]
    observations = {
        "pass-1": make_observations("pass-1", "P1"),
        "fail-1": make_observations("fail-1", "F1", close=Decimal("1")),
    }
    actions = {
        "pass-1": [make_corporate_action("pass-1")],
        "fail-1": [make_corporate_action("fail-1")],
    }

    funnel = CandidateFunnel()
    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"pass-1": "r1", "fail-1": "r2"},
    )

    assert len(results) == 3
    by_id = {r.instrument_id: r for r in results}
    assert by_id["pass-1"].rejection_reason is None
    assert by_id["fail-1"].rejection_reason is RejectionReason.STAGE_A_FILTERED
    assert by_id["missing-1"].rejection_reason is RejectionReason.NOT_IN_UNIVERSE


def test_no_execution_authority_in_funnel():
    """Funnel module has no trading/execution imports."""
    import inspect

    import quantlab.candidate_funnel as cf

    source = inspect.getsource(cf)
    forbidden = ["PaperBroker", "ExecutionEngine", "OrderIntent", "place_order", "submit_order"]
    for term in forbidden:
        assert term not in source, f"Funnel must not reference {term}"


def test_stage_a_momentum_filter():
    """Momentum outside configured bounds gets rejected."""
    config = StageAConfig(min_momentum=Decimal("0.01"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    # Zero momentum (flat prices, no corporate actions to adjust closes)
    observations = {"asset-1": make_observations(close=Decimal("100"))}
    actions: dict[str, list[CorporateAction]] = {}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED
    assert "momentum_too_low" in results[0].rejection_details


def test_stage_a_trend_filter():
    """Trend outside configured bounds gets rejected."""
    config = StageAConfig(min_trend=Decimal("0.01"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    # Zero trend (flat prices, no corporate actions to adjust closes)
    observations = {"asset-1": make_observations(close=Decimal("100"))}
    actions: dict[str, list[CorporateAction]] = {}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED
    assert "trend_too_low" in results[0].rejection_details


def test_stage_a_mean_reversion_filter():
    """Mean reversion outside configured bounds gets rejected."""
    config = StageAConfig(min_mean_reversion=Decimal("0.01"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    # Zero mean reversion (flat prices, no corporate actions to adjust closes)
    observations = {"asset-1": make_observations(close=Decimal("100"))}
    actions: dict[str, list[CorporateAction]] = {}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED
    assert "mean_reversion_too_low" in results[0].rejection_details


def test_empty_universe_produces_empty_results():
    """Empty instrument list produces empty results."""
    funnel = CandidateFunnel()
    results = funnel.run_stage_a(
        "snapshot-1",
        [],
        {},
        {},
        DAYS,
        NOW,
    )
    assert results == ()


def test_stage_b_budget_exhausted_does_not_lose_stage_a_evidence():
    """When Stage B budget is exhausted, Stage A evidence is preserved in FunnelRun."""
    config = StageBConfig(max_candidates=1)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = RecordingStageBEvaluator(config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    # All 3 Stage A results are preserved
    assert len(run.stage_a_results) == 3
    # Only 1 Stage B result (budget exhausted for rest)
    assert len(run.stage_b_results) == 3
    passed_b = [r for r in run.stage_b_results if r.passed]
    assert len(passed_b) == 1


def test_stage_b_default_config_does_not_immediately_stop():
    """Default config (max_llm_calls=0) must not block Stage B via LLM budget."""
    evaluator = StageBEvaluator(StageBConfig())
    evaluator.start()
    assert not evaluator.should_stop()


def test_stage_b_runtime_budget_enforced():
    """Stage B should_stop triggers when elapsed time exceeds runtime budget."""
    config = StageBConfig(max_runtime_seconds=1.0)
    evaluator = StageBEvaluator(config)
    evaluator.start()
    assert not evaluator.should_stop()
    evaluator._start_time = datetime.now(UTC) - timedelta(seconds=2)
    assert evaluator.should_stop()


def test_stage_b_llm_budget_enforced_when_configured():
    """Stage B stops when LLM call budget is exhausted (only when max_llm_calls > 0)."""
    config = StageBConfig(max_llm_calls=3)
    evaluator = StageBEvaluator(config)
    evaluator.start()
    assert not evaluator.should_stop()
    evaluator.record_llm_call(3)
    assert evaluator.should_stop()


def test_stage_b_candidate_budget_enforced():
    """Stage B stops after processing max_candidates candidates."""
    config = StageBConfig(max_candidates=2, max_provider_requests=100)
    evaluator = StageBEvaluator(config)
    evaluator.start()
    assert not evaluator.should_stop()
    evaluator.record_candidate_processed(2)
    assert evaluator.should_stop()


def test_stage_b_max_concurrency_config():
    """Stage B config includes concurrency budget; default is sequential (1)."""
    config = StageBConfig()
    assert config.max_concurrency == 1
    config_concurrent = StageBConfig(max_concurrency=4)
    assert config_concurrent.max_concurrency == 4
    with pytest.raises(ValueError):
        StageBConfig(max_concurrency=0)


class ResourcePressureStageBEvaluator(StageBEvaluator):
    """Evaluator that simulates resource pressure by exceeding the runtime budget."""

    @property
    def elapsed_seconds(self) -> float:
        return self.config.max_runtime_seconds + 1.0

    def stop_reason(self) -> RejectionReason | None:
        return RejectionReason.NOT_EVALUATED_RESOURCE_PRESSURE

    def evaluate_one(
        self,
        instrument_id: str,
        symbol: str,
        stage_a_evidence: dict[str, object],
    ) -> StageBResult:
        raise AssertionError("Stage B must not evaluate under resource pressure")


def test_resource_pressure_defers_stage_b_preserving_evidence():
    """Resource pressure defers Stage B without losing Stage A evidence."""
    config = StageBConfig(max_runtime_seconds=300.0)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = ResourcePressureStageBEvaluator(config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    # Stage B was deferred (no candidates processed)
    assert evaluator._candidates_processed == 0
    # All 3 Stage A results are preserved (fail-closed: Stage A evidence retained)
    assert len(run.stage_a_results) == 3
    assert all(r.passed for r in run.stage_a_results)
    # All Stage B candidates marked as not evaluated due to resource pressure
    assert len(run.stage_b_results) == 3
    assert all(
        r.rejection_reason is RejectionReason.NOT_EVALUATED_RESOURCE_PRESSURE
        for r in run.stage_b_results
    )
    # No final candidates emerged from Stage B
    assert len(run.final_candidates) == 0


def test_funnel_run_summary_provides_counts_and_rejections():
    """to_summary() provides funnel counts and rejection categories without HTTP."""
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}
    # Third instrument fails Stage A (low price)
    observations["asset-2"] = make_observations("asset-2", "A2", close=Decimal("1"))
    actions["asset-2"] = []

    funnel = CandidateFunnel()
    evaluator = RecordingStageBEvaluator(StageBConfig())
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    summary = run.to_summary()
    assert summary["universe_snapshot_id"] == "snapshot-1"
    assert summary["stage_a_config_hash"]
    assert summary["stage_b_config_hash"]
    counts = summary["funnel_counts"]
    assert counts["universe_size"] == 3
    assert counts["stage_a_accepted"] == 2
    assert counts["stage_a_rejected"] == 1
    assert counts["stage_b_candidates_evaluated"] == 2
    assert counts["stage_b_accepted"] == 2
    assert counts["stage_b_rejected"] == 0
    assert counts["final_candidates"] == 2
    assert RejectionReason.STAGE_A_FILTERED.value in summary["rejection_categories"]
    assert summary["rejection_categories"][RejectionReason.STAGE_A_FILTERED.value] == 1


def test_funnel_run_summary_empty_universe():
    """to_summary() handles empty universe gracefully."""
    funnel = CandidateFunnel()
    evaluator = RecordingStageBEvaluator(StageBConfig())
    run = funnel.run(
        "snapshot-empty",
        [],
        {},
        {},
        DAYS,
        NOW,
        evaluator,
    )

    summary = run.to_summary()
    assert summary["funnel_counts"]["universe_size"] == 0
    assert summary["funnel_counts"]["stage_a_accepted"] == 0
    assert summary["funnel_counts"]["final_candidates"] == 0
    assert summary["rejection_categories"] == {}


# ─── Review BLOCKER A: REJECTED vs NOT_EVALUATED ───────────────────────────


def test_budget_exhaustion_is_not_evaluated_budget():
    """Budget exhaustion produces NOT_EVALUATED_BUDGET, not REJECTED_RULE."""
    config = StageBConfig(max_candidates=1)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = RecordingStageBEvaluator(config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    # 1 evaluated, 2 not evaluated due to budget
    assert len(evaluator.calls) == 1
    not_evaluated = [r for r in run.stage_b_results if not r.passed]
    assert len(not_evaluated) == 2
    assert all(r.rejection_reason is RejectionReason.NOT_EVALUATED_BUDGET for r in not_evaluated)
    # Completion state is PARTIAL_BUDGET
    assert run.completion_state is CompletionState.PARTIAL_BUDGET


def test_timeout_is_not_evaluated_timeout():
    """Timeout produces NOT_EVALUATED_TIMEOUT, not REJECTED_RULE."""
    config = StageBConfig(max_runtime_seconds=1.0)
    evaluator = StageBEvaluator(config)
    evaluator.start()
    evaluator._start_time = datetime.now(UTC) - timedelta(seconds=2)

    reason = evaluator.stop_reason()
    assert reason is RejectionReason.NOT_EVALUATED_TIMEOUT


def test_resource_pressure_is_not_evaluated_resource_pressure():
    """Resource pressure produces NOT_EVALUATED_RESOURCE_PRESSURE."""
    config = StageBConfig(max_runtime_seconds=300.0)
    evaluator = ResourcePressureStageBEvaluator(config)
    evaluator.start()

    reason = evaluator.stop_reason()
    assert reason is RejectionReason.NOT_EVALUATED_RESOURCE_PRESSURE


def test_completion_state_complete_when_all_evaluated():
    """CompletionState.COMPLETE when all Stage A candidates are evaluated."""
    funnel = CandidateFunnel()
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = RecordingStageBEvaluator(StageBConfig())
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    assert run.completion_state is CompletionState.COMPLETE


def test_completion_state_partial_budget():
    """CompletionState.PARTIAL_BUDGET when budget exhausted."""
    config = StageBConfig(max_candidates=1)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = RecordingStageBEvaluator(config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    assert run.completion_state is CompletionState.PARTIAL_BUDGET


def test_completion_state_partial_timeout():
    """CompletionState.PARTIAL_TIMEOUT when timeout occurs."""

    class TimeoutEvaluator(StageBEvaluator):
        @property
        def elapsed_seconds(self) -> float:
            return self.config.max_runtime_seconds + 1.0

        def stop_reason(self) -> RejectionReason | None:
            return RejectionReason.NOT_EVALUATED_TIMEOUT

    config = StageBConfig(max_runtime_seconds=300.0)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = TimeoutEvaluator(config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    assert run.completion_state is CompletionState.PARTIAL_TIMEOUT


def test_completion_state_partial_resource_pressure():
    """CompletionState.PARTIAL_RESOURCE_PRESSURE when resource pressure defers."""
    config = StageBConfig(max_runtime_seconds=300.0)
    funnel = CandidateFunnel(stage_b_config=config)
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(3)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(3)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(3)}

    evaluator = ResourcePressureStageBEvaluator(config)
    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        evaluator,
        readiness_ids={f"asset-{i}": f"r{i}" for i in range(3)},
    )

    assert run.completion_state is CompletionState.PARTIAL_RESOURCE_PRESSURE


# ─── Review BLOCKER B: deterministic top-K ──────────────────────────────────


def test_deterministic_top_k_same_snapshot_same_candidates():
    """Same snapshot + config + budget produces same ordered candidate set."""
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(10)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(10)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(10)}
    readiness = {f"asset-{i}": f"r{i}" for i in range(10)}

    config = StageBConfig(max_candidates=3)
    funnel = CandidateFunnel(stage_b_config=config)

    run1 = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )
    run2 = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )

    assert run1.final_candidates == run2.final_candidates
    assert len(run1.final_candidates) == 3


def test_deterministic_top_k_different_budget_different_candidates():
    """Different budget produces different candidate set."""
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(10)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(10)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(10)}
    readiness = {f"asset-{i}": f"r{i}" for i in range(10)}

    config_small = StageBConfig(max_candidates=2)
    config_large = StageBConfig(max_candidates=5)
    funnel_small = CandidateFunnel(stage_b_config=config_small)
    funnel_large = CandidateFunnel(stage_b_config=config_large)

    run_small = funnel_small.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config_small),
        readiness_ids=readiness,
    )
    run_large = funnel_large.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config_large),
        readiness_ids=readiness,
    )

    assert len(run_small.final_candidates) == 2
    assert len(run_large.final_candidates) == 5
    # Small is a prefix of large (deterministic ordering)
    assert run_small.final_candidates == run_large.final_candidates[:2]


# ─── Review BLOCKER C: funnel_version in lineage ────────────────────────────


def test_funnel_version_in_run_lineage():
    """FunnelRun contains funnel_version for downstream lineage."""
    funnel = CandidateFunnel()
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(StageBConfig()),
        readiness_ids={"asset-1": "r1"},
    )

    assert run.funnel_version
    assert isinstance(run.funnel_version, str)
    assert len(run.funnel_version) == 64  # SHA-256 hex


def test_funnel_version_changes_with_config():
    """Different configs produce different funnel_version."""
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    config_a = StageAConfig(version="stage-a-v1")
    config_b = StageAConfig(version="stage-a-v2")

    funnel_a = CandidateFunnel(stage_a_config=config_a)
    funnel_b = CandidateFunnel(stage_a_config=config_b)

    run_a = funnel_a.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(StageBConfig()),
        readiness_ids={"asset-1": "r1"},
    )
    run_b = funnel_b.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(StageBConfig()),
        readiness_ids={"asset-1": "r1"},
    )

    assert run_a.funnel_version != run_b.funnel_version


def test_funnel_version_in_summary():
    """to_summary() includes funnel_version and completion_state."""
    funnel = CandidateFunnel()
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    run = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(StageBConfig()),
        readiness_ids={"asset-1": "r1"},
    )

    summary = run.to_summary()
    assert summary["funnel_version"] == run.funnel_version
    assert summary["completion_state"] == run.completion_state.value


# ─── Additional acceptance: async execution order ───────────────────────────


def test_async_execution_order_does_not_change_selection():
    """Different evaluation order produces same final candidate set."""
    instruments = [make_instrument(f"asset-{i}", f"A{i}") for i in range(5)]
    observations = {f"asset-{i}": make_observations(f"asset-{i}", f"A{i}") for i in range(5)}
    actions = {f"asset-{i}": [make_corporate_action(f"asset-{i}")] for i in range(5)}
    readiness = {f"asset-{i}": f"r{i}" for i in range(5)}

    config = StageBConfig(max_candidates=3)
    funnel = CandidateFunnel(stage_b_config=config)

    # Run 1: normal order
    run1 = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )

    # Run 2: reversed instrument order (simulates different async completion)
    reversed_instruments = list(reversed(instruments))
    run2 = funnel.run(
        "snapshot-1",
        reversed_instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )

    # Same final candidates (order may differ but set is same)
    assert set(run1.final_candidates) == set(run2.final_candidates)
    assert len(run1.final_candidates) == 3
    assert len(run2.final_candidates) == 3


# ─── Additional acceptance: Stage B agent cannot expand scope ───────────────


def test_stage_b_agent_cannot_expand_scope():
    """Stage B evaluator only receives Stage A candidates, never full universe."""
    funnel = CandidateFunnel()
    instruments = [
        make_instrument("pass-1", "PASS1"),
        make_instrument("fail-1", "FAIL1"),
    ]
    observations = {
        "pass-1": make_observations("pass-1", "PASS1"),
        "fail-1": make_observations("fail-1", "FAIL1", close=Decimal("1")),
    }
    actions = {
        "pass-1": [make_corporate_action("pass-1")],
        "fail-1": [make_corporate_action("fail-1")],
    }

    stage_a_results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"pass-1": "r1", "fail-1": "r2"},
    )

    evaluator = RecordingStageBEvaluator(StageBConfig())
    funnel.run_stage_b(stage_a_results, evaluator)

    # Only pass-1 was evaluated; fail-1 was never sent to Stage B
    assert evaluator.calls == ["pass-1"]


# ─── Review BLOCKER B: preregistered, versioned ordering key + deterministic tie-break ───


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


def test_ranking_key_is_preregistered_and_versioned():
    """The ordering key is part of the versioned Stage B config (BLOCKER B)."""
    default = StageBConfig()
    assert default.ranking_key is RankingKey.INSTRUMENT_ID_ASC
    assert default.ranking_key_version == "ranking-v1"
    ranked = StageBConfig(ranking_key=RankingKey.MOMENTUM_DESC)
    assert ranked.ranking_key_version == default.ranking_key_version
    # The ranking key changes the config hash (and therefore funnel_version).
    assert ranked.config_hash != default.config_hash
    with pytest.raises(ValueError):
        StageBConfig(ranking_key="momentum:desc")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        StageBConfig(max_rank_variants=0)


def test_cap_applied_to_preregistered_order_not_completion_order():
    """Cap selects the top-K of the preregistered order, not the arrival order."""
    n = 10
    # Vary volume so the feed-dollar-volume ranking is non-degenerate.
    instruments = [make_instrument(f"asset-{i:03d}", f"A{i}") for i in range(n)]
    observations = {}
    actions = {}
    for i in range(n):
        observations[f"asset-{i:03d}"] = make_observations(
            f"asset-{i:03d}", f"A{i}", volume=Decimal(str(20000 + i * 1000))
        )
        actions[f"asset-{i:03d}"] = [make_corporate_action(f"asset-{i:03d}")]
    readiness = {f"asset-{i:03d}": f"r{i}" for i in range(n)}

    config = StageBConfig(max_candidates=3, ranking_key=RankingKey.FEED_DOLLAR_VOLUME_20_DESC)
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

    # Highest volume first -> asset-009, asset-008, asset-007.
    assert run.final_candidates == ("asset-009", "asset-008", "asset-007")
    assert run.ranking_key == RankingKey.FEED_DOLLAR_VOLUME_20_DESC.value
    assert run.stage_a_candidate_ranks[:3] == ("asset-009", "asset-008", "asset-007")


def test_deterministic_tie_break_on_equal_metric():
    """Equal metric ranks tie-break on instrument_id ascending (BLOCKER B)."""
    n = 6
    instruments, observations, actions, readiness = _funnel_universe(n)
    config = StageBConfig(max_candidates=6, ranking_key=RankingKey.MOMENTUM_DESC)
    funnel = CandidateFunnel(stage_b_config=config)

    # Flat prices -> all momentum values equal -> pure instrument_id tie-break.
    forward = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )
    backward = funnel.run(
        "snapshot-1",
        list(reversed(instruments)),
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )

    expected = tuple(f"asset-{i:03d}" for i in range(n))
    assert forward.stage_a_candidate_ranks == expected
    assert backward.stage_a_candidate_ranks == expected
    assert forward.stage_a_candidate_ranks == backward.stage_a_candidate_ranks


def test_cap_is_never_post_hoc_from_stage_b_result():
    """Two evaluators with opposite Stage-B outcomes select the SAME candidate set.

    The cap must be decided by the Stage A ordering alone; a Stage-B score may
    not influence which instruments are admitted (BLOCKER B).
    """

    class ScoreStageBEvaluator(StageBEvaluator):
        def __init__(self, config: StageBConfig, reject: set[str]) -> None:
            super().__init__(config)
            self.reject = reject

        def evaluate_one(self, instrument_id, symbol, stage_a_evidence):
            if instrument_id in self.reject:
                return StageBResult(
                    instrument_id=instrument_id,
                    symbol=symbol,
                    passed=False,
                    rejection_reason=RejectionReason.REJECTED_RULE,
                )
            return StageBResult(
                instrument_id=instrument_id,
                symbol=symbol,
                passed=True,
                rejection_reason=None,
                score=Decimal("1"),
            )

    n = 8
    instruments, observations, actions, readiness = _funnel_universe(n)
    config = StageBConfig(max_candidates=3, ranking_key=RankingKey.FEED_DOLLAR_VOLUME_20_DESC)
    funnel = CandidateFunnel(stage_b_config=config)

    # Reject the three that would otherwise be selected.
    run_reject_top = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        ScoreStageBEvaluator(config, {"asset-000", "asset-001", "asset-002"}),
        readiness_ids=readiness,
    )
    run_reject_bottom = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        ScoreStageBEvaluator(config, {"asset-005", "asset-006", "asset-007"}),
        readiness_ids=readiness,
    )

    # Same admitted candidates regardless of Stage-B outcome; only pass flag differs.
    admitted_top = [r.instrument_id for r in run_reject_top.stage_b_results]
    admitted_bottom = [r.instrument_id for r in run_reject_bottom.stage_b_results]
    assert admitted_top == admitted_bottom
    assert run_reject_top.stage_a_candidate_ranks == run_reject_bottom.stage_a_candidate_ranks


def test_missing_ranking_metric_fails_closed():
    """A metric ranking key with absent Stage A evidence fails closed (BLOCKER B)."""
    instruments, observations, actions, readiness = _funnel_universe(2)
    # Strip the metric from Stage A evidence to simulate a partial snapshot.
    config = StageBConfig(ranking_key=RankingKey.MOMENTUM_DESC)
    funnel = CandidateFunnel(stage_b_config=config)
    stage_a_results = funnel.run_stage_a(
        "snapshot-1", instruments, observations, actions, DAYS, NOW, readiness_ids=readiness
    )
    stripped = tuple(
        replace(
            r, screening_evidence={k: v for k, v in r.screening_evidence.items() if k != "momentum"}
        )
        for r in stage_a_results
    )
    with pytest.raises(FunnelRankingError):
        funnel.run_stage_b(stripped, RecordingStageBEvaluator(config))


def test_ranking_variant_changes_funnel_version():
    """Changing only the ranking key changes funnel_version (BLOCKER C)."""
    instruments, observations, actions, readiness = _funnel_universe(3)
    config_a = StageBConfig(ranking_key=RankingKey.INSTRUMENT_ID_ASC)
    config_b = StageBConfig(ranking_key=RankingKey.TREND_DESC)
    run_a = CandidateFunnel(stage_b_config=config_a).run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config_a),
        readiness_ids=readiness,
    )
    run_b = CandidateFunnel(stage_b_config=config_b).run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config_b),
        readiness_ids=readiness,
    )
    assert run_a.funnel_version != run_b.funnel_version


def test_evaluator_cannot_change_preregistered_ranking_config():
    """A Stage-B evaluator with a different config fails closed (BLOCKER C)."""
    instruments, observations, actions, readiness = _funnel_universe(3)
    funnel = CandidateFunnel(stage_b_config=StageBConfig(ranking_key=RankingKey.MOMENTUM_DESC))
    stage_a_results = funnel.run_stage_a(
        "snapshot-1", instruments, observations, actions, DAYS, NOW, readiness_ids=readiness
    )
    rogue = RecordingStageBEvaluator(StageBConfig(ranking_key=RankingKey.TREND_DESC))
    with pytest.raises(FunnelConfigMismatch):
        funnel.run_stage_b(stage_a_results, rogue)
    assert rogue.calls == []


# ─── Stage A config bounds are enforced, not decoration ───


def test_stage_a_enforces_minimum_price_bound():
    """StageAConfig.minimum_price_usd is part of the config hash and must be enforced."""
    config = StageAConfig(minimum_price_usd=Decimal("150"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations(close=Decimal("100"))}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert not results[0].passed
    assert results[0].rejection_reason is RejectionReason.STAGE_A_FILTERED
    assert "price_too_low" in results[0].rejection_details


def test_stage_a_enforces_minimum_sessions_bound():
    """A shorter-than-configured history is rejected even if the canonical policy passes."""
    config = StageAConfig(minimum_sessions=200)
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert not results[0].passed
    assert "sessions_too_few" in results[0].rejection_details


def test_stage_a_enforces_minimum_coverage_bound():
    """A coverage bound tighter than the canonical policy is enforced."""
    config = StageAConfig(minimum_coverage=Decimal("0.999"))
    funnel = CandidateFunnel(stage_a_config=config)
    instruments = [make_instrument()]
    # Drop one mid-history session: coverage = 179/180 = 0.9944, which the canonical
    # 0.98 policy accepts but the tighter 0.999 bound must reject.
    sessions = DAYS[:50] + DAYS[51:]
    observations = {"asset-1": make_observations(sessions=sessions)}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert results[0].screening_evidence["eligible"]
    assert not results[0].passed
    assert "coverage_too_low" in results[0].rejection_details


def test_stage_a_default_bounds_do_not_reject_valid_instrument():
    """The default Stage A bounds must not reject a canonical eligible instrument."""
    funnel = CandidateFunnel()
    instruments = [make_instrument()]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}

    results = funnel.run_stage_a(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        readiness_ids={"asset-1": "r1"},
    )

    assert results[0].passed
    assert results[0].rejection_reason is None


# ─── Review BLOCKER C: candidate rank / selection reason / trial family in lineage ───


def test_candidate_lineage_contains_rank_and_selection_reason():
    """candidate_lineage() exposes rank, selection reason and completeness (BLOCKER C)."""
    n = 4
    instruments, observations, actions, readiness = _funnel_universe(n)
    config = StageBConfig(max_candidates=2, ranking_key=RankingKey.FEED_DOLLAR_VOLUME_20_DESC)
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

    lineage = run.candidate_lineage()
    assert [row["candidate_rank"] for row in lineage] == [1, 2, 3, 4]
    assert all(row["funnel_version"] == run.funnel_version for row in lineage)
    assert all(row["ranking_key"] == run.ranking_key for row in lineage)
    assert all(row["completeness"] is not None for row in lineage)
    admitted = [
        row for row in lineage if row["selection_reason"] == SelectionReason.STAGE_B_SELECTED.value
    ]
    assert len(admitted) == 2
    deferred = [row for row in lineage if row["stage_b_passed"] is False]
    assert len(deferred) == 2
    assert all(
        row["selection_reason"] == SelectionReason.STAGE_A_SELECTED.value for row in deferred
    )


def test_summary_exposes_ranking_key_and_rank_variants():
    """to_summary() exposes the ordering key, its version and trial-family size."""
    n = 3
    instruments, observations, actions, readiness = _funnel_universe(n)
    config = StageBConfig(ranking_key=RankingKey.MOMENTUM_DESC, max_rank_variants=4)
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
    summary = run.to_summary()
    assert summary["ranking_key"] == RankingKey.MOMENTUM_DESC.value
    assert summary["ranking_key_version"] == "ranking-v1"
    assert summary["rank_variants"] == 4


def test_trial_family_key_distinguishes_funnel_variants():
    """Each funnel variant is a distinct trial-family member (STAT-BLOCKER B)."""
    n = 4
    instruments, observations, actions, readiness = _funnel_universe(n)
    config_a = StageBConfig(max_candidates=2, ranking_key=RankingKey.MOMENTUM_DESC)
    config_b = StageBConfig(max_candidates=3, ranking_key=RankingKey.MOMENTUM_DESC)
    run_a = CandidateFunnel(stage_b_config=config_a).run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config_a),
        readiness_ids=readiness,
    )
    run_b = CandidateFunnel(stage_b_config=config_b).run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config_b),
        readiness_ids=readiness,
    )
    assert run_a.trial_family_key() != run_b.trial_family_key()


def test_replay_check_covers_ranking_and_candidate_ranks():
    """deterministic_replay_check() compares ordering and rank lineage, not only the set."""
    n = 5
    instruments, observations, actions, readiness = _funnel_universe(n)
    config = StageBConfig(max_candidates=2, ranking_key=RankingKey.FEED_DOLLAR_VOLUME_20_DESC)
    funnel = CandidateFunnel(stage_b_config=config)
    run1 = funnel.run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )
    run2 = funnel.run(
        "snapshot-1",
        list(reversed(instruments)),
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )
    assert deterministic_replay_check(run1, run2)
    assert run1.stage_a_candidate_ranks == run2.stage_a_candidate_ranks

    # A tampered ranking key on one run breaks replay equality.
    tampered = replace(run2, ranking_key=RankingKey.INSTRUMENT_ID_ASC.value)
    assert not deterministic_replay_check(run1, tampered)
