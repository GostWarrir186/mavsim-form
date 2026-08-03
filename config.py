import os
import sys
import time
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
managers_sheet = None
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
        "Статус", "ID заказа", "Дата принятия", "Дата доставки", "Цена", "Город откуда", "Адрес откуда",
        "Город куда", "Адрес куда", "Ориентир", "Тип", "Вес", "Габариты",
        "ФИО отправителя", "Тел отправителя", "ФИО получателя", "Тел получателя",
        "Источник", "Имя курьера", "Chat ID клиента", "Telegram ID курьера"
    ]
    def _rename_header(ws, old_name: str, new_name: str) -> None:
        """Переименовывает заголовок столбца, если старое имя есть, а нового ещё нет."""
        headers = ws.row_values(1)
        if old_name in headers and new_name not in headers:
            ws.update_cell(1, headers.index(old_name) + 1, new_name)
            logging.info(f"✅ Столбец '{old_name}' → '{new_name}' в листе '{ws.title}'")

    def _ensure_column_at(ws, header: str, col: int) -> None:
        """Физически вставляет пустой столбец с заголовком на позицию col (1-based),
        сдвигая существующие данные вправо. Ничего не делает, если столбец уже есть."""
        headers = ws.row_values(1)
        if header in headers:
            return
        ws.insert_cols([[header]], col=col)
        logging.info(f"✅ Вставлен столбец '{header}' на позицию {col} в листе '{ws.title}'")

    try:
        sheet = spreadsheet.worksheet("Лист1")
        if not sheet.row_values(1):
            sheet.update("A1", [LIST1_HEADERS])
            logging.info("✅ Заголовки добавлены в существующий лист 'Лист1'")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title="Лист1", rows=5000, cols=21)
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

    # Миграция схемы Лист1: "Дата" → "Дата принятия", "Дата доставки" сразу после (столбец D)
    _rename_header(sheet, "Дата", "Дата принятия")
    _ensure_column_at(sheet, "Дата доставки", 4)

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
        managers_sheet = spreadsheet.worksheet("Менеджеры")
    except gspread.WorksheetNotFound:
        managers_sheet = spreadsheet.add_worksheet(title="Менеджеры", rows=100, cols=2)
        managers_sheet.append_row(["Telegram ID", "ФИО"])
        logging.info("✅ Создан лист 'Менеджеры'")

    try:
        orders_info_sheet = spreadsheet.worksheet("Заказы")
        _rename_header(orders_info_sheet, "Дата", "Дата принятия")
        _ensure_column_at(orders_info_sheet, "Дата доставки", 3)
    except gspread.WorksheetNotFound:
        orders_info_sheet = spreadsheet.add_worksheet(title="Заказы", rows=5000, cols=17)
        orders_info_sheet.append_row([
            "ID заказа", "Дата принятия", "Дата доставки", "Статус", "Цена (TJS)", "Тип доставки", "Вес (кг)", "Габариты",
            "ФИО отправителя", "Тел отправителя", "Город откуда", "Адрес откуда",
            "ФИО получателя", "Тел получателя", "Город куда", "Адрес куда", "Ориентир"
        ])
        logging.info("✅ Создан лист 'Заказы'")

    logging.info("✅ База данных Google Sheets успешно подключена!")
except Exception as e:
    logging.critical(f"❌ Критическая ошибка подключения к Google Таблицам: {e}")
    sys.exit(1)

def sanitize_for_sheet(value) -> str:
    """Предотвращает formula injection в Google Sheets: строки, начинающиеся
    с = + - @ \\t \\r, экранируются апострофом. Длина ограничена."""
    s = str(value) if value is not None else ""
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        s = "'" + s
    return s[:500]


_MD_STRIP = str.maketrans({c: " " for c in "_*`[]"})


def md_escape(value) -> str:
    """Нейтрализует Markdown-разметку в пользовательских значениях (ФИО, адреса,
    причины), чтобы инъекция не ломала parse_mode=Markdown-сообщения."""
    return str(value).translate(_MD_STRIP) if value is not None else ""


SUPPORT_CHAT_ID: str = os.getenv("SUPPORT_CHAT_ID", "")


# Список менеджеров меняется раз в месяц, а читался при КАЖДОМ новом заказе,
# регистрации курьера и отказе — лишние запросы к Google на ровном месте.
_MANAGERS_CACHE_TTL = 60  # секунд
_managers_cache: dict = {"ids": [], "ts": 0.0}


def get_manager_chat_ids(force_refresh: bool = False) -> list[str]:
    """Telegram ID менеджеров: лист 'Менеджеры' (колонка 'Telegram ID') + MANAGER_CHAT_ID из .env, если задан.
    Результат кэшируется на _MANAGERS_CACHE_TTL секунд."""
    now = time.monotonic()
    if not force_refresh and _managers_cache["ids"] and now - _managers_cache["ts"] < _MANAGERS_CACHE_TTL:
        return list(_managers_cache["ids"])

    ids: list[str] = []
    if managers_sheet:
        try:
            rows = managers_sheet.get_all_values()[1:]
            ids = [row[0].strip() for row in rows if row and row[0].strip()]
        except Exception as e:
            logging.error(f"Ошибка чтения листа 'Менеджеры': {e}")
            # при сбое отдаём последний удачный список, а не пустоту:
            # иначе менеджеры просто перестают получать уведомления
            if _managers_cache["ids"]:
                return list(_managers_cache["ids"])
    env_id = os.getenv("MANAGER_CHAT_ID", "")
    if env_id and env_id not in ids:
        ids.append(env_id)

    _managers_cache["ids"] = list(ids)
    _managers_cache["ts"] = now
    return ids


def sync_update_order_info_status(order_id: str, status: str) -> None:
    """Синхронизирует статус заказа в чистый лист 'Заказы' (колонка D) по ID заказа (колонка A)."""
    if not orders_info_sheet:
        return
    try:
        cell = orders_info_sheet.find(str(order_id), in_column=1)
        if cell:
            orders_info_sheet.update_cell(cell.row, 4, status)
    except Exception as e:
        logging.error(f"Ошибка синхронизации статуса в листе 'Заказы' ({order_id}): {e}")


def _col_index_by_header(ws, header: str):
    """Номер столбца (1-based) по названию заголовка, либо None."""
    try:
        headers = ws.row_values(1)
        return headers.index(header) + 1 if header in headers else None
    except Exception:
        return None


def sync_set_delivery_time(order_id: str, when: str) -> None:
    """Пишет дату/время доставки в столбец 'Дата доставки' листов 'Лист1' и 'Заказы' по ID заказа."""
    if sheet:
        try:
            cell = sheet.find(str(order_id), in_column=2)  # ID заказа = столбец B
            col = _col_index_by_header(sheet, "Дата доставки")
            if cell and col:
                sheet.update_cell(cell.row, col, when)
        except Exception as e:
            logging.error(f"Ошибка записи даты доставки в 'Лист1' ({order_id}): {e}")
    if orders_info_sheet:
        try:
            cell = orders_info_sheet.find(str(order_id), in_column=1)  # ID заказа = столбец A
            col = _col_index_by_header(orders_info_sheet, "Дата доставки")
            if cell and col:
                orders_info_sheet.update_cell(cell.row, col, when)
        except Exception as e:
            logging.error(f"Ошибка записи даты доставки в 'Заказы' ({order_id}): {e}")