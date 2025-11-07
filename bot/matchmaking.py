# bot/matchmaking.py
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, func, and_, union_all
import re
import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, Room, RoomSlot, MatchStatus
from salute.giga import analyze_winner
from salute.jazz import get_room_transcription, SaluteJazzAPI
from config import SDK_KEY_ENCODED, significant_difference, MIN_POINTS_TO_WIN



async def calculate_player_text_length(transcription: str, player_name: str) -> int:
    """Вычисляет общую длину текста (в символах) для указанного игрока в транскрипции."""
    try:
        lines = transcription.split('\n')
        player_text = ""

        for line in lines:
            if line.startswith(f"{player_name}:"):
                # Убираем имя игрока и двоеточие, оставляем только текст
                text_start = len(player_name) + 1
                if len(line) > text_start:
                    player_text += line[text_start:].strip() + " "

        return len(player_text.strip())

    except Exception as e:
        logging.error(
            f"Ошибка при подсчете длины текста для игрока {player_name}: {e}")
        return 0


async def process_completed_match(session: AsyncSession, slot: RoomSlot):
    """Обрабатывает завершенный матч: анализирует транскрипцию, сохраняет оценки и обновляет статистику."""
    try:
        if not slot.transcription or slot.transcription_processed or slot.player1_id is None or slot.player2_id is None:
            return

        elimination = slot.elimination
        case_context = slot.case.content if slot.case else ""
        transcription_text = slot.transcription

        # Анализ победителя
        winner_analysis = await analyze_winner(transcription_text, case_context)
        winner_verdict = winner_analysis.get('answer', '')

        # Парсим вердикт для определения победителя
        lines = winner_verdict.split('\n')
        verdict_line = lines[0] if lines else ""
        logging.info(f"Вердикт для матча {slot.id}: {verdict_line}")
        # Первоначальное определение победителя по вердикту
        if "Игрок 1" in verdict_line or slot.player1.full_name in verdict_line:
            slot.first_is_winner = True
        elif "Игрок 2" in verdict_line or slot.player2.full_name in verdict_line:
            slot.first_is_winner = False
        else:
            slot.first_is_winner = None
        # Сохраняем анализ для каждого игрока
        slot.player1_analysis = "Анализ недоступен"
        slot.player2_analysis = "Анализ недоступен"

        # ВЫЧИСЛЕНИЕ ДЛИН ТЕКСТОВ ИГРОКОВ ДЛЯ СУММАРНОЙ СТАТИСТИКИ
        player1_text_length = await calculate_player_text_length(transcription_text, slot.player1.full_name)
        player2_text_length = await calculate_player_text_length(transcription_text, slot.player2.full_name)

        # ОБНОВЛЕНИЕ СУММАРНОЙ ДЛИНЫ ТРАНСКРИПЦИИ ПОЛЬЗОВАТЕЛЕЙ
        slot.player1.total_transcription_length += player1_text_length
        slot.player2.total_transcription_length += player2_text_length\
            # Если победитель не определен по вердикту, определяем по длине текста
        if slot.first_is_winner is None:
            # определяем победителя по длине текста
            if player1_text_length < player2_text_length:
                slot.first_is_winner = True
            else:
                slot.first_is_winner = False

            # Увеличиваем счетчик побед только после окончательного определения победителя
        if slot.first_is_winner is not None:
            if slot.first_is_winner:
                slot.player1.wins_count += 1
                if elimination:
                    slot.player2.eliminated = True
            else:
                slot.player2.wins_count += 1
                if elimination:
                    slot.player1.eliminated = True

        slot.transcription_processed = True
        await session.commit()

        logging.info(
            f"Матч {slot.id} обработан. Победитель: {slot.first_is_winner}")

    except Exception as e:
        logging.error(f"Ошибка обработки матча {slot.id}: {e}")
        await session.rollback()


async def send_match_results(bot, slot: RoomSlot):
    """Отправляет результаты матча игрокам"""
    try:
        if slot.status == MatchStatus.CANCELED:
            return

        elimination = slot.elimination
        message = f"Результаты матча в {slot.start_time.strftime('%H:%M')}:\n"

        if slot.first_is_winner is not None:
            if slot.first_is_winner:
                winner = slot.player1.full_name
            else:
                winner = slot.player2.full_name
            message += f"Победитель: {winner}\n"
            if elimination:
                message += f"Другой участник выбывает.\n"
        else:
            # Случай, когда победитель не определен (оба проиграли)
            message += "Победитель не определен.\n"
            if elimination:
                message += "Оба участника выбывают из турнира.\n"

        # Отправляем каждому игроку его персональный анализ
        await bot.send_message(
            slot.player1.tg_id,
            message +
            f"\nВаши очки: {slot.player1_points}/50\n" +
            f"Анализ вашего выступления:\n{getattr(slot, 'player1_analysis', 'Анализ недоступен')}"
        )

        await bot.send_message(
            slot.player2.tg_id,
            message +
            f"\nВаши очки: {slot.player2_points}/50\n" +
            f"Анализ вашего выступления:\n{getattr(slot, 'player2_analysis', 'Анализ недоступен')}"
        )

    except Exception as e:
        logging.error(f"Ошибка отправки результатов: {e}")


