# admin/routers/tournament.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete  # Добавляем delete здесь
from datetime import datetime, timedelta, date, time, timezone
from db.models import User, Room, RoomSlot, MatchStatus, Case, TimePreference
from bot.scheduler import schedule_matches
from db.database import get_db
from pydantic import BaseModel
from sqlalchemy.orm import aliased, selectinload
from typing import Optional
from config import BOT_TOKEN
from common.time_utils import to_moscow, as_utc_naive
import asyncio
import logging

from bot.handlers.confirm import send_confirmation_request
import os
from aiogram import Bot
from bot.bot import bot

router = APIRouter()

# Модель запроса для планирования
class ScheduleRequest(BaseModel):
    start_date: date
    end_date: date
    elimination: bool = True  # Добавляем параметр режима планирования
    
# Модель для ответа со слотами
class RoomSlotResponse(BaseModel):
    id: int
    start_time: datetime
    end_time: datetime
    status: str
    player1: Optional[str] = None
    player2: Optional[str] = None
    case_title: Optional[str] = None
    case_content: Optional[str] = None

async def get_stats_from_db(db: AsyncSession):
    """Получаем статистику из базы данных"""
    users_count = await db.scalar(
        select(func.count(User.id))
        .where(User.registered == True)
        .where(User.tg_id.isnot(None))
    )
    
    rooms_count = await db.scalar(
        select(func.count(Room.id))
        .where(Room.is_active == True)
    )
    
    slots_count = await db.scalar(select(func.count(RoomSlot.id)))
    
    # Добавляем подсчет кейсов
    cases_count = await db.scalar(select(func.count(Case.id)))
    
    return {
        "users": users_count or 0,
        "rooms": rooms_count or 0,
        "slots": slots_count or 0,
        "cases": cases_count or 0
    }

async def schedule_delayed_task(delay_seconds, coro_func, *args):
    """Запускает отложенную задачу"""
    await asyncio.sleep(delay_seconds)
    await coro_func(*args)

@router.get("/stats")
async def get_tournament_stats(
    db: AsyncSession = Depends(get_db)
):
    """Получение статистики по турниру"""
    try:
        return await get_stats_from_db(db)
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка получения статистики: {str(e)}"
        )

@router.post("/schedule")
async def run_scheduling(
    request: ScheduleRequest,
    db: AsyncSession = Depends(get_db)
):
    """Запускает процесс планирования матчей для диапазона дат"""
    try:
        total_scheduled = 0
        total_days = 0
        current_date = request.start_date
        
        while current_date <= request.end_date:
            target_date = datetime.combine(current_date, time.min).replace(tzinfo=timezone.utc)

            # Проверяем, есть ли уже матчи на эту дату
            start_dt = as_utc_naive(target_date)
            end_dt = as_utc_naive(target_date + timedelta(days=1))
            
            existing_count = await db.scalar(
                select(func.count(RoomSlot.id))
                .where(RoomSlot.start_time >= start_dt)
                .where(RoomSlot.start_time < end_dt)
                .where(RoomSlot.player1_id.isnot(None))
            )
            
            available_count = await db.scalar(
                select(func.count(RoomSlot.id))
                .where(RoomSlot.start_time >= start_dt)
                .where(RoomSlot.start_time < end_dt)
                .where(RoomSlot.is_occupied == False)
            )

            if existing_count and existing_count > 0 and not available_count:
                # Пропускаем час, если уже всё запланировано
                current_date += timedelta(hours=1)
                continue
            
            # Передаем параметр elimination в schedule_matches
            result = await schedule_matches(target_date, elimination=request.elimination)
            total_scheduled += result['scheduled_count']
            total_days += 1
            current_date += timedelta(hours=1)
        
        stats = await get_stats_from_db(db)
        
        return {
            "message": f"Планирование на период с {request.start_date} по {request.end_date} завершено! Запланировано матчей: {total_scheduled} за {total_days} дней.",
            "scheduled_count": total_scheduled,
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка планирования: {str(e)}"
        )


@router.get("/rooms")
async def get_active_rooms(db: AsyncSession = Depends(get_db)):
    """Получение списка активных комнат"""
    try:
        result = await db.execute(
            select(Room.id, Room.room_name, Room.room_url)  # Добавлено room_url
            .where(Room.is_active == True))
        return [{"id": r[0], "name": r[1], "url": r[2]} for r in result.all()]
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка получения комнат: {str(e)}"
        )

