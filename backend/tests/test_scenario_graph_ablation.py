"""Tests for the scenario-graph incremental-value ablation harness (issue #271).

Covers Phase 3 (shadow forecast contribution) and Phase 4 (incremental-value
experiment) plus every statistical blocker recorded on issue #271:

- STAT-BLOCKER A — typed score vs calibrated probability
- STAT-BLOCKER B — simulation replicates are not real-world observations
- STAT-BLOCKER C — trial-family accounting retains losing variants
- STAT-BLOCKER D — a burned holdout may not carry an incremental-value claim
- STAT-BLOCKER E — paired ablation on identical opportunities with clustered CI
- STAT-BLOCKER F — discovery evaluated separately from weighting
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.scenario_graph_ablation import (
    DEFAULT_BOOTSTRAP_REPLICATES,
    MAX_BOOTSTRAP_REPLICATES,
    MIN_CLUSTERS_FOR_PROMOTION,
    AblationError,
    AblationVerdict,
    ForecastOpportunity,
    InsufficientEvidenceError,
    ScenarioAblationHarness,
    ScenarioVariant,
    TrialFamilyEntry,
    TrialFamilyLedger,
    build_scenario_modifier,
    result_to_dict,
)

_BASE_TIME = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _variant(variant_id: str = "v1") -> ScenarioVariant:
    return ScenarioVariant(
        variant_id=variant_id,
        prompt_hash="p",
        model_id="rule-based:scenario-graph",
        graph_policy_hash="g",
        agent_population_hash="a",
        aggregation_rule="mean-strength",
        sampling_policy="deterministic",
    )


def _family(family_id: str = "fam-271") -> TrialFamilyLedger:
    return TrialFamilyLedger(
        family_id=family_id,
        target_spec_hash="target-1",
        funnel_version="funnel-1",
        cost_model_id="cost-1",
    )


def _opportunity(
    index: int,
    *,
    cluster_id: str | None = None,
    baseline: Decimal = Decimal("0.5"),
    scenario: Decimal | None = Decimal("0.9"),
    outcome: int = 1,
    event_class: str = "EARNINGS",
    regime: str = "CALM",
    forecast_ref: str | None = "fc-1",
    calibration_report_ref: str | None = "cal-1",
    scenario_run_id: str | None = "run-1",
    runtime_ms: int = 3,
) -> ForecastOpportunity:
    return ForecastOpportunity(
        opportunity_id=f"opp-{index:04d}",
        decision_time=_BASE_TIME + timedelta(days=index),
        cluster_id=cluster_id or f"cluster-{index:04d}",
        event_class=event_class,
        regime=regime,
        baseline_probability=baseline,
        scenario_score=scenario,
        outcome=outcome,
        forecast_ref=forecast_ref,
        calibration_report_ref=calibration_report_ref,
        scenario_run_id=scenario_run_id,
        scenario_runtime_ms=runtime_ms,
    )


def _well_powered(
    n: int = MIN_CLUSTERS_FOR_PROMOTION,
    **kwargs: object,
) -> list[ForecastOpportunity]:
    return [_opportunity(i, **kwargs) for i in range(n)]  # type: ignore[arg-type]


class TestOpportunityValidation:
    def test_naive_decision_time_rejected(self) -> None:
        with pytest.raises(AblationError, match="timezone-aware"):
            ForecastOpportunity(
                opportunity_id="o1",
                decision_time=datetime(2026, 6, 1, 12, 0),  # naive
                cluster_id="c1",
                event_class="E",
                regime="R",
                baseline_probability=Decimal("0.5"),
                scenario_score=Decimal("0.6"),
                outcome=1,
            )

    def test_non_binary_outcome_rejected(self) -> None:
        with pytest.raises(AblationError, match="outcome must be 0 or 1"):
            _opportunity(1, outcome=2)

    def test_out_of_range_probability_rejected(self) -> None:
        with pytest.raises(AblationError, match="baseline_probability must be in"):
            _opportunity(1, baseline=Decimal("1.5"))

    def test_negative_runtime_rejected(self) -> None:
        with pytest.raises(AblationError, match="scenario_runtime_ms must be non-negative"):
            _opportunity(1, runtime_ms=-1)

    def test_abstention_without_scenario_score_is_allowed(self) -> None:
        opp = _opportunity(1, scenario=None)
        assert opp.scenario_score is None


class TestScenarioModifier:
    """STAT-BLOCKER A: an uncalibrated score enters only as a bounded modifier."""

    def test_shift_is_capped(self) -> None:
        modified = build_scenario_modifier(Decimal("0.5"), Decimal("1.0"), Decimal("0.1"))
        assert modified == Decimal("0.6")

    def test_downward_shift_is_capped(self) -> None:
        modified = build_scenario_modifier(Decimal("0.5"), Decimal("0.0"), Decimal("0.1"))
        assert modified == Decimal("0.4")

    def test_shift_is_clamped_into_unit_interval(self) -> None:
        modified = build_scenario_modifier(Decimal("0.95"), Decimal("1.0"), Decimal("0.5"))
        assert Decimal("0") < modified < Decimal("1")

    def test_max_shift_above_half_rejected(self) -> None:
        with pytest.raises(AblationError, match="max_shift must be in"):
            build_scenario_modifier(Decimal("0.5"), Decimal("0.6"), Decimal("0.6"))

    def test_zero_shift_is_identity(self) -> None:
        assert build_scenario_modifier(Decimal("0.42"), Decimal("0.9"), Decimal("0")) == Decimal(
            "0.42"
        )


class TestTrialFamilyAccounting:
    """STAT-BLOCKER C/D: every variant is retained; a burned holdout is flagged."""

    def test_losing_variants_are_retained(self) -> None:
        family = _family()
        family.declare(TrialFamilyEntry(_variant("winner"), True, Decimal("0.01"), True, False))
        family.declare(TrialFamilyEntry(_variant("loser"), True, Decimal("-0.02"), False, False))
        assert family.variant_count == 2
        assert family.evaluated_count == 2
        assert family.contamination_flag() is False

    def test_duplicate_variant_rejected(self) -> None:
        family = _family()
        family.declare(TrialFamilyEntry(_variant("v1"), True, Decimal("0"), False, False))
        with pytest.raises(AblationError, match="already declared"):
            family.declare(TrialFamilyEntry(_variant("v1"), True, Decimal("0"), False, False))

    def test_contaminated_holdout_flags_family(self) -> None:
        family = _family()
        family.declare(TrialFamilyEntry(_variant("v1"), True, Decimal("0.05"), True, True))
        assert family.contamination_flag() is True


class TestPromotionGate:
    """A promotion recommendation arises only on proven, admissible evidence."""

    def test_promote_on_clean_well_powered_experiment(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.PROMOTE
        assert result.may_promote is True
        assert result.paired.significant_improvement is True
        assert result.paired.brier_delta > Decimal("0")
        assert result.paired.n_clusters == MIN_CLUSTERS_FOR_PROMOTION
        assert result.blockers == ()

    def test_reject_when_improvement_is_not_significant(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        # scenario_score == baseline ⇒ no shift ⇒ zero delta ⇒ not significant.
        opportunities = _well_powered(baseline=Decimal("0.5"), scenario=Decimal("0.5"))
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.REJECT
        assert result.may_promote is False
        assert result.blockers == ()
        assert any("not significant" in w for w in result.warnings)

    def test_reject_on_noisy_signal_with_ci_spanning_zero(self) -> None:
        """A realistic noisy set must not produce a PROMOTE: the clustered CI spans 0."""
        import random

        rng = random.Random(42)
        opportunities = []
        for i in range(120):
            base = Decimal(str(round(rng.uniform(0.2, 0.8), 3)))
            score = Decimal(
                str(round(min(0.99, max(0.01, float(base) + rng.gauss(0.05, 0.15))), 3))
            )
            outcome = 1 if rng.random() < float(base) + 0.05 else 0
            opportunities.append(
                ForecastOpportunity(
                    opportunity_id=f"o{i}",
                    decision_time=_BASE_TIME + timedelta(days=i),
                    cluster_id=f"day-{i // 3}",
                    event_class="EARNINGS",
                    regime="CALM" if i % 2 else "STRESS",
                    baseline_probability=base,
                    scenario_score=score,
                    outcome=outcome,
                    forecast_ref="fc",
                    calibration_report_ref="cal",
                    scenario_run_id="run",
                    scenario_runtime_ms=4,
                )
            )
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.paired.n_clusters == 40
        assert result.paired.ci_low < Decimal("0") < result.paired.ci_high
        assert result.paired.significant_improvement is False
        assert result.verdict is AblationVerdict.REJECT
        assert result.may_promote is False

    def test_insufficient_when_temporal_knowledge_uncontrolled(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=False,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert any("UNCONTROLLED" in b for b in result.blockers)

    def test_insufficient_when_not_fully_reproducible(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=True,
            fully_reproducible=False,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert any("not fully reproducible" in b for b in result.blockers)

    def test_insufficient_when_holdout_contaminated(self) -> None:
        family = _family()
        family.declare(TrialFamilyEntry(_variant("v1"), True, Decimal("0.05"), True, True))
        harness = ScenarioAblationHarness(family=family)
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert result.holdout_contaminated is True
        assert any("contaminated" in b for b in result.blockers)

    def test_insufficient_with_too_few_independent_clusters(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        # Many rows, but all in ONE cluster: effective sample size is 1.
        opportunities = _well_powered(n=200, cluster_id="one-cluster")
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert result.paired.n_clusters == 1
        assert any("independent real-world clusters" in b for b in result.blockers)

    def test_insufficient_without_forecast_ledger_lineage(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        opportunities = _well_powered(forecast_ref=None)
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert any("#266" in b for b in result.blockers)

    def test_insufficient_without_calibration_lineage(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        opportunities = _well_powered(calibration_report_ref=None)
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert any("#269" in b for b in result.blockers)

    def test_insufficient_when_discovery_failed(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        # A second event class exists but produced no scenario run ⇒ discovery failure.
        opportunities = _well_powered() + [
            _opportunity(999, event_class="MACRO", scenario=None, scenario_run_id=None)
        ]
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.verdict is AblationVerdict.INSUFFICIENT
        assert result.discovery.discovery_failed_classes == ("MACRO",)
        assert any("discovery failed" in b for b in result.blockers)

    def test_ablation_id_is_stable_for_identical_inputs(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        kwargs = {"temporal_knowledge_controlled": True, "fully_reproducible": True}
        first = harness.evaluate(_well_powered(), **kwargs)  # type: ignore[arg-type]
        second = harness.evaluate(_well_powered(), **kwargs)  # type: ignore[arg-type]
        assert first.ablation_id == second.ablation_id


class TestPairedAblation:
    """STAT-BLOCKER E: paired on identical opportunities, clustered uncertainty."""

    def test_ablation_is_paired_on_the_same_opportunities(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(n=40),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.paired.n_opportunities == 40
        assert result.paired.n_clusters == 40

    def test_clustered_ci_is_deterministic(self) -> None:
        harness = ScenarioAblationHarness(family=_family(), seed=7)
        kwargs = {"temporal_knowledge_controlled": True, "fully_reproducible": True}
        a = harness.evaluate(_well_powered(), **kwargs)  # type: ignore[arg-type]
        b = harness.evaluate(_well_powered(), **kwargs)  # type: ignore[arg-type]
        assert a.paired.ci_low == b.paired.ci_low
        assert a.paired.ci_high == b.paired.ci_high

    def test_cluster_count_drives_uncertainty_not_row_count(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        # 40 rows across 2 clusters.
        opportunities = [_opportunity(i, cluster_id=f"c{i % 2}") for i in range(40)]
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.paired.n_opportunities == 40
        assert result.paired.n_clusters == 2
        assert result.paired.bootstrap_replicates == DEFAULT_BOOTSTRAP_REPLICATES

    def test_direction_inconsistency_is_reported_as_a_warning(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        # Half the event classes improve, half degrade.
        opportunities = [
            _opportunity(i, event_class="A", scenario=Decimal("0.9"), outcome=1)
            for i in range(MIN_CLUSTERS_FOR_PROMOTION)
        ] + [
            _opportunity(
                1000 + i,
                event_class="B",
                baseline=Decimal("0.9"),
                scenario=Decimal("0.1"),
                outcome=1,
            )
            for i in range(MIN_CLUSTERS_FOR_PROMOTION)
        ]
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.paired.direction_consistent is False
        assert any("inconsistent" in w for w in result.warnings)

    def test_brier_and_logloss_deltas_are_both_reported(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.paired.brier_delta > Decimal("0")
        assert result.paired.logloss_delta > Decimal("0")
        assert result.paired.brier_baseline > result.paired.brier_scenario


class TestEffectiveSampleSize:
    """STAT-BLOCKER B: repeated simulation seeds are not market observations."""

    def test_simulation_replicates_do_not_change_the_effective_sample_size(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        kwargs = {"temporal_knowledge_controlled": True, "fully_reproducible": True}
        single = harness.evaluate(_well_powered(), simulation_replicates=1, **kwargs)  # type: ignore[arg-type]
        many = harness.evaluate(_well_powered(), simulation_replicates=20, **kwargs)  # type: ignore[arg-type]
        assert single.paired.n_clusters == many.paired.n_clusters
        assert single.paired.brier_delta == many.paired.brier_delta

    def test_zero_replicates_rejected(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        with pytest.raises(AblationError, match="simulation_replicates must be positive"):
            harness.evaluate(
                _well_powered(),
                temporal_knowledge_controlled=True,
                fully_reproducible=True,
                simulation_replicates=0,
            )


class TestDiscoveryVsWeighting:
    """STAT-BLOCKER F."""

    def test_discovery_coverage_is_reported_separately(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        opportunities = _well_powered(event_class="A") + [
            _opportunity(5000 + i, event_class="B") for i in range(5)
        ]
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.discovery.discovery_coverage == Decimal("1")
        assert result.discovery.discovery_failed_classes == ()

    def test_discovery_coverage_counts_abstaining_classes(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        opportunities = _well_powered() + [
            _opportunity(900, event_class="MACRO", scenario=None, scenario_run_id=None)
        ]
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.discovery.discovery_coverage == Decimal("0.5")
        assert result.discovery.event_classes_evaluated == ("EARNINGS", "MACRO")

    def test_abstentions_stay_in_the_coverage_denominator(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        opportunities = _well_powered(n=40) + [
            _opportunity(900 + i, scenario=None) for i in range(10)
        ]
        result = harness.evaluate(
            opportunities,
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.coverage_rate == Decimal("40") / Decimal("50")


class TestCostReporting:
    def test_measured_cost_is_summed_from_opportunities(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(n=40, runtime_ms=5),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.cost.measured is True
        assert result.cost.total_runtime_ms == 40 * 5
        assert result.cost.mean_runtime_ms == Decimal("5")

    def test_unmeasured_cost_is_flagged(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(n=40, runtime_ms=0),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.cost.measured is False
        assert any("not measured" in w for w in result.warnings)

    def test_token_and_model_calls_are_reported(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(n=40),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        assert result.cost.total_tokens == 0
        assert result.cost.total_model_calls == 0


class TestHarnessBounds:
    def test_empty_opportunities_rejected(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        with pytest.raises(InsufficientEvidenceError, match="no opportunities"):
            harness.evaluate(
                [],
                temporal_knowledge_controlled=True,
                fully_reproducible=True,
            )

    def test_all_abstentions_rejected(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        with pytest.raises(InsufficientEvidenceError, match="no scenario-scored"):
            harness.evaluate(
                [_opportunity(1, scenario=None)],
                temporal_knowledge_controlled=True,
                fully_reproducible=True,
            )

    def test_bootstrap_cap_enforced(self) -> None:
        with pytest.raises(AblationError, match="exceeds cap"):
            ScenarioAblationHarness(
                family=_family(), bootstrap_replicates=MAX_BOOTSTRAP_REPLICATES + 1
            )

    def test_zero_bootstrap_rejected(self) -> None:
        with pytest.raises(AblationError, match="must be positive"):
            ScenarioAblationHarness(family=_family(), bootstrap_replicates=0)

    def test_invalid_ci_level_rejected(self) -> None:
        with pytest.raises(AblationError, match="ci_level must be in"):
            ScenarioAblationHarness(family=_family(), ci_level=Decimal("0.4"))


class TestShadowOnlyHarness:
    """The harness has no execution authority and no I/O."""

    def test_result_contains_no_order_fields(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        data = result_to_dict(result)
        for forbidden in ("order", "side", "quantity", "broker", "execution"):
            assert forbidden not in data

    def test_harness_exposes_no_execution_method(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        for name in dir(harness):
            lowered = name.lower()
            for forbidden in ("order", "execute", "broker", "submit", "trade"):
                assert forbidden not in lowered, f"harness exposes {name}"

    def test_module_has_no_io_or_network_imports(self) -> None:
        module_path = (
            Path(__file__).resolve().parents[1] / "src" / "quantlab" / "scenario_graph_ablation.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        forbidden = {
            "socket",
            "subprocess",
            "requests",
            "httpx",
            "urllib",
            "os",
            "http",
            "shutil",
            "openai",
            "anthropic",
            "mirofish",
            "zep",
        }
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden)

    def test_module_has_no_io_calls(self) -> None:
        module_path = (
            Path(__file__).resolve().parents[1] / "src" / "quantlab" / "scenario_graph_ablation.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        io_names = {"open", "read", "write", "input", "print", "exec", "eval"}
        offenders = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in io_names
        ]
        assert offenders == []


class TestResultSerialization:
    def test_result_serializes_with_all_evidence_fields(self) -> None:
        harness = ScenarioAblationHarness(family=_family())
        result = harness.evaluate(
            _well_powered(),
            temporal_knowledge_controlled=True,
            fully_reproducible=True,
        )
        data = result_to_dict(result)
        for key in (
            "ablation_id",
            "verdict",
            "may_promote",
            "paired",
            "discovery",
            "cost",
            "coverage_rate",
            "temporal_knowledge_controlled",
            "fully_reproducible",
            "holdout_contaminated",
            "blockers",
            "warnings",
        ):
            assert key in data
        assert data["verdict"] == "PROMOTE"
