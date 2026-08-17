from datetime import UTC, datetime

from enterprise_message_bot.models import OutboundQueueItem


def test_failed_queue_item_fields_can_be_rearmed() -> None:
    item = OutboundQueueItem(
        contact_attempt_id="00000000-0000-0000-0000-000000000001",
        kind="permission_opener",
        recipient="22900000000",
        body="Bonjour",
        status="failed",
        scheduled_at=datetime.now(UTC),
        error_message="provider unavailable",
    )

    item.status = "queued"
    item.error_message = None
    item.provider_message_id = None
    item.sent_at = None

    assert item.status == "queued"
    assert item.error_message is None
