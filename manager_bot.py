import asyncio
import base64
import json
import logging
import os
import traceback

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
    send_client_push,
)

MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID", "")
ADMIN_PANEL_URL  = os.getenv("ADMIN_PANEL_URL", "")


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

    if data.get("action") != "reassign_request":
        return

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
