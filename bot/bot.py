from config import BOT_TOKEN
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Bot, Dispatcher

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
