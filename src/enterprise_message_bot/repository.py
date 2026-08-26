import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, desc, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .conversation import campaign_delay_seconds, classify_permission_reply
from .database import SessionFactory
from .models import (
    Company,
    ContactAttempt,
    IncomingMessage,
    OutboundQueueItem,
    PageSnapshot,
    ScrapeRun,
    WhatsAppConversation,
    WhatsAppConversationMessage,
)
from .schemas import RegistryCompany, RegistryPage, parse_registry_date

settings = get_settings()

FIDELAPP_OFFER_MESSAGE = (
    "Je m'appelle Ulrich, je suis de FidelApp, une société qui aide les boutiques et "
    "magasins à booster leurs ventes drastiquement. J'ai une offre pour vous, voulez-vous "
    "l'écouter ?"
)
FIDELAPP_PRESENTATION_CAPTION = (
    "Voici le tout nouvel outil qui marche très bien en ce moment avec les magasins "
    "avec lesquels nous collaborons déjà.\n\n"
    "FidelApp est une application mobile disponible sur le store qui vous aide simplement à transformer les clients qui viennent une fois "
    "chez vous en clients qui reviennent régulièrement."
    ""
    "Ceci est un PowerPoint qui explique comment cela vous aide."
    "C'est quelque chose qui vous intéresse ?, notre équipe est la pour vous aider a le mettre en place"
)


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


