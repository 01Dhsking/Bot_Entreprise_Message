"""Add persistent paced WhatsApp queue.

Revision ID: 20260816_0004
Revises: 20260816_0003
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260816_0004"
down_revision = "20260816_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_queue_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("recipient", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_message_id", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["contact_attempt_id"], ["contact_attempts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contact_attempt_id", "kind", name="uq_outbound_queue_attempt_kind"
        ),
    )
    op.create_index(
        "ix_outbound_queue_due",
        "outbound_queue_items",
        ["status", "scheduled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbound_queue_due", table_name="outbound_queue_items")
    op.drop_table("outbound_queue_items")
