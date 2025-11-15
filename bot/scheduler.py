from __future__ import annotations

from datetime import datetime

from app.container import get_container

_container = get_container()


async def schedule_matches(
    target_date: datetime,
    elimination: bool = True,
    tournament_mode: bool = True,
):
    return await _container.match_scheduler.schedule_matches(
        target_date,
        elimination=elimination,
        tournament_mode=tournament_mode,
    )


async def create_rooms_and_slots(
    target_date: datetime,
    room_count: int | None = None,
    slot_duration_minutes: int | None = None,
):
    await _container.match_scheduler.create_rooms_and_slots(
        target_date,
        room_count=room_count or _container.settings.default_room_count,
        duration_minutes=slot_duration_minutes or _container.settings.slot_duration_minutes,
    )


async def process_pending_matches():
    await _container.match_result_service.process_pending()
