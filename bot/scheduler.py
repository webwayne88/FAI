# bot/scheduler.py
import os
import asyncio
import logging
import json
import re
from aiogram import Bot
from datetime import datetime, timedelta, time
from typing import List, Dict, Tuple, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_, or_, func, delete

from db.models import User, RoomSlot, TimePreference, MatchStatus, Room
from db.database import async_session, init_db
from bot.handlers.confirm import send_confirmation_request
from bot.matchmaking import process_completed_match
from bot.bot import bot
from salute.jazz import SaluteJazzAPI
from salute.giga import analyze_winner
from config import (
    BOT_TOKEN,
    SDK_KEY_ENCODED,
    PERIODS,
    RATING_THRESHOLD,
    REFRESH_CHECK_PERIOD,
    INVITATION_TIMEOUT,
    slot_duration_minutes,
    TOURNAMENT_SLOT_STARTS_MSK,
    DEFAULT_ROOM_COUNT,
    MOSCOW_TZ,
    UTC_TZ,
)
from common.time_utils import as_utc_naive, ensure_utc, format_moscow


async def get_all_room_slots_on_date(session, date: datetime.date) -> List[RoomSlot]:
    start = datetime.combine(date, time.min)
    end = start + timedelta(days=1)
    result = await session.execute(
        select(RoomSlot)
        .where(RoomSlot.start_time >= start)
        .where(RoomSlot.start_time < end)
        .order_by(RoomSlot.start_time)
    )
    return result.scalars().all()


async def has_scheduled_match_today(session, user_id: int, date) -> bool:
    start = datetime.combine(date, time.min)
    end = start + timedelta(days=1)
    count = await session.scalar(
        select(func.count(RoomSlot.id))
        .where(RoomSlot.start_time >= start)
        .where(RoomSlot.start_time < end)
        .where((RoomSlot.player1_id == user_id) | (RoomSlot.player2_id == user_id))
    )
    return count > 0


