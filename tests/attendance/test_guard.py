from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.attendance.guard import AttendanceGuard


class FakeJazzClient:
    def __init__(self, responses):
        self._responses = responses
        self._calls = 0

    async def get_room_participants(self, room_id: str):
        idx = min(self._calls, len(self._responses) - 1)
        self._calls += 1
        return self._responses[idx]


@pytest.mark.asyncio
async def test_guard_marks_presence(message_service, fast_sleep, sample_users):
    now = datetime.now(timezone.utc)
    slot = SimpleNamespace(
        id=10,
        start_time=now,
        room=SimpleNamespace(room_url="https://salute/room/77"),
        player1=sample_users[0],
        player2=sample_users[1],
    )

    current = now

    def now_func():
        return current

    async def sleep(seconds: float):
        nonlocal current
        current += timedelta(seconds=seconds)
        await fast_sleep(0)

    class StubGuard(AttendanceGuard):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.present_marked = False

        async def _load_slot(self, slot_id):
            return slot

        async def _mark_present(self, slot_id):
            self.present_marked = True

    jazz_client = FakeJazzClient(
        responses=[[{"name": "игрок 1"}, {"name": "игрок 2"}]]
    )

    guard = StubGuard(
        session_factory=lambda: None,
        jazz_client=jazz_client,
        message_service=message_service,
        poll_interval=10,
        grace_period=60,
        sleep_func=sleep,
        now_func=now_func,
    )

    await guard.watch_slot(slot.id)
    await guard._tasks[slot.id]

    assert guard.present_marked


@pytest.mark.asyncio
async def test_guard_notifies_on_missing(message_service, fake_bot, fast_sleep, sample_users):
    now = datetime.now(timezone.utc)
    slot = SimpleNamespace(
        id=11,
        start_time=now,
        room=SimpleNamespace(room_url="https://salute/room/99"),
        player1=sample_users[0],
        player2=sample_users[1],
    )

    current = now

    def now_func():
        return current

    async def sleep(seconds: float):
        nonlocal current
        current += timedelta(seconds=seconds)
        await fast_sleep(0)

    class StubGuard(AttendanceGuard):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.missing = None

        async def _load_slot(self, slot_id):
            return slot

        async def _handle_no_show(self, slot_id, missing_users):
            self.missing = missing_users
            await self._message_service.notify_missing_participants(slot, missing_users)

    jazz_client = FakeJazzClient(
        responses=[[{"name": "игрок 1"}]]
    )

    guard = StubGuard(
        session_factory=lambda: None,
        jazz_client=jazz_client,
        message_service=message_service,
        poll_interval=10,
        grace_period=10,
        sleep_func=sleep,
        now_func=now_func,
    )

    await guard.watch_slot(slot.id)
    await guard._tasks[slot.id]

    assert guard.missing and guard.missing[0].id == sample_users[1].id
    assert fake_bot.sent_messages[0]["chat_id"] == sample_users[0].tg_id
