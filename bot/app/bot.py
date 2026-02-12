import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

API = "http://backend:8000/create"

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher(bot)

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    r = requests.post(API, params={
        "telegram_id": str(message.from_user.id),
        "username": message.from_user.username
    })

    data = r.json()

    if "error" in data:
        await message.answer("Already registered.")
    else:
        await message.answer(f"Your VPN:\n{data['vless_link']}")

if __name__ == "__main__":
    executor.start_polling(dp)
