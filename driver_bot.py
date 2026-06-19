import asyncio
import base64
import json
import logging
import os
import traceback
from datetime import datetime, timezone, timedelta
from io import BytesIO

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from aiogram import types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import driver_bot as bot, client_bot, driver_dp as dp, sheet, drivers_sheet

# ─── Конфигурация ───────────────────────────────────────────────────────────
_raw_ids = os.getenv("ALLOWED_DRIVER_IDS", "")
ALLOWED_DRIVER_IDS: set[int] = {
    int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()
}
DRIVER_WEBAPP_URL = os.getenv("DRIVER_WEBAPP_URL", "")
DEFAULT_DRIVER_RATE = float(os.getenv("DEFAULT_DRIVER_RATE", "15.0"))
LINK_TO_DRIVER_OFFER = os.getenv("DRIVER_OFFER_URL", "https://www.google.com")

DUSHANBE_TZ = timezone(timedelta(hours=5))


class DriverRegistration(StatesGroup):
    waiting_for_fio = State()


# ─── Вспомогательные функции ────────────────────────────────────────────────
def is_authorized_driver(user_id: int) -> bool:
    if not ALLOWED_DRIVER_IDS:
        return True
    return user_id in ALLOWED_DRIVER_IDS


def _pad_row(row: list, size: int = 20) -> list:
    return row + [""] * max(0, size - len(row))


def _now_dushanbe() -> str:
    return datetime.now(DUSHANBE_TZ).strftime("%d.%m.%Y %H:%M")


# ─── Google Sheets: Водители ─────────────────────────────────────────────────
def _sync_get_driver(chat_id: str) -> list | None:
    """Ищет водителя по Telegram ID (столбец D = 4)."""
    if not drivers_sheet:
        return None
    try:
        cell = drivers_sheet.find(str(chat_id), in_column=4)
        return drivers_sheet.row_values(cell.row) if cell else None
    except Exception as e:
        logging.error(f"Ошибка поиска водителя {chat_id}: {e}")
        return None


def _sync_register_driver(chat_id: str, fio: str) -> bool:
    if not drivers_sheet:
        return False
    try:
        now = _now_dushanbe()
        drivers_sheet.append_row([
            "ACTIVE", now, fio, str(chat_id),
            str(DEFAULT_DRIVER_RATE), now, "", ""
        ])
        return True
    except Exception as e:
        logging.error(f"Ошибка регистрации водителя {chat_id}: {e}")
        return False


def _sync_get_driver_deliveries(chat_id: str, month_str: str) -> list[dict]:
    """
    Возвращает доставки курьера за указанный месяц (формат 'MM.YYYY').
    Ищет по столбцу T (индекс 19) = Telegram ID курьера.
    """
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
            date_cell = row[2].strip()  # "DD.MM.YYYY HH:MM"
            if len(date_cell) < 10 or date_cell[3:10] != month_str:
                continue
            try:
                dt = datetime.strptime(date_cell, "%d.%m.%Y %H:%M")
                date_iso = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M")
            except ValueError:
                date_iso = date_cell[:10]
                time_str = date_cell[11:16] if len(date_cell) > 10 else "—"
            result.append({
                "i": row[1],          # order_id
                "d": date_iso,        # "2026-06-19"
                "t": time_str,        # "14:30"
                "f": row[4],          # city_pickup
                "to": row[6],         # city_delivery
                "tp": row[9],         # PVZ / DOOR
                "s": row[0].upper(),  # status
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
                    "row_num":        idx + 1,
                    "id":             row[1],
                    "price":          row[3],
                    "city_pickup":    row[4],
                    "address_pickup": row[5],
                    "city_delivery":  row[6],
                    "address_delivery": row[7],
                    "driver_comment": row[8]  if len(row) > 8  else "Нет",
                    "delivery_type":  row[9]  if len(row) > 9  else "DOOR",
                    "s_name":         row[12] if len(row) > 12 else "—",
                    "r_phone":        row[15] if len(row) > 15 else "—",
                })
        return free_list
    except Exception as e:
        logging.error(f"Ошибка чтения свободных заказов: {e}")
        return []


