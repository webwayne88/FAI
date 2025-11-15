from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from common.time_utils import ensure_utc
from db.models import RoomSlot


NowCallable = Callable[[], datetime]
SleepCallable = Callable[[float], Awaitable[None]]
SessionFactory = Callable[[], Awaitable]


class CaseDispatchService:
    """Schedules case/link delivery ahead of the actual debate."""

    def __init__(
        self,
        session_factory: Callable[[], Awaitable],
        message_service,
        lead_time_seconds: int,
        *,
        sleep_func: SleepCallable | None = None,
        now_func: NowCallable | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._message_service = message_service
        self._lead_time = lead_time_seconds
        self._sleep = sleep_func or asyncio.sleep
        self._now = now_func or (lambda: ensure_utc(datetime.now(timezone.utc)))
        self._tasks: Dict[int, asyncio.Task] = {}

    async def schedule(self, slot_id: int) -> None:
        """(Re)schedule case delivery for the provided slot."""
        await self.cancel(slot_id)
        task = asyncio.create_task(self._deliver(slot_id))
        self._tasks[slot_id] = task

    async def cancel(self, slot_id: int) -> None:
        task = self._tasks.pop(slot_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:  # pragma: no cover - expected branch
                pass

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.cancel(slot_id) for slot_id in list(self._tasks)))

    async def _deliver(self, slot_id: int) -> None:
        slot = await self._load_slot(slot_id)
        if not slot:
            logging.warning("Case dispatch: slot %s not found", slot_id)
            return

        fire_at = ensure_utc(slot.start_time) - timedelta(seconds=self._lead_time)
        delay = (fire_at - self._now()).total_seconds()
        if delay > 0:
            await self._sleep(delay)

        await self._message_service.send_case_delivery(slot)

    async def _load_slot(self, slot_id: int) -> Optional[RoomSlot]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoomSlot)
                .where(RoomSlot.id == slot_id)
                .options(
                    selectinload(RoomSlot.case),
                    selectinload(RoomSlot.room),
                    selectinload(RoomSlot.player1),
                    selectinload(RoomSlot.player2),
                )
            )
            slot = result.scalar_one_or_none()
            return slot
