import time
import uuid

from sqlalchemy import BigInteger, Float, Integer, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    active_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, default="campus")
    title: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="detected")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    recovered_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    proposal: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proposal_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    checks: Mapped[list] = mapped_column(JSONB, default=list)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class Timeline(Base):
    __tablename__ = "timeline"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    kind: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    action_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class Preference(Base):
    __tablename__ = "preferences"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)


def database_url(value):
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    return value.replace("postgresql://", "postgresql+psycopg://", 1)


class Database:
    def __init__(self, url):
        self.engine = create_async_engine(database_url(url), pool_size=3, max_overflow=1,
                                         pool_pre_ping=True, connect_args={"prepare_threshold": None}) if url else None
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False) if self.engine else None

    async def ready(self):
        if not self.engine:
            return False
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM incidents LIMIT 1"))
            return True
        except Exception:
            return False

    async def claim(self):
        async with self.sessions() as session, session.begin():
            # Running jobs are not blindly reclaimed: their external effects may be uncertain.
            job = await session.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at)
                                       .with_for_update(skip_locked=True).limit(1))
            if job:
                job.status, job.lease_until = "running", time.time() + 180
                job.lease_token = uuid.uuid4().hex
                job.attempts += 1
            return job


def record(session, kind, message, incident_id=None, **payload):
    session.add(Timeline(kind=kind, message=message, incident_id=incident_id, payload=payload))


def incident_dict(item):
    return {column.name: getattr(item, column.name) for column in Incident.__table__.columns}
