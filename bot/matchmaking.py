from __future__ import annotations

from app.core.matchmaking.service import calculate_player_text_length


def _get_container():
    from app.container import get_container

    return get_container()


async def process_completed_match(session, slot):
    container = _get_container()
    await container.match_result_service.process_slot(session, slot)


async def send_match_results(bot, slot):  # bot kept for backward compatibility
    container = _get_container()
    await container.match_result_service.send_match_results(slot)


__all__ = ["process_completed_match", "send_match_results", "calculate_player_text_length"]
