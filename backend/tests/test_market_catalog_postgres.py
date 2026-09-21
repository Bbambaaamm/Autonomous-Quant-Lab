import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_market_catalog import directory

from quantlab.market_catalog import MarketCatalogService

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI"
)


def test_postgres_catalog_is_atomic_and_immutable():
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            service = MarketCatalogService(sessions)
            now = datetime.now(UTC)
            identity = service.sync(
                actor="postgres-test",
                reason="Immutable evidence",
                fetch=directory,
                clock=lambda: now,
            )
            assert service.read(now)["snapshot"]["id"] == identity
            for statement in (
                "UPDATE market_catalog_snapshots SET listing_count=0 WHERE snapshot_id=:id",
                "DELETE FROM market_catalog_listings WHERE snapshot_id=:id",
            ):
                with pytest.raises(DBAPIError), sessions() as session, session.begin():
                    session.execute(text(statement), {"id": identity})
            assert service.read(now)["snapshot"]["listing_count"] == 2
        finally:
            transaction.rollback()
    engine.dispose()


def test_postgres_asset_directory_and_batch_are_immutable():
    from datetime import date

    from test_market_pipeline import assets

    from quantlab.asset_directory import AssetDirectoryService
    from quantlab.market_pipeline import MarketPipeline

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            now = datetime.now(UTC)
            snapshot = AssetDirectoryService(sessions).sync(
                assets(), actor="postgres-test", reason="Immutable identities", received_at=now
            )
            pipeline = MarketPipeline(sessions)
            batch = pipeline.create(
                snapshot,
                date(2026, 1, 2),
                date(2026, 1, 5),
                "alpaca:iex",
                "postgres-test",
                "Immutable batch",
                now,
            )
            for statement, identity in (
                (
                    "UPDATE asset_directory_snapshots SET actor='changed' WHERE snapshot_id=:id",
                    snapshot,
                ),
                ("DELETE FROM asset_directory_entries WHERE snapshot_id=:id", snapshot),
                ("DELETE FROM market_batches WHERE batch_id=:id", batch),
            ):
                with pytest.raises(DBAPIError), sessions() as session, session.begin():
                    session.execute(text(statement), {"id": identity})
            assert pipeline.read()["total"] == 2
        finally:
            transaction.rollback()
    engine.dispose()
