from datetime import date

from enterprise_message_bot.schemas import RegistryCompany, parse_registry_date


def test_registry_company_serialization() -> None:
    company = RegistryCompany(
        legal_name="Example SARL",
        registration_number="RB/COT/26 B 100",
        city="COTONOU",
    )
    assert company.to_dict()["legal_name"] == "Example SARL"
    assert company.to_dict()["city"] == "COTONOU"


def test_parse_registry_date() -> None:
    assert parse_registry_date("19/02/2020") == date(2020, 2, 19)
    assert parse_registry_date("2024-03-01") == date(2024, 3, 1)
    assert parse_registry_date("") is None
    assert parse_registry_date("invalid") is None
