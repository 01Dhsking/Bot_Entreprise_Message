from pydantic import SecretStr

from enterprise_message_bot import outreach
from enterprise_message_bot.outreach import (
    build_evolution_api_request,
    encode_json_ascii,
    extract_primary_email,
    normalize_benin_phone,
    render_template,
)


def test_extract_first_email_from_concatenated_source_value() -> None:
    raw = "first@example.comsecond@example.com"
    assert extract_primary_email(raw) == "first@example.com"


def test_normalize_legacy_benin_phone() -> None:
    assert normalize_benin_phone("96 95 29 07") == "22996952907"
    assert normalize_benin_phone("01 96 95 29 07") == "22996952907"
    assert normalize_benin_phone("+229 01 96 95 29 07") == "22996952907"


def test_render_message_template() -> None:
    company = {
        "legal_name": "CAFE DE ZOE",
        "owner_first_name": "Raymonde",
        "owner_last_name": "Hountondji",
        "city": "COTONOU",
    }
    rendered = render_template("Bonjour {owner_name}, equipe de {name} a {city}.", company)
    assert rendered == "Bonjour Raymonde Hountondji, equipe de CAFE DE ZOE a COTONOU."


def test_build_evolution_api_request(monkeypatch) -> None:
    monkeypatch.setattr(outreach.settings, "whatsapp_provider", "evolution_api")
    monkeypatch.setattr(outreach.settings, "evolution_api_base_url", "https://evo.example.com/")
    monkeypatch.setattr(outreach.settings, "evolution_api_key", SecretStr("secret"))
    monkeypatch.setattr(outreach.settings, "evolution_api_instance", "Solvex solution")

    endpoint, headers, payload = build_evolution_api_request("2290190000000", "Bonjour")

    assert endpoint == "https://evo.example.com/message/sendText/Solvex%20solution"
    assert headers["apikey"] == "secret"
    assert payload == {
        "number": "2290190000000",
        "text": "Bonjour",
        "delay": 123,
        "linkPreview": True,
    }


def test_encode_json_ascii_preserves_accents_as_escapes() -> None:
    encoded = encode_json_ascii(
        {
            "number": "22994482118",
            "text": "Beaucoup d\u2019entreprises \u00e0 Cotonou",
        }
    )

    assert encoded.isascii()
    assert "\\u2019" in encoded
    assert "\\u00e0" in encoded
