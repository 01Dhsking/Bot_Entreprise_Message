"""Add dual registry fields and contact history.

Revision ID: 20260811_0002
Revises: 20260811_0001
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260811_0002"
down_revision = "20260811_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scrape_runs",
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="companies",
            nullable=False,
        ),
    )
    op.add_column(
        "page_snapshots",
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="companies",
            nullable=False,
        ),
    )
    op.add_column("page_snapshots", sa.Column("page_size", sa.Integer(), nullable=True))
    op.create_index(
        "ix_page_snapshots_collection",
        "page_snapshots",
        ["source_type", "page_size", "page_number"],
        unique=False,
    )
    op.add_column(
        "companies",
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="companies",
            nullable=False,
        ),
    )
    op.add_column("companies", sa.Column("activity", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("owner_first_name", sa.String(300), nullable=True))
    op.add_column("companies", sa.Column("owner_last_name", sa.String(300), nullable=True))
    op.add_column(
        "companies",
        sa.Column("do_not_contact", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("companies", sa.Column("contact_notes", sa.Text(), nullable=True))
    op.create_index("ix_companies_source_type", "companies", ["source_type"], unique=False)
    op.create_index("ix_companies_activity", "companies", ["activity"], unique=False)
    op.create_index("ix_companies_district", "companies", ["district"], unique=False)
    op.create_index("ix_companies_creation_date", "companies", ["creation_date"], unique=False)

    op.create_table(
        "contact_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("recipient", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider_message_id", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "channel", name="uq_contact_attempt_company_channel"),
        sa.UniqueConstraint("channel", "recipient", name="uq_contact_attempt_channel_recipient"),
    )
    op.create_index("ix_contact_attempts_channel", "contact_attempts", ["channel"], unique=False)
    op.create_index("ix_contact_attempts_sent_at", "contact_attempts", ["sent_at"], unique=False)
    op.create_index("ix_contact_attempts_status", "contact_attempts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_contact_attempts_status", table_name="contact_attempts")
    op.drop_index("ix_contact_attempts_sent_at", table_name="contact_attempts")
    op.drop_index("ix_contact_attempts_channel", table_name="contact_attempts")
    op.drop_table("contact_attempts")
    op.drop_index("ix_page_snapshots_collection", table_name="page_snapshots")
    op.drop_column("page_snapshots", "page_size")
    op.drop_column("page_snapshots", "source_type")
    op.drop_index("ix_companies_creation_date", table_name="companies")
    op.drop_index("ix_companies_district", table_name="companies")
    op.drop_index("ix_companies_activity", table_name="companies")
    op.drop_index("ix_companies_source_type", table_name="companies")
    op.drop_column("companies", "contact_notes")
    op.drop_column("companies", "do_not_contact")
    op.drop_column("companies", "owner_last_name")
    op.drop_column("companies", "owner_first_name")
    op.drop_column("companies", "activity")
    op.drop_column("companies", "source_type")
    op.drop_column("scrape_runs", "source_type")