async def queue_outbound_message(
    *,
    attempt_id: str,
    kind: str,
    recipient: str,
    body: str,
    scheduled_at: datetime,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with SessionFactory() as session:
        normalized_attempt_id = uuid.UUID(attempt_id)
        item = await session.scalar(
            select(OutboundQueueItem)
            .where(
                OutboundQueueItem.contact_attempt_id == normalized_attempt_id,
                OutboundQueueItem.kind == kind,
            )
            .with_for_update()
        )
        if item and item.status == "sent":
            raise AlreadyContactedError("This queued message was already sent")
        if item and item.status in {"queued", "sending"}:
            return {
                "queue_item_id": str(item.id),
                "status": item.status,
                "scheduled_at": item.scheduled_at.isoformat(),
            }
        if item is None:
            item = OutboundQueueItem(contact_attempt_id=normalized_attempt_id, kind=kind)
            session.add(item)
        item.recipient = recipient
        item.body = body
        item.status = "queued"
        item.scheduled_at = scheduled_at
        item.provider_message_id = None
        item.error_message = None
        item.metadata_json = metadata or {}
        item.sent_at = None
        await session.commit()
        return {
            "queue_item_id": str(item.id),
            "status": item.status,
            "scheduled_at": item.scheduled_at.isoformat(),
        }


async def next_whatsapp_campaign_start() -> datetime:
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        latest = await session.scalar(
            select(func.max(OutboundQueueItem.scheduled_at)).where(
                OutboundQueueItem.status.in_(["queued", "sending"]),
                OutboundQueueItem.kind == "permission_opener",
            )
        )
    return max(now, latest + timedelta(seconds=30)) if latest else now


async def outbound_dispatch_wait_seconds() -> float:
    async with SessionFactory() as session:
        latest_sent = await session.scalar(
            select(func.max(OutboundQueueItem.sent_at)).where(OutboundQueueItem.status == "sent")
        )
        sent_count = await session.scalar(
            select(func.count())
            .select_from(OutboundQueueItem)
            .where(OutboundQueueItem.status == "sent")
        )
    if latest_sent is None:
        return 0
    required_delay = campaign_delay_seconds(max(int(sent_count or 1) - 1, 0))
    ready_at = latest_sent + timedelta(seconds=required_delay)
    return max((ready_at - datetime.now(UTC)).total_seconds(), 0)


async def claim_next_outbound_message() -> dict[str, Any] | None:
    async with SessionFactory() as session:
        item = await session.scalar(
            select(OutboundQueueItem)
            .where(
                OutboundQueueItem.status == "queued",
                OutboundQueueItem.scheduled_at <= datetime.now(UTC),
            )
            .order_by(OutboundQueueItem.scheduled_at, OutboundQueueItem.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        item.status = "sending"
        item.attempt_count += 1
        await session.commit()
        return {
            "id": str(item.id),
            "attempt_id": str(item.contact_attempt_id),
            "kind": item.kind,
            "recipient": item.recipient,
            "body": item.body,
            "provider": (item.metadata_json or {}).get("provider"),
            "session": (item.metadata_json or {}).get("session"),
        }


async def complete_outbound_message(
    queue_item_id: str,
    *,
    success: bool,
    provider_message_id: str | None = None,
    error_message: str | None = None,
) -> None:
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        item = await session.get(OutboundQueueItem, uuid.UUID(queue_item_id))
        if item is None:
            raise ValueError("Outbound queue item not found")
        attempt = await session.get(ContactAttempt, item.contact_attempt_id)
        if attempt is None:
            raise ValueError("Contact attempt not found")

        item.status = "sent" if success else "failed"
        item.provider_message_id = provider_message_id
        item.error_message = error_message
        item.sent_at = now if success else None
        metadata = dict(attempt.metadata_json or {})
        if item.kind == "permission_opener":
            attempt.status = "sent" if success else "failed"
            attempt.provider_message_id = provider_message_id
            attempt.error_message = error_message
            attempt.sent_at = now if success else None
            metadata["conversation_stage"] = "awaiting_permission" if success else "opener_failed"
        elif item.kind == "campaign_follow_up":
            metadata["conversation_stage"] = "follow_up_sent" if success else "follow_up_failed"
            metadata["follow_up_provider_message_id"] = provider_message_id
            metadata["follow_up_error"] = error_message
        if success:
            item_metadata = dict(item.metadata_json or {})
            provider = str(item_metadata.get("provider") or "").strip()
            session_name = str(item_metadata.get("session") or "").strip()
            if provider and session_name:
                remote_suffix = "@c.us" if provider == "waha" else "@s.whatsapp.net"
                conversation = await _get_or_create_conversation(
                    session,
                    provider=provider,
                    session_name=session_name,
                    remote_jid=f"{item.recipient}{remote_suffix}",
                    phone=item.recipient,
                    company_id=attempt.company_id,
                )
                conversation.automatic_messages_sent = min(
                    conversation.automatic_messages_sent + 1,
                    conversation.automatic_message_limit,
                )
                if conversation.automatic_messages_sent >= conversation.automatic_message_limit:
                    conversation.mode = "ai"
                session.add(
                    WhatsAppConversationMessage(
                        conversation_id=conversation.id,
                        direction="outgoing",
                        source="automatic",
                        status="sent",
                        text=item.body,
                        provider_message_id=provider_message_id,
                        sent_at=now,
                        metadata_json={"kind": item.kind},
                    )
                )
        attempt.metadata_json = metadata
        await session.commit()


async def handle_permission_reply(
    sender_phone: str | None,
    text: str | None,
    *,
    provider: str | None = None,
    session_name: str | None = None,
) -> dict[str, Any]:
    if not sender_phone:
        return {"action": "ignored", "reason": "sender phone is unavailable"}
    async with SessionFactory() as session:
        attempt = await session.scalar(
            select(ContactAttempt)
            .where(
                ContactAttempt.channel == "whatsapp",
                ContactAttempt.recipient == sender_phone,
                ContactAttempt.status == "sent",
            )
            .order_by(desc(ContactAttempt.sent_at))
            .with_for_update()
            .limit(1)
        )
        if attempt is None:
            return {"action": "ignored", "reason": "no matching WhatsApp campaign"}
        metadata = dict(attempt.metadata_json or {})
        expected_provider = metadata.get("provider")
        expected_session = metadata.get("session")
        if expected_provider and provider and expected_provider != provider:
            return {"action": "ignored", "reason": "campaign belongs to another provider"}
        if expected_session and session_name and expected_session != session_name:
            return {"action": "ignored", "reason": "campaign belongs to another session"}
        if metadata.get("conversation_stage") != "awaiting_permission":
            return {"action": "ignored", "reason": "campaign is not awaiting permission"}

        classification = classify_permission_reply(text)
        if classification == "opt_out":
            company = await session.get(Company, attempt.company_id)
            if company:
                company.do_not_contact = True
                company.contact_notes = "WhatsApp opt-out received"
            metadata["conversation_stage"] = "opted_out"
        elif classification == "negative":
            metadata["conversation_stage"] = "permission_declined"
        elif classification == "ambiguous":
            metadata["conversation_stage"] = "awaiting_permission"
            metadata["last_reply_classification"] = "ambiguous"
        else:
            follow_up_body = str(metadata.get("follow_up_body") or "").strip()
            if not follow_up_body:
                metadata["conversation_stage"] = "awaiting_permission"
                metadata["last_reply_classification"] = "ambiguous"
                classification = "ambiguous"
            else:
                session.add(
                    OutboundQueueItem(
                        contact_attempt_id=attempt.id,
                        kind="campaign_follow_up",
                        recipient=attempt.recipient,
                        body=follow_up_body,
                        status="queued",
                        scheduled_at=datetime.now(UTC),
                        metadata_json={
                            "trigger": "positive_permission_reply",
                            "provider": provider or expected_provider,
                            "session": session_name or expected_session,
                        },
                    )
                )
                metadata["conversation_stage"] = "follow_up_queued"
        attempt.metadata_json = metadata
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"action": "ignored", "reason": "follow-up is already queued"}
        return {"action": classification, "stage": metadata["conversation_stage"]}


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
                "conversation_stage": (attempt.metadata_json or {}).get("conversation_stage"),
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
        incoming_count = await session.scalar(select(func.count()).select_from(IncomingMessage))
        unread_count = await session.scalar(
            select(func.count())
            .select_from(IncomingMessage)
            .where(IncomingMessage.read_at.is_(None))
        )
        queued_count = await session.scalar(
            select(func.count())
            .select_from(OutboundQueueItem)
            .where(OutboundQueueItem.status.in_(["queued", "sending"]))
        )
        return {
            "companies": int(company_count or 0),
            "runs": int(run_count or 0),
            "snapshots": int(snapshot_count or 0),
            "sent_contacts": int(contacted_count or 0),
            "incoming_messages": int(incoming_count or 0),
            "unread_messages": int(unread_count or 0),
            "queued_whatsapp_messages": int(queued_count or 0),
        }


async def save_incoming_message(message: dict[str, Any]) -> dict[str, Any]:
    async with SessionFactory() as session:
        existing = await session.scalar(
            select(IncomingMessage).where(
                IncomingMessage.provider == message.get("provider", "evolution_api"),
                IncomingMessage.instance == message["instance"],
                IncomingMessage.provider_message_id == message["provider_message_id"],
            )
        )
        if existing:
            return {"message_id": str(existing.id), "created": False}

        attempt = None
        sender_phone = message.get("sender_phone")
        if sender_phone:
            attempt = await session.scalar(
                select(ContactAttempt)
                .where(
                    ContactAttempt.channel == "whatsapp",
                    ContactAttempt.recipient == sender_phone,
                )
                .order_by(desc(ContactAttempt.attempted_at))
                .limit(1)
            )
        incoming = IncomingMessage(
            company_id=attempt.company_id if attempt else None,
            contact_attempt_id=attempt.id if attempt else None,
            **message,
        )
        session.add(incoming)
        conversation = await _get_or_create_conversation(
            session,
            provider=str(message.get("provider") or "evolution_api"),
            session_name=message["instance"],
            remote_jid=message["remote_jid"],
            phone=str(sender_phone or ""),
            display_name=message.get("sender_name"),
            company_id=attempt.company_id if attempt else None,
        )
        session.add(
            WhatsAppConversationMessage(
                conversation_id=conversation.id,
                direction="incoming",
                source="customer",
                status="received",
                text=message.get("text"),
                provider_message_id=message["provider_message_id"],
                metadata_json={"message_type": message.get("message_type", "unknown")},
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            duplicate = await session.scalar(
                select(IncomingMessage).where(
                    IncomingMessage.provider == message.get("provider", "evolution_api"),
                    IncomingMessage.instance == message["instance"],
                    IncomingMessage.provider_message_id == message["provider_message_id"],
                )
            )
            if duplicate:
                return {"message_id": str(duplicate.id), "created": False}
            raise
        return {
            "message_id": str(incoming.id),
            "conversation_id": str(conversation.id),
            "created": True,
        }


async def list_incoming_messages(
    *, unread_only: bool = True, sender_phone: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    statement = (
        select(IncomingMessage, Company)
        .outerjoin(Company, Company.id == IncomingMessage.company_id)
        .order_by(desc(IncomingMessage.received_at))
        .limit(min(max(limit, 1), 200))
    )
    if unread_only:
        statement = statement.where(IncomingMessage.read_at.is_(None))
    if sender_phone:
        digits = "".join(character for character in sender_phone if character.isdigit())
        statement = statement.where(IncomingMessage.sender_phone == digits)
    async with SessionFactory() as session:
        rows = (await session.execute(statement)).all()
        return [
            {
                "id": str(message.id),
                "provider": message.provider,
                "instance": message.instance,
                "provider_message_id": message.provider_message_id,
                "sender_phone": message.sender_phone,
                "sender_name": message.sender_name,
                "message_type": message.message_type,
                "text": message.text,
                "received_at": message.received_at.isoformat(),
                "captured_at": message.captured_at.isoformat(),
                "read_at": message.read_at.isoformat() if message.read_at else None,
                "company": company_to_dict(company) if company else None,
                "contact_attempt_id": (
                    str(message.contact_attempt_id) if message.contact_attempt_id else None
                ),
            }
            for message, company in rows
        ]


async def get_incoming_message(message_id: str | uuid.UUID) -> dict[str, Any] | None:
    normalized_id = uuid.UUID(str(message_id))
    statement = (
        select(IncomingMessage, Company)
        .outerjoin(Company, Company.id == IncomingMessage.company_id)
        .where(IncomingMessage.id == normalized_id)
    )
    async with SessionFactory() as session:
        row = (await session.execute(statement)).one_or_none()
        if row is None:
            return None
        message, company = row
        return {
            "id": str(message.id),
            "provider": message.provider,
            "instance": message.instance,
            "provider_message_id": message.provider_message_id,
            "sender_phone": message.sender_phone,
            "sender_name": message.sender_name,
            "message_type": message.message_type,
            "text": message.text,
            "received_at": message.received_at.isoformat(),
            "captured_at": message.captured_at.isoformat(),
            "read_at": message.read_at.isoformat() if message.read_at else None,
            "company": company_to_dict(company) if company else None,
            "contact_attempt_id": (
                str(message.contact_attempt_id) if message.contact_attempt_id else None
            ),
        }


async def _get_or_create_conversation(
    session,
    *,
    provider: str,
    session_name: str,
    remote_jid: str,
    phone: str,
    display_name: str | None = None,
    company_id: uuid.UUID | None = None,
) -> WhatsAppConversation:
    conversation = await session.scalar(
        select(WhatsAppConversation).where(
            WhatsAppConversation.provider == provider,
            WhatsAppConversation.session_name == session_name,
            WhatsAppConversation.remote_jid == remote_jid,
        )
    )
    if conversation is None:
        conversation = WhatsAppConversation(
            provider=provider,
            session_name=session_name,
            remote_jid=remote_jid,
            phone=phone,
            display_name=display_name,
            company_id=company_id,
        )
        session.add(conversation)
        await session.flush()
    else:
        conversation.display_name = display_name or conversation.display_name
        conversation.company_id = company_id or conversation.company_id
        conversation.updated_at = datetime.now(UTC)
    return conversation


async def record_whatsapp_outbound(
    *,
    provider: str,
    session_name: str,
    phone: str,
    text: str,
    provider_message_id: str,
    source: str,
    incoming_message_id: str | None = None,
) -> dict[str, str]:
    remote_jid = f"{phone}@c.us" if provider == "waha" else f"{phone}@s.whatsapp.net"
    async with SessionFactory() as session:
        conversation = await _get_or_create_conversation(
            session,
            provider=provider,
            session_name=session_name,
            remote_jid=remote_jid,
            phone=phone,
        )
        item = WhatsAppConversationMessage(
            conversation_id=conversation.id,
            direction="outgoing",
            source=source,
            status="sent",
            text=text,
            provider_message_id=provider_message_id,
            in_reply_to_message_id=(
                uuid.UUID(incoming_message_id) if incoming_message_id else None
            ),
            sent_at=datetime.now(UTC),
            metadata_json={},
        )
        session.add(item)
        await session.commit()
        return {"conversation_id": str(conversation.id), "conversation_message_id": str(item.id)}


async def list_whatsapp_conversations(limit: int = 50) -> list[dict[str, Any]]:
    statement = (
        select(WhatsAppConversation, Company)
        .outerjoin(Company, Company.id == WhatsAppConversation.company_id)
        .order_by(desc(WhatsAppConversation.updated_at))
        .limit(min(max(limit, 1), 200))
    )
    async with SessionFactory() as session:
        rows = (await session.execute(statement)).all()
        return [
            {
                "id": str(conversation.id),
                "provider": conversation.provider,
                "session": conversation.session_name,
                "phone": conversation.phone,
                "display_name": conversation.display_name,
                "mode": conversation.mode,
                "automatic_message_limit": conversation.automatic_message_limit,
                "automatic_messages_sent": conversation.automatic_messages_sent,
                "status": conversation.status,
                "metadata": dict(conversation.metadata_json or {}),
                "updated_at": conversation.updated_at.isoformat(),
                "company": company_to_dict(company) if company else None,
            }
            for conversation, company in rows
        ]


async def get_whatsapp_conversation(conversation_id: str, limit: int = 100) -> dict[str, Any]:
    normalized_id = uuid.UUID(conversation_id)
    async with SessionFactory() as session:
        conversation = await session.get(WhatsAppConversation, normalized_id)
        if conversation is None:
            raise ValueError("WhatsApp conversation not found")
        messages = (
            await session.scalars(
                select(WhatsAppConversationMessage)
                .where(WhatsAppConversationMessage.conversation_id == normalized_id)
                .order_by(WhatsAppConversationMessage.created_at)
                .limit(min(max(limit, 1), 500))
            )
        ).all()
        return {
            "id": str(conversation.id),
            "provider": conversation.provider,
            "session": conversation.session_name,
            "phone": conversation.phone,
            "display_name": conversation.display_name,
            "mode": conversation.mode,
            "automatic_message_limit": conversation.automatic_message_limit,
            "automatic_messages_sent": conversation.automatic_messages_sent,
            "status": conversation.status,
            "metadata": dict(conversation.metadata_json or {}),
            "messages": [
                {
                    "id": str(message.id),
                    "direction": message.direction,
                    "source": message.source,
                    "status": message.status,
                    "text": message.text,
                    "scheduled_at": message.scheduled_at.isoformat()
                    if message.scheduled_at
                    else None,
                    "sent_at": message.sent_at.isoformat() if message.sent_at else None,
                    "created_at": message.created_at.isoformat(),
                    "error": message.error_message,
                }
                for message in messages
            ],
        }


async def list_pollable_waha_conversations() -> list[dict[str, Any]]:
    statement = select(WhatsAppConversation).where(
        WhatsAppConversation.provider == "waha",
        WhatsAppConversation.status == "active",
        WhatsAppConversation.mode != "paused",
    )
    async with SessionFactory() as session:
        conversations = (await session.scalars(statement)).all()
        return [
            {
                "id": str(conversation.id),
                "session": conversation.session_name,
                "phone": conversation.phone,
                "remote_jid": conversation.remote_jid,
                "created_at": conversation.created_at,
            }
            for conversation in conversations
        ]


async def plan_whatsapp_message(
    *,
    conversation_id: str,
    text: str,
    source: str,
    scheduled_at: datetime | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source not in {"prewritten", "ai_suggested", "human"}:
        raise ValueError("source must be prewritten, ai_suggested or human")
    async with SessionFactory() as session:
        conversation = await session.get(WhatsAppConversation, uuid.UUID(conversation_id))
        if conversation is None:
            raise ValueError("WhatsApp conversation not found")
        status = "scheduled" if scheduled_at else "draft"
        item = WhatsAppConversationMessage(
            conversation_id=conversation.id,
            direction="outgoing",
            source=source,
            status=status,
            text=text,
            scheduled_at=scheduled_at,
            metadata_json=metadata or {},
        )
        session.add(item)
        await session.commit()
        return {
            "message_id": str(item.id),
            "conversation_id": str(conversation.id),
            "status": status,
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        }


async def start_fidelapp_sales_sequence(
    *,
    phone: str,
    company_name: str,
    provider: str,
    session_name: str,
) -> dict[str, Any]:
    remote_suffix = "@c.us" if provider == "waha" else "@s.whatsapp.net"
    first_message = (
        f"Bonjour, suis-je bien chez {company_name.strip()} ?"
        if company_name.strip()
        else "Bonjour, suis-je bien chez vous ?"
    )
    async with SessionFactory() as session:
        conversation = await _get_or_create_conversation(
            session,
            provider=provider,
            session_name=session_name,
            remote_jid=f"{phone}{remote_suffix}",
            phone=phone,
        )
        cancelled = await session.execute(
            update(WhatsAppConversationMessage)
            .where(
                WhatsAppConversationMessage.conversation_id == conversation.id,
                WhatsAppConversationMessage.direction == "outgoing",
                WhatsAppConversationMessage.status.in_({"draft", "scheduled", "sending"}),
            )
            .values(
                status="cancelled",
                scheduled_at=None,
                error_message="Superseded by restarted FidelApp sequence",
            )
        )
        conversation.mode = "automatic"
        conversation.automatic_message_limit = 3
        conversation.automatic_messages_sent = 0
        conversation.status = "active"
        conversation.metadata_json = {
            "sequence": "fidelapp_sales",
            "stage": "opener_queued",
            "company_name": company_name.strip() or None,
        }
        item = WhatsAppConversationMessage(
            conversation_id=conversation.id,
            direction="outgoing",
            source="prewritten",
            status="scheduled",
            text=first_message,
            scheduled_at=datetime.now(UTC),
            metadata_json={"stage_after_send": "awaiting_identity_reply"},
        )
        session.add(item)
        await session.commit()
        return {
            "conversation_id": str(conversation.id),
            "message_id": str(item.id),
            "status": "scheduled",
            "provider": provider,
            "session": session_name,
            "recipient": phone,
            "text": first_message,
            "cancelled_pending_messages": int(cancelled.rowcount or 0),
        }


async def resume_fidelapp_sales_sequence(
    *,
    phone: str,
    company_name: str,
    provider: str,
    session_name: str,
) -> dict[str, Any]:
    remote_suffix = "@c.us" if provider == "waha" else "@s.whatsapp.net"
    async with SessionFactory() as session:
        conversation = await _get_or_create_conversation(
            session,
            provider=provider,
            session_name=session_name,
            remote_jid=f"{phone}{remote_suffix}",
            phone=phone,
        )
        conversation.mode = "ai"
        conversation.status = "active"
        conversation.metadata_json = {
            "sequence": "fidelapp_sales",
            "stage": "ai_active",
            "company_name": company_name.strip() or None,
        }
        await session.commit()
        return {
            "conversation_id": str(conversation.id),
            "status": "resumed",
            "provider": provider,
            "session": session_name,
            "recipient": phone,
            "stage": "ai_active",
        }


async def advance_fidelapp_sales_sequence(conversation_id: str, text: str | None) -> dict[str, Any]:
    async with SessionFactory() as session:
        conversation = await session.get(WhatsAppConversation, uuid.UUID(conversation_id))
        if conversation is None:
            return {"action": "ignored", "reason": "conversation not found"}
        sequence = dict(conversation.metadata_json or {})
        if sequence.get("sequence") != "fidelapp_sales":
            return {"action": "ignored", "reason": "not a FidelApp sales sequence"}
        stage = sequence.get("stage")
        classification = classify_permission_reply(text)
        if classification == "opt_out":
            conversation.mode = "paused"
            conversation.status = "opted_out"
            sequence["stage"] = "opted_out"
            conversation.metadata_json = sequence
            if conversation.company_id:
                company = await session.get(Company, conversation.company_id)
                if company:
                    company.do_not_contact = True
                    company.contact_notes = "WhatsApp opt-out received"
            await session.commit()
            return {"action": "opt_out", "stage": "opted_out"}

        if stage == "awaiting_identity_reply":
            item = WhatsAppConversationMessage(
                conversation_id=conversation.id,
                direction="outgoing",
                source="prewritten",
                status="scheduled",
                text=FIDELAPP_OFFER_MESSAGE,
                scheduled_at=datetime.now(UTC),
                metadata_json={"stage_after_send": "awaiting_offer_reply"},
            )
            session.add(item)
            sequence["stage"] = "offer_queued"
            action = "offer_queued"
        elif stage == "awaiting_offer_reply":
            if classification == "negative":
                conversation.mode = "paused"
                conversation.status = "declined"
                sequence["stage"] = "offer_declined"
                action = "negative"
            elif classification == "positive":
                item = WhatsAppConversationMessage(
                    conversation_id=conversation.id,
                    direction="outgoing",
                    source="prewritten",
                    status="scheduled",
                    text=FIDELAPP_PRESENTATION_CAPTION,
                    scheduled_at=datetime.now(UTC),
                    metadata_json={
                        "stage_after_send": "awaiting_ai_handoff_reply",
                        "attachment_path": str(settings.fidelapp_presentation_path),
                    },
                )
                session.add(item)
                sequence["stage"] = "presentation_queued"
                action = "presentation_queued"
            else:
                action = "awaiting_clear_offer_reply"
        elif stage == "awaiting_ai_handoff_reply":
            conversation.mode = "ai"
            sequence["stage"] = "ai_active"
            action = "ai_handoff"
        else:
            return {"action": "ignored", "stage": stage}
        conversation.metadata_json = sequence
        await session.commit()
        return {"action": action, "stage": sequence.get("stage")}


async def update_planned_whatsapp_message(
    message_id: str, *, action: str, scheduled_at: datetime | None = None
) -> dict[str, Any]:
    if action not in {"approve", "cancel"}:
        raise ValueError("action must be approve or cancel")
    async with SessionFactory() as session:
        item = await session.get(WhatsAppConversationMessage, uuid.UUID(message_id))
        if item is None or item.direction != "outgoing":
            raise ValueError("Planned WhatsApp message not found")
        if item.status not in {"draft", "scheduled"}:
            raise ValueError(f"Message cannot be changed from status {item.status}")
        item.status = "cancelled" if action == "cancel" else "scheduled"
        item.scheduled_at = None if action == "cancel" else (scheduled_at or datetime.now(UTC))
        await session.commit()
        return {"message_id": str(item.id), "status": item.status}


async def set_whatsapp_conversation_mode(
    conversation_id: str, *, mode: str, automatic_message_limit: int | None = None
) -> dict[str, Any]:
    if mode not in {"automatic", "ai", "human", "paused"}:
        raise ValueError("mode must be automatic, ai, human or paused")
    async with SessionFactory() as session:
        conversation = await session.get(WhatsAppConversation, uuid.UUID(conversation_id))
        if conversation is None:
            raise ValueError("WhatsApp conversation not found")
        conversation.mode = mode
        if automatic_message_limit is not None:
            conversation.automatic_message_limit = min(max(automatic_message_limit, 0), 2)
        await session.commit()
        return {
            "conversation_id": str(conversation.id),
            "mode": conversation.mode,
            "automatic_message_limit": conversation.automatic_message_limit,
        }


async def claim_next_planned_whatsapp_message() -> dict[str, Any] | None:
    async with SessionFactory() as session:
        item = await session.scalar(
            select(WhatsAppConversationMessage)
            .where(
                WhatsAppConversationMessage.status == "scheduled",
                WhatsAppConversationMessage.scheduled_at <= datetime.now(UTC),
            )
            .order_by(WhatsAppConversationMessage.scheduled_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        conversation = await session.get(WhatsAppConversation, item.conversation_id)
        if conversation is None or conversation.mode == "paused":
            return None
        if (
            item.source == "prewritten"
            and conversation.mode == "automatic"
            and conversation.automatic_messages_sent >= conversation.automatic_message_limit
        ):
            item.status = "draft"
            conversation.mode = "ai"
            await session.commit()
            return None
        item.status = "sending"
        await session.commit()
        return {
            "id": str(item.id),
            "provider": conversation.provider,
            "session": conversation.session_name,
            "recipient": conversation.phone,
            "body": item.text or "",
            "metadata": dict(item.metadata_json or {}),
        }


async def complete_planned_whatsapp_message(
    message_id: str,
    *,
    success: bool,
    provider_message_id: str | None = None,
    error_message: str | None = None,
) -> None:
    async with SessionFactory() as session:
        item = await session.get(WhatsAppConversationMessage, uuid.UUID(message_id))
        if item is None:
            raise ValueError("Planned WhatsApp message not found")
        item.status = "sent" if success else "failed"
        item.provider_message_id = provider_message_id
        item.error_message = error_message
        item.sent_at = datetime.now(UTC) if success else None
        if success and item.source == "prewritten":
            conversation = await session.get(WhatsAppConversation, item.conversation_id)
            if conversation and conversation.mode == "automatic":
                conversation.automatic_messages_sent = min(
                    conversation.automatic_messages_sent + 1,
                    conversation.automatic_message_limit,
                )
                is_fidelapp_sequence = (conversation.metadata_json or {}).get("sequence") == (
                    "fidelapp_sales"
                )
                if (
                    not is_fidelapp_sequence
                    and conversation.automatic_messages_sent >= conversation.automatic_message_limit
                ):
                    conversation.mode = "ai"
        if success:
            conversation = await session.get(WhatsAppConversation, item.conversation_id)
            next_stage = (item.metadata_json or {}).get("stage_after_send")
            if conversation and next_stage:
                sequence = dict(conversation.metadata_json or {})
                sequence["stage"] = next_stage
                conversation.metadata_json = sequence
        await session.commit()


async def acknowledge_incoming_messages(message_ids: list[str]) -> dict[str, int]:
    normalized_ids = [uuid.UUID(value) for value in message_ids]
    if not normalized_ids:
        raise ValueError("message_ids cannot be empty")
    if len(normalized_ids) > 100:
        raise ValueError("at most 100 messages can be acknowledged at once")
    async with SessionFactory() as session:
        result = await session.execute(
            update(IncomingMessage)
            .where(IncomingMessage.id.in_(normalized_ids), IncomingMessage.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
        )
        await session.commit()
        return {"acknowledged": int(result.rowcount or 0)}


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
