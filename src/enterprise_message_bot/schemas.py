from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(slots=True)
class RegistryCompany:
    legal_name: str
    source_type: str = "companies"
    trade_name: str = ""
    creation_date: str = ""
    registration_number: str = ""
    activity: str = ""
    owner_first_name: str = ""
    owner_last_name: str = ""
    city: str = ""
    district: str = ""
    phone: str = ""
    email: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class RegistryPage:
    source_type: str
    url: str
    title: str
    page_number: int | None
    total_pages: int | None
    total_records: int | None
    companies: list[RegistryCompany]
    raw_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "url": self.url,
            "title": self.title,
            "page_number": self.page_number,
            "total_pages": self.total_pages,
            "total_records": self.total_records,
            "companies": [company.to_dict() for company in self.companies],
            "raw_metadata": self.raw_metadata,
        }


def parse_registry_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        pass
    parts = value.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(part) for part in parts)
        return date(year, month, day)
    except ValueError:
        return None
