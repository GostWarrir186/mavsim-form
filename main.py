import asyncio
import logging
import os
import signal
import sys

def emergency_exit():
    print("\n🛑 Принудительное завершение процессов...")
    os._exit(0)

# Импортируем модули для регистрации хэндлеров
import bot as bot_module
import driver_bot as driver_bot_module

# Импортируем объекты с явными алиасами чтобы не конфликтовать с именами модулей
from config import client_dp, client_bot, driver_dp
from config import driver_bot as driver_bot_instance

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def start_all():
    print("🚀 СЕТЬ ОК! Оба бота успешно запущены и слушают команды.")
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, emergency_exit)
    except NotImplementedError:
        pass
    
    await asyncio.gather(
    client_dp.start_polling(client_bot, handle_signals=False, drop_pending_updates=True),
    driver_dp.start_polling(driver_bot_instance, handle_signals=False, drop_pending_updates=True)
)

if __name__ == "__main__":
    try:
        asyncio.run(start_all())
    except (KeyboardInterrupt, SystemExit):
        emergency_exit()