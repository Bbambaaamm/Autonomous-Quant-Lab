from copy import deepcopy
from decimal import Decimal

from quantlab.domain import Side
from quantlab.phase4 import ProductionRiskConfig
from quantlab.runtime_identity import (
    build_runtime_manifest,
    canonical_json,
    components_from_manifest,
    manifest_hash,
)
from quantlab.trading import CostModel, FixedBpsSlippage


def test_manifest_hash_is_canonical_and_order_independent() -> None:
    manifest = build_runtime_manifest(code_sha="a" * 40)
    reordered = dict(reversed(list(manifest.items())))
    assert canonical_json(manifest) == canonical_json(reordered)
    assert manifest_hash(manifest) == manifest_hash(reordered)


def test_economic_change_changes_manifest_and_deployment_input_identity() -> None:
    first = build_runtime_manifest()
    second = build_runtime_manifest(risk=ProductionRiskConfig(max_position_pct=Decimal("0.20")))
    assert manifest_hash(first) != manifest_hash(second)


def test_manifest_reconstructs_risk_commission_slippage_and_sizing() -> None:
    manifest = build_runtime_manifest()
    components = components_from_manifest(manifest)
    assert components.risk.max_position_pct == Decimal("0.25")
    assert components.costs.rate == Decimal("0.001")
    assert components.slippage.basis_points == Decimal("5")
    assert components.volume_fraction == Decimal("0.10")
    assert manifest["portfolio"]["rounding"] == "ROUND_DOWN"  # type: ignore[index]


def test_unsupported_execution_semantics_fail_closed() -> None:
    manifest = deepcopy(build_runtime_manifest())
    manifest["execution"]["market_reference_price"] = "mutable-close"  # type: ignore[index]
    try:
        components_from_manifest(manifest)
    except ValueError as exc:
        assert str(exc) == "RUNTIME_CONFIG_MODEL_UNSUPPORTED"
    else:
        raise AssertionError("Nepodporovaný execution model nesmí být přijat")


def test_approved_components_ignore_later_risk_commission_and_slippage_defaults() -> None:
    approved = build_runtime_manifest(
        risk=ProductionRiskConfig(max_position_pct=Decimal("0.20")),
        costs=CostModel(rate=Decimal("0.002")),
        slippage=FixedBpsSlippage(Decimal("7")),
    )
    # Simuluje pozdější procesní defaulty; runtime se vždy rekonstruuje z approved JSON.
    build_runtime_manifest(
        risk=ProductionRiskConfig(max_position_pct=Decimal("0.05")),
        costs=CostModel(rate=Decimal("0.01")),
        slippage=FixedBpsSlippage(Decimal("50")),
    )
    components = components_from_manifest(approved)
    assert components.risk.max_position_pct == Decimal("0.20")
    assert components.costs.commission(Decimal("1000")) == Decimal("3.000")
    assert components.slippage.apply(Decimal("100"), Side.BUY) == Decimal("100.0700")
