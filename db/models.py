# db/models.py
from sqlalchemy import Column, Integer, String, Boolean, Text, Enum, ForeignKey, DateTime, Date, func, Index, BigInteger
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from enum import Enum as PythonEnum
from sqlalchemy import Enum as SQLAlchemyEnum

from db.database import Base

class TimePreference(PythonEnum):
    MORNING = "Утром (9:00-12:00)"
    AFTERNOON = "Днём (12:00-18:00)"
    EVENING = "Вечером (18:00-23:00)"
    ANYTIME = "Без разницы"
    
class MatchStatus(PythonEnum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELED = "canceled"
    SEARCHING = "searching"

class Room(Base):
    __tablename__ = "rooms"
    
    id = Column(Integer, primary_key=True)
    room_name = Column(Text, nullable=False)
    room_url = Column(Text, nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(), default=func.now())

    slots = relationship("RoomSlot", back_populates="room")

class RoomSlot(Base):
    __tablename__ = "room_slots"
    
    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete='SET NULL'), nullable=True)
    start_time = Column(DateTime(), nullable=False)
    end_time = Column(DateTime(), nullable=False)
    
    player1_id = Column(Integer, ForeignKey("users.id", ondelete='SET NULL'), nullable=True)
    player2_id = Column(Integer, ForeignKey("users.id", ondelete='SET NULL'), nullable=True)
    
    player1_confirmed = Column(Boolean, default=False)
    player2_confirmed = Column(Boolean, default=False)
    
    is_occupied = Column(Boolean, default=False)
    status = Column(SQLAlchemyEnum(MatchStatus), nullable=True)
    elimination = Column(Boolean, default=False)
    transcription_processed = Column(Boolean, default=False)
    # player1_points = Column(Integer, default=0)
    # player2_points = Column(Integer, default=0)
    player1_analysis = Column(Text, nullable=True)
    player2_analysis = Column(Text, nullable=True)

    first_is_winner = Column(Boolean, nullable=True)
    transcription = Column(Text, nullable=True)

    
    case_id = Column(Integer, ForeignKey("cases.id", ondelete='SET NULL'), nullable=True)
    personalyzed_case = Column(Text)
    
    room = relationship("Room", back_populates="slots")
    player1 = relationship("User", foreign_keys=[player1_id])
    player2 = relationship("User", foreign_keys=[player2_id])
    case = relationship("Case")
    
    __table_args__ = (
        Index('idx_room_slots_room_start', 'room_id', 'start_time'),
        Index('idx_room_slots_room_id', 'room_id'),
        Index('idx_room_slots_start_time', 'start_time'),
        Index('idx_room_slots_end_time', 'end_time'),
        Index('idx_room_slots_status', 'status'),
        Index('idx_room_slots_occupied', 'is_occupied'),
        Index('idx_room_slots_player1', 'player1_id'),
        Index('idx_room_slots_player2', 'player2_id'),
    )
    
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, unique=True)
    full_name = Column(Text)
    university = Column(Text)
    contact = Column(Text)
    registered = Column(Boolean, default=False)
    secret_code_hashed = Column(Text)
    time_preference = Column(SQLAlchemyEnum(TimePreference), nullable=True)
    
    matches_played = Column(Integer, default=0)
    matches_played_cycle = Column(Integer, default=0)  
    eliminated = Column(Boolean, default=False)
    declines_count = Column(Integer, default=0)
    wins_count = Column(Integer, default=0)
    sum_points = Column(Integer, default=0)
    total_transcription_length = Column(Integer, default=0)

    case_history = relationship("UserCaseHistory", back_populates="user")
    
    __table_args__ = (
        Index('idx_users_tg_id', 'tg_id'),
        Index('idx_users_registered', 'registered'),
        Index('idx_users_wins_count', 'wins_count'),
    )
    
class Case(Base):
    __tablename__ = "cases"
    
    id = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    roles = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(), default=func.now())
    
    usage_history = relationship("UserCaseHistory", back_populates="case")
    
    __table_args__ = (
        Index('idx_cases_active', 'is_active'),
    )

class UserCaseHistory(Base):
    __tablename__ = "user_case_history"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete='SET NULL'), nullable=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete='SET NULL'), nullable=True)
    slot_id = Column(Integer, ForeignKey("room_slots.id", ondelete='SET NULL'), nullable=True)
    used_at = Column(DateTime(), default=func.now())
    
    user = relationship("User", back_populates="case_history")
    case = relationship("Case", back_populates="usage_history")
    slot = relationship("RoomSlot")
    
    __table_args__ = (
        Index('idx_user_case_history_user_id', 'user_id'),
        Index('idx_user_case_history_case_id', 'case_id'),
        Index('idx_user_case_history_slot_id', 'slot_id'),
    )
