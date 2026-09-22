"""Price acquisition must survive an action outage without granting eligibility."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_market_pipeline import END, NOW, START, Provider, assets

from quantlab.asset_directory import AssetDirectoryService
from quantlab.market_data import (
    DatasetInvalid,
    ProviderRateLimited,
    ProviderUnavailable,
    XNYSCalendar,
)
from quantlab.market_pipeline import MarketPipeline, MarketTask, ReceivedData, _PriceOnlyReceipt
from quantlab.market_screening import MarketScreening, MarketScreenItem, MarketScreenRun
from quantlab.persistence import (
    CorporateActionReadinessRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
)
from quantlab.phase4 import Phase4Repository


@pytest.fixture
def env(tmp_path):
    repository = Phase4Repository(f"sqlite:///{tmp_path / 'receipts.db'}")
    factory = sessionmaker(repository.engine)
    snapshot = AssetDirectoryService(factory).sync(
        assets(), actor="test", reason="Receipt regression", received_at=NOW
    )
    pipeline = MarketPipeline(factory)
    batch = pipeline.create(snapshot, START, END, "alpaca:iex", "test", "Receipt batch", NOW)
    return factory, pipeline, batch


class MissingEvidence(Provider):
    def corporate_actions(self, symbol, start, end):
        raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")


def test_missing_evidence_preserves_prices_and_failed_readiness(env):
    factory, pipeline, _ = env
    calls = []

    class Checked(MissingEvidence):
        def corporate_actions(self, symbol, start, end):
            with factory() as session:
                assert session.scalar(select(func.count()).select_from(MarketObservationRecord)) > 0
            calls.append((symbol, start, end))
            return super().corporate_actions(symbol, start, end)

    pipeline.step(lambda _: Checked(), clock=lambda: NOW)
    report = pipeline.read()
    assert report["counts"] == {"DATA_BLOCKED": 1, "PENDING": 1}
    assert report["total"] == 2
    assert report["coverage_summary"]["downloaded"] == 1
    assert report["coverage_summary"]["complete_period"] == 1
    item = next(row for row in report["items"] if row["state"] == "DATA_BLOCKED")
    assert item["bars"] == len(XNYSCalendar().sessions_between(START, END))
    assert item["coverage"] == Decimal(1)
    assert all(item[key] is None for key in ("momentum", "trend", "mean_reversion"))
    assert len(calls) == 1
    assert calls[0][1:] == (START, END)
    with factory() as session:
        ingestion = session.scalar(select(MarketDataIngestionRecord))
        assert ingestion.status == "SUCCEEDED"
        readiness = session.scalar(select(CorporateActionReadinessRecord))
        assert readiness.status == "FAILED"
        assert readiness.blocking_reason == "CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE"
        task = session.scalar(select(MarketTask).where(MarketTask.state == "DATA_BLOCKED"))
        evidence = json.loads(task.evidence_json)
        assert evidence["eligible_for_promotion"] is False
        assert evidence["screening"]["eligible"] is False
        assert evidence["screening"]["research_eligible"] is False
        assert evidence["screening"]["action_readiness_id"] is None
        assert "ACTIONS_NOT_VERIFIED" in evidence["screening"]["reasons"]
        instrument_id = task.instrument_id
    before = pipeline.screen(instrument_id, "alpaca:iex", START, END, NOW - timedelta(seconds=1))
    assert before["bars"] == 0
    assert before["screening"]["eligible"] is False


@pytest.mark.parametrize(
    "error,state",
    [
        (ProviderUnavailable("offline"), "RETRY"),
        (ProviderRateLimited(30), "RETRY"),
        (DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE"), "DATA_BLOCKED"),
    ],
)
def test_action_failure_is_not_a_price_failure(env, error, state):
    factory, pipeline, _ = env

    class FailedActions(Provider):
        def corporate_actions(self, symbol, start, end):
            raise error

    pipeline.step(lambda _: FailedActions(), clock=lambda: NOW)
    report = pipeline.read()
    assert report["counts"] == {state: 1, "PENDING": 1}
    assert report["coverage_summary"]["downloaded"] == 1
    with factory() as session:
        assert session.scalar(select(CorporateActionReadinessRecord)).status == "FAILED"
        assert session.scalar(select(MarketDataIngestionRecord)).status == "SUCCEEDED"


def test_unsupported_actions_are_not_an_empty_success(env):
    factory, pipeline, _ = env

    class Unsupported(Provider):
        metadata = replace(Provider.metadata, supports_actions=False)

        def corporate_actions(self, symbol, start, end):
            pytest.fail("An unsupported action source must not be queried")

    pipeline.step(lambda _: Unsupported(), clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"DATA_BLOCKED": 1, "PENDING": 1}
    assert pipeline.read()["coverage_summary"]["downloaded"] == 1
    with factory() as session:
        assert session.scalar(select(CorporateActionReadinessRecord)).status == "UNSUPPORTED"


def test_price_receipt_cannot_claim_action_completeness():
    provider = MissingEvidence()
    received = ReceivedData(provider, "TESTA", START, END)
    assert received.actions is None
    receipt = _PriceOnlyReceipt(received)
    assert receipt.metadata.supports_actions is False
    assert provider.metadata.supports_actions is True
    assert receipt.metadata.persistent_name == provider.metadata.persistent_name
    assert receipt.metadata.version == provider.metadata.version
    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTIONS_UNSUPPORTED$"):
        receipt.corporate_actions("TESTA", START, END)
    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE$"):
        received.corporate_actions("TESTA", START, END)
    assert received.actions is None


@pytest.mark.parametrize("invalid_price", [False, True])
def test_bad_prices_do_not_create_a_receipt_or_call_actions(env, invalid_price):
    factory, pipeline, _ = env

    class BadPrices(Provider):
        def historical_daily(self, symbol, start, end):
            if not invalid_price:
                raise ProviderUnavailable("price endpoint offline")
            bars = super().historical_daily(symbol, start, end)
            return [replace(bars[0], open=Decimal(-1))]

        def corporate_actions(self, symbol, start, end):
            pytest.fail("Action verification must follow a successful price ingest")

    pipeline.step(lambda _: BadPrices(), clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"RETRY": 1, "PENDING": 1}
    assert pipeline.read()["coverage_summary"]["downloaded"] == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MarketObservationRecord)) == 0
        assert session.scalar(select(func.count()).select_from(CorporateActionReadinessRecord)) == 0


def test_action_retry_uses_price_checkpoint_without_duplicate_observations(env):
    factory, pipeline, _ = env
    requested = []
    action_ranges = []

    class Transient(Provider):
        def corporate_actions(self, symbol, start, end):
            raise ProviderUnavailable("temporary action outage")

    for _ in range(2):
        pipeline.step(lambda _: Transient(), clock=lambda: NOW)
    assert pipeline.read()["counts"] == {"RETRY": 2}
    with factory() as session:
        original_ids = set(session.scalars(select(MarketObservationRecord.observation_id)))
    assert len(original_ids) == 2 * len(XNYSCalendar().sessions_between(START, END))

    class Recovered(Provider):
        def historical_daily(self, symbol, start, end):
            requested.append((start, end))
            return super().historical_daily(symbol, start, end)

        def corporate_actions(self, symbol, start, end):
            action_ranges.append((start, end))
            return []

    resumed = MarketPipeline(factory)
    later = NOW + timedelta(minutes=6)
    for _ in range(2):
        resumed.step(lambda _: Recovered(), clock=lambda: later)
    overlap_start = XNYSCalendar().sessions_between(START, END)[-5]
    assert requested == [(overlap_start, END)] * 2
    assert action_ranges == [(START, END)] * 2
    assert resumed.read()["counts"] == {"DONE": 2}
    assert resumed.read()["coverage_summary"]["downloaded"] == 2
    with factory() as session:
        assert set(session.scalars(select(MarketObservationRecord.observation_id))) == original_ids
        assert set(session.scalars(select(MarketObservationRecord.revision))) == {1}
        statuses = list(session.scalars(select(CorporateActionReadinessRecord.status)))
        assert statuses.count("FAILED") == 2
        assert statuses.count("COMPLETE") == 2


def test_finished_price_only_batch_keeps_every_member_ineligible(env):
    factory, pipeline, batch = env
    for _ in range(2):
        pipeline.step(lambda _: MissingEvidence(), clock=lambda: NOW)
    assert pipeline.read()["coverage_summary"]["downloaded"] == 2
    assert pipeline.read()["counts"] == {"DATA_BLOCKED": 2}
    run_id = MarketScreening(factory).finalize(batch, NOW)
    assert run_id is not None
    with factory() as session:
        run = session.get(MarketScreenRun, run_id)
        assert run.total == 2
        assert run.eligible == 0
        items = list(
            session.scalars(select(MarketScreenItem).where(MarketScreenItem.run_id == run_id))
        )
        assert len(items) == 2
        assert all(item.eligible == 0 for item in items)
