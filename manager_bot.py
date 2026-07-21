import asyncio
import logging
import os
from datetime import datetime, timedelta
from io import BytesIO

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from aiogram import types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    manager_bot as bot,
    manager_dp as dp,
    driver_bot as driver_bot_instance,
    client_bot,
    sheet,
    drivers_sheet,
)
from driver_bot import (
    _pad_row,
    _sync_get_all_active_drivers,
    _async_get_admin_dashboard_data,
    _sync_get_driver,
    _sync_get_driver_deliveries,
    _week_label,
    generate_excel_report,
    send_client_push,
)

MANAGER_CHAT_ID     = os.getenv("MANAGER_CHAT_ID", "")
ADMIN_PANEL_URL     = os.getenv("ADMIN_PANEL_URL", "")
DEFAULT_DRIVER_RATE = float(os.getenv("DEFAULT_DRIVER_RATE", "15.0"))

CANCEL_REASONS = [
    "Не смогли согласовать детали",
    "Адрес недоступен",
    "Нет курьеров в этой зоне",
    "Дублирующий заказ",
]


class ManagerCancelOrder(StatesGroup):
    waiting_for_reason = State()


def _is_manager(chat_id) -> bool:
    return bool(MANAGER_CHAT_ID) and str(chat_id) == str(MANAGER_CHAT_ID)


# ─── Google Sheets: курьеры ──────────────────────────────────────────────────

def _sync_approve_driver(telegram_id: str) -> str | None:
    if not drivers_sheet:
        return None
    try:
        cell = drivers_sheet.find(str(telegram_id), in_column=4)
        if not cell:
            return None
        row = drivers_sheet.row_values(cell.row)
        drivers_sheet.update_cell(cell.row, 1, "ACTIVE")
        return row[2] if len(row) > 2 else "Курьер"
    except Exception as e:
        logging.error(f"Ошибка одобрения курьера {telegram_id}: {e}")
        return None


def _sync_reject_driver(telegram_id: str) -> str | None:
    if not drivers_sheet:
        return None
    try:
        cell = drivers_sheet.find(str(telegram_id), in_column=4)
        if not cell:
            return None
        row = drivers_sheet.row_values(cell.row)
        drivers_sheet.update_cell(cell.row, 1, "REJECTED")
        return row[2] if len(row) > 2 else "Курьер"
    except Exception as e:
        logging.error(f"Ошибка отклонения курьера {telegram_id}: {e}")
        return None


# ─── Google Sheets: заказы ───────────────────────────────────────────────────

def _sync_set_order_ready(order_id: str) -> tuple[bool, str, str]:
    """NEW → READY_FOR_DRIVERS. Returns (success, client_chat_id, error_detail)."""
    if not sheet:
        return False, "", "база недоступна"
    try:
        cell = sheet.find(str(order_id), in_column=2)
        if not cell:
            return False, "", "не найден"
        row = _pad_row(sheet.row_values(cell.row))
        status = row[0].upper().strip()
        if status != "NEW":
            return False, "", f"статус: {status}"
        sheet.update_cell(cell.row, 1, "READY_FOR_DRIVERS")
        return True, row[18], ""
    except Exception as e:
        logging.error(f"Ошибка перевода заказа {order_id} в READY: {e}")
        return False, "", str(e)


def _sync_change_order_status(order_id: str, new_status: str) -> tuple[bool, str, str, str]:
    """Меняет статус заказа. Returns (success, client_chat_id, courier_id, error)."""
    if not sheet:
        return False, "", "", "база недоступна"
    try:
        cell = sheet.find(str(order_id), in_column=2)
        if not cell:
            return False, "", "", "не найден"
        row = _pad_row(sheet.row_values(cell.row))
        sheet.update_cell(cell.row, 1, new_status)
        return True, row[18], row[19], ""
    except Exception as e:
        logging.error(f"Ошибка смены статуса заказа {order_id}: {e}")
        return False, "", "", str(e)


def _sync_cancel_order(order_id: str) -> tuple[bool, str, str]:
    """NEW → CANCELLED. Returns (success, client_chat_id, error_detail)."""
    if not sheet:
        return False, "", "база недоступна"
    try:
        cell = sheet.find(str(order_id), in_column=2)
        if not cell:
            return False, "", "не найден"
        row = _pad_row(sheet.row_values(cell.row))
        status = row[0].upper().strip()
        if status != "NEW":
            return False, "", f"статус: {status}"
        sheet.update_cell(cell.row, 1, "CANCELLED")
        return True, row[18], ""
    except Exception as e:
        logging.error(f"Ошибка отмены заказа {order_id}: {e}")
        return False, "", str(e)