async def schedule_matches(
    target_date: datetime,
    elimination=True,
    tournament_mode=True  # <-- новый флаг
):
    async with async_session() as session:
        users = await get_active_users(session, target_date)
        if not users:
            logging.info("Нет активных пользователей для планирования.")
            return {"scheduled_count": 0, "reserve_users": 0}

        if tournament_mode:
            # В турнирном режиме: получаем ВСЕ слоты на дату, отсортированные по времени
            # В турнирном режиме игнорируем фильтр по времени и ищем полностью свободные слоты
            available_slots = await get_available_room_slots(
                session,
                target_date,
                ignore_time_filter=True,
            )

            if not available_slots:
                logging.warning(f"Слоты на {target_date.date()} не найдены. Запускаю автоматическое создание...")
                await create_rooms_and_slots(target_date=target_date, slot_duration_minutes=slot_duration_minutes)
                available_slots = await get_available_room_slots(session, target_date)
            # Определяем время первого свободного раунда
            first_slot_time = min(slot.start_time for slot in available_slots)
            round_slots = [
                slot for slot in available_slots if slot.start_time == first_slot_time]

            # Сортируем пользователей
            free_users = sorted(
                [u for u in users if not await has_scheduled_match_today(session, u.id, target_date.date())],
                key=lambda u: u.matches_played
            )

            scheduled_count = 0
            for i in range(0, len(free_users) - 1, 2):
                if i // 2 >= len(round_slots):
                    break
                p1, p2 = free_users[i], free_users[i + 1]
                slot = round_slots[i // 2]

                slot.player1_id = p1.id
                slot.player2_id = p2.id
                slot.status = MatchStatus.SCHEDULED
                slot.is_occupied = True
                slot.elimination = elimination

                await notify_match_scheduled(p1, p2, slot)
                scheduled_count += 1

            await session.commit()
            return {"scheduled_count": scheduled_count, "reserve_users": 0}

        else:
            scheduled_count=0
            await session.commit()
            return {
                "scheduled_count": scheduled_count,
                "reserve_users": 0
            }


async def get_active_users(session: AsyncSession, target_date: datetime) -> List[User]:
    # Использовать EXISTS вместо NOT IN для лучшей производительности
    busy_subq = select(1).where(
        (RoomSlot.player1_id == User.id) | (RoomSlot.player2_id == User.id),
        func.date(RoomSlot.start_time) == target_date.date()
    ).exists()

    result = await session.execute(
        select(User)
        .where(User.registered == True)
        .where(User.tg_id.isnot(None))
        # .where(User.matches_played_cycle == 0)
        .where(User.eliminated == False)
        .where(~busy_subq)
        .order_by(User.matches_played.asc(), User.declines_count.asc())
    )
    users = result.scalars().all()
    logging.info(
        "Найдено %s активных пользователей для планирования на %s.",
        len(users),
        target_date.date(),
    )
    logging.info("Активные пользователи: %s", [user.full_name for user in users])
    return users



async def get_available_room_slots(
    session: AsyncSession,
    target_date: datetime,
    ignore_time_filter: bool = False  # <-- новый параметр
) -> List[RoomSlot]:
    start_of_day = datetime.combine(target_date.date(), time.min)
    end_of_day = datetime.combine(target_date.date(), time.max)

    if ignore_time_filter:
        # В турнирном режиме: возвращаем ВСЕ слоты на дату, даже в прошлом
        filter_start_time = start_of_day
    else:
        # Стандартный режим: не показывать "прошедшие" слоты
        now_utc = ensure_utc(datetime.now(UTC_TZ)) + \
            timedelta(seconds=INVITATION_TIMEOUT)
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


async def notify_match_scheduled(player1: User, player2: User, slot: RoomSlot):
    await send_confirmation_request(bot, player1, player2, slot)
    await send_confirmation_request(bot, player2, player1, slot)


async def create_rooms_and_slots(
    target_date: datetime,
    room_count: int = DEFAULT_ROOM_COUNT,
    slot_duration_minutes: int = slot_duration_minutes
):
    """
    Создает слоты времени для существующих комнат на основе периодов из конфига.

    Args:
        target_date (datetime): Дата, для которой создаются слоты.
        room_count (int): Количество комнат для создания (если комнат нет).
        slot_duration_minutes (int): Длительность одного слота в минутах.
    """

    api = SaluteJazzAPI(SDK_KEY_ENCODED)

    async with async_session() as session:
        # --- 1. Получаем существующие активные комнаты из БД ---
        result = await session.execute(
            select(Room).where(Room.is_active == True)
        )
        existing_rooms = result.scalars().all()

        rooms_to_create = max(0, room_count - len(existing_rooms))

        if rooms_to_create > 0:
            logging.info(
                "Необходимо создать %s дополнительных комнат Salute Jazz.",
                rooms_to_create,
            )
            rooms_list = []
            try:
                for i in range(rooms_to_create):
                    room_number = len(existing_rooms) + i + 1
                    room_title = f"Room #{room_number}"
                    logging.info("Создание комнаты: %s", room_title)

                    room_data = await api.create_room(room_title)

                    room_info = {
                        "name": room_title,
                        "url": room_data['roomUrl'],
                        "api_id": room_data['roomId']
                    }
                    rooms_list.append(room_info)
                    await asyncio.sleep(1)

                logging.info(
                    "Успешно создано %s комнат через API.", len(rooms_list))

            except Exception:
                logging.exception("Ошибка при создании комнат через API")
                return

            # --- 3. Сохраняем новые комнаты в БД ---
            for room_data in rooms_list:
                name = room_data.get('name')
                url = room_data.get('url')

                if not name or not url:
                    logging.error(
                        "Данные комнаты из API не содержат name или url. Пропускаем.")
                    continue

                room = Room(room_name=name, room_url=url, is_active=True)
                session.add(room)

            await session.commit()

            # --- 4. Получаем сохраненные комнаты для создания слотов ---
            result = await session.execute(
                select(Room).where(Room.is_active == True)
            )
            existing_rooms = result.scalars().all()
        else:
            logging.info(
                "Найдено %s активных комнат в БД. Используем их для создания слотов.",
                len(existing_rooms),
            )

        # --- 5. Создаем слоты для каждой существующей комнаты на основе периодов из конфига ---
        start_of_day = datetime.combine(target_date.date(), time.min)
        end_of_day = start_of_day + timedelta(days=1)

        for room in existing_rooms:
            # Проверяем, есть ли уже слоты для этой комнаты на целевую дату
            result = await session.execute(
                select(RoomSlot)
                .where(RoomSlot.room_id == room.id)
                .where(RoomSlot.start_time >= start_of_day)
                .where(RoomSlot.start_time < end_of_day)
            )

            if result.scalars().first():
                logging.info(
                    f"Слоты для комнаты {room.id} ({room.room_name}) на {target_date.date()} уже существуют.")
                continue

            # Генерируем новые слоты на основе периодов из конфига
            logging.info(
                f"Генерируем слоты для комнаты {room.id} ({room.room_name}) на основе периодов из конфига...")

            new_slots = []

            # Проходим по всем периодам из конфига
            for slot_time in TOURNAMENT_SLOT_STARTS_MSK:
                slot_start_msk = datetime.combine(
                    target_date.date(), slot_time, tzinfo=MOSCOW_TZ)
                slot_start_utc = slot_start_msk.astimezone(UTC_TZ)
                slot_end_utc = slot_start_utc + \
                    timedelta(minutes=slot_duration_minutes)

                slot = RoomSlot(
                    room_id=room.id,
                    start_time=slot_start_utc.replace(tzinfo=None),
                    end_time=slot_end_utc.replace(tzinfo=None),
                    is_occupied=False
                )
                new_slots.append(slot)

            # Сохраняем слоты в БД
            session.add_all(new_slots)
            await session.commit()
            logging.info(
                "Добавлено %s новых слотов для комнаты %s (%s).",
                len(new_slots),
                room.id,
                room.room_name,
            )

    logging.info("Процесс создания слотов завершен.")


async def process_pending_matches():
    """Фоновая задача для обработки матчей, которые не были обработаны"""
    # TO DO оставить только отправку в гигачат на оценку - фоновая задача например в конце каждого дня
    while True:
        await asyncio.sleep(REFRESH_CHECK_PERIOD)
        try:
            async with async_session() as session:
                # Находим матчи, которые завершились, но не обработаны
                result = await session.execute(
                    select(RoomSlot)
                    .where(
                        RoomSlot.end_time
                        < as_utc_naive(ensure_utc(datetime.now(UTC_TZ)) - timedelta(hours=2))
                    )
                    .where(RoomSlot.transcription_processed == False)
                    .where(RoomSlot.status == MatchStatus.CONFIRMED)
                    .options(selectinload(RoomSlot.player1), selectinload(RoomSlot.player2), selectinload(RoomSlot.room), selectinload(RoomSlot.case))
                )

                pending_slots = result.scalars().all()

                for slot in pending_slots:
                    await process_completed_match(session, slot)

        except Exception as e:
            logging.error(f"Ошибка в обработке pending matches: {e}")
