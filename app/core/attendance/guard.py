from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from common.time_utils import ensure_utc
from db.models import MatchStatus, RoomSlot, User


class JazzClientProtocol:
    async def get_room_participants(self, room_id: str) -> Sequence[dict]:  # pragma: no cover - protocol
        raise NotImplementedError


NowCallable = Callable[[], datetime]
SleepCallable = Callable[[float], Awaitable[None]]


@dataclass
class AttendanceSnapshot:
    present_ids: set[int]
    missing_users: List[User]


class AttendanceGuard:
    """Polls Salute Jazz for participant presence and reacts to no-shows."""

    def __init__(
        self,
        session_factory,
        jazz_client: JazzClientProtocol,
        message_service,
        *,
        poll_interval: int,
        grace_period: int,
        sleep_func: SleepCallable | None = None,
        now_func: NowCallable | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._jazz = jazz_client
        self._message_service = message_service
        self._poll_interval = poll_interval
        self._grace_period = grace_period
        self._sleep = sleep_func or asyncio.sleep
        self._now = now_func or (lambda: ensure_utc(datetime.now(timezone.utc)))
        self._tasks: Dict[int, asyncio.Task] = {}
        self._on_no_show: Callable[[RoomSlot, Sequence[User]], Awaitable[None]] | None = None

    def on_no_show(self, callback: Callable[[RoomSlot, Sequence[User]], Awaitable[None]]) -> None:
        self._on_no_show = callback

    async def watch_slot(self, slot_id: int) -> None:
        if slot_id in self._tasks:
            return
        task = asyncio.create_task(self._monitor(slot_id))
        self._tasks[slot_id] = task

    async def cancel(self, slot_id: int) -> None:
        task = self._tasks.pop(slot_id, None)
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - expected
            pass

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.cancel(slot_id) for slot_id in list(self._tasks)))

    async def _monitor(self, slot_id: int) -> None:
        slot = await self._load_slot(slot_id)
        if not slot or not slot.room:
            logging.warning("AttendanceGuard: slot %s is missing room info", slot_id)
            return

        start_at = ensure_utc(slot.start_time)
        while self._now() < start_at:
            await self._sleep(min(self._poll_interval, (start_at - self._now()).total_seconds()))

        deadline = start_at + timedelta(seconds=self._grace_period)
        while self._now() <= deadline:
            snapshot = await self._fetch_snapshot(slot)
            if slot.player1_id in snapshot.present_ids and slot.player2_id in snapshot.present_ids:
                await self._mark_present(slot_id)
                return
            await self._sleep(self._poll_interval)

        snapshot = await self._fetch_snapshot(slot)
        await self._handle_no_show(slot_id, snapshot.missing_users)

    async def _fetch_snapshot(self, slot: RoomSlot) -> AttendanceSnapshot:
        room_id = self._extract_room_id(slot.room.room_url) if slot.room else None
        if not room_id:
            return AttendanceSnapshot(set(), [u for u in (slot.player1, slot.player2) if u])

        participants = await self._jazz.get_room_participants(room_id)
        names = {self._normalise(participant.get("name", "")) for participant in participants}
        present_ids: set[int] = set()
        missing: List[User] = []

        for user in (slot.player1, slot.player2):
            if not user:
                continue
            if self._normalise(user.full_name) in names:
                present_ids.add(user.id)
            else:
                missing.append(user)

        return AttendanceSnapshot(present_ids, missing)

    async def _mark_present(self, slot_id: int) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoomSlot)
                .where(RoomSlot.id == slot_id)
                .options(selectinload(RoomSlot.player1), selectinload(RoomSlot.player2))
            )
            slot = result.scalar_one_or_none()
            if not slot:
                return

            slot.player1_confirmed = True
            slot.player2_confirmed = True
            if slot.status == MatchStatus.SCHEDULED:
                slot.status = MatchStatus.CONFIRMED
            await session.commit()

    async def _handle_no_show(self, slot_id: int, missing_users: Sequence[User]) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoomSlot)
                .where(RoomSlot.id == slot_id)
                .options(
                    selectinload(RoomSlot.player1),
                    selectinload(RoomSlot.player2),
                    selectinload(RoomSlot.room),
                )
            )
            slot = result.scalar_one_or_none()
            if not slot:
                return

            if slot.status not in (MatchStatus.CANCELED, MatchStatus.COMPLETED):
                slot.status = MatchStatus.CANCELED
                slot.is_occupied = False
            for user in missing_users:
                if not user:
                    continue
                user.declines_count = (user.declines_count or 0) + 1

            await session.commit()

        await self._message_service.notify_missing_participants(slot, missing_users)
        if self._on_no_show:
            await self._on_no_show(slot, missing_users)

    async def _load_slot(self, slot_id: int) -> RoomSlot | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoomSlot)
                .where(RoomSlot.id == slot_id)
                .options(
                    selectinload(RoomSlot.player1),
                    selectinload(RoomSlot.player2),
                    selectinload(RoomSlot.room),
                )
            )
            slot = result.scalar_one_or_none()
            return slot

    def _extract_room_id(self, room_url: str | None) -> str | None:
        if not room_url:
            return None
        return room_url.rstrip("/").split("/")[-1].split("?")[0]

    def _normalise(self, value: str) -> str:
        return value.strip().lower()
