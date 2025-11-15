from types import SimpleNamespace

import pytest

from app.core.matchmaking.service import MatchResultService


class DummyMessageService:
    def __init__(self):
        self.summaries = []

    async def send_match_summary(self, slot, winner_name, p1_analysis, p2_analysis, elimination):
        self.summaries.append(
            {
                "slot_id": slot.id,
                "winner": winner_name,
                "p1": p1_analysis,
                "p2": p2_analysis,
                "elimination": elimination,
            }
        )


class FakeSession:
    def __init__(self):
        self.flushed = False

    async def flush(self):
        self.flushed = True

    async def rollback(self):  # pragma: no cover - defensive
        pass


@pytest.mark.asyncio
async def test_fallback_winner_by_text_length():
    async def fake_analyzer(transcription, context):
        return {"answer": "Ничья"}

    message_service = DummyMessageService()
    service = MatchResultService(
        session_factory=lambda: None,
        message_service=message_service,
        analyzer=fake_analyzer,
    )

    player1 = SimpleNamespace(
        id=1, full_name="Игрок 1", total_transcription_length=0, wins_count=0, eliminated=False
    )
    player2 = SimpleNamespace(
        id=2, full_name="Игрок 2", total_transcription_length=0, wins_count=0, eliminated=False
    )

    slot = SimpleNamespace(
        id=42,
        transcription="Игрок 1: коротко\nИгрок 2: очень длинный текст текста текста",
        transcription_processed=False,
        player1_id=1,
        player2_id=2,
        player1=player1,
        player2=player2,
        case=SimpleNamespace(content="case"),
        first_is_winner=None,
        player1_analysis="",
        player2_analysis="",
        elimination=True,
    )

    session = FakeSession()
    await service.process_slot(session, slot)

    assert slot.first_is_winner is False
    assert player2.wins_count == 1
    assert session.flushed


@pytest.mark.asyncio
async def test_send_match_results_uses_message_service():
    message_service = DummyMessageService()
    service = MatchResultService(
        session_factory=lambda: None,
        message_service=message_service,
    )

    slot = SimpleNamespace(
        id=50,
        player1=SimpleNamespace(full_name="Игрок 1"),
        player2=SimpleNamespace(full_name="Игрок 2"),
        first_is_winner=True,
        player1_analysis="strong",
        player2_analysis="weak",
        elimination=False,
    )

    await service.send_match_results(slot)

    assert message_service.summaries[0]["winner"] == "Игрок 1"
