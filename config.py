import os
import sys
import logging
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

CLIENT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DRIVER_TOKEN = os.getenv("DRIVER_BOT_TOKEN")

if not CLIENT_TOKEN or not DRIVER_TOKEN:
    logging.critical("⚠️ Проверь .env файл! TELEGRAM_BOT_TOKEN или DRIVER_BOT_TOKEN отсутствуют!")
    sys.exit(1)

client_bot = Bot(token=CLIENT_TOKEN)
driver_bot = Bot(token=DRIVER_TOKEN)

client_dp = Dispatcher()
driver_dp = Dispatcher()

scope = ["https://www.googleapis.com/auth/spreadsheets"]
creds_path = os.getenv("GOOGLE_CREDS_PATH", "creds.json")
sheet_name = os.getenv("GOOGLE_SHEET_NAME", "Заявки Mavsimi Rason")

sheet = None
try:
    if os.path.exists(creds_path):
        creds = Credentials.from_service_account_file(creds_path, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open(sheet_name).worksheet("Лист1")
        logging.info("✅ База данных Google Sheets успешно подключена!")
    else:
        # Критическая ошибка — без Google Sheets боты бесполезны
        logging.critical(f"❌ Файл авторизации {creds_path} не найден! Запуск невозможен.")
        sys.exit(1)
except Exception as e:
    logging.critical(f"❌ Критическая ошибка подключения к Google Таблицам: {e}")
    sys.exit(1)