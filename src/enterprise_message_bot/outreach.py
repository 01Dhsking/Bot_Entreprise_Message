import asyncio
import json
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from .config import get_settings
from .conversation import permission_opener
from .repository import complete_contact_attempt, queue_outbound_message, reserve_contact_attempt
from .whatsapp import build_evolution_request, send_whatsapp, whatsapp_provider_status

settings = get_settings()

EMAIL_PATTERN = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+?\.(?:com|org|net|bj|fr|africa|io)", re.IGNORECASE
)


class MissingProviderConfiguration(RuntimeError):
    pass


class SafeTemplateValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def extract_primary_email(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    matches = EMAIL_PATTERN.findall(raw_value.replace(" ", ""))
    return matches[0].lower() if matches else None


def normalize_benin_phone(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    digits = "".join(character for character in raw_value if character.isdigit())
    country_code = settings.phone_country_code
    national_prefix = settings.phone_national_prefix
    if len(digits) == 8:
        return f"{country_code}{digits}"
    if len(digits) == 10 and digits.startswith(national_prefix):
        return f"{country_code}{digits[len(national_prefix) :]}"
    if len(digits) == len(country_code) + 8 and digits.startswith(country_code):
        return digits
    if len(digits) == len(country_code) + 10 and digits.startswith(
        f"{country_code}{national_prefix}"
    ):
        return f"{country_code}{digits[len(country_code) + len(national_prefix) :]}"
    if digits.startswith(country_code) and len(digits) >= len(country_code) + 10:
        return digits
    return None


def template_values(company: dict[str, Any]) -> SafeTemplateValues:
    owner_name = " ".join(
        part for part in (company.get("owner_first_name"), company.get("owner_last_name")) if part
    )
    return SafeTemplateValues(
        name=str(company.get("legal_name") or company.get("trade_name") or ""),
        legal_name=str(company.get("legal_name") or ""),
        trade_name=str(company.get("trade_name") or ""),
        owner_name=owner_name,
        city=str(company.get("city") or ""),
        district=str(company.get("district") or ""),
        activity=str(company.get("activity") or ""),
        registration_number=str(company.get("registration_number") or ""),
    )


def render_template(template: str, company: dict[str, Any]) -> str:
    return template.format_map(template_values(company)).strip()


def provider_status() -> dict[str, Any]:
    smtp_password_configured = bool(
        settings.smtp_password and settings.smtp_password.get_secret_value()
    )
    return {
        "email": {
            "configured": bool(settings.smtp_username and smtp_password_configured),
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "from_email": settings.smtp_from_email,
        },
        "whatsapp": whatsapp_provider_status(),
    }


def _send_smtp(recipient: str, subject: str, body: str) -> str:
    if not settings.smtp_password or not settings.smtp_password.get_secret_value():
        raise MissingProviderConfiguration(
            "SMTP_PASSWORD is missing. Use a Google app password for the configured mailbox."
        )
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        server.ehlo()
        if settings.smtp_use_starttls:
            server.starttls()
            server.ehlo()
        server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        refused = server.send_message(message)
        if refused:
            raise RuntimeError(f"SMTP refused recipients: {sorted(refused)}")
    return str(message.get("Message-ID") or "smtp-accepted")


def build_evolution_api_request(recipient: str, body: str) -> tuple[str, dict, dict]:
    if settings.whatsapp_provider != "evolution_api":
        raise MissingProviderConfiguration(
            "WhatsApp is disabled. Configure WHATSAPP_PROVIDER=evolution_api, "
            "EVOLUTION_API_BASE_URL, EVOLUTION_API_KEY and EVOLUTION_API_INSTANCE."
        )
    return build_evolution_request(recipient, body)


def encode_json_ascii(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


async def _send_evolution_api(recipient: str, body: str) -> str:
    return await send_whatsapp(recipient, body, provider="evolution_api")


def preview_message(
    company: dict[str, Any],
    channel: str,
    subject_template: str | None,
    body_template: str,
    *,
    permission_first: bool = False,
) -> dict[str, Any]:
    if channel == "email":
        recipient = extract_primary_email(company.get("email"))
        subject = render_template(subject_template or "", company)
    elif channel == "whatsapp":
        recipient = normalize_benin_phone(company.get("phone"))
        subject = None
    else:
        raise ValueError("channel must be email or whatsapp")
    campaign_body = render_template(body_template, company)
    outbound_body = (
        permission_opener(company) if channel == "whatsapp" and permission_first else campaign_body
    )
    result = {
        "company_id": company["id"],
        "company_name": company.get("legal_name"),
        "channel": channel,
        "recipient": recipient,
        "subject": subject,
        "body": outbound_body,
        "sendable": bool(recipient) and not company.get("do_not_contact", False),
        "already_contacted": channel in company.get("contacted_channels", []),
    }
    if channel == "whatsapp" and permission_first:
        result["follow_up_body"] = campaign_body
        result["conversation_stage"] = "permission_opener"
    return result


async def send_message(
    company: dict[str, Any],
    channel: str,
    subject_template: str | None,
    body_template: str,
    *,
    permission_first: bool = False,
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    preview = preview_message(
        company,
        channel,
        subject_template,
        body_template,
        permission_first=permission_first,
    )
    if not preview["recipient"]:
        raise ValueError(f"No valid {channel} recipient for this company")
    if preview["already_contacted"]:
        raise RuntimeError(f"This company was already contacted by {channel}")
    status = provider_status()[channel]
    if not status["configured"]:
        raise MissingProviderConfiguration(f"The {channel} provider is not configured")

    reservation = await reserve_contact_attempt(
        company_id=company["id"],
        channel=channel,
        recipient=preview["recipient"],
        subject=preview["subject"],
        body=preview["body"],
        metadata={
            "source_type": company.get("source_type"),
            "provider": settings.whatsapp_provider if channel == "whatsapp" else None,
            "session": (
                settings.waha_default_session
                if channel == "whatsapp" and settings.whatsapp_provider == "waha"
                else settings.evolution_api_instance
                if channel == "whatsapp"
                else None
            ),
            "conversation_stage": "opener_queued" if permission_first else None,
            "follow_up_body": preview.get("follow_up_body"),
        },
    )
    attempt_id = reservation["attempt_id"]
    try:
        if channel == "whatsapp" and permission_first:
            if scheduled_at is None:
                raise ValueError("scheduled_at is required for a paced WhatsApp campaign")
            queued = await queue_outbound_message(
                attempt_id=attempt_id,
                kind="permission_opener",
                recipient=preview["recipient"],
                body=preview["body"],
                scheduled_at=scheduled_at,
                metadata={
                    "conversation_stage": "permission_opener",
                    "provider": settings.whatsapp_provider,
                    "session": (
                        settings.waha_default_session
                        if settings.whatsapp_provider == "waha"
                        else settings.evolution_api_instance
                    ),
                },
            )
            return {
                **preview,
                "status": "queued",
                "attempt_id": attempt_id,
                **queued,
            }
        if channel == "email":
            provider_message_id = await asyncio.to_thread(
                _send_smtp,
                preview["recipient"],
                preview["subject"],
                preview["body"],
            )
        else:
            provider_message_id = await send_whatsapp(preview["recipient"], preview["body"])
        await complete_contact_attempt(
            attempt_id, success=True, provider_message_id=provider_message_id
        )
        return {
            **preview,
            "status": "sent",
            "attempt_id": attempt_id,
            "provider_message_id": provider_message_id,
        }
    except Exception as exc:
        await complete_contact_attempt(attempt_id, success=False, error_message=str(exc))
        raise
