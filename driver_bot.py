import asyncio
import logging
import os
import traceback
from aiogram import types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import driver_bot as bot, client_bot, driver_dp as dp, sheet

_raw_ids = os.getenv("ALLOWED_DRIVER_IDS", "")
ALLOWED_DRIVER_IDS: set[int] = {
    int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()
}

def is_authorized_driver(user_id: int) -> bool:
    if not ALLOWED_DRIVER_IDS:
        return True  # dev-режим: пусто = без ограничений
    return user_id in ALLOWED_DRIVER_IDS

def _pad_row(row: list, size: int = 20) -> list:
    return row + [""] * max(0, size - len(row))


def _sync_get_free_orders():
    if not sheet:
        return []
    try:
        all_rows = sheet.get_all_values()
        free_list = []
        for idx, row in enumerate(all_rows):
            if idx == 0:  # Пропускаем шапку
                continue
            status = row[0].upper().strip() if len(row) > 0 else ""
            if status == "READY_FOR_DRIVERS":
                free_list.append({
                    "row_num": idx + 1,
                    "id":               row[1],
                    "price":            row[3],
                    "city_pickup":      row[4],
                    "address_pickup":   row[5],
                    "city_delivery":    row[6],
                    "address_delivery": row[7],
                    "driver_comment":   row[8]  if len(row) > 8  else "Нет",
                    "delivery_type":    row[9]  if len(row) > 9  else "DOOR",
                    "s_name":           row[12] if len(row) > 12 else "-",
                    "r_phone":          row[15] if len(row) > 15 else "-",
                })
        return free_list
    except Exception as e:
        logging.error(f"Ошибка чтения свободных заказов: {e}")
        return []


def _sync_take_order(row_num: int, courier_name: str, courier_id: str) -> bool:
    """
    Атомарная проверка + захват заказа в одной функции.
    Минимизирует окно гонки при одновременном захвате двумя курьерами.
    Использует batch_update для сокращения числа HTTP-запросов к Google API.
    """
    if not sheet:
        return False
    try:
        row = sheet.row_values(row_num)
        # Перепроверяем статус прямо перед записью
        current_status = row[0].upper().strip() if row else ""
        if current_status != "READY_FOR_DRIVERS":
            return False  # Уже кто-то забрал
        sheet.batch_update([
            {'range': f'A{row_num}', 'values': [["TAKEN"]]},
            {'range': f'R{row_num}', 'values': [[courier_name]]},
            {'range': f'T{row_num}', 'values': [[str(courier_id)]]},
        ])
        return True
    except Exception as e:
        logging.error(f"Ошибка захвата заказа (строка {row_num}): {e}")
        return False


def _sync_update_status(row_num: int, status: str) -> bool:
    """Обновляет только статус заказа (столбец A)."""
    if not sheet:
        return False
    try:
        sheet.update_cell(row_num, 1, status)
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления статуса на {status} (строка {row_num}): {e}")
        return False


async def send_client_push(chat_id: str, text: str):
    if client_bot and chat_id and str(chat_id).isdigit():
        try:
            await client_bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Не удалось отправить пуш клиенту {chat_id}: {e}")


@dp.message(CommandStart())
async def cmd_start_driver(message: types.Message):
    if not is_authorized_driver(message.from_user.id):
        await message.answer("⛔ Доступ запрещён. Обратитесь к администратору.")
        return
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔍 Посмотреть свободные заказы")
    await message.answer(
        "💼 **Кабинет курьера Mavsimi Rason**\n\nИспользуйте кнопку снизу для поиска актуальных задач на бирже:",
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )


