"""Kanonická, neměnná ekonomická identita paper deploymentu."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from quantlab.phase4 import ProductionRiskConfig
from quantlab.trading import CostModel, FixedBpsSlippage

RUNTIME_MANIFEST_VERSION = 1


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def manifest_hash(manifest: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode()).hexdigest()


def build_runtime_manifest(
    *,
    risk: ProductionRiskConfig | None = None,
    costs: CostModel | None = None,
    slippage: FixedBpsSlippage | None = None,
    volume_fraction: Decimal = Decimal("0.10"),
    code_sha: str | None = None,
) -> dict[str, object]:
    """Sestaví pouze allowlisted ekonomickou konfiguraci, nikdy environment dump."""
    risk = risk or ProductionRiskConfig()
    costs = costs or CostModel()
    slippage = slippage or FixedBpsSlippage()
    return {
        "runtime_manifest_version": RUNTIME_MANIFEST_VERSION,
        "risk": {
            "model": "production-risk",
            "version": 1,
            "max_position_pct": str(risk.max_position_pct),
            "max_single_order_pct": str(risk.max_single_order_pct),
            "max_single_order_notional": str(risk.max_single_order_notional),
            "max_gross_exposure": str(risk.max_gross_exposure),
            "max_net_exposure": str(risk.max_net_exposure),
            "max_number_positions": risk.max_number_positions,
            "max_daily_loss": str(risk.max_daily_loss),
            "max_portfolio_drawdown": str(risk.max_portfolio_drawdown),
            "max_orders_per_day": risk.max_orders_per_day,
            "max_notional_per_day": str(risk.max_notional_per_day),
            "instrument_allowlist": sorted(risk.instrument_allowlist),
            "long_only": risk.long_only,
            "max_leverage": str(risk.max_leverage),
            "stale_data_threshold_seconds": int(risk.stale_data_threshold.total_seconds()),
            "clip_quantity": risk.clip_quantity,
        },
        "portfolio": {
            "model": "equity-target-weight",
            "version": 1,
            "valuation_price": "account_equity_at_cycle_start",
            "target_quantity": "floor(equity*weight/raw_executable_open)",
            "pending_orders": "subtract_signed_remaining_quantity",
            "rebalance": "strategy_rebalance_frequency",
            "cash_allocation": "long_only_cash_constrained",
            "quantity_scale": 8,
            "rounding": "ROUND_DOWN",
        },
        "commission": {
            "model": "fixed-plus-notional-rate",
            "version": 1,
            "fixed": str(costs.fixed),
            "rate": str(costs.rate),
            "minimum": str(costs.minimum),
        },
        "slippage": {
            "model": "fixed-bps-directional",
            "version": 1,
            "basis_points": str(slippage.basis_points),
        },
        "execution": {
            "model": "persistent-paper-next-bar",
            "version": 1,
            "broker": "PersistentPaperBroker",
            "broker_version": 1,
            "order_type": "MARKET",
            "market_reference_price": "raw_executable_open",
            "limit_reference_price": "price-improved-open-or-limit",
            "partial_fill": "floor(bar_volume*volume_fraction)",
            "volume_fraction": str(volume_fraction),
            "lot_accounting": "FIFO",
            "quantity_rounding": "ROUND_DOWN",
        },
        "artifact": {"experiment_code_sha": code_sha},
    }


@dataclass(frozen=True)
class RuntimeComponents:
    risk: ProductionRiskConfig
    costs: CostModel
    slippage: FixedBpsSlippage
    volume_fraction: Decimal


def components_from_manifest(manifest: dict[str, Any]) -> RuntimeComponents:
    """Validuje podporované schema a rekonstruuje přesně schválené komponenty."""
    if manifest.get("runtime_manifest_version") != RUNTIME_MANIFEST_VERSION:
        raise ValueError("RUNTIME_CONFIG_SCHEMA_UNSUPPORTED")
    risk = manifest.get("risk")
    portfolio = manifest.get("portfolio")
    commission = manifest.get("commission")
    slippage = manifest.get("slippage")
    execution = manifest.get("execution")
    if not all(
        isinstance(item, dict) for item in (risk, portfolio, commission, slippage, execution)
    ):
        raise ValueError("RUNTIME_CONFIG_INVALID")
    assert isinstance(risk, dict) and isinstance(portfolio, dict)
    assert isinstance(commission, dict) and isinstance(slippage, dict)
    assert isinstance(execution, dict)
    expected = build_runtime_manifest(
        code_sha=(manifest.get("artifact") or {}).get("experiment_code_sha")
    )
    for section, identity_keys in {
        "portfolio": (
            "model",
            "version",
            "valuation_price",
            "target_quantity",
            "pending_orders",
            "rebalance",
            "cash_allocation",
            "quantity_scale",
            "rounding",
        ),
        "execution": (
            "model",
            "version",
            "broker",
            "broker_version",
            "order_type",
            "market_reference_price",
            "limit_reference_price",
            "partial_fill",
            "lot_accounting",
            "quantity_rounding",
        ),
    }.items():
        if any(manifest[section].get(key) != expected[section][key] for key in identity_keys):  # type: ignore[index]
            raise ValueError("RUNTIME_CONFIG_MODEL_UNSUPPORTED")
    if risk.get("model") != "production-risk" or risk.get("version") != 1:
        raise ValueError("RUNTIME_CONFIG_MODEL_UNSUPPORTED")
    if commission.get("model") != "fixed-plus-notional-rate" or commission.get("version") != 1:
        raise ValueError("RUNTIME_CONFIG_MODEL_UNSUPPORTED")
    if slippage.get("model") != "fixed-bps-directional" or slippage.get("version") != 1:
        raise ValueError("RUNTIME_CONFIG_MODEL_UNSUPPORTED")
    return RuntimeComponents(
        ProductionRiskConfig(
            max_position_pct=Decimal(risk["max_position_pct"]),
            max_single_order_pct=Decimal(risk["max_single_order_pct"]),
            max_single_order_notional=Decimal(risk["max_single_order_notional"]),
            max_gross_exposure=Decimal(risk["max_gross_exposure"]),
            max_net_exposure=Decimal(risk["max_net_exposure"]),
            max_number_positions=int(risk["max_number_positions"]),
            max_daily_loss=Decimal(risk["max_daily_loss"]),
            max_portfolio_drawdown=Decimal(risk["max_portfolio_drawdown"]),
            max_orders_per_day=int(risk["max_orders_per_day"]),
            max_notional_per_day=Decimal(risk["max_notional_per_day"]),
            instrument_allowlist=frozenset(risk["instrument_allowlist"]),
            long_only=bool(risk["long_only"]),
            max_leverage=Decimal(risk["max_leverage"]),
            stale_data_threshold=timedelta(seconds=int(risk["stale_data_threshold_seconds"])),
            clip_quantity=bool(risk["clip_quantity"]),
        ),
        CostModel(
            Decimal(commission["fixed"]),
            Decimal(commission["rate"]),
            Decimal(commission["minimum"]),
        ),
        FixedBpsSlippage(Decimal(slippage["basis_points"])),
        Decimal(execution["volume_fraction"]),
    )
