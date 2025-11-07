# bot/states.py
from aiogram.fsm.state import StatesGroup, State

class Registration(StatesGroup):
    full_name = State()
    university = State()
    secret_code = State()
    privacy_agreement = State()
    time_agreement = State() 
