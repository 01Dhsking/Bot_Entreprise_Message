from enterprise_message_bot.conversation import (
    campaign_delay_seconds,
    classify_permission_reply,
    permission_opener,
)


def test_campaign_pacing_uses_auditable_variable_cycle() -> None:
    assert [campaign_delay_seconds(index) for index in range(7)] == [
        30,
        60,
        120,
        30,
        60,
        120,
        30,
    ]


def test_permission_opener_identifies_sender_and_requests_consent() -> None:
    message = permission_opener({"owner_first_name": "Awa", "owner_last_name": "Diop"})
    assert message.startswith("Bonjour Awa Diop")
    assert "Ulrich de SolvexSolution" in message
    assert "Répondez OUI" in message
    assert "STOP" in message


def test_permission_reply_classification() -> None:
    assert classify_permission_reply("Oui, allez-y") == "positive"
    assert classify_permission_reply("Non merci") == "negative"
    assert classify_permission_reply("STOP") == "opt_out"
    assert classify_permission_reply("Bonjour, qui êtes-vous ?") == "ambiguous"
