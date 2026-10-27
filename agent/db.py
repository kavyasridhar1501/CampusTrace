"""SQLAlchemy 2 models for every table in the plan's data model.

infra/alembic/versions/0001_initial_schema.py creates the same schema by hand, and
tests/test_migrations.py checks that the two agree. The receipts and anchors tables are
append-only: a trigger in that migration rejects UPDATE, DELETE and TRUNCATE.
"""

import uuid
from datetime import datetime, time
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    FetchedValue,
    Float,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NODE_TYPES = (
    "building",
    "floor",
    "room",
    "hvac_zone",
    "air_handler",
    "plant",
    "panel",
    "circuit",
    "elevator_bank",
    "elevator",
    "water_heater",
    "plumbing_riser",
)
EDGE_TYPES = ("contains", "served_by", "part_of")
SCHEDULE_KINDS = ("class", "exam", "event")
REPORT_SOURCES = ("sim", "pilot", "311-eval")
PRIORITIES = ("P1", "P2", "P3", "P4")
TRADES = ("plumbing", "electrical", "hvac", "structural", "custodial", "grounds")
TICKET_STATUSES = (
    "new",
    "clarifying",
    "triaged",
    "merged_duplicate",
    "linked_root_job",
    "escalated",
    "scheduled",
    "inspection",
    "in_progress",
    "resolved_pending_check",
    "closed",
    "reopened",
)
FOLLOWUP_RESULTS = ("fixed", "not_fixed", "no_response")

# Repair-note embeddings. 1024 matches Voyage's default output size; changing the
# embedding model to one with another size needs a migration.
EMBEDDING_DIM = 1024


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


naming = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming)


class Asset(Base):
    """Every node in the asset graph, rooms included."""

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(_in("type", NODE_TYPES), name="type"),
        CheckConstraint("criticality BETWEEN 1 AND 5", name="criticality"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    building: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(Text)
    install_year: Mapped[int | None] = mapped_column(SmallInteger)
    criticality: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class AssetEdge(Base):
    __tablename__ = "asset_edges"
    __table_args__ = (CheckConstraint(_in("edge_type", EDGE_TYPES), name="edge_type"),)

    from_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    to_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True, index=True)
    edge_type: Mapped[str] = mapped_column(String(16), primary_key=True)


class Room(Base):
    __tablename__ = "rooms"

    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    capacity: Mapped[int] = mapped_column(Integer, server_default="0")
    room_type: Mapped[str] = mapped_column(String(32), index=True)


class ScheduleBlock(Base):
    __tablename__ = "schedule_blocks"
    __table_args__ = (
        CheckConstraint(_in("kind", SCHEDULE_KINDS), name="kind"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.asset_id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(Text)


class Crew(Base):
    __tablename__ = "crews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    trades: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    shift_days: Mapped[list[str]] = mapped_column(ARRAY(String(3)))
    shift_start: Mapped[time] = mapped_column(Time)
    shift_end: Mapped[time] = mapped_column(Time)
    home_building: Mapped[str] = mapped_column(String(16))


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (CheckConstraint(_in("source", REPORT_SOURCES), name="source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reporter_id: Mapped[str] = mapped_column(String(64), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    photo_path: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.asset_id"), index=True)
    symptom_type: Mapped[str | None] = mapped_column(String(32))
    safety_flags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    source: Mapped[str] = mapped_column(String(16))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint(_in("trade", TRADES), name="trade"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    ticket_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}"
    )
    trade: Mapped[str] = mapped_column(String(16))
    priority_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_estimate: Mapped[int | None] = mapped_column(Integer, comment="minutes")
    assigned_crew: Mapped[str | None] = mapped_column(ForeignKey("crews.id"))
    slot_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slot_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(_in("status", TICKET_STATUSES), name="status"),
        CheckConstraint(_in("priority", PRIORITIES), name="priority"),
        CheckConstraint(_in("trade", TRADES), name="trade"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), server_default="new", index=True)
    priority: Mapped[str | None] = mapped_column(String(2))
    trade: Mapped[str | None] = mapped_column(String(16))
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    report_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}"
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    approved_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Repair(Base):
    __tablename__ = "repairs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Null for simulated history entries that predate the jobs table.
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    symptom: Mapped[str] = mapped_column(String(32))
    confirmed_cause: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    parts: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    minutes: Mapped[int | None] = mapped_column(Integer)
    note_raw: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class Followup(Base):
    __tablename__ = "followups"
    __table_args__ = (CheckConstraint(_in("result", FOLLOWUP_RESULTS), name="result"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(16))


class Receipt(Base):
    """One ICE receipt. Append-only: the database rejects UPDATE, DELETE and TRUNCATE."""

    __tablename__ = "receipts"

    # Assigned by the receipts_link_and_number trigger after the chain-link check.
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default=FetchedValue())
    receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    previous_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    public_key_id: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Anchor(Base):
    """Chain tip published to the public anchor repo. Append-only like receipts."""

    __tablename__ = "anchors"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    seq: Mapped[int] = mapped_column(ForeignKey("receipts.seq"), index=True)
    tip_hash: Mapped[str] = mapped_column(String(64))
    anchored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    github_commit_sha: Mapped[str | None] = mapped_column(String(40))


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    policy: Mapped[str] = mapped_column(String(32))
    seed: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
