from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from db.models import TimePreference
from aiogram.utils.keyboard import InlineKeyboardBuilder

class ConfirmStatus:
    CONFIRM = "confirm"
    CANT = "cant"


yes_no_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Да"), KeyboardButton(text="Нет")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)


def time_preference_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TimePreference.MORNING.value)],
            [KeyboardButton(text=TimePreference.AFTERNOON.value)],
            [KeyboardButton(text=TimePreference.EVENING.value)],
            [KeyboardButton(text=TimePreference.ANYTIME.value)]
        ],
        resize_keyboard=True
    )
    return keyboard

def create_confirmation_keyboard(slot_id: int):
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="✅ Приду",
            callback_data=f"confirm:{slot_id}:{ConfirmStatus.CONFIRM}"
        ),
        InlineKeyboardButton(
            text="❌ Не смогу играть",
            callback_data=f"confirm:{slot_id}:{ConfirmStatus.CANT}"
        )
    )

    # по одной кнопке в ряд
    builder.adjust(1)
    return builder.as_markup()
