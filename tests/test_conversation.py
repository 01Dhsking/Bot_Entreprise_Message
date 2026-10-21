from datetime import UTC, datetime, timedelta

from enterprise_message_bot.conversation import (
    campaign_delay_seconds,
    classify_permission_reply,
    pacing_period,
    permission_opener,
)


def test_campaign_pacing_is_variable_bounded_and_stable() -> None:
    at = datetime(2026, 10, 21, 9, tzinfo=UTC)
    values = [campaign_delay_seconds(index, at=at) for index in range(50)]
    assert all(30 <= value <= 180 for value in values)
    assert len(set(values)) > 20
    assert values == [campaign_delay_seconds(index, at=at) for index in range(50)]
    assert values != [
        campaign_delay_seconds(index, at=at + timedelta(days=3)) for index in range(50)
    ]


def test_pacing_rotates_every_two_then_three_benin_days() -> None:
    start = datetime(2026, 1, 1, 0, tzinfo=UTC)
    assert [pacing_period(start + timedelta(days=d)) for d in range(11)] == [
        0,
        0,
        1,
        1,
        1,
        2,
        2,
        3,
        3,
        3,
        4,
    ]
    # Midnight in Benin is 23:00 UTC; the interval is stable across this boundary.
    assert pacing_period(datetime(2026, 1, 2, 22, 59, tzinfo=UTC)) == 0
    assert pacing_period(datetime(2026, 1, 2, 23, 0, tzinfo=UTC)) == 1


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
