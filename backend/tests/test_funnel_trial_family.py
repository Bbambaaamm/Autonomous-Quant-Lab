"""Tests for funnel trial-family accounting (#268, review STAT-BLOCKER B).

Covers:

- a funnel variant is identified deterministically from preregistered parameters and from
  the funnel's own ``trial_family_key()``;
- a trial family must be a real preregistration (named use case, selection policy,
  non-empty variant set, no duplicate variant);
- an undeclared variant cannot be observed (post-hoc) and a declared variant cannot
  vanish from the accounting;
- the winner may not be chosen from the best full-history metric unless the declaration
  pre-specified that exact variant as primary;
- the family size / hash / selection policy travel into downstream lineage, and a record
  missing them is flagged;
- purity: no I/O, no clock read, no execution authority.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quantlab.candidate_funnel import (
    CandidateFunnel,
    RankingKey,
    StageBConfig,
    StageBEvaluator,
    StageBResult,
)
from quantlab.funnel_pit import PIT_SEMANTICS_VERSION, pit_decision_time, recompute_pit_replay
from quantlab.funnel_trial_family import (
    FUNNEL_TRIAL_FAMILY_VERSION,
    FullHistorySelectionError,
    FunnelVariant,
    PostHocVariantError,
    SelectionBasis,
    TrialFamilyAccounting,
    TrialFamilyDeclaration,
    TrialFamilyDeclarationError,
    UnpreregisteredWinnerError,
    VariantExclusion,
    VariantObservation,
    assert_family_fully_accounted,
    assert_selection_is_not_full_history,
    audit_trial_family_lineage,
    declaration_from_trial_family_keys,
    declaration_from_variants,
    funnel_trial_family_required_fields,
    summarise_trial_family,
    trial_family_lineage,
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
    return {"instrument_id": instrument_id, "symbol": symbol, "exchange": exchange}


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
    def __init__(self, config: StageBConfig, score_map: dict[str, Decimal] | None = None):
        super().__init__(config)
        self.score_map = score_map or {}

    def evaluate_one(
        self,
        instrument_id: str,
        symbol: str,
        stage_a_evidence: dict[str, object],
    ) -> StageBResult:
        self.record_provider_request(1)
        return StageBResult(
            instrument_id=instrument_id,
            symbol=symbol,
            passed=True,
            rejection_reason=None,
            score=self.score_map.get(instrument_id, Decimal("0.5")),
        )


def _funnel_universe(n: int):
    instruments = [make_instrument(f"asset-{i:03d}", f"A{i}") for i in range(n)]
    observations = {
        f"asset-{i:03d}": make_observations(f"asset-{i:03d}", f"A{i}") for i in range(n)
    }
    actions = {f"asset-{i:03d}": [make_corporate_action(f"asset-{i:03d}")] for i in range(n)}
    readiness = {f"asset-{i:03d}": f"readiness-{i}" for i in range(n)}
    return instruments, observations, actions, readiness


def _run(n: int = 4, max_candidates: int = 3, ranking_key=RankingKey.INSTRUMENT_ID_ASC):
    instruments, observations, actions, readiness = _funnel_universe(n)
    config = StageBConfig(max_candidates=max_candidates, ranking_key=ranking_key)
    run = CandidateFunnel(stage_b_config=config).run(
        "snapshot-1",
        instruments,
        observations,
        actions,
        DAYS,
        NOW,
        RecordingStageBEvaluator(config),
        readiness_ids=readiness,
    )
    return run, config


def _variant(
    *,
    funnel_version: str = "funnel-a",
    stage_a: str = "sa-1",
    stage_b: str = "sb-1",
    ranking_key: str = RankingKey.INSTRUMENT_ID_ASC.value,
    max_candidates: int = 3,
    snapshot: str = "snapshot-1",
) -> FunnelVariant:
    return FunnelVariant(
        funnel_version=funnel_version,
        stage_a_config_hash=stage_a,
        stage_b_config_hash=stage_b,
        ranking_key=ranking_key,
        ranking_key_version="ranking-v1",
        max_candidates=max_candidates,
        universe_snapshot_id=snapshot,
    )


def _declaration(*variants: FunnelVariant, primary: str | None = None, before: bool = True):
    return declaration_from_variants(
        "family-1",
        "paper-promotion",
        "preregistered-primary-then-out-of-sample",
        list(variants),
        primary_variant_id=primary,
        declared_before_observation=before,
    )


# ─── variant identity ─────────────────────────────────────────────────────────────────


def test_variant_id_is_deterministic_and_parameter_only():
    """The same parameters give the same variant id; a different cap gives a different id."""
    a = _variant()
    b = _variant()
    c = _variant(max_candidates=4)
    assert a.variant_id == b.variant_id
    assert a.variant_id != c.variant_id


def test_variant_id_is_independent_of_label():
    """A cosmetic label must not change the statistical identity of a variant."""
    assert (
        _variant().variant_id
        == FunnelVariant(
            funnel_version="funnel-a",
            stage_a_config_hash="sa-1",
            stage_b_config_hash="sb-1",
            ranking_key=RankingKey.INSTRUMENT_ID_ASC.value,
            ranking_key_version="ranking-v1",
            max_candidates=3,
            universe_snapshot_id="snapshot-1",
            label="a friendlier name",
        ).variant_id
    )


def test_variant_rejects_empty_identity_fields():
    with pytest.raises(TrialFamilyDeclarationError, match="funnel_version"):
        _variant(funnel_version="  ")
    with pytest.raises(TrialFamilyDeclarationError, match="max_candidates"):
        _variant(max_candidates=0)


def test_variant_trial_family_key_matches_funnel_run():
    """The variant's key is the funnel's own selection-operator key, not a parallel one."""
    run, config = _run()
    variant = FunnelVariant.from_run(run, max_candidates=config.max_candidates)
    assert variant.trial_family_key == run.trial_family_key()
    assert variant.variant_id


def _folds(*indexes: int):
    return tuple(
        pit_decision_time(
            index, XNYSCalendar().session_close(DAYS[index]), f"snapshot-{index}", DAYS
        )
        for index in indexes
    )


def test_variant_from_replay_uses_first_decision_snapshot():
    instruments = [make_instrument("asset-1", "A1")]
    observations = {"asset-1": make_observations()}
    actions = {"asset-1": [make_corporate_action()]}
    replay = recompute_pit_replay(
        _folds(139, 179), instruments, observations, actions, readiness_ids={"asset-1": "r1"}
    )
    variant = FunnelVariant.from_replay(replay, max_candidates=2)
    assert variant.trial_family_key == replay.trial_family_key()
    assert variant.universe_snapshot_id == replay.decision_snapshots[0].universe_snapshot_id
    assert replay.pit_version == PIT_SEMANTICS_VERSION


def test_variant_from_empty_replay_fails_closed():
    class _EmptyReplay:
        decision_snapshots: tuple[object, ...] = ()

    with pytest.raises(TrialFamilyDeclarationError, match="no decision snapshots"):
        FunnelVariant.from_replay(_EmptyReplay(), max_candidates=1)


# ─── declaration is a real preregistration ────────────────────────────────────────────


def test_declaration_requires_a_use_case():
    with pytest.raises(TrialFamilyDeclarationError, match="use case"):
        TrialFamilyDeclaration(
            family_id="f",
            use_case="",
            selection_policy="policy",
            variants=(_variant(),),
        )


def test_declaration_requires_a_selection_policy():
    with pytest.raises(TrialFamilyDeclarationError, match="selection policy"):
        TrialFamilyDeclaration(
            family_id="f",
            use_case="use-case",
            selection_policy=" ",
            variants=(_variant(),),
        )


def test_declaration_with_no_variants_is_not_a_preregistration():
    with pytest.raises(TrialFamilyDeclarationError, match="no variants"):
        _declaration()


def test_declaration_rejects_a_duplicated_variant():
    v = _variant()
    with pytest.raises(TrialFamilyDeclarationError, match="twice"):
        TrialFamilyDeclaration(
            family_id="f",
            use_case="use-case",
            selection_policy="policy",
            variants=(v, v),
        )


def test_declaration_primary_must_be_a_member():
    v = _variant()
    with pytest.raises(TrialFamilyDeclarationError, match="primary_variant_id"):
        _declaration(v, primary="not-a-member")


def test_family_size_and_hash_are_order_independent():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    forward = _declaration(a, b)
    backward = _declaration(b, a)
    assert forward.family_size == 2
    assert forward.config_hash == backward.config_hash
    assert forward.variant_ids == backward.variant_ids


def test_declaration_hash_changes_with_selection_policy():
    a = _variant()
    d1 = declaration_from_variants("f", "use", "policy-a", [a])
    d2 = declaration_from_variants("f", "use", "policy-b", [a])
    assert d1.config_hash != d2.config_hash


def test_declaration_variant_lookup_fails_closed():
    d = _declaration(_variant())
    with pytest.raises(UnpreregisteredWinnerError, match="not a declared member"):
        d.variant("unknown")


def test_declaration_from_trial_family_keys_bridges_the_funnel_key():
    run, config = _run()
    key = run.trial_family_key()
    declaration = declaration_from_trial_family_keys(
        "family-1",
        "paper-promotion",
        "preregistered-primary",
        [key, (*key[:3], key[3] + 1)],
        stage_a_config_hash=run.stage_a_config_hash,
        stage_b_config_hash=run.stage_b_config_hash,
        universe_snapshot_id=run.universe_snapshot_id,
    )
    assert declaration.family_size == 2
    assert run.trial_family_key() in {v.trial_family_key for v in declaration.variants}
    assert config.max_candidates == 3


# ─── accounting: post-hoc variants cannot be observed ─────────────────────────────────


def test_observing_a_declared_variant_is_recorded():
    a = _variant()
    accounting = TrialFamilyAccounting(_declaration(a)).observe(
        VariantObservation(
            a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
        )
    )
    assert accounting.observed_variant_ids == (a.variant_id,)
    assert accounting.is_fully_accounted


def test_observing_an_undeclared_variant_fails_closed():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a))
    with pytest.raises(PostHocVariantError, match="was not declared in trial family"):
        accounting.observe(
            VariantObservation(
                b.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
            )
        )


def test_accounting_constructor_rejects_an_undeclared_observation():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    with pytest.raises(PostHocVariantError, match="not a declared member"):
        TrialFamilyAccounting(
            _declaration(a),
            observations=(
                VariantObservation(
                    b.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
                ),
            ),
        )


def test_a_variant_cannot_be_observed_twice():
    a = _variant()
    accounting = TrialFamilyAccounting(_declaration(a)).observe(
        VariantObservation(
            a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
        )
    )
    with pytest.raises(TrialFamilyDeclarationError, match="already accounted"):
        accounting.observe(
            VariantObservation(
                a.variant_id, "2026-10-05T01:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
            )
        )


def test_exclusion_requires_a_reason():
    with pytest.raises(TrialFamilyDeclarationError, match="unexplained exclusion"):
        VariantExclusion("abc", "  ")


def test_a_variant_cannot_be_both_observed_and_excluded():
    a = _variant()
    accounting = TrialFamilyAccounting(_declaration(a)).observe(
        VariantObservation(
            a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
        )
    )
    with pytest.raises(TrialFamilyDeclarationError, match="already accounted"):
        accounting.exclude(VariantExclusion(a.variant_id, "dropped"))


def test_excluding_a_declared_variant_keeps_the_accounting_complete():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = (
        TrialFamilyAccounting(_declaration(a, b))
        .observe(
            VariantObservation(
                a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
            )
        )
        .exclude(VariantExclusion(b.variant_id, "budget exhausted before this variant ran"))
    )
    assert accounting.is_fully_accounted
    assert accounting.unaccounted_variant_ids == ()
    assert_family_fully_accounted(accounting)


def test_unaccounted_declared_variant_fails_closed():
    """A declared variant that simply vanished would understate the family size."""
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a, b)).observe(
        VariantObservation(
            a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
        )
    )
    assert accounting.unaccounted_variant_ids == (b.variant_id,)
    with pytest.raises(TrialFamilyDeclarationError, match="neither observed nor excluded"):
        assert_family_fully_accounted(accounting)


def test_accounting_is_immutable():
    """observe()/exclude() return new instances; the original is untouched."""
    a = _variant()
    base = TrialFamilyAccounting(_declaration(a))
    _ = base.observe(
        VariantObservation(
            a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
        )
    )
    assert base.observations == ()


# ─── winner selection is not post-hoc full-history ────────────────────────────────────


def test_best_full_history_without_primary_fails_closed():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a, b))
    with pytest.raises(FullHistorySelectionError, match="without a preregistered primary"):
        assert_selection_is_not_full_history(
            accounting, b.variant_id, SelectionBasis.BEST_FULL_HISTORY_METRIC
        )


def test_best_full_history_that_is_not_the_primary_fails_closed():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a, b, primary=a.variant_id))
    with pytest.raises(FullHistorySelectionError, match="not the preregistered primary"):
        assert_selection_is_not_full_history(
            accounting, b.variant_id, SelectionBasis.BEST_FULL_HISTORY_METRIC
        )


def test_preregistered_primary_is_accepted_even_if_chosen_by_full_history():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = (
        TrialFamilyAccounting(_declaration(a, b, primary=a.variant_id))
        .observe(
            VariantObservation(
                a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.BEST_FULL_HISTORY_METRIC
            )
        )
        .exclude(VariantExclusion(b.variant_id, "not run in this slice"))
    )
    assert_selection_is_not_full_history(
        accounting, a.variant_id, SelectionBasis.BEST_FULL_HISTORY_METRIC
    )


def test_selection_claiming_a_primary_when_none_was_declared_fails_closed():
    a = _variant()
    accounting = TrialFamilyAccounting(_declaration(a))
    with pytest.raises(FullHistorySelectionError, match="names none"):
        assert_selection_is_not_full_history(
            accounting, a.variant_id, SelectionBasis.PREREGISTERED_PRIMARY
        )


def test_selection_claiming_the_wrong_primary_fails_closed():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a, b, primary=a.variant_id))
    with pytest.raises(FullHistorySelectionError, match="preregistered"):
        assert_selection_is_not_full_history(
            accounting, b.variant_id, SelectionBasis.PREREGISTERED_PRIMARY
        )


def test_undeclared_winner_fails_closed():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a))
    with pytest.raises(UnpreregisteredWinnerError, match="not a declared member"):
        assert_selection_is_not_full_history(
            accounting, b.variant_id, SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
        )


def test_family_declared_after_observation_cannot_select_a_winner():
    a = _variant()
    accounting = TrialFamilyAccounting(_declaration(a, before=False))
    with pytest.raises(FullHistorySelectionError, match="not declared before observation"):
        assert_selection_is_not_full_history(
            accounting, a.variant_id, SelectionBasis.PREREGISTERED_PRIMARY
        )


def test_out_of_sample_selection_requires_full_accounting():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = TrialFamilyAccounting(_declaration(a, b)).observe(
        VariantObservation(
            a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
        )
    )
    with pytest.raises(TrialFamilyDeclarationError, match="neither observed nor excluded"):
        assert_selection_is_not_full_history(
            accounting, a.variant_id, SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
        )


def test_out_of_sample_selection_passes_with_full_accounting():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    accounting = (
        TrialFamilyAccounting(_declaration(a, b))
        .observe(
            VariantObservation(
                a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
            )
        )
        .observe(
            VariantObservation(
                b.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
            )
        )
    )
    assert_selection_is_not_full_history(
        accounting, a.variant_id, SelectionBasis.BEST_OUT_OF_SAMPLE_METRIC
    )


# ─── lineage / dashboard ──────────────────────────────────────────────────────────────


def test_lineage_carries_family_size_hash_and_policy():
    a, b = _variant(max_candidates=3), _variant(max_candidates=4)
    declaration = _declaration(a, b, primary=a.variant_id)
    accounting = (
        TrialFamilyAccounting(declaration)
        .observe(
            VariantObservation(
                a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
            )
        )
        .exclude(VariantExclusion(b.variant_id, "not run"))
    )
    lineage = accounting.to_lineage(selected_variant_id=a.variant_id)
    assert lineage["trial_family_version"] == FUNNEL_TRIAL_FAMILY_VERSION
    assert lineage["trial_family_id"] == "family-1"
    assert lineage["trial_family_size"] == 2
    assert lineage["trial_family_hash"] == declaration.config_hash
    assert lineage["trial_family_selected_variant_id"] == a.variant_id
    assert lineage["trial_family_is_fully_accounted"] is True


def test_trial_family_lineage_works_before_any_observation():
    """The family size must be knowable before the winner is, not only afterwards."""
    lineage = trial_family_lineage(_declaration(_variant()), selected_variant_id=None)
    assert lineage["trial_family_size"] == 1
    assert lineage["trial_family_selected_variant_id"] is None


def test_audit_flags_records_missing_family_accounting_fields():
    a = _variant()
    good = _declaration(a).to_lineage()
    gaps = audit_trial_family_lineage([good, {"funnel_version": "x"}])
    assert len(gaps) == 1
    index, missing = gaps[0]
    assert index == 1
    assert set(missing) == set(funnel_trial_family_required_fields())


def test_summary_reports_counts_without_computation():
    a, b, c = (
        _variant(max_candidates=3),
        _variant(max_candidates=4),
        _variant(max_candidates=5),
    )
    accounting = (
        TrialFamilyAccounting(_declaration(a, b, c))
        .observe(
            VariantObservation(
                a.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
            )
        )
        .exclude(VariantExclusion(b.variant_id, "budget"))
    )
    summary = summarise_trial_family(accounting, selected_variant_id=a.variant_id).to_summary()
    assert summary["trial_family_size"] == 3
    assert summary["observed_variants"] == 1
    assert summary["excluded_variants"] == 1
    assert summary["unaccounted_variants"] == 1
    assert summary["selected_variant_id"] == a.variant_id


def test_funnel_run_variant_feeds_the_declaration_end_to_end():
    """A real FunnelRun round-trips into a declared family without a parallel vocabulary."""
    run, config = _run(n=4, max_candidates=3)
    variant = FunnelVariant.from_run(run, max_candidates=config.max_candidates)
    declaration = _declaration(variant, primary=variant.variant_id)
    accounting = TrialFamilyAccounting(declaration).observe(
        VariantObservation(
            variant.variant_id, "2026-10-05T00:00:00Z", SelectionBasis.PREREGISTERED_PRIMARY
        )
    )
    assert_selection_is_not_full_history(
        accounting, variant.variant_id, SelectionBasis.PREREGISTERED_PRIMARY
    )
    assert accounting.to_lineage()["trial_family_size"] == 1


# ─── purity / safety ──────────────────────────────────────────────────────────────────


def _module_source() -> str:
    path = Path(__file__).resolve().parents[1] / "src" / "quantlab" / "funnel_trial_family.py"
    return path.read_text(encoding="utf-8")


def test_trial_family_module_performs_no_io_and_reads_no_clock():
    tree = ast.parse(_module_source())
    banned_calls = {"open", "input", "print", "exec", "eval", "__import__"}
    banned_attrs = {"now", "today", "utcnow"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in banned_calls:
                raise AssertionError(f"banned call: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in banned_attrs:
                raise AssertionError(f"banned clock read: {func.attr}")


def test_trial_family_module_has_no_execution_authority():
    source = _module_source().lower()
    for token in (
        "paperbroker",
        "executionengine",
        "orderintent",
        "place_order",
        "submit_order",
        "broker",
        "trading",
    ):
        assert token not in source, f"execution-authority token present: {token}"


def test_trial_family_module_imports_no_session_or_worker():
    """Purity at the import level: no DB session, network client or process spawning."""
    tree = ast.parse(_module_source())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"sqlalchemy", "requests", "httpx", "subprocess", "socket", "urllib"}
    assert not (imported & banned), f"impure dependency imported: {sorted(imported & banned)}"
