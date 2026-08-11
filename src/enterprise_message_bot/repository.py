import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, desc, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from .database import SessionFactory
from .models import Company, ContactAttempt, PageSnapshot, ScrapeRun
from .schemas import RegistryCompany, RegistryPage, parse_registry_date


class ContactBlockedError(RuntimeError):
    pass


class AlreadyContactedError(ContactBlockedError):
    pass


def company_source_key(company: RegistryCompany) -> str:
    identity_parts = (
        company.registration_number,
        company.legal_name,
        company.creation_date,
        company.city,
        company.phone,
    )
    identity = "|".join(value.strip().casefold() for value in identity_parts)
    if company.source_type != "companies":
        identity = f"{company.source_type}|{identity}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def company_to_dict(
    company: Company, contacted_channels: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": str(company.id),
        "source_type": company.source_type,
        "legal_name": company.legal_name,
        "trade_name": company.trade_name,
        "activity": company.activity,
        "owner_first_name": company.owner_first_name,
        "owner_last_name": company.owner_last_name,
        "creation_date": company.creation_date.isoformat() if company.creation_date else None,
        "registration_number": company.registration_number,
        "city": company.city,
        "district": company.district,
        "phone": company.phone,
        "email": company.email,
        "do_not_contact": company.do_not_contact,
        "contact_notes": company.contact_notes,
        "contacted_channels": contacted_channels or [],
        "first_seen_at": company.first_seen_at.isoformat(),
        "last_seen_at": company.last_seen_at.isoformat(),
    }


async def save_registry_page(page: RegistryPage, *, query: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        run = ScrapeRun(
            source_url=page.url,
            source_type=page.source_type,
            query=query,
            status="running",
            records_seen=len(page.companies),
        )
        session.add(run)
        await session.flush()

        try:
            session.add(
                PageSnapshot(
                    run_id=run.id,
                    source_type=page.source_type,
                    url=page.url,
                    title=page.title,
                    page_number=page.page_number,
                    total_pages=page.total_pages,
                    total_records=page.total_records,
                    page_size=page.raw_metadata.get("page_size"),
                    rows=[company.to_dict() for company in page.companies],
                    metadata_json={**page.raw_metadata, "source_type": page.source_type},
                )
            )

            for company in page.companies:
                values = {
                    "source_record_key": company_source_key(company),
                    "source_type": company.source_type,
                    "legal_name": company.legal_name,
                    "trade_name": company.trade_name or None,
                    "activity": company.activity or None,
                    "owner_first_name": company.owner_first_name or None,
                    "owner_last_name": company.owner_last_name or None,
                    "creation_date": parse_registry_date(company.creation_date),
                    "registration_number": company.registration_number or None,
                    "city": company.city or None,
                    "district": company.district or None,
                    "phone": company.phone or None,
                    "email": company.email or None,
                    "source_url": page.url,
                    "raw_data": company.to_dict(),
                    "last_seen_at": now,
                }
                statement = insert(Company).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=[Company.source_record_key],
                    set_={
                        key: value for key, value in values.items() if key != "source_record_key"
                    },
                )
                await session.execute(statement)

            run.status = "completed"
            run.records_saved = len(page.companies)
            run.completed_at = now
            await session.commit()
            return {
                "run_id": str(run.id),
                "source_type": page.source_type,
                "status": run.status,
                "records_seen": run.records_seen,
                "records_saved": run.records_saved,
                "page_number": page.page_number,
                "total_pages": page.total_pages,
                "total_records": page.total_records,
            }
        except Exception as exc:
            await session.rollback()
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = now
            session.add(run)
            await session.commit()
            raise


def _contact_exists(channel: str | None = None):
    criteria = [
        ContactAttempt.company_id == Company.id,
        ContactAttempt.status == "sent",
    ]
    if channel:
        criteria.append(ContactAttempt.channel == channel)
    return exists(select(ContactAttempt.id).where(and_(*criteria)))


