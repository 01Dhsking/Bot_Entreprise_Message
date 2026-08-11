"""Create company directory persistence tables.

Revision ID: 20260811_0001
Revises:
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260811_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scrape_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("records_seen", sa.Integer(), nullable=False),
        sa.Column("records_saved", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_record_key", sa.String(length=64), nullable=False),
        sa.Column("legal_name", sa.String(length=500), nullable=False),
        sa.Column("trade_name", sa.String(length=500), nullable=True),
        sa.Column("creation_date", sa.Date(), nullable=True),
        sa.Column("registration_number", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=200), nullable=True),
        sa.Column("district", sa.String(length=300), nullable=True),
        sa.Column("phone", sa.String(length=150), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_record_key"),
    )
    op.create_index("ix_companies_city", "companies", ["city"], unique=False)
    op.create_index("ix_companies_legal_name", "companies", ["legal_name"], unique=False)
    op.create_index(
        "ix_companies_registration_number", "companies", ["registration_number"], unique=False
    )
    op.create_table(
        "page_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("total_records", sa.Integer(), nullable=True),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["scrape_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("page_snapshots")
    op.drop_index("ix_companies_registration_number", table_name="companies")
    op.drop_index("ix_companies_legal_name", table_name="companies")
    op.drop_index("ix_companies_city", table_name="companies")
    op.drop_table("companies")
    op.drop_table("scrape_runs")
