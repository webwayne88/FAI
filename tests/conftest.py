from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.messaging.service import MessageService


@pytest.fixture
def fake_bot():
    class _Bot:
        def __init__(self):
            self.sent_messages = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent_messages.append(
                {"chat_id": chat_id, "text": text, "kwargs": kwargs}
            )

    return _Bot()


@pytest.fixture
def message_service(fake_bot):
    return MessageService(fake_bot)


@pytest.fixture
def sample_users():
    user1 = SimpleNamespace(id=1, tg_id=101, full_name="Игрок 1", declines_count=0)
    user2 = SimpleNamespace(id=2, tg_id=202, full_name="Игрок 2", declines_count=0)
    return user1, user2


@pytest.fixture
def sample_slot(sample_users):
    from datetime import datetime, timedelta

    player1, player2 = sample_users
    return SimpleNamespace(
        id=1,
        start_time=datetime.utcnow() + timedelta(hours=1),
        room=SimpleNamespace(room_url="https://salute/room/123"),
        case=SimpleNamespace(title="Case", content="Content"),
        player1=player1,
        player2=player2,
        elimination=True,
    )


@pytest.fixture
def fast_sleep():
    async def _sleep(seconds: float):
        await asyncio.sleep(0)

    return _sleep
