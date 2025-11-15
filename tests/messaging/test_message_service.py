import pytest


@pytest.mark.asyncio
async def test_send_case_delivery(message_service, fake_bot, sample_slot):
    await message_service.send_case_delivery(sample_slot)

    assert len(fake_bot.sent_messages) == 0


@pytest.mark.asyncio
async def test_notify_missing_participants(message_service, fake_bot, sample_slot, sample_users):
    await message_service.notify_missing_participants(sample_slot, [sample_users[0]])

    assert len(fake_bot.sent_messages) == 1
    delivered = fake_bot.sent_messages[0]
    assert delivered["chat_id"] == sample_users[1].tg_id
    assert "отсутствие" in delivered["text"]