@router.get("/room/{room_id}/schedule")
async def get_room_schedule(
    room_id: int,
    date: date = Query(..., description="Дата в формате YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)):
    """Получение расписания для конкретной комнаты на конкретную дату"""
    try:
        start_dt = datetime.combine(date, time.min)
        end_dt = start_dt + timedelta(days=1)
        
        Player1 = aliased(User)
        Player2 = aliased(User)
        
        result = await db.execute(
            select(
                RoomSlot.id,
                RoomSlot.start_time,
                RoomSlot.end_time,
                RoomSlot.status,
                Player1.full_name.label("player1_name"),
                Player2.full_name.label("player2_name"),
                RoomSlot.player1_confirmed,
                RoomSlot.player2_confirmed,
                RoomSlot.is_occupied,
                Case.title.label("case_title"),
                Case.content.label("case_content"),
                # Добавляем новые поля
                RoomSlot.player1_analysis,
                RoomSlot.player2_analysis,
                RoomSlot.transcription
            )
            .outerjoin(Player1, Player1.id == RoomSlot.player1_id)
            .outerjoin(Player2, Player2.id == RoomSlot.player2_id)
            .outerjoin(Case, Case.id == RoomSlot.case_id)
            .where(RoomSlot.room_id == room_id)
            .where(RoomSlot.start_time >= start_dt)
            .where(RoomSlot.start_time < end_dt)
            .order_by(RoomSlot.start_time)
        )
        
        slots = []
        for row in result.all():
            # Определяем статус слота
            if row.status == MatchStatus.CANCELED:
                actual_status = "CANCELED"
            elif row.is_occupied:
                actual_status = row.status.name if row.status else "OCCUPIED"
            else:
                actual_status = "FREE"
            
            # Форматируем имена игроков с статусом подтверждения
            player1_info = f"{row.player1_name} ✓" if row.player1_confirmed and row.player1_name else row.player1_name
            player2_info = f"{row.player2_name} ✓" if row.player2_confirmed and row.player2_name else row.player2_name
            
            slots.append({
                "id": row.id,
                "start_time": to_moscow(row.start_time).isoformat(),
                "end_time": to_moscow(row.end_time).isoformat(),
                "status": actual_status,
                "player1": player1_info,
                "player2": player2_info,
                "player1_confirmed": row.player1_confirmed,
                "player2_confirmed": row.player2_confirmed,
                "case_title": row.case_title,
                "case_content": row.case_content,
                # Добавляем новые поля в ответ
                "player1_analysis": row.player1_analysis,
                "player2_analysis": row.player2_analysis,
                "transcription": row.transcription
            })
            
        return slots
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка получения расписания: {str(e)}"
        )
'''
@router.delete("/slots/{target_date}")
async def clean_schedule(
    target_date: date,
    db: AsyncSession = Depends(get_db)
):
    """Очищает расписание и резервных пользователей на указанную дату"""
    try:
        start_dt = datetime.combine(target_date, time.min)
        end_dt = start_dt + timedelta(days=1)
        
        # 1. Очищаем слоты комнат
        result = await db.execute(
            select(RoomSlot)
            .where(RoomSlot.start_time >= start_dt)
            .where(RoomSlot.start_time < end_dt)
        )
        slots = result.scalars().all()
        
        for slot in slots:
            slot.player1_id = None
            slot.player2_id = None
            slot.player1_confirmed = False
            slot.player2_confirmed = False
            slot.is_occupied = False
            slot.status = None
            slot.case_id = None
        
        await db.commit()
        
        return {"message": f"Расписание и резервные пользователи на {target_date} очищены. Затронуто слотов: {len(slots)}"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка очистки расписания: {str(e)}"
        )
'''

@router.delete("/slots/{target_date}")
async def clean_schedule(
    target_date: date,
    db: AsyncSession = Depends(get_db)
):
    """Очищает расписание на указанную дату, полностью удаляя слоты"""
    try:
        start_dt = datetime.combine(target_date, time.min)
        end_dt = start_dt + timedelta(days=1)
        
        result = await db.execute(
            delete(RoomSlot)
            .where(RoomSlot.start_time >= start_dt)
            .where(RoomSlot.start_time < end_dt)
        )
        
        deleted_count = result.rowcount
        
        await db.commit()
        
        return {"message": f"Расписание на {target_date} полностью удалено. Удалено слотов: {deleted_count}"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка удаления расписания: {str(e)}"
        )

@router.get("/upcoming-matches")
async def get_upcoming_matches(
    hours: int = Query(24, description="Количество часов для просмотра вперед"),
    db: AsyncSession = Depends(get_db)
):
    """Получает предстоящие матчи в указанном временном диапазоне"""
    try:
        now = datetime.now(timezone.utc)
        end_time = now + timedelta(hours=hours)
        
        Player1 = aliased(User)
        Player2 = aliased(User)
        
        result = await db.execute(
            select(
                RoomSlot.id,
                RoomSlot.start_time,
                RoomSlot.end_time,
                RoomSlot.status,
                Room.room_name,
                Room.room_url,
                Player1.full_name.label("player1_name"),
                Player2.full_name.label("player2_name"),
                Case.title.label("case_title")  # Добавляем заголовок кейса
            )
            .join(Room, Room.id == RoomSlot.room_id)
            .outerjoin(Player1, Player1.id == RoomSlot.player1_id)
            .outerjoin(Player2, Player2.id == RoomSlot.player2_id)
            .outerjoin(Case, Case.id == RoomSlot.case_id)  # Добавляем join с кейсом
            .where(RoomSlot.start_time >= as_utc_naive(now))
            .where(RoomSlot.start_time <= as_utc_naive(end_time))
            .where(RoomSlot.is_occupied == True)
            .order_by(RoomSlot.start_time)
        )
        
        matches = []
        for row in result.all():
            matches.append({
                "id": row.id,
                "start_time": to_moscow(row.start_time).isoformat(),
                "end_time": to_moscow(row.end_time).isoformat(),
                "status": row.status.name if row.status else "SCHEDULED",
                "room_name": row.room_name,
                "room_url": row.room_url,
                "player1": row.player1_name,
                "player2": row.player2_name,
                "case_title": row.case_title  # Добавляем заголовок кейса
            })
            
        return matches
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка получения предстоящих матчей: {str(e)}"
        )
        
@router.post("/reset-cycle")
async def reset_cycle(
    db: AsyncSession = Depends(get_db)
):
    """Сбрасывает счетчик матчей в цикле у всех пользователей"""
    try:
        # Обновляем всех пользователей, устанавливая matches_played_cycle = 0
        from sqlalchemy import update
        stmt = update(User).values(matches_played_cycle=0)
        await db.execute(stmt)
        await db.commit()
        
        return {"message": "Счетчик матчей в цикле сброшен для всех пользователей."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Ошибка сброса цикла: {str(e)}"
        )

