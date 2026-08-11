from enterprise_message_bot.repository import company_source_key
from enterprise_message_bot.schemas import RegistryCompany


def test_company_source_key_is_stable() -> None:
    first = RegistryCompany(
        legal_name="Example SARL",
        registration_number="RB/COT/26 B 100",
        creation_date="01/01/2026",
        city="COTONOU",
    )
    second = RegistryCompany(
        legal_name=" example sarl ",
        registration_number="rb/cot/26 b 100",
        creation_date="01/01/2026",
        city="cotonou",
    )
    assert company_source_key(first) == company_source_key(second)


def test_source_key_separates_establishments() -> None:
    company = RegistryCompany(
        source_type="companies",
        legal_name="Example",
        registration_number="RB/COT/26 A 1",
    )
    establishment = RegistryCompany(
        source_type="establishments",
        legal_name="Example",
        registration_number="RB/COT/26 A 1",
    )
    assert company_source_key(company) != company_source_key(establishment)
