# bot/handlers/confirm.py
from aiogram import Router, types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram import F
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta, timezone
import logging
import asyncio
import json
from random import choice

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func

from db.models import User, Room, RoomSlot, MatchStatus, Case, UserCaseHistory
from db.database import async_session
from bot.keyboards import create_confirmation_keyboard
from bot.matchmaking import process_completed_match, send_match_results
from salute.giga import change_case
from salute.jazz import get_room_transcription, parse_transcriptions, api
from config import INVITATION_TIMEOUT, CASE_READ_TIME, LINK_FOLLOW_TIME, analyze_time
from common.time_utils import ensure_utc, format_moscow, to_moscow

router = Router()

class ConfirmStatus:
    CONFIRM = "confirm"
    CANT = "cant"


async def log_old_room_url(room_id: int, old_room_url: str, new_room_url: str):
    """Записывает старую ссылку на комнату в файл"""
    try:
        log_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'room_id': room_id,
            'old_room_url': old_room_url,
            'new_room_url': new_room_url
        }
        
        with open('old_rooms_log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        
        logging.info(f"Старая ссылка комнаты {room_id} записана в файл")
    except Exception as e:
        logging.error(f"Ошибка при записи старой ссылки в файл: {e}")



async def send_confirmation_request(
    bot: Bot,
    user: User,
    opponent: User,
    slot: RoomSlot
):
    """Отправляет запрос на подтверждение участия в матче"""
    time_str = format_moscow(slot.start_time, "%H:%M")
    date_str = format_moscow(slot.start_time, "%d.%m.%Y")

    message = (
        f"Ваш матч запланирован!\n\n"
        f"📅 Дата: {date_str}\n"
        f"⏰ Время: {time_str}\n"
        f"🧑‍💻 Соперник: {opponent.full_name}\n\n"
        "Пожалуйста, подтвердите ваше участие:\n"
        "✅ Приду - Подтверждаю участие\n"
        "❌ Не смогу - Не смогу играть\n"
    )

    try:
        await bot.send_message(
            chat_id=user.tg_id,
            text=message,
            reply_markup=create_confirmation_keyboard(slot.id)
        )
        logging.info(f"Запрос подтверждения отправлен {user.full_name} (ID: {user.tg_id})")
        asyncio.create_task(
            check_confirmation_response(bot, user, slot, INVITATION_TIMEOUT)
        )
    except Exception as e:
        logging.error(f"Ошибка отправки запроса подтверждения {user.full_name}: {e}")

async def check_confirmation_response(bot: Bot, user: User, slot: RoomSlot, timeout: int):
    """Проверяет ответ пользователя и обрабатывает отсутствие ответа."""
    await asyncio.sleep(timeout)

    async with async_session() as session:
        result = await session.execute(
            select(RoomSlot)
            .where(RoomSlot.id == slot.id)
            .options(selectinload(RoomSlot.player1), selectinload(RoomSlot.player2))
            .with_for_update()
        )
        updated_slot = result.scalar_one_or_none()

        if not updated_slot or updated_slot.status != MatchStatus.SCHEDULED:
            return

        is_player1 = updated_slot.player1_id == user.id
        is_confirmed = (is_player1 and updated_slot.player1_confirmed) or \
                       (not is_player1 and updated_slot.player2_confirmed)

        if not is_confirmed:
            logging.info(f"Пользователь {user.full_name} не подтвердил участие в матче {updated_slot.id} вовремя.")
            await handle_cancellation(bot, updated_slot, user.id, session, "не подтвердил(а) участие вовремя")
            await session.commit()

async def assign_case_to_slot(session: AsyncSession, slot: RoomSlot):
    """Назначает случайный кейс, которого не было у игроков"""
    subquery = select(UserCaseHistory.case_id).where(
        UserCaseHistory.user_id.in_([slot.player1_id, slot.player2_id])
    )

    available_case_query = select(Case).where(
        Case.is_active == True,
        ~Case.id.in_(subquery)
    ).order_by(func.random()).limit(1)

    result = await session.execute(available_case_query)
    selected_case = result.scalar_one_or_none()

    if not selected_case:
        any_active_case_query = select(Case).where(
            Case.is_active == True
        ).order_by(func.random()).limit(1)
        result = await session.execute(any_active_case_query)
        selected_case = result.scalar_one_or_none()

    if selected_case:
        slot.case_id = selected_case.id
        history1 = UserCaseHistory(user_id=slot.player1_id, case_id=selected_case.id, slot_id=slot.id)
        history2 = UserCaseHistory(user_id=slot.player2_id, case_id=selected_case.id, slot_id=slot.id)
        session.add_all([history1, history2])
        await session.flush()
        return selected_case
    return None

async def notify_match_confirmed(
    bot: Bot,
    slot: RoomSlot,
    case: Case = None
):
    """Уведомляет об успешном подтверждении матча"""
    time_str = format_moscow(slot.start_time, "%H:%M")
    date_str = format_moscow(slot.start_time, "%d.%m.%Y")

    message = (
        f"✅ Матч подтвержден!\n\n"
        f"📅 Дата: {date_str}\n"
        f"⏰ Время: {time_str}\n"
        f"Соперники: {slot.player1.full_name} и {slot.player2.full_name}"
    )

    try:
        await bot.send_message(slot.player1.tg_id, message)
        await bot.send_message(slot.player2.tg_id, message)
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления о подтверждении: {e}")

async def notify_opponent(
    bot: Bot,
    user: User,
    slot: RoomSlot,
    reason: str
):
    """Уведомляет соперника об изменениях"""
    try:
        await bot.send_message(
            chat_id=user.tg_id,
            text=f"ℹ️ Информация по вашему матчу в {format_moscow(slot.start_time, '%H:%M')}:\n{reason}"
        )
    except Exception as e:
        logging.error(f"Ошибка уведомления соперника {user.full_name}: {e}")

async def handle_cancellation(bot: Bot, slot: RoomSlot, canceling_user_id: int, session: AsyncSession, reason_for_opponent: str = "отменил(а) игру"):
    """Обрабатывает отмену матча: отменяет слот и исключает отказавшегося игрока."""
    elimination = slot.elimination
    is_player1_canceling = (slot.player1 and slot.player1.id == canceling_user_id)
    canceling_user = slot.player1 if is_player1_canceling else slot.player2
    remaining_user = slot.player2 if is_player1_canceling else slot.player1

    if canceling_user:
        if elimination:
            canceling_user.eliminated = True
            logging.info(f"Игрок {canceling_user.full_name} (ID: {canceling_user.id}) был исключен из-за отмены матча.")
            try:
                message = (
                    f"Ваш матч, запланированный на {format_moscow(slot.start_time, '%d.%m.%Y %H:%M')}, отменен.\n"
                    f"Причина: {canceling_user.full_name} {reason_for_opponent}.\n"
                    f"Этот участник выбывает из игры."
                )
                await bot.send_message(canceling_user.tg_id, message)
            except Exception as e:
                logging.error(f"Не удалось уведомить оставшегося игрока {remaining_user.full_name}: {e}")
    if remaining_user and remaining_user.tg_id:
        try:
            message = (
                f"Ваш матч, запланированный на {format_moscow(slot.start_time, '%d.%m.%Y %H:%M')}, отменен.\n"
                f"Причина: Ваш соперник {canceling_user.full_name} {reason_for_opponent}.\n"
                f"Соперник выбывает из игры."
            )
            await bot.send_message(remaining_user.tg_id, message)
        except Exception as e:
            logging.error(f"Не удалось уведомить оставшегося игрока {remaining_user.full_name}: {e}")

    slot.player1_id = None
    slot.player2_id = None
    slot.player1_confirmed = False
    slot.player2_confirmed = False
    slot.status = MatchStatus.CANCELED
    slot.is_occupied = False
    await session.flush()

@router.callback_query(F.data.startswith("confirm:"))
async def process_confirmation(callback_query: types.CallbackQuery, bot: Bot):
    """Обрабатывает ответы на запрос подтверждения."""
    _, slot_id, status = callback_query.data.split(":")
    slot_id = int(slot_id)
    user_tg_id = callback_query.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(RoomSlot)
            .options(
                selectinload(RoomSlot.player1),
                selectinload(RoomSlot.player2),
                selectinload(RoomSlot.room)
            )
            .where(RoomSlot.id == slot_id)
            .with_for_update()
        )
        slot = result.scalar_one_or_none()

        if not slot:
            await callback_query.answer("Слот не найден!", show_alert=True)
            return

        is_player1 = slot.player1 and slot.player1.tg_id == user_tg_id
        is_player2 = slot.player2 and slot.player2.tg_id == user_tg_id

        if not (is_player1 or is_player2):
            await callback_query.answer("Это не ваш матч.", show_alert=True)
            return

        if slot.status != MatchStatus.SCHEDULED:
            await callback_query.answer("Действие уже неактуально.", show_alert=True)
            return

        current_user = slot.player1 if is_player1 else slot.player2
        opponent = slot.player2 if is_player1 else slot.player1

        # Убираем клавиатуру после ответа
        try:
            await bot.edit_message_reply_markup(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                reply_markup=None
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                logging.error(f"Ошибка при удалении клавиатуры: {e}")

        # === Обработка подтверждения ===
        if status == ConfirmStatus.CONFIRM:
            if is_player1:
                slot.player1_confirmed = True
            else:
                slot.player2_confirmed = True

            await session.flush()  # записываем изменение в транзакции
            await session.refresh(slot, attribute_names=["player1_confirmed", "player2_confirmed"])

            if slot.player1_confirmed and slot.player2_confirmed:
                # Оба подтвердили
                slot.player1.matches_played += 1
                slot.player2.matches_played += 1
                if slot.elimination:
                    slot.player1.matches_played_cycle += 1
                    slot.player2.matches_played_cycle += 1

                assigned_case = await assign_case_to_slot(session, slot)
                slot.status = MatchStatus.CONFIRMED

                await notify_match_confirmed(bot, slot, assigned_case)
                asyncio.create_task(on_match_confirmed(bot, slot, assigned_case))
                await callback_query.answer("Матч подтвержден! Ожидайте кейс за 5 минут до начала.", show_alert=True)
            else:
                # Только один подтвердил
                await callback_query.answer("Вы подтвердили участие. Ждем ответа от соперника.", show_alert=True)
                if opponent:
                    await notify_opponent(bot, opponent, slot, "Ваш соперник подтвердил участие.")

        # === Обработка отказа ===
        elif status == ConfirmStatus.CANT:
            await callback_query.answer("Вы отменили игру и выбываете из турнира.", show_alert=True)
            await handle_cancellation(bot, slot, current_user.id, session)

        await session.commit()

async def on_match_confirmed(bot: Bot, slot: RoomSlot, case: Case):
    """Действия после подтверждения матча"""
    try:
        # Передаем как содержимое кейса, так и роли/интересы
        personalized_case_data = await change_case(
            slot.player1.full_name,
            slot.player2.full_name, 
            case.content,
            case.roles  # Передаем отдельно роли и интересы
        )
        personalized_case = personalized_case_data.get('answer', '')

        # Сохраняем персонализированный кейс в базу данных
        async with async_session() as session:
            result = await session.execute(
                select(RoomSlot)
                .options(selectinload(RoomSlot.player1), selectinload(RoomSlot.player2))
                .where(RoomSlot.id == slot.id)
                .with_for_update()
            )
            updated_slot = result.scalar_one_or_none()
            if updated_slot:
                updated_slot.personalyzed_case = personalized_case
                await session.commit()
                
                # Используем обновленный слот с загруженными связями
                slot_with_relations = updated_slot

        now = datetime.now(timezone.utc)
        start_time_utc = ensure_utc(slot_with_relations.start_time)

        delay_until_case = max(0, (start_time_utc - now).total_seconds() - CASE_READ_TIME)

        if delay_until_case <= 0:
            await send_personalized_case(bot, slot_with_relations.player1, slot_with_relations.player2, personalized_case)
        else:
            asyncio.create_task(send_case_before_match(bot, slot_with_relations.id, delay_until_case))

        delay_until_link = max(0, (start_time_utc - now).total_seconds() - LINK_FOLLOW_TIME)
        
        async def send_links_and_process():
            # Перезагружаем слот в новой сессии
            async with async_session() as session:
                result = await session.execute(
                    select(RoomSlot)
                    .options(
                        selectinload(RoomSlot.player1),
                        selectinload(RoomSlot.player2),
                        selectinload(RoomSlot.room)
                    )
                    .where(RoomSlot.id == slot_with_relations.id)
                )
                current_slot = result.scalar_one_or_none()
                
                if not current_slot or current_slot.status != MatchStatus.CONFIRMED:
                    logging.info(f"Матч {slot_with_relations.id} отменен, ссылки не отправляются")
                    return
                    
                await asyncio.sleep(delay_until_link)
                await send_link(bot, current_slot.player1, current_slot)
                await send_link(bot, current_slot.player2, current_slot)
                await process_match_after_completion(bot, current_slot)

        asyncio.create_task(send_links_and_process())

    except Exception as e:
        logging.error(f"Ошибка в on_match_confirmed: {e}")


async def send_case_before_match(bot: Bot, slot_id: int, delay: float):
    """Отправляет кейс за указанное время до начала матча"""
    try:
        await asyncio.sleep(delay)
        
        # Перезагружаем слот в новой сессии
        async with async_session() as session:
            result = await session.execute(
                select(RoomSlot)
                .options(selectinload(RoomSlot.player1), selectinload(RoomSlot.player2))
                .where(RoomSlot.id == slot_id)
            )
            updated_slot = result.scalar_one_or_none()
            
            if not updated_slot or updated_slot.status != MatchStatus.CONFIRMED:
                logging.info(f"Матч {slot_id} отменен или завершен, кейс не отправляется")
                return
                
            if updated_slot.personalyzed_case:
                await send_personalized_case(bot, updated_slot.player1, updated_slot.player2, updated_slot.personalyzed_case)
                logging.info(f"Кейс отправлен игрокам за 5 минут до матча {slot_id}")
            else:
                logging.error(f"Для матча {slot_id} не найден персонализированный кейс")
    except Exception as e:
        logging.error(f"Ошибка при отправке кейса перед матчем: {e}")

async def process_match_after_completion(bot: Bot, slot: RoomSlot):
    """Обработка матча после его завершения"""
    try:
        # Ждем время окончания матча - 5 минут
        wait_time = (ensure_utc(slot.end_time) - datetime.now(timezone.utc)).total_seconds() - 5 * 60
        if wait_time > 0:
            await asyncio.sleep(wait_time)
            
        async with async_session() as session:
            result = await session.execute(
                select(RoomSlot)
                .options(
                    selectinload(RoomSlot.player1),
                    selectinload(RoomSlot.player2),
                    selectinload(RoomSlot.room),
                    selectinload(RoomSlot.case)
                )
                .where(RoomSlot.id == slot.id)
            )
            updated_slot = result.scalar_one_or_none()
            
            if not updated_slot or updated_slot.status != MatchStatus.CONFIRMED:
                return

            transcription_text = await get_room_transcription(updated_slot.room.room_url)
            await refresh_link(bot, updated_slot)

            parsed_transcription = parse_transcriptions(
                transcription_text,
                [updated_slot.player1.full_name, updated_slot.player2.full_name],
                start_time=to_moscow(updated_slot.start_time),
                end_time=to_moscow(updated_slot.end_time) - timedelta(minutes=analyze_time)
            )
            
            await save_transcription(session, updated_slot.id, parsed_transcription)

            player1_connected = check_player_connection(parsed_transcription, updated_slot.player1.full_name)
            player2_connected = check_player_connection(parsed_transcription, updated_slot.player2.full_name)

            if not player1_connected or not player2_connected:
                updated_slot.status = MatchStatus.CANCELED
                if not player1_connected and not player2_connected:
                    # updated_slot.player1.eliminated = True
                    # updated_slot.player2.eliminated = True
                    updated_slot.player1.matches_played_cycle = 0
                    updated_slot.player2.matches_played_cycle = 0
                    await bot.send_message(updated_slot.player1.tg_id, "Матч отменен, так как не удалось обработать диалог. Возможно один из участников не подключился. Вы остаетесь в игре.")
                    await bot.send_message(updated_slot.player2.tg_id, "Матч отменен, так как не удалось обработать диалог. Возможно один из участников не подключился. Вы остаетесь в игре.")
                elif not player1_connected:
                    if updated_slot.elimination:
                        updated_slot.player1.eliminated = True
                        await bot.send_message(updated_slot.player1.tg_id, "Вы не подключились к матчу и выбываете из игры.")
                    await bot.send_message(updated_slot.player2.tg_id, f"Ваш соперник {updated_slot.player1.full_name} не подключился. Матч отменен.")
                elif not player2_connected:
                    if updated_slot.elimination:
                        updated_slot.player2.eliminated = True
                        await bot.send_message(updated_slot.player2.tg_id, "Вы не подключились к матчу и выбываете из игры.")
                    await bot.send_message(updated_slot.player1.tg_id, f"Ваш соперник {updated_slot.player2.full_name} не подключился. Матч отменен.")
                await session.commit()
            else:
                await process_completed_match(session, updated_slot)
                await send_match_results(bot, updated_slot)
    except Exception as e:
        logging.error(f"Ошибка в process_match_after_completion: {e}")

def check_player_connection(transcription: str, player_name: str) -> bool:
    """Проверяет, подключился ли игрок к матчу по транскрипции"""
    return player_name in transcription

async def save_transcription(session: AsyncSession, slot_id: int, transcription: str):
    """Сохраняет транскрипцию в базе данных"""
    try:
        slot = await session.get(RoomSlot, slot_id)
        if slot:
            slot.transcription = transcription
            slot.transcription_processed = False
            await session.commit()
            logging.info(f"Транскрипция для слота {slot_id} была успешно сохранена.")
        else:
            logging.warning(f"Не удалось найти слот с ID {slot_id} для сохранения транскрипции.")
    except Exception as e:
        logging.error(f"Произошла ошибка при сохранении транскрипции для слота {slot_id}: {e}")
        await session.rollback()

async def send_personalized_case(bot: Bot, player1: User, player2: User, case_text: str):
    """Отправляет персонализированный кейс игрокам с экранированием MarkdownV2 и жирными заголовками."""
    
    def escape_markdown_v2(text: str) -> str:
        """Экранирует специальные символы для MarkdownV2."""
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

    try:
        escaped_text = escape_markdown_v2(case_text)
        escaped_header = escape_markdown_v2("--- Распределение ролей ---")
        bold_header = r" *Распределение ролей* "
        final_text = escaped_text.replace(escaped_header, bold_header)
        message_to_send = f"📋 *Ваш кейс*:\n\n{final_text}"
        
        await bot.send_message(player1.tg_id, message_to_send, parse_mode='MarkdownV2')
        await bot.send_message(player2.tg_id, message_to_send, parse_mode='MarkdownV2')

    except Exception as e:
        logging.error(f"Ошибка отправки кейса: {e}")


async def refresh_link(bot: Bot, slot: RoomSlot):
    """Обновляет ссылку на комнату если количество матчей кратно заданному числу"""
    try:
        async with async_session() as session:
            # Обновляем данные о комнате из базы
            result = await session.execute(
                select(Room)
                .where(Room.id == slot.room_id)
            )
            room = result.scalar_one_or_none()
            
            if not room:
                logging.error(f"Комната {slot.room_id} не найдена")
                return

            # Сохраняем старую ссылку перед обновлением
            old_room_url = room.room_url
            old_room_id = old_room_url.split('/')[-1].split('?')[0]
                
            # Отключаем старую комнату
            await api.disable_room(old_room_id)
            
            # Создаем новую комнату
            new_room_data = await api.create_room(room.room_name)
            new_room_url = new_room_data['roomUrl']
            new_room_id = new_room_data['roomId']
    
            # Обновляем ссылку в базе данных
            room.room_url = new_room_url
            
            await session.commit()
                
            # Записываем старую ссылку в файл
            await log_old_room_url(room.id, old_room_url, new_room_url)
                
            logging.info(f"Обновлена ссылка для комнаты {room.id}: {new_room_url}")
                
    except Exception as e:
        logging.error(f"Ошибка при обновлении ссылки комнаты {room.id}: {e}")
        await session.rollback()

async def send_link(bot: Bot, player: User, slot: RoomSlot):
    """Отправляет кейс и ссылку игроку"""
    try:
        message = (
            f"🔗 Ссылка на комнату: {slot.room.room_url}"
        )
        await bot.send_message(player.tg_id, message)
    except Exception as e:
        logging.error(f"Ошибка отправки ссылки и кейса: {e}")
