import asyncio
import base64
import json
import logging
import os
import traceback
from datetime import datetime, timedelta
from io import BytesIO

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from aiogram import types, F
from aiogram.filters import Command, CommandStart
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
    _sync_find_order_by_id,
    _sync_reassign_order,
    _sync_get_all_active_drivers,
    _async_get_admin_dashboard_data,
    _sync_get_driver,
    _sync_get_driver_deliveries,
    _week_label,
    generate_excel_report,
    send_client_push,
)

MANAGER_CHAT_ID    = os.getenv("MANAGER_CHAT_ID", "")
ADMIN_PANEL_URL    = os.getenv("ADMIN_PANEL_URL", "")
DEFAULT_DRIVER_RATE = float(os.getenv("DEFAULT_DRIVER_RATE", "15.0"))


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

def _build_panel_message(data: dict) -> tuple[str, types.InlineKeyboardMarkup]:
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
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    b = InlineKeyboardBuilder()
    if ADMIN_PANEL_URL:
        b.button(text="🎛 Открыть панель", web_app=types.WebAppInfo(url=f"{ADMIN_PANEL_URL}?d={b64}"))
    b.button(text="🔄 Обновить", callback_data="admin_refresh")
    b.adjust(1)
    return text, b.as_markup()


@dp.message(CommandStart())
async def cmd_start_manager(message: types.Message):
    if not _is_manager(message.chat.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer(
        "🎛 <b>Панель менеджера Mavsimi Rason</b>\n\n"
        "Доступные команды:\n"
        "/panel — открыть панель управления\n"
        "/reassign &lt;id&gt; — переназначить курьера\n"
        "/ready &lt;id&gt; — перевести заказ в доставку",
        parse_mode="HTML"
    )


@dp.message(Command("panel"))
async def cmd_admin_panel(message: types.Message):
    if not _is_manager(message.chat.id):
        return
    wait = await message.answer("⏳ Загружаю данные...")
    data = await _async_get_admin_dashboard_data()
    text, kb = _build_panel_message(data)
    await wait.delete()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh(callback: types.CallbackQuery):
    if not _is_manager(callback.message.chat.id):
        await callback.answer()
        return
    data = await _async_get_admin_dashboard_data()
    text, kb = _build_panel_message(data)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer("Обновлено ✅")
    except Exception as e:
        if "message is not modified" in str(e):
            await callback.answer("Данные актуальны")
        else:
            await callback.answer("Ошибка обновления")
            logging.error(f"admin_refresh edit error: {e}")


# ─── Переназначение заказов ──────────────────────────────────────────────────

@dp.message(Command("reassign"))
async def cmd_reassign(message: types.Message):
    if not _is_manager(message.chat.id):
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /reassign &lt;order_id&gt;", parse_mode="HTML")
        return
    order_id = parts[1].strip()
    result = await asyncio.to_thread(_sync_find_order_by_id, order_id)
    if not result:
        await message.answer(f"❌ Заказ <b>{order_id}</b> не найден.", parse_mode="HTML")
        return

    row_num, row_vals = result
    status = row_vals[0].upper().strip()
    if status not in ("TAKEN", "LOADING", "IN_TRANSIT", "ARRIVED"):
        await message.answer(f"❌ Заказ {order_id} нельзя переназначить (статус: {status}).")
        return

    active_drivers = await asyncio.to_thread(_sync_get_all_active_drivers)
    if not active_drivers:
        await message.answer("❌ Нет активных курьеров.")
        return

    status_ru = {"TAKEN": "Взят", "LOADING": "Погрузка", "IN_TRANSIT": "В пути", "ARRIVED": "На месте"}
    b = InlineKeyboardBuilder()
    for d in active_drivers:
        b.button(text=f"👤 {d['fio']}", callback_data=f"rt:{row_num}:{d['row_num']}")
    b.adjust(1)
    await message.answer(
        f"📦 <b>Заказ {order_id}</b>\n"
        f"Статус: {status_ru.get(status, status)}\n"
        f"Текущий курьер: {row_vals[17] or '—'}\n\n"
        f"Выберите нового курьера:",
        reply_markup=b.as_markup(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("rt:"))
async def do_reassign(callback: types.CallbackQuery):
    await callback.answer()
    if not _is_manager(callback.message.chat.id):
        return
    try:
        _, order_row_str, driver_row_str = callback.data.split(":")
        order_row  = int(order_row_str)
        driver_row = int(driver_row_str)

        driver_row_vals = _pad_row(await asyncio.to_thread(drivers_sheet.row_values, driver_row))
        new_fio = driver_row_vals[2]
        new_tid = driver_row_vals[3]

        success, old_courier_id, order_id = await asyncio.to_thread(
            _sync_reassign_order, order_row, new_fio, new_tid
        )
        if not success:
            await callback.message.edit_text(
                "❌ Не удалось переназначить. Проверьте статус заказа.", reply_markup=None
            )
            return

        order_row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, order_row))
        client_chat_id = order_row_vals[18]
        city_from      = order_row_vals[4]
        city_to        = order_row_vals[6]

        await callback.message.edit_text(
            f"✅ Заказ <b>{order_id}</b> переназначен → <b>{new_fio}</b>",
            reply_markup=None, parse_mode="HTML"
        )

        if old_courier_id and old_courier_id != new_tid:
            try:
                await driver_bot_instance.send_message(
                    chat_id=int(old_courier_id),
                    text=f"⚠️ <b>Ваш заказ {order_id} переназначен другому курьеру.</b>",
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить старого курьера: {e}")

        b = InlineKeyboardBuilder()
        b.button(text="📦 Приступить к погрузке", callback_data=f"load:{order_row}")
        try:
            await driver_bot_instance.send_message(
                chat_id=int(new_tid),
                text=(
                    f"📦 <b>Вам назначен заказ {order_id}!</b>\n"
                    f"📍 {city_from} → {city_to}\n\n"
                    f"Нажмите кнопку, когда начнёте погрузку:"
                ),
                reply_markup=b.as_markup(),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить нового курьера {new_tid}: {e}")

        if client_chat_id:
            await send_client_push(
                client_chat_id,
                f"🔄 Ваш заказ *{order_id}* передан новому курьеру: *{new_fio}*."
            )
    except Exception:
        logging.error(f"Сбой reassign: {traceback.format_exc()}")
        await callback.message.answer("❌ Ошибка при переназначении.")


# ─── WebApp от панели менеджера ──────────────────────────────────────────────

@dp.message(F.web_app_data)
async def handle_webapp(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return

    action = data.get("action")

    # ── Переназначение из WebApp ─────────────────────────────────────────────
    if action == "reassign_request":
        order_row = data.get("order_row")
        order_id  = data.get("order_id")
        if not order_row or not order_id:
            return
        active_drivers = await asyncio.to_thread(_sync_get_all_active_drivers)
        if not active_drivers:
            await message.answer("❌ Нет активных курьеров.")
            return
        b = InlineKeyboardBuilder()
        for d in active_drivers:
            b.button(text=f"👤 {d['fio']}", callback_data=f"rt:{order_row}:{d['row_num']}")
        b.adjust(1)
        await message.answer(
            f"📦 Переназначение заказа <b>{order_id}</b>\n\nВыберите нового курьера:",
            reply_markup=b.as_markup(),
            parse_mode="HTML"
        )
        return

    # ── Отчёт на одного курьера ──────────────────────────────────────────────
    if action == "manager_report":
        courier_tid    = str(data.get("courier_tid", ""))
        courier_name   = data.get("courier_name", "Курьер")
        week_start_str = data.get("week_start", "")
        if not courier_tid or not week_start_str:
            return
        try:
            week_start = datetime.strptime(week_start_str, "%Y-%m-%d")
        except ValueError:
            await message.answer("❌ Некорректный формат даты.")
            return
        week_end     = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        period_label = _week_label(week_start, week_end)

        driver_data = await asyncio.to_thread(_sync_get_driver, courier_tid)
        try:
            rate = float(driver_data[4]) if driver_data and len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
        except (ValueError, TypeError):
            rate = DEFAULT_DRIVER_RATE

        wait = await message.answer(f"⏳ Формирую отчёт для {courier_name}...")
        deliveries = await asyncio.to_thread(_sync_get_driver_deliveries, courier_tid, week_start, week_end)

        if not deliveries:
            await wait.delete()
            await message.answer(f"📭 За {period_label} у курьера <b>{courier_name}</b> доставок не найдено.", parse_mode="HTML")
            return

        excel_buf = await asyncio.to_thread(generate_excel_report, courier_name, rate, deliveries, period_label)
        delivered_count = sum(1 for d in deliveries if d["s"] == "DELIVERED")
        await wait.delete()
        await message.answer_document(
            types.BufferedInputFile(excel_buf.read(), filename=f"report_{courier_tid}_{week_start_str}.xlsx"),
            caption=(
                f"📄 <b>Отчёт: {period_label}</b>\n"
                f"👤 {courier_name}\n"
                f"✅ Доставлено: {delivered_count}\n"
                f"💰 К выплате: {delivered_count * rate:.2f} TJS"
            ),
            parse_mode="HTML"
        )
        return

    # ── Сводный отчёт на всех курьеров ───────────────────────────────────────
    if action == "manager_report_all":
        week_start_str = data.get("week_start", "")
        if not week_start_str:
            return
        try:
            week_start = datetime.strptime(week_start_str, "%Y-%m-%d")
        except ValueError:
            await message.answer("❌ Некорректный формат даты.")
            return
        week_end     = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        period_label = _week_label(week_start, week_end)

        active_couriers = await asyncio.to_thread(_sync_get_all_active_drivers)
        if not active_couriers:
            await message.answer("❌ Нет активных курьеров.")
            return

        wait = await message.answer(f"⏳ Формирую сводный отчёт за {period_label}...")

        deliveries_map, rates_map = await asyncio.gather(
            asyncio.to_thread(_sync_get_all_couriers_deliveries, week_start, week_end),
            asyncio.to_thread(_sync_get_all_drivers_rates),
        )

        couriers_stats = []
        for c in active_couriers:
            rate  = rates_map.get(c["telegram_id"], DEFAULT_DRIVER_RATE)
            stats = deliveries_map.get(c["telegram_id"], {"total": 0, "delivered": 0})
            couriers_stats.append({
                "fio":       c["fio"],
                "rate":      rate,
                "total":     stats["total"],
                "delivered": stats["delivered"],
            })

        excel_buf   = await asyncio.to_thread(generate_summary_excel, couriers_stats, period_label)
        total_earn  = sum(c["delivered"] * c["rate"] for c in couriers_stats)
        total_deliv = sum(c["delivered"] for c in couriers_stats)
        await wait.delete()
        await message.answer_document(
            types.BufferedInputFile(excel_buf.read(), filename=f"summary_{week_start_str}.xlsx"),
            caption=(
                f"📊 <b>Сводный отчёт: {period_label}</b>\n"
                f"👥 Курьеров: {len(couriers_stats)}\n"
                f"✅ Доставлено: {total_deliv}\n"
                f"💰 Итого к выплате: {total_earn:.2f} TJS"
            ),
            parse_mode="HTML"
        )
        return


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
                "❌ <b>Ваша заявка отклонена.</b>\n\n"
                "К сожалению, мы не можем принять вас на данный момент.\n"
                "По вопросам обратитесь к администрации."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить курьера {telegram_id} об отклонении: {e}")


# ─── NEW → READY_FOR_DRIVERS ─────────────────────────────────────────────────

@dp.message(Command("ready"))
async def cmd_ready(message: types.Message):
    if not _is_manager(message.chat.id):
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /ready &lt;order_id&gt;", parse_mode="HTML")
        return
    order_id = parts[1].strip()
    success, client_chat_id, err = await asyncio.to_thread(_sync_set_order_ready, order_id)
    if not success:
        await message.answer(f"❌ Не удалось — {err}.")
        return
    await message.answer(f"✅ Заказ <b>{order_id}</b> переведён в READY_FOR_DRIVERS.", parse_mode="HTML")
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"📦 Ваш заказ *{order_id}* принят и передан в доставку!\nОжидайте назначения курьера."
        )