@dp.message(F.text == "🔍 Посмотреть свободные заказы")
async def show_jobs(message: types.Message):
    if not is_authorized_driver(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    free_jobs = await asyncio.to_thread(_sync_get_free_orders)
    if not free_jobs:
        await message.answer("🕳️ К сожалению, на бирже сейчас нет свободных заказов.")
        return

    await message.answer(f"📦 **Свободные заказы на бирже ({len(free_jobs)}):**", parse_mode="Markdown")
    for o in free_jobs:
        dtype_readable = "ПВЗ 🏢" if o['delivery_type'] == "PVZ" else "До двери 🚪"
        card = (
            f"🆔 **Заказ №:** `{o['id']}`\n"
            f"• **Получатель:** {o['r_phone']}\n"
            f"• **Тип:** {dtype_readable}\n"
            f"• **Ориентир:** {o['driver_comment']}\n"
            f"────────────────────\n"
            f"📍 **Откуда:** {o['city_pickup']}, {o['address_pickup']}\n"
            f"🌆 **Куда:** {o['city_delivery']}, {o['address_delivery']}\n"
            f"💰 **Тариф:** {o['price']} TJS\n"
            f"👤 **Отправитель:** {o['s_name']}"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Взять заказ", callback_data=f"take:{o['row_num']}")
        await message.answer(card, reply_markup=builder.as_markup(), parse_mode="Markdown")


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
        c_id = callback.from_user.id

        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))

        order_id = row_vals[1]         # B - ID заказа
        client_chat_id = row_vals[18]  # S - Chat ID клиента

        success = await asyncio.to_thread(_sync_take_order, row_num, c_name, c_id)
        if not success:
            await callback.answer("❌ Этот заказ уже забрал другой водитель!", show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="📦 Приступить к погрузке товара", callback_data=f"load:{row_num}")

        await callback.message.edit_text(
            f"🎉 **Вы взяли в работу заказ {order_id}!**\n\nСтатус заказа изменен на **[Взят курьером]**. "
            f"Отправляйтесь на точку забора и нажмите кнопку ниже, когда начнете погрузку.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        await callback.answer()

        if client_chat_id:
            await send_client_push(
                client_chat_id,
                f"🚚 **Ваш заказ {order_id} принят курьером!**\n👤 **Курьер:** {c_name}\n\n*Ожидайте погрузки товара.*"
            )
    except Exception as e:
        logging.error(f"Критический сбой кнопки take: {traceback.format_exc()}")
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

        c_name = callback.from_user.full_name

        row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, row_num))

        order_id = row_vals[1]         # B
        client_chat_id = row_vals[18]  # S

        await asyncio.to_thread(_sync_update_status, row_num, "LOADING")

        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Товар погружен, выехать в путь", callback_data=f"transit:{row_num}")

        await callback.message.edit_text(
            f"📦 **Заказ {order_id}: Погрузка товара**\n\nСтатус изменен на **[Погрузка]**. "
            f"После того как укомплектуете посылку в машину, нажмите кнопку ниже для выезда.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"📦 **Курьер {c_name} начал погрузку вашего отправления {order_id}.**")
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

        order_id = row_vals[1]         # B
        client_chat_id = row_vals[18]  # S

        await asyncio.to_thread(_sync_update_status, row_num, "IN_TRANSIT")

        builder = InlineKeyboardBuilder()
        builder.button(text="📍 Я на месте (прибыл к клиенту)", callback_data=f"arrived:{row_num}")

        await callback.message.edit_text(
            f"🚚 **Заказ {order_id}: В пути**\n\nСтатус изменен на **[В пути]**. "
            f"Управляйте машиной осторожно. Как будете у дверей получателя или на ПВЗ назначения, нажмите «На месте».",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"🚀 **Ваш заказ {order_id} уже в пути!**\nКурьер направляется по адресу назначения.")
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

        order_id = row_vals[1]         # B
        client_chat_id = row_vals[18]  # S

        await asyncio.to_thread(_sync_update_status, row_num, "ARRIVED")

        builder = InlineKeyboardBuilder()
        builder.button(text="🏁 Заказ успешно доставлен", callback_data=f"done:{row_num}")

        await callback.message.edit_text(
            f"📍 **Заказ {order_id}: Курьер на месте**\n\nСтатус изменен на **[На месте]**. "
            f"Передайте посылку получателю, проверьте оплату и зафиксируйте успешное завершение.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"🔔 **Курьер уже прибыл на ваш адрес с заказом {order_id}!**\nПожалуйста, будьте на связи для получения.")
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

        order_id = row_vals[1]         # B
        client_chat_id = row_vals[18]  # S

        await asyncio.to_thread(_sync_update_status, row_num, "DELIVERED")

        await callback.message.edit_text(
            f"🏁 **Заказ {order_id} успешно закрыт!**\n\nСтатус изменен на **[Доставлен]**. Отличная работа! Отдыхайте или ищите новые задачи на бирже.",
            reply_markup=None,
            parse_mode="Markdown"
        )
        await callback.answer()
        if client_chat_id:
            await send_client_push(client_chat_id, f"✅ **Ваш заказ {order_id} успешно доставлен получателю!**\nСпасибо, что выбрали Mavsimi Rason!")
    except Exception as e:
        logging.error(f"Ошибка done (строка {row_num}): {e}", exc_info=True)
        await callback.answer("❌ Ошибка при завершении. Попробуйте позже.", show_alert=True)