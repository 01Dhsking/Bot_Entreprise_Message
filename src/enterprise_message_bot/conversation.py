import re
from typing import Literal

CAMPAIGN_PACING_SECONDS = (30, 60, 120)

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


def campaign_delay_seconds(position: int) -> int:
    return CAMPAIGN_PACING_SECONDS[position % len(CAMPAIGN_PACING_SECONDS)]


def permission_opener(company: dict) -> str:
    owner_name = " ".join(
        part
        for part in (company.get("owner_first_name"), company.get("owner_last_name"))
        if part
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