# ─── Google Sheets: сводные данные по всем курьерам ─────────────────────────

def _sync_get_all_couriers_deliveries(date_from: datetime, date_to: datetime) -> dict:
    """Returns {telegram_id: {total: N, delivered: M}} for the given date range."""
    if not sheet:
        return {}
    try:
        result = {}
        for idx, row in enumerate(sheet.get_all_values()):
            if idx == 0:
                continue
            row = _pad_row(row)
            courier_id = str(row[19]).strip()
            if not courier_id:
                continue
            date_cell = row[2].strip()
            try:
                dt = datetime.strptime(date_cell[:16], "%d.%m.%Y %H:%M")
            except ValueError:
                continue
            if not (date_from <= dt <= date_to):
                continue
            if courier_id not in result:
                result[courier_id] = {"total": 0, "delivered": 0}
            result[courier_id]["total"] += 1
            if row[0].upper().strip() == "DELIVERED":
                result[courier_id]["delivered"] += 1
        return result
    except Exception as e:
        logging.error(f"Ошибка чтения доставок всех курьеров: {e}")
        return {}


def _sync_get_all_drivers_rates() -> dict:
    """Returns {telegram_id: rate} for all ACTIVE drivers."""
    if not drivers_sheet:
        return {}
    try:
        result = {}
        for idx, row in enumerate(drivers_sheet.get_all_values()):
            if idx == 0 or len(row) < 5:
                continue
            if row[0].upper().strip() != "ACTIVE":
                continue
            tid = str(row[3]).strip()
            try:
                rate = float(row[4]) if row[4] else DEFAULT_DRIVER_RATE
            except (ValueError, TypeError):
                rate = DEFAULT_DRIVER_RATE
            result[tid] = rate
        return result
    except Exception as e:
        logging.error(f"Ошибка чтения ставок курьеров: {e}")
        return {}


# ─── Excel: сводный отчёт на всех курьеров ───────────────────────────────────

