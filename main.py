import asyncio
import logging
import os
import signal
import sys

import uvicorn

def emergency_exit():
    print("\n🛑 Принудительное завершение процессов...")
    os._exit(0)

# Порядок важен: driver_bot должен быть импортирован до manager_bot,
# а web_api — после всех трёх ботов (переиспользует их sync-функции).
import client_bot as client_bot_module
import driver_bot as driver_bot_module
import manager_bot as manager_bot_module
import web_api

from config import client_dp, client_bot, driver_dp, manager_dp
from config import driver_bot as driver_bot_instance
from config import manager_bot as manager_bot_instance

API_PORT = int(os.getenv("API_PORT", "8090"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def start_all():
    bot_count = 2 + (1 if manager_bot_instance else 0)
    print(f"🚀 СЕТЬ ОК! {bot_count} бота успешно запущены и слушают команды.")
    print(f"🌐 WebApp API слушает 0.0.0.0:{API_PORT} (наружу проброшен только 127.0.0.1 хоста, см. docker-compose.yml)")
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, emergency_exit)
    except NotImplementedError:
        pass

    tasks = [
        client_dp.start_polling(client_bot, handle_signals=False, drop_pending_updates=True),
        driver_dp.start_polling(driver_bot_instance, handle_signals=False, drop_pending_updates=True),
        uvicorn.Server(
            uvicorn.Config(web_api.app, host="0.0.0.0", port=API_PORT, log_level="info")
        ).serve(),
    ]
    if manager_bot_instance and manager_dp:
        tasks.append(
            manager_dp.start_polling(manager_bot_instance, handle_signals=False, drop_pending_updates=True)
        )
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(start_all())
    except (KeyboardInterrupt, SystemExit):
        emergency_exit()