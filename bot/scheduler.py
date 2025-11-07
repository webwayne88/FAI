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

async def schedule_matches(target_date: datetime, elimination=True):
    async with async_session() as session:
        users = await get_active_users(session, target_date)
        if not users:
            logging.info("Нет активных пользователей для планирования.")
            return {"scheduled_count": 0, "reserve_users": 0}

        room_slots = await get_available_room_slots(session, target_date)
        if not room_slots:
            logging.warning(f"Слоты на {target_date.date()} не найдены. Запускаю автоматическое создание...")
            await create_rooms_and_slots(target_date=target_date, slot_duration_minutes=slot_duration_minutes)
            room_slots = await get_available_room_slots(session, target_date)

        users_with_scheduled_match = set()
        users_by_period = {period: [] for period in PERIODS}
        
        for user in users:
            if user.time_preference == TimePreference.ANYTIME:
                for period in PERIODS:
                    users_by_period[period].append(user)
            else:
                users_by_period[user.time_preference].append(user)
        
        anytime_users = [user for user in users if user.time_preference == TimePreference.ANYTIME]

        scheduled_count = 0
        for period, period_config in PERIODS.items():
            period_start = as_utc_naive(datetime.combine(target_date.date(), period_config['start']))
            period_end = as_utc_naive(datetime.combine(target_date.date(), period_config['end']))

            
            period_slots = [
                slot for slot in room_slots
                if period_start <= slot.start_time < period_end and not slot.is_occupied
            ]
            
            period_slots = sorted(period_slots, key=lambda x: x.start_time)
            
            period_users = sorted([user for user in users_by_period[period] if user.id not in users_with_scheduled_match], key = lambda x: -1*x.matches_played)
            
            for slot in period_slots:
                if len(period_users) >= 2:
                    player1 = period_users.pop()
                    player2 = period_users.pop()
                    
                    # Назначаем матч
                    slot.player1_id = player1.id
                    slot.player2_id = player2.id
                    slot.status = MatchStatus.SCHEDULED
                    slot.is_occupied = True
                    slot.elimination = elimination
                    
                    # Добавляем пользователей в множество тех, кому уже запланирован матч
                    users_with_scheduled_match.add(player1.id)
                    users_with_scheduled_match.add(player2.id)
                    
                    await notify_match_scheduled(player1, player2, slot)
                    scheduled_count += 1
                    logging.info(
                        "Создан матч в периоде %s: %s vs %s в %s (комната %s)",
                        period,
                        player1.full_name,
                        player2.full_name,
                        format_moscow(slot.start_time, "%H:%M"),
                        slot.room_id,
                    )
        # Второй проход: заполняем оставшиеся слоты пользователями с ANYTIME
        # и пользователями из других периодов, у которых еще нет матча
        remaining_slots = [slot for slot in room_slots if not slot.is_occupied]
        remaining_users = [user for user in users if user.id not in users_with_scheduled_match]

        
        # Сортируем оставшиеся слоты по времени
        remaining_slots = sorted(remaining_slots, key=lambda x: x.start_time)
        
        # Заполняем оставшиеся слоты
        for i in range(0, len(remaining_users) - 1, 2):
            if i >= len(remaining_slots):
                break
                
            player1 = remaining_users[i]
            player2 = remaining_users[i + 1]
            slot = remaining_slots[i // 2]  # Берем слот по порядку
            
            # Назначаем матч
            slot.player1_id = player1.id
            slot.player2_id = player2.id
            slot.status = MatchStatus.SCHEDULED
            slot.is_occupied = True
            
            # Добавляем пользователей в множество тех, кому уже запланирован матч
            users_with_scheduled_match.add(player1.id)
            users_with_scheduled_match.add(player2.id)
            
            await notify_match_scheduled(player1, player2, slot)
            scheduled_count += 1
            logging.info(f"Создан матч в свободном слоте: {player1.full_name} vs {player2.full_name} в {slot.start_time.strftime('%H:%M')} (комната {slot.room_id})")
        
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
        .where(User.matches_played_cycle == 0)
        .where(User.eliminated == False)
        .where(~busy_subq)
        .order_by(User.matches_played.asc(), User.declines_count.asc())
    )
    return result.scalars().all()

async def get_available_room_slots(session: AsyncSession, target_date: datetime) -> List[RoomSlot]:
    start_of_day = datetime.combine(target_date.date(), time.min)
    end_of_day = datetime.combine(target_date.date(), time.max)
    
    # Get the current time with the same date as the target date to ensure consistent filtering
    now_utc = ensure_utc(datetime.now(UTC_TZ)) + timedelta(seconds=INVITATION_TIMEOUT)
    if target_date.date() == now_utc.date():
        filter_start_time = as_utc_naive(now_utc)
    else:
        # Otherwise, select all slots from the beginning of the day
        filter_start_time = start_of_day
    
    result = await session.execute(
        select(RoomSlot)
        .where(RoomSlot.start_time >= filter_start_time)  # This is the new condition
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
                    
                logging.info("Успешно создано %s комнат через API.", len(rooms_list))

            except Exception:
                logging.exception("Ошибка при создании комнат через API")
                return

            # --- 3. Сохраняем новые комнаты в БД ---
            for room_data in rooms_list:
                name = room_data.get('name')
                url = room_data.get('url')
                
                if not name or not url:
                    logging.error("Данные комнаты из API не содержат name или url. Пропускаем.")
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
                logging.info(f"Слоты для комнаты {room.id} ({room.room_name}) на {target_date.date()} уже существуют.")
                continue

            # Генерируем новые слоты на основе периодов из конфига
            logging.info(f"Генерируем слоты для комнаты {room.id} ({room.room_name}) на основе периодов из конфига...")
            
            new_slots = []
            
            # Проходим по всем периодам из конфига
            for slot_time in TOURNAMENT_SLOT_STARTS_MSK:
                slot_start_msk = datetime.combine(target_date.date(), slot_time, tzinfo=MOSCOW_TZ)
                slot_start_utc = slot_start_msk.astimezone(UTC_TZ)
                slot_end_utc = slot_start_utc + timedelta(minutes=slot_duration_minutes)

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
    #TO DO оставить только отправку в гигачат на оценку - фоновая задача например в конце каждого дня
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
