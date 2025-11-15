from datetime import datetime, timedelta, timezone

import pytest

from app.core.scheduling.case_dispatcher import CaseDispatchService


@pytest.mark.asyncio
async def test_case_sent_at_expected_time(message_service, fake_bot, sample_slot, fast_sleep):
    now = datetime.now(timezone.utc)
    sample_slot.start_time = now + timedelta(minutes=10)

    delays = []

    async def fake_sleep(seconds: float):
        delays.append(seconds)
        await fast_sleep(0)

    class StubDispatcher(CaseDispatchService):
        async def _load_slot(self, slot_id: int):  # type: ignore[override]
            return sample_slot

    dispatcher = StubDispatcher(
        session_factory=lambda: None,
        message_service=message_service,
        lead_time_seconds=5 * 60,
        sleep_func=fake_sleep,
        now_func=lambda: now,
    )

    await dispatcher.schedule(sample_slot.id)
    await fast_sleep(0)  # drain task queue

    assert delays == [5 * 60]
    assert len(fake_bot.sent_messages) == 2