def _sync_take_order(row_num: int, courier_name: str, courier_id: str) -> bool:
    if not sheet:
        return False
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


def _sync_update_status(row_num: int, status: str) -> bool:
    if not sheet:
        return False
    try:
        sheet.update_cell(row_num, 1, status)
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления статуса на {status} (строка {row_num}): {e}")
        return False


# ─── Excel-отчёт ─────────────────────────────────────────────────────────────
def generate_excel_report(driver_name: str, rate: float, deliveries: list[dict], month_label: str) -> BytesIO:
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
        c.font = Font(bold=bold, color=color, size=size,
                      name="Calibri")
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
    c2 = ws.cell(row=2, column=1, value=f"Курьер: {driver_name}   |   Период: {month_label}   |   Ставка: {rate:.2f} TJS / доставка")
    c2.font = Font(bold=False, color="FF555555", size=11, name="Calibri")
    c2.fill = PatternFill("solid", fgColor=GRAY)
    c2.alignment = Alignment(horizontal="center", vertical="center")

    delivered = [d for d in deliveries if d["s"] == "DELIVERED"]
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
        status_label = {"DELIVERED": "✓ Доставлен", "IN_TRANSIT": "В пути", "LOADING": "Погрузка",
                        "TAKEN": "Взят", "ARRIVED": "На месте"}.get(d["s"], d["s"])
        cell_style(ws, r, 1, i,           bg=bg, align="center")
        cell_style(ws, r, 2, d["d"],      bg=bg, align="center")
        cell_style(ws, r, 3, d["t"],      bg=bg, align="center")
        cell_style(ws, r, 4, d["f"],      bg=bg)
        cell_style(ws, r, 5, d["to"],     bg=bg)
        cell_style(ws, r, 6, "ПВЗ" if d["tp"] == "PVZ" else "До двери", bg=bg, align="center")
        cell_style(ws, r, 7, status_label, bg=bg, align="center",
                   color="FF2BCA80" if is_done else "FF555555", bold=is_done)
        earn_cell = cell_style(ws, r, 8, earn, bg=bg, align="center",
                               bold=is_done, color="FF2481CC" if is_done else "FF999999")
        earn_cell.number_format = '0.00 "TJS"'
        ws.row_dimensions[r].height = 18

    # Итоговая строка
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
def get_driver_main_menu():
    b = ReplyKeyboardBuilder()
    b.button(text="🔍 Свободные заказы")
    b.button(text="📊 Мой кабинет")
    b.button(text="📄 Отчёт за месяц")
    b.adjust(1)
    return b.as_markup(resize_keyboard=True)


async def send_client_push(chat_id: str, text: str):
    if client_bot and chat_id and str(chat_id).isdigit():
        try:
            await client_bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Не удалось отправить пуш клиенту {chat_id}: {e}")


# ─── Хэндлеры: регистрация ───────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start_driver(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_authorized_driver(message.from_user.id):
        await message.answer("⛔ Доступ запрещён. Обратитесь к администратору.")
        return

    driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))
    if driver_data and driver_data[0].upper() == "ACTIVE":
        fio = driver_data[2] if len(driver_data) > 2 else "Курьер"
        await message.answer(
            f"👋 **С возвращением, {fio}!**\n\nВыберите действие:",
            reply_markup=get_driver_main_menu(),
            parse_mode="Markdown"
        )
    else:
        b = ReplyKeyboardBuilder()
        b.button(text="📝 Принять оферту и зарегистрироваться")
        await message.answer(
            "💼 **Добро пожаловать в Mavsimi Rason!**\n\n"
            "Для работы курьером необходимо принять условия сотрудничества.\n\n"
            f"📋 [Оферта для курьеров]({LINK_TO_DRIVER_OFFER})\n\n"
            "Ознакомьтесь с документом и нажмите кнопку ниже:",
            reply_markup=b.as_markup(resize_keyboard=True),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )


