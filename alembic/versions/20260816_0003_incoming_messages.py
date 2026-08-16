"""Store incoming Evolution API messages.

Revision ID: 20260816_0003
Revises: 20260811_0002
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260816_0003"
down_revision = "20260811_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incoming_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contact_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instance", sa.String(length=200), nullable=False),
        sa.Column("provider_message_id", sa.String(length=500), nullable=False),
        sa.Column("remote_jid", sa.String(length=500), nullable=False),
        sa.Column("sender_phone", sa.String(length=80), nullable=True),
        sa.Column("sender_name", sa.String(length=500), nullable=True),
        sa.Column("message_type", sa.String(length=100), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["contact_attempt_id"], ["contact_attempts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instance", "provider_message_id", name="uq_incoming_message_instance_provider_id"
        ),
    )
    op.create_index(
        "ix_incoming_messages_received_at", "incoming_messages", ["received_at"], unique=False
    )
    op.create_index("ix_incoming_messages_read_at", "incoming_messages", ["read_at"], unique=False)
    op.create_index(
        "ix_incoming_messages_sender_phone", "incoming_messages", ["sender_phone"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_incoming_messages_sender_phone", table_name="incoming_messages")
    op.drop_index("ix_incoming_messages_read_at", table_name="incoming_messages")
    op.drop_index("ix_incoming_messages_received_at", table_name="incoming_messages")
    op.drop_table("incoming_messages")
