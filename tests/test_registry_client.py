from enterprise_message_bot.registry_client import parse_api_record


def test_parse_establishment_api_record() -> None:
    record = parse_api_record(
        "establishments",
        {
            "Nom commercial": "CAFE DE ZOE",
            "Date de création": "2020-02-18",
            "Numéro RC": "RB/COT/20 A 55184",
            "Activité principale": "Transfert d'argent",
            "Prénom": "Raymonde",
            "Nom": "Hountondji",
            "Commune": "COTONOU",
            "Quartier": "FIDJROSSE KPOTA",
            "Téléphone": "+22996952907",
            "Email": "owner@example.com",
        },
    )
    assert record is not None
    assert record.source_type == "establishments"
    assert record.activity == "Transfert d'argent"
    assert record.owner_first_name == "Raymonde"


def test_parse_company_nested_city() -> None:
    record = parse_api_record(
        "companies",
        {
            "Dénomination": "EXAMPLE SARL",
            "Commune": {"key": "08.1", "value": "COTONOU"},
        },
    )
    assert record is not None
    assert record.legal_name == "EXAMPLE SARL"
    assert record.city == "COTONOU"