@dp.message(F.text == "📝 Принять оферту и зарегистрироваться")
async def accept_offer(message: types.Message, state: FSMContext):
    await message.answer(
        "✅ Отлично! Вы принимаете условия оферты.\n\n"
        "Введите ваше **ФИО** (Фамилия Имя Отчество):",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(DriverRegistration.waiting_for_fio)


@dp.message(DriverRegistration.waiting_for_fio)
async def save_driver_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip() if message.text else ""
    await state.clear()
    if not fio or len(fio) < 3:
        await message.answer("❌ Пожалуйста, введите полное ФИО (минимум 3 символа).")
        await state.set_state(DriverRegistration.waiting_for_fio)
        return

    success = await asyncio.to_thread(_sync_register_driver, str(message.from_user.id), fio)
    if success:
        await message.answer(
            f"🎉 **Регистрация завершена!**\n\nДобро пожаловать, **{fio}**!\n"
            "Теперь вы можете брать заказы и отслеживать свои доставки.",
            reply_markup=get_driver_main_menu(),
            parse_mode="Markdown"
        )
    else:
        await message.answer("❌ Ошибка при регистрации. Попробуйте позже (/start).")


# ─── Хэндлеры: кабинет и отчёт ──────────────────────────────────────────────
@dp.message(F.text == "📊 Мой кабинет")
async def open_cabinet(message: types.Message):
    if not is_authorized_driver(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    if not DRIVER_WEBAPP_URL:
        await message.answer("⚙️ Кабинет временно недоступен. Обратитесь к администратору.")
        return

    driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))
    if not driver_data:
        await message.answer("❌ Вы не зарегистрированы. Нажмите /start.")
        return

    fio  = driver_data[2] if len(driver_data) > 2 else "Курьер"
    try:
        rate = float(driver_data[4]) if len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
    except (ValueError, TypeError):
        rate = DEFAULT_DRIVER_RATE

    now = datetime.now(DUSHANBE_TZ)
    month_str = now.strftime("%m.%Y")        # "06.2026"
    month_label = now.strftime("%B %Y")      # для заголовка

    deliveries = await asyncio.to_thread(
        _sync_get_driver_deliveries, str(message.from_user.id), month_str
    )

    payload = {
        "name": fio,
        "rate": rate,
        "month": month_str,
        "month_label": month_label,
        "deliveries": deliveries,
    }
    raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(raw_json.encode()).decode()
    webapp_url = f"{DRIVER_WEBAPP_URL}#d={b64}"

    b = InlineKeyboardBuilder()
    b.button(text="📊 Открыть кабинет", web_app=types.WebAppInfo(url=webapp_url))
    await message.answer(
        f"📊 **Кабинет курьера**\n👤 {fio}\n📅 {month_label}",
        reply_markup=b.as_markup(),
        parse_mode="Markdown"
    )


