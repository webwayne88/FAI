from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config.settings import get_settings, Settings
from app.core.attendance.guard import AttendanceGuard
from app.core.matchmaking.service import MatchResultService
from app.core.messaging.service import MessageService
from app.core.scheduling.case_dispatcher import CaseDispatchService
from app.core.scheduling.service import MatchScheduler
from bot.handlers.confirm import send_confirmation_request
from db.database import async_session
from salute.jazz import SaluteJazzAPI


@dataclass
class AppContainer:
    settings: Settings
    bot: Bot
    dispatcher: Dispatcher
    message_service: MessageService
    case_dispatcher: CaseDispatchService
    attendance_guard: AttendanceGuard
    match_scheduler: MatchScheduler
    match_result_service: MatchResultService


@lru_cache()
def get_container() -> AppContainer:
    settings = get_settings()
    storage = MemoryStorage()
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=storage)

    message_service = MessageService(bot)

    if not settings.jazz_sdk:
        raise RuntimeError("JAZZ_SDK env variable is required for Salute Jazz client")
    jazz_api = SaluteJazzAPI(settings.jazz_sdk)

    case_dispatcher = CaseDispatchService(
        async_session,
        message_service,
        lead_time_seconds=settings.case_dispatch_lead_seconds,
    )

    attendance_guard = AttendanceGuard(
        async_session,
        jazz_client=jazz_api,
        message_service=message_service,
        poll_interval=settings.attendance_poll_interval,
        grace_period=settings.attendance_grace_period,
    )

    async def confirmation_sender(user, opponent, slot):
        await send_confirmation_request(bot, user, opponent, slot)

    match_scheduler = MatchScheduler(
        async_session,
        jazz_api=jazz_api,
        attendance_guard=attendance_guard,
        case_dispatcher=case_dispatcher,
        confirmation_sender=confirmation_sender,
    )

    match_result_service = MatchResultService(
        async_session,
        message_service=message_service,
        refresh_period=settings.refresh_check_period,
    )

    return AppContainer(
        settings=settings,
        bot=bot,
        dispatcher=dispatcher,
        message_service=message_service,
        case_dispatcher=case_dispatcher,
        attendance_guard=attendance_guard,
        match_scheduler=match_scheduler,
        match_result_service=match_result_service,
    )
