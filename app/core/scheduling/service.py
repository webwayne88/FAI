from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Sequence, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.orm import selectinload

from common.time_utils import as_utc_naive, ensure_utc
from config import (
    DEFAULT_ROOM_COUNT,
    INVITATION_TIMEOUT,
    MOSCOW_TZ,
    TOURNAMENT_SLOT_STARTS_MSK,
    UTC_TZ,
    slot_duration_minutes,
)
from db.models import MatchStatus, Room, RoomSlot, User


class MatchScheduler:
    """Coordinates slot creation and participant pairing."""

    def __init__(
        self,
        session_factory,
        jazz_api,
        attendance_guard,
        case_dispatcher,
        confirmation_sender,
    ) -> None:
        self._session_factory = session_factory
        self._jazz_api = jazz_api
        self._attendance_guard = attendance_guard
        self._case_dispatcher = case_dispatcher
        self._confirmation_sender = confirmation_sender

    async def schedule_matches(
        self,
        target_date: datetime,
        *,
        elimination: bool = True,
        tournament_mode: bool = True,
    ) -> Dict[str, int]:
        async with self._session_factory() as session:
            users = await self._get_active_users(session, target_date)
            if not users:
                logging.info("Нет активных пользователей для планирования.")
                return {"scheduled_count": 0, "reserve_users": 0}

            slots = await self._get_available_room_slots(session, target_date)
            if not slots:
                logging.warning("Свободные слоты не найдены. Триггерим создание.")
                await self.create_rooms_and_slots(target_date)
                slots = await self._get_available_room_slots(session, target_date)

            if not slots:
                return {"scheduled_count": 0, "reserve_users": len(users)}

            first_slot_time = min(slot.start_time for slot in slots)
            round_slots = [
                slot for slot in slots if slot.start_time == first_slot_time
            ]

            free_users = sorted(
                [
                    u
                    for u in users
                    if not await self._has_scheduled_match_today(
                        session, u.id, target_date.date()
                    )
                ],
                key=lambda u: (u.matches_played, u.declines_count),
            )

            scheduled_count = 0
            scheduled_slots: List[tuple[RoomSlot, User, User]] = []
            for idx in range(0, len(free_users) - 1, 2):
                if idx // 2 >= len(round_slots):
                    break
                p1, p2 = free_users[idx], free_users[idx + 1]
                slot = round_slots[idx // 2]

                slot.player1_id = p1.id
                slot.player2_id = p2.id
                slot.status = MatchStatus.SCHEDULED
                slot.is_occupied = True
                slot.elimination = elimination

                scheduled_slots.append((slot, p1, p2))
                scheduled_count += 1

            await session.commit()
        # post-commit hooks
        for slot, p1, p2 in scheduled_slots:
            slot.player1 = p1
            slot.player2 = p2
            await self._confirm_players(slot, p1, p2)
            await self._case_dispatcher.schedule(slot.id)
            await self._attendance_guard.watch_slot(slot.id)

        return {"scheduled_count": scheduled_count, "reserve_users": 0}

    async def _get_available_room_slots(
        self,
        session,
        target_date: datetime,
    ) -> List[RoomSlot]:
        start_of_day = datetime.combine(target_date.date(), time.min)
        end_of_day = datetime.combine(target_date.date(), time.max)

        now_utc = ensure_utc(datetime.now(UTC_TZ)) + timedelta(seconds=INVITATION_TIMEOUT)
        if target_date.date() == now_utc.date():
            filter_start_time = as_utc_naive(now_utc)
        else:
            filter_start_time = start_of_day

        result = await session.execute(
            select(RoomSlot)
            .where(RoomSlot.start_time >= filter_start_time)
            .where(RoomSlot.start_time <= end_of_day)
            .where(RoomSlot.is_occupied == False)
            .options(selectinload(RoomSlot.room))
        )
        return result.scalars().all()

    async def _get_active_users(
        self,
        session,
        target_date: datetime,
    ) -> Sequence[User]:
        busy_subq = (
            select(RoomSlot.id)
            .where(
                (RoomSlot.player1_id == User.id) | (RoomSlot.player2_id == User.id),
                func.date(RoomSlot.start_time) == target_date.date(),
            )
            .exists()
        )

        result = await session.execute(
            select(User)
            .where(User.registered == True)
            .where(User.tg_id.isnot(None))
            .where(User.eliminated == False)
            .where(~busy_subq)
            .order_by(User.matches_played.asc(), User.declines_count.asc())
        )
        return result.scalars().all()

    async def _has_scheduled_match_today(self, session, user_id: int, date) -> bool:
        start = datetime.combine(date, time.min)
        end = start + timedelta(days=1)
        count = await session.scalar(
            select(func.count(RoomSlot.id))
            .where(RoomSlot.start_time >= start)
            .where(RoomSlot.start_time < end)
            .where((RoomSlot.player1_id == user_id) | (RoomSlot.player2_id == user_id))
        )
        return count > 0

    async def create_rooms_and_slots(
        self,
        target_date: datetime,
        room_count: int = DEFAULT_ROOM_COUNT,
        duration_minutes: int = slot_duration_minutes,
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(Room).where(Room.is_active == True))
            rooms = result.scalars().all()

            rooms_to_create = max(0, room_count - len(rooms))
            if rooms_to_create:
                await self._provision_rooms(session, rooms_to_create, len(rooms))
                result = await session.execute(select(Room).where(Room.is_active == True))
                rooms = result.scalars().all()

            start_of_day = datetime.combine(target_date.date(), time.min)
            end_of_day = start_of_day + timedelta(days=1)

            for room in rooms:
                result = await session.execute(
                    select(RoomSlot)
                    .where(RoomSlot.room_id == room.id)
                    .where(RoomSlot.start_time >= start_of_day)
                    .where(RoomSlot.start_time < end_of_day)
                )
                if result.scalars().first():
                    continue

                new_slots: List[RoomSlot] = []
                for slot_time in TOURNAMENT_SLOT_STARTS_MSK:
                    slot_start_msk = datetime.combine(
                        target_date.date(), slot_time, tzinfo=MOSCOW_TZ
                    )
                    slot_start_utc = slot_start_msk.astimezone(UTC_TZ)
                    slot_end_utc = slot_start_utc + timedelta(minutes=duration_minutes)

                    new_slots.append(
                        RoomSlot(
                            room_id=room.id,
                            start_time=slot_start_utc.replace(tzinfo=None),
                            end_time=slot_end_utc.replace(tzinfo=None),
                            is_occupied=False,
                        )
                    )

                session.add_all(new_slots)
                await session.commit()

    async def _provision_rooms(self, session, amount: int, existing_count: int) -> None:
        rooms_list = []
        for i in range(amount):
            room_number = existing_count + i + 1
            room_title = f"Room #{room_number}"
            room_data = await self._jazz_api.create_room(room_title)
            rooms_list.append(room_data)
            await asyncio.sleep(1)

        for idx, room_data in enumerate(rooms_list):
            url = room_data.get("roomUrl")
            if not url:
                continue
            room = Room(
                room_name=f"Room #{existing_count + idx + 1}",
                room_url=url,
                is_active=True,
            )
            session.add(room)

        await session.commit()

    async def _confirm_players(self, slot: RoomSlot, player1: User, player2: User) -> None:
        await self._confirmation_sender(player1, player2, slot)
        await self._confirmation_sender(player2, player1, slot)
