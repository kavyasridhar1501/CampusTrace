"""Initial schema: extensions, every table in the plan, and the append-only receipts ledger.

Revision ID: 0001
Revises:
Create Date: 2026-10-27

Receipts and anchors are append-only. Row triggers reject UPDATE and DELETE, statement
triggers reject TRUNCATE. A BEFORE INSERT trigger on receipts serialises writers with an
advisory lock, checks that previous_hash equals the current chain tip ('genesis' for the
first receipt), and only then assigns seq, so seq order always equals chain order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NODE_TYPES = (
    "'building', 'floor', 'room', 'hvac_zone', 'air_handler', 'plant', 'panel', "
    "'circuit', 'elevator_bank', 'elevator', 'water_heater', 'plumbing_riser'"
)
TRADES = "'plumbing', 'electrical', 'hvac', 'structural', 'custodial', 'grounds'"
TICKET_STATUSES = (
    "'new', 'clarifying', 'triaged', 'merged_duplicate', 'linked_root_job', 'escalated', "
    "'scheduled', 'inspection', 'in_progress', 'resolved_pending_check', 'closed', 'reopened'"
)

APPEND_ONLY_SQL = """
CREATE FUNCTION reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER receipts_no_update_delete
    BEFORE UPDATE OR DELETE ON receipts
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER receipts_no_truncate
    BEFORE TRUNCATE ON receipts
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

CREATE TRIGGER anchors_no_update_delete
    BEFORE UPDATE OR DELETE ON anchors
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER anchors_no_truncate
    BEFORE TRUNCATE ON anchors
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

CREATE SEQUENCE receipts_seq AS bigint OWNED BY receipts.seq;

CREATE FUNCTION receipts_link_and_number() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    tip text;
BEGIN
    -- Held until commit, so concurrent writers append one at a time.
    PERFORM pg_advisory_xact_lock(hashtext('campustrace.receipts'));
    SELECT content_hash INTO tip FROM receipts ORDER BY seq DESC LIMIT 1;
    IF tip IS NULL THEN
        tip := 'genesis';
    END IF;
    IF NEW.previous_hash IS DISTINCT FROM tip THEN
        RAISE EXCEPTION 'receipt previous_hash % does not match chain tip %',
            NEW.previous_hash, tip
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.seq := nextval('receipts_seq');
    NEW.recorded_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER receipts_link_and_number
    BEFORE INSERT ON receipts
    FOR EACH ROW EXECUTE FUNCTION receipts_link_and_number();

