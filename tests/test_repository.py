from enterprise_message_bot.repository import (
    company_source_key,
    fidelapp_identity_message,
    natural_company_name,
)
from enterprise_message_bot.schemas import RegistryCompany


def test_natural_company_name_prefers_quoted_trade_name() -> None:
    assert (
        natural_company_name('BAR RESTAURANT LA COUR DES GRANDS "BABY JAY"')
        == "Baby Jay"
    )


def test_fidelapp_identity_message_is_truthful_and_restaurant_specific() -> None:
    message = fidelapp_identity_message("Bar Restaurant La Cour Des Grands")

    assert message.startswith("Bonjour Monsieur/Madame,\n")
    assert "entreprise technologique spécialisée dans les solutions digitales" in message
    assert "récompenser leurs clients après leurs achats" in message
    assert "Google Play et l'App Store" in message
    assert "Seriez-vous disponible pour un court échange à votre convenance ?" in message
    assert "https://fidelapp.net" in message
    assert "Je vous écris de la société FidelApp." not in message


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
