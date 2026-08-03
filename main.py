import asyncio
import logging
import os
import signal
import sys

def emergency_exit():
    print("\n🛑 Принудительное завершение процессов...")
    os._exit(0)

# Порядок важен: driver_bot должен быть импортирован до manager_bot
import client_bot as client_bot_module
import driver_bot as driver_bot_module
import manager_bot as manager_bot_module

from config import client_dp, client_bot, driver_dp, manager_dp
from config import driver_bot as driver_bot_instance
from config import manager_bot as manager_bot_instance
import db as db_module

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def start_all():
    bot_count = 2 + (1 if manager_bot_instance else 0)
    print(f"🚀 СЕТЬ ОК! {bot_count} бота успешно запущены и слушают команды.")
    loop = asyncio.get_running_loop()
    # SIGTERM обязателен: именно его шлёт `docker stop` / `compose up -d`.
    # Без него контейнер убивали по таймауту, обрывая запись в Таблицу на полуслове.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, emergency_exit)
        except (NotImplementedError, AttributeError):
            pass

    tasks = [
        client_dp.start_polling(client_bot, handle_signals=False, drop_pending_updates=True),
        driver_dp.start_polling(driver_bot_instance, handle_signals=False, drop_pending_updates=True),
        # База-зеркало для статистики: снапшот из Google Таблиц каждые 10 минут.
        # Не пишет обратно в таблицы; падение снапшота не ронять ботов (см. db.run_sync_loop).
        db_module.run_sync_loop(interval_sec=600),
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