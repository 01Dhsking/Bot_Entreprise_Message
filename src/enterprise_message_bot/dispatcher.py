import asyncio
import logging

from .repository import (
    claim_next_outbound_message,
    claim_next_planned_whatsapp_message,
    complete_outbound_message,
    complete_planned_whatsapp_message,
    outbound_dispatch_wait_seconds,
    planned_whatsapp_message_is_sendable,
)
from .whatsapp import send_whatsapp, send_whatsapp_file

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
                planned = await claim_next_planned_whatsapp_message()
                if planned is None:
                    await asyncio.sleep(2)
                    continue
                try:
                    if not await planned_whatsapp_message_is_sendable(planned["id"]):
                        continue
                    attachment_path = planned["metadata"].get("attachment_path")
                    if attachment_path:
                        if planned["provider"] != "waha":
                            raise RuntimeError("File messages currently require WAHA")
                        provider_message_id = await send_whatsapp_file(
                            planned["recipient"],
                            attachment_path,
                            caption=planned["body"],
                            session=planned["session"],
                        )
                    else:
                        provider_message_id = await send_whatsapp(
                            planned["recipient"],
                            planned["body"],
                            provider=planned["provider"],
                            session=planned["session"],
                        )
                    await complete_planned_whatsapp_message(
                        planned["id"], success=True, provider_message_id=provider_message_id
                    )
                except Exception as exc:
                    log.exception("Planned WhatsApp message %s failed", planned["id"])
                    await complete_planned_whatsapp_message(
                        planned["id"], success=False, error_message=str(exc)
                    )
                continue
            try:
                provider_message_id = await send_whatsapp(
                    item["recipient"],
                    item["body"],
                    provider=item.get("provider"),
                    session=item.get("session"),
                )
                await complete_outbound_message(
                    item["id"], success=True, provider_message_id=provider_message_id
                )
            except Exception as exc:
                log.exception("Queued WhatsApp message %s failed", item["id"])
                await complete_outbound_message(item["id"], success=False, error_message=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("WhatsApp dispatcher iteration failed")
            await asyncio.sleep(5)