CREATE FUNCTION anchors_check_tip() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM receipts WHERE seq = NEW.seq AND content_hash = NEW.tip_hash
    ) THEN
        RAISE EXCEPTION 'anchor tip_hash % does not match receipt seq %', NEW.tip_hash, NEW.seq
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER anchors_check_tip
    BEFORE INSERT ON anchors
    FOR EACH ROW EXECUTE FUNCTION anchors_check_tip();
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "assets",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("building", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("install_year", sa.SmallInteger(), nullable=True),
        sa.Column("criticality", sa.SmallInteger(), server_default="3", nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.CheckConstraint(f"type IN ({NODE_TYPES})", name=op.f("ck_assets_type")),
        sa.CheckConstraint("criticality BETWEEN 1 AND 5", name=op.f("ck_assets_criticality")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    op.create_index(op.f("ix_assets_building"), "assets", ["building"])
    op.create_index(op.f("ix_assets_type"), "assets", ["type"])

    op.create_table(
        "asset_edges",
        sa.Column("from_id", sa.String(64), nullable=False),
        sa.Column("to_id", sa.String(64), nullable=False),
        sa.Column("edge_type", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "edge_type IN ('contains', 'served_by', 'part_of')",
            name=op.f("ck_asset_edges_edge_type"),
        ),
        sa.ForeignKeyConstraint(
            ["from_id"], ["assets.id"], name=op.f("fk_asset_edges_from_id_assets")
        ),
        sa.ForeignKeyConstraint(["to_id"], ["assets.id"], name=op.f("fk_asset_edges_to_id_assets")),
        sa.PrimaryKeyConstraint("from_id", "to_id", "edge_type", name=op.f("pk_asset_edges")),
    )
    op.create_index(op.f("ix_asset_edges_to_id"), "asset_edges", ["to_id"])

    op.create_table(
        "rooms",
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("capacity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("room_type", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name=op.f("fk_rooms_asset_id_assets")),
        sa.PrimaryKeyConstraint("asset_id", name=op.f("pk_rooms")),
    )
    op.create_index(op.f("ix_rooms_room_type"), "rooms", ["room_type"])

    op.create_table(
        "schedule_blocks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("room_id", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('class', 'exam', 'event')", name=op.f("ck_schedule_blocks_kind")
        ),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_schedule_blocks_ends_after_start")),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.asset_id"], name=op.f("fk_schedule_blocks_room_id_rooms")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_blocks")),
    )
    op.create_index(op.f("ix_schedule_blocks_room_id"), "schedule_blocks", ["room_id"])

    op.create_table(
        "crews",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("trades", sa.ARRAY(sa.String(16)), nullable=False),
        sa.Column("shift_days", sa.ARRAY(sa.String(3)), nullable=False),
        sa.Column("shift_start", sa.Time(), nullable=False),
        sa.Column("shift_end", sa.Time(), nullable=False),
        sa.Column("home_building", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crews")),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("reporter_id", sa.String(64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("photo_path", sa.Text(), nullable=True),
        sa.Column(
            "fields", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("room_id", sa.String(64), nullable=True),
        sa.Column("symptom_type", sa.String(32), nullable=True),
        sa.Column("safety_flags", sa.ARRAY(sa.String(32)), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "source IN ('sim', 'pilot', '311-eval')", name=op.f("ck_reports_source")
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.asset_id"], name=op.f("fk_reports_room_id_rooms")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
    )
    op.create_index(op.f("ix_reports_created_at"), "reports", ["created_at"])
    op.create_index(op.f("ix_reports_reporter_id"), "reports", ["reporter_id"])
    op.create_index(op.f("ix_reports_room_id"), "reports", ["room_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("ticket_ids", sa.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("trade", sa.String(16), nullable=False),
        sa.Column("priority_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_estimate", sa.Integer(), nullable=True, comment="minutes"),
        sa.Column("assigned_crew", sa.String(32), nullable=True),
        sa.Column("slot_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slot_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.CheckConstraint(f"trade IN ({TRADES})", name=op.f("ck_jobs_trade")),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name=op.f("fk_jobs_asset_id_assets")),
        sa.ForeignKeyConstraint(
            ["assigned_crew"], ["crews.id"], name=op.f("fk_jobs_assigned_crew_crews")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(op.f("ix_jobs_asset_id"), "jobs", ["asset_id"])

    op.create_table(
        "tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="new", nullable=False),
        sa.Column("priority", sa.String(2), nullable=True),
        sa.Column("trade", sa.String(16), nullable=True),
        sa.Column("parent_job_id", sa.UUID(), nullable=True),
        sa.Column("report_ids", sa.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority IN ('P1', 'P2', 'P3', 'P4')", name=op.f("ck_tickets_priority")
        ),
        sa.CheckConstraint(f"status IN ({TICKET_STATUSES})", name=op.f("ck_tickets_status")),
        sa.CheckConstraint(f"trade IN ({TRADES})", name=op.f("ck_tickets_trade")),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name=op.f("ck_tickets_confidence")),
        sa.ForeignKeyConstraint(
            ["parent_job_id"], ["jobs.id"], name=op.f("fk_tickets_parent_job_id_jobs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("thread_id", name=op.f("uq_tickets_thread_id")),
    )
    op.create_index(op.f("ix_tickets_parent_job_id"), "tickets", ["parent_job_id"])
    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"])

    op.create_table(
        "repairs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("symptom", sa.String(32), nullable=False),
        sa.Column("confirmed_cause", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("parts", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("note_raw", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name=op.f("fk_repairs_asset_id_assets")
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_repairs_job_id_jobs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repairs")),
    )
    op.create_index(op.f("ix_repairs_asset_id"), "repairs", ["asset_id"])
    op.create_index(op.f("ix_repairs_completed_at"), "repairs", ["completed_at"])
    op.create_index(op.f("ix_repairs_job_id"), "repairs", ["job_id"])

    op.create_table(
        "followups",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("result", sa.String(16), nullable=True),
        sa.CheckConstraint(
            "result IN ('fixed', 'not_fixed', 'no_response')", name=op.f("ck_followups_result")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_followups_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_followups")),
    )
    op.create_index(op.f("ix_followups_ticket_id"), "followups", ["ticket_id"])

    # seq has no default: the insert trigger assigns it from receipts_seq after the
    # chain-link check.
    op.create_table(
        "receipts",
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("receipt_id", sa.UUID(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("public_key_id", sa.String(64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_receipts")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_receipts_content_hash")),
        sa.UniqueConstraint("receipt_id", name=op.f("uq_receipts_receipt_id")),
    )

    op.create_table(
        "anchors",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("tip_hash", sa.String(64), nullable=False),
        sa.Column(
            "anchored_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("github_commit_sha", sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(["seq"], ["receipts.seq"], name=op.f("fk_anchors_seq_receipts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anchors")),
    )
    op.create_index(op.f("ix_anchors_seq"), "anchors", ["seq"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )

    op.execute(APPEND_ONLY_SQL)


def downgrade() -> None:
    # Downgrading would drop the receipts table, which is a delete of every receipt.
    raise NotImplementedError("0001 cannot be downgraded: the receipts ledger is append-only.")
