import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="companies")
    query: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    snapshots: Mapped[list["PageSnapshot"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scrape_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="companies")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    page_number: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    total_records: Mapped[int | None] = mapped_column(Integer)
    page_size: Mapped[int | None] = mapped_column(Integer)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[ScrapeRun] = relationship(back_populates="snapshots")


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        Index("ix_companies_legal_name", "legal_name"),
        Index("ix_companies_registration_number", "registration_number"),
        Index("ix_companies_city", "city"),
        Index("ix_companies_district", "district"),
        Index("ix_companies_activity", "activity"),
        Index("ix_companies_creation_date", "creation_date"),
        Index("ix_companies_source_type", "source_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_record_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="companies")
    legal_name: Mapped[str] = mapped_column(String(500), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(500))
    activity: Mapped[str | None] = mapped_column(Text)
    owner_first_name: Mapped[str | None] = mapped_column(String(300))
    owner_last_name: Mapped[str | None] = mapped_column(String(300))
    creation_date: Mapped[date | None] = mapped_column(Date)
    registration_number: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(200))
    district: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(320))
    do_not_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contact_notes: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    contact_attempts: Mapped[list["ContactAttempt"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class ContactAttempt(Base):
    __tablename__ = "contact_attempts"
    __table_args__ = (
        UniqueConstraint("company_id", "channel", name="uq_contact_attempt_company_channel"),
        UniqueConstraint("channel", "recipient", name="uq_contact_attempt_channel_recipient"),
        Index("ix_contact_attempts_status", "status"),
        Index("ix_contact_attempts_channel", "channel"),
        Index("ix_contact_attempts_sent_at", "sent_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="contact_attempts")


class IncomingMessage(Base):
    __tablename__ = "incoming_messages"
    __table_args__ = (
        UniqueConstraint(
            "instance", "provider_message_id", name="uq_incoming_message_instance_provider_id"
        ),
        Index("ix_incoming_messages_received_at", "received_at"),
        Index("ix_incoming_messages_read_at", "read_at"),
        Index("ix_incoming_messages_sender_phone", "sender_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )
    contact_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contact_attempts.id", ondelete="SET NULL")
    )
    instance: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(500), nullable=False)
    remote_jid: Mapped[str] = mapped_column(String(500), nullable=False)
    sender_phone: Mapped[str | None] = mapped_column(String(80))
    sender_name: Mapped[str | None] = mapped_column(String(500))
    message_type: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown")
    text: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
