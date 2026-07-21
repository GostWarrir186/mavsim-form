import asyncio
import base64
import json
import logging
import os
import threading
import traceback
from datetime import datetime, timezone, timedelta
from io import BytesIO

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from aiogram import types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import driver_bot as bot, client_bot, manager_bot as mgr_bot, driver_dp as dp, sheet, drivers_sheet, get_or_create_feedback_topic

# ─── Конфигурация ───────────────────────────────────────────────────────────
DRIVER_WEBAPP_URL   = os.getenv("DRIVER_WEBAPP_URL", "")
REPORT_PICKER_URL   = os.getenv("REPORT_PICKER_URL", "")
DEFAULT_DRIVER_RATE = float(os.getenv("DEFAULT_DRIVER_RATE", "15.0"))
LINK_TO_DRIVER_OFFER = os.getenv("DRIVER_OFFER_URL", "https://www.google.com")
SUPPORT_CHAT_ID     = os.getenv("SUPPORT_CHAT_ID", "")
MANAGER_CHAT_ID     = os.getenv("MANAGER_CHAT_ID", "")
NEW_ORDERS_POLL_SECONDS = int(os.getenv("NEW_ORDERS_POLL_SECONDS", "15"))

DUSHANBE_TZ = timezone(timedelta(hours=5))

RU_MONTHS = {
    1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
    7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь",
}
RU_MONTHS_GEN = {
    1:"января",2:"февраля",3:"марта",4:"апреля",5:"мая",6:"июня",
    7:"июля",8:"августа",9:"сентября",10:"октября",11:"ноября",12:"декабря",
}

# Блокировка для атомарных операций с заказами
_order_take_lock = threading.Lock()


class DriverRegistration(StatesGroup):
    waiting_for_fio   = State()
    waiting_for_phone = State()

class DriverRejectReason(StatesGroup):
    waiting_for_reason = State()

class DriverSupport(StatesGroup):
    waiting_for_message = State()
    chatting = State()

class DriverFeedback(StatesGroup):
    waiting_for_message = State()


# ─── Вспомогательные функции ────────────────────────────────────────────────
async def _get_active_driver(user_id: int) -> list | None:
    data = await asyncio.to_thread(_sync_get_driver, str(user_id))
    return data if (data and data[0].upper() == "ACTIVE") else None


def _pad_row(row: list, size: int = 20) -> list:
    return row + [""] * max(0, size - len(row))


def _now_dushanbe() -> str:
    return datetime.now(DUSHANBE_TZ).strftime("%d.%m.%Y %H:%M")


def _month_label(month_str: str) -> str:
    try:
        dt = datetime.strptime(month_str, "%m.%Y")
        return f"{RU_MONTHS[dt.month]} {dt.year}"
    except ValueError:
        return month_str


def _week_label(week_start: datetime, week_end: datetime) -> str:
    if week_start.month == week_end.month:
        return f"{week_start.day}–{week_end.day} {RU_MONTHS_GEN[week_start.month]} {week_start.year}"
    return (
        f"{week_start.day} {RU_MONTHS_GEN[week_start.month]} – "
        f"{week_end.day} {RU_MONTHS_GEN[week_end.month]} {week_end.year}"
    )


def _current_week_range(now: datetime) -> tuple[datetime, datetime]:
    naive = now.replace(tzinfo=None)
    monday = naive.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=naive.weekday())
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return monday, sunday


def _month_range(now: datetime) -> tuple[datetime, datetime]:
    naive = now.replace(tzinfo=None)
    first = naive.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_m = (naive.month % 12) + 1
    next_y = naive.year + (1 if naive.month == 12 else 0)
    last = datetime(next_y, next_m, 1) - timedelta(seconds=1)
    return first, last


# ─── Google Sheets: Водители ─────────────────────────────────────────────────
def _sync_get_driver(chat_id: str) -> list | None:
    if not drivers_sheet:
        return None
    try:
        cell = drivers_sheet.find(str(chat_id), in_column=4)
        return drivers_sheet.row_values(cell.row) if cell else None
    except Exception as e:
        logging.error(f"Ошибка поиска водителя {chat_id}: {e}")
        return None


def _sync_get_driver_support_topic(chat_id: str) -> str | None:
    if not drivers_sheet:
        return None
    try:
        cell = drivers_sheet.find(str(chat_id), in_column=4)
        if not cell:
            return None
        row = drivers_sheet.row_values(cell.row)
        return row[6] if len(row) > 6 and row[6] else None
    except Exception as e:
        logging.error(f"Ошибка получения driver support_topic для {chat_id}: {e}")
        return None


def _sync_save_driver_support_topic(chat_id: str, topic_id: int) -> None:
    if not drivers_sheet:
        return
    try:
        cell = drivers_sheet.find(str(chat_id), in_column=4)
        if cell:
            drivers_sheet.update_cell(cell.row, 7, str(topic_id))
    except Exception as e:
        logging.error(f"Ошибка сохранения driver support_topic для {chat_id}: {e}")


def _sync_get_driver_by_topic(topic_id: str) -> str | None:
    if not drivers_sheet:
        return None
    try:
        cell = drivers_sheet.find(str(topic_id), in_column=7)
        if not cell:
            return None
        row = drivers_sheet.row_values(cell.row)
        return row[3] if len(row) > 3 else None
    except Exception as e:
        logging.error(f"Ошибка поиска водителя по topic_id={topic_id}: {e}")
        return None


def _sync_register_driver(chat_id: str, fio: str, phone: str = "") -> bool:
    if not drivers_sheet:
        return False
    try:
        now = _now_dushanbe()
        drivers_sheet.append_row([
            "PENDING", now, fio, str(chat_id),
            str(DEFAULT_DRIVER_RATE), now, "", phone
        ], table_range="A1")
        return True
    except Exception as e:
        logging.error(f"Ошибка регистрации водителя {chat_id}: {e}")
        return False


def _sync_get_all_active_drivers() -> list[dict]:
    if not drivers_sheet:
        return []
    try:
        all_rows = drivers_sheet.get_all_values()
        result = []
        for idx, row in enumerate(all_rows):
            if idx == 0 or len(row) < 4:
                continue
            if row[0].upper().strip() != "ACTIVE":
                continue
            result.append({"row_num": idx + 1, "fio": row[2], "telegram_id": row[3]})
        return result
    except Exception as e:
        logging.error(f"Ошибка получения активных курьеров: {e}")
        return []


