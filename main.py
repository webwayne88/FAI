import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import BotCommand, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.container import get_container
from bot.tg_bot import bot, dp
from bot.handlers import register_handlers
from db.database import async_session, init_db
from db.models import User

container = get_container()


async def scheduled_task():
    """Задача для планирования матчей"""
    try:
        target_date = datetime.now(timezone.utc) + timedelta(days=1)
        await container.match_scheduler.schedule_matches(target_date)
    except Exception as e:
        logging.error(f"Ошибка при планировании матчей: {e}")

async def set_commands(bot: Bot):
    """Установка команд меню"""
    commands = [
        BotCommand(command="/start", description="Начать"),
        BotCommand(command="/help", description="Помощь"),
        BotCommand(command="/info", description="Инфо"),
    ]
    await bot.set_my_commands(commands)

async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = (
        "🤖 Помощь по боту:\n\n"
        "• /start - начать работу\n"
        "• /info - ваша информация\n"
        "• /help - это сообщение"
    )
    await message.answer(help_text) 

async def cmd_info(message: Message):
    """Обработчик команды /info - показывает информацию о пользователе"""
    async with async_session() as session:
        try:
            # Ищем пользователя в базе данных
            user = await session.scalar(
                select(User).where(User.tg_id == message.from_user.id)
            )
            
            if user and user.registered:
                # Форматируем информацию о пользователе
                time_pref = user.time_preference.value if user.time_preference else "Не указано"
                
                info_text = (
                    "👤 Ваша информация:\n\n"
                    f"• Имя: {user.full_name or 'Не указано'}\n"
                    f"• Университет: {user.university or 'Не указано'}\n"
                    f"• Контакт: {user.contact or 'Не указано'}\n"
                    f"• Предпочтение по времени: {time_pref}\n"
                )
            else:
                info_text = (
                    "❌ Вы еще не зарегистрированы!\n\n"
                    "Для регистрации используйте команду /start и следуйте инструкциям."
                )
        except Exception as e:
            logging.error(f"Ошибка при получении информации о пользователе: {e}")
            info_text = "❌ Ошибка при получении информации"

    await message.answer(info_text)

async def main():
    logging.basicConfig(level=logging.INFO)

    # Регистрируем обработчики
    register_handlers(dp)
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_info, Command("info"))

    # Установка команд меню
    await set_commands(bot)

    # Инициализация базы данных
    await init_db()

    dp["container"] = container

    # Запуск планировщика
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scheduled_task,
        'cron',
        hour=20,
        minute=0
    )
    scheduler.start()
    
    asyncio.create_task(container.match_result_service.run_pending_loop())

    logging.info("Бот запущен. Планировщик активирован.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
