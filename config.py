import os
import sys
import logging
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

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

    LIST1_HEADERS = [
        "Статус", "ID заказа", "Дата", "Цена", "Город откуда", "Адрес откуда",
        "Город куда", "Адрес куда", "Ориентир", "Тип", "Вес", "Габариты",
        "ФИО отправителя", "Тел отправителя", "ФИО получателя", "Тел получателя",
        "Источник", "Имя курьера", "Chat ID клиента", "Telegram ID курьера"
    ]
    try:
        sheet = spreadsheet.worksheet("Лист1")
        if not sheet.row_values(1):
            sheet.update("A1", [LIST1_HEADERS])
            logging.info("✅ Заголовки добавлены в существующий лист 'Лист1'")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title="Лист1", rows=5000, cols=20)
        sheet.append_row(LIST1_HEADERS)
        logging.info("✅ Создан лист 'Лист1'")

    def _ensure_columns(ws, col_names: tuple[str, ...]) -> None:
        """Дописывает недостающие заголовки в конец листа, расширяя сетку при нехватке столбцов."""
        existing_headers = ws.row_values(1)
        col_count = ws.col_count  # кэш до наших изменений, дальше отслеживаем сами
        for col_name in col_names:
            if col_name in existing_headers:
                continue
            existing_headers.append(col_name)
            needed = len(existing_headers)
            if col_count < needed:
                ws.add_cols(needed - col_count)
                col_count = needed
            ws.update_cell(1, needed, col_name)
            logging.info(f"✅ Добавлена колонка '{col_name}' в лист '{ws.title}'")

    try:
        drivers_sheet = spreadsheet.worksheet("Водители")
        _ensure_columns(drivers_sheet, ("Язык", "Заявка ФИО"))
    except gspread.WorksheetNotFound:
        drivers_sheet = spreadsheet.add_worksheet(title="Водители", rows=1000, cols=10)
        drivers_sheet.append_row([
            "Статус", "Дата рег.", "ФИО", "Telegram ID",
            "Ставка (TJS)", "Дата оферты", "Topic ID", "Телефон", "Язык", "Заявка ФИО"
        ])
        logging.info("✅ Создан лист 'Водители'")

    try:
        clients_sheet = spreadsheet.worksheet("Клиенты")
        _ensure_columns(clients_sheet, ("Язык",))
    except gspread.WorksheetNotFound:
        clients_sheet = spreadsheet.add_worksheet(title="Клиенты", rows=5000, cols=8)
        clients_sheet.append_row([
            "Статус", "Дата рег.", "ФИО", "Телефон", "Адрес забора", "Chat ID", "Topic ID", "Язык"
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

LANG_SEPARATOR = "───────────────────────"


def pick_lang(text: str, lang: str) -> str:
    """
    Большинство длинных сообщений в коде написаны в формате
    "тадж. часть\\n{LANG_SEPARATOR}\\nрус. часть". Если у пользователя уже
    сохранён язык — возвращает только его часть, иначе (lang не задан/'both'
    или в тексте нет разделителя) — текст как есть, без изменений.
    """
    if lang not in ("ru", "tj") or LANG_SEPARATOR not in text:
        return text
    tj_part, ru_part = text.split(LANG_SEPARATOR, 1)
    return ru_part.strip() if lang == "ru" else tj_part.strip()

SUPPORT_CHAT_ID: str = os.getenv("SUPPORT_CHAT_ID", "")