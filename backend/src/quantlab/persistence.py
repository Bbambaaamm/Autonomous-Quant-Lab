import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strategy: Mapped[str] = mapped_column(String(100))
    result_json: Mapped[str] = mapped_column(Text)


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    raise TypeError(f"Nelze serializovat {type(value)}")


class RunRepository:
    def __init__(self, url: str = "sqlite:///:memory:") -> None:
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)

    def save(self, strategy: str, result: dict[str, object], created_at: datetime) -> int:
        with Session(self.engine) as session:
            row = RunRecord(strategy=strategy, result_json=json.dumps(result, default=_json_default), created_at=created_at)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    def list(self) -> list[dict[str, object]]:
        with Session(self.engine) as session:
            records: list[dict[str, object]] = []
            for row in session.query(RunRecord).all():
                created_at = row.created_at
                # SQLite časovou zónu neuchová; repository obnoví explicitní UTC.
                if created_at.tzinfo is None:
                    from datetime import timezone

                    created_at = created_at.replace(tzinfo=timezone.utc)
                records.append(
                    {
                        "id": row.id,
                        "strategy": row.strategy,
                        "created_at": created_at,
                        "result": json.loads(row.result_json),
                    }
                )
            return records
