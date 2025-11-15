from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from common.time_utils import as_utc_naive, ensure_utc
from config import REFRESH_CHECK_PERIOD, UTC_TZ
from db.models import MatchStatus, RoomSlot
from salute.giga import analyze_winner


class MatchResultService:
    """Encapsulates winner detection, transcription parsing and result delivery."""

    def __init__(
        self,
        session_factory,
        message_service,
        *,
        analyzer: Callable[[str, str], Awaitable[dict]] = analyze_winner,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
        refresh_period: int = REFRESH_CHECK_PERIOD,
    ) -> None:
        self._session_factory = session_factory
        self._message_service = message_service
        self._analyzer = analyzer
        self._sleep = sleep_func or asyncio.sleep
        self._refresh_period = refresh_period

    async def process_slot(self, session, slot: RoomSlot) -> None:
        if not slot:
            logging.warning("MatchResultService: slot отсутствует, обработка прервана")
            return
        if not slot.transcription:
            logging.warning("MatchResultService: слот %s без транскрипции", slot.id)
            return
        if slot.transcription_processed:
            logging.info("MatchResultService: слот %s уже обработан", slot.id)
            return
        if slot.player1_id is None or slot.player2_id is None:
            logging.warning(
                "MatchResultService: слот %s имеет пустые идентификаторы игроков", slot.id
            )
            return

        try:
            case_context = slot.case.content if slot.case else ""
            winner_analysis = await self._analyzer(slot.transcription, case_context)
            winner_verdict = winner_analysis.get("answer", "")
            lines = winner_verdict.split("\n")

            verdict_line = lines[0] if lines else ""
            if "Игрок 1" in verdict_line or (
                slot.player1 and slot.player1.full_name in verdict_line
            ):
                slot.first_is_winner = True
            elif "Игрок 2" in verdict_line or (
                slot.player2 and slot.player2.full_name in verdict_line
            ):
                slot.first_is_winner = False

            player1_text_length = calculate_player_text_length(
                slot.transcription, slot.player1.full_name if slot.player1 else ""
            )
            player2_text_length = calculate_player_text_length(
                slot.transcription, slot.player2.full_name if slot.player2 else ""
            )

            slot.player1_analysis = "\n".join(lines[1:3]) if len(lines) > 1 else ""
            slot.player2_analysis = slot.player1_analysis

            if slot.player1:
                slot.player1.total_transcription_length += player1_text_length
            if slot.player2:
                slot.player2.total_transcription_length += player2_text_length

            if slot.first_is_winner is None:
                slot.first_is_winner = player1_text_length < player2_text_length

            if slot.first_is_winner is not None:
                if slot.first_is_winner and slot.player1:
                    slot.player1.wins_count += 1
                    if slot.elimination and slot.player2:
                        slot.player2.eliminated = True
                elif not slot.first_is_winner and slot.player2:
                    slot.player2.wins_count += 1
                    if slot.elimination and slot.player1:
                        slot.player1.eliminated = True

            slot.transcription_processed = True
            await session.flush()

        except Exception as exc:  # pragma: no cover - logging path
            logging.error("Ошибка обработки матча %s: %s", slot.id, exc)
            await session.rollback()
            raise

    async def process_pending(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RoomSlot)
                .where(
                    RoomSlot.end_time
                    < as_utc_naive(ensure_utc(datetime.now(UTC_TZ)) - timedelta(hours=2))
                )
                .where(RoomSlot.transcription_processed == False)
                .where(RoomSlot.status == MatchStatus.CONFIRMED)
                .options(
                    selectinload(RoomSlot.player1),
                    selectinload(RoomSlot.player2),
                    selectinload(RoomSlot.room),
                    selectinload(RoomSlot.case),
                )
            )

            pending_slots = result.scalars().all()
            for slot in pending_slots:
                await self.process_slot(session, slot)
                await self._message_service.send_match_summary(
                    slot,
                    self._winner_name(slot),
                    slot.player1_analysis,
                    slot.player2_analysis,
                    slot.elimination,
                )

            await session.commit()

    async def run_pending_loop(self) -> None:
        while True:
            await self._sleep(self._refresh_period)
            try:
                await self.process_pending()
            except Exception as exc:  # pragma: no cover - background log
                logging.error("Ошибка в обработке pending matches: %s", exc)

    async def send_match_results(self, slot: RoomSlot) -> None:
        await self._message_service.send_match_summary(
            slot,
            self._winner_name(slot),
            slot.player1_analysis,
            slot.player2_analysis,
            slot.elimination,
        )

    def _winner_name(self, slot: RoomSlot) -> str | None:
        if slot.first_is_winner is None:
            return None
        return slot.player1.full_name if slot.first_is_winner else slot.player2.full_name


def calculate_player_text_length(transcription: str, player_name: str) -> int:
    try:
        lines = transcription.split("\n")
        player_text: list[str] = []

        for line in lines:
            if line.startswith(f"{player_name}:"):
                text_start = len(player_name) + 1
                if len(line) > text_start:
                    player_text.append(line[text_start:].strip())

        return len(" ".join(player_text).strip())
    except Exception as exc:  # pragma: no cover - defensive
        logging.error("Ошибка при подсчете длины текста: %s", exc)
        return 0
