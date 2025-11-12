# registration.py
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram import html
from sqlalchemy import select

from bot.states import Registration
from bot.keyboards import yes_no_keyboard
from bot.utils import check_user_exists, hash_password
from db.database import async_session, init_db
from db.models import User, TimePreference
from config import CORRECT_SECRET_CODE

router = Router()
logger = logging.getLogger(__name__)
CONSENT_FILE = Path(__file__).resolve().parent / "soglasie.pdf"

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Инициализируем базу данных при первом запуске
    await init_db()
    
    user = await check_user_exists(message.from_user.id)
    if user:
        await message.answer("Добрый день! Вы уже зарегистрированы.")
        await state.clear()
    else:
        await message.answer(
            "Привет! Для регистрации нам нужны ваши данные. "
            "Пожалуйста, введите ваше полное ФИО:"
        )
        await state.set_state(Registration.full_name)

@router.message(Registration.full_name)
async def process_full_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name.split()) < 2:
        await message.answer("Пожалуйста, введите полное ФИО (минимум 2 слова).")
        return
        
    await state.update_data(full_name=full_name)
    await message.answer("Отлично! Теперь введите вашу организацию (университет/компанию):")
    await state.set_state(Registration.university)

@router.message(Registration.university)
async def process_university(message: Message, state: FSMContext):
    university = message.text.strip()
    if len(university) < 3:
        await message.answer("Пожалуйста, введите полное название организации.")
        return
        
    await state.update_data(university=university)
    await message.answer(
        "Теперь введите секретный код, который вы получили от организаторов:"
    )
    await state.set_state(Registration.secret_code)

@router.message(Registration.secret_code)
async def process_secret_code(message: Message, state: FSMContext):
    secret_code = message.text.strip()
    
    # Проверяем правильность секретного кода
    if secret_code != CORRECT_SECRET_CODE:
        await message.answer("Неверный секретный код. Пожалуйста, введите правильный код.")
        return
    
    # Если код правильный, отправляем документ и запрашиваем согласие
    await state.update_data(secret_code=secret_code)
    
    # Отправляем PDF с документом об обработке персональных данных
    if CONSENT_FILE.exists():
        try:
            document = FSInputFile(str(CONSENT_FILE))
            await message.answer_document(
                document,
                caption="Согласие на обработку персональных данных."
            )
        except Exception as exc:
            logger.exception("Failed to send consent document: %s", exc)
            await message.answer("Не удалось отправить документ. Попробуйте позже.")
    else:
        logger.error("Consent document file not found: %s", CONSENT_FILE)
        await message.answer("Файл согласия не найден в системе. Пожалуйста, свяжитесь с организаторами или повторите попытку позже.")

    
    # Запрашиваем согласие
    await message.answer(
        "Для продолжения регистрации необходимо ваше согласие на обработку персональных данных. "
        "Вы согласны с условиями документа?",
        reply_markup=yes_no_keyboard
    )
    await state.set_state(Registration.privacy_agreement)

@router.message(Registration.privacy_agreement, F.text.in_(["Да", "Нет"]))
async def process_privacy_agreement(message: Message, state: FSMContext):
    user_response = message.text.strip()
    
    if user_response == 'Нет':
        await message.answer(
            "Без вашего согласия на обработку персональных данных мы не можем завершить регистрацию. "
            "Если вы передумаете, введите заново /start.",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
        return
    
    # Если согласие получено, переходим к предупреждению о времени матчей
    await message.answer(
        "⚠️ Обратите внимание! Все матчи проводятся с 18:00 до 22:00.\n\n"
        "Вы согласны участвовать в указанное время?",
        reply_markup=yes_no_keyboard
    )
    await state.set_state(Registration.time_agreement)

# Обработчик для некорректных ответов на согласие
@router.message(Registration.privacy_agreement)
async def process_invalid_privacy_agreement(message: Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, выберите вариант из предложенных кнопок:",
        reply_markup=yes_no_keyboard
    )

@router.message(Registration.time_agreement, F.text.in_(["Да", "Нет"]))
async def process_time_agreement(message: Message, state: FSMContext):
    user_response = message.text.strip()
    
    if user_response == 'Нет':
        await message.answer(
            "К сожалению, без согласия на участие в указанное время мы не можем завершить регистрацию. "
            "Если вы передумаете, введите заново /start.",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
        return
    
    # Если согласие получено, сохраняем пользователя в базу с временем "без разницы"
    data = await state.get_data()
    hashed_code = hash_password(data['secret_code'])
    
    async with async_session() as session:
        # Создаем нового пользователя
        new_user = User(
            tg_id=message.from_user.id,
            full_name=data['full_name'],
            university=data['university'],
            secret_code_hashed=hashed_code,
            registered=True,
            time_preference=TimePreference.ANYTIME  # Автоматически устанавливаем "без разницы"
        )
        session.add(new_user)
        await session.commit()
    
    await message.answer(
        "🎉 Регистрация успешно завершена!\n\n"
        "Ожидайте уведомления о предстоящих матчах!",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

# Обработчик для некорректных ответов на согласие о времени
@router.message(Registration.time_agreement)
async def process_invalid_time_agreement(message: Message, state: FSMContext):
    await message.answer(
        "Пожалуйста, выберите вариант из предложенных кнопок:",
        reply_markup=yes_no_keyboard
    )
