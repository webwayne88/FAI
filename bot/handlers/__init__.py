from aiogram import Dispatcher
from bot.handlers.registration import router as registration_router
from bot.handlers.confirm import router as confirmation_router

def register_handlers(dp: Dispatcher):
    dp.include_router(registration_router)
    dp.include_router(confirmation_router)
    #dp.include_router(info_router)