@dp.message(F.text == "📄 Отчёт за месяц")
async def send_monthly_report(message: types.Message):
    if not is_authorized_driver(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    driver_data = await asyncio.to_thread(_sync_get_driver, str(message.from_user.id))
    if not driver_data:
        await message.answer("❌ Вы не зарегистрированы. Нажмите /start.")
        return

    fio = driver_data[2] if len(driver_data) > 2 else "Курьер"
    try:
        rate = float(driver_data[4]) if len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
    except (ValueError, TypeError):
        rate = DEFAULT_DRIVER_RATE

    now = datetime.now(DUSHANBE_TZ)
    month_str   = now.strftime("%m.%Y")
    month_label = now.strftime("%B %Y")

    wait_msg = await message.answer("⏳ Формирую отчёт...")
    deliveries = await asyncio.to_thread(
        _sync_get_driver_deliveries, str(message.from_user.id), month_str
    )

    if not deliveries:
        await wait_msg.delete()
        await message.answer(f"📭 За {month_label} доставок не найдено.")
        return

    try:
        excel_buf = await asyncio.to_thread(generate_excel_report, fio, rate, deliveries, month_label)
        filename = f"report_{now.strftime('%Y_%m')}_{message.from_user.id}.xlsx"
        delivered_count = sum(1 for d in deliveries if d["s"] == "DELIVERED")
        await wait_msg.delete()
        await message.answer_document(
            types.BufferedInputFile(excel_buf.read(), filename=filename),
            caption=(
                f"📄 **Отчёт за {month_label}**\n"
                f"👤 {fio}\n"
                f"✅ Доставлено: {delivered_count}\n"
                f"💰 К выплате: {delivered_count * rate:.2f} TJS"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка генерации Excel для {message.from_user.id}: {e}", exc_info=True)
        await wait_msg.delete()
        await message.answer("❌ Ошибка при создании отчёта. Попробуйте позже.")


# ─── Хэндлеры: биржа заказов ─────────────────────────────────────────────────
@dp.message(F.text == "🔍 Свободные заказы")
async def show_jobs(message: types.Message):
    if not is_authorized_driver(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    free_jobs = await asyncio.to_thread(_sync_get_free_orders)
    if not free_jobs:
        await message.answer("🕳️ На бирже сейчас нет свободных заказов.")
        return

    await message.answer(f"📦 **Свободные заказы ({len(free_jobs)}):**", parse_mode="Markdown")
    for o in free_jobs:
        dtype_readable = "ПВЗ 🏢" if o["delivery_type"] == "PVZ" else "До двери 🚪"
        card = (
            f"🆔 **Заказ №:** `{o['id']}`\n"
            f"• **Получатель:** {o['r_phone']}\n"
            f"• **Тип:** {dtype_readable}\n"
            f"• **Ориентир:** {o['driver_comment']}\n"
            f"────────────────────\n"
            f"📍 **Откуда:** {o['city_pickup']}, {o['address_pickup']}\n"
            f"🌆 **Куда:** {o['city_delivery']}, {o['address_delivery']}\n"
            f"💰 **Тариф заказа:** {o['price']} TJS\n"
            f"👤 **Отправитель:** {o['s_name']}"
        )
        b = InlineKeyboardBuilder()
        b.button(text="✅ Взять заказ", callback_data=f"take:{o['row_num']}")
        await message.answer(card, reply_markup=b.as_markup(), parse_mode="Markdown")


# ─── Хэндлеры: управление заказом ────────────────────────────────────────────
@dp.callback_query(F.data.startswith("take:"))
async def accept_order(callback: types.CallbackQuery):
    if not is_authorized_driver(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if row_num < 2:
            await callback.answer("❌ Некорректный номер заказа.", show_alert=True)
            return
        if not sheet:
            await callback.answer("❌ База данных недоступна.", show_alert=True)
            return

        c_name = callback.from_user.full_name
        c_id   = callback.from_user.id
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]

        success = await asyncio.to_thread(_sync_take_order, row_num, c_name, c_id)
        if not success:
            await callback.answer("❌ Этот заказ уже забрал другой водитель!", show_alert=True)
            return

        b = InlineKeyboardBuilder()
        b.button(text="📦 Приступить к погрузке", callback_data=f"load:{row_num}")
        await callback.message.edit_text(
            f"🎉 **Вы взяли заказ {order_id}!**\n\nСтатус: **[Взят курьером]**.\n"
            "Отправляйтесь на точку забора и нажмите кнопку, когда начнёте погрузку.",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"🚚 **Ваш заказ {order_id} принят курьером!**\n👤 **Курьер:** {c_name}\n\n*Ожидайте погрузки.*")
    except Exception:
        logging.error(f"Сбой take: {traceback.format_exc()}")
        await callback.answer("❌ Ошибка на сервере. Попробуйте позже.", show_alert=True)


@dp.callback_query(F.data.startswith("load:"))
async def load_order(callback: types.CallbackQuery):
    if not is_authorized_driver(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if not sheet:
            await callback.answer("❌ База данных недоступна.", show_alert=True)
            return
        c_name   = callback.from_user.full_name
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "LOADING")
        b = InlineKeyboardBuilder()
        b.button(text="🚀 Товар погружен — выехать", callback_data=f"transit:{row_num}")
        await callback.message.edit_text(
            f"📦 **Заказ {order_id}: Погрузка**\n\nСтатус: **[Погрузка]**.\nПосле укомплектовки нажмите кнопку выезда.",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"📦 **Курьер {c_name} начал погрузку вашего заказа {order_id}.**")
    except Exception as e:
        logging.error(f"Ошибка load (строка {row_num}): {e}", exc_info=True)
        await callback.answer("❌ Ошибка при погрузке. Попробуйте позже.", show_alert=True)


@dp.callback_query(F.data.startswith("transit:"))
async def transit_order(callback: types.CallbackQuery):
    if not is_authorized_driver(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if not sheet:
            await callback.answer("❌ База данных недоступна.", show_alert=True)
            return
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "IN_TRANSIT")
        b = InlineKeyboardBuilder()
        b.button(text="📍 Я на месте (прибыл)", callback_data=f"arrived:{row_num}")
        await callback.message.edit_text(
            f"🚚 **Заказ {order_id}: В пути**\n\nСтатус: **[В пути]**.\nКак будете у получателя — нажмите «На месте».",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"🚀 **Ваш заказ {order_id} уже в пути!**")
    except Exception as e:
        logging.error(f"Ошибка transit (строка {row_num}): {e}", exc_info=True)
        await callback.answer("❌ Ошибка при выезде. Попробуйте позже.", show_alert=True)


@dp.callback_query(F.data.startswith("arrived:"))
async def arrived_order(callback: types.CallbackQuery):
    if not is_authorized_driver(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if not sheet:
            await callback.answer("❌ База данных недоступна.", show_alert=True)
            return
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "ARRIVED")
        b = InlineKeyboardBuilder()
        b.button(text="🏁 Заказ доставлен", callback_data=f"done:{row_num}")
        await callback.message.edit_text(
            f"📍 **Заказ {order_id}: На месте**\n\nСтатус: **[На месте]**.\nПередайте посылку, проверьте оплату.",
            reply_markup=b.as_markup(), parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"🔔 **Курьер прибыл с вашим заказом {order_id}!**")
    except Exception as e:
        logging.error(f"Ошибка arrived (строка {row_num}): {e}", exc_info=True)
        await callback.answer("❌ Ошибка при прибытии. Попробуйте позже.", show_alert=True)


@dp.callback_query(F.data.startswith("done:"))
async def finish_order(callback: types.CallbackQuery):
    if not is_authorized_driver(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    try:
        row_num = int(callback.data.split(":")[1])
        if not sheet:
            await callback.answer("❌ База данных недоступна.", show_alert=True)
            return
        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))
        order_id       = row_vals[1]
        client_chat_id = row_vals[18]
        await asyncio.to_thread(_sync_update_status, row_num, "DELIVERED")
        await callback.message.edit_text(
            f"🏁 **Заказ {order_id} закрыт!**\n\nСтатус: **[Доставлен]**. Отличная работа! 💪",
            reply_markup=None, parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id,
                f"✅ **Ваш заказ {order_id} успешно доставлен!**\nСпасибо, что выбрали Mavsimi Rason!")
    except Exception as e:
        logging.error(f"Ошибка done (строка {row_num}): {e}", exc_info=True)
        await callback.answer("❌ Ошибка при завершении. Попробуйте позже.", show_alert=True)
