\set ON_ERROR_STOP on
\if :{?runtime_role}
\else
\echo 'Chybí psql proměnná runtime_role'
\quit 2
\endif
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO :"runtime_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"runtime_role";

-- Immutable evidence is append-only for the runtime role. PostgreSQL triggers
-- are the authoritative defense; privilege revocation provides least-privilege
-- defense in depth for the same boundary.
REVOKE UPDATE, DELETE ON TABLE
    risk_decisions,
    paper_fills,
    audit_events,
    risk_events,
    reconciliation_results,
    corporate_action_events,
    corporate_action_event_audit,
    corporate_action_revisions,
    corporate_action_cancellations,
    corporate_action_revision_canonicalizations,
    corporate_action_event_symbols,
    phase6_eligibility_decisions,
    preopen_execution_intents,
    market_catalog_snapshots,
    market_catalog_listings,
    asset_directory_snapshots,
    asset_directory_entries,
    asset_directory_snapshot_metrics,
    market_batches,
    market_screen_runs,
    market_screen_items,
    market_action_receipts,
    market_action_reviews,
    market_batch_metrics
FROM :"runtime_role";

-- Future tables default to read + append only between role-configuration runs.
-- Deploy-time configuration still grants UPDATE broadly for mutable runtime-state
-- compatibility, so every new immutable evidence table must be added to the
-- explicit revoke list in the same reviewed change.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE UPDATE, DELETE ON TABLES FROM :"runtime_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_role";
