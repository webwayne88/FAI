from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional, List

class RoomSlotResponse(BaseModel):
    id: int
    start_time: datetime
    end_time: datetime
    status: str
    player1: Optional[str] = None
    player2: Optional[str] = None

class ScheduleRequest(BaseModel):
    date: date