def _sync_get_driver_deliveries(chat_id: str, date_from: datetime, date_to: datetime) -> list[dict]:
    if not sheet:
        return []
    try:
        all_rows = sheet.get_all_values()
        result = []
        for idx, row in enumerate(all_rows):
            if idx == 0:
                continue
            row = _pad_row(row)
            if str(row[19]).strip() != str(chat_id):
                continue
            date_cell = row[2].strip()
            try:
                dt = datetime.strptime(date_cell[:16], "%d.%m.%Y %H:%M")
            except ValueError:
                continue
            if not (date_from <= dt <= date_to):
                continue
            result.append({
                "i":  row[1],
                "d":  dt.strftime("%Y-%m-%d"),
                "t":  dt.strftime("%H:%M"),
                "f":  row[4],
                "to": row[6],
                "tp": row[9],
                "s":  row[0].upper(),
            })
        return result
    except Exception as e:
        logging.error(f"Ошибка чтения доставок курьера {chat_id}: {e}")
        return []


# ─── Google Sheets: Заказы ───────────────────────────────────────────────────
def _sync_get_free_orders() -> list:
    if not sheet:
        return []
    try:
        all_rows = sheet.get_all_values()
        free_list = []
        for idx, row in enumerate(all_rows):
            if idx == 0:
                continue
            status = row[0].upper().strip() if row else ""
            if status == "READY_FOR_DRIVERS":
                free_list.append({
                    "row_num":          idx + 1,
                    "id":               row[1],
                    "price":            row[3],
                    "city_pickup":      row[4],
                    "address_pickup":   row[5],
                    "city_delivery":    row[6],
                    "address_delivery": row[7],
                    "driver_comment":   row[8]  if len(row) > 8  else "Нет",
                    "delivery_type":    row[9]  if len(row) > 9  else "DOOR",
                    "s_name":           row[12] if len(row) > 12 else "—",
                    "r_phone":          row[15] if len(row) > 15 else "—",
                })
        return free_list
    except Exception as e:
        logging.error(f"Ошибка чтения свободных заказов: {e}")
        return []


def _sync_take_order(row_num: int, courier_name: str, courier_id: str) -> bool:
    if not sheet:
        return False
    with _order_take_lock:
        try:
            row = sheet.row_values(row_num)
            if (row[0].upper().strip() if row else "") != "READY_FOR_DRIVERS":
                return False
            sheet.batch_update([
                {"range": f"A{row_num}", "values": [["TAKEN"]]},
                {"range": f"R{row_num}", "values": [[courier_name]]},
                {"range": f"T{row_num}", "values": [[str(courier_id)]]},
            ])
            return True
        except Exception as e:
            logging.error(f"Ошибка захвата заказа (строка {row_num}): {e}")
            return False


def _sync_release_order(row_num: int, courier_id: str) -> bool:
    """TAKEN → READY_FOR_DRIVERS. Проверяет, что именно этот курьер владеет заказом."""
    if not sheet:
        return False
    with _order_take_lock:
        try:
            row = _pad_row(sheet.row_values(row_num))
            if row[0].upper().strip() != "TAKEN":
                return False
            if str(row[19]).strip() != str(courier_id):
                return False
            sheet.batch_update([
                {"range": f"A{row_num}", "values": [["READY_FOR_DRIVERS"]]},
                {"range": f"R{row_num}", "values": [[""]]},
                {"range": f"T{row_num}", "values": [[""]]},
            ])
            return True
        except Exception as e:
            logging.error(f"Ошибка освобождения заказа (строка {row_num}): {e}")
            return False


def _sync_update_status(row_num: int, status: str) -> bool:
    if not sheet:
        return False
    try:
        sheet.update_cell(row_num, 1, status)
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления статуса на {status} (строка {row_num}): {e}")
        return False


def _sync_find_order_by_id(order_id: str) -> tuple[int, list] | None:
    if not sheet:
        return None
    try:
        cell = sheet.find(str(order_id), in_column=2)
        if not cell:
            return None
        return cell.row, _pad_row(sheet.row_values(cell.row))
    except Exception as e:
        logging.error(f"Ошибка поиска заказа {order_id}: {e}")
        return None


def _sync_reassign_order(row_num: int, new_courier_name: str, new_courier_id: str) -> tuple[bool, str, str]:
    """Переназначает заказ. Разрешено для TAKEN/LOADING/IN_TRANSIT/ARRIVED → сбрасывает в TAKEN."""
    if not sheet:
        return False, "", ""
    with _order_take_lock:
        try:
            row = _pad_row(sheet.row_values(row_num))
            status = row[0].upper().strip()
            if status not in ("TAKEN", "LOADING", "IN_TRANSIT", "ARRIVED"):
                return False, "", ""
            old_courier_id = row[19]
            order_id = row[1]
            sheet.batch_update([
                {"range": f"A{row_num}", "values": [["TAKEN"]]},
                {"range": f"R{row_num}", "values": [[new_courier_name]]},
                {"range": f"T{row_num}", "values": [[str(new_courier_id)]]},
            ])
            return True, old_courier_id, order_id
        except Exception as e:
            logging.error(f"Ошибка переназначения заказа (строка {row_num}): {e}")
            return False, "", ""


def _sync_get_orders_for_dashboard() -> tuple[list, list, list]:
    """Читает Лист1, возвращает (active_orders, free_orders, new_orders)."""
    active, free, new = [], [], []
    if not sheet:
        return active, free, new
    try:
        for idx, row in enumerate(sheet.get_all_values()):
            if idx == 0:
                continue
            row = _pad_row(row)
            status = row[0].upper().strip()
            if status in ("TAKEN", "LOADING", "IN_TRANSIT", "ARRIVED"):
                active.append({
                    "row":        idx + 1,
                    "id":         row[1],
                    "status":     status,
                    "courier":    row[17],
                    "courier_id": row[19],
                    "city_from":  row[4],
                    "city_to":    row[6],
                    "addr_from":  row[5],
                    "addr_to":    row[7],
                    "price":      row[3],
                    "r_phone":    row[15],
                    "s_name":     row[12],
                })
            elif status == "READY_FOR_DRIVERS":
                free.append({
                    "row":       idx + 1,
                    "id":        row[1],
                    "city_from": row[4],
                    "city_to":   row[6],
                    "price":     row[3],
                    "s_name":    row[12],
                })
            elif status == "NEW":
                new.append({
                    "row":       idx + 1,
                    "id":        row[1],
                    "city_from": row[4],
                    "city_to":   row[6],
                    "price":     row[3],
                    "s_name":    row[12],
                    "date":      row[2],
                })
    except Exception as e:
        logging.error(f"Ошибка чтения заказов для дашборда: {e}")
    return active, free, new


