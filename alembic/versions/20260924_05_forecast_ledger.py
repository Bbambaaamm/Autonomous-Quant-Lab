"""Immutable Forecast Ledger — append-only probability forecast evidence (#266).

Revision ID: 20260924_05
Revises: 20260924_04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260924_05"
down_revision = "20260924_04"
branch_labels = None
depends_on = None

TABLE = "forecast_ledger"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("forecast_id", sa.String(64), primary_key=True),
        sa.Column("decision_identity", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("degraded_reason", sa.String(40)),
        sa.Column("degraded_detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_kind", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("opportunity_id", sa.String(128), nullable=False),
        sa.Column("preregistered", sa.Integer(), nullable=False),
        sa.Column("target_spec_json", sa.Text(), nullable=False),
        sa.Column("target_spec_hash", sa.String(64), nullable=False),
        sa.Column("target_definition", sa.Text(), nullable=False),
        sa.Column("outcome_kind", sa.String(20), nullable=False),
        sa.Column("raw_probability", sa.Numeric(30, 12)),
        sa.Column("calibrated_probability", sa.Numeric(30, 12)),
        sa.Column("calibrator_id", sa.String(128)),
        sa.Column("calibrator_version", sa.String(64)),
        sa.Column("distribution_json", sa.Text(), nullable=False),
        sa.Column("baseline_probability", sa.Numeric(30, 12)),
        sa.Column("baseline_source", sa.String(128)),
        sa.Column("baseline_source_version", sa.String(64)),
        sa.Column("baseline_as_of", sa.DateTime(timezone=True)),
        sa.Column("confidence", sa.Numeric(30, 12)),
        sa.Column("uncertainty_json", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("code_sha", sa.String(64), nullable=False),
        sa.Column("strategy_identity", sa.String(128), nullable=False),
        sa.Column("feature_extractor_version", sa.String(64), nullable=False),
        sa.Column("market_snapshot_id", sa.String(128), nullable=False),
        sa.Column("market_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("market_snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deployment_id", sa.String(64)),
        sa.Column("research_experiment_id", sa.String(64)),
        sa.Column("calibration_version", sa.String(64)),
        sa.Column("trial_family_id", sa.String(64)),
        sa.Column("source_research_identity", sa.String(128)),
        sa.Column("prior_forecast_id", sa.String(64)),
        sa.Column("regime", sa.String(64)),
        sa.Column("source", sa.String(128)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("decision_identity", name="uq_forecast_ledger_decision_identity"),
        sa.CheckConstraint(
            "(status = 'FORECAST_EMITTED' AND degraded_reason IS NULL "
            "AND ((outcome_kind = 'BINARY' AND raw_probability IS NOT NULL) "
            "OR (outcome_kind = 'MULTICLASS' AND raw_probability IS NULL))) OR "
            "(status <> 'FORECAST_EMITTED' AND raw_probability IS NULL "
            "AND calibrated_probability IS NULL AND degraded_reason IS NOT NULL)",
            name="ck_forecast_ledger_probability_presence",
        ),
        sa.CheckConstraint(
            "raw_probability IS NULL OR (raw_probability >= 0 AND raw_probability <= 1)",
            name="ck_forecast_ledger_raw_probability_range",
        ),
        sa.CheckConstraint(
            "calibrated_probability IS NULL "
            "OR (calibrated_probability >= 0 AND calibrated_probability <= 1)",
            name="ck_forecast_ledger_calibrated_probability_range",
        ),
        sa.CheckConstraint(
            "calibrated_probability IS NULL "
            "OR (calibrator_id IS NOT NULL AND calibrator_version IS NOT NULL)",
            name="ck_forecast_ledger_calibrator_identity",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_forecast_ledger_confidence_range",
        ),
        sa.CheckConstraint(
            "baseline_probability IS NULL OR (baseline_source IS NOT NULL "
            "AND baseline_source_version IS NOT NULL AND baseline_as_of IS NOT NULL)",
            name="ck_forecast_ledger_baseline_identity",
        ),
        sa.CheckConstraint(
            "resolution_at > decision_time", name="ck_forecast_ledger_resolution_after_decision"
        ),
        sa.CheckConstraint(
            "created_at >= decision_time", name="ck_forecast_ledger_created_after_decision"
        ),
        sa.CheckConstraint(
            "prior_forecast_id IS NULL OR prior_forecast_id <> forecast_id",
            name="ck_forecast_ledger_prior_not_self",
        ),
        sa.CheckConstraint(
            "status <> 'FORECAST_EMITTED' OR created_at < resolution_at",
            name="ck_forecast_ledger_emitted_before_resolution",
        ),
        sa.CheckConstraint(
            "preregistered = 0 OR trial_family_id IS NOT NULL",
            name="ck_forecast_ledger_preregistered_trial_family",
        ),
    )
    for index_name, columns in (
        ("ix_forecast_ledger_scope", ["scope_kind", "scope_id", "decision_time"]),
        ("ix_forecast_ledger_opportunity", ["opportunity_id"]),
        ("ix_forecast_ledger_decision_time", ["decision_time"]),
        ("ix_forecast_ledger_created_at", ["created_at"]),
        ("ix_forecast_ledger_target_spec", ["target_spec_hash"]),
        ("ix_forecast_ledger_trial_family", ["trial_family_id"]),
    ):
        op.create_index(index_name, TABLE, columns)
    op.create_index("ix_forecast_ledger_status", TABLE, ["status"])

    if op.get_bind().dialect.name == "postgresql":
        # DB-level immutability mirrors the #217 core-evidence boundary: the
        # append-only contract is enforced by the database, not only by code.
        op.execute(
            f"CREATE TRIGGER {TABLE}_immutable BEFORE UPDATE OR DELETE ON {TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_core_evidence_mutation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {TABLE} LIMIT 1")).first() is not None:
        raise RuntimeError(
            "Downgrade 20260924_05 není možný bez ztráty immutable forecast evidence"
        )
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_immutable ON {TABLE}")
    for index_name in (
        "ix_forecast_ledger_status",
        "ix_forecast_ledger_trial_family",
        "ix_forecast_ledger_target_spec",
        "ix_forecast_ledger_created_at",
        "ix_forecast_ledger_decision_time",
        "ix_forecast_ledger_opportunity",
        "ix_forecast_ledger_scope",
    ):
        op.drop_index(index_name, table_name=TABLE)
    op.drop_table(TABLE)
