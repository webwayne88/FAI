from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

from aiogram import Bot

from common.time_utils import format_moscow
from db.models import RoomSlot, User


@dataclass
class SentMessage:
    chat_id: int
    text: str


class MessageService:
    """Thin wrapper over aiogram Bot with domain-specific helpers."""

    def __init__(self, bot: Bot):
        self._bot = bot

    async def send_case_delivery(self, slot: RoomSlot) -> None:
        """Delivery is deferred until closer to the match via confirm handler."""
        logging.info(
            "Skipping immediate link delivery for slot %s; handled later", slot.id
        )

    async def notify_missing_participants(
        self,
        slot: RoomSlot,
        missing_users: Sequence[User],
    ) -> None:
        """Warn present participants about a no-show situation."""
        if not missing_users:
            return

        message = (
            f"⚠️ Проверка посещаемости матча {self._format_slot_time(slot)} "
            f"зафиксировала отсутствие: "
            + ", ".join(user.full_name for user in missing_users if user)
        )
        present = [
            user
            for user in (slot.player1, slot.player2)
            if user and user not in missing_users
        ]
        await self.send_custom(present, message)

    async def send_match_summary(
        self,
        slot: RoomSlot,
        winner_name: str | None,
        player1_analysis: str | None,
        player2_analysis: str | None,
        elimination: bool,
    ) -> None:
        """Send final result breakdown to players."""
        summary = [
            f"📝 Итоги матча {self._format_slot_time(slot)}",
        ]
        if winner_name:
            summary.append(f"🏆 Победитель: {winner_name}")
        await self._broadcast(slot, "\n".join(summary))

    async def send_custom(self, users: Iterable[User], message: str) -> None:
        """Utility for ad-hoc notifications."""
        for user in users:
            if not user or not user.tg_id:
                continue
            try:
                await self._bot.send_message(chat_id=user.tg_id, text=message)
            except Exception as exc:  # pragma: no cover - logging only
                logging.error("Failed to send custom message: %s", exc)

    async def _broadcast(
        self,
        slot: RoomSlot,
        message: str,
    ) -> None:
        for user in (slot.player1, slot.player2):
            if not user or not user.tg_id:
                continue
            try:
                await self._bot.send_message(chat_id=user.tg_id, text=message)
            except Exception as exc:  # pragma: no cover - logging only
                logging.error(
                    "Failed to deliver message to %s (%s): %s",
                    user.full_name,
                    user.tg_id,
                    exc,
                )

    def _format_slot_time(self, slot: RoomSlot) -> str:
        return format_moscow(slot.start_time, "%d.%m %H:%M")