def _sync_get_drivers_for_dashboard() -> list:
    """Читает лист Водители, возвращает список ACTIVE курьеров."""
    result = []
    if not drivers_sheet:
        return result
    try:
        for idx, row in enumerate(drivers_sheet.get_all_values()):
            if idx == 0 or len(row) < 4:
                continue
            if row[0].upper().strip() != "ACTIVE":
                continue
            result.append({"fio": row[2], "tid": row[3], "row": idx + 1})
    except Exception as e:
        logging.error(f"Ошибка чтения курьеров для дашборда: {e}")
    return result


async def _async_get_admin_dashboard_data() -> dict:
    """Читает оба листа параллельно — вдвое быстрее последовательного чтения."""
    (active, free, new), drivers = await asyncio.gather(
        asyncio.to_thread(_sync_get_orders_for_dashboard),
        asyncio.to_thread(_sync_get_drivers_for_dashboard),
    )
    busy_ids = {o["courier_id"] for o in active}
    couriers = [{**d, "busy": d["tid"] in busy_ids} for d in drivers]
    return {"orders": active, "free": free, "new": new, "couriers": couriers}


# ─── Excel-отчёт ─────────────────────────────────────────────────────────────
def generate_excel_report(driver_name: str, rate: float, deliveries: list[dict], period_label: str) -> BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Отчёт"

    BLUE   = "FF2481CC"
    L_BLUE = "FFD6EAF8"
    WHITE  = "FFFFFFFF"
    GRAY   = "FFF4F4F5"

    thin = Side(border_style="thin", color="FFD0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def cell_style(ws, row, col, value, bold=False, bg=None, color="FF1C1C1E",
                   align="left", size=11, wrap=False):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=bold, color=color, size=size, name="Calibri")
        if bg:
            c.fill = PatternFill("solid", fgColor=bg)
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
        c.border = border
        return c

    ws.merge_cells("A1:H1")
    c = ws.cell(row=1, column=1, value="MAVSIMI RASON — Отчёт курьера")
    c.font = Font(bold=True, color=WHITE, size=14, name="Calibri")
    c.fill = PatternFill("solid", fgColor=BLUE)
    c.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("A2:H2")
    c2 = ws.cell(row=2, column=1,
                 value=f"Курьер: {driver_name}   |   Период: {period_label}   |   Ставка: {rate:.2f} TJS / доставка")
    c2.font = Font(bold=False, color="FF555555", size=11, name="Calibri")
    c2.fill = PatternFill("solid", fgColor=GRAY)
    c2.alignment = Alignment(horizontal="center", vertical="center")

    delivered   = [d for d in deliveries if d["s"] == "DELIVERED"]
    total_count = len(delivered)
    total_earn  = total_count * rate

    ws.merge_cells("A3:D3")
    ws.merge_cells("E3:H3")
    c3a = ws.cell(row=3, column=1, value=f"Итого доставок: {total_count}")
    c3a.font = Font(bold=True, color=WHITE, size=12, name="Calibri")
    c3a.fill = PatternFill("solid", fgColor=BLUE)
    c3a.alignment = Alignment(horizontal="center", vertical="center")
    c3b = ws.cell(row=3, column=5, value=f"Итого к выплате: {total_earn:.2f} TJS")
    c3b.font = Font(bold=True, color=WHITE, size=12, name="Calibri")
    c3b.fill = PatternFill("solid", fgColor="FF22A368")
    c3b.alignment = Alignment(horizontal="center", vertical="center")

    headers = ["№", "Дата", "Время", "Откуда", "Куда", "Тип", "Статус", "Заработок (TJS)"]
    for col, h in enumerate(headers, 1):
        cell_style(ws, 4, col, h, bold=True, bg=BLUE, color=WHITE, align="center", size=11)

    col_widths = [5, 13, 9, 18, 18, 12, 14, 16]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 24
    ws.row_dimensions[4].height = 20

    for i, d in enumerate(deliveries, 1):
        r = i + 4
        bg = L_BLUE if i % 2 == 0 else WHITE
        is_done = d["s"] == "DELIVERED"
        earn = rate if is_done else 0.0
        status_label = {
            "DELIVERED": "✓ Доставлен", "IN_TRANSIT": "В пути",
            "LOADING": "Погрузка", "TAKEN": "Взят", "ARRIVED": "На месте",
        }.get(d["s"], d["s"])
        cell_style(ws, r, 1, i,            bg=bg, align="center")
        cell_style(ws, r, 2, d["d"],       bg=bg, align="center")
        cell_style(ws, r, 3, d["t"],       bg=bg, align="center")
        cell_style(ws, r, 4, d["f"],       bg=bg)
        cell_style(ws, r, 5, d["to"],      bg=bg)
        cell_style(ws, r, 6, "ПВЗ" if d["tp"] == "PVZ" else "До двери", bg=bg, align="center")
        cell_style(ws, r, 7, status_label, bg=bg, align="center",
                   color="FF2BCA80" if is_done else "FF555555", bold=is_done)
        earn_cell = cell_style(ws, r, 8, earn, bg=bg, align="center",
                               bold=is_done, color="FF2481CC" if is_done else "FF999999")
        earn_cell.number_format = '0.00 "TJS"'
        ws.row_dimensions[r].height = 18

    last = len(deliveries) + 5
    ws.merge_cells(f"A{last}:G{last}")
    cell_style(ws, last, 1, "ИТОГО К ВЫПЛАТЕ:", bold=True, bg=GRAY, align="right", size=12)
    earn_total = ws.cell(row=last, column=8, value=total_earn)
    earn_total.font = Font(bold=True, color="FF22A368", size=13, name="Calibri")
    earn_total.fill = PatternFill("solid", fgColor=GRAY)
    earn_total.alignment = Alignment(horizontal="center", vertical="center")
    earn_total.number_format = '0.00 "TJS"'
    earn_total.border = border
    ws.row_dimensions[last].height = 22
    ws.freeze_panes = "A5"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ─── Клавиатура главного меню водителя ──────────────────────────────────────
