# bot/utils.py
import json
import os
import logging
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import ReplyKeyboardRemove
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import bcrypt

from datetime import datetime, timedelta
from db.models import User
from db.database import async_session
from salute.jazz import create_rooms
from config import PERIODS, slot_duration_minutes

JAZZ_ROOMS_FILE = "jazz_rooms.json"

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


async def check_user_exists(user_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.tg_id == user_id)
        )
        return result.scalars().first()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return pwd_context.verify(plain_password, hashed_password)

# def get_password_hash(password: str) -> str:
#     return pwd_context.hash(password)


# async def get_user_by_details(session: AsyncSession, full_name: str, university: str) -> User | None:
#     result = await session.execute(
#         select(User)
#         .filter(func.lower(User.full_name) == func.lower(full_name))
#         .filter(func.lower(User.university) == func.lower(university))
#     )
#     return result.scalar_one_or_none()

# async def get_or_create_rooms(session, max_rooms=2, delay_sec=1):
#     """Получает комнаты из файла или генерирует новые"""
#     rooms = []
    
#     # Пытаемся загрузить из файла
#     if os.path.exists(JAZZ_ROOMS_FILE):
#         try:
#             with open(JAZZ_ROOMS_FILE, 'r') as f:
#                 rooms = json.load(f)
#                 logging.info(f"Loaded {len(rooms)} rooms from file")
#         except Exception as e:
#             logging.error(f"Error loading rooms file: {e}")
    
#     # Если комнат недостаточно - генерируем новые
#     if len(rooms) < max_rooms:
#         new_rooms = await create_rooms(max_rooms - len(rooms))
#         rooms.extend(new_rooms)
        
#         # Сохраняем обновленный список
#         with open(JAZZ_ROOMS_FILE, 'w') as f:
#             json.dump(rooms, f, indent=2)
    
#     return rooms

# async def setup_room_schedule(room_url, days=3, time_between=10):
#     """Создает расписание слотов для комнаты на несколько дней на основе периодов из конфига"""
#     now = datetime.now()
#     slots = []
    
#     for day in range(days):
#         current_date = now + timedelta(days=day)
        
#         # Проходим по всем периодам из конфига
#         for period_name, period_info in PERIODS.items():
#             start_time = period_info["start"]
#             end_time = period_info["end"]
            
#             # Если время задано как datetime.time, комбинируем с текущей датой
#             if isinstance(start_time, datetime.time):
#                 period_start = datetime.combine(current_date, start_time)
#                 period_end = datetime.combine(current_date, end_time)
#             else:
#                 # Если время уже содержит дату (как в тестовом конфиге), используем как есть
#                 period_start = datetime.combine(current_date, start_time.time())
#                 period_start = period_start.replace(
#                     hour=start_time.hour,
#                     minute=start_time.minute,
#                     second=start_time.second
#                 )
#                 period_end = datetime.combine(current_date, end_time.time())
#                 period_end = period_end.replace(
#                     hour=end_time.hour,
#                     minute=end_time.minute,
#                     second=end_time.second
#                 )
            
#             # Генерация слотов для текущего периода
#             current_slot = period_start
#             while current_slot + timedelta(minutes=slot_duration_minutes) <= period_end:
#                 slot_end = current_slot + timedelta(minutes=slot_duration_minutes)
                
#                 slots.append({
#                     "start_time": current_slot,
#                     "end_time": slot_end
#                 })
                
#                 # Переход к следующему слоту
#                 current_slot = slot_end + timedelta(minutes=time_between)
    
#     return slots

