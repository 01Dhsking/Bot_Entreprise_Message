import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal

MIN_SEND_DELAY_SECONDS = 30
MAX_SEND_DELAY_SECONDS = 180
PACING_EPOCH = date(2026, 1, 1)

POSITIVE_REPLY_PATTERN = re.compile(
    r"\b(oui|yes|ok|okay|d[' ]?accord|allez[ -]?y|pourquoi pas|interesse|intéressé|"
    r"je vous ecoute|je vous écoute|volontiers)\b",
    re.IGNORECASE,
)
NEGATIVE_REPLY_PATTERN = re.compile(
    r"\b(non|no|pas interesse|pas intéressé|ne suis pas interesse|ne suis pas intéressé|"
    r"laissez[- ]moi|stop|désabonne|desabonne)\b",
    re.IGNORECASE,
)
OPT_OUT_PATTERN = re.compile(
    r"\b(stop|désabonne|desabonne|ne (?:me )?contactez plus|retirez[- ]moi)\b",
    re.IGNORECASE,
)


def pacing_period(at: datetime) -> int:
    """Stable Benin-local periods alternating two and three days, across restarts."""
    if at.tzinfo is None:
        raise ValueError("pacing timestamp must include a timezone")
    local_day = (at.astimezone(UTC) + timedelta(hours=1)).date()
    days = (local_day - PACING_EPOCH).days
    block, offset = divmod(days, 5)
    return block * 2 + int(offset >= 2)


def campaign_delay_seconds(position: int, *, at: datetime | None = None) -> int:
    # Deterministic per message/period: polling must not draw a new delay each time.
    period = pacing_period(at or datetime.now(UTC))
    digest = hashlib.sha256(f"whatsapp-pacing-v1:{period}:{position}".encode()).digest()
    span = MAX_SEND_DELAY_SECONDS - MIN_SEND_DELAY_SECONDS + 1
    return MIN_SEND_DELAY_SECONDS + int.from_bytes(digest[:8], "big") % span


def permission_opener(company: dict) -> str:
    owner_name = " ".join(
        part for part in (company.get("owner_first_name"), company.get("owner_last_name")) if part
    ).strip()
    greeting = f"Bonjour {owner_name}" if owner_name else "Bonjour"
    return (
        f"{greeting}, j'espère que vous allez bien. Je suis Ulrich de SolvexSolution. "
        "Puis-je vous présenter brièvement notre solution pour les entreprises ? "
        "Répondez OUI pour recevoir la présentation, ou STOP pour ne plus être contacté."
    )


def classify_permission_reply(
    text: str | None,
) -> Literal["positive", "negative", "opt_out", "ambiguous"]:
    normalized = (text or "").strip()
    if not normalized:
        return "ambiguous"
    if OPT_OUT_PATTERN.search(normalized):
        return "opt_out"
    if NEGATIVE_REPLY_PATTERN.search(normalized):
        return "negative"
    if POSITIVE_REPLY_PATTERN.search(normalized):
        return "positive"
    return "ambiguous"