async def build_driver_main_menu(driver_id: int):
    b = ReplyKeyboardBuilder()
    b.button(text="🔍 Фармоишҳои озод / Свободные заказы")
    if DRIVER_WEBAPP_URL:
        try:
            driver_data = await asyncio.to_thread(_sync_get_driver, str(driver_id))
            if driver_data and driver_data[0].upper() == "ACTIVE":
                fio  = driver_data[2] if len(driver_data) > 2 else "Курьер"
                rate = float(driver_data[4]) if len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
                now = datetime.now(DUSHANBE_TZ)
                date_from, date_to = _month_range(now)
                week_start_dt, week_end_dt = _current_week_range(now)
                deliveries = await asyncio.to_thread(
                    _sync_get_driver_deliveries, str(driver_id), date_from, date_to
                )
                payload = {
                    "name": fio, "rate": rate,
                    "month": now.strftime("%m.%Y"),
                    "month_label": _week_label(week_start_dt, week_end_dt),
                    "deliveries": deliveries,
                }
                b64 = base64.urlsafe_b64encode(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
                ).decode().rstrip("=")
                b.button(text="📊 Кабинети ман / Мой кабинет",
                         web_app=types.WebAppInfo(url=f"{DRIVER_WEBAPP_URL}?d={b64}"))
            else:
                b.button(text="📊 Кабинети ман / Мой кабинет")
        except Exception:
            b.button(text="📊 Кабинети ман / Мой кабинет")
    else:
        b.button(text="📊 Кабинети ман / Мой кабинет")
    b.button(text="📞 Дастгирӣ / Поддержка")
    b.button(text="💡 Бознигарӣ / Обратная связь")
    b.adjust(1)
    return b.as_markup(resize_keyboard=True)


async def send_client_push(chat_id: str, text: str):
    if client_bot and chat_id and str(chat_id).isdigit():
        try:
            await client_bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Не удалось отправить пуш клиенту {chat_id}: {e}")


# ─── Глобальная навигация ────────────────────────────────────────────────────
@dp.message(F.text == "🔙 Главное меню")
async def driver_go_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Амалро интихоб кунед / Выберите действие:",
                         reply_markup=await build_driver_main_menu(message.from_user.id))


