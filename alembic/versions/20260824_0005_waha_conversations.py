"""Add WAHA multi-session conversations and planned messages.

Revision ID: 20260824_0005
Revises: 20260816_0004
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260824_0005"
down_revision = "20260816_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incoming_messages",
        sa.Column("provider", sa.String(length=30), server_default="evolution_api", nullable=False),
    )
    op.drop_constraint(
        "uq_incoming_message_instance_provider_id", "incoming_messages", type_="unique"
    )
    op.create_unique_constraint(
        "uq_incoming_message_provider_instance_id",
        "incoming_messages",
        ["provider", "instance", "provider_message_id"],
    )
    op.create_table(
        "whatsapp_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("session_name", sa.String(length=200), nullable=False),
        sa.Column("remote_jid", sa.String(length=500), nullable=False),
        sa.Column("phone", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("automatic_message_limit", sa.Integer(), nullable=False),
        sa.Column("automatic_messages_sent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "session_name", "remote_jid", name="uq_whatsapp_conversation_address"
        ),
    )
    op.create_index("ix_whatsapp_conversations_updated", "whatsapp_conversations", ["updated_at"])
    op.create_index("ix_whatsapp_conversations_phone", "whatsapp_conversations", ["phone"])
    op.create_table(
        "whatsapp_conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=500), nullable=True),
        sa.Column("in_reply_to_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["whatsapp_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["in_reply_to_message_id"], ["incoming_messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_whatsapp_conversation_messages_due",
        "whatsapp_conversation_messages",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_whatsapp_conversation_messages_conversation",
        "whatsapp_conversation_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("whatsapp_conversation_messages")
    op.drop_table("whatsapp_conversations")
    op.drop_constraint(
        "uq_incoming_message_provider_instance_id", "incoming_messages", type_="unique"
    )
    op.create_unique_constraint(
        "uq_incoming_message_instance_provider_id",
        "incoming_messages",
        ["instance", "provider_message_id"],
    )
    op.drop_column("incoming_messages", "provider")
