import asyncio
import math
import unicodedata
from typing import Any

import httpx

from .browser import validate_source_type
from .config import get_settings
from .schemas import RegistryCompany, RegistryPage

settings = get_settings()


def _normalize_key(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.casefold().split())


def _plain_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("value") or value.get("label") or value.get("key") or ""
    return " ".join(str(value).split())


def parse_api_record(source_type: str, raw: dict[str, Any]) -> RegistryCompany | None:
    values = {_normalize_key(key): _plain_value(value) for key, value in raw.items()}

    def field(*names: str) -> str:
        return next((values[name] for name in names if values.get(name)), "")

    if source_type == "companies":
        legal_name = field("denomination")
        trade_name = field("nom commercial")
        owner_first_name = ""
        owner_last_name = ""
    else:
        trade_name = field("nom commercial")
        owner_first_name = field("prenom")
        owner_last_name = field("nom")
        legal_name = trade_name or " ".join(
            value for value in (owner_first_name, owner_last_name) if value
        )
    if not legal_name:
        return None
    return RegistryCompany(
        source_type=source_type,
        legal_name=legal_name,
        trade_name=trade_name,
        creation_date=field("date creation", "date de creation"),
        registration_number=field("numero rc"),
        activity=field("activite principale", "activite"),
        owner_first_name=owner_first_name,
        owner_last_name=owner_last_name,
        city=field("commune"),
        district=field("quartier"),
        phone=field("telephone"),
        email=field("email"),
    )


async def fetch_registry_page(
    source_type: str,
    *,
    page_number: int = 1,
    page_size: int = 100,
    query: str | None = None,
) -> RegistryPage:
    source_type = validate_source_type(source_type)
    if page_number < 1:
        raise ValueError("page_number must be greater than zero")
    if page_size not in {10, 15, 20, 30, 50, 100, 200, 1000}:
        raise ValueError("Unsupported page_size")
    params: dict[str, str | int] = {"page": page_number, "page_size": page_size}
    if query and query.strip():
        params["search"] = query.strip()
    async with httpx.AsyncClient(timeout=settings.navigation_timeout_seconds) as client:
        response = await client.get(settings.data_api_url_for(source_type), params=params)
        response.raise_for_status()
        payload = response.json()

    total_records = int(payload.get("count") or 0)
    records = [
        record
        for raw in payload.get("results", [])
        if (record := parse_api_record(source_type, raw)) is not None
    ]
    return RegistryPage(
        source_type=source_type,
        url=str(response.url),
        title=(
            "Annuaire des societes" if source_type == "companies" else "Annuaire des etablissements"
        ),
        page_number=int(payload.get("currentPage") or page_number),
        total_pages=math.ceil(total_records / page_size) if total_records else 0,
        total_records=total_records,
        companies=records,
        raw_metadata={
            "page_size": page_size,
            "query": query,
            "transport": "public_api",
            "next": payload.get("next"),
            "previous": payload.get("previous"),
        },
    )


async def fetch_registry_pages(
    source_type: str,
    *,
    start_page: int,
    max_pages: int,
    page_size: int,
):
    for page_number in range(start_page, start_page + max_pages):
        page = await fetch_registry_page(source_type, page_number=page_number, page_size=page_size)
        yield page
        if page.total_pages is not None and page_number >= page.total_pages:
            break
        await asyncio.sleep(settings.action_delay_seconds)