def _apply_company_filters(
    statement,
    *,
    query: str | None = None,
    source_type: str | None = None,
    city: str | None = None,
    district: str | None = None,
    activity: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    channel: str | None = None,
    contact_status: str = "all",
    include_do_not_contact: bool = False,
):
    if query and query.strip():
        term = f"%{query.strip()}%"
        statement = statement.where(
            or_(
                Company.legal_name.ilike(term),
                Company.trade_name.ilike(term),
                Company.activity.ilike(term),
                Company.owner_first_name.ilike(term),
                Company.owner_last_name.ilike(term),
                Company.registration_number.ilike(term),
                Company.city.ilike(term),
                Company.district.ilike(term),
                Company.phone.ilike(term),
                Company.email.ilike(term),
            )
        )
    if source_type and source_type != "all":
        statement = statement.where(Company.source_type == source_type)
    if city:
        statement = statement.where(Company.city.ilike(f"%{city.strip()}%"))
    if district:
        statement = statement.where(Company.district.ilike(f"%{district.strip()}%"))
    if activity:
        statement = statement.where(Company.activity.ilike(f"%{activity.strip()}%"))
    if created_from:
        statement = statement.where(Company.creation_date >= created_from)
    if created_to:
        statement = statement.where(Company.creation_date <= created_to)
    if channel == "email":
        statement = statement.where(Company.email.is_not(None), Company.email != "")
    elif channel == "whatsapp":
        statement = statement.where(Company.phone.is_not(None), Company.phone != "")
    if contact_status == "contacted":
        statement = statement.where(_contact_exists(channel))
    elif contact_status == "uncontacted":
        statement = statement.where(~_contact_exists(channel))
    elif contact_status != "all":
        raise ValueError("contact_status must be all, contacted or uncontacted")
    if not include_do_not_contact:
        statement = statement.where(Company.do_not_contact.is_(False))
    return statement


