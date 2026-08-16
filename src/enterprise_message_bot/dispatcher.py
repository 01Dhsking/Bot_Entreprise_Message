import asyncio
import logging

from .outreach import _send_evolution_api
from .repository import (
    claim_next_outbound_message,
    complete_outbound_message,
    outbound_dispatch_wait_seconds,
)

log = logging.getLogger(__name__)


async def run_outbound_dispatcher() -> None:
    log.info("Persistent WhatsApp dispatcher started")
    while True:
        try:
            wait_seconds = await outbound_dispatch_wait_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(min(wait_seconds, 2))
                continue
            item = await claim_next_outbound_message()
            if item is None:
                await asyncio.sleep(2)
                continue
            try:
                provider_message_id = await _send_evolution_api(item["recipient"], item["body"])
                await complete_outbound_message(
                    item["id"], success=True, provider_message_id=provider_message_id
                )
            except Exception as exc:
                log.exception("Queued WhatsApp message %s failed", item["id"])
                await complete_outbound_message(
                    item["id"], success=False, error_message=str(exc)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("WhatsApp dispatcher iteration failed")
            await asyncio.sleep(5)
