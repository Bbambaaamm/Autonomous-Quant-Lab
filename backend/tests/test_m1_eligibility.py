import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from quantlab.market_data import DatasetInvalid
from quantlab.persistence import Base, ExperimentRecord, Phase6EligibilityDecisionRecord
from quantlab.phase6_runtime import EligibilityPolicy, Phase6EligibilityService
from tests.test_phase6_audit_fixes import _seed


def _factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_policy_boundaries_and_deterministic_retry() -> None:
    factory = _factory()
    _seed(factory)
    service = Phase6EligibilityService(factory)
    policy = EligibilityPolicy(minimum_total_return=0.1, minimum_sharpe=1.0, maximum_drawdown=0.1)
    first = service.evaluate_eligibility(
        "e", actor={"id": "admin"}, reason="boundary evidence", policy=policy
    )
    second = service.evaluate_eligibility("e", actor={"id": "admin"}, reason="retry", policy=policy)
    assert first.decision_id == second.decision_id
    assert first.status == "ELIGIBLE"
    assert all(rule["passed"] for rule in json.loads(first.rules_json))


def test_policy_fail_and_promotion_is_fail_closed() -> None:
    factory = _factory()
    _seed(factory)
    service = Phase6EligibilityService(factory)
    decision = service.evaluate_eligibility(
        "e",
        actor={"id": "admin"},
        reason="strict policy",
        policy=EligibilityPolicy(minimum_total_return=0.2),
    )
    assert decision.status == "INELIGIBLE"
    with pytest.raises(DatasetInvalid, match="ELIGIBLE"):
        service.promote("e", actor={"id": "test"}, reason="test promotion")


def test_missing_decision_and_tampering_are_rejected() -> None:
    factory = _factory()
    _seed(factory)
    service = Phase6EligibilityService(factory)
    with pytest.raises(DatasetInvalid, match="ELIGIBLE"):
        service.promote("e", actor={"id": "test"}, reason="test promotion")
    service.evaluate_eligibility("e", actor={"id": "admin"}, reason="valid evidence")
    with factory() as session, session.begin():
        row = session.scalar(select(Phase6EligibilityDecisionRecord))
        assert row is not None
        row.integrity_hash = "0" * 64
    with pytest.raises(DatasetInvalid, match="ELIGIBLE"):
        service.promote("e", actor={"id": "test"}, reason="test promotion")


def test_seed_lineage_change_is_rejected() -> None:
    factory = _factory()
    _seed(factory)
    service = Phase6EligibilityService(factory)
    service.evaluate_eligibility("e", actor={"id": "admin"}, reason="valid evidence")
    with factory() as session, session.begin():
        experiment = session.get(ExperimentRecord, "e")
        assert experiment is not None
        experiment.seed = 43
    with pytest.raises(DatasetInvalid, match="lineage"):
        service.promote("e", actor={"id": "admin"}, reason="changed lineage")


@pytest.mark.parametrize("value", [None, float("nan"), float("inf")])
def test_missing_or_invalid_metric_fails_closed(value: float | None) -> None:
    factory = _factory()
    _seed(factory)
    with factory() as session, session.begin():
        experiment = session.get(ExperimentRecord, "e")
        assert experiment is not None
        experiment.sharpe = value
    with pytest.raises(DatasetInvalid, match="úplné konečné"):
        Phase6EligibilityService(factory).evaluate_eligibility(
            "e", actor={"id": "admin"}, reason="invalid metric"
        )


def test_policy_version_and_threshold_change_identity() -> None:
    factory_one = _factory()
    _seed(factory_one)
    first = Phase6EligibilityService(factory_one).evaluate_eligibility(
        "e", actor={"id": "admin"}, reason="policy v1"
    )
    factory_two = _factory()
    _seed(factory_two)
    second = Phase6EligibilityService(factory_two).evaluate_eligibility(
        "e",
        actor={"id": "admin"},
        reason="policy v2",
        policy=EligibilityPolicy(version=2, minimum_total_return=0.05),
    )
    assert first.decision_id != second.decision_id
    assert json.loads(second.policy_json)["version"] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": 0},
        {"maximum_drawdown": -0.1},
        {"minimum_sharpe": float("nan")},
    ],
)
def test_invalid_policy_fails_closed(kwargs: dict[str, int | float]) -> None:
    with pytest.raises(ValueError, match="policy"):
        EligibilityPolicy(**kwargs)