def generate_summary_excel(couriers_stats: list[dict], period_label: str) -> BytesIO:
    """
    couriers_stats: [{fio, rate, total, delivered}]
    Generates a styled summary Excel — one row per courier + totals.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Сводка"

    BLUE   = "FF2481CC"
    GREEN  = "FF22A368"
    WHITE  = "FFFFFFFF"
    GRAY   = "FFF4F4F5"
    L_BLUE = "FFD6EAF8"

    thin = Side(border_style="thin", color="FFD0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def sc(row, col, val, bold=False, bg=None, color="FF1C1C1E", align="center", size=11):
        c = ws.cell(row=row, column=col, value=val)
        c.font = Font(bold=bold, color=color, size=size, name="Calibri")
        if bg:
            c.fill = PatternFill("solid", fgColor=bg)
        c.alignment = Alignment(horizontal=align, vertical="center")
        c.border = border
        return c

    ws.merge_cells("A1:F1")
    t = ws.cell(row=1, column=1, value="MAVSIMI RASON — Сводный отчёт")
    t.font = Font(bold=True, color=WHITE, size=14, name="Calibri")
    t.fill = PatternFill("solid", fgColor=BLUE)
    t.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("A2:F2")
    t2 = ws.cell(row=2, column=1, value=f"Период: {period_label}")
    t2.font = Font(color="FF555555", size=11, name="Calibri")
    t2.fill = PatternFill("solid", fgColor=GRAY)
    t2.alignment = Alignment(horizontal="center", vertical="center")

    headers = ["№", "ФИО курьера", "Ставка (TJS)", "Заказов взято", "Доставлено", "К выплате (TJS)"]
    for col, h in enumerate(headers, 1):
        sc(3, col, h, bold=True, bg=BLUE, color=WHITE)

    col_widths = [4, 26, 13, 14, 12, 16]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 20
    ws.row_dimensions[3].height = 20

    total_delivered = 0
    total_earnings  = 0.0

    for i, cs in enumerate(couriers_stats, 1):
        r  = i + 3
        bg = L_BLUE if i % 2 == 0 else WHITE
        delivered = cs["delivered"]
        rate      = cs["rate"]
        earnings  = delivered * rate
        total_delivered += delivered
        total_earnings  += earnings
        sc(r, 1, i,             bg=bg)
        sc(r, 2, cs["fio"],     bg=bg, align="left")
        sc(r, 3, rate,          bg=bg)
        sc(r, 4, cs["total"],   bg=bg)
        sc(r, 5, delivered,     bg=bg, bold=True)
        ec = sc(r, 6, earnings, bg=bg, bold=True, color="FF2481CC")
        ec.number_format = '0.00 "TJS"'
        ws.row_dimensions[r].height = 18

    last = len(couriers_stats) + 4
    ws.merge_cells(f"A{last}:E{last}")
    lbl = ws.cell(row=last, column=1, value="ИТОГО К ВЫПЛАТЕ:")
    lbl.font = Font(bold=True, size=12, name="Calibri")
    lbl.fill = PatternFill("solid", fgColor=GRAY)
    lbl.alignment = Alignment(horizontal="right", vertical="center")
    lbl.border = border
    tot = ws.cell(row=last, column=6, value=total_earnings)
    tot.font = Font(bold=True, color=GREEN, size=13, name="Calibri")
    tot.fill = PatternFill("solid", fgColor=GRAY)
    tot.alignment = Alignment(horizontal="center", vertical="center")
    tot.number_format = '0.00 "TJS"'
    tot.border = border
    ws.row_dimensions[last].height = 22

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ─── Панель управления ───────────────────────────────────────────────────────

def _build_panel_message(data: dict) -> tuple[str, str | None]:
    # admin_panel.html больше не получает данные через base64 в URL —
    # страница сама тянет их из GET /api/v1/manager/dashboard и обновляется по таймеру.
    active_cnt = len(data["orders"])
    free_cnt   = len(data["free"])
    busy_cnt   = sum(1 for c in data["couriers"] if c["busy"])
    total_cnt  = len(data["couriers"])
    text = (
        f"🎛 <b>Панель управления</b>\n\n"
        f"📦 Активных заказов: <b>{active_cnt}</b>\n"
        f"🕳️ Свободных заказов: <b>{free_cnt}</b>\n"
        f"🚗 Курьеров в работе: <b>{busy_cnt}/{total_cnt}</b>"
    )
    webapp_url = ADMIN_PANEL_URL or None
    return text, webapp_url


@dp.message(CommandStart())
async def cmd_start_manager(message: types.Message):
    if not _is_manager(message.chat.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    await _send_panel(message.chat.id, bot)


async def _send_panel(chat_id: int, bot_instance):
    data = await _async_get_admin_dashboard_data()
    text, webapp_url = _build_panel_message(data)
    reply_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔄 Обновить")]],
        resize_keyboard=True
    )
    await bot_instance.send_message(chat_id, text, reply_markup=reply_kb, parse_mode="HTML")
    if webapp_url:
        # Только inline-кнопка передаёт Telegram подписанный initData для web_api.py
        b = InlineKeyboardBuilder()
        b.button(text="🎛 Открыть панель", web_app=types.WebAppInfo(url=webapp_url))
        await bot_instance.send_message(chat_id, "👇 Панель управления:", reply_markup=b.as_markup())



@dp.message(F.text == "🔄 Обновить")
async def panel_refresh_text(message: types.Message):
    if not _is_manager(message.chat.id):
        return
    await _send_panel(message.chat.id, bot)




# ─── Принятие / отмена нового заказа (push от клиентского бота) ─────────────

@dp.callback_query(F.data.startswith("oa:"))
async def order_accept(callback: types.CallbackQuery):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    order_id = callback.data.split(":", 1)[1]
    success, client_chat_id, err = await asyncio.to_thread(_sync_set_order_ready, order_id)
    if not success:
        await callback.message.edit_text(
            callback.message.text + f"\n\n❌ Не удалось принять — {err}.",
            reply_markup=None
        )
        return
    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>Принят и передан на биржу!</b>",
        reply_markup=None,
        parse_mode="HTML"
    )
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"✅ Ваш заказ *{order_id}* принят!\nОжидайте назначения курьера."
        )


@dp.callback_query(F.data.startswith("oc:"))
async def order_cancel_menu(callback: types.CallbackQuery):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    order_id = callback.data.split(":", 1)[1]
    b = InlineKeyboardBuilder()
    for i, reason in enumerate(CANCEL_REASONS):
        b.button(text=reason, callback_data=f"ocr:{order_id}:{i}")
    b.button(text="✏️ Своя причина", callback_data=f"ocx:{order_id}")
    b.adjust(1)
    await callback.message.edit_reply_markup(reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("ocr:"))
async def order_cancel_reason(callback: types.CallbackQuery):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    _, order_id, idx_str = callback.data.split(":", 2)
    reason = CANCEL_REASONS[int(idx_str)]
    success, client_chat_id, err = await asyncio.to_thread(_sync_cancel_order, order_id)
    if not success:
        await callback.message.edit_text(
            callback.message.html_text + f"\n\n❌ Не удалось отменить — {err}.",
            reply_markup=None, parse_mode="HTML"
        )
        return
    await callback.message.edit_text(
        callback.message.html_text + f"\n\n❌ <b>Отменён.</b> Причина: {reason}",
        reply_markup=None, parse_mode="HTML"
    )
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"❌ К сожалению, ваш заказ *{order_id}* был отменён.\n📝 Причина: {reason}\n\nПожалуйста, свяжитесь с поддержкой если возникли вопросы."
        )


@dp.callback_query(F.data.startswith("ocx:"))
async def order_cancel_custom_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    order_id = callback.data.split(":", 1)[1]
    await state.set_state(ManagerCancelOrder.waiting_for_reason)
    await state.update_data(order_id=order_id, msg_id=callback.message.message_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"✏️ Введите причину отмены заказа <code>{order_id}</code>:", parse_mode="HTML")


@dp.message(ManagerCancelOrder.waiting_for_reason)
async def order_cancel_custom_reason(message: types.Message, state: FSMContext):
    if not _is_manager(message.chat.id):
        return
    data = await state.get_data()
    order_id = data.get("order_id", "")
    reason = message.text.strip()
    await state.clear()

    success, client_chat_id, err = await asyncio.to_thread(_sync_cancel_order, order_id)
    if not success:
        await message.answer(f"❌ Не удалось отменить заказ {order_id} — {err}.")
        return
    await message.answer(f"❌ Заказ <code>{order_id}</code> отменён.\nПричина: {reason}", parse_mode="HTML")
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"❌ К сожалению, ваш заказ *{order_id}* был отменён.\n📝 Причина: {reason}\n\nПожалуйста, свяжитесь с поддержкой если возникли вопросы."
        )


# ─── Одобрение / отклонение курьеров ────────────────────────────────────────

@dp.callback_query(F.data.startswith("approve_driver:"))
async def approve_driver(callback: types.CallbackQuery):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    telegram_id = callback.data.split(":", 1)[1]
    fio = await asyncio.to_thread(_sync_approve_driver, telegram_id)
    if not fio:
        await callback.message.edit_text("❌ Курьер не найден или уже обработан.", reply_markup=None)
        return
    await callback.message.edit_text(
        f"✅ Курьер <b>{fio}</b> одобрен и активирован.",
        reply_markup=None, parse_mode="HTML"
    )
    try:
        await driver_bot_instance.send_message(
            chat_id=int(telegram_id),
            text=(
                f"🎉 <b>Табрик, {fio}!</b>\n\n"
                "Аккаунти курьери шумо фаъол шуд.\n"
                "/start-ро пахш кунед то кор оғоз кунед.\n\n"
                f"🎉 <b>Поздравляем, {fio}!</b>\n\n"
                "Ваш аккаунт курьера активирован.\n"
                "Нажмите /start чтобы начать работу."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить курьера {telegram_id} об активации: {e}")


@dp.callback_query(F.data.startswith("reject_driver:"))
async def reject_driver_cb(callback: types.CallbackQuery):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    telegram_id = callback.data.split(":", 1)[1]
    fio = await asyncio.to_thread(_sync_reject_driver, telegram_id)
    if not fio:
        await callback.message.edit_text("❌ Курьер не найден или уже обработан.", reply_markup=None)
        return
    await callback.message.edit_text(
        f"❌ Курьер <b>{fio}</b> отклонён.",
        reply_markup=None, parse_mode="HTML"
    )
    try:
        await driver_bot_instance.send_message(
            chat_id=int(telegram_id),
            text=(
                "❌ <b>Дархости шумо рад шуд.</b>\n\n"
                "Мутаассифона, мо дар айни ҳол шуморо қабул карда наметавонем.\n"
                "Барои саволҳо ба маъмурият муроҷиат кунед.\n\n"
                "❌ <b>Ваша заявка отклонена.</b>\n\n"
                "К сожалению, мы не можем принять вас на данный момент.\n"
                "По вопросам обратитесь к администрации."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить курьера {telegram_id} об отклонении: {e}")


