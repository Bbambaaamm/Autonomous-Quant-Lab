"""Statistical Trial Registry + Holdout Ledger + purged-validation foundation.

Revision ID: 20261004_01
Revises: 20260924_04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20261004_01"
down_revision = "20260924_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATISTICAL_TABLES = (
    "statistical_trial_families",
    "statistical_trial_campaigns",
    "statistical_split_plans",
    "statistical_trials",
    "statistical_trial_partition_results",
    "statistical_holdouts",
    "statistical_holdout_assignments",
    "statistical_holdout_access_events",
    "statistical_validation_results",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # 1. statistical_trial_families
    op.create_table(
        "statistical_trial_families",
        sa.Column("family_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("economic_identity_hash", sa.String(64), nullable=False),
        sa.Column("economic_identity_json", sa.Text(), nullable=False),
        sa.Column("target_spec_hash", sa.String(64), nullable=False),
        sa.Column("target_spec_json", sa.Text(), nullable=False),
        sa.Column("opportunity_scope_hash", sa.String(64), nullable=False),
        sa.Column("opportunity_scope_json", sa.Text(), nullable=False),
        sa.Column("benchmark_policy_hash", sa.String(64), nullable=False),
        sa.Column("benchmark_policy_json", sa.Text(), nullable=False),
        sa.Column("registry_policy_id", sa.String(80), nullable=False),
        sa.Column("registry_policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "parent_family_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_families.family_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_by_json", sa.Text(), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
        sa.Index("ix_stat_trial_families_econ_identity", "economic_identity_hash"),
    )

    # 2. statistical_trial_campaigns
    op.create_table(
        "statistical_trial_campaigns",
        sa.Column("campaign_id", sa.String(64), primary_key=True),
        sa.Column(
            "family_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            sa.String(64),
            sa.ForeignKey("research_experiments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "split_plan_id",
            sa.String(64),
            sa.ForeignKey("statistical_split_plans.split_plan_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("selection_policy_hash", sa.String(64), nullable=False),
        sa.Column("selection_policy_json", sa.Text(), nullable=False),
        sa.Column("code_sha", sa.String(64), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status_at_creation", sa.String(30), nullable=False),
        sa.Column(
            "parent_campaign_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_campaigns.campaign_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("adaptive_reason", sa.Text(), nullable=True),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
    )

    # 3. statistical_split_plans
    op.create_table(
        "statistical_split_plans",
        sa.Column("split_plan_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("target_spec_hash", sa.String(64), nullable=False),
        sa.Column("decision_time_policy", sa.Text(), nullable=False),
        sa.Column("label_interval_policy", sa.Text(), nullable=False),
        sa.Column("purge_policy_json", sa.Text(), nullable=False),
        sa.Column("embargo_policy_json", sa.Text(), nullable=False),
        sa.Column("partitions_json", sa.Text(), nullable=False),
        sa.Column("final_holdout_hash", sa.String(64), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
    )

    # 4. statistical_trials
    op.create_table(
        "statistical_trials",
        sa.Column("trial_id", sa.String(64), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_campaigns.campaign_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "family_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_trial_id",
            sa.String(64),
            sa.ForeignKey("statistical_trials.trial_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_kind", sa.String(40), nullable=False),
        sa.Column("variant_hash", sa.String(64), nullable=False),
        sa.Column("variant_json", sa.Text(), nullable=False),
        sa.Column(
            "strategy_identity",
            sa.String(64),
            sa.ForeignKey("strategies.strategy_identity", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("model_artifact_json", sa.Text(), nullable=True),
        sa.Column("funnel_version_hash", sa.String(64), nullable=True),
        sa.Column("target_spec_hash", sa.String(64), nullable=False),
        sa.Column("code_sha", sa.String(64), nullable=False),
        sa.Column("counts_policy_version", sa.Integer(), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
        sa.UniqueConstraint("campaign_id", "variant_hash"),
    )

    # 5. statistical_trial_partition_results
    op.create_table(
        "statistical_trial_partition_results",
        sa.Column("result_id", sa.String(64), primary_key=True),
        sa.Column(
            "trial_id",
            sa.String(64),
            sa.ForeignKey("statistical_trials.trial_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_campaigns.campaign_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("partition_id", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sample_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n_observations", sa.Integer(), nullable=False),
        sa.Column("n_returns", sa.Integer(), nullable=False),
        sa.Column("mean_return", sa.Float(), nullable=True),
        sa.Column("variance_return", sa.Float(), nullable=True),
        sa.Column("skewness", sa.Float(), nullable=True),
        sa.Column("excess_kurtosis", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("selection_score", sa.Float(), nullable=True),
        sa.Column("total_return", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("returns_hash", sa.String(64), nullable=True),
        sa.Column("returns_json", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
        sa.UniqueConstraint("trial_id", "partition_id", "stage"),
    )

    # 6. statistical_holdouts
    op.create_table(
        "statistical_holdouts",
        sa.Column("holdout_id", sa.String(64), primary_key=True),
        sa.Column(
            "split_plan_id",
            sa.String(64),
            sa.ForeignKey("statistical_split_plans.split_plan_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("dataset_snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("partition_hash", sa.String(64), nullable=False),
        sa.Column("partition_json", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
    )

    # 7. statistical_holdout_assignments
    op.create_table(
        "statistical_holdout_assignments",
        sa.Column("assignment_id", sa.String(64), primary_key=True),
        sa.Column(
            "holdout_id",
            sa.String(64),
            sa.ForeignKey("statistical_holdouts.holdout_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "family_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_campaigns.campaign_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_trial_id",
            sa.String(64),
            sa.ForeignKey("statistical_trials.trial_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection_evidence_hash", sa.String(64), nullable=False),
        sa.Column("selection_evidence_json", sa.Text(), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
        sa.UniqueConstraint("holdout_id", "family_id"),
    )

    # 8. statistical_holdout_access_events
    op.create_table(
        "statistical_holdout_access_events",
        sa.Column("access_id", sa.String(64), primary_key=True),
        sa.Column(
            "holdout_id",
            sa.String(64),
            sa.ForeignKey("statistical_holdouts.holdout_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "family_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_campaigns.campaign_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trial_id",
            sa.String(64),
            sa.ForeignKey("statistical_trials.trial_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_json", sa.Text(), nullable=False),
        sa.Column("component", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
    )

    # 9. statistical_validation_results
    op.create_table(
        "statistical_validation_results",
        sa.Column("validation_id", sa.String(64), primary_key=True),
        sa.Column(
            "family_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_families.family_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            sa.String(64),
            sa.ForeignKey("statistical_trial_campaigns.campaign_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_trial_id",
            sa.String(64),
            sa.ForeignKey("statistical_trials.trial_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "holdout_assignment_id",
            sa.String(64),
            sa.ForeignKey(
                "statistical_holdout_assignments.assignment_id", ondelete="RESTRICT"
            ),
            nullable=True,
        ),
        sa.Column("method_id", sa.String(80), nullable=False),
        sa.Column("method_version", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_trial_set_hash", sa.String(64), nullable=False),
        sa.Column("trial_count", sa.Integer(), nullable=False),
        sa.Column("effective_trial_count", sa.Float(), nullable=True),
        sa.Column("observed_sharpe", sa.Float(), nullable=True),
        sa.Column("psr", sa.Float(), nullable=True),
        sa.Column("dsr", sa.Float(), nullable=True),
        sa.Column("dsr_reference_sharpe", sa.Float(), nullable=True),
        sa.Column("pbo", sa.Float(), nullable=True),
        sa.Column("cscv_split_count", sa.Integer(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("decision_state", sa.String(30), nullable=False),
        sa.Column("integrity_hash", sa.String(64), nullable=False, unique=True),
    )

    # Immutability triggers — same pattern as 20260924_04
    op.execute(
        """
        CREATE FUNCTION reject_statistical_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'statistical evidence is immutable';
        END;
        $$
        """
    )
    for table in STATISTICAL_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_statistical_evidence_mutation()"
        )

    # REVOKE runtime role from statistical tables (same pattern as #217)
    # The runtime role is expected to be 'quantlab_runtime' — revoke all DML
    for table in STATISTICAL_TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM quantlab_runtime")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(STATISTICAL_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_statistical_evidence_mutation()")
    for table in reversed(STATISTICAL_TABLES):
        op.drop_table(table)