# ─── Регистрация ─────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start_driver(message: types.Message, state: FSMContext):
    await state.clear()
    driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))

    if not driver_data:
        b = ReplyKeyboardBuilder()
        b.button(text="📝 Офертаро қабул кунед / Принять оферту и зарегистрироваться")
        await message.answer(
            "💼 **Ба Mavsimi Rason хуш омадед!**\n\n"
            "Барои кор ҳамчун курьер шартҳои ҳамкориро қабул кунед.\n\n"
            f"📋 [Оферта барои курьерҳо]({LINK_TO_DRIVER_OFFER})\n\n"
            "──────────────────────\n\n"
            "💼 **Добро пожаловать в Mavsimi Rason!**\n\n"
            "Для работы курьером необходимо принять условия сотрудничества.\n\n"
            f"📋 [Оферта для курьеров]({LINK_TO_DRIVER_OFFER})\n\n"
            "Ознакомьтесь и нажмите кнопку ниже:",
            reply_markup=b.as_markup(resize_keyboard=True),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    elif driver_data[0].upper() == "PENDING":
        await message.answer(
            "⏳ **Дархост дар баррасӣ.**\n\n"
            "Менеҷер маълумоти шуморо баррасӣ мекунад.\n"
            "Баъдтар /start-ро пахш кунед.\n\n"
            "⏳ **Заявка на рассмотрении.**\n\n"
            "Менеджер проверит ваши данные и активирует аккаунт.\n"
            "Попробуйте позже — нажмите /start чтобы проверить статус.",
            parse_mode="Markdown"
        )
    elif driver_data[0].upper() == "ACTIVE":
        fio = driver_data[2] if len(driver_data) > 2 else "Курьер"
        await message.answer(
            f"👋 **Хуш омадед, {fio}!**\n\nАмалро интихоб кунед:\n\n"
            f"👋 **С возвращением, {fio}!**\n\nВыберите действие:",
            reply_markup=await build_driver_main_menu(message.from_user.id),
            parse_mode="Markdown"
        )
    else:
        await message.answer("⛔ Аккаунти шумо баста шудааст.\n\n⛔ Аккаунт заблокирован. Обратитесь к администратору.")


@dp.message(F.text == "📝 Офертаро қабул кунед / Принять оферту и зарегистрироваться")
async def accept_offer(message: types.Message, state: FSMContext):
    await message.answer(
        "✅ Офертаро қабул кардед!\n\n"
        "**Номи пурра**-и худро ворид кунед (Фамилия Ном Насаб):\n\n"
        "✅ Отлично! Вы принимаете условия оферты.\n\n"
        "Введите ваше **ФИО** (Фамилия Имя Отчество):",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(DriverRegistration.waiting_for_fio)


@dp.message(DriverRegistration.waiting_for_fio)
async def save_driver_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip() if message.text else ""
    if not fio or len(fio) < 3:
        await message.answer("❌ Номи пурраи худро ворид кунед (ҳадди ақал 3 аломат).\n\n❌ Пожалуйста, введите полное ФИО (минимум 3 символа).")
        return
    await state.update_data(fio=fio)
    await state.set_state(DriverRegistration.waiting_for_phone)
    b = ReplyKeyboardBuilder()
    b.button(text="📱 Рақами телефонро мубодила кунед / Поделиться номером", request_contact=True)
    await message.answer(
        f"✅ Ном қабул шуд: *{fio}*\n\n"
        "Рақами телефони худро ворид кунед ё тугмаи зерро пахш кунед:\n\n"
        "Теперь введите ваш номер телефона или нажмите кнопку ниже:",
        reply_markup=b.as_markup(resize_keyboard=True, one_time_keyboard=True),
        parse_mode="Markdown"
    )


@dp.message(DriverRegistration.waiting_for_phone)
async def save_driver_phone(message: types.Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
        if not phone.startswith("+"):
            phone = "+" + phone
    elif message.text:
        phone = message.text.strip()
    else:
        await message.answer("❌ Пожалуйста, отправьте номер телефона.")
        return

    data = await state.get_data()
    fio = data.get("fio", "")
    await state.clear()

    success = await asyncio.to_thread(_sync_register_driver, str(message.from_user.id), fio, phone)
    if success:
        await message.answer(
            f"✅ **Дархост фиристода шуд, {fio}!**\n\n"
            "Менеҷер маълумотро баррасӣ мекунад.\n"
            "/start-ро пахш кунед то ҳолатро тафтиш кунед.\n\n"
            "✅ **Заявка отправлена!**\n\n"
            "Менеджер проверит данные и активирует ваш аккаунт.\n"
            "Нажмите /start чтобы проверить статус.",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        if mgr_bot and MANAGER_CHAT_ID:
            try:
                b = InlineKeyboardBuilder()
                b.button(text="✅ Одобрить", callback_data=f"approve_driver:{message.from_user.id}")
                b.button(text="❌ Отклонить", callback_data=f"reject_driver:{message.from_user.id}")
                b.adjust(2)
                await mgr_bot.send_message(
                    chat_id=int(MANAGER_CHAT_ID),
                    text=(
                        f"👤 <b>Новый курьер</b>\n"
                        f"ФИО: <b>{fio}</b>\n"
                        f"📱 Телефон: <code>{phone}</code>\n"
                        f"ID: <code>{message.from_user.id}</code>\n\n"
                        f"Одобрить заявку?"
                    ),
                    reply_markup=b.as_markup(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить менеджера о новом курьере: {e}")
    else:
        await message.answer("❌ Ошибка при регистрации. Попробуйте позже (/start).")


@dp.message(F.web_app_data)
async def handle_webapp(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return

    action = data.get("action")

    # ── Еженедельный отчёт ───────────────────────────────────────────────────
    if action == "generate_report":
        if not await _get_active_driver(message.from_user.id):
            return
        week_start_str = data.get("week_start", "")
        try:
            week_start = datetime.strptime(week_start_str, "%Y-%m-%d")
        except ValueError:
            await message.answer("❌ Некорректный формат даты.")
            return
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        period_label = _week_label(week_start, week_end)

        driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))
        if not driver_data:
            await message.answer("❌ Вы не зарегистрированы. Нажмите /start.")
            return

        fio = driver_data[2] if len(driver_data) > 2 else "Курьер"
        try:
            rate = float(driver_data[4]) if len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
        except (ValueError, TypeError):
            rate = DEFAULT_DRIVER_RATE

        wait_msg = await message.answer("⏳ Формирую отчёт...")
        deliveries = await asyncio.to_thread(
            _sync_get_driver_deliveries, str(message.from_user.id), week_start, week_end
        )

        if not deliveries:
            await wait_msg.delete()
            await message.answer(f"📭 За {period_label} доставок не найдено.")
            return

        excel_buf = await asyncio.to_thread(generate_excel_report, fio, rate, deliveries, period_label)
        filename = f"report_{week_start_str}_{message.from_user.id}.xlsx"
        delivered_count = sum(1 for d in deliveries if d["s"] == "DELIVERED")
        await wait_msg.delete()
        await message.answer_document(
            types.BufferedInputFile(excel_buf.read(), filename=filename),
            caption=(
                f"📄 **Отчёт: {period_label}**\n"
                f"👤 {fio}\n"
                f"✅ Доставлено: {delivered_count}\n"
                f"💰 К выплате: {delivered_count * rate:.2f} TJS"
            ),
            parse_mode="Markdown"
        )
        return

    logging.warning(f"Неизвестный action из WebApp: {action}")


# ─── Биржа заказов ───────────────────────────────────────────────────────────
@dp.message(F.text == "🔍 Фармоишҳои озод / Свободные заказы")
async def show_jobs(message: types.Message):
    if not await _get_active_driver(message.from_user.id):
        await message.answer("⛔ Дастрасӣ манъ аст. /start-ро пахш кунед.\n\n⛔ Доступ запрещён. Нажмите /start.")
        return
    free_jobs = await asyncio.to_thread(_sync_get_free_orders)
    if not free_jobs:
        await message.answer("🕳️ Ҳоло дар бирже фармоишҳои озод нест.\n\n🕳️ На бирже сейчас нет свободных заказов.")
        return

    await message.answer(f"📦 **Фармоишҳои озод / Свободные заказы ({len(free_jobs)}):**", parse_mode="Markdown")
    for o in free_jobs:
        dtype_readable = "ПВЗ 🏢" if o["delivery_type"] == "PVZ" else "То дар / До двери 🚪"
        card = (
            f"🆔 **Фармоиш / Заказ №:** `{o['id']}`\n"
            f"• **Қабулкунанда / Получатель:** {o['r_phone']}\n"
            f"• **Намуд / Тип:** {dtype_readable}\n"
            f"• **Ориентир:** {o['driver_comment']}\n"
            f"────────────────────\n"
            f"📍 **Аз куҷо / Откуда:** {o['city_pickup']}, {o['address_pickup']}\n"
            f"🌆 **Ба куҷо / Куда:** {o['city_delivery']}, {o['address_delivery']}\n"
            f"💰 **Тариф:** {o['price']} TJS\n"
            f"👤 **Фиристанда / Отправитель:** {o['s_name']}"
        )
        b = InlineKeyboardBuilder()
        b.button(text="✅ Фармоишро гиред / Взять заказ", callback_data=f"take:{o['row_num']}")
        await message.answer(card, reply_markup=b.as_markup(), parse_mode="Markdown")


# ─── Управление заказом ──────────────────────────────────────────────────────
@dp.callback_query(F.data.startswith("take:"))
async def accept_order(callback: types.CallbackQuery):
    await callback.answer()
    if not await _get_active_driver(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён. Нажмите /start.")
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if row_num < 2 or not sheet:
            await callback.message.answer("❌ Некорректный номер заказа.")
            return

        c_name = callback.from_user.full_name
        c_id   = callback.from_user.id
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]

        success = await asyncio.to_thread(_sync_take_order, row_num, c_name, c_id)
        if not success:
            await callback.message.edit_text("❌ Этот заказ уже забрал другой водитель!", reply_markup=None)
            return

        b = InlineKeyboardBuilder()
        b.button(text="📦 Боркуниро оғоз кунед / Приступить к погрузке", callback_data=f"load:{row_num}")
        b.button(text="❌ Аз фармоиш даст кашидан / Отказаться от заказа", callback_data=f"reject:{row_num}")
        b.adjust(1)
        await callback.message.edit_text(
            f"🎉 **Шумо фармоиш {order_id} гирифтед!**\n\n"
            "Ба нуқтаи забт равед ва ҳангоми боркунӣ тугмаро пахш кунед.\n\n"
            f"🎉 **Вы взяли заказ {order_id}!**\n\n"
            "Отправляйтесь на точку забора и нажмите кнопку, когда начнёте погрузку.",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"🚚 **Фармоиши шумо {order_id} қабул шуд!**\n👤 **Курьер:** {c_name}\n\n"
                f"🚚 **Ваш заказ {order_id} принят курьером!**\n👤 **Курьер:** {c_name}")
    except Exception:
        logging.error(f"Сбой take: {traceback.format_exc()}")
        await callback.message.answer("❌ Ошибка на сервере. Попробуйте позже.")


@dp.callback_query(F.data.startswith("reject:"))
async def reject_order(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not await _get_active_driver(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён.")
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if not sheet:
            await callback.message.answer("❌ База данных недоступна.")
            return
        row_vals       = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]

        await state.set_state(DriverRejectReason.waiting_for_reason)
        await state.update_data(
            row_num=row_num,
            order_id=order_id,
            client_chat_id=client_chat_id,
            courier_name=callback.from_user.full_name,
            courier_id=str(callback.from_user.id),
        )

        b = InlineKeyboardBuilder()
        b.button(text="Гузаштан / Пропустить", callback_data="reject_skip")
        await callback.message.edit_text(
            f"↩️ Аз фармоиш даст кашидан <b>{order_id}</b>\n\n"
            "Сабаби рад кардан (матн ё акс бо тавзеҳот) нависед.\n"
            "Ё «Гузаштан»-ро пахш кунед.\n\n"
            f"↩️ Отказ от заказа <b>{order_id}</b>\n\n"
            "Укажите причину отказа (текст или фото с подписью).\n"
            "Или нажмите «Пропустить».",
            reply_markup=b.as_markup(),
            parse_mode="HTML"
        )
    except Exception:
        logging.error(f"Сбой reject: {traceback.format_exc()}")
        await callback.message.answer("❌ Ошибка на сервере.")


async def _do_reject(chat_id: int, state: FSMContext, reason: str | None, photo_file_id: str | None):
    """Финализирует отказ: освобождает заказ, уведомляет клиента и менеджера."""
    data = await state.get_data()
    await state.clear()

    row_num        = data["row_num"]
    order_id       = data["order_id"]
    client_chat_id = data["client_chat_id"]
    c_name         = data["courier_name"]
    c_id           = data["courier_id"]

    success = await asyncio.to_thread(_sync_release_order, row_num, c_id)
    if not success:
        from config import driver_bot as _bot
        await _bot.send_message(chat_id, "❌ Не удалось отказаться — статус заказа уже изменился.")
        return

    from config import driver_bot as _bot
    await _bot.send_message(
        chat_id,
        f"↩️ Шумо аз фармоиш <b>{order_id}</b> даст кашидед.\nФармоиш ба бирже баргашт.\n\n"
        f"↩️ Вы отказались от заказа <b>{order_id}</b>.\nЗаказ возвращён на биржу.",
        parse_mode="HTML"
    )

    async def _notify_client():
        if not client_chat_id:
            return
        await send_client_push(
            client_chat_id,
            f"ℹ️ Дар фармоиши шумо *{order_id}* тағйирот рӯй дод — курьери нав меҷӯем.\n\n"
            f"ℹ️ По вашему заказу *{order_id}* происходят изменения — ищем нового курьера."
        )

    async def _notify_manager():
        if not (mgr_bot and MANAGER_CHAT_ID):
            return
        mgr_text = (
            f"⚠️ Курьер <b>{c_name}</b> отказался от заказа <b>{order_id}</b>.\n"
            f"📝 Причина: {reason or '—'}"
        )
        if photo_file_id:
            # file_id привязан к боту, который его получил (driver_bot) —
            # manager_bot чужой file_id использовать не может, скачиваем и грузим заново
            photo_bytes = await bot.download(photo_file_id)
            await mgr_bot.send_photo(
                chat_id=int(MANAGER_CHAT_ID),
                photo=types.BufferedInputFile(photo_bytes.read(), filename="reject.jpg"),
                caption=mgr_text,
                parse_mode="HTML"
            )
        else:
            await mgr_bot.send_message(
                chat_id=int(MANAGER_CHAT_ID),
                text=mgr_text,
                parse_mode="HTML"
            )

    results = await asyncio.gather(_notify_client(), _notify_manager(), return_exceptions=True)
    for label, result in zip(("клиента", "менеджера"), results):
        if isinstance(result, Exception):
            logging.error(f"Не удалось уведомить {label} об отказе: {result}")


@dp.callback_query(F.data == "reject_skip", DriverRejectReason.waiting_for_reason)
async def reject_skip(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("↩️ Обрабатываю отказ...", reply_markup=None)
    await _do_reject(callback.message.chat.id, state, reason=None, photo_file_id=None)


@dp.message(DriverRejectReason.waiting_for_reason, F.photo)
async def reject_reason_photo(message: types.Message, state: FSMContext):
    reason = message.caption.strip() if message.caption else None
    photo_file_id = message.photo[-1].file_id
    await _do_reject(message.chat.id, state, reason=reason, photo_file_id=photo_file_id)


@dp.message(DriverRejectReason.waiting_for_reason, F.text)
async def reject_reason_text(message: types.Message, state: FSMContext):
    await _do_reject(message.chat.id, state, reason=message.text.strip(), photo_file_id=None)


@dp.callback_query(F.data.startswith("load:"))
async def load_order(callback: types.CallbackQuery):
    await callback.answer()
    if not await _get_active_driver(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён.")
        return
    try:
        row_num  = int(callback.data.split(":")[1])
        if not sheet:
            await callback.message.answer("❌ База данных недоступна.")
            return
        c_name   = callback.from_user.full_name
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "LOADING")
        b = InlineKeyboardBuilder()
        b.button(text="🚀 Мол бор шуд — роҳ афтодам / Товар погружен — выехать", callback_data=f"transit:{row_num}")
        await callback.message.edit_text(
            f"📦 **Фармоиш {order_id}: Боркунӣ**\n\nСтатус: **[Боркунӣ]**.\n\n"
            f"📦 **Заказ {order_id}: Погрузка**\n\nПосле укомплектовки нажмите кнопку выезда.",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"📦 **Курьер {c_name} ба боркунии фармоиши шумо {order_id} шурӯъ кард.**\n\n"
                f"📦 **Курьер {c_name} начал погрузку вашего заказа {order_id}.**")
    except Exception as e:
        logging.error(f"Ошибка load (строка {row_num}): {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при погрузке. Попробуйте позже.")


@dp.callback_query(F.data.startswith("transit:"))
async def transit_order(callback: types.CallbackQuery):
    await callback.answer()
    if not await _get_active_driver(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён.")
        return
    try:
        row_num  = int(callback.data.split(":")[1])
        if not sheet:
            await callback.message.answer("❌ База данных недоступна.")
            return
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "IN_TRANSIT")
        b = InlineKeyboardBuilder()
        b.button(text="📍 Ман дар ҷой ҳастам / Я на месте (прибыл)", callback_data=f"arrived:{row_num}")
        await callback.message.edit_text(
            f"🚚 **Фармоиш {order_id}: Дар роҳ**\n\nСтатус: **[Дар роҳ]**.\n\n"
            f"🚚 **Заказ {order_id}: В пути**\n\nКак будете у получателя — нажмите «На месте».",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"🚀 **Фармоиши шумо {order_id} дар роҳ аст!**\n\n"
                f"🚀 **Ваш заказ {order_id} уже в пути!**")
    except Exception as e:
        logging.error(f"Ошибка transit (строка {row_num}): {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при выезде. Попробуйте позже.")


@dp.callback_query(F.data.startswith("arrived:"))
async def arrived_order(callback: types.CallbackQuery):
    await callback.answer()
    if not await _get_active_driver(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён.")
        return
    try:
        row_num  = int(callback.data.split(":")[1])
        if not sheet:
            await callback.message.answer("❌ База данных недоступна.")
            return
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "ARRIVED")
        b = InlineKeyboardBuilder()
        b.button(text="🏁 Фармоиш расонида шуд / Заказ доставлен", callback_data=f"done:{row_num}")
        await callback.message.edit_text(
            f"📍 **Фармоиш {order_id}: Дар ҷой**\n\nМолро диҳед, пардохтро санҷед.\n\n"
            f"📍 **Заказ {order_id}: На месте**\n\nПередайте посылку, проверьте оплату.",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"🔔 **Курьер бо фармоиши шумо {order_id} расид!**\n\n"
                f"🔔 **Курьер прибыл с вашим заказом {order_id}!**")
    except Exception as e:
        logging.error(f"Ошибка arrived (строка {row_num}): {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при прибытии. Попробуйте позже.")


@dp.callback_query(F.data.startswith("done:"))
async def finish_order(callback: types.CallbackQuery):
    await callback.answer()
    if not await _get_active_driver(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён.")
        return
    try:
        row_num  = int(callback.data.split(":")[1])
        if not sheet:
            await callback.message.answer("❌ База данных недоступна.")
            return
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "DELIVERED")
        await callback.message.edit_text(
            f"🏁 **Фармоиш {order_id} баста шуд!**\n\nСтатус: **[Расонида шуд]**. Корхонаи хуб!\n\n"
            f"🏁 **Заказ {order_id} закрыт!**\n\nОтличная работа!",
            reply_markup=None, parse_mode="Markdown"
        )
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"✅ **Фармоиши шумо {order_id} бомуваффақият расонида шуд!**\n\n"
                f"✅ **Ваш заказ {order_id} успешно доставлен!**\nСпасибо, что выбрали Mavsimi Rason!")
    except Exception as e:
        logging.error(f"Ошибка done (строка {row_num}): {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при завершении. Попробуйте позже.")


# ─── Поддержка курьеров (Topics) ─────────────────────────────────────────────
@dp.message(F.text == "📞 Дастгирӣ / Поддержка")
async def driver_support_start(message: types.Message, state: FSMContext):
    if not await _get_active_driver(message.from_user.id):
        await message.answer("⛔ Дастрасӣ манъ аст. /start-ро пахш кунед.\n\n⛔ Доступ запрещён. Нажмите /start.")
        return
    if not SUPPORT_CHAT_ID:
        await message.answer("⚙️ Дастгирӣ муваққатан дастнорас аст.\n\n⚙️ Поддержка временно недоступна.")
        return
    await message.answer(
        "📞 <b>Саволи худро нависед:</b>\n"
        "Мо ҳарчи зудтар ҷавоб хоҳем дод.\n\n"
        "📞 <b>Напишите ваш вопрос или проблему:</b>\n"
        "Мы ответим в ближайшее время.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(DriverSupport.waiting_for_message)


@dp.message(DriverSupport.waiting_for_message)
async def driver_support_send(message: types.Message, state: FSMContext):
    driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))
    fio = driver_data[2] if driver_data and len(driver_data) > 2 else "Курьер"
    chat_id_str = str(message.from_user.id)
    try:
        topic_id_str = await asyncio.to_thread(_sync_get_driver_support_topic, chat_id_str)
        if topic_id_str:
            topic_id = int(topic_id_str)
        else:
            topic = await bot.create_forum_topic(
                chat_id=int(SUPPORT_CHAT_ID),
                name=f"🚗 Курьер: {fio}"
            )
            topic_id = topic.message_thread_id
            await asyncio.to_thread(_sync_save_driver_support_topic, chat_id_str, topic_id)

        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=f"🚗 <b>Курьер:</b> {message.text}",
            parse_mode="HTML"
        )
        back_kb = ReplyKeyboardBuilder()
        back_kb.button(text="🔙 Главное меню")
        await state.update_data(fio=fio, topic_id=topic_id)
        await state.set_state(DriverSupport.chatting)
        await message.answer(
            "✅ Фиристода шуд! Менеҷер ин ҷо ҷавоб хоҳад дод.\n\n"
            "✅ Отправлено! Менеджер ответит здесь.",
            reply_markup=back_kb.as_markup(resize_keyboard=True),
        )
    except Exception as e:
        await state.clear()
        logging.error(f"Ошибка поддержки курьера (SUPPORT_CHAT_ID={SUPPORT_CHAT_ID}): {type(e).__name__}: {e}")
        await message.answer("❌ Ошибка. Попробуйте позже.", reply_markup=await build_driver_main_menu(message.from_user.id))


@dp.message(DriverSupport.chatting)
async def driver_support_continue(message: types.Message, state: FSMContext):
    data = await state.get_data()
    topic_id = data.get("topic_id")
    if not topic_id:
        await state.clear()
        await message.answer("❌ Сессия истекла. Нажмите кнопку поддержки снова.", reply_markup=await build_driver_main_menu(message.from_user.id))
        return
    try:
        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=f"🚗 <b>Курьер:</b> {message.text}",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения курьера в топик: {type(e).__name__}: {e}")
        await message.answer("❌ Не удалось отправить. Попробуйте позже.")


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def driver_support_group_message(message: types.Message):
    if not SUPPORT_CHAT_ID or str(message.chat.id) != str(SUPPORT_CHAT_ID):
        return
    if not message.message_thread_id:
        return
    if not message.from_user or message.from_user.is_bot:
        return
    if not message.text:
        return
    driver_telegram_id = await asyncio.to_thread(
        _sync_get_driver_by_topic, str(message.message_thread_id)
    )
    if not driver_telegram_id:
        return
    try:
        await bot.send_message(
            chat_id=int(driver_telegram_id),
            text=f"💬 <b>Ответ от поддержки:</b>\n\n{message.text}",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки ответа курьеру {driver_telegram_id}: {type(e).__name__}: {e}")


# ─── Обратная связь ───────────────────────────────────────────────────────────
@dp.message(F.text == "💡 Бознигарӣ / Обратная связь")
async def driver_feedback_start(message: types.Message, state: FSMContext):
    if not SUPPORT_CHAT_ID:
        await message.answer("⚙️ Бознигарӣ муваққатан дастнорас аст.\n\n⚙️ Обратная связь временно недоступна.")
        return
    await message.answer(
        "💡 <b>Бознигарӣ</b>\n\n"
        "Хато ё пешниҳоди худро нависед.\n\n"
        "💡 <b>Обратная связь</b>\n\n"
        "Опишите баг или предложение по улучшению бота.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(DriverFeedback.waiting_for_message)


@dp.message(DriverFeedback.waiting_for_message)
async def driver_feedback_send(message: types.Message, state: FSMContext):
    await state.clear()
    driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))
    fio = driver_data[2] if driver_data and len(driver_data) > 2 else "Курьер"

    topic_id = await get_or_create_feedback_topic(bot)
    if not topic_id:
        await message.answer("❌ Не удалось отправить. Попробуйте позже.", reply_markup=await build_driver_main_menu(message.from_user.id))
        return

    text = (
        f"💡 <b>Обратная связь [Курьер]</b>\n"
        f"👤 {fio}\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"───────────────\n"
        f"{message.text}"
    )
    try:
        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=text,
            parse_mode="HTML"
        )
        await message.answer(
            "✅ Ташаккур! Бознигарии шумо қабул шуд.\n\n✅ Спасибо! Ваш отзыв получен.",
            reply_markup=await build_driver_main_menu(message.from_user.id),
        )
    except Exception as e:
        logging.error(f"Ошибка отправки обратной связи курьера: {type(e).__name__}: {e}")
        await message.answer("❌ Не удалось отправить. Попробуйте позже.", reply_markup=await build_driver_main_menu(message.from_user.id))


# ─── Автоуведомление курьеров о новых заказах ────────────────────────────────
_notified_order_ids: set[str] = set()


async def _broadcast_new_free_orders():
    while True:
        try:
            free_jobs = await asyncio.to_thread(_sync_get_free_orders)
            current_ids = {o["id"] for o in free_jobs}
            new_jobs = [o for o in free_jobs if o["id"] not in _notified_order_ids]

            if new_jobs:
                drivers = await asyncio.to_thread(_sync_get_all_active_drivers)
                if not drivers:
                    logging.info("Новые заказы есть, но активных курьеров нет — повторим на следующей проверке.")
                for o in (new_jobs if drivers else []):
                    dtype_readable = "ПВЗ 🏢" if o["delivery_type"] == "PVZ" else "То дар / До двери 🚪"
                    card = (
                        f"🆕 **Фармоиши нав / Новый заказ!**\n\n"
                        f"🆔 **Фармоиш / Заказ №:** `{o['id']}`\n"
                        f"• **Қабулкунанда / Получатель:** {o['r_phone']}\n"
                        f"• **Намуд / Тип:** {dtype_readable}\n"
                        f"• **Ориентир:** {o['driver_comment']}\n"
                        f"────────────────────\n"
                        f"📍 **Аз куҷо / Откуда:** {o['city_pickup']}, {o['address_pickup']}\n"
                        f"🌆 **Ба куҷо / Куда:** {o['city_delivery']}, {o['address_delivery']}\n"
                        f"💰 **Тариф:** {o['price']} TJS\n"
                        f"👤 **Фиристанда / Отправитель:** {o['s_name']}"
                    )
                    b = InlineKeyboardBuilder()
                    b.button(text="✅ Фармоишро гиред / Взять заказ", callback_data=f"take:{o['row_num']}")
                    keyboard = b.as_markup()

                    for d in drivers:
                        try:
                            await bot.send_message(
                                d["telegram_id"], card,
                                reply_markup=keyboard, parse_mode="Markdown"
                            )
                        except Exception as e:
                            logging.warning(f"Не удалось отправить пуш курьеру {d['telegram_id']}: {e}")

                    _notified_order_ids.add(o["id"])

            # заказ забрали/отменили — освобождаем id, чтобы он мог снова попасть в рассылку при повторном появлении
            _notified_order_ids.intersection_update(current_ids)
        except Exception:
            logging.error(f"Сбой автоуведомления о новых заказах: {traceback.format_exc()}")

        await asyncio.sleep(NEW_ORDERS_POLL_SECONDS)


@dp.startup()
async def _on_driver_bot_startup(**kwargs):
    asyncio.create_task(_broadcast_new_free_orders())