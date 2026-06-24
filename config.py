import os
import sys
import logging
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

CLIENT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
DRIVER_TOKEN  = os.getenv("DRIVER_BOT_TOKEN")
MANAGER_TOKEN = os.getenv("MANAGER_BOT_TOKEN")

if not CLIENT_TOKEN or not DRIVER_TOKEN:
    logging.critical("⚠️ Проверь .env файл! TELEGRAM_BOT_TOKEN или DRIVER_BOT_TOKEN отсутствуют!")
    sys.exit(1)

if not MANAGER_TOKEN:
    logging.warning("⚠️ MANAGER_BOT_TOKEN не задан — менеджерский бот отключён.")

client_bot  = Bot(token=CLIENT_TOKEN)
driver_bot  = Bot(token=DRIVER_TOKEN)
manager_bot = Bot(token=MANAGER_TOKEN) if MANAGER_TOKEN else None

client_dp  = Dispatcher()
driver_dp  = Dispatcher()
manager_dp = Dispatcher() if MANAGER_TOKEN else None

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]
creds_path = os.getenv("GOOGLE_CREDS_PATH", "creds.json")
sheet_name = os.getenv("GOOGLE_SHEET_NAME", "Заявки Mavsimi Rason")

sheet = None
drivers_sheet = None
clients_sheet = None
orders_info_sheet = None
try:
    if os.path.exists(creds_path):
        creds = Credentials.from_service_account_file(creds_path, scopes=scope)
        gc = gspread.authorize(creds)
    else:
        logging.critical(f"❌ Файл авторизации {creds_path} не найден! Запуск невозможен.")
        sys.exit(1)

    spreadsheet = gc.open(sheet_name)
    sheet = spreadsheet.worksheet("Лист1")

    try:
        drivers_sheet = spreadsheet.worksheet("Водители")
    except gspread.WorksheetNotFound:
        drivers_sheet = spreadsheet.add_worksheet(title="Водители", rows=1000, cols=9)
        drivers_sheet.append_row([
            "Статус", "Дата рег.", "ФИО", "Telegram ID",
            "Ставка (TJS)", "Дата оферты", "Topic ID", "Телефон"
        ])
        logging.info("✅ Создан лист 'Водители'")

    try:
        clients_sheet = spreadsheet.worksheet("Клиенты")
    except gspread.WorksheetNotFound:
        clients_sheet = spreadsheet.add_worksheet(title="Клиенты", rows=5000, cols=7)
        clients_sheet.append_row([
            "Статус", "Дата рег.", "ФИО", "Телефон", "Адрес забора", "Chat ID", "Topic ID"
        ])
        logging.info("✅ Создан лист 'Клиенты'")

    try:
        orders_info_sheet = spreadsheet.worksheet("Заказы")
    except gspread.WorksheetNotFound:
        orders_info_sheet = spreadsheet.add_worksheet(title="Заказы", rows=5000, cols=16)
        orders_info_sheet.append_row([
            "ID заказа", "Дата", "Статус", "Цена (TJS)", "Тип доставки", "Вес (кг)", "Габариты",
            "ФИО отправителя", "Тел отправителя", "Город откуда", "Адрес откуда",
            "ФИО получателя", "Тел получателя", "Город куда", "Адрес куда", "Ориентир"
        ])
        logging.info("✅ Создан лист 'Заказы'")

    logging.info("✅ База данных Google Sheets успешно подключена!")
except Exception as e:
    logging.critical(f"❌ Критическая ошибка подключения к Google Таблицам: {e}")
    sys.exit(1)

SUPPORT_CHAT_ID: str = os.getenv("SUPPORT_CHAT_ID", "")

# Общий топик обратной связи — шарится обоими ботами в одном процессе
feedback_topic_id: Optional[int] = None


async def get_or_create_feedback_topic(bot_instance: Bot) -> Optional[int]:
    """Возвращает topic_id топика обратной связи. Создаёт его при первом вызове."""
    global feedback_topic_id
    if feedback_topic_id:
        return feedback_topic_id
    env_id = os.getenv("FEEDBACK_TOPIC_ID", "")
    if env_id:
        feedback_topic_id = int(env_id)
        return feedback_topic_id
    if not SUPPORT_CHAT_ID:
        return None
    try:
        topic = await bot_instance.create_forum_topic(
            chat_id=int(SUPPORT_CHAT_ID),
            name="📋 Обратная связь"
        )
        feedback_topic_id = topic.message_thread_id
        logging.info(
            f"✅ Создан топик обратной связи ID={feedback_topic_id}. "
            f"Добавьте в .env: FEEDBACK_TOPIC_ID={feedback_topic_id}"
        )
        return feedback_topic_id
    except Exception as e:
        logging.error(f"Ошибка создания топика обратной связи: {e}")
        return None