async def list_companies(
    *,
    query: str | None = None,
    source_type: str | None = None,
    city: str | None = None,
    district: str | None = None,
    activity: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    channel: str | None = None,
    contact_status: str = "all",
    include_do_not_contact: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    safe_limit = min(max(limit, 1), 1000)
    statement = select(Company).order_by(Company.legal_name.asc()).limit(safe_limit)
    statement = _apply_company_filters(
        statement,
        query=query,
        source_type=source_type,
        city=city,
        district=district,
        activity=activity,
        created_from=created_from,
        created_to=created_to,
        channel=channel,
        contact_status=contact_status,
        include_do_not_contact=include_do_not_contact,
    )

    async with SessionFactory() as session:
        companies = (await session.scalars(statement)).all()
        company_ids = [company.id for company in companies]
        contacted: dict[uuid.UUID, list[str]] = {}
        if company_ids:
            attempts = (
                await session.execute(
                    select(ContactAttempt.company_id, ContactAttempt.channel).where(
                        ContactAttempt.company_id.in_(company_ids),
                        ContactAttempt.status == "sent",
                    )
                )
            ).all()
            for company_id, contacted_channel in attempts:
                contacted.setdefault(company_id, []).append(contacted_channel)
        return [company_to_dict(company, contacted.get(company.id)) for company in companies]


async def get_company(company_id: str | uuid.UUID) -> dict[str, Any] | None:
    normalized_id = uuid.UUID(str(company_id))
    async with SessionFactory() as session:
        company = await session.get(Company, normalized_id)
        if company is None:
            return None
        channels = (
            await session.scalars(
                select(ContactAttempt.channel).where(
                    ContactAttempt.company_id == company.id,
                    ContactAttempt.status == "sent",
                )
            )
        ).all()
        return company_to_dict(company, list(channels))


async def reserve_contact_attempt(
    *,
    company_id: str,
    channel: str,
    recipient: str,
    subject: str | None,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_id = uuid.UUID(company_id)
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        company = await session.scalar(
            select(Company).where(Company.id == normalized_id).with_for_update()
        )
        if company is None:
            raise ValueError("Company not found")
        if company.do_not_contact:
            raise ContactBlockedError("This company is marked as do not contact")

        attempt = await session.scalar(
            select(ContactAttempt)
            .where(
                ContactAttempt.company_id == normalized_id,
                ContactAttempt.channel == channel,
            )
            .with_for_update()
        )
        if attempt and attempt.status == "sent":
            raise AlreadyContactedError(f"This company was already contacted by {channel}")
        if attempt and attempt.status == "pending":
            raise AlreadyContactedError(
                f"A {channel} send is already registered and requires manual review"
            )

        if attempt is None:
            attempt = ContactAttempt(company_id=normalized_id, channel=channel, recipient=recipient)
            session.add(attempt)
        attempt.recipient = recipient
        attempt.subject = subject
        attempt.body = body
        attempt.status = "pending"
        attempt.error_message = None
        attempt.metadata_json = metadata or {}
        attempt.attempted_at = now
        attempt.sent_at = None
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise AlreadyContactedError(f"A {channel} send is already registered") from exc
        return {"attempt_id": str(attempt.id), "company": company_to_dict(company)}


async def complete_contact_attempt(
    attempt_id: str,
    *,
    success: bool,
    provider_message_id: str | None = None,
    error_message: str | None = None,
) -> None:
    async with SessionFactory() as session:
        attempt = await session.get(ContactAttempt, uuid.UUID(attempt_id))
        if attempt is None:
            raise ValueError("Contact attempt not found")
        attempt.status = "sent" if success else "failed"
        attempt.provider_message_id = provider_message_id
        attempt.error_message = error_message
        attempt.sent_at = datetime.now(UTC) if success else None
        await session.commit()


async def list_contact_history(
    *,
    channel: str | None = None,
    source_type: str | None = None,
    status: str = "sent",
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_limit = min(max(limit, 1), 1000)
    statement = (
        select(ContactAttempt, Company)
        .join(Company, Company.id == ContactAttempt.company_id)
        .order_by(desc(ContactAttempt.attempted_at))
        .limit(safe_limit)
    )
    if channel and channel != "all":
        statement = statement.where(ContactAttempt.channel == channel)
    if source_type and source_type != "all":
        statement = statement.where(Company.source_type == source_type)
    if status != "all":
        if status not in {"pending", "sent", "failed"}:
            raise ValueError("status must be all, pending, sent or failed")
        statement = statement.where(ContactAttempt.status == status)
    async with SessionFactory() as session:
        rows = (await session.execute(statement)).all()
        return [
            {
                "attempt_id": str(attempt.id),
                "channel": attempt.channel,
                "recipient": attempt.recipient,
                "subject": attempt.subject,
                "status": attempt.status,
                "error_message": attempt.error_message,
                "attempted_at": attempt.attempted_at.isoformat(),
                "sent_at": attempt.sent_at.isoformat() if attempt.sent_at else None,
                "company": company_to_dict(
                    company, [attempt.channel] if attempt.status == "sent" else []
                ),
            }
            for attempt, company in rows
        ]


async def set_do_not_contact(
    company_id: str, blocked: bool, notes: str | None = None
) -> dict[str, Any]:
    async with SessionFactory() as session:
        company = await session.get(Company, uuid.UUID(company_id))
        if company is None:
            raise ValueError("Company not found")
        company.do_not_contact = blocked
        company.contact_notes = notes
        await session.commit()
        return company_to_dict(company)


async def repository_stats() -> dict[str, int]:
    async with SessionFactory() as session:
        company_count = await session.scalar(select(func.count()).select_from(Company))
        run_count = await session.scalar(select(func.count()).select_from(ScrapeRun))
        snapshot_count = await session.scalar(select(func.count()).select_from(PageSnapshot))
        contacted_count = await session.scalar(
            select(func.count()).select_from(ContactAttempt).where(ContactAttempt.status == "sent")
        )
        return {
            "companies": int(company_count or 0),
            "runs": int(run_count or 0),
            "snapshots": int(snapshot_count or 0),
            "sent_contacts": int(contacted_count or 0),
        }


async def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = min(max(limit, 1), 100)
    statement = select(ScrapeRun).order_by(desc(ScrapeRun.started_at)).limit(safe_limit)
    async with SessionFactory() as session:
        runs = (await session.scalars(statement)).all()
        return [
            {
                "id": str(run.id),
                "source_type": run.source_type,
                "status": run.status,
                "query": run.query,
                "records_seen": run.records_seen,
                "records_saved": run.records_saved,
                "error_message": run.error_message,
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }
            for run in runs
        ]


async def saved_page_numbers(
    source_type: str, page_size: int, start_page: int, end_page: int
) -> set[int]:
    statement = (
        select(PageSnapshot.page_number)
        .join(ScrapeRun, ScrapeRun.id == PageSnapshot.run_id)
        .where(
            PageSnapshot.source_type == source_type,
            PageSnapshot.page_size == page_size,
            PageSnapshot.page_number >= start_page,
            PageSnapshot.page_number <= end_page,
            ScrapeRun.query.is_(None),
            ScrapeRun.status == "completed",
        )
    )
    async with SessionFactory() as session:
        return {value for value in (await session.scalars(statement)).all() if value is not None}
